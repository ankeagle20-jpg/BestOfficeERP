# -*- coding: utf-8 -*-
"""Payafin module-pricing contact API — IP bazlı bellek içi hız sınırı."""
from __future__ import annotations

import threading
import time

from flask import request

_CONTACT_POST_LIMIT = 5
_CONTACT_POST_WINDOW_SEC = 3600.0

_LOCK = threading.Lock()
_BUCKETS: dict[str, list[float]] = {}


def client_ip() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return (request.remote_addr or "unknown").strip() or "unknown"


def _prune(key: str, window_sec: float, now: float) -> list[float]:
    hits = _BUCKETS.get(key, [])
    cutoff = now - window_sec
    hits = [t for t in hits if t > cutoff]
    _BUCKETS[key] = hits
    return hits


def _check_limit(key: str, limit: int, window_sec: float) -> tuple[bool, int]:
    now = time.time()
    with _LOCK:
        hits = _prune(key, window_sec, now)
        if len(hits) >= limit:
            return False, 0
        hits.append(now)
        _BUCKETS[key] = hits
        retry_after = int(max(1, window_sec - (now - hits[0])))
        return True, retry_after


def check_module_pricing_contact_rate(ip: str | None = None) -> tuple[bool, int]:
    """POST /api/module-pricing/contact: saatte 5 istek / IP."""
    addr = ip or client_ip()
    return _check_limit(
        f"module_pricing:contact:{addr}",
        _CONTACT_POST_LIMIT,
        _CONTACT_POST_WINDOW_SEC,
    )


def reset_module_pricing_contact_rate_limits_for_tests() -> None:
    """Test harness: bellek içi sayaçları sıfırla."""
    with _LOCK:
        _BUCKETS.clear()
