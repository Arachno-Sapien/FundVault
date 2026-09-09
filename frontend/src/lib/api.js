const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

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
    throw new Error(payload?.error || `Request failed (${response.status})`);
  }
  return payload;
}

export async function extractReceipt(file, token) {
  // Sends the image as multipart/form-data — NOT JSON
  // DO NOT set Content-Type header (browser sets it with boundary automatically)
  const form = new FormData();
  form.append("image", file);
  const response = await fetch(`${API_BASE}/api/extract-receipt`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = null; }
  if (!response.ok) throw new Error(payload?.error || `Extraction failed (${response.status})`);
  return payload;
}

export { API_BASE };
