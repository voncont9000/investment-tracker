# `Analyse COMPANY` — LLM-powered equity research report

## Context

The bot currently only tracks prices and thresholds — it has no way to answer "should I actually buy this?" The idea of an on-demand `Analyse AAPL` command was already sketched in the very first planning session (see the "Stock analysis via subagent" note in the earlier plan doc), and `.env.example` already carries a commented-out placeholder: `ANTHROPIC_API_KEY=  # only needed once the "analyze" command is built`. This increment builds that command.

Vincent supplied a detailed 8-section analyst-report prompt (Executive Summary → Financial Performance → Valuation → Business Model & Moat → Growth → Management → Risk → Recommendation) and asked to optimize it alongside designing the feature. Three things from the prompt as given don't survive contact with reality and are addressed below:

1. It asks for exact 5-year financials and multiples vs. history/industry/competitors — accurate as of *right now*, which is past any model's training cutoff. That data has to come from real tool calls, not the model's memory.
2. It has literal blanks (`[Competitor A]`, `[Competitor B]`, `[Competitor C]`) — the bot has no way to fill those in ahead of time, so the model must identify competitors itself.
3. The rendered report is several thousand words — far past Telegram's 4096-character message cap.

**Confirmed decisions from clarifying questions:**
- **Delivery:** a short in-chat summary + verdict, then the full report sent as a markdown file attachment.
- **Cost guardrail:** cap tool-call turns (the web-research tools get a hard `max_uses`).
- **File handling:** markdown file, sent to the user *and* saved to a local `reports/` folder for future reference.
- **Depth:** the research needs to be genuinely thorough, including actually reading multiple sell-side analysts' published reports/ratings/price targets and comparing them against each other — not just current multiples, and not just a single aggregator page that lists several banks' targets. This is handled by giving the model both a search tool and a fetch tool plus an explicit "Analyst Coverage" section (below), not by adding custom tools — see the architecture note.

## Architecture — simpler than the original "subagent" idea

The earlier plan doc sketched Tool Runner + custom tools wrapping `prices.py`. Working through it in detail, that's more machinery than this task needs. The insight: the *quantitative* data (5 years of financials, current multiples) is something **we already know we need for this ticker regardless of what the model decides** — so fetch it deterministically in Python, the same way `app/prices.py` does for the alert loop, and embed it directly in the prompt. Reserve the model's own tool-calling for the genuinely open-ended research: competitors, moat, TAM, management, recent catalysts, and analyst coverage.

That means:
- **No custom tools, no Tool Runner.** One `client.messages.create()` call (Claude API, not Managed Agents — this is a single self-contained report, not a long-lived stateful workflow) with two **server-side tools** declared: `web_search_20260209` to find sources, and `web_fetch_20260209` to actually open and read them. Both are server-side, so there's no client-side tool-execution loop to write.
- **Why both tools, not just search:** a plain search often surfaces a single ratings-aggregator page (Yahoo Finance, TipRanks, MarketBeat) that lists several banks' price targets on one page. The model could "compare 3 analysts" from that one page without ever reading a single analyst's actual reasoning — that's not real comparison, it's transcription. Giving it `web_fetch` and instructing it explicitly (see the prompt below) to open the individual news articles/notes behind each analyst's call — not just cite the aggregator listing — is what makes "search through different analyst reports" actually mean reading different reports.
- **Resumption for `pause_turn`.** A server-tool turn can stop after 10 internal search/fetch iterations combined with `stop_reason: "pause_turn"` before either tool's `max_uses` is reached. Given how many searches+fetches "read 3+ distinct analyst reports plus competitor/moat/TAM/management/news research" implies, this will very likely be hit at least once per report. Per the API docs, resuming is just resending `[original_user_message, {"role": "assistant", "content": response.content}]` — no "Continue" message needed. Loop this up to a cap (e.g. 4 resumes) so a stalled analysis can't run away, while still leaving room for a genuinely research-heavy report to finish.
- **Search/fetch budget:** `max_uses: 25` on `web_search`, `max_uses: 15` on `web_fetch` — enough to find and actually open 3+ distinct analyst sources plus cover competitors/moat/TAM/management/news, without being unbounded.
- **Model:** `claude-opus-5`, `output_config={"effort": "high"}` (financial analysis is intelligence-sensitive; `xhigh` is available later as a cost/quality knob if reports still feel shallow at this depth). Adaptive thinking stays on by default — no `thinking` param needed.
- **`max_tokens`:** 12000 (non-streaming) — a report this thorough, with a dedicated analyst-comparison section, runs longer than a bare-bones one; still comfortably under the ~16K threshold where streaming becomes necessary.
- **Async execution.** The Anthropic call plus 15-25 web searches/fetches will take well over a minute — far too long for a Telegram handler to block on. The handler resolves the ticker, sends an immediate "🔎 Analyzing… this may take a couple of minutes" ack, then does the real work in `asyncio.create_task(...)` (wrapping the blocking yfinance + Anthropic calls in `asyncio.to_thread`) so it doesn't stall the bot's price-poll job or other commands. The report and file are sent from that background task when done.

