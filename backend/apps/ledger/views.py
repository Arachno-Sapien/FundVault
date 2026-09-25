import json
import logging
from datetime import datetime

from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.permissions import Action, needs_approval, require
from apps.common.audit import add_audit
from apps.common.auth import auth_required
from apps.common.utils import json_error, parse_body, parse_number, uid
from apps.ledger.models import (
    AuditLog,
    DatabaseFund,
    RecurringTransaction,
    TransactionFund,
    TrashItem,
)
from apps.ledger.serializers import (
    serialize_audit,
    serialize_database,
    serialize_recurring,
    serialize_transaction,
    serialize_trash,
)
from apps.ledger.services import process_due_recurring, recalculate_running_balances
from apps.orgs.context import current_org_alias

logger = logging.getLogger(__name__)

# Two finite credits can still add up past a float's max and leave a fund's
# balance (and the org's JSON) as Infinity. Above ~9e13, 2-decimal rounding
# stops being exact in a float anyway, so this is nowhere near a real ledger.
MAX_AMOUNT = 1e12


def _parse_iso_datetime(raw):
    if not raw:
        return None
    try:
        fixed = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(fixed)
        if timezone.is_naive(dt):
            return timezone.make_aware(dt)
        return dt
    except ValueError:
        return None


def _get_user_database(user, database_id, include_deleted=False):
    """Look up a fund within the caller's org.

    The org boundary is the database connection itself, so no ownership filter
    is applied here. The `user` parameter is retained because callers pass it
    and Phase 4 uses it for role checks.
    """
    query = DatabaseFund.objects.filter(id=database_id)
    if not include_deleted:
        query = query.filter(is_deleted=False)
    return query.first()


def _storage_for(request):
    from apps.ledger.storage import StorageNotConfigured, parse_storage_config

    try:
        return parse_storage_config(request.fv_org.storage_config)
    except StorageNotConfigured:
        return None


def _delete_receipts_on_commit(request, keys):
    """Remove receipt objects from the org's bucket once no row points at them.

    Runs after the surrounding DB change commits, so a rollback never loses an
    image that is still referenced. databases_merge copies rows with their
    receipt_key, so one object can back several rows: only keys nothing still
    references are deleted. Best-effort -- storage being down or unconfigured
    must never fail the request, so failures are only logged.
    """
    keys = {key for key in keys if key}
    storage = _storage_for(request) if keys else None
    if storage is None:
        return
    alias = current_org_alias()

    def cleanup():
        from apps.ledger.storage import _redact, delete_object

        still_used = set(
            TransactionFund.objects.using(alias)
            .filter(receipt_key__in=keys)
            .values_list("receipt_key", flat=True)
        )
        for key in keys - still_used:
            try:
                delete_object(storage, key)
            except Exception as exc:
                logger.warning("Could not delete receipt %s: %s", key, _redact(str(exc), storage))

    # robust: a failure in the reference check is logged, not raised.
    transaction.on_commit(cleanup, using=alias, robust=True)


@csrf_exempt
@auth_required
def databases_list_create(request):
    if request.method == "GET":
        rows = DatabaseFund.objects.filter(is_deleted=False).order_by("-created_at")
        return JsonResponse([serialize_database(row) for row in rows], safe=False)

    if request.method != "POST":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.MANAGE_FUNDS)
    if denied:
        return denied

    payload = parse_body(request)
    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "")).strip()
    low_balance_threshold = parse_number(payload.get("lowBalanceThreshold") or 0)
    approval_threshold = parse_number(payload.get("approvalThreshold") or 0)
    if not name:
        return json_error("Name required", 400)
    if low_balance_threshold is None or approval_threshold is None:
        return json_error("Thresholds must be numbers", 400)

    db = DatabaseFund.objects.create(
        id=uid(),
        created_by_id=request.fv_user.id,
        name=name,
        description=description,
        low_balance_threshold=low_balance_threshold,
        approval_threshold=approval_threshold,
    )
    add_audit(request.fv_user.id, "create", "database", db.id, f'Database "{db.name}" created')
    return JsonResponse(serialize_database(db))


