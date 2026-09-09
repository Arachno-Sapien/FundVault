export default function AuthView({
  authTab,
  setAuthTab,
  loginForm,
  setLoginForm,
  signupForm,
  setSignupForm,
  onLogin,
  onSignup
}) {
  return (
    <div className="auth-container">
      <div className="auth-card">
        <div className="auth-logo">
          <div className="logo">
            <span className="logo-icon">◈</span> FundVault
          </div>
          <div className="header-sub">Fund Management System</div>
        </div>

        <div className="auth-tabs">
          <button className={`auth-tab ${authTab === "login" ? "active" : ""}`} onClick={() => setAuthTab("login")}>
            Login
          </button>
          <button className={`auth-tab ${authTab === "signup" ? "active" : ""}`} onClick={() => setAuthTab("signup")}>
            Sign Up
          </button>
        </div>

        <div className={`auth-form ${authTab === "login" ? "active" : ""}`}>
          <div className="form-group" style={{ marginBottom: 16 }}>
            <label>Username or Email</label>
            <input
              type="text"
              value={loginForm.username}
              onChange={e => setLoginForm(prev => ({ ...prev, username: e.target.value }))}
              placeholder="Enter username or email"
            />
          </div>
          <div className="form-group" style={{ marginBottom: 24 }}>
            <label>Password</label>
            <input
              type="password"
              value={loginForm.password}
              onChange={e => setLoginForm(prev => ({ ...prev, password: e.target.value }))}
              placeholder="Enter password"
            />
          </div>
          <button className="btn btn-primary" style={{ width: "100%" }} onClick={onLogin}>
            Login →
          </button>
        </div>

        <div className={`auth-form ${authTab === "signup" ? "active" : ""}`}>
          <div className="form-group" style={{ marginBottom: 16 }}>
            <label>Username</label>
            <input
              type="text"
              value={signupForm.username}
              onChange={e => setSignupForm(prev => ({ ...prev, username: e.target.value }))}
              placeholder="Choose a username"
            />
          </div>
          <div className="form-group" style={{ marginBottom: 16 }}>
            <label>Email</label>
            <input
              type="email"
              value={signupForm.email}
              onChange={e => setSignupForm(prev => ({ ...prev, email: e.target.value }))}
              placeholder="Enter your email"
            />
          </div>
          <div className="form-group" style={{ marginBottom: 16 }}>
            <label>Password</label>
            <input
              type="password"
              value={signupForm.password}
              onChange={e => setSignupForm(prev => ({ ...prev, password: e.target.value }))}
              placeholder="Create a password"
            />
          </div>
          <div className="form-group" style={{ marginBottom: 24 }}>
            <label>Confirm Password</label>
            <input
              type="password"
              value={signupForm.confirm}
              onChange={e => setSignupForm(prev => ({ ...prev, confirm: e.target.value }))}
              placeholder="Confirm password"
            />
          </div>
          <button className="btn btn-primary" style={{ width: "100%" }} onClick={onSignup}>
            Create Account →
          </button>
        </div>
      </div>
    </div>
  );
}
