import { formatDate } from "lib/format";

export default function AuditView({ auditLogs, filters, setFilters }) {
  const filtered = auditLogs.filter(entry => {
    const search = filters.search.toLowerCase();
    const matchesSearch =
      !search ||
      (entry.details || "").toLowerCase().includes(search) ||
      (entry.entity_type || "").toLowerCase().includes(search);
    const matchesAction = !filters.action || entry.action === filters.action;
    return matchesSearch && matchesAction;
  });

  return (
    <div className="view">
      <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: "2rem", color: "var(--primary)", marginBottom: 8 }}>
        📋 Audit Log
      </h2>
      <p style={{ color: "var(--muted)", marginBottom: 24 }}>Complete history of all actions</p>

      <div className="filter-bar filter-bar-audit">
        <div className="search-box">
          <input
            type="text"
            placeholder="Search audit log..."
            value={filters.search}
            onChange={e => setFilters(prev => ({ ...prev, search: e.target.value }))}
          />
        </div>
        <div className="filter-group">
          <label>Action</label>
          <select
            className="filter-select"
            value={filters.action}
            onChange={e => setFilters(prev => ({ ...prev, action: e.target.value }))}
          >
            <option value="">All Actions</option>
            <option value="create">Create</option>
            <option value="update">Update</option>
            <option value="delete">Delete</option>
            <option value="void">Void</option>
          </select>
        </div>
      </div>

      <div className="audit-log">
        {!filtered.length && <div className="empty-state">No audit entries found</div>}
        {filtered.slice(0, 100).map(entry => (
          <div key={entry.id} className="audit-item">
            <div>
              <div>{entry.details}</div>
              <div className="audit-meta">
                {entry.entity_type} • {entry.action}
              </div>
            </div>
            <div className="audit-time">{formatDate(entry.timestamp)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
