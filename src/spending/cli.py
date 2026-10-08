"""Command line entry point: `uv run spend <command>`."""

import argparse
import subprocess
import sys
from pathlib import Path

from . import db
from .classify import (
    FALLBACK,
    add_category,
    categorize,
    classify_with_llm,
    confident,
    criteria_key,
    load_config,
    load_criteria,
)
from .parser import parse_csv, transaction_ids


def cmd_import(args) -> None:
    conn = db.connect()
    memory = db.get_memory(conn)
    for path in args.files:
        print(f"{path.name}:")
        txns = parse_csv(path)
        if not txns:
            print("  No transactions found. Is this an MSUFCU transactions CSV?")
            continue

        results, answers = categorize(
            txns,
            memory,
            use_llm=not args.no_llm,
            manual=db.get_manual_merchants(conn),
            unplaced=db.get_unplaced(conn),
        )
        rows = [
            {
                "id": tid,
                "month": t.month,
                "date": t.date.isoformat(),
                "description": t.description,
                "merchant": t.merchant,
                "amount": t.amount,
                "category": cat,
                "source": src,
                "statement": path.name,
            }
            for tid, t, (cat, src) in zip(
                transaction_ids(txns), txns, results, strict=True
            )
        ]

        if args.dry_run:
            for r in rows:
                print(
                    f"  {r['date']}  {r['amount']:>9.2f}  {r['category']:<18} [{r['source']}]  {r['description']}"
                )
            continue

        learned = confident(answers)
        for merchant, cat in learned.items():
            db.remember(conn, merchant, cat, "llm")
        memory.update(learned)
        db.save_answers(conn, answers)
        added = db.insert_transactions(conn, rows)
        conn.commit()
        months = sorted({r["month"] for r in rows})
        flagged = sum(r["source"] == "fallback" for r in rows)
        print(f"  {len(rows)} transactions ({months[0]} to {months[-1]}), {added} new.")
        if flagged:
            print(
                f"  {flagged} couldn't be categorized; run `spend review` to fix them."
            )


def cmd_review(args) -> None:
    conn = db.connect()
    categories, _ = load_config()
    if args.resort is not None:
        new = args.resort or None
        if new is not None and new not in categories:
            sys.exit(f"Unknown category. Choose from: {', '.join(categories)}")
        resort(conn, categories, new)
        return
    sql = "SELECT merchant, MIN(description) d, COUNT(*) n, SUM(amount) s FROM transactions WHERE source = 'fallback'"
    if args.all:
        sql = "SELECT merchant, MIN(description) d, COUNT(*) n, SUM(amount) s FROM transactions WHERE source != 'manual'"
    merchants = conn.execute(sql + " GROUP BY merchant ORDER BY s DESC").fetchall()
    if not merchants:
        print("Nothing to review.")
        return
    review_merchants(conn, categories, merchants)


