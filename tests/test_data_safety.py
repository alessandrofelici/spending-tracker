"""Keeping your data safe: what's private on disk, what stays on this machine and
what may leave it. Run after any change to storage or categorizing."""

import json
import os
import re
import shutil
import stat
import subprocess
import tomllib
from argparse import Namespace
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml

from spending import classify, cli, db
from spending.parser import Transaction

ROOT = Path(__file__).resolve().parents[1]


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- The suite itself ---------------------------------------------------------


def test_tests_never_touch_your_real_files_or_keys():
    assert db.DB_PATH != ROOT / "data" / "spending.db"
    assert ROOT not in db.DB_PATH.parents
    assert classify.CATEGORIES_PATH == ROOT / "categories.example.toml"
    assert not os.environ["OPENROUTER_KEY"]
    assert not os.environ["OPENROUTER_API_KEY"]


# --- Private on disk ----------------------------------------------------------


def test_database_is_created_owner_only(tmp_path):
    path = tmp_path / "data" / "spending.db"
    db.connect(path).close()
    assert mode(path.parent) == 0o700
    assert mode(path) == 0o600


CSV = """\
"0009990001 L50 CREDITLINE"
"Date","Amount","Fee","Interest/Penalty","Draft Number","New Balance","Description","Category","Note",,,,
"03/14/2026","25.00","0.00","0.00","778899","4321.09","Credit Card Ln Adv:PETSMART 1234 LANSING MI","","",,,,
"03/20/2026","-300.00","0.00","0.00","","4021.09","ACH Pmt:0005550123","","",,,,
"""


def test_csv_import_keeps_only_date_amount_and_description(
    monkeypatch, db_path, tmp_path
):
    path = tmp_path / "export.csv"
    path.write_text(CSV)
    connect = db.connect
    monkeypatch.setattr(db, "connect", lambda: connect(db_path))
    cli.cmd_import(Namespace(files=[path], no_llm=True, dry_run=False))

    conn = connect(db_path)
    stored = [tuple(r) for r in conn.execute("SELECT * FROM transactions")]
    conn.close()
    values = {v for r in stored for v in r}
    assert len(stored) == 2
    assert not values & {4321.09, 4021.09, "778899"}  # balance, draft number
    assert b"0009990001" not in db_path.read_bytes()  # the account line


# --- What reaches Jev (OpenRouter) ---------------------------------------------


@pytest.mark.parametrize(
    ("description", "sent"),
    [
        ("SQ *SHOP 4412 GRAND RAPIDS MI", "SQ *SHOP #### GRAND RAPIDS MI"),
        ("ACH PMT 0001234567 REF A1B2C3999", "ACH PMT #### REF ####"),
        ("CALL 800-555-0100 FOR HELP", "CALL #### FOR HELP"),
        ("7-ELEVEN 12 LANSING MI", "7-ELEVEN 12 LANSING MI"),  # short numbers stay
    ],
)
def test_numbers_are_masked_before_anything_is_sent(description, sent):
    assert classify.redact(description) == sent


def test_only_new_merchants_are_asked_about(monkeypatch):
    asked = []
    monkeypatch.setattr(
        classify, "classify_with_llm", lambda d, _cats: asked.append(dict(d)) or {}
    )

    def t(desc: str, amount: float = 10.0) -> Transaction:
        return Transaction(date(2026, 3, 14), desc, amount)

    txns = [
        t("ACH PMT 0005550123", -300.0),  # card payment: never sent
        t("NORTHWIND GROCERY 0042 LANSING MI"),  # remembered
        t("BLUE HERON CAFE EAST LANSING MI"),  # set by you
        t("QUIET PINE BOOKS 0007 OKEMOS MI"),  # Jev already couldn't place it
        t("PETSMART 1234 LANSING MI"),
        t("PETSMART 1234 LANSING MI"),  # same merchant: asked once
    ]
    memory = {txns[1].merchant: "Groceries", txns[2].merchant: "Dining"}
    key = classify.criteria_key(classify.load_criteria(classify.load_config()))
    results, _ = classify.categorize(
        txns, memory, manual={txns[2].merchant}, unplaced={txns[3].merchant: key}
    )

    assert asked == [{txns[4].merchant: "PETSMART 1234 LANSING MI"}]
    assert [source for _, source in results] == [
        "payment",
        "memory",
        "manual",
        "fallback",
        "fallback",
        "fallback",
    ]


