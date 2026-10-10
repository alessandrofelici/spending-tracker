"""SQLite storage. One row per transaction, grouped by the month it happened in."""

import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:  # classify imports db, so only for type hints
    from .classify import Answer

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(
    ROOT / ".env"
)  # before reading SPENDING_DB; real environment variables still win
DB_PATH = Path(os.environ.get("SPENDING_DB", ROOT / "data" / "spending.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id          TEXT PRIMARY KEY,   -- stable hash, makes re-importing a statement a no-op
    month       TEXT NOT NULL,      -- YYYY-MM of the transaction date
    date        TEXT NOT NULL,      -- transaction date, YYYY-MM-DD
    description TEXT NOT NULL,      -- raw text from the statement
    merchant    TEXT NOT NULL,      -- normalized merchant key
    amount      REAL NOT NULL,      -- positive = charge, negative = payment/refund
    category    TEXT NOT NULL,
    source      TEXT NOT NULL,      -- payment | memory | llm | manual | fallback (rule: older imports)
    statement   TEXT NOT NULL       -- CSV file it was imported from
);
CREATE INDEX IF NOT EXISTS idx_txn_month ON transactions(month);

-- Remembered merchant -> category decisions (from the LLM or from you),
-- so each merchant only ever has to be classified once.
CREATE TABLE IF NOT EXISTS merchant_memory (
    merchant TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    source   TEXT NOT NULL
);

-- Jev's latest answer per merchant, including ones too unsure to use, so
-- `spend review` can suggest them and unplaced merchants aren't re-asked.
CREATE TABLE IF NOT EXISTS jev_answers (
    merchant   TEXT PRIMARY KEY,
    choice     TEXT,            -- Jev's top pick; NULL if the request failed
    confidence REAL NOT NULL,
    outcome    TEXT NOT NULL,   -- confident | unsure | chose_other | failed
    asked_with TEXT NOT NULL,   -- hash of the categories + descriptions it chose from
    latency_ms REAL             -- that request, including its retry; NULL if older
);

-- `python -m spending.jev_eval`: Jev's answer for merchants a keyword rule in
-- categories.toml matches. Kept apart from jev_answers so it never changes review suggestions.
CREATE TABLE IF NOT EXISTS jev_evals (
    merchant     TEXT NOT NULL,
    rule         TEXT NOT NULL,   -- the category the keyword rule gave it
    choice       TEXT,            -- Jev's pick; NULL if the request failed
    confidence   REAL NOT NULL,
    outcome      TEXT NOT NULL,   -- confident | unsure | chose_other | failed
    latency_ms   REAL NOT NULL,
    evaluated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Columns added after a table was first created: (table, column, definition).
MIGRATIONS = [("jev_answers", "latency_ms", "REAL")]

# Payment rows used to be matched by a keyword rule (source 'rule'); give them
# their own source so they stay out of Jev now that other rule rows don't.
DATA_MIGRATIONS = [
    "UPDATE transactions SET source = 'payment'"
    " WHERE source = 'rule' AND category = 'Payments & Credits'"
]


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, column, definition in MIGRATIONS:
        if column not in {
            r["name"] for r in conn.execute(f"PRAGMA table_info({table})")
        }:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    for sql in DATA_MIGRATIONS:
        conn.execute(sql)
    conn.commit()
    return conn


def get_memory(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        r["merchant"]: r["category"]
        for r in conn.execute("SELECT merchant, category FROM merchant_memory")
    }


def get_manual_merchants(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT merchant FROM merchant_memory WHERE source = 'manual'"
        )
    }


def get_unplaced(conn: sqlite3.Connection) -> dict[str, str]:
    """Merchants Jev answered but couldn't place -> the criteria they were asked with."""
    rows = conn.execute(
        "SELECT merchant, asked_with FROM jev_answers"
        " WHERE outcome IN ('unsure', 'chose_other')"
    )
    return {r["merchant"]: r["asked_with"] for r in rows}


def get_answers(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["merchant"]: r for r in conn.execute("SELECT * FROM jev_answers")}


def save_answers(conn: sqlite3.Connection, answers: Mapping[str, "Answer"]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO jev_answers
           (merchant, choice, confidence, outcome, asked_with, latency_ms)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [(m, *a) for m, a in answers.items()],
    )


def save_evals(
    conn: sqlite3.Connection, answers: Mapping[str, "Answer"], rules: Mapping[str, str]
) -> None:
    """rules: merchant -> the category its keyword rule gave it."""
    conn.executemany(
        """INSERT INTO jev_evals
           (merchant, rule, choice, confidence, outcome, latency_ms)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (m, rules[m], a.choice, a.confidence, a.outcome, a.latency_ms)
            for m, a in answers.items()
        ],
    )


def find_merchants(conn: sqlite3.Connection, text: str) -> list[str]:
    """Exact merchant key if it exists, otherwise every merchant containing `text`."""
    text = text.upper().strip()
    if conn.execute(
        "SELECT 1 FROM transactions WHERE merchant = ? LIMIT 1", (text,)
    ).fetchone():
        return [text]
    rows = conn.execute(
        "SELECT DISTINCT merchant FROM transactions WHERE merchant LIKE ? OR UPPER(description) LIKE ? ORDER BY merchant",
        (f"%{text}%", f"%{text}%"),
    )
    return [r[0] for r in rows]


def remember(
    conn: sqlite3.Connection, merchant: str, category: str, source: str
) -> None:
    # Manual decisions are never overwritten by the LLM.
    conn.execute(
        """INSERT INTO merchant_memory (merchant, category, source) VALUES (?, ?, ?)
           ON CONFLICT(merchant) DO UPDATE SET category = excluded.category, source = excluded.source
           WHERE merchant_memory.source != 'manual' OR excluded.source = 'manual'""",
        (merchant, category, source),
    )


def insert_transactions(conn: sqlite3.Connection, rows: list[dict]) -> int:
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO transactions
           (id, month, date, description, merchant, amount, category, source, statement)
           VALUES (:id, :month, :date, :description, :merchant, :amount, :category, :source, :statement)""",
        rows,
    )
    return conn.total_changes - before


def set_merchant_category(
    conn: sqlite3.Connection, merchant: str, category: str
) -> int:
    remember(conn, merchant, category, "manual")
    cur = conn.execute(
        "UPDATE transactions SET category = ?, source = 'manual' WHERE merchant = ?",
        (category, merchant),
    )
    return cur.rowcount


def place_by_llm(conn: sqlite3.Connection, merchant: str, category: str) -> int:
    """Give a merchant the LLM's category on every row the LLM (or nobody)
    decided; rows you set and payment rows are never touched."""
    remember(conn, merchant, category, "llm")
    cur = conn.execute(
        "UPDATE transactions SET category = ?, source = 'llm'"
        " WHERE merchant = ? AND source NOT IN ('manual', 'payment')",
        (category, merchant),
    )
    return cur.rowcount
