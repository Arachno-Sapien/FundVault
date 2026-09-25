import json
from datetime import timedelta

from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.connections import alias_for_org, drop_connection, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import EmailIndex, JoinCode, Org, new_join_code

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# join_org/join_codes/revoke_join_code all resolve the org's tenant database
# through apps.orgs.connections.ensure_connection (alias "org_o1"), never
# through the statically declared "tenant_dev" alias — those are two
# different Django connections onto the same physical database. A plain
# TestCase wraps each declared alias in its own uncommitted transaction, so a
# fixture written via "tenant_dev" would be invisible to a request read via
# "org_o1" (see test_org_middleware.py / test_tenant_isolation.py for the
# same reasoning already established in this codebase). Fixtures below use
# ORG_ALIAS directly so setup and the request-under-test share one
# connection. Registered at import time, same as test_org_middleware.py's
# ORG_ALIAS: Django computes each TestCase's per-test database allowlist from
# whatever aliases already exist right before that class's setUpClass runs.
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))


class JoinFlowTests(TestCase):
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # Re-register in case an unrelated test (e.g. the LRU cap test in
        # test_org_connections.py) evicted ORG_ALIAS from the shared registry
        # between module import and now.
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme Funds", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.code = JoinCode.objects.create(
            code=new_join_code(), org=self.org, grants_role="member",
            expires_at=timezone.now() + timedelta(days=7), max_uses=3,
        )

    def _join(self, **overrides):
        payload = {
            "code": self.code.code,
            "username": "bob",
            "email": "bob@example.com",
            "password": "hunter22",
        }
        payload.update(overrides)
        return self.client.post(
            "/api/orgs/join", data=json.dumps(payload), content_type="application/json"
        )

    def test_preview_returns_the_org_name_only(self):
        response = self.client.post(
            "/api/orgs/join/preview",
            data=json.dumps({"code": self.code.code}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body["org"]["name"], "Acme Funds")
        self.assertNotIn("db_connection", json.dumps(body))
        self.assertNotIn("postgres://", response.content.decode("utf-8"))

    def test_join_creates_a_user_with_the_granted_role(self):
        response = self._join()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)["user"]["role"], "member")

    def test_join_consumes_one_use(self):
        self._join()
        self.code.refresh_from_db()
        self.assertEqual(self.code.uses, 1)

    def test_join_writes_the_email_index(self):
        self._join()
        self.assertTrue(
            EmailIndex.objects.filter(email="bob@example.com", org=self.org).exists()
        )

    def test_expired_code_is_refused(self):
        JoinCode.objects.filter(code=self.code.code).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self._join()
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", json.loads(response.content)["error"].lower())

    def test_revoked_code_is_refused(self):
        JoinCode.objects.filter(code=self.code.code).update(revoked=True)
        self.assertEqual(self._join().status_code, 400)

    def test_unknown_code_is_refused(self):
        self.assertEqual(self._join(code="FUNDVAULT-ZZZZ-ZZZZ").status_code, 404)

    def test_duplicate_email_in_the_same_org_is_refused(self):
        self._join()
        response = self._join(username="bob2")
        self.assertEqual(response.status_code, 400)

    def test_exhausted_code_is_refused(self):
        JoinCode.objects.filter(code=self.code.code).update(max_uses=1, uses=1)
        self.assertEqual(self._join().status_code, 400)


