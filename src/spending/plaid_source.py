"""Pull transactions from your bank through Plaid (documents/plaid-ideas.md).

The dashboard calls sync() when it opens: /transactions/sync returns what changed
at the bank since the last call, and new rows go through the same categorize ->
store steps as `spend import`, so your choices, rules, remembered merchants and
Jev all apply.

Safety measures:
  * PLAID_CLIENT_ID and PLAID_SECRET stay in .env. The access token Plaid issues
    for your bank login is a credential too: it's kept in the OS keyring, or where
    there is none (e.g. WSL) in a file next to the database that only you can
    read. It never reaches the browser, the database or a log.
  * Only credit accounts are stored (the card, or a credit line). Checking and
    savings would double count card payments (documents/checking-ideas.md).
  * Pending transactions are skipped: they can still change amount or disappear.
  * Jev sees what it sees on a CSV import: one redacted description per new merchant.
"""

import contextlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date

import httpx
import keyring
from keyring.errors import KeyringError

from . import db
from .parser import Transaction, clean_description
from .pipeline import categorize_rows, save_rows

HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}
KEYRING_SERVICE = "spending-tracker-plaid"
TOKENS_PATH = db.DB_PATH.with_name("plaid-tokens.json")
STATEMENT = "Plaid"  # what the statement column says for rows from Plaid
# The most history Plaid allows; rows up to the cutover date are skipped anyway.
DAYS_REQUESTED = 730
# Fixed by signing in to the bank again. Link's update mode keeps the same bank
# login, which matters on the Trial plan: removing a login never frees its slot.
SIGN_IN_AGAIN = {"ITEM_LOGIN_REQUIRED", "PASSWORD_RESET_REQUIRED", "ACCESS_NOT_GRANTED"}
# The bank login no longer exists at Plaid; only a new one helps.
LOGIN_GONE = {"ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"}


class PlaidError(Exception):
    """An error response from Plaid. str() is a message fit to show the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Changes:
    """What /transactions/sync reported, every page together."""

    cursor: str = ""
    added: list[dict] = field(default_factory=list)
    modified: list[dict] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)  # transaction ids
    accounts: dict[str, dict] = field(default_factory=dict)  # account_id -> account
    status: str = ""  # transactions_update_status, e.g. NOT_READY


@dataclass
class SyncResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    flagged: int = 0  # new rows left as Other for `spend review`
    skipped_accounts: list[str] = field(default_factory=list)  # not credit, not stored
    status: str = ""  # Plaid's transactions_update_status


class Plaid:
    def __init__(
        self,
        client_id: str,
        secret: str,
        env: str = "sandbox",
        transport: httpx.BaseTransport | None = None,
    ):
        if env not in HOSTS:
            raise ValueError(
                f"PLAID_ENV must be one of {', '.join(HOSTS)}, not {env!r}."
            )
        self.env = env
        self._auth = {"client_id": client_id, "secret": secret}
        self._transport = transport

    @classmethod
    def from_env(cls) -> "Plaid | None":
        """None when bank sync isn't set up: no Plaid keys in .env."""
        client_id = os.environ.get("PLAID_CLIENT_ID")
        secret = os.environ.get("PLAID_SECRET")
        if not (client_id and secret):
            return None
        return cls(client_id, secret, os.environ.get("PLAID_ENV", "sandbox"))

    def _post(self, path: str, body: dict) -> dict:
        with httpx.Client(
            base_url=HOSTS[self.env], transport=self._transport, timeout=60
        ) as http:
            resp = http.post(path, json=self._auth | body)
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise
        if resp.is_error:
            raise PlaidError(
                data.get("error_code", ""),
                data.get("display_message")
                or data.get("error_message")
                or resp.reason_phrase,
            )
        return data

    def link_token(self, access_token: str | None = None) -> str:
        """A token that opens Plaid Link: to add a bank login, or with
        access_token, to sign in to an existing one again (update mode)."""
        body: dict = {
            "client_name": "Spending Tracker",
            "language": "en",
            "country_codes": ["US"],
            # A single local user; Plaid wants a stable id without personal details.
            "user": {"client_user_id": "local-user"},
        }
        if access_token:
            body["access_token"] = access_token
        else:
            body["products"] = ["transactions"]
            body["transactions"] = {"days_requested": DAYS_REQUESTED}
        return self._post("/link/token/create", body)["link_token"]

    def exchange(self, public_token: str) -> tuple[str, str]:
        """Link's one-time public token -> (access_token, item_id)."""
        data = self._post("/item/public_token/exchange", {"public_token": public_token})
        return data["access_token"], data["item_id"]

    def changes(self, access_token: str, cursor: str | None) -> Changes:
        """Everything that changed since cursor, following has_more to the end."""
        for _attempt in range(3):
            try:
                return self._changes(access_token, cursor)
            except PlaidError as e:
                # The data changed while paging. Plaid says to restart from the
                # first page's cursor, not to retry the failed page.
                if e.code != "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                    raise
        raise PlaidError(
            "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION",
            "Your bank's transactions kept changing during the sync. Try again in a minute.",
        )

    def _changes(self, access_token: str, cursor: str | None) -> Changes:
        out = Changes(cursor=cursor or "")
        while True:
            body: dict = {
                "access_token": access_token,
                "count": 500,
                # The bank's own description, the closest to the CSV export, so
                # keyword rules and remembered merchants keep matching.
                "options": {"include_original_description": True},
            }
            if out.cursor:
                body["cursor"] = out.cursor
            page = self._post("/transactions/sync", body)
            out.added += page["added"]
            out.modified += page["modified"]
            out.removed += [r["transaction_id"] for r in page["removed"]]
            out.accounts |= {a["account_id"]: a for a in page["accounts"]}
            # Empty while Plaid is still fetching a new login's data; stay put.
            out.cursor = page["next_cursor"] or out.cursor
            out.status = page.get("transactions_update_status", "")
            if not page["has_more"]:
                return out


