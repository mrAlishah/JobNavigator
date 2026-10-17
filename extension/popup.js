// ===== The Navigator popup — capture + settings =====
// Theme: 'system' leaves the attribute off so the prefers-color-scheme rule
// decides; an explicit choice pins it and wins over the OS.
function applyTheme(t) {
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  else document.documentElement.removeAttribute('data-theme');
}

const $ = (id) => document.getElementById(id);
const VERSION = (chrome.runtime.getManifest && chrome.runtime.getManifest().version) || '';

// ---- URL / company detection (ported) ----
function hostMatches(url, ...domains) {
  let host;
  try { host = new URL(url).hostname.toLowerCase(); } catch { return false; }
  return domains.some(raw => {
    const d = (raw || '').toLowerCase().replace(/\/$/, '');
    return d && (host === d || host.endsWith('.' + d));
  });
}
const COMPANY_DOMAINS = {
  'microsoft.com': 'Microsoft', 'salesforce.com': 'Salesforce', 'servicenow.com': 'ServiceNow',
  'workday.com': 'Workday', 'paypal.com': 'PayPal', 'jpmorgan.com': 'JPMorgan Chase',
  'jpmorganchase.com': 'JPMorgan Chase', 'blackrock.com': 'BlackRock', 'addepar.com': 'Addepar',
  'oracle.com': 'Oracle', 'intuit.com': 'Intuit', 'google.com': 'Google', 'amazon.com': 'Amazon',
  'amazon.jobs': 'Amazon', 'stripe.com': 'Stripe', 'visa.com': 'Visa', 'mastercard.com': 'Mastercard',
  'uber.com': 'Uber', 'block.xyz': 'Block', 'plaid.com': 'Plaid', 'clearstreet.io': 'Clear Street',
  'simcorp.com': 'SimCorp', 'cisco.com': 'Cisco', 'ibm.com': 'IBM', 'meta.com': 'Meta',
  'metacareers.com': 'Meta', 'apple.com': 'Apple', 'databricks.com': 'Databricks', 'coinbase.com': 'Coinbase',
  'ubs.com': 'UBS', 'robinhood.com': 'Robinhood', 'affirm.com': 'Affirm', 'kraken.com': 'Kraken',
  'chime.com': 'Chime', 'ramp.com': 'Ramp', 'brex.com': 'Brex', 'rippling.com': 'Rippling',
  'greenhouse.io': '', 'lever.co': '', 'myworkdayjobs.com': '', 'taleo.net': '', 'icims.com': '',
  'eightfold.ai': '', 'linkedin.com': '', 'indeed.com': '', 'ziprecruiter.com': '',
};
function detectCompany(url) {
  try {
    const parsed = new URL(url);
    const hostname = parsed.hostname.toLowerCase();
    const pathParts = parsed.pathname.replace(/^\//, '').split('/').filter(Boolean);
    let detected = '';
    for (const [domain, company] of Object.entries(COMPANY_DOMAINS)) {
      if (hostname.includes(domain)) { detected = company; break; }
    }
    const titleCase = (s) => s.split(/[-_\s]+/).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
    if (!detected) {
      for (const ats of ['eightfold.ai', 'myworkdayjobs.com', 'taleo.net', 'icims.com']) {
        if (hostname.endsWith(ats)) {
          const sub = hostname.replace(`.${ats}`, '').replace(/\./g, ' ').trim();
          if (sub && !['www', 'jobs', 'careers', 'apply'].includes(sub)) detected = titleCase(sub);
          break;
        }
      }
    }
    if (!detected) {
      for (const ats of ['greenhouse.io', 'lever.co', 'ashbyhq.com']) {
        if (hostname.endsWith(ats) && pathParts.length >= 1) {
          const slug = pathParts[0];
          if (slug && !['jobs', 'job', 'embed', 'api'].includes(slug)) detected = titleCase(slug);
          break;
        }
      }
    }
    if (!detected && hostMatches(url, 'rippling.com') && pathParts.length >= 1) {
      const slug = hostname === 'ats.rippling.com' || hostname.endsWith('.ats.rippling.com')
        ? pathParts[0] : (pathParts[0] === 'careers' && pathParts[1] ? pathParts[1] : '');
      if (slug) detected = titleCase(slug);
    }
    if (!detected && hostname === 'jobs.apple.com') detected = 'Apple';
    if (!detected && hostMatches(url, 'metacareers.com')) detected = 'Meta';
    if (!detected && hostMatches(url, 'google.com') && parsed.pathname.includes('/careers')) detected = 'Google';
    if (!detected) {
      const parts = hostname.split('.');
      const skip = ['www', 'jobs', 'careers', 'apply', 'boards', 'hire', 'recruiting', 'talent'];
      const tlds = ['com', 'org', 'net', 'co', 'io', 'ai', 'dev', 'xyz', 'us', 'uk', 'de', 'fr', 'ca', 'au', 'jobs'];
      const meaningful = parts.filter(p => !skip.includes(p) && !tlds.includes(p) && p.length > 1);
      if (meaningful.length) {
        const core = meaningful.length === 1 ? meaningful[0] : meaningful[meaningful.length - 1];
        detected = core.charAt(0).toUpperCase() + core.slice(1);
      }
    }
    return detected;
  } catch { return ''; }
}

// in-page extractors (executed in the tab)
function _extractTitleFn() {
  const skip = /^(careers|jobs|job search|open positions|work with|join)\b/i;
  const clean = (t) => t.trim().split('\n')[0].trim().substring(0, 200);
  const good = (t) => { t = (t || '').trim(); return t.length > 3 && !skip.test(t); };
  try {
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      const d = JSON.parse(s.textContent);
      if (d['@type'] === 'JobPosting' && d.title) return clean(d.title);
    }
  } catch {}
  const sels = ['h1.job-title', 'h1.posting-headline', '.job-title h1', 'h1[data-job-title]',
    '.top-card-layout__title', 'h2[class*="position-title"]', '[class*="position-title"]', 'h1.t-24',
    '.jobsearch-JobInfoHeader-title', '.job-details h1', '.job-header h1', '.job-info h1',
    '[data-automation-id="jobPostingHeader"]'];
  for (const sel of sels) { const el = document.querySelector(sel); if (el && good(el.textContent)) return clean(el.textContent); }
  const og = document.querySelector('meta[property="og:title"]');
  if (og && good(og.content)) return clean(og.content);
  const h1 = document.querySelector('h1'); if (h1 && good(h1.textContent)) return clean(h1.textContent);
  const t = document.title.split(' - ')[0].split(' | ')[0].trim();
  return good(t) ? t : document.title;
}
function _extractDescFn() {
  const sels = ['[class*="jobs-description-content__text"]', '[class*="job-description"]', '[class*="jobDescription"]',
    '[class*="job_description"]', '[data-automation-id="jobPostingDescription"]', '.posting-page .content',
    '.job-details', '.description', 'article', '[role="article"]', 'main'];
  for (const sel of sels) { const el = document.querySelector(sel); if (el && el.innerText.trim().length > 100) return el.innerText.trim().substring(0, 15000); }
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { const d = JSON.parse(s.textContent); if (d['@type'] === 'JobPosting' && d.description) { const div = document.createElement('div'); div.innerHTML = d.description; return div.innerText.trim().substring(0, 15000); } } catch {}
  }
  return '';
}

