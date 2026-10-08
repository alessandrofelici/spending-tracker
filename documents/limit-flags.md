# Limit CLI Flags

**Status: open.** Not started.

## Motivation
- Keep the CLI small: every flag is something to learn, document and test.
- Each command should do the obvious thing without needing a flag.

## Current flags

| Command | Flag | What it does | Added |
|---|---|---|---|
| `import` | `--dry-run` | Show what would be imported without saving | MVP |
| `import` | `--no-llm` | Don't ask Jev; unknown merchants become `Other` | MVP |
| `review` | `--all` | Also review merchants categorized by a rule or by Jev | MVP |
| `review` | `--resort [CATEGORY]` | Ask Jev again: apply its answers for `Other`, and move merchants into CATEGORY | extend-review |
| `set` | `--all-matches` | Apply to every merchant matching the text | MVP |
| `summary` | `--month YYYY-MM` | Pick the month | MVP |

Settings in `.env` (not flags): `OPENROUTER_KEY`, `JEV_MODEL`, `JEV_MIN_CONFIDENCE`, `SPENDING_DB`, `SPENDING_CATEGORIES`.

## Specifications

### 1. Remove `review --resort` (recommended)
- `+` in review already runs the re-sort, so the flag only covers two cases:
  - a category added to `categories.toml` by hand
  - a category's description edited
- `spend review` can detect both by itself:
  - Save the category list and descriptions Jev was last asked with, e.g. in a small table keyed by the hash `jev_answers.asked_with` already uses.
  - When they've changed, say so before reviewing, e.g. `Categories changed since Jev last looked (new: Athletic). Re-sort now? [Y/n]`.
  - Then run the same re-sort as `+`, with the detected new categories.
- A description-only change maps to today's plain `--resort`: retry `Other` with the current categories.
- Moving merchants into a new category is decided by Jev's answer, so more than one new category at once has to work (today's `resort()` takes one).

### 2. Merge `--dry-run` and `--no-llm` (optional)
- Today `--dry-run` alone still sends merchants to Jev but saves nothing, so it costs requests for a preview.
- Option: make `--dry-run` always offline, and drop `--no-llm`.
  - Lost: importing for real without Jev, and previewing what Jev would say.
  - Alternative: keep `--no-llm`, and only make `--dry-run` imply it.

### 3. Keep the rest
- `--all`: no other way to spot-check rule and Jev results.
- `--all-matches`: a safety catch against setting many merchants by accident.
- `--month`: basic.

## Open questions
- If `--resort` goes, is the "Re-sort now?" prompt enough, or should a re-sort also happen automatically on `import` after a category change?
- Is there any use for previewing Jev's answers (`--dry-run` with Jev) worth keeping?
