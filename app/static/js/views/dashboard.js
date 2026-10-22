import { get, post } from '../api.js';
import { can } from '../state.js';
import { PALETTE, chip, clear, donut, empty, errorState, fail, fmtDate, fmtDateTime, h, label, loading, meter, scoreColor, stackedBar, status, toast } from '../ui.js';

export const crumbs = ['Overview'];

const kpi = (title, value, sub, color) => h('div', { class: 'card kpi' }, h('div', { class: 'label' }, title), h('div', { class: 'value', style: color ? { color } : null }, value), h('div', { class: 'sub' }, sub));

export function render(root) {
  const host = h('div', {}, loading());
  root.append(host);
  async function load() {
    try {
      const d = await get('/api/dashboard');
      clear(host);
      const r = d.readiness;
      const onboarding = !d.assessments.length;
      host.append(
        h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Compliance overview'), h('p', {}, 'Every figure on this page is calculated from your organization\'s saved records. Scores are internal indicators, not audit opinions.'))),
        onboarding ? onboardingCard(d) : null,
        h('div', { class: 'grid g4' },
          kpi('Audit-readiness', r.insufficient_data.length === 4 ? '-' : r.score, r.insufficient_data.length ? `Insufficient data: ${r.insufficient_data.map(label).join(', ')}` : 'Weighted composite (see formula)', scoreColor(r.score)),
          kpi('Control health', `${d.control_health.healthy}/${Object.values(d.control_health).reduce((a, b) => a + b, 0)}`, `${d.control_health.failing} failing, ${d.control_health.no_evidence} without evidence`),
          kpi('Evidence current', `${d.evidence_status.current + d.evidence_status.expiring}`, `${d.evidence_status.expired} expired, ${d.evidence_status.pending} pending review`),
          kpi('Open findings', Object.values(d.open_findings).reduce((a, b) => a + b, 0), `${d.open_findings.critical} critical, ${d.open_findings.high} high`)),
        h('details', { class: 'card', style: { marginTop: '16px' } }, h('summary', {}, h('strong', {}, 'How is the readiness score calculated?')),
          h('p', { class: 'muted' }, r.formula), h('table', {}, h('thead', {}, h('tr', {}, ['Component', 'Value', 'Weight', 'Contribution'].map((x) => h('th', {}, x)))),
            h('tbody', {}, Object.entries(r.components).map(([k, v]) => h('tr', {}, h('td', {}, label(k)), h('td', {}, v.value ?? 'no data'), h('td', {}, `${v.weight * 100}%`), h('td', {}, v.contribution))))), h('p', { class: 'small muted' }, r.note)),
        h('div', { class: 'grid g-main', style: { marginTop: '16px' } },
          h('div', { class: 'stack' },
            h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', {}, 'Framework scorecards'), h('a', { href: '#/assessments' }, 'Open assessments')),
              d.assessments.length ? h('div', { class: 'stack' }, d.assessments.map((a) => h('div', {}, h('div', { class: 'row between' }, h('a', { href: `#/assessments/${a.assessment_id}` }, a.name), h('span', { class: 'small muted' }, `${a.applicable} applicable requirements`)),
                h('div', { class: 'small muted' }, `Assessment score ${a.assessment_score ?? '-'}`), meter(a.assessment_score, scoreColor(a.assessment_score)),
                h('div', { class: 'small muted', style: { marginTop: '4px' } }, `Evidence coverage ${a.evidence_coverage ?? '-'}%`), meter(a.evidence_coverage, PALETTE.caramel)))) : empty('No assessments yet', 'Start one from the framework library.', h('a', { class: 'btn primary', href: '#/frameworks' }, 'Browse frameworks'))),
            h('div', { class: 'grid g2' },
              h('div', { class: 'card' }, h('h3', {}, 'Control health'), donut([{ label: 'Healthy', value: d.control_health.healthy, color: PALETTE.ok }, { label: 'At risk', value: d.control_health.at_risk, color: PALETTE.warn }, { label: 'Failing', value: d.control_health.failing, color: PALETTE.bad }, { label: 'No evidence', value: d.control_health.no_evidence, color: PALETTE.neutral }], undefined, 'controls')),
              h('div', { class: 'card' }, h('h3', {}, 'Evidence freshness'), stackedBar([{ label: 'Current', value: d.evidence_status.current, color: PALETTE.ok }, { label: 'Expiring', value: d.evidence_status.expiring, color: PALETTE.warn }, { label: 'Expired', value: d.evidence_status.expired, color: PALETTE.bad }, { label: 'Pending', value: d.evidence_status.pending, color: PALETTE.info }, { label: 'Rejected', value: d.evidence_status.rejected, color: PALETTE.neutral }]),
                h('h3', { style: { marginTop: '18px' } }, 'Open risks by level'), stackedBar([{ label: 'Critical', value: d.risks_by_level.critical, color: PALETTE.bad }, { label: 'High', value: d.risks_by_level.high, color: '#C86B3C' }, { label: 'Medium', value: d.risks_by_level.medium, color: PALETTE.warn }, { label: 'Low', value: d.risks_by_level.low, color: PALETTE.ok }]))),
            h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', {}, `Recommended actions (${d.recommendation_count})`), h('a', { href: '#/insights/recommendations' }, 'See all')),
              d.recommendations.length ? h('ul', { class: 'clean' }, d.recommendations.map((x) => h('li', { class: 'row between' }, h('div', {}, h('div', {}, x.title), h('div', { class: 'small muted' }, x.reason)), h('div', { class: 'row' }, status(x.priority),
                can('contribute') && !x.already_tasked ? h('button', { class: 'btn sm', onClick: async () => { try { await post('/api/insights/recommendations/create-task', { key: x.key }); toast('Task created', 'ok'); load(); } catch (e) { fail(e); } } }, 'Create task') : x.already_tasked ? chip('tasked', 'ok') : null)))) : empty('Nothing to recommend', 'No expired evidence, unowned controls or other gaps were detected.'))),
          h('div', { class: 'stack' },
            h('div', { class: 'card' }, h('h3', {}, 'Work queue'), h('ul', { class: 'clean' },
              h('li', { class: 'row between' }, h('a', { href: '#/tasks' }, 'Open tasks'), h('span', {}, `${d.tasks.open}${d.tasks.overdue ? ` (${d.tasks.overdue} overdue)` : ''}`)),
              h('li', { class: 'row between' }, h('a', { href: '#/workflow' }, 'Pending approvals'), h('span', {}, d.pending_approvals)),
              h('li', { class: 'row between' }, h('a', { href: '#/evidence' }, 'Evidence pending review'), h('span', {}, d.evidence_status.pending)),
              h('li', { class: 'row between' }, h('a', { href: '#/notifications' }, 'Unread notifications'), h('span', {}, d.unread_notifications)))),
            h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h3', {}, 'Next 30 days'), h('a', { href: '#/insights/calendar' }, 'Calendar')),
              d.upcoming.length ? h('ul', { class: 'clean' }, d.upcoming.map((e) => h('li', {}, h('div', { class: 'small muted' }, fmtDate(e.date)), e.title))) : h('p', { class: 'muted' }, 'No dated items in the next 30 days.')),
            h('div', { class: 'card' }, h('h3', {}, 'Recent activity'), d.recent_activity.length ? h('ul', { class: 'clean' }, d.recent_activity.map((e) => h('li', {}, h('div', {}, h('strong', {}, e.action), ' ', h('span', { class: 'muted' }, e.detail.slice(0, 80))), h('div', { class: 'small muted' }, `${e.actor} - ${fmtDateTime(e.at)}`)))) : h('p', { class: 'muted' }, 'No activity yet.')))));
    } catch (e) { clear(host).append(errorState(e, load)); }
  }
  load();
}

function onboardingCard(d) {
  const step = (done, text, href) => h('li', { class: 'row' }, chip(done ? 'done' : 'todo', done ? 'ok' : ''), h('a', { href }, text));
  return h('div', { class: 'card', style: { marginBottom: '16px' } }, h('h3', {}, 'Get started'), h('p', { class: 'muted' }, 'This organization has no assessments yet, so scores show "no data" instead of fabricated numbers.'),
    h('ul', { class: 'clean' }, step(false, 'Choose a framework and start an assessment', '#/frameworks'), step(d.counts.controls > 0, 'Create or auto-generate internal controls', '#/controls'),
      step(d.counts.evidence > 0, 'Upload evidence and link it to controls', '#/evidence'), step(d.counts.policies > 0, 'Add policies so the copilot can cite them', '#/policies')));
}
