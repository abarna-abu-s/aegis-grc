import { del, get, patch, post } from '../api.js';
import { can, resetLookups, userOptions } from '../state.js';
import { chip, clear, confirmDialog, empty, errorState, fail, fmtDate, formModal, h, label, listView, loading, refresh, status, table, toast } from '../ui.js';
import { uploadEvidenceForm } from './evidence.js';

const FIELDS = [
  { name: 'control_key', label: 'Control ID', required: true, hint: 'Unique within your organization, e.g. AC-01' }, { name: 'title', label: 'Title', required: true },
  { name: 'description', label: 'Description / control statement', type: 'textarea', full: true }, { name: 'owner_id', label: 'Control owner', type: 'select', options: userOptions },
  { name: 'implementation_status', label: 'Implementation', type: 'select', required: true, options: ['not_started', 'in_progress', 'implemented', 'not_applicable'].map((v) => ({ value: v, label: label(v) })), value: 'not_started' },
  { name: 'review_frequency_days', label: 'Review every (days)', type: 'number', min: 7, maxValue: 3650, value: 90 }, { name: 'next_review_date', label: 'Next review', type: 'date' },
  { name: 'test_procedure', label: 'Test procedure', type: 'textarea', full: true },
];

function editBody(v, rec) {
  const body = {};
  for (const f of FIELDS) {
    let x = v[f.name];
    if (x === '' || x === null) { if (!rec) continue; x = f.type === 'textarea' || !f.type ? '' : null; }
    body[f.name] = x;
  }
  return body;
}

export function renderList(root) {
  const open = (rec) => formModal({ title: rec ? 'Edit control' : 'New control', fields: FIELDS, initial: rec || {}, wide: true,
    onSubmit: async (v) => { if (rec) await patch(`/api/controls/${rec.id}`, editBody(v, rec)); else await post('/api/controls', editBody(v)); resetLookups(); toast('Control saved', 'ok'); list.reload(); } });
  const list = listView({
    endpoint: '/api/controls', pageSize: 20, filters: [{ name: 'implementation_status', label: 'Implementation', options: ['not_started', 'in_progress', 'implemented', 'not_applicable'] }],
    onRow: (r) => { location.hash = `#/controls/${r.id}`; }, emptyTitle: 'No controls yet', emptyText: 'Create controls manually, or start an assessment and use "Create / reuse controls".',
    emptyAction: can('manage') ? h('button', { class: 'btn primary', onClick: () => open(null) }, '+ New control') : null,
    columns: [{ key: 'control_key', label: 'ID', sortKey: 'control_key' }, { key: 'title', label: 'Control', sortKey: 'title' }, { key: 'owner_name', label: 'Owner' }, { label: 'Health', render: (r) => status(r.health) },
      { label: 'Evidence', num: true, key: 'evidence_count' }, { label: 'Latest test', render: (r) => (r.latest_test ? status(r.latest_test) : '-') }, { label: 'Frameworks', render: (r) => h('span', { class: 'small' }, r.frameworks.slice(0, 3).join(', ') + (r.frameworks.length > 3 ? ` +${r.frameworks.length - 3}` : '')) },
      { label: 'Next review', sortKey: 'next_review_date', render: (r) => fmtDate(r.next_review_date) }],
  });
  root.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Control library'), h('p', {}, 'Internal controls are implemented once and mapped to many framework requirements, so evidence is collected once and reused.')),
    can('manage') ? h('button', { class: 'btn primary', onClick: () => open(null) }, '+ New control') : null), list);
}

