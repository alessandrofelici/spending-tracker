# Project Structure

Generated with the `tree` alias from `~/.bashrc` (`exa -T --icons`), limited to 3 levels:

```
 .
├──  categories.example.toml
├──  categories.toml
├──  data
├──  documents
│   ├──  checking-ideas.md
│   ├──  jev-review.md
│   └──  plaid-ideas.md
├──  pyproject.toml
├── 󰂺 README.md
├── 󰣞 src
│   └──  spending
│       ├──  __init__.py
│       ├──  classify.py
│       ├──  cli.py
│       ├──  dashboard.py
│       ├──  db.py
│       ├──  jev_eval.py
│       ├──  jev_tab.py
│       └──  parser.py
├──  statements
│   └──  l50csvdl.csv
├──  STRUCTURE.md
└──  uv.lock
```


## Who touches what

| Path | User | Developer | Status | Notes |
|---|:-:|:-:|---|---|
| `statements/` | ✅ | | **In use** | Drop MSUFCU CSV exports here. Holds `l50csvdl.csv` (Jan–Oct 2026). Git-ignored. |
| `uv run spend …` (CLI) | ✅ | | **In use** | `import`, `review`, `set`, `summary`, `dashboard` |
| Dashboard (browser) | ✅ | | **In use** | Streamlit app launched by `spend dashboard` |
| `categories.toml` | ✅ | | **In use** | Each user's own category list and the descriptions Jev reads, plus example keyword rules used only by `jev_eval`. Copied from the example at setup; users add categories with `+` in `spend review`. Git-ignored. |
| `categories.example.toml` | | ✅ | Config | The committed template for `categories.toml`. Devs change the default categories here. |
| `data/` | | | Generated | `spending.db` (SQLite) is created on first import: `transactions`, `merchant_memory`, `jev_answers` (Jev's last answer per merchant with its confidence and latency, used for review suggestions and the Categorization tab) and `jev_evals` (results of `python -m spending.jev_eval`). Git-ignored. |
| `src/spending/` | | ✅ | **Active development** | All app code (~1,400 lines) |
| `pyproject.toml` / `uv.lock` | | ✅ | Config | Dependencies and the `spend` entry point, plus dev tools (Ruff, ty, pre-commit) and their settings |
| `.pre-commit-config.yaml` | | ✅ | Config | Git pre-commit hooks: Ruff, ty, whitespace, bank-data and API-key guards |
| `.env` | | ✅ | Secret | `OPENROUTER_KEY`. Git-ignored. |
| `README.md` | ✅ | ✅ | Docs | Usage and how categorization works |
| `STRUCTURE.md` | | ✅ | Docs | This file |
| `documents/` | | ✅ | **Planning** | Feature proposals, separate from the MVP (see [Planned features](#planned-features)) |
| `documents/plaid-ideas.md` | | ✅ | Proposal | Automatic monthly pull via Plaid instead of manual CSV downloads. MSUFCU is supported (Plaid Exchange); free Trial plan covers it. |
| `documents/jev-review.md` | | ✅ | Docs | What Jev is, how `classify.py` uses it, agreement eval results, and speed/cost vs a chat LLM (with how it was estimated) |
| `documents/checking-ideas.md` | | ✅ | Proposal | Import the checking account too (rent, investing, P2P), with card-payment reconciliation to avoid double counting |
| `documents/extend-review.md` | | ✅ | Implemented | Jev's guesses in `spend review`, `+` to add a category, and `--resort` |
| `documents/resort-issues.md` | | ✅ | Resolved | Analysis of two re-sort problems found on real data: summary counts that didn't add up, and 98%-confident moves that still asked for approval |
| `documents/limit-flags.md` | | ✅ | Proposal | Fewer CLI flags: replace `review --resort` with change detection; maybe merge `--dry-run` and `--no-llm` |
| `documents/per-user-categories.md` | | ✅ | Implemented | `categories.toml` is each user's git-ignored copy of `categories.example.toml`, made at setup; how to upgrade an existing checkout |
| `.venv/` | | | Generated | Created by `uv sync` |

## Layers

```mermaid
flowchart TB
    subgraph USER["👤 User layer"]
        direction LR
        U1["statements/<br/>CSV exports"]
        U2["spend CLI<br/>import · review · set · summary"]
        U3["Dashboard<br/>(browser)"]
    end

    subgraph SHARED["⚙️ Configuration (user + developer)"]
        direction LR
        C1["categories.toml<br/>categories + descriptions"]
        C2[".env<br/>OPENROUTER_KEY"]
    end

    subgraph DEV["🛠️ Developer layer: src/spending/"]
        direction LR
        D1["cli.py"]
        D2["parser.py"]
        D3["classify.py"]
        D4["db.py"]
        D5["dashboard.py<br/>+ jev_tab.py"]
        D6["jev_eval.py"]
    end

    subgraph STORE["💾 Generated"]
        S1["data/spending.db"]
    end

    U1 --> U2
    U2 --> D1
    U3 --> D5
    C1 --> D3
    C2 --> D3
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
    toml[("categories.toml")]
    sqlite[("data/spending.db")]
    llm{{"Jev via OpenRouter<br/>typesafe/jev-1.13"}}

    cli --> parser
    cli --> classify
    cli --> db
    cli -. "spend dashboard" .-> dash
    classify --> toml
    classify --> llm
    classify --> db
    dash --> db
    dash --> jevtab
    jevtab --> db
    jeval --> classify
    jeval --> db
    db --> sqlite
```

## Import pipeline (what runs on `spend import`)

```mermaid
flowchart TD
    A["MSUFCU CSV<br/>statements/*.csv"] --> B["parser.py<br/>skip account line, read Date / Amount / Description,<br/>strip 'Credit Card Ln Adv:'"]
    B --> MC{"Set by you<br/>(review / set)?"}
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
    MN & R & M & L & O --> H["db.py: INSERT OR IGNORE<br/>deduplicated by hash"]
    H --> I[("data/spending.db")]
    I --> J["spend summary / dashboard"]
```

## Privacy boundary

Only the Description column ever leaves the machine, and payment rows never do.

```mermaid
flowchart LR
    subgraph LOCAL["🔒 Stays local"]
        direction TB
        a1["Account line<br/>(first line of the export)"]
        a2["Amount · Fee · New Balance<br/>Draft Number · Dates"]
        a3["Payment rows<br/>ACH Pmt / HB XFR Pmt / Credit Voucher<br/>(matched locally)"]
        a4["data/spending.db"]
    end
    subgraph SENT["🌐 Sent to Jev (OpenRouter, zero data retention)"]
        b1["Unseen merchant descriptions,<br/>redacted, e.g.<br/>SQ *SHOP #### GRAND RAPIDS MI"]
    end
    LOCAL ~~~ SENT
```

## Planned features

Both proposals live in `documents/` and are **not built yet**. They extend the MVP rather than replace it: CSV import stays as the no-third-party fallback.

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
        p1["spend link / sync / unlink"]:::plan
        p2["/transactions/sync<br/>cursor + scheduler"]:::plan
        p3["Plaid category step<br/>before the LLM"]:::plan
        p1 --> p2
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

### Proposed changes by file

| File | Plaid | Checking |
|---|---|---|
| `src/spending/cli.py` | `link`, `sync`, `unlink` commands | `import --account`, reconciliation in `review` |
| `src/spending/parser.py` | New sibling `plaid_source.py` (Plaid → `Transaction`) | Accept checking exports; local rules for income / P2P |
| `src/spending/classify.py` | Plaid category mapping before the LLM | Never send income or P2P rows to the LLM |
| `src/spending/db.py` | `source_kind`, `external_id`, `account` columns; `plaid_items` table | `account`, `kind`, `match_id` columns |
| `src/spending/dashboard.py` | "Last synced" note | Cash flow, savings rate, account filter, reconciliation panel |
| `categories.toml` | — | Rent & Housing, Investing, Income, Transfers, People (P2P) |
| New: `payees.toml` | — | Local payee → category map for Zelle/Venmo/checks |
| `.env` / OS keyring | `PLAID_CLIENT_ID`, `PLAID_SECRET` in `.env`; `access_token` in keyring | — |

### Open questions blocking each

| Proposal | First thing to verify |
|---|---|
| Plaid | Does MSUFCU expose the **credit line** in Plaid Link, or only share/checking accounts? |
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
