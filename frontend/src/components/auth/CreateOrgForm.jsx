"use client";

import { useState } from "react";

import { createOrg, validateConnection } from "lib/api";

const PRESETS = [
  { id: "supabase", label: "Supabase", hint: "Project settings → Database → Connection string → URI. Use the pooled string on port 6543." },
  { id: "neon", label: "Neon", hint: "Dashboard → Connection Details → Connection string." },
  { id: "railway", label: "Railway", hint: "Postgres service → Variables → DATABASE_URL." },
  { id: "other", label: "Other Postgres", hint: "Any postgres:// URL your server can reach." }
];

export default function CreateOrgForm({ onCreated, onError }) {
  const [preset, setPreset] = useState("supabase");
  const [form, setForm] = useState({
    name: "",
    databaseUrl: "",
    username: "",
    email: "",
    password: ""
  });
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const set = (key, value) => setForm(prev => ({ ...prev, [key]: value }));

  const runCheck = async () => {
    if (!form.databaseUrl.trim()) return;
    setChecking(true);
    setCheckResult(null);
    try {
      setCheckResult(await validateConnection(form.databaseUrl.trim()));
    } catch (err) {
      setCheckResult({ ok: false, message: err.message });
    } finally {
      setChecking(false);
    }
  };

  const submit = async () => {
    if (!form.name.trim() || !form.databaseUrl.trim()) {
      onError("Organisation name and database URL are required");
      return;
    }
    if (form.password.length < 6) {
      onError("Password must be at least 6 characters");
      return;
    }
    setBusy(true);
    try {
      const result = await createOrg({
        name: form.name.trim(),
        databaseUrl: form.databaseUrl.trim(),
        username: form.username.trim(),
        email: form.email.trim(),
        password: form.password
      });
      onCreated(result);
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const active = PRESETS.find(item => item.id === preset);

  return (
    <div className="org-form">
      <label>Organisation name</label>
      <input value={form.name} onChange={e => set("name", e.target.value)} placeholder="Acme Funds" />

      <label>Database provider</label>
      <div className="preset-row">
        {PRESETS.map(item => (
          <button
            key={item.id}
            type="button"
            className={`preset ${preset === item.id ? "active" : ""}`}
            onClick={() => setPreset(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <p className="hint">{active.hint}</p>

      <label>Connection string</label>
      <input
        value={form.databaseUrl}
        onChange={e => {
          set("databaseUrl", e.target.value);
          setCheckResult(null);
        }}
        placeholder="postgres://user:password@host:5432/database"
        autoComplete="off"
        spellCheck={false}
      />
      <button type="button" className="btn-secondary" onClick={runCheck} disabled={checking}>
        {checking ? "Testing…" : "Test connection"}
      </button>
      {checkResult && (
        <p className={checkResult.ok ? "check-ok" : "check-bad"}>{checkResult.message}</p>
      )}

      <hr />
      <p className="hint">You become the Owner of this organisation.</p>

      <label>Your username</label>
      <input value={form.username} onChange={e => set("username", e.target.value)} />

      <label>Your email</label>
      <input type="email" value={form.email} onChange={e => set("email", e.target.value)} />

      <label>Password</label>
      <input type="password" value={form.password} onChange={e => set("password", e.target.value)} />

      <button type="button" className="btn-primary" onClick={submit} disabled={busy}>
        {busy ? "Creating organisation…" : "Create organisation"}
      </button>
      <p className="hint">
        Creating the organisation builds its tables in your database. Nothing is saved if it fails.
      </p>
    </div>
  );
}
