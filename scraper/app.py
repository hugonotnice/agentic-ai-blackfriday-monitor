import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bf-scraper")

app = Flask(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"service": "playwright-arm64-scraper", "status": "healthy"}), 200


@app.route("/api/article", methods=["GET"])
def get_article():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400

    full_content = request.args.get("full-content", "true").lower() in ("1", "true", "yes")
    sleep_ms = min(int(request.args.get("sleep", "2000")), 6000)
    timeout_ms = min(int(request.args.get("timeout", "25000")), 45000)

    final_url = url
    html_content = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
            ],
        )
        try:
            context = browser.new_context(
                user_agent=UA,
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                viewport={"width": 1366, "height": 900},
                extra_http_headers={
                    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()
            page.route(
                "**/*.{woff,woff2,mp4,webm,mp3}",
                lambda route: route.abort(),
            )
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception as e:
                logger.warning(f"goto partial/timeout for {url}: {e}")

            try:
                page.wait_for_load_state("networkidle", timeout=min(5000, timeout_ms))
            except Exception:
                pass

            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)

            final_url = page.url or url
            html_content = page.content()
        finally:
            browser.close()

    soup = BeautifulSoup(html_content, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    result_id = hashlib.md5(final_url.encode()).hexdigest()

    return jsonify(
        {
            "id": result_id,
            "url": final_url,
            "domain": urlparse(final_url).netloc,
            "title": title,
            "fullContent": html_content if full_content else None,
            "content": html_content[:2000],
            "date": datetime.now(timezone.utc).isoformat(),
            "resultUri": f"api://article/{result_id}",
        }
    ), 200
