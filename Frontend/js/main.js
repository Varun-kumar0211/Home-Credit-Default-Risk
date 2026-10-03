import { api, hasToken, setToken } from "./api.js";
import { assessManual, loadDefault, upload } from "./views.js";

const $ = selector => document.querySelector(selector);
const required = selector => {
  const element = $(selector);
  if (!element) throw new Error(`Missing required UI element: ${selector}`);
  return element;
};
const fields = ["GENDER", "QUALIFICATION", "FAMILY_STATUS", "OCCUPATION", "CONTRACT_TYPE", "TOTAL_INCOME", "CREDIT_AMOUNT", "ANNUAL_LOAN_PAYMENT", "GOODS_PRICE", "AGE", "YEARS_OF_EXPERIENCE", "CREDIT_SCORE", "CREDIT_HISTORY"];
const show = id => document.querySelectorAll(".page").forEach(page => page.classList.toggle("hidden", page.id !== id));

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
    alert("Settings saved!");
  };
}

async function start() {
  required("#loginForm").onsubmit = async event => {
    event.preventDefault();
    try {
      const response = await api.login($("#username").value, $("#password").value);
      setToken(response.access_token);
      required("#loginPanel").classList.add("hidden");
      required("#workspace").classList.remove("hidden");
      await loadDefault();
    } catch (error) { required("#loginMessage").textContent = error.message; }
  };
  window.addEventListener("auth-expired", () => {
    setToken(null); required("#workspace").classList.add("hidden"); required("#loginPanel").classList.remove("hidden");
  });
  required("#logout").onclick = () => { setToken(null); location.reload(); };
  document.querySelectorAll("[data-page]").forEach(link => link.onclick = () => show(link.dataset.page));
  required("#defaultLink").onclick = loadDefault;
  required("#csvFile").onchange = event => required("#fileName").textContent = event.target.files[0]?.name || "";
  required("#uploadButton").onclick = () => { const file = required("#csvFile").files[0]; if (file) upload(file); };
  required("#closeDialog").onclick = () => required("#resultDialog").close();
  setupManualForm();
  setupSettingsForm();
  if (hasToken()) { required("#loginPanel").classList.add("hidden"); required("#workspace").classList.remove("hidden"); await loadDefault(); }
}
try { start(); } catch (error) { console.error(error); }
