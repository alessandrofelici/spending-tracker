"""How often does Jev agree with your keyword rules?

    uv run python -m spending.jev_eval [--no-save]

Sends every merchant a keyword rule already categorized to Jev (redacted, same as an
import) and compares the answers with the rules. Payment rows are left out, so they
stay local. Answers are stored in jev_decisions with run = 'eval'; they are never
written to merchant_memory and don't change any transaction.
"""

import argparse
import statistics
import time
from collections import Counter

from . import db
from .classify import ask_jev, load_config

LOCAL_ONLY = {"Payments & Credits"}


def rule_merchants(conn) -> dict[str, tuple[str, str]]:
    """merchant -> (rule category, example description), from stored rule-matched rows."""
    rows = conn.execute(
        """SELECT merchant, category, MIN(description) AS description
           FROM transactions WHERE source = 'rule' GROUP BY merchant, category"""
    )
    return {
        r["merchant"]: (r["category"], r["description"])
        for r in rows
        if r["category"] not in LOCAL_ONLY
    }


def percentile(values: list[float], p: int) -> float:
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[p - 1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-save", action="store_true", help="Don't store answers in jev_decisions"
    )
    args = parser.parse_args()

    conn = db.connect()
    expected = rule_merchants(conn)
    if not expected:
        print("No rule-matched merchants yet. Import a statement first.")
        return
    categories, _ = load_config()

    print(f"Asking Jev about {len(expected)} rule-matched merchant(s)...")
    start = time.perf_counter()
    decisions = ask_jev({m: desc for m, (_, desc) in expected.items()}, categories)
    wall = time.perf_counter() - start

    answered = [d for d in decisions if d.status != "failed"]
    agree = [d for d in answered if d.choice == expected[d.merchant][0]]
    used = [d for d in answered if d.status == "ok"]
    used_agree = [d for d in used if d.choice == expected[d.merchant][0]]

    print(f"\nAgreement with rules: {len(agree)}/{len(answered)} answered merchants")
    print(
        f"Confident answers only (status ok, what an import would keep): "
        f"{len(used_agree)}/{len(used)}"
    )
    status = Counter(d.status for d in decisions)
    print("Status: " + ", ".join(f"{s} {n}" for s, n in status.most_common()))

    print("\nBy rule category        agree / total")
    per_cat: dict[str, list[bool]] = {}
    for d in answered:
        per_cat.setdefault(expected[d.merchant][0], []).append(
            d.choice == expected[d.merchant][0]
        )
    for cat, hits in sorted(per_cat.items(), key=lambda kv: -len(kv[1])):
        print(f"  {cat:<20} {sum(hits):>5} / {len(hits)}")

    misses = [d for d in answered if d.choice != expected[d.merchant][0]]
    if misses:
        print("\nDisagreements (rule -> Jev, confidence)")
        for d in sorted(misses, key=lambda d: -(d.confidence or 0)):
            print(
                f"  {d.merchant:<28} {expected[d.merchant][0]} -> {d.choice} "
                f"({d.confidence:.2f}, {d.status})"
            )

    latencies = [d.latency_ms for d in decisions]
    print(
        f"\nLatency per request: p50 {statistics.median(latencies):.0f} ms, "
        f"p95 {percentile(latencies, 95):.0f} ms, max {max(latencies):.0f} ms"
    )
    print(f"Wall time for {len(decisions)} requests: {wall:.1f}s")

    if not args.no_save:
        db.record_decisions(conn, [d._asdict() for d in decisions], run="eval")
        conn.commit()
        print("Saved to jev_decisions (run = 'eval').")


if __name__ == "__main__":
    main()
