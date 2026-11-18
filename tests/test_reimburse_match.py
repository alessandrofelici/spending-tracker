"""Spotting purchases you were paid back for: only a Zelle payment into checking is
proposed, against a card purchase within a few days of it, never for more than is
left of it, never twice, and never again once you've said it's wrong.
Made-up data only."""

import pytest
from test_storage import row

from spending import db, reimburse


def incoming(
    tid: str, amount: float, day: str, description: str = "ZELLE FROM FRIEND"
) -> dict:
    """Money in to checking: negative, like a refund on the card."""
    return row(tid, description, -amount, day, "Incoming", "checking") | {
        "account": "checking"
    }


@pytest.fixture
def card(conn):
    db.insert_transactions(
        conn,
        [
            row("gas", "SPEEDWAY 1234 LANSING MI", 42.0, "2026-03-10"),
            row("dinner", "NOODLE HOUSE LANSING MI", 96.0, "2026-03-12"),
            row("flight", "EXAMPLE AIRLINE 0042", 318.4, "2026-03-02"),
            row("coffee", "BLUE HERON CAFE EAST LANSING MI", 4.5, "2026-03-12"),
        ],
    )
    return conn


def proposed(conn, **kwargs) -> list[tuple[str, str, float]]:
    return [
        (m.incoming_id, m.purchase_id, m.amount)
        for m in reimburse.propose(conn, **kwargs)
    ]


def test_splitting_gas(card):
    db.insert_transactions(card, [incoming("z", 21.0, "2026-03-11")])
    [m] = reimburse.propose(card)
    assert (m.purchase_id, m.share) == ("gas", 2)


def test_an_uneven_share_of_a_restaurant_bill(card):
    # Not an even share of anything: the purchase closest in date wins.
    db.insert_transactions(card, [incoming("z", 37.5, "2026-03-13")])
    assert proposed(card) == [("z", "dinner", 37.5)]


def test_paying_for_someone_elses_ticket(card):
    db.insert_transactions(card, [incoming("z", 318.4, "2026-03-05")])
    [m] = reimburse.propose(card)
    assert (m.purchase_id, m.share) == ("flight", 1)


def test_friends_shares_add_up_to_the_bill(card):
    db.insert_transactions(
        card,
        [
            incoming("a", 32.0, "2026-03-12"),
            incoming("b", 32.0, "2026-03-13"),
            incoming("c", 32.0, "2026-03-14"),
        ],
    )
    matches = reimburse.propose(card)
    assert [(m.incoming_id, m.purchase_id, m.share) for m in matches] == [
        ("a", "dinner", 3),
        ("b", "dinner", 3),
        ("c", "dinner", 3),
    ]
    assert [m.left for m in matches] == [96.0, 64.0, 32.0]


@pytest.mark.parametrize(
    "description",
    ["VENMO CASHOUT", "PAYROLL EXAMPLE CO", "MOBILE DEPOSIT", "TRANSFER FROM SAVINGS"],
)
def test_only_zelle_payments_are_considered(card, description):
    db.insert_transactions(card, [incoming("x", 21.0, "2026-03-11", description)])
    assert proposed(card) == []


@pytest.mark.parametrize(
    "description",
    ["ZELLE FROM JANE DOE", "Zelle payment from Jane Doe", "ONLINE ZELLE CR 1234"],
)
def test_zelle_is_recognized_however_the_bank_writes_it(card, description):
    db.insert_transactions(card, [incoming("x", 21.0, "2026-03-11", description)])
    assert proposed(card) == [("x", "gas", 21.0)]


def test_within_a_few_days_either_way(card):
    db.insert_transactions(
        card,
        [
            # The card purchase can post after the friend already paid.
            incoming("before", 21.0, "2026-03-04"),  # 6 days before the gas
            incoming("late", 21.0, "2026-03-20"),  # 8 days after the last purchase
        ],
    )
    assert proposed(card) == [("before", "gas", 21.0)]
    assert [i for i, _, _ in proposed(card, window=10)] == ["before", "late"]


def test_never_more_than_is_left_and_the_minimum_purchase(card):
    db.add_reimbursement(card, "dinner", 80)
    db.insert_transactions(
        card, [incoming("big", 50.0, "2026-03-12"), incoming("tiny", 4.5, "2026-03-12")]
    )
    # 50 fits neither the gas (42) nor the 16 left of the dinner; 4.50 is the
    # coffee's full price.
    assert proposed(card) == [("tiny", "coffee", 4.5)]
    assert proposed(card, minimum=10) == [("tiny", "dinner", 4.5)]