@csrf_exempt
@auth_required
def databases_merge(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    denied = require(request.fv_user, Action.MANAGE_FUNDS)
    if denied:
        return denied
    payload = parse_body(request)
    source_id = str(payload.get("sourceId", "")).strip()
    target_id = str(payload.get("targetId", "")).strip()
    merged_name = str(payload.get("name", "")).strip()

    if not source_id or not target_id or not merged_name:
        return json_error("Source, target, and name are required", 400)
    if source_id == target_id:
        return json_error("Cannot merge a database with itself", 400)

    source = _get_user_database(request.fv_user, source_id)
    target = _get_user_database(request.fv_user, target_id)
    if not source or not target:
        return json_error("Database not found", 404)

    # DatabaseFund/TransactionFund are tenant-routed (apps.orgs.router.TenantRouter),
    # so the atomic block must open on that same alias -- a bare atomic()
    # defaults to "default" and select_for_update() elsewhere in this file
    # would raise TransactionManagementError against the org's own connection.
    with transaction.atomic(using=current_org_alias()):
        merged = DatabaseFund.objects.create(
            id=uid(),
            created_by_id=request.fv_user.id,
            name=merged_name,
            description=f'Merged from "{source.name}" and "{target.name}"',
            balance=0,
            low_balance_threshold=max(source.low_balance_threshold or 0, target.low_balance_threshold or 0),
            approval_threshold=max(source.approval_threshold or 0, target.approval_threshold or 0),
            is_archived=False,
            is_deleted=False,
        )

        txns = list(
            TransactionFund.objects.filter(database_id__in=[source.id, target.id]).order_by("date", "created_at", "id")
        )
        for txn in txns:
            txn.pk = uid()
            txn.database_id = merged.id
        TransactionFund.objects.bulk_create(txns)

        source.is_archived = True
        target.is_archived = True
        source.save(update_fields=["is_archived"])
        target.save(update_fields=["is_archived"])

        recalculate_running_balances(merged.id)
        add_audit(
            request.fv_user.id,
            "create",
            "database",
            merged.id,
            f'Merged "{source.name}" and "{target.name}" into "{merged_name}"',
        )

    return JsonResponse(serialize_database(merged))


@csrf_exempt
@auth_required
def database_detail(request, database_id):
    db = _get_user_database(request.fv_user, database_id)
    if not db:
        return json_error("Database not found", 404)

    if request.method == "GET":
        txns = TransactionFund.objects.filter(database_id=db.id).order_by("-date")
        storage = _storage_for(request)
        payload = serialize_database(db)
        payload["transactions"] = [serialize_transaction(txn, storage) for txn in txns]
        return JsonResponse(payload)

    if request.method == "PUT":
        denied = require(request.fv_user, Action.MANAGE_FUNDS)
        if denied:
            return denied
        body = parse_body(request)
        name = str(body.get("name", "")).strip()
        description = str(body.get("description", "")).strip()
        low_balance_threshold = parse_number(body.get("lowBalanceThreshold") or 0)
        approval_threshold = parse_number(body.get("approvalThreshold") or 0)
        if not name:
            return json_error("Name required", 400)
        if low_balance_threshold is None or approval_threshold is None:
            return json_error("Thresholds must be numbers", 400)

        db.name = name
        db.description = description
        db.low_balance_threshold = low_balance_threshold
        db.approval_threshold = approval_threshold
        db.save(update_fields=["name", "description", "low_balance_threshold", "approval_threshold"])
        add_audit(request.fv_user.id, "update", "database", db.id, f'Database "{db.name}" updated')
        return JsonResponse(serialize_database(db))

    if request.method == "DELETE":
        denied = require(request.fv_user, Action.MANAGE_FUNDS)
        if denied:
            return denied
        db.is_deleted = True
        db.save(update_fields=["is_deleted"])
        TrashItem.objects.create(
            id=uid(),
            entity_type="database",
            entity_data=json.dumps(serialize_database(db)),
            deleted_by_id=request.fv_user.id,
        )
        add_audit(request.fv_user.id, "delete", "database", db.id, f'Database "{db.name}" deleted')
        return JsonResponse({"success": True})

    return json_error("Method not allowed", 405)


@csrf_exempt
@auth_required
def database_archive(request, database_id):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    denied = require(request.fv_user, Action.MANAGE_FUNDS)
    if denied:
        return denied
    db = _get_user_database(request.fv_user, database_id)
    if not db:
        return json_error("Database not found", 404)
    db.is_archived = not db.is_archived
    db.save(update_fields=["is_archived"])
    add_audit(
        request.fv_user.id,
        "update",
        "database",
        db.id,
        f'Database "{db.name}" {"archived" if db.is_archived else "unarchived"}',
    )
    return JsonResponse({"success": True, "is_archived": db.is_archived})


@csrf_exempt
@auth_required
def database_transactions(request, database_id):
    db = _get_user_database(request.fv_user, database_id)
    if not db:
        return json_error("Database not found", 404)

    if request.method == "GET":
        rows = TransactionFund.objects.filter(database_id=database_id).order_by("-date", "-created_at")
        storage = _storage_for(request)
        return JsonResponse([serialize_transaction(row, storage) for row in rows], safe=False)

    if request.method != "POST":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.CREATE_TXN)
    if denied:
        return denied

    body = parse_body(request)
    tx_type = str(body.get("type", "")).strip()
    # ponytail: money is a FloatField, so amounts and balances are rounded to
    # paise at every write; move to DecimalField to drop the rounding.
    amount = round(parse_number(body.get("amount")) or 0, 2)
    tx_date = _parse_iso_datetime(body.get("date"))
    sender = str(body.get("sender", "")).strip()
    receiver = str(body.get("receiver", "")).strip()
    mode = str(body.get("mode", "")).strip()
    mode_data = body.get("modeData") or {}
    location = str(body.get("location", "")).strip()
    notes = str(body.get("notes", "")).strip()

    if tx_type not in ("credit", "debit"):
        return json_error("Invalid transaction type", 400)
    if mode not in ("electronic", "cheque", "cash"):
        return json_error("Invalid transaction mode", 400)
    if amount <= 0 or amount > MAX_AMOUNT:
        return json_error("Amount must be greater than 0", 400)
    if tx_date is None:
        return json_error("Transaction date is required", 400)
    # Must open on the org's own alias: select_for_update() below requires an
    # already-open transaction on the SAME alias its query targets, and a
    # bare atomic() always opens on "default" instead.
    with transaction.atomic(using=current_org_alias()):
        # Re-read the fund under a row lock: the balance read above happened
        # outside any transaction and another request may have moved it.
        locked = DatabaseFund.objects.select_for_update().filter(id=database_id).first()
        if not locked:
            return json_error("Database not found", 404)

        if tx_type == "debit" and amount > locked.balance:
            return json_error("Insufficient balance", 400)

        requires_approval = needs_approval(request.fv_user, amount, locked.approval_threshold)
        new_balance = (
            locked.balance
            if requires_approval
            else round(locked.balance + amount if tx_type == "credit" else locked.balance - amount, 2)
        )

        txn = TransactionFund.objects.create(
            id=uid(),
            database_id=database_id,
            type=tx_type,
            amount=amount,
            date=tx_date,
            sender=sender or None,
            receiver=receiver or None,
            mode=mode,
            mode_data=json.dumps(mode_data),
            location=location or None,
            notes=notes or None,
            running_balance=new_balance,
            receipt_key=None,
            requires_approval=requires_approval,
            approved=(not requires_approval),
            created_by_id=request.fv_user.id,
        )
        if not requires_approval:
            # The row above was stamped with a running balance as if it were the
            # latest entry; a backdated one isn't, so rebuild the fund's rows in
            # date order (one SELECT and no row updates when it is the latest).
            new_balance = recalculate_running_balances(database_id)
            txn.refresh_from_db(fields=["running_balance"])

    add_audit(
        request.fv_user.id,
        "create",
        "transaction",
        txn.id,
        f'{"Credit" if tx_type == "credit" else "Debit"} of ₹{amount} {"pending approval" if requires_approval else "recorded"}',
    )
    return JsonResponse(
        {
            "transaction": serialize_transaction(txn, _storage_for(request)),
            "requiresApproval": requires_approval,
            "newBalance": new_balance,
        }
    )


