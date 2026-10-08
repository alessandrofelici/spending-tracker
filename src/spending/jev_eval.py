"""How often does Jev agree with your keyword rules?

    uv run python -m spending.jev_eval [--no-save]

Sends every merchant a keyword rule already categorized to Jev (redacted, same as an
import) and compares the answers with the rules. Payment rows are left out, so they
stay local. Answers are stored in jev_evals; they never touch jev_answers (review
suggestions), merchant_memory or any transaction.
"""

import argparse
import statistics
import time
from collections import Counter

from . import db
from .classify import classify_with_llm, load_config

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
        "--no-save", action="store_true", help="Don't store answers in jev_evals"
    )
    args = parser.parse_args()

    conn = db.connect()
    expected = rule_merchants(conn)
    if not expected:
        print("No rule-matched merchants yet. Import a statement first.")
        return
    rule = {m: cat for m, (cat, _) in expected.items()}
    categories, _ = load_config()

    print(f"Asking Jev about {len(expected)} rule-matched merchant(s)...")
    start = time.perf_counter()
    answers = classify_with_llm(
        {m: desc for m, (_, desc) in expected.items()}, categories
    )
    wall = time.perf_counter() - start

    answered = {m: a for m, a in answers.items() if a.outcome != "failed"}
    agree = [m for m, a in answered.items() if a.choice == rule[m]]
    used = {m: a for m, a in answered.items() if a.outcome == "confident"}
    used_agree = [m for m, a in used.items() if a.choice == rule[m]]

    print(f"\nAgreement with rules: {len(agree)}/{len(answered)} answered merchants")
    print(
        f"Confident answers only (what an import would keep): "
        f"{len(used_agree)}/{len(used)}"
    )
    outcomes = Counter(a.outcome for a in answers.values())
    print("Outcome: " + ", ".join(f"{s} {n}" for s, n in outcomes.most_common()))

    print("\nBy rule category        agree / total")
    per_cat: dict[str, list[bool]] = {}
    for m, a in answered.items():
        per_cat.setdefault(rule[m], []).append(a.choice == rule[m])
    for cat, hits in sorted(per_cat.items(), key=lambda kv: -len(kv[1])):
        print(f"  {cat:<20} {sum(hits):>5} / {len(hits)}")

    misses = {m: a for m, a in answered.items() if a.choice != rule[m]}
    if misses:
        print("\nDisagreements (rule -> Jev, confidence)")
        for m, a in sorted(misses.items(), key=lambda kv: -kv[1].confidence):
            print(
                f"  {m:<28} {rule[m]} -> {a.choice} ({a.confidence:.2f}, {a.outcome})"
            )

    latencies = [a.latency_ms for a in answers.values()]
    print(
        f"\nLatency per request: p50 {statistics.median(latencies):.0f} ms, "
        f"p95 {percentile(latencies, 95):.0f} ms, max {max(latencies):.0f} ms"
    )
    print(f"Wall time for {len(answers)} requests: {wall:.1f}s")

    if not args.no_save:
        db.save_evals(conn, answers, rule)
        conn.commit()
        print("Saved to jev_evals.")


if __name__ == "__main__":
    main()
