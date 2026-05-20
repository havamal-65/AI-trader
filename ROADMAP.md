# AI-Trader Roadmap

> For system design and architecture decisions, see [ARCHITECTURE.md](ARCHITECTURE.md).
> This document tracks what's been built, what's next, and what's on the horizon.

---

## Current State (May 2026)

Paper trading on OKX — BTC/USDT, ETH/USDT, SOL/USDT futures.
$100 USDT dry-run wallet. Bot runs unattended in Docker.

---

## Phase 1 — Foundation `DONE`

- [x] Freqtrade 2026.4 running in Docker (`docker-compose.yml`)
- [x] OKX exchange connected (dry run — no API keys needed)
- [x] 3 futures pairs: BTC/USDT:USDT, ETH/USDT:USDT, SOL/USDT:USDT
- [x] SQLite trade persistence (`tradesv3.dryrun.sqlite`)
- [x] FreqUI accessible at `localhost:8080`
- [x] Risk layer: 2% stoploss, StoplossGuard, MaxDrawdown (3% daily / 8% weekly)
- [x] Correlation veto: max 2 of 3 correlated pairs open simultaneously

---

## Phase 2 — Regime-Aware Strategy `DONE`

- [x] `RegimeClassifier` — vectorized ADX + BB-width + ATR ratio rules
  - TRENDING_UP / TRENDING_DOWN / RANGING_LOW / RANGING_HIGH / CHOPPY
- [x] `RegimeAwareStrategy` — hard-mapped selector (Design 1)
  - TRENDING_UP → momentum long (EMA cross + RSI filter)
  - TRENDING_DOWN → momentum short (EMA cross + RSI filter)
  - RANGING_LOW → mean reversion long (RSI < 30)
  - CHOPPY / RANGING_HIGH → stay flat
- [x] Shorts enabled (`can_short = True`, futures mode, leverage locked at 1x)
- [x] BB reversal strategy removed (1.5% win rate in backtest)
- [x] 2% stoploss confirmed optimal (wider triggers MaxDrawdown, net worse)
- [x] Backtested: +1.13% profit in -36% bear market (2025), +0.44% over 18 months

---

## Phase 3 — Monitoring & Observability `DONE`

- [x] `tools/monitor.py` — live signal monitor (cross-platform, stdlib only)
  - Header: wallet balance, all-time P&L, win rate
  - Open trade table: direction, entry, unrealized P&L, duration
  - Per-pair temperature: regime, RSI, ADX, EMA spread, proximity score
  - Approaching-signal hints (RSI near threshold, EMA lines converging)
  - Desktop notification on entry signal fire and trade open (BurntToast on Windows, notify-send on Linux, silent fallback)
- [x] `tools/daily_report.py` — end-of-day performance report (cross-platform)
  - Writes `user_data/logs/report_YYYY-MM-DD.txt`
  - Shows wallet, all-time P&L, Sharpe, max drawdown, today's trades
  - Fires a desktop notification summary
- [x] Scheduling docs: Task Scheduler (Windows) and cron (Linux) snippets in README
- [x] `tools/start_monitor.bat` / `tools/start_monitor.sh` — platform launchers

---

## Phase 4 — Paper Trading & Optimization `IN PROGRESS`

**Target:** 60+ days of live paper data before any live money.
**Gate to Phase 5:** positive expectancy net of estimated fees, no regime-specific blowups.

- [ ] Collect 60 days of paper trading data (started ~May 2026, target ~July 2026)
- [ ] Open Kraken account with Intermediate KYC + futures trading enabled
- [ ] Monitor per-regime win rate from daily reports
  - If RANGING_LOW mean-reversion underperforms → tune RSI threshold or disable
  - If regime is CHOPPY >70% of the time → review ADX thresholds
- [ ] Run Hyperopt on key thresholds once 60 days of data exists
  - RSI oversold threshold (currently 30)
  - RSI overbought filter on entries (currently 65 / 35)
  - ADX trending threshold (currently 25)
- [ ] Walk-forward validation: backtest on data the bot never saw
- [ ] Review stoploss on Krakenfutures (fee structure differs from OKX)

---

## Phase 5 — Live Trading Prep `PLANNED`

