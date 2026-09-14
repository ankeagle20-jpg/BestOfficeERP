# -*- coding: utf-8 -*-
"""Admin: Payafin platform kimlik bilgileri vault (yalnız public host, write-only)."""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from auth import admin_gerekli
from credentials_vault import (
    CATEGORY_LABELS,
    CredentialsVaultError,
    clear_credential,
    list_credential_status,
    set_credential,
)
from db import ensure_platform_credentials_table

logger = logging.getLogger(__name__)

bp = Blueprint("admin_credentials", __name__)

MSG_PLATFORM_ONLY = (
    "Platform kimlik bilgileri yalnızca ana (public) host'ta kullanılabilir."
)

CATEGORY_ORDER = ("mail", "gib", "ai", "robot", "ops", "paytr")


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_credentials_admin(f):
    """@admin_gerekli + kiracı subdomain'inde 403 (platform-only)."""

    @wraps(f)
    def _tenant_guard(*args, **kwargs):
        if getattr(g, "tenant_schema", None):
            path = request.path or ""
            if "/api/" in path or request.is_json or (
                request.accept_mimetypes.best == "application/json"
            ):
                return _json403(MSG_PLATFORM_ONLY)
            return MSG_PLATFORM_ONLY, 403
        return f(*args, **kwargs)

    return admin_gerekli(_tenant_guard)


def _actor() -> str:
    return (getattr(current_user, "username", None) or getattr(current_user, "id", "") or "")[:200]


def _group_by_category(rows: list[dict]) -> list[dict]:
    by: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
    extra: dict[str, list] = {}
    for r in rows:
        cat = r.get("category") or "ops"
        if cat in by:
            by[cat].append(r)
        else:
            extra.setdefault(cat, []).append(r)
    out = []
    for cat in CATEGORY_ORDER:
        out.append(
            {
                "category": cat,
                "label": CATEGORY_LABELS.get(cat, cat),
                "entries": by[cat],
            }
        )
    for cat, items in sorted(extra.items()):
        out.append(
            {
                "category": cat,
                "label": CATEGORY_LABELS.get(cat, cat),
                "entries": items,
            }
        )
    return out


@bp.route("/credentials")
@platform_credentials_admin
def credentials_page():
    try:
        ensure_platform_credentials_table()
    except Exception:
        logger.exception("ensure_platform_credentials_table")
    rows = []
    err = None
    try:
        rows = list_credential_status()
    except Exception as e:
        logger.exception("list_credential_status")
        err = str(e)
    return render_template(
        "admin/credentials.html",
        username=getattr(current_user, "username", "") or "",
        groups=_group_by_category(rows),
        load_error=err,
    )


@bp.route("/api/credentials")
@platform_credentials_admin
def api_credentials_list():
    """Yalnız durum — plaintext yok."""
    try:
        ensure_platform_credentials_table()
        rows = list_credential_status()
        # value_hint bile API'de opsiyonel; güven için kaldır
        safe = [
            {
                "credential_key": r["credential_key"],
                "description": r["description"],
                "category": r["category"],
                "category_label": r["category_label"],
                "is_configured": r["is_configured"],
                "env_fallback": r["env_fallback"],
            }
            for r in rows
        ]
        return jsonify({"ok": True, "credentials": safe, "n": len(safe)})
    except Exception as e:
        logger.exception("api_credentials_list")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/credentials/<path:credential_key>", methods=["PUT", "POST"])
@platform_credentials_admin
def api_credentials_set(credential_key: str):
    key = (credential_key or "").strip()
    body = request.get_json(silent=True) or {}
    # Form fallback
    raw = body.get("value")
    if raw is None:
        raw = request.form.get("value")
    if raw is None or str(raw).strip() == "":
        return jsonify({"ok": False, "mesaj": "value gerekli (boş = Temizle kullanın)"}), 400
    try:
        ensure_platform_credentials_table()
        set_credential(key, str(raw), updated_by=_actor())
        return jsonify({"ok": True, "credential_key": key, "is_configured": True})
    except CredentialsVaultError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("api_credentials_set key=%s", key)
        return jsonify({"ok": False, "mesaj": type(e).__name__}), 500


@bp.route("/api/credentials/<path:credential_key>", methods=["DELETE"])
@platform_credentials_admin
def api_credentials_clear(credential_key: str):
    key = (credential_key or "").strip()
    try:
        ensure_platform_credentials_table()
        clear_credential(key, updated_by=_actor())
        return jsonify({"ok": True, "credential_key": key, "is_configured": False})
    except CredentialsVaultError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("api_credentials_clear key=%s", key)
        return jsonify({"ok": False, "mesaj": type(e).__name__}), 500


@bp.route("/credentials/save", methods=["POST"])
@platform_credentials_admin
def credentials_save_form():
    """HTML form: write-only kaydet."""
    key = (request.form.get("credential_key") or "").strip()
    value = request.form.get("value")
    if not key:
        flash("credential_key eksik.", "danger")
        return redirect(url_for("admin_credentials.credentials_page"))
    if value is None or str(value).strip() == "":
        flash("Boş değer kaydedilmez. Temizlemek için Temizle kullanın.", "warning")
        return redirect(url_for("admin_credentials.credentials_page"))
    try:
        ensure_platform_credentials_table()
        set_credential(key, str(value), updated_by=_actor())
        flash(f"«{key}» güncellendi (değer panelde gösterilmez).", "success")
    except CredentialsVaultError as e:
        flash(str(e), "danger")
    except Exception:
        logger.exception("credentials_save_form")
        flash("Kayıt başarısız.", "danger")
    return redirect(url_for("admin_credentials.credentials_page"))


@bp.route("/credentials/clear", methods=["POST"])
@platform_credentials_admin
def credentials_clear_form():
    key = (request.form.get("credential_key") or "").strip()
    if not key:
        flash("credential_key eksik.", "danger")
        return redirect(url_for("admin_credentials.credentials_page"))
    try:
        ensure_platform_credentials_table()
        clear_credential(key, updated_by=_actor())
        flash(f"«{key}» temizlendi.", "info")
    except Exception:
        logger.exception("credentials_clear_form")
        flash("Temizleme başarısız.", "danger")
    return redirect(url_for("admin_credentials.credentials_page"))