@csrf_exempt
@auth_required
def transaction_void(request, transaction_id):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    denied = require(request.fv_user, Action.MODIFY_TXN)
    if denied:
        return denied
    body = parse_body(request)
    reason = str(body.get("reason", "")).strip()
    if not reason:
        return json_error("Void reason required", 400)

    txn = (
        TransactionFund.objects.select_related("database")
        .filter(id=transaction_id, database__is_deleted=False)
        .first()
    )
    if not txn:
        return json_error("Transaction not found", 404)
    if txn.is_voided:
        return json_error("Transaction is already voided", 400)

    # Tenant-routed model -- see the comment on database_transactions above.
    with transaction.atomic(using=current_org_alias()):
        # Lock the fund first (fund, then transaction, like create and
        # approve) so a concurrent create can't slip in before the recalc.
        DatabaseFund.objects.select_for_update().filter(id=txn.database_id).first()
        txn.is_voided = True
        txn.void_reason = reason
        txn.voided_by = request.fv_user.username
        txn.voided_at = timezone.now()
        txn.save(update_fields=["is_voided", "void_reason", "voided_by", "voided_at"])
        recalculate_running_balances(txn.database_id)

    add_audit(request.fv_user.id, "void", "transaction", txn.id, f"Transaction voided: {reason}")
    return JsonResponse({"success": True})


