import { del, download, get, post } from '../api.js';
import { can, controlOptions, userOptions } from '../state.js';
import { PALETTE, aiChip, chip, clear, confirmDialog, empty, errorState, fail, fmtDate, fmtDateTime, formModal, h, label, listView, loading, modal, refresh, status, table, toast } from '../ui.js';
import { resourcePage } from '../resources.js';
import { tabbed } from './common.js';

export function uploadEvidenceForm({ controlId, requestId, title, onDone } = {}) {
  formModal({ title: 'Upload evidence', wide: true, intro: 'New evidence starts as "pending" and only counts toward coverage once an authorised reviewer approves it. Allowed: PDF, PNG, JPG, DOCX, XLSX, TXT, MD, CSV.',
    fields: [{ name: 'title', label: 'Title', required: true, value: title || '', full: true }, { name: 'description', label: 'Description', type: 'textarea', full: true },
      { name: 'file', label: 'File', type: 'file', accept: '.pdf,.png,.jpg,.jpeg,.docx,.xlsx,.txt,.md,.csv', full: true }, { name: 'source', label: 'Source / provenance', value: 'manual upload', hint: 'Where did this come from? e.g. "Okta export by E. Engineer".' },
      { name: 'owner_id', label: 'Evidence owner', type: 'select', options: userOptions }, { name: 'collected_on', label: 'Collected on', type: 'date', value: new Date().toISOString().slice(0, 10) }, { name: 'valid_until', label: 'Valid until', type: 'date', hint: 'Leave blank only if the evidence does not expire.' },
      { name: 'control_ids', label: 'Linked control', type: 'select', options: controlOptions, value: controlId || '' }, { name: 'sensitivity', label: 'Sensitivity', type: 'select', required: true, options: [{ value: 'standard', label: 'Standard' }, { value: 'restricted', label: 'Restricted (reviewers/auditors only)' }], value: 'standard' }],
    onSubmit: async (v) => {
      const fd = new FormData();
      ['title', 'description', 'source', 'owner_id', 'collected_on', 'valid_until', 'sensitivity'].forEach((k) => { if (v[k]) fd.append(k, v[k]); });
      if (v.control_ids) fd.append('control_ids', v.control_ids);
      if (requestId) fd.append('request_id', requestId);
      if (v.file) fd.append('file', v.file);
      const { api } = await import('../api.js');
      await api('/api/evidence', { method: 'POST', form: fd });
      toast('Evidence uploaded and awaiting review', 'ok'); if (onDone) onDone(); else refresh();
    } });
}

