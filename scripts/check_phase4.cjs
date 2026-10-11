// Dependency-free native module and event-driven frontend verification.
// Run: node --experimental-vm-modules scripts/check_phase4.cjs
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync('Frontend/index.html', 'utf8'), root = path.resolve('Frontend/js');
const nodes = new Map(), storage = new Map(), listeners = new Map();
let autoConfirm = true, allowConfirm = true, focus = '', requests = [];
function node(id) {
  if (nodes.has(id)) return nodes.get(id);
  const classes = new Set(), children = new Map();
  const item = { id, innerHTML: '', textContent: '', value: '', disabled: false, dataset: {}, attrs: {}, style: {}, entries: [],
    classList: { add: (...values) => values.forEach(value => classes.add(value)), remove: (...values) => values.forEach(value => classes.delete(value)),
      contains: value => classes.has(value), toggle(value, enabled) { enabled ? classes.add(value) : classes.delete(value); } },
    setAttribute(key, value) { this.attrs[key] = value; }, addEventListener() {}, focus() { focus = id; }, reset() {},
    querySelector(selector) { return selector.startsWith('#') ? node(selector.slice(1)) : node(id + ':' + selector); },
    close() { this.open = false; }, showModal() { this.open = true; if (id === 'portalConfirmation' && autoConfirm) node(allowConfirm ? 'portalConfirmAccept' : 'portalConfirmCancel').onclick(); },
    querySelectorAll(selector) {
      const attribute = selector.slice(1, -1);
      const regex = new RegExp(attribute + '="([^"]+)"([^>]*)', 'g');
      return [...this.innerHTML.matchAll(regex)].map(match => {
        if (!children.has(match[1])) children.set(match[1], node(id + ':' + match[1]));
        const button = children.get(match[1]);
        button.dataset[attribute.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = match[1];
        button.disabled = match[2].includes('disabled'); return button;
      });
    }
  };
  nodes.set(id, item); return item;
}
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
assert.equal(ids.length, new Set(ids).size); ids.forEach(node);
const pages = [...html.matchAll(/<section id="([^"]+)" class="page[^>]*data-role="([^"]+)"/g)].map(match => {
  const page = node(match[1]); page.dataset.role = match[2]; page.classList.add('hidden'); return page;
});
const nav = [...html.matchAll(/<button(?: id="[^"]+")? data-page="([^"]+)"/g)].map((match, index) => {
  const button = node('nav-' + index); button.dataset.page = match[1]; return button;
});
function show(id) { pages.forEach(page => page.classList.toggle('hidden', page.id !== id)); }
const context = vm.createContext({ console, Date, URLSearchParams, setTimeout, Object, Number,
  FormData: class { constructor(form) { this.form = form; } entries() { return this.form.entries.values(); } },
  localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  document: { getElementById: node, querySelector: selector => node(selector.slice(1)), querySelectorAll: selector => selector === '.page' ? pages : selector === '[data-page]' ? nav : [] },
  window: { addEventListener: (type, fn) => listeners.set(type, fn), dispatchEvent: event => listeners.get(event.type)?.(), confirm: () => true },
  CustomEvent: class { constructor(type) { this.type = type; } },
  fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, json: async () => ({}) }; }
});
const modules = new Map();
for (const file of fs.readdirSync(root).filter(file => file.endsWith('.js'))) {
  const filename = path.join(root, file);
  modules.set(filename, new vm.SourceTextModule(fs.readFileSync(filename, 'utf8'), { context, identifier: filename }));
}
const tick = () => new Promise(resolve => setImmediate(resolve)), event = { preventDefault() {} };
async function run() {
  const main = modules.get(path.join(root, 'main.js'));
  await main.link((specifier, parent) => modules.get(path.resolve(path.dirname(parent.identifier), specifier)));
  for (const name of ['api', 'portal', 'views', 'reviewer']) await modules.get(path.join(root, name + '.js')).evaluate();
  const { api, setToken, setReadOnlyDemo } = modules.get(path.join(root, 'api.js')).namespace;
  const portal = modules.get(path.join(root, 'portal.js')).namespace, views = modules.get(path.join(root, 'views.js')).namespace;
  console.log(`PASS native linking: ${modules.size} modules; ${ids.length} unique HTML IDs`);
  setToken('applicant');
  await api.applicantApplications(25, 25); await api.applicantApplication('a/b'); await api.intake(25, 0);
  await api.claimIntake('a/b', { reviewer_username: 'reviewer', expected_updated_at: 'version' });
  assert(requests[0].url.includes('limit=25&offset=25')); assert(requests[1].url.endsWith('a%2Fb'));
  assert.deepEqual(JSON.parse(requests[3].options.body), { reviewer_username: 'reviewer', expected_updated_at: 'version' });
  console.log('PASS actual portal API paths, encoded IDs, pagination and claim version payload');
  setReadOnlyDemo(true);
  const beforeDemoGuard = requests.length;
  await assert.rejects(api.updateUserRole('target', { role: 'admin', reason: 'blocked', expected_version: 0 }), /read-only/);
  assert.equal(requests.length, beforeDemoGuard, 'Demo mutation reached the network');
  await api.demoLogin('reviewer');
  assert.equal(requests.at(-1).url, '/auth/demo/reviewer');
  setReadOnlyDemo(false); setToken('private-staff');
  await api.updateUserRole('target', { role: 'reviewer', reason: 'private-session', expected_version: 0 });
  assert.equal(requests.length, beforeDemoGuard + 2, 'Normal session retained demo restrictions');
  console.log('PASS read-only recruiter mutation guard, role switching and normal-session recovery');
  const input = { GENDER: 'M', QUALIFICATION: 'Higher education', FAMILY_STATUS: 'Single / not married', OCCUPATION: 'Core staff', CONTRACT_TYPE: 'Cash loans', TOTAL_INCOME: '50000', CREDIT_AMOUNT: '150000', ANNUAL_LOAN_PAYMENT: '12000', GOODS_PRICE: '150000', AGE: '30', YEARS_OF_EXPERIENCE: '5', CREDIT_SCORE: '710', CREDIT_HISTORY: '0' };
  let sent, opened;
  portal.setPortalNavigator(show, async id => { opened = id; });
  api.submitApplicant = async payload => { sent = payload; return { application_id: 'submitted' }; };
  api.applicantApplication = async id => ({ application_id: id, loan_amount: 150000, progress: 'Approved', status: 'approved', created_at: '2026-10-11T00:00:00+00:00', applicant_snapshot: { ...input, password_hash: 'SECRET', review_notes: 'SECRET' }, review_notes: 'SECRET', shap: 'SECRET' });
  node('applicantForm').entries = Object.entries({ ...input, APPROVE_THRESHOLD: '.99', assigned_reviewer: 'attacker' });
  show('applicantNewPage'); portal.setupApplicantForm(); await node('applicantForm').onsubmit(event);
  assert.equal(Object.keys(sent.applicant_snapshot).length, 13); assert.equal(sent.applicant_snapshot.CREDIT_AMOUNT, 150000);
  assert(!('APPROVE_THRESHOLD' in sent.applicant_snapshot)); assert(node('applicantDetail').innerHTML.includes('Approved')); assert(!node('applicantDetail').innerHTML.includes('SECRET'));
  console.log('PASS numeric 13-field submission and allowlisted Applicant status/detail rendering');
  const offsets = [];
  api.applicantApplications = async (limit, offset) => { offsets.push(offset); return { applications: [{ application_id: 'page-' + offset, loan_amount: 1, progress: '<script>x</script>' }], total: 26, limit, offset }; };
  show('applicantApplicationsPage'); await portal.loadApplicantApplications({ offset: 0 });
  assert(node('portalApplicantRows').innerHTML.includes('&lt;script&gt;')); assert.equal(node('applicantPrev').disabled, true);
  await node('applicantNext').onclick(); await tick(); assert.equal(offsets.at(-1), 25); assert.equal(node('applicantNext').disabled, true);
  let resolveList; api.applicantApplications = () => new Promise(resolve => { resolveList = resolve; });
  const late = portal.loadApplicantApplications(); portal.clearPortalState(); setToken('new-session'); resolveList({ applications: [{ application_id: 'OLD' }], total: 1 }); await late;
  assert.equal(node('portalApplicantRows').innerHTML, '');
  console.log('PASS Applicant pagination, escaping and late list suppression after session reset');
  setToken('analyst'); show('analystIntakePage');
  api.intake = async (limit, offset) => ({ applications: [{ application_id: 'claim-id', owner: '<img>', updated_at: 'v1' }], total: 26, limit, offset });
  api.reviewers = async () => ({ reviewers: ['reviewer'] });
  let claims = 0; api.claimIntake = async (id, payload) => { claims++; assert.equal(payload.expected_updated_at, 'v1'); assert.equal(payload.reviewer_username, 'reviewer'); };
  await portal.loadIntake({ offset: 0 });
  let claimButton = node('intakeRows').querySelectorAll('[data-intake-claim]')[0];
  await claimButton.onclick(); assert.equal(claims, 0); assert.equal(focus, 'intakeReviewer');
  node('intakeReviewer').value = 'reviewer'; allowConfirm = false; await claimButton.onclick(); assert.equal(claims, 0);
  allowConfirm = true; await claimButton.onclick(); assert.equal(claims, 1); assert.equal(opened, 'claim-id');
  api.claimIntake = async () => { const error = Error('Already claimed'); error.status = 409; throw error; };
  await portal.loadIntake(); claimButton = node('intakeRows').querySelectorAll('[data-intake-claim]')[0]; node('intakeReviewer').value = 'reviewer'; await claimButton.onclick();
  assert(node('intakeFeedback').textContent.includes('Refresh intake')); assert.equal(claimButton.disabled, true);
  console.log('PASS required Reviewer, cancelled/confirmed claims, captured versions, open workflow and visible 409');
  autoConfirm = false; await portal.loadIntake(); claimButton = node('intakeRows').querySelectorAll('[data-intake-claim]')[0]; node('intakeReviewer').value = 'reviewer';
  const confirmation = claimButton.onclick(); await tick(); assert(node('portalConfirmation').open); portal.clearPortalState(); setToken('other'); await confirmation;
  assert.equal(node('portalConfirmation').open, false); assert.equal(node('portalConfirmationText').textContent, '');
  console.log('PASS logout cancels confirmation and clears portal financial and pagination state');
  autoConfirm = true;
  setToken('analyst'); let predictionRequests = 0, listRequests = 0;
  api.listApplications = async (limit, offset) => { listRequests++; assert.equal(limit, 25); return { applications: [{ application_id: 'legacy', status: 'approved', prediction_count: 2 }], total: 30, limit, offset }; };
  api.listPredictions = async () => { predictionRequests++; return { predictions: [] }; };
  views.resetDatasetCache(); await views.loadLoanApplications(); assert.equal(predictionRequests, 0);
  await node('loanNext').onclick(); await tick(); assert.equal(listRequests, 2);
  api.getApplication = async id => ({ application_id: id, source_type: 'manual', status: 'approved' });
  await views.openApplication('legacy'); assert(node('assessApplicationButton').disabled);
  let resolveDefault; api.defaultData = () => new Promise(resolve => { resolveDefault = resolve; });
  const oldDefault = views.loadDefault(); views.resetDatasetCache(); setToken('applicant-new'); resolveDefault({}); await oldDefault;
  assert.equal(node('metrics').textContent, '');
  console.log('PASS bounded Analyst list without per-row prediction calls, terminal UI and stale dataset suppression');
  const reviewer = modules.get(path.join(root, 'reviewer.js')).namespace;
  let reviewPayload, reviewsSaved = 0;
  api.submitReview = async (id, payload) => { reviewPayload = payload; reviewsSaved++; };
  const renderReview = () => reviewer.renderDecisionActions(node('reviewDecisionActions'), {
    application: { application_id: 'review-case', status: 'pending_review' },
    predictions: [{ prediction_id: 'prediction', model_decision: 'Auto Approve' }], reviews: [],
  });
  renderReview(); node('btnDecisionApprove').onclick();
  allowConfirm = false; await node('btnSubmitReviewDecision').onclick(); assert.equal(reviewsSaved, 0);
  allowConfirm = true; await node('btnSubmitReviewDecision').onclick();
  assert.equal(reviewsSaved, 1); assert.equal(reviewPayload.reviewer_decision, 'approved');
  assert.equal(node('portalConfirmationTitle').textContent, 'Confirm final review');
  renderReview(); node('btnDecisionApprove').onclick(); autoConfirm = false;
  const delayedReview = node('btnSubmitReviewDecision').onclick(); await tick();
  reviewer.clearReviewerState(); portal.clearPortalState(); setToken('next-reviewer'); await delayedReview;
  assert.equal(reviewsSaved, 1); autoConfirm = true;
  console.log('PASS accessible Reviewer confirmation, cancellation, final payload and logout before confirmation');
  let reviewerOffset; api.getPendingReviews = async (limit, offset) => { reviewerOffset = offset; return { applications: [], total: 30, limit, offset }; };
  await reviewer.loadPendingReviews({ offset: 0 }); node('reviewerPendingNext').onclick(); await tick(); assert.equal(reviewerOffset, 25);
  console.log('PASS Reviewer server pagination and pager boundaries');
  const adminModule = modules.get(path.join(root, 'admin.js')); await adminModule.evaluate();
  api.getAdminAudit = async () => ({ events: [{ event_type: 'analyst_claimed', application_id: 'claim-id',
    actor: 'analyst', new_value: { assigned_analyst: 'named-analyst', assigned_reviewer: 'named-reviewer', applicant_snapshot: 'SECRET' } }], total: 1, limit: 25 });
  await adminModule.namespace.loadAdminAudit();
  assert(node('adminAuditRows').innerHTML.includes('named-analyst')); assert(!node('adminAuditRows').innerHTML.includes('SECRET'));
  console.log('PASS Admin claim audit includes only sanitized assignment fields');
  setToken(null); await main.evaluate();
  let identity = { username: 'public', role: 'applicant' }, staffCalls = 0;
  api.register = async () => ({ access_token: 'registered' }); api.login = async () => ({ access_token: 'login-' + identity.role }); api.me = async () => identity;
  api.applicantApplications = async () => ({ applications: [], total: 0, limit: 25, offset: 0 });
  api.getAdminOverview = api.getAssignedApplications = api.defaultData = async () => { staffCalls++; return {}; };
  node('registerPassword').value = node('registerConfirm').value = 'fictional'; await node('registerForm').onsubmit(event); await tick();
  assert(!node('applicantApplicationsPage').classList.contains('hidden')); assert.equal(staffCalls, 0);
  for (const id of ['adminOversightPage', 'loanApplicationsPage', 'reviewerAssignedPage']) nav.find(button => button.dataset.page === id).onclick();
  await tick(); assert.equal(staffCalls, 0);
  nav.find(button => button.dataset.page === 'applicantNewPage').onclick(); assert(!node('applicantNewPage').classList.contains('hidden'));
  node('logout').onclick(); identity = { username: 'analyst', role: 'analyst' };
  let finishDataset; api.defaultData = () => new Promise(resolve => { finishDataset = resolve; });
  const staffLogin = node('loginForm').onsubmit(event); await tick();
  nav.find(button => button.dataset.page === 'analystIntakePage').onclick(); await tick();
  finishDataset({}); await staffLogin;
  assert(!node('analystIntakePage').classList.contains('hidden'), 'Late startup data replaced Intake');
  console.log('PASS delayed Analyst startup dataset cannot replace a newer Intake page');
  node('logout').onclick(); identity = { username: 'unknown', role: 'invalid' }; await node('loginForm').onsubmit(event); assert(node('workspace').classList.contains('hidden')); assert.equal(staffCalls, 0);
  let resolveLogin; api.login = () => new Promise(resolve => { resolveLogin = resolve; });
  const pendingLogin = node('loginForm').onsubmit(event); node('logout').onclick(); resolveLogin({ access_token: 'outdated-login' }); await pendingLogin;
  assert(node('workspace').classList.contains('hidden')); assert.equal(storage.get('credit_token'), undefined);
  console.log('PASS actual Applicant registration/navigation, staff isolation, unknown role rejection and stale login guard');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
