import { api, del, download, get, patch, post } from '../api.js';
import { can, resetLookups, state } from '../state.js';
import { PALETTE, chip, clear, confirmDialog, empty, errorState, fail, fmtDate, fmtDateTime, formModal, h, label, listView, loading, status, table, toast } from '../ui.js';
import { RESOURCES, lvl, resourcePage } from '../resources.js';
import { tabbed } from './common.js';

const head = (title, text) => h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, title), h('p', {}, text)));
const page = (title, text, tabs, tab) => (root) => root.append(head(title, text), tabbed(tabs, tab));

export const risks = (root, _i, _s, tab) => root.append(head('Risk management', 'Scored register, impact-based heat matrix and treatment plans.'),
  tabbed([['register', 'Register', () => resourcePage(RESOURCES.risks, { embedded: true })], ['matrix', 'Risk matrix', riskMatrix], ['plans', 'Treatment plans', () => resourcePage(RESOURCES['treatment-plans'], { embedded: true })]], tab));

function riskMatrix() {
  const host = h('div', {}, loading());
  let view = 'inherent';
  async function load() {
    try {
      const d = await get('/api/risk-matrix', { view });
      const bg = (l, i) => { const s = l * i; return s >= 20 ? PALETTE.bad : s >= 12 ? '#C86B3C' : s >= 6 ? PALETTE.warn : PALETTE.ok; };
      const cells = [];
      for (let imp = 5; imp >= 1; imp--) {
        cells.push(h('div', { class: 'ax' }, imp));
        for (let l = 1; l <= 5; l++) { const rs = d.cells[`${l},${imp}`] || []; cells.push(h('div', { class: 'mc', style: { background: bg(l, imp), color: '#fff', opacity: rs.length ? 1 : 0.35 }, title: rs.map((r) => r.title).join('\n') }, rs.length ? [h('strong', {}, `${rs.length} risk${rs.length > 1 ? 's' : ''}`), ...rs.slice(0, 2).map((r) => h('div', {}, h('a', { href: '#/risks', style: { color: '#fff' } }, r.title.slice(0, 26)))), rs.length > 2 ? `+${rs.length - 2}` : null] : null)); }
      }
      cells.push(h('div', {}), ...[1, 2, 3, 4, 5].map((l) => h('div', { class: 'ax' }, l)));
      clear(host).append(h('div', { class: 'card' }, h('div', { class: 'row between' }, h('p', { class: 'muted', style: { margin: 0 } }, d.formula), h('div', { class: 'row' }, ['inherent', 'residual'].map((v) => h('button', { class: `btn sm ${v === view ? 'primary' : ''}`, onClick: () => { view = v; load(); } }, label(v))))),
        h('div', { style: { margin: '14px 0' } }, h('div', { class: 'small muted' }, 'Impact (vertical) vs likelihood (horizontal)'), h('div', { class: 'matrix', style: { maxWidth: '620px' } }, cells))));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  load(); return host;
}

export const assets = page('Assets & privacy', 'Asset inventory and the records of processing that reference them.', [['assets', 'Asset inventory', () => resourcePage(RESOURCES.assets, { embedded: true })], ['privacy', 'Privacy & data inventory', () => resourcePage(RESOURCES['privacy-records'], { embedded: true })]]);
export const vendors = (root) => root.append(resourcePage(RESOURCES.vendors));
export const tasks = (root) => root.append(resourcePage(RESOURCES.tasks));
export const audits = page('Audit & remediation', 'Plan audits, track findings, and drive corrective actions to verified closure.', [['audits', 'Audit planning', () => resourcePage(RESOURCES.audits, { embedded: true })], ['findings', 'Findings', () => resourcePage(RESOURCES.findings, { embedded: true })], ['caps', 'Corrective actions', () => resourcePage(RESOURCES['corrective-actions'], { embedded: true })]]);

// ---------------------------------------------------------------- workflow: approvals + exceptions
export function workflow(root, _i, _s, tab) {
  const approvals = () => {
    let l;
    l = listView({ endpoint: '/api/approvals', search: false, filters: [{ name: 'status', label: 'Status', options: ['pending', 'approved', 'rejected'] }], emptyTitle: 'No approval requests', emptyText: 'Policies, evidence and exceptions sent for approval appear here.',
      columns: [{ label: 'Type', render: (a) => chip(a.entity_type) }, { key: 'title', label: 'Item' }, { key: 'requested_name', label: 'Requested by' }, { label: 'Requested', render: (a) => fmtDate(a.created_at) }, { label: 'Status', render: (a) => status(a.status) }, { key: 'decided_name', label: 'Decided by' }],
      rowActions: (a, reload) => (a.status === 'pending' && can('approve') ? [h('button', { class: 'btn sm primary', onClick: () => decide(a, 'approved', reload) }, 'Approve'), h('button', { class: 'btn sm danger', onClick: () => decide(a, 'rejected', reload) }, 'Reject')] : []) });
    return l;
  };
  async function decide(a, decision, reload) {
    formModal({ title: `${label(decision).replace('ed', 'e')}: ${a.title}`, intro: 'Your decision and comment are written to the audit trail.', fields: [{ name: 'comment', label: 'Comment', type: 'textarea', full: true }], submitText: label(decision).replace('ed', 'e'),
      onSubmit: async (v) => { await post(`/api/approvals/${a.id}/decision`, { decision, comment: v.comment }); toast(`Request ${decision}`, 'ok'); reload(); } });
  }
  root.append(head('Approvals & exceptions', 'Maker-checker workflow: requesters cannot approve their own items when another approver exists.'),
    tabbed([['approvals', 'Approval queue', approvals], ['exceptions', 'Exceptions', () => resourcePage(RESOURCES.exceptions, { embedded: true })]], tab));
}

// ---------------------------------------------------------------- reports
export function reports(root, id, setCrumbs) {
  if (id) return reportView(root, id, setCrumbs);
  const host = h('div', {}, loading());
  const prompt = h('input', { class: 'input', placeholder: 'e.g. "Give me a risk summary" or "ISO 27001 status report"', maxlength: 500, 'aria-label': 'Report request', style: { flex: 1 } });
  async function load() {
    try {
      const d = await get('/api/reports');
      clear(host).append(d.items.length ? table([{ label: 'Report', render: (r) => h('a', { href: `#/reports/${r.id}` }, r.title) }, { label: 'Kind', render: (r) => chip(r.kind) }, { label: 'Request', render: (r) => r.params?.prompt || '-' }, { label: 'AI summary', render: (r) => (r.ai_assisted ? chip('yes', 'info') : 'no') }, { label: 'Generated', render: (r) => fmtDateTime(r.created_at) }], d.items)
        : empty('No reports yet', 'Describe what you need above, or pick a standard report.'));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  const gen = async (body) => { try { const r = await post('/api/reports/generate', body); toast('Report generated', 'ok'); location.hash = `#/reports/${r.id}`; } catch (e) { fail(e); } };
  root.append(head('Reports', 'Natural-language report generation over live data. Reports are snapshots saved with the request that produced them.'),
    h('div', { class: 'card', style: { marginBottom: '16px' } }, h('div', { class: 'row' }, prompt, h('button', { class: 'btn primary', onClick: () => gen({ prompt: prompt.value }) }, 'Generate')),
      h('div', { class: 'row', style: { marginTop: '10px' } }, [['summary', 'Posture summary'], ['audit_readiness', 'Audit readiness'], ['risk_summary', 'Risk summary'], ['evidence_status', 'Evidence status']].map(([k, t]) => h('button', { class: 'btn sm', onClick: () => gen({ kind: k, prompt: t }) }, t)))), host);
  load();
}

async function reportView(root, id, setCrumbs) {
  const host = h('div', {}, loading()); root.append(host);
  try {
    const r = await get(`/api/reports/${id}`); setCrumbs(['Reports', r.title]);
    clear(host).append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, r.title), h('p', {}, `Generated ${fmtDateTime(r.created_at)}${r.ai_assisted ? ' - includes an AI-written summary' : ''}`)),
      h('div', { class: 'actions' }, h('button', { class: 'btn', onClick: () => download(`/api/reports/${id}/download`, `report-${id}.md`).catch(fail) }, 'Download .md'), h('button', { class: 'btn', onClick: () => window.print() }, 'Print'),
        can('manage') ? h('button', { class: 'btn danger', onClick: async () => { if (await confirmDialog('Delete this report?', { danger: true, confirmText: 'Delete' })) { await del(`/api/reports/${id}`); location.hash = '#/reports'; } } }, 'Delete') : null)), h('pre', { class: 'report' }, r.content));
  } catch (e) { clear(host).append(errorState(e)); }
}

