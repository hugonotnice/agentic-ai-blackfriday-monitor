# Security Policy

## Local-First Security Practices

1. **Never commit `.env` or `harness.env`:** Store PriceBuddy `APP_KEY`, admin passwords, Sanctum API tokens, and `TELEGRAM_BOT_TOKEN` only in untracked local files.
2. **Loopback binding by default:** `docker-compose.yml` binds services strictly to `127.0.0.1` (`8080` and `3035`) so the admin dashboard and Playwright scraper are not exposed to your LAN or the public internet.
3. **Database & cookie isolation:** Runtime SQLite databases (`storage/database.sqlite`, `storage/harness.sqlite`) and session files stay local and are excluded via `.gitignore`.
4. **No autonomous checkout:** This tool monitors prices and sends read-only deal alerts; it never stores payment cards or places orders automatically.

## Reporting a Vulnerability

If you discover a security issue, credential leak, or unsafe default, please open a private security advisory on GitHub or contact the maintainer directly before disclosing publicly.
