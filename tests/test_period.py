"""Choosing a time range (#27): presets, custom ranges, the period before, and the
stats for longer periods. Made-up data only."""

from datetime import date

import pandas as pd
import pytest

from spending.period import Period, custom, month_range, presets, shift, summarize


def test_months_shift_across_years():
    assert shift("2026-01", -1) == "2025-12"
    assert shift("2025-11", 3) == "2026-02"
    assert month_range("2025-11", "2026-02") == [
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    ]
    assert month_range("2026-02", "2026-01") == []


def test_presets_follow_the_data():
    months = ["2025-08", "2025-12", "2026-01", "2026-03"]
    p = presets(months)
    assert list(p) == [
        "Last 3 months",
        "Last 6 months",
        "Last 12 months",
        "2026",
        "2025",
        "All time",
    ]
    assert (p["Last 3 months"].start, p["Last 3 months"].end) == ("2026-01", "2026-03")
    # Never starts before the first month with data.
    assert p["Last 12 months"].start == "2025-08"
    assert (p["2025"].start, p["2025"].end) == ("2025-08", "2025-12")
    assert (p["2026"].start, p["2026"].end) == ("2026-01", "2026-03")
    assert (p["All time"].start, p["All time"].end) == ("2025-08", "2026-03")
    assert presets([]) == {}


def test_custom_range_in_either_order_and_the_period_before():
    p = custom("2026-03", "2026-01")
    assert (p.start, p.end, p.label) == ("2026-01", "2026-03", "2026-01 to 2026-03")
    prev = p.previous()
    assert (prev.start, prev.end) == ("2025-10", "2025-12")
    one = Period("2026-01", "2026-01", "2026-01")
    assert one.single and one.previous().label == "2025-12"


def frame(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["month", "category", "amount"])


def test_stats_leave_the_unfinished_month_out_of_averages():
    df = frame(
        [
            ("2026-01", "Groceries", 100.0),
            ("2026-01", "Dining", 50.0),
            ("2026-03", "Groceries", 60.0),
            ("2026-03", "Payments & Credits", -20.0),  # a refund
            ("2026-04", "Dining", 500.0),  # this month, not over yet
        ]
    )
    s = summarize(df, custom("2026-01", "2026-04"), today=date(2026, 4, 10))
    assert s.total == 690.0
    assert s.transactions == 4
    assert s.average_purchase == pytest.approx(177.5)
    assert s.complete_months == ["2026-01", "2026-02", "2026-03"]
    assert s.per_month.to_dict() == {
        "2026-01": 150.0,
        "2026-02": 0.0,  # no spending counts as $0
        "2026-03": 40.0,
        "2026-04": 500.0,
    }
    assert s.monthly_average == pytest.approx(190 / 3)
    assert s.highest == ("2026-01", 150.0)
    assert s.lowest == ("2026-02", 0.0)
    cats = s.categories
    assert list(cats.index[:2]) == ["Dining", "Groceries"]
    assert cats.loc["Dining", "share"] == pytest.approx(550 / 690)
    assert cats.loc["Dining", "monthly_average"] == pytest.approx(50 / 3)


def test_a_period_of_only_this_month_has_no_average():
    df = frame([("2026-04", "Dining", 20.0)])
    s = summarize(df, custom("2026-04", "2026-04"), today=date(2026, 4, 10))
    assert (s.total, s.monthly_average, s.highest) == (20.0, None, None)
