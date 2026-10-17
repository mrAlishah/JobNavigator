"""Shared Playwright harness for the v2 verification pass. Lives at /tmp/v2t/h.py in the backend container.

Usage inside a test script:
    import sys; sys.path.insert(0, '/tmp/v2t'); from h import *
    with browser() as b:
        pg = page(b, appearance='dark')           # authed context, warmed up
        go(pg, '/feed')
        r = rect(pg, 'text=Jobs')            # {'x','y','w','h','top','bottom'}
        cs = style(pg, '.v2-card >> nth=0', ['borderColor','backgroundColor'])
        assert_int_tops(pg, '[data-row]')    # fails on fractional getBoundingClientRect().top
        hov = hover_delta(pg, '.v2-card >> nth=0', ['borderColor','backgroundColor','color'])
        snap(pg, 'feed-dark')                # /tmp/v2t/shots/feed-dark.png
"""
import json, os, contextlib, urllib.request, urllib.error, time
from playwright.sync_api import sync_playwright

BASE = os.environ.get("JN_BASE", "http://caddy")
KEY = os.environ.get("JN_KEY", "pick-a-password")
OUT = "/tmp/v2t"
os.makedirs(f"{OUT}/shots", exist_ok=True)


# ── accounts (multi-tenancy) ─────────────────────────────────────────────
# With 2+ accounts the install key alone selects no account (401 account_required):
# the API path needs a signed X-Account-Token (POST /api/auth/verify-pin) and the
# dashboard the signed jn_account cookie (POST /api/auth/switch). The harness acts as
# the OWNER. Whether it does is decided ONCE per process from the account count at
# that moment (`_common` pins it at import, before any case runs): the mt-* cases add
# a second account mid-case on a one-account install and expect the picker then, so a
# context opened during such a case must not switch on its own. JN_OWNER_PIN is only
# needed when the owner has a PIN.
OWNER_PIN = os.environ.get("JN_OWNER_PIN") or None
_ACCT = {"multi": None, "owner": None, "token": None, "count": None}


