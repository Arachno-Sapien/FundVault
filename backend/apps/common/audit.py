from apps.common.utils import uid


def add_audit(user_id, action, entity_type, entity_id, details):
    from apps.ledger.models import AuditLog

    AuditLog.objects.create(
        id=uid(),
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )
