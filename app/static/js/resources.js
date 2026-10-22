// Config-driven CRUD pages for the many record types that share the same list/create/edit/delete behaviour.
import { del, get, patch, post, put } from './api.js';
import { can, controlOptions, optionsOf, userOptions } from './state.js';
import { chip, confirmDialog, empty, fail, fmtDate, formModal, h, label, listView, modal, status, toast } from './ui.js';

const opts = (arr) => arr.map((v) => ({ value: v, label: label(v) }));
const num = (lo, hi, extra = {}) => ({ type: 'number', min: lo, maxValue: hi, ...extra });
const owner = { name: 'owner_id', label: 'Owner', type: 'select', options: userOptions };

export const lvl = (n) => (n >= 20 ? 'critical' : n >= 12 ? 'high' : n >= 6 ? 'medium' : 'low');

export const RESOURCES = {
  assets: {
    path: 'assets', singular: 'asset', title: 'Assets', perm: 'manage', subtitle: 'Systems, applications, data stores and devices in scope. Risks and privacy records link here.',
    fields: [{ name: 'name', label: 'Name', required: true, full: true }, { name: 'asset_type', label: 'Type', type: 'select', required: true, options: opts(['application', 'infrastructure', 'data_store', 'device', 'service', 'ai_system', 'other']), value: 'application' },
      { name: 'criticality', label: 'Criticality (1-5)', ...num(1, 5), value: 3 }, { name: 'data_classification', label: 'Data classification', type: 'select', required: true, options: opts(['public', 'internal', 'confidential', 'restricted']), value: 'internal' }, owner,
      { name: 'description', label: 'Description', type: 'textarea', full: true }],
    columns: [{ key: 'name', label: 'Asset', sortKey: 'name' }, { label: 'Type', render: (r) => label(r.asset_type) }, { label: 'Criticality', render: (r) => `${r.criticality}/5` }, { label: 'Classification', render: (r) => chip(r.data_classification) }, { key: 'owner_name', label: 'Owner' }],
    filters: [{ name: 'asset_type', label: 'Type', options: ['application', 'infrastructure', 'data_store', 'device', 'service', 'ai_system', 'other'] }, { name: 'data_classification', label: 'Classification', options: ['public', 'internal', 'confidential', 'restricted'] }],
  },
  vendors: {
    path: 'vendors', singular: 'vendor', title: 'Vendors & third parties', perm: 'manage', subtitle: 'Third-party inventory with criticality, data access and assessment cadence.',
    fields: [{ name: 'name', label: 'Vendor', required: true }, { name: 'service', label: 'Service provided' }, { name: 'criticality', label: 'Criticality (1-5)', ...num(1, 5), value: 3 },
      { name: 'data_access', label: 'Data access', type: 'select', required: true, options: opts(['none', 'internal', 'personal', 'sensitive']), value: 'none' }, { name: 'risk_tier', label: 'Risk tier', type: 'select', required: true, options: opts(['low', 'medium', 'high', 'critical']), value: 'medium' },
      { name: 'status', label: 'Status', type: 'select', required: true, options: opts(['onboarding', 'active', 'offboarding', 'terminated']), value: 'active' }, owner,
      { name: 'last_assessed', label: 'Last assessed', type: 'date' }, { name: 'next_assessment', label: 'Next assessment', type: 'date' }, { name: 'contract_end', label: 'Contract end', type: 'date' },
      { name: 'assurance_notes', label: 'Assurance notes', type: 'textarea', full: true, hint: 'e.g. which attestations or reports you have received and reviewed (organisation-entered).' }],
    columns: [{ key: 'name', label: 'Vendor', sortKey: 'name' }, { key: 'service', label: 'Service' }, { label: 'Tier', render: (r) => status(r.risk_tier) }, { label: 'Data access', render: (r) => label(r.data_access) },
      { label: 'Next assessment', sortKey: 'next_assessment', render: (r) => fmtDate(r.next_assessment) }, { label: 'Status', render: (r) => status(r.status) }],
    filters: [{ name: 'risk_tier', label: 'Tier', options: ['low', 'medium', 'high', 'critical'] }, { name: 'status', label: 'Status', options: ['onboarding', 'active', 'offboarding', 'terminated'] }],
  },
  'privacy-records': {
    path: 'privacy-records', singular: 'processing record', title: 'Privacy & data inventory', perm: 'manage', subtitle: 'Records of processing: purpose, data categories, lawful basis, retention and transfers.',
    fields: [{ name: 'name', label: 'Processing activity', required: true, full: true }, { name: 'purpose', label: 'Purpose', type: 'textarea', full: true }, { name: 'data_categories', label: 'Data categories' }, { name: 'data_subjects', label: 'Data subjects' },
      { name: 'lawful_basis', label: 'Lawful basis / ground' }, { name: 'retention_period', label: 'Retention period' }, { name: 'asset_id', label: 'System / asset', type: 'select', options: optionsOf('/api/assets', 'name') }, { name: 'vendor_id', label: 'Processor / vendor', type: 'select', options: optionsOf('/api/vendors', 'name') },
      owner, { name: 'dpia_status', label: 'DPIA status', type: 'select', required: true, options: opts(['not_started', 'in_progress', 'completed', 'not_required']), value: 'not_started' },
      { name: 'cross_border', label: 'Cross-border transfer', type: 'checkbox' }, { name: 'dpia_required', label: 'DPIA required', type: 'checkbox' }],
    columns: [{ key: 'name', label: 'Activity', sortKey: 'name' }, { key: 'data_categories', label: 'Data' }, { key: 'lawful_basis', label: 'Lawful basis' }, { label: 'Cross-border', render: (r) => (r.cross_border ? chip('yes', 'warn') : 'No') },
      { label: 'DPIA', render: (r) => (r.dpia_required ? status(r.dpia_status) : '-') }],
    filters: [{ name: 'dpia_status', label: 'DPIA', options: ['not_started', 'in_progress', 'completed', 'not_required'] }],
  },
  risks: {
    path: 'risks', singular: 'risk', title: 'Risk register', perm: 'manage', subtitle: 'Score = likelihood x impact (1-5 each). Low <6, medium 6-11, high 12-19, critical 20+.',
    fields: [{ name: 'title', label: 'Risk', required: true, full: true }, { name: 'description', label: 'Description', type: 'textarea', full: true }, { name: 'category', label: 'Category', value: 'security' },
      { name: 'asset_id', label: 'Asset', type: 'select', options: optionsOf('/api/assets', 'name') }, { name: 'vendor_id', label: 'Vendor', type: 'select', options: optionsOf('/api/vendors', 'name') }, owner,
      { name: 'likelihood', label: 'Inherent likelihood (1-5)', ...num(1, 5), value: 3 }, { name: 'impact', label: 'Inherent impact (1-5)', ...num(1, 5), value: 3 },
      { name: 'residual_likelihood', label: 'Residual likelihood', ...num(1, 5) }, { name: 'residual_impact', label: 'Residual impact', ...num(1, 5) },
      { name: 'treatment', label: 'Treatment', type: 'select', required: true, options: opts(['mitigate', 'accept', 'transfer', 'avoid']), value: 'mitigate', hint: 'Accepting a risk requires a reviewer role.' },
      { name: 'status', label: 'Status', type: 'select', required: true, options: opts(['open', 'treating', 'accepted', 'closed']), value: 'open' }],
    columns: [{ key: 'title', label: 'Risk', sortKey: 'title' }, { key: 'category', label: 'Category' }, { label: 'Inherent', render: (r) => h('span', {}, `${r.inherent_score} `, status(r.inherent_level)) },
      { label: 'Residual', render: (r) => (r.residual_score ? h('span', {}, `${r.residual_score} `, status(r.residual_level)) : '-') }, { label: 'Treatment', render: (r) => label(r.treatment) }, { label: 'Plans', render: (r) => r.treatment_plans }, { label: 'Status', render: (r) => status(r.status) }, { key: 'owner_name', label: 'Owner' }],
    filters: [{ name: 'status', label: 'Status', options: ['open', 'treating', 'accepted', 'closed'] }, { name: 'treatment', label: 'Treatment', options: ['mitigate', 'accept', 'transfer', 'avoid'] }],
    extraActions: (r, reload) => [h('button', { class: 'btn sm', onClick: () => linkControls('risks', r, reload) }, `Controls (${r.control_ids.length})`)],
  },
  'treatment-plans': {
    path: 'treatment-plans', singular: 'treatment plan', title: 'Treatment plans', perm: 'contribute', subtitle: 'Actions that reduce, transfer, avoid or accept each risk.',
    fields: [{ name: 'risk_id', label: 'Risk', type: 'select', required: true, options: optionsOf('/api/risks', 'title'), full: true }, { name: 'action', label: 'Action', required: true, full: true }, { name: 'description', label: 'Details', type: 'textarea', full: true }, owner,
      { name: 'due_date', label: 'Due date', type: 'date' }, { name: 'status', label: 'Status', type: 'select', required: true, options: opts(['planned', 'in_progress', 'done', 'cancelled']), value: 'planned' }],
    columns: [{ key: 'action', label: 'Action' }, { key: 'risk_title', label: 'Risk' }, { key: 'owner_name', label: 'Owner' }, { label: 'Due', sortKey: 'due_date', render: (r) => fmtDate(r.due_date) }, { label: 'Status', render: (r) => status(r.status) }],
    filters: [{ name: 'status', label: 'Status', options: ['planned', 'in_progress', 'done', 'cancelled'] }],
  },
  audits: {
    path: 'audits', singular: 'audit', title: 'Audit planning', perm: 'audit', subtitle: 'Plan internal, external and readiness audits; findings attach to an engagement.',
    fields: [{ name: 'name', label: 'Audit name', required: true, full: true }, { name: 'framework_id', label: 'Framework', type: 'select', options: async () => (await get('/api/frameworks')).items.map((f) => ({ value: f.id, label: f.name })) },
      { name: 'audit_type', label: 'Type', type: 'select', required: true, options: opts(['internal', 'external', 'readiness']), value: 'internal' }, { name: 'scope', label: 'Scope', type: 'textarea', full: true },
      { name: 'lead_id', label: 'Audit lead', type: 'select', options: userOptions }, { name: 'status', label: 'Status', type: 'select', required: true, options: opts(['planned', 'fieldwork', 'reporting', 'closed']), value: 'planned' },
      { name: 'start_date', label: 'Start', type: 'date' }, { name: 'end_date', label: 'End', type: 'date' }],
    columns: [{ key: 'name', label: 'Audit', sortKey: 'name' }, { label: 'Type', render: (r) => label(r.audit_type) }, { key: 'lead_name', label: 'Lead' }, { label: 'Start', render: (r) => fmtDate(r.start_date) }, { label: 'End', render: (r) => fmtDate(r.end_date) }, { label: 'Status', render: (r) => status(r.status) }],
    filters: [{ name: 'status', label: 'Status', options: ['planned', 'fieldwork', 'reporting', 'closed'] }],
  },
  findings: {
    path: 'findings', singular: 'finding', title: 'Findings & remediation', perm: 'contribute', subtitle: 'A finding cannot be resolved until its corrective actions are complete.',
    fields: [{ name: 'title', label: 'Finding', required: true, full: true }, { name: 'description', label: 'Description', type: 'textarea', full: true }, { name: 'audit_id', label: 'Audit', type: 'select', options: optionsOf('/api/audits', 'name') },
      { name: 'control_id', label: 'Control', type: 'select', options: controlOptions }, { name: 'severity', label: 'Severity', type: 'select', required: true, options: opts(['low', 'medium', 'high', 'critical']), value: 'medium' },
      { name: 'status', label: 'Status', type: 'select', required: true, options: opts(['open', 'in_remediation', 'resolved', 'closed']), value: 'open' }, owner, { name: 'due_date', label: 'Due date', type: 'date' }],
    columns: [{ key: 'title', label: 'Finding', sortKey: 'title' }, { label: 'Severity', render: (r) => status(r.severity) }, { label: 'Status', render: (r) => status(r.status) }, { label: 'Actions', render: (r) => `${r.actions_done}/${r.actions_total}` },
      { key: 'owner_name', label: 'Owner' }, { label: 'Due', sortKey: 'due_date', render: (r) => h('span', {}, fmtDate(r.due_date), r.overdue ? [' ', chip('overdue', 'bad')] : '') }],
    filters: [{ name: 'severity', label: 'Severity', options: ['low', 'medium', 'high', 'critical'] }, { name: 'status', label: 'Status', options: ['open', 'in_remediation', 'resolved', 'closed'] }],
    extraActions: (r, reload) => [h('button', { class: 'btn sm', onClick: () => addAction(r, reload) }, '+ Action'),
      can('ai') ? h('button', { class: 'btn sm', onClick: () => draftRemediation(r, reload) }, 'AI draft plan') : null],
  },
  'corrective-actions': {
    path: 'corrective-actions', singular: 'corrective action', title: 'Corrective action plans', perm: 'contribute', subtitle: 'Verification requires notes explaining how closure was confirmed.',
    fields: [{ name: 'finding_id', label: 'Finding', type: 'select', required: true, options: optionsOf('/api/findings', 'title'), full: true }, { name: 'title', label: 'Action', required: true, full: true }, { name: 'description', label: 'Details', type: 'textarea', full: true }, owner,
      { name: 'due_date', label: 'Due date', type: 'date' }, { name: 'status', label: 'Status', type: 'select', required: true, options: opts(['open', 'in_progress', 'done', 'verified']), value: 'open' }, { name: 'verification_notes', label: 'Verification notes', type: 'textarea', full: true }],
    columns: [{ key: 'title', label: 'Action' }, { key: 'finding_title', label: 'Finding' }, { key: 'owner_name', label: 'Owner' }, { label: 'Due', sortKey: 'due_date', render: (r) => fmtDate(r.due_date) }, { label: 'Status', render: (r) => status(r.status) }, { label: '', render: (r) => (r.ai_drafted ? chip('AI-drafted, reviewed by a person', 'info') : '') }],
    filters: [{ name: 'status', label: 'Status', options: ['open', 'in_progress', 'done', 'verified'] }],
  },
  tasks: {
    path: 'tasks', singular: 'task', title: 'Tasks', perm: 'contribute', subtitle: 'Work items from people, recommendations and automation rules.',
    fields: [{ name: 'title', label: 'Task', required: true, full: true }, { name: 'description', label: 'Details', type: 'textarea', full: true }, { name: 'assignee_id', label: 'Assignee', type: 'select', options: userOptions },
      { name: 'due_date', label: 'Due date', type: 'date' }, { name: 'priority', label: 'Priority', type: 'select', required: true, options: opts(['low', 'medium', 'high']), value: 'medium' }, { name: 'status', label: 'Status', type: 'select', required: true, options: opts(['open', 'in_progress', 'done', 'cancelled']), value: 'open' }],
    columns: [{ key: 'title', label: 'Task', sortKey: 'title' }, { key: 'assignee_name', label: 'Assignee' }, { label: 'Priority', render: (r) => status(r.priority) }, { label: 'Due', sortKey: 'due_date', render: (r) => h('span', {}, fmtDate(r.due_date), r.overdue ? [' ', chip('overdue', 'bad')] : '') },
      { label: 'Source', render: (r) => label(r.origin) }, { label: 'Status', render: (r) => status(r.status) }],
    filters: [{ name: 'status', label: 'Status', options: ['open', 'in_progress', 'done', 'cancelled'] }, { name: 'priority', label: 'Priority', options: ['low', 'medium', 'high'] }],
    extraActions: (r, reload) => (r.status === 'done' || r.status === 'cancelled' ? [] : [h('button', { class: 'btn sm', onClick: async () => { try { await patch(`/api/tasks/${r.id}`, { status: 'done' }); toast('Task completed', 'ok'); reload(); } catch (e) { fail(e); } } }, 'Done')]),
  },
  exceptions: {
    path: 'exceptions', singular: 'exception', title: 'Exceptions', perm: 'contribute', subtitle: 'Time-boxed deviations with justification and compensating controls. Approved through the approval workflow.',
    fields: [{ name: 'title', label: 'Exception', required: true, full: true }, { name: 'control_id', label: 'Control', type: 'select', options: controlOptions }, { name: 'risk_id', label: 'Related risk', type: 'select', options: optionsOf('/api/risks', 'title') },
      { name: 'justification', label: 'Justification', type: 'textarea', required: true, full: true }, { name: 'compensating_controls', label: 'Compensating controls', type: 'textarea', full: true }, { name: 'expires_on', label: 'Expires on', type: 'date' }],
    columns: [{ key: 'title', label: 'Exception' }, { key: 'control_key', label: 'Control' }, { label: 'Expires', render: (r) => h('span', {}, fmtDate(r.expires_on), r.expired ? [' ', chip('expired', 'bad')] : '') }, { key: 'requested_name', label: 'Requested by' }, { label: 'Status', render: (r) => status(r.status) }],
    filters: [{ name: 'status', label: 'Status', options: ['requested', 'approved', 'rejected', 'expired', 'revoked'] }],
    extraActions: (r) => (r.status === 'requested' ? [h('button', { class: 'btn sm', onClick: () => requestApproval('exception', r) }, 'Request approval')] : []),
  },
};

