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
