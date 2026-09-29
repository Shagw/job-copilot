"""Tiny in-memory sliding-window rate limiter (per IP, per route group).

Good enough for a single-process app. With multiple workers this would move to Redis.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.max_requests:
                retry = int(self.window - (now - hits[0])) + 1
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many attempts. Try again later.",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# 10 login attempts / 5 min and 20 OTP actions / 10 min per IP.
login_limiter = RateLimiter(max_requests=10, window_seconds=300)
otp_limiter = RateLimiter(max_requests=20, window_seconds=600)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def limit_login(request: Request) -> None:
    login_limiter.check(_client_ip(request))


def limit_otp(request: Request) -> None:
    otp_limiter.check(_client_ip(request))
