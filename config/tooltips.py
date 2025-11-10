"""
Tooltip descriptions for CDEM Agent configuration options.

Contains user-friendly explanations for all configuration parameters.
"""

TOOLTIP_DESCRIPTIONS = {
    "master_on": (
        "Master switch to enable/disable the entire agent. This is the main on/off toggle for all agent operations. "
        "When turned off, the agent stops checking for earnings, analyzing sentiment, and executing trades, effectively "
        "pausing everything without closing the app. Turn it on to resume normal functioning."
    ),
    "sp_on": (
        "Enable holding S&P 500 ETF (e.g., SPY) when no active trades. This means when the agent isn't actively "
        "trading individual stocks based on earnings, it will invest idle cash in a broad market ETF like SPY for "
        "passive growth. It's a way to keep your portfolio working even during quiet periods."
    ),
    "paper_trading": (
        "⚠️ TOGGLE BETWEEN PAPER (FAKE) AND LIVE (REAL) TRADING. When enabled (checked), the agent uses Tradier's "
        "sandbox account with fake money for testing strategies risk-free. When disabled (unchecked), it uses your "
        "LIVE account with REAL MONEY. Always test with paper trading first! This requires separate API keys "
        "(TRADIER_PAPER_API_KEY for sandbox, TRADIER_API_KEY for live) in your .env file."
    ),
    "stock_universe": (
        "List of tickers to monitor for earnings. One ticker per line. These are the specific stocks (e.g., AAPL, TSLA) "
        "the agent will watch for upcoming earnings announcements. The agent focuses only on these, fetching their "
        "calendars and analyzing sentiment around their earnings dates."
    ),
    "check_interval_minutes": (
        "How often to check for upcoming earnings and consensus. This sets the frequency (in minutes) for the agent to "
        "scan the earnings calendar and run sentiment analysis. Shorter intervals mean more frequent updates but higher "
        "resource use; longer ones are more efficient but might miss short-term changes."
    ),
    "risk_per_trade": (
        "Maximum risk per trade as % of portfolio. This limits how much of your total portfolio value you're willing to "
        "risk losing on any single trade. For example, 0.015 means 1.5% max risk per trade, helping to prevent large "
        "losses from one bad decision."
    ),
    "max_exposure": (
        "Maximum total active exposure as % of portfolio. This caps the overall amount of your portfolio that's invested "
        "in active trades at any time. For example, 0.45 means no more than 45% in open positions, leaving the rest in "
        "cash or SPY for safety."
    ),
    "stop_loss_pct": (
        "Stop loss percentage below entry. This automatically sells a position if it drops by this percentage from your "
        "buy price. For example, 0.05 means sell if it falls 5%, protecting against big losses but potentially triggering "
        "on temporary dips."
    ),
    "trailing_trigger": (
        "Gain % to start trailing stop. Once a trade gains this percentage (e.g., 0.1 for 10%), the agent activates a "
        "trailing stop loss that follows the price up, locking in profits while allowing more upside."
    ),
    "trailing_pct": (
        "Trailing stop percentage from high. After the trailing stop triggers, this sets how much the price can fall from "
        "its highest point before selling. For example, 0.05 means sell if it drops 5% from the peak, securing gains."
    ),
    "use_options": (
        "Use options instead of stock for leverage. When on, the agent trades options contracts (calls/puts) for amplified "
        "gains/losses instead of buying stocks directly. This increases potential returns but also risk due to time decay "
        "and volatility."
    ),
    "option_exp_weeks": (
        "Weeks after earnings for option expiration. This chooses options that expire this many weeks after the earnings "
        "date, giving time for post-earnings moves. Shorter expirations are riskier but cheaper; longer ones are safer but "
        "cost more."
    ),
    "option_leverage": (
        "Leverage factor for options (limit to 50%). This determines how much leverage (e.g., 0.5 for 50%) to apply when "
        "sizing options trades, controlling risk by not over-leveraging your position."
    ),
    "test_mode": (
        "Bypass date checks for testing. When on, the agent ignores real dates and runs in simulation mode, allowing you "
        "to test strategies without waiting for actual earnings events or market hours."
    ),
}
