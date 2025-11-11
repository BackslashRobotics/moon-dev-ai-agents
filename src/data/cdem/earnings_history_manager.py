"""
Earnings History Manager for CDEM Dashboard
Handles retroactive calculation of post-earnings price movements.
Uses secondary Finnhub API key to avoid rate limits on main key.
"""

import json
import os
from datetime import datetime, timedelta
import finnhub
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Paths
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "earnings_history.json")

# Initialize secondary Finnhub client for dashboard calculations
FINNHUB_DASHBOARD_KEY = os.getenv("FINNHUB_DASHBOARD_API_KEY")
if FINNHUB_DASHBOARD_KEY:
    finnhub_dashboard_client = finnhub.Client(api_key=FINNHUB_DASHBOARD_KEY)
else:
    finnhub_dashboard_client = None
    print("⚠️ FINNHUB_DASHBOARD_API_KEY not found - dashboard movement calculations disabled")


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


def get_historical_price_movement(ticker, earnings_date_str):
    """
    Calculate price movement from earnings day close to next trading day close.
    Uses daily candle data for retroactive calculation.
    
    Args:
        ticker: Stock ticker symbol
        earnings_date_str: Earnings date in "YYYY-MM-DD" format
    
    Returns:
        dict with movement_pct, entry_price, next_day_price, or None if failed
    """
    if not finnhub_dashboard_client:
        return None
    
    try:
        # Parse earnings date
        earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        
        # Convert to timestamps for Finnhub API (they use Unix timestamps)
        earnings_ts = int(datetime.combine(earnings_date, datetime.min.time()).timestamp())
        
        # Get data from 2 days before to 3 days after (to handle weekends/holidays)
        start_ts = earnings_ts - (2 * 86400)  # 2 days before
        end_ts = earnings_ts + (5 * 86400)    # 5 days after
        
        # Fetch daily candles
        candles = finnhub_dashboard_client.stock_candles(
            ticker,
            'D',  # Daily resolution
            start_ts,
            end_ts
        )
        
        if candles['s'] != 'ok' or not candles.get('c'):
            print(f"No candle data for {ticker} around {earnings_date_str}")
            return None
        
        # Find earnings day close and next trading day close
        timestamps = candles['t']
        closes = candles['c']
        
        # Convert earnings date to timestamp for comparison
        earnings_day_start = earnings_ts
        earnings_day_end = earnings_ts + 86400
        
        # Find the earnings day close
        entry_price = None
        entry_idx = None
        for i, ts in enumerate(timestamps):
            if earnings_day_start <= ts < earnings_day_end:
                entry_price = closes[i]
                entry_idx = i
                break
        
        if entry_price is None or entry_idx is None:
            print(f"Could not find earnings day price for {ticker} on {earnings_date_str}")
            return None
        
        # Find next trading day close (next index)
        if entry_idx + 1 >= len(closes):
            print(f"No next trading day data for {ticker} after {earnings_date_str}")
            return None
        
        next_day_price = closes[entry_idx + 1]
        
        # Calculate movement percentage
        movement_pct = ((next_day_price - entry_price) / entry_price) * 100
        
        return {
            "movement_pct": round(movement_pct, 2),
            "entry_price": round(entry_price, 2),
            "next_day_price": round(next_day_price, 2)
        }
        
    except Exception as e:
        print(f"Error calculating movement for {ticker}: {e}")
        return None


def update_ticker_history(ticker, earnings_date, sentiment_score=None, consensus=None):
    """
    Update or create history entry for a ticker.
    Calculates movement if earnings date has passed.
    
    Args:
        ticker: Stock ticker symbol
        earnings_date: Earnings date as datetime.date or "YYYY-MM-DD" string
        sentiment_score: AI sentiment score (0-100)
        consensus: Consensus classification (Good/Mixed/Bad)
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
    entry["last_earnings_date"] = earnings_date_str
    
    if sentiment_score is not None:
        entry["sentiment_score"] = sentiment_score
    if consensus is not None:
        entry["consensus"] = consensus
    
    # Calculate movement if earnings day has passed and we're at least 1 trading day after
    days_since = (today - earnings_dt).days
    if days_since >= 1 and "price_movement_pct" not in entry:
        movement_data = get_historical_price_movement(ticker, earnings_date_str)
        if movement_data:
            entry["price_movement_pct"] = movement_data["movement_pct"]
            entry["entry_price"] = movement_data["entry_price"]
            entry["day_after_price"] = movement_data["next_day_price"]
            entry["calculated_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"✅ Calculated movement for {ticker}: {movement_data['movement_pct']:+.2f}%")
    
    save_earnings_history(history)


def clear_old_history_if_needed(ticker, upcoming_earnings_date):
    """
    Clear ticker history if new earnings is within 3 days.
    This makes room for fresh sentiment and movement data.
    
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
    
    # Clear if within 3 days
    if days_until <= 3:
        print(f"🗑️ Clearing old history for {ticker} (new earnings in {days_until} days)")
        del history[ticker]
        save_earnings_history(history)
        return True
    
    return False


def backfill_missing_movements():
    """
    Backfill movement calculations for any earnings that have passed
    but don't have movement data yet. Run this on dashboard load.
    """
    if not finnhub_dashboard_client:
        print("⚠️ Cannot backfill movements - FINNHUB_DASHBOARD_API_KEY not set")
        return
    
    history = load_earnings_history()
    today = datetime.now().date()
    updated_count = 0
    
    for ticker, entry in history.items():
        # Skip if already has movement data
        if "price_movement_pct" in entry:
            continue
        
        # Skip if earnings date not set
        if "last_earnings_date" not in entry:
            continue
        
        # Check if earnings has passed
        earnings_date = datetime.strptime(entry["last_earnings_date"], "%Y-%m-%d").date()
        days_since = (today - earnings_date).days
        
        if days_since >= 1:
            print(f"🔄 Backfilling movement for {ticker} (earnings: {entry['last_earnings_date']})")
            movement_data = get_historical_price_movement(ticker, entry["last_earnings_date"])
            if movement_data:
                entry["price_movement_pct"] = movement_data["movement_pct"]
                entry["entry_price"] = movement_data["entry_price"]
                entry["day_after_price"] = movement_data["next_day_price"]
                entry["calculated_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated_count += 1
                print(f"  ✅ {ticker}: {movement_data['movement_pct']:+.2f}%")
    
    if updated_count > 0:
        save_earnings_history(history)
        print(f"✅ Backfilled {updated_count} movement calculations")


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
    
    # Example: Calculate movement for a past earnings
    test_ticker = "AAPL"
    test_date = "2025-01-30"  # Adjust to a recent past earnings date
    
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

