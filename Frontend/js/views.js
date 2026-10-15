import { api, currentToken } from "./api.js";
import { renderAnalysis, renderApplicants, renderCorrelation, renderGlobalShap, renderResult, renderTarget, renderMetrics, renderRiskOverview, renderHealth, renderRiskFeatures, renderSegments } from "./components.js";

let current = { dataset: null, applicants: [] };
let datasetLoaded = false;
let loanApplications = [];
let selectedApplicationId = null;
let applicationsLoaded = false;
let viewSession = 0, loanEpoch = 0, loanOffset = 0;
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function viewGuard(external) {
  const token = currentToken(), sequence = viewSession;
  return () => token !== currentToken() || sequence !== viewSession || Boolean(external?.());
}

export function resetDatasetCache() {
  viewSession++; loanEpoch++; loanOffset = 0;
  ["loanDetail", "loanApplicationRows", "loanCreateMessage", "loanAssessMessage", "loanPageInfo", "metrics", "applicationPortfolio", "riskFeatures", "numericProfile", "applicantRows", "qualityProfile", "categoryProfile", "targetProfile", "segmentProfile", "correlationProfile", "shapProfile", "riskOverview", "resultContent"].forEach(id => {
    const target = document.getElementById(id); if (target) target.textContent = "";
  });
  ["manualForm", "loanApplicationForm"].forEach(id => document.getElementById(id)?.reset());
  const dialog = document.getElementById("resultDialog"); if (dialog?.open) dialog.close();
  const assessButton = document.getElementById("assessApplicationButton"); if (assessButton) assessButton.disabled = true;
  const createButton = document.getElementById("createApplicationButton"); if (createButton) createButton.disabled = false;
  current = { dataset: null, applicants: [] };
  datasetLoaded = false;
  loanApplications = [];
  selectedApplicationId = null;
  applicationsLoaded = false;
}

function filteredApplicants() {
  const search = document.querySelector("#applicantSearch")?.value.toLowerCase() || "";
  const risk = document.querySelector("#riskFilter")?.value || "";
  const occupation = document.querySelector("#occupationFilter")?.value || "";
  const contract = document.querySelector("#contractFilter")?.value || "";
  const sort = document.querySelector("#sortApplicants")?.value || "risk-desc";
  const rows = current.applicants.filter(item => {
    const values = item.data || {};
    const probability = item.assessment?.default_probability;
    const approve = parseFloat(localStorage.getItem("approveThreshold") || 8.0) / 100;
    const decline = parseFloat(localStorage.getItem("declineThreshold") || 20.0) / 100;
    const tier = probability === undefined ? "" : probability <= approve ? "Low Risk" : probability < decline ? "Manual Review" : "High Risk";
    return (!search || item.id.toLowerCase().includes(search))
      && (!risk || tier === risk)
      && (!occupation || values.OCCUPATION === occupation)
      && (!contract || values.CONTRACT_TYPE === contract);
  });
  rows.sort((left, right) => {
    const a = left.assessment?.default_probability ?? -1;
    const b = right.assessment?.default_probability ?? -1;
    if (sort === "risk-asc") return a - b;
    if (sort === "score-desc") return Number(right.data.CREDIT_SCORE || 0) - Number(left.data.CREDIT_SCORE || 0);
    if (sort === "amount-desc") return Number(right.data.CREDIT_AMOUNT || 0) - Number(left.data.CREDIT_AMOUNT || 0);
    return b - a;
  });
  renderApplicants(rows, assessApplicant);
}

function showError(error) {
  const target = document.querySelector("#message");
  if (!target) {
    console.error(error);
    return;
  }
  target.textContent = error.message || String(error);
  target.classList.remove("hidden");
}

async function loadAnalystPortfolio() {
  const stale = viewGuard();
  const target = document.querySelector("#applicationPortfolio");
  if (!target) return;
  target.textContent = "Loading application totals…";
  try {
    const data = await api.analystPortfolio();
    if (stale()) return;
    target.textContent = `Applications: ${data.total} · Approved: ${data.approved} (${data.approval_rate}%) · Rejected: ${data.rejected} (${data.rejection_rate}%) · Pending: ${data.pending}`;
  } catch (error) { if (!stale()) target.textContent = `Application totals unavailable: ${error.message}`; }
}

