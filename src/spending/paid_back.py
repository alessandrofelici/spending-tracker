"""The Paid back page (issue #35; paid_back_page.py runs it): confirm which Zelle
payments into checking paid back a card purchase, see the ones your bank hasn't
posted yet, mark a purchase by hand, and look back over or undo what counts.
Matching is in reimburse.py. Everything shown here stays on this machine: the
bank's descriptions name the people who paid you."""

import re
import sqlite3
from collections.abc import Callable
from datetime import date
from functools import partial

import streamlit as st

from spending import bank_sync, data, db, reimburse
from spending.reimburse import CENTS

PAGE = "paid_back_page.py"
SETTINGS = "settings_page.py"
SHOWN = 20  # purchases listed under Paid back so far before "Show more"
SHARES = {1: "all of it", 2: "half"}
ALL_MONTHS, EVERYONE, BY_HAND = "All months", "Everyone", "By hand"
# Markdown's special characters, escaped in bank and typed text. A pair of bare
# $ would even start LaTeX math, so amounts are escaped too (_usd).
MARKDOWN = re.compile(r"[\\`*_{}\[\]<>()#+\-.!|$~]")


def note() -> None:
    """On Spending: a link here when payments wait to be reviewed or are on
    their way."""
    with db.connect(db.DB_PATH) as conn:
        review = len(reimburse.propose(conn))
        coming = len(reimburse.on_the_way(conn))
    parts = []
    if review:
        parts.append(f"{_count(review, 'Zelle payment')} to review")
    if coming:
        parts.append(f"{coming} on the way")
    if parts:
        st.page_link(PAGE, label=" · ".join(parts), icon=":material/payments:")


def checking_problem() -> None:
    """Say when checking couldn't be synced this visit: new payments can't show
    up until it is."""
    sync = bank_sync.state("reimbursements")
    if sync["state"] in ("error", "sign_in_again"):
        st.warning(f"Couldn't get new payments from checking: {sync['message']}")
        st.page_link(
            SETTINGS,
            label="Open Connected accounts",
            icon=":material/account_balance:",
        )


@st.fragment
def render() -> None:
    """The page's lists. A fragment, so a click redraws only them; it clears only
    the Spending page's cache."""
    if done := st.session_state.pop("paid_back_done", None):
        st.toast(done, icon=":material/check_circle:")
    with db.connect(db.DB_PATH) as conn:
        matches = reimburse.propose(conn)
        purchases = {p["id"]: p for p in reimburse.open_purchases(conn)}
        payments = reimburse.payments(conn)
        coming = reimburse.on_the_way(conn)
        pieces = db.get_reimbursements(conn)
        hidden = reimburse.hidden(conn)
        has_checking = conn.execute(
            "SELECT 1 FROM plaid_items WHERE role = 'reimbursements'"
            " UNION SELECT 1 FROM transactions WHERE account = 'checking' LIMIT 1"
        ).fetchone()
    to_review(matches, purchases, bool(has_checking))
    on_the_way(coming)
    mark_by_hand(purchases, payments)
    so_far(pieces)
    hidden_payments(hidden)


# --- To review ----------------------------------------------------------------


def to_review(
    matches: list[reimburse.Match], purchases: dict, has_checking: bool
) -> None:
    st.subheader(f"To review ({len(matches)})" if matches else "To review")
    _problem("review")
    if not matches:
        if has_checking:
            st.caption("No new Zelle payments look like paid-back purchases.")
        else:
            st.caption(
                "Connect checking to spot Zelle payments that pay you back. You"
                " can always mark a purchase by hand."
            )
            st.page_link(
                SETTINGS,
                label="Open Connected accounts",
                icon=":material/account_balance:",
            )
        return
    st.caption(
        "Zelle payments into checking that look like someone paying you back."
        " Check the purchase each one was for, then **Paid back**: only your"
        " share of it counts."
    )
    for m in matches:
        proposal(m, purchases)


