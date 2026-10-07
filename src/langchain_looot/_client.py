"""Minimal looot REST client on httpx. Sync and async.

Endpoints and fields come from https://api.looot.ai/openapi.json.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote

import httpx

DEFAULT_BASE_URL = "https://api.looot.ai"


class LoootError(Exception):
    """A non-2xx answer. `code` is the gateway's error code, e.g. insufficient_balance."""

    def __init__(self, status: int, code: str, message: str, request_id: Optional[str] = None) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.request_id = request_id


def _clean(params: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        out[key] = ("true" if value else "false") if isinstance(value, bool) else value
    return out


def _check(response: httpx.Response, method: str, path: str) -> Any:
    try:
        data: Any = response.json() if response.content else None
    except ValueError:
        data = response.text
    if response.is_error:
        err = data.get("error") if isinstance(data, dict) else None
        err = err if isinstance(err, dict) else {}
        raise LoootError(
            response.status_code,
            err.get("code") or f"http_{response.status_code}",
            err.get("message") or f"{method} {path} failed with HTTP {response.status_code}",
            err.get("requestId"),
        )
    return data


class LoootClient:
    """Holds the token and base URL. `transport` lets tests swap in httpx.MockTransport."""

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: float = 75.0,
        transport: Optional[httpx.BaseTransport] = None,
        async_transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.token = (token if token is not None else os.environ.get("LOOOT_TOKEN", "")).strip() or None
        self.base_url = (base_url or os.environ.get("LOOOT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._transport = transport
        self._async_transport = async_transport if async_transport is not None else transport

    def _headers(self, method: str, path: str) -> Dict[str, str]:
        if not self.token:
            raise LoootError(401, "missing_token", f"{method} {path} needs a token: set LOOOT_TOKEN or pass token=")
        return {"accept": "application/json", "authorization": f"Bearer {self.token}"}

    def request(
        self, method: str, path: str, *, params: Optional[Mapping[str, Any]] = None, json: Any = None
    ) -> Any:
        headers = self._headers(method, path)
        with httpx.Client(base_url=self.base_url, timeout=self.timeout, transport=self._transport) as http:
            response = http.request(method, path, params=_clean(params or {}), json=json, headers=headers)
        return _check(response, method, path)

    async def arequest(
        self, method: str, path: str, *, params: Optional[Mapping[str, Any]] = None, json: Any = None
    ) -> Any:
        headers = self._headers(method, path)
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self._async_transport
        ) as http:
            response = await http.request(method, path, params=_clean(params or {}), json=json, headers=headers)
        return _check(response, method, path)


def path_segment(value: str) -> str:
    """Percent-encodes one path segment (endpoint ids such as job:people.email.find)."""
    return quote(value, safe="")