// ---- state ----
const state = {
  screen: 'capture', url: '', host: '', path: '',
  send: 'idle', applied: 'idle', lastError: '', filterReason: '',
  li: false, af: false, atsMode: 'off', len: 250, captured: 0,
  theme: 'system',
  liInfo: false, afInfo: false, charsOpen: false, urlOpen: false,
  serverUrl: 'http://localhost', apiKey: '',
  // MT-18 / QA F1: target account. The signed API token the server issued for it (after its
  // PIN, when it has one) lives in chrome.storage.local with the id the row shows (per
  // browser profile, not synced: a synced token would carry to another machine).
  accounts: [], accountId: '', accountToken: '', acctOpen: false, acctPending: '', acctNote: '',
};
let _sendT, _saveT;

async function forgetAccount(note) {
  state.accountId = ''; state.accountToken = ''; state.acctNote = note;
  await chrome.storage.local.remove(['accountId', 'accountToken']);
  await chrome.storage.local.set({ accountRemoved: true });
}

async function cfg() {
  const s = await chrome.storage.sync.get(['serverUrl', 'apiKey']);
  state.serverUrl = s.serverUrl || 'http://localhost';
  state.apiKey = s.apiKey || '';
  const l = await chrome.storage.local.get(['accountId', 'accountToken', 'accountRemoved']);
  state.accountId = l.accountId || '';
  state.accountToken = l.accountToken || '';
  if (l.accountRemoved) state.acctNote = 'Account removed — pick again';
  // a choice stored by the pre-token extension (bare id, no token) no longer counts
  if (state.accountId && !state.accountToken) await forgetAccount('Pick the account again');
  return { serverUrl: state.serverUrl, apiKey: state.apiKey };
}

