import json
from datetime import timedelta
from unittest import mock

from django.db import connections
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.ledger import views as ledger_views
from apps.ledger.models import DatabaseFund, RecurringTransaction, TransactionFund
from apps.orgs.context import org_context
from apps.orgs.models import Org
from tests.support import ORG_ALIAS, OrgTestMixin, TENANT_URL


class LedgerMoneyTests(OrgTestMixin, TestCase):
    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        token = self.make_user("u_owner", "owner", username="owner", email="o@example.com")
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        with org_context(ORG_ALIAS):
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

    def test_amount_above_the_cap_has_its_own_message(self):
        # A user who typed 2e12 gets told about the cap, not "greater than 0".
        response = self._post_txn("credit", 2e12)
        self.assertEqual(response.status_code, 400, response.content)
        message = response.json()["error"]
        self.assertIn("₹", message)
        self.assertIn("1,000,000,000,000", message)
        self.assertNotEqual(message, "Amount must be greater than 0")

    def test_update_refuses_an_over_cap_amount(self):
        with org_context(ORG_ALIAS):
            TransactionFund.objects.create(
                id="t1", database_id="f1", type="credit", amount=10.0,
                date=timezone.now(), mode="cash", running_balance=10.0,
            )
            DatabaseFund.objects.filter(id="f1").update(balance=10.0)
        response = self._send("put", "/api/transactions/t1", {"amount": 2e12})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("₹", response.json()["error"])
        with org_context(ORG_ALIAS):
            self.assertEqual(TransactionFund.objects.get(id="t1").amount, 10.0)
        self.assertEqual(self._fund().balance, 10.0)

    def test_recurring_create_refuses_an_over_cap_amount(self):
        response = self._send("post", "/api/databases/f1/recurring", {
            "type": "credit", "amount": 2e12, "frequency": "monthly",
            "description": "rent", "nextRun": "2030-01-01",
        })
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("₹", response.json()["error"])
        with org_context(ORG_ALIAS):
            self.assertFalse(RecurringTransaction.objects.exists())
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

    def test_zero_amount_message_is_exact(self):
        with org_context(ORG_ALIAS):
            TransactionFund.objects.create(
                id="t1", database_id="f1", type="credit", amount=10.0,
                date=timezone.now(), mode="cash", running_balance=10.0,
            )
        response = self._post_txn("credit", 0)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Amount must be greater than 0")
        response = self._send("put", "/api/transactions/t1", {"amount": 0})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Amount must be greater than 0")

    def test_approve_re_checks_the_transaction_under_lock(self):
        # The view reads the transaction row before it takes the fund lock.
        # If an Admin's PUT edits that pending debit's amount in the window
        # between that read and the lock, approving against the stale copy
        # would pass the balance check on the old (smaller) amount and then
        # recalculate_running_balances would rebuild from the *edited*
        # (larger) amount actually in the database, driving the fund negative.
        real_lock_fund = ledger_views.lock_fund

        def edit_then_lock(database_id):
            # Simulate that concurrent edit landing right here: after this
            # view's first, lock-free read of the transaction, before the
            # fund lock below.
            TransactionFund.objects.filter(id="t1").update(amount=2000.0)
            return real_lock_fund(database_id)

        with org_context(ORG_ALIAS):
            TransactionFund.objects.create(
                id="t1", database_id="f1", type="debit", amount=30.0,
                date=timezone.now(), mode="cash", running_balance=0.0,
                requires_approval=True, approved=False, created_by_id="u_owner",
            )
            DatabaseFund.objects.filter(id="f1").update(balance=1000.0)

        with mock.patch("apps.ledger.views.lock_fund", side_effect=edit_then_lock):
            response = self._send("post", "/api/transactions/t1/approve", {})

        self.assertEqual(response.status_code, 400, response.content)
        with org_context(ORG_ALIAS):
            txn = TransactionFund.objects.get(id="t1")
        self.assertFalse(txn.approved)
        self.assertGreaterEqual(self._fund().balance, 0)
        self.assertEqual(self._fund().balance, 1000.0)

    def test_cannot_post_a_transaction_to_an_archived_fund(self):
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.filter(id="f1").update(is_archived=True)
        response = self._post_txn("credit", 10)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"], "This fund is archived")

    def test_cannot_post_a_transaction_that_is_archived_while_waiting_on_the_lock(self):
        # The pre-lock is_archived check above is only a fast path. If a merge
        # (or an archive toggle) commits while this POST is waiting on
        # lock_fund(), the fund is archived by the time the lock is granted.
        # Without a re-check under the lock, the post lands in a fund that is
        # now archived -- exactly the orphan the merge lock was meant to close.
        real_lock_fund = ledger_views.lock_fund

        def archive_then_lock(database_id):
            fund = real_lock_fund(database_id)
            DatabaseFund.objects.filter(id=database_id).update(is_archived=True)
            fund.is_archived = True
            return fund

        with mock.patch("apps.ledger.views.lock_fund", side_effect=archive_then_lock):
            response = self._post_txn("credit", 10)

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"], "This fund is archived")
        with org_context(ORG_ALIAS):
            self.assertEqual(TransactionFund.objects.filter(database_id="f1").count(), 0)
        self.assertEqual(self._fund().balance, 0.0)
