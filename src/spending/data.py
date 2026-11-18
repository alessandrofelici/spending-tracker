"""What the Spending page charts, cached across reruns. A page that changes what
was paid back clears just this (load.clear()), so other caches stay."""

import pandas as pd
import streamlit as st

from spending import db

EXCLUDED = {"Payments & Credits"}  # not spending


@st.cache_data(ttl=30)
def load() -> pd.DataFrame:
    """Card rows, each counted for what it cost you: what friends paid back of a
    purchase comes off (`gross` keeps the full price). Checking isn't spending."""
    with db.connect(db.DB_PATH) as conn:
        df = pd.read_sql(db.SPENDING_SQL, conn, parse_dates=["date"])
    df["gross"] = df.amount
    df["amount"] = df.gross - df.paid_back
    return df[~df.category.isin(EXCLUDED)]
