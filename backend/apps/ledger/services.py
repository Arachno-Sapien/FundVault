import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import Action, can, needs_approval
from apps.common.audit import add_audit
from apps.common.utils import uid
from apps.ledger.models import DatabaseFund, RecurringTransaction, TransactionFund
from apps.orgs.context import current_org_alias


def recalculate_running_balances(database_id):
    # These are tenant-routed models (apps.orgs.router.TenantRouter), so the
    # atomic block must open on that same alias. A `@transaction.atomic`
    # decorator can't do this correctly here: as a decorator,
    # `using=current_org_alias()` would be evaluated once at import time
    # (before any request has set an org in context, and it would never
    # change per-request afterwards) -- it has to be a context manager
    # evaluated fresh on every call instead.
    with transaction.atomic(using=current_org_alias()):
        # Lock the fund before reading its rows, as every money write does:
        # otherwise a create committing between the read and the balance
        # write below is overwritten by a total that never saw it.
        lock_fund(database_id)
        approved = (
            TransactionFund.objects.filter(database_id=database_id, is_voided=False, approved=True)
            .order_by("date", "created_at", "id")
            .only("id", "type", "amount", "running_balance")
        )
        balance = 0
        changed = []
        for txn in approved:
            balance = round(balance + txn.amount if txn.type == "credit" else balance - txn.amount, 2)
            if txn.running_balance != balance:
                txn.running_balance = balance
                changed.append(txn)
        TransactionFund.objects.bulk_update(changed, ["running_balance"], batch_size=500)
        DatabaseFund.objects.filter(id=database_id).update(balance=balance)
        return balance


class InsufficientBalance(Exception):
    """A debit larger than the fund's current balance."""


def lock_fund(database_id):
    """Row-lock a fund inside the caller's tenant atomic block and return it.

    Every money write takes this first -- the fund, then its transaction rows
    -- so writes to one fund run one at a time and never deadlock each other.
    """
    return DatabaseFund.objects.select_for_update().filter(id=database_id).first()


def post_transaction(fund, *, tx_type, amount, date, requires_approval, created_by_id, **fields):
    """Insert a transaction into a fund locked with lock_fund().

    A debit larger than the balance is refused whether or not it needs
    approval. One that needs approval is stored pending and leaves the balance
    alone (transaction_approve moves it later); an approved one moves the
    balance, and running balances are rebuilt so a backdated entry leaves every
    later row right.
    """
    if tx_type == "debit" and amount > fund.balance:
        raise InsufficientBalance()
    running = fund.balance if requires_approval else round(
        fund.balance + amount if tx_type == "credit" else fund.balance - amount, 2
    )
    txn = TransactionFund.objects.create(
        id=uid(),
        database_id=fund.id,
        type=tx_type,
        amount=amount,
        date=date,
        running_balance=running,
        requires_approval=requires_approval,
        approved=not requires_approval,
        created_by_id=created_by_id,
        **fields,
    )
    if not requires_approval:
        fund.balance = recalculate_running_balances(fund.id)
        txn.refresh_from_db(fields=["running_balance"])
    return txn


def next_recurring_date(current_date, frequency):
    if frequency == "daily":
        return current_date + timedelta(days=1)
    if frequency == "weekly":
        return current_date + timedelta(days=7)
    if frequency == "monthly":
        month = current_date.month + 1
        year = current_date.year
        if month > 12:
            month = 1
            year += 1
        day = min(current_date.day, 28)
        return current_date.replace(year=year, month=month, day=day)
    if frequency == "yearly":
        return current_date.replace(year=current_date.year + 1)
    return current_date


def process_due_recurring(user):
    # Tenant-routed models -- see the comment on recalculate_running_balances
    # above for why this must be a context manager (evaluating
    # current_org_alias() fresh on every call) rather than a bare decorator.
    with transaction.atomic(using=current_org_alias()):
        today = timezone.now().date()
        due_ids = list(
            RecurringTransaction.objects
            .filter(
                database__is_deleted=False, database__is_archived=False,
                is_active=True, next_run__lte=today,
            )
            .order_by("next_run", "created_at")
            .values_list("id", flat=True)
        )
        created = []
        for rec_id in due_ids:
            # Lock the rule and re-check it is still due: the list above was
            # read without a lock, and the frontend fires POST /recurring/process
            # on every app load, so two overlapping calls can both see the same
            # rule as due. Whichever locks second waits here, then sees the
            # first one's advanced next_run and skips it instead of posting twice.
            rec = (
                RecurringTransaction.objects.select_for_update()
                .filter(id=rec_id, is_active=True, next_run__lte=today)
                .first()
            )
            if rec is None:
                continue
            db = lock_fund(rec.database_id)
            # Re-evaluated against the creator's *current* standing every run
            # (not frozen at creation time). Fail closed: only a creator who
            # still exists, is active, and could still create this rule
            # (MANAGE_FUNDS: Admin/Owner) posts straight through. Anyone else --
            # demoted, deactivated, deleted (SET_NULL), or a legacy row with no
            # creator -- is gated exactly as a Member's transaction would be.
            requires_approval = not can(rec.created_by, Action.MANAGE_FUNDS) and needs_approval(
                User(role=User.Role.MEMBER), rec.amount, db.approval_threshold
            )
            try:
                txn = post_transaction(
                    db,
                    tx_type=rec.type,
                    amount=rec.amount,
                    date=timezone.now(),
                    requires_approval=requires_approval,
                    created_by_id=rec.created_by_id,
                    sender="Recurring",
                    receiver=rec.description or "",
                    mode=TransactionFund.TxnMode.ELECTRONIC,
                    mode_data=json.dumps({"elecId": f"REC-{rec.id}"}),
                    location="Auto",
                    notes=f"Recurring {rec.frequency} transaction",
                    is_voided=False,
                )
            except InsufficientBalance:
                rec.next_run = next_recurring_date(rec.next_run, rec.frequency)
                rec.save(update_fields=["next_run"])
                continue
            rec.next_run = next_recurring_date(rec.next_run, rec.frequency)
            rec.save(update_fields=["next_run"])
            add_audit(
                user.id, "create", "transaction", txn.id,
                f"Recurring {rec.type} of ₹{rec.amount} "
                f"{'pending approval' if requires_approval else 'auto-posted'}",
            )
            created.append(txn)

        return created
