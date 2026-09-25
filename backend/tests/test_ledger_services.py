import io
import json
from datetime import timedelta
from unittest import mock

from PIL import Image

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.ledger.models import DatabaseFund, RecurringTransaction, TransactionFund
from apps.ledger.services import process_due_recurring
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# See tests/test_ledger_permissions.py for why this alias must exist at import
# time: Django computes each TestCase's database allowlist before setUp runs.
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))


class LedgerServicesTests(TestCase):
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
        self.owner_token = self._user("u_owner", "owner")
        self.owner_auth = {"HTTP_AUTHORIZATION": f"Bearer {self.owner_token}"}

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

    def test_overview_counts_in_two_queries(self):
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="f1", name="Fund 1", balance=200.0, created_by_id="u_owner")
            DatabaseFund.objects.create(id="f2", name="Fund 2", balance=75.0, created_by_id="u_owner")
            DatabaseFund.objects.create(
                id="f3", name="Fund 3", balance=999.0, is_deleted=True, created_by_id="u_owner",
            )
            now = timezone.now()
            rows = [
                # Fund 1: counted (4 non-voided + 1 voided = 5 rows).
                ("t1", "f1", "credit", 100.0, False),
                ("t2", "f1", "credit", 50.0, False),
                ("t3", "f1", "debit", 20.0, False),
                ("t4", "f1", "credit", 30.0, False),
                ("t5", "f1", "credit", 999.0, True),  # voided -- excluded from totals
                # Fund 2: counted (4 rows).
                ("t6", "f2", "credit", 40.0, False),
                ("t7", "f2", "debit", 10.0, False),
                ("t8", "f2", "credit", 5.0, False),
                ("t9", "f2", "debit", 5.0, False),
                # Fund 3 is deleted: excluded from totals entirely (1 row).
                ("t10", "f3", "credit", 500.0, False),
            ]
            self.assertEqual(len(rows), 10)
            for txn_id, fund_id, tx_type, amount, voided in rows:
                TransactionFund.objects.create(
                    id=txn_id, database_id=fund_id, type=tx_type, amount=amount,
                    date=now, mode="cash", running_balance=amount, is_voided=voided,
                )

        with CaptureQueriesContext(connections[ORG_ALIAS]) as queries:
            response = self.client.get("/api/analytics/overview", **self.owner_auth)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["totalDatabases"], 2)
        self.assertEqual(body["totalBalance"], 275.0)
        self.assertEqual(body["totalCredits"], 225.0)
        self.assertEqual(body["totalDebits"], 35.0)

        ledger_queries = [
            q for q in queries.captured_queries
            if "databases" in q["sql"] or "transactions" in q["sql"]
        ]
        self.assertLessEqual(len(ledger_queries), 2, ledger_queries)

    def test_overview_with_no_funds_has_the_same_four_keys(self):
        response = self.client.get("/api/analytics/overview", **self.owner_auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"totalDatabases", "totalBalance", "totalCredits", "totalDebits"})
        self.assertEqual(body["totalDatabases"], 0)
        self.assertEqual(body["totalBalance"], 0)
        self.assertEqual(body["totalCredits"], 0)
        self.assertEqual(body["totalDebits"], 0)

    def test_extract_receipt_without_ai_config_is_503_and_calls_nothing(self):
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), color="red").save(buf, format="PNG")
        upload = SimpleUploadedFile("receipt.png", buf.getvalue(), content_type="image/png")

        with mock.patch("apps.ledger.receipt_extractor.extract_from_receipt_image") as extract:
            response = self.client.post(
                "/api/extract-receipt", {"image": upload}, **self.owner_auth
            )
        self.assertEqual(response.status_code, 503, response.content)
        extract.assert_not_called()

    def test_recurring_and_manual_posts_share_rules(self):
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(
                id="f1", name="Fund", balance=1000.0, approval_threshold=500.0,
                created_by_id="u_owner",
            )
            # An opening credit backs the balance above: recalculate_running_balances
            # rebuilds a fund's balance from its approved rows on every approved post.
            TransactionFund.objects.create(
                id="t_opening", database_id="f1", type="credit", amount=1000.0,
                date=timezone.now() - timedelta(days=365), mode="cash",
                running_balance=1000.0, approved=True, created_by_id="u_owner",
            )
        member_token = self._user("u_member", "member")

        # A Member's manual transaction at or above the threshold awaits approval.
        response = self.client.post(
            "/api/databases/f1/transactions",
            data=json.dumps({
                "type": "credit", "amount": 600.0, "mode": "cash",
                "date": timezone.now().isoformat(),
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {member_token}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["requiresApproval"])
        with org_context(ORG_ALIAS):
            self.assertEqual(DatabaseFund.objects.get(id="f1").balance, 1000.0)

        # A recurring rule created by an Admin, who is later demoted to
        # Member, is gated exactly as a Member's manual transaction would be.
        self._user("u_admin", "admin")
        with org_context(ORG_ALIAS):
            RecurringTransaction.objects.create(
                id="r1", database_id="f1", type="credit", amount=600.0,
                frequency="monthly", description="rent",
                next_run=timezone.now().date(), is_active=True,
                created_by_id="u_admin",
            )
            User.objects.filter(id="u_admin").update(role=User.Role.MEMBER)
            owner = User.objects.get(id="u_owner")
            created = process_due_recurring(owner)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].requires_approval)
        self.assertFalse(created[0].approved)
        with org_context(ORG_ALIAS):
            self.assertEqual(DatabaseFund.objects.get(id="f1").balance, 1000.0)

    def test_merge_response_carries_the_real_balance(self):
        # databases_merge builds its response from the in-memory `merged`
        # object; recalculate_running_balances writes the real balance with a
        # queryset .update(), which that object never sees on its own.
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="src", name="Source", created_by_id="u_owner")
            DatabaseFund.objects.create(id="tgt", name="Target", created_by_id="u_owner")
            now = timezone.now()
            TransactionFund.objects.create(
                id="ts1", database_id="src", type="credit", amount=100.0,
                date=now, mode="cash", running_balance=100.0,
                approved=True, requires_approval=False,
            )
            TransactionFund.objects.create(
                id="tt1", database_id="tgt", type="credit", amount=50.0,
                date=now, mode="cash", running_balance=50.0,
                approved=True, requires_approval=False,
            )
        response = self.client.post(
            "/api/databases/merge",
            data=json.dumps({"sourceId": "src", "targetId": "tgt", "name": "Merged"}),
            content_type="application/json", **self.owner_auth,
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["balance"], 150.0)
        with org_context(ORG_ALIAS):
            merged_id = body["id"]
            self.assertEqual(DatabaseFund.objects.get(id=merged_id).balance, 150.0)
