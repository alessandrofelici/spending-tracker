"""What each bank login shares and whether it's working, for the Connected
accounts page (settings_page.py).

check() asks Plaid about a login (/item/get, /accounts/get) and assess() turns
the answers into a Connection: no Streamlit here, so the rules are testable.
Only names, types and the last digits Plaid gives for each account are kept;
the access token never leaves check().
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from .plaid_source import LOGIN_GONE, SIGN_IN_AGAIN, Plaid, PlaidError, tracks

ROLE_NAMES = {"card": "Credit card", "reimbursements": "Checking"}
# What a login of each role uses its accounts for.
USED_FOR = {
    "card": "Card spending",
    "reimbursements": "Money in, to spot paid-back purchases",
}
NOT_USED = {
    "card": "Nothing: only credit accounts count as spending",
    "reimbursements": "Nothing: only checking is used",
}
NOT_TRACKING = {
    "card": "{bank} shares no credit card through this login, so no card"
    " transactions come in. Use **Switch bank** to sign in to the bank that"
    " issues your card.",
    "reimbursements": "{bank} shares no checking account through this login, so"
    " paid-back purchases can't be spotted. Use **Switch bank** to sign in to the"
    " bank where that money arrives.",
}
# How long before the bank's permission ends to start asking for a sign-in.
CONSENT_WARNING = timedelta(days=14)

# Connection.state, with the label the page shows for it.
STATES = {
    "working": "Working",
    "waiting": "Getting transactions",
    "expiring": "Sign in soon",
    "bank_failing": "Can't reach the bank",
    "sign_in_again": "Needs sign-in",
    "not_tracking": "Not tracking anything",
    "reconnect": "Connect again",
    "unknown": "Couldn't check",
}


@dataclass
class Account:
    name: str
    kind: str  # Plaid's subtype, e.g. "credit card", "checking"
    mask: str | None  # the last digits, as the bank gives them
    tracked: bool


@dataclass
class Connection:
    role: str
    bank: str
    state: str  # a key of STATES
    message: str = ""  # what's wrong and what to do; empty when working
    accounts: list[Account] = field(default_factory=list)
    bank_success: datetime | None = None  # when Plaid last got data from the bank
    bank_failure: datetime | None = None  # when Plaid last failed to
    consent_ends: datetime | None = None  # when the bank's permission runs out

    @property
    def label(self) -> str:
        return STATES[self.state]


def check(
    plaid: Plaid,
    item: sqlite3.Row,
    token: str | None,
    now: datetime | None = None,
) -> Connection:
    """Ask Plaid how this login is doing. Never raises for Plaid or network
    errors: they become the Connection's state."""
    now = now or datetime.now(UTC)
    role, bank = item["role"], item["institution"] or "your bank"

    def failed(state: str, message: str) -> Connection:
        return Connection(role, bank, state, message)

    if token is None:
        return failed(
            "reconnect",
            "This login's sign-in token is missing here, so it can't sync."
            " Connect it again.",
        )
    try:
        info = plaid.item(token)
        accounts = None
        if not info["item"].get("error"):
            accounts = plaid.accounts(token)
    except PlaidError as e:
        if e.code in LOGIN_GONE:
            return failed("reconnect", f"Plaid no longer has this login: {e}")
        if e.code in SIGN_IN_AGAIN:
            return failed("sign_in_again", f"{bank} needs you to sign in again. {e}")
        return failed("unknown", f"Plaid couldn't check this login: {e}")
    except httpx.HTTPError as e:
        return failed("unknown", f"Couldn't reach Plaid ({e}). Try again.")
    return assess(role, bank, info, accounts, now)


def assess(
    role: str,
    bank: str,
    info: dict,
    accounts: list[dict] | None,
    now: datetime,
) -> Connection:
    """A login's state from Plaid's /item/get answer (info) and its accounts
    (None when Plaid wouldn't list them, e.g. while it needs a sign-in)."""
    item = info["item"]
    bank = item.get("institution_name") or bank
    pulls = (info.get("status") or {}).get("transactions") or {}
    out = Connection(
        role,
        bank,
        "working",
        accounts=[
            Account(
                a["name"], a.get("subtype") or a["type"], a.get("mask"), tracks(role, a)
            )
            for a in accounts or []
        ],
        bank_success=parse_time(pulls.get("last_successful_update")),
        bank_failure=parse_time(pulls.get("last_failed_update")),
        consent_ends=parse_time(item.get("consent_expiration_time")),
    )
    success, failure, ends = out.bank_success, out.bank_failure, out.consent_ends
    if error := item.get("error"):
        code = error.get("error_code", "")
        why = error.get("display_message") or error.get("error_message") or code
        if code in SIGN_IN_AGAIN:
            out.state, out.message = (
                "sign_in_again",
                f"{bank} needs you to sign in again. {why}",
            )
        else:
            out.state = "bank_failing"
            out.message = f"Plaid reports a problem with this login: {why}"
    elif accounts is not None and not any(a.tracked for a in out.accounts):
        out.state, out.message = "not_tracking", NOT_TRACKING[role].format(bank=bank)
    elif failure and (success is None or failure > success):
        out.state = "bank_failing"
        out.message = (
            f"Plaid's last try to get new transactions from {bank} failed"
            f" ({ago(failure, now)}). Plaid keeps trying on its own; if this lasts"
            " more than a day, sign in again."
        )
    elif success is None:
        out.state = "waiting"
        out.message = (
            f"Plaid hasn't finished getting transactions from {bank} yet."
            " Check again in a few minutes."
        )
    elif ends and ends - now < CONSENT_WARNING:
        out.state = "expiring"
        out.message = (
            f"{bank}'s permission for this app ends {ends:%b} {ends.day},"
            f" {ends.year}. Sign in again before then to keep syncing."
        )
    return out


def parse_time(value: str | None) -> datetime | None:
    """Plaid's ISO 8601 times, or the database's UTC 'YYYY-MM-DD HH:MM:SS'."""
    if not value:
        return None
    when = datetime.fromisoformat(value)
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def ago(when: datetime, now: datetime) -> str:
    seconds = (now - when).total_seconds()
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 90:
        return f"{round(minutes)} minutes ago"
    hours = minutes / 60
    if hours < 36:
        return f"{round(hours)} hours ago"
    return f"{round(hours / 24)} days ago"
