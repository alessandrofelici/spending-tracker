"""Shared setup. Every test runs against a throwaway database and token file, with
the example categories, no API keys and no network, so the suite can never read
or change your real data or reach Jev, OpenRouter or Plaid."""

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path(tempfile.mkdtemp(prefix="spending-tests-"))

# Before spending is imported: db.py reads these when it loads, and load_dotenv
# never overrides a variable that's already set, so .env's real keys stay out.
os.environ["SPENDING_DB"] = str(SANDBOX / "data" / "spending.db")
os.environ["SPENDING_CATEGORIES"] = str(ROOT / "categories.example.toml")
for key in ("OPENROUTER_KEY", "OPENROUTER_API_KEY", "PLAID_CLIENT_ID", "PLAID_SECRET"):
    os.environ[key] = ""
os.environ["PLAID_ENV"] = "sandbox"
os.environ["JEV_MIN_CONFIDENCE"] = "0.6"

import httpx  # noqa: E402
import keyring  # noqa: E402
import pytest  # noqa: E402
from fakes import FakePlaid  # noqa: E402
from keyring.errors import NoKeyringError, PasswordDeleteError  # noqa: E402

from spending import db, plaid_source  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real HTTP request fails the test. Fakes use httpx.MockTransport instead."""

    def refuse(self, request):
        raise AssertionError(f"Tests must not reach the network: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def keyring_store(monkeypatch, tmp_path) -> dict:
    """An in-memory OS keyring, and a token file inside this test's folder."""
    store: dict[tuple[str, str], str] = {}

    def delete(service, user):
        if store.pop((service, user), None) is None:
            raise PasswordDeleteError("No such password")

    monkeypatch.setattr(
        keyring, "set_password", lambda s, u, p: store.update({(s, u): p})
    )
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    monkeypatch.setattr(keyring, "delete_password", delete)
    monkeypatch.setattr(
        plaid_source, "TOKENS_PATH", tmp_path / "data" / "plaid-tokens.json"
    )
    return store


@pytest.fixture
def no_keyring(monkeypatch):
    """No OS keyring, as on WSL: tokens fall back to the private file."""

    def missing(*_args):
        raise NoKeyringError("No recommended backend was available")

    for name in ("set_password", "get_password", "delete_password"):
        monkeypatch.setattr(keyring, name, missing)


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "data" / "spending.db"


@pytest.fixture
def conn(db_path):
    conn = db.connect(db_path)
    yield conn
    conn.close()


@pytest.fixture
def fake_plaid() -> FakePlaid:
    return FakePlaid()
