# Contributing

How the code is laid out and how to work on it. For using the app, see [README.md](README.md).

## Development setup

Commits are checked by [pre-commit](https://pre-commit.com) hooks: Ruff (lint + format), ty (type checking), whitespace fixes, and guards that block bank data, databases, `.env` and API keys. The tools are dev dependencies, so their versions are pinned in `uv.lock`.

```bash
uv sync                             # installs the dev tools too
uv run pre-commit install           # once per clone; covers all git worktrees
uv run pre-commit run --all-files   # check everything by hand
```

If a hook fixes files (Ruff, whitespace), the commit stops. Review the changes, `git add` them and commit again. In VS Code, the [ty extension](https://marketplace.visualstudio.com/items?itemName=astral-sh.ty) shows the same type errors the hook checks for.

### Tests

Run the tests before and after any big change, especially to storage, bank sync or categorizing:

```bash
uv run pytest
```

They check that your data stays safe (the database and token file are private, tokens never reach the database, only redacted new merchants reach Jev, only keys and the token reach Plaid, bank data is git-ignored and blocked by the commit hook, the dashboard only listens on this machine), that imports never duplicate or overwrite your choices, and that bank sync handles new, changed, removed and pending transactions, paging and errors.

They run offline in a few seconds and never touch your data: each test gets its own throwaway database, the example categories, no API keys, and a fake Plaid (`tests/fakes.py`) with made-up transactions. Any real network request fails the test.

## Project structure

Paths marked "git-ignored" belong to each user and are never committed.

```
.
├── .claude/settings.json       Claude Code permissions (see CLAUDE.md)
├── .env                        keys and settings, copied from .env.example (git-ignored)
├── .env.example                template for .env, with every setting explained
├── .pre-commit-config.yaml     commit hooks: Ruff, ty, whitespace, bank-data and API-key guards
├── CLAUDE.md                   rules for Claude: commits, pull requests, issues
├── CONTRIBUTING.md             this file
├── README.md                   usage
├── categories.toml             the user's categories and descriptions (git-ignored)
├── categories.example.toml     template for categories.toml; change the default categories here
├── data/                       the database, and the Plaid token without an OS keyring (git-ignored)
├── documents/                  feature proposals (see Proposals below)
├── pyproject.toml, uv.lock     dependencies, the `spend` entry point, dev tools and their settings
├── src/spending/
│   ├── .streamlit/config.toml  dashboard server settings
│   ├── cli.py                  entry point: `spend …`
│   ├── parser.py               CSV → Transaction
│   ├── classify.py             categories, the payment check, redaction, Jev
│   ├── pipeline.py             categorize → store, shared by CSV import and bank sync
│   ├── db.py                   SQLite schema, migrations, queries
│   ├── plaid_source.py         Plaid client, /transactions/sync, token storage
│   ├── bank_sync.py            the dashboard's sync on open and sign-in card
│   ├── dashboard.py            Streamlit app and its Spending tab
│   ├── jev_tab.py              the Categorization tab
│   └── jev_eval.py             `python -m spending.jev_eval`: Jev vs the example keyword rules
├── statements/                 the user's CSV exports (git-ignored)
└── tests/                      offline tests; fakes.py is a fake Plaid
```

- **`data/`**: `spending.db` (SQLite) is created on first import, with the tables `transactions`, `merchant_memory`, `jev_answers`, `jev_evals` and `plaid_items`, each commented in `db.py`. `plaid-tokens.json` holds the Plaid access token, only where there's no OS keyring (e.g. WSL). The folder and its files are created owner-only (700 / 600).
- **`src/spending/.streamlit/config.toml`** applies however the dashboard is started: it listens only on `127.0.0.1`, accepts only the hostnames `localhost` and `127.0.0.1` (blocks DNS rebinding), and sends no usage statistics.
- **`categories.toml`** is copied from the example at setup, and `+` in `spend review` adds to it. Its `[rules]` are only labels for `jev_eval`; imports don't use them. Each git worktree needs its own copy, or set `SPENDING_CATEGORIES` in `.env` to share one.

## Proposals

| Path | Status | Notes |
|---|---|---|
| `documents/plaid-ideas.md` | In progress | Pull transactions via Plaid instead of downloading CSVs. MVP built and connected to MSUFCU: the dashboard syncs when it opens and signs in through Plaid Link; `spend unlink` disconnects. Not yet: Plaid's categories, `spend sync`, a schedule. |
| `documents/jev-review.md` | Docs | What Jev is, how `classify.py` uses it, agreement eval results, and speed/cost vs a chat LLM (with how it was estimated) |
| `documents/checking-ideas.md` | Proposal | Import the checking account too (rent, investing, P2P), with card-payment reconciliation to avoid double counting |
| `documents/extend-review.md` | Implemented | Jev's guesses in `spend review`, `+` to add a category, and `--resort` |
| `documents/resort-issues.md` | Resolved | Analysis of two re-sort problems found on real data: summary counts that didn't add up, and 98%-confident moves that still asked for approval |
| `documents/limit-flags.md` | Proposal | Fewer CLI flags: replace `review --resort` with change detection; maybe merge `--dry-run` and `--no-llm` |
| `documents/per-user-categories.md` | Implemented | `categories.toml` is each user's git-ignored copy of `categories.example.toml`, made at setup; how to upgrade an existing checkout |

## Layers

```mermaid
flowchart TB
    subgraph USER["👤 User layer"]
        direction LR
        U1["statements/<br/>CSV exports"]
        U2["spend CLI<br/>import · review · set · summary · unlink"]
        U3["Dashboard<br/>(browser)"]
    end

    subgraph SHARED["⚙️ Configuration (user + developer)"]
        direction LR
        C1["categories.toml<br/>categories + descriptions"]
        C2[".env<br/>OPENROUTER_KEY<br/>PLAID_* (optional)"]
    end

    subgraph DEV["🛠️ Developer layer: src/spending/"]
        direction LR
        D1["cli.py"]
        D2["parser.py"]
        D3["classify.py<br/>+ pipeline.py"]
        D4["db.py"]
        D5["dashboard.py<br/>+ jev_tab.py"]
        D6["jev_eval.py"]
        D7["bank_sync.py<br/>+ plaid_source.py"]
    end

    subgraph STORE["💾 Generated"]
        S1["data/spending.db"]
    end

    U1 --> U2
    U2 --> D1
    U3 --> D5
    D5 --> D7
    C1 --> D3
    C2 --> D3
    C2 --> D7
    D7 --> D3
    D4 --> S1
    D5 --> S1
    D6 --> S1
```

## Module dependencies

```mermaid
flowchart LR
    cli["cli.py<br/>entry point: spend"]
    parser["parser.py<br/>CSV → Transaction"]
    classify["classify.py<br/>manual → payments → memory → Jev"]
    db["db.py<br/>SQLite schema + queries"]
    dash["dashboard.py<br/>Streamlit + Plotly"]
    jevtab["jev_tab.py<br/>Categorization tab"]
    jeval["jev_eval.py<br/>Jev vs example keyword rules"]
    pipeline["pipeline.py<br/>categorize → store"]
    banksync["bank_sync.py<br/>sync on open, sign-in"]
    plaidsrc["plaid_source.py<br/>Plaid client + sync"]
    toml[("categories.toml")]
    sqlite[("data/spending.db")]
    llm{{"Jev via OpenRouter<br/>typesafe/jev-1.13"}}
    plaid{{"Plaid API<br/>+ Plaid Link in the browser"}}

    cli --> parser
    cli --> pipeline
    cli --> classify
    cli --> db
    cli -. "spend unlink" .-> plaidsrc
    cli -. "spend dashboard" .-> dash
    pipeline --> classify
    pipeline --> db
    classify --> toml
    classify --> llm
    classify --> db
    dash --> banksync
    dash --> db
    dash --> jevtab
    banksync --> plaidsrc
    banksync --> db
    plaidsrc --> parser
    plaidsrc --> pipeline
    plaidsrc --> db
    plaidsrc --> plaid
    jevtab --> db
    jeval --> classify
    jeval --> db
    db --> sqlite
```

## Import pipeline (what runs on `spend import` and on bank sync)

Both sources go through `pipeline.py`, so a row from Plaid is categorized exactly like one from a CSV.

```mermaid
flowchart TD
    A["MSUFCU CSV<br/>statements/*.csv"] --> B["parser.py<br/>skip account line, read Date / Amount / Description,<br/>strip 'Credit Card Ln Adv:'"]
    A2["Plaid /transactions/sync<br/>(when the dashboard opens)"] --> B2["plaid_source.py<br/>credit accounts, posted, after the CSV cutover;<br/>the bank's description, MSUFCU's wrapper removed"]
    B --> MC{"Set by you<br/>(review / set)?"}
    B2 --> MC
    MC -- yes --> MN["category (manual)"]
    MC -- no --> C{"Card payment<br/>or refund?"}
    C -- yes --> R["Payments & Credits<br/>(payment, never sent)"]
    C -- no --> D{"Merchant seen<br/>before?"}
    D -- yes --> M["category (memory)"]
    D -- no --> U{"Jev already unsure<br/>with these categories?"}
    U -- yes --> O
    U -- no --> E["redact: mask tokens with 3+ digits"]
    E --> F["Jev via OpenRouter<br/>choice question, one request per merchant"]
    F --> G{"Valid category and<br/>confidence ≥ 0.6?"}
    F -. "every answer:<br/>confidence, outcome, latency" .-> JD[("jev_answers")]
    G -- yes --> L["category (llm)<br/>+ saved to merchant_memory"]
    G -- no --> O["Other (fallback)<br/>guess saved to jev_answers<br/>→ spend review"]
    MN & R & M & L & O --> H["db.py: INSERT OR IGNORE<br/>deduplicated by hash or Plaid's transaction id"]
    H --> I[("data/spending.db")]
    I --> J["spend summary / dashboard"]
```

## Privacy boundary

Only the Description column ever leaves the machine, and payment rows never do.

Bank sync adds a different kind of trust: Plaid reads the accounts you share in Plaid Link, at the bank. The app only sends Plaid its keys and the access token, never your data, and it only stores credit-account rows.

```mermaid
flowchart LR
    subgraph LOCAL["🔒 Stays local"]
        direction TB
        a1["Account line<br/>(first line of the export)"]
        a2["Amount · Fee · New Balance<br/>Draft Number · Dates"]
        a3["Payment rows<br/>ACH Pmt / HB XFR Pmt / Credit Voucher /<br/>ACH Loan Payment (bank sync)<br/>(matched locally)"]
        a4["data/spending.db"]
    end
    subgraph SENT["🌐 Sent to Jev (OpenRouter, zero data retention)"]
        b1["Unseen merchant descriptions,<br/>redacted, e.g.<br/>SQ *SHOP #### GRAND RAPIDS MI"]
    end
    LOCAL ~~~ SENT
```

## Planned features

Both proposals live in `documents/`. The Plaid one has a working MVP (blue below); the rest of both is **not built yet**. They extend the MVP rather than replace it: CSV import stays as the no-third-party fallback.

```mermaid
flowchart LR
    classDef mvp fill:#2a78d6,color:#fff,stroke:#1c5cab
    classDef plan fill:#898781,color:#fff,stroke:#52514e

    subgraph MVP["MVP (built)"]
        direction TB
        m1["Card CSV<br/>spend import"]:::mvp
        m2["classify.py<br/>manual → payments → memory → Jev"]:::mvp
        m3[("spending.db<br/>card only")]:::mvp
        m4["Dashboard<br/>spending by category"]:::mvp
        m1 --> m2 --> m3 --> m4
    end

    subgraph PLAID["documents/plaid-ideas.md"]
        direction TB
        p1["Sign in via Plaid Link<br/>in the dashboard"]:::mvp
        p2["/transactions/sync<br/>on dashboard open"]:::mvp
        p3["Plaid category step<br/>before the LLM"]:::plan
        p4["spend sync / unlink<br/>+ scheduler"]:::plan
        p1 --> p2
        p2 --> p4
    end

    subgraph CHK["documents/checking-ideas.md"]
        direction TB
        c1["Checking CSV<br/>spend import --account checking"]:::plan
        c2["kind column<br/>spend · income · investment ·<br/>card_payment · transfer"]:::plan
        c3["Reconciliation<br/>pair card payments ±5 days"]:::plan
        c4["Cash flow + savings rate"]:::plan
        c1 --> c2 --> c3 --> c4
    end

    p2 -. "replaces manual download" .-> m1
    p3 -. "new step" .-> m2
    p2 -. "same Plaid Item covers checking" .-> c1
    c2 -. "schema change" .-> m3
    c4 -. "new views" .-> m4
```

### Changes by file

✅ = built (Plaid MVP); the rest is proposed.

| File | Plaid | Checking |
|---|---|---|
| `src/spending/cli.py` | ✅ `unlink`; `sync` still proposed (signing in and syncing live in the dashboard) | `import --account`, reconciliation in `review` |
| `src/spending/parser.py` | ✅ New sibling `plaid_source.py` (Plaid → `Transaction`), via `pipeline.py` like a CSV | Accept checking exports; local rules for income / P2P |
| `src/spending/classify.py` | Plaid category mapping before the LLM | Never send income or P2P rows to the LLM |
| `src/spending/db.py` | ✅ `source_kind` column, `plaid_items` table (`id` is Plaid's transaction id, so no `external_id`) | `account`, `kind`, `match_id` columns |
| `src/spending/dashboard.py` | ✅ Sync on open, "Connect your bank" card, last-sync caption (`bank_sync.py`) | Cash flow, savings rate, account filter, reconciliation panel |
| `categories.toml` | — | Rent & Housing, Investing, Income, Transfers, People (P2P) |
| New: `payees.toml` | — | Local payee → category map for Zelle/Venmo/checks |
| `.env` / OS keyring | ✅ `PLAID_*` in `.env`; `access_token` in the keyring, or `data/plaid-tokens.json` (0600) where there's none | — |

### Open questions blocking each

| Proposal | First thing to verify |
|---|---|
| Plaid | How long MSUFCU's OAuth consent lasts before you have to sign in again. (Whether the **credit line** comes through is answered: it does.) |
| Checking | What does a real checking CSV look like (payroll, Zelle, card-payment rows)? |

## Current status (2026-10-07)

```mermaid
flowchart LR
    classDef active fill:#1baf7a,color:#fff,stroke:#0f7a54
    classDef ready fill:#2a78d6,color:#fff,stroke:#1c5cab
    classDef pending fill:#eda100,color:#000,stroke:#b07800
    classDef idea fill:#898781,color:#fff,stroke:#52514e

    s1["CSV parser"]:::active
    s2["Jev categorizer"]:::active
    s3["CLI + dashboard"]:::ready
    s4["First real import<br/>(data/ is empty)"]:::pending
    s5["Initial git commit<br/>(everything untracked)"]:::pending
    s6["Plaid automation<br/>documents/plaid-ideas.md"]:::idea
    s7["Checking account<br/>documents/checking-ideas.md"]:::idea

    s1 --> s2 --> s3 --> s4
    s4 -.-> s6
    s4 -.-> s7
    s6 -. "shares Plaid Item" .-> s7
```

| Legend | Meaning |
|---|---|
| 🟢 Active | Recently changed (PDF → CSV switch, payment redaction) and verified on the real export |
| 🔵 Ready | Built and smoke-tested on sample data |
| 🟡 Pending | Next step |
| ⚪ Idea | Proposal written, not started (`documents/`) |
