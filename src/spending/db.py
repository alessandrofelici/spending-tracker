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
    id          TEXT PRIMARY KEY,   -- stable hash (CSV) or Plaid's transaction_id; re-importing is a no-op
    month       TEXT NOT NULL,      -- YYYY-MM of the transaction date
    date        TEXT NOT NULL,      -- transaction date, YYYY-MM-DD
    description TEXT NOT NULL,      -- raw text from the statement
    merchant    TEXT NOT NULL,      -- normalized merchant key
    amount      REAL NOT NULL,      -- positive = charge, negative = payment/refund
    category    TEXT NOT NULL,
    source      TEXT NOT NULL,      -- payment | memory | llm | manual | fallback (rule: older imports)
    statement   TEXT NOT NULL,      -- CSV file it was imported from, or "Plaid"
    source_kind TEXT NOT NULL DEFAULT 'csv', -- csv | plaid
    account     TEXT NOT NULL DEFAULT 'credit' -- credit (spending) | checking (money in, only to spot paid-back purchases)
);
CREATE INDEX IF NOT EXISTS idx_txn_month ON transactions(month);

-- Bank logins linked through Plaid. The access token is a credential, so it
-- lives in the OS keyring (or a private file next to this database), never here.
CREATE TABLE IF NOT EXISTS plaid_items (
    item_id     TEXT PRIMARY KEY,
    institution TEXT,   -- bank name from Plaid Link
    cursor      TEXT,   -- where the last /transactions/sync stopped; NULL = never synced
    cutover     TEXT,   -- newest stored date when linked; Plaid rows up to it are skipped
    last_synced TEXT,   -- UTC, YYYY-MM-DD HH:MM:SS
    role        TEXT NOT NULL DEFAULT 'card'  -- card (spending) | reimbursements (checking: money paid back to you)
);

-- Purchases you were paid back for, in full or in part: one row per piece, e.g.
-- one per friend's transfer. Spending counts amount - SUM(paid back).
CREATE TABLE IF NOT EXISTS reimbursements (
    id          INTEGER PRIMARY KEY,
    txn_id      TEXT NOT NULL,          -- the card purchase
    amount      REAL NOT NULL CHECK (amount > 0),
    note        TEXT,
    incoming_id TEXT UNIQUE,            -- the checking transfer that paid it back; NULL if marked by hand
    created     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reimb_txn ON reimbursements(txn_id);

-- Proposed purchase <-> transfer matches you said were wrong, so they aren't proposed again.
CREATE TABLE IF NOT EXISTS dismissed_matches (
    txn_id      TEXT NOT NULL,
    incoming_id TEXT NOT NULL,
    PRIMARY KEY (txn_id, incoming_id)
);

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
"""

# Columns added after a table was first created: (table, column, definition).
MIGRATIONS = [
    ("jev_answers", "latency_ms", "REAL"),
    ("transactions", "source_kind", "TEXT NOT NULL DEFAULT 'csv'"),
    ("transactions", "account", "TEXT NOT NULL DEFAULT 'credit'"),
    ("plaid_items", "role", "TEXT NOT NULL DEFAULT 'card'"),
]

# Payment rows used to be matched by a keyword rule (source 'rule'); give them
# their own source so they stay out of Jev now that other rule rows don't.
DATA_MIGRATIONS = [
    "UPDATE transactions SET source = 'payment'"
    " WHERE source = 'rule' AND category = 'Payments & Credits'"
]


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    # Owner-only, like the Plaid token file: this is your bank data. SQLite gives
    # its journal the database's permissions. An existing folder or file keeps
    # its own (see the README for tightening an older setup).
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
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


def find_merchants(conn: sqlite3.Connection, text: str) -> list[str]:
    """Exact merchant key if it exists, otherwise every merchant containing `text`."""
    text = text.upper().strip()
    if conn.execute(
        "SELECT 1 FROM transactions WHERE merchant = ? AND account = 'credit' LIMIT 1",
        (text,),
    ).fetchone():
        return [text]
    rows = conn.execute(
        "SELECT DISTINCT merchant FROM transactions WHERE account = 'credit'"
        " AND (merchant LIKE ? OR UPPER(description) LIKE ?) ORDER BY merchant",
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
    """Rows without an account are card (credit) rows."""
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO transactions
           (id, month, date, description, merchant, amount, category, source, statement, source_kind, account)
           VALUES (:id, :month, :date, :description, :merchant, :amount, :category, :source, :statement, :source_kind, :account)""",
        [{"account": "credit"} | r for r in rows],
    )
    return conn.total_changes - before


def update_plaid_transaction(conn: sqlite3.Connection, row: dict) -> bool:
    """Apply a change Plaid reported to a stored row, keeping its category.
    False if the row isn't stored."""
    cur = conn.execute(
        """UPDATE transactions
           SET month = :month, date = :date, description = :description,
               merchant = :merchant, amount = :amount
           WHERE id = :id AND source_kind = 'plaid'""",
        row,
    )
    return cur.rowcount > 0


def delete_plaid_transactions(conn: sqlite3.Connection, ids: list[str]) -> int:
    """Delete rows the bank removed. A paid-back piece tied to a removed purchase
    or transfer goes too: what it pointed to no longer exists."""
    cur = conn.executemany(
        "DELETE FROM transactions WHERE id = ? AND source_kind = 'plaid'",
        [(i,) for i in ids],
    )
    deleted = cur.rowcount
    conn.executemany(
        "DELETE FROM reimbursements WHERE txn_id = ?1 OR incoming_id = ?1",
        [(i,) for i in ids],
    )
    return deleted


ROLES = {"card": "credit", "reimbursements": "checking"}  # login role -> account


