"""A stand-in for Plaid's API, and made-up transactions to feed it.

Nothing here is real bank data: the merchants, amounts and ids are invented, in the
shape MSUFCU's data takes through Plaid."""

import json
from collections.abc import Sequence

import httpx

from spending.plaid_source import Plaid

CARD = {
    "account_id": "acct-card",
    "name": "Example Visa",
    "type": "credit",
    "subtype": "credit card",
}
CREDIT_LINE = {  # how some credit unions report the card
    "account_id": "acct-line",
    "name": "Example Credit Line",
    "type": "loan",
    "subtype": "line of credit",
}
CHECKING = {
    "account_id": "acct-checking",
    "name": "Example Checking",
    "type": "depository",
    "subtype": "checking",
}

ACCESS_TOKEN = "access-sandbox-0000-test"


def wrapped(merchant: str, day: str = "03/14/26") -> str:
    """A description as MSUFCU sends it through Plaid, around what the CSV shows."""
    return f"Credit Card Advance {merchant} Date {day} 5550001 0042 Card #0000"


def txn(
    tid: str,
    day: str,
    merchant: str,
    amount: float,
    account: str = CARD["account_id"],
    pending: bool = False,
    wrap: bool = True,
) -> dict:
    """A Plaid transaction, as /transactions/sync returns it. wrap=False for rows
    MSUFCU doesn't wrap, like card payments."""
    return {
        "transaction_id": tid,
        "account_id": account,
        "date": day,
        "original_description": wrapped(merchant) if wrap else merchant,
        "name": merchant.title(),
        "amount": amount,
        "pending": pending,
    }


class FakePlaid:
    """Plaid's API as far as the app uses it, behind httpx.MockTransport.

    Queue responses with page() and fail(); every request is kept in .requests
    so tests can check what the app sent."""

    def __init__(self) -> None:
        self.accounts = [CARD, CHECKING]
        self.requests: list[tuple[str, dict]] = []
        self.queued: dict[str, list[httpx.Response]] = {}
        self.removed: list[str] = []  # access tokens revoked through /item/remove
        self._logins = 0

    def client(self) -> Plaid:
        return Plaid(
            "test-client-id", "test-secret", "sandbox", httpx.MockTransport(self.handle)
        )

    def page(
        self,
        added: Sequence[dict] = (),
        modified: Sequence[dict] = (),
        removed: Sequence[str] = (),
        next_cursor: str = "cursor-1",
        has_more: bool = False,
        status: str = "HISTORICAL_UPDATE_COMPLETE",
    ) -> None:
        """Queue one page of /transactions/sync."""
        self._queue(
            "/transactions/sync",
            httpx.Response(
                200,
                json={
                    "added": list(added),
                    "modified": list(modified),
                    "removed": [{"transaction_id": i} for i in removed],
                    "accounts": self.accounts,
                    "next_cursor": next_cursor,
                    "has_more": has_more,
                    "transactions_update_status": status,
                },
            ),
        )

    def fail(
        self,
        path: str,
        code: str,
        message: str = "",
        status: int = 400,
        display: str | None = None,
    ) -> None:
        """Queue an error response, shaped like Plaid's."""
        self._queue(
            path,
            httpx.Response(
                status,
                json={
                    "error_code": code,
                    "error_message": message or code,
                    "display_message": display,
                },
            ),
        )

    def bodies(self, path: str) -> list[dict]:
        return [body for p, body in self.requests if p == path]

    def _queue(self, path: str, response: httpx.Response) -> None:
        self.queued.setdefault(path, []).append(response)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content)
        self.requests.append((path, body))
        if self.queued.get(path):
            return self.queued[path].pop(0)
        if path == "/transactions/sync":
            raise AssertionError(
                "A sync page was requested that the test didn't queue."
            )
        if path == "/item/public_token/exchange":
            self._logins += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"access-sandbox-login-{self._logins}",
                    "item_id": f"item-{self._logins}",
                },
            )
        if path == "/item/remove":
            self.removed.append(body["access_token"])
            return httpx.Response(200, json={"request_id": "req"})
        if path == "/link/token/create":
            return httpx.Response(200, json={"link_token": "link-sandbox-test"})
        raise AssertionError(f"Unexpected Plaid call: {path}")
