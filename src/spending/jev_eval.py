"""How often does Jev agree with keyword rules?

    uv run python -m spending.jev_eval [--no-save]

Imports don't use keyword rules; Jev categorizes every merchant. The `[rules]` in
categories.toml are kept only as labels to check Jev against: every stored merchant
a rule matches is sent to Jev (redacted, same as an import) and the answers are
compared with the rules. Payment rows are left out, so they stay local. Answers are stored in jev_evals; they never touch jev_answers (review
suggestions), merchant_memory or any transaction.
"""

import argparse
import statistics
import time
from collections import Counter

from . import db
from .classify import (
    PAYMENTS,
    classify_with_llm,
    is_payment,
    load_config,
    load_rules,
    match_rule,
)


def rule_merchants(conn, rules: dict[str, list[str]]) -> dict[str, tuple[str, str]]:
    """merchant -> (rule category, example description), for stored merchants a rule matches."""
    rows = conn.execute(
        """SELECT merchant, MIN(description) AS description
           FROM transactions WHERE source != 'payment' GROUP BY merchant"""
    )
    out = {}
    for r in rows:
        if is_payment(r["description"]):
            continue
        cat = match_rule(r["description"], rules)
        if cat and cat != PAYMENTS:
            out[r["merchant"]] = (cat, r["description"])
    return out


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
    categories = load_config()
    rules = load_rules(categories)
    if not rules:
        print("No [rules] in categories.toml. Copy them from categories.example.toml.")
        return
    expected = rule_merchants(conn, rules)
    if not expected:
        print("No stored merchants match a rule. Import a statement first.")
        return
    rule = {m: cat for m, (cat, _) in expected.items()}

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