@csrf_exempt
@auth_required
def transaction_delete_voided(request, transaction_id):
    """Permanently delete a voided transaction from the ledger."""
    if request.method != "DELETE":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.MODIFY_TXN)
    if denied:
        return denied

    txn = (
        TransactionFund.objects.select_related("database")
        .filter(id=transaction_id, database__is_deleted=False)
        .first()
    )
    if not txn:
        return json_error("Transaction not found", 404)
    if not txn.is_voided:
        return json_error("Only voided transactions can be deleted", 400)

    db_id = txn.database_id
    txn_desc = f"#{txn.id} {txn.type} {txn.amount}"

    # Tenant-routed model -- see the comment on database_transactions above.
    with transaction.atomic(using=current_org_alias()):
        # Lock the fund first (fund, then transaction, like create and
        # approve) so a concurrent create can't slip in before the recalc.
        DatabaseFund.objects.select_for_update().filter(id=db_id).first()
        txn.delete()
        recalculate_running_balances(db_id)
        _delete_receipts_on_commit(request, [txn.receipt_key])

    add_audit(request.fv_user.id, "delete", "transaction", transaction_id, f"Voided transaction deleted: {txn_desc}")
    return JsonResponse({"success": True})


@csrf_exempt
@auth_required
def transaction_approve(request, transaction_id):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.APPROVE)
    if denied:
        return denied

    txn = (
        TransactionFund.objects.select_related("database")
        .filter(id=transaction_id, database__is_deleted=False)
        .first()
    )
    if not txn:
        return json_error("Transaction not found", 404)
    if txn.is_voided:
        return json_error("Cannot approve a voided transaction", 400)
    if txn.approved:
        return json_error("Transaction is already approved", 400)
    if not txn.requires_approval:
        return json_error("Transaction does not require approval", 400)

    # Must open on the org's own alias -- see the comment on
    # database_transactions above (select_for_update() below needs it).
    with transaction.atomic(using=current_org_alias()):
        locked = DatabaseFund.objects.select_for_update().filter(id=txn.database_id).first()
        if not locked:
            return json_error("Database not found", 404)

        if txn.type == "debit" and txn.amount > locked.balance:
            return json_error("Insufficient balance to approve this debit transaction", 400)

        new_balance = round(
            locked.balance + txn.amount if txn.type == "credit" else locked.balance - txn.amount, 2
        )
        txn.approved = True
        txn.approved_by = request.fv_user.username
        txn.approved_at = timezone.now()
        txn.running_balance = new_balance
        txn.save(update_fields=["approved", "approved_by", "approved_at", "running_balance"])
        locked.balance = new_balance
        locked.save(update_fields=["balance"])
        recalculate_running_balances(locked.id)

    add_audit(
        request.fv_user.id,
        "update",
        "transaction",
        txn.id,
        f"Transaction approved by {request.fv_user.username}",
    )
    return JsonResponse({"success": True, "newBalance": new_balance})


