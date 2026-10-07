"""Read transactions from an MSUFCU "Download Transactions" CSV export.

    "Date","Amount","Fee","Interest/Penalty","Draft Number","New Balance","Description","Category","Note",,,,
    "04/20/2026","2.62","0.00","0.00","","240.09","Credit Card Ln Adv:STARBUCKS LIBRARY EAST LANSING MI","","",,,,

Only Date, Amount and Description are used. Balance and draft number are never stored or sent anywhere.
"""

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

PURCHASE_PREFIX_RE = re.compile(r"^Credit Card Ln Adv\s*:\s*", re.IGNORECASE)


@dataclass
class Transaction:
    date: date
    description: str
    amount: float  # positive = charge, negative = payment/refund

    @property
    def month(self) -> str:
        return self.date.strftime("%Y-%m")

    @property
    def merchant(self) -> str:
        return normalize_merchant(self.description)


def normalize_merchant(desc: str) -> str:
    """Reduce a description to a stable merchant key, e.g.
    'AMAZON.COM*2K4LM1 AMZN.COM/BILL WA' -> 'AMAZON COM AMZN COM'."""
    s = desc.upper()
    s = re.sub(r"[#*]\s*\w*\d\w*", " ", s)  # store numbers / order ids
    s = re.sub(r"\d+", " ", s)
    s = re.sub(r"[^A-Z&' ]+", " ", s)
    words = s.split()
    if len(words) > 1 and len(words[-1]) == 2:  # trailing state code
        words = words[:-1]
    return " ".join(words[:4]) or desc.strip().upper()


def clean_description(raw: str) -> str:
    return " ".join(PURCHASE_PREFIX_RE.sub("", raw).split())


def _parse_amount(raw: str) -> float:
    raw = raw.strip()
    negative = raw.startswith("(") or "-" in raw
    value = float(re.sub(r"[^\d.]", "", raw) or 0)
    return -value if negative else value


def parse_csv(path: Path) -> list[Transaction]:
    txns = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = f.readlines()
    # The export starts with an account line (e.g. "0001234567 L50 CREDITLINE"); skip to the header.
    start = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith('"Date"')), None
    )
    if start is None:
        return []
    for row in csv.DictReader(lines[start:]):
        if not (row.get("Date") or "").strip():
            continue
        try:
            d = datetime.strptime(row["Date"].strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        amount = _parse_amount(row.get("Amount") or "0")
        desc = clean_description(row.get("Description") or "")
        if amount == 0 or not desc:
            continue
        txns.append(Transaction(d, desc, amount))
    return txns


def transaction_ids(txns: list[Transaction]) -> list[str]:
    """Stable ids so overlapping downloads don't create duplicates;
    identical same-day charges get distinct ids via an occurrence counter."""
    seen: dict[tuple, int] = {}
    ids = []
    for t in txns:
        key = (t.date.isoformat(), t.description, f"{t.amount:.2f}")
        n = seen.get(key, 0)
        seen[key] = n + 1
        ids.append(hashlib.sha256("|".join((*key, str(n))).encode()).hexdigest()[:20])
    return ids
