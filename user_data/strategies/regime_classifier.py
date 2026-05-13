"""
RegimeClassifier — market state detection.

Phase 2: Vectorized rule-based classifier using ADX, Bollinger Bandwidth, ATR ratio.
Phase 3+: Optional HMM-based classifier.

Regime labels (plain strings for simple DataFrame column comparisons):
    TRENDING_UP    — ADX > 25, BB expanding, close above EMA(50)
    TRENDING_DOWN  — ADX > 25, BB expanding, close below EMA(50)
    RANGING_HIGH   — ADX < 20, BB contracting, ATR ratio above rolling median
    RANGING_LOW    — ADX < 20, BB contracting, ATR ratio below rolling median
    CHOPPY         — everything else (ADX 20–25, or mixed signals)
"""
from __future__ import annotations

import logging

import numpy as np
from pandas import DataFrame

logger = logging.getLogger(__name__)

REGIME_COLUMN: str = "regime"


class RegimeClassifier:
    """
    Classifies market regime from OHLCV-derived indicators.

    Called from RegimeAwareStrategy.populate_indicators(). All indicator
    columns (adx, atr_ratio, bb_width) are computed in that method before
    classify() is called — this class only reads them.

    ADX thresholds are tunable via constructor so hyperopt can sweep them.
    """

    def __init__(
        self,
        adx_trending_threshold: float = 25.0,
        adx_ranging_threshold: float = 20.0,
    ) -> None:
        self.adx_trending_threshold = adx_trending_threshold
        self.adx_ranging_threshold = adx_ranging_threshold

    def classify(self, dataframe: DataFrame) -> DataFrame:
        """
        Append a 'regime' column to the dataframe using vectorized numpy.select().

        Reads columns written by populate_indicators() before this call:
            adx, bb_width, bb_width_ma, close, ema_50, atr_ratio, atr_ratio_median

        :param dataframe:  Indicator DataFrame from populate_indicators()
        :return:           Same DataFrame with REGIME_COLUMN added/updated
        """
        is_trending = (dataframe["adx"] > self.adx_trending_threshold) & (dataframe["bb_width"] > dataframe["bb_width_ma"])
        is_ranging  = (dataframe["adx"] < self.adx_ranging_threshold) & (dataframe["bb_width"] <= dataframe["bb_width_ma"])

        conditions = [
            is_trending & (dataframe["close"] > dataframe["ema_50"]),
            is_trending & (dataframe["close"] <= dataframe["ema_50"]),
            is_ranging  & (dataframe["atr_ratio"] > dataframe["atr_ratio_median"]),
            is_ranging  & (dataframe["atr_ratio"] <= dataframe["atr_ratio_median"]),
        ]
        choices = ["TRENDING_UP", "TRENDING_DOWN", "RANGING_HIGH", "RANGING_LOW"]

        dataframe[REGIME_COLUMN] = np.select(conditions, choices, default="CHOPPY")
        logger.debug(
            "RegimeClassifier: %s", dataframe[REGIME_COLUMN].value_counts().to_dict()
        )
        return dataframe

    def _classify_row(self, adx: float, atr_ratio: float, bb_width: float) -> str:
        """Superseded by vectorized classify(). Retained for API compatibility."""
        raise NotImplementedError("superseded by vectorized classify()")