@csrf_exempt
@auth_required
def transaction_update(request, transaction_id):
    if request.method != "PUT":
        return json_error("Method not allowed", 405)
    denied = require(request.fv_user, Action.MODIFY_TXN)
    if denied:
        return denied
    body = parse_body(request)
    txn = (
        TransactionFund.objects.select_related("database")
        .filter(id=transaction_id, database__is_deleted=False)
        .first()
    )
    if not txn:
        return json_error("Transaction not found", 404)
    if txn.is_voided:
        return json_error("Cannot edit a voided transaction", 400)

    amount = round(parse_number(body["amount"]) or 0, 2) if "amount" in body else txn.amount
    if amount <= 0 or amount > MAX_AMOUNT:
        return json_error("Enter a valid amount", 400)
    tx_date = txn.date
    if "date" in body:
        parsed_date = _parse_iso_datetime(body.get("date"))
        if parsed_date is None:
            return json_error("Transaction date is required", 400)
        tx_date = parsed_date

    txn.amount = amount
    txn.date = tx_date
    txn.sender = str(body.get("sender", txn.sender or "")).strip() or None
    txn.receiver = str(body.get("receiver", txn.receiver or "")).strip() or None
    txn.location = str(body.get("location", txn.location or "")).strip() or None
    txn.notes = str(body.get("notes", txn.notes or "")).strip() or None
    with transaction.atomic(using=current_org_alias()):
        # Lock the fund first (fund, then transaction, like create and
        # approve) so a concurrent create can't slip in before the recalc.
        DatabaseFund.objects.select_for_update().filter(id=txn.database_id).first()
        txn.save(update_fields=["amount", "date", "sender", "receiver", "location", "notes"])
        recalculate_running_balances(txn.database_id)
    add_audit(request.fv_user.id, "update", "transaction", txn.id, "Transaction edited")
    return JsonResponse(serialize_transaction(txn, _storage_for(request)))


@auth_required
def audit_list(request):
    if request.method != "GET":
        return json_error("Method not allowed", 405)
    # No user filter: the tenant connection is the org boundary, so this is
    # the org's full audit trail, not just the caller's own actions.
    logs = AuditLog.objects.order_by("-timestamp")[:500]
    return JsonResponse([serialize_audit(entry) for entry in logs], safe=False)


@auth_required
def analytics_overview(request):
    if request.method != "GET":
        return json_error("Method not allowed", 405)

    databases = DatabaseFund.objects.filter(is_deleted=False)
    db_ids = list(databases.values_list("id", flat=True))
    if not db_ids:
        return JsonResponse(
            {
                "totalDatabases": 0,
                "totalBalance": 0,
                "totalCredits": 0,
                "totalDebits": 0,
                "monthlyData": [],
                "modeData": [],
            }
        )

    credits = (
        TransactionFund.objects.filter(database_id__in=db_ids, type="credit", is_voided=False)
        .aggregate(total=Sum("amount"))
        .get("total")
        or 0
    )
    debits = (
        TransactionFund.objects.filter(database_id__in=db_ids, type="debit", is_voided=False)
        .aggregate(total=Sum("amount"))
        .get("total")
        or 0
    )
    total_balance = databases.aggregate(total=Sum("balance")).get("total") or 0
    return JsonResponse(
        {
            "totalDatabases": databases.count(),
            "totalBalance": total_balance,
            "totalCredits": credits,
            "totalDebits": debits,
        }
    )


