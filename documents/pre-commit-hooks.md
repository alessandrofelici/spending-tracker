# Git Pre-Commit Hooks

**Status: implemented** ([#1](https://github.com/alessandrofelici/spending-tracker/issues/1)). Setup and usage are in README.md, under "Development".

## Motivation
- Maintain good code quality

## Specifications
- Pylance syntax checking (shown by vscode extension)
- Ty and Ruff checking

## Decisions
- **Runner:** [pre-commit](https://pre-commit.com), with every hook local and run through `uv run`. Ruff, ty, pre-commit and pre-commit-hooks are dev dependencies, so versions live in `uv.lock`.
- **Pylance:** covered by ty instead of running Pyright in the hook. The two overlap heavily and ty is much faster. Install the ty VS Code extension so the editor and the hook agree.
- **Ruff:** lint (`--fix`) + format. Rules: `E, F, W, I, B, UP, SIM, C4`, with `E501` ignored because the formatter owns line length.
- **ty:** checks the whole project on each commit (not just staged files), since type errors cross files.
- **Extras:** whitespace and end-of-file fixers, TOML/YAML checks, merge-conflict and large-file checks, and guards that block bank data (`*.csv`, `*.pdf`, `*.db`, `statements/`, `data/`), `.env`, and OpenRouter keys.
