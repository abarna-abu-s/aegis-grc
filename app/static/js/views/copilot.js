import { get, post } from '../api.js';
import { can } from '../state.js';
import { aiChip, chip, clear, empty, errorState, fail, fmtDate, h, label, loading, meter, status, table, toast } from '../ui.js';
import { tabbed } from './common.js';

export function render(root, _id, _setCrumbs, tab) {
  const statusBar = h('div', {});
  get('/api/ai/status').then((s) => statusBar.append(s.ai_enabled ? h('div', { class: 'callout' }, `AI provider: ${s.provider} (${s.model}). Answers are generated only from retrieved sources.`)
    : h('div', { class: 'callout warn' }, s.fallback, ' Retrieval, citations, mapping suggestions, gap analysis and reports still work using deterministic methods.'))).catch(() => {});
  root.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'AI compliance copilot'), h('p', {}, 'Answers cite your approved policies, evidence and documents. Everything here is a suggestion for a person to review; the copilot never marks a control compliant.'))), statusBar,
    tabbed([['ask', 'Ask', ask], ['questionnaire', 'Questionnaire assistant', questionnaire], ['review', 'Suggestions to review', reviewQueue], ['dupes', 'Duplicate evidence', dupes], ['fresh', 'Freshness check', fresh], ['risk', 'Risk summary', riskSummary]], tab));
}

function sourceCards(sources) {
  return sources.map((s, i) => h('div', { class: 'cite' }, h('strong', {}, `[${i + 1}] ${s.title}`), ' ', chip(s.source_type), chip(`v${s.version}`), chip(`relevance ${s.score}`), s.verified_framework_text ? chip('org-imported framework text', 'info') : null, h('div', { class: 'small', style: { marginTop: '4px' } }, s.excerpt.slice(0, 420) + (s.excerpt.length > 420 ? '...' : ''))));
}

