import json
from datetime import datetime

from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.common.audit import add_audit
from apps.common.auth import auth_required
from apps.common.utils import json_error, parse_body, uid
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
    query = DatabaseFund.objects.filter(id=database_id, user_id=user.id)
    if not include_deleted:
        query = query.filter(is_deleted=False)
    return query.first()


@csrf_exempt
@auth_required
def databases_list_create(request):
    if request.method == "GET":
        rows = DatabaseFund.objects.filter(user_id=request.fv_user.id, is_deleted=False).order_by("-created_at")
        return JsonResponse([serialize_database(row) for row in rows], safe=False)

    if request.method != "POST":
        return json_error("Method not allowed", 405)

    payload = parse_body(request)
    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "")).strip()
    low_balance_threshold = float(payload.get("lowBalanceThreshold") or 0)
    approval_threshold = float(payload.get("approvalThreshold") or 0)
    if not name:
        return json_error("Name required", 400)

    db = DatabaseFund.objects.create(
        id=uid(),
        user_id=request.fv_user.id,
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

    with transaction.atomic():
        merged = DatabaseFund.objects.create(
            id=uid(),
            user_id=request.fv_user.id,
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
            txn.save(force_insert=True)

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
        payload = serialize_database(db)
        payload["transactions"] = [serialize_transaction(txn) for txn in txns]
        return JsonResponse(payload)

    if request.method == "PUT":
        body = parse_body(request)
        name = str(body.get("name", "")).strip()
        description = str(body.get("description", "")).strip()
        low_balance_threshold = float(body.get("lowBalanceThreshold") or 0)
        approval_threshold = float(body.get("approvalThreshold") or 0)
        if not name:
            return json_error("Name required", 400)

        db.name = name
        db.description = description
        db.low_balance_threshold = low_balance_threshold
        db.approval_threshold = approval_threshold
        db.save(update_fields=["name", "description", "low_balance_threshold", "approval_threshold"])
        add_audit(request.fv_user.id, "update", "database", db.id, f'Database "{db.name}" updated')
        return JsonResponse(serialize_database(db))

    if request.method == "DELETE":
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
        return JsonResponse([serialize_transaction(row) for row in rows], safe=False)

    if request.method != "POST":
        return json_error("Method not allowed", 405)

    body = parse_body(request)
    tx_type = str(body.get("type", "")).strip()
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        return json_error("Amount must be greater than 0", 400)
    tx_date = _parse_iso_datetime(body.get("date"))
    sender = str(body.get("sender", "")).strip()
    receiver = str(body.get("receiver", "")).strip()
    mode = str(body.get("mode", "")).strip()
    mode_data = body.get("modeData") or {}
    location = str(body.get("location", "")).strip()
    notes = str(body.get("notes", "")).strip()
    receipt_image = body.get("receiptImage")

    if tx_type not in ("credit", "debit"):
        return json_error("Invalid transaction type", 400)
    if mode not in ("electronic", "cheque", "cash"):
        return json_error("Invalid transaction mode", 400)
    if amount <= 0:
        return json_error("Amount must be greater than 0", 400)
    if tx_date is None:
        return json_error("Transaction date is required", 400)
    if tx_type == "debit" and amount > db.balance:
        return json_error("Insufficient balance", 400)

    requires_approval = db.approval_threshold > 0 and amount >= db.approval_threshold
    new_balance = db.balance if requires_approval else (db.balance + amount if tx_type == "credit" else db.balance - amount)

    with transaction.atomic():
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
            receipt_image=receipt_image,
            requires_approval=requires_approval,
            approved=(not requires_approval),
        )
        if not requires_approval:
            db.balance = new_balance
            db.save(update_fields=["balance"])

    add_audit(
        request.fv_user.id,
        "create",
        "transaction",
        txn.id,
        f'{"Credit" if tx_type == "credit" else "Debit"} of ₹{amount} {"pending approval" if requires_approval else "recorded"}',
    )
    return JsonResponse(
        {
            "transaction": serialize_transaction(txn),
            "requiresApproval": requires_approval,
            "newBalance": new_balance,
        }
    )


@csrf_exempt
@auth_required
def transaction_void(request, transaction_id):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    body = parse_body(request)
    reason = str(body.get("reason", "")).strip()
    if not reason:
        return json_error("Void reason required", 400)

    txn = (
        TransactionFund.objects.select_related("database")
        .filter(id=transaction_id, database__user_id=request.fv_user.id, database__is_deleted=False)
        .first()
    )
    if not txn:
        return json_error("Transaction not found", 404)
    if txn.is_voided:
        return json_error("Transaction is already voided", 400)

    with transaction.atomic():
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

    txn = (
        TransactionFund.objects.select_related("database")
        .filter(id=transaction_id, database__user_id=request.fv_user.id, database__is_deleted=False)
        .first()
    )
    if not txn:
        return json_error("Transaction not found", 404)
    if not txn.is_voided:
        return json_error("Only voided transactions can be deleted", 400)

    db_id = txn.database_id
    txn_desc = f"#{txn.id} {txn.type} {txn.amount}"

    with transaction.atomic():
        txn.delete()
        recalculate_running_balances(db_id)

    add_audit(request.fv_user.id, "delete", "transaction", transaction_id, f"Voided transaction deleted: {txn_desc}")
    return JsonResponse({"success": True})


@csrf_exempt
@auth_required
def transaction_approve(request, transaction_id):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    txn = (
        TransactionFund.objects.select_related("database")
        .filter(id=transaction_id, database__user_id=request.fv_user.id, database__is_deleted=False)
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

    db = txn.database
    if txn.type == "debit" and txn.amount > db.balance:
        return json_error("Insufficient balance to approve this debit transaction", 400)

    new_balance = db.balance + txn.amount if txn.type == "credit" else db.balance - txn.amount
    with transaction.atomic():
        txn.approved = True
        txn.approved_by = request.fv_user.username
        txn.approved_at = timezone.now()
        txn.running_balance = new_balance
        txn.save(update_fields=["approved", "approved_by", "approved_at", "running_balance"])
        db.balance = new_balance
        db.save(update_fields=["balance"])
        recalculate_running_balances(db.id)

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
    body = parse_body(request)
    txn = (
        TransactionFund.objects.select_related("database")
        .filter(id=transaction_id, database__user_id=request.fv_user.id, database__is_deleted=False)
        .first()
    )
    if not txn:
        return json_error("Transaction not found", 404)
    if txn.is_voided:
        return json_error("Cannot edit a voided transaction", 400)

    try:
        amount = float(body.get("amount") if "amount" in body else txn.amount)
    except (TypeError, ValueError):
        return json_error("Enter a valid amount", 400)
    if amount <= 0:
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
    txn.save(update_fields=["amount", "date", "sender", "receiver", "location", "notes"])
    recalculate_running_balances(txn.database_id)
    add_audit(request.fv_user.id, "update", "transaction", txn.id, "Transaction edited")
    return JsonResponse(serialize_transaction(txn))


@auth_required
def audit_list(request):
    if request.method != "GET":
        return json_error("Method not allowed", 405)
    logs = AuditLog.objects.filter(user_id=request.fv_user.id).order_by("-timestamp")[:500]
    return JsonResponse([serialize_audit(entry) for entry in logs], safe=False)


@auth_required
def analytics_overview(request):
    if request.method != "GET":
        return json_error("Method not allowed", 405)

    databases = DatabaseFund.objects.filter(user_id=request.fv_user.id, is_deleted=False)
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
    if request.method == "GET":
        items = TrashItem.objects.filter(deleted_by_id=request.fv_user.id).order_by("-deleted_at")
        return JsonResponse([serialize_trash(item) for item in items], safe=False)

    if request.method == "DELETE":
        items = list(TrashItem.objects.filter(deleted_by_id=request.fv_user.id))
        for item in items:
            _delete_trash_item_permanently(item, request.fv_user)
        return JsonResponse({"success": True})

    return json_error("Method not allowed", 405)


def _delete_trash_item_permanently(item, user):
    if item.entity_type == "database":
        data = json.loads(item.entity_data)
        db_id = data.get("id")
        RecurringTransaction.objects.filter(database_id=db_id).delete()
        TransactionFund.objects.filter(database_id=db_id).delete()
        DatabaseFund.objects.filter(id=db_id, user_id=user.id).delete()
    item.delete()


@csrf_exempt
@auth_required
def trash_restore(request, item_id):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    item = TrashItem.objects.filter(id=item_id, deleted_by_id=request.fv_user.id).first()
    if not item:
        return json_error("Item not found", 404)

    if item.entity_type == "database":
        data = json.loads(item.entity_data)
        DatabaseFund.objects.filter(id=data.get("id"), user_id=request.fv_user.id).update(is_deleted=False)

    item.delete()
    add_audit(request.fv_user.id, "update", item.entity_type, item.id, "Item restored from trash")
    return JsonResponse({"success": True})


@csrf_exempt
@auth_required
def trash_delete(request, item_id):
    if request.method != "DELETE":
        return json_error("Method not allowed", 405)
    item = TrashItem.objects.filter(id=item_id, deleted_by_id=request.fv_user.id).first()
    if not item:
        return json_error("Item not found", 404)
    _delete_trash_item_permanently(item, request.fv_user)
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

    body = parse_body(request)
    tx_type = str(body.get("type", "")).strip()
    amount = float(body.get("amount") or 0)
    frequency = str(body.get("frequency", "")).strip()
    description = str(body.get("description", "")).strip()
    next_run = body.get("nextRun")

    if tx_type not in ("credit", "debit"):
        return json_error("Invalid transaction type", 400)
    if amount <= 0:
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
    )
    add_audit(request.fv_user.id, "create", "recurring", item.id, f"Recurring {tx_type} of ₹{amount} ({frequency}) created")
    return JsonResponse(serialize_recurring(item))


@csrf_exempt
@auth_required
def recurring_delete(request, recurring_id):
    if request.method != "DELETE":
        return json_error("Method not allowed", 405)
    item = (
        RecurringTransaction.objects.select_related("database")
        .filter(id=recurring_id, database__user_id=request.fv_user.id)
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
    created = process_due_recurring(request.fv_user)
    return JsonResponse({"success": True, "processed": len(created)})


@csrf_exempt
@auth_required
def extract_receipt(request):
    """Extract transaction data from a receipt / screenshot image using Gemini Vision."""
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    # ── Check configuration ────────────────────────────────────────────
    from django.conf import settings as django_settings

    mock_mode = (
        getattr(django_settings, "NVIDIA_RECEIPT_MOCK", False)
        or getattr(django_settings, "GEMINI_RECEIPT_MOCK", False)
    )
    has_any_key = bool(getattr(django_settings, "NVIDIA_API_KEY", "")) or bool(
        getattr(django_settings, "GEMINI_API_KEY", "")
    )
    if not mock_mode and not has_any_key:
        return json_error(
            "Receipt extraction not configured — set NVIDIA_API_KEY or GEMINI_API_KEY in .env",
            503,
        )

    # ── Validate uploaded file ─────────────────────────────────────────
    image_file = request.FILES.get("image")
    if not image_file:
        return json_error("No image file provided", 400)

    if image_file.size > 5 * 1024 * 1024:
        return json_error("Image must be less than 5 MB", 400)

    mime_type = getattr(image_file, "content_type", "") or ""
    if not mime_type.startswith("image/"):
        return json_error("File must be an image", 400)

    # ── Extract ────────────────────────────────────────────────────────
    from apps.ledger.receipt_extractor import extract_from_receipt_image

    image_bytes = image_file.read()
    result = extract_from_receipt_image(image_bytes, mime_type)
    return JsonResponse(result)