def _raw(method, path, body=None, headers=None, key=KEY, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    hd = {"X-API-Key": key, "Content-Type": "application/json"}
    hd.update(headers or {})
    req = urllib.request.Request(BASE + "/api" + path, data=data, method=method, headers=hd)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def owner_account(key=KEY, refresh=False):
    """The owner's public record from /api/auth/accounts (install key only), cached."""
    if _ACCT["owner"] is None or refresh:
        st, txt = _raw("GET", "/auth/accounts", key=key)
        if st != 200:
            raise RuntimeError(f"GET /api/auth/accounts: {st} {txt[:200]}")
        accs = json.loads(txt)
        own = [a for a in accs if a.get("role") == "owner"] or accs[:1]
        _ACCT["owner"] = own[0] if own else None
        _ACCT["count"] = len(accs)
    return _ACCT["owner"]


def account_count(key=KEY):
    """Live account count (uncached)."""
    owner_account(key=key, refresh=True)
    return _ACCT["count"]


def owner_mode(key=KEY):
    """True when the harness must name the owner explicitly (2+ accounts at first call)."""
    if _ACCT["multi"] is None:
        _ACCT["multi"] = account_count(key=key) > 1
    return _ACCT["multi"]


def owner_token(key=KEY, refresh=False):
    """A signed owner X-Account-Token from verify-pin, cached until refresh."""
    if _ACCT["token"] is None or refresh:
        own = owner_account(key=key, refresh=refresh)
        body = {"account_id": str(own["id"])}
        if OWNER_PIN:
            body["pin"] = OWNER_PIN
        st, txt = _raw("POST", "/auth/verify-pin", body, key=key)
        if st != 200:
            raise RuntimeError(f"verify-pin for the owner: {st} {txt[:200]}")
        _ACCT["token"] = json.loads(txt)["token"]
    return _ACCT["token"]


def owner_cookie(ctx, key=KEY):
    """Give a browser context the owner's jn_account cookie the way the dashboard's
    picker does (POST /api/auth/switch; the Set-Cookie lands in the context's jar).
    No-op unless owner_mode()."""
    if not owner_mode(key=key):
        return False
    body = {"account_id": str(owner_account(key=key)["id"])}
    if OWNER_PIN:
        body["pin"] = OWNER_PIN
    r = ctx.request.post(BASE + "/api/auth/switch", data=body, headers={"X-API-Key": key})
    if r.status != 200:
        raise RuntimeError(f"auth/switch to the owner: {r.status} {r.text()[:200]}")
    return True


# ── API ──────────────────────────────────────────────────────────────────
_RETRY_DETAILS = ("account_required", "invalid_account_token")


def api(method, path, body=None, key=KEY, raw=False, timeout=120, headers=None):
    """Call the backend. Returns (status, json_or_text). Never raises on HTTP errors.

    Without an account header of the caller's own, an owner X-Account-Token is attached
    on a multi-account install (and fetched afresh once on a 401 account_required /
    invalid_account_token, which also covers an install that gained an account later)."""
    hd = dict(headers or {})
    own_hdr = any(k.lower() == "x-account-token" for k in hd)
    if not own_hdr and owner_mode(key=key):
        hd["X-Account-Token"] = owner_token(key=key)
    st, txt = _raw(method, path, body, headers=hd, key=key, timeout=timeout)
    if st == 401 and not own_hdr:
        try:
            detail = (json.loads(txt) or {}).get("detail")
        except (ValueError, AttributeError):
            detail = None
        if detail in _RETRY_DETAILS:
            hd["X-Account-Token"] = owner_token(key=key, refresh=True)
            st, txt = _raw(method, path, body, headers=hd, key=key, timeout=timeout)
    if raw:
        return st, txt
    try:
        return st, json.loads(txt) if txt else None
    except ValueError:
        return st, txt


def get(path, **kw): return api("GET", path, **kw)
def post(path, body=None, **kw): return api("POST", path, body, **kw)
def patch(path, body=None, **kw): return api("PATCH", path, body, **kw)
def delete(path, **kw): return api("DELETE", path, **kw)


# ── browser ──────────────────────────────────────────────────────────────
@contextlib.contextmanager
def browser(headless=True):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        try:
            yield b
        finally:
            b.close()


def context(b, appearance="light", width=1440, height=900, dsf=1, key=KEY, extra_ls=None):
    ctx = b.new_context(viewport={"width": width, "height": height}, device_scale_factor=dsf)
    ls = {"jobnavigator_api_key": key, "jobnavigator_dark_mode": "true" if appearance == "dark" else "false",
          "jobnavigator_welcome_seen": "true", "jobnavigator_v2_welcome_seen": "true",
          "jobnavigator_welcomed": "1"}
    ls.update(extra_ls or {})
    ctx.add_init_script("try{" + "".join(f"localStorage.setItem({json.dumps(k)},{json.dumps(v)});" for k, v in ls.items()) + "}catch(e){}")
    owner_cookie(ctx, key=key)   # 2+ accounts: open as the owner, no picker
    # warm-up: lets App.jsx sync the cookie session so /api hrefs work
    w = ctx.new_page(); w.goto(BASE + "/feed", wait_until="networkidle"); w.wait_for_timeout(500); w.close()
    return ctx


def page(b, appearance="light", **kw):
    """Fresh page with console/network capture attached at pg.jn_log."""
    ctx = context(b, appearance=appearance, **kw)
    pg = ctx.new_page()
    attach_log(pg)
    return pg


def attach_log(pg):
    log = {"console": [], "pageerrors": [], "http": [], "reqfailed": []}
    # only our own frames: postings are framed cross-origin and log their own errors
    def _ours_url(u):
        return (not u) or u.startswith(BASE) or u.startswith("about:")
    def _on_console(m):
        if m.type not in ("error", "warning"): return
        try: u = (m.location or {}).get("url", "")
        except Exception: u = ""
        if _ours_url(u): log["console"].append({"type": m.type, "text": m.text[:400]})
    def _on_pageerror(e):
        st = str(getattr(e, "stack", "") or "")
        if "://" in st and BASE not in st: return
        log["pageerrors"].append(str(e)[:400])
    pg.on("console", _on_console)
    pg.on("pageerror", _on_pageerror)
    pg.on("response", lambda r: log["http"].append({"status": r.status, "url": r.url[-140:], "method": r.request.method}) if r.status >= 400 else None)
    pg.on("requestfailed", lambda r: log["reqfailed"].append({"url": r.url[-140:], "err": (r.failure or "")[:100]}) if "favicon" not in r.url and not r.url.startswith("blob:") else None)
    pg.jn_log = log
    return log


def go(pg, route, settle=800, wait="networkidle", timeout=45000):
    pg.goto(BASE + route, wait_until=wait, timeout=timeout)
    pg.wait_for_timeout(settle)
    return pg


def set_appearance(pg, appearance):
    pg.evaluate(f"localStorage.setItem('jobnavigator_dark_mode', '{'true' if appearance == 'dark' else 'false'}')")
    pg.reload(wait_until="networkidle"); pg.wait_for_timeout(600)


# ── measurement ──────────────────────────────────────────────────────────
def rect(pg, sel):
    return pg.evaluate("""(sel) => { const el = typeof sel === 'string' ? document.querySelector(sel) : sel; if (!el) return null;
        const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, top: r.top, bottom: r.bottom, left: r.left, right: r.right}; }""", sel) if not hasattr(sel, "evaluate") else sel.evaluate("el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, top: r.top, bottom: r.bottom, left: r.left, right: r.right}; }")


def style(pg_or_loc, sel_or_props, props=None):
    """style(pg, css_selector, [props]) or style(locator, [props])."""
    js = "(el, props) => { const cs = getComputedStyle(el); const o = {}; for (const p of props) o[p] = cs[p]; return o; }"
    if props is None:
        loc, props = pg_or_loc, sel_or_props
        return loc.evaluate(js, props)
    return pg_or_loc.locator(sel_or_props).first.evaluate(js, props)


def hover_delta(pg, sel, props, settle=250):
    """Computed style before vs after hover for the first match of sel. Returns {'before':{},'after':{},'changed':[...]}"""
    loc = pg.locator(sel).first
    before = style(loc, props)
    loc.hover(); pg.wait_for_timeout(settle)
    after = style(loc, props)
    pg.mouse.move(0, 0); pg.wait_for_timeout(120)
    return {"before": before, "after": after, "changed": [p for p in props if before[p] != after[p]]}


def tops(pg, sel):
    return pg.evaluate("(sel) => [...document.querySelectorAll(sel)].map(e => e.getBoundingClientRect().top)", sel)


def assert_int_tops(pg, sel):
    ts = tops(pg, sel)
    frac = [t for t in ts if abs(t - round(t)) > 0.01]
    return {"count": len(ts), "fractional": len(frac), "samples": frac[:5]}


def text(pg, sel):
    return pg.locator(sel).first.inner_text()


def snap(pg, name, full=False, clip_sel=None, dsf=None):
    path = f"{OUT}/shots/{name}.png"
    if clip_sel:
        pg.locator(clip_sel).first.screenshot(path=path)
    else:
        pg.screenshot(path=path, full_page=full)
    return path


def px_stats(path):
    """Mean RGB + count of distinct colours of a PNG (needs PIL; falls back to None)."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB"); px = list(im.getdata())
        n = len(px); mean = tuple(sum(c[i] for c in px) // n for i in range(3))
        return {"mean": mean, "distinct": len(set(px)), "size": im.size}
    except Exception as e:
        return {"error": str(e)[:100]}


def report(pg):
    """Console/network log summary for the page."""
    l = pg.jn_log
    return {k: len(v) for k, v in l.items()} | {"detail": l}
