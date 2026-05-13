"""
AI-Trader Signal Monitor — cross-platform port of monitor.ps1.

Polls the Freqtrade REST API every N seconds, prints a per-pair temperature
read (regime / RSI / ADX / EMA spread / proximity), and fires desktop
notifications when an entry signal fires or a trade actually opens.

Reads connection details and the pair whitelist from user_data/config.json.

Usage:
    python tools/monitor.py                  # default 300s poll
    python tools/monitor.py --poll-seconds 60
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from typing import Any

from freqtrade_client import (
    FreqtradeClient,
    FreqtradeClientError,
    load_settings,
)
from notify import notify


def get_proximity(regime: str, rsi: float, spread: float) -> int:
    """0–100 score for how close the current candle is to firing an entry.

    Necessary but not sufficient — the EMA cross is a discrete event between
    bars, so a high spread proximity doesn't guarantee a cross will happen.
    Updates only at the top of each candle.
    """
    spread_threshold = 0.5  # percent — closer to zero EMA spread = closer to a cross
    rsi_band = 10           # RSI points away from the trigger that count as approaching

    if regime == "TRENDING_UP":
        if rsi >= 65:
            return 0
        p = (1 - min(abs(spread), spread_threshold) / spread_threshold) * 100
        return int(round(p))
    if regime == "TRENDING_DOWN":
        if rsi <= 35:
            return 0
        p = (1 - min(abs(spread), spread_threshold) / spread_threshold) * 100
        return int(round(p))
    if regime == "RANGING_LOW":
        # Triggers at RSI < 30; band = 40 means RSI 40 -> 0%, RSI 30 -> 100%
        p = ((40 - rsi) / rsi_band) * 100
        return int(round(max(0.0, min(100.0, p))))
    return 0  # RANGING_HIGH and CHOPPY have no entry strategy


def fmt_signed(value: float, decimals: int = 2) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{round(value, decimals)}"


def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


def show_header(client: FreqtradeClient) -> None:
    ts = datetime.now().strftime("%a %d-%b-%Y  %H:%M")
    print("=" * 70)
    print(f"  AI-Trader  |  {ts}  |  Paper Trading")

    try:
        b = client.balance()
        p = client.profit()
        total = round(float(b.get("total", 0)), 2)
        pnl_abs = round(float(p.get("profit_all_coin", 0)), 2)
        pnl_pct = round(float(p.get("profit_all_percent", 0)), 2)
        closed = int(p.get("closed_trade_count", 0) or 0)
        wins = int(p.get("winning_trades", 0) or 0)
        losses = int(p.get("losing_trades", 0) or 0)
        wr = round(wins / closed * 100) if closed else 0
        print(
            f"  Wallet: ${total} USDT   |   "
            f"All-time P&L: {fmt_signed(pnl_abs)} USDT ({fmt_signed(pnl_pct)}%)"
        )
        print(f"  Closed trades: {closed}   |   Win rate: {wr}%   ({wins}W / {losses}L)")
    except FreqtradeClientError as e:
        print(f"  [balance unavailable: {e}]")

    print("=" * 70)


def show_open_trades(client: FreqtradeClient) -> None:
    try:
        trades = client.status()
    except FreqtradeClientError as e:
        print(f"  [open trades unavailable: {e}]")
        return

    if not trades:
        print("  No open trades.")
        return

    print(f"  Open Trades ({len(trades)}/3 slots used)")
    print(
        f"  {'#':<4} {'Dir':<6} {'Entry':<10} {'Unreal%':<9} "
        f"{'Unreal$':<9} {'Tag':<22} Open for"
    )
    now = datetime.now(timezone.utc)
    for t in trades:
        direction = "SHORT" if t.get("is_short") else "LONG"
        pct = fmt_signed(float(t.get("profit_percent", 0)) * 100) + "%"
        abs_pnl = fmt_signed(float(t.get("profit_abs", 0)))
        try:
            opened = datetime.fromisoformat(str(t["open_date"]).replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            dur = now - opened
            hours = int(dur.total_seconds() // 3600)
            minutes = int((dur.total_seconds() % 3600) // 60)
            dur_s = f"{hours}h {minutes}m"
        except (KeyError, ValueError):
            dur_s = "--"
        tag = str(t.get("enter_tag") or "")
        if len(tag) > 22:
            tag = tag[:21] + "~"
        print(
            f"  {t.get('trade_id', '?'):<4} {direction:<6} "
            f"{round(float(t.get('open_rate', 0)), 2):<10} "
            f"{pct:<9} {abs_pnl:<9} {tag:<22} {dur_s}"
        )


class SignalChecker:
    """Tracks which signals/trades have already been notified, across cycles."""

    def __init__(self) -> None:
        self._notified_signals: set[str] = set()
        self._open_trade_ids: set[Any] = set()

    def check_signals(self, client: FreqtradeClient) -> None:
        for pair in client.pairs:
            try:
                r = client.pair_candles(pair, timeframe="1h", limit=1)
            except FreqtradeClientError as e:
                print(f"{now_hhmm()} Warn [{pair}]: {e}")
                continue

            data = r.get("data") or []
            cols = r.get("columns") or []
            if not data or not cols:
                continue

            row = dict(zip(cols, data[-1]))
            sym = pair.split("/")[0]
            date = row.get("date")
            regime = row.get("regime") or "UNKNOWN"
            tag = row.get("enter_tag") or ""
            try:
                rsi = round(float(row.get("rsi") or 0), 1)
                adx = round(float(row.get("adx") or 0), 1)
                ema_20 = float(row.get("ema_20") or 0)
                ema_50 = float(row.get("ema_50") or 0)
                spread = round((ema_20 - ema_50) / ema_50 * 100, 3) if ema_50 else 0.0
            except (TypeError, ValueError):
                rsi, adx, spread = 0.0, 0.0, 0.0

            prox = get_proximity(regime, rsi, spread)
            flag = " ***" if prox >= 80 else ""
            print(
                f"{now_hhmm()}  {sym:<4}  regime={regime:<14}  RSI={rsi:>5}  "
                f"ADX={adx:>5}  EMA-spread={spread:>7}%  prox={prox:>3}%{flag}"
            )

            if int(r.get("enter_long_signals", 0) or 0) > 0:
                key = f"{pair}:long:{date}"
                if key not in self._notified_signals:
                    self._notified_signals.add(key)
                    notify(f"Long Signal: {sym}", f"{tag} | {regime} | RSI {rsi}")
                    print(f"  *** LONG entry signal fired: {pair} [{tag}]")

            if int(r.get("enter_short_signals", 0) or 0) > 0:
                key = f"{pair}:short:{date}"
                if key not in self._notified_signals:
                    self._notified_signals.add(key)
                    notify(f"Short Signal: {sym}", f"{tag} | {regime} | RSI {rsi}")
                    print(f"  *** SHORT entry signal fired: {pair} [{tag}]")

            # Approaching-signal hints (terminal only, no toast)
            long_signals = int(r.get("enter_long_signals", 0) or 0)
            short_signals = int(r.get("enter_short_signals", 0) or 0)
            if regime == "RANGING_LOW" and rsi < 35 and long_signals == 0:
                print(
                    f"  --> {sym} RSI approaching oversold threshold "
                    f"(need below 30 for mean-rev long)"
                )
            if (
                regime == "TRENDING_UP"
                and abs(spread) < 0.05
                and long_signals == 0
            ):
                print(
                    f"  --> {sym} EMA spread under 0.05pct in TRENDING_UP "
                    f"- momentum cross may be near"
                )
            if (
                regime == "TRENDING_DOWN"
                and abs(spread) < 0.05
                and short_signals == 0
            ):
                print(
                    f"  --> {sym} EMA spread under 0.05pct in TRENDING_DOWN "
                    f"- short cross may be near"
                )

    def check_trades(self, client: FreqtradeClient) -> None:
        try:
            trades = client.status()
        except FreqtradeClientError as e:
            print(f"{now_hhmm()} Warn [status]: {e}")
            return

        current_ids: set[Any] = set()
        for t in trades:
            tid = t.get("trade_id")
            current_ids.add(tid)
            if tid not in self._open_trade_ids:
                self._open_trade_ids.add(tid)
                pair = t.get("pair", "?")
                sym = str(pair).split("/")[0]
                direction = "SHORT" if t.get("is_short") else "LONG"
                rate = round(float(t.get("open_rate", 0)), 2)
                tag = t.get("enter_tag") or ""
                notify(f"Trade Opened: {sym} {direction}", f"@ {rate} USDT | {tag}")
                print(
                    f"{now_hhmm()}  Trade #{tid} opened: {pair} {direction} "
                    f"@ {rate}  [{tag}]"
                )

        # Drop closed trades from the dedupe set so a future re-open notifies.
        for tid in list(self._open_trade_ids):
            if tid not in current_ids:
                self._open_trade_ids.discard(tid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=300,
        help="Polling interval in seconds (default: 300)",
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
    except FreqtradeClientError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    client = FreqtradeClient(settings)
    checker = SignalChecker()

    print(f"AI-Trader Monitor running - poll every {args.poll_seconds}s")
    print(f"Pairs: {', '.join(settings.pairs)}")
    print("Press Ctrl+C to stop")

    while True:
        try:
            show_header(client)
            show_open_trades(client)
            checker.check_signals(client)
            checker.check_trades(client)
        except FreqtradeClientError as e:
            print(f"{now_hhmm()} Error: {e}")
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0

        print("-" * 70)
        try:
            time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
