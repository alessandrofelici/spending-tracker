"""The dashboard counts what purchases cost you: what friends paid back comes off,
and money coming in to checking is never spending. Runs the real app offline
(Streamlit's AppTest) against a throwaway database with made-up rows."""

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from test_reimbursements import checking
from test_storage import row

from spending import db

APP = str(db.ROOT / "src" / "spending" / "dashboard.py")


@pytest.fixture
def app(monkeypatch, db_path):
    """The dashboard reading db_path. Plaid keys are blank (conftest), so bank
    sync is off and nothing reaches the network."""
    monkeypatch.setattr(db, "DB_PATH", db_path)

    def run() -> AppTest:
        st.cache_data.clear()  # the app caches its query across runs in one process
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        assert not at.exception, at.exception
        return at

    return run


def metric(at: AppTest, label: str) -> str:
    return next(m.value for m in at.metric if m.label == label)


def test_paid_back_comes_off_the_total_and_checking_is_left_out(conn, app):
    db.insert_transactions(
        conn,
        [
            row("concert", "TICKETS 1234 LANSING MI", 240.0, "2026-03-10"),
            row("coffee", "BLUE HERON CAFE EAST LANSING MI", 4.5, "2026-03-11"),
            checking("zelle", 80.0, "2026-03-12"),
        ],
    )
    conn.commit()
    assert metric(app(), "Total spent") == "$244.50"

    db.add_reimbursement(conn, "concert", 80.0, incoming_id="zelle")
    db.add_reimbursement(conn, "concert", 80.0, note="paid in cash")
    conn.commit()
    at = app()
    assert metric(at, "Total spent") == "$84.50"
    assert metric(at, "Transactions") == "2"
    table = at.dataframe[-1].value
    assert list(table.amount) == [80.0, 4.5]
    assert list(table.gross) == [240.0, 4.5]


@pytest.fixture
def concert(conn):
    db.insert_transactions(
        conn,
        [
            row("concert", "TICKETS 1234 LANSING MI", 240.0, "2026-03-10"),
            row("dinner", "NOODLE HOUSE LANSING MI", 90.0, "2026-03-11"),
            checking("zelle", 80.0, "2026-03-12"),
            checking("paycheck", 1500.0, "2026-03-13"),
        ],
    )
    conn.commit()
    return conn


def button(at: AppTest, key: str):
    return next(b for b in at.button if b.key == key)


def test_confirming_a_proposed_transfer_counts_it(concert, app):
    at = app()
    assert metric(at, "Total spent") == "$330.00"
    button(at, "ok-zelle").click().run()
    assert not at.exception
    assert metric(at, "Total spent") == "$250.00"
    assert [
        (r["txn_id"], r["incoming_id"]) for r in db.get_reimbursements(concert)
    ] == [("concert", "zelle")]
    # The transfer is used up, so it isn't proposed again.
    assert "ok-zelle" not in [b.key for b in at.button]

    [piece] = db.get_reimbursements(concert)
    button(at, f"undo-{piece['id']}").click().run()
    assert metric(at, "Total spent") == "$330.00"
    assert db.get_reimbursements(concert) == []


def test_dismissing_a_proposal(concert, app):
    at = app()
    first = db.get_reimbursements(concert)
    assert first == [] and "ok-zelle" in [b.key for b in at.button]
    button(at, "never-zelle").click().run()
    assert "ok-zelle" not in [b.key for b in at.button]
    assert metric(at, "Total spent") == "$330.00"


def test_marking_a_purchase_by_hand(concert, app):
    at = app()
    at.selectbox[-1].select("concert")
    at.number_input[0].set_value(120.0)
    at.text_input[0].input("split in half")
    at.button[-1].click()  # the form's Save
    at.run()
    assert not at.exception
    assert metric(at, "Total spent") == "$210.00"
    [piece] = db.get_reimbursements(concert)
    assert (piece["amount"], piece["note"], piece["incoming_id"]) == (
        120.0,
        "split in half",
        None,
    )


def test_marking_more_than_the_price_is_refused(concert, app):
    at = app()
    at.selectbox[-1].select("dinner")
    at.number_input[0].set_value(100.0)
    next(b for b in at.button if b.label == "Save").click()
    at.run()
    assert any("left to pay back" in e.value for e in at.error)
    assert db.get_reimbursements(concert) == []
