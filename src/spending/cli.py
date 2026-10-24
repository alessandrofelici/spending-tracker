"""Command line entry point: `uv run spend <command>`."""

import argparse
import subprocess
import sys
from pathlib import Path

import httpx

from . import db, plaid_source
from .classify import (
    CATEGORIES_PATH,
    FALLBACK,
    add_category,
    classify_with_llm,
    criteria_key,
    load_config,
    load_criteria,
    openrouter_key,
)
from .parser import parse_csv, transaction_ids
from .pipeline import categorize_rows, save_rows


def cmd_import(args) -> None:
    conn = db.connect()
    # Without a key, new merchants go to review instead of Jev, like bank sync.
    use_llm = openrouter_key() is not None
    if not use_llm:
        print("No OPENROUTER_KEY in .env: new merchants are left for `spend review`.")
    for path in args.files:
        print(f"{path.name}:")
        txns = parse_csv(path)
        if not txns:
            print("  No transactions found. Is this an MSUFCU transactions CSV?")
            continue

        rows, answers = categorize_rows(
            conn, txns, transaction_ids(txns), path.name, use_llm=use_llm
        )
        added = save_rows(conn, rows, answers)
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
    categories = load_config()
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
    uncategorized merchants are applied (with `new`, only answers for `new`; the
    rest become review suggestions), and so are confident moves into `new`
    for merchants Jev had categorized itself (it picked without `new` before).
    Moves of merchants you set by hand are only proposed. Payment rows are
    never sent."""
    merchants = conn.execute(
        """SELECT merchant, MIN(description) d, COUNT(*) n, SUM(amount) s,
                  MIN(category) category, MIN(source = 'fallback') fallback,
                  MAX(source = 'manual') manual
           FROM transactions GROUP BY merchant
           HAVING MAX(source = 'payment') = 0 ORDER BY s DESC"""
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

    placed, suggested, left, moved, to_confirm, kept = [], [], [], [], [], 0
    for m in merchants:
        a = answers.get(m["merchant"])
        sure = a is not None and a["outcome"] == "confident" and a["asked_with"] == key
        if m["fallback"]:
            # Adding a category shifts Jev's confidence across all of them, so
            # an answer for an old category only becomes a suggestion.
            if sure and new and a["choice"] != new:
                suggested.append(m)
            elif sure:
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
    print(f"In '{FALLBACK}': {len(placed) + len(suggested) + len(left)} merchant(s)")
    if placed:
        print(f"  {len(placed)} placed by Jev{f' in {new!r}' if new else ''}:")
        print("\n".join(placed))
    if suggested:
        print(
            f"  {len(suggested)} Jev now suggests an existing category for;"
            " review below"
        )
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
    if to_confirm or suggested or left:
        print()
        review_merchants(conn, categories, to_confirm + suggested + left)


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
                resort(conn, load_config(), name)
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
    categories = load_config()
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


def cmd_dashboard(args) -> None:
    app = Path(__file__).with_name("dashboard.py")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app)], check=False)


def cmd_unlink(args) -> None:
    """Revoke the bank connection at Plaid and delete its token. Transactions stay."""
    plaid = plaid_source.Plaid.from_env()
    if plaid is None:
        sys.exit(
            "Bank sync isn't set up: PLAID_CLIENT_ID and PLAID_SECRET aren't in .env."
        )
    conn = db.connect()
    items = db.get_plaid_items(conn)
    if not items:
        print("No bank is connected.")
        return
    names = ", ".join(i["institution"] or "your bank" for i in items)
    print(
        f"This revokes the app's access to {names} at Plaid and deletes the token."
        " Synced transactions stay. Connecting again later is a new connection:"
        " one of the 10 on Plaid's Trial plan."
    )
    if (_ask("Disconnect? [y/N] ") or "").lower() != "y":
        print("Nothing changed.")
        return
    for item in items:
        name = item["institution"] or "your bank"
        try:
            revoked = plaid_source.unlink(conn, plaid, item["item_id"])
        except (plaid_source.PlaidError, httpx.HTTPError) as e:
            sys.exit(
                f"Couldn't revoke {name} at Plaid: {e}\nIt's still connected; try again later."
            )
        conn.commit()
        print(
            f"Disconnected {name}."
            if revoked
            else f"Forgot {name}, but its token was missing, so Plaid couldn't revoke it."
        )
    print(
        "Your bank may still list the app as connected (some OAuth banks do);"
        " remove it in the bank's connected-apps settings too."
    )


def main() -> None:
    p = argparse.ArgumentParser(
        prog="spend", description="Credit card spending tracker"
    )
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("import", help="Import MSUFCU transaction CSV(s)")
    s.add_argument("files", nargs="+", type=Path)
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

    s = sub.add_parser("dashboard", help="Open the charts dashboard")
    s.set_defaults(func=cmd_dashboard)

    s = sub.add_parser(
        "unlink", help="Disconnect your bank: revoke Plaid's access, delete the token"
    )
    s.set_defaults(func=cmd_unlink)

    args = p.parse_args()
    # categories.toml is each user's own copy (git-ignored), made at setup.
    if args.func in (cmd_import, cmd_review, cmd_set) and not CATEGORIES_PATH.exists():
        sys.exit(
            f"{CATEGORIES_PATH} not found. Copy categories.example.toml to"
            " categories.toml and edit it to fit your spending (see README)."
        )
    args.func(args)


if __name__ == "__main__":
    main()
