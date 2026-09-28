"""
In-process TTL cache for Meta responses.

This is not a performance nicety. The app is on Meta's `development_access`
tier, whose ads_insights budget is small enough that a few page loads push
`total_time` past 50% and Graph starts throttling -- at which point the client's
backoff turns a 5s audit into a 38s one. One audit is ~10 insights calls, so
caching the assembled result is the difference between browsing the dashboard
freely and burning the hourly budget on tab switches.

Per-key locks mean ten concurrent requests for the same account collapse into
one upstream fetch rather than ten (the cache-stampede case, which is exactly
what a page with several components mounting at once would cause).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

DEFAULT_TTL = 300.0  # 5 minutes; ad stats do not move faster than this


class TTLCache:
    def __init__(self, ttl: float = DEFAULT_TTL, max_entries: int = 64):
        self.ttl = ttl
        self.max_entries = max_entries
        self._store: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [k for k, (stamp, _) in self._store.items() if now - stamp > self.ttl]
        for key in expired:
            self._store.pop(key, None)
            self._locks.pop(key, None)
        # Hard ceiling so a long-running process cannot grow without bound.
        while len(self._store) > self.max_entries:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
            self._locks.pop(oldest, None)

    def peek(self, key: str) -> tuple[Any, float] | None:
        """Cached value plus its age in seconds, or None."""
        entry = self._store.get(key)
        if not entry:
            return None
        stamp, value = entry
        age = time.monotonic() - stamp
        if age > self.ttl:
            self._store.pop(key, None)
            return None
        return value, age

    async def get_or_set(
        self, key: str, producer: Callable[[], Awaitable[Any]], *, fresh: bool = False
    ) -> tuple[Any, float]:
        """Returns (value, age_seconds). age 0.0 means it was just fetched."""
        if not fresh:
            hit = self.peek(key)
            if hit:
                return hit

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Another waiter may have filled it while we queued for the lock.
            if not fresh:
                hit = self.peek(key)
                if hit:
                    return hit
            value = await producer()
            self._store[key] = (time.monotonic(), value)
            self._prune()
            return value, 0.0

    def clear(self) -> None:
        self._store.clear()
        self._locks.clear()


audit_cache = TTLCache(ttl=DEFAULT_TTL)
breakdown_cache = TTLCache(ttl=DEFAULT_TTL)
