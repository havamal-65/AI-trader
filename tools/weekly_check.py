"""
AI-Trader Weekly AI Health Check - headless Claude review.

Invokes `claude -p` with a self-contained prompt that diagnoses the bot's
state and judges whether anything looks anomalous. Writes the digest to
user_data/logs/ai_check_YYYY-MM-DD.md and fires a desktop notification.

Designed to run unattended (Task Scheduler / cron). The Claude session is
cold each run - the prompt below is self-contained.

Usage:
    python tools/weekly_check.py
    python tools/weekly_check.py --dry-run     # print prompt, don't call claude
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from freqtrade_client import REPO_ROOT
from notify import notify

LOG_DIR = REPO_ROOT / "user_data" / "logs"

PROMPT = f"""You are running an unattended weekly health check on the AI-Trader bot.
Repository root: {REPO_ROOT}
Working directory is the repo root.

Inspect these sources and produce a short markdown digest:

1. **Container:** `docker ps --filter "name=ai-trader" --format "{{{{.Names}}}}\\t{{{{.Status}}}}"` - confirm container is up.
2. **Trade count + P&L:** read `user_data/tradesv3.dryrun.sqlite` with sqlite3 - total trades, open trades, total closed P&L, first/last open_date.
3. **Regime mix (last 168h):** for each pair in user_data/config.json's `exchange.pair_whitelist`, call the Freqtrade REST API (see tools/freqtrade_client.py for auth pattern) at `/api/v1/pair_candles?pair=<pair>&timeframe=1h&limit=200` and tally the `regime` column over the last 168 candles. Also count `enter_long`/`enter_short` bars in that window.
4. **Daily-report cron health:** list `user_data/logs/report_*.txt` files and check that the last 7 daily dates are present. Missing files = cron is broken.

Then judge against these flags and call them out explicitly if hit:

- Container not "Up" -> CRITICAL
- Zero trades AND zero entry-bars across all pairs for >7 days -> flag "strategy may be too tight, consider relaxing entry filters or adding pullback-in-trend entries"
- Daily report files missing for any of the last 3 days -> flag "daily report cron broken"
- ADX > 25 in a pair currently classified as CHOPPY -> flag "classifier/ADX mismatch - worth inspecting regime_classifier.py"
- Wallet balance drift from $100 by more than 5% in dry-run -> suspicious (paper account shouldn't move that fast)

Output format (markdown, terse - this gets read at a glance):

```
# AI-Trader Weekly Check - <YYYY-MM-DD>

## Status
- Container: <up/down + duration>
- Trades: <N total, N open, P&L>
- Daily reports: <N/7 present>

## Regime mix (last 168h)
| Pair | Now | Mix | Entry bars |
|---|---|---|---|
...

## Flags
- <list of flags hit, or "None - bot operating within expected bounds">

## Recommendation
<one or two sentences>
```

Do not propose code changes. Do not run any commands beyond reads (docker ps, sqlite SELECT, REST GET, file reads). Be terse - this is a digest, not a report.
"""


def find_claude() -> str | None:
    return shutil.which("claude")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the prompt and exit")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="Max seconds to wait for claude (default: 600)",
    )
    args = parser.parse_args()

    if args.dry_run:
        print(PROMPT)
        return 0

    claude_bin = find_claude()
    if not claude_bin:
        print("error: `claude` not on PATH", file=sys.stderr)
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LOG_DIR / f"ai_check_{datetime.now().strftime('%Y-%m-%d')}.md"

    cmd = [
        claude_bin,
        "-p",
        PROMPT,
        "--allowed-tools",
        "Read,Bash,Glob,Grep",
        "--output-format",
        "text",
    ]

    print(f"Running weekly check via {claude_bin} (timeout {args.timeout_seconds}s)...")
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=args.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"error: claude timed out after {args.timeout_seconds}s", file=sys.stderr)
        notify("AI-Trader Weekly Check", "FAILED: claude timed out")
        return 2

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:500]
        print(f"error: claude exited {result.returncode}: {stderr}", file=sys.stderr)
        notify("AI-Trader Weekly Check", f"FAILED (exit {result.returncode})")
        return result.returncode

    out_path.write_text(result.stdout, encoding="utf-8")

    first_flags_line = ""
    in_flags = False
    for line in result.stdout.splitlines():
        if line.strip().startswith("## Flags"):
            in_flags = True
            continue
        if in_flags and line.strip().startswith("-"):
            first_flags_line = line.strip().lstrip("- ").strip()
            break
        if in_flags and line.strip().startswith("##"):
            break

    summary = first_flags_line or "No flags raised."
    notify("AI-Trader Weekly Check", summary[:200])
    print(f"Digest written: {out_path}")
    print(f"Summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
