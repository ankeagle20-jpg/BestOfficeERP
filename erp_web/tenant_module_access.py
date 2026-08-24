# -*- coding: utf-8 -*-
"""Platform modül entitlement okuma katmanı + module_required dekoratörü (Faz 2).

Sözleşme:
- has_module_entitlement(tenant_id, module_key) → bool
- 60 sn bellek içi cache (cache_utils / pricing_public_cache deseni)
- invalidate_module_entitlement_cache(tenant_id, module_key=None)
- module_required(module_key) → yoksa 403 (JSON/HTML, platform_public_only deseni)

Not: Bu aşamada dekoratör HİÇBİR üretim route'una uygulanmaz (Faz 3).
g.tenant_schema yoksa (ana şirket / public host) → public.tenants slug/schema='public'.
"""
from __future__ import annotations

import logging
from functools import wraps

from flask import g, jsonify, request

from cache_utils import (
    _SIMPLE_CACHE,
    simple_cache_get,
    simple_cache_invalidate,
    simple_cache_set,
)
from db import fetch_one

logger = logging.getLogger(__name__)

MODULE_ENTITLEMENT_CACHE_TTL_SEC = 60.0
_CACHE_PREFIX = "modent:v1:"

MSG_MODULE_DENIED = "Bu modül hesabınızda aktif değil."
MSG_TENANT_UNRESOLVED = "Kiracı kimliği çözülemedi; modül erişimi reddedildi."


def module_entitlement_cache_key(tenant_id: int, module_key: str) -> str:
    return f"{_CACHE_PREFIX}{int(tenant_id)}:{str(module_key or '').strip()}"


def invalidate_module_entitlement_cache(
    tenant_id: int,
    module_key: str | None = None,
) -> None:
    """Admin panelinden entitlement değişince çağrılacak.

    module_key verilirse yalnız o anahtar; None ise tenant_id'nin tüm
    modül cache girdileri silinir.
    """
    tid = int(tenant_id)
    if module_key is not None and str(module_key).strip():
        simple_cache_invalidate(module_entitlement_cache_key(tid, str(module_key).strip()))
        return
    prefix = f"{_CACHE_PREFIX}{tid}:"
    for key in list(_SIMPLE_CACHE.keys()):
        if isinstance(key, str) and key.startswith(prefix):
            simple_cache_invalidate(key)


def _load_entitlement_from_db(tenant_id: int, module_key: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS ok
        FROM public.tenant_module_entitlements
        WHERE tenant_id = %s
          AND module_key = %s
          AND status IN ('trial', 'active')
          AND starts_at <= NOW()
          AND (ends_at IS NULL OR ends_at >= NOW())
        LIMIT 1
        """,
        (int(tenant_id), str(module_key).strip()),
    )
    return bool(row)


def has_module_entitlement(tenant_id: int, module_key: str) -> bool:
    """Kiracının verilen modüle geçerli (trial/active + tarih penceresi) hakkı var mı?"""
    tid = int(tenant_id)
    mk = str(module_key or "").strip()
    if not mk:
        return False

    key = module_entitlement_cache_key(tid, mk)
    cached = simple_cache_get(key, max_age_sec=MODULE_ENTITLEMENT_CACHE_TTL_SEC)
    if cached is not None:
        return bool(cached)

    allowed = _load_entitlement_from_db(tid, mk)
    simple_cache_set(key, allowed)
    return allowed


def resolve_request_tenant_id() -> int | None:
    """g.tenant_schema → public.tenants.id; yoksa platform sahibi (public) satırı."""
    schema = getattr(g, "tenant_schema", None)
    if schema:
        row = fetch_one(
            """
            SELECT id
            FROM public.tenants
            WHERE schema_name = %s AND status = 'active'
            LIMIT 1
            """,
            (str(schema),),
        )
        if row:
            return int(row["id"])
        return None

    row = fetch_one(
        """
        SELECT id
        FROM public.tenants
        WHERE (slug = 'public' OR schema_name = 'public')
          AND status = 'active'
        LIMIT 1
        """
    )
    if row:
        return int(row["id"])
    return None


def _deny_response(message: str):
    path = request.path or ""
    if "/api/" in path or request.is_json or (
        request.accept_mimetypes.best == "application/json"
    ):
        return jsonify({"ok": False, "mesaj": message}), 403
    return message, 403


def module_required(module_key: str):
    """Route guard: geçerli entitlement yoksa 403.

    Faz 3'e kadar üretim route'larına uygulanmaz — yalnızca altyapı sözleşmesi.
    """
    mk = str(module_key or "").strip()

    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not mk:
                logger.warning("module_required empty module_key path=%s", request.path)
                return _deny_response(MSG_MODULE_DENIED)

            tenant_id = resolve_request_tenant_id()
            if tenant_id is None:
                logger.warning(
                    "module_required tenant unresolved module=%s path=%s schema=%s",
                    mk,
                    request.path,
                    getattr(g, "tenant_schema", None),
                )
                return _deny_response(MSG_TENANT_UNRESOLVED)

            if not has_module_entitlement(tenant_id, mk):
                logger.info(
                    "module_required denied tenant_id=%s module=%s path=%s",
                    tenant_id,
                    mk,
                    request.path,
                )
                return _deny_response(MSG_MODULE_DENIED)

            return f(*args, **kwargs)

        return wrapped

    return decorator
