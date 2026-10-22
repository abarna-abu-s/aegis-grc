import { get, post } from '../api.js';
import { can, controlOptions, frameworkOptions } from '../state.js';
import { PALETTE, chip, clear, empty, errorState, fail, fmtDate, formModal, h, label, lineChart, loading, meter, scoreColor, status, svg, table, toast } from '../ui.js';
import { tabbed } from './common.js';

const lazy = (fn) => { const host = h('div', {}, loading()); fn().then((el) => clear(host).append(el)).catch((e) => clear(host).append(errorState(e))); return host; };

export function render(root, _id, _setCrumbs, tab) {
  root.append(h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Compliance insights'), h('p', {}, 'Live analytics over your saved records: scorecards with visible formulas, control graph, calendar, recommendations, priority matrix and a what-if simulator.'))),
    tabbed([['scorecards', 'Scorecards & trends', scorecards], ['graph', 'Control graph', graph], ['calendar', 'Calendar', calendar], ['recommendations', 'Recommendations', recommendations], ['priority', 'Remediation priority', priority], ['simulator', 'Posture simulator', simulator]], tab));
}

function scorecards() {
  return lazy(async () => {
    const d = await get('/api/insights/scorecards');
    const r = d.current;
    const colors = [PALETTE.brown, PALETTE.caramel, PALETTE.info, PALETTE.ok, PALETTE.warn];
    const series = Object.entries(d.series).map(([k, pts], i) => ({ label: k === 'readiness' ? 'Audit readiness' : d.names[k] || k, color: colors[i % colors.length], points: pts }));
    return h('div', { class: 'stack' }, h('div', { class: 'grid g-main' }, h('div', { class: 'card' }, h('h3', {}, 'Score trend'), lineChart(series), h('p', { class: 'small muted' }, d.note)),
      h('div', { class: 'card' }, h('h3', {}, `Audit readiness: ${r.score}`), h('p', { class: 'small muted' }, r.formula), table([{ label: 'Component', render: (c) => label(c[0]) }, { label: 'Value', render: (c) => c[1].value ?? 'no data' }, { label: 'Weight', render: (c) => `${c[1].weight * 100}%` }, { label: 'Points', render: (c) => c[1].contribution }], Object.entries(r.components)))),
      h('div', { class: 'card' }, h('h3', {}, 'Framework scorecards'), d.assessments.length ? table([{ label: 'Assessment', render: (a) => h('a', { href: `#/assessments/${a.assessment_id}` }, a.name) }, { label: 'Score', render: (a) => h('div', { style: { minWidth: '140px' } }, a.assessment_score ?? '-', meter(a.assessment_score, scoreColor(a.assessment_score))) },
        { label: 'Evidence coverage', render: (a) => `${a.evidence_coverage ?? '-'}%` }, { label: 'Compliant', num: true, render: (a) => a.counts.compliant }, { label: 'Partial', num: true, render: (a) => a.counts.partial }, { label: 'Non-compliant', num: true, render: (a) => a.counts.non_compliant }, { label: 'Not assessed', num: true, render: (a) => a.counts.not_assessed }], d.assessments)
        : empty('No assessments', 'Scorecards appear once an assessment exists.')));
  });
}

