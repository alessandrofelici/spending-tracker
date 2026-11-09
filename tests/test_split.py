"""Everyday spending vs travel and rare big purchases (#26): the two always add up
to the overall total. Made-up data only."""

import pandas as pd

from spending.split import EVERYDAY, ONE_OFF, kind, one_off_min


def test_travel_and_big_purchases_are_one_offs():
    df = pd.DataFrame(
        {
            "category": ["Travel", "Shopping", "Shopping", "Dining", "Groceries"],
            "amount": [45.0, 199.99, 200.0, 80.0, -30.0],
        }
    )
    assert list(kind(df, minimum=200)) == [
        ONE_OFF,  # travel, however small
        EVERYDAY,
        ONE_OFF,  # at the threshold
        EVERYDAY,  # e.g. your share of a bigger bill friends paid back
        EVERYDAY,  # a refund
    ]


def test_threshold_comes_from_env(monkeypatch):
    assert one_off_min() == 200.0
    monkeypatch.setenv("ONE_OFF_MIN", "500")
    assert one_off_min() == 500.0
