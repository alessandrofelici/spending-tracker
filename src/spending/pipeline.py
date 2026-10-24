"""Categorize parsed transactions and store them. Shared by every source of
transactions, so a CSV import and a bank sync go through the same steps."""

import sqlite3
from collections.abc import Mapping, Sequence

from . import db
from .classify import Answer, categorize, confident
from .parser import Transaction


def categorize_rows(
    conn: sqlite3.Connection,
    txns: Sequence[Transaction],
    ids: Sequence[str],
    statement: str,
    source_kind: str = "csv",
    use_llm: bool = True,
) -> tuple[list[dict], dict[str, Answer]]:
    """Database rows for txns (ids aligned with them) and Jev's answers from this run.
    Nothing is saved until save_rows()."""
    results, answers = categorize(
        txns,
        db.get_memory(conn),
        use_llm=use_llm,
        manual=db.get_manual_merchants(conn),
        unplaced=db.get_unplaced(conn),
    )
    rows = [
        {
            "id": tid,
            "month": t.month,
            "date": t.date.isoformat(),
            "description": t.description,
            "merchant": t.merchant,
            "amount": t.amount,
            "category": cat,
            "source": src,
            "statement": statement,
            "source_kind": source_kind,
        }
        for tid, t, (cat, src) in zip(ids, txns, results, strict=True)
    ]
    return rows, answers


def save_rows(
    conn: sqlite3.Connection, rows: list[dict], answers: Mapping[str, Answer]
) -> int:
    """Remember Jev's confident answers, keep every answer for `spend review`, and
    insert the rows, skipping ids already stored. Returns how many rows were new.
    The caller commits."""
    for merchant, cat in confident(answers).items():
        db.remember(conn, merchant, cat, "llm")
    db.save_answers(conn, answers)
    return db.insert_transactions(conn, rows)
