"""How spending is changing (issue #16). Pure pandas, no Streamlit, so every number
can be tested and explained; analysis_tab.py shows them.

Every function takes the dashboard's rows: one per card transaction, with month,
date, category, merchant and amount (your share, after anything paid back).
How the edge cases are handled, and said so on screen:
  * refunds count in the month they posted, netted against purchases, and are
    not visits;
  * months with no spending count as $0 in averages;
  * the current, unfinished month is left out of averages and baselines.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from spending.period import month_range, shift, this_month

# A month is "well above" or "below" its category's usual when it's this far
# from the average of the category's other complete months...
UNUSUAL = 0.5
# ...unless the category averages less than this a month (tiny ones swing wildly).
SMALL_CATEGORY = 20.0


def per_month(rows: pd.DataFrame, months: list[str]) -> pd.Series:
    """Spent per month, $0 for months without any."""
    return rows.groupby("month").amount.sum().reindex(months, fill_value=0.0)


@dataclass(frozen=True)
class Trend:
    months: pd.Series  # month -> spent
    latest: str
    change_from_previous: float | None
    average_of_others: float | None  # complete months other than `latest`
    change_from_average: float | None


def category_trend(
    df: pd.DataFrame, category: str, months: list[str], today: date | None = None
) -> Trend:
    """One category month by month, and how its latest month compares with the
    month before and with the average of the other complete months."""
    series = per_month(df[df.category == category], months)
    latest = months[-1]
    current = this_month(today)
    others = [m for m in months[:-1] if m < current]
    prev = series.iloc[-2] if len(series) > 1 else None
    avg = float(series[others].mean()) if others else None
    return Trend(
        months=series,
        latest=latest,
        change_from_previous=float(series.iloc[-1] - prev)
        if prev is not None
        else None,
        average_of_others=avg,
        change_from_average=float(series.iloc[-1] - avg) if avg is not None else None,
    )


def compare(df: pd.DataFrame, before: str, after: str) -> pd.DataFrame:
    """Each category's spending in two months, biggest change first."""
    by = (
        df[df.month.isin([before, after])]
        .pivot_table(index="category", columns="month", values="amount", aggfunc="sum")
        .reindex(columns=[before, after])
        .fillna(0.0)
    )
    out = pd.DataFrame({"before": by[before], "after": by[after]})
    out["change"] = out.after - out.before
    out["change_pct"] = (out.change / out.before).where(out.before > 0)
    return out.reindex(out.change.abs().sort_values(ascending=False).index)


def baselines(
    df: pd.DataFrame, months: list[str], today: date | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(averages, unusual): each category's average month over the complete
    months, and the category-months that are well above or below the average of
    that category's other complete months."""
    current = this_month(today)
    complete = [m for m in months if m < current]
    if not complete:
        return pd.DataFrame(columns=["average"]), pd.DataFrame(
            columns=["category", "month", "spent", "usual", "difference"]
        )
    grid = (
        df[df.month.isin(complete)]
        .pivot_table(index="category", columns="month", values="amount", aggfunc="sum")
        .reindex(columns=complete)
        .fillna(0.0)
    )
    averages = pd.DataFrame({"average": grid.mean(axis=1)}).sort_values(
        "average", ascending=False
    )
    unusual = []
    if len(complete) > 1:
        for category, row in grid.iterrows():
            if row.mean() < SMALL_CATEGORY:
                continue
            for month in complete:
                usual = row.drop(month).mean()
                spent = row[month]
                if abs(spent - usual) > UNUSUAL * usual:
                    unusual.append((category, month, spent, usual, spent - usual))
    return averages, pd.DataFrame(
        unusual, columns=["category", "month", "spent", "usual", "difference"]
    )


def merchant_trend(df: pd.DataFrame, merchant: str, months: list[str]) -> pd.DataFrame:
    """Spent and visits (purchases, not refunds) per month at one merchant."""
    rows = df[df.merchant == merchant]
    return pd.DataFrame(
        {
            "spent": per_month(rows, months),
            "visits": rows[rows.amount > 0]
            .groupby("month")
            .size()
            .reindex(months, fill_value=0),
        }
    )


@dataclass(frozen=True)
class Pace:
    month: str
    day: int
    so_far: float
    usual_by_now: float | None  # average spent by this day in past complete months
    past_months: list[str]


def on_pace(df: pd.DataFrame, today: date | None = None, lookback: int = 6) -> Pace:
    """Spending so far this month against what the last `lookback` complete
    months had spent by the same day of the month."""
    today = today or date.today()
    month = this_month(today)
    day = today.day
    by_day = df.assign(day=pd.to_datetime(df.date).dt.day)
    so_far = float(by_day[(by_day.month == month) & (by_day.day <= day)].amount.sum())
    # Only months the data covers: a month before the first import, or after the
    # last sync, isn't a $0 month, it's just missing.
    past = [
        m
        for m in month_range(shift(month, -lookback), shift(month, -1))
        if len(df) and df.month.min() <= m <= df.month.max()
    ]
    if not past:
        return Pace(month, day, so_far, None, [])
    early = by_day[by_day.month.isin(past) & (by_day.day <= day)]
    usual = float(per_month(early, past).mean())
    return Pace(month, day, so_far, usual, past)
