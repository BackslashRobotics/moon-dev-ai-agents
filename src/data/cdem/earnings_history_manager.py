"""
Earnings Dates Manager for CDEM Dashboard
Handles tracking of past/upcoming earnings dates and retroactive calculation of post-earnings price movements.
Uses Grok LLM to fetch historical price data (no premium API needed).
Supports earnings hour (BMC/BMO/AMC) for accurate movement calculations.
"""

import json
import os
import sys
import threading
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Add parent directories to path for model imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

# Load environment variables
load_dotenv()

# Paths - go up to project root, then into app_data folder
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "app_data", "earnings_dates.json")

# Thread-safe file operations lock
EARNINGS_FILE_LOCK = threading.Lock()

# Initialize Grok model for historical price fetching
try:
    from src.models.model_factory import ModelFactory
    model_factory = ModelFactory()
    grok_model = model_factory.get_model("xai")
    print("✅ Grok model initialized for historical price data")
except Exception as e:
    grok_model = None
    print(f"⚠️ Failed to initialize Grok model: {e} - movement calculations disabled")


def load_earnings_history():
    """Load earnings history from JSON file (thread-safe)"""
    with EARNINGS_FILE_LOCK:
        if not os.path.exists(HISTORY_FILE):
            return {}
        
        try:
            with open(HISTORY_FILE, "r") as f:
                content = f.read()
                if not content or content.strip() == "":
                    return {}
                return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Error loading earnings history (JSON decode): {e}")
            return {}
        except Exception as e:
            print(f"Error loading earnings history: {e}")
            return {}


