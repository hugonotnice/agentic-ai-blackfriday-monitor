import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_HARNESS_DB = ROOT / "storage" / "harness.sqlite"
DEFAULT_BASELINES_FILE = ROOT / "baselines.json"
EXAMPLE_BASELINES_FILE = ROOT / "baselines.example.json"

GLOBAL_FEEDBACK_RULES_SEED = [
    ("exclude_keyword", "capinha", None, "Protective case accessory"),
    ("exclude_keyword", "película", None, "Screen protector accessory"),
    ("exclude_keyword", "pelicula", None, "Screen protector accessory"),
    ("exclude_keyword", "pulseira avulsa", None, "Replacement watch band"),
    ("exclude_keyword", "earpad", None, "Replacement earpad"),
    ("exclude_keyword", "almofada reposicao", None, "Replacement pad"),
    ("exclude_keyword", "seminovo", None, "Used condition"),
    ("exclude_keyword", "recondicionado", None, "Refurbished condition"),
    ("exclude_keyword", "vitrine", None, "Display unit"),
    ("exclude_keyword", "refurbished", None, "Refurbished condition"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_baselines_seed() -> list[dict[str, Any]]:
    source = DEFAULT_BASELINES_FILE if DEFAULT_BASELINES_FILE.exists() else EXAMPLE_BASELINES_FILE
    if not source.exists():
        return []
    return json.loads(source.read_text(encoding="utf-8"))


def init_db(db_path: Path = DEFAULT_HARNESS_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS product_baselines (
            product_id INTEGER PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL,
            model_name TEXT NOT NULL,
            normal_price REAL NOT NULL,
            target_price REAL NOT NULL,
            min_drop_pct REAL NOT NULL DEFAULT 18.0,
            min_price_floor REAL NOT NULL DEFAULT 50.0,
            match_groups_json TEXT NOT NULL,
            exclude_terms_json TEXT NOT NULL,
            primary_url TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telegram_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            post_id TEXT UNIQUE NOT NULL,
            post_url TEXT NOT NULL,
            published_at TEXT,
            raw_text TEXT NOT NULL,
            extracted_links_json TEXT NOT NULL,
            matched_product_id INTEGER,
            parsed_price REAL,
            is_pix_only INTEGER NOT NULL DEFAULT 0,
            is_installment_unit INTEGER NOT NULL DEFAULT 0,
            is_accessory INTEGER NOT NULL DEFAULT 0,
            discount_pct REAL,
            status TEXT NOT NULL DEFAULT 'candidate',
            rejection_reason TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feedback_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type TEXT NOT NULL,
            pattern TEXT NOT NULL,
            product_id INTEGER,
            source_deal_id INTEGER,
            notes TEXT,
            hits INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_rules_unique
            ON feedback_rules(rule_type, pattern, COALESCE(product_id, 0));

        CREATE TABLE IF NOT EXISTS store_health (
            store_slug TEXT PRIMARY KEY,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            last_success_at TEXT,
            last_failure_at TEXT,
            alerted_outage INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS alert_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            deal_id INTEGER,
            price REAL NOT NULL,
            normal_price REAL NOT NULL,
            discount_pct REAL NOT NULL,
            url TEXT NOT NULL,
            message_text TEXT NOT NULL,
            telegram_message_id INTEGER,
            delivery_status TEXT NOT NULL DEFAULT 'pending_token',
            feedback TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

    ts = now_iso()
    for b in load_baselines_seed():
        conn.execute(
            """
            INSERT INTO product_baselines (
                product_id, slug, category, model_name, normal_price, target_price,
                min_drop_pct, min_price_floor, match_groups_json, exclude_terms_json,
                primary_url, active, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                slug=excluded.slug,
                category=excluded.category,
                model_name=excluded.model_name,
                normal_price=excluded.normal_price,
                target_price=excluded.target_price,
                min_drop_pct=excluded.min_drop_pct,
                min_price_floor=excluded.min_price_floor,
                match_groups_json=excluded.match_groups_json,
                exclude_terms_json=excluded.exclude_terms_json,
                primary_url=excluded.primary_url,
                active=excluded.active,
                updated_at=excluded.updated_at
            """,
            (
                b["product_id"],
                b["slug"],
                b["category"],
                b["model_name"],
                b["normal_price"],
                b["target_price"],
                b["min_drop_pct"],
                b["min_price_floor"],
                json.dumps(b["match_groups"], ensure_ascii=False),
                json.dumps(b["exclude_terms"], ensure_ascii=False),
                b["primary_url"],
                b["active"],
                ts,
            ),
        )

    for rtype, pat, pid, notes in GLOBAL_FEEDBACK_RULES_SEED:
        conn.execute(
            """
            INSERT OR IGNORE INTO feedback_rules (rule_type, pattern, product_id, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (rtype, pat.lower(), pid, notes, ts),
        )

    for store_slug in ["amazon-br", "kabum", "flexform", "mercado-livre", "aliexpress"]:
        conn.execute(
            "INSERT OR IGNORE INTO store_health (store_slug, consecutive_failures) VALUES (?, 0)",
            (store_slug,),
        )

    conn.commit()
    return conn


if __name__ == "__main__":
    c = init_db()
    cnt_b = c.execute("SELECT COUNT(*) FROM product_baselines").fetchone()[0]
    cnt_r = c.execute("SELECT COUNT(*) FROM feedback_rules").fetchone()[0]
    print(f"harness.sqlite initialized: {cnt_b} baselines, {cnt_r} feedback_rules")
    c.close()
