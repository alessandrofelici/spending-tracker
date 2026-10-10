"""Dashboard "Categorization" tab: what Jev decided, how well it matches example
keyword rules, and how fast.

See documents/jev-review.md for how the chat-LLM estimates below were made.
"""

import os
import statistics

import pandas as pd
import plotly.express as px
import streamlit as st

from spending.classify import DEFAULT_MIN_CONFIDENCE
from spending.db import DB_PATH, connect

# Who decided a transaction's category. Fixed order and colors, shared by every
# chart on this tab; "Needs review" is the neutral gray, not a series hue.
# "Keyword rule" only appears for rows imported when rules still categorized.
LEGACY_RULE = "Keyword rule"
DECIDER_COLORS = {
    LEGACY_RULE: "#2a78d6",
    "Jev": "#eb6834",
    "You (manual)": "#1baf7a",
    "Needs review": "#898781",
}
DECIDERS = list(DECIDER_COLORS)
JEV_COLOR = DECIDER_COLORS["Jev"]
ESTIMATE_COLOR = "#898781"

# Chat-LLM estimate per merchant: TTFT + output_tokens / output_speed.
# Artificial Analysis P50 figures and token counts, checked 2026-10-07.
CHAT_MODELS = {
    # label: (TTFT s, output tokens/s, output tokens)
    "Claude 4.5 Haiku, JSON only": (0.59, 90, 8),
    "Claude 4.5 Haiku, JSON + reasoning": (0.59, 90, 42),
    "Gemini 3.8 Flash (high reasoning)": (24.33, 129, 8),
}
MERCHANTS_PER_MONTH = 10  # median new non-payment merchants per month in the real DB
WORKERS = 8


@st.cache_data(ttl=30)
def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with connect(DB_PATH) as conn:
        txns = pd.read_sql(
            """SELECT t.merchant, t.amount, t.category, t.source,
                      m.source AS memory_source
               FROM transactions t LEFT JOIN merchant_memory m USING (merchant)
               WHERE t.category != 'Payments & Credits'""",
            conn,
        )
        answers = pd.read_sql("SELECT * FROM jev_answers", conn)
        evals = pd.read_sql(
            "SELECT * FROM jev_evals", conn, parse_dates=["evaluated_at"]
        )
    return txns, answers, evals


def decider(row) -> str:
    if row.source == "rule":
        return LEGACY_RULE
    if row.source == "llm" or (row.source == "memory" and row.memory_source == "llm"):
        return "Jev"
    if row.source in ("manual", "memory"):
        return "You (manual)"
    return "Needs review"


def render() -> None:
    txns, answers, evals = load()
    if txns.empty:
        st.info("No transactions yet.")
        return
    txns["decider"] = txns.apply(decider, axis=1)
    deciders = [
        d for d in DECIDERS if d != LEGACY_RULE or (txns.decider == LEGACY_RULE).any()
    ]

    st.subheader("Who categorized your spending")
    shares = pd.DataFrame(
        {
            "Transactions": txns.groupby("decider").size(),
            "Merchants": txns.groupby("decider").merchant.nunique(),
            "Dollars": txns[txns.amount > 0].groupby("decider").amount.sum(),
        }
    ).reindex(deciders, fill_value=0)

    cols = st.columns(len(deciders))
    for col, name in zip(cols, deciders, strict=True):
        n, total = shares.at[name, "Transactions"], shares["Transactions"].sum()
        col.metric(name, f"{n / total:.0%}", f"{n} transactions", delta_color="off")

    long = (
        (shares / shares.sum() * 100)
        .reset_index(names="decider")
        .melt(id_vars="decider", var_name="measure", value_name="share")
    )
    long["raw"] = shares.reset_index(names="decider").melt(id_vars="decider").value
    fig = px.bar(
        long,
        x="share",
        y="measure",
        color="decider",
        orientation="h",
        category_orders={
            "decider": deciders,
            "measure": ["Transactions", "Merchants", "Dollars"],
        },
        color_discrete_map=DECIDER_COLORS,
        labels={"share": "Share (%)", "measure": "", "decider": "Decided by"},
        custom_data=["raw"],
    )
    fig.update_traces(
        texttemplate="%{x:.0f}%",
        textposition="inside",
        insidetextanchor="middle",
        hovertemplate="%{fullData.name}, %{y}: %{x:.1f}% (%{customdata[0]:,.0f})<extra></extra>",
    )
    fig.update_layout(
        height=260,
        margin={"l": 0, "r": 10, "t": 10, "b": 0},
        bargap=0.35,
        xaxis_range=[0, 100],
        legend={"orientation": "h", "y": -0.25},
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Jev includes merchants it categorized on an earlier import (source `memory`). "
        "Card payments and refunds are left out; they're matched locally and never sent."
    )

    left, right = st.columns(2)
    with left:
        render_review_reasons(txns, answers)
    with right:
        render_confidence(answers, evals)

    render_eval(evals)
    render_speed(answers, evals)


