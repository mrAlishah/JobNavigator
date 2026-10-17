import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,  // send jn_session + jn_account cookies on every request
});

// ── which account this page shows (QA F7 / R6-01) ──────────────────────────
// The account cookie is per BROWSER: a switch in one tab moves every open tab.
// Each page therefore binds to the account its first answer named (the
// middleware puts `X-JN-Account` on every account-bound response) and sends it
// back as `X-Expected-Account` on every write. The server refuses a write whose
// expected account is not the browser's account with 409 `account_changed`; the
// page then (or as soon as a broadcast from the switching tab, or any later
// answer, names another account) raises `jn:account-changed`, stops writing and
// App.jsx shows the blocking "reload" notice.
export const WRITE_METHODS = ['post', 'put', 'patch', 'delete'];
const SWITCH_PATHS = ['/auth/switch', '/auth/verify-pin'];
let pageAccount = null;          // id string, once known
let changedTo = null;            // { id, name } once this page is stale
let switchingHere = false;       // this tab is switching: its own 409s are expected
const inflight = new Set();      // promises of writes still on the wire

export const getPageAccount = () => pageAccount;
export const accountChanged = () => changedTo;
// Bind the page (first answer wins). Exported for /auth/me and the tests.
export function bindPageAccount(id) {
  const v = id == null ? '' : String(id).toLowerCase();
  if (!v) return;
  if (pageAccount == null) { pageAccount = v; return; }
  if (v !== pageAccount) markAccountChanged({ id: v });
}
// This page now shows another account than the browser is on.
export function markAccountChanged(account) {
  if (switchingHere || changedTo) return;
  changedTo = { id: account?.id ? String(account.id).toLowerCase() : '', name: account?.name || '' };
  window.dispatchEvent(new CustomEvent('jn:account-changed', { detail: changedTo }));
}
// accounts.js `finishSwitch`: the reload that follows is ours, not a stale tab.
export function beginOwnSwitch() { switchingHere = true; }
// Resolve once every write already on the wire has answered.
export function waitForWrites() {
  return Promise.allSettled([...inflight]).then(() => undefined);
}
// Headers for a write sent outside axios (Persona's keepalive flush).
export function writeHeaders() {
  const h = { 'Content-Type': 'application/json' };
  try { const k = localStorage.getItem('jobnavigator_api_key'); if (k) h['X-API-Key'] = k; } catch { /* ignore */ }
  if (pageAccount) h['X-Expected-Account'] = pageAccount;
  return h;
}
// Test hook: forget the binding.
export function _resetPageAccount() { pageAccount = null; changedTo = null; switchingHere = false; inflight.clear(); }

const isWrite = (config) => WRITE_METHODS.includes(String(config?.method || 'get').toLowerCase());
const isSwitch = (config) => SWITCH_PATHS.some((p) => String(config?.url || '').startsWith(p));

// Attach API key from localStorage as a fallback header (extension + API client parity)
api.interceptors.request.use((config) => {
  const apiKey = localStorage.getItem('jobnavigator_api_key') || '';
  if (apiKey) {
    config.headers['X-API-Key'] = apiKey;
  }
  if (isWrite(config)) {
    // a stale page writes nothing (the reload notice is up)
    if (changedTo && !isSwitch(config)) {
      const e = new Error('account_changed');
      e.code = 'ERR_ACCOUNT_CHANGED';
      e.config = config;
      return Promise.reject(e);
    }
    if (pageAccount) config.headers['X-Expected-Account'] = pageAccount;
    let done;
    const p = new Promise((r) => { done = r; });
    inflight.add(p);
    config._jnDone = () => { inflight.delete(p); done(); };
  }
  return config;
});

// What an auth failure asks the UI to do (backend/accounts/auth.py, MT-10/11):
//   'account'      401 `account_required` (header X-JN-Auth: account): the install
//                  password is fine but this browser has picked no account → picker
//   'pin_required' 401 from /auth/switch (or a PIN change): the account needs its PIN
//   'wrong_pin'    401 + `tries_left`
//   'pin_cooldown' 429 + Retry-After seconds (5 wrong tries → 30 s)
//   'login'        any other 401: the install password is missing or wrong → sign-in
// null for everything else (including a 429 that is not a PIN cooldown).
export const PIN_FAILURES = ['pin_required', 'wrong_pin', 'pin_cooldown'];
export function authFailure(error) {
  const r = error?.response;
  if (!r) return null;
  const detail = typeof r.data?.detail === 'string' ? r.data.detail : '';
  const header = String(r.headers?.['x-jn-auth'] || '').toLowerCase();
  if (r.status === 401 && (detail === 'account_required' || detail === 'invalid_account' || header === 'account')) {
    return { kind: 'account' };
  }
  if (r.status === 401 && detail === 'pin_required') return { kind: 'pin_required' };
  if (r.status === 401 && detail === 'wrong_pin') {
    const n = Number(r.data?.tries_left);
    return { kind: 'wrong_pin', triesLeft: Number.isFinite(n) ? n : null };
  }
  if (r.status === 429 && detail === 'pin_cooldown') {
    const s = parseInt(r.headers?.['retry-after'], 10);
    return { kind: 'pin_cooldown', retryAfter: Number.isFinite(s) && s > 0 ? s : 30 };
  }
  if (r.status === 401) return { kind: 'login' };
  return null;
}

// The account an answer names (X-JN-Account), read on success and failure alike.
// The switch's own answer names the NEW account and must not stale this page.
const noteAccount = (resp) => {
  if (!resp || isSwitch(resp.config)) return;
  const id = resp.headers?.['x-jn-account'];
  if (id) bindPageAccount(id);
};

// Broadcast a global event so the app can show the right overlay: the account
// picker for `account_required`, the install-password sign-in for any other 401,
// the reload notice for a 409 `account_changed`.
// PIN answers are the caller's own business (the PIN pad shows them), never an overlay.
api.interceptors.response.use(
  (response) => {
    response?.config?._jnDone?.();
    noteAccount(response);
    return response;
  },
  (error) => {
    error?.config?._jnDone?.();
    const r = error?.response;
    if (r?.status === 409 && r.data?.detail === 'account_changed') {
      markAccountChanged(r.data?.account || {});
      return Promise.reject(error);
    }
    noteAccount(r);
    const f = authFailure(error);
    if (f?.kind === 'account') {
      window.dispatchEvent(new CustomEvent('jn:account-required'));
    } else if (f?.kind === 'login') {
      window.dispatchEvent(new CustomEvent('jn:unauthorized'));
    }
    return Promise.reject(error);
  }
);

export default api;
