"""Connected accounts (/settings): the bank logins the app reads through Plaid,
what each one shares, and whether it's working (connections.py does the
checking). From here you can sync now, sign in again, switch a login to another
bank, or disconnect one login and keep the other."""

import sqlite3
from datetime import UTC, datetime
from typing import Literal

import httpx
import pandas as pd
import streamlit as st

from spending import bank_sync, connections, db, plaid_source
from spending.connections import NOT_USED, ROLE_NAMES, USED_FOR, Connection, ago
from spending.plaid_source import Plaid, PlaidError

BADGE_COLORS: dict[str, Literal["green", "blue", "orange", "red", "gray"]] = {
    "working": "green",
    "waiting": "blue",
    "expiring": "orange",
    "bank_failing": "orange",
    "sign_in_again": "red",
    "not_tracking": "red",
    "reconnect": "red",
    "unknown": "gray",
}
NOT_CONNECTED = {
    "card": "Not connected. Sign in to the bank that issues your credit card:"
    " Plaid only offers credit accounts here.",
    "reimbursements": "Not connected. Optional: connect the checking account where"
    " friends pay you back, to spot purchases they paid back. Plaid only offers"
    " checking accounts here.",
}
SWITCH_HELP = (
    "Opens Plaid to sign in to another bank. This login is disconnected once the"
    " new one is connected; closing Plaid without finishing changes nothing. Each"
    " new connection uses up one of the 10 on Plaid's Trial plan."
)


def show(conn: sqlite3.Connection, plaid: Plaid, role: str, token: str | None) -> None:
    """One login: its bank, status, accounts, how syncing goes, and what to do."""
    items = db.get_plaid_items(conn, role)  # read again: a sync just updated it
    item = items[0] if items else None
    with st.container(border=True):
        if item is None:
            st.markdown(f"#### {ROLE_NAMES[role]}")
            st.write(NOT_CONNECTED[role])
            label = "Sign in to your bank" if role == "card" else "Connect checking"
            bank_sync.sign_in_button(plaid, label, role)
            return
        checks = st.session_state.setdefault("connections", {})  # item_id -> it
        if item["item_id"] not in checks:
            with st.spinner(f"Checking {item['institution'] or 'your bank'}..."):
                checks[item["item_id"]] = connections.check(plaid, item, token)
        c: Connection = checks[item["item_id"]]

        st.markdown(f"#### {ROLE_NAMES[role]} · {c.bank}")
        st.badge(c.label, color=BADGE_COLORS[c.state])
        if c.state == "waiting":
            st.info(c.message)
        elif c.message:
            (st.warning if c.state in ("expiring", "bank_failing") else st.error)(
                c.message
            )
        if c.accounts:
            st.dataframe(
                pd.DataFrame(
                    {
                        "Account": a.name,
                        "Type": a.kind,
                        "Number": f"••{a.mask}" if a.mask else "",
                        "Used for": USED_FOR[role] if a.tracked else NOT_USED[role],
                    }
                    for a in c.accounts
                ),
                hide_index=True,
                width="stretch",
            )
        st.caption(" · ".join(details(conn, item, c)))
        actions(conn, plaid, item, token, c)


def details(conn: sqlite3.Connection, item: sqlite3.Row, c: Connection) -> list[str]:
    """When the app and Plaid last got transactions, and what came in."""
    now = datetime.now(UTC)
    out = []
    sync = bank_sync.state(c.role)
    if sync["state"] in ("error", "sign_in_again"):
        out.append(f"This visit's sync failed: {sync['message']}")
    elif synced := connections.parse_time(item["last_synced"]):
        text = f"Synced {ago(synced, now)}"
        if sync["state"] == "synced" and sync["result"].status != "NOT_READY":
            r = sync["result"]
            text += f": {r.added} new, {r.updated} updated, {r.removed} removed"
        out.append(text)
    else:
        out.append("Not synced yet")
    if c.bank_success:
        out.append(f"Plaid last got data from the bank {ago(c.bank_success, now)}")
    if newest := db.newest_date(conn, db.ROLES[c.role]):
        out.append(f"Newest {db.ROLES[c.role]} transaction saved: {newest}")
    if ends := c.consent_ends:
        out.append(f"Bank permission ends {ends:%b} {ends.day}, {ends.year}")
    return out


def actions(
    conn: sqlite3.Connection,
    plaid: Plaid,
    item: sqlite3.Row,
    token: str | None,
    c: Connection,
) -> None:
    role = c.role
    confirm = f"confirm-unlink-{role}"
    if st.session_state.get(confirm):
        st.warning(
            f"Disconnect {c.bank}? Plaid's access is revoked and the sign-in token"
            " deleted; the transactions you have stay. Connecting again later uses"
            " up another of the 10 connections on Plaid's Trial plan."
        )
        with st.container(horizontal=True):
            yes = st.button("Yes, disconnect", key=f"unlink-yes-{role}", type="primary")
            no = st.button("Cancel", key=f"unlink-no-{role}")
        if yes:
            disconnect(conn, plaid, item, c)
        elif no:
            del st.session_state[confirm]
            st.rerun()
        return
    with st.container(horizontal=True):
        if c.state in ("sign_in_again", "expiring") and token:
            bank_sync.sign_in_button(
                plaid, "Sign in again", role, token, key=f"again-{role}"
            )
        bank_sync.sign_in_button(
            plaid,
            "Connect again" if c.state == "reconnect" else "Switch bank",
            role,
            key=f"switch-{role}",
            primary=c.state in ("not_tracking", "reconnect"),
            help=SWITCH_HELP,
        )
        if st.button("Disconnect", key=f"unlink-{role}"):
            st.session_state[confirm] = True
            st.rerun()


def disconnect(
    conn: sqlite3.Connection, plaid: Plaid, item: sqlite3.Row, c: Connection
) -> None:
    """Revoke just this login; the other one stays."""
    try:
        revoked = plaid_source.unlink(conn, plaid, item["item_id"])
    except (PlaidError, httpx.HTTPError) as e:
        st.error(f"Couldn't disconnect {c.bank}: {e} It's still connected; try again.")
        return
    conn.commit()
    del st.session_state[f"confirm-unlink-{c.role}"]
    st.session_state.connections.pop(item["item_id"], None)
    bank_sync.sync_again(c.role)
    st.session_state.disconnected_note = (
        f"Disconnected {c.bank}. The transactions you have stay."
        if revoked
        else f"Forgot {c.bank}, but its token was missing, so Plaid couldn't revoke"
        " it: remove the app in your bank's connected-apps settings."
    )
    st.rerun()


st.title("Connected accounts")
plaid = bank_sync.client()
if plaid == "off":
    st.info(
        "Bank sync isn't set up: put PLAID_CLIENT_ID and PLAID_SECRET in .env and"
        " restart the dashboard (see README)."
    )
if isinstance(plaid, str):
    st.stop()

logins = bank_sync.sync_logins(plaid)  # once per visit, whichever page opens first
for note in ("signed_in_note", "disconnected_note"):
    if text := st.session_state.pop(note, None):
        st.success(text)
st.caption(
    "The bank logins this app reads through Plaid. Each is synced when the"
    " dashboard opens. Plaid gets new transactions from your bank a few times a"
    " day on its own; **Sync now** fetches whatever it has."
)
if st.button("Sync now", icon=":material/sync:", key="sync-now"):
    bank_sync.sync_again()
    st.session_state.pop("connections", None)  # check every login again too
    st.rerun()
with db.connect() as conn:
    for role in ROLE_NAMES:
        show(conn, plaid, role, logins[role][1])
