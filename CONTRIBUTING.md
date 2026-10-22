# Contributing

PRs welcome. No formal process: open a PR, we iterate.

## Setup

```bash
git clone https://github.com/vesaias/JobNavigator.git
cd JobNavigator
cp .env.example .env
docker compose up --build -d          # dashboard at http://localhost, blank key on first run
```

- Backend: volume-mounted, no hot reload. `docker compose restart backend` after a `.py` change.
- Frontend: built into its image. `docker compose up -d --build --no-deps frontend` after a change.
- Node is only needed inside Docker.

## Tests

```bash
docker compose exec -T backend sh -c "cd /app && python -m pytest backend/tests -q -p no:cacheprovider -m 'not live'"
bash tests/tools/fe-test.sh           # frontend Vitest (node:24-alpine one-off)
bash tests/e2e/run.sh [case-filter]   # Playwright e2e, see tests/e2e/README.md
```

Add tests with the change. Backend tests run with `MT_STRICT=1`: a query with no account context raises.

## Rules

**Accounts (multi-tenancy)**
- Owned models carry `account_id`; ordinary ORM queries are filtered and new rows stamped automatically.
- Raw SQL, `text()` and `Session.get` bypass that. Load by id with `get_owned_or_404(db, Model, id)` (`backend/accounts/owned.py`), and `owned_id_or_404` for ids in request bodies.
- Read settings with `get_setting(db, key, default)` (`backend/accounts/settings.py`), never `Setting.key == ...`. A new key gets a default in `DEFAULT_SETTINGS` (`backend/seed.py`) and a section in `_SECTIONS` (`backend/accounts/settings.py`); an unlisted key is owner-only.
- `backend/tests/test_mt_leaks.py` calls every route as one account with another account's ids. A route that is install-wide on purpose goes in `backend/tests/mt_leak_allowlist.py` with a reason.

**Frontend**
- Build screens from the primitives in `frontend/src/ui.jsx` and the tokens in `frontend/src/theme.css` ([DESIGN-SYSTEM.md](frontend/src/DESIGN-SYSTEM.md)).
- No raw colour, font, radius or shadow outside those two files: `py tests/tools/stylelint.py` must report 0.
- Feedback through `useToasts`; destructive actions through `ConfirmDialog`.

**Commits and PRs**
- Plain, descriptive commit messages; one topic per PR.
- Say what changed and how you tested it; add a screenshot for UI changes.
- Update `CHANGELOG.md` under `[Unreleased]` for anything a user would notice.

## Add a job-board source

Copy `backend/scraper/sources/trulyremote.py` (shared fetch, detail pages, preview) and its tests (`backend/tests/test_trulyremote_*.py`).

1. `backend/scraper/sources/<name>.py` exposes:
   - `async def run(search, shared=None) -> dict` returning `{"jobs_found", "new_jobs", "error", "duration"}`. It runs once per subscribing account, already scoped to that account.
   - `async def preview(search, db) -> dict` for the Test button. Cap detail pages with a module constant (`PREVIEW_DETAIL_LIMIT = 20`) and say so in `preview_note`.
2. Fetch through `shared` when it is given: `listings = await shared.get(fetch_fn)` fetches once for every account; `shared.memo` caches raw follow-up pages. Filter, dedup and save per account, never in the shared part.
3. Ingest with the shared helpers:
   - filters: `search_title_filters`, `title_kept`, `company_included`, `build_search_exclude_sets` (`_shared/filters.py`);
   - ids: `make_external_id`, `make_content_hash` (`_shared/dedup.py`);
   - `await prefetch_h1b(companies)` before the first `db.add`, then `await analyze_inline(job, db=db)` per job (`_shared/analysis.py`);
   - a job you drop after reading its page, or whose description hits a body-exclusion phrase, is saved through `mark_ignored(job)` (no `content_hash`), so its URL is never read again but saving it by hand still works.
