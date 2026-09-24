// Mirrors backend/apps/accounts/permissions.py. The backend is authoritative —
// this exists only so the UI does not offer buttons that would 403.
export const ACTIONS = {
  VIEW: "view",
  CREATE_TXN: "create_txn",
  APPROVE: "approve",
  MODIFY_TXN: "modify_txn",
  MANAGE_FUNDS: "manage_funds",
  MANAGE_MEMBERS: "manage_members",
  MINT_ADMIN_CODE: "mint_admin_code",
  CHANGE_ROLE: "change_role",
  MANAGE_ORG_CONFIG: "manage_org_config",
  TRANSFER_OWNERSHIP: "transfer_ownership"
};

const VIEWER = [ACTIONS.VIEW];
const MEMBER = [...VIEWER, ACTIONS.CREATE_TXN];
const ADMIN = [...MEMBER, ACTIONS.APPROVE, ACTIONS.MODIFY_TXN, ACTIONS.MANAGE_FUNDS, ACTIONS.MANAGE_MEMBERS];
const OWNER = [...ADMIN, ACTIONS.MINT_ADMIN_CODE, ACTIONS.CHANGE_ROLE, ACTIONS.MANAGE_ORG_CONFIG, ACTIONS.TRANSFER_OWNERSHIP];

const TABLE = { owner: OWNER, admin: ADMIN, member: MEMBER, viewer: VIEWER };

export const can = (user, action) =>
  Boolean(user && user.is_active !== false && (TABLE[user.role] || []).includes(action));
