"""Whether each bank login is working, as the Connected accounts page shows it:
what the login shares, which of those accounts the app uses, and what's wrong
when it isn't syncing. Plaid is faked (tests/fakes.py)."""

from datetime import UTC, datetime

import httpx
import pytest
from fakes import CARD, CHECKING, CREDIT_LINE, SAVINGS

from spending import connections, db
from spending.plaid_source import Plaid

NOW = datetime(2026, 3, 14, 12, 0, tzinfo=UTC)  # the fake's last pull: 9:40


@pytest.fixture
def plaid(fake_plaid) -> Plaid:
    return fake_plaid.client()


def check(conn, plaid, role="card", token="access-test", institution="Old Name"):
    db.add_plaid_item(conn, f"item-{role}", institution, role)
    conn.commit()
    [item] = db.get_plaid_items(conn, role)
    return connections.check(plaid, item, token, now=NOW)


def test_a_working_card_login(conn, plaid, fake_plaid):
    fake_plaid.accounts = [CARD, SAVINGS]
    c = check(conn, plaid)
    assert (c.state, c.label, c.message) == ("working", "Working", "")
    assert c.bank == "Example Credit Union"  # Plaid's name for the bank
    assert [(a.name, a.kind, a.mask, a.tracked) for a in c.accounts] == [
        ("Example Visa", "credit card", "1234", True),
        ("Example Savings", "savings", "3456", False),
    ]
    assert connections.ago(c.bank_success, NOW) == "2 hours ago"
    # Plaid gets the keys and this login's token, nothing else.
    for path in ("/item/get", "/accounts/get"):
        [body] = fake_plaid.bodies(path)
        assert body == {
            "client_id": "test-client-id",
            "secret": "test-secret",
            "access_token": "access-test",
        }


def test_a_credit_line_counts_as_the_card(conn, plaid, fake_plaid):
    fake_plaid.accounts = [CREDIT_LINE]
    assert check(conn, plaid).state == "working"


def test_a_card_login_that_shares_no_credit_card_tracks_nothing(
    conn, plaid, fake_plaid
):
    """A bank with only checking and savings as the card login: every sync said
    "nothing new" while no card transactions came in."""
    fake_plaid.accounts = [CHECKING, SAVINGS]
    c = check(conn, plaid)
    assert (c.state, c.label) == ("not_tracking", "Not tracking anything")
    assert "shares no credit card" in c.message
    assert not any(a.tracked for a in c.accounts)


def test_a_checking_login_that_shares_no_checking(conn, plaid, fake_plaid):
    fake_plaid.accounts = [CARD, SAVINGS]
    c = check(conn, plaid, role="reimbursements")
    assert c.state == "not_tracking"
    assert "shares no checking account" in c.message


def test_a_login_that_needs_a_sign_in(conn, plaid, fake_plaid):
    fake_plaid.item_error = {
        "error_code": "ITEM_LOGIN_REQUIRED",
        "error_message": "the login details of this item have changed",
        "display_message": None,
    }
    c = check(conn, plaid)
    assert (c.state, c.label) == ("sign_in_again", "Needs sign-in")
    assert "login details" in c.message
    assert fake_plaid.bodies("/accounts/get") == []  # Plaid wouldn't list them


def test_plaid_failing_to_reach_the_bank(conn, plaid, fake_plaid):
    fake_plaid.pulls = {
        "last_successful_update": "2026-03-12T08:00:00Z",
        "last_failed_update": "2026-03-14T08:00:00Z",
    }
    c = check(conn, plaid)
    assert c.state == "bank_failing"
    assert "failed (4 hours ago)" in c.message


def test_an_old_failure_is_fine_once_plaid_got_data_again(conn, plaid, fake_plaid):
    fake_plaid.pulls = {
        "last_successful_update": "2026-03-14T09:40:00Z",
        "last_failed_update": "2026-03-01T08:00:00Z",
    }
    assert check(conn, plaid).state == "working"


def test_a_new_login_plaid_is_still_getting(conn, plaid, fake_plaid):
    fake_plaid.pulls = {"last_successful_update": None, "last_failed_update": None}
    assert check(conn, plaid).state == "waiting"


def test_permission_that_ends_soon(conn, plaid, fake_plaid):
    fake_plaid.consent_ends = "2026-03-20T00:00:00Z"
    c = check(conn, plaid)
    assert (c.state, c.label) == ("expiring", "Sign in soon")
    assert "ends Mar 20, 2026" in c.message


def test_permission_that_ends_later_is_fine(conn, plaid, fake_plaid):
    fake_plaid.consent_ends = "2026-09-01T00:00:00Z"
    c = check(conn, plaid)
    assert c.state == "working"
    assert c.consent_ends == datetime(2026, 9, 1, tzinfo=UTC)


def test_a_login_plaid_no_longer_has(conn, plaid, fake_plaid):
    fake_plaid.fail(
        "/item/get", "ITEM_NOT_FOUND", "The Item you requested cannot be found."
    )
    c = check(conn, plaid)
    assert (c.state, c.label) == ("reconnect", "Connect again")
    assert c.bank == "Old Name"  # what was saved when it was connected


def test_a_missing_token_never_reaches_plaid(conn, plaid, fake_plaid):
    c = check(conn, plaid, token=None)
    assert c.state == "reconnect"
    assert fake_plaid.requests == []


def test_plaid_out_of_reach(conn):
    def down(request):
        raise httpx.ConnectError("connection refused")

    plaid = Plaid("id", "secret", "sandbox", httpx.MockTransport(down))
    c = check(conn, plaid)
    assert (c.state, c.label) == ("unknown", "Couldn't check")
    assert "Couldn't reach Plaid" in c.message


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (30, "just now"),
        (600, "10 minutes ago"),
        (7200, "2 hours ago"),
        (259200, "3 days ago"),
    ],
)
def test_ago(seconds, text):
    when = datetime.fromtimestamp(NOW.timestamp() - seconds, UTC)
    assert connections.ago(when, NOW) == text


def test_times_from_plaid_and_from_the_database():
    assert connections.parse_time("2026-03-14T09:40:03.024Z") == datetime(
        2026, 3, 14, 9, 40, 3, 24000, tzinfo=UTC
    )
    # The database stores UTC without saying so.
    assert connections.parse_time("2026-03-14 09:40:00") == datetime(
        2026, 3, 14, 9, 40, tzinfo=UTC
    )
    assert connections.parse_time(None) is None