// Every backend call carries the install key and, when one is chosen, the account token.
function apiHeaders(extra) {
  const h = Object.assign({ 'X-API-Key': state.apiKey }, extra || {});
  if (state.accountToken) h['X-Account-Token'] = state.accountToken;
  return h;
}

// 401 invalid_account_token = the chosen account was removed, its PIN changed or the
// token expired: forget it and say so.
async function noteIfAccountGone(resp) {
  if (resp.status !== 401) return false;
  let d = null;
  try { d = await resp.clone().json(); } catch {}
  if (d && d.detail === 'account_required') { state.errHint = 'Choose an account in Settings › Saves to.'; return false; }
  if (!d || d.detail !== 'invalid_account_token') return false;
  state.errHint = 'Account changed or removed — pick again in Settings › Saves to.';
  await forgetAccount('Account changed or removed — pick again');
  await loadAccounts(); render();
  return true;
}

// ---- account selector (MT-18) ----
// The accounts list needs only the install key (no token: a stale one would 401).
async function loadAccounts(url, key) {
  try {
    const resp = await fetch(`${url || state.serverUrl}/api/auth/accounts`, { headers: { 'X-API-Key': key !== undefined ? key : state.apiKey } });
    state.accounts = resp.ok ? await resp.json() : [];
  } catch { state.accounts = []; }
  if (state.accounts.length >= 2 && state.accountId && !state.accounts.some(a => a.id === state.accountId)) {
    await forgetAccount('Account removed — pick again');
  }
  renderAccounts();
}
function renderAccounts() {
  const multi = state.accounts.length >= 2;
  $('acctField').classList.toggle('hide', !multi);
  if (!multi) return;
  const cur = state.accounts.find(a => a.id === state.accountId);
  $('acctName').textContent = cur ? cur.name : 'Not chosen';
  $('acctDot').style.background = cur ? cur.color : '';
  $('acctNote').textContent = state.acctNote;
  $('acctNote').classList.toggle('hide', !state.acctNote);
  $('acctList').classList.toggle('hide', !state.acctOpen || !!state.acctPending);
  $('acctList').innerHTML = '';
  state.accounts.forEach(a => {
    const row = document.createElement('div');
    row.className = 'aopt' + (a.id === state.accountId ? ' cur' : '');
    const dot = document.createElement('span'); dot.className = 'adot'; dot.style.background = a.color;
    const nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = a.name;
    const tag = document.createElement('div'); tag.className = 'tag'; tag.textContent = a.has_pin ? 'PIN' : '';
    row.append(dot, nm, tag);
    row.onclick = () => pickAccount(a);
    $('acctList').appendChild(row);
  });
  const pend = state.accounts.find(a => a.id === state.acctPending);
  $('acctPin').classList.toggle('hide', !pend);
  if (pend) $('acctPinFor').textContent = `Enter the PIN for ${pend.name} (the owner's PIN also works)`;
}
async function storeAccount(a, token) {
  state.accountId = a.id; state.accountToken = token;
  state.acctNote = ''; state.acctOpen = false; state.acctPending = '';
  await chrome.storage.local.set({ accountId: a.id, accountToken: token });
  await chrome.storage.local.remove('accountRemoved');
  renderAccounts();
  // The popup reads the persona-bound state per account; background reads storage on each call.
  checkConnection();
}
// POST /api/auth/verify-pin issues the account's token: an account without a PIN gets it
// with no PIN; one with a PIN after its PIN (or the owner's, for a member).
async function requestToken(a, pin) {
  const body = { account_id: a.id };
  if (pin !== undefined) body.pin = pin;
  const resp = await fetch(`${$('serverUrl').value.trim() || state.serverUrl}/api/auth/verify-pin`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': $('apiKey').value.trim() || state.apiKey },
    body: JSON.stringify(body),
  });
  const d = await resp.json().catch(() => ({}));
  return { resp, d };
}
async function pickAccount(a) {
  if (a.id === state.accountId && state.accountToken) { state.acctOpen = false; renderAccounts(); return; }
  if (!a.has_pin) {
    try {
      const { resp, d } = await requestToken(a);
      if (resp.ok && d.token) { await storeAccount(a, d.token); return; }
      if (d.detail !== 'pin_required') {      // a PIN set since the list loaded falls through to the PIN field
        state.acctNote = resp.status === 404 ? 'That account no longer exists' : `Server error ${resp.status}`;
        if (resp.status === 404) await loadAccounts(); else renderAccounts();
        return;
      }
    } catch { state.acctNote = 'Cannot reach server'; renderAccounts(); return; }
  }
  state.acctPending = a.id; $('acctPinIn').value = ''; $('acctPinErr').classList.add('hide');
  renderAccounts(); $('acctPinIn').focus();
}
async function verifyPinAndStore() {
  const a = state.accounts.find(x => x.id === state.acctPending);
  if (!a) return;
  const err = (t) => { $('acctPinErr').textContent = t; $('acctPinErr').classList.remove('hide'); };
  try {
    const { resp, d } = await requestToken(a, $('acctPinIn').value);
    if (resp.ok && d.token) { await storeAccount(a, d.token); return; }
    if (d.detail === 'wrong_pin') err(`Wrong PIN — ${d.tries_left} ${d.tries_left === 1 ? 'try' : 'tries'} left`);
    else if (d.detail === 'pin_cooldown') err(`Too many tries — wait ${resp.headers.get('Retry-After') || 30} s`);
    else if (d.detail === 'pin_required') err('Enter the PIN');
    else if (resp.status === 404) { err('That account no longer exists'); await loadAccounts(); }
    else err(`Server error ${resp.status}`);
  } catch { err('Cannot reach server'); }
  $('acctPinIn').value = '';
}

