import { useState, useRef, useCallback } from "react";
import { jsPDF } from "jspdf";
import autoTable from "jspdf-autotable";

import { fmt, formatDate, formatDateShort, nowInput } from "lib/format";
import { extractReceipt } from "lib/api";

function Modal({ open, id, title, children, onClose, large = false }) {
  return (
    <div className={`overlay ${open ? "open" : ""}`} id={id} onClick={e => e.target.id === id && onClose()}>
      <div className={`modal ${large ? "modal-lg" : ""}`}>
        <div className="modal-head">
          <span className="modal-title">{title}</span>
          <button className="close-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export default function AppModals({
  modals,
  setModals,
  state,
  actions
}) {
  const close = key => setModals(prev => ({ ...prev, [key]: false }));
  const isFirstTransaction = (state.transactions || []).filter(txn => !txn.is_voided).length === 0;

  // ── AI Receipt extraction local state ───
  const [extracting, setExtracting] = useState(false);
  const [rawFile, setRawFile] = useState(null);
  const [extractConfidence, setExtractConfidence] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [imageUrl, setImageUrl] = useState("");
  const fileInputRef = useRef(null);

  const processImageFile = useCallback((file) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      actions.toast("Please select an image file", "error");
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      actions.toast("Image must be less than 5MB", "error");
      return;
    }
    setRawFile(file);
    setExtractConfidence(null);
    const reader = new FileReader();
    reader.onload = e => actions.setTxnForm(prev => ({ ...prev, receiptImage: e.target.result }));
    reader.readAsDataURL(file);
  }, [actions]);

  const handlePaste = useCallback((e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) processImageFile(file);
        return;
      }
    }
  }, [processImageFile]);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer?.files?.[0];
    if (file) processImageFile(file);
  }, [processImageFile]);

  const handleImageUrl = useCallback(async () => {
    const url = imageUrl.trim();
    if (!url) return;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch image");
      const blob = await res.blob();
      if (!blob.type.startsWith("image/")) {
        actions.toast("URL does not point to an image", "error");
        return;
      }
      const file = new File([blob], "pasted-image." + (blob.type.split("/")[1] || "png"), { type: blob.type });
      processImageFile(file);
      setImageUrl("");
    } catch {
      actions.toast("Could not load image from URL", "error");
    }
  }, [imageUrl, actions, processImageFile]);

  const confidenceBadge = (fieldHasValue) => {
    if (extractConfidence === null || !fieldHasValue) return null;
    if (extractConfidence >= 0.85) {
      return <span style={{ color: "#22c55e", fontSize: ".75rem", marginLeft: 6, fontWeight: 600 }} title={`Confidence: ${(extractConfidence * 100).toFixed(0)}%`}>✓</span>;
    }
    return <span style={{ color: "#eab308", fontSize: ".75rem", marginLeft: 6, fontWeight: 600 }} title={`Confidence: ${(extractConfidence * 100).toFixed(0)}%`}>⚠</span>;
  };

  const handleExtractReceipt = async () => {
    if (!rawFile || !state.currentUser?.token) return;
    setExtracting(true);
    try {
      const data = await extractReceipt(rawFile, state.currentUser.token);
      if (data.error) {
        actions.toast(data.error, "error");
        setExtracting(false);
        return;
      }
      setExtractConfidence(data.confidence ?? null);
      const updates = {};
      if (data.amount != null) updates.amount = String(data.amount);
      if (data.date) {
        // Convert ISO8601 to datetime-local format (YYYY-MM-DDTHH:mm)
        const dt = data.date.replace("Z", "");
        updates.date = dt.length > 16 ? dt.slice(0, 16) : dt;
      }
      if (data.sender) updates.sender = data.sender;
      if (data.receiver) updates.receiver = data.receiver;
      if (data.mode && ["electronic", "cheque", "cash"].includes(data.mode)) {
        updates.mode = data.mode;
        if (data.mode === "electronic") {
          updates.modeData = { elecId: data.reference_id || "" };
        } else if (data.mode === "cheque") {
          updates.modeData = { chequeNo: "", chequeDate: "", chequeBank: "" };
        } else {
          updates.modeData = {};
        }
      } else if (data.reference_id && state.txnForm.mode === "electronic") {
        updates.modeData = { ...state.txnForm.modeData, elecId: data.reference_id };
      }
      actions.setTxnForm(prev => ({ ...prev, ...updates }));
      actions.toast("Receipt data extracted successfully", "success");
    } catch (err) {
      actions.toast(err.message, "error");
    } finally {
      setExtracting(false);
    }
  };

  const exportCSV = () => {
    if (!state.currentDb) return;
    const txns = (state.transactions || []).filter(txn => !txn.is_voided);
    const from = state.exportForm.dateFrom;
    const to = state.exportForm.dateTo;
    let filtered = txns;
    if (from) filtered = filtered.filter(txn => new Date(txn.date) >= new Date(from));
    if (to) filtered = filtered.filter(txn => new Date(txn.date) <= new Date(`${to}T23:59:59`));

    const headers = ["Date", "Type", "Amount", "Sender", "Receiver", "Mode", "Location", "Notes", "Running Balance"];
    const rows = filtered.map(txn => [
      formatDate(txn.date),
      txn.type.toUpperCase(),
      Number(txn.amount || 0).toFixed(2),
      txn.sender || "",
      txn.receiver || "",
      txn.mode,
      txn.location || "",
      txn.notes || "",
      Number(txn.running_balance || 0).toFixed(2)
    ]);
    const csv = [headers, ...rows].map(row => row.map(col => `"${String(col).replaceAll('"', '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.currentDb.name}_transactions_${new Date().toISOString().split("T")[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    close("export");
    actions.toast("CSV exported", "success");
  };

  const exportPDF = () => {
    if (!state.currentDb) return;
    const txns = (state.transactions || []).filter(txn => !txn.is_voided);
    const from = state.exportForm.dateFrom;
    const to = state.exportForm.dateTo;
    let filtered = txns;
    if (from) filtered = filtered.filter(txn => new Date(txn.date) >= new Date(from));
    if (to) filtered = filtered.filter(txn => new Date(txn.date) <= new Date(`${to}T23:59:59`));

    const totalCr = filtered.filter(txn => txn.type === "credit").reduce((sum, txn) => sum + Number(txn.amount || 0), 0);
    const totalDr = filtered.filter(txn => txn.type === "debit").reduce((sum, txn) => sum + Number(txn.amount || 0), 0);

    const doc = new jsPDF();
    doc.setFontSize(18);
    doc.text(state.exportForm.orgName || "FundVault", 14, 20);
    doc.setFontSize(14);
    doc.text(`${state.currentDb.name} - Transaction Ledger`, 14, 30);
    doc.setFontSize(10);
    doc.text(`Generated: ${formatDate(new Date().toISOString())}`, 14, 38);
    doc.text(`Total Credits: ${fmt(totalCr)}`, 14, 48);
    doc.text(`Total Debits: ${fmt(totalDr)}`, 80, 48);
    doc.text(`Balance: ${fmt(state.currentDb.balance)}`, 140, 48);

    autoTable(doc, {
      startY: 56,
      head: [["#", "Date", "Type", "Amount", "Sender", "Receiver", "Balance"]],
      body: filtered.map((txn, idx) => [
        idx + 1,
        formatDateShort(txn.date),
        txn.type.toUpperCase(),
        Number(txn.amount || 0).toFixed(2),
        txn.sender || "-",
        txn.receiver || "-",
        Number(txn.running_balance || 0).toFixed(2)
      ])
    });
    doc.save(`${state.currentDb.name}_ledger_${new Date().toISOString().split("T")[0]}.pdf`);
    close("export");
    actions.toast("PDF exported", "success");
  };

  return (
    <>
      <Modal open={modals.profile} id="profileModal" title="Profile Settings" onClose={() => close("profile")}>
        <div className="profile-preview">
          <div className="profile-avatar">
            {state.profileForm.profileImage ? (
              <img src={state.profileForm.profileImage} alt="Profile" />
            ) : (
              <span>{(state.profileForm.username || "G")[0]?.toUpperCase()}</span>
            )}
          </div>
          <div className="profile-upload">
            <label className="upload-btn">
              Upload Photo
              <input
                type="file"
                accept="image/*"
                onChange={e => actions.updateProfileImage(e.target.files?.[0])}
              />
            </label>
            {state.profileForm.profileImage && (
              <button className="btn btn-ghost" onClick={() => actions.setProfileForm(prev => ({ ...prev, profileImage: "" }))}>
                Remove
              </button>
            )}
          </div>
        </div>

        <div className="form-group" style={{ marginBottom: 14 }}>
          <label>Username</label>
          <input value={state.profileForm.username} onChange={e => actions.setProfileForm(prev => ({ ...prev, username: e.target.value }))} />
        </div>
        <div className="form-group" style={{ marginBottom: 18 }}>
          <label>Email</label>
          <input type="email" value={state.profileForm.email} onChange={e => actions.setProfileForm(prev => ({ ...prev, email: e.target.value }))} />
        </div>

        <div className="section-label" style={{ marginBottom: 8 }}>
          Change Password (optional)
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>Current Password</label>
            <input
              type="password"
              value={state.profileForm.currentPassword}
              onChange={e => actions.setProfileForm(prev => ({ ...prev, currentPassword: e.target.value }))}
            />
          </div>
          <div className="form-group">
            <label>New Password</label>
            <input
              type="password"
              value={state.profileForm.newPassword}
              onChange={e => actions.setProfileForm(prev => ({ ...prev, newPassword: e.target.value }))}
            />
          </div>
        </div>
        <div className="form-group" style={{ marginBottom: 12 }}>
          <label>Confirm New Password</label>
          <input
            type="password"
            value={state.profileForm.confirmPassword}
            onChange={e => actions.setProfileForm(prev => ({ ...prev, confirmPassword: e.target.value }))}
          />
        </div>

        <div className="form-actions">
          <button className="btn btn-ghost" onClick={() => close("profile")}>
            Close
          </button>
          <button className="btn btn-primary" onClick={actions.saveProfile}>
            Save Changes →
          </button>
        </div>
      </Modal>

      <Modal open={modals.createDb} id="createDbModal" title="Create New Database" onClose={() => close("createDb")}>
        <div className="form-group" style={{ marginBottom: 14 }}>
          <label>Database Name</label>
          <input value={state.createDbForm.name} onChange={e => actions.setCreateDbForm(prev => ({ ...prev, name: e.target.value }))} placeholder="e.g. College Fest Fund 2025" />
        </div>
        <div className="form-group" style={{ marginBottom: 14 }}>
          <label>Description (optional)</label>
          <textarea value={state.createDbForm.description} onChange={e => actions.setCreateDbForm(prev => ({ ...prev, description: e.target.value }))} rows="2" />
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>Low Balance Alert (₹)</label>
            <input type="number" value={state.createDbForm.lowBalanceThreshold} onChange={e => actions.setCreateDbForm(prev => ({ ...prev, lowBalanceThreshold: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>Approval Threshold (₹)</label>
            <input type="number" value={state.createDbForm.approvalThreshold} onChange={e => actions.setCreateDbForm(prev => ({ ...prev, approvalThreshold: e.target.value }))} />
          </div>
        </div>
        <div className="form-actions">
          <button className="btn btn-ghost" onClick={() => close("createDb")}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={actions.createDatabase}>
            Create Database →
          </button>
        </div>
      </Modal>

      <Modal open={modals.editDb} id="editDbModal" title="Edit Database" onClose={() => close("editDb")}>
        <div className="form-group" style={{ marginBottom: 14 }}>
          <label>Database Name</label>
          <input value={state.editDbForm.name} onChange={e => actions.setEditDbForm(prev => ({ ...prev, name: e.target.value }))} />
        </div>
        <div className="form-group" style={{ marginBottom: 14 }}>
          <label>Description</label>
          <textarea value={state.editDbForm.description} onChange={e => actions.setEditDbForm(prev => ({ ...prev, description: e.target.value }))} rows="2" />
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>Low Balance Alert (₹)</label>
            <input type="number" value={state.editDbForm.lowBalanceThreshold} onChange={e => actions.setEditDbForm(prev => ({ ...prev, lowBalanceThreshold: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>Approval Threshold (₹)</label>
            <input type="number" value={state.editDbForm.approvalThreshold} onChange={e => actions.setEditDbForm(prev => ({ ...prev, approvalThreshold: e.target.value }))} />
          </div>
        </div>
        <div className="form-actions">
          <button className="btn btn-ghost" onClick={() => close("editDb")}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={actions.saveDatabaseEdit}>
            Save Changes →
          </button>
        </div>
      </Modal>

      <Modal open={modals.txn} id="txnModal" title="New Transaction" onClose={() => close("txn")}>
        <div className="section-label" style={{ marginBottom: 8 }}>
          Transaction Type
        </div>
        <div className="type-selector">
          <button
            className={`type-btn credit-hover ${state.txnForm.type === "credit" ? "active credit" : ""}`}
            onClick={() => actions.setTxnForm(prev => ({ ...prev, type: "credit" }))}
          >
            ▲ Credit
          </button>
          <button
            className={`type-btn debit-hover ${state.txnForm.type === "debit" ? "active debit" : ""}`}
            onClick={() => actions.setTxnForm(prev => ({ ...prev, type: "debit" }))}
            disabled={isFirstTransaction}
            title={isFirstTransaction ? "First transaction must be a Credit" : ""}
          >
            ▼ Debit
          </button>
        </div>

        <div className="form-row">
          <div className="form-group">
            <label>Amount (₹){confidenceBadge(state.txnForm.amount)}</label>
            <input type="number" value={state.txnForm.amount} onChange={e => actions.setTxnForm(prev => ({ ...prev, amount: e.target.value }))} min="0.01" step="0.01" />
          </div>
          <div className="form-group">
            <label>Date of Transaction{confidenceBadge(state.txnForm.date)}</label>
            <input type="datetime-local" value={state.txnForm.date} onChange={e => actions.setTxnForm(prev => ({ ...prev, date: e.target.value }))} />
          </div>
        </div>

        <div className="form-row">
          <div className="form-group">
            <label>Sender Info{confidenceBadge(state.txnForm.sender)}</label>
            <input value={state.txnForm.sender} onChange={e => actions.setTxnForm(prev => ({ ...prev, sender: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>Receiver Info{confidenceBadge(state.txnForm.receiver)}</label>
            <input value={state.txnForm.receiver} onChange={e => actions.setTxnForm(prev => ({ ...prev, receiver: e.target.value }))} />
          </div>
        </div>

        <div className="section-label" style={{ margin: "12px 0 8px" }}>
          Payment Mode
        </div>
        <div className="mode-tabs">
          {["electronic", "cheque", "cash"].map(mode => (
            <button
              key={mode}
              className={`mode-tab ${state.txnForm.mode === mode ? "active" : ""}`}
              onClick={() =>
                actions.setTxnForm(prev => ({
                  ...prev,
                  mode,
                  modeData:
                    mode === "electronic"
                      ? { elecId: "" }
                      : mode === "cheque"
                        ? { chequeNo: "", chequeDate: "", chequeBank: "" }
                        : {}
                }))
              }
            >
              {mode}
            </button>
          ))}
        </div>

        {state.txnForm.mode === "electronic" && (
          <div className="form-group">
            <label>Transaction ID / UTR Number</label>
            <input value={state.txnForm.modeData.elecId || ""} onChange={e => actions.setTxnForm(prev => ({ ...prev, modeData: { ...prev.modeData, elecId: e.target.value } }))} />
          </div>
        )}

        {state.txnForm.mode === "cheque" && (
          <>
            <div className="form-row">
              <div className="form-group">
                <label>Cheque Number</label>
                <input value={state.txnForm.modeData.chequeNo || ""} onChange={e => actions.setTxnForm(prev => ({ ...prev, modeData: { ...prev.modeData, chequeNo: e.target.value } }))} />
              </div>
              <div className="form-group">
                <label>Date on Cheque</label>
                <input type="date" value={state.txnForm.modeData.chequeDate || ""} onChange={e => actions.setTxnForm(prev => ({ ...prev, modeData: { ...prev.modeData, chequeDate: e.target.value } }))} />
              </div>
            </div>
            <div className="form-group">
              <label>Bank Name</label>
              <input value={state.txnForm.modeData.chequeBank || ""} onChange={e => actions.setTxnForm(prev => ({ ...prev, modeData: { ...prev.modeData, chequeBank: e.target.value } }))} />
            </div>
          </>
        )}

        <div className="form-group">
          <label>Location of Transaction</label>
          <input value={state.txnForm.location} onChange={e => actions.setTxnForm(prev => ({ ...prev, location: e.target.value }))} />
        </div>
        <div className="form-group">
          <label>Notes / Remarks (optional)</label>
          <textarea value={state.txnForm.notes} onChange={e => actions.setTxnForm(prev => ({ ...prev, notes: e.target.value }))} rows="2" />
        </div>
        <div className="form-group">
          <label>Attach Receipt (optional)</label>
          <div style={{ display: "flex", gap: 10, alignItems: "stretch", marginBottom: 8 }}>
            {/* File chooser */}
            <div style={{ flex: "0 0 auto" }}>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                style={{ display: "none" }}
                onChange={e => processImageFile(e.target.files?.[0])}
              />
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => fileInputRef.current?.click()}
                style={{ height: "100%", whiteSpace: "nowrap" }}
              >
                📁 Choose File
              </button>
            </div>

            {/* Paste URL input */}
            <div style={{ flex: 1, display: "flex", gap: 6 }}>
              <input
                type="text"
                value={imageUrl}
                onChange={e => setImageUrl(e.target.value)}
                onPaste={handlePaste}
                onKeyDown={e => { if (e.key === "Enter" && imageUrl.trim()) { e.preventDefault(); handleImageUrl(); } }}
                placeholder="Paste an image or image URL here..."
                style={{ flex: 1, fontSize: ".85rem" }}
              />
              {imageUrl.trim() && (
                <button type="button" className="btn btn-outline btn-sm" onClick={handleImageUrl} style={{ whiteSpace: "nowrap" }}>
                  Load
                </button>
              )}
            </div>

            {/* Drop zone */}
            <div
              onDragOver={e => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              style={{
                flex: "0 0 auto",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                padding: "8px 16px",
                border: `2px dashed ${dragOver ? "var(--accent)" : "var(--border)"}`,
                borderRadius: 8,
                color: dragOver ? "var(--accent)" : "var(--muted)",
                fontSize: ".8rem",
                cursor: "pointer",
                transition: "all .2s",
                background: dragOver ? "rgba(var(--accent-rgb, 99,102,241), 0.08)" : "transparent",
                minWidth: 120,
              }}
              onClick={() => fileInputRef.current?.click()}
            >
              <span style={{ fontSize: "1.2rem", marginBottom: 2 }}>↓</span>
              <span>Drop an image here...</span>
            </div>
          </div>

          {/* File name display */}
          {rawFile && !state.txnForm.receiptImage && (
            <div style={{ fontSize: ".82rem", color: "var(--muted)", marginBottom: 6 }}>{rawFile.name}</div>
          )}

          {/* Preview + Extract button */}
          {state.txnForm.receiptImage && (
            <div style={{ marginTop: 4, display: "flex", alignItems: "flex-start", gap: 12 }}>
              <div style={{ position: "relative" }}>
                <img src={state.txnForm.receiptImage} alt="Receipt preview" style={{ maxWidth: 160, borderRadius: 8 }} />
                <button
                  type="button"
                  onClick={() => { actions.setTxnForm(prev => ({ ...prev, receiptImage: "" })); setRawFile(null); setExtractConfidence(null); }}
                  style={{ position: "absolute", top: 4, right: 4, background: "rgba(0,0,0,0.6)", color: "#fff", border: "none", borderRadius: "50%", width: 22, height: 22, cursor: "pointer", fontSize: ".7rem", display: "flex", alignItems: "center", justifyContent: "center" }}
                  title="Remove image"
                >
                  ✕
                </button>
              </div>
              <button
                type="button"
                className="btn btn-outline btn-sm"
                disabled={extracting || !rawFile}
                onClick={handleExtractReceipt}
                style={{ whiteSpace: "nowrap", marginTop: 4 }}
              >
                {extracting ? (
                  <><span className="spinner" style={{ width: 14, height: 14, border: "2px solid var(--muted)", borderTopColor: "var(--accent)", borderRadius: "50%", display: "inline-block", animation: "spin .6s linear infinite", marginRight: 6, verticalAlign: "middle" }} /> Extracting...</>
                ) : (
                  "✨ Extract Data"
                )}
              </button>
            </div>
          )}
        </div>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        <div className="form-actions">
          <button className="btn btn-ghost" onClick={() => close("txn")}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={actions.submitTransaction}>
            Record Transaction →
          </button>
        </div>
      </Modal>

      <Modal open={modals.editTxn} id="editTxnModal" title="Edit Transaction" onClose={() => close("editTxn")} large>
        <input type="hidden" value={state.editTxnForm.id || ""} />
        <div className="form-row">
          <div className="form-group">
            <label>Amount (₹)</label>
            <input type="number" value={state.editTxnForm.amount} onChange={e => actions.setEditTxnForm(prev => ({ ...prev, amount: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>Date</label>
            <input type="datetime-local" value={state.editTxnForm.date} onChange={e => actions.setEditTxnForm(prev => ({ ...prev, date: e.target.value }))} />
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>Sender</label>
            <input value={state.editTxnForm.sender} onChange={e => actions.setEditTxnForm(prev => ({ ...prev, sender: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>Receiver</label>
            <input value={state.editTxnForm.receiver} onChange={e => actions.setEditTxnForm(prev => ({ ...prev, receiver: e.target.value }))} />
          </div>
        </div>
        <div className="form-group">
          <label>Location</label>
          <input value={state.editTxnForm.location} onChange={e => actions.setEditTxnForm(prev => ({ ...prev, location: e.target.value }))} />
        </div>
        <div className="form-group">
          <label>Notes</label>
          <textarea value={state.editTxnForm.notes} onChange={e => actions.setEditTxnForm(prev => ({ ...prev, notes: e.target.value }))} rows="2" />
        </div>
        <div className="form-actions">
          <button className="btn btn-ghost" onClick={() => close("editTxn")}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={actions.saveTransactionEdit}>
            Save Changes →
          </button>
        </div>
      </Modal>

      <Modal open={modals.voidTxn} id="voidTxnModal" title="Void Transaction" onClose={() => close("voidTxn")}>
        <p style={{ color: "var(--muted)", marginBottom: 14 }}>
          Voiding a transaction keeps it in the audit trail but reverses its effect on the balance.
        </p>
        <div className="form-group">
          <label>Reason for Voiding</label>
          <textarea value={state.voidReason} onChange={e => actions.setVoidReason(e.target.value)} rows="3" />
        </div>
        <div className="form-actions">
          <button className="btn btn-ghost" onClick={() => close("voidTxn")}>
            Cancel
          </button>
          <button className="btn btn-danger" onClick={actions.confirmVoidTransaction}>
            Void Transaction
          </button>
        </div>
      </Modal>

      <Modal open={modals.merge} id="mergeModal" title="Merge Databases" onClose={() => close("merge")}>
        <p style={{ color: "var(--muted)", marginBottom: 16 }}>
          Combine two databases into one. All transactions will be merged and balances recalculated.
        </p>
        <div className="form-row">
          <div className="form-group">
            <label>Source Database</label>
            <select value={state.mergeForm.sourceId} onChange={e => actions.setMergeForm(prev => ({ ...prev, sourceId: e.target.value }))}>
              <option value="">Select source</option>
              {state.databases.filter(db => !db.is_deleted).map(db => (
                <option key={db.id} value={db.id}>
                  {db.name}
                </option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>Target Database</label>
            <select value={state.mergeForm.targetId} onChange={e => actions.setMergeForm(prev => ({ ...prev, targetId: e.target.value }))}>
              <option value="">Select target</option>
              {state.databases.filter(db => !db.is_deleted).map(db => (
                <option key={db.id} value={db.id}>
                  {db.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="form-group">
          <label>New Database Name</label>
          <input value={state.mergeForm.name} onChange={e => actions.setMergeForm(prev => ({ ...prev, name: e.target.value }))} />
        </div>
        <div className="form-actions">
          <button className="btn btn-ghost" onClick={() => close("merge")}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={actions.mergeDatabases}>
            Merge Databases →
          </button>
        </div>
      </Modal>

      <Modal open={modals.recurring} id="recurringModal" title="Recurring Transactions" onClose={() => close("recurring")} large>
        <div style={{ marginBottom: 16 }}>
          {!state.recurringItems.length && <div style={{ color: "var(--muted)" }}>No recurring transactions set up</div>}
          {state.recurringItems.map(item => (
            <div key={item.id} className="trash-item" style={{ padding: 12, display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <div>
                <div>
                  {item.type === "credit" ? "▲" : "▼"} {fmt(item.amount)} - {item.description}
                </div>
                <div style={{ color: "var(--muted)", fontSize: ".8rem" }}>
                  {item.frequency} • Next: {formatDateShort(item.next_run)}
                </div>
              </div>
              <button className="btn btn-danger btn-sm" onClick={() => actions.deleteRecurring(item.id)}>
                🗑️
              </button>
            </div>
          ))}
        </div>

        <div className="section-label" style={{ marginBottom: 8 }}>
          Add Recurring Transaction
        </div>
        <div className="type-selector" style={{ marginBottom: 16 }}>
          <button
            className={`type-btn credit-hover ${state.recurringForm.type === "credit" ? "active credit" : ""}`}
            onClick={() => actions.setRecurringForm(prev => ({ ...prev, type: "credit" }))}
          >
            ▲ Credit
          </button>
          <button
            className={`type-btn debit-hover ${state.recurringForm.type === "debit" ? "active debit" : ""}`}
            onClick={() => actions.setRecurringForm(prev => ({ ...prev, type: "debit" }))}
          >
            ▼ Debit
          </button>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>Amount (₹)</label>
            <input type="number" value={state.recurringForm.amount} onChange={e => actions.setRecurringForm(prev => ({ ...prev, amount: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>Frequency</label>
            <select value={state.recurringForm.frequency} onChange={e => actions.setRecurringForm(prev => ({ ...prev, frequency: e.target.value }))}>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
              <option value="yearly">Yearly</option>
            </select>
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label>Description</label>
            <input value={state.recurringForm.description} onChange={e => actions.setRecurringForm(prev => ({ ...prev, description: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>Start Date</label>
            <input type="date" value={state.recurringForm.nextRun} onChange={e => actions.setRecurringForm(prev => ({ ...prev, nextRun: e.target.value }))} />
          </div>
        </div>
        <div className="form-actions">
          <button className="btn btn-primary" onClick={actions.addRecurring}>
            Add Recurring →
          </button>
        </div>
      </Modal>

      <Modal open={modals.export} id="exportModal" title="Export Data" onClose={() => close("export")}>
        <div className="section-label" style={{ marginBottom: 10 }}>
          Date Range
        </div>
        <div className="form-row" style={{ marginBottom: 16 }}>
          <div className="form-group">
            <label>From</label>
            <input type="date" value={state.exportForm.dateFrom} onChange={e => actions.setExportForm(prev => ({ ...prev, dateFrom: e.target.value }))} />
          </div>
          <div className="form-group">
            <label>To</label>
            <input type="date" value={state.exportForm.dateTo} onChange={e => actions.setExportForm(prev => ({ ...prev, dateTo: e.target.value }))} />
          </div>
        </div>
        <div className="form-group">
          <label>Organisation / Letterhead Name</label>
          <input value={state.exportForm.orgName} onChange={e => actions.setExportForm(prev => ({ ...prev, orgName: e.target.value }))} />
        </div>
        <div className="form-actions">
          <button className="btn btn-ghost" onClick={() => close("export")}>
            Cancel
          </button>
          <button className="btn btn-outline" onClick={exportCSV}>
            📄 Export CSV
          </button>
          <button className="btn btn-primary" onClick={exportPDF}>
            📕 Export PDF
          </button>
        </div>
      </Modal>

      <Modal open={modals.receipt} id="receiptModal" title="Receipt Image" onClose={() => close("receipt")}>
        {state.selectedReceipt && <img src={state.selectedReceipt} alt="Receipt" style={{ maxWidth: "100%", maxHeight: "60vh", borderRadius: 8 }} />}
      </Modal>

      <Modal open={modals.userManagement} id="userManagementModal" title="🛡️ User Management" onClose={() => close("userManagement")} large>
        <p style={{ color: "var(--muted)", marginBottom: 12 }}>
          Admin-only controls for updating user details, roles, status, password, and accounts.
        </p>
        <div className="form-actions" style={{ justifyContent: "flex-end", marginBottom: 12 }}>
          <button className="btn btn-outline btn-sm" onClick={actions.refreshManagedUsers}>
            ↻ Refresh
          </button>
        </div>
        {!state.managedUsers.length && <div style={{ textAlign: "center", padding: 20, color: "var(--muted)" }}>No users loaded</div>}
        {state.managedUsers.map(user => (
          <div key={user.id} className="trash-item" style={{ padding: 12, marginBottom: 8, display: "flex", justifyContent: "space-between", gap: 12 }}>
            <div>
              <div>
                👤 {user.username} {user.role === "admin" ? <span className="txn-type credit">ADMIN</span> : <span className="txn-type debit">MEMBER</span>}
              </div>
              <div style={{ color: "var(--muted)", fontSize: ".82rem" }}>
                {user.email} • {user.is_active ? "Active" : "Inactive"} • Joined {formatDate(user.created_at)}
              </div>
              <div style={{ color: "var(--muted)", fontSize: ".82rem" }}>
                {user.active_database_count} active DB(s) • {user.transaction_count} transaction(s)
              </div>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              <button className="btn btn-outline btn-sm" onClick={() => actions.editManagedUser(user)}>
                ✏️ Edit
              </button>
              <button className="btn btn-outline btn-sm" onClick={() => actions.toggleManagedUserRole(user)}>
                {user.role === "admin" ? "Set Member" : "Set Admin"}
              </button>
              <button className="btn btn-outline btn-sm" onClick={() => actions.toggleManagedUserStatus(user)}>
                {user.is_active ? "Deactivate" : "Activate"}
              </button>
              <button className="btn btn-outline btn-sm" onClick={() => actions.resetManagedUserPassword(user)}>
                🔐 Reset Password
              </button>
              {user.id !== state.currentUser?.id && (
                <button className="btn btn-danger btn-sm" onClick={() => actions.deleteManagedUser(user)}>
                  🗑️ Delete
                </button>
              )}
            </div>
          </div>
        ))}
      </Modal>

      <Modal open={modals.shortcuts} id="shortcutsModal" title="⌨️ Keyboard Shortcuts" onClose={() => close("shortcuts")}>
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "10px 18px" }}>
          <span>N</span>
          <span style={{ color: "var(--muted)" }}>New Transaction</span>
          <span>D</span>
          <span style={{ color: "var(--muted)" }}>New Database</span>
          <span>Esc</span>
          <span style={{ color: "var(--muted)" }}>Close Modal</span>
          <span>H</span>
          <span style={{ color: "var(--muted)" }}>Go to Home</span>
          <span>T</span>
          <span style={{ color: "var(--muted)" }}>Toggle Theme</span>
          <span>?</span>
          <span style={{ color: "var(--muted)" }}>Show Shortcuts</span>
          <span>1-4</span>
          <span style={{ color: "var(--muted)" }}>Switch Tabs</span>
        </div>
      </Modal>
    </>
  );
}
