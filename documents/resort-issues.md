# Re-sort Issues (after adding a category)

**Status: resolved** in `594f046` (issue 1) and `b01f88c` (issue 2).

Both came up the first time the re-sort ran on real data, when `Cosmetic` and then `Athletic` were added with `+` in `spend review`. The numbers below come from read-only, count-only queries against that database. No merchant names are recorded here.

## Issue 1: the "left as Other" counts don't add up

### Definition

After adding the first category the re-sort printed **26** merchants "below confidence … left as 'Other'", and Jev placed **2** in the new category. That reads as 24 left in `Other`. After adding the second category the same line said **25**, more than the 24 you'd expect from the first run.

### Analysis

The line came from `classify_with_llm`, which printed a summary of every request it made:

```
N merchant(s) below confidence 0.6; left as 'Other' for review.
```

On **import** this is accurate: only new merchants are asked, and any it isn't sure about become `Other`. The **re-sort** reused the function, but it asks about **every merchant a keyword rule didn't match**. Most of those merchants are already in a real category and keep it when Jev isn't sure. So:

- **N counted every merchant Jev wasn't sure about,** wherever it was, not just the ones in `Other`.
- **The 2 placed merchants were never part of the 26.** They were confident answers, so 26 − 2 doesn't describe anything.
- **N could go up between runs,** because each new category changes Jev's certainty about merchants outside `Other` too. A merchant that clearly fit one category can become a toss-up once a closer one exists.

The database confirms it for the second run. 96 merchants were asked (the 62 matched by keyword rules weren't), and 25 answers weren't confident:

| Where the merchant actually was | Jev unsure | Jev picked `Other` | Total |
|---|--:|--:|--:|
| `Other` (fallback) | 9 | 9 | **18** |
| A category Jev had picked | 2 | 0 | 2 |
| A category you set by hand | 2 | 3 | 5 |
| **Printed as "left as 'Other'"** | | | **25** |

Only 18 of the 25 were actually in `Other`. The first run's 26 can't be broken down the same way, because the second run overwrote those answers, but it came from the same mechanism.

### Resolution

- `classify_with_llm` no longer prints a summary; what an unsure answer means depends on the caller.
- Import prints the same two lines as before, since they're accurate there.
- The re-sort prints a breakdown where each count is a share of the line above it:

```
In 'Other': 20 merchant(s)
  2 placed by Jev:
    … -> Cosmetic
  18 still 'Other' (Jev unsure about 16, no answer for 2); review below
Already categorized: 76 merchant(s)
  2 moved by Jev (it picked them before 'Athletic' existed):
    … (Entertainment) -> Athletic
  1 you set by hand that Jev thinks belong in 'Athletic'; confirm below
  73 keep their category
```

(The numbers in this example are illustrative.)

## Issue 2: a 98%-confident move still asked for approval

### Definition

During the second re-sort, Jev said a merchant belonged in the new `Athletic` category with 98% confidence, yet the program asked for confirmation instead of moving it.

### Analysis

Confidence wasn't the deciding factor; the merchant's **current state** was. The planned policy was "auto-apply on `Other` only": confident answers were applied automatically only for merchants still in `Other`. Any move out of a real category waited for confirmation, whoever had chosen that category.

The database shows **2** merchants in this situation. Both were in `Entertainment` **because Jev had put them there** (source `llm`, not `manual`), and both got a 98% answer for `Athletic`. Both are still in `Entertainment` because the move was skipped.

The confirmation exists so Jev never overrides **you**. But replacing Jev's *own* earlier pick isn't overriding you. That pick was made before `Athletic` existed, and the new answer is better informed. Asking about those moves cost keypresses and protected nothing.

### Resolution

| Merchant's current category was set by | Confident move into the new category |
|---|---|
| Nobody (`Other`) | Applied automatically and listed (unchanged) |
| Jev (`llm` / `memory`) | **Now applied automatically and listed** |
| You (`manual`) | Still waits for confirmation; the prompt now says `(set by you)` |
| A keyword rule | Never sent to Jev (unchanged) |

- **Moves between categories that already existed are still ignored.**
- **The bar is the same `JEV_MIN_CONFIDENCE` as on import.**
- **Undoing a move** is a single `spend set` call.
- **The policy is still conservative:** only answers for the new category move anything.

**Your two `Entertainment` merchants:** their 98% answers are still stored for the current categories, so `uv run spend review --resort Athletic` moves them now without asking Jev again.

## Verification

- **Offline runs:** with canned Jev answers, a scratch database and a scratch copy of `categories.toml`:
  - The import messages are unchanged.
  - The re-sort counts add up at each level.
  - A Jev-categorized merchant moves automatically.
  - A hand-set merchant is proposed, marked `(set by you)`.
- **Checks:** `uv run pre-commit run --all-files` (Ruff, ty, guards) passes.