// ---- render ----
function render() {
  const cap = state.screen === 'capture';
  $('hdrCapture').classList.toggle('hide', !cap);
  $('hdrSettings').classList.toggle('hide', cap);
  $('screenCapture').classList.toggle('hide', !cap);
  $('screenSettings').classList.toggle('hide', cap);

  // source
  $('srcHost').textContent = state.host;
  $('srcPath').textContent = state.path;
  const detected = !!($('title').value.trim() && $('company').value.trim());
  $('chipDetected').classList.toggle('hide', !detected);
  $('chipNot').classList.toggle('hide', detected);

  // send states — 'filtered' is a 200 the server refused to feed (OPEN-12), so
  // the primary button stays available rather than leaving the popup stuck.
  $('btnSend').classList.toggle('hide', state.send !== 'idle' && state.send !== 'filtered');
  $('btnSending').classList.toggle('hide', state.send !== 'sending');
  $('btnSent').classList.toggle('hide', state.send !== 'sent');
  $('btnRetry').classList.toggle('hide', state.send !== 'error');

  // applied button
  const ab = $('btnApplied');
  ab.classList.remove('saved', 'err-btn');
  if (state.applied === 'saving') ab.innerHTML = '<div class="spin" style="border-color:rgba(0,0,0,.18); border-top-color:#57534C"></div>';
  else if (state.applied === 'saved') { ab.classList.add('saved'); ab.innerHTML = '<div class="check-sm"></div>Saved'; }
  else if (state.applied === 'exists') { ab.classList.add('saved'); ab.innerHTML = '<div class="check-sm"></div>Already logged'; }
  else if (state.applied === 'error') { ab.classList.add('err-btn'); ab.innerHTML = 'Retry'; }
  else ab.innerHTML = 'Save as applied';

  // error card (send or applied) — text depends on which action failed
  // OPEN-12: it also carries the "saved but filtered out" case, which used to be
  // completely silent — the job went to the DB as `ignored` and never showed up.
  const showErr = state.send === 'error' || state.applied === 'error' || state.send === 'filtered';
  $('errCard').classList.toggle('hide', !showErr);
  if (state.send === 'filtered') {
    $('errHd').textContent = 'Not added to the job feed';
    $('errBo').textContent = `${state.filterReason || 'Filtered out by your Extension search rules'}. Edit the rules in Searches › Extension.`;
  } else if (showErr) {
    $('errHd').textContent = state.lastError === 'applied' ? "Couldn't reach the Application Board" : "Couldn't reach the Job Feed";
    $('errBo').textContent = state.errHint ? `Not saved. ${state.errHint}` : 'Not saved. Check your connection, then retry.';
  }

  // captured strip + LinkedIn count
  $('capturedStrip').classList.toggle('hide', state.captured <= 0);
  $('capturedCount').textContent = String(state.captured);
  $('liCount').classList.toggle('hide', state.captured > 0);
  $('liCount').textContent = `${state.captured} jobs`;

  // toggles
  $('liToggle').dataset.on = String(state.li);
  $('afToggle').dataset.on = String(state.af);
  $('pvToggle').dataset.on = String(state.pv);
  $('liInfoCard').classList.toggle('hide', !state.liInfo);
  $('afInfoCard').classList.toggle('hide', !state.afInfo);
  $('pvInfoCard').classList.toggle('hide', !state.pvInfo);

  // chars
  $('lenChip').textContent = `${state.len} chars`;
  $('charsPanel').style.display = state.charsOpen ? 'flex' : 'none';
  document.querySelectorAll('.preset').forEach(p => p.classList.toggle('active', Number(p.dataset.len) === state.len));

  // ats segmented
  document.querySelectorAll('.seg-opt[data-mode]').forEach(o => o.classList.toggle('active', o.dataset.mode === state.atsMode));
  document.querySelectorAll('[data-theme-opt]').forEach(o => o.classList.toggle('active', o.dataset.themeOpt === state.theme));

  // url sheet
  $('urlSheet').classList.toggle('hide', !state.urlOpen);
}

