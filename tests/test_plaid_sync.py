"""Bank sync: how /transactions/sync changes your data. Plaid is faked
(tests/fakes.py), so these run offline with made-up transactions."""

from datetime import date

import pytest
from fakes import ACCESS_TOKEN, CARD, CHECKING, CREDIT_LINE, txn

from spending import db, plaid_source
from spending.parser import Transaction
from spending.plaid_source import PlaidError

ITEM = "item-1"
PETSMART = "PETSMART 1234 LANSING MI"
CAFE = "BLUE HERON CAFE EAST LANSING MI"


@pytest.fixture
def plaid(fake_plaid):
    return fake_plaid.client()


@pytest.fixture
def item(conn):
    """A linked bank login, made while the database was empty (so no cutover)."""
    db.add_plaid_item(conn, ITEM, "Example Credit Union")
    conn.commit()
    return ITEM


def sync(conn, plaid) -> plaid_source.SyncResult:
    return plaid_source.sync(conn, plaid, ITEM, ACCESS_TOKEN, use_llm=False)


def stored(conn) -> dict:
    return {r["id"]: r for r in conn.execute("SELECT * FROM transactions")}


def saved_cursor(conn) -> str | None:
    return conn.execute("SELECT cursor FROM plaid_items").fetchone()["cursor"]


def sent_cursors(fake_plaid) -> list[str | None]:
    return [b.get("cursor") for b in fake_plaid.bodies("/transactions/sync")]


def csv_row(tid: str, description: str, day: str) -> dict:
    t = Transaction(date.fromisoformat(day), description, 10.0)
    return {
        "id": tid,
        "month": t.month,
        "date": day,
        "description": description,
        "merchant": t.merchant,
        "amount": 10.0,
        "category": "Other",
        "source": "fallback",
        "statement": "export.csv",
        "source_kind": "csv",
    }


# --- New, changed and removed transactions ------------------------------------


def test_new_card_transactions_are_stored(conn, plaid, fake_plaid, item):
    fake_plaid.page(
        added=[
            txn("t1", "2026-03-14", PETSMART, 25.0),
            txn("t2", "2026-03-15", CAFE, 6.5),
        ]
    )
    result = sync(conn, plaid)

    assert (result.added, result.flagged) == (2, 2)
    rows = stored(conn)
    assert set(rows) == {"t1", "t2"}
    t1 = rows["t1"]
    assert t1["description"] == PETSMART  # MSUFCU's wrapper removed
    assert (t1["date"], t1["month"], t1["amount"]) == ("2026-03-14", "2026-03", 25.0)
    assert (t1["statement"], t1["source_kind"]) == ("Plaid", "plaid")
    assert (t1["category"], t1["source"]) == ("Other", "fallback")


def test_only_credit_accounts_are_stored(conn, plaid, fake_plaid, item):
    fake_plaid.accounts = [CARD, CREDIT_LINE, CHECKING]
    fake_plaid.page(
        added=[
            txn("card", "2026-03-14", PETSMART, 25.0),
            txn("line", "2026-03-14", CAFE, 6.5, account=CREDIT_LINE["account_id"]),
            txn(
                "checking",
                "2026-03-15",
                "EXAMPLE PAYROLL",
                -2000.0,
                account=CHECKING["account_id"],
            ),
        ]
    )
    result = sync(conn, plaid)
    assert set(stored(conn)) == {"card", "line"}
    assert result.skipped_accounts == ["Example Checking"]


def test_pending_transactions_wait_until_they_post(conn, plaid, fake_plaid, item):
    fake_plaid.page(
        added=[txn("t1", "2026-03-14", PETSMART, 25.0, pending=True)], next_cursor="c1"
    )
    assert sync(conn, plaid).added == 0
    assert stored(conn) == {}

    # Posted later: Plaid reports it as modified, but it isn't stored yet.
    fake_plaid.page(
        modified=[txn("t1", "2026-03-14", PETSMART, 25.0)], next_cursor="c2"
    )
    result = sync(conn, plaid)
    assert (result.added, result.updated) == (1, 0)
    assert set(stored(conn)) == {"t1"}


def test_rows_your_csv_import_has_are_skipped(conn, plaid, fake_plaid):
    db.insert_transactions(conn, [csv_row("csv-1", CAFE, "2026-03-10")])
    db.add_plaid_item(conn, ITEM, None)  # cutover: the newest stored date
    fake_plaid.page(
        added=[
            txn("before", "2026-03-09", CAFE, 6.5),
            txn("same-day", "2026-03-10", CAFE, 6.5),
            txn("after", "2026-03-11", CAFE, 6.5),
        ]
    )
    assert sync(conn, plaid).added == 1
    assert set(stored(conn)) == {"csv-1", "after"}


def test_your_choices_apply_to_bank_rows(conn, plaid, fake_plaid, item):
    # The CSV export shows the bare merchant; Plaid's wrapped one must match it.
    merchant = csv_row("x", PETSMART, "2026-03-01")["merchant"]
    grocery = csv_row("x", "NORTHWIND GROCERY 0042 LANSING MI", "2026-03-01")
    db.remember(conn, merchant, "Shopping", "manual")
    db.remember(conn, grocery["merchant"], "Groceries", "llm")
    fake_plaid.page(
        added=[
            txn("yours", "2026-03-14", PETSMART, 25.0),
            txn("remembered", "2026-03-14", grocery["description"], 40.0),
            txn(
                "payment",
                "2026-03-20",
                "ACH Loan Payment 0005550123",
                -300.0,
                wrap=False,
            ),
        ]
    )
    result = sync(conn, plaid)

    rows = stored(conn)
    assert (rows["yours"]["category"], rows["yours"]["source"]) == (
        "Shopping",
        "manual",
    )
    assert (rows["remembered"]["category"], rows["remembered"]["source"]) == (
        "Groceries",
        "memory",
    )
    assert (rows["payment"]["category"], rows["payment"]["source"]) == (
        "Payments & Credits",
        "payment",
    )
    assert result.flagged == 0


