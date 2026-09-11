import { fmt, formatDateShort, relativeTime } from "lib/format";

export default function HomeView({
  databases,
  auditLogs,
  onOpenDb,
  onOpenCreateDb,
  onOpenMerge,
  onEditDbFromCard,
  onDeleteDb,
  canManageFunds = false
}) {
  const activeDbs = databases.filter(db => !db.is_deleted);
  const recent = auditLogs.slice(0, 5);

  return (
    <div className="view">
      <div className="home-hero">
        <h1>
          Manage your
          <br />
          <em>fund databases</em> with precision
        </h1>
        <p>Create named fund databases, record detailed transactions, & track live balances.</p>
      </div>


      <div className="section-header">
        <div className="section-label">Your Databases</div>
        <div className="section-actions">
          {canManageFunds && (
            <button className="btn btn-primary btn-sm" onClick={onOpenCreateDb}>
              + New Database
            </button>
          )}
          {canManageFunds && (
            <button className="btn btn-outline btn-sm" onClick={onOpenMerge}>
              ⚙️ Merge Databases
            </button>
          )}
        </div>
      </div>

      <div className="db-grid">
        {activeDbs.length === 0 && (
          <div className="db-card" style={{ gridColumn: "1/-1", textAlign: "center" }}>
            No databases yet. Click <strong>+ New Database</strong> to get started.
          </div>
        )}

        {activeDbs.map(db => {
          const txnCount = db.transactions?.filter(t => !t.is_voided).length || 0;
          return (
            <div
              key={db.id}
              className={`db-card ${db.is_archived ? "archived" : ""}`}
              onClick={() => onOpenDb(db.id)}
            >
              <div className="db-card-head">
                <div className="db-card-name">◈ {db.name}</div>
                {canManageFunds && (
                  <div className="db-card-actions" onClick={e => e.stopPropagation()}>
                    <button className="btn btn-ghost btn-sm" onClick={() => onEditDbFromCard(db.id)}>
                      ✏️
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={() => onDeleteDb(db.id)}>
                      🗑️
                    </button>
                  </div>
                )}
              </div>
              <div className="db-card-desc">{db.description || ""}</div>
              <div className="db-card-bal">{fmt(db.balance)}</div>
              <div className="db-card-meta">
                {txnCount} transaction{txnCount !== 1 ? "s" : ""} • Created {formatDateShort(db.created_at)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