// ---- actions ----
async function doSend() {
  if (state.send === 'sending') return;
  const title = $('title').value.trim(), company = $('company').value.trim(), url = state.url.trim();
  if (!title || !company || !url) return;
  state.errHint = ''; state.send = 'sending'; render();
  let description = '';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) { const [r] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: _extractDescFn }); if (r && r.result) description = r.result; }
  } catch {}
  try {
    const resp = await fetch(`${state.serverUrl}/api/jobs/save-from-extension`, {
      method: 'POST', headers: apiHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ title, company, url, description }),
    });
    if (!resp.ok) { await noteIfAccountGone(resp); throw new Error(String(resp.status)); }
    // OPEN-12: a 200 does not mean it reached the feed — the Extension search's
    // title/company filters (merged with the global title-exclude list) store the
    // job as `ignored` so the dedup keys stick. The server now says so; say it too.
    const data = await resp.json().catch(() => ({}));
    if (data && data.saved === false) {
      state.send = 'filtered'; state.filterReason = data.reason || ''; render();
      return;
    }
    state.filterReason = '';
    state.send = 'sent'; render();
    clearTimeout(_sendT); _sendT = setTimeout(() => { state.send = 'idle'; render(); }, 1800);
  } catch { state.send = 'error'; state.lastError = 'send'; render(); }
}
async function doApplied() {
  if (state.applied === 'saving') return;
  const title = $('title').value.trim(), company = $('company').value.trim(), url = state.url.trim();
  if (!title || !company || !url) return;
  state.errHint = ''; state.applied = 'saving'; render();
  try {
    const resp = await fetch(`${state.serverUrl}/api/applications`, {
      method: 'POST', headers: apiHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ title, company, url }),
    });
    if (resp.status === 409) { state.applied = 'exists'; render(); clearTimeout(_saveT); _saveT = setTimeout(() => { state.applied = 'idle'; render(); }, 2200); return; }   // already logged for this posting
    if (!resp.ok) { await noteIfAccountGone(resp); throw new Error(String(resp.status)); }
    state.applied = 'saved'; render();
    clearTimeout(_saveT); _saveT = setTimeout(() => { state.applied = 'idle'; render(); }, 1800);
  } catch { state.applied = 'error'; state.lastError = 'applied'; render(); }
}

