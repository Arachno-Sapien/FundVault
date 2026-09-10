import json
from datetime import timedelta

import bcrypt
from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import EmailIndex, Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# The middleware resolves org "o1" to this alias at request time (see
# apps.orgs.connections.alias_for_org), not to the static "tenant_dev" alias —
# each org gets its own dynamically-registered connection, even when (as in
# dev/tests) it happens to point at the same physical database. Django computes
# its per-test database allowlist once, before any test's setUp runs, so the
# alias must already be real by then, not just a name in `databases` (see
# tests.test_org_middleware for the same pattern).
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))


class OrgDiscoveryTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()
        self.first = Org.objects.create(
            id="o1", name="Acme Funds", slug="acme",
            owner_email="x@example.com", db_connection=TENANT_URL,
        )
        self.second = Org.objects.create(
            id="o2", name="Personal", slug="personal",
            owner_email="x@example.com", db_connection=TENANT_URL,
        )
        EmailIndex.objects.create(email="x@example.com", org=self.first)
        EmailIndex.objects.create(email="x@example.com", org=self.second)

    def _lookup(self, email):
        return self.client.post(
            "/api/auth/orgs",
            data=json.dumps({"email": email}),
            content_type="application/json",
        )

    def test_lists_every_org_for_the_email(self):
        response = self._lookup("x@example.com")
        self.assertEqual(response.status_code, 200)
        names = sorted(org["name"] for org in json.loads(response.content)["orgs"])
        self.assertEqual(names, ["Acme Funds", "Personal"])

    def test_unknown_email_returns_an_empty_list_not_an_error(self):
        response = self._lookup("nobody@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["orgs"], [])

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(len(json.loads(self._lookup("X@Example.com").content)["orgs"]), 2)

    def test_no_credentials_are_returned(self):
        text = self._lookup("x@example.com").content.decode("utf-8")
        self.assertNotIn("postgres://", text)
        self.assertNotIn("devpassword", text)


class LoginTests(TestCase):
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # Re-register immediately before Django validates this class's
        # `databases` against the live registry: the connection registry's LRU
        # is shared with other test modules (e.g. test_org_connections.py's
        # eviction test), which can push ORG_ALIAS out between module import
        # and here.
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="a@example.com", db_connection=TENANT_URL,
        )
        EmailIndex.objects.create(email="alice@example.com", org=self.org)
        with org_context(ORG_ALIAS):
            User.objects.create(
                id="u1", username="alice", email="alice@example.com",
                password_hash=bcrypt.hashpw(b"hunter22", bcrypt.gensalt()).decode("utf-8"),
                role=User.Role.OWNER, is_active=True,
            )

    def _login(self, **overrides):
        payload = {"orgId": "o1", "username": "alice", "password": "hunter22"}
        payload.update(overrides)
        return self.client.post(
            "/api/auth/login", data=json.dumps(payload), content_type="application/json"
        )

    def test_login_with_an_org_succeeds(self):
        response = self._login()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(json.loads(response.content)["token"])

    def test_login_without_an_org_is_refused(self):
        response = self.client.post(
            "/api/auth/login",
            data=json.dumps({"username": "alice", "password": "hunter22"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_login_with_an_unknown_org_is_refused(self):
        self.assertEqual(self._login(orgId="nope").status_code, 404)

    def test_wrong_password_is_refused(self):
        self.assertEqual(self._login(password="wrong").status_code, 401)

    def test_login_refreshes_the_email_index(self):
        before = EmailIndex.objects.get(email="alice@example.com").last_seen_at
        self._login()
        after = EmailIndex.objects.get(email="alice@example.com").last_seen_at
        self.assertGreater(after, before)

    def test_public_signup_is_gone(self):
        response = self.client.post(
            "/api/auth/signup",
            data=json.dumps({"username": "x", "email": "x@example.com", "password": "hunter22"}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (404, 405))
