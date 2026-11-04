"""In-memory abuse protection (single process): rate limits, login lockout, locks."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class SlidingWindowLimiter:
    """At most `limit` hits per `window_seconds` for each key (e.g. an IP)."""

    def __init__(self, limit: int, window_seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int]:
        """Record a hit. Returns (allowed, seconds until the next hit is allowed)."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(1, int(hits[0] + self.window - now) + 1)
            hits.append(now)
            if len(self._hits) > 10_000:
                self._prune(now)
            return True, 0

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class LoginGuard:
    """Lock an IP out for `lock_seconds` after `max_failures` wrong passwords."""

    def __init__(self, max_failures: int = 5, lock_seconds: int = 900, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if t > now - self.lock_seconds]
        self._failures[key] = recent
        return recent

    def locked_for(self, key: str) -> int:
        now = self._clock()
        with self._lock:
            recent = self._recent(key, now)
            if len(recent) < self.max_failures:
                return 0
            return max(1, int(recent[-self.max_failures] + self.lock_seconds - now) + 1)

    def failure(self, key: str) -> int:
        """Record a failure; returns the attempts left before the lockout."""
        now = self._clock()
        with self._lock:
            recent = self._recent(key, now)
            recent.append(now)
            return max(0, self.max_failures - len(recent))

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


class KeyedLocks:
    """One lock per key, so a chat session never runs two turns at once."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
            if len(self._locks) > 5_000:
                for k in [k for k, v in self._locks.items() if not v.locked() and k != key]:
                    del self._locks[k]
        with lock:
            yield
