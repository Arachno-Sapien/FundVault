"use client";

import { useState } from "react";

import { joinOrg, previewJoinCode } from "lib/api";

export default function JoinOrgForm({ onJoined, onError }) {
  const [code, setCode] = useState("");
  const [preview, setPreview] = useState(null);
  const [form, setForm] = useState({ username: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);

  const set = (key, value) => setForm(prev => ({ ...prev, [key]: value }));

  const lookup = async () => {
    if (!code.trim()) return;
    try {
      setPreview(await previewJoinCode(code.trim().toUpperCase()));
    } catch (err) {
      setPreview(null);
      onError(err.message);
    }
  };

  const submit = async () => {
    if (form.password.length < 6) {
      onError("Password must be at least 6 characters");
      return;
    }
    setBusy(true);
    try {
      onJoined(
        await joinOrg({
          code: code.trim().toUpperCase(),
          username: form.username.trim(),
          email: form.email.trim(),
          password: form.password
        })
      );
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="org-form">
      <label>Join code</label>
      <input
        value={code}
        onChange={e => {
          setCode(e.target.value);
          setPreview(null);
        }}
        placeholder="FUNDVAULT-XXXX-XXXX"
        autoComplete="off"
        spellCheck={false}
      />
      <button type="button" className="btn btn-outline" onClick={lookup}>
        Look up
      </button>

      {preview && (
        <>
          <p className="check-ok">
            Joining <strong>{preview.org.name}</strong> as {preview.role}
          </p>

          <label>Your username</label>
          <input value={form.username} onChange={e => set("username", e.target.value)} />

          <label>Your email</label>
          <input type="email" value={form.email} onChange={e => set("email", e.target.value)} />

          <label>Password</label>
          <input
            type="password"
            value={form.password}
            onChange={e => set("password", e.target.value)}
          />

          <button type="button" className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? "Joining…" : `Join ${preview.org.name}`}
          </button>
        </>
      )}
    </div>
  );
}
