import io
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.ledger.models import AuditLog, DatabaseFund, RecurringTransaction, TransactionFund, TrashItem
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# The middleware resolves org "o1" to this alias at request time (see
# apps.orgs.connections.alias_for_org), not to the static "tenant_dev" alias —
# each org gets its own dynamically-registered connection. Django's per-test
# database allowlist is computed before any test's setUp runs, so the alias
# must already exist at import time (see tests/test_org_middleware.py, which
# established this pattern). All fixture writes below go through this alias,
# matching what the HTTP requests will actually use once OrgContextMiddleware
# resolves org "o1".
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))


class LedgerPermissionTests(TestCase):
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # Re-register in case an unrelated test's connection churn (the LRU
        # cap in apps.orgs.connections is process-wide) evicted it between
        # module import and here — see test_org_middleware.py's identical
        # safeguard.
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.tokens = {
            role: self._user(f"u_{role}", role) for role in ("owner", "admin", "member", "viewer")
        }
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(
                id="f1", name="Fund", balance=1000.0, approval_threshold=500.0,
                created_by_id="u_owner",
            )

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

    def _auth(self, role):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.tokens[role]}"}

    def _post_txn(self, role, amount=100.0):
        return self.client.post(
            "/api/databases/f1/transactions",
            data=json.dumps({
                "type": "credit", "amount": amount,
                "date": timezone.now().isoformat(), "mode": "cash",
                "sender": "x", "receiver": "y", "modeData": {},
            }),
            content_type="application/json",
            **self._auth(role),
        )

    def test_viewer_can_read_funds(self):
        self.assertEqual(self.client.get("/api/databases", **self._auth("viewer")).status_code, 200)

    def test_viewer_cannot_create_a_transaction(self):
        self.assertEqual(self._post_txn("viewer").status_code, 403)

    def test_member_can_create_a_transaction(self):
        self.assertEqual(self._post_txn("member").status_code, 200)

    def test_viewer_cannot_create_a_fund(self):
        response = self.client.post(
            "/api/databases",
            data=json.dumps({"name": "New"}),
            content_type="application/json",
            **self._auth("viewer"),
        )
        self.assertEqual(response.status_code, 403)

    def test_member_cannot_create_a_fund(self):
        response = self.client.post(
            "/api/databases",
            data=json.dumps({"name": "New"}),
            content_type="application/json",
            **self._auth("member"),
        )
        self.assertEqual(response.status_code, 403)

    def test_member_cannot_void(self):
        with org_context(ORG_ALIAS):
            TransactionFund.objects.create(
                id="t1", database_id="f1", type="credit", amount=10.0,
                date=timezone.now(), mode="cash", running_balance=10.0,
            )
        response = self.client.post(
            "/api/transactions/t1/void",
            data=json.dumps({"reason": "mistake"}),
            content_type="application/json",
            **self._auth("member"),
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_void(self):
        with org_context(ORG_ALIAS):
            TransactionFund.objects.create(
                id="t2", database_id="f1", type="credit", amount=10.0,
                date=timezone.now(), mode="cash", running_balance=10.0,
            )
        response = self.client.post(
            "/api/transactions/t2/void",
            data=json.dumps({"reason": "mistake"}),
            content_type="application/json",
            **self._auth("admin"),
        )
        self.assertEqual(response.status_code, 200)


class ApprovalRuleIntegrationTests(LedgerPermissionTests):
    def test_member_transaction_over_threshold_awaits_approval(self):
        response = self._post_txn("member", amount=600.0)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)["requiresApproval"])

    def test_admin_transaction_over_threshold_posts_directly(self):
        response = self._post_txn("admin", amount=600.0)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.content)["requiresApproval"])

    def test_admin_can_approve_a_members_transaction(self):
        created = json.loads(self._post_txn("member", amount=600.0).content)
        txn_id = created["transaction"]["id"]
        response = self.client.post(f"/api/transactions/{txn_id}/approve", **self._auth("admin"))
        self.assertEqual(response.status_code, 200)

    def test_member_cannot_approve(self):
        created = json.loads(self._post_txn("member", amount=600.0).content)
        txn_id = created["transaction"]["id"]
        response = self.client.post(f"/api/transactions/{txn_id}/approve", **self._auth("member"))
        self.assertEqual(response.status_code, 403)


