import json
from datetime import timedelta

import jwt
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.connections import alias_for_org, drop_connection, ensure_connection
from apps.orgs.context import current_org_alias, org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# The middleware resolves org "o1" to this alias at request time (see
# apps.orgs.connections.alias_for_org). Django's per-test database allowlist is
# computed once, from whatever aliases already exist, before any test's setUp
# runs — so it must already be a real, valid alias by then, not just a name in
# `databases`. Registering it here at import time (during test discovery,
# before Django's database checks and setup_databases run) makes that hold.
# Uses the real ensure_connection() (not a raw dict write) so the registry's
# own LRU bookkeeping stays consistent.
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))


class TokenTests(TestCase):
    databases = {"default"}

    def test_token_carries_the_org_claim(self):
        token = create_session_token("u1", "o1")
        decoded = jwt.decode(token, settings.FUNDVAULT_JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(decoded["id"], "u1")
        self.assertEqual(decoded["org_id"], "o1")


class MiddlewareTests(TransactionTestCase):
    # TransactionTestCase, not TestCase: this test drops and re-registers the
    # "org_o1" connection mid-test (to simulate a connection string changing
    # underneath an already-open alias). TestCase wraps every declared alias in
    # an outer atomic block for the whole class; closing that connection out
    # from under it corrupts Django's own rollback bookkeeping. TransactionTestCase
    # doesn't wrap connections in atomics, so closing/reopening one is safe.
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # ORG_ALIAS shares the connection registry's module-level LRU with
        # every other test that registers tenant connections (e.g. the
        # eviction test in test_org_connections.py) — when the full suite
        # runs, an unrelated test filling that cache past MAX_TENANT_CONNECTIONS
        # can evict it between module import and here. Django validates this
        # class's `databases` against the live registry right after this
        # method starts, so re-register it immediately before that happens.
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        ensure_connection(self.org)
        self.token = create_session_token("u1", "o1")
        with org_context(ORG_ALIAS):
            User.objects.create(
                id="u1", username="alice", email="alice@example.com",
                password_hash="x", role=User.Role.OWNER,
            )
            Session.objects.create(
                id="s1", user_id="u1", token=self.token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        self.client = Client()

    def test_context_is_clear_after_a_request(self):
        self.client.get("/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertIsNone(
            current_org_alias(), "middleware leaked org context past the response"
        )

    def test_unknown_org_is_rejected(self):
        stray = create_session_token("u1", "does-not-exist")
        response = self.client.get("/api/databases", HTTP_AUTHORIZATION=f"Bearer {stray}")
        self.assertEqual(response.status_code, 401)

    def test_token_without_an_org_claim_is_rejected(self):
        legacy = jwt.encode(
            {"id": "u1", "exp": int((timezone.now() + timedelta(hours=1)).timestamp())},
            settings.FUNDVAULT_JWT_SECRET,
            algorithm="HS256",
        )
        response = self.client.get("/api/databases", HTTP_AUTHORIZATION=f"Bearer {legacy}")
        self.assertEqual(response.status_code, 401)

    def test_request_without_a_token_is_rejected(self):
        response = self.client.get("/api/databases")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content)["error"], "No token provided")

    def test_unreachable_tenant_returns_503_naming_the_org(self):
        Org.objects.filter(id="o1").update(
            db_connection="postgres://fundvault:devpassword@127.0.0.1:9/nothing"
        )
        # ensure_connection() is idempotent by alias, so the already-open, good
        # connection from setUp would otherwise mask the org's new (bad) one.
        # Drop it so the middleware re-derives the config from the row we just
        # updated, and restore a reachable one afterward so Django's own
        # post-test table flush (which runs against every declared alias)
        # doesn't itself try to talk to the unreachable host.
        drop_connection(ORG_ALIAS)
        self.addCleanup(lambda: (drop_connection(ORG_ALIAS), ensure_connection(self.org)))

        response = self.client.get("/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Acme", json.loads(response.content)["error"])
