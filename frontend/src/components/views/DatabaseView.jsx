import { useMemo, useState } from "react";

import { fmt, formatDate } from "lib/format";

function getModeDetails(txn) {
  const modeData = txn.mode_data || {};
  if (txn.mode === "electronic") return modeData.elecId ? `TXN ID: ${modeData.elecId}` : "—";
  if (txn.mode === "cheque") {
    const parts = [];
    if (modeData.chequeNo) parts.push(`Chq#: ${modeData.chequeNo}`);
    if (modeData.chequeDate) parts.push(`Dt: ${modeData.chequeDate}`);
    if (modeData.chequeBank) parts.push(`Bank: ${modeData.chequeBank}`);
    return parts.join(" • ") || "—";
  }
  if (txn.mode === "cash") return "Cash";
  return "—";
}

export default function DatabaseView({
  database,
  transactions,
  filters,
  setFilters,
  onGoHome,
  onOpenEditDb,
  onArchiveDb,
  onOpenRecurring,
  onOpenExport,
  onPrint,
  onOpenNewTxn,
  onOpenEditTxn,
  onOpenVoidTxn,
  onApproveTxn,
  onDeleteVoidedTxn,
  onViewReceipt,
  permissions = {}
}) {
  const [sortColumn, setSortColumn] = useState(null);
  const [sortDirection, setSortDirection] = useState("asc");
  if (!database) return null;

  const toggleSort = column => {
    if (sortColumn === column) {
      setSortDirection(prev => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    setSortColumn(column);
    setSortDirection("asc");
  };

  const filtered = transactions.filter(txn => {
    const search = filters.search.toLowerCase();
    const searchable = `${txn.sender || ""} ${txn.receiver || ""} ${txn.location || ""} ${txn.id} ${txn.notes || ""}`.toLowerCase();
    if (search && !searchable.includes(search)) return false;
    if (filters.type && txn.type !== filters.type) return false;
    if (filters.mode && txn.mode !== filters.mode) return false;
    if (filters.dateFrom && new Date(txn.date) < new Date(filters.dateFrom)) return false;
    if (filters.dateTo && new Date(txn.date) > new Date(`${filters.dateTo}T23:59:59`)) return false;
    const amountMin = Number(filters.amountMin || 0);
    const amountMax = filters.amountMax ? Number(filters.amountMax) : Number.POSITIVE_INFINITY;
    if (Number(txn.amount) < amountMin || Number(txn.amount) > amountMax) return false;
    return true;
  });

  const sorted = useMemo(() => {
    if (!sortColumn) return filtered;
    const list = [...filtered];
    list.sort((a, b) => {
      let left;
      let right;
      if (sortColumn === "date") {
        left = new Date(a.date).getTime();
        right = new Date(b.date).getTime();
      } else if (sortColumn === "amount") {
        left = Number(a.amount || 0);
        right = Number(b.amount || 0);
      } else if (sortColumn === "type") {
        left = String(a.type || "");
        right = String(b.type || "");
      } else {
        left = 0;
        right = 0;
      }
      if (left < right) return sortDirection === "asc" ? -1 : 1;
      if (left > right) return sortDirection === "asc" ? 1 : -1;
      return 0;
    });
    return list;
  }, [filtered, sortColumn, sortDirection]);

  const sortIcon = column => {
    if (sortColumn !== column) return "↕";
    return sortDirection === "asc" ? "↑" : "↓";
  };

  const activeTransactions = transactions.filter(txn => !txn.is_voided);
  const totalCr = activeTransactions.filter(txn => txn.type === "credit").reduce((sum, txn) => sum + Number(txn.amount || 0), 0);
  const totalDr = activeTransactions.filter(txn => txn.type === "debit").reduce((sum, txn) => sum + Number(txn.amount || 0), 0);
  const lowBalance = Number(database.low_balance_threshold || 0) > 0 && Number(database.balance || 0) < Number(database.low_balance_threshold || 0);

  return (
    <div className="view">
      <button className="btn btn-ghost btn-sm" onClick={onGoHome}>
        ← Back to databases
      </button>

      <div className="db-header db-header-main">
        <div className="db-title-wrap">
          <div className="db-title">{database.name}</div>
          <div className="db-subtext">{database.description || ""}</div>
          <div className="db-subtext">Created on {formatDate(database.created_at)}</div>
          <div className="db-actions">
            {permissions.manageFunds && (
              <button className="btn btn-outline btn-sm" onClick={onOpenEditDb}>
                ✏️ Edit
              </button>
            )}
            {permissions.manageFunds && (
              <button className="btn btn-outline btn-sm" onClick={onArchiveDb}>
                📁 {database.is_archived ? "Unarchive" : "Archive"}
              </button>
            )}
            {permissions.manageFunds && (
              <button className="btn btn-outline btn-sm" onClick={onOpenRecurring}>
                🔄 Recurring
              </button>
            )}
            <button className="btn btn-outline btn-sm" onClick={onOpenExport}>
              📤 Export
            </button>
            <button className="btn btn-outline btn-sm" onClick={onPrint}>
              🖨️ Print
            </button>
          </div>
        </div>
        <div className="balance-panel">
          <div className="balance-label">Current Balance</div>
          <div className="balance-amount">{fmt(database.balance)}</div>
          {lowBalance && <div className="low-balance-note">⚠️ Below threshold</div>}
        </div>
      </div>

      <div className="summary-row">
        <div className="chip">
          <div className="chip-label">Total Credits</div>
          <div className="chip-val credit">{fmt(totalCr)}</div>
        </div>
        <div className="chip">
          <div className="chip-label">Total Debits</div>
          <div className="chip-val debit">{fmt(totalDr)}</div>
        </div>
        <div className="chip">
          <div className="chip-label">Net Balance</div>
          <div className="chip-val neutral">{fmt(totalCr - totalDr)}</div>
        </div>
        <div className="chip">
          <div className="chip-label">Transactions</div>
          <div className="chip-val neutral">{activeTransactions.length}</div>
        </div>
      </div>

      <div className="filter-bar filter-bar-ledger">
        <div className="search-box">
          <input value={filters.search} onChange={e => setFilters(prev => ({ ...prev, search: e.target.value }))} placeholder="Search transactions..." />
        </div>
        <div className="filter-group">
          <label>Type</label>
          <select className="filter-select" value={filters.type} onChange={e => setFilters(prev => ({ ...prev, type: e.target.value }))}>
            <option value="">All</option>
            <option value="credit">Credit</option>
            <option value="debit">Debit</option>
          </select>
        </div>
        <div className="filter-group">
          <label>Mode</label>
          <select className="filter-select" value={filters.mode} onChange={e => setFilters(prev => ({ ...prev, mode: e.target.value }))}>
            <option value="">All</option>
            <option value="electronic">Electronic</option>
            <option value="cheque">Cheque</option>
            <option value="cash">Cash</option>
          </select>
        </div>
        <div className="filter-group">
          <label>From</label>
          <input type="date" className="filter-input" value={filters.dateFrom} onChange={e => setFilters(prev => ({ ...prev, dateFrom: e.target.value }))} />
        </div>
        <div className="filter-group">
          <label>To</label>
          <input type="date" className="filter-input" value={filters.dateTo} onChange={e => setFilters(prev => ({ ...prev, dateTo: e.target.value }))} />
        </div>
        <div className="filter-group">
          <label>Min ₹</label>
          <input type="number" className="filter-input" value={filters.amountMin} onChange={e => setFilters(prev => ({ ...prev, amountMin: e.target.value }))} />
        </div>
        <div className="filter-group">
          <label>Max ₹</label>
          <input type="number" className="filter-input" value={filters.amountMax} onChange={e => setFilters(prev => ({ ...prev, amountMax: e.target.value }))} />
        </div>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => setFilters({ search: "", type: "", mode: "", dateFrom: "", dateTo: "", amountMin: "", amountMax: "" })}
        >
          Clear
        </button>
      </div>

      <div className="ledger-head">
        <div className="section-label ledger-label">
          Transaction Ledger
        </div>
        {permissions.createTxn && (
          <button className="btn btn-primary btn-sm" onClick={onOpenNewTxn}>
            + New Transaction
          </button>
        )}
      </div>

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th onClick={() => toggleSort("date")}>
                Date & Time {sortIcon("date")}
              </th>
              <th onClick={() => toggleSort("type")}>
                Type {sortIcon("type")}
              </th>
              <th onClick={() => toggleSort("amount")}>
                Amount {sortIcon("amount")}
              </th>
              <th>Sender</th>
              <th>Receiver</th>
              <th>Mode</th>
              <th>Details</th>
              <th>Location</th>
              <th>Running Balance</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {!filtered.length && (
              <tr>
                <td colSpan="11" className="table-empty">
                  No transactions match your filters
                </td>
              </tr>
            )}
            {sorted.map((txn, idx) => (
              <tr key={txn.id}>
                <td>{String(idx + 1).padStart(3, "0")}</td>
                <td>{formatDate(txn.date)}</td>
                <td>
                  {txn.is_voided ? (
                    <span className="txn-type voided">VOIDED</span>
                  ) : txn.requires_approval && !txn.approved ? (
                    <span>⏳ Pending Approval</span>
                  ) : (
                    <span className={`txn-type ${txn.type}`}>{txn.type === "credit" ? "▲ CREDIT" : "▼ DEBIT"}</span>
                  )}
                </td>
                <td className={`txn-amount ${txn.type}`}>
                  {txn.type === "credit" ? "+" : "−"} {fmt(txn.amount)}
                </td>
                <td>{txn.sender || "—"}</td>
                <td>{txn.receiver || "—"}</td>
                <td>{txn.mode}</td>
                <td>
                  <div>{getModeDetails(txn)}</div>
                  {txn.receipt_image && (
                    <button className="btn btn-ghost btn-sm" onClick={() => onViewReceipt(txn)}>
                      📎 Receipt
                    </button>
                  )}
                </td>
                <td>{txn.location || "—"}</td>
                <td>{fmt(txn.running_balance)}</td>
                <td>
                  <div className="row-actions">
                    {!txn.is_voided && permissions.modifyTxn && (
                      <>
                        <button className="btn btn-ghost btn-sm" onClick={() => onOpenEditTxn(txn)}>
                          ✏️
                        </button>
                        <button className="btn btn-danger btn-sm" onClick={() => onOpenVoidTxn(txn)}>
                          ⊘
                        </button>
                      </>
                    )}
                    {txn.is_voided && permissions.modifyTxn && (
                      <button className="btn btn-danger btn-sm" onClick={() => onDeleteVoidedTxn(txn.id)} title="Delete voided transaction">
                        🗑
                      </button>
                    )}
                    {txn.requires_approval && !txn.approved && !txn.is_voided && permissions.approve && (
                      <button className="btn btn-outline btn-sm" onClick={() => onApproveTxn(txn.id)}>
                        ✓
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