def proposal(m: reimburse.Match, purchases: dict) -> None:
    """One payment: who sent it, the purchase it best fits (or any other), and
    what to do with it."""
    # The purchases it fits, best first, then any other it could pay back,
    # newest first. The box is searchable, for one more than a week away.
    others = [
        i
        for i, p in purchases.items()
        if i not in m.candidates and p["left"] + CENTS / 2 >= m.amount
    ]
    with st.container(border=True):
        st.markdown(
            f"{when(m.incoming_date)} · **{_usd(m.amount)}**"
            f" {_from(m.incoming_description)}",
            help=_md(m.incoming_description),
        )
        pick, act = st.columns([3, 2], vertical_alignment="bottom")
        pick.selectbox(
            "For",
            [*m.candidates, *others],
            format_func=lambda i: purchase_label(purchases[i], m.amount),
            key=f"for-{m.incoming_id}",
            help="The purchases this payment fits come first, best first. Type"
            " to search any other.",
        )
        with act, st.container(horizontal=True):
            st.button(
                "Paid back",
                key=f"ok-{m.incoming_id}",
                type="primary",
                on_click=_confirm,
                args=(m,),
            )
            st.button(
                "Not a payback",
                key=f"never-{m.incoming_id}",
                help="A friend repaying a loan, say. It's listed under Hidden,"
                " where you can bring it back.",
                on_click=_act,
                args=(
                    partial(reimburse.not_a_reimbursement, incoming_id=m.incoming_id),
                    "Won't suggest that payment again.",
                    "review",
                ),
            )


def _confirm(m: reimburse.Match) -> None:
    picked = st.session_state.get(f"for-{m.incoming_id}") or m.purchase_id
    _act(
        partial(
            db.add_reimbursement,
            txn_id=picked,
            amount=m.amount,
            incoming_id=m.incoming_id,
        ),
        f"Counted {_usd(m.amount)} as paid back.",
        "review",
    )


# --- On the way ---------------------------------------------------------------


def on_the_way(coming: list[sqlite3.Row]) -> None:
    if not coming:
        return
    st.subheader(f"On the way ({len(coming)})")
    st.caption(
        "Zelle payments your bank hasn't posted yet. They come up under To review"
        " once it does, usually within a business day or two."
    )
    for p in coming:
        st.markdown(
            f"{when(p['date'])} · **{_usd(-p['amount'])}** {_from(p['description'])}",
            help=_md(p["description"]),
        )


# --- Mark by hand -------------------------------------------------------------


def mark_by_hand(purchases: dict, payments: list[sqlite3.Row]) -> None:
    st.subheader("Mark by hand")
    _problem("mark")
    if not purchases:
        st.caption("No purchases are left to pay back.")
        return
    st.caption(
        "For cash, Venmo, or a Zelle payment that came more than a week from the"
        " purchase. Any month: type to search."
    )
    n = st.session_state.get("mark_n", 0)  # a save bumps it, clearing the fields
    paid = {p["id"]: p for p in payments}
    st.selectbox(
        "Purchase",
        list(purchases),
        index=None,
        format_func=lambda i: purchase_label(purchases[i]),
        placeholder="Search any purchase...",
        key=f"mark-purchase-{n}",
    )
    by, amount, note = st.columns([2, 1, 2])
    payment = by.selectbox(
        "Paid by",
        [None, *paid],
        format_func=lambda i: (
            "Cash, Venmo or anything else" if i is None else payment_label(paid[i])
        ),
        key=f"mark-payment-{n}",
        help="The Zelle payment it came in, if any: it's then used up, so it"
        " isn't suggested for another purchase.",
    )
    amount.number_input(
        "Paid back ($)",
        min_value=0.0,
        value=paid[payment]["amount"] if payment else None,
        step=1.0,
        format="%.2f",
        key=f"mark-amount-{n}-{payment}",  # a new payment fills in its amount
    )
    note.text_input("Note (optional)", placeholder="split 3 ways", key=f"mark-note-{n}")
    st.button("Save", key="mark-save", type="primary", on_click=_mark, args=(n,))


def _mark(n: int) -> None:
    state = st.session_state
    purchase = state.get(f"mark-purchase-{n}")
    payment = state.get(f"mark-payment-{n}")
    amount = state.get(f"mark-amount-{n}-{payment}")
    if purchase is None:
        state.paid_back_error = ("mark", "Pick the purchase that was paid back.")
    elif not amount:
        state.paid_back_error = ("mark", "Enter how much of it was paid back.")
    elif _apply(
        partial(
            db.add_reimbursement,
            txn_id=purchase,
            amount=amount,
            note=state.get(f"mark-note-{n}"),
            incoming_id=payment,
        ),
        f"Saved {_usd(amount)} as paid back.",
        "mark",
    ):
        state.mark_n = n + 1


# --- Paid back so far ---------------------------------------------------------


