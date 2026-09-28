"""
Async client for the Meta Marketing API (Graph API).

Everything the audit needs is a GET -- this never writes to an ad account.
Two things it wraps that raw httpx does not give you:

  - cursor pagination, which every Graph edge uses and which you hit the
    moment an account has more than 25 campaigns
  - Meta's error envelope, which arrives as HTTP 400 with the real reason
    buried in body["error"]["message"]; the status code alone tells you
    nothing, and rate limits come back as codes 17 / 613 / 80004
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..config import get_settings

GRAPH_HOST = "https://graph.facebook.com"

RATE_LIMIT_CODES = {4, 17, 32, 613, 80000, 80003, 80004, 80005, 80006, 80008}
AUTH_CODES = {102, 190}

# Ceiling on concurrent Graph requests, process-wide.
#
# An audit needs ~10 insights calls. Firing them all at once with
# asyncio.gather reliably trips Meta's *user-level* limit (code 17 / subcode
# 2446079) even while the ad account's own budget sits near 50% -- the burst
# shape is what it objects to, not the total. Three at a time keeps the audit
# nearly as fast (the calls still overlap) without the burst, and a throttle
# here is far cheaper than the 35s of backoff one 429 costs.
_CONCURRENCY = asyncio.Semaphore(3)


class MetaApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
        subcode: int | None = None,
        fbtrace_id: str | None = None,
        path: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.subcode = subcode
        self.fbtrace_id = fbtrace_id
        self.path = path

    @property
    def is_rate_limit(self) -> bool:
        return self.code in RATE_LIMIT_CODES

    @property
    def is_auth_error(self) -> bool:
        return self.code in AUTH_CODES or self.status == 401


def _token_and_version() -> tuple[str, str]:
    settings = get_settings()
    if not settings.meta_access_token:
        raise MetaApiError(
            "META_ACCESS_TOKEN is not set. Copy .env.example to .env.local and fill it in."
        )
    return settings.meta_access_token, settings.graph_api_version


async def graph_get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    retries: int = 3,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """One GET against the Graph API, retrying rate limits and 5xx with backoff.

    Meta's throttling is per-app-per-hour and recovers on its own, so backing
    off is genuinely the fix here rather than a way of hiding a bug.
    """
    token, version = _token_and_version()
    clean = path.lstrip("/")
    url = f"{GRAPH_HOST}/{version}/{clean}"

    query: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None or value == "":
            continue
        query[key] = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    query["access_token"] = token

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=60.0)
    last_error: MetaApiError | None = None

    try:
        for attempt in range(retries + 1):
            try:
                async with _CONCURRENCY:
                    response = await client.get(url, params=query)
            except httpx.HTTPError as cause:
                last_error = MetaApiError(f"Network error calling {clean}: {cause}", path=clean)
                if attempt < retries:
                    await asyncio.sleep(2**attempt)
                    continue
                raise last_error from cause

            try:
                body = response.json()
            except ValueError:
                body = {}

            if response.is_success and "error" not in body:
                return body

            error = body.get("error", {})
            last_error = MetaApiError(
                error.get("message") or f"Graph API returned {response.status_code}",
                status=response.status_code,
                code=error.get("code"),
                subcode=error.get("error_subcode"),
                fbtrace_id=error.get("fbtrace_id"),
                path=clean,
            )

            retryable = last_error.is_rate_limit or response.status_code >= 500
            if retryable and attempt < retries:
                # Rate limits need a real pause, not a 1s gesture.
                base = 5 if last_error.is_rate_limit else 1
                await asyncio.sleep(base * (2**attempt))
                continue
            raise last_error
    finally:
        if owns_client:
            await client.aclose()

    raise last_error or MetaApiError("Unknown Graph API failure", path=clean)


async def graph_get_all(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    max_pages: int = 25,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Walk every page of an edge and return the flattened rows.

    ``max_pages`` is a guard rail rather than a preference: an account with
    thousands of ads would otherwise spend the whole request budget paginating.
    """
    params = {"limit": 100, **(params or {})}
    rows: list[dict[str, Any]] = []

    page = await graph_get(path, params, client=client)
    rows.extend(page.get("data", []))

    for _ in range(max_pages - 1):
        after = (page.get("paging", {}).get("cursors", {}) or {}).get("after")
        if not page.get("paging", {}).get("next") or not after:
            break
        page = await graph_get(path, {**params, "after": after}, client=client)
        data = page.get("data", [])
        if not data:
            break
        rows.extend(data)

    return rows


async def debug_token(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Who does this token belong to, and what can it still do?"""
    token, _ = _token_and_version()
    body = await graph_get("debug_token", {"input_token": token}, client=client)
    data = body.get("data", {})
    return {
        "appId": data.get("app_id"),
        "appName": data.get("application"),
        "userId": data.get("user_id"),
        "isValid": data.get("is_valid"),
        "scopes": data.get("scopes", []),
        "expiresAt": data.get("expires_at"),
        "dataAccessExpiresAt": data.get("data_access_expires_at"),
    }