export function showDataset(payload, showPage) {
  current = { dataset: payload.dataset_id, applicants: payload.applicants };
  if (showPage) showPage("dashboardPage");
  else document.querySelectorAll(".page").forEach(page => page.classList.toggle("hidden", page.id !== "dashboardPage"));
  const analysis = renderAnalysis(payload.analysis);
  document.querySelector("#datasetTitle").textContent = `${payload.filename} · ${payload.analysis.rows} applicants`;
  document.querySelector("#metrics").innerHTML = renderMetrics(payload.analysis);
  document.querySelector("#riskOverview").innerHTML = renderRiskOverview(payload.analysis);
  document.querySelector("#healthSummary").textContent = payload.analysis.data_health?.validation_status === "good" ? "✓ Good" : "⚠ Review";
  document.querySelector("#qualityProfile").innerHTML = renderHealth(payload.analysis) + analysis.quality;
  document.querySelector("#categoryProfile").innerHTML = analysis.categories || "<p class='muted'>No categories detected.</p>";
  document.querySelector("#targetProfile").innerHTML = renderTarget(payload.analysis);
  document.querySelector("#segmentProfile").innerHTML = renderSegments(payload.analysis);
  document.querySelector("#riskFeatures").innerHTML = renderRiskFeatures(payload.analysis);
  document.querySelector("#numericProfile").innerHTML = analysis.numeric;
  loadAnalystPortfolio();
  document.querySelector("#refreshAnalystPortfolio").onclick = loadAnalystPortfolio;
  document.querySelector("#correlationProfile").innerHTML = renderCorrelation(payload.analysis);
  document.querySelector("#shapProfile").innerHTML = renderGlobalShap(payload.analysis);
  ["occupationFilter", "contractFilter"].forEach((id) => {
    const select = document.querySelector(`#${id}`);
    const field = id === "occupationFilter" ? "OCCUPATION" : "CONTRACT_TYPE";
    const values = [...new Set(current.applicants.map(item => item.data?.[field]).filter(Boolean))].sort();
    select.innerHTML = `<option value="">All ${id === "occupationFilter" ? "occupations" : "contract types"}</option>${values.map(value => `<option value="${value}">${value}</option>`).join("")}`;
  });
  ["applicantSearch", "riskFilter", "occupationFilter", "contractFilter", "sortApplicants"].forEach(id => document.querySelector(`#${id}`)?.addEventListener(id === "applicantSearch" ? "input" : "change", filteredApplicants));
  filteredApplicants();
  document.querySelector("#dashboardPage").classList.remove("hidden");
}

async function assessApplicant(button) {
  const stale = viewGuard();
  button.disabled = true;
  button.textContent = "Scoring…";
  try {
    const response = await api.predictApplicant(current.dataset, button.dataset.id);
    if (stale()) return;
    renderResult(document.querySelector("#resultContent"), response);
    document.querySelector("#resultDialog").showModal();
  } catch (error) {
    if (!stale()) showError(error);
  } finally {
    if (stale()) return;
    button.disabled = false;
    button.textContent = "Run assessment →";
  }
}

export async function assessManual(payload) {
  const stale = viewGuard();
  try {
    const result = await api.manualAssessment(payload);
    if (stale()) return;
    renderResult(document.querySelector("#resultContent"), result);
    document.querySelector("#resultDialog").showModal();
  } catch (error) { if (!stale()) showError(error); }
}

export function refreshRiskLabels() {
  if (current.dataset) filteredApplicants();
}

export async function loadDefault({ force = false, showPage, isStale } = {}) {
  const stale = viewGuard(isStale);
  if (datasetLoaded && !force) {
    if (showPage) showPage("dashboardPage");
    else document.querySelectorAll(".page").forEach(page => page.classList.toggle("hidden", page.id !== "dashboardPage"));
    return;
  }
  try { const data = await api.defaultData(); if (stale()) return; showDataset(data, showPage); datasetLoaded = true; } catch (error) { if (!stale()) { datasetLoaded = false; showError(error); } }
}

