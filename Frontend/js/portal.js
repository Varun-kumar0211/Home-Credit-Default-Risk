import { api, currentToken } from "./api.js";

let sessionEpoch = 0, applicantEpoch = 0, intakeEpoch = 0, detailEpoch = 0;
let applicantOffset = 0, intakeOffset = 0, selectedId = null, navigate = null, openClaimed = null;
let cancelConfirmation = null;
const numeric = ["TOTAL_INCOME", "CREDIT_AMOUNT", "ANNUAL_LOAN_PAYMENT", "GOODS_PRICE", "AGE", "YEARS_OF_EXPERIENCE", "CREDIT_SCORE", "CREDIT_HISTORY"];
const fields = ["GENDER", "QUALIFICATION", "FAMILY_STATUS", "OCCUPATION", "CONTRACT_TYPE", ...numeric];
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
const date = value => value ? `${String(value).slice(0, 19).replace("T", " ")} UTC` : "—";
const money = value => Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });

function guard(external, page) {
  const token = currentToken(), epoch = sessionEpoch;
  return () => token !== currentToken() || epoch !== sessionEpoch || Boolean(external?.()) || Boolean(page && $(page)?.classList.contains("hidden"));
}

function feedback(id, message) {
  const target = $(id);
  if (target) target.textContent = message;
}

export function setPortalNavigator(fn, claimed) { navigate = fn; openClaimed = claimed; }

export function clearPortalState() {
  sessionEpoch++; applicantEpoch++; intakeEpoch++; detailEpoch++;
  applicantOffset = intakeOffset = 0; selectedId = null;
  cancelConfirmation?.();
  ["portalApplicantRows", "intakeRows", "applicantDetail"].forEach(id => { if ($(id)) $(id).innerHTML = ""; });
  ["applicantFeedback", "applicantSubmitFeedback", "applicantDetailFeedback", "intakeFeedback", "applicantPageInfo", "intakePageInfo", "portalConfirmationText"].forEach(id => feedback(id, ""));
  $("applicantForm")?.reset();
  if ($("intakeReviewer")) $("intakeReviewer").innerHTML = '<option value="">Select active Reviewer…</option>';
  if ($("applicantSubmit")) $("applicantSubmit").disabled = false;
}