export function renderEvidence(root, id, setCrumbs, tab) {
  if (id) return renderOne(root, id, setCrumbs);
  const repo = () => {
    const list = listView({
      endpoint: '/api/evidence', filters: [{ name: 'status', label: 'Freshness', options: ['current', 'expiring', 'expired', 'pending', 'rejected'] }, { name: 'review_status', label: 'Review', options: ['pending', 'approved', 'rejected'] }, { name: 'sensitivity', label: 'Sensitivity', options: ['standard', 'restricted'] }],
      onRow: (e) => { location.hash = `#/evidence/${e.id}`; }, emptyTitle: 'No evidence yet', emptyText: 'Upload evidence and link it to the controls it supports.',
      emptyAction: can('contribute') ? h('button', { class: 'btn primary', onClick: () => uploadEvidenceForm() }, '+ Upload evidence') : null,
      columns: [{ key: 'title', label: 'Evidence' }, { label: 'Controls', render: (e) => (e.control_keys.length ? e.control_keys.join(', ') : chip('unlinked', 'warn')) }, { key: 'owner_name', label: 'Owner' }, { label: 'Collected', render: (e) => fmtDate(e.collected_on) },
        { label: 'Valid until', render: (e) => h('span', {}, fmtDate(e.valid_until), e.days_until_expiry !== null && e.days_until_expiry >= 0 && e.days_until_expiry <= 30 ? h('div', { class: 'small muted' }, `${e.days_until_expiry} days left`) : '') },
        { label: 'Freshness', render: (e) => status(e.status) }, { label: 'Review', render: (e) => status(e.review_status) }, { label: 'v', num: true, render: (e) => e.latest_version ?? '-' }, { label: '', render: (e) => (e.sensitivity === 'restricted' ? chip('restricted', 'warn') : '') }],
    });
    return h('div', {}, h('div', { class: 'row between', style: { marginBottom: '8px' } }, h('p', { class: 'muted', style: { margin: 0 } }, 'Every item records provenance, an owner, a validity period, a reviewer and a version history.'),
      can('contribute') ? h('button', { class: 'btn primary', onClick: () => uploadEvidenceForm() }, '+ Upload evidence') : null), list);
  };
  const requests = () => resourcePage({
    path: 'evidence-requests', singular: 'evidence request', title: 'Collection requests', perm: 'contribute', subtitle: 'Ask control owners to supply specific evidence by a due date. Uploading against a request closes it.',
    fields: [{ name: 'control_id', label: 'Control', type: 'select', required: true, options: controlOptions, full: true }, { name: 'title', label: 'What is needed', required: true, full: true }, { name: 'instructions', label: 'Instructions', type: 'textarea', full: true },
      { name: 'assignee_id', label: 'Assignee', type: 'select', options: userOptions }, { name: 'due_date', label: 'Due', type: 'date' }, { name: 'status', label: 'Status', type: 'select', required: true, options: ['open', 'submitted', 'closed', 'cancelled'].map((v) => ({ value: v, label: label(v) })), value: 'open' }],
    columns: [{ key: 'title', label: 'Request' }, { key: 'control_key', label: 'Control' }, { key: 'assignee_name', label: 'Assignee' }, { label: 'Due', sortKey: 'due_date', render: (r) => h('span', {}, fmtDate(r.due_date), r.overdue ? [' ', chip('overdue', 'bad')] : '') }, { label: 'Status', render: (r) => status(r.status) }],
    filters: [{ name: 'status', label: 'Status', options: ['open', 'submitted', 'closed', 'cancelled'] }],
    extraActions: (r) => (r.status === 'open' && can('contribute') ? [h('button', { class: 'btn sm primary', onClick: () => uploadEvidenceForm({ controlId: r.control_id, requestId: r.id, title: r.title }) }, 'Fulfil')] : []),
  }, { embedded: true });
  const missing = () => lazy('/api/evidence-intel/missing', (d) => (d.items.length ? h('div', {}, h('p', { class: 'muted' }, 'Controls in an active assessment that have no approved, unexpired evidence. Required evidence is advisory guidance.'),
    table([{ label: 'Control', render: (m) => h('a', { href: `#/controls/${m.control_id}` }, `${m.control_key} ${m.title}`) }, { key: 'reason', label: 'Why' }, { label: 'Frameworks', render: (m) => h('span', { class: 'small' }, m.frameworks.join(', ')) }, { label: 'Needed', render: (m) => h('span', { class: 'small' }, m.required_evidence.slice(0, 3).join('; ')) },
      { label: '', render: (m) => (can('contribute') ? h('div', { class: 'row' }, h('button', { class: 'btn sm primary', onClick: () => uploadEvidenceForm({ controlId: m.control_id }) }, 'Upload'), h('button', { class: 'btn sm', onClick: () => requestFor(m) }, 'Request')) : null) }], d.items))
    : empty('No missing evidence', 'Every control linked to an active assessment has current approved evidence (or nothing is linked yet).')));
  const heat = () => lazy('/api/evidence-intel/heatmap', (d) => {
    if (!d.columns.length || !d.rows.length) return empty('No coverage data', 'Start an assessment and link controls to see evidence coverage by domain.');
    const color = (c) => (c === null ? 'var(--muted-bg)' : c.pct >= 75 ? '#4F7A55' : c.pct >= 40 ? '#C28A2B' : '#A8402F');
    return h('div', {}, h('p', { class: 'muted' }, 'Share of applicable requirements in each domain whose control has approved, unexpired evidence. Cross-framework reuse is already included because evidence attaches to controls.'),
      h('div', { class: 'heat', style: { gridTemplateColumns: `minmax(160px, 1.3fr) repeat(${d.columns.length}, minmax(90px, 1fr))` } },
        h('div', {}), d.columns.map((c) => h('div', { class: 'small muted', style: { textAlign: 'center' } }, c.name)),
        d.rows.map((r, i) => [h('div', { style: { alignSelf: 'center' } }, r), ...d.cells[i].map((c) => h('div', { class: 'cell', style: { background: color(c), color: c ? '#fff' : 'var(--text-3)' }, title: c ? `${c.covered} of ${c.total} requirements covered` : 'No requirements' }, c ? `${c.pct}%` : '-'))])),
      h('div', { class: 'legend' }, h('span', {}, h('i', { style: { background: '#4F7A55' } }), '75%+'), h('span', {}, h('i', { style: { background: '#C28A2B' } }), '40-74%'), h('span', {}, h('i', { style: { background: '#A8402F' } }), 'under 40%')));
  });
  const docs = () => documents();
  root.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Evidence'), h('p', {}, 'Repository, collection workflow and coverage intelligence for audit evidence.'))),
    tabbed([['repo', 'Repository', repo], ['requests', 'Collection requests', requests], ['missing', 'Missing evidence', missing], ['heatmap', 'Coverage heatmap', heat], ['docs', 'Reference documents', docs]], tab));
}