def render_review_reasons(txns: pd.DataFrame, answers: pd.DataFrame) -> None:
    st.subheader("Why merchants need review")
    review = txns[txns.decider == "Needs review"].merchant.drop_duplicates()
    if review.empty:
        st.success("Nothing needs review.")
        return
    latest = answers.set_index("merchant").outcome
    labels = {
        "unsure": "Jev unsure (below threshold)",
        "chose_other": "Jev picked Other",
        "failed": "Request failed",
    }
    reasons = (
        review.map(lambda m: labels.get(latest.get(m), "Not recorded"))
        .value_counts()
        .rename_axis("reason")
        .reset_index(name="merchants")
    )
    fig = px.bar(reasons, x="merchants", y="reason", orientation="h", text_auto=True)
    fig.update_traces(
        marker_color=ESTIMATE_COLOR,
        hovertemplate="%{y}: %{x} merchants<extra></extra>",
    )
    fig.update_layout(
        height=260,
        margin={"l": 0, "r": 10, "t": 10, "b": 0},
        bargap=0.35,
        yaxis_title="",
        xaxis_title="Merchants",
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Not recorded: imported before Jev's answers were logged, or with `--no-llm`. "
        "Fix these with `uv run spend review`."
    )


def render_confidence(answers: pd.DataFrame, evals: pd.DataFrame) -> None:
    st.subheader("How sure Jev was")
    answered = pd.concat([answers, evals])
    answered = answered[answered.outcome != "failed"]
    if answered.empty:
        st.info("No Jev answers recorded yet. They're logged on every import.")
        return
    threshold = float(os.environ.get("JEV_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE))
    fig = px.histogram(answered, x="confidence", nbins=20, range_x=[0, 1])
    fig.update_traces(
        marker_color=JEV_COLOR,
        hovertemplate="confidence %{x}: %{y} answers<extra></extra>",
    )
    fig.add_vline(
        x=threshold,
        line_dash="dash",
        line_width=2,
        line_color="#52514e",
        annotation_text=f"threshold {threshold}",
        annotation_position="top left",
    )
    fig.update_layout(
        height=260,
        margin={"l": 0, "r": 10, "t": 10, "b": 0},
        bargap=0.1,
        xaxis_title="Confidence",
        yaxis_title="Answers",
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        f"{len(answered)} answers from imports and evals. "
        "Below the threshold, the merchant goes to `spend review` instead of being guessed."
    )


def render_eval(evals: pd.DataFrame) -> None:
    st.subheader("Does Jev agree with keyword rules?")
    st.caption(
        "Imports don't use keyword rules. The example `[rules]` in categories.toml "
        "serve as known labels: every merchant they match is sent to Jev, and its "
        "answer is compared with the rule's."
    )
    evals = (
        evals[evals.outcome != "failed"]
        .sort_values("evaluated_at")
        .drop_duplicates("merchant", keep="last")
    )
    if evals.empty:
        st.info(
            "Run `uv run python -m spending.jev_eval` to compare Jev with the rules."
        )
        return
    evals["agrees"] = evals.choice == evals.rule

    c1, c2 = st.columns([1, 3])
    c1.metric(
        "Agreement",
        f"{evals.agrees.mean():.0%}",
        f"{evals.agrees.sum()} of {len(evals)} merchants",
        delta_color="off",
    )
    c1.caption(f"Last eval: {evals.evaluated_at.max():%b %d, %Y}")
    misses = evals[~evals.agrees][
        ["merchant", "rule", "choice", "confidence", "outcome"]
    ]
    c2.dataframe(
        misses.rename(columns={"rule": "Rule says", "choice": "Jev says"}),
        column_config={"confidence": st.column_config.NumberColumn(format="%.2f")},
        hide_index=True,
        width="stretch",
    )
    c2.caption(
        "Disagreements. A confident Jev answer here often means the rule is too broad; "
        "outcome `unsure` means an import would have sent it to review instead."
    )


def render_speed(answers: pd.DataFrame, evals: pd.DataFrame) -> None:
    st.subheader("How fast, compared with a chat LLM")
    timed = pd.concat([answers, evals])
    measured = timed[timed.outcome != "failed"].latency_ms.dropna()
    if measured.empty:
        st.info("No Jev timings recorded yet.")
        return
    jev_s = statistics.median(measured) / 1000
    per_merchant = {"Jev (measured median)": jev_s} | {
        f"{name} (estimate)": ttft + tokens / speed
        for name, (ttft, speed, tokens) in CHAT_MODELS.items()
    }
    rounds = -(-MERCHANTS_PER_MONTH // WORKERS)
    speed = pd.DataFrame(
        {
            "model": list(per_merchant),
            "seconds": [s * rounds for s in per_merchant.values()],
            "kind": ["Measured"] + ["Estimate"] * len(CHAT_MODELS),
        }
    )
    fig = px.bar(
        speed,
        x="seconds",
        y="model",
        color="kind",
        orientation="h",
        color_discrete_map={"Measured": JEV_COLOR, "Estimate": ESTIMATE_COLOR},
        category_orders={"model": list(per_merchant)},
        labels={"seconds": "Seconds", "model": "", "kind": ""},
        text_auto=".1f",
    )
    fig.update_traces(hovertemplate="%{y}: %{x:.1f} s<extra></extra>")
    fig.update_layout(
        height=280,
        margin={"l": 0, "r": 10, "t": 10, "b": 0},
        bargap=0.35,
        legend={"orientation": "h", "y": -0.25},
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        f"Time to categorize a typical month (~{MERCHANTS_PER_MONTH} new merchants, "
        f"{WORKERS} requests at a time = {rounds} rounds). Jev's time is the median of "
        f"{len(measured)} logged requests. The estimates are TTFT + output tokens / "
        "output speed, using Artificial Analysis P50 figures (checked 2026-10-07); "
        "see documents/jev-review.md for every input and its source."
    )
