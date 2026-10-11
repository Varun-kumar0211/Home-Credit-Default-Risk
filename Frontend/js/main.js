import { api, currentToken, hasToken, setToken, setReadOnlyDemo } from "./api.js";
import { assessManual, assessSelectedApplication, loadDefault, loadLoanApplications, loadReviewers, openApplication, refreshRiskLabels, resetDatasetCache, submitLoanApplication, upload } from "./views.js";
import {
  loadAssignedApplications,
  loadPendingReviews,
  loadReviewHistory,
  openReviewApplication,
  loadReviewDetail,
  submitReviewDecision,
  renderFinancialInfo,
  renderPredictionInfo,
  renderDecisionActions,
  renderShapInfo,
  clearReviewerState,
  setReviewerNavigator
} from "./reviewer.js";
import { clearAdminState, loadAdminAssignments, loadAdminOverview, loadAdminUsers, loadAdminAudit } from "./admin.js";

import { clearPortalState, loadApplicantApplications, loadIntake, setupApplicantForm, setPortalNavigator } from "./portal.js";

const VALID_ROLES = new Set(["analyst", "reviewer", "admin", "applicant"]);
let session = null;
let analystLoaded = false;
let sessionSeq = 0;
let navigationSeq = 0;

function showPage(id) {
  const role = session?.role;
  const target = document.getElementById(id);
  if (!target || !role) return false;
  if (target.dataset.role && target.dataset.role !== role) return false;
  navigationSeq += 1;
  document.querySelectorAll(".page").forEach(page => {
    page.classList.toggle("hidden", page.id !== id);
  });
  return true;
}
setReviewerNavigator(showPage);
function sessionGuard() {
  const sequence = sessionSeq, token = currentToken();
  return () => sequence !== sessionSeq || token !== currentToken();
}
setPortalNavigator(showPage, async id => {
  if (session?.role !== "analyst") return;
  const isStale = sessionGuard();
  showPage("loanApplicationsPage");
  await Promise.all([loadLoanApplications({ force: true, offset: 0, isStale }), loadReviewers({ isStale })]);
  if (!isStale()) await openApplication(id, { isStale });
});

function showAuthView(view) {
  const login = view !== "register";
  document.querySelector("#loginForm")?.classList.toggle("hidden", !login);
  document.querySelector("#registerForm")?.classList.toggle("hidden", login);
  document.querySelector("#loginTab")?.classList.toggle("active", login);
  document.querySelector("#registerTab")?.classList.toggle("active", !login);
  document.querySelector("#loginTab")?.setAttribute("aria-selected", String(login));
  document.querySelector("#registerTab")?.setAttribute("aria-selected", String(!login));
  document.querySelector("#authTitle").textContent = login ? "Sign in to continue" : "Create your account";
  document.querySelector("#authSubtitle").textContent = login
    ? "Applicant, Analyst, Reviewer, and Admin accounts use the same secure sign-in. Your workspace opens automatically after verification."
    : "Public registration creates an Applicant account. Staff access is provisioned separately.";
  document.querySelector("#loginMessage").textContent = "";
}

function resetAuthChrome() {
  setReadOnlyDemo(false);
  document.querySelector("#demoNotice")?.classList.add("hidden");
  analystLoaded = false;
  clearAdminState();
  clearReviewerState();
  clearPortalState();
  resetDatasetCache();
  document.querySelector("#workspace").classList.add("hidden");
  document.querySelector("#loginPanel").classList.remove("hidden");
  document.querySelector("#sessionActions").classList.add("hidden");
  document.querySelector("#roleBadge").classList.add("hidden");
  document.querySelector("#roleBadge").textContent = "";
  ["applicantNav", "analystNav", "reviewerNav", "adminNav"].forEach(id => document.querySelector(`#${id}`)?.classList.add("hidden"));
  document.querySelectorAll(".page").forEach(page => page.classList.add("hidden"));
  const messageTarget = document.querySelector("#message");
  if (messageTarget) { messageTarget.textContent = ""; messageTarget.classList.add("hidden"); }
}

function showLogin(message = "") {
  session = null;
  sessionSeq += 1;
  clearReviewerState();
  resetAuthChrome();
  showAuthView("login");
  if (message) document.querySelector("#loginMessage").textContent = message;
}