function ask() {
  const log = h('div', { class: 'chat' }, h('div', { class: 'msg' }, 'Ask about your policies, evidence or documents - for example "How often do we review privileged access?" Only passages you are permitted to see are retrieved.'));
  const input = h('input', { class: 'input', placeholder: 'Ask a compliance question...', maxlength: 1000, 'aria-label': 'Question' });
  const go = async () => {
    const q = input.value.trim(); if (q.length < 3) return;
    input.value = ''; log.append(h('div', { class: 'msg user' }, q)); const wait = h('div', { class: 'msg' }, loading('Searching your sources...')); log.append(wait);
    try {
      const r = await post('/api/ai/ask', { question: q });
      wait.replaceWith(h('div', { class: 'msg' }, h('div', { class: 'row' }, aiChip(), chip(r.mode === 'llm' ? 'model-written' : r.mode === 'extractive' ? 'retrieved passages only' : 'no sources', r.mode === 'no_sources' ? 'warn' : ''), r.confidence ? chip(`retrieval confidence ${r.confidence}`) : null),
        h('div', { style: { margin: '8px 0', whiteSpace: 'pre-wrap' } }, r.answer), r.sources.length ? h('div', {}, h('strong', { class: 'small' }, 'Sources'), sourceCards(r.sources)) : null, h('div', { class: 'small muted' }, r.disclaimer)));
    } catch (e) { wait.replaceWith(h('div', { class: 'msg' }, errorState(e))); }
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  return h('div', { class: 'card' }, log, h('div', { class: 'row', style: { marginTop: '12px' } }, input, h('button', { class: 'btn primary', onClick: go }, 'Ask')));
}

function questionnaire() {
  const ta = h('textarea', { class: 'input', rows: 6, placeholder: 'Paste questionnaire questions, one per line...', 'aria-label': 'Questions' });
  const out = h('div', {});
  return h('div', { class: 'card' }, h('p', { class: 'muted' }, 'Drafts answers from your approved policies and documents. Every answer stays "needs review" and shows its sources; unanswerable questions are flagged rather than guessed.'), ta,
    h('button', { class: 'btn primary', style: { margin: '10px 0' }, onClick: async () => {
      clear(out).append(loading());
      try { const r = await post('/api/ai/questionnaire', { questions: ta.value });
        clear(out).append(table([{ key: 'question', label: 'Question' }, { label: 'Draft answer', render: (a) => (a.draft_answer ? h('div', {}, a.draft_answer.slice(0, 400)) : chip(a.note || 'No source', 'warn')) }, { label: 'Confidence', render: (a) => a.confidence ?? '-' },
          { label: 'Sources', render: (a) => a.sources.map((s) => s.title).join(', ') || '-' }, { label: 'Status', render: (a) => status('pending') }], r.answers), h('p', { class: 'small muted' }, r.disclaimer)); } catch (e) { clear(out).append(errorState(e)); }
    } }, 'Draft answers'), out);
}

function reviewQueue() {
  const host = h('div', {}, loading());
  async function load() {
    try {
      const d = await get('/api/ai/suggestions');
      clear(host).append(d.items.length ? table([{ label: 'Kind', render: (s) => chip(s.kind) }, { label: 'Target', render: (s) => `${label(s.target_type)} #${s.target_id}` }, { label: 'Summary', render: (s) => h('span', { class: 'small' }, summary(s)) }, { label: 'Confidence', render: (s) => s.confidence ?? '-' }, { label: 'Created', render: (s) => fmtDate(s.created_at) },
        { label: '', render: (s) => (can('manage') || can('contribute') ? h('div', { class: 'row' }, ['evidence_link', 'control_mapping', 'framework_mapping'].includes(s.kind) ? h('button', { class: 'btn sm primary', onClick: () => decide(s, 'accepted') }, 'Accept') : null, h('button', { class: 'btn sm', onClick: () => decide(s, 'dismissed') }, 'Dismiss')) : null) }], d.items)
        : empty('Nothing waiting for review', 'AI and heuristic suggestions you generate elsewhere (evidence classification, control links, gap analyses) are listed here until a person accepts or dismisses them.'));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  const summary = (s) => (s.kind === 'evidence_link' ? `Top candidate: ${s.payload.candidates?.[0]?.control_key || 'none'}` : s.kind === 'control_mapping' ? `${s.payload.requirement} -> ${s.payload.control_key}` : s.kind === 'policy_gap' ? s.payload.summary : s.kind === 'remediation_draft' ? `${s.payload.steps?.length} draft steps` : '');
  async function decide(s, decision) {
    try { const body = { decision }; if (decision === 'accepted' && s.kind === 'evidence_link') { const c = s.payload.candidates?.[0]; if (!c) throw new Error('No candidate'); body.chosen_control_id = c.control_id; }
      const r = await post(`/api/ai/suggestions/${s.id}/decision`, body); toast(r.result, 'ok'); load(); } catch (e) { fail(e); }
  }
  load(); return host;
}

const lazy = (path, build) => { const host = h('div', {}, loading()); get(path).then((d) => clear(host).append(build(d))).catch((e) => clear(host).append(errorState(e))); return host; };
const dupes = () => lazy('/api/ai/duplicates', (d) => (d.exact.length + d.near.length ? h('div', { class: 'card' }, h('div', { class: 'callout' }, d.disclaimer),
  table([{ label: 'Evidence', render: (x) => x.evidence_ids.map((i) => h('a', { href: `#/evidence/${i}`, style: { marginRight: '8px' } }, `#${i}`)) }, { key: 'reason', label: 'Why flagged' }], [...d.exact, ...d.near])) : empty('No duplicates found', 'No identical files or near-identical extracted text were detected.')));
const fresh = () => lazy('/api/ai/freshness', (d) => (d.items.length ? h('div', { class: 'card' }, h('p', { class: 'muted' }, d.disclaimer), table([{ label: 'Evidence', render: (x) => h('a', { href: `#/evidence/${x.evidence_id}` }, x.title) }, { label: 'Issue', render: (x) => status(x.status.startsWith('no_expiry') ? 'pending' : x.status) }, { label: 'Detail', render: (x) => (x.status.startsWith('no_expiry') ? 'No expiry set and older than a year' : x.valid_until ? `Valid until ${fmtDate(x.valid_until)}` : 'Awaiting review') }], d.items)) : empty('All evidence is fresh', 'No expired, expiring or unreviewed items.')));
const riskSummary = () => {
  const host = h('div', { class: 'card' }, h('p', { class: 'muted' }, 'Summarises the saved risk register. Facts come from the database; a model (if configured) only rephrases them.'), h('button', { class: 'btn primary', onClick: async () => {
    const out = host.querySelector('.out'); clear(out).append(loading());
    try { const r = await post('/api/ai/risk-summary'); clear(out).append(h('div', { class: 'row' }, aiChip(), chip(r.mode)), h('p', {}, r.summary), r.risks_without_plan.length ? h('div', { class: 'callout warn' }, 'High/critical risks without a treatment plan: ', r.risks_without_plan.map((x) => x.title).join('; ')) : null, h('p', { class: 'small muted' }, r.disclaimer)); } catch (e) { clear(out).append(errorState(e)); }
  } }, 'Summarise risks'), h('div', { class: 'out', style: { marginTop: '12px' } }));
  return host;
};
void meter;
