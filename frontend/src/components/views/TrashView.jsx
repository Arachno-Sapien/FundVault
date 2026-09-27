import { relativeTime } from "lib/format";

export default function TrashView({ items, onRestore, onPermanentDelete, onEmptyTrash, canManageFunds = false }) {
  return (
    <div className="view">
      <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: "2rem", color: "var(--primary)", marginBottom: 8 }}>
        🗑️ Trash Bin
      </h2>
      <p style={{ color: "var(--muted)", marginBottom: 24 }}>Recover deleted fund databases</p>

      <div className="trash-list">
        {!items.length && <div className="trash-item empty-state">Trash is empty</div>}
        {items.map(item => {
          let data = {};
          try {
            data = JSON.parse(item.entity_data || "{}");
          } catch (_e) {
            data = {};
          }
          return (
            <div key={item.id} className="trash-item" style={{ padding: 14, display: "flex", justifyContent: "space-between", gap: 12 }}>
              <div className="trash-item-content">
                <div style={{ fontWeight: 600 }}>
                  {item.entity_type === "database" ? "📁" : "📝"} {data.name || item.entity_type}
                </div>
                <div className="trash-item-meta">Deleted {relativeTime(item.deleted_at)}</div>
              </div>
              {canManageFunds && (
                <div className="trash-item-actions">
                  <button className="btn btn-outline btn-sm" onClick={() => onRestore(item.id)}>
                    ↩️ Restore
                  </button>
                  <button className="btn btn-danger btn-sm" onClick={() => onPermanentDelete(item.id)}>
                    🗑️
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {canManageFunds && items.length > 0 && (
        <div className="trash-footer">
          <button className="btn btn-danger btn-sm" onClick={onEmptyTrash}>
            🗑️ Empty Trash
          </button>
        </div>
      )}
    </div>
  );
}
