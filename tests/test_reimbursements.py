"""Purchases you were paid back for: the paid-back part comes off your spending,
never more than the purchase cost, each checking transfer pays back at most one
purchase, and checking rows never show up as spending or in review."""

import pytest
from test_storage import row

from spending import db


def checking(tid: str, amount: float, day: str = "2026-03-20") -> dict:
    """Money in to checking: negative, like a refund on the card."""
    return row(tid, "ZELLE FROM FRIEND", -amount, day, "Incoming", "checking") | {
        "account": "checking"
    }


@pytest.fixture
def concert(conn):
    db.insert_transactions(
        conn,
        [
            row("concert", "TICKETS 1234 LANSING MI", 240.0),
            row("coffee", "BLUE HERON CAFE EAST LANSING MI", 4.5),
            row("refund", "PETSMART 1234 LANSING MI", -20.0),
            checking("zelle1", 80.0),
            checking("zelle2", 80.0),
        ],
    )
    return conn


def spending(conn) -> dict[str, tuple[float, float]]:
    return {
        r["id"]: (r["amount"], r["paid_back"]) for r in conn.execute(db.SPENDING_SQL)
    }


def test_paid_back_part_comes_off_and_checking_is_not_spending(concert):
    db.add_reimbursement(concert, "concert", 80, incoming_id="zelle1")
    db.add_reimbursement(concert, "concert", 80, note="split 3 ways")
    assert spending(concert) == {
        "concert": (240.0, 160.0),
        "coffee": (4.5, 0),
        "refund": (-20.0, 0),
    }
    pieces = db.get_reimbursements(concert)
    assert [(p["amount"], p["incoming_id"], p["note"]) for p in pieces] == [
        (80, "zelle1", None),
        (80, None, "split 3 ways"),
    ]


@pytest.mark.parametrize(
    ("txn_id", "amount", "incoming_id", "error"),
    [
        ("concert", 240.01, None, "left to pay back"),
        ("concert", 0, None, "more than \\$0"),
        ("concert", 90, "zelle1", "more than the transfer"),
        ("refund", 10, None, "card purchase"),
        ("zelle1", 10, None, "card purchase"),
        ("missing", 10, None, "card purchase"),
        ("concert", 10, "coffee", "isn't in your checking"),
    ],
)
def test_impossible_reimbursements_are_refused(
    concert, txn_id, amount, incoming_id, error
):
    with pytest.raises(ValueError, match=error):
        db.add_reimbursement(concert, txn_id, amount, incoming_id=incoming_id)
    assert db.get_reimbursements(concert) == []


def test_a_transfer_pays_back_one_purchase_and_totals_cap_at_the_price(concert):
    db.add_reimbursement(concert, "concert", 200)
    with pytest.raises(ValueError, match="Only \\$40.00"):
        db.add_reimbursement(concert, "concert", 80, incoming_id="zelle1")
    db.add_reimbursement(concert, "coffee", 4.5, incoming_id="zelle1")
    with pytest.raises(ValueError, match="already paid back"):
        db.add_reimbursement(concert, "concert", 40, incoming_id="zelle1")


def test_undo_and_bank_removals(concert):
    rid = db.add_reimbursement(concert, "concert", 80, incoming_id="zelle1")
    assert db.remove_reimbursement(concert, rid)
    assert not db.remove_reimbursement(concert, rid)
    db.add_reimbursement(concert, "concert", 80, incoming_id="zelle1")
    db.add_reimbursement(concert, "coffee", 4.5)
    concert.execute("UPDATE transactions SET source_kind = 'plaid'")
    # The bank dropped the transfer: the piece it paid back goes with it.
    assert db.delete_plaid_transactions(concert, ["zelle1"]) == 1
    assert [p["txn_id"] for p in db.get_reimbursements(concert)] == ["coffee"]
    db.delete_plaid_transactions(concert, ["coffee"])
    assert db.get_reimbursements(concert) == []


def test_checking_rows_stay_out_of_categorizing(concert):
    zelle = row("x", "ZELLE FROM FRIEND")["merchant"]
    assert db.find_merchants(concert, "zelle") == []
    assert db.set_merchant_category(concert, zelle, "Shopping") == 0
    assert db.place_by_llm(concert, zelle, "Shopping") == 0
    rows = concert.execute(
        "SELECT category FROM transactions WHERE account = 'checking'"
    )
    assert {r[0] for r in rows} == {"Incoming"}


def test_logins_by_role_and_their_cutovers(concert):
    db.add_plaid_item(concert, "card-login", "Card bank")
    db.add_plaid_item(concert, "checking-login", "Checking bank", "reimbursements")
    card, checking_login = (
        db.get_plaid_items(concert, "card")[0],
        db.get_plaid_items(concert, "reimbursements")[0],
    )
    assert (card["item_id"], card["cutover"]) == ("card-login", "2026-03-14")
    assert (checking_login["item_id"], checking_login["cutover"]) == (
        "checking-login",
        "2026-03-20",
    )
    assert [i["item_id"] for i in db.get_plaid_items(concert)] == [
        "checking-login",
        "card-login",
    ]
