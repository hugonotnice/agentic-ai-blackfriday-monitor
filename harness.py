#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from harness_db import init_db, now_iso

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / "harness.env"
DEFAULT_TELEGRAM_CHANNELS = [
    "pelandobr",
    "promobit",
    "hardmoboficial",
    "benchpromos",
    "promotop",
    "pechinchou",
    "economizzando",
]

# Detect single installment like "10x de R$ 129,90" or "12x R$ 39,90"
INSTALLMENT_UNIT_RE = re.compile(
    r"(?i)(?:em\s*(?:at[eé]\s*)?)?(\d{1,2})\s*x\s*(?:de\s*)?R\$\s*([\d\.]+(?:,\d{1,2})?)"
)
PRICE_RE = re.compile(r"R\$\s*([\d\.]+(?:,\d{1,2})?)")
PIX_MARKERS = [
    "no pix",
    "via pix",
    "à vista no pix",
    "a vista no pix",
    "somente pix",
    "exclusivo pix",
    "boleto ou pix",
    "pix/boleto",
    "cash only",
]
INSTALLMENT_OK_MARKERS = [
    "sem juros",
    "parcelado",
    "no cartao",
    "no cartão",
    "em ate",
    "em até",
]


def load_env(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    if not path.exists():
        return vals
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def get_config() -> dict[str, str]:
    cfg = load_env(ENV_FILE)
    extra_path_str = os.environ.get("EXTRA_ENV_PATH") or cfg.get("EXTRA_ENV_PATH", "")
    extra_cfg = load_env(Path(extra_path_str).expanduser()) if extra_path_str else {}
    return {**extra_cfg, **cfg, **os.environ}


def get_telegram_channels(cfg: dict[str, str]) -> list[str]:
    raw = cfg.get("TELEGRAM_CHANNELS", "").strip()
    if not raw:
        return DEFAULT_TELEGRAM_CHANNELS
    return [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]


def normalize_text(txt: str) -> str:
    nfkd = unicodedata.normalize("NFKD", txt.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def parse_brl(raw: str) -> Optional[float]:
    s = re.sub(r"[^\d,\.]", "", raw)
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") == 1:
        parts = s.split(".")
        if len(parts[1]) == 3 and parts[0].lstrip("0") != "":
            s = parts[0] + parts[1]
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def retry_call(fn, attempts: int = 3, base_delay: float = 1.5):
    last_err = None
    for idx in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_err = exc
            if idx < attempts - 1:
                time.sleep(base_delay * (2**idx))
    raise last_err


def telegram_send(cfg: dict[str, str], text: str, alert_id: Optional[int] = None) -> tuple[str, Optional[int]]:
    token = cfg.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = cfg.get("TELEGRAM_HOME_CHANNEL", "").strip()
    thread_id = cfg.get("TELEGRAM_HOME_CHANNEL_THREAD_ID", "").strip()
    if not token or not chat_id:
        return "pending_token", None

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = int(thread_id)
    if alert_id is not None:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [
                    {"text": "Good alert", "callback_data": f"fb:good:{alert_id}"},
                    {"text": "False positive", "callback_data": f"fb:fp:{alert_id}"},
                ]
            ]
        }

    r = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=20.0,
    )
    if r.status_code == 200:
        data = r.json()
        return "sent", data.get("result", {}).get("message_id")
    return f"error_{r.status_code}", None


