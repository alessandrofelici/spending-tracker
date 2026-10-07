"""Command line entry point: `uv run spend <command>`."""

import argparse
import subprocess
import sys
from pathlib import Path

from . import db
from .classify import categorize, load_config
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

        results, learned = categorize(txns, memory, use_llm=not args.no_llm, manual=db.get_manual_merchants(conn))
        rows = [
            {
                "id": tid, "month": t.month, "date": t.date.isoformat(), "description": t.description,
                "merchant": t.merchant, "amount": t.amount, "category": cat, "source": src,
                "statement": path.name,
            }
            for tid, t, (cat, src) in zip(transaction_ids(txns), txns, results)
        ]

        if args.dry_run:
            for r in rows:
                print(f"  {r['date']}  {r['amount']:>9.2f}  {r['category']:<18} [{r['source']}]  {r['description']}")
            continue

        for merchant, cat in learned.items():
            db.remember(conn, merchant, cat, "llm")
        memory.update(learned)
        added = db.insert_transactions(conn, rows)
        conn.commit()
        months = sorted({r["month"] for r in rows})
        flagged = sum(r["source"] == "fallback" for r in rows)
        print(f"  {len(rows)} transactions ({months[0]} to {months[-1]}), {added} new.")
        if flagged:
            print(f"  {flagged} couldn't be categorized; run `spend review` to fix them.")


def cmd_review(args) -> None:
    conn = db.connect()
    categories, _ = load_config()
    sql = "SELECT merchant, MIN(description) d, COUNT(*) n, SUM(amount) s FROM transactions WHERE source = 'fallback'"
    if args.all:
        sql = "SELECT merchant, MIN(description) d, COUNT(*) n, SUM(amount) s FROM transactions WHERE source != 'manual'"
    merchants = conn.execute(sql + " GROUP BY merchant ORDER BY s DESC").fetchall()
    if not merchants:
        print("Nothing to review.")
        return

    for i, c in enumerate(categories, 1):
        print(f"  {i:>2}. {c}")
    print("Enter a number to set the category, Enter to skip, q to quit.\n")
    for m in merchants:
        current = conn.execute("SELECT category FROM transactions WHERE merchant = ? LIMIT 1", (m["merchant"],)).fetchone()[0]
        choice = input(f"{m['d']}  ({m['n']}x, ${m['s']:.2f}, now: {current}) > ").strip()
        if choice.lower() == "q":
            break
        if choice.isdigit() and 1 <= int(choice) <= len(categories):
            n = db.set_merchant_category(conn, m["merchant"], categories[int(choice) - 1])
            conn.commit()
            print(f"  -> {categories[int(choice) - 1]} ({n} transactions updated)")


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
    month = args.month or (conn.execute("SELECT MAX(month) FROM transactions").fetchone()[0])
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
        print(f"  {r['category']:<20} {r['total']:>10.2f}  {share:5.1f}%  ({r['n']} txns)")
    print(f"  {'Total':<20} {grand:>10.2f}")


def cmd_dashboard(args) -> None:
    app = Path(__file__).with_name("dashboard.py")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app)], check=False)


def main() -> None:
    p = argparse.ArgumentParser(prog="spend", description="Credit card spending tracker")
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("import", help="Import MSUFCU transaction CSV(s)")
    s.add_argument("files", nargs="+", type=Path)
    s.add_argument("--no-llm", action="store_true", help="Only use rules and remembered merchants")
    s.add_argument("--dry-run", action="store_true", help="Show what would be imported without saving")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("review", help="Fix uncategorized merchants interactively")
    s.add_argument("--all", action="store_true", help="Review every non-manual merchant, not just uncategorized")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("set", help="Set a merchant's category for all its transactions")
    s.add_argument("merchant", help="Merchant name or part of it, e.g. KROGER")
    s.add_argument("category")
    s.add_argument("--all-matches", action="store_true", help="Apply to every merchant containing the text")
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
