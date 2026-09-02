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


def _mask_secret(value: str | None) -> dict:
    """Secret sızdırmaz durum göstergesi."""
    t = (value or "").strip()
    if not t:
        return {"state": "BOŞ", "len": 0, "prefix": None}
    return {"state": "DOLU", "len": len(t), "prefix": t[:2] + "***"}


@bp.route("/test-paytr-oid-format", methods=["GET"])
@platform_credentials_admin
def test_paytr_oid_format_gate():
    """GEÇİCİ Aşama 3.0 gate: tireli merchant_oid PayTR get-token kabulü.

    Gerçek faturaya bağlı değil. Secret değer ASLA döndürülmez.
    Kullanım sonrası kaldırılacak.
    """
    import base64
    import hashlib
    import hmac
    import json as _json
    import secrets
    import urllib.error
    import urllib.parse
    import urllib.request

    from credentials_vault import get_credential

    mid = get_credential("paytr.merchant_id")
    mkey = get_credential("paytr.merchant_key")
    msalt = get_credential("paytr.merchant_salt")
    mode = get_credential("paytr.mode")

    # A2 convention formatı; sahte invoice id — DB'ye yazılmaz
    merchant_oid = f"INV-999999-{secrets.token_hex(4)}"

    payload = {
        "ok": True,
        "gecici": True,
        "gate": "paytr-3-0-oid-format",
        "uyari": (
            "GEÇİCİ Aşama 3.0 gate — tireli merchant_oid format testi. "
            "Kullanım sonrası kaldırılacak. Secret değerler yanıtta yoktur."
        ),
        "env": {
            "paytr.merchant_id": _mask_secret(mid),
            "paytr.merchant_key": _mask_secret(mkey),
            "paytr.merchant_salt": _mask_secret(msalt),
            "paytr.mode": _mask_secret(mode)
            if (mode or "").strip()
            else {
                "state": "BOŞ",
                "len": 0,
                "prefix": None,
                "note": "PAYTR_MODE env önerilir (test)",
            },
        },
        "probe": {
            "merchant_oid": merchant_oid,
            "merchant_oid_has_hyphen": "-" in merchant_oid,
            "format": "INV-{fake_invoice_id}-{8 hex}",
            "bound_to_real_invoice": False,
        },
        "paytr": {
            "endpoint": "https://www.paytr.com/odeme/api/get-token",
            "test_mode": "1",
            "payment_amount_kurus": "100",
            "status": None,
            "reason": None,
            "reason_suggests_oid_format": None,
            "token_masked": None,
            "http_status": None,
        },
    }

    if not ((mid or "").strip() and (mkey or "").strip() and (msalt or "").strip()):
        payload["paytr"]["status"] = "skipped"
        payload["paytr"]["reason"] = "merchant_id/key/salt eksik (env veya vault)"
        return jsonify(payload)

    payment_amount = "100"
    email = "oid-gate@payafin.com"
    user_ip = request.headers.get("X-Forwarded-For") or request.remote_addr or "1.2.3.4"
    user_ip = str(user_ip).split(",")[0].strip()[:39] or "1.2.3.4"
    user_basket = base64.b64encode(
        _json.dumps([["Payafin oid-format gate", "1.00", 1]], ensure_ascii=False).encode()
    ).decode()
    no_installment = "0"
    max_installment = "0"
    currency = "TL"
    test_mode = "1"

    mid_s = mid.strip()
    mkey_s = mkey.strip()
    msalt_s = msalt.strip()
    hash_str = (
        mid_s
        + user_ip
        + merchant_oid
        + email
        + payment_amount
        + user_basket
        + no_installment
        + max_installment
        + currency
        + test_mode
    )
    paytr_token = base64.b64encode(
        hmac.new(
            mkey_s.encode(),
            (hash_str + msalt_s).encode(),
            hashlib.sha256,
        ).digest()
    ).decode()

    post = {
        "merchant_id": mid_s,
        "user_ip": user_ip,
        "merchant_oid": merchant_oid,
        "email": email,
        "payment_amount": payment_amount,
        "paytr_token": paytr_token,
        "user_basket": user_basket,
        "debug_on": "1",
        "no_installment": no_installment,
        "max_installment": max_installment,
        "user_name": "Payafin Oid Gate",
        "user_address": "TR",
        "user_phone": "05000000000",
        "merchant_ok_url": "https://payafin.com/",
        "merchant_fail_url": "https://payafin.com/",
        "timeout_limit": "5",
        "currency": currency,
        "test_mode": test_mode,
        "lang": "tr",
    }

    j: dict = {}
    try:
        data = urllib.parse.urlencode(post).encode()
        req = urllib.request.Request(
            "https://www.paytr.com/odeme/api/get-token",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload["paytr"]["http_status"] = int(resp.status)
            j = _json.loads(raw)
    except urllib.error.HTTPError as e:
        payload["paytr"]["http_status"] = int(getattr(e, "code", 0) or 0)
        try:
            body = e.read().decode("utf-8", errors="replace")
            j = _json.loads(body) if body else {}
        except Exception:
            j = {}
            payload["paytr"]["reason"] = "http_error_body_parse_failed"
    except Exception as e:
        logger.exception("test_paytr_oid_format_gate")
        payload["paytr"]["status"] = "error"
        payload["paytr"]["reason"] = type(e).__name__
        return jsonify(payload)

    payload["paytr"]["status"] = j.get("status")
    reason = j.get("reason")
    if reason is not None:
        payload["paytr"]["reason"] = reason
        reason_l = str(reason).lower()
        payload["paytr"]["reason_suggests_oid_format"] = any(
            tip in reason_l
            for tip in (
                "merchant_oid",
                "sipariş no",
                "siparis no",
                "alfanumerik",
                "alfa nümerik",
                "format",
                "geçersiz",
                "gecersiz",
            )
        )
    tok = j.get("token")
    if tok:
        payload["paytr"]["token_masked"] = _mask_secret(str(tok))

    return jsonify(payload)

