"""Streamlit dashboard: `uv run spend dashboard`."""

import pandas as pd
import plotly.express as px
import streamlit as st

from spending.analysis_tab import render as render_analysis
from spending.bank_sync import render as render_bank_sync
from spending.db import DB_PATH, SPENDING_SQL, connect
from spending.jev_check import render as render_jev_check
from spending.paid_back import render as render_paid_back
from spending.period import Period, custom, presets, summarize
from spending.split import EVERYDAY, ONE_OFF, kind, one_off_min

# Validated colorblind-safe categorical order (fixed, never cycled).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]
REST = "Everything else"
REST_COLOR = "#898781"
EXCLUDED = {"Payments & Credits"}  # not spending

st.set_page_config(page_title="Spending", layout="wide")


@st.cache_data(ttl=30)
def load() -> pd.DataFrame:
    """Card rows, each counted for what it cost you: what friends paid back of a
    purchase comes off (`gross` keeps the full price). Checking isn't spending."""
    with connect(DB_PATH) as conn:
        df = pd.read_sql(SPENDING_SQL, conn, parse_dates=["date"])
    df["gross"] = df.amount
    df["amount"] = df.gross - df.paid_back
    return df[~df.category.isin(EXCLUDED)]


st.title("Spending")
# Before load(), so the charts include whatever it just pulled from the bank.
bank = render_bank_sync()

df = load()
if df.empty:
    st.info(
        "No transactions yet. "
        + ("Sign in to your bank above, or import" if bank == "sign_in" else "Import")
        + " a statement with `uv run spend import statements/<file>.csv`."
    )
    st.stop()

# One switch for the dashboard's views; the period picker below applies to the
# first two.
view = st.sidebar.radio(
    "View", ["Spending", "Analysis", "Check categories"], key="view"
)
if view == "Check categories":
    render_jev_check()
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
df["kind"] = kind(df)
colors = {c: PALETTE[i] for i, c in enumerate(top)} | {REST: REST_COLOR}
order = top + [REST]


def pick_period(months: list[str]) -> Period:
    """The sidebar's period picker: one month (the newest by default, as before),
    a preset range, or a custom From/To month range."""
    newest_first = sorted(months, reverse=True)
    choices = presets(months)
    with st.sidebar:
        st.header("Period")
        kind = st.selectbox(
            "Show", ["One month", *choices, "Custom range"], key="period"
        )
        if kind == "One month":
            month = st.selectbox("Month", newest_first, key="month")
            return Period(month, month, month)
        if kind == "Custom range":
            start = st.selectbox(
                "From", newest_first, index=len(months) - 1, key="from"
            )
            end = st.selectbox("To", newest_first, key="to")
            return custom(start, end)
        return choices[kind]


def monthly_chart(rows: pd.DataFrame, key: str) -> None:
    """Spending per month, stacked by category (the same colors in every chart)."""
    if rows.empty:
        st.caption("Nothing in this period.")
        return
    monthly = rows.groupby(["month", "group"]).amount.sum().reset_index()
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
    st.plotly_chart(fig, width="stretch", key=f"monthly-{key}")


period = pick_period(sorted(df.month.unique()))
if view == "Analysis":
    render_analysis(df, period)
    st.stop()
month = period.label  # how the period is named in headings
cur = period.rows(df)
prev = period.previous()
has_prev = prev.end >= df.month.min()

total = cur.amount.sum()
paid_back = cur.paid_back.sum()
c1, c2, c3 = st.columns(3)
delta = None
if has_prev:
    prev_total = prev.rows(df).amount.sum()
    delta = f"{total - prev_total:+,.2f} vs {prev.label}"
c1.metric(
    "Total spent",
    f"${total:,.2f}",
    delta,
    delta_color="inverse",
    help=f"Not counting ${paid_back:,.2f} friends paid you back."
    if paid_back
    else None,
)
c2.metric("Transactions", f"{(cur.amount > 0).sum()}")
everyday_total = cur[cur.kind == EVERYDAY].amount.sum()
c1.caption(
    f"Everyday ${everyday_total:,.2f} · {ONE_OFF.lower()} ${total - everyday_total:,.2f}"
)
by_cat = cur.groupby("category").amount.sum().sort_values()
c3.metric(
    "Biggest category",
    by_cat.index[-1] if len(by_cat) else "-",
    f"${by_cat.iloc[-1]:,.2f}" if len(by_cat) else None,
    delta_color="off",
)

if not period.single:
    stats = summarize(cur, period)
    c1, c2, c3, c4 = st.columns(4)
    unfinished = len(period.months) - len(stats.complete_months)
    c1.metric(
        "Average per month",
        f"${stats.monthly_average:,.2f}" if stats.monthly_average is not None else "-",
        help=(
            f"Over {len(stats.complete_months)} complete month(s), counting months"
            " with no spending as $0"
            + (". This month isn't over yet, so it's left out." if unfinished else ".")
        ),
    )
    c2.metric(
        "Highest month",
        stats.highest[0] if stats.highest else "-",
        f"${stats.highest[1]:,.2f}" if stats.highest else None,
        delta_color="off",
    )
    c3.metric(
        "Lowest month",
        stats.lowest[0] if stats.lowest else "-",
        f"${stats.lowest[1]:,.2f}" if stats.lowest else None,
        delta_color="off",
    )
    c4.metric(
        "Average purchase",
        f"${stats.average_purchase:,.2f}" if stats.average_purchase else "-",
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
    # One month: every month for context. A longer period: just its months.
    shown_months = df if period.single else cur
    overall, one_off, everyday = st.tabs(["Overall", ONE_OFF, EVERYDAY])
    with overall:
        monthly_chart(shown_months, "overall")
    with one_off:
        st.caption(
            f"Travel, plus any purchase of ${one_off_min():,.0f} or more"
            " (your share). Set the amount with ONE_OFF_MIN in .env."
        )
        monthly_chart(shown_months[shown_months.kind == ONE_OFF], "one-off")
    with everyday:
        st.caption("Everything else: what a normal month costs.")
        monthly_chart(shown_months[shown_months.kind == EVERYDAY], "everyday")

if not period.single:
    st.subheader(f"Categories, {month}")
    st.dataframe(
        stats.categories,
        column_config={
            "total": st.column_config.NumberColumn(format="$%.2f"),
            "share": st.column_config.NumberColumn(format="percent"),
            "monthly_average": st.column_config.NumberColumn(
                "per month", format="$%.2f"
            ),
        },
        width="stretch",
    )

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

render_paid_back(month, cur)

st.subheader("Transactions")
picked = st.multiselect("Categories", sorted(cur.category.unique()))
shown = cur[cur.category.isin(picked)] if picked else cur
columns = ["date", "description", "amount", "category", "source"]
if shown.paid_back.any():
    columns[3:3] = ["gross", "paid_back"]
st.dataframe(
    shown.sort_values("date")[columns],
    column_config={
        "date": st.column_config.DateColumn(
            format="MMM D" if period.single else "MMM D, YYYY"
        ),
        "amount": st.column_config.NumberColumn("your share", format="$%.2f"),
        "gross": st.column_config.NumberColumn("price", format="$%.2f"),
        "paid_back": st.column_config.NumberColumn("paid back", format="$%.2f"),
    },
    hide_index=True,
    width="stretch",
)
st.caption(
    "source: payment = card payment or refund (never sent), memory = seen before, "
    "llm = categorized by the model, manual = set by you, "
    "fallback = needs review (`uv run spend review`), rule = older keyword match"
)
