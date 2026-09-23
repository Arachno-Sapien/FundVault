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
        approved = list(
            TransactionFund.objects.filter(database_id=database_id, is_voided=False, approved=True)
            .order_by("date", "created_at", "id")
        )
        balance = 0
        for txn in approved:
            balance = balance + txn.amount if txn.type == "credit" else balance - txn.amount
            if txn.running_balance != balance:
                txn.running_balance = balance
                txn.save(update_fields=["running_balance"])
        DatabaseFund.objects.filter(id=database_id).update(balance=balance)
        return balance


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
            .filter(database__is_deleted=False, is_active=True, next_run__lte=today)
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
            # Re-read the fund under a row lock: rec.database came from an
            # unlocked select_related() prefetch, so a concurrent debit/approval
            # could move the balance between that read and this write.
            db = DatabaseFund.objects.select_for_update().get(id=rec.database_id)
            if rec.type == "debit" and rec.amount > db.balance:
                rec.next_run = next_recurring_date(rec.next_run, rec.frequency)
                rec.save(update_fields=["next_run"])
                continue

            # Re-evaluated against the creator's *current* standing every run
            # (not frozen at creation time). Fail closed: only a creator who
            # still exists, is active, and could still create this rule
            # (MANAGE_FUNDS: Admin/Owner) posts straight through. Anyone else --
            # demoted, deactivated, deleted (SET_NULL), or a legacy row with no
            # creator -- is gated exactly as a Member's transaction would be.
            requires_approval = not can(rec.created_by, Action.MANAGE_FUNDS) and needs_approval(
                User(role=User.Role.MEMBER), rec.amount, db.approval_threshold
            )
            new_balance = (
                db.balance
                if requires_approval
                else (db.balance + rec.amount if rec.type == "credit" else db.balance - rec.amount)
            )
            txn = TransactionFund.objects.create(
                id=uid(),
                database_id=db.id,
                type=rec.type,
                amount=rec.amount,
                date=timezone.now(),
                sender="Recurring",
                receiver=rec.description or "",
                mode=TransactionFund.TxnMode.ELECTRONIC,
                mode_data=json.dumps({"elecId": f"REC-{rec.id}"}),
                location="Auto",
                notes=f"Recurring {rec.frequency} transaction",
                running_balance=new_balance,
                requires_approval=requires_approval,
                approved=not requires_approval,
                created_by_id=rec.created_by_id,
                is_voided=False,
            )
            if not requires_approval:
                db.balance = new_balance
                db.save(update_fields=["balance"])
            rec.next_run = next_recurring_date(rec.next_run, rec.frequency)
            rec.save(update_fields=["next_run"])
            add_audit(
                user.id, "create", "transaction", txn.id,
                f"Recurring {rec.type} of ₹{rec.amount} "
                f"{'pending approval' if requires_approval else 'auto-posted'}",
            )
            created.append(txn)

        return created