**Gate to Phase 6:** Kraken account approved, secrets rotated, paper vs live fee model reconciled.

- [ ] Switch exchange to `krakenfutures` in `config.json`
  - Update pair format to Kraken futures notation
  - Update `risk_manager.py` correlated pairs set
- [ ] Rotate all secrets before going live
  - `jwt_secret_key` in `config.json`
  - `ws_token` in `config.json`
  - `api_server.password` in `config.json`
- [ ] Confirm Kraken API keys work in dry-run first
- [ ] Run 1–2 weeks paper on Krakenfutures to verify data feed and fills
- [ ] Set `dry_run: false` only after above passes

---

## Phase 6 — Live Trading `PLANNED`

**Gate to Phase 7:** 30+ days live with small size, performance matches paper within tolerance.

- [ ] Go live with $100 USDT on Krakenfutures
- [ ] Monitor daily: drawdown, regime distribution, per-tag win rate
- [ ] Add tax export integration (CoinTracker or Koinly CSV from trade DB)
- [ ] Add more pairs if liquidity and correlation analysis supports it
  - Candidates: AVAX, LINK, MATIC — verify they aren't fully correlated with BTC

---

## Phase 7 — Strategy Enhancement `FUTURE`

These require real live performance data to evaluate — do not build speculatively.

- [ ] Dynamic stake sizing (ATR-based or Kelly fraction)
  - Larger position in low-volatility trending regimes, smaller in ranging
  - Currently hardcoded at $25 per trade
- [ ] HMM-based regime classifier to replace rule-based
  - More adaptive — learns regime transitions from data
  - Only worth building if rule-based shows systematic misclassification
- [ ] Per-strategy-per-regime analytics layer
  - Track win rate / expectancy per entry tag per regime
  - Feed into Design 2 performance-weighted selector (see ARCHITECTURE.md)
- [ ] Donchian Channel Breakout strategy for strong TRENDING regimes
- [ ] Volatility Expansion strategy for squeeze-to-breakout detection

---

## Phase 8 — v2 Hybrid Orchestrator `FUTURE`

Only warranted if multiple strategies show meaningfully different regime fitness in live data.
Full design in [ARCHITECTURE.md — Appendix A](ARCHITECTURE.md).

- [ ] Separate Freqtrade instance per strategy (momentum, mean-rev, etc.)
- [ ] Orchestrator service: classifies regime, allocates capital across instances via REST
- [ ] Performance-weighted ensemble (Design 2): auto-disable underperforming strategies
- [ ] Shared metrics store for per-strategy-per-regime P&L

---

## Decisions Still Open

| Decision | Status | Notes |
|---|---|---|
| Exchange for live trading | Kraken planned | User opening account |
| Tax tracking tool | Not chosen | CoinTracker / Koinly both work |
| Hosting | Local PC | Fine for now; VPS if uptime becomes critical |
| Capital beyond $100 | Pending Phase 6 results | Wait for positive live track record |
| Pairs beyond BTC/ETH/SOL | Pending correlation analysis | Phase 6+ |

---

## Key Files

**Tracked in repo:**

| File | Purpose |
|---|---|
| `docker-compose.yml` | Freqtrade container |
| `user_data/config.example.json` | Config template (copy to `config.json` and fill in secrets) |
| `user_data/strategies/regime_aware_strategy.py` | Main strategy |
| `user_data/strategies/regime_classifier.py` | Regime detection logic |
| `user_data/strategies/risk_manager.py` | Correlation veto |
| `tools/freqtrade_client.py` | Shared REST client (loads config, JWT auth) |
| `tools/notify.py` | Cross-platform desktop notification helper |
| `tools/monitor.py` | Live signal monitor |
| `tools/daily_report.py` | End-of-day performance report |
| `tools/weekly_check.py` | Weekly headless Claude health check |
| `tools/start_monitor.bat`, `tools/start_monitor.sh` | Monitor launchers (Windows / Linux) |

**Runtime artifacts (gitignored):**

| Path | Purpose |
|---|---|
| `user_data/config.json` | Live config with secrets — never committed |
| `user_data/logs/report_*.txt` | Daily report archive |
| `user_data/tradesv3.dryrun.sqlite` | Trade history DB |