export async function upload(file, addToCurrent, showPage, { isStale } = {}) {
  const stale = viewGuard(isStale);
  try { const data = await api.uploadCsv(file, addToCurrent); if (stale()) return; showDataset(data, showPage); datasetLoaded = true; } catch (error) { if (!stale()) showError(error); }
}

export async function submitLoanApplication(form) {
  const stale = viewGuard();
  const message = document.querySelector("#loanCreateMessage");
  const button = document.querySelector("#createApplicationButton");
  const payload = Object.fromEntries(new FormData(form).entries());
  ["TOTAL_INCOME", "CREDIT_AMOUNT", "ANNUAL_LOAN_PAYMENT", "GOODS_PRICE", "AGE", "YEARS_OF_EXPERIENCE", "CREDIT_SCORE", "CREDIT_HISTORY"].forEach(field => payload[field] = Number(payload[field]));
  const reviewer = document.querySelector("#loanReviewer")?.value;
  if (message) message.textContent = "Submitting…";
  if (button) button.disabled = true;
  try {
    const created = await api.createApplication({ source_type: "manual", reviewer_username: reviewer || undefined, applicant_snapshot: payload });
    if (stale()) return;
    selectedApplicationId = created.application_id;
    applicationsLoaded = false;
    if (message) message.textContent = `Created ${created.application_id} (${created.status}). Assessment not run yet.`;
    await loadLoanApplications({ force: true, offset: 0, isStale: stale });
    if (!stale()) await openApplication(created.application_id, { isStale: stale });
    return created;
  } catch (error) {
    if (message && !stale()) message.textContent = error.message;
    throw error;
  } finally {
    if (button && !stale()) button.disabled = false;
  }
}

export async function assessSelectedApplication() {
  const sessionStale = viewGuard();
  const applicationId = selectedApplicationId;
  const stale = () => sessionStale() || applicationId !== selectedApplicationId;
  const message = document.querySelector("#loanAssessMessage");
  const button = document.querySelector("#assessApplicationButton");
  if (!selectedApplicationId) return;
  if (button) button.disabled = true;
  if (message) message.textContent = "Running ML assessment…";
  try {
    const assessment = await api.assessApplication(applicationId);
    if (stale()) return;
    if (message) message.textContent = `Assessed: ${assessment.result.decision} (${(assessment.result.default_probability * 100).toFixed(2)}%).`;
    renderResult(document.querySelector("#resultContent"), assessment);
    document.querySelector("#resultDialog").showModal();
    applicationsLoaded = false;
    await loadLoanApplications({ force: true, isStale: sessionStale });
    if (!stale()) await openApplication(applicationId, { isStale: stale });
  } catch (error) {
    if (message && !stale()) message.textContent = `Assessment failed — application remains saved. Retry when ready. (${error.message})`;
  } finally {
    if (button && !stale()) button.disabled = false;
  }
}

export async function loadLoanApplications({ force = false, isStale, offset = loanOffset } = {}) {
  if (applicationsLoaded && !force && offset === loanOffset) return loanApplications;
  const sessionStale = viewGuard(isStale), epoch = ++loanEpoch;
  const stale = () => sessionStale() || epoch !== loanEpoch;
  const response = await api.listApplications(25, offset);
  if (stale()) return [];
  loanOffset = offset; loanApplications = response.applications || [];
  applicationsLoaded = true;
  renderLoanRows(stale);
  const previous = document.getElementById("loanPrev"), next = document.getElementById("loanNext"), info = document.getElementById("loanPageInfo");
  if (info) info.textContent = `${response.total || 0} applications; page ${Math.floor(offset / 25) + 1}`;
  const reload = nextOffset => loadLoanApplications({ force: true, offset: nextOffset, isStale }).catch(error => { if (!stale()) showError(error); });
  if (previous) { previous.disabled = offset === 0; previous.onclick = () => { if (!stale()) reload(Math.max(0, offset - 25)); }; }
  if (next) { next.disabled = offset + 25 >= (response.total || 0); next.onclick = () => { if (!stale()) reload(offset + 25); }; }
  return loanApplications;
}

