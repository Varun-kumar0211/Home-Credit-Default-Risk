export const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
}[c]));

export function renderMetrics(analysis) {
  const missing = (analysis.missing_values || []).reduce((sum, item) => sum + item.missing, 0);
  const metrics = [["Applicants", analysis.rows], ["Fields", analysis.columns], ["Missing values", missing], ["Valid rows", analysis.valid_rows ?? analysis.rows]];
  if (analysis.average_default_probability !== undefined) metrics.push(["Avg. default risk", `${analysis.average_default_probability}%`]);
  return metrics
    .map(([label, value]) => `<div class="metric"><small>${label}</small><strong>${value}</strong><span class="subtle">Portfolio insight</span></div>`).join("");
}

export function renderRiskOverview(analysis) {
  const segments = analysis.risk_overview?.segments || [];
  const total = analysis.risk_overview?.total || 0;
  let offset = 0;
  const gradient = segments.map(item => {
    const start = offset;
    offset += item.percentage;
    return `var(--${item.tone}) ${start}% ${offset}%`;
  }).join(", ");
  return `<div class="risk-overview-layout"><div class="donut" style="background:conic-gradient(${gradient || "#d9e2ec 0 100%"})"><div><strong>${total}</strong><span>Applicants</span></div></div><div class="legend">${segments.map(item => `<div class="legend-row"><i class="dot ${item.tone}"></i><span>${esc(item.label)}</span><b>${item.count} <small>(${item.percentage}%)</small></b></div>`).join("")}</div></div>`;
}

export function renderHealth(analysis) {
  const health = analysis.data_health || {};
  const status = (value) => value === "good" ? ["good", "✓ Good"] : value === "warning" ? ["warn", "⚠ Warning"] : ["muted", "— Not available"];
  const rows = [
    ["Missing values", health.missing_cells ? ["warn", `${health.missing_cells} cells`] : ["good", "✓ None"]],
    ["Duplicate rows", health.duplicate_rows ? ["warn", health.duplicate_rows] : ["good", "✓ None"]],
    ["Schema / validation", status(health.validation_status)],
    ["Numeric range checks", status(health.range_status)],
    ["Category checks", status(health.category_status)],
    ["Data drift", status(health.drift_status)],
  ];
  return rows.map(([label, [tone, text]]) => `<div class="health-row"><span>${label}</span><b class="${tone}">${text}</b></div>`).join("");
}

export function renderAnalysis(analysis) {
  const numeric = (analysis.numeric_summary || []).map(item => {
    const range = (item.maximum ?? 0) - (item.minimum ?? 0);
    const left = range ? ((item.q1 - item.minimum) / range) * 100 : 0;
    const width = range ? ((item.q3 - item.q1) / range) * 100 : 100;
    const median = range ? ((item.median - item.minimum) / range) * 100 : 50;
    return `<div class="profile-row"><div class="stat-row"><span>${esc(item.field)}</span><b>${item.average ?? "—"}</b></div><div class="boxplot"><i style="left:${left}%;width:${Math.max(2, width)}%"></i><em style="left:${median}%"></em></div><small>${item.minimum ?? "—"} · Q1 ${item.q1 ?? "—"} · Median ${item.median ?? "—"} · Q3 ${item.q3 ?? "—"} · ${item.maximum ?? "—"}</small></div>`;
  }).join("");
  const missing = analysis.missing_values?.length
    ? analysis.missing_values.map(item => `<div class="stat-row"><span>${esc(item.field)}</span><b class="bad">${item.missing} missing</b></div>`).join("")
    : "<p class='good'>✓ No missing values detected</p>";
  const drift = (analysis.drift || []).map(item => `<div class="stat-row"><span>${esc(item.field)} <small>PSI ${item.psi ?? "—"}</small></span><b class="${item.status === "warning" ? "bad" : "good"}">${item.status}</b></div><div class="subtle">${esc(item.message)}</div>`).join("");
  const policy = `<p class="subtle"><b>Preprocessing:</b> ${esc(analysis.preprocessing?.missing_value_policy || "Invalid rows are retained for review and excluded from assessment.")}</p>`;
  const categories = (analysis.categorical_summary || []).map(item =>
    `<div class="category-item"><h4>${esc(item.field)} <span class="subtle">(${item.unique_values} values)</span></h4>${item.top_values.map(value => `<div class="category-line"><span>${esc(value.value)}</span><b>${value.count} <small>${analysis.rows ? Math.round(value.count / analysis.rows * 100) : 0}%</small></b></div>`).join("")}</div>`).join("");
  return { numeric: numeric || "<p class='muted'>No numeric fields detected.</p>", quality: `${missing}${drift}${policy}`, categories };
}

export function renderRiskFeatures(analysis) {
  return (analysis.risk_by_feature || []).map(profile => {
    const max = Math.max(...profile.groups.map(item => item.average_default_probability), 1);
    return `<div class="feature-risk"><h4>${esc(profile.field)}</h4>${profile.groups.map(item => `<div class="stat-row"><span>${esc(item.label)} <small>n=${item.applicants}</small></span><b>${item.average_default_probability}%</b></div><div class="bar"><i style="width:${item.average_default_probability / max * 100}%"></i></div>`).join("")}</div>`;
  }).join("") || "<p class='muted'>No suitable numeric fields were available for grouped risk analysis.</p>";
}

export function renderSegments(analysis) {
  const entries = Object.entries(analysis.segment_analysis || {}).filter(([field]) => field !== "OCCUPATION");
  return entries.map(([field, items]) => `<div class="segment-block"><h4>${esc(field.replaceAll("_", " "))}</h4>${items.slice(0, 5).map(item => `<div class="stat-row"><span>${esc(item.segment)} <small>n=${item.applicants}</small></span><b>${item.average_default_probability}%</b></div><div class="bar"><i style="width:${Math.min(100, item.average_default_probability)}%"></i></div>`).join("")}</div>`).join("") || "<p class='muted'>No additional categorical segments are available.</p>";
}

