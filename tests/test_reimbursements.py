"""Purchases you were paid back for: the paid-back part comes off your spending,
never more than the purchase cost, each checking transfer pays back at most one
purchase, and checking rows never show up as spending or in review."""

import pytest
from fakes import ACCESS_TOKEN, CARD, CHECKING, txn
from test_storage import row

from spending import db, plaid_source


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


# --- The checking login (Plaid is faked: tests/fakes.py) ----------------------


def on_checking(
    tid: str, day: str, description: str, amount: float, pending: bool = False
) -> dict:
    return txn(
        tid, day, description, amount, CHECKING["account_id"], pending, wrap=False
    )


def test_a_checking_login_keeps_the_card_login(conn, fake_plaid):
    plaid = fake_plaid.client()
    plaid_source.link(conn, plaid, "public-card", "Card bank")
    _, problems = plaid_source.link(
        conn, plaid, "public-checking", "Checking bank", "reimbursements"
    )
    assert problems == [] and fake_plaid.removed == []
    roles = {i["item_id"]: i["role"] for i in db.get_plaid_items(conn)}
    assert roles == {"item-1": "card", "item-2": "reimbursements"}

    # Signing in to checking again replaces only the older checking login.
    plaid_source.link(conn, plaid, "public-again", "Checking bank", "reimbursements")
    roles = {i["item_id"]: i["role"] for i in db.get_plaid_items(conn)}
    assert roles == {"item-1": "card", "item-3": "reimbursements"}
    assert fake_plaid.removed == ["access-sandbox-login-2"]


def test_checking_sync_stores_only_money_in_and_never_asks_jev(
    conn, fake_plaid, monkeypatch
):
    def no_jev(*_args, **_kwargs):
        raise AssertionError("checking rows must never be categorized")

    monkeypatch.setattr(plaid_source, "categorize_rows", no_jev)
    db.add_plaid_item(conn, "item-chk", "Checking bank", "reimbursements")
    fake_plaid.page(
        added=[
            on_checking("in", "2026-03-20", "ZELLE FROM FRIEND", -80.0),
            on_checking("rent", "2026-03-01", "RENT PAYMENT", 900.0),
            on_checking("pend", "2026-03-21", "VENMO CASHOUT", -40.0, pending=True),
            txn("card", "2026-03-14", "TICKETS 1234 LANSING MI", 240.0),
        ]
    )
    result = plaid_source.sync(conn, fake_plaid.client(), "item-chk", ACCESS_TOKEN)

    assert (result.added, result.flagged) == (1, 0)
    assert result.skipped_accounts == [CARD["name"]]
    [r] = conn.execute("SELECT * FROM transactions").fetchall()
    assert (r["id"], r["account"], r["amount"], r["category"], r["source"]) == (
        "in",
        "checking",
        -80.0,
        "Incoming",
        "checking",
    )
    assert list(conn.execute(db.SPENDING_SQL)) == []  # not spending
