# investment-tracker

A personal Telegram bot that tracks a stock watchlist and your holdings, and
flags both a technically attractive **entry point** and reasons to
**consider selling** a stock you hold.

- **5 entry-point setups** — Uptrend Pullback, Momentum + First Dip, Breakout
  Retest, Oversold Reversal, and Deep Pullback — each with its own trigger
  conditions. Scans both your watchlist and your holdings. See
  [docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md](docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md)
  for the exact rules.
- **Sell alerts on your holdings** — three P&L rules against your average
  cost (take-profit, stop-loss, trailing stop) plus 2 technical exit setups
  (Trend Break, Momentum Breakdown). If any of these fire for a ticker, that
  poll's entry alerts for the same ticker are suppressed — one message, not
  two contradictory ones. See
  [docs/superpowers/specs/2026-09-05-sell-alerts-design.md](docs/superpowers/specs/2026-09-05-sell-alerts-design.md).
- **Weekly thesis check** — once a week, Claude reads recent news on each
  holding (via Exa search) and flags it only if something looks like it
  weakens the reason to hold. Silence means nothing found. Optional — needs
  `ANTHROPIC_API_KEY` and `EXA_API_KEY`.
- Alerts fire **once per episode**, not on every check; price/technical
  alerts include a chart.

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
3. (Optional) For `Analyse Apple` and the weekly thesis check, put an
   [Anthropic API key](https://console.anthropic.com/) in `.env` as
   `ANTHROPIC_API_KEY`. Every other command and alert works fine without it.
4. (Optional) For the weekly thesis check, also put an
   [Exa API key](https://dashboard.exa.ai/) in `.env` as `EXA_API_KEY`. Needs
   both keys to run; missing either one just disables that one weekly job.
5. Initialize the database and start:

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
| `TAKE_PROFIT_PCT` | `0.30` | Suggest taking profit this far above your average cost |
| `STOP_LOSS_PCT` | `0.20` | Suggest cutting losses this far below your average cost |
| `TRAILING_STOP_PCT` | `0.15` | Suggest selling this far off the peak price since purchase |

The 5 entry setups' and 2 exit setups' numeric thresholds (return bands,
pullback depth, moving average windows, etc.) are hardcoded in
`app/setup_thresholds.py` rather than `.env` — there are too many to expose
sanely as environment variables. Tune them by editing that file and
restarting the bot. The 3 P&L rules above are `.env`-configurable instead —
there are only three, and they're exactly the numbers you'd want to tune
without touching code.

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
.venv/bin/python -m pytest tests/ -q      # 213 tests, no network required
.venv/bin/python scripts/seed_test_data.py  # prints setup/dedup logic against a fake scenario
```

### Adding a command

Commands dispatch through a registry, so adding one touches three places and
nothing else:

1. Add a pattern in `app/parser.py` returning a new intent string.
2. Add a handler in `app/commands/` (signature: `async def handle(update, context, parsed)`).
3. Register it in `COMMAND_HANDLERS` in `app/commands/__init__.py`.

## Notes

- Daily price history (~2 years per ticker) is cached in memory once a day;
  the 15-minute poll re-fetches only the live price and re-evaluates all 5
  setups against the cached bars — see
  [docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md](docs/superpowers/specs/2026-09-05-entry-point-alerts-design.md).
- Profit/loss is **per share** — the schema records a purchase price but not a
  share quantity, so `Sold X` reports a percentage and per-share delta, not a
  portfolio total.
- Buying the same stock more than once creates separate lots; `Sold X` closes
  them all and reports against the average cost basis.
- `Analyse X` costs a Claude API call (model `claude-sonnet-5`, with web search
  and web fetch) and takes 1-3 minutes — it fetches five years of financials
  itself, then has the model research competitors, moat, growth, management,
  and at least 3 distinct analyst reports. The full report is saved to
  `reports/` as well as sent as a file. Design notes:
  [docs/plans/2026-08-08-analyse-company-command.md](docs/plans/2026-08-08-analyse-company-command.md).
