import json


def _parse_mode_data(mode_data):
    if not mode_data:
        return {}
    try:
        return json.loads(mode_data)
    except (json.JSONDecodeError, TypeError):
        return {}


def serialize_database(database):
    return {
        "id": database.id,
        "user_id": database.user_id,
        "name": database.name,
        "description": database.description or "",
        "balance": float(database.balance or 0),
        "low_balance_threshold": float(database.low_balance_threshold or 0),
        "approval_threshold": float(database.approval_threshold or 0),
        "is_archived": bool(database.is_archived),
        "is_deleted": bool(database.is_deleted),
        "created_at": database.created_at.isoformat() if database.created_at else None,
    }


def serialize_transaction(txn):
    base = {
        "id": txn.id,
        "database_id": txn.database_id,
        "type": txn.type,
        "amount": float(txn.amount),
        "date": txn.date.isoformat() if txn.date else None,
        "sender": txn.sender or "",
        "receiver": txn.receiver or "",
        "mode": txn.mode,
        "mode_data": _parse_mode_data(txn.mode_data),
        "location": txn.location or "",
        "notes": txn.notes or "",
        "running_balance": float(txn.running_balance),
        "receipt_image": txn.receipt_image,
        "requires_approval": bool(txn.requires_approval),
        "approved": bool(txn.approved),
        "approved_by": txn.approved_by,
        "approved_at": txn.approved_at.isoformat() if txn.approved_at else None,
        "is_voided": bool(txn.is_voided),
        "void_reason": txn.void_reason,
        "voided_by": txn.voided_by,
        "voided_at": txn.voided_at.isoformat() if txn.voided_at else None,
        "created_at": txn.created_at.isoformat() if txn.created_at else None,
    }
    mode_data = base["mode_data"]
    if txn.mode == "electronic" and mode_data.get("elecId"):
        base["elecId"] = mode_data["elecId"]
    if txn.mode == "cheque":
        for key in ("chequeNo", "chequeDate", "chequeBank"):
            if mode_data.get(key):
                base[key] = mode_data[key]
    return base


def serialize_audit(entry):
    return {
        "id": entry.id,
        "user_id": entry.user_id,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "details": entry.details or "",
        "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
    }


def serialize_trash(item):
    return {
        "id": item.id,
        "entity_type": item.entity_type,
        "entity_data": item.entity_data,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
        "deleted_by": item.deleted_by_id,
    }


def serialize_recurring(item):
    return {
        "id": item.id,
        "database_id": item.database_id,
        "type": item.type,
        "amount": float(item.amount),
        "frequency": item.frequency,
        "description": item.description or "",
        "next_run": item.next_run.isoformat() if item.next_run else None,
        "is_active": bool(item.is_active),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
