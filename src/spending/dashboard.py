"""Streamlit dashboard: `uv run spend dashboard`."""

import pandas as pd
import plotly.express as px
import streamlit as st

from spending.db import DB_PATH, connect
from spending.jev_tab import render as render_jev_tab

# Validated colorblind-safe categorical order (fixed, never cycled).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]
REST = "Everything else"
REST_COLOR = "#898781"
EXCLUDED = {"Payments & Credits"}  # not spending

st.set_page_config(page_title="Spending", layout="wide")


@st.cache_data(ttl=30)
def load() -> pd.DataFrame:
    with connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT * FROM transactions", conn, parse_dates=["date"])
    return df[~df.category.isin(EXCLUDED)]


df = load()
if df.empty:
    st.title("Spending")
    st.info(
        "No transactions yet. Import a statement with `uv run spend import statements/<file>.csv`."
    )
    st.stop()

# Top 7 categories by all-time spend keep their own color; color follows the
# category, so it never changes when you switch months.
top = (
    df.groupby("category")
    .amount.sum()
    .sort_values(ascending=False)
    .index[: len(PALETTE)]
    .tolist()
)
df["group"] = df.category.where(df.category.isin(top), REST)
colors = {c: PALETTE[i] for i, c in enumerate(top)} | {REST: REST_COLOR}
order = top + [REST]

st.title("Spending")
spending_tab, jev_tab = st.tabs(["Spending", "Categorization"])

with jev_tab:
    render_jev_tab()

with spending_tab:
    months = sorted(df.month.unique(), reverse=True)
    month = st.selectbox("Month", months)
    cur = df[df.month == month]
    prev_month = (
        months[months.index(month) + 1]
        if months.index(month) + 1 < len(months)
        else None
    )

    total = cur.amount.sum()
    c1, c2, c3 = st.columns(3)
    delta = None
    if prev_month:
        prev_total = df[df.month == prev_month].amount.sum()
        delta = f"{total - prev_total:+,.2f} vs {prev_month}"
    c1.metric("Total spent", f"${total:,.2f}", delta, delta_color="inverse")
    c2.metric("Transactions", f"{(cur.amount > 0).sum()}")
    by_cat = cur.groupby("category").amount.sum().sort_values()
    c3.metric(
        "Biggest category",
        by_cat.index[-1] if len(by_cat) else "-",
        f"${by_cat.iloc[-1]:,.2f}" if len(by_cat) else None,
        delta_color="off",
    )

    left, right = st.columns(2)

    with left:
        st.subheader(f"By category, {month}")
        fig = px.bar(
            by_cat.reset_index(),
            x="amount",
            y="category",
            orientation="h",
            labels={"amount": "Spent ($)", "category": ""},
            text_auto="$,.0f",
        )
        fig.update_traces(
            marker_color=PALETTE[0], hovertemplate="%{y}: $%{x:,.2f}<extra></extra>"
        )
        fig.update_layout(
            height=420, margin={"l": 0, "r": 10, "t": 10, "b": 0}, bargap=0.35
        )
        st.plotly_chart(fig, width="stretch")

    with right:
        st.subheader("Month by month")
        monthly = df.groupby(["month", "group"]).amount.sum().reset_index()
        fig = px.bar(
            monthly,
            x="month",
            y="amount",
            color="group",
            # Plotly orders category axes by first appearance across traces, so a
            # month missing from the first group's trace would end up out of order.
            category_orders={"group": order, "month": sorted(monthly.month.unique())},
            color_discrete_map=colors,
            labels={"amount": "Spent ($)", "month": "", "group": "Category"},
        )
        fig.update_traces(hovertemplate="%{fullData.name}: $%{y:,.2f}<extra></extra>")
        fig.update_layout(
            height=420,
            margin={"l": 0, "r": 10, "t": 10, "b": 0},
            xaxis_type="category",
            hovermode="x unified",
        )
        st.plotly_chart(fig, width="stretch")

    st.subheader(f"Top merchants, {month}")
    merchants = (
        cur.groupby("merchant")
        .agg(
            spent=("amount", "sum"),
            visits=("amount", "size"),
            category=("category", "first"),
        )
        .sort_values("spent", ascending=False)
        .head(10)
    )
    st.dataframe(
        merchants,
        column_config={"spent": st.column_config.NumberColumn(format="$%.2f")},
        width="stretch",
    )

    st.subheader("Transactions")
    picked = st.multiselect("Categories", sorted(cur.category.unique()))
    shown = cur[cur.category.isin(picked)] if picked else cur
    st.dataframe(
        shown.sort_values("date")[
            ["date", "description", "amount", "category", "source"]
        ],
        column_config={
            "date": st.column_config.DateColumn(format="MMM D"),
            "amount": st.column_config.NumberColumn(format="$%.2f"),
        },
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "source: payment = card payment or refund (never sent), memory = seen before, "
        "llm = categorized by the model, manual = set by you, "
        "fallback = needs review (`uv run spend review`), rule = older keyword match"
    )
