export const fmt = n =>
  "₹ " +
  Number(n || 0).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });

export const formatDate = value =>
  value
    ? new Date(value).toLocaleString("en-IN", {
        dateStyle: "medium",
        timeStyle: "short"
      })
    : "—";

export const formatDateShort = value =>
  value ? new Date(value).toLocaleDateString("en-IN") : "—";

export const relativeTime = value => {
  if (!value) return "—";
  const diff = Date.now() - new Date(value).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
};

export const nowInput = () => new Date().toISOString().slice(0, 16);