function pagination(prefix, page, reload, stale) {
  const { total = 0, limit = 25, offset = 0 } = page;
  feedback(`${prefix}PageInfo`, total ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total}` : "0 applications");
  $(`${prefix}Prev`).disabled = offset === 0;
  $(`${prefix}Next`).disabled = offset + limit >= total;
  $(`${prefix}Prev`).onclick = () => { if (!stale()) reload(Math.max(0, offset - limit)); };
  $(`${prefix}Next`).onclick = () => { if (!stale()) reload(offset + limit); };
}

export function confirmPortal(message, title = "Confirm application claim", acceptLabel = "Confirm claim") {
  cancelConfirmation?.();
  return new Promise(resolve => {
    const dialog = $("portalConfirmation");
    let settled = false;
    const finish = accepted => {
      if (settled) return;
      settled = true; cancelConfirmation = null; dialog.close();
      feedback("portalConfirmationText", ""); resolve(accepted);
    };
    cancelConfirmation = () => finish(false);
    feedback("portalConfirmationTitle", title);
    feedback("portalConfirmAccept", acceptLabel);
    feedback("portalConfirmationText", message);
    $("portalConfirmCancel").onclick = () => finish(false);
    $("portalConfirmAccept").onclick = () => finish(true);
    dialog.oncancel = event => { event.preventDefault(); finish(false); };
    dialog.showModal(); $("portalConfirmCancel").focus();
  });
}

export function setupApplicantForm({ isStale } = {}) {
  const sessionStale = guard(isStale);
  const stale = guard(isStale, "applicantNewPage");
  const form = $("applicantForm"), button = $("applicantSubmit");
  form.onsubmit = async event => {
    event.preventDefault();
    if (stale() || button.disabled) return;
    const values = Object.fromEntries(new FormData(form).entries());
    const applicant_snapshot = Object.fromEntries(fields.map(field => [field, numeric.includes(field) ? Number(values[field]) : values[field]]));
    if (numeric.some(field => !Number.isFinite(applicant_snapshot[field]))) {
      feedback("applicantSubmitFeedback", "Enter finite numbers for all financial fields."); return;
    }
    button.disabled = true;
    feedback("applicantSubmitFeedback", "Submitting application…");
    try {
      const application = await api.submitApplicant({ applicant_snapshot });
      if (stale()) return;
      form.reset();
      await openApplicantApplication(application.application_id, { isStale });
    } catch (error) {
      if (!stale()) feedback("applicantSubmitFeedback", error.message);
    } finally {
      // Resetting this button is safe only in the originating session.
      if (!sessionStale()) button.disabled = false;
    }
  };
}

export async function loadApplicantApplications({ isStale, offset = applicantOffset } = {}) {
  const epoch = ++applicantEpoch, obsolete = guard(isStale, "applicantApplicationsPage");
  const stale = () => epoch !== applicantEpoch || obsolete();
  const rows = $("portalApplicantRows");
  if (!rows || stale()) return;
  applicantOffset = offset;
  rows.innerHTML = '<tr><td colspan="5">Loading your applications…</td></tr>';
  rows.setAttribute("aria-busy", "true");
  feedback("applicantFeedback", "");
  $("applicantPrev").disabled = $("applicantNext").disabled = true;
  const reload = next => loadApplicantApplications({ isStale, offset: next });
  $("applicantRefresh").onclick = () => { if (!stale()) reload(offset); };
  try {
    const page = await api.applicantApplications(25, offset);
    if (stale()) return;
    pagination("applicant", page, reload, stale);
    rows.innerHTML = page.applications.length ? page.applications.map(item => `<tr><td class="id">${esc(item.application_id)}</td><td>${esc(date(item.created_at))}</td><td>${esc(money(item.loan_amount))}</td><td>${esc(item.progress)}</td><td><button type="button" class="secondary" data-applicant-open="${esc(item.application_id)}">View status</button></td></tr>`).join("") : '<tr><td colspan="5">No applications yet. Choose New application to submit one.</td></tr>';
    rows.querySelectorAll("[data-applicant-open]").forEach(button => {
      button.onclick = () => { if (!stale()) openApplicantApplication(button.dataset.applicantOpen, { isStale }); };
    });
  } catch (error) {
    if (!stale()) { rows.innerHTML = '<tr><td colspan="5">Applications unavailable. Use Refresh to try again.</td></tr>'; feedback("applicantFeedback", error.message); }
  } finally { if (!stale()) rows.setAttribute("aria-busy", "false"); }
}

export async function openApplicantApplication(id, { isStale } = {}) {
  if (guard(isStale)()) return;
  navigate?.("applicantDetailPage"); selectedId = id;
  const epoch = ++detailEpoch, obsolete = guard(isStale, "applicantDetailPage");
  const stale = () => epoch !== detailEpoch || selectedId !== id || obsolete();
  const target = $("applicantDetail");
  target.textContent = "Loading application…";
  feedback("applicantDetailFeedback", "");
  $("applicantDetailRefresh").onclick = () => { if (!stale()) openApplicantApplication(id, { isStale }); };
  try {
    const item = await api.applicantApplication(id);
    if (stale()) return;
    const summary = [["Application ID", item.application_id], ["Submitted", date(item.created_at)], ["Loan amount", money(item.loan_amount)], ["Progress", item.progress], ["Last updated", date(item.updated_at)]];
    const inputs = fields.filter(field => Object.hasOwn(item.applicant_snapshot || {}, field)).map(field => [field.toLowerCase().replaceAll("_", " "), item.applicant_snapshot[field]]);
    target.innerHTML = `<dl class="portal-details">${[...summary, ...inputs].map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>`;
    if (item.status === "needs_information") feedback("applicantDetailFeedback", "Additional information is required. Contact staff for clarification.");
  } catch (error) { if (!stale()) { target.textContent = ""; feedback("applicantDetailFeedback", error.message); } }
}

export async function loadIntake({ isStale, offset = intakeOffset } = {}) {
  const epoch = ++intakeEpoch, obsolete = guard(isStale, "analystIntakePage");
  const stale = () => epoch !== intakeEpoch || obsolete();
  const rows = $("intakeRows"), reviewer = $("intakeReviewer");
  if (!rows || stale()) return;
  intakeOffset = offset;
  rows.innerHTML = '<tr><td colspan="4">Loading intake…</td></tr>'; rows.setAttribute("aria-busy", "true");
  feedback("intakeFeedback", "");
  $("intakePrev").disabled = $("intakeNext").disabled = true;
  const reload = next => loadIntake({ isStale, offset: next });
  $("intakeRefresh").onclick = () => { if (!stale()) reload(offset); };
  try {
    const [page, staff] = await Promise.all([api.intake(25, offset), api.reviewers()]);
    if (stale()) return;
    reviewer.innerHTML = '<option value="">Select active Reviewer…</option>' + staff.reviewers.map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
    pagination("intake", page, reload, stale);
    rows.innerHTML = page.applications.length ? page.applications.map(item => `<tr><td class="id">${esc(item.application_id)}</td><td>${esc(item.owner)}</td><td>${esc(date(item.created_at))}</td><td><button class="secondary" type="button" data-intake-claim="${esc(item.application_id)}" ${staff.reviewers.length ? "" : "disabled"}>Claim and open</button></td></tr>`).join("") : '<tr><td colspan="4">No unclaimed submissions.</td></tr>';
    if (!staff.reviewers.length) feedback("intakeFeedback", "No active Reviewer is available. Ask an Admin to activate one.");
    rows.querySelectorAll("[data-intake-claim]").forEach(button => {
      button.onclick = async () => {
        if (stale() || button.disabled) return;
        const item = page.applications.find(app => app.application_id === button.dataset.intakeClaim);
        const selectedReviewer = reviewer.value;
        if (!staff.reviewers.includes(selectedReviewer)) { feedback("intakeFeedback", "Select an active Reviewer before claiming."); reviewer.focus(); return; }
        button.disabled = true;
        let conflicted = false;
        try {
          if (!await confirmPortal(`Claim application ${item.application_id} and assign Reviewer ${selectedReviewer}?`) || stale()) return;
          await api.claimIntake(item.application_id, { reviewer_username: selectedReviewer, expected_updated_at: item.updated_at });
          if (stale()) return;
          await openClaimed?.(item.application_id);
        } catch (error) {
          if (!stale()) {
            feedback("intakeFeedback", error.status === 409 ? `${error.message} Refresh intake before trying again.` : error.message);
            if (error.status === 409) conflicted = true;
          }
        } finally { if (!stale() && !conflicted) button.disabled = false; }
      };
    });
  } catch (error) {
    if (!stale()) { rows.innerHTML = '<tr><td colspan="4">Intake unavailable. Use Refresh to try again.</td></tr>'; feedback("intakeFeedback", error.message); }
  } finally { if (!stale()) rows.setAttribute("aria-busy", "false"); }
}
