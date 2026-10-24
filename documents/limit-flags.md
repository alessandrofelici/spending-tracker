# Limit CLI Flags

**Status: open** ([#6](https://github.com/alessandrofelici/spending-tracker/issues/6)). Removed so far: `import --dry-run` and `--no-llm`, `spend summary` (with `--month`) and `jev_eval --no-save`. Left: `review --resort`.

## Motivation
- Keep the CLI small: every flag is something to learn, document and test.
- Each command should do the obvious thing without needing a flag.
- Day to day, the user only runs `spend dashboard`, which syncs with the bank. The other commands are for setup, CSV imports and fixing categories.

## Current flags

Every flag is described in [README.md](../README.md#commands): `review --all` and `--resort [CATEGORY]`, and `set --all-matches`. Settings that aren't flags go in `.env` (see [.env.example](../.env.example)).

## Done
- **`import --dry-run` and `--no-llm`:** removed. Without an OpenRouter key, import leaves new merchants for `spend review` by itself, as bank sync does. The offline tests replaced the preview as the check before commits.
- **`spend summary` and `--month`:** removed; the dashboard shows the same totals.
- **`jev_eval --no-save`:** removed with the `jev_evals` table. Nothing read the saved answers once the dashboard's Categorization tab was gone, so the eval only prints.

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

### 2. Keep the rest
- `--all`: no other way to spot-check Jev's results.
- `--all-matches`: a safety catch against setting many merchants by accident.

## Open questions
- If `--resort` goes, is the "Re-sort now?" prompt enough, or should a re-sort also happen automatically on `import` after a category change?
