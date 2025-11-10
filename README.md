# 📊 CDEM Trading Agent - Consensus-Driven Earnings Management

A sophisticated GUI-driven stock trading system that combines AI-powered sentiment analysis with earnings calendar management. Built on Moon Dev's AI agent framework.

## 🎯 What is CDEM?

The **Consensus-Driven Earnings Management (CDEM)** agent is an automated stock trading system that:

- 📅 **Monitors earnings calendars** - Tracks upcoming earnings dates for your stock universe
- 🤖 **AI sentiment analysis** - Uses Grok AI to analyze earnings sentiment and generate trading signals
- 💼 **Automated execution** - Places trades via Tradier API based on configurable risk parameters
- 📈 **Risk management** - Enforces position limits, stop losses, trailing stops, and portfolio exposure caps
- 🎨 **Beautiful GUI** - Full-featured desktop app built with Tkinter for complete control

## 🖥️ Desktop Application Features

Launch the GUI with `python app.py`:

### Dashboard Tab
- Real-time earnings calendar with sentiment scores
- Color-coded past/upcoming earnings dates
- Agent status monitoring and control
- Trading mode indicator (Paper/Live)
- Account balance and buying power display
- Trade execution alerts

### Config Tab
- Stock universe management with auto-alphabetization
- Synchronized scrolling ticker/company name display
- Brand color-coded stock names (powered by Grok)
- Duplicate ticker detection and removal
- Trading parameters configuration:
  - Risk per trade percentage
  - Maximum portfolio exposure
  - Stop loss and trailing stop settings
  - Options trading toggle
  - Paper/live trading mode

### Visuals Tab
- Real-time sentiment score charts
- Portfolio exposure visualization
- Complete trade history table with PnL tracking
- Interactive matplotlib charts

### Logs
- **Terminal Logs** - Agent decision-making and trade execution
- **Grok Logs** - Formatted AI model interactions with reasoning
- **App Logs** - System events and API interactions
- Real-time updates with color-coded severity levels

### Settings
- Customizable UI preferences per tab
- Font sizes, column widths, refresh rates
- Auto-scroll and display options
- Persistent window position

## 🚀 Quick Start

### Prerequisites
- Python 3.10.9+
- Conda (recommended) or pip
- Windows (tested), macOS/Linux (should work)

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/BackslashRobotics/moon-dev-ai-agents.git
cd moon-dev-ai-agents
```

2. **Create conda environment**
```bash
conda create -n cdem-agent-env python=3.10.9
conda activate cdem-agent-env
pip install -r requirements.txt
```

3. **Set up environment variables**

Copy `.env_example` to `.env` and add your API keys:

```bash
# Required for CDEM Agent
XAI_API_KEY=your_xai_grok_api_key_here
FINNHUB_API_KEY=your_finnhub_api_key_here

# Tradier API (for live/paper trading)
TRADIER_LIVE_API_KEY=your_tradier_live_key_here
TRADIER_LIVE_ACCOUNT_ID=your_live_account_id_here
TRADIER_SANDBOX_API_KEY=your_tradier_sandbox_key_here
TRADIER_SANDBOX_ACCOUNT_ID=your_sandbox_account_id_here

