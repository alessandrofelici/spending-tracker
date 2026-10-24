"""Your data stays correct: re-imports never duplicate or overwrite rows, your own
choices win over Jev's, and older databases upgrade in place without losing rows."""

import sqlite3
from datetime import date

from spending import db
from spending.parser import Transaction, transaction_ids


def row(
    tid: str,
    description: str,
    amount: float = 10.0,
    day: str = "2026-03-14",
    category: str = "Other",
    source: str = "fallback",
) -> dict:
    t = Transaction(date.fromisoformat(day), description, amount)
    return {
        "id": tid,
        "month": t.month,
        "date": day,
        "description": description,
        "merchant": t.merchant,
        "amount": amount,
        "category": category,
        "source": source,
        "statement": "export.csv",
    }


def categories(conn) -> dict[str, tuple[str, str]]:
    return {
        r["id"]: (r["category"], r["source"])
        for r in conn.execute("SELECT id, category, source FROM transactions")
    }


def test_reimporting_adds_nothing_and_keeps_your_changes(conn):
    rows = [row("a", "PETSMART 1234 LANSING MI")]
    assert db.insert_transactions(conn, rows) == 1
    db.set_merchant_category(conn, rows[0]["merchant"], "Shopping")
    assert db.insert_transactions(conn, rows) == 0
    assert categories(conn) == {"a": ("Shopping", "manual")}


def test_ids_are_stable_and_tell_identical_charges_apart():
    coffee = Transaction(date(2026, 3, 14), "BLUE HERON CAFE EAST LANSING MI", 4.5)
    ids = transaction_ids([coffee, coffee])
    assert ids[0] != ids[1]  # two coffees on the same day are two rows
    assert transaction_ids([coffee, coffee]) == ids
    # A download that holds only the first of them gives it the same id.
    assert transaction_ids([coffee]) == ids[:1]


def test_jev_never_overwrites_your_choice(conn):
    db.remember(conn, "PETSMART LANSING", "Shopping", "manual")
    db.remember(conn, "PETSMART LANSING", "Other", "llm")
    assert db.get_memory(conn) == {"PETSMART LANSING": "Shopping"}
    db.remember(conn, "PETSMART LANSING", "Health", "manual")  # you can change it
    assert db.get_memory(conn) == {"PETSMART LANSING": "Health"}


def test_jev_leaves_rows_you_set_and_payments_alone(conn):
    desc = "PETSMART 1234 LANSING MI"
    db.insert_transactions(
        conn,
        [
            row("yours", desc, category="Shopping", source="manual"),
            row("unsure", desc),
            row("payment", desc, category="Payments & Credits", source="payment"),
        ],
    )
    assert db.place_by_llm(conn, row("x", desc)["merchant"], "Health") == 1
    assert categories(conn) == {
        "yours": ("Shopping", "manual"),
        "unsure": ("Health", "llm"),
        "payment": ("Payments & Credits", "payment"),
    }


def test_setting_a_category_moves_only_that_merchant(conn):
    db.insert_transactions(
        conn,
        [
            row("a", "PETSMART 1234 LANSING MI"),
            row("b", "PETSMART 5678 LANSING MI", day="2026-03-20"),
            row("c", "BLUE HERON CAFE EAST LANSING MI"),
        ],
    )
    merchant = row("x", "PETSMART 1234 LANSING MI")["merchant"]
    assert db.set_merchant_category(conn, merchant, "Shopping") == 2
    assert categories(conn) == {
        "a": ("Shopping", "manual"),
        "b": ("Shopping", "manual"),
        "c": ("Other", "fallback"),
    }
    assert db.get_manual_merchants(conn) == {merchant}


# Tables as they were before the latency_ms column existed.
OLD_SCHEMA = """
CREATE TABLE transactions (
    id TEXT PRIMARY KEY, month TEXT NOT NULL, date TEXT NOT NULL,
    description TEXT NOT NULL, merchant TEXT NOT NULL, amount REAL NOT NULL,
    category TEXT NOT NULL, source TEXT NOT NULL, statement TEXT NOT NULL
);
CREATE TABLE jev_answers (
    merchant TEXT PRIMARY KEY, choice TEXT, confidence REAL NOT NULL,
    outcome TEXT NOT NULL, asked_with TEXT NOT NULL
);
INSERT INTO transactions VALUES ('pay', '2026-03', '2026-03-20', 'ACH PMT 0005550123',
    'ACH PMT', -300, 'Payments & Credits', 'rule', 'export.csv');
INSERT INTO transactions VALUES ('shop', '2026-03', '2026-03-14', 'PETSMART 1234 LANSING MI',
    'PETSMART LANSING', 25, 'Shopping', 'rule', 'export.csv');
INSERT INTO jev_answers VALUES ('PETSMART LANSING', 'Shopping', 0.9, 'confident', 'abc');
"""


def test_older_databases_upgrade_without_losing_rows(db_path):
    db_path.parent.mkdir()
    old = sqlite3.connect(db_path)
    old.executescript(OLD_SCHEMA)
    old.close()

    conn = db.connect(db_path)
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM transactions")}
    assert set(rows) == {"pay", "shop"}
    assert rows["pay"]["source"] == "payment"  # payment rows get their own source
    assert rows["shop"]["source"] == "rule"  # other old rows keep theirs
    assert db.get_answers(conn)["PETSMART LANSING"]["latency_ms"] is None
    conn.close()

    db.connect(db_path).close()  # upgrading again is a no-op