def save_earnings_history(history):
    """Save earnings history to JSON file (thread-safe)"""
    with EARNINGS_FILE_LOCK:
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            
            # Write atomically: write to temp file, then rename
            temp_file = HISTORY_FILE + ".tmp"
            with open(temp_file, "w") as f:
                json.dump(history, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            
            # Atomic rename
            os.replace(temp_file, HISTORY_FILE)
        except Exception as e:
            print(f"Error saving earnings history: {e}")


def get_historical_price_movement(ticker, earnings_date_str, earnings_hour="amc"):
    """
    Calculate price movement for the ACTUAL trade window based on earnings timing.
    Matches the exact buy→sell windows the agent uses:
    - BMC: Buy @ 3:00 PM → Sell @ 3:55 PM (same day, ~55 min window)
    - BMO: Buy @ T-1 close → Sell @ T-0 open (overnight gap)
    - AMC: Buy @ T-0 close → Sell @ T+1 open (overnight gap)
    
    Args:
        ticker: Stock ticker symbol
        earnings_date_str: Earnings date in "YYYY-MM-DD" format
        earnings_hour: "bmc"/"bmo"/"amc"
    
    Returns:
        dict with movement_pct, entry_price, exit_price, or None if failed
    """
    if not grok_model:
        return None
    
    try:
        # Parse earnings date
        earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        
        # Determine which trading window to measure based on earnings hour
        if earnings_hour in ["bmc"]:
            # BMC: Price at 3:00 PM → Close (same day)
            # Since 3:55 PM is close to 4:00 PM close, we'll use close price as exit
            system_prompt = """You are a financial data assistant. You have access to historical intraday stock price data.
Respond ONLY with valid JSON in this exact format: {"entry_price": 123.45, "exit_price": 124.56}
No additional text, explanations, or formatting. Just the JSON object."""
            
            user_prompt = f"""Get the price for {ticker} at approximately 3:00 PM ET and the closing price (4:00 PM ET) on {earnings_date_str}.
If it falls on a weekend or holiday, use the next available trading day.
Return JSON with entry_price (3PM price) and exit_price (close price)."""
            
        elif earnings_hour in ["bmo"]:
            # BMO: Previous day close → Earnings day open (overnight gap)
            prev_day = earnings_date - timedelta(days=1)
            system_prompt = """You are a financial data assistant. You have access to historical stock price data.
Respond ONLY with valid JSON in this exact format: {"entry_price": 123.45, "exit_price": 124.56}
No additional text, explanations, or formatting. Just the JSON object."""
            
            user_prompt = f"""Get the closing price for {ticker} on {prev_day.strftime('%Y-%m-%d')} and the opening price on {earnings_date_str}.
This measures the overnight gap from close to open across earnings.
If dates fall on weekends/holidays, use the nearest available trading days.
Return JSON with entry_price (prior day close) and exit_price (earnings day open)."""
            
        else:  # "amc" - After market close
            # AMC: Earnings day close → Next day open (overnight gap)
            next_day = earnings_date + timedelta(days=1)
            system_prompt = """You are a financial data assistant. You have access to historical stock price data.
Respond ONLY with valid JSON in this exact format: {"entry_price": 123.45, "exit_price": 124.56}
No additional text, explanations, or formatting. Just the JSON object."""
            
            user_prompt = f"""Get the closing price for {ticker} on {earnings_date_str} and the opening price on {next_day.strftime('%Y-%m-%d')}.
This measures the overnight gap from close to open across earnings.
If dates fall on weekends/holidays, use the nearest available trading days.
Return JSON with entry_price (earnings day close) and exit_price (next day open)."""
        
        # Call Grok with timeout protection
        response = None
        for attempt in range(3):  # Up to 3 attempts
            try:
                response = grok_model.generate_response(system_prompt, user_prompt)
                if response and response.content:
                    break
            except Exception as e:
                print(f"Grok call attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    continue
                return None
        
        if not response or not response.content:
            print(f"Empty response from Grok for {ticker} on {earnings_date_str} ({earnings_hour})")
            return None
        
        # Parse JSON response
        data = json.loads(response.content.strip())
        entry_price = float(data.get("entry_price", 0))
        exit_price = float(data.get("exit_price", 0))
        
        if entry_price <= 0 or exit_price <= 0:
            print(f"Invalid price data from Grok for {ticker}: entry=${entry_price}, exit=${exit_price}")
            return None
        
        # Calculate movement percentage
        movement_pct = ((exit_price - entry_price) / entry_price) * 100
        
        return {
            "movement_pct": round(movement_pct, 2),
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2)
        }
        
    except json.JSONDecodeError as e:
        print(f"JSON parse error for {ticker}: {e}")
        print(f"Response content: {response.content if response else 'None'}")
        return None
    except Exception as e:
        print(f"Error calculating movement for {ticker}: {e}")
        return None


def update_ticker_history(ticker, earnings_date, sentiment_score=None, consensus=None, earnings_hour="amc"):
    """
    Update or create history entry for a ticker.
    Calculates movement if earnings date has passed.
    
    Args:
        ticker: Stock ticker symbol
        earnings_date: Earnings date as datetime.date or "YYYY-MM-DD" string
        sentiment_score: AI sentiment score (0-100)
        consensus: Consensus classification (Good/Mixed/Bad)
        earnings_hour: "bmc"/"bmo" or "amc" for accurate movement calculation
    """
    history = load_earnings_history()
    
    # Convert date to string if needed
    if isinstance(earnings_date, datetime):
        earnings_date = earnings_date.date()
    if hasattr(earnings_date, 'strftime'):
        earnings_date_str = earnings_date.strftime("%Y-%m-%d")
    else:
        earnings_date_str = str(earnings_date)
    
    # Parse date for comparison
    earnings_dt = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
    today = datetime.now().date()
    
    # Initialize entry
    if ticker not in history:
        history[ticker] = {}
    
    entry = history[ticker]
    entry["past_earnings_date"] = earnings_date_str
    entry["past_earnings_hour"] = earnings_hour
    
    if sentiment_score is not None:
        entry["sentiment_score"] = sentiment_score
    if consensus is not None:
        entry["consensus"] = consensus
    
    # Calculate movement if earnings day has passed and we're at least 1 trading day after
    days_since = (today - earnings_dt).days
    if days_since >= 1 and "price_movement_pct" not in entry:
        movement_data = get_historical_price_movement(ticker, earnings_date_str, earnings_hour)
        if movement_data:
            entry["price_movement_pct"] = movement_data["movement_pct"]
            entry["entry_price"] = movement_data["entry_price"]
            entry["exit_price"] = movement_data["exit_price"]
            entry["calculated_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"✅ Calculated movement for {ticker}: {movement_data['movement_pct']:+.2f}%")
    
    save_earnings_history(history)


def clear_old_history_if_needed(ticker, upcoming_earnings_date):
    """
    Clear ticker history if new earnings is within 7 days (changed from 3).
    This makes room for fresh sentiment and movement data.
    Clears both movement data AND past earnings date to prepare for new cycle.
    
    Args:
        ticker: Stock ticker symbol
        upcoming_earnings_date: Upcoming earnings date as datetime.date or string
    """
    history = load_earnings_history()
    
    if ticker not in history:
        return False  # Nothing to clear
    
    # Convert date to comparable format
    if isinstance(upcoming_earnings_date, str):
        upcoming_dt = datetime.strptime(upcoming_earnings_date, "%Y-%m-%d").date()
    elif hasattr(upcoming_earnings_date, 'date'):
        upcoming_dt = upcoming_earnings_date.date()
    else:
        upcoming_dt = upcoming_earnings_date
    
    today = datetime.now().date()
    days_until = (upcoming_dt - today).days
    
    # Clear if within 7 days (changed from 3)
    if days_until <= 7:
        print(f"🗑️ Clearing old history for {ticker} (new earnings in {days_until} days)")
        # Remove all old data including movement % and past earnings date
        del history[ticker]
        save_earnings_history(history)
        return True
    
    return False


def backfill_missing_movements():
    """
    Backfill movement calculations for any earnings that have passed
    but don't have movement data yet. Run this on dashboard load.
    Uses Grok LLM to fetch historical prices.
    
    Returns:
        dict: {"success": int, "failed": int, "failed_tickers": list}
    """
    if not grok_model:
        print("⚠️ Cannot backfill movements - Grok model not initialized")
        return {"success": 0, "failed": 0, "failed_tickers": []}
    
    history = load_earnings_history()
    today = datetime.now().date()
    updated_count = 0
    failed_count = 0
    failed_tickers = []
    
    for ticker, entry in history.items():
        # Skip if already has movement data
        if "price_movement_pct" in entry:
            continue
        
        # Skip if earnings date not set (check both old and new field names for backwards compatibility)
        earnings_date_str = entry.get("past_earnings_date") or entry.get("last_earnings_date")
        if not earnings_date_str:
            continue
        
        # Get earnings hour (default to amc if not set)
        earnings_hour = entry.get("past_earnings_hour", "amc")
        
        # Check if earnings has passed
        earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        days_since = (today - earnings_date).days
        
        if days_since >= 1:
            print(f"🔄 Backfilling movement for {ticker} (earnings: {earnings_date_str} {earnings_hour})")
            movement_data = get_historical_price_movement(ticker, earnings_date_str, earnings_hour)
            if movement_data:
                entry["price_movement_pct"] = movement_data["movement_pct"]
                entry["entry_price"] = movement_data["entry_price"]
                entry["exit_price"] = movement_data["exit_price"]
                entry["calculated_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated_count += 1
                print(f"  ✅ {ticker}: {movement_data['movement_pct']:+.2f}%")
            else:
                failed_count += 1
                failed_tickers.append(ticker)
                print(f"  ❌ {ticker}: Failed to fetch historical prices")
    
    if updated_count > 0:
        save_earnings_history(history)
        print(f"✅ Backfilled {updated_count} movement calculations")
    
    if failed_count > 0:
        print(f"⚠️ {failed_count} tickers failed: {', '.join(failed_tickers[:10])}{'...' if len(failed_tickers) > 10 else ''}")
    
    return {"success": updated_count, "failed": failed_count, "failed_tickers": failed_tickers}


def get_ticker_movement(ticker):
    """
    Get the price movement for a ticker if available.
    
    Args:
        ticker: Stock ticker symbol
    
    Returns:
        float: Movement percentage, or None if not available
    """
    history = load_earnings_history()
    if ticker in history and "price_movement_pct" in history[ticker]:
        return history[ticker]["price_movement_pct"]
    return None


def get_historical_sentiment(ticker, earnings_date_str, earnings_hour="amc"):
    """
    Calculate historical sentiment as if it was gathered 10 minutes before the trade window.
    Mimics the CDEM agent's sentiment analysis for historical earnings dates.
    
    Trade windows (and thus sentiment gathering times):
    - BMC: Sentiment gathered on T-0 morning (trade at 3:00 PM same day)
    - BMO: Sentiment gathered on T-1 afternoon (trade at 3:59 PM T-1)
    - AMC: Sentiment gathered on T-0 afternoon (trade at 3:59 PM same day)
    
    Args:
        ticker: Stock ticker symbol
        earnings_date_str: Earnings date in "YYYY-MM-DD" format
        earnings_hour: "bmc"/"bmo"/"amc"
    
    Returns:
        dict with consensus, sentiment_score, or None if failed
    """
    if not grok_model:
        return None
    
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        
        # Determine when sentiment should have been gathered (matches agent logic)
        if earnings_hour in ["bmo"]:
            # BMO: Sentiment on T-1 (day before earnings)
            sentiment_date = earnings_date - timedelta(days=1)
        else:  # "amc" or "bmc"
            # AMC/BMC: Sentiment on T-0 (earnings day)
            sentiment_date = earnings_date
        
        # Build sentiment prompt (matches agent's assess_consensus)
        prompt = f"""
Use your up-to-date knowledge to analyze pre-earnings consensus for {ticker} as of {sentiment_date}. Dig deep into available data from sources like X (tweets since {sentiment_date - timedelta(days=1)}), Reddit r/stocks, StockTwits, Seeking Alpha, Bloomberg previews, and options IV data for sentiment.
Classify as:
- Good: >70% positive (beat expected, strong growth, hype).
- Mixed: 40-70% positive (balanced views with risks).
- Bad: <40% positive (anticipated weakness).
Detect leaks/hype. Reason step-by-step before classifying. Return as JSON: {{"classification": "Good", "score": 75, "reasoning": "...", "sources": []}}.
"""
        system_prompt = "You are a financial sentiment analyst. Respond ONLY with valid JSON for the classification. No additional text, explanations, or formatting."
        
        # Run 5 parallel sentiment analyses (matches agent logic)
        def run_single_sentiment(run_num):
            """Run one sentiment analysis with retry logic"""
            for attempt in range(5):  # Up to 5 attempts per run
                try:
                    response = grok_model.generate_response(system_prompt, prompt)
                    if not response or not response.content:
                        continue
                    
                    # Parse JSON
                    content = response.content.strip()
                    json_start = content.find('{')
                    json_end = content.rfind('}')
                    if json_start == -1 or json_end == -1:
                        continue
                    
                    json_str = content[json_start:json_end+1]
                    parsed = json.loads(json_str)
                    score = int(parsed.get("score", 0))
                    
                    if 0 <= score <= 100:
                        return score
                except:
                    continue
            
            return None
        
        # Execute 5 runs in parallel
        scores = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(run_single_sentiment, i): i for i in range(1, 6)}
            for future in as_completed(futures):
                score = future.result()
                if score is not None:
                    scores.append(score)
        
        if not scores:
            print(f"❌ Failed to get sentiment for {ticker} on {sentiment_date}")
            return None
        
        # Calculate average and classify
        avg_score = sum(scores) / len(scores)
        if avg_score >= 70:
            consensus = "Good"
        elif avg_score >= 40:
            consensus = "Mixed"
        else:
            consensus = "Bad"
        
        print(f"✅ Historical sentiment for {ticker} ({sentiment_date}): {consensus} ({avg_score:.1f})")
        
        return {
            "consensus": consensus,
            "sentiment_score": round(avg_score, 2),
            "sentiment_date": sentiment_date.strftime("%Y-%m-%d"),
            "scores": scores
        }
        
    except Exception as e:
        print(f"Error calculating historical sentiment for {ticker}: {e}")
        return None


def backfill_historical_sentiment():
    """
    Backfill historical sentiment for past earnings that have movement data.
    Simulates running sentiment analysis 10 minutes before trade execution.
    
    Returns:
        dict: {"success": int, "failed": int, "failed_tickers": list}
    """
    if not grok_model:
        print("⚠️ Cannot backfill sentiment - Grok model not initialized")
        return {"success": 0, "failed": 0, "failed_tickers": []}
    
    history = load_earnings_history()
    updated_count = 0
    failed_count = 0
    failed_tickers = []
    
    for ticker, entry in history.items():
        # Only backfill for entries that have movement data but no sentiment
        if "price_movement_pct" not in entry:
            continue
        
        if "sentiment_score" in entry and entry.get("sentiment_score") is not None:
            continue  # Already has sentiment
        
        # Get past earnings info
        earnings_date_str = entry.get("past_earnings_date")
        earnings_hour = entry.get("past_earnings_hour", "amc")
        
        if not earnings_date_str:
            continue
        
        print(f"Calculating historical sentiment for {ticker} (earnings: {earnings_date_str} {earnings_hour.upper()})...")
        
        # Calculate sentiment
        sentiment_data = get_historical_sentiment(ticker, earnings_date_str, earnings_hour)
        
        if sentiment_data:
            entry["sentiment_score"] = sentiment_data["sentiment_score"]
            entry["consensus"] = sentiment_data["consensus"]
            entry["sentiment_date"] = sentiment_data["sentiment_date"]
            updated_count += 1
        else:
            failed_count += 1
            failed_tickers.append(ticker)
    
    if updated_count > 0:
        save_earnings_history(history)
        print(f"✅ Backfilled {updated_count} historical sentiment scores")
    
    if failed_count > 0:
        print(f"⚠️ {failed_count} tickers failed sentiment backfill: {', '.join(failed_tickers[:10])}{'...' if len(failed_tickers) > 10 else ''}")
    
    return {"success": updated_count, "failed": failed_count, "failed_tickers": failed_tickers}


if __name__ == "__main__":
    # Test the module
    print("Testing Earnings History Manager...")
    print("Note: Now uses Grok LLM for historical prices (no premium API needed)")
    
    # Example: Calculate movement for a past earnings
    test_ticker = "AAPL"
    test_date = "2024-10-30"  # Recent past earnings date
    
    print(f"\nTesting movement calculation for {test_ticker} on {test_date}")
    result = get_historical_price_movement(test_ticker, test_date)
    if result:
        print(f"Entry Price: ${result['entry_price']}")
        print(f"Next Day Price: ${result['next_day_price']}")
        print(f"Movement: {result['movement_pct']:+.2f}%")
    else:
        print("Failed to calculate movement")
    
    print("\nRunning backfill...")
    backfill_missing_movements()
    
    print("\nCurrent history:")
    history = load_earnings_history()
    for ticker, data in history.items():
        print(f"{ticker}: {data}")

