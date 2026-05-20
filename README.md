# AI-Trader — Regime-Aware Crypto Trading Bot

An adaptive crypto trading bot that classifies the current market regime (trending, ranging, choppy) and routes each pair to the strategy best suited to those conditions. Built on [Freqtrade](https://www.freqtrade.io/) with a custom regime classifier, hard-mapped strategy selector, multi-layer risk controls, and a cross-platform Python signal monitor (Windows + Linux) with optional desktop notifications.

Currently in paper-trading on OKX perpetual futures (BTC, ETH, SOL).

---

## Architecture at a glance

```
Exchange (OKX futures)
        │  OHLCV via Freqtrade DataProvider
        ▼
Regime Classifier  ──►  TRENDING_UP / TRENDING_DOWN / RANGING_LOW
   (ADX, BB-width,        / RANGING_HIGH / CHOPPY
    ATR ratio, EMA50)
        │
        ▼
Strategy Selector (hard-mapped, Design 1)
   ├─ TRENDING_UP    → momentum long  (EMA cross + RSI filter)
   ├─ TRENDING_DOWN  → momentum short (EMA cross + RSI filter)
   ├─ RANGING_LOW    → mean reversion long (RSI < 30)
   └─ CHOPPY / HIGH  → stay flat
        │
        ▼
Risk Manager
   ├─ 2% stoploss per trade
   ├─ StoplossGuard (pause on consecutive losses)
   ├─ MaxDrawdown protections (3% daily / 8% weekly)
   └─ Correlation veto (max 2 of {BTC, ETH, SOL} simultaneously)
        │
        ▼
Freqtrade Execution → Exchange  →  Trade log + SQLite
```

Full system design, build phases, and the v1→v2 evolution plan are in **[ARCHITECTURE.md](ARCHITECTURE.md)**. Current build state and what's next are in **[ROADMAP.md](ROADMAP.md)**.

---

## Tech stack

| Layer | Tool |
|---|---|
| Execution | [Freqtrade](https://www.freqtrade.io/) 2026.4 (Docker) |
| Exchange | OKX perpetual futures (paper trading) via `ccxt` |
| Indicators | TA-Lib, `qtpylib` (VWAP), pandas |
| Persistence | SQLite (`tradesv3.dryrun.sqlite`) |
| Monitoring | Python 3.10+ (stdlib only) + Freqtrade REST API + cross-platform desktop notifications (BurntToast on Windows, `notify-send` on Linux) |
| Hyperparameter search | Freqtrade hyperopt + `SharpeHyperOptLoss` |
| Validation | walk-forward backtest, lookahead-analysis, recursive-analysis |

---

## Key files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Freqtrade container definition |
| `user_data/config.example.json` | Template config — copy to `config.json` and fill in secrets |
| `user_data/strategies/regime_aware_strategy.py` | Production strategy (1h timeframe) |
| `user_data/strategies/regime_classifier.py` | ADX/BB-width/ATR-ratio rule-based regime classifier |
| `user_data/strategies/risk_manager.py` | Correlation veto layer above Freqtrade's built-in protections |
| `tools/monitor.py` | Live signal monitor — wallet, open trades, per-pair regime/proximity |
| `tools/daily_report.py` | End-of-day P&L / Sharpe / drawdown report |
| `tools/weekly_check.py` | Weekly headless Claude health check — writes markdown digest |
| `tools/start_monitor.bat` / `tools/start_monitor.sh` | Double-click / shell launchers for the monitor |

---

## Running it locally

**Prerequisites:** Docker, Python 3.10+ (the monitor scripts use only the Python standard library — no extra packages required).

### 1. Copy the config template and edit it

PowerShell (Windows):
```powershell
Copy-Item user_data\config.example.json user_data\config.json
```

Bash (Linux / macOS / Git Bash):
```sh
cp user_data/config.example.json user_data/config.json
```

Then open `user_data/config.json` and fill in the `api_server.username` and `api_server.password` fields (used by the monitor and FreqUI). Defaults are fine for dry-run mode; exchange API keys only need values if you switch `dry_run` to `false`.

### 2. Start the bot

```sh
docker compose up -d
```

FreqUI is available at `http://localhost:8080`.

### 3. Run the live monitor (optional, both OSes)

```sh
python tools/monitor.py                    # default: poll every 300s
python tools/monitor.py --poll-seconds 60  # poll every minute
```

Or use the platform-specific launchers:

- Windows: double-click `tools\start_monitor.bat`
- Linux: `./tools/start_monitor.sh`

### 4. Schedule automated checks (optional)

Two layers, both unattended:

**Daily report** — telemetry digest, no LLM:

```sh
python tools/daily_report.py
```

Writes `user_data/logs/report_YYYY-MM-DD.txt` and fires a desktop notification.

**Weekly AI check** — headless `claude -p` reads the bot state, judges anomalies, writes a markdown digest:

```sh
python tools/weekly_check.py
python tools/weekly_check.py --dry-run    # preview the prompt without calling claude
```

Writes `user_data/logs/ai_check_YYYY-MM-DD.md` and notifies with the first flag (or "No flags raised"). Requires `claude` CLI on PATH.

**Schedule both:**

Windows (Task Scheduler) — run in an elevated PowerShell, adjust paths if needed:

```powershell
schtasks /Create /SC DAILY /TN "AI-Trader Daily Report" `
  /TR "cmd /c cd /d D:\GitHub\AI-trader && python tools\daily_report.py" /ST 23:55

schtasks /Create /SC WEEKLY /D SUN /TN "AI-Trader Weekly AI Check" `
  /TR "cmd /c cd /d D:\GitHub\AI-trader && python tools\weekly_check.py" /ST 09:00
```

Linux (cron) — `crontab -e`:

```
55 23 * * *   cd /path/to/AI-trader && python3 tools/daily_report.py
 0  9 * * 0   cd /path/to/AI-trader && python3 tools/weekly_check.py
```

Notifications are best-effort: `BurntToast` on Windows, `notify-send` on Linux. If the notification system isn't available, both jobs still run and persist their files.

---

## Validation workflow

Strategy changes go through this gauntlet before any consideration of live deployment:

1. **Backtest on held-out window** — at least 8 months of data the strategy was not tuned against.
2. **`lookahead-analysis`** — Freqtrade's built-in scan for accidentally peeking at future bars.
3. **`recursive-analysis`** — verifies indicator convergence at the configured `startup_candle_count`.
4. **Hyperopt with discipline** — limited parameter count, walk-forward calibration/holdout split, hard rule that an overfit result (in-sample > holdout × 1.3) is reported as the holdout number.
5. **Paper-trade for 60+ days** before any real capital, before any live switch.

---

## Roadmap snapshot

- ✅ **Phase 1–3:** Foundation, regime-aware strategy, monitoring infrastructure
- 🟡 **Phase 4:** Paper trading (in progress, ~60-day target)
- ⏳ **Phase 5–6:** Move to Kraken Futures with rotated secrets; small-size live trading
- 🔮 **Phase 7–8:** Volatility-based dynamic sizing, HMM regime classifier, v2 hybrid orchestrator

Detail in [ROADMAP.md](ROADMAP.md).

---

## Disclaimer

This is a personal research project. It runs in dry-run mode (paper trading, no real money) and the code is shared for educational reference. Nothing here is financial advice; running this against real capital is at your own risk.