def so_far(pieces: list[sqlite3.Row]) -> None:
    """One row per purchase, newest first, with the pieces that paid it back."""
    st.subheader("Paid back so far")
    _problem("so-far")
    if not pieces:
        st.caption("Nothing yet.")
        return
    months = sorted({p["month"] for p in pieces}, reverse=True)
    people = sorted({_person(p) for p in pieces} - {BY_HAND})
    left, right = st.columns(2)
    month = left.selectbox(
        "Month",
        [ALL_MONTHS, *months],
        format_func=lambda m: (
            m if m == ALL_MONTHS else f"{date.fromisoformat(m + '-01'):%b %Y}"
        ),
        key="so-far-month",
    )
    person = right.selectbox("Who", [EVERYONE, *people, BY_HAND], key="so-far-person")
    shown = [
        p
        for p in pieces
        if month in (ALL_MONTHS, p["month"]) and person in (EVERYONE, _person(p))
    ]
    if not shown:
        st.caption("Nothing for that month and person.")
        return
    every: dict[str, list[sqlite3.Row]] = {}  # a purchase's pieces, for its totals
    for p in pieces:
        every.setdefault(p["txn_id"], []).append(p)
    ids = list(dict.fromkeys(p["txn_id"] for p in shown))
    limit = st.session_state.get("so_far_limit", SHOWN)
    for txn_id in ids[:limit]:
        purchase_paid_back(every[txn_id], [p for p in shown if p["txn_id"] == txn_id])
    if len(ids) > limit:
        st.button(
            f"Show {min(SHOWN, len(ids) - limit)} more",
            key="so-far-more",
            on_click=_set,
            args=("so_far_limit", limit + SHOWN),
        )
    undo_all(shown, filtered=(month, person) != (ALL_MONTHS, EVERYONE))


def purchase_paid_back(pieces: list[sqlite3.Row], shown: list[sqlite3.Row]) -> None:
    first = pieces[0]
    paid = round(sum(p["amount"] for p in pieces), 2)
    left = round(first["purchase_amount"] - paid, 2)
    with st.container(border=True):
        st.markdown(
            f"**{_md(first['merchant'])}** · {when(first['date'])}"
            f" · {_usd(first['purchase_amount'])}  \n"
            f"paid back {_usd(paid)}, "
            + (f"{_usd(left)} left" if left > CENTS / 2 else "all of it")
        )
        for p in shown:
            text, undo = st.columns([4, 1], vertical_alignment="center")
            text.markdown(
                _piece(p),
                help=_md(p["incoming_description"]) if p["incoming_id"] else None,
            )
            undo.button(
                "Undo",
                key=f"undo-{p['id']}",
                on_click=_act,
                args=(
                    partial(db.remove_reimbursement, reimbursement_id=p["id"]),
                    "Undone.",
                    "so-far",
                ),
            )


def undo_all(shown: list[sqlite3.Row], filtered: bool) -> None:
    """Undo every piece shown, after a confirm step: pieces marked by hand can't
    be brought back."""
    ids = [p["id"] for p in shown]
    if not st.session_state.get("undo_all"):
        st.button(
            "Undo all shown..." if filtered else "Undo all...",
            key="undo-all",
            on_click=_set,
            args=("undo_all", True),
        )
        return
    purchases = len({p["txn_id"] for p in shown})
    by_hand = sum(not p["incoming_id"] for p in shown)
    st.warning(
        f"Undo {_count(len(ids), 'piece')} paid back for"
        f" {_count(purchases, 'purchase')}? They count in full again, and their"
        " Zelle payments are suggested again."
        + (
            f" {_count(by_hand, 'piece')} marked by hand can't be brought back."
            if by_hand
            else ""
        )
    )
    with st.container(horizontal=True):
        st.button(
            "Yes, undo them",
            key="undo-all-yes",
            type="primary",
            on_click=_undo_all,
            args=(ids,),
        )
        st.button("Cancel", key="undo-all-no", on_click=_set, args=("undo_all", False))


def _undo_all(ids: list[int]) -> None:
    st.session_state.undo_all = False
    _act(
        partial(db.remove_reimbursements, ids=ids),
        f"Undid {_count(len(ids), 'piece')}.",
        "so-far",
    )


# --- Hidden -------------------------------------------------------------------


