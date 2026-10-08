# Extend Review Command

**Status: implemented** ([#2](https://github.com/alessandrofelici/spending-tracker/issues/2)). Usage is in README.md, under `spend review`.

## Motivation
- User can self categorize transactions jev is not confident about
- For new users, or significant spending changes, this can be much easier to add a new category which didn't exist before

## Specifications
- In the case the user wants to add a new category, a certain command should be run which will handle all transactions labeled as "other"
- When running through the data the first time, consider making this latter step easier by flagging "other" or similarly acting on this data

## Decisions
- **Flagging on import:** Jev's answer is saved for every merchant it's asked about (table `jev_answers`: pick, confidence, outcome `confident | unsure | chose_other | failed`, and a hash of the categories + descriptions it chose from). Review shows the pick as a `[suggestion]` that Enter accepts; `s` skips.
- **No repeat requests:** import doesn't re-ask a merchant Jev couldn't place until the categories or their descriptions change. Failed requests are retried.
- **Adding a category:** `+` (or `+Name`) at any review prompt. Written to `categories.toml` with tomlkit so comments survive, inserted before `Other`. Duplicate names (any case) are refused.
- **Re-sort after adding:** Jev is asked again about every merchant a keyword rule didn't match. Confident answers for merchants in `Other` are applied automatically only when they pick the new category (a confident pick of an existing category becomes the review suggestion), and so are confident moves into the new category for merchants Jev had categorized itself. Moves of merchants you set by hand (including manual `Other`) wait for confirmation. Moves between categories that already existed are ignored. (Changed after first use; see `resort-issues.md`.)
- **Rule-matched merchants are never sent,** to keep payments and other rule matches inside the privacy boundary. They can still be moved with `spend review --all` or `spend set`.
- **`spend review --resort [CATEGORY]`** runs the same re-sort without adding a category (e.g. after editing `categories.toml` by hand). Without CATEGORY it only retries `Other`.
