"use client";

import { useEffect, useState } from "react";

import Modal from "components/modals/Modal";
import { formatDate } from "lib/format";
import { ACTIONS, can } from "lib/permissions";

const LABEL = { admin: "Admin", member: "Member", viewer: "Viewer" };

const statusOf = row => {
  if (row.revoked) return "Revoked";
  if (new Date(row.expires_at) < new Date()) return "Expired";
  if (row.uses >= row.max_uses) return "Used up";
  return "Active";
};

export default function JoinCodesModal({ open, onClose, request, toast, currentUser }) {
  const roles = can(currentUser, ACTIONS.MINT_ADMIN_CODE) ? ["member", "viewer", "admin"] : ["member", "viewer"];
  const [codes, setCodes] = useState([]);
  const [form, setForm] = useState({ role: "member", maxUses: 1, expiresInDays: 14 });
  const [busy, setBusy] = useState(false);

  const load = () =>
    request("/orgs/codes")
      .then(setCodes)
      .catch(err => toast(err.message, "error"));

  useEffect(() => {
    if (open) load();
  }, [open]);

  if (!open) return null;

  const copy = async code => {
    try {
      await navigator.clipboard.writeText(code);
      toast("Join code copied", "success");
    } catch {
      toast("Couldn't copy — select the code and copy it by hand", "error");
    }
  };

  const create = async () => {
    setBusy(true);
    try {
      const row = await request("/orgs/codes", {
        method: "POST",
        body: JSON.stringify({
          role: form.role,
          maxUses: Number(form.maxUses),
          expiresInDays: Number(form.expiresInDays)
        })
      });
      setCodes(prev => [row, ...prev]);
      await copy(row.code);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async code => {
    if (!window.confirm("Revoke this join code? Anyone who hasn't used it yet won't be able to.")) return;
    try {
      await request(`/orgs/codes/${encodeURIComponent(code)}`, { method: "DELETE" });
      setCodes(prev => prev.map(row => (row.code === code ? { ...row, revoked: true } : row)));
      toast("Join code revoked", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };

  return (
    <Modal open={open} id="joinCodesModal" title="Invite members" onClose={onClose} large>
      <p className="hint">
        Share a code with the person you&apos;re inviting. They choose &quot;Join an organisation&quot; on the
        sign-in screen and paste it.
      </p>
      <div className="form-row">
        <div className="form-group">
          <label>Role</label>
          <select value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}>
            {roles.map(role => (
              <option key={role} value={role}>
                {LABEL[role]}
              </option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label>Uses</label>
          <input
            type="number"
            min="1"
            max="100"
            value={form.maxUses}
            onChange={e => setForm({ ...form, maxUses: e.target.value })}
          />
        </div>
        <div className="form-group">
          <label>Expires in (days)</label>
          <input
            type="number"
            min="1"
            max="90"
            value={form.expiresInDays}
            onChange={e => setForm({ ...form, expiresInDays: e.target.value })}
          />
        </div>
      </div>
      <button className="btn btn-primary" onClick={create} disabled={busy}>
        {busy ? "Creating…" : "Create code"}
      </button>

      <div className="section-label" style={{ margin: "20px 0 8px" }}>
        Codes
      </div>
      {codes.length === 0 ? (
        <p className="hint">No join codes yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Code</th>
                <th>Role</th>
                <th>Uses</th>
                <th>Expires</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {codes.map(row => {
                const status = statusOf(row);
                return (
                  <tr key={row.code}>
                    <td>
                      <code>{row.code}</code>
                    </td>
                    <td>{LABEL[row.grants_role] || row.grants_role}</td>
                    <td>
                      {row.uses} / {row.max_uses}
                    </td>
                    <td>{formatDate(row.expires_at)}</td>
                    <td>{status}</td>
                    <td>
                      {status === "Active" && (
                        <>
                          <button className="btn btn-sm btn-outline" onClick={() => copy(row.code)}>
                            Copy
                          </button>{" "}
                          <button className="btn btn-sm btn-ghost" onClick={() => revoke(row.code)}>
                            Revoke
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}
