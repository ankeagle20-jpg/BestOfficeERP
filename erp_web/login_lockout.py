# -*- coding: utf-8 -*-
"""Bellek içi giriş kilidi — kiracı+kullanıcı bazlı ardışık başarısız deneme."""
from __future__ import annotations

import threading
import time

_MAX_FAILURES = 10
_LOCKOUT_SEC = 15 * 60.0

_LOCK = threading.Lock()
_STATE: dict[str, dict[str, float | int]] = {}


class LoginLockedOut(Exception):
    """Kullanıcı geçici olarak kilitlendi."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _key(tenant_schema: str | None, username: str) -> str:
    tenant = str(tenant_schema or "public").strip() or "public"
    user = str(username or "").strip().lower()
    return f"{tenant}:{user}"


def _lockout_message(locked_until: float, now: float) -> str:
    remaining_min = max(1, int((locked_until - now + 59) // 60))
    return (
        f"Çok fazla başarısız deneme. "
        f"{remaining_min} dakika sonra tekrar deneyin."
    )


def check_login_lockout(tenant_schema: str | None, username: str) -> None:
    """Kilitliyse LoginLockedOut fırlatır."""
    key = _key(tenant_schema, username)
    now = time.time()
    with _LOCK:
        st = _STATE.get(key)
        if not st:
            return
        locked_until = float(st.get("locked_until") or 0)
        if locked_until > now:
            raise LoginLockedOut(_lockout_message(locked_until, now))
        if locked_until > 0:
            _STATE.pop(key, None)


def record_login_failure(tenant_schema: str | None, username: str) -> None:
    key = _key(tenant_schema, username)
    now = time.time()
    with _LOCK:
        st = _STATE.get(key) or {"failures": 0, "locked_until": 0.0}
        locked_until = float(st.get("locked_until") or 0)
        if locked_until > now:
            return
        failures = int(st.get("failures") or 0) + 1
        new_locked = locked_until
        if failures >= _MAX_FAILURES:
            new_locked = now + _LOCKOUT_SEC
        _STATE[key] = {"failures": failures, "locked_until": new_locked}


def record_login_success(tenant_schema: str | None, username: str) -> None:
    key = _key(tenant_schema, username)
    with _LOCK:
        _STATE.pop(key, None)


def reset_login_lockouts_for_tests() -> None:
    with _LOCK:
        _STATE.clear()
