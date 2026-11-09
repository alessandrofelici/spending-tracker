"""The dashboard counts what purchases cost you: what friends paid back comes off,
and money coming in to checking is never spending. Runs the real app offline
(Streamlit's AppTest) against a throwaway database with made-up rows."""

import base64
import json

import numpy as np
import pytest
import streamlit as st
from fakes import CHECKING, txn
from streamlit.testing.v1 import AppTest
from test_reimbursements import checking
from test_storage import row

from spending import db, plaid_source
from spending.plaid_source import Plaid

APP = str(db.ROOT / "src" / "spending" / "dashboard.py")


@pytest.fixture
def app(monkeypatch, db_path):
    """The dashboard reading db_path. Plaid keys are blank (conftest), so bank
    sync is off and nothing reaches the network."""
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # connect()'s default path was bound when db.py loaded; bank sync relies on it.
    monkeypatch.setattr(db.connect, "__defaults__", (db_path,))

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
    at.selectbox(key="paid_back_purchase").select("concert")
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
    at.selectbox(key="paid_back_purchase").select("dinner")
    at.number_input[0].set_value(100.0)
    next(b for b in at.button if b.label == "Save").click()
    at.run()
    assert any("left to pay back" in e.value for e in at.error)
    assert db.get_reimbursements(concert) == []


# --- Bank sync with a card login and a checking login (Plaid is faked) --------


@pytest.fixture
def banks(monkeypatch, conn, fake_plaid):
    """Bank sync on, talking to the fake Plaid."""
    monkeypatch.setattr(
        Plaid, "from_env", classmethod(lambda _cls: fake_plaid.client())
    )
    return fake_plaid


def login(conn, item_id: str, role: str) -> None:
    db.add_plaid_item(conn, item_id, f"{role} bank", role)
    plaid_source.save_token(item_id, f"access-{item_id}")
    conn.commit()


def test_both_logins_sync_when_the_app_opens(conn, banks, app):
    login(conn, "item-card", "card")
    login(conn, "item-chk", "reimbursements")
    banks.page(added=[txn("t1", "2026-03-10", "TICKETS 1234 LANSING MI", 240.0)])
    banks.page(
        added=[
            txn(
                "in1",
                "2026-03-12",
                "ZELLE FROM FRIEND",
                -80.0,
                CHECKING["account_id"],
                wrap=False,
            )
        ]
    )
    at = app()
    assert [b["access_token"] for b in banks.bodies("/transactions/sync")] == [
        "access-item-card",
        "access-item-chk",
    ]
    accounts = dict(conn.execute("SELECT id, account FROM transactions").fetchall())
    assert accounts == {"t1": "credit", "in1": "checking"}
    assert any("1 new transfer(s) in" in c.value for c in at.caption)
    assert metric(at, "Total spent") == "$240.00"
    assert "ok-in1" in [b.key for b in at.button]  # proposed against the tickets


def test_connecting_checking_offers_only_checking_accounts(conn, banks, app):
    login(conn, "item-card", "card")
    banks.page()
    db.insert_transactions(conn, [row("t1", "TICKETS 1234 LANSING MI", 240.0)])
    conn.commit()
    at = app()
    assert "sign-in-card" not in [b.key for b in at.button]
    button(at, "sign-in-reimbursements").click().run()
    [body] = banks.bodies("/link/token/create")
    assert body["account_filters"] == {"depository": {"account_subtypes": ["checking"]}}
    assert at.session_state.link_attempt["role"] == "reimbursements"


def test_checking_waits_until_the_card_is_connected(conn, banks, app):
    at = app()
    keys = [b.key for b in at.button]
    assert "sign-in-card" in keys and "sign-in-reimbursements" not in keys


# --- Time range (#27) ----------------------------------------------------------


@pytest.fixture
def months(conn):
    db.insert_transactions(
        conn,
        [
            row("a", "NORTHWIND GROCERY 0042 LANSING MI", 100.0, "2025-11-03"),
            row("b", "NORTHWIND GROCERY 0042 LANSING MI", 80.0, "2025-12-03"),
            row("c", "NOODLE HOUSE LANSING MI", 40.0, "2026-01-03"),
            row("d", "NOODLE HOUSE LANSING MI", 60.0, "2026-02-03"),
        ],
    )
    conn.commit()
    return conn


def test_one_month_is_still_the_default(months, app):
    at = app()
    assert at.selectbox(key="period").value == "One month"
    assert at.selectbox(key="month").value == "2026-02"
    assert metric(at, "Total spent") == "$60.00"
    assert "Average per month" not in [m.label for m in at.metric]


def test_a_year_and_a_custom_range(months, app):
    at = app()
    at.selectbox(key="period").select("2025").run()
    assert metric(at, "Total spent") == "$180.00"
    assert "Average per month" in [m.label for m in at.metric]
    assert len(at.dataframe[-1].value) == 2  # the transactions in 2025

    at.selectbox(key="period").select("Custom range").run()
    at.selectbox(key="from").select("2025-12")
    at.selectbox(key="to").select("2026-01").run()
    assert not at.exception
    assert metric(at, "Total spent") == "$120.00"
    # Compared with the two months before: just November's $100.
    total = next(m for m in at.metric if m.label == "Total spent")
    assert "+20.00 vs 2025-10 to 2025-11" in total.delta


# --- Everyday vs travel and big one-offs (#26) ---------------------------------


def chart_total(at: AppTest, key: str) -> float:
    """Sum of every bar in the monthly chart with this key."""
    chart = next(c for c in at.get("plotly_chart") if f"monthly-{key}" in c.proto.id)
    fig = json.loads(chart.proto.spec)
    total = 0.0
    for trace in fig["data"]:
        y = trace["y"]
        if isinstance(y, dict):  # Plotly's binary encoding
            y = np.frombuffer(base64.b64decode(y["bdata"]), dtype=y["dtype"])
        total += float(sum(y))
    return total


def test_everyday_and_one_offs_add_up_to_the_total(conn, app):
    db.insert_transactions(
        conn,
        [
            row("hotel", "EXAMPLE HOTEL 0042", 90.0, "2026-03-02", "Travel", "manual"),
            row(
                "laptop",
                "EXAMPLE ELECTRONICS",
                900.0,
                "2026-03-05",
                "Shopping",
                "manual",
            ),
            row("food", "NORTHWIND GROCERY 0042 LANSING MI", 60.0, "2026-03-07"),
        ],
    )
    conn.commit()
    at = app()
    assert metric(at, "Total spent") == "$1,050.00"
    assert any("Everyday $60.00" in c.value for c in at.caption)
    assert [t.label for t in at.tabs] == [
        "Overall",
        "Travel & big one-offs",
        "Everyday",
    ]
    assert chart_total(at, "overall") == 1050.0
    assert chart_total(at, "one-off") == 990.0
    assert chart_total(at, "everyday") == 60.0
