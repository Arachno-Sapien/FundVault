"use client";

import { useEffect, useState } from "react";

import Modal from "components/modals/Modal";

const BLANK_STORAGE = { endpoint_url: "", bucket: "", region: "auto", access_key: "", secret_key: "" };
const BLANK_AI = { provider: "openai_compatible", base_url: "", model: "", api_key: "" };

export default function OrgSettingsModal({ open, onClose, request, toast }) {
  const [current, setCurrent] = useState(null);
  const [storage, setStorage] = useState(BLANK_STORAGE);
  const [ai, setAi] = useState(BLANK_AI);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    request("/orgs/settings")
      .then(setCurrent)
      .catch(err => toast(err.message, "error"));
  }, [open]);

  if (!open) return null;

  const save = async (section, payload) => {
    setBusy(true);
    try {
      await request("/orgs/settings", { method: "PUT", body: JSON.stringify({ [section]: payload }) });
      toast(`${section === "ai" ? "AI provider" : "Storage"} saved and verified`, "success");
      setCurrent(await request("/orgs/settings"));
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} id="orgSettingsModal" title="Organisation settings" onClose={onClose} large>
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
      {current?.ai?.primary ? (
        <p className="hint">
          Primary: {current.ai.primary.model} (key {current.ai.primary.api_key})
        </p>
      ) : (
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
      <div className="form-actions">
        <button className="btn btn-ghost" onClick={onClose}>
          Close
        </button>
        <button className="btn btn-primary" disabled={busy} onClick={() => save("ai", { primary: ai })}>
          {busy ? "Verifying…" : "Save and verify provider"}
        </button>
      </div>
    </Modal>
  );
}
