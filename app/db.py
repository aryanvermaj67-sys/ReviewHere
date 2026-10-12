"""SQLite access layer. Raw SQL, no ORM - the schema is small and stays readable."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(config.DB_PATH) if config.DB_PATH else BASE_DIR / "data" / "nfc.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT    NOT NULL UNIQUE,
    name                TEXT    NOT NULL,
    business_type       TEXT    NOT NULL DEFAULT 'restaurant',
    tagline             TEXT    NOT NULL DEFAULT '',
    logo_url            TEXT    NOT NULL DEFAULT '',
    google_review_url   TEXT    NOT NULL DEFAULT '',
    accent              TEXT    NOT NULL DEFAULT '#e0603a',
    positive_threshold  INTEGER NOT NULL DEFAULT 4,
    google_auto         INTEGER NOT NULL DEFAULT 0,  -- 1 = send happy customers straight to Google
    -- rate_first: ask for a rating, then route. direct: forward every tap to Google.
    tap_mode            TEXT    NOT NULL DEFAULT 'rate_first',
    -- open: invite every rater to Google (Google's policy allows this).
    -- gated: invite only ratings at or above the threshold (review gating).
    routing             TEXT    NOT NULL DEFAULT 'open',
    tz_offset           INTEGER NOT NULL DEFAULT 0,  -- minutes east of UTC, for local-day stats
    -- billing: trial -> active (paying) | expired | cancelled
    plan                TEXT    NOT NULL DEFAULT 'trial',
    trial_ends_at       TEXT    NOT NULL DEFAULT (datetime('now', '+7 days')),
    subscription_id     TEXT    NOT NULL DEFAULT '',
    subscription_status TEXT    NOT NULL DEFAULT '',
    period_end          TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id   INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    email         TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL DEFAULT '',
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One row per physical NFC tag (or printed QR). token is what the tag encodes.
CREATE TABLE IF NOT EXISTS tags (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id   INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    token         TEXT    NOT NULL UNIQUE,
    label         TEXT    NOT NULL,
    zone          TEXT    NOT NULL DEFAULT '',
    uid           TEXT    NOT NULL DEFAULT '',  -- hardware UID, filled in when tags are written
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source     TEXT    NOT NULL DEFAULT 'web',
    user_agent TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id      INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    tag_id           INTEGER REFERENCES tags(id) ON DELETE SET NULL,
    rating           INTEGER NOT NULL,
    quality          INTEGER,
    service          INTEGER,
    environment      INTEGER,
    value            INTEGER,
    comment          TEXT    NOT NULL DEFAULT '',
    customer_name    TEXT    NOT NULL DEFAULT '',
    customer_contact TEXT    NOT NULL DEFAULT '',
    routed_public    INTEGER NOT NULL DEFAULT 0,  -- 1 = customer was sent to Google
    status           TEXT    NOT NULL DEFAULT 'new',  -- new | resolved
    owner_note       TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One-off purchases of physical tags. Amounts are in paise, never floats.
CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id   INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    quantity      INTEGER NOT NULL,
    unit_paise    INTEGER NOT NULL,
    amount_paise  INTEGER NOT NULL,
    currency      TEXT    NOT NULL DEFAULT 'INR',
    status        TEXT    NOT NULL DEFAULT 'created',  -- created | paid | failed | shipped
    provider_order_id   TEXT NOT NULL DEFAULT '',
    provider_payment_id TEXT NOT NULL DEFAULT '',
    ship_name     TEXT    NOT NULL DEFAULT '',
    ship_phone    TEXT    NOT NULL DEFAULT '',
    ship_address  TEXT    NOT NULL DEFAULT '',
    note          TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    paid_at       TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_orders_business ON orders(business_id, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_provider ON orders(provider_order_id);

-- Every webhook we accept, so a replayed or duplicated delivery cannot be
-- processed twice.
CREATE TABLE IF NOT EXISTS billing_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT    NOT NULL UNIQUE,
    event_type  TEXT    NOT NULL DEFAULT '',
    payload     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Single-use password reset tokens. Only the hash is stored, so a leaked database
-- backup cannot be used to take over accounts.
CREATE TABLE IF NOT EXISTS password_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT    NOT NULL UNIQUE,
    expires_at TEXT    NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_resets_user ON password_resets(user_id);
CREATE INDEX IF NOT EXISTS idx_reviews_rest_date ON reviews(business_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reviews_tag       ON reviews(tag_id);
CREATE INDEX IF NOT EXISTS idx_scans_tag         ON scans(tag_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tags_rest         ON tags(business_id);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after the first release. Each is applied once, on boot.
MIGRATIONS = [
    ("businesses", "tz_offset", "INTEGER NOT NULL DEFAULT 0"),
    ("businesses", "business_type", "TEXT NOT NULL DEFAULT 'restaurant'"),
    ("businesses", "plan", "TEXT NOT NULL DEFAULT 'trial'"),
    ("businesses", "trial_ends_at", "TEXT NOT NULL DEFAULT ''"),
    ("businesses", "subscription_id", "TEXT NOT NULL DEFAULT ''"),
    ("businesses", "subscription_status", "TEXT NOT NULL DEFAULT ''"),
    ("businesses", "period_end", "TEXT NOT NULL DEFAULT ''"),
    ("businesses", "google_auto", "INTEGER NOT NULL DEFAULT 0"),
    ("businesses", "tap_mode", "TEXT NOT NULL DEFAULT 'rate_first'"),
    ("businesses", "routing", "TEXT NOT NULL DEFAULT 'open'"),
]


def _migrate(conn) -> None:
    for table, column, spec in MIGRATIONS:
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if column not in existing:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, spec))


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def query(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def execute(conn, sql, params=()):
    cur = conn.execute(sql, params)
    return cur.lastrowid
