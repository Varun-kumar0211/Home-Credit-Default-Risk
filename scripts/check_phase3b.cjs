// Run from the repository root: node --experimental-vm-modules scripts/check_phase3b.cjs
// Native ES linking and event-driven checks with isolated API/DOM substitutes.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve('Frontend/js');
function element() {
  const classes = new Set();
  const children = new Map();
  return { innerHTML: '', textContent: '', value: '', disabled: false, style: {}, attrs: {}, dataset: {},
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle(c, on) { on ? classes.add(c) : classes.delete(c); } },
    setAttribute(k, v) { this.attrs[k] = v; }, reset() {},
    querySelector(k) { if (!children.has(k)) children.set(k, element()); return children.get(k); },
    querySelectorAll() {
      return [...this.innerHTML.matchAll(/data-manage-assignment="([^"]+)"/g)].map(match => {
        const button = this.querySelector(match[1]);
        button.dataset.manageAssignment = match[1];
        return button;
      });
    }
  };
}
const nodes = new Map();
const getNode = id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
let confirmations = 0;
const context = vm.createContext({ console, Date, setTimeout,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  window: { confirm() { confirmations++; return true; }, dispatchEvent() {} },
  CustomEvent: class {}, document: { getElementById: getNode, querySelector: selector => getNode(selector.slice(1)) }
});
const modules = new Map();
for (const file of fs.readdirSync(root).filter(f => f.endsWith('.js'))) {
  const filename = path.join(root, file);
  modules.set(filename, new vm.SourceTextModule(fs.readFileSync(filename, 'utf8'), { context, identifier: filename }));
}
async function run() {
  await modules.get(path.join(root, 'main.js')).link((specifier, parent) => {
    const target = path.resolve(path.dirname(parent.identifier), specifier);
    assert(modules.has(target), `Missing module: ${specifier}`);
    return modules.get(target);
  });
  console.log(`PASS native ES import/export linking: ${modules.size} modules`);
  const apiModule = modules.get(path.join(root, 'api.js'));
  await apiModule.evaluate();
  const { api, setToken } = apiModule.namespace;
  const adminModule = modules.get(path.join(root, 'admin.js'));
  await adminModule.evaluate();
  const admin = adminModule.namespace;
  setToken('admin-session');
  const application = { application_id: 'application-1', owner: '<analyst>', assigned_reviewer: 'old',
    status: 'pending_review', updated_at: 'version-1', created_at: '2026-10-11', can_reassign: true };
  let assignedCalls = 0;
  const workloads = [{ username: 'old', pending: 1, finalized: 0, total_assigned: 1 },
    { username: 'new', pending: 0, finalized: 0, total_assigned: 0 }];
  api.getAdminAssignments = async (limit, offset) => {
    assignedCalls++; assert.equal(limit, 25); assert.equal(offset, 0);
    return { applications: [application], reviewer_workload: workloads, total: 1, limit, offset };
  };
  api.listAudit = async () => ({ audit: [{ event_type: 'assignment_changed', actor: 'admin', created_at: '2026-10-11',
    old_value: { assigned_reviewer: 'old' }, new_value: { assigned_reviewer: 'new', reason: '<balance>' } }] });
  let overviewCalls = 0;
  api.getAdminOverview = async () => { overviewCalls++; return { totals: { total: 1, approved: 0, rejected: 0, pending: 1,
    pending_review: 1, submitted: 0, needs_information: 0, awaiting_assessment: 0 }, reviewer_workload: workloads,
    unassigned_count: 0, recent_applications: [] }; };
  await admin.loadAdminAssignments();
  assert(getNode('adminAssignmentRows').innerHTML.includes('&lt;analyst&gt;'));
  assert.equal(getNode('adminAssignmentNext').disabled, true);
  await getNode('adminAssignmentRows').querySelector('application-1').onclick();
  assert(getNode('adminAssignmentReviewer').innerHTML.includes('new — 0 pending / 0 assigned'));
  assert(getNode('adminAssignmentHistory').innerHTML.includes('&lt;balance&gt;'));
  console.log('PASS assignment load, bounded pagination, workload options, persisted history and HTML escaping');
  let submissions = 0;
  api.reassignApplication = async (id, payload) => {
    submissions++; assert.equal(id, 'application-1'); assert.equal(payload.reviewer_username, 'new');
    assert.equal(payload.reason, 'Balance workload'); assert.equal(payload.expected_updated_at, 'version-1');
    application.assigned_reviewer = 'new'; application.updated_at = 'version-2';
    workloads[0].pending = 0; workloads[1].pending = 1;
  };
  const form = getNode('adminAssignmentForm');
  const event = { preventDefault() {} };
  getNode('adminAssignmentReviewer').value = '';
  getNode('adminAssignmentReason').value = ' ';
  await form.onsubmit(event); assert.equal(submissions, 0); assert.equal(confirmations, 0);
  getNode('adminAssignmentReviewer').value = 'new';
  await form.onsubmit(event); assert.equal(submissions, 0);
  getNode('adminAssignmentReason').value = '  Balance workload  ';
  await form.onsubmit(event);
  assert.equal(submissions, 1); assert.equal(confirmations, 1); assert.equal(assignedCalls, 2); assert.equal(overviewCalls, 1);
  assert(getNode('adminAssignmentFeedback').textContent.includes('Assignment updated to new'));
  assert(getNode('adminAssignmentWorkloads').innerHTML.includes('new'));
  console.log('PASS required fields, confirmation, versioned submission and list/workload/overview refresh');
  await getNode('adminAssignmentRows').querySelector('application-1').onclick();
  getNode('adminAssignmentReviewer').value = 'old';
  getNode('adminAssignmentReason').value = 'Another balance';
  api.reassignApplication = async () => { throw Error('Application changed. Refresh before reassigning.'); };
  await form.onsubmit(event);
  assert(getNode('adminAssignmentFeedback').textContent.includes('Application changed'));
  assert.equal(getNode('adminAssignmentSubmit').disabled, false);
  console.log('PASS stale-assignment error is visible and submission recovers');
  application.can_reassign = false;
  await admin.loadAdminAssignments();
  await getNode('adminAssignmentRows').querySelector('application-1').onclick();
  assert(getNode('adminAssignmentForm').classList.contains('hidden'));
  assert(!getNode('adminAssignmentUnavailable').classList.contains('hidden'));
  console.log('PASS finalized application exposes history with assignment form hidden');
  let resolvePage;
  api.getAdminAssignments = () => new Promise(resolve => { resolvePage = resolve; });
  const pending = admin.loadAdminAssignments();
  setToken('other-session'); admin.clearAdminState();
  resolvePage({ applications: [application], reviewer_workload: workloads, total: 1, limit: 25, offset: 0 });
  await pending;
  assert.equal(getNode('adminAssignmentRows').innerHTML, '');
  console.log('PASS stale assignment response cannot render after account switch');
  const reviewerModule = modules.get(path.join(root, 'reviewer.js'));
  await reviewerModule.evaluate();
  const reviewer = reviewerModule.namespace;
  for (const [decision, style, icon] of [['Auto Approve', 'approve', '✓'], ['Auto Decline', 'decline', '✗'],
    ['Manual Review Required', 'manual', '⚠'], ['<Unknown>', 'neutral', 'ℹ'], [null, 'neutral', 'ℹ']]) {
    const predictionContainer = element();
    reviewer.renderPredictionInfo(predictionContainer, [{ model_decision: decision, default_probability: .1234,
      risk_tier: 'Manual Review', model_id: 'model-1', prediction_id: 'prediction-1', confidence: .9 }]);
    assert(predictionContainer.innerHTML.includes(`model-recommendation--${style}`));
    assert(predictionContainer.innerHTML.includes(icon));
    assert(predictionContainer.innerHTML.includes('MODEL RECOMMENDATION'));
    assert(predictionContainer.innerHTML.includes('12.34%'));
    assert(predictionContainer.innerHTML.includes(decision === '<Unknown>' ? '&lt;Unknown&gt;' : decision || 'Not available'));
    const actions = element();
    reviewer.renderDecisionActions(actions, { application, predictions: [{ model_decision: decision }], reviews: [] });
    assert.equal((actions.innerHTML.match(/aria-pressed="false"/g) || []).length, 2);
    assert(actions.innerHTML.includes('Human Reviewer Decision'));
    const approve = actions.querySelector('#btnDecisionApprove'); const reject = actions.querySelector('#btnDecisionReject');
    approve.onclick(); assert.equal(approve.attrs['aria-pressed'], 'true'); assert.equal(reject.attrs['aria-pressed'], 'false');
    reject.onclick(); assert.equal(reject.attrs['aria-pressed'], 'true'); assert.equal(approve.attrs['aria-pressed'], 'false');
    assert.equal(approve.style.background, '#fff'); assert.equal(reject.style.background, '#fde8e8');
  }
  console.log('PASS all model badge variants, exact wording, metadata, independent human choices and selection highlighting');
  let rejectDetail;
  api.getApplication = () => new Promise((resolve, reject) => { rejectDetail = reject; });
  api.listPredictions = async () => ({ predictions: [] }); api.listReviews = async () => ({ reviews: [] });
  reviewer.setReviewerNavigator(() => {});
  const detail = reviewer.openReviewApplication('old-app');
  reviewer.clearReviewerState(); setToken('new-reviewer');
  getNode('reviewDetailAppInfo').textContent = 'New session';
  rejectDetail(Error('Old session failure')); await detail;
  assert.equal(getNode('reviewDetailAppInfo').textContent, 'New session');
  console.log('PASS stale reviewer detail error cannot render in a new session');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
