import { api, currentToken } from "./api.js";

// ============================================================
// Admin Portfolio Oversight module
// ============================================================

let _overviewLoaded = false;
let assignmentEpoch = 0;
let assignmentSelection = 0;
let usersEpoch = 0;
let userSelection = 0;
let userBusy = false;
let auditEpoch = 0;
let auditFilters = {};
let pendingUserConfirmation = null;

export function clearAdminState() {
  pendingUserConfirmation?.(false);
  usersEpoch += 1;
  userSelection += 1;
  auditEpoch += 1;
  userBusy = false;
  auditFilters = {};
  for (const id of ["adminUsersRows", "adminUsersPageInfo", "adminUserSelected", "adminUserConfirmationText", "adminAuditRows", "adminAuditPageInfo"]) {
    const element = document.getElementById(id);
    if (element) element.innerHTML = "";
  }
  document.getElementById("adminUserForm")?.reset();
  document.getElementById("adminAuditFilters")?.reset();
  document.getElementById("adminUserDetail")?.classList.add("hidden");
  for (const prefix of ["adminUsers", "adminAudit"]) {
    adminFeedback(prefix, "");
    document.getElementById(`${prefix}Rows`)?.setAttribute("aria-busy", "false");
    for (const action of ["Previous", "Next", "Refresh"]) {
      const button = document.getElementById(`${prefix}${action}`);
      if (button) { button.onclick = null; button.disabled = true; }
    }
  }
  assignmentEpoch += 1;
  assignmentSelection += 1;
  for (const id of ["adminAssignmentRows", "adminAssignmentWorkloads", "adminAssignmentHistory", "adminAssignmentSelected", "adminAssignmentPageInfo"]) {
    const element = document.getElementById(id);
    if (element) element.innerHTML = "";
  }
  document.getElementById("adminAssignmentForm")?.reset();
  document.getElementById("adminAssignmentDetail")?.classList.add("hidden");
  assignmentFeedback("");
  _overviewLoaded = false;
  for (const id of ["adminKpiGrid", "adminStatusChart", "adminReviewerWorkload", "adminRecentApplications"]) {
    const element = document.getElementById(id);
    if (element) element.innerHTML = "";
  }
  const refreshed = document.getElementById("adminLastRefreshed");
  if (refreshed) refreshed.textContent = "";
  setAdminError(null);
  setAdminLoading(true);
}

/**
 * Safe HTML escaping.
 * @param {*} v
 * @returns {string}
 */