def record_feedback(conn: sqlite3.Connection, alert_id: int, verdict: str, extra_keyword: Optional[str] = None) -> str:
    row = conn.execute("SELECT * FROM alert_log WHERE id = ?", (alert_id,)).fetchone()
    if not row:
        return f"Alert #{alert_id} not found"

    fb_val = "good" if verdict in ("good", "bom") else "false_positive"
    conn.execute("UPDATE alert_log SET feedback = ? WHERE id = ?", (fb_val, alert_id))
    ts = now_iso()

    if row["deal_id"]:
        deal = conn.execute("SELECT * FROM telegram_deals WHERE id = ?", (row["deal_id"],)).fetchone()
        if deal:
            new_status = "feedback_good" if fb_val == "good" else "feedback_false_positive"
            conn.execute("UPDATE telegram_deals SET status = ? WHERE id = ?", (new_status, deal["id"]))

    if fb_val == "false_positive":
        if extra_keyword:
            conn.execute(
                """
                INSERT INTO feedback_rules (rule_type, pattern, product_id, source_deal_id, notes, hits, active, created_at)
                VALUES ('exclude_keyword', ?, ?, ?, 'Blocked via human feedback', 1, 1, ?)
                ON CONFLICT(rule_type, pattern, COALESCE(product_id, 0)) DO UPDATE SET hits = hits + 1, active = 1
                """,
                (extra_keyword.lower(), row["product_id"], row["deal_id"], ts),
            )
        else:
            floor_cand = min(round(row["price"] * 1.05, 2), round(row["normal_price"] * 0.65, 2))
            conn.execute(
                """
                INSERT INTO feedback_rules (rule_type, pattern, product_id, source_deal_id, notes, hits, active, created_at)
                VALUES ('min_price_floor', ?, ?, ?, 'Price floor raised after false positive', 1, 1, ?)
                ON CONFLICT(rule_type, pattern, COALESCE(product_id, 0)) DO UPDATE SET hits = hits + 1, active = 1
                """,
                (str(floor_cand), row["product_id"], row["deal_id"], ts),
            )
    else:
        conn.execute(
            """
            INSERT INTO feedback_rules (rule_type, pattern, product_id, source_deal_id, notes, hits, active, created_at)
            VALUES ('confirmed_source', ?, ?, ?, 'Validated by user feedback', 1, 1, ?)
            ON CONFLICT(rule_type, pattern, COALESCE(product_id, 0)) DO UPDATE SET hits = hits + 1
            """,
            (row["source"], row["product_id"], row["deal_id"], ts),
        )

    conn.commit()
    return f"Feedback '{fb_val}' saved in SQLite for alert #{alert_id}"


def poll_telegram_feedback(conn: sqlite3.Connection, cfg: dict[str, str]) -> int:
    token = cfg.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return 0
    row = conn.execute("SELECT value FROM bot_state WHERE key = 'tg_offset'").fetchone()
    offset = int(row["value"]) if row else 0
    try:
        r = httpx.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": 2, "allowed_updates": '["callback_query"]'},
            timeout=10.0,
        )
        if r.status_code != 200:
            return 0
        updates = r.json().get("result", [])
    except Exception:
        return 0

    processed = 0
    max_uid = offset
    for upd in updates:
        uid = upd.get("update_id", 0)
        if uid >= max_uid:
            max_uid = uid + 1
        cb = upd.get("callback_query")
        if not cb:
            continue
        data = cb.get("data", "")
        if data.startswith("fb:"):
            parts = data.split(":")
            if len(parts) == 3 and parts[2].isdigit():
                verdict = parts[1]
                aid = int(parts[2])
                msg = record_feedback(conn, aid, verdict)
                processed += 1
                try:
                    httpx.post(
                        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                        json={"callback_query_id": cb["id"], "text": msg[:180]},
                        timeout=10.0,
                    )
                except Exception:
                    pass
    if max_uid > offset:
        conn.execute(
            "INSERT INTO bot_state (key, value, updated_at) VALUES ('tg_offset', ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (str(max_uid), now_iso()),
        )
        conn.commit()
    return processed


def store_slug_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "amazon." in host:
        return "amazon-br"
    if "kabum." in host:
        return "kabum"
    if "flexform." in host:
        return "flexform"
    if "mercadolivre." in host:
        return "mercado-livre"
    if "aliexpress." in host:
        return "aliexpress"
    return host


def mark_store_result(conn: sqlite3.Connection, cfg: dict[str, str], store_slug: str, ok: bool, err_msg: str = ""):
    ts = now_iso()
    if ok:
        conn.execute(
            """
            INSERT INTO store_health (store_slug, consecutive_failures, last_error, last_success_at, alerted_outage)
            VALUES (?, 0, NULL, ?, 0)
            ON CONFLICT(store_slug) DO UPDATE SET
                consecutive_failures = 0,
                last_error = NULL,
                last_success_at = excluded.last_success_at,
                alerted_outage = 0
            """,
            (store_slug, ts),
        )
        conn.commit()
        return

    conn.execute(
        """
        INSERT INTO store_health (store_slug, consecutive_failures, last_error, last_failure_at, alerted_outage)
        VALUES (?, 1, ?, ?, 0)
        ON CONFLICT(store_slug) DO UPDATE SET
            consecutive_failures = consecutive_failures + 1,
            last_error = excluded.last_error,
            last_failure_at = excluded.last_failure_at
        """,
        (store_slug, err_msg[:300], ts),
    )
    row = conn.execute("SELECT * FROM store_health WHERE store_slug = ?", (store_slug,)).fetchone()
    if row and row["consecutive_failures"] >= 3 and row["alerted_outage"] == 0:
        msg = f"Operational Alert: store {store_slug} failed {row['consecutive_failures']} consecutive times. Last error: {err_msg[:180]}"
        telegram_send(cfg, msg, alert_id=None)
        conn.execute("UPDATE store_health SET alerted_outage = 1 WHERE store_slug = ?", (store_slug,))
    conn.commit()


