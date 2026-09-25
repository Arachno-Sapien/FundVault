import threading
import time
from datetime import timedelta
from unittest import mock

from django.db import connections
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.common.audit import add_audit
from apps.ledger.models import DatabaseFund, RecurringTransaction, TransactionFund
from apps.ledger.services import process_due_recurring
from apps.orgs.context import org_context


def _slow_add_audit(*args, **kwargs):
    # add_audit runs inside process_due_recurring's atomic block, after the
    # posting and before the commit. Holding the transaction open here
    # guarantees the other thread reads the due list while this one's post
    # is still uncommitted -- the exact window two overlapping app loads hit.
    time.sleep(0.3)
    return add_audit(*args, **kwargs)


def _process(user, barrier, errors):
    try:
        # Rendezvous before any lock is taken -- see tests.test_balance_concurrency
        # for why waiting after the lock would deadlock the two threads.
        barrier.wait(timeout=5)
        with org_context("tenant_dev"):
            process_due_recurring(user)
    except Exception as exc:  # surfaced in the assertion below
        errors.append(exc)
    finally:
        connections["tenant_dev"].close()


class ConcurrentRecurringProcessTests(TransactionTestCase):
    databases = {"tenant_dev"}

    @mock.patch("apps.ledger.services.add_audit", side_effect=_slow_add_audit)
    def test_two_overlapping_runs_post_a_due_rule_once(self, _audit):
        with org_context("tenant_dev"):
            user = User.objects.create(
                id="u1", username="a", email="a@example.com", password_hash="x",
                role=User.Role.OWNER,
            )
            DatabaseFund.objects.create(id="f1", created_by=user, name="Fund", balance=100.0)
            # An opening credit backs the balance above: process_due_recurring now
            # posts through services.post_transaction, which rebuilds the fund's
            # balance from its approved rows (recalculate_running_balances) instead
            # of trusting a balance with no rows behind it.
            TransactionFund.objects.create(
                id="t_opening", database_id="f1", type="credit", amount=100.0,
                date=timezone.now() - timedelta(days=1), mode="cash",
                running_balance=100.0, approved=True, created_by=user,
            )
            RecurringTransaction.objects.create(
                id="r1", database_id="f1", type="credit", amount=10.0,
                frequency="monthly", description="rent",
                next_run=timezone.now().date(), is_active=True, created_by=user,
            )

        barrier = threading.Barrier(2)
        errors = []
        threads = [threading.Thread(target=_process, args=(user, barrier, errors)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"threads raised: {errors}")
        with org_context("tenant_dev"):
            # Excludes the setUp opening credit: only the recurring rule's own posts count here.
            posted = TransactionFund.objects.filter(database_id="f1").exclude(id="t_opening").count()
            balance = DatabaseFund.objects.get(id="f1").balance
        self.assertEqual(posted, 1, "the same due rule was posted by both runs")
        self.assertEqual(balance, 110.0)