// ---------------------------------------------------------------- audit trail
export function auditTrail(root) {
  if (!can('audit')) { root.append(head('Audit trail', ''), empty('Access restricted', 'Your role does not include audit trail access.')); return; }
  const verifyBox = h('div', {});
  root.append(head('Audit trail', 'Append-only record of who did what. Events are hash-chained so edits made through the application are detectable (tamper-evident, not tamper-proof).'),
    h('div', { class: 'card', style: { marginBottom: '16px' } }, h('div', { class: 'row between' }, h('strong', {}, 'Integrity check'), h('button', { class: 'btn', onClick: async () => { clear(verifyBox).append(loading()); try { const v = await get('/api/audit-trail/verify'); clear(verifyBox).append(h('div', { class: `callout ${v.valid ? '' : 'bad'}` }, v.valid ? `Hash chain verified across ${v.checked} events.` : `Chain broken at event #${v.broken_at_event_id} (${v.checked} events checked).`), h('p', { class: 'small muted' }, v.note)); } catch (e) { clear(verifyBox).append(errorState(e)); } } }, 'Verify hash chain')), verifyBox),
    listView({ endpoint: '/api/audit-trail', pageSize: 40, emptyTitle: 'No events yet', columns: [{ label: 'When', render: (e) => fmtDateTime(e.created_at) }, { key: 'actor_label', label: 'Actor' }, { label: 'Action', render: (e) => chip(e.action) }, { label: 'Entity', render: (e) => (e.entity_type ? `${e.entity_type}${e.entity_id ? ' #' + e.entity_id : ''}` : '-') }, { key: 'detail', label: 'Detail' }, { label: 'IP', render: (e) => h('span', { class: 'mono' }, e.ip || '-') }] }));
}

