# Adaptive Crypto Trading Bot — Architecture Plan

> **Goal:** A bot that detects what the market is doing right now (trending, ranging, volatile, dead) and automatically picks the strategy best suited to those conditions, sizes positions appropriately, executes trades, and monitors itself.

-----

## ⚠️ Reality Check (read first)

Three things that determine whether this succeeds or fails — and they're not the code:

1. **Strategy selection is the hardest part of algo trading, not the easiest.** Most "regime-aware" bots overfit to backtest data and break in live markets. The plan below assumes this is true and builds defensively around it.
2. **Backtests lie.** A strategy that prints money on historical data routinely loses in live trading because of slippage, fees, latency, and regime shifts the backtest never saw. Forward testing (paper trading) is non-negotiable.
3. **Risk management matters more than strategy.** A mediocre strategy with great risk controls survives. A great strategy with bad risk controls eventually blows up. Build the risk layer *first*.

**Concrete implication:** budget at least 2–3 months of paper trading per strategy before touching real money, and start with capital you'd be fine losing entirely.

-----

## System Overview

```mermaid
flowchart TB
    A[Exchange APIs<br/>Binance/Coinbase/Kraken] -->|OHLCV + orderbook| B[Data Layer]
    B --> C[Feature Engineering<br/>Indicators, vol, volume profile]
    C --> D[Regime Detector<br/>Classifies market state]
    C --> E[Strategy Library<br/>5-10 strategies]
    D -->|current regime| F[Strategy Selector]
    E -->|signals from each| F
    F -->|chosen signal| G[Risk Manager<br/>Sizing, stops, limits]
    G -->|approved order| H[Execution Engine]
    H -->|orders| A
    H --> I[Portfolio State]
    I --> G
    H --> J[Performance Tracker]
    J --> K[Dashboard + Alerts]
    J -.feedback.-> F
```

-----

## Layer 1 — Data Ingestion

The boring layer that everything else depends on. Get this wrong and nothing downstream works.

| Need                     | Detail                                                           |
| ------------------------ | ---------------------------------------------------------------- |
| Real-time prices         | WebSocket feeds for sub-second latency on the pairs you trade    |
| Historical OHLCV         | Multiple timeframes (1m, 5m, 1h, 4h, 1d) for ~2+ years           |
| Order book depth         | Top 20 levels, used for slippage estimation and liquidity checks |
| Funding rates            | For perpetual futures if you go that route                       |
| On-chain data (optional) | Whale flows, exchange inflows — useful for regime context        |

**Decision:** spot-only or perpetuals? Perpetuals add leverage (good and bad) and funding rate complexity. Recommend **spot-only for v1**.

-----

## Layer 2 — Market Regime Detection ("the intelligent part")

This is the brain. It classifies what the market is doing so the bot knows which strategy to run. There are three viable approaches:

### Approach A: Rule-based (recommended for v1)

Combine 2–3 well-understood indicators into a regime label.

```mermaid
flowchart LR
    A[ADX<br/>trend strength] --> D{Classify}
    B[ATR / Realized Vol<br/>volatility level] --> D
    C[Bollinger Bandwidth<br/>compression vs expansion] --> D
    D --> E[Trending Up]
    D --> F[Trending Down]
    D --> G[Ranging Low Vol]
    D --> H[Ranging High Vol]
    D --> I[Choppy / Avoid]
```

**Why start here:** explainable, debuggable, hard to overfit. You can read a chart and verify the classifier was right.

### Approach B: Hidden Markov Model (HMM)

Statistical model that infers hidden "regimes" from price/volume. Good middle ground — more adaptive than rules, more interpretable than ML.

### Approach C: ML classifier (later, maybe never)

Train a model (XGBoost, LSTM) on labeled historical regimes. Powerful but very easy to overfit. **Skip this until v3 at earliest.**

-----

## Layer 3 — Strategy Library

Each strategy has a *natural habitat* — a regime where it works. The bot's job is to run the right one at the right time.

