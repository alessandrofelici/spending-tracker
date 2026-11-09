"""The time range the dashboard shows (issue #27): one month, the last few months, a
year, all time, or a custom From/To month range. Whole months only, so averages
and comparisons stay simple. Pure functions here; the picker is in dashboard.py."""

from dataclasses import dataclass
from datetime import date

import pandas as pd


def shift(month: str, n: int) -> str:
    """'2026-03' shifted by n months, e.g. shift('2026-01', -1) == '2025-12'."""
    y, m = map(int, month.split("-"))
    total = y * 12 + (m - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_range(start: str, end: str) -> list[str]:
    """Every month from start to end, both included (empty if start > end)."""
    out, m = [], start
    while m <= end:
        out.append(m)
        m = shift(m, 1)
    return out


def this_month(today: date | None = None) -> str:
    return (today or date.today()).strftime("%Y-%m")


@dataclass(frozen=True)
class Period:
    label: str
    start: str  # first month, YYYY-MM
    end: str  # last month, YYYY-MM

    @property
    def months(self) -> list[str]:
        return month_range(self.start, self.end)

    @property
    def single(self) -> bool:
        return self.start == self.end

    def previous(self) -> "Period":
        """The period of the same length just before this one."""
        n = len(self.months)
        start, end = shift(self.start, -n), shift(self.start, -1)
        label = start if n == 1 else f"{start} to {end}"
        return Period(label, start, end)

    def rows(self, df: pd.DataFrame) -> pd.DataFrame:
        return df[(df.month >= self.start) & (df.month <= self.end)]


def presets(months: list[str]) -> dict[str, Period]:
    """Choices for the period picker, from the months that have transactions.
    'Last N months' end at the newest month with data; each year with data gets
    its own entry, newest first."""
    if not months:
        return {}
    first, last = min(months), max(months)
    out = {}
    for n in (3, 6, 12):
        start = max(shift(last, -(n - 1)), first)
        out[f"Last {n} months"] = Period(f"last {n} months", start, last)
    for year in sorted({m[:4] for m in months}, reverse=True):
        start, end = max(f"{year}-01", first), min(f"{year}-12", last)
        out[year] = Period(year, start, end)
    out["All time"] = Period("all time", first, last)
    return out


def custom(start: str, end: str) -> Period:
    start, end = min(start, end), max(start, end)
    return Period(start if start == end else f"{start} to {end}", start, end)


@dataclass(frozen=True)
class Summary:
    total: float
    transactions: int  # purchases (positive amounts)
    average_purchase: float | None
    per_month: pd.Series  # month -> spent, every month of the period (0 if none)
    complete_months: list[str]  # months that are over, used for the average
    monthly_average: float | None
    highest: tuple[str, float] | None  # among complete months
    lowest: tuple[str, float] | None
    categories: pd.DataFrame  # category: total, share, monthly_average


def summarize(df: pd.DataFrame, period: Period, today: date | None = None) -> Summary:
    """Stats for the period's rows (df already limited to it). The current,
    unfinished month counts in the total but not in averages or highs and lows;
    months with no spending count as $0."""
    current = this_month(today)
    per_month = df.groupby("month").amount.sum().reindex(period.months, fill_value=0.0)
    complete = [m for m in period.months if m < current]
    done = per_month[complete]
    purchases = df[df.amount > 0]
    total = float(df.amount.sum())
    by_cat = df.groupby("category").amount.sum().sort_values(ascending=False)
    done_by_cat = df[df.month.isin(complete)].groupby("category").amount.sum()
    categories = pd.DataFrame(
        {
            "total": by_cat,
            "share": by_cat / total if total else 0.0,
            "monthly_average": (done_by_cat / len(complete)).reindex(by_cat.index)
            if complete
            else float("nan"),
        }
    )
    return Summary(
        total=total,
        transactions=len(purchases),
        average_purchase=float(purchases.amount.mean()) if len(purchases) else None,
        per_month=per_month,
        complete_months=complete,
        monthly_average=float(done.mean()) if complete else None,
        highest=(done.idxmax(), float(done.max())) if complete else None,
        lowest=(done.idxmin(), float(done.min())) if complete else None,
        categories=categories,
    )