// ---------------------------------------------------------------- notifications
export function notificationsPage(root) {
  const host = h('div', {}, loading()); root.append(head('Notifications', 'Alerts from your configured rules and workflow events.'), host);
  get('/api/notifications', { limit: 100 }).then((d) => clear(host).append(h('div', { class: 'card' }, d.items.length ? h('ul', { class: 'clean' }, d.items.map((n) => h('li', {}, h('div', {}, h('strong', {}, n.title), ' ', n.read ? null : chip('new', 'info')), h('div', {}, n.body), h('div', { class: 'small muted' }, fmtDateTime(n.created_at))))) : empty('No notifications', 'Configure alert rules in Settings, then run them.')))).catch((e) => clear(host).append(errorState(e)));
}

// ---------------------------------------------------------------- settings
export function settings(root, _i, _s, tab) {
  const org = () => { const host = h('div', { class: 'card' }, loading()); get('/api/org').then((o) => clear(host).append(h('h3', {}, 'Organization'), h('dl', {}, h('dt', { class: 'muted' }, 'Name'), h('dd', {}, o.name), h('dt', { class: 'muted' }, 'Your role'), h('dd', {}, label(o.role)), h('dt', { class: 'muted' }, 'Data'), h('dd', {}, o.is_demo ? 'Demo organization with fictional sample data' : 'Production data')),
    can('admin') ? h('button', { class: 'btn', onClick: () => formModal({ title: 'Edit organization', fields: [{ name: 'name', label: 'Name', required: true, value: o.name }, { name: 'industry', label: 'Industry', value: o.industry }], onSubmit: async (v) => { await patch('/api/org', v); toast('Saved', 'ok'); location.reload(); } }) }, 'Edit') : null)); return host; };
  const members = () => {
    const host = h('div', {});
    const load = async () => {
      clear(host).append(loading());
      try {
        const m = await get('/api/members');
        clear(host).append(h('div', { class: 'row between', style: { marginBottom: '10px' } }, h('p', { class: 'muted', style: { margin: 0 } }, 'Roles: owner, admin, compliance manager, control owner, auditor (read + audit), viewer (read-only). Permissions are enforced on the server.'),
          can('admin') ? h('button', { class: 'btn primary', onClick: invite }, '+ Add member') : null), table([{ key: 'full_name', label: 'Name' }, { key: 'email', label: 'Email' }, { label: 'Role', render: (x) => chip(x.role) },
          { label: '', render: (x) => (can('admin') && x.user_id !== state.me.id ? h('div', { class: 'row' }, h('button', { class: 'btn sm', onClick: () => changeRole(x) }, 'Change role'), h('button', { class: 'btn sm danger', onClick: async () => { if (await confirmDialog(`Remove ${x.email} from this organization?`, { danger: true, confirmText: 'Remove' })) { try { await del(`/api/members/${x.id}`); resetLookups(); load(); } catch (e) { fail(e); } } } }, 'Remove')) : null) }], m));
      } catch (e) { clear(host).append(errorState(e, load)); }
    };
    const roles = ['owner', 'admin', 'compliance_manager', 'control_owner', 'auditor', 'viewer'].map((v) => ({ value: v, label: label(v) }));
    const invite = () => formModal({ title: 'Add member', intro: 'If the person already has an account they are added with the chosen role; otherwise an account is created with the temporary password you set (share it securely).',
      fields: [{ name: 'email', label: 'Email', type: 'email', required: true }, { name: 'full_name', label: 'Full name' }, { name: 'role', label: 'Role', type: 'select', required: true, options: roles, value: 'viewer' }, { name: 'temporary_password', label: 'Temporary password', type: 'password', hint: 'Only needed for new accounts. 10+ chars, mixed case, a digit.' }],
      onSubmit: async (v) => { await post('/api/members', v); resetLookups(); toast('Member added', 'ok'); load(); } });
    const changeRole = (x) => formModal({ title: `Change role: ${x.email}`, fields: [{ name: 'role', label: 'Role', type: 'select', required: true, options: roles, value: x.role }], onSubmit: async (v) => { await patch(`/api/members/${x.id}`, v); toast('Role updated', 'ok'); load(); } });
    load(); return host;
  };
  const alerts = () => h('div', {}, h('div', { class: 'row between', style: { marginBottom: '8px' } }, h('p', { class: 'muted', style: { margin: 0 } }, 'Alert rules raise notifications (once per day per rule). Metrics: ' + 'evidence_expiring_days, controls_without_evidence, risk_score_above, overdue_tasks, readiness_below, open_critical_findings.'),
    can('manage') ? h('button', { class: 'btn', onClick: async () => { try { const r = await post('/api/alerts/run'); toast(r.fired.length ? `${r.fired.length} alert(s) raised` : 'No alerts triggered', 'ok'); } catch (e) { fail(e); } } }, 'Evaluate now') : null),
    resourcePage({ path: 'alert-rules', singular: 'alert rule', title: '', perm: 'admin', subtitle: '', fields: [{ name: 'name', label: 'Name', required: true, full: true }, { name: 'metric', label: 'Metric', type: 'select', required: true, options: ['evidence_expiring_days', 'controls_without_evidence', 'risk_score_above', 'overdue_tasks', 'readiness_below', 'open_critical_findings'].map((v) => ({ value: v, label: label(v) })) },
      { name: 'threshold', label: 'Threshold', type: 'number', min: 0, maxValue: 1000, value: 30, hint: 'Days, count or score depending on the metric.' }, { name: 'enabled', label: 'Enabled', type: 'checkbox', value: true }],
      columns: [{ key: 'name', label: 'Rule' }, { label: 'Metric', render: (r) => label(r.metric) }, { key: 'threshold', label: 'Threshold' }, { label: 'Enabled', render: (r) => (r.enabled ? chip('on', 'ok') : chip('off')) }] }, { embedded: true }));
  const automation = () => h('div', {}, h('div', { class: 'row between', style: { marginBottom: '8px' } }, h('p', { class: 'muted', style: { margin: 0 } }, 'Automation rules create tasks or notifications from data conditions. Duplicate tasks are never created for the same record.'),
    can('manage') ? h('button', { class: 'btn', onClick: async () => { try { const r = await post('/api/automation/run'); toast(r.actions.length ? `${r.actions.length} action(s) taken` : 'Nothing to do', 'ok'); } catch (e) { fail(e); } } }, 'Run now') : null),
    resourcePage({ path: 'automation-rules', singular: 'automation rule', title: '', perm: 'admin', subtitle: '', fields: [{ name: 'name', label: 'Name', required: true, full: true }, { name: 'trigger', label: 'When', type: 'select', required: true, options: ['evidence_expired', 'risk_high', 'finding_critical_unowned', 'vendor_assessment_due', 'policy_review_overdue'].map((v) => ({ value: v, label: label(v) })) },
      { name: 'action', label: 'Then', type: 'select', required: true, options: [{ value: 'create_task', label: 'Create task' }, { value: 'notify', label: 'Send notification' }], value: 'create_task' }, { name: 'enabled', label: 'Enabled', type: 'checkbox', value: true }],
      columns: [{ key: 'name', label: 'Rule' }, { label: 'Trigger', render: (r) => label(r.trigger) }, { label: 'Action', render: (r) => label(r.action) }, { label: 'Enabled', render: (r) => (r.enabled ? chip('on', 'ok') : chip('off')) }, { label: 'Last run', render: (r) => fmtDateTime(r.last_run_at) }] }, { embedded: true }));
  const workspaces = () => resourcePage({ path: 'workspaces', singular: 'workspace', title: '', perm: 'manage', subtitle: 'Workspaces group assessments, e.g. by business unit or product.', fields: [{ name: 'name', label: 'Name', required: true, full: true }, { name: 'description', label: 'Description', type: 'textarea', full: true }],
    columns: [{ key: 'name', label: 'Workspace' }, { key: 'description', label: 'Description' }, { label: 'Created', render: (r) => fmtDate(r.created_at) }] }, { embedded: true });
  const account = () => h('div', { class: 'card' }, h('h3', {}, 'Your account'), h('p', {}, `${state.me.full_name} (${state.me.email})`), h('button', { class: 'btn', onClick: () => formModal({ title: 'Change password', intro: 'All other sessions are signed out.', fields: [{ name: 'current_password', label: 'Current password', type: 'password', required: true, full: true }, { name: 'new_password', label: 'New password', type: 'password', required: true, full: true }],
    onSubmit: async (v) => { const r = await api('/api/auth/change-password', { method: 'POST', body: v }); const { session } = await import('../api.js'); session.set(r.access_token); toast('Password changed', 'ok'); } }) }, 'Change password'));
  root.append(head('Settings', 'Organization, members, workspaces, alert and automation rules.'), tabbed([['org', 'Organization', org], ['members', 'Members & roles', members], ['workspaces', 'Workspaces', workspaces], ['alerts', 'Alert rules', alerts], ['automation', 'Automation', automation], ['account', 'Account', account]], tab));
}