| Strategy                      | Best Regime                 | How it works                             | Complexity |
| ----------------------------- | --------------------------- | ---------------------------------------- | ---------- |
| **Momentum / Breakout**       | Trending (up or down)       | Buy new highs, sell new lows             | Low        |
| **Mean Reversion (RSI)**      | Ranging, low vol            | Buy oversold, sell overbought            | Low        |
| **Bollinger Band Reversal**   | Ranging, normal vol         | Fade band touches                        | Low        |
| **Donchian Channel Breakout** | Strong trends               | Classic Turtle-style breakouts           | Low        |
| **Grid Trading**              | Sideways, predictable range | Place buy/sell ladder around price       | Medium     |
| **Volatility Expansion**      | Coming out of compression   | Trade direction of breakout from squeeze | Medium     |
| **Funding Rate Arbitrage**    | Any (if using perps)        | Exploit funding payments                 | High       |
| **Stay Flat / Cash**          | Choppy, unclear regime      | Do nothing (this is a *real* strategy)   | Trivial    |

**Critical:** the "stay flat" option must be respected. Most blowups happen because bots feel obligated to trade.

-----

## Layer 4 — Strategy Selector

How the bot picks. Two valid designs:

### Design 1: Hard mapping (recommended start)

Regime → strategy is a lookup table.

```
TRENDING_UP  → Momentum
TRENDING_DOWN → Stay flat (or short via perp)
RANGING_LOW  → Mean Reversion
RANGING_HIGH → Bollinger Reversal (smaller size)
CHOPPY       → Stay flat
```

### Design 2: Performance-weighted ensemble

Track each strategy's recent live performance per regime. Allocate capital proportionally to recent win-rate × expectancy. Auto-disable strategies that decay.

```mermaid
flowchart LR
    A[Current Regime: TRENDING_UP] --> B[Look up eligible strategies]
    B --> C[Strategy A: 62% win rate last 30d]
    B --> D[Strategy B: 41% win rate last 30d]
    B --> E[Strategy C: 58% win rate last 30d]
    C --> F[Weight: 50%]
    D --> G[Weight: 0%<br/>auto-disabled]
    E --> H[Weight: 50%]
```

This is the path to "intelligent." Build Design 1 first, evolve to Design 2.

-----

## Layer 5 — Risk Management (build this *first*)

Non-negotiable rules that override everything else.

| Control                  | Example value                       | Purpose                               |
| ------------------------ | ----------------------------------- | ------------------------------------- |
| Max position size        | 5% of portfolio per trade           | Survive any single bad trade          |
| Max concurrent positions | 3–5                                 | Avoid correlated blowups              |
| Per-trade stop loss      | 1–2% of portfolio                   | Cap loss before it spreads            |
| Daily loss limit         | 3%                                  | Stop trading for the day, cool off    |
| Weekly drawdown limit    | 8%                                  | Pause bot, manual review required     |
| Max leverage             | 1x (spot only for v1)               | Don't get liquidated                  |
| Kill switch              | Manual + automatic                  | Flatten everything if things go weird |
| Correlation check        | Don't hold 3 longs in BTC, ETH, SOL | They move together                    |

**Position sizing:** use volatility-based sizing (Kelly-fraction or ATR-based), not fixed dollar amounts. Smaller positions in high-vol regimes.

-----

## Layer 6 — Execution

```mermaid
sequenceDiagram
    Strategy->>Risk Manager: Proposed order: BUY 0.1 BTC @ market
    Risk Manager->>Risk Manager: Check limits, sizing, correlation
    Risk Manager->>Execution: Approved (or rejected)
    Execution->>Execution: Check orderbook liquidity
    Execution->>Exchange: Submit limit order (not market)
    Exchange->>Execution: Fill confirmation
    Execution->>Portfolio: Update positions
    Execution->>Tracker: Log trade
```

**Key choices:**

- **Limit orders > market orders** wherever possible (saves fees + slippage)
- **Smart order routing** if using multiple exchanges
- **Idempotency** — never double-submit on retry
- **Partial fill handling** — what if only half your order fills?

-----

## Layer 7 — Monitoring & Feedback

