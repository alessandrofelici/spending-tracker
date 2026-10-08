# Spending Tracker: instructions for Claude

See README.md for usage and STRUCTURE.md for layout. Feature proposals live in `documents/`; their work is tracked as GitHub issues (see [GitHub issues](#github-issues-filing-them-for-the-developer)).

## Git: committing on the developer's behalf

You are **authorized to commit without asking**, within these rules. Anything outside them still needs the developer's OK.

### When to commit

- **One commit per logical change**: commit as soon as a self-contained change is done and verified (a bug fix, one step of a feature, a doc update). Don't batch a whole task into one commit, and don't mix unrelated changes in one commit.
- **Verify first.** Before committing code, at minimum run `uv run pre-commit run --all-files` (Ruff, ty and the bank-data/secret guards; the same hooks run on `git commit`), plus whatever check proves the change works (e.g. a `--dry-run --no-llm` import against sample data). Never commit something you know is broken; say so instead.
- **Only commit your own changes.** If the working tree already had uncommitted edits from the developer when you started, leave them unstaged. Stage files explicitly (`git add <path>`), not `git add -A` / `git add .`.

### Where

- **Never commit directly to `main`.** If you're on `main`, create a branch first: `git switch -c <type>/<short-name>`, e.g. `feat/jev-review`, `fix/set-matching`, `chore/pre-commit-hooks`.
- In a git worktree, commit on that worktree's branch.
- **Never push**, and never amend, rebase or reset commits; ask the developer instead. (Push is blocked in `.claude/settings.json`; amend, rebase, `reset --hard` and switching to `main` prompt.)

### Message format: Conventional Commits

```
<type>(<scope>): <summary>

<body: why the change was made, and anything non-obvious>
```

- **type**: `feat` · `fix` · `docs` · `refactor` · `test` · `chore` (tooling, deps, config) · `perf`
- **scope** (optional, use when it fits): `classify` · `parser` · `cli` · `db` · `dashboard` · `config` (categories.toml, .env.example) · `docs` · `hooks` · `deps`
- **summary**: imperative mood ("add", not "added"), lowercase, no trailing period, ≤ 72 characters
- **body**: wrap at 72 characters. Explain *why*, not what the diff already shows. Skip it only for trivial changes.
- **Breaking changes** (schema change, renamed CLI flag or setting): add `!` after the type/scope and a `BREAKING CHANGE:` line in the body.
- **No `Co-Authored-By` or other Claude attribution trailers.**

Examples:

```
fix(cli): match merchants by substring in `spend set`

The exact normalized key (e.g. "KROGER EAST LANSING") isn't visible
to users, so `spend set kroger ...` silently updated nothing.
```

```
feat(db)!: add account and kind columns

BREAKING CHANGE: databases created before this change must be
re-imported; old rows have no account or kind.
```

### Never commit

- `.env`, real API keys, or anything matching `sk-or-`
- Bank data: `statements/`, `data/`, `*.csv`, `*.pdf`, `*.db` (they're git-ignored; never use `git add -f` on them)
- Generated files: `.venv/`, `__pycache__/`

Before each commit, check `git diff --cached --stat` to confirm only the intended files are staged.

## GitHub issues: filing them for the developer

You can create issues on `alessandrofelici/spending-tracker` with the `gh` CLI (already logged in). **The repo is public**, so every issue is public.

### When

- **Only when the developer asks** ("open an issue for X", "turn documents/foo.md into an issue"). Don't file issues on your own initiative; suggest one instead.
- Usually an issue tracks a proposal in `documents/`: the doc holds the design, the issue tracks the work. Check `gh issue list --state all` first so you don't file a duplicate.

### How

```bash
gh issue create --title "<title>" --label <label> --body-file <file>
```

- Write the body to a file in your scratchpad and pass `--body-file`, so quoting can't mangle Markdown.
- **title**: short and imperative, like a commit summary without the type ("Limit CLI flags", "Make categories.toml per-user").
- **label**: `enhancement` (feature), `bug`, or `documentation`. Use the existing labels; don't create new ones.
- **body**:
  - one or two sentences on the problem and why it matters
  - a link to the proposal, e.g. `[documents/limit-flags.md](https://github.com/alessandrofelici/spending-tracker/blob/main/documents/limit-flags.md)`, instead of copying it
  - the decisions or open questions still to settle, as a task list (`- [ ]`)
- After creating it, add the issue link to the doc's **Status** line (e.g. `**Status: open** ([#3](https://github.com/alessandrofelici/spending-tracker/issues/3)).`) and commit that as a `docs` change.

### Never

- Never put bank data in an issue: no real merchant names from `statements/` or the database, amounts, account numbers, `.env` contents or keys. Use made-up examples like the README's (`PETSMART 1234 LANSING MI`).
- Never close, edit, delete or comment on existing issues without the developer's OK. (`gh issue create`, `edit`, `close`, `comment` prompt; `delete` is blocked.)