class InvalidOrgConnectionTests(TransactionTestCase):
    # TransactionTestCase, not TestCase: this test drops and re-registers the
    # "org_o1" connection mid-test (see apps.orgs.connections.drop_connection
    # below). TestCase wraps every declared alias in an outer atomic block for
    # the whole class; closing that connection out from under it corrupts
    # Django's own rollback bookkeeping. TransactionTestCase doesn't wrap
    # connections in atomics, so closing/reopening one is safe — same
    # reasoning as MiddlewareTests in test_org_middleware.py.
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme Funds", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.code = JoinCode.objects.create(
            code=new_join_code(), org=self.org, grants_role="member",
            expires_at=timezone.now() + timedelta(days=7), max_uses=3,
        )

    def test_invalid_org_connection_string_does_not_leak_details(self):
        # join_org is on the public/unauthenticated path, so
        # OrgContextMiddleware never resolves this org itself — its own
        # InvalidConnectionString -> 503 handling doesn't run for this
        # request. This proves join_org's own guard around ensure_connection
        # (added during self-review; see apps/orgs/views.py) does the same
        # job, the way test_org_middleware.py proves it for the authenticated
        # path.
        bogus = "not-a-postgres-url-at-all"
        Org.objects.filter(id="o1").update(db_connection=bogus)
        drop_connection(ORG_ALIAS)
        self.addCleanup(lambda: (drop_connection(ORG_ALIAS), ensure_connection(self.org)))

        response = self.client.post(
            "/api/orgs/join",
            data=json.dumps({
                "code": self.code.code, "username": "bob",
                "email": "bob@example.com", "password": "hunter22",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.content)["error"]
        self.assertIn("Acme Funds", body)
        self.assertNotIn(bogus, body)


class CodeManagementTests(TestCase):
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.owner_token = self._user("u1", "owner", User.Role.OWNER)
        self.member_token = self._user("u2", "bob", User.Role.MEMBER)
        self.admin_token = self._user("u3", "carol", User.Role.ADMIN)

    def _user(self, user_id, username, role):
        token = create_session_token(user_id, "o1")
        with org_context(ORG_ALIAS):
            User.objects.create(
                id=user_id, username=username, email=f"{username}@example.com",
                password_hash="x", role=role,
            )
            Session.objects.create(
                id=f"s{user_id}", user_id=user_id, token=token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        return token

    def _mint(self, token, role="member"):
        return self.client.post(
            "/api/orgs/codes",
            data=json.dumps({"role": role}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_owner_can_mint_an_admin_code(self):
        self.assertEqual(self._mint(self.owner_token, "admin").status_code, 200)

    def test_member_cannot_mint_any_code(self):
        self.assertEqual(self._mint(self.member_token).status_code, 403)

    def test_member_cannot_list_or_revoke_codes(self):
        code = json.loads(self._mint(self.owner_token).content)["code"]
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.member_token}"}
        self.assertEqual(self.client.get("/api/orgs/codes", **auth).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/orgs/codes/{code}", **auth).status_code, 403)
        self.assertFalse(JoinCode.objects.get(code=code).revoked)

    def test_minted_code_is_shaped_correctly(self):
        response = self._mint(self.owner_token)
        self.assertRegex(json.loads(response.content)["code"], r"^FUNDVAULT-")

    def test_owner_role_cannot_be_granted_by_a_code(self):
        response = self._mint(self.owner_token, "owner")
        self.assertEqual(response.status_code, 400)

    def test_admin_can_mint_a_member_code(self):
        self.assertEqual(self._mint(self.admin_token, "member").status_code, 200)

    def test_admin_cannot_mint_an_admin_code(self):
        # The authorization-sensitive boundary this task is named for: only
        # the Owner may hand out Admin access, never an Admin themselves.
        response = self._mint(self.admin_token, "admin")
        self.assertEqual(response.status_code, 400)

    def test_revoking_a_code_prevents_its_use(self):
        code = json.loads(self._mint(self.owner_token).content)["code"]
        response = self.client.delete(
            f"/api/orgs/codes/{code}", HTTP_AUTHORIZATION=f"Bearer {self.owner_token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(JoinCode.objects.get(code=code).revoked)

    def _codes(self, token):
        return self.client.get(
            "/api/orgs/codes", HTTP_AUTHORIZATION=f"Bearer {token}"
        )

    def test_admin_code_is_hidden_from_an_admins_list_but_visible_to_the_owner(self):
        admin_code = json.loads(self._mint(self.owner_token, "admin").content)["code"]
        admin_codes = [row["code"] for row in json.loads(self._codes(self.admin_token).content)]
        owner_codes = [row["code"] for row in json.loads(self._codes(self.owner_token).content)]
        self.assertNotIn(admin_code, admin_codes)
        self.assertIn(admin_code, owner_codes)

    def test_admin_cannot_revoke_an_admin_code(self):
        admin_code = json.loads(self._mint(self.owner_token, "admin").content)["code"]
        response = self.client.delete(
            f"/api/orgs/codes/{admin_code}", HTTP_AUTHORIZATION=f"Bearer {self.admin_token}"
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(JoinCode.objects.get(code=admin_code).revoked)

    def test_admin_can_still_list_and_revoke_member_and_viewer_codes(self):
        member_code = json.loads(self._mint(self.owner_token, "member").content)["code"]
        viewer_code = json.loads(self._mint(self.owner_token, "viewer").content)["code"]
        admin_codes = [row["code"] for row in json.loads(self._codes(self.admin_token).content)]
        self.assertIn(member_code, admin_codes)
        self.assertIn(viewer_code, admin_codes)

        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
        response = self.client.delete(f"/api/orgs/codes/{member_code}", **auth)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(JoinCode.objects.get(code=member_code).revoked)

    def test_owner_can_revoke_the_admin_code(self):
        admin_code = json.loads(self._mint(self.owner_token, "admin").content)["code"]
        response = self.client.delete(
            f"/api/orgs/codes/{admin_code}", HTTP_AUTHORIZATION=f"Bearer {self.owner_token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(JoinCode.objects.get(code=admin_code).revoked)