def recently_alerted(conn: sqlite3.Connection, product_id: int, price: float) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        """
        SELECT id FROM alert_log
        WHERE product_id = ? AND created_at >= ? AND ABS(price - ?) <= 5.0
        ORDER BY id DESC LIMIT 1
        """,
        (product_id, cutoff, price),
    ).fetchone()
    return row is not None


def create_alert(
    conn: sqlite3.Connection,
    cfg: dict[str, str],
    source: str,
    product_id: int,
    deal_id: Optional[int],
    model_name: str,
    price: float,
    normal_price: float,
    target_price: float,
    url: str,
    note: str = "",
) -> int:
    discount_pct = round(((normal_price - price) / normal_price) * 100.0, 1)
    lines = [
        f"BLACK FRIDAY ALERT: {model_name}",
        f"Validated price: {price:,.2f}",
        f"Baseline: {normal_price:,.2f} | Target: {target_price:,.2f} | Real drop: {discount_pct:.1f}%",
        f"Source: {source}" + (f" | {note}" if note else ""),
        f"Link: {url}",
    ]
    msg_text = "\n".join(lines)
    cur = conn.execute(
        """
        INSERT INTO alert_log (source, product_id, deal_id, price, normal_price, discount_pct, url, message_text, delivery_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_token', ?)
        """,
        (source, product_id, deal_id, price, normal_price, discount_pct, url, msg_text, now_iso()),
    )
    alert_id = cur.lastrowid
    status, tg_msg_id = telegram_send(cfg, msg_text, alert_id=alert_id)
    conn.execute(
        "UPDATE alert_log SET delivery_status = ?, telegram_message_id = ? WHERE id = ?",
        (status, tg_msg_id, alert_id),
    )
    conn.commit()
    return alert_id


