import { del, get, patch, post } from '../api.js';
import { can, controlOptions, frameworkOptions, invalidate, optionsOf, resetLookups } from '../state.js';
import { PALETTE, aiChip, chip, clear, confirmDialog, empty, errorState, fail, fmtDate, formModal, h, label, listView, loading, meter, modal, scoreColor, status, table, toast } from '../ui.js';

const SOURCE = { template_unverified: ['Template - not legally verified', 'warn'], imported: ['Imported by your organization', 'info'], custom: ['Custom (your organization)', 'info'] };
export const sourceChip = (s) => chip(SOURCE[s]?.[0] || s, SOURCE[s]?.[1] || '');

export function renderLibrary(root) {
  const host = h('div', {}, loading());
  root.append(host);
  async function load() {
    try {
      const d = await get('/api/frameworks');
      clear(host);
      host.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Framework library'), h('p', {}, 'Built-in entries are templates: identifiers and titles only, not legally verified requirement text and not a statement of certification. Clone a template to import your own licensed or official text, or author a custom framework.')),
        can('manage') ? h('div', { class: 'actions' }, h('button', { class: 'btn primary', onClick: () => newCustom(load) }, '+ Custom framework')) : null),
        table([
          { label: 'Framework', render: (f) => h('a', { href: `#/frameworks/${f.id}` }, `${f.name}`) }, { key: 'version', label: 'Version' }, { key: 'jurisdiction', label: 'Jurisdiction' }, { label: 'Type', render: (f) => label(f.kind) },
          { label: 'Content status', render: (f) => sourceChip(f.source_status) }, { label: 'Controls', num: true, render: (f) => f.control_count || chip('none loaded', 'warn') }, { label: 'Assessments', num: true, key: 'adopted_assessments' },
          { label: '', render: (f) => h('div', { class: 'row' }, can('manage') && f.control_count ? h('button', { class: 'btn sm primary', onClick: () => startAssessment(f) }, 'Start assessment') : null,
            can('manage') && f.global_template ? h('button', { class: 'btn sm', onClick: () => clone(f, load) }, 'Clone') : null) }], d.items));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  load();
}

export function startAssessment(f) {
  formModal({ title: `Start assessment: ${f.name}`, intro: 'Creates one assessment item per framework control. Nothing is marked compliant; results are set by reviewers once evidence exists.',
    fields: [{ name: 'name', label: 'Assessment name', required: true, value: `${f.name} ${new Date().getFullYear()}`, full: true }, { name: 'workspace_id', label: 'Workspace', type: 'select', options: optionsOf('/api/workspaces', 'name'), hint: 'Optional grouping, e.g. a business unit.' }, { name: 'due_date', label: 'Target date', type: 'date' }],
    onSubmit: async (v) => { const r = await post('/api/assessments', { framework_id: f.id, name: v.name, workspace_id: v.workspace_id || undefined, due_date: v.due_date || undefined }); toast(`Assessment created with ${r.items} items`, 'ok'); location.hash = `#/assessments/${r.id}`; } });
}

function clone(f, reload) {
  formModal({ title: `Create editable copy of ${f.name}`, intro: 'Your copy is versioned and owned by your organization, so you can import official/licensed requirement text or adapt controls.',
    fields: [{ name: 'version', label: 'Version label', required: true, value: `${f.version}-org` }],
    onSubmit: async (v) => { const r = await post(`/api/frameworks/${f.id}/clone`, v); toast('Copy created', 'ok'); location.hash = `#/frameworks/${r.id}`; reload(); } });
}

function newCustom(reload) {
  formModal({ title: 'New custom framework', wide: true, fields: [{ name: 'key', label: 'Short key', required: true, hint: 'e.g. internal-baseline' }, { name: 'name', label: 'Name', required: true }, { name: 'version', label: 'Version', value: '1' }, { name: 'publisher', label: 'Owner / publisher' },
    { name: 'description', label: 'Description', type: 'textarea', full: true }, { name: 'csv', label: 'Controls (optional CSV)', type: 'textarea', full: true, rows: 6, hint: 'Header: ref_id,title,domain,requirement_text,evidence_requirements,test_procedure. You can also import later.' }],
    onSubmit: async (v) => {
      const f = await post('/api/frameworks', { key: v.key, name: v.name, version: v.version || '1', publisher: v.publisher, description: v.description });
      if (v.csv.trim()) await post(`/api/frameworks/${f.id}/import`, { csv: v.csv });
      toast('Framework created', 'ok'); resetLookups(); location.hash = `#/frameworks/${f.id}`; reload();
    } });
}

