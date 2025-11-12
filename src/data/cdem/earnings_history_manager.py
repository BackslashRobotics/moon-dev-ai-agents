"""
Earnings Dates Manager for CDEM Dashboard
Handles tracking of past/upcoming earnings dates and retroactive calculation of post-earnings price movements.
Uses Grok LLM to fetch historical price data (no premium API needed).
Supports earnings hour (BMC/BMO/AMC) for accurate movement calculations.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Add parent directories to path for model imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

# Load environment variables
load_dotenv()

# Paths - go up to project root, then into app_data folder
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "app_data", "earnings_dates.json")

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
    """Load earnings history from JSON file"""
    if not os.path.exists(HISTORY_FILE):
        return {}
    
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading earnings history: {e}")
        return {}


def save_earnings_history(history):
    """Save earnings history to JSON file"""
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=4)
    except Exception as e:
        print(f"Error saving earnings history: {e}")


def get_historical_price_movement(ticker, earnings_date_str, earnings_hour="amc"):
    """
    Calculate price movement based on earnings announcement time.
    - BMC/BMO (before market open): Same day open → close
    - AMC (after market close): Next day open → close
    Uses Grok LLM with its real-time knowledge to fetch historical prices.
    
    Args:
        ticker: Stock ticker symbol
        earnings_date_str: Earnings date in "YYYY-MM-DD" format
        earnings_hour: "bmc"/"bmo" (before market) or "amc" (after market close)
    
    Returns:
        dict with movement_pct, entry_price, exit_price, or None if failed
    """
    if not grok_model:
        return None
    
    try:
        # Parse earnings date
        earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        
        # Determine which trading session to measure based on earnings hour
        if earnings_hour in ["bmc", "bmo"]:  # Before market open
            # Movement = same day open → close
            system_prompt = """You are a financial data assistant. You have access to historical stock price data.
Respond ONLY with valid JSON in this exact format: {"open": 123.45, "close": 124.56}
No additional text, explanations, or formatting. Just the JSON object."""
            
            user_prompt = f"""Get the opening and closing prices for {ticker} on {earnings_date_str}.
If it falls on a weekend or holiday, use the next available trading day.
Return JSON with the open and close prices."""
            
        else:  # "amc" - After market close
            # Movement = next day open → close
            next_trading_date = earnings_date + timedelta(days=1)
            system_prompt = """You are a financial data assistant. You have access to historical stock price data.
Respond ONLY with valid JSON in this exact format: {"open": 123.45, "close": 124.56}
No additional text, explanations, or formatting. Just the JSON object."""
            
            user_prompt = f"""Get the opening and closing prices for {ticker} on {next_trading_date.strftime('%Y-%m-%d')} (the day after earnings).
If it falls on a weekend or holiday, use the next available trading day.
Return JSON with the open and close prices."""
        
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
        open_price = float(data.get("open", 0))
        close_price = float(data.get("close", 0))
        
        if open_price <= 0 or close_price <= 0:
            print(f"Invalid price data from Grok for {ticker}: open=${open_price}, close=${close_price}")
            return None
        
        # Calculate movement percentage
        movement_pct = ((close_price - open_price) / open_price) * 100
        
        return {
            "movement_pct": round(movement_pct, 2),
            "entry_price": round(open_price, 2),
            "exit_price": round(close_price, 2)
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

