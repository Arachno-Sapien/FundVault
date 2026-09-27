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
from apps.orgs.context import org_context
from apps.orgs.models import EmailIndex, Org
from tests.support import ORG_ALIAS, OrgTestMixin, TENANT_URL


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


class LoginTests(OrgTestMixin, TestCase):
    def setUp(self):
        # login is rate limited per IP on Django's cache, and LocMemCache is one
        # dict for the whole test run — without this, the burst test below would
        # leave every later login in this process refused with a 429.
        cache.clear()
        self.addCleanup(cache.clear)
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

    def test_a_username_matching_someone_elses_email_does_not_shadow_their_login(self):
        # A member can set their own username to anything, including another
        # member's email address. Before this fix, the username match was
        # tried first and won unconditionally -- locking alice out of logging
        # in with her own email once someone else's username equalled it.
        with org_context(ORG_ALIAS):
            User.objects.create(
                id="u2", username="alice@example.com", email="shadow@example.com",
                password_hash=bcrypt.hashpw(b"malpass", bcrypt.gensalt()).decode("utf-8"),
                role=User.Role.MEMBER, is_active=True,
            )
        response = self._login(username="alice@example.com")  # alice's real email + real password
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)["user"]["id"], "u1")

    def test_non_object_login_body_is_a_normal_400_not_a_500(self):
        # The middleware parses orgId out of the body itself, before
        # auth_required ever runs, and used to skip parse_body's dict guard.
        for body in ("[]", "null", '"just a string"', "42"):
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/auth/login", data=body, content_type="application/json"
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(
                    json.loads(response.content)["error"], "Choose an organisation first"
                )

    def test_non_utf8_login_body_is_a_normal_400_not_a_500(self):
        # A body that isn't valid UTF-8 at all makes request.body.decode
        # raise UnicodeDecodeError straight out of parse_body, inside the
        # middleware's own org lookup -- before the dict-guard in the test
        # above ever runs. It must land on the same 400 as a missing orgId,
        # not a 500.
        response = self.client.post(
            "/api/auth/login", data=b"\xff\xfe{", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            json.loads(response.content)["error"], "Choose an organisation first"
        )

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

    def test_logout_then_me_is_401(self):
        response = self._login()
        token = json.loads(response.content)["token"]
        self.assertEqual(
            self.client.post("/api/auth/logout", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 200
        )
        self.assertEqual(
            self.client.get("/api/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 401
        )

    def test_login_with_the_right_password_but_an_inactive_account_is_403(self):
        with org_context(ORG_ALIAS):
            User.objects.create(
                id="u_inactive", username="bob", email="bob@example.com",
                password_hash=bcrypt.hashpw(b"hunter22", bcrypt.gensalt()).decode("utf-8"),
                role=User.Role.MEMBER, is_active=False,
            )
        self.assertEqual(self._login(username="bob", password="hunter22").status_code, 403)


class HealthCheckTests(TestCase):
    databases = {"default"}

    def test_health_check_needs_no_token(self):
        response = Client().get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"status": "ok"})
