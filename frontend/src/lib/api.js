const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

// Callers need the status (a 401 means the session is gone), not just the text.
function httpError(payload, status, fallback) {
  const err = new Error(payload?.error || `${fallback} (${status})`);
  err.status = status;
  return err;
}

export async function apiRequest(endpoint, options = {}, token = null) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {})
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (_err) {
    payload = null;
  }

  if (!response.ok) {
    throw httpError(payload, response.status, "Request failed");
  }
  return payload;
}

// Sends the image as multipart/form-data — NOT JSON
// DO NOT set Content-Type header (browser sets it with boundary automatically)
async function postImage(path, file, token, fallbackMessage) {
  const form = new FormData();
  form.append("image", file);
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = null; }
  if (!response.ok) throw httpError(payload, response.status, fallbackMessage);
  return payload;
}

export const extractReceipt = (file, token) => postImage("/api/extract-receipt", file, token, "Extraction failed");

export const uploadReceipt = (transactionId, file, token) =>
  postImage(`/api/transactions/${transactionId}/receipt`, file, token, "Upload failed");

export const lookupOrgs = email =>
  apiRequest("/api/auth/orgs", { method: "POST", body: JSON.stringify({ email }) });

export const previewJoinCode = code =>
  apiRequest("/api/orgs/join/preview", { method: "POST", body: JSON.stringify({ code }) });

export const validateConnection = databaseUrl =>
  apiRequest("/api/orgs/validate-connection", {
    method: "POST",
    body: JSON.stringify({ databaseUrl })
  });

export const createOrg = payload =>
  apiRequest("/api/orgs/create", { method: "POST", body: JSON.stringify(payload) });

export const joinOrg = payload =>
  apiRequest("/api/orgs/join", { method: "POST", body: JSON.stringify(payload) });
