# Architecture Overview

## System Boundaries

Agentic AI Blackfriday Monitor separates price collection, anti-trap verification, human feedback learning, and user presentation into four decoupled layers so no single scraper failure or store layout change corrupts target alerts.

### 1. PriceBuddy Core Container

- **Runtime:** Laravel web dashboard and REST API backed by local SQLite.
- **SQLite `json_each` patch:** `patches/Store.php` replaces MySQL-only `whereJsonContains` domain filters with SQLite `json_each` expressions so store domain matching works without PostgreSQL or MySQL.
- **Traffic-light UI patches:** `patches/product-card.blade.php` renders distance-to-target badges (green at or below target, yellow within +15%, red above +15%), while `patches/product-badges.blade.php` suppresses false positive percentage-match badges when current price exceeds target price.

### 2. ARM64 Playwright Scraper Container

- **Runtime:** Python Flask + Gunicorn + Chromium inside `scraper/` exposing `/health` and `/api/article`.
- **Anti-bot mitigation:** Masks `navigator.webdriver`, configures locale headers, and blocks heavy font/media payloads (`woff2`, `mp4`, `webm`, `mp3`) to keep headless renders fast on Apple Silicon and x86_64 hosts.

### 3. Python Verification & Telegram Harness

- **Store REST sync:** `harness.py` triggers `/api/meta-extraction` with `heal=false` so pre-calibrated installment selectors are never overwritten by cash-only prices, and records out-of-range failures into `store_health`.
- **Anti-Pix / cash-only gate:** Rejects cash-only discounts when installment markers are absent, and selects the installment price whenever both cash and installment prices appear together.
- **Installment-unit stripping:** Removes fragments such as `10x R$ 129,90` before parsing total prices, preventing monthly installment amounts from triggering fake discount alerts.
- **Public Telegram ingestion:** Polls `t.me/s/<channel>` HTML feeds without requiring personal MTProto session credentials.
- **Human feedback loop:** Telegram alerts include inline `Good alert` and `False positive` buttons. Callbacks update `feedback_rules` in SQLite to auto-block noisy keywords or raise `min_price_floor` dynamically.

### 4. Native macOS Companions

- **Dock window launcher:** `/Applications/PriceBuddy.app` wraps Chrome `--app` mode for a clean standalone window.
- **Menu Bar status item:** `macos/PriceBuddyBar.m` compiles into an ~80 KB Objective-C / Cocoa `LSUIElement` binary polling `/api/products` every 60 seconds and showing live traffic-light counts in the top menu bar.
