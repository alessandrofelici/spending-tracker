# Jev Review

**Status: implemented** ([#4](https://github.com/alessandrofelici/spending-tracker/issues/4)) in [#9](https://github.com/alessandrofelici/spending-tracker/pull/9). Kept as a reference: what Jev is, how the app uses it, and whether it was the right choice. The dashboard's Categorization tab shows the same measurements for your own data.

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
- **Most of the cost is the question, not the merchant.** About 600 of the 623 input tokens are the instructions and the 13 category descriptions, which are resent with every request. That works out to about $0.000026 per merchant, or $0.04 per million input tokens.
- `output_tokens` is reported but not billed.

## How the app uses it

Jev is the **last resort**: a transaction only reaches it after your choices, the local payment check and merchant memory (the full order is in [README.md](../README.md#how-categorization-works)). In `src/spending/classify.py`, `categorize()` collects the merchants left over, and `classify_with_llm()` asks Jev about each with `_decide()`:

- **One request per unique merchant, not per transaction.** A merchant you visit 20 times costs one request, and the answer is saved to `merchant_memory`, so it's never asked again.
- **Redacted before sending.** `redact()` masks any token with 3+ digits (store numbers, phone numbers, references). Only the description goes out: no amount, date or account. Payment rows are caught locally first, so they never reach Jev.
- **Criteria come from `categories.toml`.** `load_criteria()` turns the `[descriptions]` table into `criteria`, so editing a description directly changes what Jev is choosing between.
- **8 requests in parallel** (`WORKERS`), each in its own request, so one odd description can't affect another's answer.
- **One retry** on a network or parse error. After that the merchant counts as failed.
- **Checked again locally.** An answer that isn't one of your categories is rejected.
- **Unsure answers go to review.** If the answer is `Other` or `confidence < JEV_MIN_CONFIDENCE` (0.6 by default), the merchant becomes `fallback` and isn't remembered, so you decide it in `spend review`.
- **Zero data retention only** (`provider.zdr`).

## Why Jev instead of a chat LLM

Below is the case for Jev. The next two sections test it with real numbers.

| | Chat LLM | Jev |
|---|---|---|
| Output | free text; you prompt for JSON and hope | a typed answer from your list |
| Validity | can invent categories, misspell them or add prose; needs parsing and retries | always one of `criteria` |
| Uncertainty | no reliable signal; asking it to rate its own confidence doesn't produce a calibrated number | a probability per category plus a confidence, which drive the 0.6 threshold |
| Prompt injection via the description | can be steered into writing anything | can only choose among your categories |
| Cost | input **and** output tokens | input tokens only |
| Latency | time to first token plus generation time | no generation step; p50 216 ms per request (see [Speed and cost](#speed-and-cost-jev-vs-a-chat-llm)) |

**Where Jev falls short:**

- **It can't explain an answer.** When it's wrong you see the probabilities but not the reason, and the only fix is a manual choice or a clearer `[descriptions]` entry.
- **The API is alpha**, so the request or response shape may change.
- **It only sees the redacted description**, by design. Short or vague descriptions (`SQ *...`, LLC names) don't carry enough signal, and no model can fix that. Those end up in `spend review`.
- **The question costs more than the answer.** The fixed ~600-token prompt dominates the cost. It's still a fraction of a cent per import, but trimming the descriptions makes every request cheaper.
- **Older imports have no decision records.** Confidence, timing and the reason a merchant fell back are only stored (in `jev_answers`) for merchants asked after this change.

## Accuracy: agreement with keyword rules

Imports no longer use keyword rules; the `[rules]` in `categories.toml` are kept only as these labels.

`uv run python -m spending.jev_eval` (run 2026-10-07 on the real DB). Every merchant a keyword rule had already categorized was sent to Jev exactly as an import would send it, and Jev's answer was compared with the rule's. That's 59 merchants; payment rows are left out so they stay local. The rules serve as labels we already have, though they aren't always right themselves.

| | Result |
|---|---|
| Jev agrees with the rule | **55 / 59 (93%)** |
| Confident answers only (what an import keeps) | **53 / 55 (96%)** |
| Outcome | confident 55, unsure 3, chose_other 1, failed 0 |

| Rule category | Agree / total |
|---|--:|
| Dining | 29 / 29 |
| Shopping | 8 / 9 |
| Subscriptions | 5 / 5 |
| Gas & Transport | 5 / 5 |
| Groceries | 4 / 4 |
| Education | 1 / 3 |
| Entertainment | 2 / 2 |
| Travel | 0 / 1 |
| Health | 1 / 1 |

The four disagreements:

| Merchant | Rule says | Jev says (confidence, outcome) | Who's right? |
|---|---|---|---|
| A warehouse club's gas station | Shopping (`COSTCO`) | Gas & Transport (1.00, confident) | **Jev**: the rule is too broad |
| A university police department | Education (`MSU `) | Other (0.64, chose_other) | **Jev**: probably a parking ticket or fine, not education. It would go to review. |
| A university bike repair shop | Education (`MSU `) | Gas & Transport (0.60, confident) | **Jev**: bike repair is transport |
| A rail ticket purchase | Travel (`AMTRAK`) | Gas & Transport (0.50, unsure) | **Rule**, but Jev wasn't confident, so an import would send it to review rather than mislabel it |

**Takeaway:** where Jev disagreed with the rules, it was usually catching a rule that's too broad (`COSTCO`, `MSU `). Its only real miss came back below the threshold, so the confidence check worked as designed. These results are why rules no longer categorize imports: Jev alone does as well or better, and too-broad rules were the main source of mistakes.

## Speed and cost: Jev vs a chat LLM

### Method

**Jev is measured.** The eval above timed every request. The numbers are wall-clock times from this machine, network included, with `WORKERS = 8`:

- per request: **p50 216 ms, p95 446 ms, max 492 ms**
- 59 merchants: **1.9 s** in total

**The chat LLM is estimated** per merchant as:

```
time = TTFT + output_tokens / output_speed
cost = input_tokens × input_price + output_tokens × output_price
```

| Input | Value | Where it comes from |
|---|---|---|
| input tokens | **246** | The same instructions, the 13 category descriptions and one description as a chat prompt, plus "reply with only JSON". Counted with `tiktoken` `o200k_base`; other models' tokenizers differ by roughly ±20%. Jev reports **623** for the same information, because its own request format adds overhead. |
| output tokens | **8** (bare JSON) or **42** (JSON + one sentence of reasoning) | Counted the same way, on `{"category": "Gas & Transport"}` and on the same answer with a short `reasoning` field |
| Fast non-reasoning model: Claude 4.5 Haiku | TTFT **0.59 s**, **90 tok/s** | [Artificial Analysis](https://artificialanalysis.ai/models/claude-4-5-haiku/providers), Anthropic endpoint, P50 over 72 h, checked 2026-10-07. Haiku 5.5 was released on 2026-10-07 and has no published benchmarks yet, so 4.5 stands in for it. |
| Reasoning model: Gemini 3.8 Flash (high) | TTFT **24.33 s**, **129 tok/s** | [Artificial Analysis](https://artificialanalysis.ai/models/gemini-3-8-flash/providers), the only setting listed; TTFT includes thinking time. Checked 2026-10-07. |
| Prices per 1M tokens (input / output) | Jev $0.042 / free · Haiku 5.5 $0.10 / $0.50 · Gemini 3.8 Flash $0.375 / $1.875 | OpenRouter `/api/v1/models/.../endpoints`, cheapest endpoint, 2026-10-07 |
| Merchants per import | **96** on the first import; **1–19** per month after that (median ≈ 10) | The real DB: merchants not matched by a rule, by the month they first appeared |
| Retries for unparseable or off-list replies | **ignored** | An assumption. At a few percent it would add only a few percent of one round. |

**Caveats:**

- Artificial Analysis measures TTFT with a 10,000-token prompt, while ours is 246 tokens. Real chat TTFT is therefore probably *lower* than in the table, so the chat estimates below are on the slow side.
- Thinking tokens for the reasoning model are billed as output but not published per request, so its cost below is a floor.

### Results

At 8 requests in parallel, an import takes ⌈merchants / 8⌉ rounds of one request each.

| Per merchant | Time | Cost |
|---|--:|--:|
| **Jev** (measured p50) | **0.22 s** | **$0.000026** (measured `usage.cost`) |
| Haiku, bare JSON | 0.68 s | $0.000029 |
| Haiku, JSON + reasoning | 1.06 s | $0.000046 |
| Gemini 3.8 Flash (high) | 24.4 s | ≥ $0.000107 + thinking |

| Per import | Jev | Haiku (JSON) | Haiku (+ reasoning) | Gemini Flash (high) |
|---|--:|--:|--:|--:|
| Typical month, ~10 merchants (2 rounds) | **~0.4 s** | ~1.4 s | ~2.1 s | ~49 s |
| First import, 96 merchants (12 rounds) | **~2.6 s** (59 measured in 1.9 s) | ~8.2 s | ~12.7 s | ~4.9 min |

### Is the difference noticeable?

- **Compared with a reasoning model: yes.** Seconds instead of minutes on a first import, and close to a minute saved every month.
- **Compared with a fast non-reasoning chat model: barely.** Jev is about 3× faster per request, but for a monthly import that's under a second against about a second and a half. Cost per merchant is about the same, a few thousandths of a cent.

**So speed and cost aren't the strongest reasons to use Jev here. The output contract is:**

- **Every answer is one of your categories.** A chat model needs a JSON-parsing step plus retry and validation code, and still occasionally invents a label.
- **Each answer comes with a confidence.** That number decides what goes to `spend review`, and in the eval it caught the one real mistake (Amtrak, 0.50). A chat model has no comparable signal; asking it to rate its own confidence doesn't produce a calibrated number.
- **The description can't steer the output.** The worst a malicious description can do is pick the wrong category from your list.
