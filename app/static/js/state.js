import { get } from './api.js';

// Mirrors server permissions for *UI hints only* (hiding buttons). The server enforces every rule.
const MANAGE = ['owner', 'admin', 'compliance_manager'];
const PERMS = {
  admin: ['owner', 'admin'], manage: MANAGE, approve: MANAGE, contribute: [...MANAGE, 'control_owner'],
  audit: [...MANAGE, 'auditor'], restricted: [...MANAGE, 'auditor'], ai: [...MANAGE, 'control_owner', 'auditor'],
};
export const state = { me: null, org: null, role: null };
export const can = (p) => !!state.role && PERMS[p].includes(state.role);

export async function loadMe() {
  state.me = await get('/api/auth/me');
  state.role = state.me.role;
  state.org = state.me.memberships.find((m) => m.org_id === state.me.active_org_id) || state.me.memberships[0];
  return state.me;
}

let cache = {};
export const resetLookups = () => { cache = {}; };
async function cached(key, fn) { if (!cache[key]) cache[key] = fn(); return cache[key]; }
export const userOptions = () => cached('users', async () => (await get('/api/members')).map((m) => ({ value: m.user_id, label: `${m.full_name} (${m.role.replace('_', ' ')})` })));
export const controlOptions = () => cached('controls', async () => (await get('/api/controls', { page_size: 200, sort: 'control_key' })).items.map((c) => ({ value: c.id, label: `${c.control_key} - ${c.title}` })));
export const frameworkOptions = () => cached('fw', async () => (await get('/api/frameworks')).items.map((f) => ({ value: f.id, label: `${f.name} ${f.version}${f.control_count ? '' : ' (no controls loaded)'}` })));
export const optionsOf = (path, labelKey = 'title', extra = {}) => async () => (await get(path, { page_size: 200, ...extra })).items.map((r) => ({ value: r.id, label: r[labelKey] || r.name }));
export const invalidate = (k) => { delete cache[k]; };
