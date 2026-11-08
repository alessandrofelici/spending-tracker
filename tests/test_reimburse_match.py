"""Spotting purchases you were paid back for: a transfer into checking is proposed
against a recent card purchase, never for more than is left of it, never twice,
and never again once you've said it's wrong. Made-up data only."""

import pytest
from test_reimbursements import checking
from test_storage import row

from spending import db, reimburse


@pytest.fixture
def concert(conn):
    db.insert_transactions(
        conn,
        [
            row("concert", "TICKETS 1234 LANSING MI", 240.0, "2026-03-10"),
            row("coffee", "BLUE HERON CAFE EAST LANSING MI", 4.5, "2026-03-11"),
            row("dinner", "NOODLE HOUSE LANSING MI", 90.0, "2026-03-12"),
        ],
    )
    return conn


def proposed(conn, **kwargs) -> list[tuple[str, str, float]]:
    return [
        (m.incoming_id, m.purchase_id, m.amount)
        for m in reimburse.propose(conn, **kwargs)
    ]


def test_friends_shares_are_proposed_against_the_purchase(concert):
    db.insert_transactions(
        concert,
        [
            checking("a", 80.0, "2026-03-11"),
            checking("b", 80.0, "2026-03-15"),
            checking("c", 80.0, "2026-03-20"),
        ],
    )
    matches = reimburse.propose(concert)
    assert [(m.incoming_id, m.purchase_id, m.share) for m in matches] == [
        ("a", "concert", 3),
        ("b", "concert", 3),
        ("c", "concert", 3),
    ]
    assert [m.left for m in matches] == [240.0, 160.0, 80.0]


def test_a_transfer_only_matches_purchases_in_the_window_before_it(concert):
    db.insert_transactions(
        concert,
        [
            checking("early", 50.0, "2026-03-09"),  # before every purchase
            checking("late", 50.0, "2026-04-12"),  # 31 days after the dinner
            checking("ok", 50.0, "2026-04-11"),  # 30 days after the dinner
        ],
    )
    assert proposed(concert) == [("ok", "dinner", 50.0)]
    # With a longer window, both are proposed (the older one first).
    assert [i for i, _, _ in proposed(concert, window=60)] == ["ok", "late"]


def test_never_more_than_is_left_and_small_purchases_are_ignored(concert):
    db.add_reimbursement(concert, "concert", 200)
    db.insert_transactions(
        concert,
        [checking("big", 300.0), checking("mid", 60.0), checking("tiny", 4.5)],
    )
    # 300 fits nothing; 60 fits the dinner, not the 40 left of the concert;
    # the coffee is below the minimum purchase, so 4.50 goes to what the dinner
    # has left after the 60.
    assert proposed(concert) == [("mid", "dinner", 60.0), ("tiny", "dinner", 4.5)]
    # Without a minimum, the coffee's full price is the better match.
    assert proposed(concert, minimum=0)[1] == ("tiny", "coffee", 4.5)


def test_an_even_share_wins_over_the_most_recent_purchase(concert):
    db.insert_transactions(concert, [checking("half", 120.0, "2026-03-13")])
    [m] = reimburse.propose(concert)
    assert (m.purchase_id, m.share) == ("concert", 2)


def test_confirmed_and_dismissed_matches_are_not_proposed_again(concert):
    db.insert_transactions(
        concert,
        [checking("a", 80.0), checking("pay", 90.0), checking("b", 45.0)],
    )
    first = reimburse.propose(concert)[0]
    reimburse.confirm(concert, first, "split 3 ways")
    assert db.paid_back(concert, "concert") == 80.0
    assert "a" not in [i for i, _, _ in proposed(concert)]

    reimburse.not_a_reimbursement(concert, "pay")  # e.g. a paycheck
    assert "pay" not in [i for i, _, _ in proposed(concert)]

    # "Not this purchase": the next best one is proposed instead.
    [(_, purchase, _)] = [p for p in proposed(concert) if p[0] == "b"]
    db.dismiss_match(concert, purchase, "b")
    [(_, other, _)] = [p for p in proposed(concert) if p[0] == "b"]
    assert other != purchase


def test_accepting_every_proposal_never_pays_back_too_much(concert):
    db.insert_transactions(
        concert, [checking(f"t{n}", 70.0, f"2026-03-1{n}") for n in range(3, 8)]
    )
    for m in reimburse.propose(concert):
        reimburse.confirm(concert, m)
    for r in concert.execute(db.SPENDING_SQL):
        assert r["paid_back"] <= r["amount"]


def test_settings_come_from_env(monkeypatch):
    monkeypatch.setenv("REIMBURSE_WINDOW_DAYS", "7")
    monkeypatch.setenv("REIMBURSE_MIN_PURCHASE", "100")
    assert (reimburse.window_days(), reimburse.min_purchase()) == (7, 100.0)
