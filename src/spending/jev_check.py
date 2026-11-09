"""The dashboard's "Check categories" view (issue #14): catch the stores Jev placed
confidently but wrongly. Its unsure picks already wait in `spend review`; the
confident ones were never shown to anyone. This lists every store Jev placed,
biggest spend first, since a mistake there distorts the charts most.

Fixing one works like `spend set`: the store's past and future transactions get
the category, and Jev never overrides it. "Looks right" keeps the category but
marks it as yours, so the store leaves the list. Corrections aren't fed back to
Jev: if one category keeps catching the wrong stores, sharpen its description in
categories.toml, which is what Jev reads.
"""

import sqlite3

import streamlit as st

from spending import db
from spending.classify import CATEGORIES_PATH, load_config

SHOWN = 15  # stores listed at once; the rest come up as these are checked


def to_check(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Stores whose category Jev chose and you haven't confirmed, biggest spend
    first, with Jev's confidence when it was recorded."""
    return conn.execute(
        """SELECT t.merchant, MIN(t.description) AS example, MIN(t.category) AS category,
                  COUNT(*) AS n, SUM(t.amount) AS spent, j.confidence
           FROM transactions t LEFT JOIN jev_answers j ON j.merchant = t.merchant
           WHERE t.account = 'credit' AND t.source = 'llm'
           GROUP BY t.merchant
           ORDER BY spent DESC, t.merchant"""
    ).fetchall()


def render() -> None:
    st.subheader("Check Jev's categories")
    if note := st.session_state.pop("jev_check_note", None):
        st.success(note)
    if not CATEGORIES_PATH.exists():
        st.warning(f"{CATEGORIES_PATH.name} not found, so categories can't be changed.")
        return
    categories = load_config()
    with db.connect(db.DB_PATH) as conn:
        stores = to_check(conn)
    if not stores:
        st.write(
            "Nothing to check: every store Jev placed has been confirmed or"
            " changed by you."
        )
        return
    st.write(
        f"Jev placed {len(stores)} store(s) you haven't checked. The biggest"
        " spending comes first, since a wrong category there skews the charts"
        " most. A change applies to every purchase at that store, past and"
        " future, and Jev never overrides it."
    )
    for s in stores[:SHOWN]:
        _store(s, categories)
    if len(stores) > SHOWN:
        st.caption(f"{len(stores) - SHOWN} more after these.")
    st.caption(
        "If Jev keeps putting the wrong stores in one category, make that"
        " category's description in categories.toml clearer: it's what Jev reads."
    )


def _store(s: sqlite3.Row, categories: list[str]) -> None:
    key = s["merchant"]
    sure = f", {s['confidence']:.0%} sure" if s["confidence"] is not None else ""
    with st.container(border=True):
        text, pick, ok = st.columns([3, 2, 1])
        text.markdown(
            f"**{s['example']}**  \n{s['n']} purchase(s), ${s['spent']:,.2f}."
            f" Jev chose **{s['category']}**{sure}."
        )
        current = categories.index(s["category"]) if s["category"] in categories else 0
        chosen = pick.selectbox(
            "Category",
            categories,
            index=current,
            key=f"jev-cat-{key}",
            label_visibility="collapsed",
        )
        if chosen != s["category"]:
            if ok.button("Save", key=f"jev-save-{key}", type="primary"):
                _set(key, chosen, f"{s['example']} is now {chosen}.")
        elif ok.button("Looks right", key=f"jev-ok-{key}"):
            _set(key, chosen, f"Kept {s['example']} in {chosen}.")


def _set(merchant: str, category: str, done: str) -> None:
    with db.connect(db.DB_PATH) as conn:
        db.set_merchant_category(conn, merchant, category)
        conn.commit()
    st.session_state.jev_check_note = done
    st.cache_data.clear()  # the charts read the categories through a cache
    st.rerun()
