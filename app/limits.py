"""Small in-process rate limiter.

Public endpoints (review submit, signup, login) are open to the whole internet once
this is deployed, so each one gets a ceiling. State lives in memory: it resets on
deploy and is per-machine, which is the right trade for this size of app - no Redis,
no extra moving part. If you ever run several machines, each enforces its own share.
"""
import threading
import time
from collections import defaultdict

from fastapi import Request

_hits: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()
_last_sweep = 0.0


def client_ip(request: Request) -> str:
    """Real client IP, trusting the proxy's own header when present.

    Behind Cloudflare or Fly every request arrives from the proxy, so without this
    the whole internet would share one rate-limit bucket. Each provider's dedicated
    header is preferred over x-forwarded-for, which anyone can spoof when there is
    no proxy in front.
    """
    forwarded = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("fly-client-ip")
        or request.headers.get("x-forwarded-for", "")
    )
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _sweep(now: float, window: float) -> None:
    """Drop keys nobody has touched recently so the dict cannot grow forever."""
    global _last_sweep
    if now - _last_sweep < 300:
        return
    _last_sweep = now
    for key in list(_hits):
        fresh = [t for t in _hits[key] if now - t < window]
        if fresh:
            _hits[key] = fresh
        else:
            del _hits[key]


def allow(key: str, limit: int, window_seconds: int) -> bool:
    """True if this key may act again; False once it has spent its allowance."""
    now = time.time()
    with _lock:
        _sweep(now, window_seconds)
        recent = [t for t in _hits[key] if now - t < window_seconds]
        if len(recent) >= limit:
            _hits[key] = recent
            return False
        recent.append(now)
        _hits[key] = recent
        return True
