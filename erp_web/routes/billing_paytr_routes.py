# -*- coding: utf-8 -*-
"""PayTR ödeme sayfaları — yalnızca public/marketing host (kiracı subdomain yok)."""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, abort, g, render_template, request
from flask_login import current_user

from auth import admin_gerekli
from db import fetch_one
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
