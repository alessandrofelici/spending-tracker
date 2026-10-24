# Spending Tracker

See where your money goes: your MSUFCU credit card's transactions, categorized and charted in a dashboard on your own computer.

Everything runs locally. [Plaid](https://plaid.com) reads the card's transactions from your bank, and [Jev](https://openrouter.ai/docs/guides/community/jev) (via OpenRouter) gets a redacted name for stores it hasn't seen before. Nothing else leaves your machine (see [Privacy](#privacy)).

## Setup

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env                        # then add your keys (below)
cp categories.example.toml categories.toml  # then make it fit your spending
chmod 600 .env                              # it holds a bank-access secret
```

**`.env`:**

- `OPENROUTER_KEY` from [openrouter.ai/keys](https://openrouter.ai/keys), so Jev can categorize new stores. Without it, they wait for you in `spend review`.
- `PLAID_CLIENT_ID`, `PLAID_SECRET` and `PLAID_ENV`, for bank sync. Create a free account at [dashboard.plaid.com](https://dashboard.plaid.com/signup) and copy the keys from [Developers → Keys](https://dashboard.plaid.com/developers/keys). `PLAID_ENV=sandbox` (Plaid's test banks) works right away; for your real bank, apply for the free **Trial plan** ("Personal use") and use `production`. Each environment has its own secret.

**`categories.toml`** is yours (git-ignored). Drop the categories you don't need, add the ones you do, and make each `[descriptions]` line say what belongs there: that's what Jev reads.

Then open the dashboard and click **Sign in to your bank**:

```bash
uv run spend dashboard
```

Pick your bank and sign in on its own page. When asked which accounts to share, pick only your credit card. In the sandbox, sign in with `user_good` / `pass_good`.

## Using it

`uv run spend dashboard` is the whole routine. Each time it opens, it gets new transactions from your bank, categorizes them, and shows the month's total against the month before, spending by category and month by month, your top merchants, and every transaction. It runs at `http://127.0.0.1:8501`; stop it with Ctrl+C.

### Fixing categories

When the dashboard says some transactions need categorizing, or one looks wrong:

```bash
uv run spend review                 # go through the stores Jev couldn't place
uv run spend review --all           # ...and the ones it did, to check them
uv run spend set kroger Groceries   # one store directly; part of its name works
```

In `review`, type a category's number, Enter to accept Jev's `[suggestion]`, `+` to add a new category, `s` to skip or `q` to stop. Your choice applies to all of that store's transactions, past and future, and Jev never overrides it. After `+`, Jev is asked again, and stores that fit the new category move into it (ones you set yourself are only proposed).

If `spend set` matches several stores, it lists them: use the exact name, or add `--all-matches` to set them all.

### Without bank sync

Download your transactions from MSUFCU Online Banking (Download Transactions, as CSV), then:

```bash
uv run spend import statements/*.csv
```

Importing the same file again adds nothing twice. Once your bank is connected, don't import CSVs with later dates: those transactions would be counted twice.

### Your bank connection

Now and then your bank ends the connection. The dashboard then shows a **Sign in again** button: use it rather than connecting from scratch, since on Plaid's Trial plan every new connection uses up one of 10 for good.

To disconnect:

```bash
uv run spend unlink
```

It revokes the app's access at Plaid and deletes the access token; your transactions stay. Check your bank's connected-apps settings too, since some banks still list the app.

## Privacy

- Only a store's description goes to Jev, and only the first time it's seen, with account, reference and phone numbers masked (`SQ *SHOP #### GRAND RAPIDS MI`). Amounts, dates, balances and card payments never leave your computer. Jev is used through zero-data-retention endpoints.
- Your bank password goes only to your bank's own page. Plaid reads only the accounts you share, and only credit accounts are stored.
- Plaid's access token is kept in your OS keyring, or where there is none (as in WSL) in `data/plaid-tokens.json`, readable only by you.
- `data/` and the database are readable only by you. For a setup from before that, run `chmod 700 data && chmod 600 data/*`.
- The dashboard only accepts connections from this computer, and sends no usage statistics.

## Troubleshooting

| Problem | Fix |
|---|---|
| `categories.toml not found` | `cp categories.example.toml categories.toml` |
| No "Connect your bank" card | `PLAID_CLIENT_ID` and `PLAID_SECRET` aren't both in `.env`. Restart the dashboard after editing it. |
| `Couldn't start the sign-in` | Wrong keys, or `PLAID_ENV` doesn't match the secret. |
| `Plaid is still getting your transactions` | A new connection's first sync isn't ready yet. Open the dashboard again in a few minutes. |
| Your bank `needs you to sign in again` | Click **Sign in again**. |
| Many transactions left as `Other` | Check `OPENROUTER_KEY` in `.env`, make the `[descriptions]` in `categories.toml` clearer, or add a category with `+` in `spend review`. |

## Development

Setup, tests, the code layout and feature proposals are in [CONTRIBUTING.md](CONTRIBUTING.md).
