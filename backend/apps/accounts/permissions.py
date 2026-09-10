"""Who may do what, in one place.

Every capability check in the application resolves through this table, so the
spec's roles table has exactly one implementation rather than a scatter of
inline role comparisons.
"""

from apps.accounts.models import User
from apps.common.utils import json_error


class Action:
    VIEW = "view"
    CREATE_TXN = "create_txn"
    APPROVE = "approve"
    MODIFY_TXN = "modify_txn"
    MANAGE_FUNDS = "manage_funds"
    MANAGE_MEMBERS = "manage_members"
    MINT_ADMIN_CODE = "mint_admin_code"
    CHANGE_ROLE = "change_role"
    MANAGE_ORG_CONFIG = "manage_org_config"
    TRANSFER_OWNERSHIP = "transfer_ownership"


_VIEWER = {Action.VIEW}
_MEMBER = _VIEWER | {Action.CREATE_TXN}
_ADMIN = _MEMBER | {
    Action.APPROVE,
    Action.MODIFY_TXN,
    Action.MANAGE_FUNDS,
    Action.MANAGE_MEMBERS,
}
_OWNER = _ADMIN | {
    Action.MINT_ADMIN_CODE,
    Action.CHANGE_ROLE,
    Action.MANAGE_ORG_CONFIG,
    Action.TRANSFER_OWNERSHIP,
}

CAPABILITIES = {
    User.Role.OWNER: _OWNER,
    User.Role.ADMIN: _ADMIN,
    User.Role.MEMBER: _MEMBER,
    User.Role.VIEWER: _VIEWER,
}


def can(user, action):
    if user is None or not getattr(user, "is_active", False):
        return False
    return action in CAPABILITIES.get(user.role, frozenset())


def require(user, action):
    """Return a 403 JsonResponse if the user may not perform action, else None."""
    if can(user, action):
        return None
    return json_error("You do not have permission to do that", 403)


def needs_approval(user, amount, threshold):
    """Approval gates Member-created transactions only, per the spec."""
    if not threshold or threshold <= 0:
        return False
    if user.role != User.Role.MEMBER:
        return False
    return amount >= threshold
