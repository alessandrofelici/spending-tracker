"""The dashboard's "Paid back" section (issue #19): confirm which transfers into
checking paid back a card purchase, mark one by hand (cash, or a transfer that
never reached the connected account), and undo either. Matching is in
reimburse.py; everything shown here stays on this machine."""

import pandas as pd
import streamlit as st

from spending import db, reimburse

SHOWN = 10  # proposals listed at once; the rest come up as these are settled


def render(month: str, purchases: pd.DataFrame) -> None:
    """purchases: the chosen month's card rows, as the dashboard loaded them."""
    st.subheader("Paid back")
    if note := st.session_state.pop("paid_back_note", None):
        st.success(note)
    with db.connect(db.DB_PATH) as conn:
        matches = reimburse.propose(conn)
        pieces = db.get_reimbursements(conn)
        has_checking = conn.execute(
            "SELECT 1 FROM plaid_items WHERE role = 'reimbursements'"
            " UNION SELECT 1 FROM transactions WHERE account = 'checking' LIMIT 1"
        ).fetchone()
    if matches:
        st.write(
            f"{len(matches)} transfer(s) into checking look like someone paying"
            " you back. Confirm the ones that are, and only your share counts."
        )
        for m in matches[:SHOWN]:
            _proposal(m)
        if len(matches) > SHOWN:
            st.caption(f"{len(matches) - SHOWN} more after these.")
    elif has_checking:
        st.caption("No new transfers into checking look like paid-back purchases.")
    else:
        st.caption(
            "Connect your checking account above to spot transfers that pay you"
            " back. You can always mark a purchase by hand."
        )
    _mark_by_hand(month, purchases)
    if pieces:
        _recorded(pieces)


def _proposal(m: reimburse.Match) -> None:
    share = {1: "all of it", 2: "half"}.get(m.share or 0) or (
        f"1/{m.share}" if m.share else f"${m.amount:,.2f} of ${m.left:,.2f} left"
    )
    with st.container(border=True):
        left, right = st.columns([3, 2])
        left.markdown(
            f"**${m.amount:,.2f}** in on {m.incoming_date}: {m.incoming_description}"
            f"  \nfor **{m.purchase_description}** on {m.purchase_date}"
            f" (${m.purchase_amount:,.2f}; {share})"
        )
        key = m.incoming_id
        a, b, c = right.columns(3)
        if a.button("Paid back", key=f"ok-{key}", type="primary"):
            _act(lambda conn: reimburse.confirm(conn, m), "Counted as paid back.")
        if b.button("Not this purchase", key=f"no-{key}"):
            _act(
                lambda conn: db.dismiss_match(conn, m.purchase_id, m.incoming_id),
                "Won't propose that pair again.",
            )
        if c.button("Not a payback", key=f"never-{key}"):
            _act(
                lambda conn: reimburse.not_a_reimbursement(conn, m.incoming_id),
                "Won't propose that transfer again.",
            )


def _mark_by_hand(month: str, purchases: pd.DataFrame) -> None:
    open_ = purchases[(purchases.gross > 0) & (purchases.amount > 0.005)]
    if open_.empty:
        return
    open_ = open_.sort_values("gross", ascending=False)
    with (
        st.expander(f"Mark a purchase from {month} as paid back"),
        st.form("mark_paid_back", clear_on_submit=True),
    ):
        picked = st.selectbox(
            "Purchase",
            open_.id.tolist(),
            format_func=lambda i: _label(open_.set_index("id").loc[i]),
        )
        amount = st.number_input(
            "Paid back ($)", min_value=0.0, step=1.0, format="%.2f"
        )
        note = st.text_input("Note (optional)", placeholder="split 3 ways")
        if st.form_submit_button("Save"):
            _act(
                lambda conn: db.add_reimbursement(conn, picked, amount, note),
                f"Saved ${amount:,.2f} as paid back.",
            )


def _label(r: pd.Series) -> str:
    left = f", ${r.amount:,.2f} left" if r.paid_back else ""
    return f"{r.date:%b %d}  {r.description}  ${r.gross:,.2f}{left}"


def _recorded(pieces: list) -> None:
    with st.expander(f"Paid back so far ({len(pieces)})"):
        for p in pieces:
            by = (
                f"transfer on {p['incoming_date']}: {p['incoming_description']}"
                if p["incoming_id"]
                else "marked by hand"
            )
            note = f" ({p['note']})" if p["note"] else ""
            text, undo = st.columns([5, 1])
            text.markdown(
                f"**${p['amount']:,.2f}** of {p['description']} on {p['date']}"
                f" (${p['purchase_amount']:,.2f}), {by}{note}"
            )
            if undo.button("Undo", key=f"undo-{p['id']}"):
                _act(
                    lambda conn, rid=p["id"]: db.remove_reimbursement(conn, rid),
                    "Undone.",
                )


def _act(change, done: str) -> None:
    """Apply a change, then redraw with the totals it affects."""
    with db.connect(db.DB_PATH) as conn:
        try:
            change(conn)
        except ValueError as e:
            st.error(str(e))
            return
        conn.commit()
    st.session_state.paid_back_note = done
    st.cache_data.clear()
    st.rerun()
