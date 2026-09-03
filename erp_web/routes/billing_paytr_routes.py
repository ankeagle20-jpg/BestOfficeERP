# -*- coding: utf-8 -*-
"""PayTR ödeme sayfaları + callback — yalnızca public host.

Aşama 3.5: PAYTR_CALLBACK_APPLY=true iken tek transaction ile payment+paid;
varsayılan false → dry-run (yalnız log, yazma yok).
"""
from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from functools import wraps

from flask import Blueprint, Response, abort, g, render_template, request
from flask_login import current_user
from psycopg2.errors import UniqueViolation

from auth import admin_gerekli
from db import db as db_tx
from db import fetch_one
from paytr_client import verify_callback_signature
from routes.admin_billing_routes import run_paytr_init

logger = logging.getLogger(__name__)

bp = Blueprint("billing_paytr", __name__)

MSG_PLATFORM_ONLY = "PayTR ödeme sayfaları yalnızca ana (public) host'ta kullanılabilir."


def platform_host_only(f):
    """Kiracı alt alan adında bilerek 404 (route reklam etme)."""

    @wraps(f)
    def _guard(*args, **kwargs):
        if getattr(g, "tenant_schema", None):
            abort(404)
        return f(*args, **kwargs)

    return _guard


def _callback_apply_enabled() -> bool:
    """3.5'te yazım açılacak. Varsayılan false — 3.4 dry-run asla yazmaz."""
    v = (os.environ.get("PAYTR_CALLBACK_APPLY") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _meta_dict(val) -> dict:
    if val is None or val == "":
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _oid_mask(oid: str) -> str:
    if not oid:
        return "(empty)"
    if len(oid) >= 8:
        return oid[:8] + "***"
    return "(short)"


def _plain_ok() -> Response:
    return Response("OK", status=200, mimetype="text/plain")


def _record_paytr_paid(inv: dict, merchant_oid: str, status: str) -> str:
    """Tek transaction: payment INSERT + invoice paid. Dönüş: recorded|already.

    UniqueViolation (race) → already (idempotent, hata yükseltmez).
    """
    payment_type = (request.form.get("payment_type") or "").strip() or None
    test_mode = (request.form.get("test_mode") or "").strip() or None
    pay_meta = {
        "merchant_oid": merchant_oid,
        "status": status,
    }
    if test_mode is not None:
        pay_meta["test_mode"] = test_mode
    if payment_type is not None:
        pay_meta["payment_type"] = payment_type

    try:
        with db_tx() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO public.platform_tenant_payments (
                    tenant_id, tenant_slug, invoice_id, amount, currency,
                    paid_at, method, reference, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    NOW(), 'paytr', %s, %s::jsonb
                )
                """,
                (
                    inv["tenant_id"],
                    inv["tenant_slug"],
                    inv["id"],
                    inv["total_gross"],
                    inv.get("currency") or "TRY",
                    merchant_oid,
                    json.dumps(pay_meta),
                ),
            )
            cur.execute(
                """
                UPDATE public.platform_tenant_invoices
                SET status = 'paid',
                    paid_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (inv["id"],),
            )
        return "recorded"
    except UniqueViolation:
        return "already"


@bp.route("/billing/paytr/pay/<int:invoice_id>", methods=["GET"])
@platform_host_only
@admin_gerekli
def paytr_pay_page(invoice_id: int):
    """Minimal ödeme sayfası: dahili init → PayTR iFrame. Fatura onayı burada yapılmaz."""
    ok, http_status, payload = run_paytr_init(invoice_id, data={})
    if not ok:
        return (
            render_template(
                "billing/paytr_pay_error.html",
                mesaj=payload.get("mesaj") or "Ödeme başlatılamadı",
                http_status=http_status,
                invoice_id=invoice_id,
            ),
            http_status if http_status in (400, 403, 404, 409, 502) else 400,
        )

    inv = payload.get("invoice") or {}
    return render_template(
        "billing/paytr_pay.html",
        token=payload.get("token"),
        merchant_oid=payload.get("merchant_oid"),
        payment_amount_kurus=payload.get("payment_amount_kurus"),
        test_mode=payload.get("test_mode"),
        invoice_id=inv.get("id") or invoice_id,
        invoice_no=inv.get("invoice_no") or "",
        currency=inv.get("currency") or "TRY",
        total_gross=inv.get("total_gross"),
        tenant_slug=inv.get("tenant_slug") or "",
    )


@bp.route("/billing/paytr/ok", methods=["GET"])
@platform_host_only
def paytr_ok_page():
    """Bilgilendirme only — fatura durumu ASLA değiştirilmez."""
    oid = (request.args.get("oid") or request.args.get("merchant_oid") or "").strip()
    invoice_status = None
    invoice_no = None
    if oid and oid.isalnum():
        row = fetch_one(
            """
            SELECT invoice_no, status, metadata
            FROM public.platform_tenant_invoices
            WHERE metadata->>'merchant_oid' = %s
            LIMIT 1
            """,
            (oid,),
        )
        if row:
            # Salt okuma — UPDATE yok
            invoice_no = str(row.get("invoice_no") or "")
            invoice_status = str(row.get("status") or "")
    return render_template(
        "billing/paytr_ok.html",
        merchant_oid=oid or None,
        invoice_no=invoice_no,
        invoice_status=invoice_status,
        user_email=getattr(current_user, "email", None) if current_user.is_authenticated else None,
    )


