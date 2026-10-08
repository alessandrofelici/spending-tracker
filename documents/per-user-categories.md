# Per-User Categories

**Status: implemented** on `feat/per-user-categories`, not merged yet. No issue yet: file one with the GitHub issues workflow in CLAUDE.md.

## Motivation
- `categories.toml` is personal: the categories someone needs, the descriptions Jev reads, and keyword rules naming the stores they shop at.
- It was committed, so:
  - `+` in `spend review` rewrites it, leaving the working tree dirty after normal use.
  - Its rules showed one person's spending habits in a public repo.
  - A second user would get the first user's categories, and pulling updates would conflict with their edits.

## Specifications

### 1. Example file, git-ignored copy
- `categories.toml` → `categories.example.toml` (renamed in git, so history follows it). It's the template, with a header comment saying to copy it.
- `categories.toml` is git-ignored, and the `no-bank-data` pre-commit hook blocks it too, so it can't be committed even with `git add -f`.
- Same pattern as `.env.example` / `.env`.

### 2. Setup step
- README "First-time setup" has a new step 3: `cp categories.example.toml categories.toml`, marked TODO to edit it, followed by a paragraph on what to change.
- Troubleshooting has a row for the missing-file error.

### 3. Clear error when it's missing
- `spend import`, `review` and `set` exit before doing anything if the file isn't there:
  `.../categories.toml not found. Copy categories.example.toml to categories.toml and edit it to fit your spending (see README).`
- `summary` and `dashboard` only read the database, so they still work.
- `SPENDING_CATEGORIES` in `.env` still overrides the path; the check uses whatever it points to.
- The file is **not** copied automatically: copying it silently would skip the step where the user makes it theirs.

### 4. Docs
- CLAUDE.md: `categories.toml` is under "Never commit"; the `config` commit scope now means `categories.example.toml`.
- STRUCTURE.md: separate rows for the example (developer) and the copy (user).

## Upgrading an existing checkout
- Merging or pulling this **deletes** `categories.toml` from the checkout, because git removes a file that stops being tracked.
  - Before merging: `cp categories.toml categories.toml.bak`, then move it back after.
  - Or, if it had no changes of your own: `cp categories.example.toml categories.toml`.
- Each git worktree needs its own copy, or point `SPENDING_CATEGORIES` in `.env` at a shared one.
- The old committed versions stay in git history. This stops new ones being committed; it doesn't remove past ones.

## Open questions
- Should `categories.example.toml` drop the East Lansing–specific rules (`CATA`, `OLIN`, `SPARROW`, `MSU `, `MICHIGAN STATE`, `SPARTAN`) for a more generic starting point?
- When `categories.example.toml` gains a category later, existing users won't get it. Is a note in the release enough, or should `spend` point out categories in the example that are missing from your copy?
