# investment-tracker

A personal Telegram bot that tracks a stock watchlist and your holdings, and
alerts you when something moves sharply.

- **Watchlist alerts** — pings you when a watched stock **drops ≥10%** in 12 hours.
- **Holdings alerts** — pings you when a stock you own **rises ≥10%** in 12 hours.
- Alerts fire **once per move**, not on every check, so a stock hovering at
  -10% doesn't spam you.
- **Daily politician-trades digest** — every morning, screens what members of
  Congress disclosed trading in the last day and messages the top 3 ranked
  purchases; you reply with which ones get the full `Analyse`-style report.

Everything is driven by plain messages — no command syntax to memorize.

## Commands

| Send | Does |
|---|---|
| `Watch Apple` | Adds to your watchlist |
| `Remove Apple` | Removes from your watchlist |
| `Bought Apple` | Records a purchase at the current market price |
| `Bought Apple for $150` | Records a purchase at a price you specify |
| `Sold Apple` | Closes the position and reports profit/loss |
| `Analyse Apple` | Sends a full equity research report — quick take in chat, full report as a file |
| `/list` | Shows your watchlist and holdings |

Also accepted: `Add`/`Track`/`Follow` for watching, `Delete`/`Unwatch`/`Drop`/
`Stop watching` for removing, `Buy`/`Purchased` and `Sell` for trades,
`Analyze`/`Research` for analysis.

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
3. (Optional) For `Analyse Apple` and the daily politician-trades digest's
   full reports, put an [Anthropic API key](https://console.anthropic.com/)
   in `.env` as `ANTHROPIC_API_KEY`. Every other command works fine without it.
4. Initialize the database and start:

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
| `TIMEZONE` | `Europe/London` | Timezone the daily politician-trades digest fires in |
| `DAILY_DIGEST_TIME` | `07:00` | Local time (`HH:MM`) the digest fires at |
| `SENATE_SOURCE_PROVIDER` | unset | Optional — see "Politician-trades digest" below |

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
.venv/bin/python -m pytest tests/ -q      # 150 tests, no network required
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
- `Analyse X` costs a Claude API call (model `claude-opus-5`, with web search
  and web fetch) and takes 1-3 minutes — it fetches five years of financials
  itself, then has the model research competitors, moat, growth, management,
  and at least 3 distinct analyst reports. The full report is saved to
  `reports/` as well as sent as a file. Design notes:
  [docs/plans/2026-08-08-analyse-company-command.md](docs/plans/2026-08-08-analyse-company-command.md).

### Politician-trades digest

Every morning at `DAILY_DIGEST_TIME` (`TIMEZONE`), the bot checks the official
House Clerk disclosure index and the White House's own disclosures page for
Periodic Transaction Reports filed in the last day (House members and the
President — Trump's PTRs are Form 278-T filings published directly at
whitehouse.gov/disclosures/), extracts each trade (Claude Haiku reads the
scanned PDF directly — these filings have no extractable text), and scores
every **purchase** itself in plain Python (amount disclosed, how fast it was
filed, whether multiple filers bought the same ticker). No Claude call runs
for the screening itself — it costs a fraction of a cent per filing (PDF
reads only), and $0 beyond that unless you ask for more.

You get a message with the top 3 ranked picks. Reply with:

| Reply | Does |
|---|---|
| `1 3` (or `1, 3` / `1 and 3`) | Runs the full `Analyse`-style report on picks #1 and #3 |
| `all` | Runs it on all 3 |
| `skip` (or `none`) | Dismisses today's picks, no reports run |

Each report you request costs the same Claude API call as `Analyse` (1-3
minutes) and is saved to `reports/politician-trades/`.

**Coverage:** House + the President today. No Senate yet — there's no working
free source for Senate disclosures (their own site blocks automated access,
and the free third-party mirrors that used to cover it are dead).
`SENATE_SOURCE_PROVIDER` is a placeholder for wiring up a paid provider
later; nothing else needs to change to add one. (Quiver Quantitative sells
API access to this same politician-trades data, including a Trump-specific
tracker — deliberately not used here since scraping their site would
violate their Terms of Service and their trade tables aren't even
server-rendered; the President's own filings are published directly and
freely by the White House instead.) Design notes:
[docs/plans/2026-08-08-politician-trades-digest.md](docs/plans/2026-08-08-politician-trades-digest.md).
