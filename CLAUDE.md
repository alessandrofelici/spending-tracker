# Spending Tracker: instructions for Claude

README.md covers usage. CONTRIBUTING.md covers dev setup, the layout, and the feature proposals in `documents/`, whose work is tracked as GitHub issues (see [GitHub issues](#github-issues)).

## Bank data stays private

The repo is public. Keep bank data out of everything published: commits, PR titles and descriptions, issues and docs. That means no merchant names, amounts, dates or account numbers from `statements/` or the database, and no `.env` contents or keys. Describe real transactions generically (e.g. "a warehouse-club gas station") or use made-up examples like the README's (`PETSMART 1234 LANSING MI`).

## Git

You are **authorized to commit without asking**, within these rules. Anything outside them still needs the developer's OK.

- **One commit per logical change** (a bug fix, one step of a feature, a doc update), made as soon as it's done and verified. Don't batch a whole task into one commit, and don't mix unrelated changes.
- **Verify first.** Before committing code, run at least `uv run pre-commit run --all-files` (Ruff, ty and the bank-data/secret guards; the same hooks run on `git commit`) and `uv run pytest` (offline), plus whatever proves the change works (e.g. importing a made-up CSV into a throwaway `SPENDING_DB`). Never commit something you know is broken; say so instead.
- **Commit only your own changes.** Leave edits the developer already had in the working tree unstaged. Stage paths explicitly (`git add <path>`, not `-A` or `.`), and check `git diff --cached --stat` before each commit.
- **Never commit** `.env`, real API keys or anything matching `sk-or-`; `categories.toml` (the developer's own, git-ignored; change default categories in `categories.example.toml`); bank data (`statements/`, `data/`, `*.csv`, `*.pdf`, `*.db`; never `git add -f` them); or generated files (`.venv/`, `__pycache__/`).
- **Never commit directly to `main`.** On `main`, branch first: `git switch -c <type>/<short-name>`, e.g. `feat/jev-review`, `fix/set-matching`. In a git worktree, commit on that worktree's branch.
- **Push, and open or merge pull requests, only when the developer asks**, and never push to `main`. Never amend, rebase or reset commits; ask the developer instead. (`.claude/settings.json` allows push and `gh pr …` so they aren't blocked when asked, not as permission to use them unprompted; amend, rebase, `reset --hard` and switching to `main` prompt.)

### Commit messages: Conventional Commits

```
<type>(<scope>): <summary>

<body: why the change was made, and anything non-obvious>
```

- **type**: `feat` · `fix` · `docs` · `refactor` · `test` · `chore` (tooling, deps, config) · `perf`
- **scope** (optional, when it fits): `classify` · `parser` · `cli` · `db` · `dashboard` · `config` (categories.example.toml, .env.example) · `docs` · `hooks` · `deps`
- **summary**: imperative mood ("add", not "added"), lowercase, no trailing period, ≤ 72 characters
- **body**: wrapped at 72 characters. Explain *why*, not what the diff already shows. Skip it only for trivial changes.
- **Breaking changes** (schema change, renamed CLI flag or setting): `!` after the type/scope and a `BREAKING CHANGE:` line in the body.
- **No `Co-Authored-By` or other Claude attribution trailers.**

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

## GitHub issues

You can create issues on `alessandrofelici/spending-tracker` with the `gh` CLI (already logged in). Every issue is public.

- **File one only when the developer asks** ("open an issue for X", "turn documents/foo.md into an issue"); otherwise suggest one. Check `gh issue list --state all` first so you don't file a duplicate. An issue usually tracks a proposal in `documents/`: the doc holds the design, the issue tracks the work.
- **Create it** with `gh issue create --title "<title>" --label <label> --body-file <file>`, writing the body to a file in your scratchpad so quoting can't mangle Markdown.
  - **title**: short and imperative, like a commit summary without the type ("Limit CLI flags", "Make categories.toml per-user").
  - **label**: an existing one, `enhancement` (feature), `bug` or `documentation`. Don't create labels.
  - **body**: one or two sentences on the problem and why it matters; a link to the proposal instead of a copy of it, e.g. `[documents/checking-ideas.md](https://github.com/alessandrofelici/spending-tracker/blob/main/documents/checking-ideas.md)`; and the decisions or open questions still to settle, as a task list (`- [ ]`).
- **Afterwards**, add the issue link to the doc's Status line (e.g. `**Status: open** ([#3](https://github.com/alessandrofelici/spending-tracker/issues/3)).`) and commit that as a `docs` change. When a pull request finishes an issue's work, put `Closes #N` in its description so merging closes the issue.
- **Never close, edit, delete or comment on existing issues** without the developer's OK. (`gh issue create`, `edit`, `close` and `comment` prompt; `delete` is blocked.)