def save_token(item_id: str, token: str) -> str:
    """Store a bank login's access token. Returns where it went, to tell the user."""
    try:
        keyring.set_password(KEYRING_SERVICE, item_id, token)
        return "the OS keyring"
    except KeyringError:  # no keyring here, e.g. WSL without a desktop session
        tokens = _read_tokens()
        tokens[item_id] = token
        _write_tokens(tokens)
        return str(TOKENS_PATH)


def load_token(item_id: str) -> str | None:
    with contextlib.suppress(KeyringError):
        if token := keyring.get_password(KEYRING_SERVICE, item_id):
            return token
    return _read_tokens().get(item_id)


def delete_token(item_id: str) -> None:
    with contextlib.suppress(KeyringError):  # including "there was none"
        keyring.delete_password(KEYRING_SERVICE, item_id)
    tokens = _read_tokens()
    if tokens.pop(item_id, None) is not None:
        _write_tokens(tokens)


def _read_tokens() -> dict[str, str]:
    try:
        return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _write_tokens(tokens: dict[str, str]) -> None:
    TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Created owner-only, so the token is never readable by others, not even briefly.
    fd = os.open(TOKENS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(tokens, f)
    os.chmod(TOKENS_PATH, 0o600)  # in case it already existed with wider permissions


def link(
    conn: sqlite3.Connection, plaid: Plaid, public_token: str, institution: str | None
) -> str:
    """Finish signing in: swap Link's public token for an access token, store it
    and record the bank login, replacing any earlier one (one bank for now).
    Returns where the token was stored."""
    access_token, item_id = plaid.exchange(public_token)
    for old in db.get_plaid_items(conn):
        delete_token(old["item_id"])
        db.remove_plaid_item(conn, old["item_id"])
    where = save_token(item_id, access_token)
    db.add_plaid_item(conn, item_id, institution)
    conn.commit()
    return where


_lock = threading.Lock()  # Streamlit runs each browser tab in its own thread


def sync(
    conn: sqlite3.Connection,
    plaid: Plaid,
    item_id: str,
    access_token: str,
    use_llm: bool = True,
) -> SyncResult:
    """Apply what changed at the bank since this bank login's last sync."""
    with _lock:
        item = conn.execute(
            "SELECT cursor, cutover FROM plaid_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        changes = plaid.changes(access_token, item["cursor"])
        credit = {i for i, a in changes.accounts.items() if _is_credit(a)}
        result = SyncResult(
            status=changes.status,
            skipped_accounts=sorted(
                a["name"] for i, a in changes.accounts.items() if i not in credit
            ),
        )

        def wanted(t: dict) -> bool:
            return (
                t["account_id"] in credit
                and not t["pending"]
                # Up to the cutover, the CSV import already has it.
                and (item["cutover"] is None or t["date"] > item["cutover"])
            )

        new = [t for t in changes.added if wanted(t)]
        for t in filter(wanted, changes.modified):
            txn = _transaction(t)
            if txn and db.update_plaid_transaction(
                conn, _fields(t["transaction_id"], txn)
            ):
                result.updated += 1
            else:  # not stored yet, e.g. it was skipped while pending
                new.append(t)
        result.removed = db.delete_plaid_transactions(conn, changes.removed)

        found = [(t["transaction_id"], txn) for t in new if (txn := _transaction(t))]
        if found:
            ids, txns = zip(*found, strict=True)
            rows, answers = categorize_rows(
                conn, txns, ids, STATEMENT, source_kind="plaid", use_llm=use_llm
            )
            result.added = save_rows(conn, rows, answers)
            result.flagged = sum(r["source"] == "fallback" for r in rows)
        db.save_plaid_cursor(conn, item_id, changes.cursor)
        conn.commit()
        return result


def _is_credit(account: dict) -> bool:
    """The card itself, or a credit line (how some credit unions report the card)."""
    return account["type"] == "credit" or (
        account["type"] == "loan" and account.get("subtype") == "line of credit"
    )


def _transaction(t: dict) -> Transaction | None:
    """A Plaid transaction as the CSV parser would have read it, or None where an
    import would skip it (no amount or no description)."""
    desc = clean_description(t.get("original_description") or t.get("name") or "")
    amount = float(t["amount"])  # Plaid's sign matches ours: positive = money out
    if amount == 0 or not desc:
        return None
    # `date` rather than authorized_date: every posted transaction has one.
    return Transaction(date.fromisoformat(t["date"]), desc, amount)


def _fields(tid: str, t: Transaction) -> dict:
    return {
        "id": tid,
        "month": t.month,
        "date": t.date.isoformat(),
        "description": t.description,
        "merchant": t.merchant,
        "amount": t.amount,
    }
