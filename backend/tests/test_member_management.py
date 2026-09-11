import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# The middleware resolves org "o1" to this alias at request time (see
# apps.orgs.connections.alias_for_org), not to the static "tenant_dev" alias —
# each org gets its own dynamically-registered connection. Django's per-test
# database allowlist is computed before any test's setUp runs, so the alias
# must already exist at import time (see tests/test_org_middleware.py and
# tests/test_ledger_permissions.py, which established this pattern).
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))


class MemberManagementTests(TestCase):
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # Re-register in case an unrelated test's connection churn (the LRU
        # cap in apps.orgs.connections is process-wide) evicted it between
        # module import and here.
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.owner = self._user("u_owner", "owner")
        self.admin = self._user("u_admin", "admin")
        self.member = self._user("u_member", "member")

    def _user(self, user_id, role):
        token = create_session_token(user_id, "o1")
        with org_context(ORG_ALIAS):
            User.objects.create(
                id=user_id, username=user_id, email=f"{user_id}@example.com",
                password_hash="x", role=role, is_active=True,
            )
            Session.objects.create(
                id=f"s_{user_id}", user_id=user_id, token=token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        return token

    def _put(self, token, user_id, payload):
        return self.client.put(
            f"/api/admin/users/{user_id}",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def _delete(self, token, user_id):
        return self.client.delete(
            f"/api/admin/users/{user_id}",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def _transfer(self, token, user_id):
        return self.client.post(
            f"/api/admin/users/{user_id}/transfer-ownership",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def _reset_password(self, token, user_id):
        return self.client.post(
            f"/api/admin/users/{user_id}/reset-password",
            data=json.dumps({"newPassword": "newpass123"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    # --- role changes: CHANGE_ROLE is Owner-only ---

    def test_owner_can_change_a_role(self):
        self.assertEqual(self._put(self.owner, "u_member", {"role": "admin"}).status_code, 200)

    def test_admin_cannot_change_a_role(self):
        self.assertEqual(self._put(self.admin, "u_member", {"role": "admin"}).status_code, 403)

    def test_admin_can_deactivate_a_member(self):
        response = self._put(self.admin, "u_member", {"is_active": False})
        self.assertEqual(response.status_code, 200)

    # --- sole-Owner invariant ---

    def test_nobody_can_demote_the_only_owner(self):
        response = self._put(self.owner, "u_owner", {"role": "member"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("owner", json.loads(response.content)["error"].lower())

    def test_owner_cannot_deactivate_themselves(self):
        self.assertEqual(self._put(self.owner, "u_owner", {"is_active": False}).status_code, 400)

    def test_admin_cannot_remove_the_only_owner(self):
        response = self._delete(self.admin, "u_owner")
        self.assertEqual(response.status_code, 400)
        self.assertIn("owner", json.loads(response.content)["error"].lower())

    def test_a_role_cannot_be_set_to_owner_directly(self):
        response = self._put(self.owner, "u_member", {"role": "owner"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("transfer", json.loads(response.content)["error"].lower())

    # --- ownership transfer ---

    def test_ownership_transfer_swaps_both_roles(self):
        response = self._transfer(self.owner, "u_admin")
        self.assertEqual(response.status_code, 200, response.content)
        with org_context(ORG_ALIAS):
            self.assertEqual(User.objects.get(id="u_admin").role, "owner")
            self.assertEqual(User.objects.get(id="u_owner").role, "admin")

    def test_admin_cannot_transfer_ownership(self):
        self.assertEqual(self._transfer(self.admin, "u_admin").status_code, 403)

    def test_once_a_second_owner_exists_the_original_can_be_demoted(self):
        # Transfer first: u_admin becomes Owner, u_owner becomes Admin. The
        # sole-Owner guard now protects u_admin, not u_owner, so u_owner (now
        # just an Admin) can be freely deactivated by the new Owner.
        self.assertEqual(self._transfer(self.owner, "u_admin").status_code, 200)
        response = self._put(self.admin, "u_owner", {"is_active": False})
        self.assertEqual(response.status_code, 200, response.content)

    def test_once_a_second_owner_exists_the_original_can_be_removed(self):
        self.assertEqual(self._transfer(self.owner, "u_admin").status_code, 200)
        response = self._delete(self.admin, "u_owner")
        self.assertEqual(response.status_code, 200, response.content)

    # --- member list / password reset: MANAGE_MEMBERS ---

    def test_member_list_requires_manage_members(self):
        response = self.client.get(
            "/api/admin/users", HTTP_AUTHORIZATION=f"Bearer {self.member}"
        )
        self.assertEqual(response.status_code, 403)

    def test_member_list_allowed_for_admin_and_owner(self):
        for token in (self.admin, self.owner):
            response = self.client.get(
                "/api/admin/users", HTTP_AUTHORIZATION=f"Bearer {token}"
            )
            self.assertEqual(response.status_code, 200)

    def test_password_reset_requires_manage_members(self):
        self.assertEqual(self._reset_password(self.member, "u_admin").status_code, 403)

    def test_admin_can_reset_a_members_password(self):
        self.assertEqual(self._reset_password(self.admin, "u_member").status_code, 200)
