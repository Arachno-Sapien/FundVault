import json
from datetime import timedelta

import jwt
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from apps.common.auth import create_session_token
from apps.orgs.connections import drop_connection, ensure_connection
from apps.orgs.context import current_org_alias
from apps.orgs.models import Org
from tests.support import ORG_ALIAS, OrgTestMixin, TENANT_URL


class TokenTests(TestCase):
    databases = {"default"}

    def test_token_carries_the_org_claim(self):
        token = create_session_token("u1", "o1")
        decoded = jwt.decode(token, settings.FUNDVAULT_JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(decoded["id"], "u1")
        self.assertEqual(decoded["org_id"], "o1")


class MiddlewareTests(OrgTestMixin, TransactionTestCase):
    # TransactionTestCase, not TestCase: this test drops and re-registers the
    # "org_o1" connection mid-test (to simulate a connection string changing
    # underneath an already-open alias). TestCase wraps every declared alias in
    # an outer atomic block for the whole class; closing that connection out
    # from under it corrupts Django's own rollback bookkeeping. TransactionTestCase
    # doesn't wrap connections in atomics, so closing/reopening one is safe.

    def setUp(self):
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        ensure_connection(self.org)
        self.token = self.make_user("u1", "owner", username="alice", email="alice@example.com")
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
        body = json.loads(response.content)["error"]
        self.assertIn("Acme", body)
        # The raw driver exception (psycopg's OperationalError) can include the
        # tenant's host/port/username — none of that may reach the client.
        self.assertNotIn("127.0.0.1", body)
        self.assertNotIn(":9", body)
        self.assertNotIn("psycopg", body)
        self.assertLess(len(body), 150, "error body looks like it leaked driver detail")

    def test_invalid_connection_string_does_not_leak_details(self):
        bogus = "not-a-postgres-url-at-all"
        Org.objects.filter(id="o1").update(db_connection=bogus)
        drop_connection(ORG_ALIAS)
        self.addCleanup(lambda: (drop_connection(ORG_ALIAS), ensure_connection(self.org)))

        response = self.client.get("/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.content)["error"]
        self.assertIn("Acme", body)
        self.assertNotIn(bogus, body)

    def test_expired_or_invalid_bearer_token_is_a_clean_401_not_a_500(self):
        # A header is present but doesn't decode (garbage/expired/malformed).
        # OrgContextMiddleware._resolve_org returns None for this by design —
        # no org context is ever set — so auth_required must not touch the
        # tenant database (the session lookup) before it has confirmed
        # the token decodes, or it crashes with NoOrgContext instead of 401.
        response = self.client.get(
            "/api/databases", HTTP_AUTHORIZATION="Bearer not-a-real-jwt"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content)["error"], "Invalid token")