export async function requestApproval(type, rec) {
  const ok = await confirmDialog(`Send "${rec.title || rec.name}" for approval?`, { confirmText: 'Send for approval' });
  if (!ok) return;
  try { await post('/api/approvals/request', { entity_type: type, entity_id: rec.id }); toast('Approval requested', 'ok'); } catch (e) { fail(e); }
}

async function linkControls(kind, rec, reload) {
  const options = await controlOptions();
  const sel = h('select', { multiple: true, size: 10, class: 'input', style: { width: '100%' }, 'aria-label': 'Controls' }, options.map((o) => h('option', { value: o.value, selected: rec.control_ids.includes(o.value) }, o.label)));
  const m = modal(`Link controls to "${rec.title}"`, h('div', {}, h('p', { class: 'muted' }, 'Ctrl/Cmd-click to select several controls.'), sel), {
    footer: (close) => [h('button', { class: 'btn', onClick: close }, 'Cancel'), h('button', { class: 'btn primary', onClick: async () => {
      try { await put(`/api/${kind}/${rec.id}/controls`, { control_ids: [...sel.selectedOptions].map((o) => Number(o.value)) }); toast('Links saved', 'ok'); close(); reload(); } catch (e) { fail(e); } } }, 'Save')] });
  return m;
}

function addAction(finding, reload) {
  formModal({ title: `Corrective action for "${finding.title}"`, fields: [{ name: 'title', label: 'Action', required: true, full: true }, { name: 'description', label: 'Details', type: 'textarea', full: true }, { name: 'owner_id', label: 'Owner', type: 'select', options: userOptions }, { name: 'due_date', label: 'Due date', type: 'date' }],
    onSubmit: async (v) => { await post('/api/corrective-actions', { ...clean(v), finding_id: finding.id }); toast('Action added', 'ok'); reload(); } });
}