export function renderFramework(root, id, setCrumbs) {
  const host = h('div', {}, loading());
  root.append(host);
  let q = '';
  async function load() {
    try {
      const f = await get(`/api/frameworks/${id}`, { q });
      setCrumbs(['Frameworks', f.name]);
      clear(host);
      const own = !f.global_template;
      host.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, `${f.name} ${f.version}`), h('p', {}, f.description), h('div', { class: 'row', style: { marginTop: '6px' } }, sourceChip(f.source_status), chip(f.jurisdiction || 'n/a'), chip(`${f.control_count} controls`))),
        h('div', { class: 'actions' }, can('manage') && f.control_count ? h('button', { class: 'btn primary', onClick: () => startAssessment(f) }, 'Start assessment') : null,
          can('manage') && f.global_template ? h('button', { class: 'btn', onClick: () => clone(f, () => {}) }, 'Create editable copy') : null,
          can('manage') && own ? h('button', { class: 'btn', onClick: () => importCsv(f, load) }, 'Import controls (CSV)') : null,
          can('manage') && own ? h('button', { class: 'btn danger', onClick: async () => { if (await confirmDialog('Delete this framework copy and its organization-specific mappings?', { danger: true, confirmText: 'Delete' })) { try { await del(`/api/frameworks/${id}`); toast('Deleted', 'ok'); location.hash = '#/frameworks'; } catch (e) { fail(e); } } } }, 'Delete') : null)),
        f.source_status === 'template_unverified' ? h('div', { class: 'callout warn' }, 'Requirement text is not loaded. Suggested evidence below is generic guidance generated by the platform, not text from the standard or law.') : null,
        h('div', { class: 'filters', style: { marginTop: '14px' } }, h('input', { class: 'input', type: 'search', placeholder: 'Filter controls...', value: q, onChange: (e) => { q = e.target.value; load(); } })),
        f.controls.length ? table([
          { key: 'ref_id', label: 'ID' }, { key: 'title', label: 'Title' }, { key: 'domain', label: 'Domain' },
          { label: 'Requirement text', render: (c) => (c.has_requirement_text ? chip('loaded', 'ok') : chip('not loaded', 'warn')) },
          { label: 'Suggested evidence', render: (c) => h('span', { class: 'small' }, (c.evidence_requirements || []).slice(0, 3).join('; ')) },
          { label: 'Mappings', render: (c) => h('button', { class: 'btn sm', onClick: () => showMappings(c, f) }, `${c.mapping_count} mapping${c.mapping_count === 1 ? '' : 's'}`) }], f.controls)
          : empty('No controls loaded', 'This framework entry has no control list. Create an editable copy and import your licensed control list as CSV.'));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  load();
}

function importCsv(f, reload) {
  formModal({ title: `Import controls into ${f.name}`, wide: true, intro: 'Rows are matched on ref_id: existing controls are updated, new ones are added. Only import text you are licensed to use.',
    fields: [{ name: 'csv', label: 'CSV content', type: 'textarea', required: true, rows: 10, full: true, hint: 'ref_id,title,domain,requirement_text,evidence_requirements (semicolon separated),test_procedure' },
      { name: 'file', label: 'or choose a .csv file', type: 'file', accept: '.csv,text/csv', full: true }, { name: 'mark_as_licensed_text', label: 'I confirm this is official or licensed requirement text (marks the framework as imported)', type: 'checkbox', full: true }],
    onSubmit: async (v) => { const text = v.file ? await v.file.text() : v.csv; const r = await post(`/api/frameworks/${f.id}/import`, { csv: text, mark_as_licensed_text: v.mark_as_licensed_text }); toast(`Imported ${r.imported} controls`, 'ok'); reload(); } });
}

