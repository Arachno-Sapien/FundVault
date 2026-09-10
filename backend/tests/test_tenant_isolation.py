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
# Org B gets a genuinely different physical database (same Postgres server,
# different database name — see settings_test.py's "tenant_dev_b" entry).
# This design isolates orgs by physical database, not by a row-level org_id
# column (apps/ledger/models.py has none), so if both test orgs shared one
# physical database, ANY query would return both orgs' rows regardless of
# which alias the router picked — a real router bug (hard-routing every
# request to org A) would be indistinguishable from correct behaviour.
# Verified empirically: pointing both orgs at the same TENANT_URL and
# asserting a filtered result fails even with a correctly working router,
# because Postgres has no way to tell the two orgs' rows apart in one table.
TENANT_URL_B = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev_orgb"

# Django computes its per-test database allowlist once, from whatever aliases
# already exist, before any test's setUp runs (same reasoning as
# test_org_middleware.py's ORG_ALIAS) — so these must be real, registered
# aliases by then, not just names in `databases`.
ORG_A_ALIAS = alias_for_org("orga")
ORG_B_ALIAS = alias_for_org("orgb")


def _register_tenant_alias(org_id, url, base_alias):
    """Register (or re-register) a dynamic tenant alias for this test run.

    ensure_connection() builds a fresh config straight from the raw URL when
    the alias isn't already registered. That's exactly what happens if an
    unrelated test evicts it from the shared connection registry first (see
    the registry cap test in test_org_connections.py) — the LRU cap test
    floods the same module-level registry these aliases live in, and its
    eviction pops the alias's dict entry out entirely. A freshly built config
    carries the connection string's real database name, not the "test_"
    prefixed database Django's test runner swapped `base_alias` to at suite
    startup (that one-time swap only reaches aliases that already existed
    then). Re-pointing NAME at whatever `base_alias` currently resolves to
    keeps this alias on the same physical test database no matter what
    happened to the registry in between.
    """
    alias = ensure_connection(Org(id=org_id, db_connection=url))
    connections.databases[alias]["NAME"] = connections[base_alias].settings_dict["NAME"]
    return alias


_register_tenant_alias("orga", TENANT_URL, "tenant_dev")
_register_tenant_alias("orgb", TENANT_URL_B, "tenant_dev_b")


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
    databases = {"default", "tenant_dev", "tenant_dev_b", ORG_A_ALIAS, ORG_B_ALIAS}

    @classmethod
    def setUpClass(cls):
        # Re-register immediately before Django validates `databases` against
        # the live registry: an unrelated test's eviction (see the registry
        # cap test in test_org_connections.py) can drop these between module
        # import and here — and by now setup_databases() has already run, so
        # a plain ensure_connection() would rebuild the alias pointing at the
        # real database instead of the "test_" one. _register_tenant_alias
        # re-syncs each to whatever its base alias actually resolves to.
        _register_tenant_alias("orga", TENANT_URL, "tenant_dev")
        _register_tenant_alias("orgb", TENANT_URL_B, "tenant_dev_b")
        super().setUpClass()

    def setUp(self):
        # Org A and org B point at two different physical databases on the
        # same Postgres server (see TENANT_URL / TENANT_URL_B above) — the
        # actual production isolation boundary, since this design has no
        # row-level org_id column. A router bug that hard-routed every
        # request to org A's alias would then make org B's request read (or
        # even write) org A's physical database — a real, visible leak.
        self.client = Client()
        Org.objects.create(
            id="orga", name="Alpha Funds", slug="alpha",
            owner_email="a@example.com", db_connection=TENANT_URL,
        )
        Org.objects.create(
            id="orgb", name="Beta Funds", slug="beta",
            owner_email="b@example.com", db_connection=TENANT_URL_B,
        )
        self.token_a = create_session_token("u1", "orga")
        self.token_b = create_session_token("u2", "orgb")
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
        # Second, fully independent org fixture, in org B's own physical
        # database. "Beta Fund" is deliberately distinct from "Alpha Fund" so
        # a leak in either direction is obvious in a failing assertion, not
        # disguised by both orgs happening to share a fund name.
        with org_context("tenant_dev_b"):
            user_b = User.objects.create(
                id="u2", username="bob", email="bob@example.com",
                password_hash="x", role=User.Role.OWNER,
            )
            DatabaseFund.objects.create(id="f2", created_by=user_b, name="Beta Fund")
            Session.objects.create(
                id="s2", user_id="u2", token=self.token_b,
                expires_at=timezone.now() + timedelta(hours=1),
            )

    def test_authenticated_request_reads_its_own_org(self):
        # The actual cross-org proof: org A sees only its own fund, org B
        # sees only its own fund, even though both requests hit the same
        # view with the same code path. If the router ever ignored the JWT's
        # org_id and hard-routed every request to org A's alias, org B's
        # request would come back with "Alpha Fund" instead of "Beta Fund"
        # (or org A's own data would be reachable from org B's token) and
        # this would fail.
        response_a = self.client.get(
            "/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token_a}"
        )
        self.assertEqual(response_a.status_code, 200)
        names_a = [row["name"] for row in json.loads(response_a.content)]
        self.assertEqual(names_a, ["Alpha Fund"])

        response_b = self.client.get(
            "/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token_b}"
        )
        self.assertEqual(response_b.status_code, 200)
        names_b = [row["name"] for row in json.loads(response_b.content)]
        self.assertEqual(names_b, ["Beta Fund"])

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
