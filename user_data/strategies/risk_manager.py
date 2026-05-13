"""
RiskManager — correlation veto for risks not expressible in Freqtrade config.

Risk control map:
  max_open_trades: 3        → config.json (Freqtrade native)
  stake_amount 5%           → config.json (Freqtrade native)
  stoploss -0.02            → strategy class attribute (Freqtrade native)
  daily loss 3%             → config.json MaxDrawdown lookback=1440m (Freqtrade native)
  weekly drawdown 8%        → config.json MaxDrawdown lookback=10080m (Freqtrade native)
  kill switch               → protections + `freqtrade stopentry` CLI
  correlation veto          → THIS CLASS (BTC/ETH/SOL move together; cap at 2 of 3)
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# All three target pairs are strongly correlated — treat as one group.
_CORRELATED_PAIRS: frozenset[str] = frozenset({"BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"})
# Holding all 3 simultaneously is effectively 3× concentrated BTC risk.
_MAX_CORRELATED_POSITIONS: int = 2


class RiskManager:
    """
    Veto layer for correlation risk that Freqtrade config cannot express.

    Usage (inside RegimeAwareStrategy.confirm_trade_entry):
        approved = self._risk_manager.approve_entry(pair, open_trades, current_time)
        if not approved:
            return False
    """

    def approve_entry(
        self,
        pair: str,
        open_trades: list,
        current_time: datetime,
    ) -> bool:
        """
        Return True to allow the entry, False to veto it.

        :param pair:         Pair being considered (e.g. 'BTC/USD')
        :param open_trades:  List of open Trade objects from Freqtrade persistence
        :param current_time: Current UTC datetime supplied by Freqtrade
        """
        return self._check_correlation(pair, open_trades)

    def _check_correlation(self, pair: str, open_trades: list) -> bool:
        if pair not in _CORRELATED_PAIRS:
            return True

        correlated_open = sum(1 for t in open_trades if t.pair in _CORRELATED_PAIRS)

        if correlated_open >= _MAX_CORRELATED_POSITIONS:
            logger.info(
                "RiskManager: VETO %s — %d/%d correlated positions already open (%s)",
                pair,
                correlated_open,
                _MAX_CORRELATED_POSITIONS,
                ", ".join(t.pair for t in open_trades if t.pair in _CORRELATED_PAIRS),
            )
            return False

        return True
