"""The load-bearing test of the design: two orgs cannot see each other."""

import json
from datetime import timedelta

from django.db import connections
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.ledger.models import DatabaseFund
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import Org
from apps.orgs.router import NoOrgContext

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# Django computes its per-test database allowlist once, from whatever aliases
# already exist, before any test's setUp runs (same reasoning as
# test_org_middleware.py's ORG_ALIAS) — so these must be real, registered
# aliases by then, not just names in `databases`.
ORG_A_ALIAS = alias_for_org("orga")
ORG_B_ALIAS = alias_for_org("orgb")


def _register_tenant_alias(org_id):
    """Register (or re-register) a dynamic tenant alias for this test run.

    ensure_connection() builds a fresh config straight from the raw URL when
    the alias isn't already registered. That's exactly what happens if an
    unrelated test evicts it from the shared connection registry first (see
    the registry cap test in test_org_connections.py) — the LRU cap test
    floods the same module-level registry these aliases live in, and its
    eviction pops the alias's dict entry out entirely. A freshly built config
    carries the connection string's real database name, not the "test_"
    prefixed database Django's test runner swapped "tenant_dev" to at suite
    startup (that one-time swap only reaches aliases that already existed
    then). Re-pointing NAME at whatever "tenant_dev" currently resolves to
    keeps this alias on the same physical test database no matter what
    happened to the registry in between.
    """
    alias = ensure_connection(Org(id=org_id, db_connection=TENANT_URL))
    connections.databases[alias]["NAME"] = connections["tenant_dev"].settings_dict["NAME"]
    return alias


_register_tenant_alias("orga")
_register_tenant_alias("orgb")


class NoContextTests(TestCase):
    databases = {"default", "tenant_dev"}

    def test_tenant_query_outside_a_request_raises(self):
        with self.assertRaises(NoOrgContext):
            list(DatabaseFund.objects.all())

    def test_control_plane_query_outside_a_request_is_fine(self):
        self.assertEqual(Org.objects.count(), 0)


class TwoOrgTests(TransactionTestCase):
    # TransactionTestCase, not TestCase: the fixture below writes through the
    # "tenant_dev" alias while the request under test reads through the
    # dynamically routed "org_orga" alias — a different connection to the
    # same physical database. TestCase wraps each declared alias in its own
    # uncommitted transaction for the whole class, so a write on one alias's
    # connection is invisible to a completely different connection's read,
    # even against the identical physical database (ordinary Postgres
    # cross-session visibility, nothing to do with tenant routing).
    # TransactionTestCase commits for real, so the write is visible everywhere.
    databases = {"default", "tenant_dev", ORG_A_ALIAS, ORG_B_ALIAS}

    @classmethod
    def setUpClass(cls):
        # Re-register immediately before Django validates `databases` against
        # the live registry: an unrelated test's eviction (see the registry
        # cap test in test_org_connections.py) can drop these between module
        # import and here — and by now setup_databases() has already run, so
        # a plain ensure_connection() would rebuild the alias pointing at the
        # real database instead of the "test_" one. _register_tenant_alias
        # re-syncs it to whatever "tenant_dev" actually resolves to.
        _register_tenant_alias("orga")
        _register_tenant_alias("orgb")
        super().setUpClass()

    def setUp(self):
        # Both orgs point at the same physical development database on purpose.
        # If isolation held only because the databases differed, this would
        # prove nothing about routing. Here the alias is the only separation,
        # so a routing bug shows up as a visible cross-org read.
        self.client = Client()
        Org.objects.create(
            id="orga", name="Alpha Funds", slug="alpha",
            owner_email="a@example.com", db_connection=TENANT_URL,
        )
        Org.objects.create(
            id="orgb", name="Beta Funds", slug="beta",
            owner_email="b@example.com", db_connection=TENANT_URL,
        )
        self.token_a = create_session_token("u1", "orga")
        with org_context("tenant_dev"):
            user = User.objects.create(
                id="u1", username="alice", email="alice@example.com",
                password_hash="x", role=User.Role.OWNER,
            )
            DatabaseFund.objects.create(id="f1", created_by=user, name="Alpha Fund")
            Session.objects.create(
                id="s1", user_id="u1", token=self.token_a,
                expires_at=timezone.now() + timedelta(hours=1),
            )

    def test_authenticated_request_reads_its_own_org(self):
        response = self.client.get(
            "/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token_a}"
        )
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in json.loads(response.content)]
        self.assertEqual(names, ["Alpha Fund"])

    def test_tampered_org_claim_is_rejected(self):
        head, payload, _sig = self.token_a.split(".")
        tampered = f"{head}.{payload}.deadbeefsignature"
        response = self.client.get(
            "/api/databases", HTTP_AUTHORIZATION=f"Bearer {tampered}"
        )
        self.assertEqual(response.status_code, 401)

    def test_every_tenant_response_omits_credentials(self):
        response = self.client.get(
            "/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token_a}"
        )
        body = response.content.decode("utf-8")
        for secret in ("postgres://", "devpassword", "db_connection"):
            self.assertNotIn(secret, body, f"{secret} leaked into an API response")
