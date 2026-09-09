export default function HeaderBar({
  currentUser,
  theme,
  onToggleTheme,
  onLogout,
  onOpenUserManagement,
  onOpenProfile,
  onClearCache,
  userDropdownOpen,
  setUserDropdownOpen
}) {
  const avatar = (currentUser?.username || "G")[0]?.toUpperCase();
  const canManageUsers = currentUser?.role === "admin" && !!currentUser?.token;

  return (
    <header>
      <div>
        <div className="logo">
          <span className="logo-icon">◈</span> FundVault
        </div>
        <div className="header-sub">Fund Management System</div>
      </div>

      <div className="header-right">
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
