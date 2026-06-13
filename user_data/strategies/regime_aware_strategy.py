"""
RegimeAwareStrategy — Freqtrade v1 meta-strategy, Phase 2.

populate_indicators  → EMA(20/50), ADX, ATR ratio, Bollinger Bands, RSI, regime column
populate_entry_trend → hard-mapped selector: 3 long strategies + 1 short, gated by regime
populate_exit_trend  → regime-aware exits for both long and short positions
confirm_trade_entry  → RiskManager correlation veto (max 2 of BTC/ETH/SOL open)
leverage             → locked at 1x (spot margin, no amplification)

Regime → Strategy mapping:
    TRENDING_UP   → Momentum long (EMA cross up)
    TRENDING_DOWN → Momentum short (EMA cross down)
    RANGING_LOW   → Mean Reversion long (RSI oversold)
    RANGING_HIGH  → Bollinger Band Reversal long (lower band touch)
    CHOPPY        → Stay flat

All numeric parameters are class-level constants so subclasses (e.g. for a
different timeframe) can override one number without touching method bodies.
Period-based fields are in candles; threshold-based fields are unitless.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

# Freqtrade adds user_data/strategies/ to sys.path for the MAIN process only.
# Hyperopt's joblib workers spawn fresh interpreters that don't inherit it,
# so flat sibling imports (regime_classifier, risk_manager) fail under
# hyperopt with "ModuleNotFoundError". Add the path ourselves to be safe.
_STRATEGIES_DIR = str(Path(__file__).parent)
if _STRATEGIES_DIR not in sys.path:
    sys.path.insert(0, _STRATEGIES_DIR)

from regime_classifier import REGIME_COLUMN, RegimeClassifier  # noqa: E402
from risk_manager import RiskManager  # noqa: E402

logger = logging.getLogger(__name__)


class RegimeAwareStrategy(IStrategy):
    INTERFACE_VERSION: int = 3
    can_short: bool = False  # SPOT ONLY — no shorting (user constraint, never futures)

    # 1h gives ~50 bars warmup for EMA(50). Subclass to switch timeframes.
    timeframe: str = "1h"

    # -----------------------------------------------------------------------
    # Tunable constants — subclasses override these to retarget timeframe
    # or hyperopt parameters override the *_PARAM equivalents at runtime.
    # -----------------------------------------------------------------------

    # Indicator periods (in candles)
    EMA_FAST_PERIOD: int = 20
    EMA_SLOW_PERIOD: int = 50
    ADX_PERIOD: int = 14
    ATR_PERIOD: int = 14
    BB_PERIOD: int = 20
    RSI_PERIOD: int = 14

    # Rolling window lookbacks (in candles)
    ATR_RATIO_LOOKBACK: int = 100
    BB_WIDTH_MA_LOOKBACK: int = 20

    # Entry/exit thresholds (unitless — RSI 0–100)
    RSI_OVERBOUGHT_ENTRY: int = 65
    RSI_OVERSOLD_ENTRY: int = 30
    RSI_NOT_OVERSOLD_SHORT: int = 35
    RSI_OVERBOUGHT_EXIT: int = 70

    # Regime classifier ADX cutoffs
    ADX_TRENDING_THRESHOLD: float = 25.0
    ADX_RANGING_THRESHOLD: float = 20.0

    # Structural: how many bars back to look for an EMA cross.
    # 1 = original edge-trigger behavior (cross fired on this exact bar).
    # >1 = level-trigger window: cross fired any time in the last N bars
    #      AND ema_20 still above (for long) / below (for short) ema_50.
    # This addresses the "missed mid-trend re-entry" issue surfaced in the
    # diagnostic — only ~1% of bars produce edge crosses but ~50% of bars
    # are in a sustained EMA arrangement.
    CROSS_LOOKBACK_BARS: int = 1

    # Stop loss (signed ratio — Freqtrade convention).
    # Class attribute (not @property) because Freqtrade's StrategyResolver
    # normalizes it at startup with strategy.stoploss = float(strategy.stoploss),
    # which would crash on a property without a setter.
    stoploss: float = -0.02

    # StoplossGuard windows (in candles — scale with timeframe)
    STOPLOSS_GUARD_LOOKBACK_CANDLES: int = 24
    STOPLOSS_GUARD_STOP_CANDLES: int = 4

    # -----------------------------------------------------------------------
    # Freqtrade-required attributes derived from the constants above
    # -----------------------------------------------------------------------

    # 200 bars covers EMA(50) warmup + ADX/ATR + BB needs at default sizes.
    # Override in subclasses if EMA_SLOW_PERIOD changes drastically.
    startup_candle_count: int = 200

    @property
    def protections(self) -> list:
        """
        Risk controls Freqtrade enforces via the protection framework.
        Candle-based fields scale with the strategy's timeframe via the
        STOPLOSS_GUARD_*_CANDLES constants. Minute-based fields (daily/weekly
        MaxDrawdown lookbacks) stay in absolute time and don't need scaling.
        """
        return [
            {
                "method": "StoplossGuard",
                "lookback_period_candles": self.STOPLOSS_GUARD_LOOKBACK_CANDLES,
                "trade_limit": 2,
                "stop_duration_candles": self.STOPLOSS_GUARD_STOP_CANDLES,
                "only_per_pair": False,
            },
            {
                # 3% daily loss limit — locks trading for 1 day if breached
                "method": "MaxDrawdown",
                "lookback_period": 1440,
                "trade_limit": 1,
                "stop_duration": 1440,
                "max_allowed_drawdown": 0.03,
            },
            {
                # 8% weekly drawdown limit — locks trading for 7 days if breached
                "method": "MaxDrawdown",
                "lookback_period": 10080,
                "trade_limit": 1,
                "stop_duration": 10080,
                "max_allowed_drawdown": 0.08,
            },
        ]

    # Disabled ROI table — exits driven by signal, not time.
    minimal_roi: dict = {"0": 100}

    trailing_stop: bool = False
    use_exit_signal: bool = True
    exit_profit_only: bool = False
    ignore_roi_if_entry_signal: bool = False
    process_only_new_candles: bool = True

    # Limit orders on all sides — saves fees + slippage per architecture.
    order_types: dict = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "limit",
        "stoploss_on_exchange": False,
    }
    order_time_in_force: dict = {"entry": "GTC", "exit": "GTC"}

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._risk_manager = RiskManager()

    def _build_classifier(self) -> RegimeClassifier:
        """
        Built fresh each populate_indicators call so hyperopt parameter
        changes (which mutate self.* between epochs) propagate without
        needing a per-epoch reset hook.
        """
        return RegimeClassifier(
            adx_trending_threshold=self.ADX_TRENDING_THRESHOLD,
            adx_ranging_threshold=self.ADX_RANGING_THRESHOLD,
        )

    # -----------------------------------------------------------------------
    # Leverage — always 1x (spot margin with no amplification)
    # -----------------------------------------------------------------------

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return 1.0

    # -----------------------------------------------------------------------
    # Indicator population
    # -----------------------------------------------------------------------

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # --- Momentum ---
        dataframe["ema_20"] = ta.EMA(dataframe, timeperiod=self.EMA_FAST_PERIOD)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=self.EMA_SLOW_PERIOD)

        # --- Regime detection inputs ---
        dataframe["adx"]              = ta.ADX(dataframe, timeperiod=self.ADX_PERIOD)
        dataframe["atr"]              = ta.ATR(dataframe, timeperiod=self.ATR_PERIOD)
        dataframe["atr_ratio"]        = dataframe["atr"] / dataframe["close"]
        dataframe["atr_ratio_median"] = dataframe["atr_ratio"].rolling(self.ATR_RATIO_LOOKBACK).median()

        bollinger                = ta.BBANDS(dataframe, timeperiod=self.BB_PERIOD, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_lower"]    = bollinger["lowerband"]
        dataframe["bb_middle"]   = bollinger["middleband"]
        dataframe["bb_upper"]    = bollinger["upperband"]
        dataframe["bb_width"]    = (dataframe["bb_upper"] - dataframe["bb_lower"]) / dataframe["bb_middle"]
        dataframe["bb_width_ma"] = dataframe["bb_width"].rolling(self.BB_WIDTH_MA_LOOKBACK).mean()

        # --- Mean reversion ---
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.RSI_PERIOD)

        # --- Classify regime (reads columns above; writes REGIME_COLUMN) ---
        dataframe = self._build_classifier().classify(dataframe)

        return dataframe

    # -----------------------------------------------------------------------
    # Cross detection — supports both edge-trigger (CROSS_LOOKBACK_BARS=1)
    # and level-trigger window (CROSS_LOOKBACK_BARS>1).
    # -----------------------------------------------------------------------

    def _ema_cross_up(self, dataframe: DataFrame):
        """
        True if ema_20 is currently above ema_50 AND a cross-up event
        occurred within the last CROSS_LOOKBACK_BARS bars.
        """
        currently_above = dataframe["ema_20"] > dataframe["ema_50"]
        crossed_within_window = False
        for k in range(1, self.CROSS_LOOKBACK_BARS + 1):
            crossed_at_k = (dataframe["ema_20"].shift(k - 1) > dataframe["ema_50"].shift(k - 1)) & \
                           (dataframe["ema_20"].shift(k) <= dataframe["ema_50"].shift(k))
            if crossed_within_window is False:
                crossed_within_window = crossed_at_k
            else:
                crossed_within_window = crossed_within_window | crossed_at_k
        return currently_above & crossed_within_window

    def _ema_cross_down(self, dataframe: DataFrame):
        """Mirror of _ema_cross_up for short entries."""
        currently_below = dataframe["ema_20"] < dataframe["ema_50"]
        crossed_within_window = False
        for k in range(1, self.CROSS_LOOKBACK_BARS + 1):
            crossed_at_k = (dataframe["ema_20"].shift(k - 1) < dataframe["ema_50"].shift(k - 1)) & \
                           (dataframe["ema_20"].shift(k) >= dataframe["ema_50"].shift(k))
            if crossed_within_window is False:
                crossed_within_window = crossed_at_k
            else:
                crossed_within_window = crossed_within_window | crossed_at_k
        return currently_below & crossed_within_window

    # -----------------------------------------------------------------------
    # Signal generation
    # -----------------------------------------------------------------------

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        trending_up   = dataframe[REGIME_COLUMN] == "TRENDING_UP"
        trending_down = dataframe[REGIME_COLUMN] == "TRENDING_DOWN"
        ranging_low   = dataframe[REGIME_COLUMN] == "RANGING_LOW"

        ema_cross_up = self._ema_cross_up(dataframe)
        ema_cross_down = self._ema_cross_down(dataframe)

        # --- Long entries ---

        # Strategy 1 — Momentum long: EMA(20) cross above EMA(50), RSI not overbought
        rsi_not_overbought = dataframe["rsi"] < self.RSI_OVERBOUGHT_ENTRY
        dataframe.loc[trending_up & ema_cross_up & rsi_not_overbought, "enter_long"] = 1
        dataframe.loc[trending_up & ema_cross_up & rsi_not_overbought, "enter_tag"]  = "momentum_ema_cross"

        # Strategy 2 — Mean reversion long: RSI oversold
        rsi_oversold = dataframe["rsi"] < self.RSI_OVERSOLD_ENTRY
        dataframe.loc[ranging_low & rsi_oversold, "enter_long"] = 1
        dataframe.loc[ranging_low & rsi_oversold, "enter_tag"]  = "mean_rev_rsi_oversold"

        # Strategy 3 (BB reversal long) removed — 1.5% win rate in Phase 2 backtest.
        # RANGING_HIGH stays flat until Phase 3 data shows a better signal.

        # --- Short entries ---

        # Strategy 1 (Short) — Momentum short: EMA(20) cross below EMA(50), RSI not oversold
        rsi_not_oversold = dataframe["rsi"] > self.RSI_NOT_OVERSOLD_SHORT
        dataframe.loc[trending_down & ema_cross_down & rsi_not_oversold, "enter_short"] = 1
        dataframe.loc[trending_down & ema_cross_down & rsi_not_oversold, "enter_tag"]   = "momentum_short_ema_cross"

        # --- Diagnostic columns (per-bar boolean flags; aggregated downstream) ---
        # These are observability-only and do not affect signal generation.
        dataframe["dbg_cross_no_regime"]       = ema_cross_up & ~trending_up
        dataframe["dbg_regime_no_cross"]       = trending_up & ~ema_cross_up
        dataframe["dbg_in_uptrend_no_cross"]   = (dataframe["ema_20"] > dataframe["ema_50"]) & ~ema_cross_up & trending_up
        dataframe["dbg_rsi_near_oversold"]     = ranging_low & (dataframe["rsi"] >= self.RSI_OVERSOLD_ENTRY) & (dataframe["rsi"] < self.RSI_OVERSOLD_ENTRY + 5)

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # --- Long exits ---

        # Universal: regime turns hostile → exit any long
        bad_regime = dataframe[REGIME_COLUMN].isin(["CHOPPY", "TRENDING_DOWN"])
        dataframe.loc[bad_regime, "exit_long"] = 1
        dataframe.loc[bad_regime, "exit_tag"]  = "regime_exit"

        # Momentum long exit: EMA(20) cross below EMA(50)
        # Note: exits keep the original edge-trigger semantics regardless of
        # CROSS_LOOKBACK_BARS — exiting on the first cross is the right move,
        # we don't want a window here.
        trending_up = dataframe[REGIME_COLUMN] == "TRENDING_UP"
        ema_cross_down_strict = (
            (dataframe["ema_20"] < dataframe["ema_50"])
            & (dataframe["ema_20"].shift(1) >= dataframe["ema_50"].shift(1))
        )
        dataframe.loc[trending_up & ema_cross_down_strict, "exit_long"] = 1
        dataframe.loc[trending_up & ema_cross_down_strict, "exit_tag"]  = "momentum_exit"

        # Mean reversion exit: RSI overbought
        ranging_low    = dataframe[REGIME_COLUMN] == "RANGING_LOW"
        rsi_overbought = dataframe["rsi"] > self.RSI_OVERBOUGHT_EXIT
        dataframe.loc[ranging_low & rsi_overbought, "exit_long"] = 1
        dataframe.loc[ranging_low & rsi_overbought, "exit_tag"]  = "mean_rev_exit"

        # --- Short exits ---

        # Universal: regime turns non-bearish → exit any short
        not_trending_down = dataframe[REGIME_COLUMN] != "TRENDING_DOWN"
        dataframe.loc[not_trending_down, "exit_short"] = 1
        # Only write exit_tag for rows where exit_long wasn't already tagged
        dataframe.loc[not_trending_down & ~bad_regime, "exit_tag"] = "regime_exit"

        # Momentum short exit: EMA(20) cross above EMA(50) while still in TRENDING_DOWN
        trending_down = dataframe[REGIME_COLUMN] == "TRENDING_DOWN"
        ema_cross_up_strict = (
            (dataframe["ema_20"] > dataframe["ema_50"])
            & (dataframe["ema_20"].shift(1) <= dataframe["ema_50"].shift(1))
        )
        dataframe.loc[trending_down & ema_cross_up_strict, "exit_short"] = 1
        dataframe.loc[trending_down & ema_cross_up_strict, "exit_tag"]   = "momentum_short_exit"

        return dataframe

    # -----------------------------------------------------------------------
    # Order confirmation — correlation veto
    # -----------------------------------------------------------------------

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        open_trades = Trade.get_trades_proxy(is_open=True)
        approved = self._risk_manager.approve_entry(pair, open_trades, current_time)

        if not approved:
            logger.info(
                "confirm_trade_entry: %s REJECTED by RiskManager at %s",
                pair,
                current_time,
            )

        return approved

    # -----------------------------------------------------------------------
    # Diagnostic logging — runs once at end of backtest, summarizes the
    # dbg_* columns set in populate_entry_trend across the full data range.
    # -----------------------------------------------------------------------

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        # Hook reserved for live-mode diagnostics; backtest summary lives in
        # the analyze script. Keeping this empty avoids any per-tick overhead.
        pass