async function draftRemediation(finding, reload) {
  try {
    const r = await post('/api/ai/draft-remediation', { finding_id: finding.id });
    modal('Draft remediation plan', h('div', {}, h('div', { class: 'callout' }, r.disclaimer), h('p', { class: 'small muted' }, r.mode === 'llm' ? 'Written by the configured AI model from your finding and retrieved sources.' : 'Template plan (no AI provider configured). Edit before use.'),
      h('ol', {}, r.steps.map((s) => h('li', {}, s))), r.narrative ? h('pre', { class: 'report' }, r.narrative) : null,
      r.sources.map((s) => h('div', { class: 'cite' }, h('strong', {}, `${s.title} v${s.version}`), h('div', { class: 'small' }, s.excerpt.slice(0, 240))))), {
      footer: (close) => [h('button', { class: 'btn', onClick: close }, 'Discard'), h('button', { class: 'btn primary', onClick: async () => {
        try { for (const s of r.steps.slice(0, 5)) await post('/api/corrective-actions', { finding_id: finding.id, title: s.slice(0, 250), ai_drafted: true }); toast('Draft steps saved as corrective actions for review', 'ok'); close(); reload(); } catch (e) { fail(e); } } }, 'Save steps as actions')] });
  } catch (e) { fail(e); }
}