function graph() {
  return lazy(async () => {
    const g = await get('/api/insights/graph');
    if (!g.nodes.length) return empty('Nothing to graph', 'Start an assessment and link controls to see how requirements, controls and frameworks connect.');
    const groups = [...new Set(g.nodes.filter((n) => n.type === 'framework').map((n) => n.group))];
    const cols = [...groups]; cols.splice(Math.ceil(groups.length / 2), 0, 'Internal controls');
    const pos = {}; const count = {};
    g.nodes.forEach((n) => { const ci = cols.indexOf(n.group); count[ci] = (count[ci] || 0) + 1; pos[n.id] = { x: 90 + ci * 250, y: 50 + (count[ci] - 1) * 30 }; });
    const H = Math.max(...Object.values(count)) * 30 + 70; const W = cols.length * 250 + 40;
    const color = { healthy: PALETTE.ok, at_risk: PALETTE.warn, failing: PALETTE.bad, no_evidence: PALETTE.neutral };
    const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', class: 'chart', role: 'img', 'aria-label': 'Cross-framework control graph' });
    cols.forEach((c, i) => s.append(svg('text', { x: 90 + i * 250, y: 22, 'text-anchor': 'middle', style: 'font-weight:700' }, c.length > 30 ? c.slice(0, 28) + '...' : c)));
    const edgeEls = g.edges.map((e) => { const a = pos[e.from], b = pos[e.to]; if (!a || !b) return null; const mx = (a.x + b.x) / 2; const el = svg('path', { d: `M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}`, fill: 'none', stroke: e.kind === 'implements' ? PALETTE.brown : PALETTE.caramel, 'stroke-width': 1.3, 'stroke-dasharray': e.kind.endsWith('template') ? '4 3' : null, opacity: 0.55 }); s.append(el); return { e, el }; }).filter(Boolean);
    const nodeEls = g.nodes.map((n) => { const p = pos[n.id]; const fill = n.type === 'control' ? color[n.health] || PALETTE.neutral : 'var(--surface)'; const grp = svg('g', { class: 'graph-node', tabindex: 0 }, svg('title', {}, `${n.label}: ${n.title}${n.health ? ` (${label(n.health)})` : ''}`),
      svg('circle', { cx: p.x, cy: p.y, r: 8, fill, stroke: n.type === 'control' ? 'none' : PALETTE.brown, 'stroke-width': 1.6 }), svg('text', { x: p.x + 13, y: p.y + 4, style: 'font-size:11px' }, n.label.length > 22 ? n.label.slice(0, 20) + '...' : n.label)); s.append(grp);
      const focus = () => { const near = new Set([n.id]); g.edges.forEach((e) => { if (e.from === n.id) near.add(e.to); if (e.to === n.id) near.add(e.from); }); edgeEls.forEach(({ e, el }) => el.setAttribute('opacity', e.from === n.id || e.to === n.id ? 1 : 0.07)); nodeEls.forEach(({ n: m, grp: gg }) => gg.setAttribute('opacity', near.has(m.id) ? 1 : 0.2)); };
      const reset = () => { edgeEls.forEach(({ el }) => el.setAttribute('opacity', 0.55)); nodeEls.forEach(({ grp: gg }) => gg.setAttribute('opacity', 1)); };
      grp.addEventListener('mouseenter', focus); grp.addEventListener('focus', focus); grp.addEventListener('mouseleave', reset); grp.addEventListener('blur', reset);
      return { n, grp }; });
    return h('div', { class: 'card' }, h('p', { class: 'muted' }, 'Internal controls (centre, coloured by health) implement requirements from each framework. Dashed lines are unverified template mappings; solid caramel lines are mappings your organization approved. Hover a node to isolate its connections.'),
      h('div', { style: { overflowX: 'auto' } }, s), h('div', { class: 'legend' }, ...Object.entries(color).map(([k, c]) => h('span', {}, h('i', { style: { background: c } }), label(k))), h('span', {}, h('i', { style: { background: 'var(--surface)', border: `2px solid ${PALETTE.brown}` } }), 'Framework requirement')));
  });
}

