import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.common.audit import add_audit
from apps.common.utils import uid
from apps.ledger.models import DatabaseFund, RecurringTransaction, TransactionFund


@transaction.atomic
def recalculate_running_balances(database_id):
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


@transaction.atomic
def process_due_recurring(user):
    today = timezone.now().date()
    items = (
        RecurringTransaction.objects.select_related("database")
        .filter(database__user_id=user.id, database__is_deleted=False, is_active=True, next_run__lte=today)
        .order_by("next_run", "created_at")
    )
    created = []
    for rec in items:
        db = rec.database
        if rec.type == "debit" and rec.amount > db.balance:
            rec.next_run = next_recurring_date(rec.next_run, rec.frequency)
            rec.save(update_fields=["next_run"])
            continue

        new_balance = db.balance + rec.amount if rec.type == "credit" else db.balance - rec.amount
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
            requires_approval=False,
            approved=True,
            is_voided=False,
        )
        db.balance = new_balance
        db.save(update_fields=["balance"])
        rec.next_run = next_recurring_date(rec.next_run, rec.frequency)
        rec.save(update_fields=["next_run"])
        add_audit(user.id, "create", "transaction", txn.id, f"Recurring {rec.type} of ₹{rec.amount} auto-posted")
        created.append(txn)

    return created