export function renderDetail(root, id, setCrumbs) {
  const host = h('div', {}, loading());
  root.append(host);
  async function load() {
    try {
      const c = await get(`/api/controls/${id}/detail`);
      setCrumbs(['Controls', c.control_key]);
      clear(host);
      const current = c.evidence.filter((e) => ['current', 'expiring'].includes(e.status)).length;
      host.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, `${c.control_key} - ${c.title}`), h('p', {}, c.description || 'No description.'), h('div', { class: 'row', style: { marginTop: '6px' } }, status(c.health), status(c.implementation_status), chip(`Owner: ${c.owner_name || 'unassigned'}`))),
        h('div', { class: 'actions' }, can('contribute') ? h('button', { class: 'btn primary', onClick: () => uploadEvidenceForm({ controlId: c.id, onDone: load }) }, '+ Upload evidence') : null,
          can('contribute') ? h('button', { class: 'btn', onClick: () => requestEvidence(c) }, 'Request evidence') : null, can('contribute') ? h('button', { class: 'btn', onClick: () => recordTest(c) }, 'Record test') : null,
          can('manage') ? h('button', { class: 'btn danger', onClick: async () => { if (await confirmDialog(`Delete control ${c.control_key}? Linked assessment items become unlinked.`, { danger: true, confirmText: 'Delete' })) { try { await del(`/api/controls/${id}`); resetLookups(); toast('Control deleted', 'ok'); location.hash = '#/controls'; } catch (e) { fail(e); } } } }, 'Delete') : null)),
        h('div', { class: 'grid g-main' }, h('div', { class: 'stack' },
          h('div', { class: 'card' }, h('h3', {}, 'Evidence'), h('p', { class: 'small muted' }, `${current} of ${c.evidence.length} items are approved and unexpired.`),
            c.evidence.length ? table([{ label: 'Title', render: (e) => (e.restricted ? chip('restricted', 'warn') : h('a', { href: `#/evidence/${e.id}` }, e.title)) }, { label: 'Collected', render: (e) => fmtDate(e.collected_on) }, { label: 'Valid until', render: (e) => fmtDate(e.valid_until) },
              { label: 'Freshness', render: (e) => (e.status ? status(e.status) : '-') }, { key: 'owner', label: 'Owner' }, { label: 'Review', render: (e) => (e.review_status ? status(e.review_status) : '-') },
              { label: '', render: (e) => (can('contribute') && !e.restricted ? h('button', { class: 'btn sm', onClick: async () => { if (await confirmDialog('Unlink this evidence from the control? The evidence itself is kept.', { confirmText: 'Unlink' })) { try { await del(`/api/controls/${id}/evidence-links/${e.id}`); toast('Unlinked', 'ok'); load(); } catch (err) { fail(err); } } } }, 'Unlink') : null) }], c.evidence)
              : empty('No evidence linked', 'Upload evidence or link existing items from the evidence repository.', can('contribute') ? h('button', { class: 'btn', onClick: () => linkExisting(c) }, 'Link existing evidence') : null),
            c.evidence.length && can('contribute') ? h('button', { class: 'btn sm', style: { marginTop: '10px' }, onClick: () => linkExisting(c) }, '+ Link existing evidence') : null),
          h('div', { class: 'card' }, h('h3', {}, 'Test results'), c.tests.length ? table([{ label: 'Date', render: (t) => fmtDate(t.tested_at) }, { label: 'Result', render: (t) => status(t.result) }, { key: 'notes', label: 'Notes' }], c.tests) : h('p', { class: 'muted' }, 'No tests recorded.'),
            c.test_procedure ? h('p', { class: 'small' }, h('strong', {}, 'Test procedure: '), c.test_procedure) : null),
          h('div', { class: 'card' }, h('h3', {}, 'Framework requirements this control satisfies'), c.requirements.length ? table([{ key: 'framework', label: 'Framework' }, { key: 'ref_id', label: 'ID' }, { key: 'title', label: 'Requirement' }, { label: 'Result', render: (r) => status(r.result) }], c.requirements) : h('p', { class: 'muted' }, 'Not yet linked to any assessment requirement.'))),
          h('div', { class: 'stack' },
            h('div', { class: 'card' }, h('h3', {}, 'Required evidence'), c.required_evidence_are_suggestions ? h('div', { class: 'callout' }, 'These are platform-generated suggestions, not text from a standard.') : null,
              h('ul', { class: 'clean' }, c.required_evidence.map((x) => h('li', {}, x)))),
            h('div', { class: 'card' }, h('h3', {}, 'Details'), h('dl', { class: 'small' }, h('dt', { class: 'muted' }, 'Review cadence'), h('dd', {}, `Every ${c.review_frequency_days} days`), h('dt', { class: 'muted' }, 'Next review'), h('dd', {}, fmtDate(c.next_review_date)))),
            h('div', { class: 'card' }, h('h3', {}, 'Policies'), c.policies.length ? h('ul', { class: 'clean' }, c.policies.map((p) => h('li', {}, h('a', { href: '#/policies' }, p.title), ' ', status(p.status)))) : h('p', { class: 'muted' }, 'No policy linked.')),
            h('div', { class: 'card' }, h('h3', {}, 'Risks mitigated'), c.risks.length ? h('ul', { class: 'clean' }, c.risks.map((r) => h('li', {}, r.title, ' ', chip(`score ${r.score}`)))) : h('p', { class: 'muted' }, 'No risk linked.')))));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  load();
}

async function linkExisting(c) {
  const d = await get('/api/evidence', { page_size: 100 }).catch((e) => { fail(e); return null; });
  if (!d) return;
  formModal({ title: 'Link existing evidence', fields: [{ name: 'evidence_id', label: 'Evidence item', type: 'select', required: true, options: d.items.map((e) => ({ value: e.id, label: `${e.title} (${e.status})` })) }],
    onSubmit: async (v) => { await post(`/api/controls/${c.id}/evidence-links`, { evidence_id: v.evidence_id }); toast('Evidence linked', 'ok'); refresh(); } });
}

function requestEvidence(c) {
  formModal({ title: `Request evidence for ${c.control_key}`, intro: 'The assignee sees this on their evidence collection queue. Uploading evidence against the request closes it.',
    fields: [{ name: 'title', label: 'What is needed', required: true, value: c.required_evidence[0] || '', full: true }, { name: 'instructions', label: 'Instructions', type: 'textarea', full: true }, { name: 'assignee_id', label: 'Assign to', type: 'select', options: userOptions }, { name: 'due_date', label: 'Due date', type: 'date' }],
    onSubmit: async (v) => { await post('/api/evidence-requests', { control_id: c.id, title: v.title, instructions: v.instructions, assignee_id: v.assignee_id || undefined, due_date: v.due_date || undefined }); toast('Request created', 'ok'); } });
}

function recordTest(c) {
  formModal({ title: `Record test for ${c.control_key}`, fields: [{ name: 'result', label: 'Result', type: 'select', required: true, options: ['pass', 'fail', 'inconclusive'].map((v) => ({ value: v, label: label(v) })) }, { name: 'tested_at', label: 'Test date', type: 'date', value: new Date().toISOString().slice(0, 10) }, { name: 'notes', label: 'Notes', type: 'textarea', full: true }],
    onSubmit: async (v) => { await post(`/api/controls/${c.id}/tests`, { result: v.result, tested_at: v.tested_at || undefined, notes: v.notes }); toast('Test recorded', 'ok'); refresh(); } });
}

