"""Claude-powered equity research report for the "Analyse COMPANY" command.

Financials are fetched deterministically via app.fundamentals and embedded
directly in the prompt — the model's own tool use (web_search + web_fetch)
is reserved for the genuinely open-ended research: competitors, moat, TAM,
management, recent catalysts, and analyst coverage. See
docs/plans/2026-08-08-analyse-company-command.md for the reasoning.
"""

from __future__ import annotations

import re
from datetime import date

import anthropic

MODEL = "claude-opus-5"
MAX_TOKENS = 12000
WEB_SEARCH_MAX_USES = 25
WEB_FETCH_MAX_USES = 15

# Server-tool turns can stop with stop_reason "pause_turn" after 10 internal
# search/fetch iterations, before either tool's max_uses is reached. This
# caps how many times we resend to let the turn continue, so a stalled
# analysis can't run away — see the "Guardrail" section of the plan doc.
MAX_PAUSE_RESUMES = 4

_SPLIT_RE = re.compile(r"\n\s*-{3,}\s*\n")

PROMPT_TEMPLATE = """\
Act as a senior equity research analyst. Compile an investment analysis \
report on {company_name} (Ticker: {ticker}), using the financial data \
provided below plus web research for everything not in that data. Be \
objective and specific — cite a source (publication or site name) for \
every claim that comes from web research, and never invent a number; if \
something isn't in the data below and you can't find it, say so rather \
than estimating.

FINANCIAL DATA (fetched programmatically, {as_of_date}):
{fundamentals_snapshot}

Structure your response in exactly two parts, in this order:

PART 1 — Quick Take (3-5 sentences, for a chat message)
State the buy/hold/sell call at the current price, then the single \
biggest reason for it and the single biggest risk against it.

Then a line containing only: ---

PART 2 — Full Report
1. Executive Summary — one sentence on the business, then the investment \
thesis (2-3 sentences) with the buy/hold/sell call, key catalysts, and \
major risks.
2. Financial Performance & Health — using the financial data above: \
revenue growth, margin trends, and FCF generation over the years \
available (may be fewer than 5 if the data source doesn't go back \
further — say so if it applies). Balance sheet strength from the \
debt/equity and current ratio figures given.
3. Valuation — current P/E, P/S, P/B, and EV/EBITDA from the data above. \
Research (via web search) how these compare to the company's own \
historical average and to its top 2-3 direct competitors (identify them \
yourself) and industry norms. State plainly that historical/peer \
comparisons are researched estimates, not computed figures. Conclude \
overvalued/undervalued/fair.
4. Analyst Coverage — find and actually read (fetch, don't just cite a \
ratings-aggregator summary page) at least 3 distinct, recently published \
sell-side analyst reports or notes on this stock, from different \
banks/firms. A single page listing several price targets is not a \
substitute for this — open the individual article or note behind each \
one. For each: the firm, the rating (buy/hold/sell or equivalent), the \
price target, its core thesis in one sentence, and the source you read it \
from. Note where they agree and where they meaningfully disagree, and why.
5. Business Model & Competitive Moat — core segments and their revenue \
contribution; source(s) of competitive advantage and how durable it is.
6. Growth Strategy & Outlook — key growth drivers and total addressable \
market, researched via web search.
7. Management & Governance — CEO/leadership tenure and track record; \
capital allocation (dividends, buybacks, M&A); insider ownership level \
(from the data above if present, else researched).
8. Risk Analysis — top 3 company-specific risks and top 3 \
external/systemic risks.
9. Final Recommendation — synthesize into one buy/hold/sell call, \
explicitly weighing your own read against where the analyst reports in \
§4 agreed or disagreed with you, with a concise justification.

Keep the full report thorough but scannable — this is read on a phone, \
not published. Favor clear, specific sentences over padding. Depth \
matters most in §3 and §4 — don't shortcut the research there.
"""


def _extract_text(content: list) -> str:
    return "".join(block.text for block in content if block.type == "text")


def run_analysis(ticker: str, canonical_name: str, fundamentals_snapshot: str, api_key: str) -> str:
    """Run the report end-to-end and return the raw response text.

    Blocking (synchronous Anthropic + resend calls) — callers on an asyncio
    event loop must wrap this in asyncio.to_thread(), the same way
    app.prices functions are wrapped.
    """
    client = anthropic.Anthropic(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(
        company_name=canonical_name,
        ticker=ticker,
        as_of_date=date.today().isoformat(),
        fundamentals_snapshot=fundamentals_snapshot,
    )
    user_message = {"role": "user", "content": prompt}
    messages = [user_message]

    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": WEB_FETCH_MAX_USES},
    ]

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": "high"},
        tools=tools,
        messages=messages,
    )

    resumes = 0
    while response.stop_reason == "pause_turn" and resumes < MAX_PAUSE_RESUMES:
        messages = [user_message, {"role": "assistant", "content": response.content}]
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": "high"},
            tools=tools,
            messages=messages,
        )
        resumes += 1

    return _extract_text(response.content)


def split_report(text: str) -> tuple[str, str]:
    """Split the model's response into (quick_take, full_report) on the '---' line.

    Falls back to using the whole text for both halves if the model didn't
    include the delimiter, so nothing is silently dropped.
    """
    parts = _SPLIT_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text.strip(), text.strip()
