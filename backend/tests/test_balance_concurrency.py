import threading
import time
from datetime import timedelta

from django.db import connections
from django.test import Client, TransactionTestCase
from django.utils import timezone

from apps.ledger.models import DatabaseFund, TransactionFund
from apps.orgs.context import org_context
from apps.orgs.models import Org
from tests.support import ORG_ALIAS, OrgTestMixin, TENANT_URL


def _request(method, url, body, token, results, errors, before=None):
    """Fire one real HTTP request from its own thread (own DB connections)."""
    try:
        if before:
            before()
        response = getattr(Client(), method)(
            url, data=body, content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        results.append(response)
    except Exception as exc:  # surfaced in the assertions below
        errors.append(exc)
    finally:
        connections["default"].close()
        connections[ORG_ALIAS].close()


class BalanceConcurrencyTests(OrgTestMixin, TransactionTestCase):
    def setUp(self):
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.token = self.make_user("u_owner", "owner", username="owner", email="o@example.com")
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="f1", created_by_id="u_owner", name="Fund", balance=100.0)
            TransactionFund.objects.create(
                id="t0", database_id="f1", type="credit", amount=100.0,
                date=timezone.now() - timedelta(days=1), mode="cash", running_balance=100.0,
            )

    def _fund_balance(self):
        with org_context(ORG_ALIAS):
            return DatabaseFund.objects.get(id="f1").balance

    def test_two_concurrent_debits_cannot_overdraw(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        body = {"type": "debit", "amount": 80, "mode": "cash", "date": timezone.now().isoformat()}
        threads = [
            threading.Thread(target=_request, args=(
                "post", "/api/databases/f1/transactions", body, self.token, results, errors,
                lambda: barrier.wait(timeout=5),
            ))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(errors, [], f"threads raised: {errors}")
        self.assertEqual(sorted(r.status_code for r in results), [200, 400])
        self.assertEqual(self._fund_balance(), 20.0)

    def test_void_racing_a_create_loses_neither(self):
        # Hold the void just before it writes the fund balance -- after it has
        # read the fund's rows -- and let a credit be posted in that window.
        # Unless the void holds the fund lock for its whole transaction, the
        # credit commits in between and the void then overwrites the balance
        # with a total that never saw it.
        void_has_read = threading.Event()

        def slow_fund_write(execute, sql, params, many, context):
            if sql.startswith('UPDATE "databases"') and '"balance"' in sql:
                void_has_read.set()
                time.sleep(0.5)
            return execute(sql, params, many, context)

        def void():
            with connections[ORG_ALIAS].execute_wrapper(slow_fund_write):
                _request("post", "/api/transactions/t0/void", {"reason": "dup"},
                         self.token, results, errors)

        results, errors = [], []
        credit = {"type": "credit", "amount": 5, "mode": "cash", "date": timezone.now().isoformat()}
        threads = [
            threading.Thread(target=void),
            threading.Thread(target=_request, args=(
                "post", "/api/databases/f1/transactions", credit, self.token, results, errors,
                lambda: void_has_read.wait(timeout=5),
            )),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertTrue(void_has_read.is_set(), "the void never reached its balance write")
        self.assertEqual(errors, [], f"threads raised: {errors}")
        self.assertEqual([r.status_code for r in results], [200, 200])
        self.assertEqual(self._fund_balance(), 5.0, "the void overwrote the concurrent credit")
        with org_context(ORG_ALIAS):
            credit_row = TransactionFund.objects.get(database_id="f1", amount=5.0)
        self.assertEqual(credit_row.running_balance, 5.0)