def get_plaid_items(
    conn: sqlite3.Connection, role: str | None = None
) -> list[sqlite3.Row]:
    """Newest first: that's the login of each role the dashboard syncs."""
    if role is None:
        return conn.execute("SELECT * FROM plaid_items ORDER BY rowid DESC").fetchall()
    return conn.execute(
        "SELECT * FROM plaid_items WHERE role = ? ORDER BY rowid DESC", (role,)
    ).fetchall()


def add_plaid_item(
    conn: sqlite3.Connection,
    item_id: str,
    institution: str | None,
    role: str = "card",
) -> None:
    """A new bank login. Plaid rows dated up to the newest stored transaction of
    its account are skipped, so nothing you already have is counted twice: CSV
    imports, or rows synced through an earlier login (a new login's transaction
    ids differ)."""
    conn.execute(
        """INSERT INTO plaid_items (item_id, institution, role, cutover)
           VALUES (?, ?, ?, (SELECT MAX(date) FROM transactions WHERE account = ?))""",
        (item_id, institution, role, ROLES[role]),
    )


def remove_plaid_item(conn: sqlite3.Connection, item_id: str) -> None:
    conn.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))


def save_plaid_cursor(conn: sqlite3.Connection, item_id: str, cursor: str) -> None:
    conn.execute(
        "UPDATE plaid_items SET cursor = ?, last_synced = datetime('now') WHERE item_id = ?",
        (cursor, item_id),
    )


def set_merchant_category(
    conn: sqlite3.Connection, merchant: str, category: str
) -> int:
    remember(conn, merchant, category, "manual")
    cur = conn.execute(
        "UPDATE transactions SET category = ?, source = 'manual'"
        " WHERE merchant = ? AND account = 'credit'",
        (category, merchant),
    )
    return cur.rowcount


def place_by_llm(conn: sqlite3.Connection, merchant: str, category: str) -> int:
    """Give a merchant the LLM's category on every row the LLM (or nobody)
    decided; rows you set and payment rows are never touched."""
    remember(conn, merchant, category, "llm")
    cur = conn.execute(
        "UPDATE transactions SET category = ?, source = 'llm'"
        " WHERE merchant = ? AND account = 'credit'"
        " AND source NOT IN ('manual', 'payment')",
        (category, merchant),
    )
    return cur.rowcount


# Card rows with what was paid back of each: what the dashboard charts.
SPENDING_SQL = """
SELECT t.*, COALESCE(SUM(r.amount), 0) AS paid_back
FROM transactions t LEFT JOIN reimbursements r ON r.txn_id = t.id
WHERE t.account = 'credit'
GROUP BY t.id
"""


def paid_back(conn: sqlite3.Connection, txn_id: str) -> float:
    return conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM reimbursements WHERE txn_id = ?",
        (txn_id,),
    ).fetchone()[0]


def add_reimbursement(
    conn: sqlite3.Connection,
    txn_id: str,
    amount: float,
    note: str | None = None,
    incoming_id: str | None = None,
) -> int:
    """Record that `amount` of a card purchase was paid back, optionally by a
    checking transfer. Raises ValueError if it isn't a purchase, the amount isn't
    positive, it would pay back more than the purchase cost, or the transfer is
    already used. Returns the new row's id. The caller commits."""
    txn = conn.execute(
        "SELECT amount FROM transactions WHERE id = ? AND account = 'credit'",
        (txn_id,),
    ).fetchone()
    if txn is None or txn["amount"] <= 0:
        raise ValueError("Only a card purchase can be paid back.")
    amount = round(amount, 2)
    if amount <= 0:
        raise ValueError("The amount paid back must be more than $0.")
    left = round(txn["amount"] - paid_back(conn, txn_id), 2)
    if amount > left:
        raise ValueError(f"Only ${left:,.2f} of this purchase is left to pay back.")
    if incoming_id is not None:
        incoming = conn.execute(
            "SELECT amount FROM transactions WHERE id = ? AND account = 'checking'",
            (incoming_id,),
        ).fetchone()
        if incoming is None:
            raise ValueError("That transfer isn't in your checking account.")
        if amount > round(-incoming["amount"], 2):
            raise ValueError("The amount is more than the transfer brought in.")
        if conn.execute(
            "SELECT 1 FROM reimbursements WHERE incoming_id = ?", (incoming_id,)
        ).fetchone():
            raise ValueError("That transfer already paid back another purchase.")
    cur = conn.execute(
        "INSERT INTO reimbursements (txn_id, amount, note, incoming_id) VALUES (?, ?, ?, ?)",
        (txn_id, amount, (note or "").strip() or None, incoming_id),
    )
    return cur.lastrowid or 0


def remove_reimbursement(conn: sqlite3.Connection, reimbursement_id: int) -> bool:
    cur = conn.execute("DELETE FROM reimbursements WHERE id = ?", (reimbursement_id,))
    return cur.rowcount > 0


def get_reimbursements(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every paid-back piece with its purchase (and transfer, if any), newest purchase first."""
    return conn.execute(
        """SELECT r.id, r.txn_id, r.amount, r.note, r.incoming_id,
                  t.date, t.description, t.amount AS purchase_amount,
                  i.date AS incoming_date, i.description AS incoming_description
           FROM reimbursements r
           JOIN transactions t ON t.id = r.txn_id
           LEFT JOIN transactions i ON i.id = r.incoming_id
           ORDER BY t.date DESC, r.id"""
    ).fetchall()


def dismiss_match(conn: sqlite3.Connection, txn_id: str, incoming_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO dismissed_matches (txn_id, incoming_id) VALUES (?, ?)",
        (txn_id, incoming_id),
    )