function applySession(identity) {
  if (!VALID_ROLES.has(identity.role)) {
    showLogin("Your account role is not recognized. Please contact an administrator.");
    return false;
  }
  session = identity;
  setReadOnlyDemo(identity.read_only);
  document.querySelector("#demoNotice")?.classList.toggle("hidden", !identity.read_only);
  document.querySelector("#loginPanel").classList.add("hidden");
  document.querySelector("#workspace").classList.remove("hidden");
  document.querySelector("#sessionActions").classList.remove("hidden");
  const badge = document.querySelector("#roleBadge");
  badge.textContent = identity.role;
  badge.classList.remove("hidden");
  ["applicantNav", "analystNav", "reviewerNav", "adminNav"].forEach(id => document.querySelector(`#${id}`)?.classList.add("hidden"));
  document.querySelector(`#${identity.role}Nav`)?.classList.remove("hidden");
  if (identity.role === "analyst") {
    showPage("manualPage");
  } else if (identity.role === "reviewer") {
    showPage("reviewerAssignedPage");
    const requestId = sessionSeq;
    const requestToken = currentToken();
    const isStale = () => requestId !== sessionSeq || currentToken() !== requestToken;
    loadAssignedApplications({ isStale }).catch(console.error);
  } else if (identity.role === "applicant") {
    showPage("applicantApplicationsPage");
    loadApplicantApplications({ isStale: sessionGuard(), offset: 0 }).catch(console.error);
  } else if (identity.role === "admin") {
    showPage("adminOversightPage");
    const requestId = sessionSeq;
    const requestToken = currentToken();
    const isStale = () => requestId !== sessionSeq || currentToken() !== requestToken;
    loadAdminOverview({ isStale }).catch(console.error);
  }
  return true;
}

async function establishSession({ lazyAnalyst = false } = {}) {
  const requestId = sessionSeq + 1;
  sessionSeq = requestId;
  const requestToken = currentToken();
  session = null;
  resetAuthChrome();
  const identity = await api.me();
  if (requestId !== sessionSeq || currentToken() !== requestToken) return;
  if (!applySession(identity)) return;
  if (identity.role === "analyst" && identity.read_only) {
    enterLoanWorkspace();
    return;
  }
  if (identity.role === "analyst" && !lazyAnalyst && !analystLoaded) {
    analystLoaded = true;
    const navigation = navigationSeq;
    await loadDefault({ showPage, isStale: () => navigation !== navigationSeq || requestId !== sessionSeq || currentToken() !== requestToken });
  }
}

const $ = selector => document.querySelector(selector);
const required = selector => {
  const element = $(selector);
  if (!element) throw new Error(`Missing required UI element: ${selector}`);
  return element;
};
const fields = ["GENDER", "QUALIFICATION", "FAMILY_STATUS", "OCCUPATION", "CONTRACT_TYPE", "TOTAL_INCOME", "CREDIT_AMOUNT", "ANNUAL_LOAN_PAYMENT", "GOODS_PRICE", "AGE", "YEARS_OF_EXPERIENCE", "CREDIT_SCORE", "CREDIT_HISTORY"];
const show = id => showPage(id);

function enterLoanWorkspace() {
  if (session?.role !== "analyst") return;
  show("loanApplicationsPage");
  const requestId = sessionSeq;
  const requestToken = currentToken();
  const isStale = () => requestId !== sessionSeq || currentToken() !== requestToken;
  loadReviewers({ isStale }).catch(error => {
    const target = document.querySelector("#loanCreateMessage");
    if (target && !isStale()) target.textContent = `Reviewer list unavailable: ${error.message}`;
  });
  loadLoanApplications({ isStale }).catch(error => {
    if (isStale()) return;
    const body = document.querySelector("#loanApplicationRows");
    if (body) body.textContent = error.message;
  });
}

function getThresholds() {
  const approve = parseFloat(localStorage.getItem("approveThreshold")) || 8.0;
  const decline = parseFloat(localStorage.getItem("declineThreshold")) || 20.0;
  return { APPROVE_THRESHOLD: approve / 100, DECLINE_THRESHOLD: decline / 100 };
}

function setupManualForm() {
  required("#manualForm").onsubmit = event => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(event.target).entries());
    ["TOTAL_INCOME", "CREDIT_AMOUNT", "ANNUAL_LOAN_PAYMENT", "GOODS_PRICE", "AGE", "YEARS_OF_EXPERIENCE", "CREDIT_SCORE", "CREDIT_HISTORY"].forEach(field => payload[field] = Number(payload[field]));
    Object.assign(payload, getThresholds());
    assessManual(payload);
  };
}