def check_pricebuddy_products(conn: sqlite3.Connection, cfg: dict[str, str], refresh_live: bool = False) -> dict[str, Any]:
    pb_url = cfg.get("PRICEBUDDY_URL", "http://127.0.0.1:8080").rstrip("/")
    pb_token = cfg.get("PRICEBUDDY_API_TOKEN", "").strip()
    pb_container = cfg.get("PRICEBUDDY_CONTAINER", "bf-pricebuddy").strip()
    headers = {"Authorization": f"Bearer {pb_token}", "Accept": "application/json"}

    baselines = {r["product_id"]: r for r in conn.execute("SELECT * FROM product_baselines WHERE active = 1").fetchall()}
    r = httpx.get(f"{pb_url}/api/products?per_page=100", headers=headers, timeout=20.0)
    r.raise_for_status()
    api_prods = {p["id"]: p for p in r.json().get("data", [])}

    stats = {"checked": 0, "refreshed": 0, "alerts": 0, "errors": 0}
    fetch_jobs: list[tuple[int, dict[str, Any]]] = []

    for pid in baselines:
        stats["checked"] += 1
        prod = api_prods.get(pid)
        if not prod or prod.get("paused"):
            continue
        if refresh_live:
            for pc in prod.get("price_cache") or []:
                url = pc.get("url") or ""
                slug = store_slug_from_url(url)
                if slug not in ("mercado-livre", "aliexpress") and url:
                    fetch_jobs.append((pid, pc))

    refreshed_rows: list[dict[str, Any]] = []
    if refresh_live and fetch_jobs:
        def _run_fetch(job):
            pid, pc = job
            u = pc["url"]

            def _extract():
                resp = httpx.post(
                    f"{pb_url}/api/meta-extraction",
                    headers=headers,
                    json={"url": u, "heal": False},
                    timeout=45.0,
                )
                resp.raise_for_status()
                return resp.json()

            res = retry_call(_extract, attempts=3, base_delay=1.5)
            return pid, pc, res.get("data", {}).get("price")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = {pool.submit(_run_fetch, j): j for j in fetch_jobs}
            for fut in as_completed(futs):
                pid, pc = futs[fut]
                slug = store_slug_from_url(pc.get("url", ""))
                try:
                    _, _, new_price = fut.result()
                    if new_price is not None:
                        new_price = float(new_price)
                        refreshed_rows.append(
                            {
                                "product_id": pid,
                                "url_id": pc.get("url_id"),
                                "store_id": pc.get("store_id"),
                                "price": new_price,
                            }
                        )
                        stats["refreshed"] += 1
                        mark_store_result(conn, cfg, slug, True)
                    else:
                        mark_store_result(conn, cfg, slug, False, f"No price returned for {pc.get('url')}")
                        stats["errors"] += 1
                except Exception as exc:
                    mark_store_result(conn, cfg, slug, False, str(exc))
                    stats["errors"] += 1

    if refreshed_rows:
        payload_b64 = base64.b64encode(json.dumps(refreshed_rows).encode()).decode()
        php_script = f"""
require '/app/vendor/autoload.php';
$app = require '/app/bootstrap/app.php';
$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();
$rows = json_decode(base64_decode('{payload_b64}'), true);
foreach ($rows as $row) {{
    $p = App\\Models\\Product::find($row['product_id']);
    if (! $p) continue;
    $p->current_price = $row['price'];
    $p->save();
    App\\Models\\Price::create([
        'price' => $row['price'],
        'url_id' => $row['url_id'],
        'store_id' => $row['store_id'],
        'notified' => false,
        'unit_price' => $row['price'],
        'price_factor' => 1.0,
    ]);
}}
"""
        subprocess.run(["docker", "exec", pb_container, "php", "-r", php_script], check=False, capture_output=True)
        subprocess.run(["docker", "exec", pb_container, "php", "artisan", "buddy:regenerate-price-cache"], check=False, capture_output=True)
        r = httpx.get(f"{pb_url}/api/products?per_page=100", headers=headers, timeout=20.0)
        if r.status_code == 200:
            api_prods = {p["id"]: p for p in r.json().get("data", [])}

    for pid, base in baselines.items():
        prod = api_prods.get(pid)
        if not prod or prod.get("paused"):
            continue
        cur_price = prod.get("current_price")
        if cur_price is None or float(cur_price) <= 0:
            continue
        cur_price = float(cur_price)

        min_floor = base["min_price_floor"]
        fb_floor = conn.execute(
            "SELECT MAX(CAST(pattern AS REAL)) FROM feedback_rules WHERE rule_type = 'min_price_floor' AND product_id = ? AND active = 1",
            (pid,),
        ).fetchone()[0]
        if fb_floor:
            min_floor = max(min_floor, float(fb_floor))

        normal = base["normal_price"]
        drop_pct = ((normal - cur_price) / normal) * 100.0
        if (
            cur_price <= base["target_price"]
            and cur_price >= min_floor
            and drop_pct >= base["min_drop_pct"]
            and not recently_alerted(conn, pid, cur_price)
        ):
            pc_list = prod.get("price_cache") or []
            best_url = pc_list[0].get("url") if pc_list else base["primary_url"]
            create_alert(
                conn,
                cfg,
                "PriceBuddy Installment",
                pid,
                None,
                base["model_name"],
                cur_price,
                normal,
                base["target_price"],
                best_url,
                note="Validated without cash-only discount",
            )
            stats["alerts"] += 1

    return stats


