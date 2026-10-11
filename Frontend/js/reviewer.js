import { api, currentToken } from "./api.js";
import { confirmPortal } from "./portal.js";

// Reviewer internal state
let currentReviewerAppId = null;
let pageNavigator = null;
let reviewerSessionSeq = 0;
const queueOffsets = { reviewerAssigned: 0, reviewerPending: 0, reviewerHistory: 0 };
const queueEpochs = { reviewerAssigned: 0, reviewerPending: 0, reviewerHistory: 0 };
function reviewerPager(prefix, response, offset, loader, external, stale) {
  const previous = document.querySelector(`#${prefix}Prev`), next = document.querySelector(`#${prefix}Next`);
  const info = document.querySelector(`#${prefix}PageInfo`), refresh = document.querySelector(`#${prefix}Refresh`);
  const total = response.total ?? (response.applications || response.history || []).length;
  if (info) info.textContent = `${total} applications; page ${Math.floor(offset / 25) + 1}`;
  const reload = value => { if (!stale()) loader({ isStale: external, offset: value }); };
  if (previous) { previous.disabled = offset === 0; previous.onclick = () => reload(Math.max(0, offset - 25)); }
  if (next) { next.disabled = offset + 25 >= total; next.onclick = () => reload(offset + 25); }
  if (refresh) refresh.onclick = () => reload(offset);
}

function reviewerSessionGuard(isStale) {
  const token = currentToken();
  const sequence = reviewerSessionSeq;
  return () => token !== currentToken() || sequence !== reviewerSessionSeq || Boolean(isStale?.());
}

/**
 * Configure page navigation callback from main.js.
 * @param {Function} fn - Function to switch visible page
 */
export function setReviewerNavigator(fn) {
  pageNavigator = fn;
}

/**
 * Safe HTML escaping to prevent XSS from applicant-controlled or user-controlled text.
 * @param {*} value
 * @returns {string}
 */