function setupSettingsForm() {
  $("#approveThreshold").value = localStorage.getItem("approveThreshold") || "8.0";
  $("#declineThreshold").value = localStorage.getItem("declineThreshold") || "20.0";
  required("#settingsForm").onsubmit = event => {
    event.preventDefault();
    localStorage.setItem("approveThreshold", $("#approveThreshold").value);
    localStorage.setItem("declineThreshold", $("#declineThreshold").value);
    refreshRiskLabels();
    alert("Settings saved!");
  };
}

async function start() {
  api.demoAvailability().then(info => document.querySelector("#recruiterDemo")?.classList.toggle("hidden", !info.enabled)).catch(() => {});
  document.querySelectorAll("[data-demo-role]").forEach(button => {
    button.onclick = async () => {
      let attempt = ++sessionSeq;
      button.disabled = true;
      try {
        const response = await api.demoLogin(button.dataset.demoRole);
        if (attempt !== sessionSeq) return;
        setToken(response.access_token);
        attempt = sessionSeq + 1;
        await establishSession({ lazyAnalyst: true });
      } catch (error) {
        if (attempt === sessionSeq) document.querySelector("#loginMessage").textContent = error.message;
      } finally { button.disabled = false; }
    };
  });
  const loginForm = document.querySelector("#loginForm");
  if (loginForm) loginForm.onsubmit = async event => {
    event.preventDefault();
    let attempt = ++sessionSeq;
    try {
      const response = await api.login($("#username").value, $("#password").value);
      if (attempt !== sessionSeq) return;
      setToken(response.access_token);
      analystLoaded = false;
      attempt = sessionSeq + 1;
      await establishSession();
    } catch (error) {
      if (attempt !== sessionSeq) return;
      const target = document.querySelector("#loginMessage");
      if (target) target.textContent = error.message;
    }
  };
  document.querySelector("#loginTab") && (document.querySelector("#loginTab").onclick = () => showAuthView("login"));
  document.querySelector("#registerTab") && (document.querySelector("#registerTab").onclick = () => showAuthView("register"));
  const registerForm = document.querySelector("#registerForm");
  if (registerForm) registerForm.onsubmit = async event => {
    event.preventDefault();
    const username = $("#registerUsername").value.trim();
    const password = $("#registerPassword").value;
    const confirm = $("#registerConfirm").value;
    if (password !== confirm) {
      const target = document.querySelector("#loginMessage");
      if (target) target.textContent = "Passwords do not match.";
      return;
    }
    let attempt = ++sessionSeq;
    try {
      const response = await api.register(username, password);
      if (attempt !== sessionSeq) return;
      setToken(response.access_token);
      analystLoaded = false;
      attempt = sessionSeq + 1;
      await establishSession();
    } catch (error) {
      if (attempt !== sessionSeq) return;
      const target = document.querySelector("#loginMessage");
      if (target) target.textContent = error.message;
    }
  };
  window.addEventListener("auth-expired", () => {
    clearReviewerState();
    setToken(null); showLogin();
  });
  const logoutButton = document.querySelector("#logout");
  if (logoutButton) logoutButton.onclick = () => { clearReviewerState(); setToken(null); showLogin(); };
  document.querySelectorAll("[data-page]").forEach(link => link.onclick = () => {
    if (!session) return;
    if (session.role === "applicant" && ["applicantNewPage", "applicantApplicationsPage"].includes(link.dataset.page)) {
      show(link.dataset.page);
      const isStale = sessionGuard();
      if (link.dataset.page === "applicantNewPage") setupApplicantForm({ isStale });
      else loadApplicantApplications({ isStale, offset: 0 }).catch(console.error);
      return;
    }
    if (session.role === "analyst" && link.dataset.page === "analystIntakePage") {
      show(link.dataset.page); loadIntake({ isStale: sessionGuard(), offset: 0 }).catch(console.error); return;
    }
    if (link.dataset.page === "loanApplicationsPage") { enterLoanWorkspace(); return; }
    if (link.id === "defaultLink" && session?.role === "analyst") {
      analystLoaded = true;
      const requestId = sessionSeq;
      const requestToken = currentToken();
      loadDefault({ showPage, isStale: () => requestId !== sessionSeq || currentToken() !== requestToken }).catch(error => document.querySelector("#message") && (document.querySelector("#message").textContent = error.message));
      return;
    }
    if (session?.role === "reviewer") {
      const requestId = sessionSeq;
      const requestToken = currentToken();
      const isStale = () => requestId !== sessionSeq || currentToken() !== requestToken;
      if (link.dataset.page === "reviewerAssignedPage") {
        show(link.dataset.page);
        loadAssignedApplications({ isStale }).catch(console.error);
        return;
      }
      if (link.dataset.page === "reviewerPendingPage") {
        show(link.dataset.page);
        loadPendingReviews({ isStale }).catch(console.error);
        return;
      }
      if (link.dataset.page === "reviewerHistoryPage") {
        show(link.dataset.page);
        loadReviewHistory({ isStale }).catch(console.error);
        return;
      }
    }
    if (session?.role === "admin" && ["adminOversightPage", "adminAssignmentsPage", "adminUsersPage", "adminAuditPage"].includes(link.dataset.page)) {
      show(link.dataset.page);
      const requestId = sessionSeq;
      const requestToken = currentToken();
      const isStale = () => requestId !== sessionSeq || currentToken() !== requestToken;
      const loaders = { adminOversightPage: loadAdminOverview, adminAssignmentsPage: loadAdminAssignments,
        adminUsersPage: loadAdminUsers, adminAuditPage: loadAdminAudit };
      const page = document.getElementById(link.dataset.page);
      loaders[link.dataset.page]({ username: session.username,
        isStale: () => isStale() || page.classList.contains("hidden") }).catch(console.error);
      return;
    }
    show(link.dataset.page);
  });
  // Admin Refresh button
  const btnRefreshAdmin = document.querySelector("#btnRefreshAdminOverview");
  if (btnRefreshAdmin) btnRefreshAdmin.onclick = () => {
    if (!session || session.role !== "admin") return;
    const requestId = sessionSeq;
    const requestToken = currentToken();
    const isStale = () => requestId !== sessionSeq || currentToken() !== requestToken;
    loadAdminOverview({ isStale }).catch(console.error);
  };
  const defaultLink = document.querySelector("#defaultLink");
  if (defaultLink) defaultLink.onclick = () => {
    if (session?.role !== "analyst") return;
    analystLoaded = true;
    const requestId = sessionSeq;
    const requestToken = currentToken();
    loadDefault({ showPage, isStale: () => requestId !== sessionSeq || currentToken() !== requestToken }).catch(error => { const target = document.querySelector("#message"); if (target) { target.textContent = error.message; target.classList.remove("hidden"); } });
  };
  const csvFile = document.querySelector("#csvFile");
  if (csvFile) csvFile.onchange = event => { const name = document.querySelector("#fileName"); if (name) name.textContent = event.target.files[0]?.name || ""; };
  const uploadButton = document.querySelector("#uploadButton");
  if (uploadButton) uploadButton.onclick = () => {
    const fileInput = document.querySelector("#csvFile");
    const file = fileInput?.files[0];
    if (file && session) {
      const requestId = sessionSeq;
      const requestToken = currentToken();
      const addToCurrent = document.querySelector("#addToCurrent")?.checked;
      upload(file, addToCurrent, showPage, { isStale: () => requestId !== sessionSeq || currentToken() !== requestToken });
    }
  };
  const closeDialog = document.querySelector("#closeDialog");
  if (closeDialog) closeDialog.onclick = () => document.querySelector("#resultDialog").close();
  const loanForm = document.querySelector("#loanApplicationForm");
  if (loanForm) loanForm.onsubmit = event => {
    event.preventDefault();
    if (!session || session.role !== "analyst") return;
    submitLoanApplication(event.target).catch(() => {});
  };
  const assessButton = document.querySelector("#assessApplicationButton");
  if (assessButton) assessButton.onclick = () => {
    if (!session || session.role !== "analyst") return;
    assessSelectedApplication();
  };
  const refreshButton = document.querySelector("#refreshApplications");
  if (refreshButton) refreshButton.onclick = () => {
    if (!session || session.role !== "analyst") return;
    const requestId = sessionSeq;
    const requestToken = currentToken();
    const isStale = () => requestId !== sessionSeq || currentToken() !== requestToken;
    loadLoanApplications({ force: true, isStale }).catch(error => {
      const body = document.querySelector("#loanApplicationRows");
      if (body && !isStale()) body.textContent = error.message;
    });
  };
  setupManualForm();
  setupSettingsForm();
  if (hasToken()) {
    try { await establishSession(); }
    catch { showLogin(); }
  }
}
try { start(); } catch (error) { console.error(error); }
