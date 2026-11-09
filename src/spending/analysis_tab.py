"""The dashboard's Analysis view (issue #16): how spending is changing. The numbers
come from analysis.py; each one has a "How it's computed" note with the formula,
the numbers and the rows behind it, so it can be checked."""

import pandas as pd
import plotly.express as px
import streamlit as st

from spending import analysis
from spending.period import Period, month_range, shift

MONEY = st.column_config.NumberColumn(format="$%.2f")


def render(df: pd.DataFrame, period: Period) -> None:
    """df: every card row, as the dashboard loaded it. A one-month period looks
    at the 12 months up to it; a longer one at its own months."""
    start = shift(period.end, -11) if period.single else period.start
    months = month_range(max(start, df.month.min()), period.end)
    rows = df[df.month.isin(months)]
    st.caption(
        f"Looking at {months[0]} to {months[-1]}. Refunds count in the month they"
        " posted; months without spending count as $0; the month that isn't over"
        " yet is left out of averages."
    )
    _on_pace(df)
    _category_trend(rows, months)
    _compare(rows, months)
    _baselines(rows, months)
    _merchant_trend(rows, months)


def _money(x: float | None) -> str:
    return "-" if x is None else f"${x:,.2f}"


def _on_pace(df: pd.DataFrame) -> None:
    pace = analysis.on_pace(df)
    if pace.usual_by_now is None or pace.month not in set(df.month):
        return  # nothing this month yet, or nothing to compare with
    st.subheader("This month so far")
    delta = pace.so_far - pace.usual_by_now
    st.metric(
        f"Spent by day {pace.day}",
        _money(pace.so_far),
        f"{delta:+,.2f} vs usual by now",
        delta_color="inverse",
    )
    with st.expander("How it's computed"):
        st.write(
            f"Spent from the 1st to day {pace.day} of {pace.month}, compared with"
            f" the average of what {', '.join(pace.past_months)} had spent by"
            f" day {pace.day}: {_money(pace.usual_by_now)}."
        )


def _category_trend(rows: pd.DataFrame, months: list[str]) -> None:
    st.subheader("One category over time")
    categories = rows.groupby("category").amount.sum().sort_values(ascending=False)
    if categories.empty:
        st.caption("Nothing in this period.")
        return
    category = st.selectbox("Category", categories.index.tolist(), key="trend_cat")
    trend = analysis.category_trend(rows, category, months)
    fig = px.bar(
        trend.months.rename_axis("month").rename("spent").reset_index(),
        x="month",
        y="spent",
        labels={"spent": "Spent ($)", "month": ""},
    )
    fig.update_layout(height=300, margin={"l": 0, "r": 10, "t": 10, "b": 0})
    fig.update_xaxes(type="category")
    st.plotly_chart(fig, width="stretch", key="trend_chart")
    c1, c2 = st.columns(2)
    c1.metric(
        f"{trend.latest} vs the month before",
        _money(trend.months.iloc[-1]),
        None
        if trend.change_from_previous is None
        else f"{trend.change_from_previous:+,.2f}",
        delta_color="inverse",
    )
    c2.metric(
        "vs its average",
        _money(trend.average_of_others),
        None
        if trend.change_from_average is None
        else f"{trend.change_from_average:+,.2f}",
        delta_color="inverse",
    )
    with st.expander("How it's computed"):
        st.write(
            f"Each bar is the sum of {category} in that month. The average is"
            " over the complete months before"
            f" {trend.latest}: {_money(trend.average_of_others)}."
        )
        st.dataframe(trend.months.rename("spent"), column_config={"spent": MONEY})


def _compare(rows: pd.DataFrame, months: list[str]) -> None:
    st.subheader("What changed between two months")
    if len(months) < 2:
        st.caption("Needs at least two months.")
        return
    c1, c2 = st.columns(2)
    newest_first = months[::-1]
    after = c2.selectbox("Month", newest_first, key="cmp_after")
    before = c1.selectbox("Compared with", newest_first, index=1, key="cmp_before")
    table = analysis.compare(rows, before, after)
    st.dataframe(
        table,
        column_config={
            "before": st.column_config.NumberColumn(before, format="$%.2f"),
            "after": st.column_config.NumberColumn(after, format="$%.2f"),
            "change": st.column_config.NumberColumn(format="$%+.2f"),
            "change_pct": st.column_config.NumberColumn("change %", format="percent"),
        },
        width="stretch",
    )
    with st.expander("How it's computed"):
        st.write(
            f"Each category's total in {before} and in {after}; change = {after}"
            f" minus {before}, biggest change first. The percentage is left empty"
            f" when there was nothing in {before}."
        )


def _baselines(rows: pd.DataFrame, months: list[str]) -> None:
    st.subheader("Usual months, and unusual ones")
    averages, unusual = analysis.baselines(rows, months)
    if averages.empty:
        st.caption("Needs at least one month that's over.")
        return
    left, right = st.columns(2)
    left.dataframe(averages, column_config={"average": MONEY}, width="stretch")
    if unusual.empty:
        right.caption("No month stands out.")
    else:
        right.dataframe(
            unusual,
            column_config={"spent": MONEY, "usual": MONEY, "difference": MONEY},
            hide_index=True,
            width="stretch",
        )
    with st.expander("How it's computed"):
        st.write(
            "Average = a category's total over the complete months divided by"
            " their number. A month is listed when it's more than"
            f" {analysis.UNUSUAL:.0%} above or below the average of that"
            " category's other complete months. Categories averaging under"
            f" ${analysis.SMALL_CATEGORY:,.0f} a month are skipped."
        )


def _merchant_trend(rows: pd.DataFrame, months: list[str]) -> None:
    st.subheader("One store over time")
    stores = rows.groupby("merchant").amount.sum().sort_values(ascending=False)
    if stores.empty:
        return
    merchant = st.selectbox("Store", stores.index.tolist(), key="trend_store")
    table = analysis.merchant_trend(rows, merchant, months)
    st.dataframe(table, column_config={"spent": MONEY}, width="stretch")
    with st.expander("How it's computed"):
        st.write(
            "Spent = the store's purchases minus its refunds in each month."
            " Visits = purchases only. One chain can show up as several stores,"
            " since the town stays in the store's name."
        )