async function showMappings(c, f) {
  const body = h('div', {}, loading());
  const m = modal(`Mappings for ${c.ref_id} ${c.title}`, body, { wide: true });
  async function load() {
    try {
      const d = await get('/api/mappings', { framework_control_id: c.id });
      clear(body).append(h('div', { class: 'callout warn' }, d.note), d.items.length ? table([
        { label: 'Mapped control', render: (x) => { const o = x.from_id === c.id ? x.to : x.from; return h('span', {}, h('strong', {}, o.ref_id), ` ${o.title} `, h('span', { class: 'muted' }, `(${o.framework})`)); } }, { label: 'Relation', render: (x) => label(x.relation) },
        { label: 'Source', render: (x) => chip(x.source) }, { label: 'Status', render: (x) => status(x.status === 'template' ? 'pending' : x.status) },
        { label: '', render: (x) => (can('manage') ? h('div', { class: 'row' }, h('button', { class: 'btn sm', onClick: () => decide(x, 'approved') }, 'Approve'), h('button', { class: 'btn sm danger', onClick: () => decide(x, 'rejected') }, 'Reject')) : null) }], d.items) : h('p', { class: 'muted' }, 'No mappings recorded for this control.'),
      can('manage') ? h('button', { class: 'btn', style: { marginTop: '12px' }, onClick: addMapping }, '+ Add mapping') : null);
    } catch (e) { clear(body).append(errorState(e, load)); }
  }
  async function decide(x, decision) { try { await post('/api/mappings/decide', { from_id: x.from_id, to_id: x.to_id, decision }); toast(`Mapping ${decision} for your organization`, 'ok'); load(); } catch (e) { fail(e); } }
  function addMapping() {
    formModal({ title: 'Add mapping', fields: [{ name: 'framework', label: 'Target framework', type: 'select', required: true, options: frameworkOptions },
      { name: 'to_id', label: 'Target control', type: 'select', required: true, options: [] }, { name: 'relation', label: 'Relation', type: 'select', required: true, options: [{ value: 'related', label: 'Related' }, { value: 'equivalent', label: 'Equivalent' }, { value: 'partial', label: 'Partial' }], value: 'related' }, { name: 'note', label: 'Note', full: true }],
      onSubmit: async (v) => { await post('/api/mappings', { from_id: c.id, to_id: v.to_id, relation: v.relation, note: v.note }); toast('Mapping added', 'ok'); load(); } });
    const dlg = document.querySelector('.overlay:last-of-type');
    const fwSel = dlg.querySelector('select[name=framework]'); const toSel = dlg.querySelector('select[name=to_id]');
    fwSel.addEventListener('change', async () => { clear(toSel); if (!fwSel.value) return; const d = await get(`/api/frameworks/${fwSel.value}`); d.controls.forEach((x) => toSel.append(h('option', { value: x.id }, `${x.ref_id} - ${x.title}`))); });
  }
  load();
  return m;
}

// ---------------------------------------------------------------- assessments
export function renderAssessments(root) {
  const host = h('div', {}, loading());
  root.append(host);
  async function load() {
    try {
      const d = await get('/api/assessments');
      clear(host);
      host.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Compliance assessments'), h('p', {}, 'Each assessment tracks one framework version. Results can only be set by reviewers, and "compliant" requires linked, approved, unexpired evidence.')),
        can('manage') ? h('a', { class: 'btn primary', href: '#/frameworks' }, '+ New assessment') : null),
        d.items.length ? table([{ label: 'Assessment', render: (a) => h('a', { href: `#/assessments/${a.id}` }, a.name) }, { key: 'framework_name', label: 'Framework' }, { label: 'Content', render: (a) => sourceChip(a.framework_source_status) }, { label: 'Status', render: (a) => status(a.status) },
          { label: 'Score', render: (a) => h('div', { style: { minWidth: '120px' } }, `${a.score.assessment_score ?? '-'}`, meter(a.score.assessment_score, scoreColor(a.score.assessment_score))) },
          { label: 'Evidence coverage', render: (a) => `${a.score.evidence_coverage ?? '-'}%` }, { label: 'Target', render: (a) => fmtDate(a.due_date) }], d.items)
          : empty('No assessments yet', 'Pick a framework from the library to start your first assessment.', h('a', { class: 'btn primary', href: '#/frameworks' }, 'Browse frameworks')));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  load();
}