def hidden_payments(hidden: list[sqlite3.Row]) -> None:
    if not hidden:
        return
    with st.expander(f"Hidden ({len(hidden)})"):
        st.caption(
            "Zelle payments you said no to. **Show again** has one suggested again"
            " when a purchase fits it; you can also pick it under Mark by hand."
        )
        _problem("hidden")
        for h in hidden:
            what = (
                "not a payback"
                if h["not_a_payback"]
                else f"{_count(h['turned_down'], 'purchase')} turned down"
            )
            text, again = st.columns([4, 1], vertical_alignment="center")
            text.markdown(
                f"{when(h['date'])} · **{_usd(h['amount'])}**"
                f" {_from(h['description'])} · {what}",
                help=_md(h["description"]),
            )
            again.button(
                "Show again",
                key=f"unhide-{h['id']}",
                on_click=_act,
                args=(
                    partial(reimburse.show_again, incoming_id=h["id"]),
                    "Showing it again.",
                    "hidden",
                ),
            )


# --- Wording ------------------------------------------------------------------


def when(iso: str) -> str:
    """'Mar 3', with the year when it isn't this one."""
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}" + ("" if d.year == date.today().year else f", {d.year}")


def purchase_label(p: sqlite3.Row, amount: float | None = None) -> str:
    """'EXAMPLE PIZZA · Mar 2 · $40.00 · half': the merchant as Top merchants
    names it, and what share of the purchase amount would be, or what's left."""
    parts = [p["merchant"] or p["description"], when(p["date"]), f"${p['amount']:,.2f}"]
    k = reimburse.share_of(amount, p["amount"]) if amount is not None else None
    if k:
        parts.append(SHARES.get(k, f"1/{k}"))
    elif amount is not None or p["left"] < p["amount"] - CENTS / 2:
        parts.append(f"${p['left']:,.2f} left")
    return " · ".join(parts)


def payment_label(p: sqlite3.Row) -> str:
    """'$20.00 from Jane Doe (Zelle) · Mar 3', as plain text."""
    name = reimburse.sender(p["description"])
    sent = f"from {name} (Zelle)" if name else p["description"]
    return f"${p['amount']:,.2f} {sent} · {when(p['date'])}"


def _from(description: str) -> str:
    """'from **Jane Doe** (Zelle)' as markdown, or the bank's own wording when
    the sender can't be told from it."""
    name = reimburse.sender(description)
    return f"from **{_md(name)}** (Zelle)" if name else f"· {_md(description)}"


def _person(p: sqlite3.Row) -> str:
    """Who paid a piece back, for the filter."""
    if not p["incoming_id"]:
        return BY_HAND
    return reimburse.sender(p["incoming_description"]) or p["incoming_description"]


def _piece(p: sqlite3.Row) -> str:
    """One piece paid back, as markdown: how much, who, how and when."""
    how = (
        f"{_md(_person(p))} · Zelle · {when(p['incoming_date'])}"
        if p["incoming_id"]
        else "by hand"
    )
    note = f' · "{_md(p["note"])}"' if p["note"] else ""
    return f"{_usd(p['amount'])} · {how}{note}"


def _usd(amount: float) -> str:
    """An amount for markdown."""
    return f"\\${amount:,.2f}"


def _md(text: str) -> str:
    """Bank or typed text, shown as is in markdown."""
    return MARKDOWN.sub(r"\\\g<0>", text)


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}{'' if n == 1 else 's'}"


# --- Acting -------------------------------------------------------------------


def _act(change: Callable[[sqlite3.Connection], object], done: str, where: str) -> None:
    """A button's on_click, so the redraw that follows shows the change."""
    _apply(change, done, where)


def _apply(
    change: Callable[[sqlite3.Connection], object], done: str, where: str
) -> bool:
    """Apply a change, and say how it went: a toast, or an error in the section
    it came from. False if it was refused."""
    with db.connect(db.DB_PATH) as conn:
        try:
            change(conn)
        except ValueError as e:
            st.session_state.paid_back_error = (where, str(e))
            return False
        conn.commit()
    st.session_state.paid_back_done = done
    data.load.clear()  # Spending's totals; nothing else changed
    return True


def _problem(where: str) -> None:
    """Show the error the last click in this section ran into."""
    error = st.session_state.get("paid_back_error")
    if error and error[0] == where:
        del st.session_state.paid_back_error
        st.error(error[1])


def _set(key: str, value: object) -> None:
    st.session_state[key] = value
