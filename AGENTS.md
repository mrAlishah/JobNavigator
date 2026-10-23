# Personal SoT Bootstrap

Canonical Personal SoT:

- Repository: `mrAlishah/myPersonal-SoT`
- Ref: `main`
- Entrypoint: `workspace/adapters/runtime_entrypoint.md`
- Local checkout: `~/Projects/myPersonal-SoT`

Before planning implementation, changing code, or performing mutating Git operations:

1. Prefer the current local checkout when it exists and is readable.
2. Otherwise use authenticated access to the private GitHub repository.
3. Read the canonical entrypoint and follow its referenced contracts.
4. Load and follow `workspace/context/personal/policies/development_conventions.md`.
5. Resolve only the additional SoT context, policies, profiles, and contracts required by the current task.
6. Treat the current canonical SoT as authoritative; do not substitute remembered, copied, or stale instructions.

If neither canonical SoT source is accessible, report the limitation and stop before dependent code or Git mutations.

Repository-owned code, tests, configuration, and repository-specific instructions remain authoritative for this repository and must be reconciled with applicable Personal SoT policy.

## Change log

Record every feature, change, fix or removal made in this fork in `myCHANGELOG.md`
(repository root, Keep a Changelog format) as part of the same change:

- add it under `## [Unreleased]` in `### Added`, `### Changed`, `### Fixed` or `### Removed`;
- one bullet per change: a bold lead, then what the user sees or what now behaves differently;
- do not edit `CHANGELOG.md`; it follows upstream releases, and fork entries there turn into merge conflicts;
- if the file is missing on the current branch, create it with the header of the existing `myCHANGELOG.md`.
