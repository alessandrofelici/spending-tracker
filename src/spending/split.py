"""Everyday spending vs travel and rare big purchases (issue #26).

A purchase is a one-off when it's in a travel category, or when your share of it
(after anything friends paid back) is at least ONE_OFF_MIN dollars. Everything
else is everyday spending, so the two always add up to the overall total.
"""

import os

import pandas as pd

ONE_OFF = "Travel & big one-offs"
EVERYDAY = "Everyday"
TRAVEL_CATEGORIES = {"Travel"}
DEFAULT_ONE_OFF_MIN = 200.0


def one_off_min() -> float:
    return float(os.environ.get("ONE_OFF_MIN", DEFAULT_ONE_OFF_MIN))


def is_one_off(df: pd.DataFrame, minimum: float | None = None) -> pd.Series:
    """True for each row that's travel or a big purchase. df has category and
    amount (your share)."""
    minimum = one_off_min() if minimum is None else minimum
    return df.category.isin(TRAVEL_CATEGORIES) | (df.amount >= minimum)


def kind(df: pd.DataFrame, minimum: float | None = None) -> pd.Series:
    return is_one_off(df, minimum).map({True: ONE_OFF, False: EVERYDAY})
