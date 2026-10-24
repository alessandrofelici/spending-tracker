"""Shared setup. Every test runs against a throwaway database, with the example
categories, no API keys and no network, so the suite can never read or change
your real data or reach Jev or OpenRouter."""

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path(tempfile.mkdtemp(prefix="spending-tests-"))

# Before spending is imported: db.py reads these when it loads, and load_dotenv
# never overrides a variable that's already set, so .env's real keys stay out.
os.environ["SPENDING_DB"] = str(SANDBOX / "data" / "spending.db")
os.environ["SPENDING_CATEGORIES"] = str(ROOT / "categories.example.toml")
for key in ("OPENROUTER_KEY", "OPENROUTER_API_KEY"):
    os.environ[key] = ""
os.environ["JEV_MIN_CONFIDENCE"] = "0.6"

import httpx  # noqa: E402
import pytest  # noqa: E402

from spending import db  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real HTTP request fails the test. Fakes use httpx.MockTransport instead."""

    def refuse(self, request):
        raise AssertionError(f"Tests must not reach the network: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "data" / "spending.db"


@pytest.fixture
def conn(db_path):
    conn = db.connect(db_path)
    yield conn
    conn.close()
