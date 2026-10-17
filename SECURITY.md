# Security

JobNavigator is a personal, self-hosted project maintained on a best-effort basis. There are no security guarantees, no SLA and no bug bounty. Run it at your own risk.

## Supported versions

Only the latest release, best effort. Older releases get no fixes; upgrade first.

## Reporting

- Something sensitive (auth bypass, a leak between accounts, credential exposure, SSRF/XSS/injection): use GitHub's [private vulnerability reporting](https://github.com/vesaias/JobNavigator/security/advisories/new).
- Anything else (hardening ideas, dependency bumps): a normal issue or PR.

Out of scope: a compromised host, scraping-terms concerns (see [LEGAL_DISCLAIMER.md](LEGAL_DISCLAIMER.md)), upstream library bugs, rate limits or bans from third-party sites.

## Running it safely

- Keep it on `localhost` or a private network, or put it behind your own authentication (VPN, reverse-proxy auth). It is not built to face the internet.
- Set an install password (Settings › System › Advanced › dashboard API key) right after the first login; with none set, the dashboard is open.
- Account PINs (optional, 4 digits, scrypt-hashed, a 30 s wait after five wrong tries) keep household members out of each other's accounts. They are a privacy latch, not protection against a determined person who has the install password.
- Secrets that only the server needs go in `.env`. API keys and tokens entered in Settings (LLM providers, Apify, Telegram, Gmail, LinkedIn, Jobright) are stored in plaintext in the database and redacted in API responses.
- Database backups (`backups/`) contain those credentials: treat them like `.env`.
