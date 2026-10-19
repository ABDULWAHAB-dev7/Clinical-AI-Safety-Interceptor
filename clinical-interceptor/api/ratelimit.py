"""Per-client rate limit (API.md 13: default 60 requests per minute; quotas are an open item).

Fixed one-minute windows per client identity. In-memory, so each worker counts separately: behind several workers the
effective limit is higher. A shared limiter (for example Redis) is deferred.
"""

import math
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateStatus:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int  # seconds until the window resets


class RateLimiter:
    def __init__(self, per_minute=60, clock=time.monotonic):
        if per_minute < 1:
            raise ValueError("per_minute must be at least 1")
        self.per_minute = per_minute
        self.window = 60.0
        self.clock = clock
        self._lock = threading.Lock()
        self._state = {}  # client_id -> (window_start, count)

    def hit(self, client_id):
        now = self.clock()
        with self._lock:
            start, count = self._state.get(client_id, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            reset = max(1, math.ceil(self.window - (now - start)))
            if count >= self.per_minute:
                self._state[client_id] = (start, count)
                return RateStatus(False, self.per_minute, 0, reset)
            count += 1
            self._state[client_id] = (start, count)
            return RateStatus(True, self.per_minute, self.per_minute - count, reset)