export async function openApplication(applicationId, { isStale } = {}) {
  const sessionStale = viewGuard(isStale);
  isStale = () => sessionStale() || applicationId !== selectedApplicationId;
  selectedApplicationId = applicationId;
  const detailTarget = document.querySelector("#loanDetail");
  const assessButton = document.querySelector("#assessApplicationButton");
  const statusTarget = document.querySelector("#loanAssessMessage");
  if (detailTarget) detailTarget.innerHTML = "<p class='muted'>Loading application…</p>";
  if (assessButton) assessButton.disabled = true;
  try {
    const application = await api.getApplication(applicationId);
    if (isStale?.()) return;
    const predictions = await api.listPredictions(applicationId);
    if (isStale?.()) return;
    const latest = predictions.predictions[0];
    if (detailTarget) {
      detailTarget.innerHTML = `<div class="stat-row"><span>Application</span><b>${application.application_id}</b></div><div class="stat-row"><span>Status</span><b>${application.status}</b></div><div class="stat-row"><span>Owner</span><b>${escape(application.owner)}</b></div><div class="stat-row"><span>Reviewer</span><b>${escape(application.assigned_reviewer || "—")}</b></div><div class="stat-row"><span>Assessments</span><b>${predictions.predictions.length}</b></div>${latest ? `<div class="stat-row"><span>Latest decision</span><b>${latest.model_decision}</b></div><div class="stat-row"><span>Default probability</span><b>${(latest.default_probability * 100).toFixed(2)}%</b></div><div class="stat-row"><span>Risk tier</span><b>${latest.risk_tier}</b></div>` : "<p class='muted'>Created, not yet assessed. Run the ML assessment below.</p>"}`;
    }
    if (detailTarget && application.source_type === "applicant") {
      detailTarget.innerHTML += `<h4>Submitted inputs</h4><dl class="portal-details">${Object.entries(application.applicant_snapshot || {}).map(([key, value]) => `<dt>${escape(key.toLowerCase().replaceAll("_", " "))}</dt><dd>${escape(value)}</dd>`).join("")}</dl>`;
    }
    if (assessButton) assessButton.disabled = ["approved", "rejected"].includes(application.status);
    if (statusTarget) statusTarget.textContent = latest ? `Latest prediction ${latest.prediction_id.slice(0, 8)}… — repeats add records.` : "";
  } catch (error) {
    if (detailTarget && !isStale()) detailTarget.textContent = error.message;
  }
}

function renderLoanRows(isStale) {
  const body = document.querySelector("#loanApplicationRows");
  if (!body) return;
  if (!loanApplications.length) {
    body.innerHTML = "<tr><td colspan='6'>No applications submitted yet.</td></tr>";
    return;
  }
  body.innerHTML = loanApplications.map(item => `<tr><td class="id">${item.application_id.slice(0, 8)}…</td><td>${(item.created_at || "").slice(0, 19).replace("T", " ")}</td><td>${escape(item.assigned_reviewer || "—")}</td><td>${item.status}</td><td>${item.prediction_count ?? "—"}</td><td><button class="run" data-application="${item.application_id}">Open →</button></td></tr>`).join("");
  body.querySelectorAll("[data-application]").forEach(button => button.onclick = () => { if (!isStale?.()) openApplication(button.dataset.application, { isStale }); });
}

export async function loadReviewers({ isStale } = {}) {
  const stale = viewGuard(isStale);
  const select = document.querySelector("#loanReviewer");
  if (!select) return;
  const response = await api.reviewers();
  if (stale()) return;
  select.innerHTML = `<option value="">Select reviewer…</option>${response.reviewers.map(name => `<option value="${escape(name)}">${escape(name)}</option>`).join("")}`;
}