function lazy(endpoint, build) {
  const host = h('div', {}, loading());
  get(endpoint).then((d) => clear(host).append(build(d))).catch((e) => clear(host).append(errorState(e, () => refresh())));
  return host;
}

function requestFor(m) {
  formModal({ title: `Request evidence for ${m.control_key}`, fields: [{ name: 'title', label: 'What is needed', required: true, value: m.required_evidence[0] || '', full: true }, { name: 'assignee_id', label: 'Assign to', type: 'select', options: userOptions }, { name: 'due_date', label: 'Due date', type: 'date' }],
    onSubmit: async (v) => { await post('/api/evidence-requests', { control_id: m.control_id, title: v.title, assignee_id: v.assignee_id || undefined, due_date: v.due_date || undefined }); toast('Request created', 'ok'); } });
}

function documents() {
  const host = h('div', {});
  async function load() {
    clear(host).append(loading());
    try {
      const d = await get('/api/documents');
      clear(host).append(h('div', { class: 'row between', style: { marginBottom: '8px' } }, h('p', { class: 'muted', style: { margin: 0 } }, 'Standards excerpts, prior reports or questionnaires that the AI copilot may cite. Text is extracted and indexed; contents are treated as untrusted data.'),
        can('manage') ? h('button', { class: 'btn primary', onClick: add }, '+ Add document') : null),
        d.items.length ? table([{ key: 'title', label: 'Title' }, { key: 'filename', label: 'File' }, { label: 'Indexed', render: (x) => (x.indexed ? chip(`${x.characters} chars`, 'ok') : chip('no text extracted', 'warn')) }, { label: 'Sensitivity', render: (x) => chip(x.sensitivity) }, { label: 'Added', render: (x) => fmtDate(x.created_at) },
          { label: '', render: (x) => (can('manage') ? h('button', { class: 'btn sm danger', onClick: async () => { if (await confirmDialog(`Delete "${x.title}" and remove it from search?`, { danger: true, confirmText: 'Delete' })) { try { await del(`/api/documents/${x.id}`); toast('Deleted', 'ok'); load(); } catch (e) { fail(e); } } } }, 'Delete') : null) }], d.items)
          : empty('No reference documents', 'Add documents so the copilot has material to cite.'));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  function add() {
    formModal({ title: 'Add reference document', fields: [{ name: 'title', label: 'Title', full: true }, { name: 'file', label: 'File (PDF, DOCX, TXT, MD, CSV)', type: 'file', required: true, accept: '.pdf,.docx,.txt,.md,.csv', full: true },
      { name: 'sensitivity', label: 'Sensitivity', type: 'select', required: true, options: [{ value: 'standard', label: 'Standard' }, { value: 'restricted', label: 'Restricted' }], value: 'standard' }],
      onSubmit: async (v) => { const fd = new FormData(); fd.append('title', v.title); fd.append('sensitivity', v.sensitivity); fd.append('file', v.file); const { api } = await import('../api.js'); const r = await api('/api/documents', { method: 'POST', form: fd }); toast(r.warning || `Indexed ${r.chunks} passages`, r.warning ? '' : 'ok'); load(); } });
  }
  load();
  return host;
}

function renderOne(root, id, setCrumbs) {
  const host = h('div', {}, loading());
  root.append(host);
  async function load() {
    try {
      const e = await get(`/api/evidence/${id}`);
      setCrumbs(['Evidence', e.title]);
      clear(host).append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, e.title), h('p', {}, e.description || 'No description.'), h('div', { class: 'row', style: { marginTop: '6px' } }, status(e.status), status(e.review_status), e.sensitivity === 'restricted' ? chip('restricted', 'warn') : null)),
        h('div', { class: 'actions' }, can('approve') && e.review_status !== 'approved' ? h('button', { class: 'btn primary', onClick: () => review(e, 'approved') }, 'Approve') : null, can('approve') && e.review_status !== 'rejected' ? h('button', { class: 'btn', onClick: () => review(e, 'rejected') }, 'Reject') : null,
          can('contribute') ? h('button', { class: 'btn', onClick: () => newVersion(e) }, 'Upload new version') : null, can('ai') ? h('button', { class: 'btn', onClick: () => classify(e) }, 'AI: suggest controls') : null,
          can('approve') ? h('button', { class: 'btn danger', onClick: async () => { if (await confirmDialog('Delete this evidence record and its links? File bytes are retained on disk for retention purposes and the deletion is audited.', { danger: true, confirmText: 'Delete' })) { try { await del(`/api/evidence/${id}`); toast('Deleted', 'ok'); location.hash = '#/evidence'; } catch (err) { fail(err); } } } }, 'Delete') : null)),
        h('div', { class: 'grid g-main' }, h('div', { class: 'stack' },
          h('div', { class: 'card' }, h('h3', {}, 'Version history'), table([{ label: 'Version', render: (v) => `v${v.version}` }, { key: 'filename', label: 'File' }, { label: 'Size', render: (v) => `${(v.size / 1024).toFixed(1)} KB` }, { label: 'SHA-256', render: (v) => h('span', { class: 'mono' }, v.sha256.slice(0, 16) + '...') }, { label: 'Uploaded', render: (v) => fmtDateTime(v.uploaded_at) },
            { label: '', render: (v) => h('button', { class: 'btn sm', onClick: async () => { try { await download(`/api/evidence/${id}/versions/${v.version}/download`, v.filename); } catch (err) { fail(err); } } }, 'Download') }], e.versions.length ? e.versions : [])),
          e.versions.length === 0 ? h('div', { class: 'callout warn' }, 'This record has no file attached (metadata-only attestation).') : null),
          h('div', { class: 'stack' }, h('div', { class: 'card' }, h('h3', {}, 'Provenance & validity'), h('dl', { class: 'small' }, ...[['Source', e.source], ['Owner', e.owner_name || '-'], ['Collected', fmtDate(e.collected_on)], ['Valid until', e.valid_until ? `${fmtDate(e.valid_until)}${e.days_until_expiry !== null ? ` (${e.days_until_expiry} days)` : ''}` : 'No expiry'], ['Reviewed by', e.reviewer_name || 'not yet reviewed'], ['Next action', e.status === 'expired' ? 'Replace with fresh evidence' : e.status === 'pending' ? 'Awaiting reviewer' : 'None']].flatMap(([k, v]) => [h('dt', { class: 'muted' }, k), h('dd', { style: { margin: '0 0 8px' } }, v)]))),
            h('div', { class: 'card' }, h('h3', {}, 'Supports controls'), e.control_keys.length ? h('ul', { class: 'clean' }, e.control_ids.map((cid, i) => h('li', {}, h('a', { href: `#/controls/${cid}` }, e.control_keys[i])))) : h('p', { class: 'muted' }, 'Not linked to any control.')))));
    } catch (err) { clear(host).append(errorState(err, load)); }
  }
  async function review(e, decision) {
    if (!(await confirmDialog(`${decision === 'approved' ? 'Approve' : 'Reject'} this evidence? The decision is recorded with your name in the audit trail.`, { confirmText: label(decision).replace('ed', 'e') }))) return;
    try { await post(`/api/evidence/${id}/review`, { decision }); toast(`Evidence ${decision}`, 'ok'); load(); } catch (err) { fail(err); }
  }
  function newVersion() {
    formModal({ title: 'Upload new version', intro: 'A new version resets the review status to pending.', fields: [{ name: 'file', label: 'File', type: 'file', required: true, full: true }],
      onSubmit: async (v) => { const fd = new FormData(); fd.append('file', v.file); const { api } = await import('../api.js'); await api(`/api/evidence/${id}/versions`, { method: 'POST', form: fd }); toast('Version added', 'ok'); load(); } });
  }
  async function classify() {
    try {
      const r = await post('/api/ai/classify-evidence', { evidence_id: Number(id) });
      const sel = {};
      modal('Suggested controls', h('div', {}, h('div', { class: 'callout' }, h('span', {}, 'This is a suggestion, not a determination. '), r.disclaimer), h('p', {}, 'Category guess: ', h('strong', {}, r.category_guess), ` (method: ${r.method})`),
        r.candidates.length ? table([{ key: 'control_key', label: 'Control' }, { key: 'title', label: 'Title' }, { label: 'Similarity', render: (c) => c.score },
          { label: '', render: (c) => h('button', { class: 'btn sm primary', onClick: async (ev) => { try { await post(`/api/ai/suggestions/${r.suggestion_id}/decision`, { decision: 'accepted', chosen_control_id: c.control_id }); toast(`Linked to ${c.control_key}`, 'ok'); ev.target.disabled = true; load(); } catch (err) { fail(err); } } }, 'Accept link') }], r.candidates)
          : empty('No similar controls', 'Create a control first, or link manually.')), { wide: true });
    } catch (err) { fail(err); }
  }
  load();
}
