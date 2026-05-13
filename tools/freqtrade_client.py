"""
Minimal Freqtrade REST client used by tools/monitor.py and tools/daily_report.py.

Reads connection details from ../user_data/config.json (the same file the bot
itself uses) so credentials live in one place and never leak into source control.
Uses only the Python standard library — no third-party dependencies.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "user_data" / "config.json"


class FreqtradeClientError(RuntimeError):
    pass


@dataclass
class Settings:
    api_base: str
    username: str
    password: str
    pairs: list[str]


def load_settings(config_path: Path = CONFIG_PATH) -> Settings:
    if not config_path.exists():
        raise FreqtradeClientError(
            f"Config not found at {config_path}. Copy user_data/config.example.json "
            f"to user_data/config.json and fill in the api_server section."
        )
    with config_path.open("r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    api_server = cfg.get("api_server", {})
    if not api_server.get("enabled", False):
        raise FreqtradeClientError(
            "config.json -> api_server.enabled is false; the monitor needs the API."
        )

    host = api_server.get("listen_ip_address", "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = int(api_server.get("listen_port", 8080))

    username = api_server.get("username") or ""
    password = api_server.get("password") or ""
    if not username or not password:
        raise FreqtradeClientError(
            "config.json -> api_server.username/password must be set "
            "(see user_data/config.example.json)."
        )

    pairs = list(cfg.get("exchange", {}).get("pair_whitelist") or [])
    if not pairs:
        raise FreqtradeClientError(
            "config.json -> exchange.pair_whitelist is empty; nothing to monitor."
        )

    return Settings(
        api_base=f"http://{host}:{port}/api/v1",
        username=username,
        password=password,
        pairs=pairs,
    )


class FreqtradeClient:
    def __init__(self, settings: Settings, timeout: float = 10.0) -> None:
        self._s = settings
        self._timeout = timeout
        self._token: str | None = None

    def _basic_auth(self) -> str:
        raw = f"{self._s.username}:{self._s.password}".encode("ascii")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _login(self) -> str:
        url = f"{self._s.api_base}/token/login"
        req = urllib.request.Request(
            url, method="POST", headers={"Authorization": self._basic_auth()}
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise FreqtradeClientError(
                f"Login failed ({e.code}): check api_server credentials in config.json"
            ) from e
        except urllib.error.URLError as e:
            raise FreqtradeClientError(
                f"Cannot reach Freqtrade API at {self._s.api_base}: {e.reason}"
            ) from e
        token = payload.get("access_token")
        if not token:
            raise FreqtradeClientError("Login response did not include access_token.")
        return token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._token is None:
            self._token = self._login()
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        url = f"{self._s.api_base}{path}{qs}"
        for attempt in range(2):
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {self._token}"}
            )
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self._token = self._login()
                    continue
                raise FreqtradeClientError(f"GET {path} failed ({e.code})") from e
            except urllib.error.URLError as e:
                raise FreqtradeClientError(
                    f"GET {path} failed: {e.reason}"
                ) from e
        raise FreqtradeClientError(f"GET {path} failed after retry")

    # Endpoint wrappers (only the ones the monitor / report actually use)
    def balance(self) -> dict[str, Any]:
        return self._get("/balance")

    def profit(self) -> dict[str, Any]:
        return self._get("/profit")

    def status(self) -> list[dict[str, Any]]:
        result = self._get("/status")
        return result if isinstance(result, list) else []

    def trades(self, limit: int = 100) -> dict[str, Any]:
        return self._get("/trades", {"limit": limit})

    def pair_candles(
        self, pair: str, timeframe: str = "1h", limit: int = 1
    ) -> dict[str, Any]:
        return self._get(
            "/pair_candles",
            {"pair": pair, "timeframe": timeframe, "limit": limit},
        )

    @property
    def pairs(self) -> list[str]:
        return list(self._s.pairs)
