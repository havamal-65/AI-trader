"""
AI-Trader Daily Performance Report — cross-platform port of Daily-Report.ps1.

Queries the Freqtrade REST API, writes a plain-text report to
user_data/logs/report_YYYY-MM-DD.txt, and fires a desktop notification summary.

Reads connection details from user_data/config.json.

Usage:
    python tools/daily_report.py
    python tools/daily_report.py --lookback 200   # widen the trades window
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from freqtrade_client import (
    REPO_ROOT,
    FreqtradeClient,
    FreqtradeClientError,
    load_settings,
)
from notify import notify

LOG_DIR = REPO_ROOT / "user_data" / "logs"


def fmt_value(value: float, unit: str = "USDT") -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{round(value, 2)} {unit}"


def get_today_closed_trades(client: FreqtradeClient, lookback: int) -> list[dict[str, Any]]:
    payload = client.trades(limit=lookback)
    today_local = datetime.now().date()
    out: list[dict[str, Any]] = []
    for t in payload.get("trades", []):
        close_date = t.get("close_date")
        if not close_date:
            continue
        try:
            dt = datetime.fromisoformat(str(close_date).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.astimezone().date() == today_local:
                out.append(t)
        except ValueError:
            continue
    return out


def format_report(
    profit: dict[str, Any],
    balance: dict[str, Any],
    today_trades: list[dict[str, Any]],
    date_label: str,
) -> str:
    sep = "=" * 50
    lines: list[str] = []

    lines.append(sep)
    lines.append(f"  AI-TRADER DAILY REPORT -- {date_label}")
    lines.append(sep)
    lines.append("")

    # WALLET
    lines.append("  WALLET")
    total = round(float(balance.get("total", 0)), 2)
    lines.append(f"  {'Current balance':<20}: ${total} USDT")
    lines.append("")

    # ALL-TIME
    lines.append("  ALL-TIME")
    pnl_abs = round(float(profit.get("profit_all_coin", 0)), 2)
    pnl_pct = round(float(profit.get("profit_all_percent", 0)), 2)
    sign = "+" if pnl_abs >= 0 else ""
    closed = int(profit.get("closed_trade_count", 0) or 0)
    wins = int(profit.get("winning_trades", 0) or 0)
    losses = int(profit.get("losing_trades", 0) or 0)
    wr = round(wins / closed * 100) if closed else 0

    pf_raw = profit.get("profit_factor")
    if pf_raw is None or (isinstance(pf_raw, (int, float)) and pf_raw > 999):
        pf = "N/A"
    else:
        pf = str(round(float(pf_raw), 2))

    best = profit.get("best_pair") or "--"
    sharpe = round(float(profit.get("sharpe", 0) or 0), 2)
    maxdd = round(float(profit.get("max_drawdown", 0) or 0) * 100, 2)

    lines.append(f"  {'Total P&L':<20}: {sign}{pnl_abs} USDT  ({sign}{pnl_pct}%)")
    lines.append(f"  {'Closed trades':<20}: {closed}")
    lines.append(f"  {'Win rate':<20}: {wr}%  ({wins}W / {losses}L)")
    lines.append(f"  {'Profit factor':<20}: {pf}")
    lines.append(f"  {'Sharpe ratio':<20}: {sharpe}")
    lines.append(f"  {'Max drawdown':<20}: {maxdd}%")
    lines.append(f"  {'Best pair':<20}: {best}")
    lines.append("")

    # TODAY
    lines.append(f"  TODAY  ({date_label})")
    if not today_trades:
        lines.append("  No trades closed today.")
    else:
        today_pnl = sum(float(t.get("profit_abs", 0) or 0) for t in today_trades)
        today_wins = sum(1 for t in today_trades if float(t.get("profit_abs", 0) or 0) > 0)
        today_count = len(today_trades)
        today_wr = round(today_wins / today_count * 100)
        sorted_by_pnl = sorted(
            today_trades, key=lambda t: float(t.get("profit_abs", 0) or 0)
        )
        worst, best_trade = sorted_by_pnl[0], sorted_by_pnl[-1]

        def trade_line(t: dict[str, Any]) -> str:
            return (
                f"{t.get('pair', '?')}  "
                f"{fmt_value(float(t.get('profit_abs', 0) or 0))}  "
                f"({t.get('enter_tag', '')})"
            )

        lines.append(f"  {'Closed trades':<20}: {today_count}")
        lines.append(f"  {'Today P&L':<20}: {fmt_value(today_pnl)}")
        lines.append(
            f"  {'Win rate':<20}: {today_wr}%  "
            f"({today_wins}W / {today_count - today_wins}L)"
        )
        lines.append(f"  {'Best trade':<20}: {trade_line(best_trade)}")
        worst_str = trade_line(worst) if worst.get("trade_id") != best_trade.get("trade_id") else "--"
        lines.append(f"  {'Worst trade':<20}: {worst_str}")
    lines.append("")

    lines.append(sep)
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (local)")
    lines.append(sep)

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback",
        type=int,
        default=100,
        help="How many recent trades to scan when filtering for today (default: 100)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=LOG_DIR,
        help=f"Where to write the report (default: {LOG_DIR})",
    )
    args = parser.parse_args()

    date_label = datetime.now().strftime("%Y-%m-%d")
    args.log_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.log_dir / f"report_{date_label}.txt"

    try:
        settings = load_settings()
    except FreqtradeClientError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    client = FreqtradeClient(settings)

    try:
        profit = client.profit()
        balance = client.balance()
        today_trades = get_today_closed_trades(client, args.lookback)

        content = format_report(profit, balance, today_trades, date_label)
        report_path.write_text(content, encoding="utf-8")

        total = round(float(balance.get("total", 0)), 2)
        today_pnl_str = (
            fmt_value(sum(float(t.get("profit_abs", 0) or 0) for t in today_trades))
            if today_trades
            else "no trades"
        )
        closed = int(profit.get("closed_trade_count", 0) or 0)
        wins = int(profit.get("winning_trades", 0) or 0)
        wr = round(wins / closed * 100) if closed else 0
        month_day = datetime.now().strftime("%b %d")

        notify(
            f"AI-Trader -- {month_day}",
            f"${total} | Today: {today_pnl_str} | {closed} trades | WR {wr}%",
        )
        print(f"Report written: {report_path}")
        print(content)
        return 0

    except FreqtradeClientError as e:
        err = (
            f"AI-Trader Daily Report -- {date_label}\n"
            f"API unreachable at {datetime.now().strftime('%H:%M')}: {e}"
        )
        report_path.write_text(err, encoding="utf-8")
        notify("AI-Trader Report FAILED", "API down -- check Docker")
        print(f"Report failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
