"""Signing in to your bank and disconnecting it, and how the Plaid client talks to
Plaid. Plaid is faked (tests/fakes.py), so nothing here reaches a real bank."""

import httpx
import pytest

from spending import db, plaid_source
from spending.plaid_source import Plaid, PlaidError

INSTITUTION = "Example Credit Union"


@pytest.fixture
def plaid(fake_plaid) -> Plaid:
    return fake_plaid.client()


def item_ids(conn) -> list[str]:
    return [i["item_id"] for i in db.get_plaid_items(conn)]


def old_login(conn, token: str | None = "access-sandbox-old") -> None:
    db.add_plaid_item(conn, "item-old", "Old Bank")
    if token:
        plaid_source.save_token("item-old", token)
    conn.commit()


# --- Signing in ---------------------------------------------------------------


def test_signing_in_stores_the_login(conn, plaid, fake_plaid):
    where, problems = plaid_source.link(conn, plaid, "public-sandbox-test", INSTITUTION)

    assert (where, problems) == ("the OS keyring", [])
    [item] = db.get_plaid_items(conn)
    assert (item["item_id"], item["institution"]) == ("item-1", INSTITUTION)
    assert plaid_source.load_token("item-1") == "access-sandbox-login-1"
    [body] = fake_plaid.bodies("/item/public_token/exchange")
    assert body["public_token"] == "public-sandbox-test"


def test_a_new_login_starts_after_what_you_already_have(conn, plaid):
    db.insert_transactions(
        conn,
        [
            {
                "id": f"csv-{day}",
                "month": "2026-03",
                "date": day,
                "description": "PETSMART 1234 LANSING MI",
                "merchant": "PETSMART LANSING",
                "amount": 25.0,
                "category": "Shopping",
                "source": "memory",
                "statement": "export.csv",
                "source_kind": "csv",
            }
            for day in ("2026-03-01", "2026-03-10")
        ],
    )
    plaid_source.link(conn, plaid, "public-sandbox-test", INSTITUTION)
    assert db.get_plaid_items(conn)[0]["cutover"] == "2026-03-10"


def test_signing_in_again_revokes_the_earlier_login(conn, plaid, fake_plaid):
    plaid_source.link(conn, plaid, "public-sandbox-1", INSTITUTION)
    _, problems = plaid_source.link(conn, plaid, "public-sandbox-2", INSTITUTION)

    assert problems == []
    assert item_ids(conn) == ["item-2"]
    assert fake_plaid.removed == ["access-sandbox-login-1"]
    assert plaid_source.load_token("item-1") is None


def test_a_login_without_its_token_is_forgotten_with_a_warning(conn, plaid, fake_plaid):
    old_login(conn, token=None)
    _, problems = plaid_source.link(conn, plaid, "public-sandbox-test", INSTITUTION)

    assert len(problems) == 1
    assert "Old Bank" in problems[0]
    assert item_ids(conn) == ["item-1"]
    assert fake_plaid.removed == []  # nothing to send


def test_a_login_plaid_cant_revoke_is_kept_to_retry(conn, plaid, fake_plaid):
    old_login(conn)
    fake_plaid.fail("/item/remove", "INTERNAL_SERVER_ERROR", status=500)
    _, problems = plaid_source.link(conn, plaid, "public-sandbox-test", INSTITUTION)

    assert len(problems) == 1
    assert "Old Bank" in problems[0]
    assert item_ids(conn) == ["item-1", "item-old"]
    assert plaid_source.load_token("item-old") == "access-sandbox-old"


# --- Disconnecting ------------------------------------------------------------


def test_unlink_revokes_and_forgets_but_keeps_transactions(conn, plaid, fake_plaid):
    old_login(conn)
    db.insert_transactions(
        conn,
        [
            {
                "id": "t1",
                "month": "2026-03",
                "date": "2026-03-14",
                "description": "PETSMART 1234 LANSING MI",
                "merchant": "PETSMART LANSING",
                "amount": 25.0,
                "category": "Shopping",
                "source": "memory",
                "statement": "Plaid",
                "source_kind": "plaid",
            }
        ],
    )

    assert plaid_source.unlink(conn, plaid, "item-old") is True
    assert fake_plaid.removed == ["access-sandbox-old"]
    assert item_ids(conn) == []
    assert plaid_source.load_token("item-old") is None
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


@pytest.mark.parametrize("code", sorted(plaid_source.LOGIN_GONE))
def test_unlink_a_login_plaid_already_forgot(conn, plaid, fake_plaid, code):
    old_login(conn)
    fake_plaid.fail("/item/remove", code)
    assert plaid_source.unlink(conn, plaid, "item-old") is True
    assert item_ids(conn) == []
    assert plaid_source.load_token("item-old") is None


def test_unlink_keeps_everything_when_plaid_fails(conn, plaid, fake_plaid):
    old_login(conn)
    fake_plaid.fail("/item/remove", "RATE_LIMIT_EXCEEDED", status=429)
    with pytest.raises(PlaidError):
        plaid_source.unlink(conn, plaid, "item-old")
    assert item_ids(conn) == ["item-old"]
    assert plaid_source.load_token("item-old") == "access-sandbox-old"


# --- The Plaid client ---------------------------------------------------------


def test_sign_in_for_a_new_login_asks_only_for_transactions(plaid, fake_plaid):
    assert plaid.link_token() == "link-sandbox-test"
    [body] = fake_plaid.bodies("/link/token/create")
    assert body["products"] == ["transactions"]
    assert body["transactions"] == {"days_requested": plaid_source.DAYS_REQUESTED}
    assert body["user"] == {"client_user_id": "local-user"}  # nothing personal
    assert "access_token" not in body


def test_signing_in_again_reuses_the_same_login(plaid, fake_plaid):
    plaid.link_token(access_token="access-sandbox-old")
    [body] = fake_plaid.bodies("/link/token/create")
    assert body["access_token"] == "access-sandbox-old"
    assert "products" not in body


@pytest.mark.parametrize(
    ("env", "host"),
    [("sandbox", "sandbox.plaid.com"), ("production", "production.plaid.com")],
)
def test_requests_go_to_the_chosen_plaid_environment(env, host):
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json={"link_token": "link-test"})

    Plaid("id", "secret", env, httpx.MockTransport(handler)).link_token()
    assert hosts == [host]


def test_an_unknown_plaid_environment_is_refused():
    with pytest.raises(ValueError, match="PLAID_ENV"):
        Plaid("id", "secret", "development")


def test_errors_carry_plaids_message_for_people(plaid, fake_plaid):
    fake_plaid.fail(
        "/link/token/create",
        "PRODUCTS_NOT_SUPPORTED",
        "internal detail",
        display="Try later.",
    )
    with pytest.raises(PlaidError) as error:
        plaid.link_token()
    assert (error.value.code, str(error.value)) == (
        "PRODUCTS_NOT_SUPPORTED",
        "Try later.",
    )

    fake_plaid.fail("/link/token/create", "INVALID_FIELD", "client_user_id is empty")
    with pytest.raises(PlaidError) as error:
        plaid.link_token()
    assert str(error.value) == "client_user_id is empty"


def test_a_non_json_error_is_an_http_error():
    plaid = Plaid(
        "id", "secret", "sandbox", httpx.MockTransport(lambda _: httpx.Response(502))
    )
    with pytest.raises(httpx.HTTPStatusError):
        plaid.link_token()