4. Register the mode:
   - `run_search` dispatch and `_search_mode_is_valid` in `backend/scraper/orchestrator.py` (plus `_NEEDS_QUERY_MODES` if it needs a term or URL);
   - `SLOW_TEST_MODES` and a `preview` branch in `test_search` (`backend/api/routes_searches.py`);
   - to share fetches across accounts: `is_shareable_search` and `search_key` (and `search_fetch_params` if needed) in `backend/scraper/targets.py`.
5. Frontend `frontend/src/screens/Searches.jsx`: `MODES`, `MODE_OPTIONS`, `TESTABLE`, the editor fields; a badge colour pair in `theme.css` (light and dark); source labels in `JobFeed.jsx` and `Applications.jsx`.

## Add an ATS handler

Copy `backend/scraper/ats/lever.py` (pure HTTP) or `meta.py` (Playwright) and `backend/tests/test_ats_lever.py`.

1. `backend/scraper/ats/<name>.py`: `def is_<name>(url) -> bool` and `async def scrape(url, debug=False)` returning job dicts with `title` and `url` (optional `location`, `locations`, `arrangement`); with `debug=True` return `(jobs, rejected)`. Check rows with `_validate_job` (`_shared/filters.py`).
2. Register it in `backend/scraper/sources/company_pages.py`: the import, `_dispatch_ats`, the ATS chain in `_fetch_postings`, and `_needs_browser` if it is pure HTTP.
3. In `backend/api/routes_companies.py`: `detect_scrape_type` and the Test scrape chain in `test_scrape_company`.
4. Optional: a description fetcher in `backend/scraper/ats/_descriptions.py`; `detectAts` / `ATS_SLUGS` in `frontend/src/screens/Companies.jsx` for the chip.

## Add a résumé or cover-letter template

1. Copy a folder in `backend/resume_templates/` or `backend/cover_letter_templates/` (e.g. `inter/`). The folder name (`[a-z0-9_]+`) is the template id; folders are discovered on every request.
2. Files: `template.html.j2` (required), `meta.json` (`{"name", "description"}`), optional `fonts/*.ttf` (passed to the template as `fonts`, data URIs). Prefer Google Fonts `@import`; the PDF renderer blocks every other host and runs without JavaScript.
3. Keep the print layout the tests check (this fork): the top margin in `@page` equals the `.page` top padding, the `@page` bottom is `{{ footer_reserved_in | default(0) }}in` (the tailored résumé footer is drawn in that band), and `.page { padding-top: 0; padding-bottom: 0; }` sits inside `@media print`; the side padding stays on `.page`.
4. Variables: résumés get `header`, `summary`, `experience`, `skills`, `education`, `projects`, `publications`, `page_format`, `fonts`; cover letters get `header`, `recipient`, `date`, `greeting`, `body_paragraphs`, `closing`, `signature`, `page_format`, `fonts`.

To let the résumé editor restyle a template ("Select & style": fonts, sizes, spacing, colours, margins, image size, in the PDF and the Word export):

- set `"style_overrides": true` in the `docx` block of its `meta.json`;
- end its `<style>` block with `{{ style_css | default("") | safe }}`;
- mark the markup the way `professional-top-left-image/template.html.j2` does: `data-section="page"`, `"header"` and `"image"` on the page, header and picture, and `<div class="section sec-<name>" data-section="<name>">` for each section;
- make the selectors in `_STYLE_PARTS` (`backend/api/routes_resumes.py`) match your classes. `test_every_part_selector_matches_something_in_the_template` in `backend/tests/test_resume_style_overrides.py` checks that for the two Professional templates; add yours to its list;
- fonts: a template can ship a font as `fonts/<Family>-<Weight>.ttf` (`Regular`, `Bold`, `SemiBold`…); it is embedded in the PDF only when a style picks that family. Add the family to `_STYLE_FONTS` (`routes_resumes.py`) and `STYLE_FONTS` (`ResumeSections.jsx`). `.ttf` files are git-ignored, so they are not committed unless you force-add them.

Templates without the flag ignore style settings.

The `garamond/` templates are git-ignored (licensed fonts) and exist only on the maintainer's machine; `garamond_alt/` is the public equivalent.
