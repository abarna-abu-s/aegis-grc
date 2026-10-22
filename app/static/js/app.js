import { api, get, post, session } from './api.js';
import { can, loadMe, resetLookups, state } from './state.js';
import { clear, errMsg, fmtDateTime, h, label, loading, toast } from './ui.js';
import * as dashboard from './views/dashboard.js';
import * as fw from './views/frameworks.js';
import * as controls from './views/controls.js';
import * as evidence from './views/evidence.js';
import * as policies from './views/policies.js';
import * as insights from './views/insights.js';
import * as copilot from './views/copilot.js';
import * as misc from './views/misc.js';

const ROUTES = {
  dashboard: { title: 'Overview', icon: '▣', group: 'Overview', view: (r) => dashboard.render(r) },
  frameworks: { title: 'Frameworks', icon: '☰', group: 'Compliance', view: (r, id, c) => (id ? fw.renderFramework(r, id, c) : fw.renderLibrary(r)) },
  assessments: { title: 'Assessments', icon: '✔', group: 'Compliance', view: (r, id, c) => (id ? fw.renderAssessment(r, id, c) : fw.renderAssessments(r)) },
  controls: { title: 'Controls', icon: '⚙', group: 'Compliance', view: (r, id, c) => (id ? controls.renderDetail(r, id, c) : controls.renderList(r)) },
  evidence: { title: 'Evidence', icon: '❐', group: 'Compliance', view: (r, id, c, t) => evidence.renderEvidence(r, id, c, t) },
  policies: { title: 'Policies', icon: '≡', group: 'Compliance', view: (r, id, c, t) => policies.render(r, id, c, t) },
  risks: { title: 'Risks', icon: '▲', group: 'Risk & assurance', view: (r, id, c, t) => misc.risks(r, id, c, t) },
  assets: { title: 'Assets & privacy', icon: '◉', group: 'Risk & assurance', view: (r, id, c, t) => misc.assets(r, id, c, t) },
  vendors: { title: 'Vendors', icon: '⚓', group: 'Risk & assurance', view: (r) => misc.vendors(r) },
  audits: { title: 'Audit & remediation', icon: '⚑', group: 'Risk & assurance', view: (r, id, c, t) => misc.audits(r, id, c, t) },
  tasks: { title: 'Tasks', icon: '☑', group: 'Operations', view: (r) => misc.tasks(r) },
  workflow: { title: 'Approvals & exceptions', icon: '⚖', group: 'Operations', view: (r, id, c, t) => misc.workflow(r, id, c, t) },
  reports: { title: 'Reports', icon: '☷', group: 'Operations', view: (r, id, c) => misc.reports(r, id, c) },
  insights: { title: 'Insights', icon: '✵', group: 'Intelligence', view: (r, id, c, t) => insights.render(r, id, c, t) },
  copilot: { title: 'AI copilot', icon: '✦', group: 'Intelligence', view: (r, id, c, t) => copilot.render(r, id, c, t) },
  'audit-trail': { title: 'Audit trail', icon: '≣', group: 'Administration', view: (r) => misc.auditTrail(r) },
  notifications: { title: 'Notifications', icon: '✉', group: null, view: (r) => misc.notificationsPage(r) },
  settings: { title: 'Settings', icon: '⚒', group: 'Administration', view: (r, id, c, t) => misc.settings(r, id, c, t) },
};

const app = document.getElementById('app');
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } } };

