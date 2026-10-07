# Jev Review

## Motivation
- Teach the developer about jev
- How it is implemented in the app
- Prove it is a good decision, and argue if the implementation difference is noticable

## Specifications
- Describe this to the user
- When showing the differnece, create a feature branch to show visually how many transaction items were categorized by jev vs. by word matching, and how quick it would be against a chat-based LLM (using an estimate, and document how estimated)

## What Jev is

Jev (`typesafe/jev-1.13`) is a **structured decision model** from TypeSafe, served through OpenRouter. A chat model takes a prompt and writes text. Jev takes your application's **state** and one or more **typed questions**, and returns **typed answers with probabilities**. It never writes prose, so there's nothing to parse.

It answers three kinds of questions:

| Question type | Asks | Returns |
|---|---|---|
| choice | Which one of these options? | the chosen option, a probability for each option, a confidence |
| yes/no | Does this condition hold? | the probability of yes |
| score | Where does this fall on an ordered scale? | a probability-weighted position, a probability per level, a confidence |

This app only uses **choice**. Other facts from [OpenRouter's Jev guide](https://openrouter.ai/docs/guides/community/jev) (checked 2026-10-07):

- **Endpoint:** `POST https://openrouter.ai/api/alpha/decisions`. It's an **alpha** API, so the shape may change.
- **Billing:** you pay for input tokens only; output tokens are free. Every response reports its own `usage.cost`.
- **Context window:** 32k tokens, covering the state plus the questions.
- **No explanations:** it can't say *why* it chose something. The guide suggests sending low-confidence cases to a human, which is what `spend review` does.

### A real exchange

This is one request built exactly as the app builds it, with a made-up description, sent on 2026-10-07:

```jsonc
// request (abridged)
{
  "model": "typesafe/jev-1.13",
  "state": { "transaction_description": "BLUE MOON BAKERY #### ANN ARBOR MI" },
  "questions": { "category": {
      "type": "choice",
      "instructions": "Which spending category does this credit card transaction belong to? ...",
      "criteria": { "Groceries": "Supermarkets and grocery stores", "Dining": "Restaurants, cafes, ...", ... }
  }},
  "provider": { "zdr": true, "data_collection": "deny" }
}

// response, 550 ms
{
  "model": "typesafe/jev-1.13-20260917",
  "answers": { "category": {
      "type": "choice",
      "choice": "Dining",
      "probabilities": { "Dining": 0.88, "Shopping": 0.05, "Groceries": 0.04, "Other": 0.03, ...all others 0 },
      "confidence": 0.87
  }},
  "usage": { "input_tokens": 623, "output_tokens": 127, "cost": 0.000026166 }
}
```

A vague description (`SQ *JMR HOLDINGS LLC LANSING MI`) came back as `"choice": "Other"` with `"confidence": 0.46` and its probability split between Other 0.50, Shopping 0.18 and Groceries 0.15. The app sends that merchant to `spend review` instead of guessing.

What to notice:

- **The answer is always a key of `criteria`.** Jev can't return a typo or a made-up category, and it doesn't wrap the answer in a sentence.
- **`confidence` isn't simply the top probability** (0.87 vs 0.88 above). The app applies its threshold to `confidence`.
- **Most of the cost is the question, not the merchant.** About 600 of the 623 input tokens are the instructions and the 13 category descriptions, which are resent with every request. That works out to about $0.000026 per merchant, or $0.04 per million input tokens. (The README's "$0.00002" is slightly low.)
- `output_tokens` is reported but not billed.

## How the app uses it

Everything is in `src/spending/classify.py`. Jev is the **last resort**: `categorize()` (line 154) gives each transaction the first answer it finds, in this order:

1. **manual:** a category you set with `spend review` / `spend set`
2. **rule:** a keyword substring from `categories.toml` (`match_rule`, line 54)
3. **memory:** a merchant already categorized on an earlier import
4. **Jev:** only for merchants never seen before
5. **fallback:** `Other`, shown in `spend review`

Step 4 (`classify_with_llm`, line 78, and `_decide`, line 120) works like this:

- **One request per unique merchant, not per transaction.** A merchant you visit 20 times costs one request, and the answer is saved to `merchant_memory`, so it's never asked again.
- **Redacted before sending.** `redact()` (line 62) masks any token with 3+ digits (store numbers, phone numbers, references). Only the description goes out: no amount, date or account. Payment rows are caught by rules first, so they never reach Jev.
- **Criteria come from `categories.toml`.** The `[descriptions]` table becomes `criteria` (line 91), so editing a description directly changes what Jev is choosing between.
- **8 requests in parallel** (`WORKERS`, line 97), each in its own request, so one odd description can't affect another's answer.
- **One retry** on a network or parse error (line 136). After that the merchant counts as failed.
- **Checked again locally.** An answer that isn't one of your categories is rejected (line 148).
- **Unsure answers go to review.** If the answer is `Other` or `confidence < JEV_MIN_CONFIDENCE` (0.6 by default, line 105), the merchant becomes `fallback` and isn't remembered, so you decide it in `spend review`.
- **Zero data retention only** (`provider.zdr`, line 134).

## Why Jev instead of a chat LLM

Below is the case for Jev. Steps 3–4 of the plan test it with real numbers.

| | Chat LLM | Jev |
|---|---|---|
| Output | free text; you prompt for JSON and hope | a typed answer from your list |
| Validity | can invent categories, misspell them or add prose; needs parsing and retries | always one of `criteria` |
| Uncertainty | no reliable signal; asking it to rate its own confidence doesn't produce a calibrated number | a probability per category plus a confidence, which drive the 0.6 threshold |
| Prompt injection via the description | can be steered into writing anything | can only choose among your categories |
| Cost | input **and** output tokens | input tokens only |
| Latency | time to first token plus generation time | one forward pass; about 0.5–1.4 s per request in the two probes above |

**Where Jev falls short:**

- **It can't explain an answer.** When it's wrong you see the probabilities but not the reason, and the only fix is a manual choice, a rule or a clearer `[descriptions]` entry.
- **The API is alpha**, so the request or response shape may change.
- **It only sees the redacted description**, by design. Short or vague descriptions (`SQ *...`, LLC names) don't carry enough signal, and no model can fix that. Those end up in `spend review`.
- **The question costs more than the answer.** The fixed ~600-token prompt dominates the cost. It's still a fraction of a cent per import, but trimming the descriptions makes every request cheaper.
- **Today's data doesn't record any of this.** Confidence, timing and the reason a merchant fell back aren't stored, which is what step 2 adds.

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
