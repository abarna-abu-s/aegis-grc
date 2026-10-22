import { del, get, patch, post, put } from '../api.js';
import { can, controlOptions, userOptions } from '../state.js';
import { aiChip, chip, clear, confirmDialog, empty, errorState, fail, fmtDate, fmtDateTime, formModal, h, label, listView, loading, modal, status, table, toast } from '../ui.js';
import { requestApproval } from '../resources.js';
import { tabbed } from './common.js';

const FIELDS = [{ name: 'title', label: 'Title', required: true, full: true }, { name: 'category', label: 'Type', type: 'select', required: true, options: ['policy', 'standard', 'procedure'].map((v) => ({ value: v, label: label(v) })), value: 'policy' }, { name: 'owner_id', label: 'Owner', type: 'select', options: userOptions },
  { name: 'next_review_date', label: 'Next review', type: 'date' }, { name: 'body', label: 'Policy text', type: 'textarea', rows: 14, full: true, hint: 'Approved policies are indexed for the AI copilot. Editing an approved policy creates a new version and returns it to draft.' }];

export function render(root, _id, _setCrumbs, tab) {
  const list = () => {
    let l;
    const open = (rec) => formModal({ title: rec ? `Edit policy (v${rec.version})` : 'New policy', fields: FIELDS, initial: rec || {}, wide: true,
      onSubmit: async (v) => {
        const body = { title: v.title, category: v.category, body: v.body, owner_id: v.owner_id || null, next_review_date: v.next_review_date || null };
        if (rec) await patch(`/api/policies/${rec.id}`, body); else await post('/api/policies', body);
        toast('Policy saved', 'ok'); l.reload();
      } });
    l = listView({
      endpoint: '/api/policies', filters: [{ name: 'status', label: 'Status', options: ['draft', 'in_review', 'approved', 'retired'] }, { name: 'category', label: 'Type', options: ['policy', 'standard', 'procedure'] }],
      onRow: (r) => open(r), emptyTitle: 'No policies yet', emptyText: 'Add your policies so they can be linked to controls and cited by the copilot.', emptyAction: can('manage') ? h('button', { class: 'btn primary', onClick: () => open(null) }, '+ New policy') : null,
      columns: [{ key: 'title', label: 'Policy', sortKey: 'title' }, { label: 'Type', render: (r) => label(r.category) }, { label: 'Version', render: (r) => `v${r.version}` }, { label: 'Status', render: (r) => status(r.status) }, { key: 'owner_name', label: 'Owner' },
        { label: 'Controls', num: true, render: (r) => r.control_ids.length }, { label: 'Next review', sortKey: 'next_review_date', render: (r) => h('span', {}, fmtDate(r.next_review_date), r.review_overdue ? [' ', chip('overdue', 'bad')] : '') }],
      rowActions: (r, reload) => [can('contribute') && ['draft'].includes(r.status) ? h('button', { class: 'btn sm primary', onClick: async () => { await requestApproval('policy', r); reload(); } }, 'Request approval') : null,
        can('manage') ? h('button', { class: 'btn sm', onClick: () => linkControls(r, reload) }, 'Controls') : null, h('button', { class: 'btn sm', onClick: () => versions(r) }, 'History'),
        can('ai') ? h('button', { class: 'btn sm', onClick: () => gap(r) }, 'AI: gap analysis') : null,
        can('manage') ? h('button', { class: 'btn sm danger', onClick: async () => { if (await confirmDialog(`Delete "${r.title}" and its version history?`, { danger: true, confirmText: 'Delete' })) { try { await del(`/api/policies/${r.id}`); toast('Deleted', 'ok'); reload(); } catch (e) { fail(e); } } } }, 'Delete') : null],
    });
    return h('div', {}, h('div', { class: 'row between', style: { marginBottom: '8px' } }, h('p', { class: 'muted', style: { margin: 0 } }, 'Policies are approved through the approval workflow; only approved policies are retrievable by the copilot.'),
      can('manage') ? h('button', { class: 'btn primary', onClick: () => open(null) }, '+ New policy') : null), l);
  };
  const trace = () => {
    const host = h('div', {}, loading());
    get('/api/policy-traceability').then((d) => clear(host).append(d.items.length ? h('div', { class: 'stack' }, d.items.map((p) => h('div', { class: 'card' }, h('div', { class: 'row between' }, h('h3', {}, p.title), status(p.status)),
      p.controls.length ? h('ul', { class: 'clean' }, p.controls.map((c) => h('li', {}, h('a', { href: `#/controls/${c.control_id}` }, `${c.control_key} ${c.title}`), h('div', { class: 'small muted' }, c.requirements.length ? `Satisfies: ${c.requirements.join(', ')}` : 'Control not linked to any framework requirement yet.')))) : h('p', { class: 'muted' }, 'Not linked to any control - add links from the Policies tab.'))))
      : empty('No policies', 'Create a policy first.'))).catch((e) => clear(host).append(errorState(e)));
    return host;
  };
  const compare = () => comparePanel();
  root.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Policies'), h('p', {}, 'Versioned policies with approval workflow and traceability from policy to control to framework requirement.'))),
    tabbed([['list', 'Policies', list], ['trace', 'Traceability', trace], ['compare', 'Compare (AI)', compare]], tab));
}

