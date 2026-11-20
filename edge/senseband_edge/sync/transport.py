"""HTTP transport to the cloud Edge Functions (stdlib only: runs on a Pi Zero too)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol


class TransportError(Exception):
    """Network down, timeout or a 5xx: retry later."""


class AuthError(Exception):
    """401/403: the device key is wrong or revoked. Retrying will not help until it is fixed."""


class Transport(Protocol):
    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def get(self, path: str, params: dict[str, str]) -> dict[str, Any]: ...


class HttpTransport:
    def __init__(self, base_url: str, device_key: str, timeout_s: float = 15.0):
        self.base, self.key, self.timeout = base_url.rstrip("/"), device_key, timeout_s

    def _call(self, req: urllib.request.Request) -> dict[str, Any]:
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - https URL from config
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError(f"HTTP {e.code}") from e
            if e.code in (400, 413):
                # our request is bad: retrying the same bytes will not help
                raise ValueError(f"HTTP {e.code}: {e.read()[:200]!r}") from e
            raise TransportError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            raise TransportError(str(e)) from e

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        return self._call(req)

    def get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        q = urllib.parse.urlencode(params)
        return self._call(urllib.request.Request(f"{self.base}{path}?{q}", method="GET"))
