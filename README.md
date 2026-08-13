<div align="center">

# 📈 Trading Platform v1.1

**Self-hosted multi-bot trading platform for Bitget Futures & Spot, by FloDePin**

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org) [![Version](https://img.shields.io/badge/version-1.1-orange)](#changelog)

🇬🇧 **English** | 🇩🇪 [Deutsch](README.de.md)

*An open-source, self-hosted multi-bot trading platform for Bitget Futures & Spot with a real-time web dashboard. Built in pure Python – no cloud, no subscription, no middleman.*

</div>

---

## Changelog

### v1.1 (2026-08)

A large reliability + features release, fully tested locally before release.

**Trading-logic fixes**
- Order sizing no longer formats BTC/ETH quantities down to `"0"` (which got orders rejected) — decimals are now derived dynamically from the market's min size.
- Grid Bot rebuilt on **crossing-based logic**: it buys when price crosses a level downward and sells (closes) when it crosses upward — no more re-accumulating longs when price oscillates around a single level.
- **Emergency Stop** now also closes/cancels every Multi-Grid instance on its **own sub-account** (own API keys), and only cancels the actually-traded symbols (the old ~250-symbol loop hit the rate limit and made panic fail).
- **DCA Bot state** (invested / quantity / buys / last buy) now persists to disk — survives restarts and no longer buys immediately on every start.
- **Order sanity-check** before every entry (rejects 0-size / oversized orders) and an **SL/TP guard** that re-arms stop-loss/take-profit if the exchange didn't attach them (inspired by an MT5 community bot).

**New signal tools**
- **Correlation matrix** + correlation-aware entry filter (skips a position too correlated with an already-open one).
- **ADX trend filter** and **order-book buy/sell pressure** as signal factors.
- **Market-regime (CoinGecko)** + **derivatives (Coinalyze)** dashboard tab.
- **Per-indicator on/off table** for the Signal Bot + a **live score breakdown** per coin (see exactly which factor contributes what), plus an optional long-EMA trend filter.

**Backtest realism**
- If a candle hits both SL and TP it now counts as a **loss** (removes the optimistic look-ahead bias).
- Fees are charged on **notional, not on profit** — trade history no longer looks too optimistic.
- **Configurable position size %**, and the multi-symbol backtest now respects the SL/TP from the UI.

**UI / housekeeping**
- Settings redesigned as a **two-column layout**; **full German/English coverage** across every tab.
- Replaced delisted **MATIC with POL** (Polygon rebrand).
- Thread-safety, XSS and fee-accounting fixes; the backtest help now states that the volatility circuit breaker isn't simulated.

### v1.0 (2026-07)

Initial hardening / security review:

- **Dashboard login.** The dashboard and its entire API now require a login (HTTP Basic Auth). On the first interactive start you choose your own username/password in the console; every subsequent start asks for the login again (3 attempts), or generates a password for headless starts and logs it once to `platform.log`.
- **Order safety.** Orders carry an idempotency key (`clientOid`), so a request retried after a network hiccup can no longer place the same order twice.
- **Grid Bot accounting fixed.** The Grid Bot tracks what it actually bought and only closes real positions instead of opening a new one on every single level.
- **Signal Bot win/loss streak tracking fixed.** A dead code path meant streaks and the trade history for SL/TP-closed positions were never logged.
- **Funding Bot clearly labeled as monitoring only.** It estimates potential yield but places no real orders; its estimated PnL is shown separately.
- **More resilient emergency stop**, plus **stored-XSS fixes** and input validation/limits in the API.

---

## What the platform can do

Runs up to 4 automated trading bots simultaneously, each on its own Bitget sub-account, controlled through a local, login-protected browser dashboard. Supports both demo (paper trading) and live trading.

**Signal Bot** – Technical analysis across multiple tokens. Scores 9 indicators and opens long/short positions when the threshold is reached, with ATR-based stop loss/take profit.

**Grid Bot** – Places a grid of buy/sell orders across a price range and closes what it actually bought. Profits from sideways markets. Supports multiple independent grid instances.

**Funding Bot** – Monitoring only: tracks funding-rate opportunities across multiple tokens and estimates the potential delta-neutral yield. Places no real orders.

**DCA Bot** – Dollar-cost averaging on the Bitget spot market. Buys a fixed amount at regular intervals.

---

## Features

### Bots
- Signal Bot: Wilder RSI, EMA cross (8/20), MACD, Bollinger Bands, Volume Ratio, ADX (trend strength), order-book buy pressure, Funding Rate, Fear & Greed, CoinGecko news sentiment, macro blackout
- ADX trend filter: dampens the signal when there is no clear trend (less trading in sideways chop); toggleable, fail-open
- Order-book buy pressure as a signal factor: buy/sell pressure from the live order book feeds into the score; toggleable, fail-open
- ATR-based dynamic stop loss and take profit
- Position sizing as % of balance
- Correlation check: max N simultaneous positions
- Correlation-aware entry filter: the Signal Bot skips a new position that is too strongly correlated with an already-open one (threshold configurable, on by default; fail-open — if the correlation calculation fails, the entry is not blocked)
- Win/loss streak tracking
- Order placement is idempotent (safe against duplicate orders on retry)
- Grid Bot tracks its own position and only closes what it bought (bounded exposure)
- Multi-Grid: multiple independent grid instances
- Emergency Stop retries failed position closes and reports which symbol was affected

### Dashboard
- Login-protected (HTTP Basic Auth) – guided setup on first start, changeable in Settings
- Real-time overview with Fear & Greed history (30 days)
- Per-bot PnL sparklines and status (Funding Bot estimate shown separately, excluded from the real total)
- Open positions across all sub-accounts
- Market tab: live prices for 15+ coins
- Economic calendar with Finnhub
- Trade history with win-rate summary
- Backtesting: up to 730 days, walk-forward, Sharpe ratio, fee-adjusted
- Multi-symbol backtest comparison
- Correlation Matrix as a heatmap — correlation of the daily returns of your Signal Bot coins, so you can see at a glance whether your positions are truly diversified or all moving together
- Trade Timing Analysis as a heatmap
- Market regime & derivatives tab: BTC/ETH dominance, total market cap and trending coins (CoinGecko, free, no key) plus open interest, funding rate, long/short ratio and liquidations (Coinalyze, free API key required; degrades gracefully when no key is set)
- Order-book pressure panel: live buy/sell pressure per coin from the Bitget order book (public, no key)
- Alerts via Telegram and/or Discord
- Bilingual: German / English

---

## Correlation integration (technical)
- The code computes pairwise Pearson correlations of daily returns from public closing prices (compute_correlation).
- The Signal Bot loads this matrix regularly (when enabled) and uses _correlation_conflict() to block entries that are too strongly correlated with already-open positions. The behavior is controlled via the settings `bots.signal.use_correlation_filter` and `bots.signal.max_correlation`.
- The dashboard has a Correlation view (Correlation tab) that renders the matrix as a color-coded table/heatmap, so you can visually verify whether your positions are truly diversified.
- Relevant code locations (platform.py): compute_correlation(...), _correlation_conflict(...), run_signal(...) and the dashboard JS/HTML that renders the matrix (renderCorrelation()).

---

## Installation

### Requirements
- Python 3.9+
- Windows, Linux or macOS

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

platform_config.json stores keys and is gitignored — never commit it.

---

## Systemd / headless operation
- The repo includes trading-platform.service — copy it to /etc/systemd/system and enable it.
- A headless first start writes the dashboard password once to platform.log (gitignored). Treat that file as secret.

---

## Security

### Why this platform is safe: 100% open source + local execution

This platform is **fundamentally different** from cloud-based trading services:

#### ✅ Full transparency
- **Full source code on GitHub.** Every line of code is auditable. No hidden algorithms, no black boxes, no cloud backend collecting data.
- **A single Python file (~5200 lines).** All logic lives in one readable file (`platform.py`). You can read exactly what it does.
- **MIT license.** Completely free to use, modify and redistribute. You own it.

#### ✅ Never leaves your computer
- **All processing runs locally.** Backtesting, calculations, bot logic, dashboard – everything runs on *your* machine.
- **API keys never leave your PC.** They are stored locally in `platform_config.json` (gitignored). Your keys are only ever sent directly to Bitget's official API endpoint (`api.bitget.com`).
- **No account needed.** No sign-up, no phone verification, no risk of account closure, no terms of service changing overnight.
- **No dependency on external services for core trading.** The only external calls are:
  - `api.bitget.com` – your exchange API
  - `finnhub.io` – free market data (optional, for the economic calendar)
  - `api.coingecko.com` – sentiment data (optional)
  - `api.alternative.me` – Fear & Greed index (optional)

  All optional integrations can be disabled. **Core trading works offline, except for the exchange connection.**

#### ✅ No surveillance, no fees, no middleman
- You trade directly with Bitget – no middleware, no commission markup, no data collection.
- No advertising, no upselling, no premium tiers.
- Run it on a local machine, a home server or a cheap VPS – your choice. No vendor lock-in.

### Critical rules
- **Never use your main Bitget account.** Use sub-accounts with limited balance.
- **API keys: Read + Trade only.** Never enable Withdraw.
- **Do not expose port 5000 publicly** without restricting access.
- **`platform_config.json` contains API keys.** It is gitignored – never commit it.
- **`platform.log` contains the auto-generated dashboard password once, on first start.** It is gitignored too – treat it as carefully as the config file.

### Dashboard login
The dashboard is protected with HTTP Basic Auth.

- **First start (interactive terminal):** you'll be asked to set your own username and password right in the console. Leave the password blank to auto-generate one instead.
- **Every subsequent start (interactive terminal):** `python platform.py` requires a login in the console (3 attempts) *before* the dashboard boots up – as an extra gate on top of the browser prompt.
- **Background/headless start (systemd, no attached terminal):** nothing is prompted – a random password is generated on first start and logged once to `platform.log`.

Username/password can be changed anytime under **Settings → Dashboard access** in the web UI.

### Restrict dashboard access
```bash
# Allow only your IP
ufw allow from YOUR.IP.HERE to any port 5000
ufw deny 5000
```

Or use [Tailscale](https://tailscale.com) for private VPN access with zero configuration.

### What this platform does NOT do
- Never transmits keys to external services
- Never makes trades outside the configured bot logic
- All API calls go exclusively to `api.bitget.com`
- Never phones home for licensing, telemetry or analytics
- Requires no internet connection except for exchange communication

---

## Disclaimer

**For educational and experimental purposes only.**

Cryptocurrency trading carries significant financial risk. You can lose all of your allocated capital. The authors take no responsibility for financial losses. Always start in demo mode.

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

MIT – free to use, modify and redistribute.

Copyright (c) 2026 Trading Platform Contributors

---

## Important setup: One-Way Mode for the Grid Bot

Before running the Grid Bot, you **must** switch your Bitget sub-account from Hedge Mode to **One-Way Mode**.

**Why:** Bitget Futures defaults to Hedge Mode (simultaneous long and short positions allowed). In Hedge Mode, the Grid Bot's sell orders open new short positions instead of closing long positions.

**How to switch:**
1. Open the Bitget app or website
2. Go to Futures trading on the Grid Bot sub-account
3. Top right → Settings → Position Mode → **One-Way Mode**

This is a one-time setup per sub-account. The Signal Bot is not affected (it manages positions explicitly via `tradeSide`).

---

## Exchange support

Currently the platform is built exclusively for **Bitget** (Futures + Spot). The `BitgetClient` class handles authentication, order placement and market data directly via Bitget's REST API.

### Adding more exchanges (roadmap)

The platform is designed so the `BitgetClient` class can be replaced with a universal exchange wrapper via [CCXT](https://github.com/ccxt/ccxt) – a Python library that supports 100+ exchanges.

Exchanges planned for future support:

| Exchange | Futures | Spot DCA | Demo / Testnet |
|---|---|---|---|
| **Bitget** | Yes (current) | Yes | Yes (`paptrading` header) |
| **Bybit** | Yes | Yes | Yes (Testnet URL) |
| **OKX** | Yes | Yes | Yes (Simulated Trading) |
| **Binance** | Yes | Yes | Yes (Testnet URL) |
| **Gate.io** | Yes | Yes | No |

### What would change with multi-exchange support

- A new `ExchangeClient` base class replacing `BitgetClient`
- An exchange selector dropdown in Settings
- Per-exchange demo mode handling (each exchange implements it differently)
- Everything else – all bots, dashboard, backtest, alerts – stays identical

### Contributing exchange support

If you want to add support for a specific exchange, these are the core functions to implement:

```python
client.balance()          # Futures account balance
client.spot_balance(coin) # Spot balance
client.price(symbol)      # Current market price
client.position(symbol)   # Open position for a symbol
client.funding_rate(symbol) # Current funding rate
client.klines(symbol, limit) # OHLCV candle data
client.place_order(...)   # Place a market order
client.set_leverage(...)  # Set leverage for a symbol
```

Once these are implemented for a new exchange, all four bots work without any further changes.
