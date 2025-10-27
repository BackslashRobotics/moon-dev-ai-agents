'''
🌙 Moon Dev's Consensus-Driven Earnings Momentum (CDEM) Agent
Built with love by Moon Dev 🚀
Adapted from sentiment_agent.py for CDEM strategy.

This agent automates the CDEM strategy:
- Monitors earnings calendar for stock universe.
- Assesses consensus 1 day before earnings using Grok LLM for deep multi-source sentiment analysis.
- Enters long trades if "Good" consensus.
- Manages hybrid portfolio with SPY ETF when idle.
- Executes risk-managed trades via Alpaca.
- Tracks performance.

Required:
- .env with XAI_API_KEY (for Grok), ALPACA_API_KEY, ALPACA_SECRET_KEY.
- Free Alpaca paper account for testing.

Notes:
- Uses 1 day before for consensus to capture leaks (changed from 2 days).
- LLM (Grok) for sentiment: High accuracy via tools (X search, web browse).
- Expand stock_universe to 50+ for diversification.
- Paper trade first; backtest with repo's backtesting.py.
'''

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Configuration
STOCK_UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]  # Start small, expand to 50+
CHECK_INTERVAL_MINUTES = 60  # Hourly checks for consensus/trades
DATA_FOLDER = "src/data/cdem"  # Where to store data
SENTIMENT_HISTORY_FILE = "src/data/cdem/sentiment_history.csv"  # Store scores
PORTFOLIO_FILE = "src/data/cdem/portfolio.csv"  # Track positions
TARGET_RETURN = 4  # Min % for backtest validation (avg 4-8%)
WIN_RATE_THRESHOLD = 70  # >70% for deployment
SHARPE_THRESHOLD = 1.0  # >1.0
RISK_PER_TRADE = 0.015  # 1.5% max loss
MAX_EXPOSURE = 0.45  # 45% portfolio in active trades
STOP_LOSS_PCT = 0.05  # 5% below entry
TRAILING_TRIGGER = 0.10  # Trail if >10% gain
TRAILING_PCT = 0.05  # Trail 5% below high
USE_OPTIONS = False  # True for ATM calls (leverage); False for stock
OPTION_EXP_WEEKS = 1  # 1-2 weeks post-earnings
OPTION_LEVERAGE = 0.5  # Limit options to 50% of position
SPY_SYMBOL = "SPY"  # Default S&P ETF

import os
from dotenv import load_dotenv
import pathlib
import time
from datetime import datetime, timedelta
import csv
import pandas as pd
import numpy as np
import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
import schedule
from termcolor import cprint
from src.models.model_factory import ModelFactory  # From repo for Grok
import torch  # For any NLP if needed
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # Fallback

# Create data directory
pathlib.Path(DATA_FOLDER).mkdir(parents=True, exist_ok=True)

# Load environment variables
load_dotenv()

# Initialize Alpaca client
alpaca = TradingClient(os.getenv('ALPACA_API_KEY'), os.getenv('ALPACA_SECRET_KEY'), paper=True)  # Paper for test

# Initialize ModelFactory for Grok
model_factory = ModelFactory()
grok_model = model_factory.get_model('xai')  # Use Grok-4-fast-reasoning

# VADER as fallback for quick scoring
analyzer = SentimentIntensityAnalyzer()