async function checkConnection() {
  $('connDot').classList.remove('bad'); $('connTx').textContent = 'Checking…';
  try {
    const url = $('serverUrl').value.trim() || state.serverUrl, key = $('apiKey').value.trim() || state.apiKey;
    const headers = { 'X-API-Key': key };
    if (state.accountToken) headers['X-Account-Token'] = state.accountToken;
    const resp = await fetch(`${url}/api/autofill/config`, { headers });
    let detail = '';
    if (resp.status === 401) { try { detail = (await resp.clone().json()).detail; } catch {} }
    if (detail === 'invalid_account_token') { await noteIfAccountGone(resp); $('connTx').textContent = 'Account changed or removed — pick again'; $('connDot').classList.add('bad'); }
    else if (detail === 'account_required') { $('connDot').classList.remove('bad'); $('connTx').textContent = 'Connected · choose an account'; }
    else if (resp.status === 401) { $('connDot').classList.add('bad'); $('connTx').textContent = 'API key rejected'; }
    else if (resp.ok) { $('connDot').classList.remove('bad'); $('connTx').textContent = `Connected · v${VERSION}`; }
    else { $('connDot').classList.add('bad'); $('connTx').textContent = `Server error ${resp.status}`; }
  } catch { $('connDot').classList.add('bad'); $('connTx').textContent = 'Cannot reach server'; }
}