def test_changes_at_the_bank_keep_your_category(conn, plaid, fake_plaid, item):
    fake_plaid.page(added=[txn("t1", "2026-03-14", PETSMART, 25.0)], next_cursor="c1")
    sync(conn, plaid)
    db.set_merchant_category(conn, stored(conn)["t1"]["merchant"], "Shopping")

    fake_plaid.page(
        modified=[txn("t1", "2026-03-15", PETSMART, 27.5)], next_cursor="c2"
    )
    result = sync(conn, plaid)

    assert (result.updated, result.added) == (1, 0)
    t1 = stored(conn)["t1"]
    assert (t1["date"], t1["amount"]) == ("2026-03-15", 27.5)
    assert (t1["category"], t1["source"]) == ("Shopping", "manual")


def test_removed_at_the_bank_deletes_only_bank_rows(conn, plaid, fake_plaid, item):
    db.insert_transactions(conn, [csv_row("csv-1", CAFE, "2026-03-01")])
    fake_plaid.page(added=[txn("t1", "2026-03-14", PETSMART, 25.0)], next_cursor="c1")
    sync(conn, plaid)

    fake_plaid.page(removed=["t1", "csv-1"], next_cursor="c2")
    assert sync(conn, plaid).removed == 1
    assert set(stored(conn)) == {"csv-1"}


def test_rows_without_an_amount_or_description_are_skipped(
    conn, plaid, fake_plaid, item
):
    no_amount = txn("zero", "2026-03-14", PETSMART, 0.0)
    no_description = txn("blank", "2026-03-14", PETSMART, 5.0)
    no_description["original_description"] = no_description["name"] = ""
    fake_plaid.page(added=[no_amount, no_description])
    assert sync(conn, plaid).added == 0
    assert stored(conn) == {}


# --- Cursor, paging and errors ------------------------------------------------


def test_each_sync_continues_where_the_last_stopped(conn, plaid, fake_plaid, item):
    fake_plaid.page(added=[txn("t1", "2026-03-14", PETSMART, 25.0)], next_cursor="c1")
    sync(conn, plaid)
    fake_plaid.page(next_cursor="c2")
    sync(conn, plaid)

    assert sent_cursors(fake_plaid) == [None, "c1"]
    saved = conn.execute("SELECT cursor, last_synced FROM plaid_items").fetchone()
    assert saved["cursor"] == "c2"
    assert saved["last_synced"]


def test_every_page_is_read(conn, plaid, fake_plaid, item):
    fake_plaid.page(
        added=[txn("t1", "2026-03-14", PETSMART, 25.0)], next_cursor="p1", has_more=True
    )
    fake_plaid.page(added=[txn("t2", "2026-03-15", CAFE, 6.5)], next_cursor="p2")
    assert sync(conn, plaid).added == 2
    assert sent_cursors(fake_plaid) == [None, "p1"]
    assert saved_cursor(conn) == "p2"


def test_changes_during_paging_restart_from_the_first_page(
    conn, plaid, fake_plaid, item
):
    first = txn("t1", "2026-03-14", PETSMART, 25.0)
    fake_plaid.page(added=[first], next_cursor="p1", has_more=True)
    fake_plaid.fail(
        "/transactions/sync", "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
    )
    fake_plaid.page(added=[first], next_cursor="p1", has_more=True)
    fake_plaid.page(added=[txn("t2", "2026-03-15", CAFE, 6.5)], next_cursor="p2")

    assert sync(conn, plaid).added == 2
    # Plaid's rule: retry from the cursor the sync started with, not the failed page.
    assert sent_cursors(fake_plaid) == [None, "p1", None, "p1"]
    assert saved_cursor(conn) == "p2"


def test_paging_gives_up_if_the_bank_keeps_changing(conn, plaid, fake_plaid, item):
    for _ in range(3):
        fake_plaid.fail(
            "/transactions/sync", "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
        )
    with pytest.raises(PlaidError) as error:
        sync(conn, plaid)
    assert error.value.code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
    assert stored(conn) == {}
    assert saved_cursor(conn) is None


def test_a_failed_sync_changes_nothing(conn, plaid, fake_plaid, item):
    fake_plaid.page(added=[txn("t1", "2026-03-14", PETSMART, 25.0)], next_cursor="c1")
    sync(conn, plaid)
    fake_plaid.fail(
        "/transactions/sync",
        "ITEM_LOGIN_REQUIRED",
        message="the login details of this item have changed",
        display="Sign in to your bank again.",
    )

    with pytest.raises(PlaidError) as error:
        sync(conn, plaid)
    assert error.value.code == "ITEM_LOGIN_REQUIRED"
    assert str(error.value) == "Sign in to your bank again."  # the text for people
    assert set(stored(conn)) == {"t1"}
    assert saved_cursor(conn) == "c1"


def test_a_new_login_waits_for_plaid(conn, plaid, fake_plaid, item):
    fake_plaid.page(next_cursor="", status="NOT_READY")
    result = sync(conn, plaid)
    assert result.status == "NOT_READY"
    assert not saved_cursor(conn)

    # Next time it starts from the beginning again.
    fake_plaid.page(added=[txn("t1", "2026-03-14", PETSMART, 25.0)], next_cursor="c1")
    assert sync(conn, plaid).added == 1
    assert sent_cursors(fake_plaid) == [None, None]