def test_confirmed_and_dismissed_matches_are_not_proposed_again(card):
    db.insert_transactions(
        card,
        [
            incoming("a", 21.0, "2026-03-11"),
            incoming("b", 30.0, "2026-03-06"),  # fits the flight and the dinner
            incoming("c", 40.0, "2026-03-11"),
        ],
    )
    [first] = [m for m in reimburse.propose(card) if m.incoming_id == "a"]
    reimburse.confirm(card, first, "gas split")
    assert db.paid_back(card, "gas") == 21.0
    assert "a" not in [i for i, _, _ in proposed(card)]

    reimburse.not_a_reimbursement(card, "c")  # e.g. a friend repaying a loan
    assert "c" not in [i for i, _, _ in proposed(card)]

    # An older "not this purchase": the next best one is proposed instead.
    [(_, purchase, _)] = [p for p in proposed(card) if p[0] == "b"]
    db.dismiss_match(card, purchase, "b")
    [(_, other, _)] = [p for p in proposed(card) if p[0] == "b"]
    assert other != purchase

    # Both kinds of "no" are listed, and can be taken back.
    assert [
        (h["id"], h["not_a_payback"], h["turned_down"]) for h in reimburse.hidden(card)
    ] == [("c", 1, 0), ("b", 0, 1)]
    reimburse.show_again(card, "c")
    reimburse.show_again(card, "b")
    assert reimburse.hidden(card) == []
    assert {i for i, _, _ in proposed(card)} == {"b", "c"}
    assert [p for p in proposed(card) if p[0] == "b"] == [("b", purchase, 30.0)]


def test_every_purchase_a_payment_fits_is_offered_best_first(card):
    db.insert_transactions(card, [incoming("b", 30.0, "2026-03-06")])
    [m] = reimburse.propose(card)
    # Not an even share of any: the closest in date first (the flight and the
    # gas are 4 days off, the earlier first), then the dinner (6 days off).
    assert m.candidates == ("flight", "gas", "dinner")
    assert m.purchase_id == m.candidates[0]


def test_purchases_left_to_pay_back_and_unused_payments(card):
    db.insert_transactions(
        card,
        [
            row("feb", "PETSMART 1234 LANSING MI", 25.0, "2026-02-03"),
            incoming("used", 4.5, "2026-03-12"),
            incoming("free", 21.0, "2026-03-11"),
            incoming("pay", 900.0, "2026-03-01", "PAYROLL EXAMPLE CO"),
        ],
    )
    db.add_reimbursement(card, "coffee", 4.5, incoming_id="used")
    db.add_reimbursement(card, "dinner", 50.0)
    open_ = {p["id"]: p["left"] for p in reimburse.open_purchases(card)}
    # Any month, newest first; the coffee is paid back in full.
    assert list(open_) == ["dinner", "gas", "flight", "feb"]
    assert open_["dinner"] == 46.0
    assert [p["id"] for p in reimburse.payments(card)] == ["free"]


@pytest.mark.parametrize(
    ("description", "name"),
    [
        ("Zelle JORDAN EXAMPLE 1234567890 ZELLE CREDIT", "Jordan Example"),
        ("ZELLE FROM JANE DOE", "Jane Doe"),
        ("Zelle payment from Jane Doe", "Jane Doe"),
        ("ZELLE PAYMENT FROM ALEX SAMPLE ON 03/03 REF #AB12", "Alex Sample"),
        ("Zelle from Sam Q. O'Example; memo dinner", "Sam Q. O'Example"),
        ("ONLINE ZELLE CR 1234", None),
        ("ZELLE 1234567890", None),
    ],
)
def test_who_sent_a_payment(description, name):
    assert reimburse.sender(description) == name


def test_accepting_every_proposal_never_pays_back_too_much(card):
    db.insert_transactions(
        card, [incoming(f"t{n}", 30.0, f"2026-03-1{n}") for n in range(0, 8)]
    )
    for m in reimburse.propose(card):
        reimburse.confirm(card, m)
    for r in card.execute(db.SPENDING_SQL):
        assert r["paid_back"] <= r["amount"]


def test_settings_come_from_env(monkeypatch):
    assert (reimburse.window_days(), reimburse.min_purchase()) == (7, 0.0)
    monkeypatch.setenv("REIMBURSE_WINDOW_DAYS", "3")
    monkeypatch.setenv("REIMBURSE_MIN_PURCHASE", "20")
    assert (reimburse.window_days(), reimburse.min_purchase()) == (3, 20.0)