def match_telegram_post(
    conn: sqlite3.Connection, text: str, baselines: list[sqlite3.Row], global_rules: list[sqlite3.Row]
) -> dict[str, Any]:
    norm = normalize_text(text)

    # 1. Global exclusion rules
    for rule in global_rules:
        if rule["rule_type"] == "exclude_keyword" and rule["product_id"] is None:
            if normalize_text(rule["pattern"]) in norm:
                return {"matched": False, "reason": f"global_rule:{rule['pattern']}"}

    # 2. Match product baseline
    matched_base = None
    for b in baselines:
        if not b["active"]:
            continue
        groups = json.loads(b["match_groups_json"])
        if all(any(normalize_text(kw) in norm for kw in grp) for grp in groups):
            matched_base = b
            break

    if not matched_base:
        return {"matched": False, "reason": "outside_shortlist"}

    pid = matched_base["product_id"]
    excl = list(json.loads(matched_base["exclude_terms_json"]))
    for rule in global_rules:
        if rule["rule_type"] == "exclude_keyword" and rule["product_id"] == pid:
            excl.append(rule["pattern"])

    for term in excl:
        if re.search(r"(?<![a-z0-9])" + re.escape(normalize_text(term)) + r"(?![a-z0-9])", norm):
            return {"matched": True, "product_id": pid, "is_accessory": True, "reason": f"excluded_term:{term}"}

    # 3. Extract total price safely (strip installment-unit fragments like "10x de R$ 129,90")
    has_installment_unit = bool(INSTALLMENT_UNIT_RE.search(text))
    cleaned_for_total = INSTALLMENT_UNIT_RE.sub(" ", text)
    prices = []
    for m in PRICE_RE.finditer(cleaned_for_total):
        val = parse_brl(m.group(1))
        if val and val > 10.0:
            prices.append(val)

    if not prices and has_installment_unit:
        for m in INSTALLMENT_UNIT_RE.finditer(text):
            n_parc = int(m.group(1))
            val_parc = parse_brl(m.group(2))
            if val_parc and 2 <= n_parc <= 24:
                prices.append(round(n_parc * val_parc, 2))

    if not prices:
        return {"matched": True, "product_id": pid, "reason": "no_valid_total_price", "is_installment_unit": has_installment_unit}

    min_floor = matched_base["min_price_floor"]
    fb_floor = conn.execute(
        "SELECT MAX(CAST(pattern AS REAL)) FROM feedback_rules WHERE rule_type = 'min_price_floor' AND product_id = ? AND active = 1",
        (pid,),
    ).fetchone()[0]
    if fb_floor:
        min_floor = max(min_floor, float(fb_floor))

    valid_prices = [p for p in prices if p >= min_floor]
    if not valid_prices:
        return {
            "matched": True,
            "product_id": pid,
            "parsed_price": min(prices),
            "is_accessory": True,
            "reason": f"below_floor_{min_floor}",
        }

    # 4. Anti-Pix gate: reject cash-only discounts without installment marker
    has_pix = any(m in norm for m in PIX_MARKERS)
    has_inst_ok = any(m in norm for m in INSTALLMENT_OK_MARKERS)
    is_pix_only = has_pix and not has_inst_ok

    chosen_price = max(valid_prices) if (has_pix and has_inst_ok and len(valid_prices) >= 2) else min(valid_prices)

    if is_pix_only:
        return {
            "matched": True,
            "product_id": pid,
            "parsed_price": chosen_price,
            "is_pix_only": True,
            "reason": "cash_only_pix",
        }

    normal = matched_base["normal_price"]
    drop_pct = ((normal - chosen_price) / normal) * 100.0
    if chosen_price > matched_base["target_price"]:
        return {
            "matched": True,
            "product_id": pid,
            "parsed_price": chosen_price,
            "discount_pct": round(drop_pct, 1),
            "reason": f"above_target_{matched_base['target_price']}",
        }

    if drop_pct < matched_base["min_drop_pct"]:
        return {
            "matched": True,
            "product_id": pid,
            "parsed_price": chosen_price,
            "discount_pct": round(drop_pct, 1),
            "reason": f"insufficient_drop_{drop_pct:.1f}%",
        }

    return {
        "matched": True,
        "product_id": pid,
        "parsed_price": chosen_price,
        "discount_pct": round(drop_pct, 1),
        "is_pix_only": False,
        "is_installment_unit": has_installment_unit,
        "is_accessory": False,
        "approved": True,
    }


