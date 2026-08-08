# investment-tracker

A personal Telegram bot that tracks a stock watchlist and your holdings, and
alerts you when something moves sharply.

- **Watchlist alerts** — pings you when a watched stock **drops ≥10%** in 12 hours.
- **Holdings alerts** — pings you when a stock you own **rises ≥10%** in 12 hours.
- Alerts fire **once per move**, not on every check, so a stock hovering at
  -10% doesn't spam you.

Everything is driven by plain messages — no command syntax to memorize.

## Commands

| Send | Does |
|---|---|
| `Watch Apple` | Adds to your watchlist |
| `Remove Apple` | Removes from your watchlist |
| `Bought Apple` | Records a purchase at the current market price |
| `Bought Apple for $150` | Records a purchase at a price you specify |
| `Sold Apple` | Closes the position and reports profit/loss |
| `/list` | Shows your watchlist and holdings |

Also accepted: `Add`/`Track`/`Follow` for watching, `Delete`/`Unwatch`/`Drop`/
`Stop watching` for removing, `Buy`/`Purchased` and `Sell` for trades.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

1. Create a bot via [@BotFather](https://t.me/BotFather) (`/newbot`) and put the
   token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message, then run
   `.venv/bin/python scripts/get_chat_id.py` and put the printed ID in `.env`
   as `TELEGRAM_CHAT_ID`. (Do this *before* starting the bot — a running bot
   consumes the updates this script reads.)
3. Initialize the database and start:

```bash
.venv/bin/python scripts/init_db.py
.venv/bin/python bot.py
```

The bot only responds to `TELEGRAM_CHAT_ID`; messages from anyone else are
ignored.

## Configuration

Set in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `POLL_INTERVAL_MINUTES` | `15` | How often prices are checked |
| `WATCHLIST_DROP_THRESHOLD` | `-0.10` | Drop that triggers a watchlist alert |
| `HOLDING_GAIN_THRESHOLD` | `0.10` | Rise that triggers a holdings alert |
| `TRAILING_WINDOW_HOURS` | `12` | Lookback window for the change calculation |

To see an alert immediately instead of waiting for a real 10% move, set the
thresholds near zero and `POLL_INTERVAL_MINUTES=1`, then restart.

## Deployment

The bot makes only outbound connections (Telegram long-polling), so it needs
no public IP, domain, open ports, or reverse proxy — any always-on Linux box
with internet access works, including a Raspberry Pi.

On a fresh Ubuntu 24.04 server, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/voncont9000/investment-tracker/main/deploy/setup.sh | bash
```

That installs dependencies, creates an unprivileged `tracker` user, prompts
for your bot token and chat ID, initializes the database, and installs a
systemd service that restarts on crash and starts on boot.

| Task | Command |
|---|---|
| Watch logs | `journalctl -u investment-tracker -f` |
| Restart | `systemctl restart investment-tracker` |
| Stop | `systemctl stop investment-tracker` |
| Status | `systemctl status investment-tracker` |
| Deploy new code | re-run the `curl ... \| bash` line |

Re-running setup pulls the latest code and restarts the service; it will not
overwrite your `.env` or database.

## Development

```bash
.venv/bin/python -m pytest tests/ -q      # 68 tests, no network required
.venv/bin/python scripts/seed_test_data.py  # prints alert logic against fake scenarios
```

### Adding a command

Commands dispatch through a registry, so adding one touches three places and
nothing else:

1. Add a pattern in `app/parser.py` returning a new intent string.
2. Add a handler in `app/commands/` (signature: `async def handle(update, context, parsed)`).
3. Register it in `COMMAND_HANDLERS` in `app/commands/__init__.py`.

## Notes

- Prices come from Yahoo Finance via `yfinance` (free, no API key). The 12-hour
  baseline is read from Yahoo's intraday history rather than locally stored
  snapshots, so a stock added a minute ago can still alert immediately.
- Profit/loss is **per share** — the schema records a purchase price but not a
  share quantity, so `Sold X` reports a percentage and per-share delta, not a
  portfolio total.
- Buying the same stock more than once creates separate lots; `Sold X` closes
  them all and reports against the average cost basis.