class ExhaustiveMutatingEndpointTests(TestCase):
    """Every mutating ledger endpoint, every role.

    The brief's "Produces" line says HTTP 403 on every mutating ledger
    endpoint for roles that lack the capability — this class checks that
    exhaustively (both the rejected roles and the allowed ones) rather than
    spot-checking a couple of endpoints, covering entries not directly
    exercised above: databases_merge, database_detail PUT/DELETE,
    database_archive, transaction_delete_voided, transaction_update,
    recurring create/delete/process, and trash restore/delete/DELETE-all.
    """

    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.tokens = {
            role: self._user(f"u_{role}", role) for role in ("owner", "admin", "member", "viewer")
        }

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

    def _auth(self, role):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.tokens[role]}"}

    def _make_fund(self, fund_id="f1"):
        with org_context(ORG_ALIAS):
            return DatabaseFund.objects.create(
                id=fund_id, name="Fund", balance=1000.0, approval_threshold=0.0,
                created_by_id="u_owner",
            )

    def _make_txn(self, txn_id="t1", fund_id="f1", **overrides):
        fields = dict(
            id=txn_id, database_id=fund_id, type="credit", amount=10.0,
            date=timezone.now(), mode="cash", running_balance=10.0,
            approved=True, requires_approval=False,
        )
        fields.update(overrides)
        with org_context(ORG_ALIAS):
            return TransactionFund.objects.create(**fields)

    def _make_recurring(self, rec_id="r1", fund_id="f1"):
        with org_context(ORG_ALIAS):
            return RecurringTransaction.objects.create(
                id=rec_id, database_id=fund_id, type="credit", amount=5.0,
                frequency="monthly", description="rent", next_run=timezone.now().date(),
                is_active=True,
            )

    def _make_trash(self, item_id="tr1", fund_id="f1", owner_id="u_owner"):
        with org_context(ORG_ALIAS):
            return TrashItem.objects.create(
                id=item_id, entity_type="database",
                entity_data=json.dumps({"id": fund_id}),
                deleted_by_id=owner_id,
            )

    # --- databases_merge: MANAGE_FUNDS -> owner/admin allowed, member/viewer denied ---

    def _merge_body(self, source, target):
        return json.dumps({"sourceId": source, "targetId": target, "name": "Merged"})

    def test_databases_merge_denied_for_member_and_viewer(self):
        for role in ("member", "viewer"):
            self._make_fund("fs_" + role)
            self._make_fund("ft_" + role)
            response = self.client.post(
                "/api/databases/merge",
                data=self._merge_body("fs_" + role, "ft_" + role),
                content_type="application/json",
                **self._auth(role),
            )
            self.assertEqual(response.status_code, 403, role)

    def test_databases_merge_allowed_for_admin_and_owner(self):
        for role in ("admin", "owner"):
            self._make_fund("fs2_" + role)
            self._make_fund("ft2_" + role)
            response = self.client.post(
                "/api/databases/merge",
                data=self._merge_body("fs2_" + role, "ft2_" + role),
                content_type="application/json",
                **self._auth(role),
            )
            self.assertEqual(response.status_code, 200, role)

    # --- database_detail PUT/DELETE: MANAGE_FUNDS ---

    def _fund_update_body(self):
        return json.dumps({"name": "Renamed"})

    def test_database_update_denied_for_member_and_viewer(self):
        for role in ("member", "viewer"):
            fund_id = "fu_" + role
            self._make_fund(fund_id)
            response = self.client.put(
                f"/api/databases/{fund_id}",
                data=self._fund_update_body(),
                content_type="application/json",
                **self._auth(role),
            )
            self.assertEqual(response.status_code, 403, role)

    def test_database_update_allowed_for_admin_and_owner(self):
        for role in ("admin", "owner"):
            fund_id = "fu2_" + role
            self._make_fund(fund_id)
            response = self.client.put(
                f"/api/databases/{fund_id}",
                data=self._fund_update_body(),
                content_type="application/json",
                **self._auth(role),
            )
            self.assertEqual(response.status_code, 200, role)

    def test_database_delete_denied_for_member_and_viewer(self):
        for role in ("member", "viewer"):
            fund_id = "fd_" + role
            self._make_fund(fund_id)
            response = self.client.delete(f"/api/databases/{fund_id}", **self._auth(role))
            self.assertEqual(response.status_code, 403, role)

    def test_database_delete_allowed_for_admin_and_owner(self):
        for role in ("admin", "owner"):
            fund_id = "fd2_" + role
            self._make_fund(fund_id)
            response = self.client.delete(f"/api/databases/{fund_id}", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)

    # --- database_archive: MANAGE_FUNDS ---

    def test_database_archive_denied_for_member_and_viewer(self):
        for role in ("member", "viewer"):
            fund_id = "fa_" + role
            self._make_fund(fund_id)
            response = self.client.post(f"/api/databases/{fund_id}/archive", **self._auth(role))
            self.assertEqual(response.status_code, 403, role)

    def test_database_archive_allowed_for_admin_and_owner(self):
        for role in ("admin", "owner"):
            fund_id = "fa2_" + role
            self._make_fund(fund_id)
            response = self.client.post(f"/api/databases/{fund_id}/archive", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)

    # --- transaction_delete_voided: MODIFY_TXN ---

    def test_delete_voided_denied_for_member_and_viewer(self):
        self._make_fund("fdv")
        for role in ("member", "viewer"):
            txn_id = "tdv_" + role
            self._make_txn(txn_id, "fdv", is_voided=True)
            response = self.client.delete(f"/api/transactions/{txn_id}/delete", **self._auth(role))
            self.assertEqual(response.status_code, 403, role)

    def test_delete_voided_allowed_for_admin_and_owner(self):
        self._make_fund("fdv2")
        for role in ("admin", "owner"):
            txn_id = "tdv2_" + role
            self._make_txn(txn_id, "fdv2", is_voided=True)
            response = self.client.delete(f"/api/transactions/{txn_id}/delete", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)

    # --- transaction_update: MODIFY_TXN ---

    def test_transaction_update_denied_for_member_and_viewer(self):
        self._make_fund("ftu")
        for role in ("member", "viewer"):
            txn_id = "ttu_" + role
            self._make_txn(txn_id, "ftu")
            response = self.client.put(
                f"/api/transactions/{txn_id}",
                data=json.dumps({"amount": 20.0}),
                content_type="application/json",
                **self._auth(role),
            )
            self.assertEqual(response.status_code, 403, role)

    def test_transaction_update_allowed_for_admin_and_owner(self):
        self._make_fund("ftu2")
        for role in ("admin", "owner"):
            txn_id = "ttu2_" + role
            self._make_txn(txn_id, "ftu2")
            response = self.client.put(
                f"/api/transactions/{txn_id}",
                data=json.dumps({"amount": 20.0}),
                content_type="application/json",
                **self._auth(role),
            )
            self.assertEqual(response.status_code, 200, role)

    # --- recurring_list_create POST: MANAGE_FUNDS ---

    def _recurring_body(self):
        return json.dumps({
            "type": "credit", "amount": 5.0, "frequency": "monthly",
            "description": "rent", "nextRun": timezone.now().date().isoformat(),
        })

    def test_recurring_create_denied_for_member_and_viewer(self):
        for role in ("member", "viewer"):
            fund_id = "frc_" + role
            self._make_fund(fund_id)
            response = self.client.post(
                f"/api/databases/{fund_id}/recurring",
                data=self._recurring_body(),
                content_type="application/json",
                **self._auth(role),
            )
            self.assertEqual(response.status_code, 403, role)

    def test_recurring_create_allowed_for_admin_and_owner(self):
        for role in ("admin", "owner"):
            fund_id = "frc2_" + role
            self._make_fund(fund_id)
            response = self.client.post(
                f"/api/databases/{fund_id}/recurring",
                data=self._recurring_body(),
                content_type="application/json",
                **self._auth(role),
            )
            self.assertEqual(response.status_code, 200, role)

    # --- recurring_delete: MANAGE_FUNDS ---

    def test_recurring_delete_denied_for_member_and_viewer(self):
        self._make_fund("frd")
        for role in ("member", "viewer"):
            rec_id = "rrd_" + role
            self._make_recurring(rec_id, "frd")
            response = self.client.delete(f"/api/recurring/{rec_id}", **self._auth(role))
            self.assertEqual(response.status_code, 403, role)

    def test_recurring_delete_allowed_for_admin_and_owner(self):
        self._make_fund("frd2")
        for role in ("admin", "owner"):
            rec_id = "rrd2_" + role
            self._make_recurring(rec_id, "frd2")
            response = self.client.delete(f"/api/recurring/{rec_id}", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)

    # --- recurring_process: not in the brief's table; flagged and gated as
    # CREATE_TXN since it auto-posts TransactionFund rows on the caller's
    # behalf, same as database_transactions' POST branch. ---

    def test_recurring_process_denied_for_viewer(self):
        response = self.client.post("/api/recurring/process", **self._auth("viewer"))
        self.assertEqual(response.status_code, 403)

    def test_recurring_process_allowed_for_member_admin_owner(self):
        for role in ("member", "admin", "owner"):
            response = self.client.post("/api/recurring/process", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)

    # --- trash_restore: MANAGE_FUNDS ---

    def test_trash_restore_denied_for_member_and_viewer(self):
        for role in ("member", "viewer"):
            fund_id = "ftr_" + role
            item_id = "itr_" + role
            self._make_fund(fund_id)
            self._make_trash(item_id, fund_id, owner_id=f"u_{role}")
            response = self.client.post(f"/api/trash/{item_id}/restore", **self._auth(role))
            self.assertEqual(response.status_code, 403, role)

    def test_trash_restore_allowed_for_admin_and_owner(self):
        for role in ("admin", "owner"):
            fund_id = "ftr2_" + role
            item_id = "itr2_" + role
            self._make_fund(fund_id)
            self._make_trash(item_id, fund_id, owner_id=f"u_{role}")
            response = self.client.post(f"/api/trash/{item_id}/restore", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)

    # --- trash_delete: MANAGE_FUNDS ---

    def test_trash_delete_denied_for_member_and_viewer(self):
        for role in ("member", "viewer"):
            fund_id = "ftd_" + role
            item_id = "itd_" + role
            self._make_fund(fund_id)
            self._make_trash(item_id, fund_id, owner_id=f"u_{role}")
            response = self.client.delete(f"/api/trash/{item_id}", **self._auth(role))
            self.assertEqual(response.status_code, 403, role)

    def test_trash_delete_allowed_for_admin_and_owner(self):
        for role in ("admin", "owner"):
            fund_id = "ftd2_" + role
            item_id = "itd2_" + role
            self._make_fund(fund_id)
            self._make_trash(item_id, fund_id, owner_id=f"u_{role}")
            response = self.client.delete(f"/api/trash/{item_id}", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)

    # --- trash_list DELETE branch (delete-all): MANAGE_FUNDS ---

    def test_trash_delete_all_denied_for_member_and_viewer(self):
        for role in ("member", "viewer"):
            response = self.client.delete("/api/trash", **self._auth(role))
            self.assertEqual(response.status_code, 403, role)

    def test_trash_delete_all_allowed_for_admin_and_owner(self):
        for role in ("admin", "owner"):
            response = self.client.delete("/api/trash", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)

    # --- extract_receipt: CREATE_TXN (guards external paid AI-extraction
    # API quota, not a DB write) ---

    def test_extract_receipt_denied_for_viewer(self):
        response = self.client.post("/api/extract-receipt", **self._auth("viewer"))
        self.assertEqual(response.status_code, 403)

    def test_extract_receipt_allowed_for_member_and_above(self):
        # No ai_config on this org, so extraction itself reports 503 ("not
        # configured") — the point of this test is only that the permission
        # gate doesn't turn these roles away with a 403 first.
        for role in ("member", "admin", "owner"):
            image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            image.name = "receipt.png"
            response = self.client.post(
                "/api/extract-receipt", data={"image": image}, **self._auth(role)
            )
            self.assertNotEqual(response.status_code, 403, role)

    # --- trash_list GET branch: no guard, every role can read ---

    def test_trash_list_get_allowed_for_every_role(self):
        for role in ("owner", "admin", "member", "viewer"):
            response = self.client.get("/api/trash", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)