def collect_telegram_channels(conn: sqlite3.Connection, cfg: dict[str, str]) -> dict[str, Any]:
    channels = get_telegram_channels(cfg)
    baselines = conn.execute("SELECT * FROM product_baselines").fetchall()
    global_rules = conn.execute("SELECT * FROM feedback_rules WHERE active = 1").fetchall()
    stats = {"channels_ok": 0, "channels_err": 0, "posts_seen": 0, "new_posts": 0, "matched": 0, "alerts": 0}

    for idx, ch in enumerate(channels):
        if idx > 0:
            time.sleep(0.8)
        url = f"https://t.me/s/{ch}"
        try:
            def _fetch_ch():
                r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20.0, follow_redirects=True)
                r.raise_for_status()
                return r.text

            html = retry_call(_fetch_ch, attempts=3, base_delay=1.2)
            stats["channels_ok"] += 1
        except Exception as exc:
            stats["channels_err"] += 1
            print(f"[WARN] Channel {ch} failed: {exc}", file=sys.stderr)
            continue

        soup = BeautifulSoup(html, "html.parser")
        wraps = soup.select(".tgme_widget_message_wrap")
        for w in wraps:
            msg = w.select_one(".tgme_widget_message")
            post_id = msg.get("data-post") if msg else None
            if not post_id:
                continue
            stats["posts_seen"] += 1

            exists = conn.execute("SELECT id FROM telegram_deals WHERE post_id = ?", (post_id,)).fetchone()
            if exists:
                continue

            txt_el = w.select_one(".tgme_widget_message_text")
            raw_text = txt_el.get_text(" ", strip=True) if txt_el else ""
            if not raw_text:
                continue

            links = []
            for a in w.select(".tgme_widget_message_text a[href], .tgme_widget_message_link_preview[href]"):
                href = a.get("href", "")
                if href and "t.me/" not in href:
                    links.append(href)
            links = list(dict.fromkeys(links))

            time_el = w.select_one("time[datetime]")
            pub_at = time_el.get("datetime") if time_el else None
            post_url = f"https://t.me/{post_id}"

            eval_res = match_telegram_post(conn, raw_text, baselines, global_rules)
            if not eval_res.get("matched"):
                status = "ignored"
            elif eval_res.get("approved"):
                status = "alerted"
                stats["matched"] += 1
            else:
                status = "rejected"
                stats["matched"] += 1

            cur = conn.execute(
                """
                INSERT INTO telegram_deals (
                    channel, post_id, post_url, published_at, raw_text, extracted_links_json,
                    matched_product_id, parsed_price, is_pix_only, is_installment_unit,
                    is_accessory, discount_pct, status, rejection_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ch,
                    post_id,
                    post_url,
                    pub_at,
                    raw_text,
                    json.dumps(links, ensure_ascii=False),
                    eval_res.get("product_id"),
                    eval_res.get("parsed_price"),
                    1 if eval_res.get("is_pix_only") else 0,
                    1 if eval_res.get("is_installment_unit") else 0,
                    1 if eval_res.get("is_accessory") else 0,
                    eval_res.get("discount_pct"),
                    status,
                    eval_res.get("reason"),
                    now_iso(),
                ),
            )
            deal_id = cur.lastrowid
            stats["new_posts"] += 1

            if eval_res.get("approved"):
                pid = eval_res["product_id"]
                price = eval_res["parsed_price"]
                base = conn.execute("SELECT * FROM product_baselines WHERE product_id = ?", (pid,)).fetchone()
                if base and not recently_alerted(conn, pid, price):
                    target_link = links[0] if links else post_url
                    create_alert(
                        conn,
                        cfg,
                        f"Telegram @{ch}",
                        pid,
                        deal_id,
                        base["model_name"],
                        price,
                        base["normal_price"],
                        base["target_price"],
                        target_link,
                        note=f"Post {post_url}",
                    )
                    stats["alerts"] += 1

        conn.commit()
    return stats


def should_run_in_cadence(conn: sqlite3.Connection, cfg: dict[str, str], force: bool) -> tuple[bool, str]:
    if force:
        return True, "forced"
    tz_offset = int(cfg.get("TIMEZONE_OFFSET_HOURS", "-3"))
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(timezone(timedelta(hours=tz_offset)))
    cutoff = datetime.strptime(cfg.get("CUTOFF_DATE", "2026-12-02"), "%Y-%m-%d").date()
    peak_start = datetime.strptime(cfg.get("PEAK_START_DATE", "2026-11-26"), "%Y-%m-%d").date()

    if now_local.date() > cutoff:
        return False, f"stopped_after_{cutoff.isoformat()}"

    row = conn.execute("SELECT value FROM bot_state WHERE key = 'last_full_run_ts'").fetchone()
    last_ts = float(row["value"]) if row else 0.0
    elapsed_min = (now_utc.timestamp() - last_ts) / 60.0

    if now_local.date() < peak_start:
        if last_ts > 0 and elapsed_min < 55.0:
            return False, f"warmup_1h_cooldown (last_run={elapsed_min:.1f}m)"
        return True, "warmup_1h"

    if last_ts > 0 and elapsed_min < 12.0:
        return False, f"peak_15m_cooldown ({elapsed_min:.1f}m)"
    return True, "peak_15m"


def print_status(conn: sqlite3.Connection):
    b_cnt = conn.execute("SELECT COUNT(*) FROM product_baselines WHERE active = 1").fetchone()[0]
    d_cnt = conn.execute("SELECT COUNT(*) FROM telegram_deals").fetchone()[0]
    r_cnt = conn.execute("SELECT COUNT(*) FROM feedback_rules WHERE active = 1").fetchone()[0]
    a_cnt = conn.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0]
    print(f"Active baselines: {b_cnt} | Saved Telegram deals: {d_cnt} | Feedback rules: {r_cnt} | Alerts: {a_cnt}")
    print("\n--- Store Health ---")
    for s in conn.execute("SELECT * FROM store_health ORDER BY store_slug"):
        print(f"  {s['store_slug']:15s} | failures={s['consecutive_failures']} | last_ok={s['last_success_at']} | err={s['last_error']}")
    print("\n--- Recent Alerts ---")
    for a in conn.execute("SELECT id, source, product_id, price, discount_pct, delivery_status, feedback, created_at FROM alert_log ORDER BY id DESC LIMIT 10"):
        print(f"  #{a['id']} [{a['source']}] prod={a['product_id']} {a['price']} (-{a['discount_pct']}%) | tg={a['delivery_status']} | fb={a['feedback']} | {a['created_at']}")


def main():
    parser = argparse.ArgumentParser(description="Agentic AI Blackfriday Monitor Harness")
    parser.add_argument("--init-only", action="store_true", help="Initialize SQLite schema and seed baselines")
    parser.add_argument("--run", action="store_true", help="Execute monitoring cycle")
    parser.add_argument("--refresh-live", action="store_true", help="Re-scrape live URLs via PriceBuddy API")
    parser.add_argument("--force", action="store_true", help="Bypass adaptive cadence cooldown")
    parser.add_argument("--poll-feedback", action="store_true", help="Poll Telegram inline feedback callbacks")
    parser.add_argument("--feedback", nargs="+", help="Record feedback manually: <alert_id> <good|fp> [keyword]")
    parser.add_argument("--status", action="store_true", help="Show SQLite health and recent alerts")
    args = parser.parse_args()

    cfg = get_config()
    db_path = Path(cfg.get("HARNESS_DB", str(ROOT / "storage" / "harness.sqlite"))).expanduser()
    if not db_path.is_absolute():
        db_path = (ROOT / db_path).resolve()
    conn = init_db(db_path)

    try:
        if args.init_only:
            print(f"SQLite initialized at {db_path}")
            return

        if args.feedback:
            aid = int(args.feedback[0])
            verdict = args.feedback[1]
            kw = args.feedback[2] if len(args.feedback) > 2 else None
            print(record_feedback(conn, aid, verdict, kw))
            return

        if args.poll_feedback:
            n = poll_telegram_feedback(conn, cfg)
            print(f"Processed callbacks: {n}")
            return

        if args.status:
            print_status(conn)
            return

        if args.run:
            ok, reason = should_run_in_cadence(conn, cfg, args.force)
            if not ok:
                print(f"[{now_iso()}] Skip run: {reason}")
                return
            fb_n = poll_telegram_feedback(conn, cfg)
            pb_stats = check_pricebuddy_products(conn, cfg, refresh_live=args.refresh_live)
            tg_stats = collect_telegram_channels(conn, cfg)
            conn.execute(
                "INSERT INTO bot_state (key, value, updated_at) VALUES ('last_full_run_ts', ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (str(datetime.now(timezone.utc).timestamp()), now_iso()),
            )
            conn.commit()
            print(
                f"[{now_iso()}] Run ({reason}) completed | feedback={fb_n} | PriceBuddy={pb_stats} | Telegram={tg_stats}"
            )
            return

        parser.print_help()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