// ---------------------------------------------------------------- auth screen
function authScreen(mode = 'login') {
  clear(app);
  const err = h('div', { class: 'callout bad hidden', role: 'alert' });
  const f = {};
  const field = (name, text, type = 'text', extra = {}) => h('label', { class: 'field' }, text, f[name] = h('input', { name, type, required: true, autocomplete: type === 'password' ? (mode === 'login' ? 'current-password' : 'new-password') : name === 'email' ? 'email' : 'off', ...extra }));
  const form = h('form', { novalidate: true, onSubmit: async (e) => {
    e.preventDefault(); err.classList.add('hidden'); btn.disabled = true;
    try {
      const body = mode === 'login' ? { email: f.email.value, password: f.password.value } : { email: f.email.value, password: f.password.value, full_name: f.full_name.value, organization_name: f.organization_name.value };
      const r = await api(`/api/auth/${mode}`, { method: 'POST', body });
      session.set(r.access_token, r.user.memberships[0]?.org_id);
      await boot();
    } catch (ex) { err.textContent = errMsg(ex); err.classList.remove('hidden'); btn.disabled = false; }
  } }, mode === 'register' ? [field('full_name', 'Your name'), field('organization_name', 'Organization name')] : null, field('email', 'Work email', 'email'), field('password', 'Password', 'password', mode === 'register' ? { minlength: 10 } : {}),
  mode === 'register' ? h('p', { class: 'small muted' }, 'At least 10 characters with upper and lower case letters and a digit.') : null, err);
  const btn = h('button', { class: 'btn primary', type: 'submit', style: { width: '100%', justifyContent: 'center' } }, mode === 'login' ? 'Sign in' : 'Create account and organization');
  form.append(btn);
  app.append(h('div', { class: 'auth' }, h('div', { class: 'auth-art' }, h('div', { class: 'brand', style: { padding: 0 } }, h('div', { class: 'brand-mark' }, 'A'), 'Aegis GRC'),
    h('h1', {}, 'One evidence base for every framework.'), h('ul', {}, h('li', {}, 'Map controls once, reuse evidence across ISO 27001, SOC 2, GDPR, DPDP and more'), h('li', {}, 'AI suggestions that always cite sources and always need a human decision'), h('li', {}, 'Transparent scores - every formula is visible'), h('li', {}, 'Tenant-isolated, role-based, audit-trailed'))),
  h('div', { class: 'auth-form' }, h('div', { class: 'card' }, h('h2', { style: { marginBottom: '14px' } }, mode === 'login' ? 'Sign in' : 'Create your workspace'), form,
    h('p', { class: 'small', style: { marginTop: '14px', textAlign: 'center' } }, mode === 'login' ? 'New here? ' : 'Already registered? ', h('a', { href: '#', onClick: (e) => { e.preventDefault(); authScreen(mode === 'login' ? 'register' : 'login'); } }, mode === 'login' ? 'Create an account' : 'Sign in'))))));
  (f.full_name || f.email).focus();
}

// ---------------------------------------------------------------- shell
let main, crumbsEl, shell, currentRoute;
function parseHash() {
  const parts = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('/').filter(Boolean);
  const name = ROUTES[parts[0]] ? parts[0] : 'dashboard';
  const second = parts[1];
  return { name, id: second && /^\d+$/.test(second) ? second : null, tab: second && !/^\d+$/.test(second) ? second : null };
}

function setCrumbs(list) {
  clear(crumbsEl);
  list.forEach((c, i) => {
    if (i) crumbsEl.append(' / ');
    const target = i === 0 ? '#/dashboard' : i === 1 && list.length > 2 ? `#/${currentRoute}` : null;
    crumbsEl.append(target ? h('a', { href: target }, c) : document.createTextNode(c));
  });
}

async function renderRoute() {
  if (!main) return;
  const { name, id, tab } = parseHash();
  currentRoute = name;
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === name));
  const route = ROUTES[name];
  document.title = `${route.title} - Aegis GRC`;
  setCrumbs(['Aegis', route.title]);
  clear(main);
  shell.classList.remove('mobile-open');
  try { route.view(main, id, (c) => setCrumbs(['Aegis', ...c]), tab); } catch (e) { main.append(h('div', { class: 'state err' }, errMsg(e))); }
  window.scrollTo(0, 0);
}

function navigate(hash) { if (location.hash === hash) renderRoute(); else location.hash = hash; }

