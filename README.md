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

## Running on a server

You can keep the dashboard running on a home server (e.g. a small Debian container: 1 vCPU, 1 GB RAM) and open it from your own computer. This assumes you can SSH into the server and reach it over a VPN that already handles sign-in, such as Tailscale or WireGuard.

> [!WARNING]
> The dashboard has no login. Anyone who can reach it can see your transactions and change your categories, so it must never be reachable from outside your VPN.

**1. Set it up.** Install [uv](https://docs.astral.sh/uv/), clone the repo and run `uv sync`, as an ordinary user rather than root. Then copy over your existing setup instead of starting again: `.env`, `categories.toml` and `data/` (the database and `plaid-tokens.json`). Copying the token keeps your bank connected without a new connection, which matters because the Trial plan allows only 10.

```bash
scp -r .env categories.toml data/ user@server:spending-tracker/
ssh user@server 'cd spending-tracker && chmod 700 data && chmod 600 .env data/*'
```

A server has no OS keyring, so the token stays in `data/plaid-tokens.json`, readable only by that user. If your computer keeps it in its keyring instead (there's no `data/plaid-tokens.json`), write it to that file first, on your computer, before copying `data/`:

```bash
uv run python -c "from spending import db, plaid_source as p; p._write_tokens({i['item_id']: p.load_token(i['item_id']) for i in db.get_plaid_items(db.connect())})"
```

Without the token, the server's dashboard offers **Sign in to your bank**, which is a new connection.

**2. Keep it running** with a systemd unit, e.g. `/etc/systemd/system/spending-dashboard.service`:

```ini
[Unit]
Description=Spending Tracker dashboard
After=network-online.target
Wants=network-online.target

[Service]
User=spend
WorkingDirectory=/home/spend/spending-tracker
ExecStart=/home/spend/.local/bin/uv run spend dashboard
# No browser to open, and no first-run prompt.
Environment=STREAMLIT_SERVER_HEADLESS=true
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now spending-dashboard
journalctl -u spending-dashboard -f   # its output
```

Other Streamlit settings go in the unit the same way, as `STREAMLIT_*` variables (e.g. `STREAMLIT_SERVER_PORT=8502`).

**3. Open it through an SSH tunnel.** This needs no other changes, and it's the recommended way:

```bash
ssh -L 8501:127.0.0.1:8501 user@server
```

Then open `http://localhost:8501`. The dashboard still only listens on the server itself, and your browser sees `localhost`, so signing in to your bank works as it does locally. Run `spend review`, `spend set` and `spend import` in the same SSH session, and copy CSVs over with `scp`.

### Opening it over the VPN without a tunnel

Set these in the server's `.env` and restart the service:

```bash
DASHBOARD_ADDRESS=100.64.0.5                      # the server's VPN address
DASHBOARD_HOSTS=myserver.example.ts.net,100.64.0.5  # what you type in the browser
```

- `DASHBOARD_ADDRESS` is where it listens. Use the VPN interface's own address, not `0.0.0.0`, which would include every network the server is on.
- `DASHBOARD_HOSTS` lists every hostname or address you open it with. It adds to `localhost` and `127.0.0.1`, so the tunnel keeps working, and every other name is refused, which blocks DNS rebinding. `*` isn't allowed.

`spend dashboard` warns when it listens beyond the server itself. The settings apply only when it's started with `spend dashboard`, not with `streamlit run`.

**Bank sign-in** from a plain-`http` address other than `localhost` is untested. Plaid only documents an HTTPS requirement for OAuth redirects, which this app doesn't use. If **Sign in to your bank** fails there, sign in through the SSH tunnel instead (the token is saved on the server either way), or put HTTPS in front with [`tailscale serve`](https://tailscale.com/kb/1312/serve) or Caddy with an internal certificate. With a proxy like that, leave `DASHBOARD_ADDRESS` unset so the dashboard only listens on the server, point the proxy at `http://127.0.0.1:8501`, and put the proxy's hostname in `DASHBOARD_HOSTS`.

## Privacy

- Only a store's description goes to Jev, and only the first time it's seen, with account, reference and phone numbers masked (`SQ *SHOP #### GRAND RAPIDS MI`). Amounts, dates, balances and card payments never leave your computer. Jev is used through zero-data-retention endpoints.
- Your bank password goes only to your bank's own page. Plaid reads only the accounts you share, and only credit accounts are stored.
- Plaid's access token is kept in your OS keyring, or where there is none (as in WSL) in `data/plaid-tokens.json`, readable only by you.
- `data/` and the database are readable only by you. For a setup from before that, run `chmod 700 data && chmod 600 data/*`.
- The dashboard only accepts connections from this computer, and sends no usage statistics. On a server, `DASHBOARD_ADDRESS` can open it to your VPN: then anyone on the VPN can reach it, since it has no login (see [Running on a server](#running-on-a-server)).

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
