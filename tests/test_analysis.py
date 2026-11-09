"""How spending is changing (#16): each number checked against made-up rows."""

from datetime import date

import pandas as pd
import pytest

from spending import analysis

MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04"]
TODAY = date(2026, 4, 10)  # April isn't over


def frame(rows) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["date", "category", "merchant", "amount"]
    ).assign(month=lambda d: d.date.str[:7])


@pytest.fixture
def df():
    return frame(
        [
            ("2026-01-05", "Groceries", "NORTHWIND", 100.0),
            ("2026-02-05", "Groceries", "NORTHWIND", 120.0),
            ("2026-03-05", "Groceries", "NORTHWIND", 200.0),
            ("2026-03-06", "Groceries", "NORTHWIND", -20.0),  # refund
            ("2026-04-03", "Groceries", "NORTHWIND", 30.0),
            ("2026-01-09", "Dining", "NOODLE HOUSE", 40.0),
            ("2026-03-09", "Dining", "NOODLE HOUSE", 10.0),
            ("2026-04-08", "Dining", "NOODLE HOUSE", 25.0),
        ]
    )


def test_category_trend(df):
    t = analysis.category_trend(df, "Groceries", MONTHS[:3], today=TODAY)
    assert t.months.to_dict() == {"2026-01": 100.0, "2026-02": 120.0, "2026-03": 180.0}
    assert t.change_from_previous == 60.0
    assert t.average_of_others == 110.0
    assert t.change_from_average == 70.0


def test_category_trend_counts_empty_months_as_zero(df):
    t = analysis.category_trend(df, "Dining", MONTHS[:3], today=TODAY)
    assert t.months["2026-02"] == 0.0
    assert t.average_of_others == 20.0


def test_compare_two_months(df):
    table = analysis.compare(df, "2026-02", "2026-03")
    assert list(table.index) == ["Groceries", "Dining"]  # biggest change first
    assert table.loc["Groceries"].to_dict() == {
        "before": 120.0,
        "after": 180.0,
        "change": 60.0,
        "change_pct": 0.5,
    }
    assert pd.isna(table.loc["Dining", "change_pct"])  # nothing to compare with


def test_baselines_and_unusual_months(df):
    averages, unusual = analysis.baselines(df, MONTHS, today=TODAY)
    # April isn't over, so it's left out.
    assert averages.average.round(2).to_dict() == {"Groceries": 133.33, "Dining": 16.67}
    # Groceries in March (180) vs its other months (110): +64%, unusual.
    # Dining is too small to judge (it averages under $20 a month).
    assert unusual[["category", "month"]].values.tolist() == [["Groceries", "2026-03"]]
    assert unusual.iloc[0].difference == 70.0


def test_merchant_trend_counts_visits_not_refunds(df):
    t = analysis.merchant_trend(df, "NORTHWIND", MONTHS)
    assert t.loc["2026-03"].to_dict() == {"spent": 180.0, "visits": 1}
    assert t.visits.sum() == 4


def test_on_pace_compares_the_same_days(df):
    pace = analysis.on_pace(df, today=TODAY)
    assert (pace.month, pace.day, pace.so_far) == ("2026-04", 10, 55.0)
    # By day 10: Jan 100 + 40, Feb 120, Mar 200 - 20 + 10 -> (140 + 120 + 190) / 3.
    assert pace.past_months == ["2026-01", "2026-02", "2026-03"]
    assert pace.usual_by_now == 150.0


def test_on_pace_ignores_months_the_data_doesnt_cover(df):
    # Data ends in April; in June, May isn't a $0 month, it's missing.
    pace = analysis.on_pace(df, today=date(2026, 6, 10))
    assert pace.past_months == ["2026-01", "2026-02", "2026-03", "2026-04"]