def test_descriptions_are_redacted_on_the_way_to_jev(monkeypatch):
    sent = []
    monkeypatch.setenv("OPENROUTER_KEY", "test-key")
    monkeypatch.setattr(
        classify,
        "_decide",
        lambda _client, _key, _model, desc, _criteria: (
            sent.append(desc) or ("Shopping", 0.9)
        ),
    )
    classify.classify_with_llm(
        {"SQ SHOP": "SQ *SHOP 4412 GRAND RAPIDS MI 616-555-0199"},
        classify.load_config(),
    )
    assert sent == ["SQ *SHOP #### GRAND RAPIDS MI ####"]


class FakeJev:
    """OpenRouter's Decisions API, always answering `choice`; keeps each request."""

    def __init__(self, choice: str | None):
        self.choice = choice
        self.requests: list[dict] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        answer = {"choice": self.choice, "confidence": 0.9}
        return httpx.Response(200, json={"answers": {"category": answer}})


def test_jev_sees_only_the_description_with_zero_retention():
    criteria = classify.load_criteria(classify.load_config())
    fake = FakeJev("Shopping")
    with fake.client() as client:
        answer = classify._decide(client, "test-key", "test-model", "SQ ####", criteria)
    assert answer == ("Shopping", 0.9)
    [body] = fake.requests
    assert body["state"] == {"transaction_description": "SQ ####"}
    assert body["provider"] == {"zdr": True, "data_collection": "deny"}


@pytest.mark.parametrize("choice", ["Crypto", None, ""])
def test_jev_can_only_answer_with_your_categories(choice):
    criteria = classify.load_criteria(classify.load_config())
    with FakeJev(choice).client() as client:
        answer = classify._decide(client, "test-key", "test-model", "SQ ####", criteria)
    assert answer == (None, 0.0)


# --- Guards in the repo -------------------------------------------------------

in_git = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="not a git checkout",
)


@in_git
@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "categories.toml",
        "statements/export.csv",
        "data/spending.db",
        "somewhere/else/export.csv",
        "statement.pdf",
        "backup.db",
    ],
)
def test_bank_data_is_git_ignored(path):
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path], cwd=ROOT, check=False
    )
    assert ignored.returncode == 0, f"{path} isn't git-ignored"


def hook(hook_id: str) -> dict:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    return next(h for r in config["repos"] for h in r["hooks"] if h["id"] == hook_id)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "categories.toml",
        "statements/export.csv",
        "data/spending.db",
        "backup/old.db",
        "statement.pdf",
    ],
)
def test_commit_hook_blocks_bank_data(path):
    assert re.search(hook("no-bank-data")["files"], path)


@pytest.mark.parametrize(
    "path",
    [
        "src/spending/db.py",
        "categories.example.toml",
        ".env.example",
        "tests/conftest.py",
    ],
)
def test_commit_hook_lets_code_through(path):
    assert not re.search(hook("no-bank-data")["files"], path)


def test_commit_hook_blocks_openrouter_keys():
    made_up = "sk-or-v1-" + "0" * 64  # built here so this file doesn't trip the hook
    assert re.search(hook("no-openrouter-key")["entry"], f"OPENROUTER_KEY={made_up}")


def test_dashboard_only_listens_on_this_machine():
    path = ROOT / "src" / "spending" / ".streamlit" / "config.toml"
    config = tomllib.loads(path.read_text())
    assert config["server"]["address"] == "127.0.0.1"
    assert set(config["server"]["allowedHosts"]) <= {"localhost", "127.0.0.1"}
    assert config["browser"]["gatherUsageStats"] is False
