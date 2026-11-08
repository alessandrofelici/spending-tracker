"""Spot purchases friends paid you back for (issue #19).

Money coming in to checking (the second bank login, see plaid_source.py) is
matched to a recent card purchase: a transfer that arrives within
REIMBURSE_WINDOW_DAYS after a purchase of at least REIMBURSE_MIN_PURCHASE, for no
more than what's still left to pay back of it. Several transfers can pay back one
purchase, e.g. three friends sending their share of a concert order.

Matches are only proposed: nothing counts until you confirm it in the dashboard
(db.add_reimbursement). Saying a match is wrong hides it for good
(db.dismiss_match), and saying a transfer isn't a paid-back share at all, like a
paycheck, hides it from every purchase. Everything stays on this machine: these
descriptions name private individuals.
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial

from . import db

DEFAULT_WINDOW_DAYS = 30
DEFAULT_MIN_PURCHASE = 50.0
# Even shares of a purchase (all of it, half, a third, ...), preferred over other
# amounts when a transfer could pay back several purchases.
SPLITS = range(1, 7)
CENTS = 0.01  # amounts within a cent are the same amount
NOT_A_REIMBURSEMENT = (
    "*"  # dismissed_matches.txn_id: hide the transfer from every purchase
)


@dataclass(frozen=True)
class Match:
    purchase_id: str
    purchase_date: str
    purchase_description: str
    purchase_amount: float
    left: float  # still to pay back of the purchase, before this transfer
    incoming_id: str
    incoming_date: str
    incoming_description: str
    amount: float  # what the transfer brought in, i.e. what it would pay back
    share: int | None  # k when the amount is 1/k of the purchase, else None


def window_days() -> int:
    return int(os.environ.get("REIMBURSE_WINDOW_DAYS", DEFAULT_WINDOW_DAYS))


def min_purchase() -> float:
    return float(os.environ.get("REIMBURSE_MIN_PURCHASE", DEFAULT_MIN_PURCHASE))


def share_of(amount: float, purchase: float) -> int | None:
    """k when amount is an even 1/k share of purchase (to the cent), else None."""
    for k in SPLITS:
        if abs(purchase / k - amount) <= CENTS:
            return k
    return None


def propose(
    conn: sqlite3.Connection,
    window: int | None = None,
    minimum: float | None = None,
) -> list[Match]:
    """At most one proposed purchase per unused transfer, oldest transfer first.
    Transfers are taken in date order, and each one only counts against what the
    earlier proposals left of a purchase, so accepting every proposal never pays
    back more than a purchase cost."""
    window = window_days() if window is None else window
    minimum = min_purchase() if minimum is None else minimum
    purchases = conn.execute(
        """SELECT t.id, t.date, t.description, t.amount,
                  t.amount - COALESCE(SUM(r.amount), 0) AS left
           FROM transactions t LEFT JOIN reimbursements r ON r.txn_id = t.id
           WHERE t.account = 'credit' AND t.amount >= ?
           GROUP BY t.id HAVING left > ?""",
        (minimum, CENTS / 2),
    ).fetchall()
    incoming = conn.execute(
        """SELECT id, date, description, -amount AS amount FROM transactions i
           WHERE account = 'checking' AND amount < 0
             AND NOT EXISTS (SELECT 1 FROM reimbursements WHERE incoming_id = i.id)
             AND NOT EXISTS (SELECT 1 FROM dismissed_matches
                             WHERE incoming_id = i.id AND txn_id = ?)
           ORDER BY date, id""",
        (NOT_A_REIMBURSEMENT,),
    ).fetchall()
    dismissed = {
        (r["txn_id"], r["incoming_id"])
        for r in conn.execute("SELECT txn_id, incoming_id FROM dismissed_matches")
    }
    left = {p["id"]: round(p["left"], 2) for p in purchases}
    out = []
    for i in incoming:
        arrived = date.fromisoformat(i["date"])
        earliest = (arrived - timedelta(days=window)).isoformat()
        amount = round(i["amount"], 2)
        fits = [
            p
            for p in purchases
            if earliest <= p["date"] <= i["date"]
            and amount <= left[p["id"]] + CENTS / 2
            and (p["id"], i["id"]) not in dismissed
        ]
        if not fits:
            continue

        best = min(fits, key=partial(_rank, amount))
        out.append(
            Match(
                purchase_id=best["id"],
                purchase_date=best["date"],
                purchase_description=best["description"],
                purchase_amount=best["amount"],
                left=left[best["id"]],
                incoming_id=i["id"],
                incoming_date=i["date"],
                incoming_description=i["description"],
                amount=amount,
                share=share_of(amount, best["amount"]),
            )
        )
        left[best["id"]] = round(left[best["id"]] - amount, 2)
    return out


def _rank(amount: float, purchase: sqlite3.Row) -> tuple:
    """Best first: an even share of the purchase (all of it before half, ...),
    then the most recent purchase."""
    share = share_of(amount, purchase["amount"])
    return (
        share is None,
        share or 0,
        -date.fromisoformat(purchase["date"]).toordinal(),
    )


def confirm(conn: sqlite3.Connection, match: Match, note: str | None = None) -> int:
    """Count a proposed match. The caller commits."""
    return db.add_reimbursement(
        conn, match.purchase_id, match.amount, note, incoming_id=match.incoming_id
    )


def not_a_reimbursement(conn: sqlite3.Connection, incoming_id: str) -> None:
    """Never propose this transfer again, for any purchase. The caller commits."""
    conn.execute(
        "INSERT OR IGNORE INTO dismissed_matches (txn_id, incoming_id) VALUES (?, ?)",
        (NOT_A_REIMBURSEMENT, incoming_id),
    )
