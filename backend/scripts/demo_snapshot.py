"""Capture 1:1, self-contained snapshots of the featured demo postings (one page at a time, politely).

    python -m backend.scripts.demo_snapshot <export.json> <out_dir> [--want 15] [--prefer "Acme,Beta"] [--delay 1.5]

Each posting is opened in Playwright, rendered, and saved as one HTML file with its styles, fonts and
images inlined as data URIs and every script, frame and event handler removed. Writes
<out_dir>/snapshots/<job_id>.html and <out_dir>/snapshots/index.json (ok ids in order, failures with reasons).
Failures are listed, never fatal; the run stops once --want snapshots succeeded.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString

from backend.scripts.demo_export import sanitize

# Boards that keep the URL alive after a posting closes (Greenhouse shows "The job you are looking for is no longer open").
EXPIRED = re.compile(r"no longer (?:open|available|accepting)|(?:job|position|posting) (?:has been|is) (?:closed|filled)", re.I)

CSP = ("default-src 'none'; style-src 'unsafe-inline' data:; img-src data:; font-src data:; "
       "media-src 'none'; frame-src 'none'; form-action 'none'")
MAX_RESOURCE = 2_500_000
MAX_FONT = 700_000
MAX_SNAPSHOT = 4_500_000
BAD_NODES = re.compile(r"cookie|consent|onetrust|cookiebot|gdpr|truste|intercom|drift-|hubspot-messages|chat-widget", re.I)
URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
EMPTY = "data:,"


def _data_uri(res):
    ctype, body = res
    return f"data:{ctype or 'application/octet-stream'};base64,{base64.b64encode(body).decode()}"


def _lookup(resources, url):
    return resources.get(url) or resources.get(url.split("#")[0])


def rewrite_css(css: str, css_url: str, resources: dict) -> str:
    """Inline every url() in a stylesheet that was captured; point the rest at an empty data URI."""
    def sub(m):
        ref = m.group(2).strip()
        if not ref or ref.startswith(("data:", "#")):
            return m.group(0)
        res = _lookup(resources, urljoin(css_url, ref))
        return f'url("{_data_uri(res)}")' if res else f'url("{EMPTY}")'
    css = re.sub(r"@import\s+(?:url\([^)]*\)|['\"][^'\"]*['\"])[^;]*;", "", css)
    return URL_RE.sub(sub, css)


def _scrub(soup) -> None:
    """Strip contact details and names from text nodes and from descriptive attributes; drop mailto links."""
    for text in soup.find_all(string=True):
        if isinstance(text, NavigableString) and text.parent and text.parent.name not in ("style", "title"):
            clean = sanitize(str(text))
            if clean != str(text):
                text.replace_with(clean)
    for tag in soup.find_all(True):
        for attr in ("content", "alt", "title", "aria-label", "placeholder", "value"):
            if isinstance(tag.get(attr), str):
                tag[attr] = sanitize(tag[attr])
        if tag.name == "a" and str(tag.get("href", "")).lower().startswith(("mailto:", "tel:")):
            del tag["href"]


def rescrub(html: str) -> str:
    """Apply the scrub to a snapshot that was saved before the attribute pass existed."""
    soup = BeautifulSoup(html, "html.parser")
    _scrub(soup)
    return str(soup)


def inline_snapshot(html: str, base_url: str, resources: dict) -> str:
    """Pure function: rendered DOM + captured resources ({absolute url: (content_type, bytes)}) -> offline page."""
    soup = BeautifulSoup(html, "html.parser")

    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for tag in soup.find_all(["script", "noscript", "iframe", "frame", "object", "embed", "video", "audio", "base", "template"]):
        tag.decompose()
    for tag in soup.find_all("meta", attrs={"http-equiv": re.compile("refresh|content-security", re.I)}):
        tag.decompose()
    for node in soup.find_all(attrs={"id": BAD_NODES}) + soup.find_all(attrs={"class": BAD_NODES}):
        if node.name not in ("html", "body", "main"):
            node.decompose()

    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel") or []).lower()
        href = link.get("href") or ""
        if "stylesheet" in rel and href:
            url = urljoin(base_url, href)
            res = _lookup(resources, url)
            if res:
                style = soup.new_tag("style")
                style.string = rewrite_css(res[1].decode("utf-8", "replace"), url, resources)
                link.replace_with(style)
                continue
        link.decompose()

    for style in soup.find_all("style"):
        if style.string:
            style.string = rewrite_css(style.string, base_url, resources)

    for tag in soup.find_all(True):
        for attr in [a for a in tag.attrs if a.lower().startswith("on")]:
            del tag.attrs[attr]
        if "style" in tag.attrs and isinstance(tag["style"], str):
            tag["style"] = rewrite_css(tag["style"], base_url, resources)
        if tag.name in ("img", "source"):
            src = tag.get("src") or tag.get("data-src") or ""
            res = None if not src or src.startswith("data:") else _lookup(resources, urljoin(base_url, src))
            if src.startswith("data:"):
                pass
            elif res:
                tag["src"] = _data_uri(res)
            else:
                tag.attrs.pop("src", None)
            for a in ("srcset", "data-src", "data-srcset", "loading", "sizes"):
                tag.attrs.pop(a, None)
        if tag.name == "a":
            href = tag.get("href") or ""
            if href.lower().startswith("javascript:"):
                del tag["href"]
            elif href and not href.startswith(("#", "mailto:", "tel:")):
                tag["href"] = urljoin(base_url, href)
                tag["rel"] = "noopener noreferrer"
                tag["target"] = "_blank"
        if tag.name == "form":
            tag.attrs.pop("action", None)

    _scrub(soup)

    head = soup.head or soup.new_tag("head")
    if soup.head is None:
        (soup.html or soup).insert(0, head)
    meta = soup.new_tag("meta")
    meta.attrs.update({"http-equiv": "Content-Security-Policy", "content": CSP})
    head.insert(0, meta)
    charset = soup.new_tag("meta", charset="utf-8")
    head.insert(0, charset)
    return "<!doctype html>\n" + str(soup)


def _title_ok(text: str, title: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9&]+", title.lower()) if len(w) > 2 and w not in ("the", "and", "for", "senior")]
    low = text.lower()
    return bool(words) and sum(1 for w in words if w in low) / len(words) >= 0.7


async def capture(page_ctx, url: str, title: str, delay: float) -> tuple[str, str | None]:
    """(html, error). Opens one page and returns the offline snapshot."""
    resources: dict = {}
    page = await page_ctx.new_page()

    async def on_response(resp):
        try:
            if resp.request.resource_type not in ("stylesheet", "font", "image") or resp.status != 200:
                return
            body = await resp.body()
            limit = MAX_FONT if resp.request.resource_type == "font" else MAX_RESOURCE
            if body and len(body) <= limit:
                ctype = (resp.headers.get("content-type") or "").split(";")[0] or None
                resources[resp.url] = (ctype, body)
        except Exception:
            pass

    page.on("response", on_response)
    try:
        try:
            await page.goto(url, wait_until="networkidle", timeout=45_000)
        except Exception:
            await page.wait_for_timeout(4000)
        await page.evaluate("async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); } window.scrollTo(0, 0); }")
        await page.wait_for_timeout(1200)
        text = await page.evaluate("document.body ? document.body.innerText : ''")
        if len(text) < 800 or not _title_ok(text, title) or EXPIRED.search(text):
            return "", "page does not show the posting (expired, redirected or blocked)"
        final_url = page.url
        html = await page.content()
    finally:
        await page.close()
    return inline_snapshot(html, final_url, resources), None


async def run(export_path: Path, out_dir: Path, want: int, prefer: list[str], delay: float) -> dict:
    from playwright.async_api import async_playwright

    data = json.loads(export_path.read_text(encoding="utf-8"))
    pool = data["featured_pool"]
    def rank(job):  # a company matches a preferred name by prefix ("Scribd" -> "Scribd, Inc.")
        c = job["company"].lower()
        return next((i for i, n in enumerate(prefer) if c.startswith(n.lower())), len(prefer))
    pool = sorted(pool, key=rank) if prefer else pool
    snaps = out_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    ok, failed = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1280, "height": 1600}, locale="en-US")
        for job in pool:
            if len(ok) >= want:
                break
            try:
                html, err = await capture(ctx, job["url"], job["title"], delay)
                if not err and len(html.encode()) > MAX_SNAPSHOT:
                    err = f"snapshot too large ({len(html) // 1000} kB)"
            except Exception as e:  # noqa: BLE001 - one bad page must not stop the run
                html, err = "", f"{type(e).__name__}: {e}"[:200]
            if err:
                failed.append({"id": job["id"], "company": job["company"], "reason": err})
                print(f"FAIL {job['company']}: {err}")
            else:
                (snaps / f"{job['id']}.html").write_text(html, encoding="utf-8")
                ok.append(job["id"])
                print(f"ok   {job['company']} ({len(html) // 1000} kB)")
            await asyncio.sleep(delay)
        await browser.close()
    index = {"ok": ok, "failed": failed}
    (snaps / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    return index


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("export_json")
    ap.add_argument("out_dir")
    ap.add_argument("--want", type=int, default=15)
    ap.add_argument("--prefer", default="")
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--rescrub", action="store_true", help="re-run the scrub over the saved snapshots in out_dir/snapshots and exit")
    a = ap.parse_args(argv)
    if a.rescrub:
        for f in (Path(a.out_dir) / "snapshots").glob("*.html"):
            f.write_text(rescrub(f.read_text(encoding="utf-8")), encoding="utf-8")
        return 0
    prefer = [p.strip() for p in a.prefer.split(",") if p.strip()]
    idx = asyncio.run(run(Path(a.export_json), Path(a.out_dir), a.want, prefer, a.delay))
    print(f"{len(idx['ok'])} snapshots, {len(idx['failed'])} failed")
    return 0 if len(idx["ok"]) >= a.want else 1


if __name__ == "__main__":
    sys.exit(main())