function esc(v) {
  if (v == null) return "";
  return String(v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Status → CSS colour token */
function statusColor(status) {
  switch (status) {
    case "approved":        return "var(--good)";
    case "rejected":        return "var(--bad)";
    case "pending_review":  return "var(--warn)";
    case "needs_information": return "var(--warn)";
    case "submitted":       return "var(--muted)";
    default:                return "var(--muted)";
  }
}

/** Status → human-readable label */
function statusLabel(status) {
  const map = {
    approved:           "Approved",
    rejected:           "Rejected",
    pending_review:     "Pending Review",
    needs_information:  "Needs Information",
    submitted:          "Awaiting Assessment",
  };
  return map[status] || status;
}

/** Format ISO timestamp to short readable string */
function fmtDate(iso) {
  if (!iso) return "—";
  return iso.slice(0, 16).replace("T", " ");
}

// ============================================================
// KPI Cards
// ============================================================

function renderKpiCards(totals) {
  const grid = document.getElementById("adminKpiGrid");
  if (!grid) return;

  const cards = [
    { label: "Total Applications",   value: totals.total,               color: "var(--accent)" },
    { label: "Approved",             value: totals.approved,            color: "var(--good)" },
    { label: "Rejected",             value: totals.rejected,            color: "var(--bad)" },
    { label: "Pending Review",       value: totals.pending,             color: "var(--warn)" },
    { label: "Awaiting Assessment",  value: totals.awaiting_assessment, color: "var(--muted)" },
  ];

  grid.innerHTML = cards.map(c => `
    <div class="metric" role="group" aria-label="${esc(c.label)}">
      <small>${esc(c.label)}</small>
      <strong style="color:${c.color}">${c.value}</strong>
    </div>
  `).join("");
}

// ============================================================
// Status Distribution Bar Chart
// ============================================================

function renderStatusChart(totals) {
  const container = document.getElementById("adminStatusChart");
  if (!container) return;

  const total = totals.total;

  if (total === 0) {
    container.innerHTML = `<p class="muted" style="text-align:center;padding:20px 0;">No applications yet.</p>`;
    return;
  }

  const segments = [
    { key: "approved",          label: "Approved",          bg: "var(--good)" },
    { key: "rejected",          label: "Rejected",          bg: "var(--bad)" },
    { key: "pending_review",    label: "Pending Review",    bg: "#e0a11a" },
    { key: "needs_information", label: "Needs Information", bg: "#a16207" },
    { key: "submitted",         label: "Awaiting Asmt.",    bg: "var(--muted)" },
  ].filter(s => (totals[s.key] || 0) > 0);

  const barHtml = segments.map(s => {
    const count = totals[s.key] || 0;
    const pct = ((count / total) * 100).toFixed(1);
    return `<div style="flex:${pct};background:${s.bg};min-width:4px;border-radius:3px;" title="${s.label}: ${count} (${pct}%)" aria-label="${s.label}: ${count}"></div>`;
  }).join("");

  const legendHtml = segments.map(s => {
    const count = totals[s.key] || 0;
    const pct = ((count / total) * 100).toFixed(1);
    return `
      <div style="display:flex;align-items:center;gap:8px;font-size:13px;">
        <span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:${s.bg};flex-shrink:0;"></span>
        <span>${esc(s.label)}</span>
        <span style="margin-left:auto;font-weight:700;color:var(--ink);">${count}</span>
        <span style="color:var(--muted);font-size:11px;width:44px;text-align:right;">${pct}%</span>
      </div>`;
  }).join("");

  container.innerHTML = `
    <div style="display:flex;gap:4px;height:22px;border-radius:6px;overflow:hidden;margin-bottom:14px;" role="img" aria-label="Status distribution bar chart">
      ${barHtml}
    </div>
    <div style="display:grid;gap:8px;">
      ${legendHtml}
    </div>
  `;
}

// ============================================================
// Reviewer Workload Table
// ============================================================

function renderReviewerWorkload(reviewerWorkload, unassignedCount, targetId = "adminReviewerWorkload") {
  const container = document.getElementById(targetId);
  if (!container) return;

  const hasReviewers = reviewerWorkload.length > 0;
  const hasUnassigned = unassignedCount > 0;

  if (!hasReviewers && !hasUnassigned) {
    container.innerHTML = `<p class="muted">No reviewers or unassigned applications found.</p>`;
    return;
  }

  const rows = reviewerWorkload.map(r => `
    <tr>
      <td class="id">${esc(r.username)}</td>
      <td style="text-align:center;">${r.total_assigned}</td>
      <td style="text-align:center;color:var(--warn);font-weight:${r.pending > 0 ? "700" : "400"};">${r.pending}</td>
      <td style="text-align:center;color:var(--good);font-weight:${r.finalized > 0 ? "700" : "400"};">${r.finalized}</td>
    </tr>
  `).join("");

  const unassignedRow = hasUnassigned ? `
    <tr style="color:var(--muted);">
      <td><em>Unassigned</em></td>
      <td style="text-align:center;">${unassignedCount}</td>
      <td style="text-align:center;">—</td>
      <td style="text-align:center;">—</td>
    </tr>
  ` : "";

  container.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Reviewer</th>
            <th style="text-align:center;">Total Assigned</th>
            <th style="text-align:center;">Pending</th>
            <th style="text-align:center;">Finalized</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
          ${unassignedRow}
        </tbody>
      </table>
    </div>
  `;
}

// ============================================================
// Recent Applications Table
// ============================================================

function renderRecentApplications(recentApplications) {
  const container = document.getElementById("adminRecentApplications");
  if (!container) return;

  if (!recentApplications || recentApplications.length === 0) {
    container.innerHTML = `<p class="muted">No applications yet.</p>`;
    return;
  }

  const rows = recentApplications.map(app => `
    <tr>
      <td class="id" style="font-size:11px;word-break:break-all;">${esc(app.application_id)}</td>
      <td>${esc(app.owner || "—")}</td>
      <td>${esc(app.assigned_reviewer || "—")}</td>
      <td><span class="tag" style="color:${statusColor(app.status)};background:transparent;border:1px solid ${statusColor(app.status)};padding:3px 7px;">${esc(statusLabel(app.status))}</span></td>
      <td>${esc(fmtDate(app.created_at))}</td>
    </tr>
  `).join("");

  container.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Application ID</th>
            <th>Creator</th>
            <th>Assigned Reviewer</th>
            <th>Status</th>
            <th>Submitted</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

// ============================================================
// Loading / Error states
// ============================================================

function setAdminLoading(isLoading) {
  const spinner = document.getElementById("adminLoadingSpinner");
  const content = document.getElementById("adminOverviewContent");
  if (spinner) spinner.classList.toggle("hidden", !isLoading);
  if (content) content.classList.toggle("hidden", isLoading);
}

function setAdminError(message) {
  const errEl = document.getElementById("adminErrorBanner");
  if (!errEl) return;
  if (message) {
    errEl.textContent = message;
    errEl.classList.remove("hidden");
  } else {
    errEl.textContent = "";
    errEl.classList.add("hidden");
  }
}

function setLastRefreshed() {
  const el = document.getElementById("adminLastRefreshed");
  if (el) el.textContent = `Last refreshed: ${new Date().toLocaleTimeString()}`;
}

// ============================================================
// Main load function (exported)
// ============================================================

/**
 * Load and render the Admin Portfolio Overview.
 * Idempotent for subsequent calls (always refreshes).
 * @param {Object} [opts]
 * @param {Function} [opts.isStale] - Staleness guard
 */
export async function loadAdminOverview({ isStale } = {}) {
  setAdminLoading(true);
  setAdminError(null);

  try {
    const data = await api.getAdminOverview();
    if (isStale?.()) return;

    const { totals, reviewer_workload, unassigned_count, recent_applications } = data;

    renderKpiCards(totals);
    renderStatusChart(totals);
    renderReviewerWorkload(reviewer_workload, unassigned_count);
    renderRecentApplications(recent_applications);
    setLastRefreshed();

    _overviewLoaded = true;
  } catch (error) {
    if (!isStale?.()) {
      setAdminError(`Failed to load portfolio overview: ${error.message}`);
    }
  } finally {
    if (!isStale?.()) {
      setAdminLoading(false);
    }
  }
}

function assignmentFeedback(message, success = false) {
  const element = document.getElementById("adminAssignmentFeedback");
  if (!element) return;
  element.textContent = message;
  element.classList.toggle("hidden", !message);
  element.classList.toggle("assignment-success", success);
}

/** Bounded portfolio page; each request and selection belongs to the current session. */
export async function loadAdminAssignments({ isStale, offset = 0, message = "" } = {}) {
  const epoch = ++assignmentEpoch;
  const token = currentToken();
  const stale = () => epoch !== assignmentEpoch || token !== currentToken() || Boolean(isStale?.());
  const rows = document.getElementById("adminAssignmentRows");
  if (!rows || stale()) return;
  assignmentSelection += 1;
  document.getElementById("adminAssignmentDetail")?.classList.add("hidden");
  assignmentFeedback(message, Boolean(message));
  rows.innerHTML = '<tr><td colspan="6">Loading assignments…</td></tr>';
  const previous = document.getElementById("adminAssignmentPrevious");
  const next = document.getElementById("adminAssignmentNext");
  previous.disabled = next.disabled = true;
  document.getElementById("adminAssignmentRefresh").onclick = () => {
    if (!stale()) loadAdminAssignments({ isStale, offset });
  };
  try {
    const page = await api.getAdminAssignments(25, offset);
    if (stale()) return;
    rows.innerHTML = page.applications.length ? page.applications.map(app => `
      <tr><td class="id">${esc(app.application_id)}</td><td>${esc(app.owner)}</td>
        <td>${esc(app.assigned_reviewer || "Unassigned")}</td>
        <td><span class="tag" style="color:${statusColor(app.status)}">${esc(statusLabel(app.status))}</span></td>
        <td>${esc(fmtDate(app.created_at))}</td>
        <td><button type="button" class="secondary" data-manage-assignment="${esc(app.application_id)}">${app.can_reassign ? "Manage" : "View history"}</button></td></tr>
    `).join("") : '<tr><td colspan="6">No applications on this page.</td></tr>';
    renderReviewerWorkload(page.reviewer_workload, 0, "adminAssignmentWorkloads");
    document.getElementById("adminAssignmentPageInfo").textContent = page.total
      ? `${offset + (page.applications.length ? 1 : 0)}–${offset + page.applications.length} of ${page.total}` : "0 applications";
    previous.disabled = offset === 0;
    next.disabled = offset + page.limit >= page.total;
    previous.onclick = () => { if (!stale()) loadAdminAssignments({ isStale, offset: Math.max(0, offset - page.limit) }); };
    next.onclick = () => { if (!stale()) loadAdminAssignments({ isStale, offset: offset + page.limit }); };
    rows.querySelectorAll("[data-manage-assignment]").forEach(button => {
      button.onclick = () => {
        if (stale()) return;
        const app = page.applications.find(item => item.application_id === button.dataset.manageAssignment);
        return selectAssignment(app, page.reviewer_workload, { stale, isStale, offset });
      };
    });
  } catch (error) {
    if (stale()) return;
    rows.innerHTML = '<tr><td colspan="6">Assignments unavailable. Use Refresh to try again.</td></tr>';
    assignmentFeedback(error.message);
  }
}

async function selectAssignment(app, workload, { stale, isStale, offset }) {
  const selection = ++assignmentSelection;
  const obsolete = () => stale() || selection !== assignmentSelection;
  const form = document.getElementById("adminAssignmentForm");
  const select = document.getElementById("adminAssignmentReviewer");
  const reason = document.getElementById("adminAssignmentReason");
  const submit = document.getElementById("adminAssignmentSubmit");
  document.getElementById("adminAssignmentDetail").classList.remove("hidden");
  document.getElementById("adminAssignmentSelected").textContent = `${app.application_id} · ${app.owner} · ${statusLabel(app.status)} · Current reviewer: ${app.assigned_reviewer || "Unassigned"}`;
  form.reset();
  form.classList.toggle("hidden", !app.can_reassign);
  document.getElementById("adminAssignmentUnavailable").classList.toggle("hidden", app.can_reassign);
  select.innerHTML = '<option value="">Select a new reviewer…</option>' + workload.map(reviewer => `
    <option value="${esc(reviewer.username)}" ${reviewer.username === app.assigned_reviewer ? "disabled" : ""}>${esc(reviewer.username)} — ${reviewer.pending} pending / ${reviewer.total_assigned} assigned${reviewer.username === app.assigned_reviewer ? " (current)" : ""}</option>
  `).join("");
  submit.disabled = !workload.some(reviewer => reviewer.username !== app.assigned_reviewer);
  if (app.can_reassign && submit.disabled) assignmentFeedback("No other eligible Reviewer accounts are available.");
  form.onsubmit = async event => {
    event.preventDefault();
    if (obsolete() || !app.can_reassign || submit.disabled) return;
    const target = select.value;
    const explanation = reason.value.trim();
    if (!target || target === app.assigned_reviewer || !workload.some(item => item.username === target) || !explanation) {
      assignmentFeedback("Choose a different reviewer and enter a reassignment reason.");
      return;
    }
    if (!window.confirm(`Reassign application ${app.application_id} from ${app.assigned_reviewer || "Unassigned"} to ${target}?\n\nReason: ${explanation}`)) return;
    submit.disabled = true;
    try {
      await api.reassignApplication(app.application_id, {
        reviewer_username: target, reason: explanation, expected_updated_at: app.updated_at,
      });
      if (obsolete()) return;
      await loadAdminAssignments({ isStale, offset, message: `Assignment updated to ${target}. Select the application to view its recorded history.` });
      if (!isStale?.()) await loadAdminOverview({ isStale });
    } catch (error) {
      if (obsolete()) return;
      assignmentFeedback(`${error.message} Refresh the list before trying again.`);
      submit.disabled = false;
    }
  };
  const history = document.getElementById("adminAssignmentHistory");
  history.innerHTML = '<p class="muted">Loading assignment history…</p>';
  try {
    const result = await api.listAudit(app.application_id);
    if (obsolete()) return;
    const events = result.audit.filter(event => event.event_type === "assignment_changed" || event.event_type === "application_created");
    history.innerHTML = events.length ? '<ul class="assignment-history">' + events.map(event => {
      const created = event.event_type === "application_created";
      const old = event.old_value?.assigned_reviewer || "Unassigned";
      const target = event.new_value?.assigned_reviewer || event.new_value?.reviewer_username || "Unassigned";
      return `<li><strong>${created ? "Initial assignment" : `${esc(old)} → ${esc(target)}`}</strong>${created ? `: ${esc(target)}` : ""}<div class="muted">${esc(event.actor)} · ${esc(fmtDate(event.created_at))}</div>${created ? "" : `<p>${esc(event.new_value?.reason)}</p>`}</li>`;
    }).join("") + '</ul>' : '<p class="muted">No recorded assignment changes.</p>';
  } catch (error) {
    if (!obsolete()) history.innerHTML = `<p class="bad">Unable to load assignment history: ${esc(error.message)}</p>`;
  }
}

// Staff and audit pages load bounded pages on navigation or explicit user action.
function adminFeedback(prefix, message, success = false) {
  const element = document.getElementById(`${prefix}Feedback`);
  if (!element) return;
  element.textContent = message;
  element.classList.toggle("hidden", !message);
  element.classList.toggle("assignment-success", success);
  element.setAttribute("role", message && !success ? "alert" : "status");
}

function pager(prefix, page, offset, load) {
  document.getElementById(`${prefix}PageInfo`).textContent = page.total
    ? `${offset + (page.items.length ? 1 : 0)}–${offset + page.items.length} of ${page.total}` : "0 results";
  const previous = document.getElementById(`${prefix}Previous`);
  const next = document.getElementById(`${prefix}Next`);
  previous.disabled = offset === 0;
  next.disabled = offset + page.limit >= page.total;
  previous.onclick = () => load(Math.max(0, offset - page.limit));
  next.onclick = () => load(offset + page.limit);
}

function startPage(prefix, columns, text) {
  const rows = document.getElementById(`${prefix}Rows`);
  rows.setAttribute("aria-busy", "true");
  rows.innerHTML = `<tr><td colspan="${columns}">${text}</td></tr>`;
  document.getElementById(`${prefix}PageInfo`).textContent = "Loading…";
  for (const action of ["Previous", "Next"]) document.getElementById(`${prefix}${action}`).disabled = true;
  document.getElementById(`${prefix}Refresh`).disabled = false;
  return rows;
}

export async function loadAdminUsers({ username = "", isStale, offset = 0, message = "" } = {}) {
  const epoch = ++usersEpoch;
  const token = currentToken();
  const stale = () => epoch !== usersEpoch || token !== currentToken() || Boolean(isStale?.());
  if (!document.getElementById("adminUsersRows") || stale()) return;
  userSelection += 1;
  userBusy = false;
  document.getElementById("adminUserDetail").classList.add("hidden");
  document.getElementById("adminUserForm").reset();
  adminFeedback("adminUsers", message, Boolean(message));
  const rows = startPage("adminUsers", 5, "Loading staff…");
  const reload = (nextOffset = offset, nextMessage = "") => {
    if (!stale() && !userBusy) return loadAdminUsers({ username, isStale, offset: nextOffset, message: nextMessage });
  };
  document.getElementById("adminUsersRefresh").onclick = () => reload();
  try {
    const page = await api.getAdminUsers(25, offset);
    if (stale()) return;
    if (offset > 0 && offset >= page.total) return reload(Math.max(0, Math.ceil(page.total / 25) * 25 - 25), message);
    rows.innerHTML = page.users.length ? page.users.map(account => {
      const self = !username || account.username === username;
      return `<tr><td class="id">${esc(account.username)}${self ? ' <span class="tag">You</span>' : ""}</td>
        <td>${esc(account.role)}</td><td><span class="tag ${account.is_active ? "good" : "bad"}">${account.is_active ? "Active" : "Inactive"}</span></td>
        <td>${esc(fmtDate(account.created_at))}</td><td><div class="admin-actions">
          <button class="secondary" type="button" data-user-change="role" data-username="${esc(account.username)}" ${self || account.role === "applicant" ? 'disabled title="Self roles and Applicant accounts cannot be converted"' : ""}>Change role</button>
          <button class="secondary" type="button" data-user-change="status" data-username="${esc(account.username)}" ${self ? 'disabled title="Your own active status is protected"' : ""}>${account.is_active ? "Deactivate" : "Activate"}</button>
        </div></td></tr>`;
    }).join("") : '<tr><td colspan="5">No staff accounts found.</td></tr>';
    pager("adminUsers", { ...page, items: page.users }, offset, reload);
    rows.querySelectorAll("[data-user-change]").forEach(button => {
      button.onclick = () => {
        if (stale() || userBusy) return;
        const account = page.users.find(item => item.username === button.dataset.username);
        if (!account || account.username === username || !username) return;
        selectUser(account, button.dataset.userChange, { stale, reload, username, button });
      };
    });
  } catch (error) {
    if (stale()) return;
    rows.innerHTML = '<tr><td colspan="5">Staff directory unavailable. Use Refresh staff to try again.</td></tr>';
    adminFeedback("adminUsers", error.message);
    document.getElementById("adminUsersPageInfo").textContent = "Unavailable";
  } finally {
    if (!stale()) rows.setAttribute("aria-busy", "false");
  }
}

function selectUser(account, action, { stale, reload, username, button }) {
  const selection = ++userSelection;
  const obsolete = () => stale() || selection !== userSelection;
  const form = document.getElementById("adminUserForm");
  const role = document.getElementById("adminUserRole");
  const reason = document.getElementById("adminUserReason");
  const submit = document.getElementById("adminUserSubmit");
  const cancel = document.getElementById("adminUserCancel");
  form.reset();
  role.value = "";
  reason.value = "";
  submit.disabled = cancel.disabled = false;
  role.disabled = reason.disabled = false;
  role.required = action === "role";
  Array.from(role.options).forEach(option => { option.disabled = option.value === account.role; });
  document.getElementById("adminUserRoleLabel").classList.toggle("hidden", action !== "role");
  const description = document.getElementById("adminUserStatusDescription");
  description.classList.toggle("hidden", action !== "status");
  description.textContent = `${account.is_active ? "Deactivate" : "Activate"} ${account.username}?`;
  document.getElementById("adminUserSelected").textContent = `${account.username} · ${account.role} · ${account.is_active ? "Active" : "Inactive"}`;
  document.getElementById("adminUserDetail").classList.remove("hidden");
  adminFeedback("adminUsers", "");
  (action === "role" ? role : reason).focus();
  cancel.onclick = () => {
    if (obsolete() || userBusy) return;
    userSelection += 1;
    form.reset();
    document.getElementById("adminUserDetail").classList.add("hidden");
    button.focus();
  };
  form.onsubmit = async event => {
    event.preventDefault();
    if (obsolete() || userBusy || account.username === username) return;
    const explanation = reason.value.trim();
    const newRole = role.value;
    if (!explanation || (action === "role" && (!['analyst', 'reviewer', 'admin'].includes(newRole) || newRole === account.role))) {
      adminFeedback("adminUsers", "Choose a different role when changing roles and enter a reason.");
      return;
    }
    const label = action === "role" ? `Change ${account.username}'s role from ${account.role} to ${newRole}`
      : `${account.is_active ? "Deactivate" : "Activate"} ${account.username}`;
    if (!await confirmUserChange(label, explanation) || obsolete()) return;
    userBusy = true;
    submit.disabled = cancel.disabled = role.disabled = reason.disabled = true;
    document.getElementById("adminUsersRefresh").disabled = true;
    adminFeedback("adminUsers", "Saving account change…", true);
    try {
      const payload = { reason: explanation, expected_version: account.version };
      if (action === "role") await api.updateUserRole(account.username, { ...payload, role: newRole });
      else await api.updateUserStatus(account.username, { ...payload, is_active: !account.is_active });
      if (obsolete()) return;
      userBusy = false;
      await reload(undefined, `${label}. Account updated and audit event recorded.`);
    } catch (error) {
      if (obsolete()) return;
      userBusy = false;
      document.getElementById("adminUsersRefresh").disabled = false;
      const conflict = error.status === 409;
      adminFeedback("adminUsers", `${conflict ? "Change blocked or account changed. " : ""}${error.message}${conflict ? " Refresh staff and select the account again before retrying." : ""}`);
      // A conflict must use a fresh directory version, never silently retry the mutation.
      submit.disabled = conflict;
      cancel.disabled = role.disabled = reason.disabled = false;
      document.getElementById("adminUsersFeedback").focus();
    }
  };
}

function confirmUserChange(label, reason) {
  const dialog = document.getElementById("adminUserConfirmation");
  document.getElementById("adminUserConfirmationText").textContent = `${label}?\n\nReason: ${reason}`;
  return new Promise(resolve => {
    const finish = accepted => {
      if (pendingUserConfirmation !== finish) return;
      pendingUserConfirmation = null;
      dialog.close();
      resolve(accepted);
    };
    pendingUserConfirmation?.(false);
    pendingUserConfirmation = finish;
    document.getElementById("adminUserConfirmationCancel").onclick = () => finish(false);
    document.getElementById("adminUserConfirmationAccept").onclick = () => finish(true);
    dialog.oncancel = event => { event.preventDefault(); finish(false); };
    dialog.showModal();
  });
}

const auditLabels = {
  application_created: "Application created", prediction_generated: "Prediction generated",
  analyst_claimed: "Analyst claimed",
  assignment_changed: "Assignment changed", manual_review: "Manual review",
  user_role_changed: "User role changed", user_activated: "User activated", user_deactivated: "User deactivated",
};

function auditDetails(event) {
  const fields = {
    application_created: ["source_type", "dataset_id", "source_applicant_id", "reviewer_username"],
    prediction_generated: ["prediction_id"], assignment_changed: ["assigned_reviewer", "reason"],
    analyst_claimed: ["assigned_analyst", "assigned_reviewer"],
    user_role_changed: ["role", "is_active", "version"], user_activated: ["role", "is_active", "version"],
    user_deactivated: ["role", "is_active", "version"],
  };
  const describe = value => {
    if (event.event_type === "manual_review") return ["submitted", "pending_review", "needs_information", "approved", "rejected"].includes(value) ? esc(statusLabel(value)) : "—";
    if (!value || typeof value !== "object" || Array.isArray(value)) return "—";
    return (fields[event.event_type] || []).filter(key => Object.hasOwn(value, key)
      && (value[key] === null || ["string", "number", "boolean"].includes(typeof value[key])))
      .map(key => `${esc(key.replaceAll("_", " "))}: ${esc(value[key] ?? "—")}`).join("<br>") || "—";
  };
  const reason = event.scope === "user" && typeof event.reason === "string"
    ? `<dt>Reason</dt><dd>${esc(event.reason)}</dd>` : "";
  return `<details class="audit-details"><summary>View details</summary><dl><dt>Before</dt><dd>${describe(event.old_value)}</dd><dt>After</dt><dd>${describe(event.new_value)}</dd>${reason}<dt>Event ID</dt><dd>${esc(event.audit_id)}</dd></dl></details>`;
}

function readAuditFilters() {
  const filters = {};
  for (const [key, id] of [["event_type", "adminAuditEvent"], ["actor", "adminAuditActor"], ["application_id", "adminAuditApplication"]]) {
    const value = document.getElementById(id).value.trim();
    if (value) filters[key] = value;
  }
  for (const [key, id] of [["date_from", "adminAuditFrom"], ["date_to", "adminAuditTo"]]) {
    const value = document.getElementById(id).value;
    if (value) {
      const date = new Date(`${value}Z`); // datetime-local fields are explicitly labelled UTC.
      if (!Number.isFinite(date.getTime())) throw Error("Enter valid UTC dates.");
      filters[key] = date.toISOString();
    }
  }
  if (filters.date_from && filters.date_to && filters.date_from > filters.date_to) throw Error("From (UTC) must precede Through (UTC).");
  return filters;
}

export async function loadAdminAudit({ isStale, offset = 0, filters = auditFilters } = {}) {
  const epoch = ++auditEpoch;
  const token = currentToken();
  const stale = () => epoch !== auditEpoch || token !== currentToken() || Boolean(isStale?.());
  if (!document.getElementById("adminAuditRows") || stale()) return;
  auditFilters = { ...filters };
  const applied = { ...filters };
  adminFeedback("adminAudit", "");
  const rows = startPage("adminAudit", 5, "Loading audit events…");
  const reload = (nextOffset = offset, nextFilters = applied) => {
    if (!stale()) return loadAdminAudit({ isStale, offset: nextOffset, filters: nextFilters });
  };
  document.getElementById("adminAuditRefresh").onclick = () => reload();
  const form = document.getElementById("adminAuditFilters");
  form.onsubmit = event => {
    event.preventDefault();
    if (stale()) return;
    try { return reload(0, readAuditFilters()); }
    catch (error) { adminFeedback("adminAudit", error.message); document.getElementById("adminAuditFeedback").focus(); }
  };
  document.getElementById("adminAuditReset").onclick = () => {
    if (stale()) return;
    form.reset();
    return reload(0, {});
  };
  try {
    const page = await api.getAdminAudit(25, offset, applied);
    if (stale()) return;
    if (offset > 0 && offset >= page.total) return reload(Math.max(0, Math.ceil(page.total / 25) * 25 - 25));
    rows.innerHTML = page.events.length ? page.events.map(event => `<tr>
      <td>${esc(fmtDate(event.created_at))}</td><td>${esc(event.actor)}</td>
      <td>${esc(auditLabels[event.event_type] || event.event_type)}</td>
      <td class="id">${event.scope === "user" ? "User" : "Application"}: ${esc(event.target_username || event.application_id || "—")}</td>
      <td>${auditDetails(event)}</td></tr>`).join("") : '<tr><td colspan="5">No audit events match these filters.</td></tr>';
    pager("adminAudit", { ...page, items: page.events }, offset, nextOffset => reload(nextOffset));
  } catch (error) {
    if (stale()) return;
    rows.innerHTML = '<tr><td colspan="5">Audit history unavailable. Use Refresh events to try again.</td></tr>';
    document.getElementById("adminAuditPageInfo").textContent = "Unavailable";
    adminFeedback("adminAudit", error.message);
  } finally {
    if (!stale()) rows.setAttribute("aria-busy", "false");
  }
}