@csrf_exempt
@auth_required
def trash_list(request):
    # No user filter on either branch: the tenant connection is the org
    # boundary, so this is the org's full trash, not just the caller's own.
    if request.method == "GET":
        items = TrashItem.objects.order_by("-deleted_at")
        return JsonResponse([serialize_trash(item) for item in items], safe=False)

    if request.method == "DELETE":
        denied = require(request.fv_user, Action.MANAGE_FUNDS)
        if denied:
            return denied
        items = list(TrashItem.objects.all())
        for item in items:
            _delete_trash_item_permanently(request, item)
        return JsonResponse({"success": True})

    return json_error("Method not allowed", 405)


def _delete_trash_item_permanently(request, item):
    with transaction.atomic(using=current_org_alias()):
        if item.entity_type == "database":
            data = json.loads(item.entity_data)
            db_id = data.get("id")
            txns = TransactionFund.objects.filter(database_id=db_id)
            _delete_receipts_on_commit(request, txns.values_list("receipt_key", flat=True))
            RecurringTransaction.objects.filter(database_id=db_id).delete()
            txns.delete()
            DatabaseFund.objects.filter(id=db_id).delete()
        item.delete()


@csrf_exempt
@auth_required
def trash_restore(request, item_id):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    denied = require(request.fv_user, Action.MANAGE_FUNDS)
    if denied:
        return denied
    # No user filter: the tenant connection is the org boundary, so any
    # trash item in this org is restorable by a MANAGE_FUNDS holder,
    # regardless of which member originally deleted it.
    item = TrashItem.objects.filter(id=item_id).first()
    if not item:
        return json_error("Item not found", 404)

    if item.entity_type == "database":
        data = json.loads(item.entity_data)
        DatabaseFund.objects.filter(id=data.get("id")).update(is_deleted=False)

    item.delete()
    add_audit(request.fv_user.id, "update", item.entity_type, item.id, "Item restored from trash")
    return JsonResponse({"success": True})


@csrf_exempt
@auth_required
def trash_delete(request, item_id):
    if request.method != "DELETE":
        return json_error("Method not allowed", 405)
    denied = require(request.fv_user, Action.MANAGE_FUNDS)
    if denied:
        return denied
    # No user filter here either -- same tenant-boundary reasoning as
    # trash_restore above.
    item = TrashItem.objects.filter(id=item_id).first()
    if not item:
        return json_error("Item not found", 404)
    _delete_trash_item_permanently(request, item)
    return JsonResponse({"success": True})


@csrf_exempt
@auth_required
def recurring_list_create(request, database_id):
    db = _get_user_database(request.fv_user, database_id)
    if not db:
        return json_error("Database not found", 404)

    if request.method == "GET":
        items = RecurringTransaction.objects.filter(database_id=database_id, is_active=True).order_by("-created_at")
        return JsonResponse([serialize_recurring(item) for item in items], safe=False)

    if request.method != "POST":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.MANAGE_FUNDS)
    if denied:
        return denied

    body = parse_body(request)
    tx_type = str(body.get("type", "")).strip()
    amount = round(parse_number(body.get("amount")) or 0, 2)
    frequency = str(body.get("frequency", "")).strip()
    description = str(body.get("description", "")).strip()
    next_run = body.get("nextRun")

    if tx_type not in ("credit", "debit"):
        return json_error("Invalid transaction type", 400)
    if amount <= 0 or amount > MAX_AMOUNT:
        return json_error("Amount must be greater than 0", 400)
    if frequency not in ("daily", "weekly", "monthly", "yearly"):
        return json_error("Invalid frequency", 400)
    if not description:
        return json_error("Description required", 400)
    try:
        next_run_date = datetime.fromisoformat(str(next_run)).date()
    except ValueError:
        return json_error("Invalid next run date", 400)

    item = RecurringTransaction.objects.create(
        id=uid(),
        database_id=database_id,
        type=tx_type,
        amount=amount,
        frequency=frequency,
        description=description,
        next_run=next_run_date,
        is_active=True,
        created_by_id=request.fv_user.id,
    )
    add_audit(request.fv_user.id, "create", "recurring", item.id, f"Recurring {tx_type} of ₹{amount} ({frequency}) created")
    return JsonResponse(serialize_recurring(item))