function escapeHtml(value) {
  if (value == null) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Clear reviewer state (called on logout/account switch).
 */
export function clearReviewerState() {
  currentReviewerAppId = null;
  reviewerSessionSeq += 1;
  for (const key of Object.keys(queueOffsets)) { queueOffsets[key] = 0; queueEpochs[key]++; }
  ["assignedApplicationsBody", "pendingReviewsBody", "reviewHistoryBody", "reviewDetailAppInfo", "reviewFinancialInfo", "reviewPredictionInfo", "reviewShapInfo", "reviewExistingHistory", "reviewDecisionActions"].forEach(id => {
    const target = document.querySelector(`#${id}`); if (target) target.textContent = "";
  });
}

// ============================================================
// Queue endpoints & rendering
// ============================================================

/**
 * Load applications assigned to the authenticated reviewer and render them.
 * @param {Object} [options]
 * @param {Function} [options.isStale] - Guard against outdated async callbacks
 * @returns {Promise<Array>}
 */
export async function loadAssignedApplications({ isStale, offset = queueOffsets.reviewerAssigned } = {}) {
  const external = reviewerSessionGuard(isStale), epoch = ++queueEpochs.reviewerAssigned;
  isStale = () => external() || epoch !== queueEpochs.reviewerAssigned;
  queueOffsets.reviewerAssigned = offset;
  const tbody = document.querySelector("#assignedApplicationsBody");
  if (tbody) {
    tbody.innerHTML = `<tr><td colspan="5">Loading assigned applications...</td></tr>`;
  }

  try {
    const response = await api.getAssignedApplications(25, offset);
    if (isStale?.()) return [];
    reviewerPager("reviewerAssigned", response, offset, loadAssignedApplications, external, isStale);
    const applications = response.applications || [];

    if (!tbody) return applications;

    if (applications.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5">No applications currently assigned to you.</td></tr>`;
      return applications;
    }

    tbody.innerHTML = applications
      .map(
        app => `
      <tr>
        <td class="id">${escapeHtml(app.application_id)}</td>
        <td>${escapeHtml((app.created_at || "").slice(0, 19).replace("T", " "))}</td>
        <td>${escapeHtml(app.assigned_analyst || app.owner || "—")}</td>
        <td><span class="tag">${escapeHtml(app.status || "—")}</span></td>
        <td><button class="run" data-open-review="${escapeHtml(app.application_id)}">Open →</button></td>
      </tr>`
      )
      .join("");

    tbody.querySelectorAll("[data-open-review]").forEach(btn => {
      btn.onclick = () => openReviewApplication(btn.dataset.openReview, { isStale });
    });

    return applications;
  } catch (error) {
    if (tbody && !isStale?.()) {
      tbody.innerHTML = `<tr><td colspan="5" class="bad">Failed to load assigned applications: ${escapeHtml(error.message)}</td></tr>`;
    }
    return [];
  }
}

/**
 * Load applications assigned to the reviewer that are still pending review.
 * @param {Object} [options]
 * @param {Function} [options.isStale]
 * @returns {Promise<Array>}
 */
export async function loadPendingReviews({ isStale, offset = queueOffsets.reviewerPending } = {}) {
  const external = reviewerSessionGuard(isStale), epoch = ++queueEpochs.reviewerPending;
  isStale = () => external() || epoch !== queueEpochs.reviewerPending;
  queueOffsets.reviewerPending = offset;
  const tbody = document.querySelector("#pendingReviewsBody");
  if (tbody) {
    tbody.innerHTML = `<tr><td colspan="5">Loading pending reviews...</td></tr>`;
  }

  try {
    const response = await api.getPendingReviews(25, offset);
    if (isStale?.()) return [];
    reviewerPager("reviewerPending", response, offset, loadPendingReviews, external, isStale);
    const applications = response.applications || [];

    if (!tbody) return applications;

    if (applications.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5">No pending reviews awaiting your decision.</td></tr>`;
      return applications;
    }

    tbody.innerHTML = applications
      .map(
        app => `
      <tr>
        <td class="id">${escapeHtml(app.application_id)}</td>
        <td>${escapeHtml((app.created_at || "").slice(0, 19).replace("T", " "))}</td>
        <td>${escapeHtml(app.assigned_analyst || app.owner || "—")}</td>
        <td><span class="tag">${escapeHtml(app.status || "—")}</span></td>
        <td><button class="run" data-open-review="${escapeHtml(app.application_id)}">Open →</button></td>
      </tr>`
      )
      .join("");

    tbody.querySelectorAll("[data-open-review]").forEach(btn => {
      btn.onclick = () => openReviewApplication(btn.dataset.openReview, { isStale });
    });

    return applications;
  } catch (error) {
    if (tbody && !isStale?.()) {
      tbody.innerHTML = `<tr><td colspan="5" class="bad">Failed to load pending reviews: ${escapeHtml(error.message)}</td></tr>`;
    }
    return [];
  }
}

/**
 * Load completed review history for the authenticated reviewer.
 * @param {Object} [options]
 * @param {Function} [options.isStale]
 * @returns {Promise<Array>}
 */
export async function loadReviewHistory({ isStale, offset = queueOffsets.reviewerHistory } = {}) {
  const external = reviewerSessionGuard(isStale), epoch = ++queueEpochs.reviewerHistory;
  isStale = () => external() || epoch !== queueEpochs.reviewerHistory;
  queueOffsets.reviewerHistory = offset;
  const tbody = document.querySelector("#reviewHistoryBody");
  if (tbody) {
    tbody.innerHTML = `<tr><td colspan="5">Loading review history...</td></tr>`;
  }

  try {
    const response = await api.getReviewHistory(25, offset);
    if (isStale?.()) return [];
    reviewerPager("reviewerHistory", response, offset, loadReviewHistory, external, isStale);
    const history = response.history || [];

    if (!tbody) return history;

    if (history.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5">No completed reviews recorded.</td></tr>`;
      return history;
    }

    tbody.innerHTML = history
      .map(item => {
        const decisionClass = item.final_decision === "approved" ? "good" : "bad";
        const overrideBadge = item.is_override
          ? `<span class="risk-badge high">Yes${item.override_reason ? `: ${escapeHtml(item.override_reason)}` : ""}</span>`
          : `<span class="risk-badge low">No</span>`;

        return `
      <tr>
        <td class="id">${escapeHtml(item.application_id)}</td>
        <td><strong class="${decisionClass}">${escapeHtml(item.final_decision)}</strong></td>
        <td>${escapeHtml((item.review_date || "").slice(0, 19).replace("T", " "))}</td>
        <td>${overrideBadge}</td>
        <td>${escapeHtml(item.notes || "—")}</td>
      </tr>`;
      })
      .join("");

    return history;
  } catch (error) {
    if (tbody && !isStale?.()) {
      tbody.innerHTML = `<tr><td colspan="5" class="bad">Failed to load review history: ${escapeHtml(error.message)}</td></tr>`;
    }
    return [];
  }
}

// ============================================================
// Application detail loading & display
// ============================================================

/**
 * Fetch application details, stored predictions, and previous reviews.
 * Does NOT run new ML inference.
 * @param {string} appId
 * @returns {Promise<Object>}
 */
export async function loadReviewDetail(appId) {
  const [appResponse, predsResponse, reviewsResponse] = await Promise.all([
    api.getApplication(appId),
    api.listPredictions(appId),
    api.listReviews(appId),
  ]);

  const predictions = predsResponse.predictions || [];
  let explanation = null;

  if (predictions.length > 0 && predictions[0].prediction_id) {
    try {
      const explResponse = await api.getPredictionExplanation(appId, predictions[0].prediction_id);
      explanation = explResponse.explanation || null;
    } catch {
      explanation = null;
    }
  }

  return {
    application: appResponse,
    predictions,
    reviews: reviewsResponse.reviews || [],
    explanation,
  };
}

/**
 * Switch view to Reviewer Detail page and populate application information.
 * @param {string} appId
 * @param {Object} [options]
 * @param {Function} [options.isStale]
 */
export async function openReviewApplication(appId, { isStale } = {}) {
  isStale = reviewerSessionGuard(isStale);
  currentReviewerAppId = appId;

  if (pageNavigator) {
    pageNavigator("reviewDetailPage");
  } else {
    document.querySelectorAll(".page").forEach(page => {
      page.classList.toggle("hidden", page.id !== "reviewDetailPage");
    });
  }

  const appInfoTarget = document.querySelector("#reviewDetailAppInfo");
  if (appInfoTarget) {
    appInfoTarget.textContent = `Loading application ${appId}…`;
  }

  const financialContainer = document.querySelector("#reviewFinancialInfo");
  const predictionContainer = document.querySelector("#reviewPredictionInfo");
  const shapContainer = document.querySelector("#reviewShapInfo");
  const historyContainer = document.querySelector("#reviewExistingHistory");
  const actionsContainer = document.querySelector("#reviewDecisionActions");

  if (financialContainer) financialContainer.innerHTML = `<p class="muted">Loading financial profile…</p>`;
  if (predictionContainer) predictionContainer.innerHTML = `<p class="muted">Loading model predictions…</p>`;
  if (shapContainer) shapContainer.innerHTML = `<p class="muted">Loading explainable risk factors…</p>`;
  if (actionsContainer) actionsContainer.innerHTML = `<p class="muted">Loading actions…</p>`;

  try {
    const detail = await loadReviewDetail(appId);
    if (isStale?.() || currentReviewerAppId !== appId) return;

    const { application, predictions, reviews, explanation } = detail;

    if (!application) {
      if (appInfoTarget) appInfoTarget.textContent = `Application ${appId} not found.`;
      return;
    }

    if (appInfoTarget) {
      appInfoTarget.textContent = `Application ID: ${application.application_id} · Analyst: ${application.assigned_analyst || application.owner} · Status: ${application.status}`;
    }

    if (financialContainer) {
      renderFinancialInfo(financialContainer, application);
    }

    if (predictionContainer) {
      renderPredictionInfo(predictionContainer, predictions);
    }

    if (shapContainer) {
      renderShapInfo(shapContainer, explanation);
    }

    if (historyContainer) {
      if (reviews && reviews.length > 0) {
        historyContainer.classList.remove("hidden");
        historyContainer.innerHTML = `
          <h3>Previous Reviews</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Reviewer</th>
                  <th>Decision</th>
                  <th>Date</th>
                  <th>Override</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                ${reviews
                  .map(
                    r => `
                  <tr>
                    <td>${escapeHtml(r.reviewer_username)}</td>
                    <td><b>${escapeHtml(r.reviewer_decision)}</b></td>
                    <td>${escapeHtml((r.reviewed_at || "").slice(0, 19).replace("T", " "))}</td>
                    <td>${r.is_override ? `<span class="bad">Yes: ${escapeHtml(r.override_reason || "")}</span>` : '<span class="good">No</span>'}</td>
                    <td>${escapeHtml(r.review_notes || "—")}</td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        `;
      } else {
        historyContainer.classList.add("hidden");
        historyContainer.innerHTML = "";
      }
    }

    if (actionsContainer) {
      renderDecisionActions(actionsContainer, {
        application,
        predictions,
        reviews,
        isStale,
        onSubmitSuccess: async () => {
          if (isStale()) return;
          await openReviewApplication(appId, { isStale });
          loadPendingReviews({ isStale }).catch(() => {});
          loadAssignedApplications({ isStale }).catch(() => {});
          loadReviewHistory({ isStale }).catch(() => {});
        },
      });
    }
  } catch (error) {
    if (isStale() || currentReviewerAppId !== appId) return;
    if (appInfoTarget) appInfoTarget.textContent = `Error loading application: ${error.message}`;
    if (financialContainer) financialContainer.innerHTML = `<p class="bad">${escapeHtml(error.message)}</p>`;
  }
}

/**
 * Render applicant snapshot and loan parameters safely into container.
 * @param {HTMLElement} container
 * @param {Object} application
 */
export function renderFinancialInfo(container, application) {
  if (!container || !application) return;

  const snap = application.applicant_snapshot || {};
  const income = Number(snap.TOTAL_INCOME) || 0;
  const credit = Number(snap.CREDIT_AMOUNT) || 0;
  const annuity = Number(snap.ANNUAL_LOAN_PAYMENT) || 0;
  const goods = Number(snap.GOODS_PRICE) || 0;

  container.innerHTML = `
    <div class="panel-heading">
      <div>
        <h3>Financial Profile & Loan Request</h3>
        <p class="muted">Applicant-submitted parameters verified by analyst.</p>
      </div>
      <div>
        <button class="secondary" id="btnBackToPending" type="button">← Back to Pending Reviews</button>
      </div>
    </div>
    <div class="analysis-grid">
      <div>
        <div class="stat-row"><span>Application ID</span><b class="id">${escapeHtml(application.application_id)}</b></div>
        <div class="stat-row"><span>Assigned Analyst</span><b>${escapeHtml(application.assigned_analyst || application.owner || "—")}</b></div>
        <div class="stat-row"><span>Assigned Reviewer</span><b>${escapeHtml(application.assigned_reviewer || "—")}</b></div>
        <div class="stat-row"><span>Application Status</span><b class="tag">${escapeHtml(application.status || "—")}</b></div>
        <div class="stat-row"><span>Contract Type</span><b>${escapeHtml(snap.CONTRACT_TYPE || "—")}</b></div>
        <div class="stat-row"><span>Credit Score</span><b>${snap.CREDIT_SCORE ?? "—"}</b></div>
        <div class="stat-row"><span>Credit History</span><b>${snap.CREDIT_HISTORY === 1 ? '<span class="good">Good (1)</span>' : '<span class="bad">Past Default / None (0)</span>'}</b></div>
      </div>
      <div>
        <div class="stat-row"><span>Requested Credit</span><b>$${credit.toLocaleString()}</b></div>
        <div class="stat-row"><span>Annual Income</span><b>$${income.toLocaleString()}</b></div>
        <div class="stat-row"><span>Annual Annuity Payment</span><b>$${annuity.toLocaleString()}</b></div>
        <div class="stat-row"><span>Goods Price</span><b>$${goods.toLocaleString()}</b></div>
        <div class="stat-row"><span>Age</span><b>${snap.AGE ? escapeHtml(snap.AGE) + " yrs" : "—"}</b></div>
        <div class="stat-row"><span>Work Experience</span><b>${snap.YEARS_OF_EXPERIENCE ? escapeHtml(snap.YEARS_OF_EXPERIENCE) + " yrs" : "—"}</b></div>
        <div class="stat-row"><span>Occupation / Education</span><b>${escapeHtml(snap.OCCUPATION || "—")} (${escapeHtml(snap.QUALIFICATION || "—")})</b></div>
      </div>
    </div>
  `;

  const backBtn = container.querySelector("#btnBackToPending");
  if (backBtn) {
    backBtn.onclick = () => {
      if (pageNavigator) {
        pageNavigator("reviewerPendingPage");
      } else {
        document.querySelectorAll(".page").forEach(page => {
          page.classList.toggle("hidden", page.id !== "reviewerPendingPage");
        });
      }
      loadPendingReviews().catch(() => {});
    };
  }
}

/**
 * Render saved ML assessment and explanations safely into container.
 * @param {HTMLElement} container
 * @param {Array} predictions
 */
export function renderPredictionInfo(container, predictions) {
  if (!container) return;

  if (!predictions || predictions.length === 0) {
    container.innerHTML = `
      <h3>Model Risk Assessment</h3>
      <div class="message" style="background:#fef3c7;color:#92400e;border:1px solid #fcd34d;border-radius:10px;padding:14px;">
        <strong>Awaiting Analyst assessment</strong>
        <p class="muted" style="margin:6px 0 0;color:#78350f;">
          This application has not yet been processed through ML assessment. An assessment must be run by an analyst before a reviewer decision can be submitted.
        </p>
      </div>
    `;
    return;
  }

  const latest = predictions[0];
  const prob = Number(latest.default_probability) || 0;
  const probPct = (prob * 100).toFixed(2);
  const approveThresh = latest.approve_threshold != null ? (Number(latest.approve_threshold) * 100).toFixed(1) + "%" : "—";
  const declineThresh = latest.decline_threshold != null ? (Number(latest.decline_threshold) * 100).toFixed(1) + "%" : "—";

  const riskScoreDisplay = latest.risk_score != null && !isNaN(Number(latest.risk_score)) ? Number(latest.risk_score).toFixed(2) : "—";
  const trustScoreDisplay = latest.trust_score != null && !isNaN(Number(latest.trust_score)) ? Number(latest.trust_score).toFixed(2) : "—";

  const confVal = Number(latest.confidence);
  const confDisplay =
    latest.confidence != null && !isNaN(confVal) && confVal >= 0 && confVal <= 1
      ? `${(confVal * 100).toFixed(2)}% (Model certainty)`
      : latest.confidence != null
      ? escapeHtml(String(latest.confidence))
      : "—";

  const fullModelId = latest.model_id || "—";
  const shortModelId = fullModelId.length > 20 ? fullModelId.slice(0, 18) + "…" : fullModelId;

  let tierClass = "review";
  if (latest.risk_tier === "Low Risk") tierClass = "low";
  else if (latest.risk_tier === "High Risk") tierClass = "high";

  const recommendation = {
    "Auto Approve": { style: "approve", icon: "✓" },
    "Auto Decline": { style: "decline", icon: "✗" },
    "Manual Review Required": { style: "manual", icon: "⚠" },
  }[latest.model_decision] || { style: "neutral", icon: "ℹ" };

  container.innerHTML = `
    <h3>Model Risk Assessment</h3>
    <div class="analysis-grid">
      <div>
        <div class="stat-row">
          <span class="recommendation-label">MODEL RECOMMENDATION</span>
          <b class="model-recommendation model-recommendation--${recommendation.style}" role="status"><span aria-hidden="true">${recommendation.icon}</span><span>${escapeHtml(latest.model_decision || "Not available")}</span></b>
        </div>
        <div class="stat-row">
          <span>Default Probability</span>
          <b style="font-size:16px;color:var(--accent);">${probPct}%</b>
        </div>
        <div class="stat-row">
          <span>Risk Tier</span>
          <b class="risk-badge ${tierClass}">${escapeHtml(latest.risk_tier || "—")}</b>
        </div>
        <div class="stat-row">
          <span>Model Version ID</span>
          <b style="font-size:11px;" title="${escapeHtml(fullModelId)}">${escapeHtml(shortModelId)}</b>
        </div>
      </div>
      <div>
        <div class="stat-row"><span>Risk Score</span><b>${riskScoreDisplay}</b></div>
        <div class="stat-row"><span>Trust Score</span><b>${trustScoreDisplay}</b></div>
        <div class="stat-row"><span>Confidence</span><b>${confDisplay}</b></div>
        <div class="stat-row"><span>Thresholds (Approve / Decline)</span><b>${approveThresh} / ${declineThresh}</b></div>
        <div class="stat-row"><span>Recommended Rate</span><b>${latest.recommended_rate ? escapeHtml(latest.recommended_rate) : "—"}</b></div>
      </div>
    </div>
    <p class="muted" style="font-size:12px;margin-top:12px;">
      Stored prediction ID: <code>${escapeHtml(latest.prediction_id)}</code> · Generated: ${escapeHtml((latest.predicted_at || "").slice(0, 19).replace("T", " "))}
    </p>
  `;
}

/**
 * Render persisted SHAP feature contributions using a proportional horizontal bar chart.
 * @param {HTMLElement} container
 * @param {Object|null} explanation
 */
export function renderShapInfo(container, explanation) {
  if (!container) return;

  if (
    !explanation ||
    !Array.isArray(explanation.feature_contributions) ||
    explanation.feature_contributions.length === 0
  ) {
    container.innerHTML = `
      <h3>Explainable Risk Assessment (SHAP)</h3>
      <div class="message" style="background:#f8fafc;color:var(--muted);border:1px solid var(--line);border-radius:10px;padding:14px;">
        <strong>SHAP Explanation Unavailable</strong>
        <p class="muted" style="margin:4px 0 0;">No persisted SHAP feature contributions are available for this assessment.</p>
      </div>
    `;
    return;
  }

  const contributions = explanation.feature_contributions;
  const maxAbs = Math.max(...contributions.map(c => Math.abs(Number(c.impact) || 0)), 0.0001);

  const riskIncreasing = contributions
    .filter(c => Number(c.impact) > 0)
    .sort((a, b) => Number(b.impact) - Number(a.impact))
    .slice(0, 5);

  const riskDecreasing = contributions
    .filter(c => Number(c.impact) < 0)
    .sort((a, b) => Number(a.impact) - Number(b.impact))
    .slice(0, 5);

  const outputSpaceLabel = explanation.output_space || "raw_model_output (log-odds)";
  const baseValStr =
    explanation.base_value != null ? Number(explanation.base_value).toFixed(4) : "—";

  function renderRows(items, isPositive) {
    if (items.length === 0) {
      return `<p class="muted" style="font-size:12px;margin:10px 0;">No prominent ${isPositive ? "risk-increasing" : "risk-decreasing"} factors identified.</p>`;
    }
    return items
      .map(item => {
        const impactNum = Number(item.impact) || 0;
        const signedImpact = isPositive ? `+${impactNum.toFixed(4)}` : impactNum.toFixed(4);
        const absImpact = Math.abs(impactNum);
        const barPct = Math.max(8, Math.min(100, Math.round((absImpact / maxAbs) * 100)));
        const valLabel = item.value != null && item.value !== "" ? `(${escapeHtml(item.value)})` : "";

        return `
          <div class="shap-row">
            <div class="shap-row-header">
              <div>
                <span class="shap-feat-name">${escapeHtml(item.label || item.feature)}</span>
                <span class="shap-feat-val">${valLabel}</span>
              </div>
              <span class="shap-impact-val ${isPositive ? "pos" : "neg"}">${signedImpact}</span>
            </div>
            <div class="shap-bar-track">
              <div class="shap-bar-fill ${isPositive ? "pos" : "neg"}" style="width:${barPct}%;"></div>
            </div>
          </div>
        `;
      })
      .join("");
  }

  container.innerHTML = `
    <h3>Explainable Risk Assessment (SHAP Feature Contributions)</h3>
    <div class="shap-notice">
      <strong>SHAP Interpretation Note:</strong> Feature contributions represent signed additive impacts on the underlying LightGBM tree explainer margin space (<em>${escapeHtml(outputSpaceLabel)}</em>) prior to probability calibration.
      <br>
      • <strong style="color:var(--bad);">Positive values (+)</strong> increase default risk.
      • <strong style="color:var(--good);">Negative values (−)</strong> reduce default risk.
      <br>
      <small class="muted" style="display:block;margin-top:4px;">
        Values are unitless model-margin contributions and do not sum directly to the calibrated default probability. Base expected value: <code>${escapeHtml(baseValStr)}</code>.
      </small>
    </div>

    <div class="shap-grid" style="margin-top:14px;">
      <div class="shap-column">
        <h4>
          <span>Top Factors Increasing Risk</span>
          <span class="shap-badge-dir risk-up">▲ Increases Risk (+)</span>
        </h4>
        ${renderRows(riskIncreasing, true)}
      </div>

      <div class="shap-column">
        <h4>
          <span>Top Factors Reducing Risk</span>
          <span class="shap-badge-dir risk-down">▼ Reduces Risk (−)</span>
        </h4>
        ${renderRows(riskDecreasing, false)}
      </div>
    </div>
  `;
}

/**
 * Determine whether human decision overrides the saved model recommendation.
 * Correctly handles 'Auto Approve', 'Auto Decline', and 'Manual Review Required'.
 * @param {string} decision - "approved" or "rejected"
 * @param {string} modelDecision - e.g. "Auto Approve", "Auto Decline", "Manual Review Required"
 * @returns {boolean}
 */
export function isDecisionOverride(decision, modelDecision) {
  const expected = {
    "Auto Approve": "approved",
    "Auto Decline": "rejected",
    "Manual Review Required": "request_information",
  }[modelDecision];

  return expected ? decision !== expected : false;
}

/**
 * Render review decision actions (Approve / Reject) with override detection.
 * @param {HTMLElement} container
 * @param {Object} options
 * @param {Object} options.application
 * @param {Array} options.predictions
 * @param {Array} options.reviews
 * @param {Function} options.onSubmitSuccess
 */
export function renderDecisionActions(container, { application, predictions, reviews, onSubmitSuccess, isStale }) {
  isStale = reviewerSessionGuard(isStale);
  if (!container || !application) return;

  const isFinalized = application.status === "approved" || application.status === "rejected";
  const hasPrediction = Boolean(predictions && predictions.length > 0);
  const latestPrediction = hasPrediction ? predictions[0] : null;

  if (isFinalized) {
    const finalReview = reviews && reviews.length > 0 ? reviews[0] : null;
    const finalDecision = finalReview ? finalReview.reviewer_decision : application.status;
    const isOverride = finalReview ? finalReview.is_override : false;
    const badgeColor = finalDecision === "approved" ? "var(--good)" : finalDecision === "rejected" ? "var(--bad)" : "var(--warn)";
    const badgeBg   = finalDecision === "approved" ? "#e5f6ee"      : finalDecision === "rejected" ? "#fde8e8"      : "#fff4d6";
    const badgeIcon = finalDecision === "approved" ? "✓" : finalDecision === "rejected" ? "✗" : "ℹ";

    container.innerHTML = `
      <h3>Human Reviewer Decision</h3>
      <div style="display:flex;align-items:center;gap:14px;margin:14px 0;">
        <span role="status" aria-label="Final determination: ${escapeHtml(finalDecision)}"
              style="display:inline-flex;align-items:center;gap:8px;background:${badgeBg};color:${badgeColor};border:2px solid ${badgeColor};border-radius:10px;padding:12px 20px;font-weight:800;font-size:16px;">
          ${badgeIcon} ${escapeHtml(finalDecision.toUpperCase())}
        </span>
        <span class="muted" style="font-size:13px;">This application has been finalized. Further editing and reassessment are disabled.</span>
      </div>
      ${isOverride ? `<p class="muted" style="font-size:12px;margin-top:6px;">⚠ Model override — reason: <em>${escapeHtml(finalReview.override_reason || "")}</em></p>` : ""}
    `;
    return;
  }

  if (!hasPrediction) {
    container.innerHTML = `
      <h3>Human Reviewer Decision</h3>
      <div class="message" style="background:#f8fafc;color:var(--muted);border:1px solid var(--line);border-radius:10px;padding:14px;">
        <strong>Review Actions Disabled</strong>
        <p class="muted" style="margin:4px 0 0;">
          Awaiting Analyst assessment. A saved model prediction is required before submitting a final decision.
        </p>
      </div>
    `;
    return;
  }

  // Active review form with improved decision highlighting
  container.innerHTML = `
    <h3>Human Reviewer Decision</h3>
    <p class="muted">Select a final determination for this application. If your determination contradicts the model's recommendation, an override reason is mandatory.</p>

    <div style="display:flex;gap:12px;margin:16px 0;" role="group" aria-label="Decision selection">
      <button type="button" id="btnDecisionApprove"
              class="decision-btn decision-btn--approve"
              aria-pressed="false"
              style="flex:1;padding:14px;font-size:15px;cursor:pointer;border-radius:9px;border:2px solid #9bcac4;background:#fff;color:var(--accent);font-weight:700;transition:all .15s;">
        <span class="decision-icon" aria-hidden="true">○</span>
        <span class="decision-label"> Approve Application</span>
      </button>
      <button type="button" id="btnDecisionReject"
              class="decision-btn decision-btn--reject"
              aria-pressed="false"
              style="flex:1;padding:14px;font-size:15px;cursor:pointer;border-radius:9px;border:2px solid #9bcac4;background:#fff;color:var(--accent);font-weight:700;transition:all .15s;">
        <span class="decision-icon" aria-hidden="true">○</span>
        <span class="decision-label"> Reject Application</span>
      </button>
    </div>

    <div id="decisionOverrideBox" class="hidden" style="background:#fff8e6;border:1px solid #ffd591;border-radius:9px;padding:14px;margin:16px 0;">
      <label style="display:block;font-weight:700;color:#b45309;margin-bottom:6px;font-size:13px;">
        ⚠ Model Override Reason (Required)*
      </label>
      <p class="muted" id="overrideExplanationText" style="font-size:12px;margin-bottom:8px;color:#92400e;"></p>
      <textarea id="reviewOverrideReason" rows="3" placeholder="Explain why your decision differs from the ML recommendation..." style="width:100%;border:1px solid #ffd591;border-radius:8px;padding:10px;font-family:inherit;font-size:13px;box-sizing:border-box;"></textarea>
    </div>

    <div style="margin:16px 0;">
      <label style="display:block;font-weight:700;color:var(--muted);margin-bottom:6px;font-size:13px;">
        Review Notes (Optional)
      </label>
      <textarea id="reviewNotesInput" rows="2" placeholder="Optional notes for audit trail..." style="width:100%;border:1px solid var(--line);border-radius:8px;padding:10px;font-family:inherit;font-size:13px;box-sizing:border-box;"></textarea>
    </div>

    <div style="display:flex;align-items:center;gap:14px;margin-top:18px;">
      <button type="button" id="btnSubmitReviewDecision" class="primary" style="width:auto;min-width:200px;margin-top:0;" disabled>
        Submit Final Review
      </button>
      <span id="decisionPromptNotice" class="muted" style="font-size:13px;">Please select Approve or Reject above.</span>
    </div>

    <p id="decisionActionFeedback" class="message hidden" style="margin-top:14px;"></p>
  `;

  let selectedDecision = null;

  const btnApprove = container.querySelector("#btnDecisionApprove");
  const btnReject = container.querySelector("#btnDecisionReject");
  const overrideBox = container.querySelector("#decisionOverrideBox");
  const overrideText = container.querySelector("#overrideExplanationText");
  const overrideInput = container.querySelector("#reviewOverrideReason");
  const notesInput = container.querySelector("#reviewNotesInput");
  const submitBtn = container.querySelector("#btnSubmitReviewDecision");
  const promptNotice = container.querySelector("#decisionPromptNotice");
  const feedback = container.querySelector("#decisionActionFeedback");

  function resetButtonStyles() {
    // Neutral unselected state for both buttons
    for (const btn of [btnApprove, btnReject]) {
      btn.style.border = "2px solid #9bcac4";
      btn.style.background = "#fff";
      btn.style.color = "var(--accent)";
      btn.style.fontWeight = "700";
      btn.setAttribute("aria-pressed", "false");
    }
    btnApprove.querySelector(".decision-icon").textContent = "○";
    btnApprove.querySelector(".decision-label").textContent = " Approve Application";
    btnReject.querySelector(".decision-icon").textContent = "○";
    btnReject.querySelector(".decision-label").textContent = " Reject Application";
  }

  function updateDecisionState(decision) {
    if (isStale()) return;
    selectedDecision = decision;
    resetButtonStyles();

    if (decision === "approved") {
      btnApprove.style.border = "2px solid var(--good)";
      btnApprove.style.background = "#e5f6ee";
      btnApprove.style.color = "var(--good)";
      btnApprove.style.fontWeight = "800";
      btnApprove.setAttribute("aria-pressed", "true");
      btnApprove.querySelector(".decision-icon").textContent = "✓";
      btnApprove.querySelector(".decision-label").textContent = " Approve Selected";
    } else {
      btnReject.style.border = "2px solid var(--bad)";
      btnReject.style.background = "#fde8e8";
      btnReject.style.color = "var(--bad)";
      btnReject.style.fontWeight = "800";
      btnReject.setAttribute("aria-pressed", "true");
      btnReject.querySelector(".decision-icon").textContent = "✗";
      btnReject.querySelector(".decision-label").textContent = " Reject Selected";
    }

    const isOverride = isDecisionOverride(decision, latestPrediction.model_decision);

    if (isOverride) {
      overrideBox.classList.remove("hidden");
      overrideText.textContent = `You are choosing to ${decision.toUpperCase()}, which overrides the model recommendation ("${latestPrediction.model_decision}"). Provide your justification below.`;
      overrideInput.required = true;
    } else {
      overrideBox.classList.add("hidden");
      overrideText.textContent = "";
      overrideInput.required = false;
      overrideInput.value = "";
    }

    submitBtn.disabled = false;
    promptNotice.textContent = `Ready to submit decision: ${decision.toUpperCase()}${isOverride ? " (with Override)" : ""}.`;
    if (feedback) feedback.classList.add("hidden");
  }

  btnApprove.onclick = () => updateDecisionState("approved");
  btnReject.onclick = () => updateDecisionState("rejected");

  submitBtn.onclick = async () => {
    if (isStale() || !selectedDecision) return;

    const isOverride = isDecisionOverride(selectedDecision, latestPrediction.model_decision);
    const overrideReason = (overrideInput?.value || "").trim();
    const notes = (notesInput?.value || "").trim();

    if (isOverride && !overrideReason) {
      if (feedback) {
        feedback.textContent = "An override reason is required when overriding the model's recommendation.";
        feedback.className = "message";
        feedback.classList.remove("hidden");
      }
      overrideInput.focus();
      return;
    }

    // Confirmation requirement
    const confirmMessage =
      `Are you sure you want to finalize application ${application.application_id} as ${selectedDecision.toUpperCase()}?\n\n` +
      (isOverride ? `* Model Recommendation: ${latestPrediction.model_decision}\n* Override Justification: ${overrideReason}\n\n` : "") +
      `This decision is final. Approved or rejected applications cannot be reassessed.`;

    if (!await confirmPortal(confirmMessage, "Confirm final review", "Confirm final review") || isStale()) {
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting Review…";
    btnApprove.disabled = true;
    btnReject.disabled = true;

    if (feedback) feedback.classList.add("hidden");

    try {
      const payload = {
        prediction_id: latestPrediction.prediction_id,
        reviewer_decision: selectedDecision,
        review_notes: notes,
        override_reason: isOverride ? overrideReason : null,
      };

      await api.submitReview(application.application_id, payload);
      if (isStale()) return;

      if (feedback) {
        feedback.textContent = `Review finalized successfully. Application status is now "${selectedDecision}".`;
        feedback.className = "message";
        feedback.style.background = "#e6f4ea";
        feedback.style.color = "var(--good)";
        feedback.style.borderColor = "#a7f3d0";
        feedback.classList.remove("hidden");
      }

      if (onSubmitSuccess) {
        await onSubmitSuccess();
      }
    } catch (error) {
      if (isStale()) return;
      if (feedback) {
        feedback.textContent = `Failed to submit review: ${error.message}`;
        feedback.className = "message";
        feedback.style.background = "#fff0f0";
        feedback.style.color = "var(--bad)";
        feedback.style.borderColor = "#fecaca";
        feedback.classList.remove("hidden");
      }
      submitBtn.disabled = false;
      submitBtn.textContent = "Submit Final Review";
      btnApprove.disabled = false;
      btnReject.disabled = false;
    }
  };
}

/**
 * Submit a reviewer decision directly through the API.
 * @param {string} appId
 * @param {string} decision - "approved" or "rejected"
 * @param {string} [notes]
 * @param {string} [overrideReason]
 * @param {string} [predictionId]
 * @returns {Promise<Object>}
 */
export async function submitReviewDecision(appId, decision, notes = "", overrideReason = null, predictionId = null) {
  if (appId !== currentReviewerAppId) {
    throw new Error("Session mismatch");
  }

  const payload = {
    prediction_id: predictionId || null,
    reviewer_decision: decision,
    review_notes: notes || "",
    override_reason: overrideReason || null,
  };

  try {
    const response = await api.submitReview(appId, payload);
    currentReviewerAppId = null;
    return response;
  } catch (error) {
    console.error("Failed to submit review:", error);
    throw error;
  }
}
