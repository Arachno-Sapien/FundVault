"use client";

import { useState } from "react";

import CreateOrgForm from "components/auth/CreateOrgForm";
import JoinOrgForm from "components/auth/JoinOrgForm";
import { apiRequest, lookupOrgs } from "lib/api";

export default function OrgGateway({ onAuthenticated, onError }) {
  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [orgs, setOrgs] = useState(null);
  const [chosen, setChosen] = useState(null);
  const [credentials, setCredentials] = useState({ username: "", password: "" });

  const findOrgs = async () => {
    if (!email.trim()) {
      onError("Enter your email");
      return;
    }
    try {
      const result = await lookupOrgs(email.trim());
      setOrgs(result.orgs);
      if (result.orgs.length === 1) setChosen(result.orgs[0]);
    } catch (err) {
      onError(err.message);
    }
  };

  const signIn = async () => {
    try {
      const response = await apiRequest("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          orgId: chosen.id,
          username: credentials.username || email.trim(),
          password: credentials.password
        })
      });
      onAuthenticated({ ...response, org: chosen });
    } catch (err) {
      onError(err.message);
    }
  };

  return (
    <div className="auth-shell">
      <h1>FundVault</h1>
      <div className="auth-tabs">
        <button className={mode === "signin" ? "active" : ""} onClick={() => setMode("signin")}>
          Sign in
        </button>
        <button className={mode === "join" ? "active" : ""} onClick={() => setMode("join")}>
          Join with a code
        </button>
        <button className={mode === "create" ? "active" : ""} onClick={() => setMode("create")}>
          Create an organisation
        </button>
      </div>

      {mode === "signin" && (
        <div className="org-form">
          <label>Email</label>
          <input
            type="email"
            value={email}
            onChange={e => {
              setEmail(e.target.value);
              setOrgs(null);
              setChosen(null);
            }}
          />
          {!orgs && (
            <button type="button" className="btn btn-primary" onClick={findOrgs}>
              Continue
            </button>
          )}

          {orgs && orgs.length === 0 && (
            <p className="check-bad">
              No organisations found for that email. Create one, or ask an admin for a join code.
            </p>
          )}

          {orgs && orgs.length > 0 && !chosen && (
            <>
              <p className="hint">Choose an organisation</p>
              {orgs.map(org => (
                <button key={org.id} type="button" className="org-choice" onClick={() => setChosen(org)}>
                  {org.name}
                </button>
              ))}
            </>
          )}

          {chosen && (
            <>
              <p className="hint">
                Signing in to <strong>{chosen.name}</strong>{" "}
                {orgs.length > 1 && (
                  <button type="button" className="linkish" onClick={() => setChosen(null)}>
                    change
                  </button>
                )}
              </p>
              <label>Username</label>
              <input
                value={credentials.username}
                onChange={e => setCredentials(prev => ({ ...prev, username: e.target.value }))}
                placeholder={email}
              />
              <label>Password</label>
              <input
                type="password"
                value={credentials.password}
                onChange={e => setCredentials(prev => ({ ...prev, password: e.target.value }))}
                onKeyDown={e => e.key === "Enter" && signIn()}
              />
              <button type="button" className="btn btn-primary" onClick={signIn}>
                Sign in
              </button>
            </>
          )}
        </div>
      )}

      {mode === "join" && <JoinOrgForm onJoined={onAuthenticated} onError={onError} />}
      {mode === "create" && <CreateOrgForm onCreated={onAuthenticated} onError={onError} />}
    </div>
  );
}
