// API client. The access token lives in sessionStorage (cleared when the tab closes); it is sent as a Bearer header,
// so cookies are never used and CSRF does not apply.
const store = {
  get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* ignore */ } },
};

export const session = {
  get token() { return store.get('aegis_token'); },
  get orgId() { return store.get('aegis_org'); },
  set(token, orgId) { store.set('aegis_token', token); if (orgId) store.set('aegis_org', String(orgId)); },
  setOrg(id) { store.set('aegis_org', String(id)); },
  clear() { store.set('aegis_token', null); store.set('aegis_org', null); },
};

export class ApiError extends Error {
  constructor(status, message, errors = {}) { super(message); this.status = status; this.errors = errors; }
}

function parseError(status, body) {
  const d = body && body.detail;
  if (typeof d === 'string') return new ApiError(status, d);
  if (d && d.message) return new ApiError(status, d.message, d.errors || {});
  if (Array.isArray(d)) {
    const errors = {};
    d.forEach((e) => { const f = (e.loc || []).slice(-1)[0]; if (f) errors[f] = e.msg; });
    return new ApiError(status, d.map((e) => `${(e.loc || []).slice(-1)[0]}: ${e.msg}`).join('; ') || 'Invalid request', errors);
  }
  return new ApiError(status, `Request failed (${status})`);
}

export async function api(path, { method = 'GET', body, form, params } = {}) {
  const url = new URL(path, location.origin);
  if (params) Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v); });
  const headers = {};
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  if (session.orgId) headers['X-Org-Id'] = session.orgId;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  let res;
  try { res = await fetch(url, { method, headers, body: payload }); } catch { throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.'); }
  if (res.status === 204) return null;
  let data = null;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('json')) data = await res.json().catch(() => null); else data = await res.text();
  if (!res.ok) {
    if (res.status === 401 && session.token) { session.clear(); window.dispatchEvent(new Event('aegis:logout')); }
    throw parseError(res.status, data);
  }
  return data;
}

export async function download(path, filename) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${session.token}`, 'X-Org-Id': session.orgId || '' } });
  if (!res.ok) throw new ApiError(res.status, 'Download failed');
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export const get = (p, params) => api(p, { params });
export const post = (p, body) => api(p, { method: 'POST', body: body ?? {} });
export const patch = (p, body) => api(p, { method: 'PATCH', body });
export const put = (p, body) => api(p, { method: 'PUT', body });
export const del = (p) => api(p, { method: 'DELETE' });