| Component           | Purpose                                                                         |
| ------------------- | ------------------------------------------------------------------------------- |
| Live dashboard      | P&L, open positions, current regime, active strategy                            |
| Trade log           | Every decision and why (regime classification + signal)                         |
| Performance metrics | Sharpe, Sortino, max drawdown, win rate, expectancy — *per strategy per regime* |
| Alerts              | Telegram/Discord/SMS for fills, stop-outs, kill switch trips                    |
| Anomaly detection   | Flag when live performance diverges from backtest expectations                  |

The trade log is what lets you debug "why did the bot do that?" six months from now. Make it verbose.

-----

## Tech Stack Recommendation

Given your prior Freqtrade exploration, two real paths:

### Path A: Build on Freqtrade (faster start)

- ✅ Battle-tested execution, exchange connectors, backtesting framework
- ✅ Strategy plugins fit your "library of strategies" model naturally
- ❌ Regime detection + strategy selector layer would be custom on top
- **Verdict:** good v1 path. You inherit a working execution engine.

### Path B: Custom Python from scratch (more control, more work)

- Stack: `ccxt` (exchange API) + `pandas`/`polars` + `vectorbt` (backtesting) + `FastAPI` dashboard + `PostgreSQL` for trade log
- ✅ Full control of architecture
- ❌ You'll spend 2 months on plumbing before placing one trade
- **Verdict:** only if Freqtrade hits a wall.

**Recommendation: Freqtrade base + custom regime detector and selector layered on top.** That matches the "build only what doesn't exist" principle and lets you focus learning on the interesting part (strategy selection) instead of WebSocket reconnection logic.

-----

## Phased Build Plan

```mermaid
gantt
    title Build Phases
    dateFormat YYYY-MM-DD
    section Phase 1
    Risk layer + paper exec      :2026-05-01, 14d
    One strategy (momentum)      :14d
    section Phase 2
    Regime detector (rule-based) :14d
    Strategy library (3-4 strats):21d
    Hard-mapped selector         :7d
    section Phase 3
    Paper trade live data        :60d
    Tune & remove broken strats  :30d
    section Phase 4
    Live trading, tiny size      :30d
    Performance-weighted selector:21d
    section Phase 5
    Scale up if metrics hold     :ongoing
```

**Phase gates** — do not advance until:

- Phase 1 → 2: bot can paper-trade one strategy end-to-end without crashes
- Phase 2 → 3: regime classifier agrees with your manual chart reading 80%+ of the time
- Phase 3 → 4: 60+ days of paper trading shows positive expectancy net of estimated fees/slippage
- Phase 4 → 5: 30+ days live with small size match paper performance within reasonable tolerance

-----

## Decisions You Need to Make

Before building, answer these:

1. **Which exchanges?** (Coinbase = US-friendly, Binance = deepest liquidity, Kraken = good API)
2. **Which pairs?** Start with 2–3 liquid majors (BTC, ETH, SOL). Don't trade microcaps.
3. **Spot or perpetuals?** Recommend spot for v1.
4. **Capital floor?** What's the smallest dollar amount you'd take seriously? (Affects whether fees eat your edge.)
5. **Hosting?** Local PC (cheap, fragile), VPS (cheap, reliable), or cloud (more expensive, fully managed)?
6. **Tax handling?** Every trade is a taxable event in the US. Plan for export to CoinTracker or similar from day one.

-----

## What This Plan Deliberately Does Not Do

- **No ML for strategy selection in v1.** It's the most overfit-prone part of the system.
- **No high-frequency anything.** You can't compete with HFT firms on latency.
- **No copy-trading other bots.** Black boxes blow up.
- **No leverage in v1.** Survival > returns.
- **No "set and forget."** This needs daily monitoring, especially in the first 6 months.

-----

## Next Steps If You Want to Move Forward

1. Pick a path (Freqtrade base vs custom) — affects everything downstream
2. Pick exchange + pairs
3. Spec the risk layer in detail (specific numbers for your capital level)
4. Spec the regime detector (which indicators, what thresholds)
5. Pick the first strategy to implement (momentum is the easiest to get right)

Happy to drill into any layer in detail — regime detection math, specific strategy logic, the risk module, the selector design, or the Freqtrade integration approach.
