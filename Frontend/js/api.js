let token = localStorage.getItem("credit_token");

export function setToken(value) {
  token = value;
  value ? localStorage.setItem("credit_token", value) : localStorage.removeItem("credit_token");
}

export function hasToken() {
  return Boolean(token);
}

async function request(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    setToken(null);
    window.dispatchEvent(new CustomEvent("auth-expired"));
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string"
      ? body.detail
      : body.message || body.error || `Request failed with status ${response.status}.`;
    throw new Error(detail);
  }
  return body;
}

export const api = {
  login: (username, password) => request("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  }),
  register: (username, password) => request("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  }),
  defaultData: () => request("/api/default-data"),
  uploadCsv: (file, addToCurrent) => {
    const form = new FormData();
    form.append("file", file);
    form.append("add_to_current", addToCurrent ? "true" : "false");
    return request("/api/csv-analysis", { method: "POST", body: form });
  },
  predictApplicant: (datasetId, applicantId) =>
    request(`/api/${datasetId === "default-demo" ? "default-data/" : `datasets/${datasetId}/`}applicants/${applicantId}/predict`, { method: "POST" }),
  manualAssessment: payload => request("/api/manual-assessment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
};
