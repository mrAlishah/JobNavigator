// extension/lib/server_url.js — pure, no DOM. Server URL + credential scoping.
// Loaded by popup.html, background.js (importScripts) and tests/extension (node).
(function (root) {
  class ServerUrlError extends Error {
    constructor(msg) { super(msg); this.name = 'ServerUrlError'; }
  }

  // 'http://host:port/' -> 'http://host:port'. Keeps protocol, host, port and a reverse-proxy
  // sub-path; drops trailing slashes, query, hash and any user:password@.
  function normalizeServerUrl(raw) {
    let u;
    try { u = new URL(String(raw == null ? '' : raw).trim()); } catch { u = null; }
    if (!u || (u.protocol !== 'http:' && u.protocol !== 'https:')) {
      throw new ServerUrlError('Server URL must start with http:// or https:// (e.g. http://localhost:8082)');
    }
    return u.origin + u.pathname.replace(/\/+$/, '');
  }

  // `path` starts with '/'. The only place an /api URL is built.
  function apiUrl(base, path) { return normalizeServerUrl(base) + path; }

  function sameServer(a, b) {
    try { return normalizeServerUrl(a) === normalizeServerUrl(b); } catch { return false; }
  }

  // The signed account token belongs to one install: never send it to another server.
  // A token stored without its server (pre-fix) is dropped; the user picks the account again.
  function accountTokenFor(local, serverUrl) {
    return local && local.accountToken && sameServer(local.accountServer, serverUrl) ? local.accountToken : '';
  }

  // Backend 401 `detail` -> what to tell the user. `forget`: drop the stored account choice.
  // Dashboard API Key = the install password; the account token is the per-account one.
  function authHint(detail) {
    switch (detail) {
      case 'API key required': return { hint: 'The Dashboard API Key is missing — add it in Settings.' };
      case 'Invalid API key': return { hint: 'The Dashboard API Key was rejected — enter the key of this server in Settings.' };
      case 'account_required': return { hint: 'Choose an account in Settings › Saves to.' };
      case 'invalid_account_token': return { hint: 'Account changed or removed — pick again in Settings › Saves to.', forget: true };
      default: return { hint: 'The server refused the credentials (401) — check Settings.' };
    }
  }

  const api = { ServerUrlError, normalizeServerUrl, apiUrl, sameServer, accountTokenFor, authHint };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.JNServer = api;
})(typeof self !== 'undefined' ? self : globalThis);
