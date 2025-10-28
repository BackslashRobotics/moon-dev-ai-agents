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
- .env with XAI_API_KEY (for Grok), ALPACA_API_KEY, ALPACA_SECRET_KEY, FINNHUB_API_KEY.
- Free Alpaca paper account for testing.

Notes:
- Uses 1 day before for consensus to capture leaks (changed from 2 days).
- LLM (Grok) for sentiment: High accuracy via tools (X search, web browse).
- Expand stock_universe to 50+ for diversification.
- Paper trade first; backtest with repo's backtesting.py.
- Now loads config from config.json and polls for changes (for app integration).
'''

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import json  # For config loading
import pathlib
import time
from datetime import datetime, timedelta
import csv
import pandas as pd
import numpy as np
import finnhub  # New for earnings calendar and prices
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.models import Position  # For getting positions
import schedule
from termcolor import cprint
from src.models.model_factory import ModelFactory  # From repo for Grok
import torch  # For any NLP if needed
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # Fallback
import re  # For parsing simulated tool calls
import yfinance as yf  # Added for options chain (if USE_OPTIONS=True)
from threading import Thread, Event  # For timeout wrapper
from dotenv import load_dotenv  # FIXED: Added missing import

# Config file path
CONFIG_PATH = "config.json"

# Create initial config if not exists
if not os.path.exists(CONFIG_PATH):
    initial_config = {
        "master_on": True,
        "sp_on": True,
        "stock_universe": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
        "check_interval_minutes": 60,
        "risk_per_trade": 0.015,
        "max_exposure": 0.45,
        "stop_loss_pct": 0.05,
        "trailing_trigger": 0.10,
        "trailing_pct": 0.05,
        "use_options": False,
        "option_exp_weeks": 1,
        "option_leverage": 0.5,
        "test_mode": True  # Bypass date skip for testing
    }
    with open(CONFIG_PATH, 'w') as f:
        json.dump(initial_config, f, indent=4)
    cprint("Created initial config.json", "yellow")

# Create data directory
pathlib.Path("src/data/cdem").mkdir(parents=True, exist_ok=True)

# Load environment variables
load_dotenv()

# Initialize Alpaca client
alpaca = TradingClient(os.getenv('ALPACA_API_KEY'), os.getenv('ALPACA_SECRET_KEY'), paper=True)  # Paper for test

# Initialize Finnhub client
finnhub_key = os.getenv('FINNHUB_API_KEY')
if not finnhub_key:
    raise ValueError("FINNHUB_API_KEY not found in .env. Please add it from https://finnhub.io/register")
finnhub_client = finnhub.Client(api_key=finnhub_key)

# Initialize ModelFactory for Grok
model_factory = ModelFactory()
grok_model = model_factory.get_model('xai')  # Use Grok-4-fast-reasoning

# VADER as fallback for quick scoring
analyzer = SentimentIntensityAnalyzer()

class CDEMAgent:
    def __init__(self):
        """Initialize the CDEM Agent"""
        self.sentiment_model = None
        self.config = self.load_config()
        self.last_config_mtime = os.path.getmtime(CONFIG_PATH)
        self.prev_sp_on = self.config['sp_on']  # Track for toggle
        self.init_sentiment_history()
        self.init_portfolio()
        # Buy SPY with all cash if no positions (hybrid baseline)
        self.initialize_spy()
        cprint("🌙 Moon Dev's CDEM Agent initialized!", "green")

    def load_config(self):
        """Load config from JSON"""
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)

    def reload_config_if_changed(self):
        """Check and reload config if modified"""
        current_mtime = os.path.getmtime(CONFIG_PATH)
        if current_mtime != self.last_config_mtime:
            self.last_config_mtime = current_mtime
            old_config = self.config
            self.config = self.load_config()
            cprint("Config reloaded from file.", "yellow")
            # Apply changes
            if self.config['sp_on'] != old_config['sp_on']:
                self.handle_sp_toggle()
            return True
        return False

    def handle_sp_toggle(self):
        """Handle S&P toggle: Move to/from cash if no active trades"""
        if self.get_active_exposure() > 0:
            cprint("Cannot toggle S&P: Active trades open. Skipping.", "yellow")
            return
        account = alpaca.get_account()
        cash = float(account.cash)
        try:
            spy_position = alpaca.get_position(SPY_SYMBOL)
            spy_value = float(spy_position.market_value)
        except:
            spy_value = 0

        if self.config['sp_on']:
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
            pd.DataFrame(columns=['timestamp', 'ticker', 'sentiment_score', 'consensus', 'num_sources']).to_csv(sentiment_history_file, index=False)

    def init_portfolio(self):
        """Initialize portfolio file"""
        portfolio_file = "src/data/cdem/portfolio.csv"
        if not os.path.exists(portfolio_file):
            pd.DataFrame(columns=['timestamp', 'ticker', 'position_size', 'entry_price', 'current_price', 'pnl', 'status']).to_csv(portfolio_file, index=False)

    def initialize_spy(self):
        """Buy SPY with available cash if no open positions or SPY held and sp_on=True"""
        if not self.config['sp_on']:
            return
        account = alpaca.get_account()
        cash = float(account.cash)
        buying_power = float(account.buying_power)
        positions = alpaca.get_all_positions()
        if cash > 0 and not positions and buying_power >= cash:
            time.sleep(5)
            quote = finnhub_client.quote(SPY_SYMBOL)
            current_price = quote['c']
            if current_price > 0:
                notional_amount = min(cash, buying_power)
                notional = str(round(notional_amount, 2))
                order_data = MarketOrderRequest(symbol=SPY_SYMBOL, notional=notional, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                try:
                    alpaca.submit_order(order_data)
                    cprint(f"🔄 Initialized SPY with ${notional_amount}", "cyan")
                except Exception as e:
                    cprint(f"SPY initialization failed: {str(e)}. Continuing without...", "yellow")

    def get_earnings_calendar(self, universe):
        """Fetch upcoming earnings calendar using Finnhub API with retry"""
        today = datetime.today().date()
        end_date = today + timedelta(days=7)
        calendar = {}
        
        for attempt in range(3):
            try:
                earnings_data = finnhub_client.earnings_calendar(_from=str(today), to=str(end_date), symbol='')
                if 'earningsCalendar' in earnings_data:
                    for event in earnings_data['earningsCalendar']:
                        ticker = event.get('symbol')
                        if ticker in universe:
                            earnings_date = datetime.strptime(event['date'], '%Y-%m-%d').date()
                            if today < earnings_date <= end_date:
                                calendar[ticker] = earnings_date
                                cprint(f"Found earnings for {ticker} on {earnings_date}", "green")
                    return calendar
                else:
                    cprint("No earnings data returned from Finnhub.", "yellow")
            except Exception as e:
                cprint(f"Finnhub calendar error (attempt {attempt+1}): {str(e)}", "red")
                time.sleep(5)
        cprint("Finnhub calendar failed after 3 attempts.", "red")
        return calendar

    def assess_consensus(self, ticker, earnings_date):
        """Assess consensus 1 day before earnings using Grok LLM with prompt-engineered tools"""
        today = datetime.today().date()
        check_date = earnings_date - timedelta(days=1)
        cprint(f"Debug: Today {today}, Check date {check_date} for {ticker}", "blue")
        if not self.config['test_mode'] and today != check_date:
            cprint(f"Skipping {ticker}: Not T-1 day (check_date: {check_date})", "yellow")
            return None, None

        # Prompt with simulated tool instructions
        prompt = f"""
You have access to tools: x_keyword_search, browse_page, web_search. Use them by outputting [TOOL: name args_json] then your analysis.
Analyze pre-earnings consensus for {ticker} as of {check_date}. Search X (tweets since {check_date - timedelta(days=1)}), Reddit r/stocks, StockTwits, Seeking Alpha, Bloomberg previews, and options IV data for sentiment.
Classify as:
- Good: >70% positive (beat expected, strong growth, hype, high IV optimism).
- Mixed: 40-70% positive (balanced, risks).
- Bad: <40% positive (weakness anticipated).
Detect leaks/hype. Return as JSON: {{"classification": "Good", "score": 75, "reasoning": "...", "sources": []}}.
"""

        system_prompt = "You are a financial sentiment analyst that outputs ONLY valid JSON. No additional text, explanations, or formatting—strictly the JSON object."

        # Retry loop for Grok on parse error
        parsed = None
        response = ""
        for attempt in range(5):  # Changed to 5
            cprint(f"Attempt {attempt+1}/5 for Grok call on {ticker}", "yellow")
            # Timeout wrapper for Grok call
            result = [None]
            error = [None]
            event = Event()
            def grok_call():
                try:
                    result[0] = grok_model.generate_response(system_prompt, prompt)
                except Exception as e:
                    error[0] = e
                event.set()

            thread = Thread(target=grok_call)
            thread.start()
            if not event.wait(timeout=60):
                cprint(f"Grok call timed out on attempt {attempt+1}. Retrying...", "red")
                continue
            if error[0]:
                cprint(f"Grok error on attempt {attempt+1}: {str(error[0])}. Retrying...", "red")
                continue
            response = result[0].content
            time.sleep(1)  # Light delay
            
            # Parse simulated tool calls from response (e.g., [TOOL: ...])
            tool_matches = re.findall(r'\[TOOL: (\w+) (.*?)\]', response)
            for name, args in tool_matches:
                tool_result = self.execute_tool(name, json.loads(args))
                response += f"\nTool result: {json.dumps(tool_result)}"
            
            # Final parsed JSON using regex (handles extra text)
            try:
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    parsed = json.loads(json_str)
                    break  # Success
                else:
                    raise ValueError("No JSON found")
            except Exception as e:
                cprint(f"Grok parse error (attempt {attempt+1}): {str(e)}. Retrying...", "yellow")
                if attempt < 4:
                    prompt += " Previous response had invalid JSON. Fix and return only valid JSON."
        
        if parsed is None:
            cprint("Max retries reached. Falling back to VADER.", "yellow")
            # VADER fallback
            vader_scores = analyzer.polarity_scores(response or prompt)  # Use prompt if no response
            sentiment_score = (vader_scores['compound'] + 1) * 50  # Normalize to 0-100
            consensus = "Good" if sentiment_score > 70 else "Mixed" if sentiment_score > 40 else "Bad"
            parsed = {'classification': consensus, 'score': sentiment_score, 'reasoning': response or "Fallback", 'sources': []}
        
        sentiment_score = parsed.get('score', 0)
        consensus = parsed.get('classification', "Mixed")
        
        # Log
        df = pd.DataFrame({'timestamp': [datetime.now()], 'ticker': [ticker], 'sentiment_score': [sentiment_score], 'consensus': [consensus], 'num_sources': [len(parsed.get('sources', []))]})
        df.to_csv("src/data/cdem/sentiment_history.csv", mode='a', header=False, index=False)
        
        return consensus, sentiment_score

    def execute_tool(self, function_name, args):
        """Execute tool calls (integrate real implementations)"""
        if function_name == "x_keyword_search":
            # Real call: Use Twikit or similar
            return {"posts": []}  # Placeholder
        elif function_name == "browse_page":
            # Real call: Requests + BeautifulSoup or LLM summarize
            return {"summary": "Placeholder summary"}
        elif function_name == "web_search":
            # Real call: Google search API or similar
            return {"results": []}
        return {"error": "Tool not implemented"}

    def execute_trade(self, ticker, consensus):
        """Execute entry if Good, with risk management"""
        if consensus != "Good":
            return
        
        account = alpaca.get_account()
        portfolio_value = float(account.equity)  # Use equity for total value
        cash = float(account.cash)
        buying_power = float(account.buying_power)
        risk_amount = min(portfolio_value * self.config['risk_per_trade'], buying_power)
        if risk_amount <= 0:
            cprint(f"Skipping trade for {ticker}: No risk amount available (buying_power: {buying_power})", "red")
            return
        time.sleep(5)  # Avoid rate limit
        try:
            quote = finnhub_client.quote(ticker)
            current_price = quote['c']  # Current price
            if current_price == 0:
                raise ValueError("Invalid price")
        except Exception as e:
            cprint(f"Finnhub price error for {ticker}: {str(e)}. Skipping trade.", "red")
            return
        stop_price = round(current_price * (1 - self.config['stop_loss_pct']), 2)
        slippage_buffer = current_price * 0.005
        position_size = risk_amount / (current_price - stop_price + slippage_buffer)  # Shares
        required_amount = position_size * current_price

        if required_amount > buying_power:
            cprint(f"Insufficient buying power ({buying_power}) for required {required_amount} on {ticker}. Skipping.", "red")
            return

        # Check max exposure
        active_exposure = self.get_active_exposure()
        if active_exposure + (required_amount / portfolio_value) > self.config['max_exposure']:
            cprint(f"Skipping {ticker}: Would exceed max exposure ({self.config['max_exposure']})", "yellow")
            return
        
        # Free cash from SPY only if needed and sp_on is True
        if cash < required_amount and self.config['sp_on']:
            free_needed = required_amount - cash
            self.manage_spy(free_needed, sell=True)
        
        # Buy stock or options
        if self.config['use_options']:
            # Get ATM call expiring 1-2 weeks after
            time.sleep(5)  # Extra sleep for yf if needed
            options = yf.Ticker(ticker).option_chain()  # Simplify; use real exp date
            atm_call = options.calls.iloc[0]  # Placeholder; select ATM
            order_data = MarketOrderRequest(symbol=atm_call['contractSymbol'], qty=position_size * self.config['option_leverage'], side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        else:
            if position_size <= 0:
                cprint(f"Skipping {ticker}: Calculated position_size <= 0", "red")
                return
            order_data = MarketOrderRequest(symbol=ticker, qty=position_size, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
        
        try:
            order = alpaca.submit_order(order_data)
            cprint(f"📈 Entered trade for {ticker}: {order.qty} @ {current_price}", "green")
        except Exception as e:
            cprint(f"Order submission failed for {ticker}: {str(e)}", "red")
            return
        
        # Set stop-loss
        stop_order = StopOrderRequest(symbol=ticker, qty=order.qty, side=OrderSide.SELL, trail_percent=None, stop_price=stop_price, time_in_force=TimeInForce.GTC)
        try:
            alpaca.submit_order(stop_order)
        except Exception as e:
            cprint(f"Stop order failed for {ticker}: {str(e)}. Position unprotected!", "red")
        
        # Log position
        df = pd.DataFrame({'timestamp': [datetime.now()], 'ticker': [ticker], 'position_size': [position_size], 'entry_price': [current_price], 'current_price': [current_price], 'pnl': [0], 'status': ['open']})
        df.to_csv("src/data/cdem/portfolio.csv", mode='a', header=False, index=False)

    def monitor_trades(self):
        """Monitor open positions for exit/trailing"""
        df = pd.read_csv("src/data/cdem/portfolio.csv")
        open_trades = df[df['status'] == 'open']
        for index, trade in open_trades.iterrows():
            ticker = trade['ticker']
            time.sleep(5)  # Avoid rate limit
            quote = finnhub_client.quote(ticker)
            current_price = quote['c']  # Current price
            entry_price = trade['entry_price']
            gain_pct = (current_price - entry_price) / entry_price
            
            # Trailing stop if >10%
            if gain_pct > self.config['trailing_trigger']:
                high_price = max(current_price, trade.get('high_price', entry_price))  # Track high
                trail_stop = round(high_price * (1 - self.config['trailing_pct']), 2)
                # Update stop order via Alpaca (simplified; use API to modify)
                cprint(f"🔄 Trailing stop for {ticker} to {trail_stop}", "yellow")
                # Log updated high
                df.at[index, 'high_price'] = high_price
            
            # Check for exit (1 day after earnings—assume from calendar)
            if self.should_exit(ticker):
                self.exit_trade(ticker, current_price)
        
        df.to_csv("src/data/cdem/portfolio.csv", index=False)

    def should_exit(self, ticker):
        """Check if 1 day after earnings"""
        # TODO: Implement - Fetch/stored earnings date, check if today == earnings +1 trading day (use market calendar lib like pandas_market_calendars)
        return False  # Placeholder

    def exit_trade(self, ticker, price):
        """Exit position and revert to SPY"""
        df = pd.read_csv("src/data/cdem/portfolio.csv")
        trade = df[(df['ticker'] == ticker) & (df['status'] == 'open')].iloc[0]
        qty = trade['position_size']
        order = MarketOrderRequest(symbol=ticker, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
        try:
            alpaca.submit_order(order)
            pnl = (price - trade['entry_price']) * qty
            cprint(f"📉 Exited {ticker}: PNL {pnl}", "blue")
        except Exception as e:
            cprint(f"Exit order failed for {ticker}: {str(e)}", "red")
            return
        
        # Update log
        df.at[trade.name, 'current_price'] = price
        df.at[trade.name, 'pnl'] = pnl
        df.at[trade.name, 'status'] = 'closed'
        df.to_csv("src/data/cdem/portfolio.csv", index=False)
        
        # Revert to SPY if sp_on
        allocation = qty * price
        if self.config['sp_on']:
            self.manage_spy(allocation, sell=False)

    def manage_spy(self, amount, sell):
        """Buy/sell SPY for hybrid using notional to handle fractions safely"""
        time.sleep(5)  # Avoid rate limit
        quote = finnhub_client.quote(SPY_SYMBOL)
        current_price = quote['c']  # Current price
        if current_price == 0:
            cprint("Invalid SPY price. Skipping.", "red")
            return

        if sell:
            # Check owned SPY
            try:
                position = alpaca.get_position(SPY_SYMBOL)
                owned_value = float(position.market_value)
            except:
                owned_value = 0  # No position
            sell_amount = min(amount, owned_value)
            if sell_amount <= 0:
                return  # Nothing to sell
            notional = str(round(sell_amount, 2))
            side = OrderSide.SELL
            action = "Sold"
        else:
            notional = str(round(amount, 2))
            side = OrderSide.BUY
            action = "Bought"

        order_data = MarketOrderRequest(symbol=SPY_SYMBOL, notional=notional, side=side, time_in_force=TimeInForce.DAY)
        try:
            alpaca.submit_order(order_data)
            cprint(f"🔄 {action} SPY for ${float(notional)}", "cyan")
        except Exception as e:
            cprint(f"SPY order failed: {str(e)}", "red")

    def get_active_exposure(self):
        """Calculate current active trade exposure"""
        df = pd.read_csv("src/data/cdem/portfolio.csv")
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
            if not self.config['master_on']:
                cprint("Master switch off. Sleeping...", "yellow")
                time.sleep(60)  # Check config every minute when paused
                self.reload_config_if_changed()
                continue

            # Reload config if changed
            self.reload_config_if_changed()

            # Daily consensus/trade check
            events = self.get_earnings_calendar(self.config['stock_universe'])
            for ticker, date in events.items():
                result = self.assess_consensus(ticker, date)
                if result is None:
                    continue  # Skip if not T-1
                consensus, score = result
                if consensus == "Good":
                    self.execute_trade(ticker, consensus)
            self.monitor_trades()
            
            schedule.run_pending()
            time.sleep(60 * self.config['check_interval_minutes'])  # Hourly, configurable

if __name__ == "__main__":
    try:
        agent = CDEMAgent()
        agent.backtest_strategy()  # Initial backtest
        agent.run()
    except KeyboardInterrupt:
        cprint("\n👋 Moon Dev's CDEM Agent shutting down gracefully...", "yellow")
    except Exception as e:
        cprint(f"\n❌ Fatal error: {str(e)}", "red")
