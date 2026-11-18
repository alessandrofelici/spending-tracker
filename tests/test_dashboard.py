"""The dashboard counts what purchases cost you: what friends paid back comes off,
and money coming in to checking is never spending. Runs the real app offline
(Streamlit's AppTest) against a throwaway database with made-up rows."""

import base64
import json

import numpy as np
import pytest
import streamlit as st
from fakes import CARD, CHECKING, txn
from streamlit.testing.v1 import AppTest
from test_reimbursements import checking
from test_storage import row

from spending import db, plaid_source
from spending.plaid_source import Plaid

APP = str(db.ROOT / "src" / "spending" / "app.py")


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


def button(at: AppTest, key: str):
    return next(b for b in at.button if b.key == key)


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
    # Proposed against the tickets, on the Paid back page.
    assert [e.proto.label for e in at.get("page_link")] == ["1 Zelle payment to review"]


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


def test_connecting_the_card_offers_only_credit_accounts(conn, banks, app):
    at = app()
    button(at, "sign-in-card").click().run()
    [body] = banks.bodies("/link/token/create")
    assert body["account_filters"] == plaid_source.ACCOUNT_FILTERS["card"]
    assert "credit" in body["account_filters"]


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


# --- Analysis view (#16) -------------------------------------------------------


def test_the_analysis_view_runs(months, app):
    at = app()
    at.sidebar.radio(key="view").set_value("Analysis").run()
    assert not at.exception
    headers = [h.value for h in at.subheader]
    assert "One category over time" in headers
    assert "What changed between two months" in headers
    assert "Total spent" not in [m.label for m in at.metric]  # the spending page is off
    at.selectbox(key="cmp_before").select("2025-11").run()
    assert not at.exception


# --- Checking Jev's categories (#14) --------------------------------------------


@pytest.fixture
def jev_placed(conn):
    db.insert_transactions(
        conn,
        [
            row(
                "rent1", "MAPLE RESORT APARTMENTS", 900.0, "2026-03-01", "Travel", "llm"
            ),
            row(
                "rent2", "MAPLE RESORT APARTMENTS", 900.0, "2026-04-01", "Travel", "llm"
            ),
            row(
                "cafe",
                "BLUE HERON CAFE EAST LANSING MI",
                6.0,
                "2026-04-02",
                "Dining",
                "llm",
            ),
            row(
                "mine",
                "PETSMART 1234 LANSING MI",
                30.0,
                "2026-04-03",
                "Shopping",
                "manual",
            ),
            row("unsure", "QUIET PINE BOOKS OKEMOS MI", 12.0, "2026-04-04"),
        ],
    )
    conn.commit()
    return conn


def check(app) -> AppTest:
    at = app()
    at.sidebar.radio(key="view").set_value("Check categories").run()
    assert not at.exception
    return at


def test_stores_jev_placed_are_listed_biggest_first(jev_placed, app):
    at = check(app)
    keys = [b.key for b in at.button]
    rent, cafe = (
        row("x", d)["merchant"]
        for d in ("MAPLE RESORT APARTMENTS", "BLUE HERON CAFE EAST LANSING MI")
    )
    # Only Jev's picks: not the store you set, nor the one waiting in review.
    assert keys == [f"jev-ok-{rent}", f"jev-ok-{cafe}"]


def test_fixing_a_store_sticks_for_all_its_purchases(jev_placed, app):
    rent = row("x", "MAPLE RESORT APARTMENTS")["merchant"]
    at = check(app)
    at.selectbox(key=f"jev-cat-{rent}").select("Bills & Utilities").run()
    button(at, f"jev-save-{rent}").click().run()
    assert not at.exception
    rows = jev_placed.execute(
        "SELECT DISTINCT category, source FROM transactions WHERE merchant = ?", (rent,)
    ).fetchall()
    assert [tuple(r) for r in rows] == [("Bills & Utilities", "manual")]
    assert db.get_memory(jev_placed)[rent] == "Bills & Utilities"
    assert f"jev-ok-{rent}" not in [b.key for b in at.button]


def test_looks_right_keeps_the_category_and_stops_asking(jev_placed, app):
    cafe = row("x", "BLUE HERON CAFE EAST LANSING MI")["merchant"]
    at = check(app)
    button(at, f"jev-ok-{cafe}").click().run()
    [r] = jev_placed.execute(
        "SELECT category, source FROM transactions WHERE merchant = ?", (cafe,)
    ).fetchall()
    assert tuple(r) == ("Dining", "manual")
    assert f"jev-ok-{cafe}" not in [b.key for b in at.button]


# --- Connected accounts page (#33) -----------------------------------------------


def settings(at: AppTest) -> AppTest:
    at.switch_page("settings_page.py").run()
    assert not at.exception, at.exception
    return at


def texts(at: AppTest) -> str:
    """Everything the page says, to search."""
    parts = [
        e.value
        for kind in ("markdown", "caption", "error", "warning")
        for e in getattr(at, kind)
    ]
    parts += [d.value.to_string() for d in at.dataframe]
    return "\n".join(parts)


