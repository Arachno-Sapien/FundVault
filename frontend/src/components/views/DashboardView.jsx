import { useEffect, useMemo, useRef } from "react";
import { Chart, BarController, BarElement, CategoryScale, LinearScale, PieController, ArcElement, LineController, LineElement, PointElement, Filler, Tooltip, Legend } from "chart.js";

import { fmt } from "lib/format";

Chart.register(
  BarController,
  BarElement,
  CategoryScale,
  LinearScale,
  PieController,
  ArcElement,
  LineController,
  LineElement,
  PointElement,
  Filler,
  Tooltip,
  Legend
);

function getMonthlyData(txns) {
  const months = {};
  txns.forEach(t => {
    const month = (t.date || "").slice(0, 7);
    if (!month) return;
    if (!months[month]) months[month] = { credits: 0, debits: 0 };
    if (t.type === "credit") months[month].credits += Number(t.amount || 0);
    else months[month].debits += Number(t.amount || 0);
  });

  const sorted = Object.keys(months).sort().slice(-6);
  return {
    labels: sorted.map(m => new Date(`${m}-01`).toLocaleDateString("en-IN", { month: "short", year: "2-digit" })),
    credits: sorted.map(m => months[m].credits),
    debits: sorted.map(m => months[m].debits)
  };
}

export default function DashboardView({ databases, overview, theme }) {
  const monthlyRef = useRef(null);
  const modeRef = useRef(null);
  const balanceRef = useRef(null);
  const chartRefs = useRef({});

  const activeDbs = useMemo(() => databases.filter(db => !db.is_deleted), [databases]);
  const allTxns = useMemo(
    () =>
      activeDbs.flatMap(db =>
        (db.transactions || []).filter(t => !t.is_voided)
      ),
    [activeDbs]
  );

  useEffect(() => {
    Object.values(chartRefs.current).forEach(c => c?.destroy());
    chartRefs.current = {};

    const isDark = theme !== "light";
    const textColor = isDark ? "#e8e0ff" : "#0a1929";
    const gridColor = isDark ? "rgba(139, 122, 160, 0.2)" : "rgba(0, 0, 0, 0.1)";

    const monthly = getMonthlyData(allTxns);
    if (monthlyRef.current) {
      chartRefs.current.monthly = new Chart(monthlyRef.current, {
        type: "bar",
        data: {
          labels: monthly.labels,
          datasets: [
            { label: "Credits", data: monthly.credits, backgroundColor: "rgba(0,255,136,0.7)" },
            { label: "Debits", data: monthly.debits, backgroundColor: "rgba(255,64,129,0.7)" }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: textColor } } },
          scales: {
            x: { ticks: { color: textColor }, grid: { color: gridColor } },
            y: { ticks: { color: textColor }, grid: { color: gridColor } }
          }
        }
      });
    }

    if (modeRef.current) {
      const electronic = allTxns.filter(t => t.mode === "electronic").length;
      const cheque = allTxns.filter(t => t.mode === "cheque").length;
      const cash = allTxns.filter(t => t.mode === "cash").length;
      chartRefs.current.modes = new Chart(modeRef.current, {
        type: "pie",
        data: {
          labels: ["Electronic", "Cheque", "Cash"],
          datasets: [{ data: [electronic, cheque, cash], backgroundColor: ["#00e5ff99", "#bf00ff99", "#ffc10799"] }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: textColor } } } }
      });
    }

    if (balanceRef.current) {
      const sorted = [...allTxns]
        .filter(t => t.approved && !t.is_voided)
        .sort((a, b) => new Date(a.date) - new Date(b.date));
      let running = 0;
      const labels = [];
      const values = [];
      sorted.forEach(tx => {
        running += tx.type === "credit" ? Number(tx.amount || 0) : -Number(tx.amount || 0);
        labels.push(new Date(tx.date).toLocaleDateString("en-IN", { day: "numeric", month: "short" }));
        values.push(running);
      });
      chartRefs.current.balance = new Chart(balanceRef.current, {
        type: "line",
        data: {
          labels: labels.slice(-30),
          datasets: [
            {
              label: "Total Balance",
              data: values.slice(-30),
              borderColor: "rgba(191, 0, 255, 1)",
              backgroundColor: "rgba(191, 0, 255, 0.1)",
              fill: true,
              tension: 0.4
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: textColor } } },
          scales: {
            x: { ticks: { color: textColor }, grid: { color: gridColor } },
            y: { ticks: { color: textColor }, grid: { color: gridColor } }
          }
        }
      });
    }

    return () => Object.values(chartRefs.current).forEach(c => c?.destroy());
  }, [allTxns, theme]);

  return (
    <div className="view">
      <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: "2rem", color: "var(--primary)", marginBottom: 8 }}>
        📊 Analytics Dashboard
      </h2>
      <p style={{ color: "var(--muted)", marginBottom: 24 }}>Overview of all your fund databases</p>

      <div className="summary-row">
        <div className="chip">
          <div className="chip-label">Total Databases</div>
          <div className="chip-val neutral">{overview?.totalDatabases || 0}</div>
        </div>
        <div className="chip">
          <div className="chip-label">Total Balance</div>
          <div className="chip-val credit">{fmt(overview?.totalBalance || 0)}</div>
        </div>
        <div className="chip">
          <div className="chip-label">Total Credits</div>
          <div className="chip-val credit">{fmt(overview?.totalCredits || 0)}</div>
        </div>
        <div className="chip">
          <div className="chip-label">Total Debits</div>
          <div className="chip-val debit">{fmt(overview?.totalDebits || 0)}</div>
        </div>
      </div>

      <div className="dashboard-grid">
        <div className="chart-card">
          <div className="chart-title">📈 Monthly Credits vs Debits</div>
          <div className="chart-container">
            <canvas ref={monthlyRef} />
          </div>
        </div>
        <div className="chart-card">
          <div className="chart-title">🥧 Transaction Modes</div>
          <div className="chart-container">
            <canvas ref={modeRef} />
          </div>
        </div>
        <div className="chart-card">
          <div className="chart-title">📉 Balance Over Time</div>
          <div className="chart-container">
            <canvas ref={balanceRef} />
          </div>
        </div>
      </div>
    </div>
  );
}
