# Changelog

All notable changes to JobNavigator are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] — 2026-08-16

### Added
- **Application Autofill** — generate a first-person answer to any free-text
  application question on any job site, straight from the Chrome extension.
  Focus a textarea, long text input, or rich-text editor and click the Navigator
  button; the answer is grounded in your **Persona** (contact, work
  authorization, preferences, résumé content) plus your saved **Q&A bank** — no
  résumé text is fed in. Review it in a popover with a live character counter and
  a length picker, then **Insert**, **Copy**, or **Save to bank** for reuse. The
  field button morphs pill → loader → check in place as the answer generates.
- Backend: `POST /api/autofill/answer` (persona-grounded, prompt-cached) and
  `POST /api/persona/qa-bank` (append to the reusable Q&A bank).
- Settings → AI: an **Application Autofill** section — LLM provider/model,
  default answer length, and an editable prompt.

## [1.0.0] — 2026-08-15

First stable release. Self-hosted job-hunt automation: scrape boards and career
pages, score jobs against your résumés with Claude, tailor résumés and cover
letters, capture LinkedIn roles via a Chrome extension, monitor Gmail for
replies, and manage it all from a React dashboard.

### Added
- **Discovery:** 6 scraping tiers — JobSpy (LinkedIn/Indeed/ZipRecruiter/Google),
  Levels.fyi, LinkedIn collections, Jobright.ai, direct career pages (auto-detects
  Workday, Greenhouse, Ashby, Lever, Oracle HCM, SmartRecruiters, Rippling, and
  more), and the "Navigator" Chrome extension. Two-layer dedup.
- **AI:** per-résumé scoring (5-criteria rubric, apply recommendations), grounded
  résumé tailoring and cover-letter generation, click-tracking tracer links.
- **Tracking:** Kanban application board, status-transition history, Gmail
  response monitoring, Telegram alerts/digests.
- **Dashboard:** React + Tailwind (dark mode), keyboard-driven Job Feed, editable
  settings (LLM providers/models, rubric, filters) — only secrets live in `.env`.

[Unreleased]: https://github.com/vesaias/JobNavigator/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/vesaias/JobNavigator/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/vesaias/JobNavigator/releases/tag/v1.0.0