function buildShell() {
  clear(app);
  const groups = {};
  Object.entries(ROUTES).forEach(([k, r]) => { if (r.group) (groups[r.group] ||= []).push([k, r]); });
  const nav = h('nav', { class: 'nav', 'aria-label': 'Main' }, Object.entries(groups).flatMap(([g, items]) => [h('div', { class: 'nav-group' }, g), ...items.map(([k, r]) => h('a', { href: `#/${k}`, 'data-route': k, title: r.title }, h('span', { class: 'ico', 'aria-hidden': 'true' }, r.icon), h('span', { class: 'nav-label' }, r.title)))]));
  const notifBtn = h('button', { class: 'icon-btn', 'aria-label': 'Notifications', onClick: (e) => { e.stopPropagation(); toggleMenu('notif'); } }, '✉');
  const menus = h('div', {});
  const theme = () => { const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = t; store.set('aegis_theme', t); };
  crumbsEl = h('div', { class: 'crumbs', 'aria-label': 'Breadcrumb' });
  main = h('main', { id: 'main' });
  const orgs = state.me.memberships;
  shell = h('div', { class: `shell ${store.get('aegis_collapsed') === '1' ? 'collapsed' : ''}` },
    h('aside', { class: 'sidebar' }, h('div', { class: 'brand' }, h('div', { class: 'brand-mark' }, 'A'), h('span', { class: 'brand-name' }, 'Aegis GRC')), nav, h('div', { class: 'side-foot' }, 'Scores are internal indicators, not audit opinions or certifications.')),
    h('div', { class: 'main' },
      state.org?.is_demo ? h('div', { class: 'demo-banner' }, 'DEMO ORGANIZATION - all data is fictional sample data.') : null,
      h('header', { class: 'topbar' },
        h('button', { class: 'icon-btn', 'aria-label': 'Toggle sidebar', onClick: () => { if (window.innerWidth <= 800) shell.classList.toggle('mobile-open'); else { shell.classList.toggle('collapsed'); store.set('aegis_collapsed', shell.classList.contains('collapsed') ? '1' : '0'); } } }, '☰'),
        h('button', { class: 'search-trigger', onClick: openPalette, 'aria-label': 'Search and commands' }, '⌕ Search records, ask questions, jump to a page...', h('kbd', {}, 'Ctrl K')),
        h('span', { class: 'spacer' }), orgs.length > 1 ? h('select', { class: 'input', style: { width: 'auto', margin: 0 }, 'aria-label': 'Organization', onChange: (e) => { session.setOrg(e.target.value); resetLookups(); boot(); } }, orgs.map((o) => h('option', { value: o.org_id, selected: o.org_id === state.org.org_id }, o.org_name))) : h('span', { class: 'org-badge', title: 'Organization' }, state.org.org_name),
        h('span', { class: 'org-badge' }, label(state.role)), h('div', { class: 'menu-wrap' }, notifBtn), h('button', { class: 'icon-btn', 'aria-label': 'Toggle dark mode', onClick: theme }, '◐'),
        h('div', { class: 'menu-wrap' }, h('button', { class: 'icon-btn', 'aria-label': 'Profile menu', onClick: (e) => { e.stopPropagation(); toggleMenu('profile'); } }, state.me.full_name.slice(0, 1).toUpperCase()))),
      h('div', { class: 'page' }, crumbsEl, main)), menus);
  app.append(shell);

  let open = null;
  function closeMenus() { menus.querySelectorAll('.menu').forEach((m) => m.remove()); open = null; }
  document.addEventListener('click', closeMenus);
  async function toggleMenu(kind) {
    if (open === kind) return closeMenus();
    closeMenus(); open = kind;
    const anchor = kind === 'notif' ? notifBtn : document.querySelector('[aria-label="Profile menu"]');
    const menu = h('div', { class: 'menu', role: 'menu', onClick: (e) => e.stopPropagation() });
    anchor.parentElement.append(menu);
    if (kind === 'profile') {
      menu.append(h('div', { class: 'menu-title' }, state.me.email), h('button', { onClick: () => { closeMenus(); navigate('#/settings/account'); } }, 'Account & password'), h('button', { onClick: () => { closeMenus(); navigate('#/settings'); } }, 'Organization settings'), h('hr'),
        h('button', { onClick: async () => { try { await post('/api/auth/logout'); } catch { /* token may already be invalid */ } session.clear(); shell = main = null; authScreen(); } }, 'Sign out'));
    } else {
      menu.append(loading());
      try {
        const d = await get('/api/notifications', { limit: 12 });
        clear(menu).append(h('div', { class: 'menu-title' }, 'Notifications'), d.items.length ? d.items.map((n) => h('div', { class: `note-item ${n.read ? '' : 'unread'}` }, h('strong', {}, n.title), h('div', { class: 'small' }, n.body), h('div', { class: 'small muted' }, fmtDateTime(n.created_at)))) : h('div', { class: 'note-item muted' }, 'You are all caught up.'),
          h('hr'), d.unread ? h('button', { onClick: async () => { await post('/api/notifications/read-all'); closeMenus(); badge(); } }, 'Mark all as read') : null, h('button', { onClick: () => { closeMenus(); navigate('#/notifications'); } }, 'View all'));
      } catch (e) { clear(menu).append(h('div', { class: 'note-item' }, errMsg(e))); }
    }
  }
  async function badge() {
    try { const d = await get('/api/notifications', { unread_only: true, limit: 50 }); notifBtn.querySelector('.badge-dot')?.remove(); if (d.unread) notifBtn.append(h('span', { class: 'badge-dot' }, d.unread > 9 ? '9+' : d.unread)); } catch { /* ignore */ }
  }
  badge();
}

