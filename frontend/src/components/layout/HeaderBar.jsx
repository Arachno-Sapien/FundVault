import { ACTIONS, can } from "lib/permissions";

export default function HeaderBar({
  currentUser,
  currentOrg,
  theme,
  onToggleTheme,
  onLogout,
  onOpenUserManagement,
  onOpenProfile,
  onOpenOrgSettings,
  onClearCache,
  userDropdownOpen,
  setUserDropdownOpen
}) {
  const avatar = (currentUser?.username || "G")[0]?.toUpperCase();
  const canManageUsers = can(currentUser, ACTIONS.MANAGE_MEMBERS);
  const canManageOrgConfig = can(currentUser, ACTIONS.MANAGE_ORG_CONFIG);

  return (
    <header>
      <div>
        <div className="logo">
          <span className="logo-icon">◈</span> FundVault
        </div>
        <div className="header-sub">Fund Management System</div>
      </div>

      <div className="header-right">
        <div className="org-badge">
          <span className="org-name">{currentOrg?.name || "—"}</span>
          <span className="role-chip">{currentUser?.role}</span>
        </div>
        <div className="user-menu-wrap">
          <div className="user-menu" onClick={() => setUserDropdownOpen(prev => !prev)}>
            <div className="user-avatar">
              {currentUser?.profile_image ? (
                <img src={currentUser.profile_image} alt="Profile" />
              ) : (
                avatar
              )}
            </div>
            <div className="user-meta">
              <div className="user-name">{currentUser?.username || "Guest"}</div>
              <div className="user-role">{currentUser?.role === "admin" ? "Admin" : "Member"}</div>
            </div>
          </div>

          {userDropdownOpen && (
            <div className="user-dropdown">
              <button
                className="user-dropdown-item"
                onClick={() => {
                  setUserDropdownOpen(false);
                  onOpenProfile();
                }}
              >
                👤 Profile
              </button>
              {canManageUsers && (
                <button
                  className="user-dropdown-item"
                  onClick={() => {
                    setUserDropdownOpen(false);
                    onOpenUserManagement();
                  }}
                >
                  🛡️ Manage Users
                </button>
              )}
              {canManageOrgConfig && (
                <button
                  className="user-dropdown-item"
                  onClick={() => {
                    setUserDropdownOpen(false);
                    onOpenOrgSettings();
                  }}
                >
                  ⚙️ Organisation settings
                </button>
              )}
              <button
                className="user-dropdown-item"
                onClick={() => {
                  setUserDropdownOpen(false);
                  onToggleTheme();
                }}
              >
                {theme === "light" ? "☀️ Light" : "🌙 Dark"}
              </button>
              <div className="user-dropdown-divider" />
              <button
                className="user-dropdown-item"
                onClick={() => {
                  setUserDropdownOpen(false);
                  onClearCache();
                }}
              >
                🗑️ Clear Cache
              </button>
              <button
                className="user-dropdown-item"
                onClick={() => {
                  setUserDropdownOpen(false);
                  onLogout();
                }}
              >
                🚪 Logout
              </button>
            </div>
          )}
        </div>

      </div>
    </header>
  );
}
