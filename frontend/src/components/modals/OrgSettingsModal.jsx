"use client";

import { useEffect, useState } from "react";

import Modal from "components/modals/Modal";

const BLANK_STORAGE = { endpoint_url: "", bucket: "", region: "auto", access_key: "", secret_key: "" };
const BLANK_AI = { provider: "openai_compatible", base_url: "", model: "", api_key: "" };
const SECTION_LABEL = { ai: "AI provider", database: "Database connection" };

export default function OrgSettingsModal({ open, onClose, request, toast }) {
  const [current, setCurrent] = useState(null);
  const [storage, setStorage] = useState(BLANK_STORAGE);
  const [ai, setAi] = useState(BLANK_AI);
  const [fallback, setFallback] = useState(BLANK_AI);
  const [databaseUrl, setDatabaseUrl] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    request("/orgs/settings")
      .then(data => {
        setCurrent(data);
        // Pre-fill only the non-secret fields the API actually returns in
        // full (access_key/secret_key come back masked, never usable here).
        if (data.storage) {
          setStorage(s => ({
            ...s,
            endpoint_url: data.storage.endpoint_url,
            bucket: data.storage.bucket,
            region: data.storage.region || "auto",
          }));
        }
        if (data.ai?.primary) {
          setAi(a => ({
            ...a,
            provider: data.ai.primary.provider,
            base_url: data.ai.primary.base_url || "",
            model: data.ai.primary.model || "",
          }));
        }
        if (data.ai?.fallback) {
          setFallback(a => ({
            ...a,
            provider: data.ai.fallback.provider,
            base_url: data.ai.fallback.base_url || "",
            model: data.ai.fallback.model || "",
          }));
        }
      })
      .catch(err => toast(err.message, "error"));
  }, [open]);

  if (!open) return null;

  // A slot's api_key is never sent back by the API (only ever masked), so
  // reopening this modal always shows it blank even when one is already
  // saved. Both slots are written together as one blob (org.ai_config), so
  // silently submitting a filled-in model with a blank key here wouldn't
  // just fail that slot -- it would overwrite the whole blob and erase a
  // key that was never touched. Block that instead of losing it quietly.
  const missingKeyFor = slot => slot.model.trim() && !slot.api_key.trim();

  const saveAi = () => {
    if (missingKeyFor(ai)) {
      toast("Enter the primary provider's API key to save", "error");
      return;
    }
    if (missingKeyFor(fallback)) {
      toast("Enter the fallback provider's API key, or clear its model field to remove it", "error");
      return;
    }
    save("ai", { primary: ai, fallback });
  };

  const save = async (section, payload) => {
    setBusy(true);
    try {
      await request("/orgs/settings", { method: "PUT", body: JSON.stringify({ [section]: payload }) });
      toast(`${SECTION_LABEL[section] || "Storage"} saved and verified`, "success");
      setCurrent(await request("/orgs/settings"));
      if (section === "database") setDatabaseUrl("");
      if (section === "storage") setStorage(BLANK_STORAGE);
      if (section === "ai") {
        setAi(BLANK_AI);
        setFallback(BLANK_AI);
      }
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} id="orgSettingsModal" title="Organisation settings" onClose={onClose} large>
      <div className="section-label" style={{ marginBottom: 8 }}>
        Database connection
      </div>
      {current?.database ? (
        <p className="hint">
          Connected: {current.database.database} on {current.database.host}:{current.database.port} (user{" "}
          {current.database.username})
        </p>
      ) : (
        <p className="hint">Not configured.</p>
      )}
      <p className="hint" style={{ marginBottom: 8 }}>
        To migrate to another provider (e.g. moving off Render Postgres to Supabase, Neon, or your own
        server): restore your data into the new database yourself first, then paste its connection string
        below. This only repoints the app — it never copies data for you.
      </p>
      <div className="form-group" style={{ marginBottom: 20 }}>
        <label>Database URL</label>
        <input
          type="password"
          placeholder="postgres://user:password@host:5432/dbname"
          value={databaseUrl}
          autoComplete="off"
          onChange={e => setDatabaseUrl(e.target.value)}
        />
        <div className="form-actions" style={{ marginTop: 10 }}>
          <button
            className="btn btn-primary"
            disabled={busy || !databaseUrl.trim()}
            onClick={() => save("database", { databaseUrl: databaseUrl.trim() })}
          >
            {busy ? "Verifying…" : "Save and verify database"}
          </button>
        </div>
      </div>

      <div className="section-label" style={{ marginBottom: 8 }}>
        Receipt storage
      </div>
      {current?.storage ? (
        <p className="hint">
          Configured: {current.storage.bucket} at {current.storage.endpoint_url} (key{" "}
          {current.storage.access_key})
        </p>
      ) : (
        <p className="hint">Not configured — receipt upload is disabled.</p>
      )}
      <div className="form-group">
        <label>S3 endpoint URL</label>
        <input value={storage.endpoint_url} onChange={e => setStorage({ ...storage, endpoint_url: e.target.value })} />
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Bucket</label>
          <input value={storage.bucket} onChange={e => setStorage({ ...storage, bucket: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Region</label>
          <input placeholder="auto" value={storage.region} onChange={e => setStorage({ ...storage, region: e.target.value })} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>Access key</label>
          <input value={storage.access_key} autoComplete="off" onChange={e => setStorage({ ...storage, access_key: e.target.value })} />
        </div>
        <div className="form-group">
          <label>Secret key</label>
          <input type="password" value={storage.secret_key} autoComplete="off" onChange={e => setStorage({ ...storage, secret_key: e.target.value })} />
        </div>
      </div>
      <div className="form-actions" style={{ marginBottom: 20 }}>
        <button className="btn btn-primary" disabled={busy} onClick={() => save("storage", storage)}>
          {busy ? "Verifying…" : "Save and verify storage"}
        </button>
      </div>

      <div className="section-label" style={{ marginBottom: 8 }}>
        Receipt extraction
      </div>
      {current?.ai?.primary && (
        <p className="hint">
          Primary: {current.ai.primary.model} (key {current.ai.primary.api_key})
        </p>
      )}
      {current?.ai?.fallback && (
        <p className="hint">
          Fallback: {current.ai.fallback.model} (key {current.ai.fallback.api_key})
        </p>
      )}
      {!current?.ai?.primary && !current?.ai?.fallback && (
        <p className="hint">Not configured — receipt extraction is disabled.</p>
      )}
      <div className="form-group">
        <label>Provider</label>
        <select value={ai.provider} onChange={e => setAi({ ...ai, provider: e.target.value })}>
          <option value="openai_compatible">OpenAI-compatible (NVIDIA, OpenRouter, Groq, vLLM)</option>
          <option value="gemini">Google Gemini</option>
        </select>
      </div>
      {ai.provider === "openai_compatible" && (
        <div className="form-group">
          <label>Base URL</label>
          <input placeholder="e.g. https://integrate.api.nvidia.com/v1" value={ai.base_url} onChange={e => setAi({ ...ai, base_url: e.target.value })} />
        </div>
      )}
      <div className="form-row">
        <div className="form-group">
          <label>Model name</label>
          <input value={ai.model} onChange={e => setAi({ ...ai, model: e.target.value })} />
        </div>
        <div className="form-group">
          <label>API key</label>
          <input type="password" value={ai.api_key} autoComplete="off" onChange={e => setAi({ ...ai, api_key: e.target.value })} />
        </div>
      </div>

      <div className="section-label" style={{ marginTop: 20, marginBottom: 8 }}>
        Fallback provider (optional)
      </div>
      <p className="hint" style={{ marginBottom: 8 }}>
        Tried automatically if the primary provider fails or is overloaded.
      </p>
      <div className="form-group">
        <label>Provider</label>
        <select value={fallback.provider} onChange={e => setFallback({ ...fallback, provider: e.target.value })}>
          <option value="openai_compatible">OpenAI-compatible (NVIDIA, OpenRouter, Groq, vLLM)</option>
          <option value="gemini">Google Gemini</option>
        </select>
      </div>
      {fallback.provider === "openai_compatible" && (
        <div className="form-group">
          <label>Base URL</label>
          <input placeholder="e.g. https://integrate.api.nvidia.com/v1" value={fallback.base_url} onChange={e => setFallback({ ...fallback, base_url: e.target.value })} />
        </div>
      )}
      <div className="form-row">
        <div className="form-group">
          <label>Model name</label>
          <input value={fallback.model} onChange={e => setFallback({ ...fallback, model: e.target.value })} />
        </div>
        <div className="form-group">
          <label>API key</label>
          <input type="password" value={fallback.api_key} autoComplete="off" onChange={e => setFallback({ ...fallback, api_key: e.target.value })} />
        </div>
      </div>

      <div className="form-actions">
        <button className="btn btn-ghost" onClick={onClose}>
          Close
        </button>
        <button className="btn btn-primary" disabled={busy} onClick={saveAi}>
          {busy ? "Verifying…" : "Save and verify provider"}
        </button>
      </div>
    </Modal>
  );
}
