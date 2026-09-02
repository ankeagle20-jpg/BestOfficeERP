# -*- coding: utf-8 -*-
"""Host / debug-header → kiracı slug; session kilidi. DNS yok."""
from __future__ import annotations

import os
import re

from flask import current_app, g, has_request_context, jsonify, redirect, request, session
from flask_login import logout_user

from db import _TENANT_SCHEMA_RE
from tenant_reserved_slugs import RESERVED_TENANT_SLUGS
TENANT_HEADER = "X-BestOffice-Tenant"
_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

# Kiracı slug YALNIZ bu apex'lerin bir alt adında: acme.bestofficeerp.com / acme.payafin.com
# www.payafin.com marketing host'tur (MARKETING_APEX_HOSTS); tenant apex DEĞİL.
_DEFAULT_TENANT_APEX = "payafin.com,bestofficeerp.com,bestofficeerp.local"
# Bu sonekte (PaaS) asla tenant çıkarılmaz: bestofficeerp.onrender.com
_DEFAULT_NO_TENANT_SUFFIXES = "onrender.com"
_DEFAULT_PUBLIC_HOSTS = "bestofficeerp.onrender.com"


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    raw = (os.environ.get(name) or default or "")
    return tuple(x.strip().lower() for x in raw.split(",") if x.strip())


def _tenant_apex_domains() -> tuple[str, ...]:
    return _csv_env("TENANT_APEX_DOMAINS", _DEFAULT_TENANT_APEX)


def _no_tenant_suffixes() -> tuple[str, ...]:
    return _csv_env("NO_TENANT_HOST_SUFFIXES", _DEFAULT_NO_TENANT_SUFFIXES)


def _public_hosts() -> tuple[str, ...]:
    return _csv_env("PUBLIC_HOSTS", _DEFAULT_PUBLIC_HOSTS)


def _marketing_apex_hosts() -> tuple[str, ...]:
    return _csv_env("MARKETING_APEX_HOSTS", "payafin.com,www.payafin.com")


def is_payafin_marketing_host(host: str | None) -> bool:
    """payafin.com / www.payafin.com marketing ana sayfa host'ları."""
    raw = _normalize_host(host)
    if not raw:
        return False
    return raw in _marketing_apex_hosts()


def schema_name_for_slug(slug: str | None) -> str | None:
    if not slug:
        return None
    schema = "tenant_" + str(slug).strip().lower()
    if not _TENANT_SCHEMA_RE.fullmatch(schema):
        return None
    return schema


def _normalize_host(host: str | None) -> str:
    raw = (host or "").split("/")[0].strip().lower()
    if ":" in raw and not raw.startswith("["):
        raw = raw.rsplit(":", 1)[0]
    if raw.startswith("[") and "]" in raw:
        raw = raw[1:raw.index("]")]
    return raw


def _host_matches_suffix(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _subdomain_from_host(host: str | None) -> str | None:
    """Yalnız {slug}.{TENANT_APEX}. PaaS / bilinmeyen host → None (public, fail-closed)."""
    raw = _normalize_host(host)
    if not raw or raw in ("localhost", "127.0.0.1", "::1"):
        return None
    if _IPV4_RE.fullmatch(raw):
        return None
    if raw in _public_hosts():
        return None
    for sfx in _no_tenant_suffixes():
        if _host_matches_suffix(raw, sfx):
            return None
    for apex in _tenant_apex_domains():
        if raw == apex:
            return None
        suffix = "." + apex
        if not raw.endswith(suffix):
            continue
        rest = raw[: -len(suffix)]
        if not rest or "." in rest:
            return None
        if rest in RESERVED_TENANT_SLUGS:
            return None
        if not _SLUG_RE.fullmatch(rest):
            return None
        return rest
    return None


def resolve_tenant_slug(*, debug: bool | None = None) -> str | None:
    """Ana domain / www / app / IP → None. Kiracı host veya debug header → slug."""
    if not has_request_context():
        return None
    if debug is None:
        try:
            debug = bool(current_app.debug)
        except Exception:
            debug = False
    slug = _subdomain_from_host(request.host or request.headers.get("Host"))
    if debug:
        hdr = (request.headers.get(TENANT_HEADER) or "").strip().lower()
        if hdr:
            if hdr.startswith("tenant_"):
                hdr = hdr[len("tenant_"):]
            if _SLUG_RE.fullmatch(hdr) and hdr not in RESERVED_TENANT_SLUGS:
                slug = hdr
    if not slug:
        return None
    if schema_name_for_slug(slug) is None:
        return None
    return slug


def bind_request_tenant():
    """before_request: g.tenant_schema (yalnız kiracı) + session↔Host kilidi."""
    if not has_request_context():
        return None
    slug = resolve_tenant_slug()
    schema = schema_name_for_slug(slug)
    if schema is not None:
        g.tenant_schema = schema
    else:
        # Public/apex host: önceki request'ten (veya test app_context'ten) kalmış
        # tenant_schema sızıntısını temizle — platform-only guard'lar buna bağlı.
        g.pop("tenant_schema", None)
    g.tenant_slug = slug or ""

    if "_user_id" not in session and "tenant_slug" not in session:
        return None
    stored = session.get("tenant_slug")
    stored_norm = "" if stored is None else str(stored)
    resolved_norm = slug or ""
    if stored_norm == resolved_norm:
        return None
    # Güvenlik: uyuşmayan oturumu İPTAL ET (başka kiracı/host cookie'si kullanılamaz).
    # UX: 403 ile kilitlenmek yerine temiz session ile /login'e yönlendir —
    # böylece /login dahil tüm sayfalar "engel" olmadan yeniden girişe açılır.
    try:
        logout_user()
    except Exception:
        pass
    session.clear()
    session.modified = True
    path = (request.path or "").rstrip("/") or "/"
    # Zaten giriş/çıkış sayfasındaysak redirect döngüsü yok; temiz session ile devam.
    if path in ("/login", "/logout"):
        return None
    if "/api/" in (request.path or ""):
        return (
            jsonify({
                "ok": False,
                "mesaj": "Oturum kiracı uyuşmazlığı; oturum temizlendi. Tekrar giriş yapın.",
                "login_url": "/login",
            }),
            401,
        )
    return redirect("/login")


def stamp_session_tenant_slug() -> None:
    """login_user sonrası: o an çözülen slug (public için boş string)."""
    if not has_request_context():
        return
    session["tenant_slug"] = resolve_tenant_slug() or ""
    session.modified = True
