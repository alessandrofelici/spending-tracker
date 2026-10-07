# Jev Review

## Motivation
- Teach the developer about jev
- How it is implemented in the app
- Prove it is a good decision, and argue if the implementation difference is noticable

## Specifications
- Describe this to the user
- When showing the differnece, create a feature branch to show visually how many transaction items were categorized by jev vs. by word matching, and how quick it would be against a chat-based LLM (using an estimate, and document how estimated)

## Plan

### Decisions
- **Visual:** a new "Categorization" tab in the existing Streamlit dashboard (no separate report).
- **Speed:** Jev's side is measured (timing added to real requests); the chat-LLM side is estimated, with the method written down next to the numbers.
- **Accuracy:** measured as Jev's agreement with the keyword rules on the merchants the rules already categorize.
- **Data:** Jev's confidence, outcome and latency are stored per merchant in a new table (non-breaking).
- **Branch:** work happens on this worktree's branch (`worktree-jev-review`), never `main`.

### Starting point (2026-10-07, real DB)
| source | transactions | merchants |
|---|--:|--:|
| rule | 169 | 62 |
| llm (Jev) | 149 | 69 |
| fallback | 40 | 27 |
| **total** | **358** | **158** |

What the data can't tell us yet:
- Jev's confidence isn't stored.
- `fallback` lumps together failed requests, answers below `JEV_MIN_CONFIDENCE` and "Jev picked Other".
- Request latency isn't recorded.
- On later imports, merchants Jev already answered show as `memory`, so "categorized by Jev" = `llm` + `memory` where `merchant_memory.source = 'llm'`.

### Steps (one commit each)
1. **Explainer** (this document): what Jev is (a typed decision model on OpenRouter's Decisions API: you give it a state and a `choice` question with criteria, it returns a choice plus a confidence, with no text to parse), and how `classify.py` uses it (manual → rule → memory → Jev → fallback; `redact()`; one request per merchant, 8 workers; `provider.zdr`; the 0.6 threshold; the local check that the answer is a valid category). Also why it suits this task better than a chat LLM, and where it falls short. Checked against OpenRouter's docs and a real response, not written from memory.
2. **Record Jev decisions** (`db.py`, `classify.py`): a new table `jev_decisions(merchant, choice, confidence, status, latency_ms, model, decided_at)` with `status` ∈ `ok | unsure | said_other | failed`, created with `CREATE TABLE IF NOT EXISTS` so existing DBs keep working. It's a separate table instead of new `merchant_memory` columns so that unsure or failed merchants don't become `memory` hits on the next import. `_decide()` returns latency and status; the import prints the batch's wall time.
3. **Agreement eval** (`src/spending/jev_eval.py`, run with `uv run python -m spending.jev_eval`): opt-in, not part of import. Sends the rule-matched merchants (redacted, ZDR; payment rows excluded so they stay local) to Jev, then reports agreement overall and per category, the disagreements and per-request latency. About 62 requests, ≈ $0.001. Rows are stored tagged `eval` and never written to `merchant_memory`. This also gives real latency numbers without re-importing.
4. **Speed estimate** (section in this document):
   - Jev: measured p50 / p95 latency per request and wall time at 8 workers, from steps 2–3.
   - Chat LLM, estimated per merchant as `TTFT + output_tokens / throughput`:
     - input tokens = instructions + the 13 category descriptions + the description, counted with a real tokenizer
     - output tokens = a JSON answer, with and without short reasoning
     - TTFT and throughput from OpenRouter's published stats for one or two chat models, cited with the date checked
     - plus an assumed retry rate for unparseable or off-list answers, labeled as an assumption
   - Cost compared the same way.
5. **Dashboard "Categorization" tab** (`dashboard.py`):
   - transactions, merchants and $ by source (rule / Jev / manual / fallback), where "Jev" includes `memory` hits it originally decided
   - fallback split by `status` where known
   - a confidence histogram with the threshold marked
   - eval agreement, if it has been run
   - a measured-Jev vs estimated-chat latency chart, captioned with how the estimate was made
6. **Docs:** README (the eval command, the new tab) and STRUCTURE (the new table and module).

### Verification
- `uv run pre-commit run --all-files` before every commit.
- `spend import --dry-run --no-llm` against a scratch copy of the DB (via `SPENDING_DB`).
- Launch the dashboard once to check the new tab renders.
- The real DB is only read, except for the opt-in eval writing `jev_decisions` rows.