async function linkControls(p, reload) {
  const options = await controlOptions();
  const sel = h('select', { multiple: true, size: 10, class: 'input', style: { width: '100%' }, 'aria-label': 'Controls' }, options.map((o) => h('option', { value: o.value, selected: p.control_ids.includes(o.value) }, o.label)));
  modal(`Controls implemented by "${p.title}"`, h('div', {}, h('p', { class: 'muted' }, 'Ctrl/Cmd-click to select several controls.'), sel), { footer: (close) => [h('button', { class: 'btn', onClick: close }, 'Cancel'),
    h('button', { class: 'btn primary', onClick: async () => { try { await put(`/api/policies/${p.id}/controls`, { control_ids: [...sel.selectedOptions].map((o) => Number(o.value)) }); toast('Links saved', 'ok'); close(); reload(); } catch (e) { fail(e); } } }, 'Save')] });
}

async function versions(p) {
  try {
    const d = await get(`/api/policies/${p.id}/versions`);
    modal(`Version history: ${p.title}`, d.items.length ? table([{ label: 'Version', render: (v) => `v${v.version}` }, { label: 'Saved', render: (v) => fmtDateTime(v.created_at) }, { label: 'Text', render: (v) => h('span', { class: 'small' }, v.body.slice(0, 160) + (v.body.length > 160 ? '...' : '')) }], d.items) : empty('No prior versions', 'The first revision has not been superseded yet.'), { wide: true });
  } catch (e) { fail(e); }
}

async function gap(p) {
  const asm = await get('/api/assessments').catch(() => ({ items: [] }));
  if (!asm.items.length) { toast('Start an assessment first; the gap analysis compares the policy to its requirements.', 'bad'); return; }
  formModal({ title: `Policy gap analysis: ${p.title}`, intro: 'Compares the policy text to each applicable requirement. Without imported requirement text it matches on requirement titles only.',
    fields: [{ name: 'assessment_id', label: 'Assessment', type: 'select', required: true, options: asm.items.map((a) => ({ value: a.id, label: a.name })) }], submitText: 'Run analysis',
    onSubmit: async (v) => {
      const r = await post('/api/ai/policy-gap', { policy_id: p.id, assessment_id: Number(v.assessment_id) });
      modal('Policy gap analysis', h('div', {}, h('div', { class: 'callout' }, aiChip(), ' ', r.disclaimer), h('p', {}, r.summary), h('p', { class: 'small muted' }, r.caveat),
        table([{ key: 'requirement', label: 'Requirement' }, { label: 'Indicator', render: (x) => chip(x.verdict === 'possible_coverage' ? 'possible coverage' : 'weak or none', x.verdict === 'possible_coverage' ? 'ok' : 'warn') }, { label: 'Score', render: (x) => x.score }, { label: 'Matching passage', render: (x) => h('span', { class: 'small' }, x.excerpt.slice(0, 160)) }], r.rows)), { wide: true });
    } });
}

function comparePanel() {
  const out = h('div', {});
  const host = h('div', {}, h('p', { class: 'muted' }, 'Compare two policies paragraph by paragraph. Results are suggestions to help a reviewer, not a legal analysis.'));
  get('/api/policies', { page_size: 200 }).then((d) => {
    if (d.items.length < 2) { host.append(empty('Need two policies', 'Create at least two policies to compare them.')); return; }
    const opts = d.items.map((p) => h('option', { value: p.id }, `${p.title} (v${p.version})`));
    const a = h('select', { class: 'input', 'aria-label': 'First policy' }, opts.map((o) => o.cloneNode(true))); const b = h('select', { class: 'input', 'aria-label': 'Second policy' }, opts.map((o) => o.cloneNode(true))); b.selectedIndex = 1;
    host.append(h('div', { class: 'filters' }, a, b, h('button', { class: 'btn primary', onClick: async () => {
      clear(out).append(loading());
      try { const r = await post('/api/ai/compare-policies', { a: Number(a.value), b: Number(b.value) });
        clear(out).append(h('div', { class: 'callout' }, `Overall text similarity ${Math.round(r.similarity * 100)}%. ${r.disclaimer}`), h('div', { class: 'grid g2', style: { marginTop: '12px' } },
          h('div', { class: 'card' }, h('h3', {}, 'Only in first'), r.only_in_first.length ? h('ul', { class: 'clean' }, r.only_in_first.map((x) => h('li', {}, x))) : h('p', { class: 'muted' }, 'Nothing unique.')),
          h('div', { class: 'card' }, h('h3', {}, 'Only in second'), r.only_in_second.length ? h('ul', { class: 'clean' }, r.only_in_second.map((x) => h('li', {}, x))) : h('p', { class: 'muted' }, 'Nothing unique.')))); } catch (e) { clear(out).append(errorState(e)); }
    } }, 'Compare')), out);
  }).catch((e) => host.append(errorState(e)));
  return host;
}
