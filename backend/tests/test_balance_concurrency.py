import threading

from django.db import connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.ledger.models import DatabaseFund, TransactionFund
from apps.orgs.context import org_context


def _post_debit(fund_id, amount, barrier, errors):
    """Mimic the view's read-decide-write sequence in a real thread."""
    try:
        # Force both threads to attempt the locked read together. This must
        # happen *before* select_for_update() is issued: once a real Postgres
        # row lock is held, the second thread's own SELECT ... FOR UPDATE
        # blocks at the database level and can't reach a later rendezvous --
        # waiting on the barrier from inside the lock would deadlock the two
        # threads against each other (proven empirically while writing this
        # test: the first thread times out waiting for the second, which is
        # itself still blocked acquiring the lock the first thread holds).
        barrier.wait(timeout=5)
        with org_context("tenant_dev"), transaction.atomic(using="tenant_dev"):
            fund = (
                DatabaseFund.objects
                .select_for_update()
                .get(id=fund_id)
            )
            if amount > fund.balance:
                return
            new_balance = fund.balance - amount
            TransactionFund.objects.create(
                id=f"txn-{threading.get_ident()}",
                database_id=fund_id,
                type="debit",
                amount=amount,
                date=timezone.now(),
                mode="cash",
                running_balance=new_balance,
            )
            fund.balance = new_balance
            fund.save(update_fields=["balance"])
    except Exception as exc:  # surfaced in the assertion below
        errors.append(exc)
    finally:
        connections["tenant_dev"].close()


class ConcurrentDebitTests(TransactionTestCase):
    databases = {"tenant_dev"}

    def test_two_concurrent_debits_cannot_overdraw(self):
        with org_context("tenant_dev"):
            user = User.objects.create(
                id="u1", username="a", email="a@example.com", password_hash="x"
            )
            fund = DatabaseFund.objects.create(
                id="f1", created_by=user, name="Fund", balance=100.0
            )

        barrier = threading.Barrier(2)
        errors = []
        threads = [
            threading.Thread(target=_post_debit, args=(fund.id, 80.0, barrier, errors))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"threads raised: {errors}")
        with org_context("tenant_dev"):
            fund.refresh_from_db()
            posted = TransactionFund.objects.count()
        self.assertGreaterEqual(
            fund.balance,
            0,
            "two 80.00 debits against a 100.00 balance overdrew the fund",
        )
        self.assertEqual(posted, 1, "only one of the two debits should have succeeded")