export function renderChart(chart) {
  if (!chart?.bins?.length) return "<p class='muted'>No distribution data available.</p>";
  const max = Math.max(...chart.counts, 1);
  return chart.bins.map((label, index) => `<div class="chart-column"><span class="chart-value">${chart.counts[index]}</span><div class="chart-bar" style="height:${Math.max(8, chart.counts[index] / max * 150)}px"></div><span class="chart-label">${esc(label)}</span></div>`).join("");
}

export function renderTarget(analysis) {
  const items = analysis.segment_analysis?.OCCUPATION || analysis.target_by_segment?.segments || [];
  const max = Math.max(...items.map(item => item.average_default_probability), 1);
  return items.map(item => `<div class="stat-row"><span>${esc(item.segment)} <small>(${item.applicants})</small></span><b>${item.average_default_probability}%</b></div><div class="bar"><i style="width:${Math.min(100, item.average_default_probability / max * 100)}%"></i></div>`).join("") || "<p class='muted'>No segment target data available.</p>";
}

export function renderCorrelation(analysis) {
  const fields = analysis.correlations?.fields || [];
  const values = analysis.correlations?.values || [];
  if (!fields.length) return "<p class='muted'>No numeric financial fields available.</p>";
  const strongest = (analysis.strongest_correlations || []).map(item => `<li>${esc(item.left)} ↔ ${esc(item.right)}: <b>${item.value > 0 ? "+" : ""}${Number(item.value).toFixed(2)}</b></li>`).join("");
  return `<div class="correlation-wrap"><div class="correlation-axis">${fields.map(field => `<span>${esc(field)}</span>`).join("")}</div><div class="correlation-grid">${values.map((row, rowIndex) => row.map((value, columnIndex) => `<div class="correlation-cell" style="background:rgba(15,118,110,${Math.min(.85,Math.abs(value))})" title="${esc(fields[rowIndex])} vs ${esc(fields[columnIndex])}">${value.toFixed(2)}</div>`).join("")).join("")}</div></div><div class="strongest"><b>Strongest correlations</b><ul>${strongest || "<li>No pairs available.</li>"}</ul></div>`;
}

export function renderGlobalShap(analysis) {
  const items = analysis.global_shap || [];
  const max = Math.max(...items.map(item => item.mean_abs_impact), 1);
  return items.map(item => `<div class="stat-row"><span>${esc(item.label)}</span><b>${item.mean_abs_impact.toFixed(4)}</b></div><div class="bar"><i style="width:${item.mean_abs_impact / max * 100}%"></i></div>`).join("") || "<p class='muted'>Run valid assessments to generate global SHAP drivers.</p>";
}

export function renderApplicants(applicants, onAssess) {
  const rows = applicants.map(item => {
    const values = item.data;
    const invalid = item.validation?.length;
    const probability = item.assessment?.default_probability;
    const risk = probability === undefined ? null : probability < .08 ? "Low Risk" : probability < .2 ? "Manual Review" : "High Risk";
    return `<tr><td class="id">${item.id}</td><td class="profile">${esc(values.OCCUPATION || "—")}</td><td>${esc(values.CONTRACT_TYPE || "—")}</td><td>${values.CREDIT_SCORE ?? "—"}</td><td>$${Number(values.CREDIT_AMOUNT || 0).toLocaleString()}</td><td>${probability === undefined ? "—" : `${(probability * 100).toFixed(2)}%`}</td><td>${risk ? `<span class="risk-badge ${risk === "Low Risk" ? "low" : risk === "High Risk" ? "high" : "review"}">${risk}</span>` : "—"}</td><td>${invalid ? `<span class="bad">${item.validation.length} validation errors</span>` : `<button class="run" data-id="${item.id}">Run assessment →</button>`}</td></tr>`;
  }).join("");
  document.querySelector("#applicantRows").innerHTML = rows || "<tr><td colspan='8'>No applicants found.</td></tr>";
  document.querySelectorAll(".run").forEach(button => button.onclick = () => onAssess(button));
}

export function renderResult(container, response) {
  const result = response.result || response;
  const explanation = (items, positive) => (items || []).map(item =>
    `<div class="contributor"><div><strong>${esc(item.label)}</strong><small>Value: ${esc(item.value)} · ${positive ? "increases" : "reduces"} estimated risk</small></div><b class="${positive ? "bad" : "good"}">${item.impact > 0 ? "+" : ""}${Number(item.impact).toFixed(4)}</b></div>`).join("");
  container.innerHTML = `<div class="result"><p class="eyebrow">${esc(response.applicant_id || "MANUAL ASSESSMENT")}</p><div class="decision">${esc(result.decision)}</div><div class="result-grid">${[["Risk tier", result.risk_tier],["Default probability", `${(result.default_probability * 100).toFixed(2)}%`],["Risk score", `${result.risk_score.toFixed(2)}/100`],["Trust score", `${result.trust_score.toFixed(2)}/100`],["Confidence", `${(result.confidence * 100).toFixed(0)}%`],["Recommended rate", result.recommended_rate]].map(([label, value]) => `<div class="result-card"><small>${label}</small><strong>${esc(value)}</strong></div>`).join("")}</div><div class="contributor-grid"><section class="contributor-panel"><h3>Top positive contributors</h3><p class="muted">Push default risk higher.</p>${explanation(result.explanations?.positive, true)}</section><section class="contributor-panel"><h3>Top negative contributors</h3><p class="muted">Pull default risk lower.</p>${explanation(result.explanations?.negative, false)}</section></div></div>`;
}