@bp.route("/billing/paytr/fail", methods=["GET"])
@platform_host_only
def paytr_fail_page():
    """Bilgilendirme only — fatura durumu ASLA değiştirilmez."""
    oid = (request.args.get("oid") or request.args.get("merchant_oid") or "").strip()
    invoice_status = None
    invoice_no = None
    if oid and oid.isalnum():
        row = fetch_one(
            """
            SELECT invoice_no, status
            FROM public.platform_tenant_invoices
            WHERE metadata->>'merchant_oid' = %s
            LIMIT 1
            """,
            (oid,),
        )
        if row:
            invoice_no = str(row.get("invoice_no") or "")
            invoice_status = str(row.get("status") or "")
    return render_template(
        "billing/paytr_fail.html",
        merchant_oid=oid or None,
        invoice_no=invoice_no,
        invoice_status=invoice_status,
    )


@bp.route("/billing/paytr/callback", methods=["POST"])
@platform_host_only
def paytr_callback():
    """PayTR bildirim URL — Aşama 3.5.

    Auth yok (PayTR sunucu→sunucu). CSRF yok (form POST, session taşımaz).
    Varsayılan PAYTR_CALLBACK_APPLY=false → dry-run (yalnız log).
    true → tek transaction payment INSERT + invoice paid; UniqueViolation → OK.
    Her durumda düz metin OK (PayTR yeniden denemesini kesmek için).
    """
    merchant_oid = (request.form.get("merchant_oid") or "").strip()
    status = (request.form.get("status") or "").strip()
    total_amount = (request.form.get("total_amount") or "").strip()
    received_hash = (request.form.get("hash") or "").strip()
    apply_flag = _callback_apply_enabled()

    # Secret yazılmaz — yalnızca alan adları / sonuç kodları
    logger.info(
        "paytr_callback_received oid_len=%s status=%s amount_len=%s hash_len=%s apply_flag=%s",
        len(merchant_oid),
        status or "(empty)",
        len(total_amount),
        len(received_hash),
        apply_flag,
    )

    if not verify_callback_signature(merchant_oid, status, total_amount, received_hash):
        logger.warning(
            "paytr_callback WOULD_REJECT_BAD_HASH oid_prefix=%s status=%s",
            _oid_mask(merchant_oid),
            status or "(empty)",
        )
        return _plain_ok()

    if not merchant_oid or not merchant_oid.isalnum():
        logger.warning("paytr_callback WOULD_REJECT_OID_FORMAT")
        return _plain_ok()

    inv = fetch_one(
        """
        SELECT id, tenant_id, tenant_slug, status, currency, total_gross, metadata
        FROM public.platform_tenant_invoices
        WHERE metadata->>'merchant_oid' = %s
        LIMIT 1
        """,
        (merchant_oid,),
    )
    if not inv:
        logger.warning(
            "paytr_callback WOULD_SKIP_INVOICE_NOT_FOUND oid_prefix=%s",
            _oid_mask(merchant_oid),
        )
        return _plain_ok()

    inv_id = int(inv["id"])
    inv_status = str(inv.get("status") or "").strip().lower()
    meta = _meta_dict(inv.get("metadata"))

    expected_kurus = str(meta.get("payment_amount_kurus") or "").strip()
    if not expected_kurus:
        try:
            expected_kurus = str(
                int(round(float(Decimal(str(inv.get("total_gross") or 0))) * 100))
            )
        except Exception:
            expected_kurus = ""

    if expected_kurus and total_amount != expected_kurus:
        logger.warning(
            "paytr_callback WOULD_REJECT_AMOUNT_MISMATCH invoice_id=%s expected=%s got=%s",
            inv_id,
            expected_kurus,
            total_amount,
        )
        return _plain_ok()

    # Idempotency (salt SELECT)
    if inv_status == "paid":
        logger.info(
            "paytr_callback WOULD_SKIP_ALREADY_PAID invoice_id=%s oid_prefix=%s",
            inv_id,
            _oid_mask(merchant_oid),
        )
        return _plain_ok()

    existing_pay = fetch_one(
        """
        SELECT id FROM public.platform_tenant_payments
        WHERE reference = %s
           OR (metadata->>'merchant_oid') = %s
        LIMIT 1
        """,
        (merchant_oid, merchant_oid),
    )
    if existing_pay:
        logger.info(
            "paytr_callback WOULD_SKIP_PAYMENT_EXISTS invoice_id=%s payment_id=%s",
            inv_id,
            existing_pay.get("id"),
        )
        return _plain_ok()

    if status.lower() != "success":
        logger.info(
            "paytr_callback WOULD_SKIP_FAILED_STATUS invoice_id=%s status=%s",
            inv_id,
            status,
        )
        return _plain_ok()

    # Başarılı yol — feature flag: yazım veya dry-run
    if apply_flag:
        logger.info(
            "paytr_callback WOULD_MARK_PAID invoice_id=%s amount_kurus=%s apply_flag=true",
            inv_id,
            total_amount,
        )
        outcome = _record_paytr_paid(inv, merchant_oid, status)
        if outcome == "already":
            logger.info(
                "paytr_callback ALREADY_PROCESSED (unique race) invoice_id=%s oid_prefix=%s",
                inv_id,
                _oid_mask(merchant_oid),
            )
        else:
            logger.info(
                "paytr_callback PAID_RECORDED invoice_id=%s tenant_id=%s oid_prefix=%s",
                inv_id,
                inv.get("tenant_id"),
                _oid_mask(merchant_oid),
            )
    else:
        logger.info(
            "paytr_callback WOULD_MARK_PAID invoice_id=%s tenant_id=%s "
            "amount_kurus=%s currency=%s apply_flag=false",
            inv_id,
            inv.get("tenant_id"),
            total_amount,
            inv.get("currency"),
        )

    return _plain_ok()