class AuditAndTrashOrgScopingTests(TestCase):
    """Regression coverage for a bug where audit_list and trash_list's GET
    (and trash_list's bulk-DELETE) branches filtered by
    user_id/deleted_by_id=request.fv_user.id -- silently scoping every org
    member's view down to only their own actions instead of the whole org.
    That contradicts the multi-tenant design used everywhere else in this
    file: the tenant connection IS the org boundary, so no per-user
    ownership filter belongs here. Two users in the same org each write one
    audit entry / trash item, then either user must see BOTH."""

    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # See LedgerPermissionTests.setUpClass above for why this
        # re-registration is needed.
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.tokens = {
            role: self._user(f"u_{role}", role) for role in ("owner", "member")
        }

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

    def _auth(self, role):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.tokens[role]}"}

    def test_audit_list_shows_both_users_entries_to_either_user(self):
        with org_context(ORG_ALIAS):
            AuditLog.objects.create(
                id="a_owner", user_id="u_owner", action="create",
                entity_type="database", entity_id="f_owner", details="owner's action",
            )
            AuditLog.objects.create(
                id="a_member", user_id="u_member", action="create",
                entity_type="database", entity_id="f_member", details="member's action",
            )

        for role in ("owner", "member"):
            response = self.client.get("/api/audit", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)
            ids = {entry["id"] for entry in json.loads(response.content)}
            self.assertEqual({"a_owner", "a_member"}, ids, role)

    def test_trash_list_get_shows_both_users_deletions_to_either_user(self):
        with org_context(ORG_ALIAS):
            TrashItem.objects.create(
                id="tr_owner", entity_type="database",
                entity_data=json.dumps({"id": "f_owner"}), deleted_by_id="u_owner",
            )
            TrashItem.objects.create(
                id="tr_member", entity_type="database",
                entity_data=json.dumps({"id": "f_member"}), deleted_by_id="u_member",
            )

        for role in ("owner", "member"):
            response = self.client.get("/api/trash", **self._auth(role))
            self.assertEqual(response.status_code, 200, role)
            ids = {item["id"] for item in json.loads(response.content)}
            self.assertEqual({"tr_owner", "tr_member"}, ids, role)

    def test_trash_bulk_delete_empties_every_users_trash(self):
        with org_context(ORG_ALIAS):
            TrashItem.objects.create(
                id="trd_owner", entity_type="database",
                entity_data=json.dumps({"id": "fd_owner"}), deleted_by_id="u_owner",
            )
            TrashItem.objects.create(
                id="trd_member", entity_type="database",
                entity_data=json.dumps({"id": "fd_member"}), deleted_by_id="u_member",
            )

        # Owner has MANAGE_FUNDS, so this is the role allowed to bulk-empty
        # trash (see test_trash_delete_all_* above) -- it must clear the
        # member's trash item too, not just its own.
        response = self.client.delete("/api/trash", **self._auth("owner"))
        self.assertEqual(response.status_code, 200)
        with org_context(ORG_ALIAS):
            self.assertEqual(TrashItem.objects.count(), 0)

    def test_trash_restore_by_admin_succeeds_for_item_deleted_by_different_member(self):
        # Same bug class as above, but for the single-item endpoints: they
        # additionally filtered by deleted_by_id=request.fv_user.id, so an
        # owner/admin could not restore a specific item a different member
        # had deleted -- it 404'd as if the item didn't exist.
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(
                id="f_cross", name="Fund", balance=1000.0, approval_threshold=0.0,
                created_by_id="u_member", is_deleted=True,
            )
            TrashItem.objects.create(
                id="tr_cross", entity_type="database",
                entity_data=json.dumps({"id": "f_cross"}), deleted_by_id="u_member",
            )

        response = self.client.post("/api/trash/tr_cross/restore", **self._auth("owner"))
        self.assertEqual(response.status_code, 200)
        with org_context(ORG_ALIAS):
            self.assertFalse(TrashItem.objects.filter(id="tr_cross").exists())
            self.assertFalse(DatabaseFund.objects.get(id="f_cross").is_deleted)

    def test_trash_delete_by_admin_succeeds_for_item_deleted_by_different_member(self):
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(
                id="f_cross2", name="Fund", balance=1000.0, approval_threshold=0.0,
                created_by_id="u_member", is_deleted=True,
            )
            TrashItem.objects.create(
                id="tr_cross2", entity_type="database",
                entity_data=json.dumps({"id": "f_cross2"}), deleted_by_id="u_member",
            )

        response = self.client.delete("/api/trash/tr_cross2", **self._auth("owner"))
        self.assertEqual(response.status_code, 200)
        with org_context(ORG_ALIAS):
            self.assertFalse(TrashItem.objects.filter(id="tr_cross2").exists())
            self.assertFalse(DatabaseFund.objects.filter(id="f_cross2").exists())