@pytest.fixture
def both_logins(conn, banks):
    """A card login and a checking login, synced when the app opened."""
    login(conn, "item-card", "card")
    login(conn, "item-chk", "reimbursements")
    banks.accounts = [CARD, CHECKING]
    banks.page()
    banks.page()
    return banks


def test_connected_accounts_shows_each_login_and_what_it_shares(both_logins, app):
    at = settings(app())
    page = texts(at)
    assert "#### Credit card · Example Credit Union" in page
    assert "#### Checking · Example Credit Union" in page
    card, checking = (d.value for d in at.dataframe)
    assert list(card["Number"]) == ["••1234", "••9012"]
    assert list(card["Used for"]) == [
        "Card spending",
        "Nothing: only credit accounts count as spending",
    ]
    assert list(checking["Used for"]) == [
        "Nothing: only checking is used",
        "Money in, to spot paid-back purchases",
    ]
    assert page.count("Working") == 2
    assert "Synced just now: 0 new, 0 updated, 0 removed" in page
    assert "access-" not in page  # tokens never reach the page
    # Checked once per visit, not on every rerun.
    at.run()
    assert len(both_logins.bodies("/item/get")) == 2


def test_opening_connected_accounts_first_still_syncs(conn, banks, app):
    login(conn, "item-card", "card")
    banks.page(added=[txn("t1", "2026-03-10", "TICKETS 1234 LANSING MI", 240.0)])
    st.cache_data.clear()
    # AppTest always opens the default page, so run the settings page by itself.
    at = AppTest.from_file(
        APP.replace("app.py", "settings_page.py"), default_timeout=60
    )
    at.run()
    assert not at.exception, at.exception
    assert len(banks.bodies("/transactions/sync")) == 1
    assert "Synced just now: 1 new" in texts(at)
    assert "Newest credit transaction saved: 2026-03-10" in texts(at)
    assert "sign-in-reimbursements" in [b.key for b in at.button]  # not connected


def test_a_card_login_with_no_credit_card_says_so(conn, banks, app):
    login(conn, "item-card", "card")
    banks.accounts = [CHECKING]
    banks.page()
    at = settings(app())
    assert "Not tracking anything" in texts(at)
    assert any("shares no credit card" in e.value for e in at.error)
    button(at, "switch-card").click().run()
    [body] = banks.bodies("/link/token/create")
    assert body["account_filters"] == plaid_source.ACCOUNT_FILTERS["card"]
    assert at.session_state.link_attempt["role"] == "card"
    assert at.session_state.link_attempt["update"] is False  # a new login


def test_a_login_that_needs_a_sign_in_offers_one(conn, banks, app):
    login(conn, "item-card", "card")
    banks.page()
    banks.item_error = {
        "error_code": "ITEM_LOGIN_REQUIRED",
        "error_message": "the login details of this item have changed",
        "display_message": None,
    }
    at = settings(app())
    assert "Needs sign-in" in texts(at)
    button(at, "again-card").click().run()
    [body] = banks.bodies("/link/token/create")
    assert body["access_token"] == "access-item-card"  # the same login
    assert at.session_state.link_attempt["update"] is True


def test_disconnecting_one_login_keeps_the_other(conn, both_logins, app):
    at = settings(app())
    button(at, "unlink-card").click().run()
    assert any("Disconnect Example Credit Union?" in w.value for w in at.warning)
    button(at, "unlink-no-card").click().run()  # Cancel
    assert both_logins.removed == []

    button(at, "unlink-card").click().run()
    button(at, "unlink-yes-card").click().run()
    assert not at.exception
    assert both_logins.removed == ["access-item-card"]
    assert [i["item_id"] for i in db.get_plaid_items(conn)] == ["item-chk"]
    assert plaid_source.load_token("item-chk") == "access-item-chk"
    assert any("Disconnected Example Credit Union" in s.value for s in at.success)
    assert "sign-in-card" in [b.key for b in at.button]  # connect a card again


def test_sync_now_syncs_and_checks_again(conn, banks, app):
    login(conn, "item-card", "card")
    banks.page()
    at = settings(app())
    banks.page(added=[txn("t1", "2026-03-10", "TICKETS 1234 LANSING MI", 240.0)])
    button(at, "sync-now").click().run()
    assert not at.exception
    assert len(banks.bodies("/transactions/sync")) == 2
    assert len(banks.bodies("/item/get")) == 2
    assert "Synced just now: 1 new" in texts(at)


def test_connected_accounts_without_bank_sync(app):
    at = settings(app())
    assert any("Bank sync isn't set up" in i.value for i in at.info)


def test_the_dashboard_warns_when_the_card_login_tracks_nothing(conn, banks, app):
    login(conn, "item-card", "card")
    banks.accounts = [CHECKING]  # a bank with no credit card
    banks.page()
    at = app()
    [warning] = [w.value for w in at.warning if "shares no credit card" in w.value]
    assert warning.startswith("card bank shares no credit card")
    assert "Not tracked" not in " ".join(c.value for c in at.caption)