## New module: `app/fundamentals.py`

Same shape and error-handling style as `app/prices.py` (each getter swallows exceptions and returns `None`/partial data on failure — a ticker with thin data shouldn't crash the report, it should just say so).

- `get_income_statement_summary(ticker)` — `yf.Ticker(ticker).financials` (annual) + `.quarterly_financials` (for a TTM sum). Extracts revenue, gross profit, operating income, net income; **computes margins in Python**, not in the prompt — per-year revenue growth % and gross/operating/net margin %. (Prompt-engineering note: asking the model to divide multi-year raw figures itself is exactly the kind of arithmetic that should live in code, not in the model's head.)
- `get_balance_sheet_summary(ticker)` — `.balance_sheet`: total debt, equity, current assets/liabilities, cash. Computes debt-to-equity and current ratio.
- `get_cash_flow_summary(ticker)` — `.cashflow`: operating cash flow, capex. Computes free cash flow = OCF − capex.
- `get_valuation_snapshot(ticker)` — `.info`: trailing/forward P/E, P/S, P/B, EV/EBITDA, market cap, sector, industry, business summary, employee count, insider/institutional ownership %.
- `build_fundamentals_snapshot(ticker)` — calls all of the above and renders one markdown block to embed in the prompt, explicitly noting any field that came back missing (e.g. `"Free cash flow: not available"`) rather than letting a gap look like a zero.

**Known, worth stating up front:** yfinance's free annual statements typically return ~4 fiscal years, not 5 — a real data-source limit, not a bug. The prompt is worded to say "based on available data" rather than promising five years it may not have.

**Deliberately out of scope:** a 5-year *own-average* P/E and industry-average multiples would require reconstructing historical trailing EPS against historical prices — real engineering effort for a personal tool. Instead, the prompt asks the model to research those comparisons via web search/fetch, the way a human analyst would pull comps from a source rather than compute them from scratch. This is a real quality tradeoff (approximate vs. computed), stated explicitly in the report structure below (§3) so it's not silently glossed over.

## The optimized prompt

Kept the 8-section structure — it's a deliberate, fixed template the user wants, which is exactly the case where format-pinning is worth keeping rather than trimming. Changes from the original: removed the unfillable competitor blanks (§3 has the model identify them), added the Quick Take/`---` split for Telegram delivery, pointed it at the embedded data instead of memory, added a citation expectation for web-sourced qualitative claims, cut repeated "be objective/data-driven" framing down to one statement, and — per the depth requirement above — added an explicit **Analyst Coverage** section requiring the model to actually fetch and read *multiple, distinct* published analyst reports rather than transcribing a single ratings-aggregator page.

```
Act as a senior equity research analyst. Compile an investment analysis report on {company_name} (Ticker: {ticker}), using the financial data provided below plus web research for everything not in that data. Be objective and specific — cite a source (publication or site name) for every claim that comes from web research, and never invent a number; if something isn't in the data below and you can't find it, say so rather than estimating.

FINANCIAL DATA (fetched programmatically, {as_of_date}):
{fundamentals_snapshot}

Structure your response in exactly two parts, in this order:

PART 1 — Quick Take (3-5 sentences, for a chat message)
State the buy/hold/sell call at the current price, then the single biggest reason for it and the single biggest risk against it.

Then a line containing only: ---

PART 2 — Full Report
1. Executive Summary — one sentence on the business, then the investment thesis (2-3 sentences) with the buy/hold/sell call, key catalysts, and major risks.
2. Financial Performance & Health — using the financial data above: revenue growth, margin trends, and FCF generation over the years available (may be fewer than 5 if the data source doesn't go back further — say so if it applies). Balance sheet strength from the debt/equity and current ratio figures given.
3. Valuation — current P/E, P/S, P/B, and EV/EBITDA from the data above. Research (via web search) how these compare to the company's own historical average and to its top 2-3 direct competitors (identify them yourself) and industry norms. State plainly that historical/peer comparisons are researched estimates, not computed figures. Conclude overvalued/undervalued/fair.
4. Analyst Coverage — find and actually read (fetch, don't just cite a ratings-aggregator summary page) at least 3 distinct, recently published sell-side analyst reports or notes on this stock, from different banks/firms. A single page listing several price targets is not a substitute for this — open the individual article or note behind each one. For each: the firm, the rating (buy/hold/sell or equivalent), the price target, its core thesis in one sentence, and the source you read it from. Note where they agree and where they meaningfully disagree, and why.
5. Business Model & Competitive Moat — core segments and their revenue contribution; source(s) of competitive advantage and how durable it is.
6. Growth Strategy & Outlook — key growth drivers and total addressable market, researched via web search.
7. Management & Governance — CEO/leadership tenure and track record; capital allocation (dividends, buybacks, M&A); insider ownership level (from the data above if present, else researched).
8. Risk Analysis — top 3 company-specific risks and top 3 external/systemic risks.
9. Final Recommendation — synthesize into one buy/hold/sell call, explicitly weighing your own read against where the analyst reports in §4 agreed or disagreed with you, with a concise justification.

Keep the full report thorough but scannable — this is read on a phone, not published. Favor clear, specific sentences over padding. Depth matters most in §3 and §4 — don't shortcut the research there.
```

## Files

- **New:** `app/fundamentals.py` — data fetching described above.
- **New:** `app/analysis.py` — `PROMPT_TEMPLATE`, `run_analysis(ticker, canonical_name, api_key) -> str` (the `client.messages.create` call with `tools=[web_search_20260209, web_fetch_20260209]` + `pause_turn` resume loop, returning the full raw response text), and `split_report(text) -> tuple[str, str]` (splits on the `---` line into `(quick_take, full_report)`).
- **New:** `app/commands/analyze.py` — handler: resolve ticker (reuse `ticker_resolver.resolve_ticker`, as every other command does) → send ack → `asyncio.create_task` background job that calls `asyncio.to_thread(fundamentals.build_fundamentals_snapshot, ticker)` then `asyncio.to_thread(analysis.run_analysis, ...)`, writes the full report to `reports/{TICKER}-{YYYY-MM-DD}.md`, replies with the quick take, then `context.bot.send_document(...)` with the file.
- **Modified:** `app/parser.py` — new `_ANALYZE_RE` matching `Analyse|Analyze|Research` + company name (consistent with the existing generous-synonym style), new intent `"analyze_company"`.
- **Modified:** `app/commands/__init__.py` — register `"analyze_company": analyze.handle_analyze_company`.
- **Modified:** `app/config.py` — add `anthropic_api_key: str | None` (optional, `os.environ.get`, **not** `require()` — a bot without the key set should still start and run normally; only `Analyse` needs it). The handler checks for `None` and replies with a clear setup message ("Set `ANTHROPIC_API_KEY` in `.env` to use this command") rather than crashing.
- **Modified:** `.env.example` — uncomment `ANTHROPIC_API_KEY=`, drop the "not needed yet" comment.
- **Modified:** `requirements.txt` — add `anthropic`.
- **Modified:** `.gitignore` — add `reports/*.md` (mirrors the existing `data/*.db` pattern); `reports/.gitkeep` added so the folder exists.
- **Modified:** `README.md` — add `Analyse Apple` to the command table; note in "Notes" that it costs a Claude API call and takes 1-3 minutes.
- **Modified:** `CLAUDE.md` — per the standing instruction to keep it current: update "Where things stand, and what's next" once this ships, and add a design-decision entry for the fetch-deterministically/research-the-rest split (it's exactly the kind of non-obvious call worth recording).

## Guardrail (per your answer)

`max_uses: 25` on `web_search_20260209` and `max_uses: 15` on `web_fetch_20260209` are the primary cap — hard, server-enforced ceilings sized for "actually read 3+ distinct analyst reports plus full qualitative research" rather than a bare-minimum default. The `pause_turn` resume loop is capped separately (e.g. 4 resumes) as a robustness fallback, not a cost lever — it exists so a report that legitimately needs more than 10 search/fetch calls in a row can still reach its budget instead of silently truncating partway through the analyst-comparison section.

## Verification

1. `.venv/bin/python -m pytest tests/ -q` — existing 68 tests must still pass unchanged.
2. New unit tests (no network, following the existing style):
   - `tests/test_parser.py` — `Analyse Apple` / `Analyze AAPL` / `Research Tesla` all parse to `analyze_company`.
   - `tests/test_analysis.py` — `split_report()` correctly separates the quick-take from the full report on a synthetic string containing the `---` delimiter, and handles the (should-never-happen) case where the delimiter is missing without crashing.
   - `tests/test_fundamentals.py` — `build_fundamentals_snapshot` against a mocked `yfinance.Ticker` degrades gracefully (missing fields render as "not available", not a crash) — mirrors how `tests/test_ticker_resolver.py` mocks network calls today.
3. **Live check (can't be done from tests):** with a real `ANTHROPIC_API_KEY` in `.env`, run the bot and send `Analyse Apple` to the live Telegram bot. Confirm: the ack arrives immediately, the price-poll job keeps running during the wait (check logs), the quick-take arrives as a normal message, the full report arrives as a `.md` file attachment, and the file also lands in `reports/`.