class CDEMAgent:
    def __init__(self):
        """Initialize the CDEM Agent"""
        self.sentiment_model = None
        self.init_sentiment_history()
        self.init_portfolio()
        cprint("🌙 Moon Dev's CDEM Agent initialized!", "green")

    def init_sentiment_history(self):
        """Initialize sentiment history file"""
        if not os.path.exists(SENTIMENT_HISTORY_FILE):
            pd.DataFrame(columns=['timestamp', 'ticker', 'sentiment_score', 'consensus', 'num_sources']).to_csv(SENTIMENT_HISTORY_FILE, index=False)

    def init_portfolio(self):
        """Initialize portfolio file"""
        if not os.path.exists(PORTFOLIO_FILE):
            pd.DataFrame(columns=['timestamp', 'ticker', 'position_size', 'entry_price', 'current_price', 'pnl', 'status']).to_csv(PORTFOLIO_FILE, index=False)

    def get_earnings_calendar(self, universe=STOCK_UNIVERSE, days_ahead=7):
        """Fetch upcoming earnings calendar with rate limiting"""
        today = datetime.today().date()
        end_date = today + timedelta(days=days_ahead)
        calendar = {}
        for ticker in universe:
            try:
                cal = yf.Ticker(ticker).calendar
                if not cal.empty and 'Earnings Date' in cal.index:
                    earnings_date = cal.loc['Earnings Date'][0].date()
                    if today < earnings_date <= end_date:
                        calendar[ticker] = earnings_date
            except Exception as e:
                cprint(f"Error fetching calendar for {ticker}: {e}", "yellow")
            time.sleep(5)  # Increased sleep to 5 secs to avoid rate limits
        return calendar

    def assess_consensus(self, ticker, earnings_date):
        """Assess consensus 1 day before earnings using Grok LLM"""
        check_date = earnings_date - timedelta(days=1)
        if datetime.today().date() != check_date:
            return None  # Only check on T-1

        # Prompt Grok for deep multi-source sentiment analysis
        prompt = f"""
Analyze pre-earnings consensus for {ticker} as of {check_date}. Use tools to search X (tweets since {check_date - timedelta(days=1)}), Reddit r/stocks, StockTwits, Seeking Alpha, Bloomberg previews, and options IV data for sentiment.
Classify as:
- Good: >70% positive (beat expected, strong growth, hype, high IV optimism).
- Mixed: 40-70% positive (balanced, risks).
- Bad: <40% positive (weakness anticipated).
Detect leaks/hype. Return classification and score (0-100 positive), with reasoning and key sources.
"""
        # Call Grok via model_factory
        response = grok_model.generate(prompt)  # Adjust based on repo's model call method
        # Parse response (assume structured output: e.g., {"classification": "Good", "score": 75, "reasoning": "..."})
        parsed = self.parse_llm_response(response)  # Implement parser below
        sentiment_score = parsed.get('score', 0)
        consensus = parsed.get('classification', "Mixed")
        
        # Log
        df = pd.DataFrame({'timestamp': [datetime.now()], 'ticker': [ticker], 'sentiment_score': [sentiment_score], 'consensus': [consensus], 'num_sources': [len(parsed.get('sources', []))]})
        df.to_csv(SENTIMENT_HISTORY_FILE, mode='a', header=False, index=False)
        
        return consensus, sentiment_score

    def parse_llm_response(self, response):
        """Parse Grok's structured response (assume JSON-like)"""
        # Simple parse; improve with regex/JSON if needed
        try:
            from ast import literal_eval
            return literal_eval(response)  # Safe eval if string dict
        except:
            # Fallback: Extract from text
            classification = "Mixed" if "Mixed" in response else "Good" if "Good" in response else "Bad"
            score = 50  # Default
            # Extract score with regex
            import re
            match = re.search(r'score:\s*(\d+)', response, re.I)
            if match:
                score = int(match.group(1))
            return {'classification': classification, 'score': score, 'reasoning': response}

    def execute_trade(self, ticker, consensus):
        """Execute entry if Good, with risk management"""
        if consensus != "Good":
            return
        
        account = alpaca.get_account()
        portfolio_value = float(account.cash) + float(account.equity)  # Approx
        risk_amount = portfolio_value * RISK_PER_TRADE
        current_price = yf.Ticker(ticker).info['regularMarketPrice']
        stop_price = current_price * (1 - STOP_LOSS_PCT)
        slippage_buffer = current_price * 0.005
        position_size = risk_amount / (current_price - stop_price + slippage_buffer)  # Shares

        # Check max exposure
        active_exposure = self.get_active_exposure()
        if active_exposure + (position_size * current_price / portfolio_value) > MAX_EXPOSURE:
            return  # Skip if over cap
        
        # Sell SPY portion for allocation
        self.manage_spy(position_size * current_price, sell=True)
        
        # Buy stock or options
        if USE_OPTIONS:
            # Get ATM call expiring 1-2 weeks after
            options = yf.Ticker(ticker).option_chain()  # Simplify; use real exp date
            atm_call = options.calls.iloc[0]  # Placeholder; select ATM
            order_data = MarketOrderRequest(symbol=atm_call['contractSymbol'], qty=position_size * OPTION_LEVERAGE, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        else:
            order_data = MarketOrderRequest(symbol=ticker, qty=position_size, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        
        order = alpaca.submit_order(order_data)
        cprint(f"📈 Entered trade for {ticker}: {order.qty} @ {current_price}", "green")
        
        # Set stop-loss
        stop_order = StopOrderRequest(symbol=ticker, qty=order.qty, side=OrderSide.SELL, trail_percent=None, stop_price=stop_price, time_in_force=TimeInForce.GTC)
        alpaca.submit_order(stop_order)
        
        # Log position
        df = pd.DataFrame({'timestamp': [datetime.now()], 'ticker': [ticker], 'position_size': [position_size], 'entry_price': [current_price], 'current_price': [current_price], 'pnl': [0], 'status': ['open']})
        df.to_csv(PORTFOLIO_FILE, mode='a', header=False, index=False)

    def monitor_trades(self):
        """Monitor open positions for exit/trailing"""
        df = pd.read_csv(PORTFOLIO_FILE)
        open_trades = df[df['status'] == 'open']
        for index, trade in open_trades.iterrows():
            ticker = trade['ticker']
            current_price = yf.Ticker(ticker).info['regularMarketPrice']
            entry_price = trade['entry_price']
            gain_pct = (current_price - entry_price) / entry_price
            
            # Trailing stop if >10%
            if gain_pct > TRAILING_TRIGGER:
                high_price = max(current_price, trade.get('high_price', entry_price))  # Track high
                trail_stop = high_price * (1 - TRAILING_PCT)
                # Update stop order via Alpaca (simplified; use API to modify)
                cprint(f"🔄 Trailing stop for {ticker} to {trail_stop}", "yellow")
                # Log updated high
                df.at[index, 'high_price'] = high_price
            
            # Check for exit (1 day after earnings—assume from calendar)
            # Placeholder: Use schedule to check daily
            if self.should_exit(ticker):  # Implement based on earnings +1 day
                self.exit_trade(ticker, current_price)
        
        df.to_csv(PORTFOLIO_FILE, index=False)

    def should_exit(self, ticker):
        """Check if 1 day after earnings"""
        # Fetch earnings date from calendar, check if today == earnings +1 trading day
        return False  # Placeholder; implement with market calendar check

    def exit_trade(self, ticker, price):
        """Exit position and revert to SPY"""
        df = pd.read_csv(PORTFOLIO_FILE)
        trade = df[(df['ticker'] == ticker) & (df['status'] == 'open')].iloc[0]
        qty = trade['position_size']
        order = MarketOrderRequest(symbol=ticker, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
        alpaca.submit_order(order)
        pnl = (price - trade['entry_price']) * qty
        cprint(f"📉 Exited {ticker}: PNL {pnl}", "blue")
        
        # Update log
        df.at[trade.name, 'current_price'] = price
        df.at[trade.name, 'pnl'] = pnl
        df.at[trade.name, 'status'] = 'closed'
        df.to_csv(PORTFOLIO_FILE, index=False)
        
        # Revert to SPY
        allocation = qty * price
        self.manage_spy(allocation, sell=False)

    def manage_spy(self, amount, sell):
        """Buy/sell SPY for hybrid"""
        current_price = yf.Ticker(SPY_SYMBOL).info['regularMarketPrice']
        qty = amount / current_price
        side = OrderSide.SELL if sell else OrderSide.BUY
        order = MarketOrderRequest(symbol=SPY_SYMBOL, qty=qty, side=side, time_in_force=TimeInForce.DAY)
        alpaca.submit_order(order)
        action = "Sold" if sell else "Bought"
        cprint(f"🔄 {action} SPY for {amount}", "cyan")

    def get_active_exposure(self):
        """Calculate current active trade exposure"""
        df = pd.read_csv(PORTFOLIO_FILE)
        open_trades = df[df['status'] == 'open']
        exposure = sum(open_trades['position_size'] * open_trades['current_price']) / self.get_portfolio_value()
        return exposure

    def get_portfolio_value(self):
        """Get total portfolio value from Alpaca"""
        account = alpaca.get_account()
        return float(account.equity)

    def backtest_strategy(self):
        """Backtest CDEM on historical data"""
        # Use repo's backtesting.py (import and run with historical calendars/sentiment proxies)
        from backtesting import Backtest, Strategy  # From repo
        # Placeholder: Load historical data, simulate consensus, test returns/win rate/Sharpe
        pass  # Implement full backtest

    def run(self):
        """Main loop with scheduling"""
        cprint("🌙 Moon Dev's CDEM Agent starting (hourly checks)...", "green")
        
        schedule.every().day.at("09:00").do(self.backtest_strategy)  # Daily backtest/review
        schedule.every().monday.do(self.get_earnings_calendar)  # Weekly calendar update
        
        while True:
            # Daily consensus/trade check
            events = self.get_earnings_calendar()
            for ticker, date in events.items():
                consensus, score = self.assess_consensus(ticker, date)
                if consensus:
                    self.execute_trade(ticker, consensus)
            self.monitor_trades()
            
            schedule.run_pending()
            time.sleep(60 * CHECK_INTERVAL_MINUTES)  # Hourly

if __name__ == "__main__":
    try:
        agent = CDEMAgent()
        agent.backtest_strategy()  # Initial backtest
        agent.run()
    except KeyboardInterrupt:
        cprint("\n👋 Moon Dev's CDEM Agent shutting down gracefully...", "yellow")
    except Exception as e:
        cprint(f"\n❌ Fatal error: {str(e)}", "red")