function calendar() {
  return lazy(async () => {
    const d = await get('/api/insights/calendar');
    if (!d.items.length) return empty('Nothing scheduled', 'Evidence expiry, reviews, task due dates, audits and vendor assessments appear here as you record them.');
    const today = new Date().toISOString().slice(0, 10);
    const types = [...new Set(d.items.map((e) => e.type))];
    const host = h('div', { class: 'card cal-list' });
    const sel = h('select', { class: 'input', 'aria-label': 'Event type', style: { maxWidth: '260px', marginBottom: '10px' }, onChange: draw }, h('option', { value: '' }, 'All event types'), types.map((t) => h('option', { value: t }, label(t))));
    const list = h('div', {});
    function draw() {
      clear(list); let month = '';
      d.items.filter((e) => !sel.value || e.type === sel.value).forEach((e) => {
        const m = new Date(e.date + 'T00:00:00').toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
        if (m !== month) { month = m; list.append(h('h3', { style: { margin: '14px 0 4px' } }, m)); }
        list.append(h('div', { class: 'day' }, h('div', {}, fmtDate(e.date), e.date < today ? h('div', {}, chip('past', 'bad')) : null), h('div', {}, h('a', { href: `#/${e.link}` }, e.title), ' ', chip(e.type))));
      });
    }
    draw(); host.append(sel, list); return host;
  });
}

function recommendations() {
  return lazy(async () => {
    const d = await get('/api/insights/recommendations');
    const host = h('div', { class: 'card' });
    host.append(h('p', { class: 'muted' }, 'Generated by rules over your current data (not by a language model). Creating a task persists it to your task list.'));
    host.append(d.items.length ? table([{ label: 'Priority', render: (r) => status(r.priority) }, { key: 'title', label: 'Recommended action' }, { key: 'reason', label: 'Why' },
      { label: '', render: (r) => (r.already_tasked ? chip('task exists', 'ok') : can('contribute') ? h('button', { class: 'btn sm primary', onClick: async (ev) => { try { await post('/api/insights/recommendations/create-task', { key: r.key }); toast('Task created', 'ok'); ev.target.replaceWith(chip('task exists', 'ok')); } catch (e) { fail(e); } } }, 'Create task') : null) }], d.items)
      : empty('No recommendations', 'No expired evidence, unowned controls, overdue policies or other gaps were detected.'));
    return host;
  });
}

function priority() {
  return lazy(async () => {
    const d = await get('/api/insights/priority-matrix');
    if (!d.items.length) return empty('Nothing to prioritise', 'Open findings, risks and failing controls are plotted here by impact and urgency.');
    const W = 560, H = 380, L = 44, B = 36;
    const s = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', class: 'chart', role: 'img', 'aria-label': 'Remediation priority matrix' });
    const x = (u) => L + ((u - 0.5) / 5) * (W - L - 10), y = (i) => 10 + (1 - (i - 0.5) / 5) * (H - B - 10);
    s.append(svg('rect', { x: x(3.5), y: y(5.5), width: x(5.5) - x(3.5), height: y(3.5) - y(5.5), fill: PALETTE.bad, opacity: 0.1 }));
    for (let i = 1; i <= 5; i++) s.append(svg('line', { x1: x(i), x2: x(i), y1: 10, y2: H - B, class: 'gridline' }), svg('line', { x1: L, x2: W - 10, y1: y(i), y2: y(i), class: 'gridline' }), svg('text', { x: x(i), y: H - B + 16, 'text-anchor': 'middle' }, i), svg('text', { x: L - 10, y: y(i) + 4, 'text-anchor': 'end' }, i));
    s.append(svg('text', { x: W / 2, y: H - 4, 'text-anchor': 'middle' }, 'Urgency'), svg('text', { x: 12, y: H / 2, transform: `rotate(-90 12 ${H / 2})`, 'text-anchor': 'middle' }, 'Impact'));
    const kinds = { finding: PALETTE.bad, risk: PALETTE.caramel, control: PALETTE.info }; const seen = {};
    d.items.forEach((it) => { const k = `${it.urgency},${it.impact}`; const n = seen[k] = (seen[k] || 0) + 1; const a = n * 1.7; s.append(svg('circle', { cx: x(it.urgency) + Math.cos(a) * (n > 1 ? 12 : 0), cy: y(it.impact) + Math.sin(a) * (n > 1 ? 12 : 0), r: 7, fill: kinds[it.type], opacity: 0.85, stroke: '#fff' }, svg('title', {}, `${label(it.type)}: ${it.title} (${it.detail})`))); });
    return h('div', { class: 'grid g-main' }, h('div', { class: 'card' }, s, h('div', { class: 'legend' }, ...Object.entries(kinds).map(([k, c]) => h('span', {}, h('i', { style: { background: c } }), label(k))), h('span', {}, 'Shaded area = act first')), h('p', { class: 'small muted' }, d.method)),
      h('div', { class: 'card' }, h('h3', {}, 'Ranked list'), h('ul', { class: 'clean' }, [...d.items].sort((a, b) => b.impact * b.urgency - a.impact * a.urgency).map((it) => h('li', {}, h('div', {}, it.title), h('div', { class: 'small muted' }, `${label(it.type)} - impact ${it.impact}, urgency ${it.urgency} - ${it.detail}`))))));
  });
}

