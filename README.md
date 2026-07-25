<div align="center">

# 📈 Trading Platform v1

**Self-hosted multi-bot trading platform for Bitget Futures & Spot, by FloDePin**

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org) [![Demo Mode](https://img.shields.io/badge/default-demo%20mode-orange)](#installation)

🇬🇧 **English** | 🇩🇪 [Deutsch](README.de.md)

*An open-source, self-hosted multi-bot trading platform for Bitget Futures & Spot, with a real-time web dashboard. Built in pure Python – no cloud, no subscription, no middleman.*

</div>

---

## What's New

A full security and correctness review (2026-07) fixed a number of issues and hardened the platform:

- **Dashboard login.** The dashboard and its whole API now require a login (HTTP Basic Auth). On first interactive start you choose your own username/password in the console; every start after that asks for the password before the dashboard boots.
- **Order safety.** Orders now carry an idempotency key (`clientOid`), so a retried request after a network hiccup can no longer place the same order twice. Starting a bot (or grid instance) twice is now safe.
- **Grid Bot accounting fixed.** The Grid Bot now tracks what it actually bought and only closes real positions instead of opening a new one on every single level trigger – exposure is bounded by design.
- **Signal Bot streak tracking fixed.** A dead code path meant win/loss streaks and trade-history logging for SL/TP-closed positions silently never ran; this is now fixed.
- **Funding Bot is clearly labeled as monitoring-only.** It tracks funding-rate opportunities and estimates potential yield, but places no real orders. Its estimated PnL is now excluded from the reported total.
- **More resilient panic button.** Emergency Stop now retries a failed position close instead of giving up after one attempt, and alerts you by name if a position still couldn't be closed.
- **Stored-XSS fixes** in alert names, bot logs, and the economic calendar; input validation/bounds on the API (backtest period, leverage, grid size) so malformed requests return a clean error instead of crashing.

---

## What it does

Runs up to 4 automated trading bots simultaneously, each on its own Bitget sub-account, controlled through a local browser dashboard secured with a login. Supports both demo (paper trading) and live trading.

**Signal Bot** – Technical analysis across multiple tokens. Scores 9 indicators and enters long/short positions when the threshold is reached, with ATR-based stop loss/take profit.

**Grid Bot** – Places a grid of buy/sell orders across a price range and closes what it actually bought. Profits from sideways markets. Supports multiple independent grid instances at once.

**Funding Bot** – Monitoring only: tracks funding rate opportunities across tokens and estimates potential delta-neutral yield. Does not place real orders.

**DCA Bot** – Dollar-cost averaging on the Bitget spot market. Buys a fixed amount at regular intervals.

---

## Features

### Bots
- Signal Bot: Wilder RSI, EMA cross (8/20), MACD, Bollinger Bands, Volume Ratio, Funding Rate, Fear & Greed, CoinGecko News Sentiment, Macro Blackout
- ATR-based dynamic Stop Loss and Take Profit
- Position sizing as % of balance
- Correlation check: max N simultaneous positions
- Win/Loss streak tracking
- Order placement is idempotent (safe against duplicate orders on retry)
- Grid Bot tracks its own position and only closes what it bought (bounded exposure)
- Multi-Grid: multiple independent grid instances
- Emergency Stop retries failed position closes and reports which symbol failed

### Dashboard
- Login-protected (HTTP Basic Auth) – guided setup on first start, changeable in Settings
- Real-time overview with Fear & Greed history chart (30 days)
- Per-bot PnL sparklines and status (Funding Bot's estimate shown separately, excluded from the real total)
- Open positions across all sub-accounts
- Market tab: live prices for 15+ coins
- Economic Calendar with Finnhub
- Trade History with win rate summary
- Backtesting: up to 730 days, walk-forward, Sharpe ratio, fee-adjusted
- Multi-symbol backtest comparison
- Correlation Matrix heatmap — daily-return correlation across your Signal Bot coins, so you can see at a glance whether your positions are actually diversified or all moving together (uses public market data, works without API keys)
- Trade Timing Analysis heatmap
- Alerts via Telegram and/or Discord
- Bilingual: Deutsch / English

---

## Installation

### Requirements
- Python 3.9+
- Windows, Linux, or macOS

### Windows
```bash
pip install requests
python platform.py
```
Open `http://localhost:5000`

### Linux / VPS
```bash
bash setup.sh
sudo systemctl start trading-platform
```
Dashboard at `http://your-server-ip:5000`

---

## Configuration

1. Go to **Settings** in the dashboard
2. Create sub-accounts on Bitget (one per bot recommended)
3. Generate API keys: **Read + Trade** only – never Withdraw
4. Enter keys, click **Test Connection**, then **Save**
5. Start in **Demo mode** (default)

### Integrations

| Service | Purpose | Free | Key needed |
|---|---|---|---|
| Finnhub | Economic Calendar | Yes | Yes |
| Telegram | Alerts + Daily Summary | Yes | Bot Token |
| Discord | Alerts + Daily Summary | Yes | Webhook URL |
| CoinGecko | News Sentiment | Yes | No |
| Alternative.me | Fear & Greed | Yes | No |

---

## Security

### Why this platform is safe: 100% Open Source + Local Execution

This platform is **fundamentally different** from cloud-based trading services:

#### ✅ Complete Transparency
- **Full source code on GitHub.** Every line of code is auditable. There are no hidden algorithms, no black boxes, no cloud backend collecting data.
- **Single Python file (~5200 lines).** All logic is in one readable file (`platform.py`). You can read and understand exactly what it does.
- **MIT License.** Completely free to use, modify, and distribute. You own it.

#### ✅ Never Leaves Your Computer
- **All processing is local.** Backtesting, calculations, bot logic, dashboard – all run on *your* machine.
- **API keys never leave your PC.** They are stored locally in `platform_config.json` (gitignored). Your keys are never sent to any server except directly to Bitget's official API endpoint (`api.bitget.com`).
- **No account needed.** No sign-up, no phone verification, no account closure risk, no terms of service changing overnight.
- **No dependency on external services for core trading.** The only external calls are:
  - `api.bitget.com` – your exchange API
  - `finnhub.io` – free market data (optional, for Economic Calendar)
  - `api.coingecko.com` – sentiment data (optional)
  - `api.alternative.me` – Fear & Greed index (optional)
  
  All optional integrations can be disabled. **Core trading works offline except for exchange connectivity.**

#### ✅ No Surveillance, No Fees, No Intermediary
- You trade directly with Bitget – no middleware, no commission markup, no data collection.
- No advertisements, no upselling, no premium tiers.
- Run it on a local machine, a home server, a cheap VPS – your choice. No vendor lock-in.

### Critical rules
- **Never use your main Bitget account.** Use sub-accounts with limited balance.
- **API keys: Read + Trade only.** Never enable Withdraw.
- **Do not expose port 5000 publicly** without restricting access.
- **`platform_config.json` contains API keys.** It is gitignored – never commit it.
- **`platform.log` contains the auto-generated dashboard password once, at first start.** It is gitignored too – treat it with the same care as the config file.

### Dashboard login
The dashboard is protected with HTTP Basic Auth.

- **First start (interactive terminal):** you'll be asked to choose your own username and
  password right in the console. Leave the password blank to auto-generate one instead.
- **Every start after that (interactive terminal):** `python platform.py` asks you to log in
  in the console (3 attempts) *before* the dashboard boots up, as a second gate in addition
  to the browser's Basic Auth prompt.
- **Headless/background start (systemd, no attached terminal):** no prompt is shown – a
  random password is generated automatically on first run and logged once to
  `platform.log`, exactly as before. This keeps unattended restarts (e.g. via systemd)
  working without a TTY.

Change username/password anytime under **Settings → Dashboard-Zugang** in the web UI.

### Restrict dashboard access
```bash
# Allow only your IP
ufw allow from YOUR.IP.HERE to any port 5000
ufw deny 5000
```

Or use [Tailscale](https://tailscale.com) for zero-config private VPN access.

### What this platform does NOT do
- Never transmits keys to external services
- Never makes trades outside configured bot logic
- All API calls go to `api.bitget.com` only
- Never phones home for licensing, telemetry, or analytics
- Never requires internet connectivity except for exchange communication

---

## Disclaimer

**For educational and experimental purposes only.**

Cryptocurrency trading involves significant financial risk. You can lose all allocated capital. The authors take no responsibility for financial losses. Always start with Demo mode.

---

## Architecture

```
platform.py             Single-file application (~5200 lines)
platform_config.json    API keys and settings (gitignored)
platform.db             SQLite: trade history + PnL snapshots
platform.log            Rotating log (5 MB)
```

---

## License

MIT – free to use, modify, and distribute.

Copyright (c) 2026 Trading Platform Contributors

---

## Critical Setup: One-Way Mode for Grid Bot

Before running the Grid Bot, you **must** switch your Bitget sub-account from Hedge Mode to **One-Way Mode**.

**Why:** Bitget Futures defaults to Hedge Mode (simultaneous long and short allowed). In Hedge Mode, the Grid Bot's sell orders open new short positions instead of closing existing longs. This causes unintended short exposure. One-Way Mode ensures all sell orders close existing long positions.

**How to switch:**
1. Open Bitget App or website
2. Go to Futures trading on the Grid Bot sub-account
3. Top right corner → Settings → Position Mode → **One-Way Mode**

This is a one-time setup per sub-account. Signal Bot is not affected (it manages positions explicitly via `tradeSide`).

---

## Exchange Support

Currently, the platform is built exclusively for **Bitget** (Futures + Spot). The `BitgetClient` class handles authentication, order placement, and market data directly via Bitget's REST API.

### Adding More Exchanges (Roadmap)

The platform is designed so that the `BitgetClient` class can be replaced with a universal exchange wrapper using [CCXT](https://github.com/ccxt/ccxt) – a Python library that supports 100+ exchanges with a unified API.

Planned exchanges for future support:

| Exchange | Futures | Spot DCA | Demo / Testnet |
|---|---|---|---|
| **Bitget** | Yes (current) | Yes | Yes (`paptrading` header) |
| **Bybit** | Yes | Yes | Yes (Testnet URL) |
| **OKX** | Yes | Yes | Yes (Simulated Trading) |
| **Binance** | Yes | Yes | Yes (Testnet URL) |
| **Gate.io** | Yes | Yes | No |

### What would change with multi-exchange support

- A new `ExchangeClient` base class replacing `BitgetClient`
- Exchange selector dropdown in Settings
- Per-exchange demo mode handling (each exchange implements it differently)
- Everything else – all bots, dashboard, backtest, alerts – stays identical

### Contributing exchange support

If you want to add support for a specific exchange, the key functions to implement are:

```python
client.balance()          # Futures account balance
client.spot_balance(coin) # Spot account balance
client.price(symbol)      # Current market price
client.position(symbol)   # Open position for a symbol
client.funding_rate(symbol) # Current funding rate
client.klines(symbol, limit) # OHLCV candle data
client.place_order(...)   # Place a market order
client.set_leverage(...)  # Set leverage for a symbol
```

Once these are implemented for a new exchange, all four bots will work without any further changes.