def resort(conn, categories: list[str], new: str | None = None) -> None:
    """Ask Jev again now that the categories changed. Confident answers for
    uncategorized merchants are applied, and so are confident moves into `new`
    for merchants Jev had categorized itself (it picked without `new` before).
    Moves of merchants you set by hand are only proposed. Merchants a keyword
    rule matched are never sent."""
    merchants = conn.execute(
        """SELECT merchant, MIN(description) d, COUNT(*) n, SUM(amount) s,
                  MIN(category) category, MIN(source = 'fallback') fallback,
                  MAX(source = 'manual') manual
           FROM transactions GROUP BY merchant
           HAVING MAX(source = 'rule') = 0 ORDER BY s DESC"""
    ).fetchall()
    key = criteria_key(load_criteria(categories))
    answers = db.get_answers(conn)
    stale = {
        m["merchant"]: m["d"]
        for m in merchants
        # Without a new category only Other's answers are used.
        if (new or m["fallback"])
        and (
            (a := answers.get(m["merchant"])) is None
            or a["asked_with"] != key
            or a["outcome"] == "failed"
        )
    }
    if stale:
        print(
            f"Asking Jev about {len(stale)} merchant(s) with the current categories..."
        )
        try:
            db.save_answers(conn, classify_with_llm(stale, categories))
        except RuntimeError as e:
            sys.exit(str(e))
        conn.commit()
        answers = db.get_answers(conn)

    placed, left, moved, to_confirm, kept = [], [], [], [], 0
    for m in merchants:
        a = answers.get(m["merchant"])
        sure = a is not None and a["outcome"] == "confident" and a["asked_with"] == key
        if m["fallback"]:
            if sure:
                db.place_by_llm(conn, m["merchant"], a["choice"])
                placed.append(f"    {m['d']} -> {a['choice']}")
            else:
                left.append(m)
        elif sure and new and a["choice"] == new and m["category"] != new:
            if m["manual"]:
                to_confirm.append(m)
            else:
                db.place_by_llm(conn, m["merchant"], new)
                moved.append(f"    {m['d']} ({m['category']}) -> {new}")
        else:
            kept += 1
    conn.commit()

    # Every count below is a share of the line above it, so they add up.
    print(f"In '{FALLBACK}': {len(placed) + len(left)} merchant(s)")
    if placed:
        print(f"  {len(placed)} placed by Jev:")
        print("\n".join(placed))
    if left:
        silent = sum(
            (a := answers.get(m["merchant"])) is None or a["outcome"] == "failed"
            for m in left
        )
        why = f"Jev unsure about {len(left) - silent}" + (
            f", no answer for {silent}" if silent else ""
        )
        print(f"  {len(left)} still '{FALLBACK}' ({why}); review below")
    if new:
        total = len(moved) + len(to_confirm) + kept
        print(f"Already categorized: {total} merchant(s)")
        if moved:
            print(
                f"  {len(moved)} moved by Jev (it picked them before '{new}' existed):"
            )
            print("\n".join(moved))
        if to_confirm:
            print(
                f"  {len(to_confirm)} you set by hand that Jev thinks belong in"
                f" '{new}'; confirm below"
            )
        if kept:
            print(f"  {kept} keep their category")
    if to_confirm or left:
        print()
        review_merchants(conn, categories, to_confirm + left)


def review_merchants(conn, categories: list[str], merchants: list) -> None:
    """Ask for a category for each merchant row (merchant, d, n, s), offering
    Jev's stored answer as the default when it's a real category."""
    answers = db.get_answers(conn)
    for i, c in enumerate(categories, 1):
        print(f"  {i:>2}. {c}")
    print(
        "Number = set that category, Enter = accept the [suggestion] (or skip if"
        " there is none), + = new category, s = skip, q = quit.\n"
    )
    for m in merchants:
        current, source = conn.execute(
            "SELECT category, source FROM transactions WHERE merchant = ? LIMIT 1",
            (m["merchant"],),
        ).fetchone()
        suggestion, note = _suggest(answers.get(m["merchant"]), categories, current)
        if source == "manual":
            note = f" (set by you){note}"
        default = f" [{suggestion}]" if suggestion else ""
        prompt = (
            f"{m['d']}  ({m['n']}x, ${m['s']:.2f}, now: {current}{note}){default} > "
        )
        while (choice := _ask(prompt)) is not None and choice.startswith("+"):
            name = _new_category(choice[1:], categories)
            if name:
                n = db.set_merchant_category(conn, m["merchant"], name)
                conn.commit()
                print(f"  -> {name} ({n} transactions updated)\n")
                # Earlier answers were picked without the new category.
                resort(conn, load_config()[0], name)
                return
        if choice is None or choice.lower() == "q":
            break
        if choice.isdigit() and 1 <= int(choice) <= len(categories):
            picked = categories[int(choice) - 1]
        elif choice == "" and suggestion:
            picked = suggestion
        else:
            continue
        n = db.set_merchant_category(conn, m["merchant"], picked)
        conn.commit()
        print(f"  -> {picked} ({n} transactions updated)")