function simulator() {
  const host = h('div', {}, loading());
  Promise.all([controlOptions(), frameworkOptions()]).then(([ctrls, fws]) => {
    const sel = h('select', { multiple: true, size: 8, class: 'input', 'aria-label': 'Controls to assume compliant', style: { width: '100%' } }, ctrls.map((c) => h('option', { value: c.value }, c.label)));
    const renew = h('input', { type: 'checkbox' }); const close = h('input', { type: 'checkbox' });
    const fw = h('select', { class: 'input', 'aria-label': 'Framework to adopt' }, h('option', { value: '' }, 'None'), fws.map((f) => h('option', { value: f.value }, f.label)));
    const out = h('div', {}, h('p', { class: 'muted' }, 'Run a scenario to see results.'));
    clear(host).append(h('div', { class: 'grid g2' }, h('div', { class: 'card' }, h('h3', {}, 'Scenario'), h('p', { class: 'muted' }, 'Pick hypothetical changes and see the projected readiness. Nothing is saved or changed.'),
      h('label', { class: 'field' }, 'Assume these controls become fully evidenced and compliant', sel), h('label', { class: 'checkbox' }, renew, 'Renew all expired / expiring evidence'), h('label', { class: 'checkbox', style: { margin: '8px 0' } }, close, 'Close all open findings'),
      h('label', { class: 'field' }, 'Adopt another framework (reuse potential)', fw), h('button', { class: 'btn primary', onClick: async () => {
        clear(out).append(loading('Simulating...'));
        try {
          const r = await post('/api/insights/simulate', { assume_compliant_controls: [...sel.selectedOptions].map((o) => Number(o.value)), renew_expired_evidence: renew.checked, close_findings: close.checked, add_framework_id: fw.value || undefined });
          clear(out).append(h('div', { class: 'callout' }, r.disclaimer), h('div', { class: 'grid g3', style: { marginTop: '12px' } }, kpi('Current', r.before.score), kpi('Projected', r.after.score), kpi('Change', `${r.delta >= 0 ? '+' : ''}${r.delta}`)),
            table([{ label: 'Component', render: (c) => label(c[0]) }, { label: 'Now', render: (c) => c[1].value ?? '-' }, { label: 'Projected', render: (c) => r.after.components[c[0]].value ?? '-' }], Object.entries(r.before.components)),
            r.new_framework ? h('div', { class: 'callout warn', style: { marginTop: '12px' } }, `${r.new_framework.with_mapped_compliant_control} of ${r.new_framework.controls} requirements in the selected framework map to a control you already assessed as compliant. ${r.new_framework.note}`) : null);
        } catch (e) { clear(out).append(errorState(e)); }
      } }, 'Run simulation')), h('div', { class: 'card' }, h('h3', {}, 'Projected outcome'), out)));
  }).catch((e) => clear(host).append(errorState(e)));
  return host;
}
const kpi = (t, v) => h('div', { class: 'card kpi' }, h('div', { class: 'label' }, t), h('div', { class: 'value' }, v));
