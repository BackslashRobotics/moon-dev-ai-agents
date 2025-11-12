# Version: 76
# REMINDER: Increase version number by 1 for every edit to this code.
"""
Consensus-Driven Earnings Momentum (CDEM) Agent
Adapted from sentiment_agent.py for CDEM strategy.

This agent automates the CDEM strategy:
- Monitors earnings calendar for stock universe.
- Assesses consensus 1 day before earnings using Grok LLM for deep multi-source sentiment analysis.
- Enters long trades if "Good" consensus.
- Manages hybrid portfolio with SPY ETF when idle.
- Executes risk-managed trades via Tradier (formerly Alpaca).
- Tracks performance.

Required:
- .env with XAI_API_KEY (for Grok), TRADIER_API_KEY, TRADIER_ACCOUNT_ID, FINNHUB_API_KEY.
- Tradier sandbox account for testing (or live account for production).

Notes:
- Uses 1 day before for consensus to capture leaks (changed from 2 days).
- LLM (Grok) for sentiment: High accuracy via internal knowledge (no tools needed).
- Expand stock_universe to 50+ for diversification.
- Paper trade first; backtest with repo's backtesting.py.
- Now loads config from config.json and polls for changes (for app integration).
- ALPACA CODE COMMENTED OUT (not deleted) for potential rollback or reference.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# FORCE termcolor to always output ANSI codes (even when piped)
# This is necessary for GUI apps that capture subprocess output
_force_color = os.getenv('FORCE_COLOR') == '1'
if _force_color:
    # Monkey-patch sys.stdout.isatty() before importing termcolor
    sys.stdout.isatty = lambda: True
    sys.stderr.isatty = lambda: True

import json  # For config loading
import pathlib
import time
from datetime import datetime, timedelta
import csv
import pandas as pd
import numpy as np
import finnhub  # New for earnings calendar and prices
# ALPACA (commented out for Tradier swap):
# from alpaca.trading.client import TradingClient
# from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest
# from alpaca.trading.enums import OrderSide, TimeInForce
# from alpaca.trading.models import Position  # For getting positions
import requests  # For Tradier API
import schedule

# Create a forced-color version of cprint that ALWAYS outputs ANSI codes
if _force_color:
    # Color code mappings for ANSI
    _COLOR_CODES = {
        'grey': 30, 'red': 31, 'green': 32, 'yellow': 33,
        'blue': 34, 'magenta': 35, 'cyan': 36, 'white': 37,
        'light_grey': 90, 'light_red': 91, 'light_green': 92,
        'light_yellow': 93, 'light_blue': 94, 'light_magenta': 95,
        'light_cyan': 96, 'light_white': 97
    }
    
    def cprint(text, color=None, on_color=None, attrs=None, **kwargs):
        """Drop-in replacement for termcolor.cprint that ALWAYS outputs ANSI codes"""
        if color and color in _COLOR_CODES:
            code = _COLOR_CODES[color]
            print(f'\x1b[{code}m{text}\x1b[0m', **kwargs)
        else:
            print(text, **kwargs)
else:
    from termcolor import cprint
from src.models.model_factory import ModelFactory  # From repo for Grok
import torch  # For any NLP if needed
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # Fallback
import re  # For parsing simulated tool calls
import yfinance as yf  # Added for options chain (if USE_OPTIONS=True)
from threading import Thread, Event, Lock  # For timeout wrapper and file locking
from concurrent.futures import ThreadPoolExecutor, as_completed  # For parallel sentiment calls
from dotenv import load_dotenv  # FIXED: Added missing import

# Import earnings history manager for dashboard
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "data", "cdem")))
from earnings_history_manager import update_ticker_history, clear_old_history_if_needed

# Config file path
CONFIG_PATH = "app_data/config.json"

# Log file for Grok interactions (in logs directory)
GROK_LOG_PATH = os.path.join("logs", "grok_logs.json")

# File lock for thread-safe logging (prevents corrupted JSON from parallel writes)
GROK_LOG_LOCK = Lock()

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# Create initial config if not exists
if not os.path.exists(CONFIG_PATH):
    initial_config = {
        "master_on": True,
        "sp_on": True,
        "paper_trading": True,
        "stock_universe": ["META", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
        "stock_colors": {},
        "check_interval_minutes": 60,
        "risk_per_trade": 0.015,
        "max_exposure": 0.45,
        "stop_loss_pct": 0.05,
        "trailing_trigger": 0.1,
        "trailing_pct": 0.05,
        "use_options": False,
        "option_exp_weeks": 1,
        "option_leverage": 0.5,
        "test_mode": True,
    }
    with open(CONFIG_PATH, "w") as f:
        json.dump(initial_config, f, indent=4)
    cprint("Created initial config.json", "yellow")

# Create data directory
pathlib.Path("src/data/cdem").mkdir(parents=True, exist_ok=True)

# Load environment variables
load_dotenv()

# ALPACA (commented out for Tradier swap):
# Initialize Alpaca client
# alpaca = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)  # Paper for test

# Initialize Tradier client - Load both live and paper credentials
TRADIER_LIVE_API_KEY = os.getenv("TRADIER_API_KEY")
TRADIER_LIVE_ACCOUNT_ID = os.getenv("TRADIER_ACCOUNT_ID")
TRADIER_PAPER_API_KEY = os.getenv("TRADIER_PAPER_API_KEY")
TRADIER_PAPER_ACCOUNT_ID = os.getenv("TRADIER_PAPER_ACCOUNT_ID")

# Helper functions to get current Tradier configuration based on paper_trading setting
def get_tradier_config(paper_trading=True):
    """Get Tradier API configuration based on paper trading setting"""
    if paper_trading:
        api_key = TRADIER_PAPER_API_KEY
        account_id = TRADIER_PAPER_ACCOUNT_ID
        base_url = "https://sandbox.tradier.com"
    else:
        api_key = TRADIER_LIVE_API_KEY
        account_id = TRADIER_LIVE_ACCOUNT_ID
        base_url = "https://api.tradier.com"
    
    if not api_key or not account_id:
        mode = "paper" if paper_trading else "live"
        raise ValueError(f"TRADIER_{mode.upper()}_API_KEY and TRADIER_{mode.upper()}_ACCOUNT_ID not found in .env")
    
    return {
        "api_key": api_key,
        "account_id": account_id,
        "base_url": base_url,
        "headers": {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }
    }

# Initialize Finnhub client
finnhub_key = os.getenv("FINNHUB_API_KEY")
if not finnhub_key:
    raise ValueError("FINNHUB_API_KEY not found in .env. Please add it from https://finnhub.io/register")
finnhub_client = finnhub.Client(api_key=finnhub_key)

# Initialize ModelFactory for Grok
model_factory = ModelFactory()
grok_model = model_factory.get_model("xai")  # Use Grok-4-fast-reasoning

# VADER as fallback for quick scoring
analyzer = SentimentIntensityAnalyzer()

SPY_SYMBOL = "SPY"

# Global variable to track current trading mode (updated from config)
_CURRENT_PAPER_TRADING = True  # Default to paper trading for safety


# ============================================================================
# TRADIER API HELPER FUNCTIONS
# ============================================================================

def get_tradier_account():
    """Get Tradier account balances"""
    try:
        config = get_tradier_config(_CURRENT_PAPER_TRADING)
        response = requests.get(
            f"{config['base_url']}/v1/accounts/{config['account_id']}/balances",
            headers=config['headers'],
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        balances = data.get("balances", {})
        return {
            "equity": float(balances.get("total_equity", 0)),
            "cash": float(balances.get("total_cash", 0)),
            "buying_power": float(balances.get("option_buying_power", 0))
        }
    except Exception as e:
        cprint(f"Tradier account error: {str(e)}", "red")
        return None


def get_tradier_positions():
    """Get all Tradier positions"""
    try:
        config = get_tradier_config(_CURRENT_PAPER_TRADING)
        response = requests.get(
            f"{config['base_url']}/v1/accounts/{config['account_id']}/positions",
            headers=config['headers'],
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        positions = data.get("positions")
        if positions == "null" or positions is None:
            return []
        if isinstance(positions, dict):
            position_list = positions.get("position", [])
            if isinstance(position_list, dict):
                return [position_list]
            return position_list if position_list else []
        return []
    except Exception as e:
        cprint(f"Tradier positions error: {str(e)}", "red")
        return []


def get_tradier_position(symbol):
    """Get specific Tradier position by symbol"""
    positions = get_tradier_positions()
    for pos in positions:
        if pos.get("symbol") == symbol:
            return {
                "symbol": symbol,
                "qty": float(pos.get("quantity", 0)),
                "market_value": float(pos.get("quantity", 0)) * float(pos.get("cost_basis", 0))
            }
    return None


def submit_tradier_order(symbol, side, qty=None, order_type="market", stop_price=None, notional=None):
    """
    Submit order to Tradier
    side: "buy" or "sell"
    order_type: "market", "stop"
    qty: number of shares (for stock orders)
    notional: dollar amount (for notional orders - Tradier doesn't directly support this, so we calculate qty)
    """
    try:
        config = get_tradier_config(_CURRENT_PAPER_TRADING)
        
        # If notional is provided, calculate qty from current price
        if notional is not None and qty is None:
            quote = get_tradier_quote(symbol)
            if quote and quote > 0:
                qty = int(notional / quote)
                if qty <= 0:
                    cprint(f"Calculated qty <= 0 for {symbol} with notional ${notional}", "red")
                    return None
        
        if qty is None or qty <= 0:
            cprint(f"Invalid qty for {symbol}: {qty}", "red")
            return None
        
        payload = {
            "class": "equity",
            "symbol": symbol,
            "side": side,
            "quantity": str(int(qty)),
            "type": order_type,
            "duration": "day"
        }
        
        if order_type == "stop":
            if stop_price is None:
                cprint(f"Stop order requires stop_price for {symbol}", "red")
                return None
            payload["stop"] = str(stop_price)
        
        response = requests.post(
            f"{config['base_url']}/v1/accounts/{config['account_id']}/orders",
            headers=config['headers'],
            data=payload,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("order", {}).get("status") == "ok":
            order_id = data["order"].get("id")
            cprint(f"Tradier order submitted: {order_id}", "green")
            return {"id": order_id, "qty": qty, "symbol": symbol}
        else:
            cprint(f"Tradier order failed: {data}", "red")
            return None
    except Exception as e:
        cprint(f"Tradier order submission error: {str(e)}", "red")
        return None


def get_tradier_quote(symbol):
    """Get current quote for symbol from Tradier"""
    try:
        config = get_tradier_config(_CURRENT_PAPER_TRADING)
        response = requests.get(
            f"{config['base_url']}/v1/markets/quotes",
            headers=config['headers'],
            params={"symbols": symbol},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        quotes = data.get("quotes", {}).get("quote", {})
        if isinstance(quotes, list) and quotes:
            quotes = quotes[0]
        return float(quotes.get("last", 0))
    except Exception as e:
        cprint(f"Tradier quote error for {symbol}: {str(e)}", "red")
        return None


class CDEMAgent:
    def __init__(self):
        """Initialize the CDEM Agent"""
        self.sentiment_model = None
        self.config = self.load_config()
        self.last_config_mtime = os.path.getmtime(CONFIG_PATH)
        self.prev_sp_on = self.config["sp_on"]  # Track for toggle
        self.init_sentiment_history()
        self.init_portfolio()
        self.is_gathering = False  # Flag for mid-gathering state
        self.added_tickers_queue = []  # Queue for newly added tickers
        # Buy SPY with all cash if no positions (hybrid baseline)
        self.initialize_spy()
        cprint("CDEM Agent initialized!", "green")

    def load_config(self):
        """Load config from JSON and update global paper trading mode"""
        global _CURRENT_PAPER_TRADING
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
        _CURRENT_PAPER_TRADING = config.get("paper_trading", True)  # Default to paper for safety
        return config

    def reload_config_if_changed(self):
        """Check and reload config if modified"""
        current_mtime = os.path.getmtime(CONFIG_PATH)
        if current_mtime != self.last_config_mtime:
            self.last_config_mtime = current_mtime
            old_config = self.config
            self.config = self.load_config()
            cprint("Config reloaded from file.", "yellow")
            # Log changes
            self.log_config_changes(old_config, self.config)
            # Apply changes
            if self.config["sp_on"] != old_config["sp_on"]:
                self.handle_sp_toggle()
            # Check for interval change or stock adds to trigger restart/selective run
            if self.config["check_interval_minutes"] != old_config["check_interval_minutes"]:
                self.perform_daily_check()  # Restart full checks on interval change
            elif set(self.config["stock_universe"]) != set(old_config["stock_universe"]):
                added = set(self.config["stock_universe"]) - set(old_config["stock_universe"])
                if added:
                    if self.is_gathering:
                        self.added_tickers_queue.extend(added)  # Queue if mid-gathering
                    else:
                        self.process_added_tickers(added)  # Run on new immediately
            return True
        return False

    def log_config_changes(self, old_config, new_config):
        """Log changes in config"""
        for key in new_config:
            if key in old_config and old_config[key] != new_config[key]:
                if key == "stock_universe":
                    old_set = set(old_config[key])
                    new_set = set(new_config[key])
                    added = new_set - old_set
                    removed = old_set - new_set
                    for ticker in added:
                        cprint(f"Ticker added: {ticker}", "yellow")
                    for ticker in removed:
                        cprint(f"Ticker removed: {ticker}", "yellow")
                elif key == "paper_trading":
                    mode = "PAPER TRADING (Sandbox)" if new_config[key] else "LIVE TRADING (Real Money)"
                    cprint(f"⚠️  Trading mode changed to: {mode}", "red" if not new_config[key] else "yellow")
                else:
                    cprint(f"{key} updated from {old_config[key]} to {new_config[key]}", "yellow")

    def handle_sp_toggle(self):
        """Handle S&P toggle: Move to/from cash if no active trades"""
        if self.get_active_exposure() > 0:
            cprint("Cannot toggle S&P: Active trades open. Skipping.", "yellow")
            return
        # ALPACA (commented out for Tradier swap):
        # account = alpaca.get_account()
        # cash = float(account.cash)
        # try:
        #     spy_position = alpaca.get_position(SPY_SYMBOL)
        #     spy_value = float(spy_position.market_value)
        # except:
        #     spy_value = 0

        # TRADIER:
        account = get_tradier_account()
        if account is None:
            cprint("Cannot get account info for S&P toggle", "red")
            return
        cash = account["cash"]
        spy_position = get_tradier_position(SPY_SYMBOL)
        spy_value = spy_position["market_value"] if spy_position else 0

        if self.config["sp_on"]:
            # To S&P: Buy with all cash
            if cash > 0:
                self.manage_spy(cash, sell=False)
        else:
            # To cash: Sell all SPY
            if spy_value > 0:
                self.manage_spy(spy_value, sell=True)

    def init_sentiment_history(self):
        """Initialize sentiment history file"""
        sentiment_history_file = "src/data/cdem/sentiment_history.csv"
        if not os.path.exists(sentiment_history_file):
            pd.DataFrame(columns=["timestamp", "ticker", "sentiment_score", "consensus", "num_sources"]).to_csv(
                sentiment_history_file, index=False
            )

    def init_portfolio(self):
        """Initialize portfolio file"""
        portfolio_file = "src/data/cdem/portfolio.csv"
        if not os.path.exists(portfolio_file):
            pd.DataFrame(
                columns=["timestamp", "ticker", "position_size", "entry_price", "current_price", "pnl", "status"]
            ).to_csv(portfolio_file, index=False)

    def initialize_spy(self):
        """Buy SPY with available cash if no open positions or SPY held and sp_on=True"""
        if not self.config["sp_on"]:
            return
        # ALPACA (commented out for Tradier swap):
        # account = alpaca.get_account()
        # cash = float(account.cash)
        # buying_power = float(account.buying_power)
        # positions = alpaca.get_all_positions()
        # if cash > 0 and not positions and buying_power >= cash:
        #     time.sleep(5)
        #     quote = finnhub_client.quote(SPY_SYMBOL)
        #     current_price = quote["c"]
        #     if current_price > 0:
        #         notional_amount = min(cash, buying_power)
        #         notional = str(round(notional_amount, 2))
        #         order_data = MarketOrderRequest(
        #             symbol=SPY_SYMBOL, notional=notional, side=OrderSide.BUY, time_in_force=TimeInForce.DAY
        #         )
        #         try:
        #             alpaca.submit_order(order_data)
        #             cprint(f"Initialized SPY with ${notional_amount}", "cyan")
        #         except Exception as e:
        #             cprint(f"SPY initialization failed: {str(e)}. Continuing without...", "yellow")
        
        # TRADIER:
        account = get_tradier_account()
        if account is None:
            return
        cash = account["cash"]
        buying_power = account["buying_power"]
        positions = get_tradier_positions()
        if cash > 0 and not positions and buying_power >= cash:
            time.sleep(5)
            quote = finnhub_client.quote(SPY_SYMBOL)
            current_price = quote["c"]
            if current_price > 0:
                notional_amount = min(cash, buying_power)
                order = submit_tradier_order(SPY_SYMBOL, "buy", notional=notional_amount)
                if order:
                    cprint(f"Initialized SPY with ${notional_amount}", "cyan")
                else:
                    cprint(f"SPY initialization failed. Continuing without...", "yellow")

    def get_earnings_calendar(self, universe):
        """Fetch upcoming earnings calendar using Finnhub API with retry and rate limiting
        Returns: dict with ticker: {"date": date_obj, "hour": "bmc"/"bmo"/"amc"}
        """
        today = datetime.today().date()
        end_date = today + timedelta(days=7)
        calendar = {}

        # Rate limit: Finnhub free tier = 60 calls/min (1 per second)
        time.sleep(1.1)  # Sleep before API call to respect rate limit
        
        for attempt in range(3):
            try:
                earnings_data = finnhub_client.earnings_calendar(_from=str(today), to=str(end_date), symbol="")
                if "earningsCalendar" in earnings_data:
                    for event in earnings_data["earningsCalendar"]:
                        ticker = event.get("symbol")
                        if ticker in universe:
                            earnings_date = datetime.strptime(event["date"], "%Y-%m-%d").date()
                            if today < earnings_date <= end_date:
                                earnings_hour = event.get("hour", "amc")  # Default to after-market close
                                calendar[ticker] = {
                                    "date": earnings_date,
                                    "hour": earnings_hour
                                }
                                cprint(f"Found earnings for {ticker} on {earnings_date} ({earnings_hour})", "green")
                    return calendar
                else:
                    cprint("No earnings data returned from Finnhub.", "yellow")
            except Exception as e:
                cprint(f"Finnhub calendar error (attempt {attempt+1}): {str(e)}", "red")
                # Check if it's a rate limit error
                if "429" in str(e) or "limit" in str(e).lower():
                    cprint("Rate limit hit, waiting 60 seconds before retry...", "yellow")
                    time.sleep(60)  # Wait full minute on rate limit
                else:
                    time.sleep(5)
        cprint("Finnhub calendar failed after 3 attempts.", "red")
        return calendar

    def get_past_earnings_bulk(self, universe):
        """Fetch the most recent past earnings for ALL tickers in ONE bulk API call
        Returns: dict with ticker: {"date": date_obj, "hour": "bmc"/"bmo"/"amc"}
        """
        today = datetime.today().date()
        start_date = today - timedelta(days=120)  # Look back 120 days
        past_calendar = {}
        
        # Rate limit: Finnhub free tier = 60 calls/min (1 per second)
        time.sleep(1.1)  # Sleep before API call to respect rate limit
        
        cprint(f"Fetching past earnings for {len(universe)} tickers (bulk call)...", "cyan")
        
        for attempt in range(3):  # Up to 3 attempts for bulk call
            try:
                # BULK CALL: symbol="" fetches ALL tickers at once
                earnings_data = finnhub_client.earnings_calendar(_from=str(start_date), to=str(today), symbol="")
                if "earningsCalendar" in earnings_data and earnings_data["earningsCalendar"]:
                    # Build dictionary of most recent past earnings for each ticker in universe
                    for event in earnings_data["earningsCalendar"]:
                        ticker = event.get("symbol")
                        if ticker in universe:
                            earnings_date = datetime.strptime(event["date"], "%Y-%m-%d").date()
                            if earnings_date < today:
                                earnings_hour = event.get("hour", "amc")  # Default to after-market close
                                # Keep only the MOST RECENT past earnings per ticker
                                if ticker not in past_calendar or earnings_date > past_calendar[ticker]["date"]:
                                    past_calendar[ticker] = {
                                        "date": earnings_date,
                                        "hour": earnings_hour
                                    }
                    
                    cprint(f"Found past earnings for {len(past_calendar)} tickers", "green")
                    return past_calendar
                else:
                    cprint("No past earnings data returned from Finnhub.", "yellow")
                    return past_calendar
            except Exception as e:
                cprint(f"Finnhub past earnings bulk error (attempt {attempt+1}): {str(e)}", "red")
                # Check if it's a rate limit error
                if "429" in str(e) or "limit" in str(e).lower():
                    cprint("Rate limit hit, waiting 60 seconds before retry...", "yellow")
                    time.sleep(60)  # Wait full minute on rate limit
                else:
                    time.sleep(5)
        
        cprint("Finnhub past earnings bulk call failed after 3 attempts.", "red")
        return past_calendar

    def perform_daily_check(self):
        """Main daily check: Get earnings calendar, assess consensus, execute trades"""
        if not self.config["master_on"]:
            cprint("Master switch is off. Skipping daily check.", "yellow")
            return

        cprint("Starting daily earnings check...", "cyan")
        universe = self.config["stock_universe"]
        calendar = self.get_earnings_calendar(universe)

        if not calendar:
            cprint("No earnings found in next 7 days.", "yellow")
        
        # Fetch past earnings for all tickers WITHOUT upcoming earnings (ONE bulk call instead of N individual calls)
        tickers_without_upcoming = [t for t in universe if t not in calendar]
        if tickers_without_upcoming:
            past_calendar = self.get_past_earnings_bulk(tickers_without_upcoming)
            
            # Report and store past earnings
            for ticker in tickers_without_upcoming:
                if ticker in past_calendar:
                    past_info = past_calendar[ticker]
                    past_date = past_info["date"]
                    past_hour = past_info["hour"]
                    cprint(f"No upcoming earnings for {ticker} (last: {past_date} {past_hour})", "yellow")
                    # Store past earnings date for persistence (Finnhub only looks back 120 days)
                    try:
                        update_ticker_history(ticker, past_date, sentiment_score=None, consensus=None, earnings_hour=past_hour)
                    except Exception as e:
                        cprint(f"Warning: Failed to store past earnings date for {ticker}: {e}", "yellow")
                else:
                    cprint(f"No earnings data found for {ticker}", "yellow")
        
        # Now process each ticker with upcoming earnings (sentiment analysis + trading)
        for ticker, earnings_info in calendar.items():
            try:
                earnings_date = earnings_info["date"]
                earnings_hour = earnings_info["hour"]
                
                # Clear old history if new earnings is within 7 days (changed from 3)
                try:
                    clear_old_history_if_needed(ticker, earnings_date)
                except Exception as e:
                    cprint(f"Warning: Failed to clear old history for {ticker}: {e}", "yellow")
                
                consensus, score = self.assess_consensus(ticker, earnings_date, earnings_hour)
                if consensus == "Good":
                    self.execute_trade(ticker, consensus)
            except Exception as e:
                cprint(f"Error processing {ticker}: {str(e)}", "red")
                continue

        # Monitor existing trades
        self.monitor_trades()

        # Process queued tickers if any
        if self.added_tickers_queue:
            queued = list(self.added_tickers_queue)
            self.added_tickers_queue.clear()
            self.process_added_tickers(queued)

    def process_added_tickers(self, tickers):
        """Process newly added tickers immediately"""
        if not tickers:
            return
        cprint(f"Processing {len(tickers)} newly added ticker(s)...", "cyan")
        for ticker in tickers:
            try:
                # Check if this ticker has upcoming earnings
                calendar = self.get_earnings_calendar([ticker])
                if ticker in calendar:
                    earnings_info = calendar[ticker]
                    earnings_date = earnings_info["date"]
                    earnings_hour = earnings_info["hour"]
                    consensus, score = self.assess_consensus(ticker, earnings_date, earnings_hour)
                    if consensus == "Good":
                        self.execute_trade(ticker, consensus)
                else:
                    # Check for past earnings
                    past_calendar = self.get_past_earnings_bulk([ticker])
                    if ticker in past_calendar:
                        past_info = past_calendar[ticker]
                        past_date = past_info["date"]
                        past_hour = past_info["hour"]
                        cprint(f"No upcoming earnings for {ticker} (last: {past_date} {past_hour})", "yellow")
                        # Store past earnings date for persistence (Finnhub only looks back 120 days)
                        try:
                            update_ticker_history(ticker, past_date, sentiment_score=None, consensus=None, earnings_hour=past_hour)
                        except Exception as e:
                            cprint(f"Warning: Failed to store past earnings date for {ticker}: {e}", "yellow")
                    else:
                        cprint(f"No upcoming earnings found for {ticker}", "yellow")
            except Exception as e:
                cprint(f"Error processing added ticker {ticker}: {str(e)}", "red")
                continue

    def log_grok_interaction(self, sent_text=None, received_text=None, ticker=None, scores=None, avg_score=None):
        """Log sent prompt, received response, or scores/avg to grok_logs.json (thread-safe)"""
        with GROK_LOG_LOCK:  # Prevent concurrent writes from parallel threads
            try:
                if not os.path.exists(GROK_LOG_PATH):
                    with open(GROK_LOG_PATH, "w") as f:
                        json.dump([], f)

                with open(GROK_LOG_PATH, "r+") as f:
                    logs = json.load(f)
                    entry = {"timestamp": datetime.now().isoformat()}
                    if sent_text is not None:
                        entry["sent"] = sent_text
                    if received_text is not None:
                        entry["received"] = received_text
                    if ticker is not None and scores is not None and avg_score is not None:
                        entry["ticker"] = ticker
                        entry["scores"] = scores
                        entry["avg_score"] = avg_score
                    logs.append(entry)
                    f.seek(0)
                    f.truncate()  # Clear file before writing to prevent leftover data
                    json.dump(logs, f, indent=4)
            except Exception as e:
                cprint(f"Error logging to grok_logs.json: {e}", "red")

    def _call_grok_with_timeout(self, system_prompt, user_prompt, timeout=180):
        """Call Grok API with timeout protection and return response or None."""
        result = [None]
        error = [None]

        def grok_call():
            try:
                result[0] = grok_model.generate_response(system_prompt, user_prompt)
            except Exception as err:
                error[0] = str(err)

        thread = Thread(target=grok_call)
        thread.start()
        thread.join(timeout=timeout)

        if error[0]:
            cprint(f"Grok error: {error[0]}. Retrying...", "red")
            return None
        if thread.is_alive():
            cprint("Grok timeout. Retrying...", "red")
            return None

        return result[0]

    def _parse_sentiment_response(self, response):
        """Parse Grok response JSON and extract sentiment score, returning None if invalid."""
        if not response or not response.content or not response.content.strip():
            cprint("Empty response. Retrying...", "red")
            return None

        try:
            content = response.content.strip()
            
            # Try to extract JSON from response (Grok sometimes adds text before/after JSON)
            # Look for JSON object boundaries
            json_start = content.find('{')
            json_end = content.rfind('}')
            
            if json_start != -1 and json_end != -1 and json_end > json_start:
                json_str = content[json_start:json_end+1]
                parsed = json.loads(json_str)
                
                if "classification" in parsed and "score" in parsed:
                    return parsed["score"]
                else:
                    cprint("Parse error: Missing keys. Retrying...", "red")
                    return None
            else:
                cprint("Parse error: No JSON object found in response. Retrying...", "red")
                return None
                
        except json.JSONDecodeError as e:
            cprint(f"JSON decode error: {str(e)}. Retrying...", "red")
            return None
        except Exception as e:
            cprint(f"Parse error: {str(e)}. Retrying...", "red")
            return None

    def _calculate_consensus(self, scores):
        """Calculate consensus classification from list of sentiment scores."""
        if not scores or len(scores) < 3:
            return None, None

        avg_score = sum(scores) / len(scores)
        if avg_score > 70:
            consensus = "Good"
        elif avg_score >= 40:
            consensus = "Mixed"
        else:
            consensus = "Bad"

        return consensus, avg_score

    def assess_consensus(self, ticker, earnings_date, earnings_hour="amc"):
        """Assess consensus 1 day before earnings using Grok LLM with prompt-engineered tools"""
        today = datetime.today().date()
        check_date = earnings_date - timedelta(days=1)
        cprint(f"Debug: Today {today}, Check date {check_date} for {ticker}", "grey")

        if not self.config["test_mode"] and today != check_date:
            cprint(f"Skipping {ticker}: Not T-1 day (check_date: {check_date})", "light_grey", end=' ')
            return None, None

        # Build prompts for sentiment analysis
        prompt = f"""
Use your up-to-date knowledge to analyze pre-earnings consensus for {ticker} as of {check_date}. Dig deep into available data from sources like X (tweets since {check_date - timedelta(days=1)}), Reddit r/stocks, StockTwits, Seeking Alpha, Bloomberg previews, and options IV data for sentiment.
Classify as:
- Good: >70% positive (beat expected, strong growth, hype).
- Mixed: 40-70% positive (balanced views with risks).
- Bad: <40% positive (anticipated weakness).
Detect leaks/hype. Reason step-by-step before classifying. Return as JSON: {{"classification": "Good", "score": 75, "reasoning": "...", "sources": []}}.
"""
        system_prompt = "You are a financial sentiment analyst. Respond ONLY with valid JSON for the classification. No additional text, explanations, or formatting."

        # Helper function for single sentiment run (with retries)
        def run_sentiment_analysis(run_num):
            """Run a single sentiment analysis with retries"""
            for attempt in range(10):
                response = self._call_grok_with_timeout(system_prompt, prompt)
                if response is None:
                    continue

                self.log_grok_interaction(sent_text=system_prompt + "\n" + prompt, received_text=response.content)

                score = self._parse_sentiment_response(response)
                if score is not None:
                    cprint(f"Score for run {run_num}: {score}", "light_green")
                    return score
            
            cprint(f"Failed to get valid score for run {run_num} after 10 attempts.", "red")
            return None

        # Collect sentiment scores across 5 runs IN PARALLEL
        cprint(f"Running 5 parallel sentiment analyses for {ticker}...", "magenta")
        scores = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            # Submit all 5 runs simultaneously
            future_to_run = {executor.submit(run_sentiment_analysis, i+1): i+1 for i in range(5)}
            
            # Collect results as they complete
            for future in as_completed(future_to_run):
                run_num = future_to_run[future]
                try:
                    score = future.result()
                    if score is not None:
                        scores.append(score)
                except Exception as e:
                    cprint(f"Exception in run {run_num}: {e}", "red")

        # Calculate consensus from scores
        consensus, avg_score = self._calculate_consensus(scores)
        if consensus is None:
            cprint(f"Insufficient valid scores for {ticker} ({len(scores)}/5). Skipping.", "yellow")
            return None, None

        self.log_grok_interaction(ticker=ticker, scores=scores, avg_score=avg_score)
        cprint(f"{ticker} consensus is {consensus} (avg score: {avg_score:.2f})", "cyan")
        print()  # Blank line for visual separation

        # Log sentiment to CSV
        df = pd.DataFrame(
            {
                "timestamp": [datetime.now()],
                "ticker": [ticker],
                "sentiment_score": [avg_score],
                "consensus": [consensus],
                "num_sources": [len(scores)],
            }
        )
        df.to_csv("src/data/cdem/sentiment_history.csv", mode="a", header=False, index=False)
        
        # Update earnings history for dashboard
        try:
            update_ticker_history(ticker, earnings_date, sentiment_score=avg_score, consensus=consensus, earnings_hour=earnings_hour)
        except Exception as e:
            cprint(f"Warning: Failed to update earnings history for {ticker}: {e}", "yellow")
        
        return consensus, avg_score

    def _get_account_info(self, ticker):
        """Get Tradier account information, return None if error."""
        # ALPACA (commented out for Tradier swap):
        # try:
        #     account = alpaca.get_account()
        #     return {
        #         "portfolio_value": float(account.equity),
        #         "cash": float(account.cash),
        #         "buying_power": float(account.buying_power),
        #     }
        # except Exception as e:
        #     cprint(f"Alpaca account error for {ticker}: {str(e)}. Skipping trade.", "red")
        #     return None
        
        # TRADIER:
        account = get_tradier_account()
        if account is None:
            cprint(f"Tradier account error for {ticker}. Skipping trade.", "red")
            return None
        return {
            "portfolio_value": account["equity"],
            "cash": account["cash"],
            "buying_power": account["buying_power"],
        }

    def _get_current_price(self, ticker):
        """Fetch current price from Finnhub, return None if error."""
        time.sleep(5)  # Rate limit protection
        try:
            quote = finnhub_client.quote(ticker)
            current_price = quote["c"]
            if current_price == 0:
                raise ValueError("Invalid price")
            return current_price
        except Exception as e:
            cprint(f"Finnhub price error for {ticker}: {str(e)}. Skipping trade.", "red")
            return None

    def _calculate_position_size(self, current_price, risk_amount):
        """Calculate position size based on current price and risk amount."""
        stop_price = round(current_price * (1 - self.config["stop_loss_pct"]), 2)
        slippage_buffer = current_price * 0.005
        position_size = risk_amount / (current_price - stop_price + slippage_buffer)
        required_amount = position_size * current_price
        return position_size, required_amount, stop_price

    def _create_order_request(self, ticker, position_size):
        """Create appropriate order parameters (stock or options)."""
        # ALPACA (commented out for Tradier swap):
        # if self.config["use_options"]:
        #     time.sleep(5)  # Extra sleep for yf
        #     options = yf.Ticker(ticker).option_chain()
        #     atm_call = options.calls.iloc[0]  # Placeholder; select ATM
        #     return MarketOrderRequest(
        #         symbol=atm_call["contractSymbol"],
        #         qty=position_size * self.config["option_leverage"],
        #         side=OrderSide.BUY,
        #         time_in_force=TimeInForce.DAY,
        #     )
        # else:
        #     if position_size <= 0:
        #         cprint(f"Skipping: Calculated position_size <= 0", "red")
        #         return None
        #     return MarketOrderRequest(
        #         symbol=ticker, qty=position_size, side=OrderSide.BUY, time_in_force=TimeInForce.DAY
        #     )
        
        # TRADIER:
        if self.config["use_options"]:
            # Options support for Tradier (placeholder - would need proper options chain lookup)
            cprint(f"Options trading not yet fully implemented for Tradier", "yellow")
            return None
        else:
            if position_size <= 0:
                cprint(f"Skipping: Calculated position_size <= 0", "red")
                return None
            return {"symbol": ticker, "qty": position_size, "side": "buy"}

    def execute_trade(self, ticker, consensus):
        """Execute entry if Good, with risk management"""
        if consensus != "Good":
            return

        # Get account information
        account_info = self._get_account_info(ticker)
        if account_info is None:
            return

        portfolio_value = account_info["portfolio_value"]
        cash = account_info["cash"]
        buying_power = account_info["buying_power"]

        # Calculate risk amount
        risk_amount = min(portfolio_value * self.config["risk_per_trade"], buying_power)
        if risk_amount <= 0:
            cprint(f"Skipping trade for {ticker}: No risk amount available (buying_power: {buying_power})", "red")
            return

        # Get current price
        current_price = self._get_current_price(ticker)
        if current_price is None:
            return

        # Calculate position sizing
        position_size, required_amount, stop_price = self._calculate_position_size(current_price, risk_amount)

        if required_amount > buying_power:
            cprint(
                f"Insufficient buying power ({buying_power}) for required {required_amount} on {ticker}. Skipping.",
                "red",
            )
            return

        # Check max exposure
        active_exposure = self.get_active_exposure()
        if active_exposure + (required_amount / portfolio_value) > self.config["max_exposure"]:
            cprint(f"Skipping {ticker}: Would exceed max exposure ({self.config['max_exposure']})", "yellow")
            return
        
        # Free cash from SPY if needed
        if cash < required_amount and self.config["sp_on"]:
            free_needed = required_amount - cash
            self.manage_spy(free_needed, sell=True)
        
        # Create order request
        order_data = self._create_order_request(ticker, position_size)
        if order_data is None:
                return

        # ALPACA (commented out for Tradier swap):
        # Submit order
        # try:
        #     order = alpaca.submit_order(order_data)
        #     cprint(f"Entered trade for {ticker}: {order.qty} @ {current_price}", "green")
        # except Exception as e:
        #     cprint(f"Order submission failed for {ticker}: {str(e)}", "red")
        #     return
        # Set stop-loss
        # stop_order = StopOrderRequest(
        #     symbol=ticker,
        #     qty=order.qty,
        #     side=OrderSide.SELL,
        #     trail_percent=None,
        #     stop_price=stop_price,
        #     time_in_force=TimeInForce.GTC,
        # )
        # try:
        #     alpaca.submit_order(stop_order)
        # except Exception as e:
        #     cprint(f"Stop order failed for {ticker}: {str(e)}. Position unprotected!", "red")
        
        # TRADIER:
        # Submit order
        order = submit_tradier_order(order_data["symbol"], order_data["side"], qty=order_data["qty"])
        if order is None:
            cprint(f"Order submission failed for {ticker}", "red")
            return
        cprint(f"Entered trade for {ticker}: {order['qty']} @ {current_price}", "green")
        
        # Set stop-loss
        stop_order = submit_tradier_order(ticker, "sell", qty=order["qty"], order_type="stop", stop_price=stop_price)
        if stop_order is None:
            cprint(f"Stop order failed for {ticker}. Position unprotected!", "red")
        
        # Log position
        df = pd.DataFrame(
            {
                "timestamp": [datetime.now()],
                "ticker": [ticker],
                "position_size": [position_size],
                "entry_price": [current_price],
                "current_price": [current_price],
                "pnl": [0],
                "status": ["open"],
            }
        )
        df.to_csv("src/data/cdem/portfolio.csv", mode="a", header=False, index=False)

    def monitor_trades(self):
        """Monitor open positions for exit/trailing"""
        try:
            df = pd.read_csv("src/data/cdem/portfolio.csv")
        except Exception as e:
            cprint(f"Error reading portfolio.csv: {str(e)}", "red")
            return

        open_trades = df[df["status"] == "open"]
        if open_trades.empty:
            return

        for index, trade in open_trades.iterrows():
            ticker = trade["ticker"]
            time.sleep(5)  # Avoid rate limit
            try:
                quote = finnhub_client.quote(ticker)
                current_price = quote["c"]  # Current price
                if current_price == 0:
                    cprint(f"Invalid price for {ticker}, skipping monitor update", "yellow")
                    continue
            except Exception as e:
                cprint(f"Error getting price for {ticker}: {str(e)}. Skipping.", "red")
                continue

            try:
                entry_price = float(trade["entry_price"])
                gain_pct = (current_price - entry_price) / entry_price

                # Update current price in portfolio
                df.at[index, "current_price"] = current_price

                # Trailing stop if >10%
                if gain_pct > self.config["trailing_trigger"]:
                    high_price = max(current_price, float(trade.get("high_price", entry_price)))  # Track high
                    trail_stop = round(high_price * (1 - self.config["trailing_pct"]), 2)
                    # Update stop order via Alpaca (simplified; use API to modify)
                    cprint(f"Trailing stop for {ticker} to {trail_stop}", "yellow")
                    # Log updated high
                    df.at[index, "high_price"] = high_price

                # Check for exit (1 day after earnings—assume from calendar)
                if self.should_exit(ticker):
                    self.exit_trade(ticker, current_price)
            except (ValueError, KeyError) as e:
                cprint(f"Error processing trade data for {ticker}: {str(e)}", "red")
                continue

        try:
            df.to_csv("src/data/cdem/portfolio.csv", index=False)
        except Exception as e:
            cprint(f"Error saving portfolio.csv: {str(e)}", "red")

    def should_exit(self, ticker):
        """Check if exit time based on earnings hour (BMC=same day close, AMC=next day close)"""
        try:
            # Try to get earnings date from sentiment history
            df_sentiment = pd.read_csv("src/data/cdem/sentiment_history.csv")
            ticker_sentiment = df_sentiment[df_sentiment["ticker"] == ticker]
            if not ticker_sentiment.empty:
                # Get the most recent sentiment entry timestamp
                latest = ticker_sentiment.iloc[-1]
                sentiment_date = pd.to_datetime(latest["timestamp"]).date()
                # Check if earnings calendar has this ticker
                calendar = self.get_earnings_calendar([ticker])
                if ticker in calendar:
                    earnings_info = calendar[ticker]
                    earnings_date = earnings_info["date"]
                    earnings_hour = earnings_info["hour"]
                    
                    # Calculate exit date based on earnings hour
                    today = datetime.today().date()
                    if earnings_hour in ["bmc", "bmo"]:  # Before market open/close
                        # Movement happens SAME DAY, exit at close same day
                        exit_date = earnings_date
                    else:  # "amc" - After market close
                        # Movement happens NEXT DAY, exit at close next day
                        exit_date = earnings_date + timedelta(days=1)
                    
                    if today >= exit_date:
                        cprint(f"{ticker}: Exit condition met (today {today} >= exit_date {exit_date}, earnings_hour: {earnings_hour})", "yellow")
                        return True
                # Fallback: if we have sentiment from T-1, assume earnings was T+1, exit T+2
                # This is approximate but better than nothing
                if (datetime.today().date() - sentiment_date).days >= 2:
                    cprint(f"{ticker}: Approximate exit condition met (2+ days since sentiment check)", "yellow")
                    return True
            return False
        except Exception as e:
            cprint(f"Error checking exit condition for {ticker}: {str(e)}", "red")
            return False

    def exit_trade(self, ticker, price):
        """Exit position and revert to SPY"""
        df = pd.read_csv("src/data/cdem/portfolio.csv")
        trade = df[(df["ticker"] == ticker) & (df["status"] == "open")].iloc[0]
        qty = trade["position_size"]
        
        # ALPACA (commented out for Tradier swap):
        # order = MarketOrderRequest(symbol=ticker, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
        # try:
        #     alpaca.submit_order(order)
        #     pnl = (price - trade["entry_price"]) * qty
        #     cprint(f"Exited {ticker}: PNL {pnl}", "blue")
        # except Exception as e:
        #     cprint(f"Exit order failed for {ticker}: {str(e)}", "red")
        #     return
        
        # TRADIER:
        order = submit_tradier_order(ticker, "sell", qty=qty)
        if order is None:
            cprint(f"Exit order failed for {ticker}", "red")
            return
        pnl = (price - trade["entry_price"]) * qty
        cprint(f"Exited {ticker}: PNL {pnl}", "blue")
        
        # Update log
        df.at[trade.name, "current_price"] = price
        df.at[trade.name, "pnl"] = pnl
        df.at[trade.name, "status"] = "closed"
        df.to_csv("src/data/cdem/portfolio.csv", index=False)
        
        # Revert to SPY if sp_on
        allocation = qty * price
        if self.config["sp_on"]:
            self.manage_spy(allocation, sell=False)

    def manage_spy(self, amount, sell):
        """Buy/sell SPY for hybrid using notional to handle fractions safely"""
        time.sleep(5)  # Avoid rate limit
        quote = finnhub_client.quote(SPY_SYMBOL)
        current_price = quote["c"]  # Current price
        if current_price == 0:
            cprint("Invalid SPY price. Skipping.", "red")
            return

        # ALPACA (commented out for Tradier swap):
        # if sell:
        #     # Check owned SPY
        #     try:
        #         position = alpaca.get_position(SPY_SYMBOL)
        #         owned_value = float(position.market_value)
        #     except:
        #         owned_value = 0  # No position
        #     sell_amount = min(amount, owned_value)
        #     if sell_amount <= 0:
        #         return  # Nothing to sell
        #     notional = str(round(sell_amount, 2))
        #     side = OrderSide.SELL
        #     action = "Sold"
        # else:
        #     notional = str(round(amount, 2))
        #     side = OrderSide.BUY
        #     action = "Bought"
        # order_data = MarketOrderRequest(symbol=SPY_SYMBOL, notional=notional, side=side, time_in_force=TimeInForce.DAY)
        # try:
        #     alpaca.submit_order(order_data)
        #     cprint(f"{action} SPY for ${float(notional)}", "cyan")
        # except Exception as e:
        #     cprint(f"SPY order failed: {str(e)}", "red")
        
        # TRADIER:
        if sell:
            # Check owned SPY
            position = get_tradier_position(SPY_SYMBOL)
            owned_value = position["market_value"] if position else 0
            sell_amount = min(amount, owned_value)
            if sell_amount <= 0:
                return  # Nothing to sell
            notional_amount = sell_amount
            side = "sell"
            action = "Sold"
        else:
            notional_amount = amount
            side = "buy"
            action = "Bought"

        order = submit_tradier_order(SPY_SYMBOL, side, notional=notional_amount)
        if order:
            cprint(f"{action} SPY for ${notional_amount:.2f}", "cyan")
        else:
            cprint(f"SPY order failed", "red")

    def get_active_exposure(self):
        """Calculate current active trade exposure"""
        df = pd.read_csv("src/data/cdem/portfolio.csv")
        open_trades = df[df["status"] == "open"]
        exposure = sum(open_trades["position_size"] * open_trades["current_price"]) / self.get_portfolio_value()
        return exposure

    def get_portfolio_value(self):
        """Get total portfolio value from Tradier"""
        # ALPACA (commented out for Tradier swap):
        # account = alpaca.get_account()
        # return float(account.equity)
        
        # TRADIER:
        account = get_tradier_account()
        if account is None:
            return 0.0
        return account["equity"]

    def backtest_strategy(self):
        """Backtest CDEM on historical data"""
        # Use repo's backtesting.py (import and run with historical calendars/sentiment proxies)
        from backtesting import Backtest, Strategy  # From repo

        # Placeholder: Load historical data, simulate consensus, test returns/win rate/Sharpe
        pass  # Implement full backtest

    def run(self):
        """Main loop with scheduling"""
        cprint("CDEM Agent starting (hourly checks)...", "green")
        
        schedule.every().day.at("09:00").do(self.backtest_strategy)  # Daily backtest/review
        schedule.every().monday.do(self.get_earnings_calendar)  # Weekly calendar update
        
        self.perform_daily_check()  # Initial check
        
        while True:
            if not self.config["master_on"]:
                cprint("Master switch off. Sleeping...", "yellow")
                time.sleep(60)  # Check config every minute when paused
                self.reload_config_if_changed()
                continue

            # Frequent polling: Sleep in 5-min chunks, check config each time
            interval_sec = 60 * self.config["check_interval_minutes"]
            for _ in range(interval_sec // 300):
                self.reload_config_if_changed()
                time.sleep(300)
            # Final check after loop
            self.reload_config_if_changed()

            schedule.run_pending()


if __name__ == "__main__":
    try:
        agent = CDEMAgent()
        agent.backtest_strategy()  # Initial backtest
        agent.run()
    except KeyboardInterrupt:
        cprint("\nCDEM Agent shutting down gracefully...", "yellow")
    except Exception as e:
        cprint(f"\nFatal error: {str(e)}", "red")
