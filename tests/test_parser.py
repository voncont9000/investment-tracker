from app.parser import parse_message


def test_watch_command():
    result = parse_message("Watch Apple")
    assert result.intent == "watchlist_add"
    assert result.company_name == "Apple"
    assert result.amount is None


def test_add_command():
    result = parse_message("Add Tesla")
    assert result.intent == "watchlist_add"
    assert result.company_name == "Tesla"


def test_watch_command_strips_watchlist_suffix():
    result = parse_message("Watch Apple to my watchlist")
    assert result.intent == "watchlist_add"
    assert result.company_name == "Apple"


def test_watch_command_strips_stock_suffix():
    result = parse_message("Add Tesla stock")
    assert result.intent == "watchlist_add"
    assert result.company_name == "Tesla"


def test_purchase_command_with_dollar_sign():
    result = parse_message("Bought Apple for $150")
    assert result.intent == "purchase_record"
    assert result.company_name == "Apple"
    assert result.amount == 150.0


def test_purchase_command_without_dollar_sign():
    result = parse_message("Bought Tesla for 200")
    assert result.intent == "purchase_record"
    assert result.company_name == "Tesla"
    assert result.amount == 200.0


def test_purchase_command_with_comma_and_decimal():
    result = parse_message("Bought Amazon for $1,234.56")
    assert result.intent == "purchase_record"
    assert result.company_name == "Amazon"
    assert result.amount == 1234.56


def test_purchase_command_takes_priority_over_watch_pattern():
    # Contains "bought ... for" — must be purchase, not misparsed as watchlist.
    result = parse_message("bought tesla for $200")
    assert result.intent == "purchase_record"


def test_unrecognized_message():
    result = parse_message("What's the weather today?")
    assert result.intent == "unknown"
    assert result.company_name is None
    assert result.amount is None


def test_empty_message():
    result = parse_message("")
    assert result.intent == "unknown"


def test_case_insensitive():
    result = parse_message("WATCH nvidia")
    assert result.intent == "watchlist_add"
    assert result.company_name == "nvidia"


# --- Purchase without an explicit price (auto-price) ---

def test_purchase_without_price():
    result = parse_message("Bought Apple")
    assert result.intent == "purchase_record"
    assert result.company_name == "Apple"
    assert result.amount is None  # handler fills this from the market price


def test_purchase_without_price_alternate_verbs():
    for text in ("Buy Tesla", "Purchased Nvidia"):
        result = parse_message(text)
        assert result.intent == "purchase_record"
        assert result.amount is None


def test_explicit_price_still_wins_over_bare_purchase():
    """Regression guard: the bare "bought X" pattern must not swallow the
    price into the company name."""
    result = parse_message("Bought Apple for $150")
    assert result.intent == "purchase_record"
    assert result.company_name == "Apple"
    assert result.amount == 150.0


def test_purchase_strips_shares_suffix():
    result = parse_message("Bought Apple shares")
    assert result.intent == "purchase_record"
    assert result.company_name == "Apple"


# --- Sell ---

def test_sold_command():
    result = parse_message("Sold Apple")
    assert result.intent == "sell_record"
    assert result.company_name == "Apple"
    assert result.amount is None


def test_sell_command_alternate_verb():
    result = parse_message("Sell Tesla")
    assert result.intent == "sell_record"
    assert result.company_name == "Tesla"


def test_sold_strips_suffix():
    result = parse_message("Sold Apple from my portfolio")
    assert result.intent == "sell_record"
    assert result.company_name == "Apple"


# --- Remove ---

def test_remove_command():
    result = parse_message("Remove Apple")
    assert result.intent == "remove_item"
    assert result.company_name == "Apple"


def test_remove_alternate_verbs():
    for text in ("Delete Tesla", "Unwatch Nvidia", "Drop Amazon"):
        result = parse_message(text)
        assert result.intent == "remove_item"


def test_stop_watching_phrase():
    result = parse_message("Stop watching Apple")
    assert result.intent == "remove_item"
    assert result.company_name == "Apple"


def test_remove_strips_watchlist_suffix():
    result = parse_message("Remove Apple from my watchlist")
    assert result.intent == "remove_item"
    assert result.company_name == "Apple"


def test_analyse_command():
    result = parse_message("Analyse Apple")
    assert result.intent == "analyze_company"
    assert result.company_name == "Apple"


def test_analyze_command_american_spelling():
    result = parse_message("Analyze AAPL")
    assert result.intent == "analyze_company"
    assert result.company_name == "AAPL"


def test_research_command():
    result = parse_message("Research Tesla")
    assert result.intent == "analyze_company"
    assert result.company_name == "Tesla"


# --- Politician-picks selection reply ---

def test_select_picks_single_number():
    result = parse_message("1")
    assert result.intent == "select_picks"
    assert result.selection == [1]


def test_select_picks_space_separated():
    result = parse_message("1 3")
    assert result.intent == "select_picks"
    assert result.selection == [1, 3]


def test_select_picks_comma_separated():
    result = parse_message("1, 3")
    assert result.intent == "select_picks"
    assert result.selection == [1, 3]


def test_select_picks_and_separated():
    result = parse_message("1 and 3")
    assert result.intent == "select_picks"
    assert result.selection == [1, 3]


def test_select_picks_dedupes_and_sorts():
    result = parse_message("3 1 3")
    assert result.intent == "select_picks"
    assert result.selection == [1, 3]


def test_select_picks_all():
    result = parse_message("all")
    assert result.intent == "select_picks"
    assert result.selection == "all"


def test_select_picks_all_case_insensitive():
    result = parse_message("ALL")
    assert result.intent == "select_picks"
    assert result.selection == "all"


def test_select_picks_skip():
    result = parse_message("skip")
    assert result.intent == "select_picks"
    assert result.selection == "skip"


def test_select_picks_none_is_same_as_skip():
    result = parse_message("none")
    assert result.intent == "select_picks"
    assert result.selection == "skip"


def test_select_picks_does_not_swallow_other_commands():
    # Sanity check: existing commands with numbers in them must still route
    # to their own intent, not select_picks.
    result = parse_message("Bought Apple for $150")
    assert result.intent == "purchase_record"


def test_other_intents_have_no_selection():
    result = parse_message("Watch Apple")
    assert result.selection is None
