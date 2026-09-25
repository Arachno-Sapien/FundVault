import json
from datetime import timedelta

import bcrypt
from django.core.cache import cache
from django.db import connections
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
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
        # The lookup is rate limited per IP on Django's cache, and LocMemCache
        # is one dict for the whole test run: clear on the way in so an earlier
        # module cannot refuse these lookups, and on the way out because the
        # burst test below leaves a counter at its limit.
        cache.clear()
        self.addCleanup(cache.clear)
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

    def _lookup(self, email, ip="10.0.0.1"):
        return self.client.post(
            "/api/auth/orgs",
            data=json.dumps({"email": email}),
            content_type="application/json",
            REMOTE_ADDR=ip,
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

    def test_email_enumeration_is_capped_per_ip(self):
        # 20 per IP per minute. Unknown addresses answer 200 with an empty
        # list, so without a cap this is a free "is this person a customer"
        # oracle over any address list.
        for attempt in range(20):
            self.assertEqual(self._lookup("nobody@example.com").status_code, 200, attempt)
        refused = self._lookup("nobody@example.com")
        self.assertEqual(refused.status_code, 429)
        self.assertIn("Too many requests", json.loads(refused.content)["error"])
        # Per IP, not per address: a fresh address does not buy a way around it.
        self.assertEqual(self._lookup("x@example.com").status_code, 429)
        self.assertEqual(self._lookup("x@example.com", ip="10.0.0.2").status_code, 200)

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
        # login is rate limited per IP on Django's cache, and LocMemCache is one
        # dict for the whole test run — without this, the burst test below would
        # leave every later login in this process refused with a 429.
        cache.clear()
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

    def test_repeated_failed_logins_are_eventually_refused(self):
        # 15 per IP per minute. An unknown username fails before bcrypt runs,
        # so the burst costs no hashing time.
        for attempt in range(15):
            self.assertEqual(self._login(username="nobody").status_code, 401, attempt)
        self.assertEqual(self._login(username="nobody").status_code, 429)
        # The budget is per IP, not per account: the right password does not
        # buy a way around it.
        self.assertEqual(self._login().status_code, 429)

    def test_public_signup_is_gone(self):
        response = self.client.post(
            "/api/auth/signup",
            data=json.dumps({"username": "x", "email": "x@example.com", "password": "hunter22"}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (404, 405))

    # --- sessions: expiry is checked per request, cleanup happens at login ---

    def _session(self, session_id, expires_in):
        token = create_session_token("u1", "o1")
        with org_context(ORG_ALIAS):
            Session.objects.create(
                id=session_id, user_id="u1", token=token,
                expires_at=timezone.now() + expires_in,
            )
        return token

    def test_an_expired_session_is_refused(self):
        # The JWT is good for another 24h; only the session row has expired.
        token = self._session("s_old", timedelta(seconds=-1))
        response = self.client.get("/api/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 401)

    def test_login_clears_expired_sessions(self):
        self._session("s_old", timedelta(seconds=-1))
        self.assertEqual(self._login().status_code, 200)
        with org_context(ORG_ALIAS):
            self.assertFalse(Session.objects.filter(id="s_old").exists())

    def test_an_authenticated_read_does_not_write(self):
        token = self._session("s_live", timedelta(hours=1))
        with CaptureQueriesContext(connections[ORG_ALIAS]) as tenant:
            response = self.client.get("/api/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([q["sql"].split()[0] for q in tenant.captured_queries], ["SELECT"])
