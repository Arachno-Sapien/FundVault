import json
from datetime import timedelta

from django.db import connections
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.ledger.models import DatabaseFund, RecurringTransaction, TransactionFund
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# See tests/test_ledger_permissions.py for why this alias must exist at import
# time: Django computes each TestCase's database allowlist before setUp runs.
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))


class LedgerMoneyTests(TestCase):
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
        token = create_session_token("u_owner", "o1")
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        with org_context(ORG_ALIAS):
            User.objects.create(
                id="u_owner", username="owner", email="o@example.com",
                password_hash="x", role="owner", is_active=True,
            )
            Session.objects.create(
                id="s_owner", user_id="u_owner", token=token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
            DatabaseFund.objects.create(id="f1", name="Fund", created_by_id="u_owner")

    def _send(self, method, url, body):
        raw = body if isinstance(body, str) else json.dumps(body)
        return getattr(self.client, method)(url, data=raw, content_type="application/json", **self.auth)

    def _post_txn(self, tx_type, amount, date=None):
        return self._send("post", "/api/databases/f1/transactions", {
            "type": tx_type, "amount": amount, "mode": "cash",
            "date": (date or timezone.now()).isoformat(),
        })

    def _fund(self):
        with org_context(ORG_ALIAS):
            return DatabaseFund.objects.get(id="f1")

    def test_non_finite_or_garbage_numbers_are_400(self):
        with org_context(ORG_ALIAS):
            TransactionFund.objects.create(
                id="t1", database_id="f1", type="credit", amount=10.0,
                date=timezone.now(), mode="cash", running_balance=10.0,
            )
        for bad in ("nan", "NaN", "inf", "-inf", "Infinity", "abc", True):
            with self.subTest(bad=bad):
                self.assertEqual(self._post_txn("credit", bad).status_code, 400)
                self.assertEqual(
                    self._send("put", "/api/transactions/t1", {"amount": bad}).status_code, 400
                )
                for field in ("lowBalanceThreshold", "approvalThreshold"):
                    self.assertEqual(
                        self._send("post", "/api/databases", {"name": "N", field: bad}).status_code, 400
                    )
                    self.assertEqual(
                        self._send("put", "/api/databases/f1", {"name": "N", field: bad}).status_code, 400
                    )
                self.assertEqual(self._send("post", "/api/databases/f1/recurring", {
                    "type": "credit", "amount": bad, "frequency": "monthly",
                    "description": "rent", "nextRun": "2030-01-01",
                }).status_code, 400)
        self.assertEqual(self._fund().balance, 0.0)
        with org_context(ORG_ALIAS):
            self.assertEqual(TransactionFund.objects.get(id="t1").amount, 10.0)
            self.assertFalse(RecurringTransaction.objects.exists())

    def test_amount_above_the_cap_is_400(self):
        # Two finite credits this large would add past a float's max and turn
        # the fund's balance (and the org's JSON) into Infinity.
        with self.subTest(amount=1e308):
            response = self._post_txn("credit", 1e308)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self._fund().balance, 0.0)

    def test_json_array_body_is_400(self):
        self.assertEqual(self._send("post", "/api/databases/f1/transactions", "[1, 2]").status_code, 400)
        self.assertEqual(self._send("post", "/api/databases", "[]").status_code, 400)

    def test_float_drift_does_not_trip_insufficient_balance(self):
        # 0.3 - 0.1 is 0.19999999999999998 in binary floating point.
        self.assertEqual(self._post_txn("credit", 0.3).status_code, 200)
        self.assertEqual(self._post_txn("debit", 0.1).status_code, 200)
        response = self._post_txn("debit", 0.2)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self._fund().balance, 0.0)

    def test_amounts_are_rounded_to_paise(self):
        response = self._post_txn("credit", "10.456")
        self.assertEqual(response.json()["transaction"]["amount"], 10.46)
        self.assertEqual(self._fund().balance, 10.46)

    def test_backdated_create_fixes_every_running_balance(self):
        now = timezone.now()
        for i in range(3):
            self._post_txn("credit", 10, now - timedelta(days=3 - i))
        self._post_txn("credit", 1000, now - timedelta(days=30))
        with org_context(ORG_ALIAS):
            rows = list(
                TransactionFund.objects.filter(database_id="f1")
                .order_by("date").values_list("amount", "running_balance")
            )
        self.assertEqual(rows, [(1000, 1000), (10, 1010), (10, 1020), (10, 1030)])
        self.assertEqual(self._fund().balance, 1030)

    def test_voiding_the_earliest_of_many_is_a_bounded_number_of_queries(self):
        start = timezone.now() - timedelta(days=1000)
        with org_context(ORG_ALIAS):
            TransactionFund.objects.bulk_create([
                TransactionFund(
                    id=f"t{i:04d}", database_id="f1", type="credit", amount=1.0,
                    date=start + timedelta(days=i), mode="cash", running_balance=i + 1.0,
                )
                for i in range(500)
            ])
            DatabaseFund.objects.filter(id="f1").update(balance=500.0)
        with CaptureQueriesContext(connections[ORG_ALIAS]) as queries:
            response = self._send("post", "/api/transactions/t0000/void", {"reason": "dup"})
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(queries), 30, f"{len(queries)} tenant queries")
        self.assertEqual(self._fund().balance, 499.0)
        with org_context(ORG_ALIAS):
            self.assertEqual(TransactionFund.objects.get(id="t0499").running_balance, 499.0)