# Optional AI providers (for other agents)
ANTHROPIC_KEY=your_anthropic_key_here
OPENAI_KEY=your_openai_key_here
DEEPSEEK_KEY=your_deepseek_key_here
```

**Get API Keys:**
- **xAI Grok**: https://console.x.ai/ (for sentiment analysis)
- **Finnhub**: https://finnhub.io/ (for market data)
- **Tradier**: https://tradier.com/ (for trade execution)

4. **Launch the application**
```bash
python app.py
```

The GUI will open automatically. Configure your stock universe in the Config tab and start the agent from the Dashboard.

## ⚙️ Configuration

Edit `config.json` or use the GUI Config tab:

```json
{
  "stock_universe": ["AAPL", "MSFT", "GOOGL", "TSLA"],
  "risk_per_trade": 0.02,
  "max_exposure": 0.5,
  "stop_loss_pct": 0.05,
  "trailing_trigger": 0.03,
  "trailing_pct": 0.02,
  "paper_trading": true,
  "check_interval_minutes": 15
}
```

### Key Parameters

- **risk_per_trade**: Percentage of portfolio to risk per trade (default: 2%)
- **max_exposure**: Maximum total portfolio exposure (default: 50%)
- **stop_loss_pct**: Stop loss percentage (default: 5%)
- **trailing_trigger**: Profit threshold to activate trailing stop (default: 3%)
- **trailing_pct**: Trailing stop distance (default: 2%)
- **paper_trading**: Toggle between paper/live trading
- **check_interval_minutes**: How often to check for earnings/sentiment updates

## 🤖 How It Works

### Sentiment Analysis Flow

1. **Earnings Detection**: Monitors Finnhub for upcoming earnings dates
2. **AI Analysis**: Sends earnings data to Grok AI for sentiment scoring (0-100)
3. **Signal Generation**: Determines BUY/SELL/HOLD based on sentiment thresholds:
   - Score ≥ 70: Strong Buy
   - Score 60-69: Buy
   - Score 40-59: Hold
   - Score 30-39: Sell
   - Score < 30: Strong Sell
4. **Risk Checks**: Validates against position limits and risk parameters
5. **Order Execution**: Places trades via Tradier API if all checks pass
6. **Position Management**: Monitors open positions and applies stop losses/trailing stops

### GUI Features

- **Automatic alphabetization** of stock tickers on save
- **Duplicate ticker detection** with popup notifications
- **Synchronized scrolling** between ticker and company name columns
- **Brand-colored stock names** fetched from Grok AI
- **Multi-threaded API calls** with rate limiting (Finnhub: 1/1.5s, Grok: 5/s)
- **Real-time log updates** via file system watchers
- **Window position persistence** across sessions
- **Split-screen buttons** for window management
- **Fullscreen mode** with multi-monitor support

## 📊 Trading Strategy

The CDEM agent implements a **sentiment-driven earnings strategy**:

### Entry Signals
- Earnings report within 7 days (configurable)
- AI sentiment score indicates positive/negative outlook
- Portfolio exposure below max_exposure limit
- No existing position in the ticker (or position size below limit)

### Exit Signals
- Stop loss triggered (default: -5%)
- Trailing stop triggered (activates after +3% gain, trails by -2%)
- Negative sentiment shift detected
- Manual exit via GUI

### Position Sizing
- Calculated based on risk_per_trade percentage
- Accounts for available buying power
- Respects max_position_percentage per ticker

## 🎨 UI Customization

All UI preferences are stored in `preferences.json` and can be adjusted via Settings buttons in each tab:

- Font sizes for logs, tables, and text areas
- Column widths for all tables
- Refresh rates and auto-scroll behavior
- Chart dimensions and colors
- Save notification duration

## 📝 Logging

Three types of logs for complete visibility:

### Terminal Logs (`logs/terminal_logs.txt`)
```
📅 2025-11-10 12:00:00
💰 AAPL earnings in 3 days
🧠 Grok sentiment: 78/100
✅ BUY signal generated
📊 Order placed: 10 shares @ $150.00
```

### Grok Logs (`logs/grok_logs.json`)
Pretty-formatted JSON with:
- Complete prompt sent to Grok
- AI reasoning and analysis
- Structured response data
- Timestamp and metadata

### App Logs (`logs/app_logs.txt`)
System-level events:
- API call results
- Configuration changes
- Stock data fetching
- UI interactions

## 🛡️ Risk Management

The agent includes multiple layers of protection:

1. **Pre-trade checks**: Validates risk limits before placing orders
2. **Position limits**: Maximum exposure per ticker and total portfolio
3. **Stop losses**: Automatic exit on adverse price movements
4. **Trailing stops**: Lock in profits as price moves favorably
5. **Paper trading mode**: Test strategies without real capital
6. **Manual override**: Stop agent instantly via GUI

## 🔄 Updates & Maintenance

The agent automatically:
- ✅ Saves window position and size
- ✅ Caches stock names and brand colors
- ✅ Sorts stock universe alphabetically
- ✅ Removes duplicate tickers
- ✅ Logs all activity for debugging
- ✅ Handles API rate limits
- ✅ Recovers from errors gracefully

## 🏗️ Project Structure

```
moon-dev-ai-agents/
├── app.py                          # Main GUI application
├── src/agents/cdem_agent.py        # Trading agent logic
├── config.json                     # Trading configuration
├── preferences.json                # UI preferences
├── logs/                           # All log files
│   ├── terminal_logs.txt
│   ├── grok_logs.json
│   └── app_logs.txt
├── src/data/cdem/                  # Agent data
│   ├── sentiment_history.csv
│   └── portfolio.csv
└── test_files/                     # Development tests (git-ignored)
```

## 🤝 Credits & Attribution

This project is built on top of **Moon Dev's AI Agents for Trading** framework:
- Original repository: https://github.com/moon-dev-ai/moon-dev-ai-agents-for-trading
- Model factory pattern and base agent architecture by Moon Dev
- Extended and specialized for earnings-based trading with GUI

### Moon Dev Resources
- YouTube: https://www.youtube.com/@MoonDevTech
- Discord: https://discord.gg/8UPuVZ53bh
- Website: https://moondev.com

## ⚠️ Disclaimers

**PLEASE READ CAREFULLY:**

1. **Not Financial Advice**: This software is for educational purposes only
2. **Substantial Risk**: Trading stocks involves substantial risk of loss
3. **No Guarantees**: Past performance does not indicate future results
4. **Your Responsibility**: You are solely responsible for your trading decisions
5. **Test First**: Always backtest and paper trade before using real capital
6. **Not a Professional Service**: I am not a licensed financial advisor

**Trading Disclaimer:**
- All trading involves risk and may result in losses
- This software is experimental and may contain bugs
- You should only trade with capital you can afford to lose
- No AI system can guarantee profitable trading
- You must develop and validate your own trading approach

**CFTC Disclaimer:** Trading commodities and securities involves substantial risk of loss. There is no guarantee that any trading strategy will result in profits.

## 📜 License

This project inherits the license from Moon Dev's original repository. See LICENSE file for details.

---

**Version**: 118 (App UI) | CDEM Agent v3.0.1

*Built by a trader, for traders. Standing on the shoulders of giants.* 🚀
