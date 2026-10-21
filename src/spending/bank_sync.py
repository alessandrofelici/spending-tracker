"""Dashboard bank sync: get new transactions from Plaid each time the app opens,
or ask you to sign in to your bank first. plaid_source.py does the Plaid side.

Signing in happens in Plaid Link, Plaid's own sign-in window: it loads from Plaid
and opens the bank's page in a pop-up, so your bank password never reaches this
app. Link hands back a one-time public token, which is swapped for the access
token in Python; the access token itself never goes to the browser.
"""

import contextlib
import sqlite3
import time
import uuid

import httpx
import streamlit as st

from spending import db, plaid_source
from spending.classify import CATEGORIES_PATH, openrouter_key
from spending.plaid_source import LOGIN_GONE, SIGN_IN_AGAIN, Plaid, PlaidError

# Opens Plaid Link over the page and reports how it ended. Streamlit calls this on
# every rerun, so it opens Link only once per sign-in attempt.
LINK_JS = """
export default function ({ data, setTriggerValue }) {
  if (window.plaidLinkAttempt === data.attempt) return;
  window.plaidLinkAttempt = data.attempt;
  const open = () =>
    window.Plaid.create({
      token: data.token,
      onSuccess: (publicToken, metadata) =>
        setTriggerValue("success", {
          public_token: publicToken,
          institution: metadata.institution ? metadata.institution.name : null,
        }),
      onExit: (err) =>
        setTriggerValue("exit", err ? err.display_message || err.error_message : ""),
    }).open();
  if (window.Plaid) return open();
  const script = document.createElement("script");
  script.src = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";
  script.onload = open;
  script.onerror = () =>
    setTriggerValue("exit", "Couldn't load Plaid. Check your internet connection.");
  document.head.appendChild(script);
}
"""
# How long to wait for a new login's first transactions before showing the page.
FIRST_SYNC_WAIT_S = 30


def render() -> str:
    """Sync once per browser session, i.e. each time the app opens, and show how
    it went. Returns "off" when bank sync isn't set up (no Plaid keys), otherwise
    the state: synced | sign_in | sign_in_again | error."""
    try:
        plaid = Plaid.from_env()
    except ValueError as e:  # a PLAID_ENV typo
        st.error(str(e))
        return "error"
    if plaid is None:
        return "off"
    if not CATEGORIES_PATH.exists():
        st.warning(
            f"{CATEGORIES_PATH.name} not found, so new transactions can't be"
            " categorized. Copy categories.example.toml to categories.toml (see README)."
        )
        return "error"

    with db.connect() as conn:
        items = db.get_plaid_items(conn)  # newest first
        item = items[0] if items else None
        token = plaid_source.load_token(item["item_id"]) if item else None
        if "bank" not in st.session_state:  # the app just opened
            _revoke_leftovers(conn, plaid, items[1:])
            st.session_state.bank = _sync(
                conn, plaid, item, token, st.session_state.pop("just_linked", False)
            )
        _finish_sign_in(conn, plaid)
    bank = st.session_state.bank
    _show(plaid, item, token, bank)
    return bank["state"]


def _revoke_leftovers(
    conn: sqlite3.Connection, plaid: Plaid, old_items: list[sqlite3.Row]
) -> None:
    """Retry revoking logins a sign-in replaced but couldn't revoke at the time."""
    for old in old_items:
        with contextlib.suppress(PlaidError, httpx.HTTPError):
            plaid_source.unlink(conn, plaid, old["item_id"])
            conn.commit()


def _sync(
    conn: sqlite3.Connection,
    plaid: Plaid,
    item: sqlite3.Row | None,
    token: str | None,
    just_linked: bool,
) -> dict:
    if item is None or token is None:
        return {"state": "sign_in"}
    name = item["institution"] or "your bank"
    deadline = time.monotonic() + (FIRST_SYNC_WAIT_S if just_linked else 0)
    try:
        with st.spinner(f"Getting new transactions from {name}..."):
            while True:
                result = plaid_source.sync(
                    conn,
                    plaid,
                    item["item_id"],
                    token,
                    use_llm=openrouter_key() is not None,
                )
                # A new login's first transactions take Plaid a few seconds.
                if result.status != "NOT_READY" or time.monotonic() > deadline:
                    break
                time.sleep(2)
    except PlaidError as e:
        if e.code in SIGN_IN_AGAIN:
            return {"state": "sign_in_again", "message": str(e)}
        if e.code in LOGIN_GONE:
            return {"state": "sign_in", "message": str(e)}
        return {"state": "error", "message": str(e)}
    except httpx.HTTPError as e:
        return {"state": "error", "message": f"Couldn't reach Plaid ({e})."}
    if result.added or result.updated or result.removed:
        st.cache_data.clear()  # the charts read the database through a cache
    if result.added:
        st.toast(f"{result.added} new transaction(s) from {name}")
    return {"state": "synced", "result": result}