@csrf_exempt
@auth_required
def recurring_delete(request, recurring_id):
    if request.method != "DELETE":
        return json_error("Method not allowed", 405)
    denied = require(request.fv_user, Action.MANAGE_FUNDS)
    if denied:
        return denied
    item = (
        RecurringTransaction.objects.select_related("database")
        .filter(id=recurring_id)
        .first()
    )
    if not item:
        return json_error("Recurring transaction not found", 404)
    item.is_active = False
    item.save(update_fields=["is_active"])
    return JsonResponse({"success": True})


@csrf_exempt
@auth_required
def recurring_process(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    # Not in the Task 17 brief's table, but this posts TransactionFund rows
    # (see apps.ledger.services.process_due_recurring), same write as
    # database_transactions' POST branch, so it gets the same gate.
    denied = require(request.fv_user, Action.CREATE_TXN)
    if denied:
        return denied
    created = process_due_recurring(request.fv_user)
    return JsonResponse({"success": True, "processed": len(created)})


@csrf_exempt
@auth_required
def extract_receipt(request):
    """Extract transaction data from a receipt / screenshot image using Gemini Vision."""
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.CREATE_TXN)
    if denied:
        return denied

    image_file = request.FILES.get("image")
    if not image_file:
        return json_error("No image file provided", 400)
    if image_file.size > 5 * 1024 * 1024:
        return json_error("Image must be less than 5 MB", 400)

    from apps.ledger.receipt_extractor import extract_from_receipt_image, parse_ai_config

    config = parse_ai_config(request.fv_org.ai_config)
    result = extract_from_receipt_image(image_file.read(), image_file.content_type or "", config)
    status = 503 if result.get("error", "").endswith("organisation settings.") else 200
    return JsonResponse(result, status=status)


@csrf_exempt
@auth_required
def transaction_receipt(request, transaction_id):
    """Attach a receipt image to a transaction."""
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.CREATE_TXN)
    if denied:
        return denied

    from apps.ledger.receipt_extractor import _compress_image
    from apps.ledger.storage import (
        StorageNotConfigured,
        _redact,
        parse_storage_config,
        put_object,
        receipt_key_for,
    )

    # Validate the upload itself before touching storage: a bad request
    # (missing/oversized/undecodable file) is a 400 regardless of whether
    # this org even has storage configured, so that check runs first.
    upload = request.FILES.get("image")
    if not upload:
        return json_error("No image file provided", 400)
    if upload.size > 5 * 1024 * 1024:
        return json_error("Image must be less than 5 MB", 400)

    txn = TransactionFund.objects.filter(id=transaction_id, database__is_deleted=False).first()
    if not txn:
        return json_error("Transaction not found", 404)

    raw = upload.read()
    try:
        compressed = _compress_image(raw)
    except Exception:
        return json_error("That file is not a readable image", 400)

    try:
        storage = parse_storage_config(request.fv_org.storage_config)
    except StorageNotConfigured as exc:
        return json_error(str(exc), 503)
    if storage is None:
        return json_error(
            "Receipt storage is not configured for this organisation. "
            "An Owner can add it in organisation settings.",
            503,
        )

    key = receipt_key_for(txn.database_id, txn.id)
    try:
        put_object(storage, key, compressed, "image/jpeg")
    except Exception as exc:
        return json_error(f"Could not upload the receipt: {_redact(str(exc), storage)}", 502)

    old_key = txn.receipt_key
    txn.receipt_key = key
    txn.save(update_fields=["receipt_key"])
    # Keys are deterministic per fund+transaction, so this only differs for a
    # row copied by databases_merge; the same key was just overwritten in place.
    if old_key != key:
        _delete_receipts_on_commit(request, [old_key])
    add_audit(request.fv_user.id, "update", "transaction", txn.id, "Receipt attached")

    from apps.ledger.storage import signed_url

    return JsonResponse({"receipt_url": signed_url(storage, key)})
