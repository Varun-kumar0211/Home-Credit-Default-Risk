let token = localStorage.getItem("credit_token");
let readOnlyDemo = false;
export function setReadOnlyDemo(value) { readOnlyDemo = Boolean(value); }

export function setToken(value) {
  token = value;
  value ? localStorage.setItem("credit_token", value) : localStorage.removeItem("credit_token");
}

export function hasToken() {
  return Boolean(token);
}

export function currentToken() {
  return token;
}

async function request(url, options = {}) {
  if (readOnlyDemo && (options.method || "GET") !== "GET" && !url.startsWith("/auth/")) {
    throw new Error("Recruiter demo is read-only. Create your own Applicant account to submit an application.");
  }
  const requestToken = token;
  const headers = { ...(options.headers || {}) };
  if (requestToken) headers.Authorization = `Bearer ${requestToken}`;
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401 && token === requestToken) {
    setToken(null);
    window.dispatchEvent(new CustomEvent("auth-expired"));
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string"
      ? body.detail
      : body.message || body.error || `Request failed with status ${response.status}.`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return body;
}

export const api = {
  demoAvailability: () => request("/auth/demo"),
  demoLogin: role => request(`/auth/demo/${encodeURIComponent(role)}`, { method: "POST" }),
  login: (username, password) => request("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  }),
  me: () => request("/auth/me"),
  reviewers: () => request("/api/reviewers"),
  createApplication: payload => request("/api/applications", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
  listApplications: (limit = 25, offset = 0) => request(`/api/applications?${new URLSearchParams({ limit, offset })}`),
  submitApplicant: payload => request("/api/applicant/applications", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }),
  applicantApplications: (limit = 25, offset = 0) => request(`/api/applicant/applications?${new URLSearchParams({ limit, offset })}`),
  applicantApplication: id => request(`/api/applicant/applications/${encodeURIComponent(id)}`),
  intake: (limit = 25, offset = 0) => request(`/api/analyst/intake?${new URLSearchParams({ limit, offset })}`),
  claimIntake: (id, payload) => request(`/api/analyst/intake/${encodeURIComponent(id)}/claim`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }),
  getApplication: applicationId => request(`/api/applications/${applicationId}`),
  assessApplication: applicationId => request(`/api/applications/${applicationId}/assess`, { method: "POST" }),
  listPredictions: applicationId => request(`/api/applications/${applicationId}/predictions`),
  getPredictionExplanation: (applicationId, predictionId) => request(`/api/applications/${applicationId}/predictions/${predictionId}/explanation`),
  listReviews: applicationId => request(`/api/applications/${applicationId}/reviews`),
  listAudit: applicationId => request(`/api/applications/${applicationId}/audit`),
  submitReview: (applicationId, payload) => request(`/api/applications/${applicationId}/reviews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
  getAssignedApplications: (limit = 25, offset = 0) => request(`/api/reviewers/assigned?${new URLSearchParams({ limit, offset })}`),
  getPendingReviews: (limit = 25, offset = 0) => request(`/api/reviewers/pending?${new URLSearchParams({ limit, offset })}`),
  getReviewHistory: (limit = 25, offset = 0) => request(`/api/reviewers/history?${new URLSearchParams({ limit, offset })}`),
  getAdminOverview: () => request("/api/admin/overview"),
  getAdminUsers: (limit = 25, offset = 0) => request(`/api/admin/users?${new URLSearchParams({ limit, offset })}`),
  updateUserRole: (username, payload) => request(`/api/admin/users/${encodeURIComponent(username)}/role`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }),
  updateUserStatus: (username, payload) => request(`/api/admin/users/${encodeURIComponent(username)}/status`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }),
  getAdminAudit: (limit = 25, offset = 0, filters = {}) => {
    const params = new URLSearchParams({ limit, offset });
    for (const key of ["event_type", "actor", "application_id", "date_from", "date_to"]) {
      if (filters[key]) params.set(key, filters[key]);
    }
    return request(`/api/admin/audit?${params}`);
  },
  getAdminAssignments: (limit = 25, offset = 0) => request(`/api/admin/assignments?limit=${limit}&offset=${offset}`),
  reassignApplication: (applicationId, payload) => request(`/api/admin/applications/${applicationId}/assignment`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
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
