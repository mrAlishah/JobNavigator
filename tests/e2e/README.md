# e2e suite

A repeatable, one-command Playwright smoke
run and flows A/B, plus five new cases that pin behaviour those passes only ever
checked by hand.

```bash
bash tests/e2e/run.sh            # every case
bash tests/e2e/run.sh modals     # only cases whose name contains "modals"
bash tests/e2e/run.sh flows      # the whole flows group
JN_E2E_LIVE=1 bash tests/e2e/run.sh flows-company-crud
```

`run.sh` copies `tests/tools/h.py` plus every `.py` in this folder into
`/tmp/v2e` inside the backend container (which already has Playwright and a
browser) and runs `main.py` there against `http://caddy`. It exits non-zero if
any case fails. The optional argument is a substring filter on the case name.

Output ends with a per-case table and one summary line:

```
E2E: 13 cases · 11 passed · 0 failed · 2 skipped · 118s
```

## Cases

| case | what it covers | writes? |
|---|---|---|
| `smoke` | every v2 route at 1440×900 and 1024×700: heading, 0 console errors, 0 pageerrors, no horizontal overflow, rail counts within ~1.2 s, health dot | read-only |
| `smoke-error-state` | a stubbed 500 on each screen's own list endpoint renders a visible, non-blank error state with no pageerror | route-mocked |
| `theme` | the `theme.js` storage migration in a real browser: legacy `jobnavigator_dark_mode` → `jobnavigator_appearance`; `jobnavigator_skin` → `jobnavigator_theme` (old key removed); a light\|dark value parked in `jobnavigator_theme` moves to appearance while the skin still wins the palette; an unknown palette falls back to `default`; the modern keys are honoured and mirrored onto `.jn-v2` | localStorage only |
| `cron` | Settings' cron helper: eleven expressions from the checked table at the bottom of `frontend/src/time.js`, read live from the field without ever saving, then the setting is re-read from the API to prove it is untouched | read-only |
| `welcome` | the first-run overlay: a profile with no welcome mark sees it, dismissal writes `jobnavigator_welcomed`, a second load does not replay it, a legacy mark also suppresses it, and Escape closes it (checked over both the Feed and Stats) | localStorage only |
| `newui` | the upgrade notice (`NewUiModal.jsx`): an existing profile sees it once, Escape and "Got it" write the versioned seen-mark, a fresh profile gets the first-run tour instead; the owner's telemetry dialog waits behind either overlay and opens when it closes, with Yes preselected (closed with Escape, nothing saved); old `/classic` bookmarks land on the same page | localStorage only; `/api/telemetry` answered in the browser |
| `persona-import` | the Persona import path with `POST /api/persona/import` **and** the modal's résumé list route-mocked: the parsed result renders, the success toast names the counts, the real POST never leaves the browser, and the persona row is re-read afterwards to prove it | route-mocked |
| `feed-collapse` | the Feed's analysis fold (`jn_feed_analysis_collapsed`) survives a reload and toggles back | localStorage only |
| `modals` | six overlays — Feed keyboard shortcuts, Feed Source menu, Companies Add-company modal, Searches row ⋯ menu, Résumés New-résumé modal, Settings Model-catalog modal — each opens, closes on Escape, and closes on an outside/scrim click | opens only, submits nothing |
| `mt-switch` | MT-12 accounts: picker on `account_required` (not the sign-in), member PIN pad (wrong PIN → tries left), switch + reload + "Switched to …" toast, rail switcher menu, Ctrl+Shift+1 back with the owner PIN, win98 square avatars; QA F7: no error behind the picker, picker named + focus trap, a second tab gets the "switched in another tab" notice and a stale write is 409 account_changed. QA F7: no error behind the picker, picker named + focus trap, a second tab gets the "switched in another tab" notice and a stale write is 409 account_changed. SKIPs unless the install has exactly one account and an owner without a PIN | sets an owner PIN and creates a `ZZE Member` account, then deletes it (the API takes a pre-removal pg_dump) and removes the PIN; back up first |
| `modes-apify` | Apify searches: `GET /searches/apify-boards` (prefix, price, Test cap, 200 cap); JobSpy boards or no board → 400; a new search starts paused; `results_wanted` clamped to 200 on create and `PATCH`; the APIFY badge in its own colours, the Paused pill, the editor's cap label and paused note; Test raises a confirm naming the estimated cost and spend cap, Cancel sends no `POST …/test` (also aborted by a route guard) and starts nothing on `/monitor/active` | creates + deletes its own row; never runs |
| `modes-caribbeanjobs` | CaribbeanJobs searches: inactive create, the CARIBBEANJOBS.COM badge in its own colours, the editor's mode note, Cloudflare block and terms notice; Test is not clicked | creates + deletes its own row |
| `modes-settings-apify` | Settings › Apify: the section is a policy section, own by default; key row (empty when no key is set) and Check key; its scope control matches Jobright.ai and LinkedIn | read-only |
| `modes-caribbeanjobs-live` | **JN_E2E_LIVE only** — a real CaribbeanJobs Test from the UI: the modal shows the readable Cloudflare error or results | creates + deletes its own row |
| `modes-trulyremote` | TrulyRemoteWork searches: inactive create with no term or URL, the TRULYREMOTEWORK.COM badge in olive (not Jobright's teal), the row summary, the editor's mode note and "New jobs per run" label, no terms notice; Test is not clicked | creates + deletes its own row |
| `modes-trulyremote-live` | **JN_E2E_LIVE only** — a real TrulyRemoteWork Test (title include "engineer") from the UI: kept rows and the "worldwide remote" params | creates + deletes its own row |
| `flows-search-crud` | `POST` → read the JSON shape back → `PATCH` → `DELETE` on a `ZZE` search | creates + deletes its own row |
| `flows-monitor` | the `/monitor/active`, `/monitor/history` and `/monitor/in-flight` shapes the spinners poll on | read-only |
| `flows-pause-pill` | the status pill's Pause/Resume contract, with the `PATCH` route-mocked so nothing is written; afterwards every other search's `active` flag is re-read to prove none moved | creates + deletes its own row |
| `flows-confirm-delete` | the ⋯ menu → `ConfirmDialog` (not `window.confirm`) → Escape cancels → confirming deletes → the rail badge agrees with the API again (`jn:counts-changed`) | creates + deletes its own row |
| `flows-undo-toast` | removing a résumé section raises an undo toast, and Undo restores it both on screen and in the autosaved `json_data` | creates + deletes its own row |
| `flows-company-crud` | **JN_E2E_LIVE only** — company create/read/patch/delete | creates + deletes its own row |
| `flows-live-run` | **JN_E2E_LIVE only** — a real test scrape and a real search run, tracked through `/monitor/active` by scope key | creates + deletes its own row |
| `flows-sweep` | asserts no `ZZE` row survived the run, anywhere | read-only |

## Safety rules this suite follows

- **Every row it creates is prefixed `ZZE` and deleted again in the same case.**
  `flows-sweep` fails the run if one survives. Nothing that was not created by
  the suite is ever deleted or modified.
- **Destructive UI actions are guarded twice.** A row's control is located only
  through `_common.row_index()`, which refuses to return an index unless the
  ancestor it settled on mentions our `ZZE` marker and no other row's name; and
  before a confirm dialog is accepted, its own text must name our row. A naive
  "walk up until an ancestor mentions the name" lookup once landed on the whole
  list and deleted a real user search — hence both guards.
- **Nothing that costs money or scrapes runs by default.** Searches are created
  with `active: false` and `auto_scoring_depth: 'off'`, the scratch résumé is
  created with `is_base: false` so it can never be scored against a job someone
  else saves mid-run, and any endpoint that calls out is either route-mocked or
  behind `JN_E2E_LIVE`.
- **Prefer route mocks to writes.** `persona-import`, `flows-pause-pill`,
  `smoke-error-state` and the Model-catalog modal all answer the browser's own
  requests instead of letting them reach the backend.

## `JN_E2E_LIVE=1`

Off by default; those cases report `SKIP (needs JN_E2E_LIVE=1)`. Setting it to
`1` unlocks exactly three cases:

- **`flows-company-crud`** — `POST /api/companies` ignores any `active` value and
  stores `active=True`, then fires a background MyVisaJobs H-1B lookup: a real
  outbound request, and a row the scheduler would consider live.
- **`flows-live-run`** — runs a real JobSpy test preview and a real search run
  against live job boards, and waits for them on `/monitor/active`.
- **`modes-caribbeanjobs-live`** — a real CaribbeanJobs Test (caribbeanjobs.com,
  up to 20 detail pages). No case ever runs a paid Apify Test or run.
- **`modes-trulyremote-live`** — a real TrulyRemoteWork Test (the corpus, cached
  in the backend for up to an hour, and up to 20 `.md` job pages).

All four still create only `ZZE` rows and delete them again.

## Environment

| var | default | meaning |
|---|---|---|
| `JN_KEY` | `pick-a-password` | dashboard API key (`X-API-Key`) |
| `JN_BASE` | `http://caddy` | base URL as seen from inside the container |
| `JN_E2E_LIVE` | unset | `1` unlocks the two live cases above |
| `JN_BACKEND` | `jtrakproject-backend-1` | backend container name |
| `DOCKER` | Docker Desktop's `docker.exe` | docker binary |

## Files

- `run.sh` — the one command; copies files in and runs them
- `main.py` — imports every case module (import order is run order) and exits with
  the runner's status
- `_suite.py` — `case()` registration, per-case try/except, `check()`/`eq()`/`skip()`,
  the per-case table and the summary line
- `_common.py` — browser contexts that speak the current storage keys, the page
  probes (overflow, rail, console, overlays), `row_index()`, and the `ZZE` bookkeeping
- `case_*.py` — one file per group

`h.py` seeds the legacy `jobnavigator_dark_mode` key, so
`_common.mpage()` / `seeded_page()` set `jobnavigator_appearance` +
`jobnavigator_theme` through `extra_ls` instead. Use `seeded_page()` (not
`mpage()`) in any case that reloads — `h.context()`'s init script re-seeds on
every navigation and would mask what the app itself wrote.

Overlays: `h.context()` marks the upgrade notice seen (`jobnavigator_newui_seen`),
and every context (`h.context()`, `_common.raw_ctx()`) answers `GET /api/telemetry`
with `asked: true` (`h.telemetry_answer`), so the owner's one-time telemetry dialog
never covers a screen under test and the install's real choice is never read or
written. A case that tests the dialog passes `raw_ctx(..., telemetry={...})`
(`case_newui.py` does: the dialog comes right after the upgrade notice or the
first-run tour).