// ---------------------------------------------------------------- command palette
function openPalette() {
  const existing = document.querySelector('.palette-overlay'); if (existing) return existing.remove();
  let items = [], sel = 0, timer, token = 0;
  const input = h('input', { type: 'text', placeholder: 'Search records, try "expired evidence" or "high risks", or type a page name...', 'aria-label': 'Command palette', autocomplete: 'off' });
  const list = h('div', { class: 'palette-list', role: 'listbox' });
  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey, true); };
  const go = (it) => { close(); if (it.action) it.action(); else navigate(it.href); };
  function draw() {
    clear(list); let grp = '';
    items.forEach((it, i) => { if (it.group !== grp) { grp = it.group; list.append(h('div', { class: 'grp' }, grp)); } list.append(h('div', { class: `palette-item ${i === sel ? 'sel' : ''}`, role: 'option', onClick: () => go(it) }, h('span', {}, it.title), h('span', { class: 'small muted' }, it.sub || ''))); });
    if (!items.length) list.append(h('div', { class: 'state' }, 'No matches.'));
  }
  const pages = () => Object.entries(ROUTES).filter(([, r]) => r.group).map(([k, r]) => ({ group: 'Go to', title: r.title, href: `#/${k}` }))
    .concat([{ group: 'Actions', title: 'Toggle dark mode', action: () => document.querySelector('[aria-label="Toggle dark mode"]').click() }, { group: 'Actions', title: 'Ask the AI copilot', href: '#/copilot' }, { group: 'Actions', title: 'Generate a report', href: '#/reports' }]);
  async function update() {
    const q = input.value.trim(); const mine = ++token;
    const nav = pages().filter((p) => !q || p.title.toLowerCase().includes(q.toLowerCase()));
    items = nav; sel = 0; draw();
    if (q.length < 2) return;
    try {
      const r = await get('/api/search', { q }); if (mine !== token) return;
      const found = r.groups.flatMap((g) => g.items.slice(0, 5).map((it) => ({ group: r.interpreted && g === r.groups[0] ? `${g.label} - ${r.interpreted}` : g.label, title: it.title, sub: it.sub, href: g.type === 'framework' ? `#/frameworks/${it.id}` : ['control', 'evidence'].includes(g.type) ? `#/${g.link}/${it.id}` : `#/${g.link}` })));
      const passages = r.passages.map((p) => ({ group: 'Document passages', title: `${p.title}: ${p.excerpt.slice(0, 70)}...`, sub: p.source_type, href: '#/copilot' }));
      items = [...found, ...passages, ...nav]; sel = 0; draw();
    } catch { /* keep nav results */ }
  }
  function onKey(e) {
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); draw(); list.querySelector('.sel')?.scrollIntoView({ block: 'nearest' }); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); draw(); list.querySelector('.sel')?.scrollIntoView({ block: 'nearest' }); }
    else if (e.key === 'Enter' && items[sel]) { e.preventDefault(); go(items[sel]); }
  }
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(update, 200); });
  const overlay = h('div', { class: 'overlay palette-overlay', onMousedown: (e) => { if (e.target === overlay) close(); } }, h('div', { class: 'palette', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Command palette' }, input, list));
  document.body.append(overlay); document.addEventListener('keydown', onKey, true); update(); input.focus();
}
document.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && state.me) { e.preventDefault(); openPalette(); } });

// ---------------------------------------------------------------- boot
async function boot() {
  if (!session.token) return authScreen();
  app.replaceChildren(loading('Loading workspace...'));
  try {
    await loadMe();
    session.setOrg(state.org.org_id);
    buildShell();
    await renderRoute();
  } catch (e) {
    session.clear();
    authScreen();
    toast(errMsg(e), 'bad');
  }
}
window.addEventListener('hashchange', renderRoute);
window.addEventListener('aegis:refresh', renderRoute);
window.addEventListener('aegis:logout', () => { shell = main = null; authScreen(); toast('Your session has ended. Please sign in again.', 'bad'); });
void can;
boot();
