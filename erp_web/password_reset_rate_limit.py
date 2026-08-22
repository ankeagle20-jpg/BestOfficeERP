# -*- coding: utf-8 -*-
"""Payafin forgot-password — IP + identifier bellek içi hız sınırı (signup deseni)."""
from __future__ import annotations

import threading
import time

from flask import request

_FORGOT_IP_LIMIT = 10
_FORGOT_IDENTIFIER_LIMIT = 3
_FORGOT_WINDOW_SEC = 3600.0

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


def check_forgot_password_ip_rate(ip: str | None = None) -> tuple[bool, int]:
    """POST /forgot-password: saatte 10 istek / IP."""
    addr = ip or client_ip()
    return _check_limit(
        f"pwd_reset:ip:{addr}",
        _FORGOT_IP_LIMIT,
        _FORGOT_WINDOW_SEC,
    )


def check_forgot_password_identifier_rate(
    identifier: str,
    ip: str | None = None,
) -> tuple[bool, int]:
    """POST /forgot-password: saatte 3 istek / normalize edilmiş identifier."""
    del ip  # identifier limiti IP'den bağımsız
    ident = (identifier or "").strip().lower()
    if not ident:
        ident = "_empty_"
    return _check_limit(
        f"pwd_reset:id:{ident}",
        _FORGOT_IDENTIFIER_LIMIT,
        _FORGOT_WINDOW_SEC,
    )


def reset_password_reset_rate_limits_for_tests() -> None:
    """Test harness: bellek içi sayaçları sıfırla."""
    with _LOCK:
        _BUCKETS.clear()