def _ask(prompt: str) -> str | None:
    """input(), stripped; None on Ctrl-D / Ctrl-C (treated like q)."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _new_category(name: str, categories: list[str]) -> str | None:
    """Ask for a new category's name (unless given) and description, and add it
    to categories.toml. None if cancelled."""
    taken = {c.casefold() for c in categories}
    name = name.strip()
    while not name or name.casefold() in taken:
        if name:
            print(f"  '{name}' already exists; pick it by number instead.")
        name = _ask("  New category name (Enter to cancel): ") or ""
        if not name:
            return None
    description = _ask(
        f"  What belongs in '{name}'? Jev reads this when choosing (optional): "
    )
    try:
        return add_category(name, description or "")
    except ValueError as e:
        print(f"  {e}")
        return None


def _suggest(answer, categories: list[str], current: str) -> tuple[str | None, str]:
    """(category to offer as the default, note for the prompt) from a jev_answers row."""
    if answer is None:
        return None, ""
    if answer["outcome"] == "failed":
        return None, ", Jev: no answer"
    choice, pct = answer["choice"], f"{answer['confidence']:.0%}"
    if choice not in categories or choice in (FALLBACK, current):
        return None, f", Jev: {choice} {pct}"
    return choice, f", Jev: {pct}"


def cmd_set(args) -> None:
    categories, _ = load_config()
    if args.category not in categories:
        sys.exit(f"Unknown category. Choose from: {', '.join(categories)}")
    conn = db.connect()
    matches = db.find_merchants(conn, args.merchant)
    if not matches:
        sys.exit(f"No merchant matches '{args.merchant}'.")
    if len(matches) > 1 and not args.all_matches:
        print(f"'{args.merchant}' matches {len(matches)} merchants:")
        for m in matches:
            print(f"  {m}")
        sys.exit("Use the exact name, or add --all-matches to set them all.")
    for m in matches:
        n = db.set_merchant_category(conn, m, args.category)
        print(f"{m} -> {args.category} ({n} transactions)")
    conn.commit()


def cmd_summary(args) -> None:
    conn = db.connect()
    month = (
        args.month
        or (conn.execute("SELECT MAX(month) FROM transactions").fetchone()[0])
    )
    if not month:
        print("No data yet. Import a statement first.")
        return
    rows = conn.execute(
        """SELECT category, COUNT(*) n, SUM(amount) total FROM transactions
           WHERE month = ? AND category != 'Payments & Credits'
           GROUP BY category ORDER BY total DESC""",
        (month,),
    ).fetchall()
    grand = sum(r["total"] for r in rows)
    print(f"Spending for {month}\n")
    for r in rows:
        share = r["total"] / grand * 100 if grand else 0
        print(
            f"  {r['category']:<20} {r['total']:>10.2f}  {share:5.1f}%  ({r['n']} txns)"
        )
    print(f"  {'Total':<20} {grand:>10.2f}")


def cmd_dashboard(args) -> None:
    app = Path(__file__).with_name("dashboard.py")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app)], check=False)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="spend", description="Credit card spending tracker"
    )
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("import", help="Import MSUFCU transaction CSV(s)")
    s.add_argument("files", nargs="+", type=Path)
    s.add_argument(
        "--no-llm", action="store_true", help="Only use rules and remembered merchants"
    )
    s.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without saving",
    )
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("review", help="Fix uncategorized merchants interactively")
    s.add_argument(
        "--all",
        action="store_true",
        help="Review every non-manual merchant, not just uncategorized",
    )
    s.add_argument(
        "--resort",
        nargs="?",
        const="",
        metavar="CATEGORY",
        help="Ask Jev again with the current categories: places what it can of 'Other'"
        " automatically, and proposes moves into CATEGORY (e.g. one you just added)",
    )
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("set", help="Set a merchant's category for all its transactions")
    s.add_argument("merchant", help="Merchant name or part of it, e.g. KROGER")
    s.add_argument("category")
    s.add_argument(
        "--all-matches",
        action="store_true",
        help="Apply to every merchant containing the text",
    )
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("summary", help="Print a month's spending by category")
    s.add_argument("--month")
    s.set_defaults(func=cmd_summary)

    s = sub.add_parser("dashboard", help="Open the charts dashboard")
    s.set_defaults(func=cmd_dashboard)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
