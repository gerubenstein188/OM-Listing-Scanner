import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "seen_oms.db"


def get_connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS seen_oms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_hash TEXT UNIQUE NOT NULL,
                url TEXT NOT NULL,
                brokerage TEXT,
                title TEXT,
                market TEXT,
                asset_type TEXT,
                units INTEGER,
                asking_price TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                matched BOOLEAN DEFAULT 0,
                github_issue_number INTEGER,
                atlasх_deal_id TEXT
            );

            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                listings_found INTEGER DEFAULT 0,
                matches_found INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0
            );
        """)


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def is_seen(url: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM seen_oms WHERE url_hash = ?", (url_hash(url),)
        ).fetchone()
        return row is not None


def record_listing(url: str, brokerage: str, title: str = None, matched: bool = False, **meta) -> int:
    now = datetime.utcnow().isoformat()
    h = url_hash(url)
    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM seen_oms WHERE url_hash = ?", (h,)).fetchone()
        if existing:
            conn.execute("UPDATE seen_oms SET last_seen = ? WHERE url_hash = ?", (now, h))
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO seen_oms
               (url_hash, url, brokerage, title, market, asset_type, units, asking_price, first_seen, last_seen, matched)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (h, url, brokerage, title,
             meta.get("market"), meta.get("asset_type"),
             meta.get("units"), meta.get("asking_price"),
             now, now, matched),
        )
        return cur.lastrowid


def update_issue_number(listing_id: int, issue_number: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE seen_oms SET github_issue_number = ? WHERE id = ?",
            (issue_number, listing_id),
        )


def start_run() -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO scan_runs (started_at) VALUES (?)",
            (datetime.utcnow().isoformat(),),
        )
        return cur.lastrowid


def finish_run(run_id: int, listings: int, matches: int, errors: int):
    with get_connection() as conn:
        conn.execute(
            """UPDATE scan_runs SET completed_at = ?, listings_found = ?,
               matches_found = ?, errors = ? WHERE id = ?""",
            (datetime.utcnow().isoformat(), listings, matches, errors, run_id),
        )
