"""Establish/refresh the logged-in LinkedIn browser session used by the Chrome-extension import
(`scraper/sources/linkedin_extension.enrich`), separately from the import since password login is
gated behind an email-PIN checkpoint that needs a human to relay the code; persists cookies to
/root/.linkedin_api/li_cookies.json so the import can load them headlessly until they expire."""
import asyncio
import json
import os
import sys

from backend.accounts import credentials as _cred
from backend.scraper.sources.linkedin_extension import _session_path

# Phase of the current refresh (idle | running | awaiting_pin | ok | failed), read by
# GET /api/linkedin/session so the Settings row can prompt for the PIN. STATE is the
# owner's; a member's lives in _MEMBER_STATES (MT-16: sessions, PIN drops and phases are
# per account, so two accounts can refresh at once).
STATE = {"phase": "idle", "detail": ""}
_MEMBER_STATES: dict = {}


def state() -> dict:
    """The phase dict of the current account's refresh."""
    member = _cred.session_member()
    if member is None:
        return STATE
    return _MEMBER_STATES.setdefault(member, {"phase": "idle", "detail": ""})


def pin_file() -> str:
    return _cred.linkedin_pin_file()


def _phase(phase: str, detail: str = "") -> None:
    st = state()
    st["phase"] = phase
    st["detail"] = detail


def _creds():
    """(email, password, missing-message, use_mock) for the account the refresh signs in with.
    `linkedin_use_mock_account` picks the mock account or the personal one; unset, it
    follows whether a mock email exists."""
    from backend.models.db import SessionLocal
    from backend.accounts.settings import get_setting
    db = SessionLocal()
    try:
        def g(k):
            return get_setting(db, k) or ""
        switch = g("linkedin_use_mock_account").strip().lower()
        if (switch or ("true" if g("linkedin_mock_email").strip() else "false")) == "true":
            return (g("linkedin_mock_email").strip(), g("linkedin_mock_password").strip(),
                    "Mock account email/password are not set. Set them, or switch off "
                    "'Use a mock account' to sign in with the personal account.", True)
        return (g("linkedin_email").strip(), g("linkedin_password").strip(),
                "Personal email/password are not set.", False)
    finally:
        db.close()