def _show(
    plaid: Plaid, item: sqlite3.Row | None, token: str | None, bank: dict
) -> None:
    name = item["institution"] if item and item["institution"] else "your bank"
    if note := st.session_state.pop("signed_in_note", None):
        st.success(note)
    if bank["state"] == "synced":
        st.caption(_summary(name, bank["result"]))
    elif bank["state"] == "error":
        st.warning(
            f"Couldn't get new transactions from {name}: {bank['message']}"
            " Showing what's already saved."
        )
    elif bank["state"] == "sign_in_again":
        st.warning(f"{name} needs you to sign in again. {bank['message']}")
        _sign_in_button(plaid, "Sign in again", token)
    else:
        with st.container(border=True):
            st.markdown("#### Connect your bank")
            st.write(
                "Sign in once through Plaid, and the dashboard pulls new"
                " transactions every time it opens. When your bank asks which"
                " accounts to share, pick only your credit card."
            )
            if bank.get("message"):
                st.warning(bank["message"])
            _sign_in_button(plaid, "Sign in to your bank")
            if plaid.env == "sandbox":
                st.caption(
                    "Plaid sandbox (test banks only): sign in with"
                    " user_good / pass_good, and 1234 if asked for a code."
                )


def _summary(name: str, r: plaid_source.SyncResult) -> str:
    if r.status == "NOT_READY":
        return (
            f"Plaid is still getting your transactions from {name}."
            " Open the app again in a few minutes."
        )
    if r.added or r.updated or r.removed:
        changes = f"{r.added} new, {r.updated} updated, {r.removed} removed"
    else:
        changes = "nothing new"
    text = f"Synced with {name} just now: {changes}."
    if r.flagged:
        text += f" {r.flagged} to categorize with `spend review`."
    if r.status == "INITIAL_UPDATE_COMPLETE":
        text += " Older history is still on its way."
    if r.skipped_accounts:
        text += (
            f" Not tracked: {', '.join(r.skipped_accounts)} (only credit accounts are)."
        )
    return text


def _sign_in_button(plaid: Plaid, label: str, token: str | None = None) -> None:
    """Opens Plaid Link; with token, in update mode for the existing login."""
    if st.button(label, type="primary"):
        try:
            link_token = plaid.link_token(access_token=token)
        except (PlaidError, httpx.HTTPError) as e:
            st.error(f"Couldn't start the sign-in: {e}")
            return
        st.session_state.link_attempt = {
            "token": link_token,
            "attempt": uuid.uuid4().hex,
            "update": token is not None,
        }
        st.rerun()


def _finish_sign_in(conn: sqlite3.Connection, plaid: Plaid) -> None:
    """While Link is open, mount it and act on how it ended."""
    link = st.session_state.get("link_attempt")
    if link is None:
        return
    # Registered on every run so it's in the running app's registry; an
    # identical re-registration is a no-op.
    plaid_link = st.components.v2.component("plaid_link", js=LINK_JS)
    result = plaid_link(
        key="plaid_link",
        data={"token": link["token"], "attempt": link["attempt"]},
        on_success_change=lambda: None,
        on_exit_change=lambda: None,
    )
    if result.success:
        del st.session_state.link_attempt
        # Update mode keeps the same login and token, so there's nothing to swap.
        if not link["update"]:
            try:
                where, problems = plaid_source.link(
                    conn,
                    plaid,
                    result.success["public_token"],
                    result.success["institution"],
                )
            except (PlaidError, httpx.HTTPError) as e:
                st.session_state.bank = {
                    "state": "sign_in",
                    "message": f"Signing in didn't finish: {e}",
                }
                st.rerun()
            st.session_state.signed_in_note = " ".join(
                [
                    f"Connected to {result.success['institution'] or 'your bank'}.",
                    f"The sign-in token is kept in {where}.",
                    *problems,
                ]
            )
        st.session_state.just_linked = True
        del st.session_state.bank  # sync again on the next run
        st.rerun()
    elif result.exit is not None:
        del st.session_state.link_attempt
        if result.exit:  # empty when you just closed the window
            st.session_state.bank["message"] = result.exit
        st.rerun()
