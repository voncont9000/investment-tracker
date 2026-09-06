"""Numeric thresholds for the 5 entry-point alert setups (app/setups.py).

Hardcoded rather than .env-configurable — there are 34 of them, too many
to expose sanely as environment variables for a personal bot. Tune by
editing this file and restarting the bot.
"""

# --- Setup 1: Uptrend + Pullback ---
SETUP1_RETURN_30D_MIN = 0.07
SETUP1_RETURN_30D_MAX = 0.30
SETUP1_PULLBACK_MIN = 0.02
SETUP1_PULLBACK_MAX = 0.10
SETUP1_RETURN_5D_MIN = -0.08
SETUP1_RETURN_5D_MAX = -0.01
SETUP1_MAX_RETRACEMENT = 0.50
SETUP1_HIGH_WINDOW_A = 20
SETUP1_HIGH_WINDOW_B = 30
SETUP1_SWING_LOOKBACK = 90
SETUP1_STABILIZATION_WINDOW = 3

# --- Setup 2: Momentum + First Meaningful Dip ---
SETUP2_RETURN_30D_MIN = 0.15
SETUP2_RETURN_10D_MIN = 0.07
SETUP2_RETURN_5D_MAX = -0.02
SETUP2_PCT_BELOW_HIGH_MAX = 0.10
SETUP2_MAX_SINGLE_DAY_DROP = 0.07
SETUP2_IDEAL_RETURN_5D_MIN = -0.07
SETUP2_IDEAL_RETURN_5D_MAX = -0.02
SETUP2_HIGH_WINDOW = 30

# --- Setup 3: Breakout Retest ---
SETUP3_BREAKOUT_MIN_PCT = 0.03
SETUP3_SUPPORT_TOLERANCE = 0.05
SETUP3_RETEST_UNDERSHOOT = 0.02
SETUP3_RETEST_OVERSHOOT = 0.05

# --- Setup 4: Oversold Reversal (higher-risk) ---
SETUP4_RETURN_30D_MIN = -0.30
SETUP4_RETURN_30D_MAX = -0.10
SETUP4_RETURN_5D_MIN = -0.03
SETUP4_RETURN_5D_MAX = 0.03
SETUP4_STOPPED_LOWS_WINDOW = 5
SETUP4_DECEL_WINDOW = 10

# --- Setup 5: Deep Pullback in Long-Term Uptrend ---
SETUP5_RETURN_30D_MIN = -0.25
SETUP5_RETURN_30D_MAX = -0.10
SETUP5_SMA200_PROXIMITY = 0.05
SETUP5_STOPPED_LOWS_WINDOW = 5
SETUP5_IMPROVING_WINDOW = 5

# --- Exit A: Trend Break (app/exits.py) ---
EXIT_A_LOOKBACK_WINDOW = 20      # "was recently in an uptrend" window
EXIT_A_BREAK_MARGIN = 0.02       # current price must be this far below SMA50
EXIT_A_SOFT_RETURN_WINDOW = 20
EXIT_A_SOFT_RETURN_MAX = -0.10   # soft: 20D return worse than this "confirms" the break

# --- Exit B: Momentum Breakdown (app/exits.py) ---
EXIT_B_NEWS_DROP_WINDOW = 3
EXIT_B_NEWS_DROP_THRESHOLD = 0.07   # a single-session drop worse than this = "something happened"
EXIT_B_SLIDE_RETURN_5D_MAX = -0.12
EXIT_B_SLIDE_HIGH_WINDOW = 30
EXIT_B_SLIDE_PCT_BELOW_HIGH_MIN = 0.15