export function renderAssessment(root, id, setCrumbs) {
  const host = h('div', {}, loading());
  root.append(host);
  let list;
  async function load() {
    try {
      const a = await get(`/api/assessments/${id}`);
      setCrumbs(['Assessments', a.name]);
      const s = a.score;
      clear(host);
      list = listView({
        endpoint: `/api/assessments/${id}/items`, pageSize: 25, filters: [{ name: 'result', label: 'Result', options: ['not_assessed', 'compliant', 'partial', 'non_compliant'] }, { name: 'applicability', label: 'Applicability', options: ['applicable', 'not_applicable'] }],
        emptyTitle: 'No requirements match', onRow: (r) => assess(r, load),
        columns: [{ key: 'ref_id', label: 'ID' }, { label: 'Requirement', render: (r) => h('div', {}, r.title, h('div', { class: 'small muted' }, r.domain)) }, { label: 'Applicability', render: (r) => (r.applicability === 'applicable' ? 'Applicable' : chip('not applicable')) },
          { label: 'Linked control', render: (r) => (r.control_id ? h('a', { href: `#/controls/${r.control_id}`, onClick: (e) => e.stopPropagation() }, r.control_key) : chip('unlinked', 'warn')) },
          { label: 'Control health', render: (r) => (r.control_health ? status(r.control_health) : '-') }, { label: 'Evidence', num: true, key: 'evidence_count' }, { label: 'Result', render: (r) => status(r.result) }],
      });
      host.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, a.name), h('p', {}, `${a.framework_name} - `, sourceChip(a.framework_source_status))),
        h('div', { class: 'actions' }, can('manage') ? h('button', { class: 'btn', onClick: autoLink }, 'Create / reuse controls') : null, can('ai') ? h('button', { class: 'btn', onClick: suggestLinks }, 'AI: suggest links') : null,
          h('button', { class: 'btn', onClick: report }, 'Generate report'), can('manage') ? h('button', { class: 'btn danger', onClick: remove }, 'Delete') : null)),
        h('div', { class: 'grid g4' }, kpiBox('Assessment score', s.assessment_score ?? '-', a.formula), kpiBox('Evidence coverage', `${s.evidence_coverage ?? '-'}%`, 'Applicable requirements whose linked control has approved, unexpired evidence'),
          kpiBox('Compliant / partial', `${s.counts.compliant} / ${s.counts.partial}`, `${s.counts.non_compliant} non-compliant, ${s.counts.not_assessed} not assessed`), kpiBox('Linked controls', `${s.linked_controls}/${s.applicable}`, `${s.not_applicable} not applicable`)),
        h('div', { style: { marginTop: '16px' } }, list));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  const kpiBox = (t, v, sub) => h('div', { class: 'card kpi' }, h('div', { class: 'label' }, t), h('div', { class: 'value' }, v), h('div', { class: 'sub' }, sub));
  async function autoLink() {
    if (!(await confirmDialog('Create an internal control for each unlinked requirement, reusing controls already linked to mapped requirements in other assessments?', { confirmText: 'Create / reuse' }))) return;
    try { const r = await post(`/api/assessments/${id}/auto-link`); resetLookups(); toast(`${r.created_controls} controls created, ${r.reused_controls} reused across frameworks`, 'ok'); load(); } catch (e) { fail(e); }
  }
  async function suggestLinks() {
    try {
      const r = await post('/api/ai/suggest-links', { assessment_id: Number(id) });
      modal('AI-suggested control links', h('div', {}, h('div', { class: 'callout' }, r.disclaimer), r.suggestions.length ? table([{ key: 'requirement', label: 'Requirement' }, { label: 'Suggested control', render: (x) => `${x.suggested_control} - ${x.control_title}` }, { label: 'Confidence', render: (x) => x.confidence },
        { label: '', render: (x) => h('div', { class: 'row' }, h('button', { class: 'btn sm primary', onClick: async (e) => { try { await post(`/api/ai/suggestions/${x.suggestion_id}/decision`, { decision: 'accepted' }); e.target.closest('tr').remove(); load(); } catch (err) { fail(err); } } }, 'Accept'),
          h('button', { class: 'btn sm', onClick: async (e) => { await post(`/api/ai/suggestions/${x.suggestion_id}/decision`, { decision: 'dismissed' }); e.target.closest('tr').remove(); } }, 'Dismiss')) }], r.suggestions) : empty('No suggestions', 'No unlinked requirement resembled an existing control closely enough.')), { wide: true });
    } catch (e) { fail(e); }
  }
  async function report() { try { const r = await post('/api/reports/generate', { kind: 'assessment', assessment_id: Number(id) }); toast('Report generated', 'ok'); location.hash = `#/reports/${r.id}`; } catch (e) { fail(e); } }
  async function remove() { if (await confirmDialog('Delete this assessment and all of its results?', { danger: true, confirmText: 'Delete' })) { try { await del(`/api/assessments/${id}`); toast('Assessment deleted', 'ok'); location.hash = '#/assessments'; } catch (e) { fail(e); } } }
  load();
}

function assess(row, reload) {
  const writable = can('manage');
  formModal({ title: `${row.ref_id} ${row.title}`, wide: true, submitText: 'Save assessment', intro: row.requirement_text ? undefined : 'Requirement text is not loaded for this framework; refer to your licensed copy of the standard.',
    initial: row,
    fields: [{ name: 'applicability', label: 'Applicability', type: 'select', required: true, options: [{ value: 'applicable', label: 'Applicable' }, { value: 'not_applicable', label: 'Not applicable' }] },
      { name: 'control_id', label: 'Implementing control', type: 'select', options: controlOptions, hint: 'Evidence attached to the control counts for every requirement it implements.' },
      { name: 'justification', label: 'Justification (required for not applicable)', type: 'textarea', full: true },
      { name: 'result', label: 'Result (reviewer decision)', type: 'select', required: true, options: ['not_assessed', 'compliant', 'partial', 'non_compliant'].map((v) => ({ value: v, label: label(v) })), hint: 'Compliant needs a linked control with approved, unexpired evidence. AI cannot set this.' },
      { name: 'notes', label: 'Reviewer notes', type: 'textarea', full: true }],
    onSubmit: async (v) => {
      if (!writable) throw new Error('Your role is read-only for assessments');
      await patch(`/api/assessment-items/${row.id}`, { applicability: v.applicability, justification: v.justification, result: v.result, notes: v.notes, control_id: v.control_id === '' ? null : v.control_id });
      toast('Saved', 'ok'); reload();
    } });
}
