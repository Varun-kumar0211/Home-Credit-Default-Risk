// Native module and event-driven DOM/API checks; no third-party dependencies.
// Run: node --experimental-vm-modules scripts/check_phase3c2.cjs
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve('Frontend/js');
const html = fs.readFileSync('Frontend/index.html', 'utf8');
const nodes = new Map();
let focused = null;
function decode(value) { return value.replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&amp;', '&').replaceAll('&#39;', "'").replaceAll('&quot;', '"'); }
function node(id) {
  if (nodes.has(id)) return nodes.get(id);
  const classes = new Set();
  const children = new Map();
  const result = { id, innerHTML: '', textContent: '', value: '', disabled: false, dataset: {}, attrs: {}, style: {},
    options: ['', 'analyst', 'reviewer', 'admin'].map(value => ({ value, disabled: false })),
    classList: { add: (...values) => values.forEach(v => classes.add(v)), remove: (...values) => values.forEach(v => classes.delete(v)),
      contains: value => classes.has(value), toggle(value, enabled) { enabled ? classes.add(value) : classes.delete(value); } },
    setAttribute(key, value) { this.attrs[key] = value; }, focus() { focused = id; }, addEventListener() {},
    showModal() { this.open = true; confirmations++; if (autoConfirm) node(confirmationAllowed ? 'adminUserConfirmationAccept' : 'adminUserConfirmationCancel').onclick(); },
    close() { this.open = false; },
    reset() {
      const ids = id === 'adminAuditFilters' ? ['adminAuditEvent','adminAuditActor','adminAuditApplication','adminAuditFrom','adminAuditTo']
        : id === 'adminUserForm' ? ['adminUserRole', 'adminUserReason'] : [];
      ids.forEach(key => { node(key).value = ''; });
    },
    querySelectorAll(selector) {
      if (selector !== '[data-user-change]') return [];
      return [...this.innerHTML.matchAll(/data-user-change="(role|status)" data-username="([^"]+)"([^>]*)/g)].map(match => {
        const key = match[1] + ':' + decode(match[2]);
        if (!children.has(key)) children.set(key, node(id + ':' + key));
        const button = children.get(key);
        button.dataset = { userChange: match[1], username: decode(match[2]) };
        button.disabled = match[3].includes('disabled');
        return button;
      });
    }
  };
  nodes.set(id, result);
  return result;
}
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
assert.equal(new Set(ids).size, ids.length, 'Duplicate HTML IDs');
ids.forEach(node);
const pages = [...html.matchAll(/<section id="([^"]+)" class="page[^>]*data-role="([^"]+)"/g)].map(match => {
  const page = node(match[1]); page.dataset.role = match[2]; page.classList.add('hidden'); return page;
});
const nav = [...html.matchAll(/<button data-page="([^"]+)"/g)].map((match, index) => {
  const button = node('nav-' + index); button.dataset.page = match[1]; return button;
});
let confirmationAllowed = true, autoConfirm = true, confirmations = 0, fetches = [], expired = 0;
const listeners = new Map();
const storage = new Map();
const context = vm.createContext({ console, Date, URLSearchParams, setTimeout, FormData: class {},
  localStorage: { getItem: key => storage.get(key) || null, setItem: (key,value) => storage.set(key,value), removeItem: key => storage.delete(key) },
  window: { confirm() { confirmations++; return confirmationAllowed; }, addEventListener: (type,fn) => listeners.set(type,fn),
    dispatchEvent(event) { expired++; listeners.get(event.type)?.(); } }, CustomEvent: class { constructor(type) { this.type = type; } },
  document: { getElementById: node, querySelector: selector => node(selector.slice(1)),
    querySelectorAll: selector => selector === '[data-page]' ? nav : selector === '.page' ? pages : [] },
  fetch: async (url, options) => { fetches.push({ url, options }); return { status: 200, ok: true, json: async () => ({}) }; }
});
const modules = new Map();
for (const file of fs.readdirSync(root).filter(file => file.endsWith('.js'))) {
  const filename = path.join(root,file);
  modules.set(filename,new vm.SourceTextModule(fs.readFileSync(filename,'utf8'), { context, identifier: filename }));
}
const event = { preventDefault() {} };
const tick = () => new Promise(resolve => setImmediate(resolve));
async function run() {
  const main = modules.get(path.join(root,'main.js'));
  await main.link((specifier,parent) => modules.get(path.resolve(path.dirname(parent.identifier),specifier)));
  const apiModule = modules.get(path.join(root,'api.js')); await apiModule.evaluate();
  const { api, setToken, currentToken } = apiModule.namespace;
  const adminModule = modules.get(path.join(root,'admin.js')); await adminModule.evaluate();
  const admin = adminModule.namespace;
  console.log(`PASS syntax and native import/export linking: ${modules.size} modules; ${ids.length} unique HTML IDs`);
  setToken('admin-token');
  await api.getAdminUsers(25,25);
  await api.updateUserRole('name/with space', { role:'reviewer', reason:'Test', expected_version:7 });
  await api.updateUserStatus('name/with space', { is_active:false, reason:'Test', expected_version:8 });
  await api.getAdminAudit(25,25,{ actor:'a&b', event_type:'manual_review', date_from:'2026-10-11T00:00:00.000Z', password:'must-not-send' });
  assert(fetches[1].url.includes('name%2Fwith%20space/role'));
  assert.equal(JSON.parse(fetches[2].options.body).is_active,false);
  assert(fetches[3].url.includes('actor=a%26b') && !fetches[3].url.includes('password'));
  console.log('PASS actual API paths, encoded usernames, typed mutation bodies and allowlisted filter query');
  // Keep real API error handling to exercise stale-401 token isolation and numeric status.
  context.fetch = async () => ({ status:409, ok:false, json:async () => ({ detail:'Account changed' }) });
  await assert.rejects(api.getAdminUsers(), error => error.status === 409);
  let resolveFetch;
  context.fetch = () => new Promise(resolve => { resolveFetch = resolve; });
  const old401 = api.getAdminUsers(); setToken('new-session');
  resolveFetch({ status:401, ok:false, json:async () => ({ detail:'Old request' }) });
  await assert.rejects(old401); assert.equal(currentToken(),'new-session'); assert.equal(expired,0);
  console.log('PASS HTTP status propagation and old 401 cannot clear a new session');
  let accounts = [ { username:'admin',role:'admin',is_active:true,version:0,created_at:'2026-10-11T10:00:00Z' },
    { username:'<staff>',role:'analyst',is_active:true,version:7,created_at:'2026-10-11T10:00:00Z' } ];
  let userCalls = [], mutations = [];
  api.getAdminUsers = async (limit,offset) => { userCalls.push(offset); assert.equal(limit,25);
    return { users:offset ? [accounts[1]] : accounts, total:26,limit,offset }; };
  await admin.loadAdminUsers({ username:'admin' });
  assert(node('adminUsersRows').innerHTML.includes('&lt;staff&gt;'));
  const button = action => node('adminUsersRows').querySelectorAll('[data-user-change]').find(b => b.dataset.username === '<staff>' && b.dataset.userChange === action);
  const self = node('adminUsersRows').querySelectorAll('[data-user-change]').filter(b => b.dataset.username === 'admin');
  assert(self.every(b => b.disabled)); self[0].onclick(); assert(node('adminUserDetail').classList.contains('hidden'));
  await node('adminUsersNext').onclick(); assert.equal(userCalls.at(-1),25);
  await node('adminUsersPrevious').onclick(); assert.equal(userCalls.at(-1),0);
  console.log('PASS bounded user pagination, escaped identities and self-lockout UI prevention');
  api.updateUserRole = async (username,payload) => { mutations.push({ username,payload }); accounts[1] = { ...accounts[1],role:payload.role,version:8 }; };
  button('role').onclick();
  node('adminUserRole').value='reviewer'; node('adminUserReason').value=' ';
  await node('adminUserForm').onsubmit(event); assert.equal(mutations.length,0);
  node('adminUserReason').value='  Staff allocation  '; confirmationAllowed=false;
  await node('adminUserForm').onsubmit(event); assert.equal(mutations.length,0);
  confirmationAllowed=true; await node('adminUserForm').onsubmit(event);
  assert.equal(mutations.length,1); assert.equal(mutations[0].payload.expected_version,7);
  assert.equal(mutations[0].payload.reason,'Staff allocation');
  assert(node('adminUsersFeedback').textContent.includes('audit event recorded'));
  assert(node('adminUsersRows').innerHTML.includes('reviewer'));
  console.log('PASS required reason, cancellation, confirmed role mutation, expected_version and refresh');
  api.updateUserStatus = async (username,payload) => { mutations.push({ username,payload }); accounts[1]={ ...accounts[1],is_active:payload.is_active,version:accounts[1].version+1 }; };
  for (const active of [false,true]) {
    button('status').onclick(); node('adminUserReason').value='Status test';
    await node('adminUserForm').onsubmit(event);
    assert.equal(mutations.at(-1).payload.is_active,active);
  }
  api.updateUserRole = async () => { const error = Error('Account changed'); error.status=409; throw error; };
  button('role').onclick(); node('adminUserRole').value='analyst'; node('adminUserReason').value='Stale';
  await node('adminUserForm').onsubmit(event);
  assert(node('adminUsersFeedback').textContent.includes('Refresh staff'));
  assert(node('adminUserSubmit').disabled);
  await node('adminUsersRefresh').onclick(); assert(node('adminUserDetail').classList.contains('hidden'));
  console.log('PASS deactivate/reactivate and visible 409 requiring a fresh directory selection');
  let auditCalls=[];
  api.getAdminAudit = async (limit,offset,filters) => { auditCalls.push({ limit,offset,filters:JSON.parse(JSON.stringify(filters)) });
    return { events:[{ audit_id:'event-1',scope:'user',target_username:'<staff>',actor:'<admin>',event_type:'user_role_changed',created_at:'2026-10-11T10:00:00Z',
      old_value:{ role:'analyst', password_hash:'must-hide' },new_value:{ role:'reviewer', version:8, nested:{ token:'must-hide' } },reason:'<approved>' }], total:26,limit,offset }; };
  await admin.loadAdminAudit();
  assert(node('adminAuditRows').innerHTML.includes('&lt;admin&gt;'));
  assert(!node('adminAuditRows').innerHTML.includes('must-hide'));
  assert(node('adminAuditRows').innerHTML.includes('<details'));
  await node('adminAuditNext').onclick(); assert.equal(auditCalls.at(-1).offset,25);
  node('adminAuditEvent').value='user_role_changed';node('adminAuditActor').value='  admin  ';
  node('adminAuditApplication').value='app-1';node('adminAuditFrom').value='2026-10-11T00:00';node('adminAuditTo').value='2026-10-11T23:59:59';
  await node('adminAuditFilters').onsubmit(event);
  assert.equal(auditCalls.at(-1).offset,0);assert.equal(auditCalls.at(-1).filters.date_from,'2026-10-11T00:00:00.000Z');
  assert.equal(auditCalls.at(-1).filters.actor,'admin');assert.equal(auditCalls.at(-1).filters.application_id,'app-1');
  const before=auditCalls.length;node('adminAuditFrom').value='2099-01-01T00:00';
  await node('adminAuditFilters').onsubmit(event);assert.equal(auditCalls.length,before);
  assert(node('adminAuditFeedback').textContent.includes('must precede'));
  await node('adminAuditReset').onclick();assert.deepEqual(auditCalls.at(-1).filters,{});
  console.log('PASS read-only allowlisted audit detail, pagination, all filters, explicit UTC and invalid date range');
  autoConfirm=false;
  button('status').onclick();node('adminUserReason').value='Cancel on session change';
  const count=mutations.length;
  const pendingConfirm=node('adminUserForm').onsubmit(event);
  assert(node('adminUserConfirmation').open);
  admin.clearAdminState();setToken('confirmation-session-change');await pendingConfirm;
  assert(!node('adminUserConfirmation').open);assert.equal(mutations.length,count);
  autoConfirm=true;
  await admin.loadAdminUsers({username:'admin'});
  let resolveMutation;
  api.updateUserStatus=() => new Promise(resolve => {resolveMutation=resolve;});
  button('status').onclick();node('adminUserReason').value='In-flight change';
  const pendingMutation=node('adminUserForm').onsubmit(event);await tick();
  admin.clearAdminState();setToken('mutation-session-change');resolveMutation({});await pendingMutation;
  assert.equal(node('adminUsersRows').innerHTML,'');assert.equal(node('adminUsersFeedback').textContent,'');
  console.log('PASS confirmation closes on session reset; late mutation cannot refresh or announce in another session');
  for (const kind of ['users','audit']) {
    let resolvePage;
    if (kind==='users') api.getAdminUsers=() => new Promise(resolve => { resolvePage=resolve; });
    else api.getAdminAudit=() => new Promise(resolve => { resolvePage=resolve; });
    const pending=kind==='users' ? admin.loadAdminUsers({ username:'admin' }) : admin.loadAdminAudit();
    setToken('replacement-'+kind);admin.clearAdminState();
    resolvePage(kind==='users' ? {users:accounts,total:2,limit:25} : {events:[],total:0,limit:25});await pending;
    assert.equal(node(kind==='users' ? 'adminUsersRows':'adminAuditRows').innerHTML,'');
  }
  let rejectPage;api.getAdminUsers=() => new Promise((resolve,reject) => { rejectPage=reject; });
  const lateError=admin.loadAdminUsers({username:'admin'});admin.clearAdminState();setToken('other');
  node('adminUsersFeedback').textContent='New workspace';rejectPage(Error('Old error'));await lateError;
  assert.equal(node('adminUsersFeedback').textContent,'New workspace');
  console.log('PASS logout clears staff/audit/filter state; stale success and error cannot render in another session');
  api.getAdminUsers=async (limit,offset) => ({users:[],total:0,limit,offset});
  await admin.loadAdminUsers({username:'admin'});assert(node('adminUsersRows').innerHTML.includes('No staff'));
  api.getAdminAudit=async (limit,offset) => ({events:[],total:0,limit,offset});
  await admin.loadAdminAudit();assert(node('adminAuditRows').innerHTML.includes('No audit events'));
  api.getAdminAudit=async () => {throw Error('Offline test');};
  await admin.loadAdminAudit();assert(node('adminAuditFeedback').textContent.includes('Offline test'));
  assert.equal(node('adminAuditRefresh').disabled,false);assert.equal(node('adminAuditRows').attrs['aria-busy'],'false');
  console.log('PASS empty states, load failure feedback and explicit-refresh recovery');
  // Execute the actual main.js navigation wiring with DOM substitutes.
  setToken(null);await main.evaluate();
  let identity={username:'admin',role:'admin'}, overviewCalls=0, auditNavCalls=0, usersNavCalls=0;
  api.login=async () => ({access_token:'session-'+identity.role});api.me=async () => identity;
  api.getAdminOverview=async () => {overviewCalls++;return { totals:{total:0},reviewer_workload:[],unassigned_count:0,recent_applications:[] };};
  api.getAdminUsers=async (limit,offset) => {usersNavCalls++;return {users:accounts,total:2,limit,offset};};
  api.getAdminAudit=async (limit,offset) => {auditNavCalls++;return {events:[],total:0,limit,offset};};
  api.getAssignedApplications=async () => ({applications:[]});
  api.defaultData=async () => {throw Error('Dataset unavailable in isolated navigation test');};
  await node('loginForm').onsubmit(event);await tick();assert.equal(overviewCalls,1);assert.equal(usersNavCalls+auditNavCalls,0);
  const open = name => nav.find(button => button.dataset.page===name).onclick();
  open('adminUsersPage');await tick();assert.equal(usersNavCalls,1);assert.equal(auditNavCalls,0);
  open('adminAuditPage');await tick();assert.equal(auditNavCalls,1);
  for (const role of ['analyst','reviewer']) {
    node('logout').onclick();identity={username:role,role};await node('loginForm').onsubmit(event);await tick();
    const calls=usersNavCalls+auditNavCalls;open('adminUsersPage');open('adminAuditPage');await tick();
    assert.equal(usersNavCalls+auditNavCalls,calls);
    assert(node('adminUsersPage').classList.contains('hidden') && node('adminAuditPage').classList.contains('hidden'));
  }
  console.log('PASS actual page navigation fetches only opened Admin page; Analyst/Reviewer cannot open or fetch Admin pages');
}
run().catch(error => { console.error(error);process.exitCode=1; });
