// Run: node --test 'tests/extension/*.test.js'
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { normalizeServerUrl, apiUrl, sameServer, accountTokenFor, authHint } =
  require('../../extension/lib/server_url.js');

const ext = (f) => fs.readFileSync(path.join(__dirname, '../../extension', f), 'utf8');

test('trailing slashes are stripped, protocol/host/port kept', () => {
  assert.equal(normalizeServerUrl('http://localhost:8082/'), 'http://localhost:8082');
  assert.equal(normalizeServerUrl('http://localhost:8082'), 'http://localhost:8082');
  assert.equal(normalizeServerUrl('  http://localhost:8082//  '), 'http://localhost:8082');
  assert.equal(normalizeServerUrl('https://jobs.example.com/'), 'https://jobs.example.com');
  assert.equal(normalizeServerUrl('https://example.com/jobnav/'), 'https://example.com/jobnav');
});

test('LAN IPv4 with a custom port', () => {
  assert.equal(normalizeServerUrl('http://192.168.178.33:8081/'), 'http://192.168.178.33:8081');
});

test('invalid server URLs are rejected with a usable message', () => {
  for (const bad of ['', '   ', 'localhost:8082', '192.168.178.33:8081', 'ftp://host', 'javascript:alert(1)', 'not a url']) {
    assert.throws(() => normalizeServerUrl(bad), (e) => e.name === 'ServerUrlError' && /http/.test(e.message), bad);
  }
});

test('API URLs never contain a double slash after the host', () => {
  for (const base of ['http://localhost:8082', 'http://localhost:8082/', 'http://192.168.178.33:8081//']) {
    for (const p of ['/api/jobs/save-from-extension', '/api/applications', '/api/auth/accounts', '/api/auth/verify-pin', '/api/autofill/config']) {
      const u = apiUrl(base, p);
      assert.ok(!/\/\/api\//.test(u), u);
      assert.ok(u.endsWith(p), u);
    }
  }
  assert.equal(apiUrl('http://192.168.178.33:8081/', '/api/jobs/save-from-extension'),
    'http://192.168.178.33:8081/api/jobs/save-from-extension');
});

test('every extension request builds its URL through apiUrl', () => {
  for (const f of ['popup.js', 'background.js']) {
    assert.doesNotMatch(ext(f), /\$\{[^}]*\}\/api\//, `${f} still interpolates a base URL into /api/`);
  }
  const popup = ext('popup.js');
  assert.match(popup, /apiUrl\(state\.serverUrl, '\/api\/jobs\/save-from-extension'\)/);
  assert.match(popup, /apiUrl\(state\.serverUrl, '\/api\/applications'\)/);
});

test('401 reasons map to separate, actionable hints (no credentials in text)', () => {
  const missing = authHint('API key required');
  const invalid = authHint('Invalid API key');
  const acct = authHint('account_required');
  const stale = authHint('invalid_account_token');
  assert.match(missing.hint, /Dashboard API Key is missing/);
  assert.match(invalid.hint, /Dashboard API Key was rejected/);
  assert.match(acct.hint, /Choose an account/);
  assert.match(stale.hint, /pick again/);
  assert.equal(stale.forget, true);
  for (const h of [missing, invalid, acct]) assert.ok(!h.forget);
  assert.equal(new Set([missing.hint, invalid.hint, acct.hint, stale.hint]).size, 4);
  assert.ok(authHint(undefined).hint);          // unknown / unparseable body
  assert.ok(!/check your connection/i.test(missing.hint + invalid.hint));
});

test('an account token is only used against the server that issued it', () => {
  const alish = 'http://localhost:8082', elsa = 'http://192.168.178.33:8081';
  const local = { accountToken: 'tok-alish', accountServer: alish };
  assert.equal(accountTokenFor(local, alish), 'tok-alish');
  assert.equal(accountTokenFor(local, alish + '/'), 'tok-alish');
  assert.equal(accountTokenFor(local, elsa), '');                 // other install: never sent
  assert.equal(accountTokenFor({ accountToken: 'legacy' }, alish), '');   // no recorded server: dropped
  assert.equal(accountTokenFor({}, alish), '');
  assert.equal(accountTokenFor(local, 'garbage'), '');
  assert.ok(!sameServer(alish, elsa));
  assert.ok(sameServer(alish, 'http://localhost:8082/'));
});
