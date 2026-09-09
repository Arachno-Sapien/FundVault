const tabs = [
  { id: "home", label: "🏠 Home" },
  { id: "dashboard", label: "📊 Dashboard" },
  { id: "audit", label: "📋 Audit Log" },
  { id: "trash", label: "🗑️ Trash" }
];

export default function NavTabs({ activeTab, onSwitch }) {
  return (
    <nav className="nav-tabs">
      {tabs.map(tab => (
        <button
          key={tab.id}
          className={`nav-tab ${activeTab === tab.id ? "active" : ""}`}
          onClick={() => onSwitch(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