// ---- init ----
document.addEventListener('DOMContentLoaded', async () => {
  await cfg();
  $('serverUrl').value = state.serverUrl;
  $('apiKey').value = state.apiKey;
  loadAccounts();   // fills the "Saves to" row (settings) when the server has 2+ accounts

  // storage-backed feature state
  const st = await chrome.storage.sync.get(['linkedinCapture', 'autofillEnabled', 'autofillDefaultLength', 'structuredAutofillEnabled', 'structuredAutofillTrigger', 'theme', 'previewUnblock']);
  state.li = !!st.linkedinCapture;
  state.af = !!st.autofillEnabled;
  state.pv = st.previewUnblock !== false;   // on until switched off
  state.len = Number(st.autofillDefaultLength) > 0 ? Number(st.autofillDefaultLength) : 250;
  state.atsMode = !st.structuredAutofillEnabled ? 'off' : (st.structuredAutofillTrigger === 'auto' ? 'auto' : 'click');
  state.theme = ['light', 'dark', 'system'].includes(st.theme) ? st.theme : 'system';
  applyTheme(state.theme);
  $('lenCustom').value = String(state.len);

  // captured count
  chrome.runtime.sendMessage({ type: 'linkedin_get_count' }, (resp) => { if (resp && resp.count !== undefined) { state.captured = resp.count; render(); } });

  // current tab → source + parse
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) {
    state.url = tab.url || '';
    try { const u = new URL(state.url); state.host = u.hostname.replace(/^www\./, ''); state.path = (u.pathname + u.search).slice(0, 40); } catch { state.host = state.url.slice(0, 40); }
    if (tab.favIconUrl) { $('favicon').src = tab.favIconUrl; } else { $('favicon').style.background = '#DCE7F3'; }
    const co = detectCompany(state.url); if (co) $('company').value = co;
    render();
    try {
      const [r] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: _extractTitleFn });
      if (r && r.result) $('title').value = r.result;
    } catch { if (tab.title) $('title').value = tab.title.split(' - ')[0].split(' | ')[0].trim(); }
  }
  render();

  // header nav
  $('toSettings').onclick = () => { state.screen = 'settings'; render(); loadAccounts().then(checkConnection); };
  $('hdrSettings').onclick = () => { state.screen = 'capture'; render(); };

  // capture actions
  $('btnSend').onclick = doSend;
  $('btnRetry').onclick = doSend;
  $('btnApplied').onclick = doApplied;
  $('title').oninput = render;
  $('company').oninput = render;

  // edit url sheet
  $('btnEditUrl').onclick = () => { $('urlText').value = state.url; state.urlOpen = true; render(); };
  $('urlCancel').onclick = () => { state.urlOpen = false; render(); };
  $('urlSheet').onclick = (e) => { if (e.target === $('urlSheet')) { state.urlOpen = false; render(); } };
  $('urlUpdate').onclick = () => {
    state.url = $('urlText').value.trim();
    try { const u = new URL(state.url); state.host = u.hostname.replace(/^www\./, ''); state.path = (u.pathname + u.search).slice(0, 40); } catch {}
    state.urlOpen = false; render();
  };

  // captured strip → send LinkedIn
  $('capturedStrip').onclick = () => {
    chrome.runtime.sendMessage({ type: 'linkedin_send' }, (resp) => { if (resp && !resp.error) { state.captured = 0; render(); } });
  };

  // footer: LinkedIn
  $('liToggle').onclick = () => { state.li = !state.li; chrome.storage.sync.set({ linkedinCapture: state.li }); render(); };
  $('liInfo').onclick = () => { state.liInfo = !state.liInfo; render(); };
  $('liClear').onclick = () => { chrome.runtime.sendMessage({ type: 'linkedin_clear' }, () => { state.captured = 0; render(); }); };

  // footer: AI-drafted
  $('afToggle').onclick = () => { state.af = !state.af; chrome.storage.sync.set({ autofillEnabled: state.af }); render(); };
  $('afInfo').onclick = () => { state.afInfo = !state.afInfo; render(); };

  // footer: posting preview (frame header rules live in background.js and follow this key)
  $('pvToggle').onclick = () => { state.pv = !state.pv; chrome.storage.sync.set({ previewUnblock: state.pv }); render(); };
  $('pvInfo').onclick = () => { state.pvInfo = !state.pvInfo; render(); };
  $('lenChip').onclick = () => { state.charsOpen = !state.charsOpen; render(); };
  document.querySelectorAll('.preset').forEach(p => p.onclick = () => {
    state.len = Number(p.dataset.len); $('lenCustom').value = String(state.len);
    chrome.storage.sync.set({ autofillDefaultLength: state.len }); render();
  });
  $('lenCustom').onchange = () => {
    const n = parseInt($('lenCustom').value, 10);
    if (n > 0) { state.len = n; chrome.storage.sync.set({ autofillDefaultLength: n }); render(); }
  };

  // footer: Fill ATS forms segmented
  document.querySelectorAll('[data-theme-opt]').forEach(o => o.onclick = () => {
    state.theme = o.dataset.themeOpt;
    chrome.storage.sync.set({ theme: state.theme });
    applyTheme(state.theme);
    render();
  });
  document.querySelectorAll('.seg-opt[data-mode]').forEach(o => o.onclick = () => {
    state.atsMode = o.dataset.mode;
    if (state.atsMode === 'off') chrome.storage.sync.set({ structuredAutofillEnabled: false });
    else chrome.storage.sync.set({ structuredAutofillEnabled: true, structuredAutofillTrigger: state.atsMode });
    render();
  });

  // settings
  $('showKey').onclick = () => {
    const on = $('apiKey').type === 'password';
    $('apiKey').type = on ? 'text' : 'password'; $('showKey').textContent = on ? 'Hide' : 'Show';
  };
  const recheck = async () => {
    await loadAccounts($('serverUrl').value.trim() || state.serverUrl, $('apiKey').value.trim());
    await checkConnection();
  };
  $('apiKey').onchange = recheck;
  $('serverUrl').onchange = recheck;
  $('acctChange').onclick = () => { state.acctOpen = !state.acctOpen; state.acctPending = ''; renderAccounts(); };
  $('acctPinOk').onclick = verifyPinAndStore;
  $('acctPinIn').onkeydown = (e) => { if (e.key === 'Enter') verifyPinAndStore(); };
  $('acctPinCancel').onclick = () => { state.acctPending = ''; renderAccounts(); };
  $('saveSettings').onclick = async () => {
    state.serverUrl = $('serverUrl').value.trim(); state.apiKey = $('apiKey').value.trim();
    await chrome.storage.sync.set({ serverUrl: state.serverUrl, apiKey: state.apiKey });
    await loadAccounts();
    await checkConnection();
    state.screen = 'capture'; render();
  };

  render();
});

// live LinkedIn count updates
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'linkedin_count_update') { state.captured = msg.count; render(); }
});
