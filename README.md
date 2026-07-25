<div align="center">

# 📈 Trading Platform v1

**Self-hosted multi-bot trading platform for Bitget Futures & Spot, by FloDePin**

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org)

🇬🇧 **English** | 🇩🇪 [Deutsch](README.de.md)

*Open-source, self-hosted multi-bot trading platform for Bitget (Futures & Spot) with a local real-time web dashboard. Built in pure Python — runs locally, no cloud required.*

</div>

---

## Summary — what this does
Runs up to 4 automated trading bots (Signal, Grid, Funding (monitor-only), DCA), each on its own Bitget sub-account. Controlled via a local, login-protected browser dashboard. Supports demo (paper trading) and real trading.

## Stack
- Language(s): Python 3.9+
- Runtime: single-file Python application (runs as a CLI + local web dashboard)
- Notable libraries: requests (minimal), plus standard Python stdlib

## Quick highlights
- Signal Bot: multi-indicator entries, ATR stop/take
- Grid Bot: bounded exposure, tracks actual fills
- Funding Bot: monitoring-only (no real orders)
- DCA Bot: scheduled spot buys
- Dashboard: HTTP Basic Auth, real-time status, backtesting, alerts (Telegram/Discord)
- Correlation filter + heatmap: the correlation matrix is computed from public daily returns, actively used by the Signal bot to avoid opening new positions that are too highly correlated with already open positions. The Dashboard includes a Correlation tab with a heatmap-like visualization.

---

## Quick start

Requirements
- Python 3.9+
- Linux, macOS, or Windows
- Bitget sub-accounts + API keys (Read+Trade only — never Withdraw)

From a fresh clone:

Linux / VPS (recommended)
```bash
# create a venv and install
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements_platform.txt

# interactive first start to create dashboard user/password
python platform.py

# to run as systemd service (the repository includes trading-platform.service)
sudo cp trading-platform.service /etc/systemd/system/trading-platform.service
sudo systemctl daemon-reload
sudo systemctl enable --now trading-platform
sudo journalctl -u trading-platform -f
```

Windows
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements_platform.txt
python platform.py
# Open http://localhost:5000 in your browser
```

Notes:
- First interactive run will prompt to create the dashboard username/password (or auto-generate a password for headless runs — written once to platform.log).
- Use demo mode for initial testing.

---

## Configuration
1. Go to Settings in the dashboard
2. Create Bitget sub-accounts (one per bot recommended)
3. Generate API keys with Read + Trade (never Withdraw)
4. Enter keys in Settings → Test Connection → Save
5. Start in Demo mode (default) until you are comfortable

platform_config.json stores keys and is gitignored — never commit it.

---

## Systemd / Headless operation
- The repo provides trading-platform.service — copy to /etc/systemd/system and enable it.
- Headless first start writes the dashboard password once to platform.log (gitignored). Treat that file as secret.

---

## Correlation integration (technical)
- The code computes a pairwise Pearson correlation matrix of daily returns (compute_correlation).
- The Signal bot calls compute_correlation() each cycle (if enabled) and uses _correlation_conflict() in its entry logic to block openings that would be too highly correlated with already open positions. This behavior is controlled by the settings `bots.signal.use_correlation_filter` and `bots.signal.max_correlation` in the config.
- In the dashboard, the Correlation tab renders a colored matrix (heatmap-like) so you can visually inspect correlations and configure the max correlation threshold in Settings.

Relevant code locations (platform.py):
- compute_correlation(...) — builds matrix from public daily closes
- _correlation_conflict(...) — decides whether a candidate entry conflicts with open positions
- run_signal(...) — loads correlation data and checks for conflicts before opening
- Dashboard HTML/JS — correlation tab and renderCorrelation() that visualizes the matrix

---

## Security
- Do not expose port 5000 publicly without access controls.
- Recommended: restrict to your IP (ufw) or use a VPN like Tailscale.
- Never run with API keys from your main Bitget account — use limited sub-accounts.

---

## Development & Contributing (suggested)
- Add CONTRIBUTING.md + CHANGELOG.md
- Pin dependencies in requirements_platform.txt (e.g., requests==2.31.0)
- Add CI for linting and basic tests

---

## Architecture
```
platform.py             Single-file application (~5200 lines)
platform_config.json    API keys and settings (gitignored)
platform.db             SQLite: trade history + PnL snapshots
platform.log            Rotating log (5 MB; may contain initial password once)
```

---

## License
MIT — Copyright (c) 2026 Trading Platform Contributors

---
