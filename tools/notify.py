"""
Cross-platform desktop notification helper.

- Windows: invokes BurntToast via PowerShell when available; silently no-ops if not.
- Linux:   invokes `notify-send` when available; silently no-ops if not.
- Other:   silently no-ops.

Notifications are best-effort. They never raise — if the notification system
isn't installed or isn't reachable (no display, headless server, etc.), the
caller's main work continues uninterrupted.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess

_IS_WINDOWS = platform.system() == "Windows"
_IS_LINUX = platform.system() == "Linux"


def notify(title: str, body: str) -> None:
    """Show a desktop notification. Never raises."""
    try:
        if _IS_WINDOWS:
            _notify_windows(title, body)
        elif _IS_LINUX:
            _notify_linux(title, body)
    except Exception:
        # Silent: a failed toast must never break the monitor loop.
        pass


def _notify_windows(title: str, body: str) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        return
    safe_title = title.replace("'", "''")
    safe_body = body.replace("'", "''")
    script = (
        "if (Get-Module -ListAvailable -Name BurntToast) {"
        " Import-Module BurntToast;"
        f" New-BurntToastNotification -Text '{safe_title}', '{safe_body}'"
        "}"
    )
    subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        timeout=10,
    )


def _notify_linux(title: str, body: str) -> None:
    notify_send = shutil.which("notify-send")
    if not notify_send:
        return
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return  # Headless session, nothing to display to.
    subprocess.run(
        [notify_send, title, body],
        check=False,
        capture_output=True,
        timeout=5,
    )
