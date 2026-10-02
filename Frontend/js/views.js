import { api } from "./api.js";
import { renderAnalysis, renderApplicants, renderCorrelation, renderGlobalShap, renderResult, renderTarget, renderMetrics, renderRiskOverview, renderHealth, renderRiskFeatures, renderSegments } from "./components.js";

let current = { dataset: null, applicants: [] };

function filteredApplicants() {
  const search = document.querySelector("#applicantSearch")?.value.toLowerCase() || "";
  const risk = document.querySelector("#riskFilter")?.value || "";
  const occupation = document.querySelector("#occupationFilter")?.value || "";
  const contract = document.querySelector("#contractFilter")?.value || "";
  const sort = document.querySelector("#sortApplicants")?.value || "risk-desc";
  const rows = current.applicants.filter(item => {
    const values = item.data || {};
    const probability = item.assessment?.default_probability;
    const tier = probability === undefined ? "" : probability < .08 ? "Low Risk" : probability < .2 ? "Manual Review" : "High Risk";
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

export function showDataset(payload) {
  document.querySelectorAll(".page").forEach(page => page.classList.toggle("hidden", page.id !== "dashboardPage"));
  current = { dataset: payload.dataset_id, applicants: payload.applicants };
  const analysis = renderAnalysis(payload.analysis);
  document.querySelector("#datasetTitle").textContent = `${payload.filename} · ${payload.analysis.rows} applicants`;
  document.querySelector("#metrics").innerHTML = renderMetrics(payload.analysis);
  document.querySelector("#riskOverview").innerHTML = renderRiskOverview(payload.analysis);
  document.querySelector("#healthSummary").textContent = payload.analysis.data_health?.validation_status === "good" ? "✓ Good" : "⚠ Review";
  document.querySelector("#numericProfile").innerHTML = analysis.numeric;
  document.querySelector("#qualityProfile").innerHTML = renderHealth(payload.analysis) + analysis.quality;
  document.querySelector("#categoryProfile").innerHTML = analysis.categories || "<p class='muted'>No categories detected.</p>";
  document.querySelector("#targetProfile").innerHTML = renderTarget(payload.analysis);
  document.querySelector("#riskFeatureProfile").innerHTML = renderRiskFeatures(payload.analysis);
  document.querySelector("#segmentProfile").innerHTML = renderSegments(payload.analysis);
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
  button.disabled = true;
  button.textContent = "Scoring…";
  try {
    const response = await api.predictApplicant(current.dataset, button.dataset.id);
    renderResult(document.querySelector("#resultContent"), response);
    document.querySelector("#resultDialog").showModal();
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
    button.textContent = "Run assessment →";
  }
}

export async function loadDefault() {
  try { showDataset(await api.defaultData()); } catch (error) { showError(error); }
}

export async function upload(file) {
  try { showDataset(await api.uploadCsv(file)); } catch (error) { showError(error); }
}

export async function assessManual(payload) {
  try {
    renderResult(document.querySelector("#resultContent"), await api.manualAssessment(payload));
    document.querySelector("#resultDialog").showModal();
  } catch (error) { showError(error); }
}