async def _login_and_save(email, password, fresh=False) -> int:
    """`fresh` drops the cookies that `_get_linkedin_browser` loads from the personal
    scraper. Otherwise /login redirects to that feed and the personal session is saved."""
    from backend.scraper.sources import linkedin_personal as lp
    pw = browser = None
    try:
        pw, browser, context, page = await lp._get_linkedin_browser()
        if fresh:
            await context.clear_cookies()
        await page.goto("https://www.linkedin.com/login",
                        wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        if "/feed" not in page.url and "/jobs" not in page.url:
            await lp._fill_login_form(page, email, password)
            try:
                await page.wait_for_url(
                    lambda u: "/feed" in u or "/jobs" in u or "/checkpoint" in u,
                    timeout=30000)
            except Exception:
                pass
            if "/checkpoint" in page.url or "/challenge" in page.url:
                if not await _solve_pin(page):
                    return 1
            elif "/login" in page.url:
                print("Still on login page — wrong credentials?")
                return 1

        # Verify against Voyager before saving (csrf-token header is required).
        me = await lp._voyager_me_status(page)
        if me != 200:
            print(f"Session check failed (voyager /me = {me}); not saving.")
            return 1

        cookies = await context.cookies()
        cookies = [c for c in cookies if "linkedin" in c.get("domain", "")]
        path = _session_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(cookies, f)
        print(f"Session OK (voyager /me=200). Saved {len(cookies)} cookies -> {path}")
        return 0
    finally:
        try:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()
        except Exception:
            pass


async def _solve_pin(page) -> bool:
    """Handle the email-PIN checkpoint by polling the account's PIN file (up to 300s)."""
    pin_box = page.locator("#input__email_verification_pin")
    try:
        await pin_box.wait_for(timeout=8000)
    except Exception:
        print(f"Checkpoint is not an email-PIN type (url={page.url}).")
        return False
    PIN_FILE = pin_file()
    if os.path.exists(PIN_FILE):
        os.remove(PIN_FILE)
    print(f"CHECKPOINT: LinkedIn emailed a PIN to the account. Drop the 6 "
          f"digits into {PIN_FILE} (waiting up to 300s)...", flush=True)
    _phase("awaiting_pin", "LinkedIn emailed a PIN to the account's inbox.")
    pin = None
    for _ in range(100):
        if os.path.exists(PIN_FILE):
            pin = "".join(c for c in open(PIN_FILE).read() if c.isdigit())
            if pin:
                break
        await asyncio.sleep(3)
    if not pin:
        print("No PIN provided within 300s.")
        return False
    print(f"Got PIN ({len(pin)} digits), submitting...", flush=True)
    _phase("running", "Submitting the PIN…")
    await pin_box.fill(pin)
    await page.locator("#email-pin-submit-button").click()
    try:
        await page.wait_for_url(lambda u: "/feed" in u or "/jobs" in u, timeout=30000)
    except Exception:
        if "/checkpoint" in page.url or "/challenge" in page.url:
            print("PIN rejected or a second challenge followed.")
            return False
    try:
        os.remove(PIN_FILE)
    except OSError:
        pass
    return True


async def run_refresh() -> int:
    """Entry point for the Settings row: same flow as the CLI, but keeps STATE updated so the UI can follow along."""
    email, password, missing, use_mock = _creds()
    if not email or not password:
        _phase("failed", missing)
        return 2
    _phase("running", "Signing in as " + email)
    try:
        code = await _login_and_save(email, password, fresh=use_mock)
    except Exception as e:  # noqa: BLE001 - surfaced to the UI verbatim
        _phase("failed", str(e)[:200])
        return 1
    _phase("ok" if code == 0 else "failed",
           "Session refreshed." if code == 0 else "Login did not complete — see backend logs.")
    return code


def _run() -> int:
    email, password, missing, use_mock = _creds()
    if not email or not password:
        print(missing)
        return 2
    print(f"Logging in as {email} via Playwright...", flush=True)
    return asyncio.run(_login_and_save(email, password, fresh=use_mock))


def resolve_account(ref: str):
    """(account id, name, note) for `--account <name|id>`, or None when no single account
    matches. The note explains which session a member on 'inherit' refreshes."""
    from backend.models.db import SessionLocal
    from backend.scripts.cli_login import find_account
    from backend.accounts.context import use_account
    db = SessionLocal()
    try:
        acc = find_account(db, ref)
        if acc is None:
            return None
        note = ""
        if acc.role != "owner":
            with use_account(acc.id):
                if _cred.linkedin_shared_from_owner(db):
                    note = (f"{acc.name}'s LinkedIn section inherits the owner's, so this refreshes "
                            "the owner's session.")
        return acc.id, acc.name, note
    finally:
        db.close()


def main(argv=None) -> int:
    """`python -m backend.refresh_linkedin_session [--account <name|id>]` (QA F8 / R5-09):
    without --account the owner's session, as before; with it, that account's session
    (its own jar when its LinkedIn section is 'own', else the owner's)."""
    import argparse
    parser = argparse.ArgumentParser(description="Refresh a logged-in LinkedIn browser session.")
    parser.add_argument("--account", help="account name (case-insensitive) or id; default: the owner")
    args = parser.parse_args(argv)
    if not args.account:
        return _run()
    found = resolve_account(args.account)
    if found is None:
        print(f"No single account named or with id {args.account!r}.", file=sys.stderr)
        return 1
    account_id, name, note = found
    from backend.accounts.context import use_account
    print(f"Refreshing the LinkedIn session of {name}.", flush=True)
    if note:
        print(note, flush=True)
    with use_account(account_id):
        return _run()


if __name__ == "__main__":
    sys.exit(main())