const clean = (v) => Object.fromEntries(Object.entries(v).filter(([, x]) => x !== '' && x !== null));

export function resourcePage(cfg, { embedded = false } = {}) {
  const writable = can(cfg.perm);
  let list;
  const open = (rec) => formModal({
    title: rec ? `Edit ${cfg.singular}` : `New ${cfg.singular}`, fields: cfg.fields, initial: rec || {}, wide: true,
    onSubmit: async (v) => {
      const body = {};
      for (const f of cfg.fields) {
        let x = v[f.name];
        if (x === '' || x === null) {
          const isText = !f.type || f.type === 'text' || f.type === 'textarea';
          if (!rec) continue;            // create: omit empty optional fields
          x = isText ? '' : null;        // edit: empty text stays empty, empty select/date/number clears the value
        }
        body[f.name] = x;
      }
      if (rec) await patch(`/api/${cfg.path}/${rec.id}`, body); else await post(`/api/${cfg.path}`, body);
      toast(`${label(cfg.singular)} saved`, 'ok'); list.reload();
    },
  });
  const remove = async (rec) => {
    if (!(await confirmDialog(`Delete "${rec.title || rec.name}"? This cannot be undone and is recorded in the audit trail.`, { confirmText: 'Delete', danger: true }))) return;
    try { await del(`/api/${cfg.path}/${rec.id}`); toast('Deleted', 'ok'); list.reload(); } catch (e) { fail(e); }
  };
  list = listView({
    endpoint: `/api/${cfg.path}`, columns: cfg.columns, filters: cfg.filters || [], onRow: writable ? open : null,
    emptyTitle: `No ${cfg.title.toLowerCase()} yet`, emptyText: writable ? `Add your first ${cfg.singular} to start tracking it.` : 'Nothing has been recorded yet.',
    emptyAction: writable ? h('button', { class: 'btn primary', onClick: () => open(null) }, `+ New ${cfg.singular}`) : null,
    rowActions: (r, reload) => [...(cfg.extraActions ? cfg.extraActions(r, reload).filter(Boolean) : []), writable ? h('button', { class: 'btn sm', onClick: () => open(r) }, 'Edit') : null, writable ? h('button', { class: 'btn sm danger', onClick: () => remove(r) }, 'Delete') : null],
  });
  if (embedded) return h('div', {}, h('div', { class: 'row between', style: { marginBottom: '10px' } }, h('p', { class: 'muted', style: { margin: 0 } }, cfg.subtitle), writable ? h('button', { class: 'btn primary', onClick: () => open(null) }, `+ New ${cfg.singular}`) : null), list);
  return h('div', {}, h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, cfg.title), h('p', {}, cfg.subtitle)), writable ? h('button', { class: 'btn primary', onClick: () => open(null) }, `+ New ${cfg.singular}`) : null), list);
}

export const emptyHint = empty;
