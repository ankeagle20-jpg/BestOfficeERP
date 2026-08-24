# -*- coding: utf-8 -*-
"""Public: Payafin fiyatlandırma salt-okuma API + Enterprise iletişim formu."""
from __future__ import annotations

import logging
import re

from flask import Blueprint, current_app, jsonify, render_template, request

from db import execute_returning, fetch_one
from mail_utils import send_mail
from module_pricing_contact_rate_limit import check_module_pricing_contact_rate
from module_pricing_public_cache import PUBLIC_MODULE_KEYS, get_public_module_pricing
from pricing_public_cache import get_public_pricing
from routes.signup_routes import platform_public_only
from signup_validation import honeypot_triggered, validate_country_code, validate_email

logger = logging.getLogger(__name__)

bp = Blueprint("public_pricing", __name__)

_PHONE_RE = re.compile(r"^[\d\s+\-().]{7,32}$")

_MODULE_CONTACT_LABELS = {
    "personnel": "Personel",
    "randevu": "Randevu",
}


def _country_query_param() -> str:
    cc = (request.args.get("country") or request.args.get("country_code") or "TR").strip().upper()
    if not cc or len(cc) != 2 or not cc.isalpha():
        raise ValueError("geçersiz country")
    return cc


def _fake_contact_success() -> tuple:
    return jsonify(
        {
            "ok": True,
            "mesaj": "Teşekkürler, sizinle iletişime geçeceğiz.",
        }
    ), 200


def _parse_nonneg_int(name: str, raw, *, required: bool = True):
    if raw is None or raw == "":
        if required:
            raise ValueError(f"{name} gerekli")
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} geçersiz") from e
    if v < 0:
        raise ValueError(f"{name} negatif olamaz")
    return v


@bp.route("/api/pricing/public", methods=["GET"])
def api_pricing_public():
    """Herkesin erişebileceği aktif kademe + overage listesi (salt okuma)."""
    try:
        cc = _country_query_param()
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400

    try:
        payload = get_public_pricing(cc)
    except Exception:
        logger.exception("api_pricing_public country=%s", cc)
        return jsonify({"ok": False, "mesaj": "Fiyatlandırma yüklenemedi."}), 404

    if payload is None:
        return jsonify({"ok": False, "mesaj": f"bilinmeyen veya pasif ülke: {cc}"}), 404

    return jsonify(payload)


@bp.route("/api/module-pricing/public", methods=["GET"])
def api_module_pricing_public():
    """Herkesin erişebileceği aktif modül kademeleri (salt okuma, kimlik yok)."""
    mk = (request.args.get("module") or request.args.get("module_key") or "").strip()
    try:
        cc = _country_query_param()
        if not mk:
            raise ValueError("module gerekli")
        payload = get_public_module_pricing(mk, cc)
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception:
        logger.exception("api_module_pricing_public module=%s", mk)
        return jsonify({"ok": False, "mesaj": "Modül fiyatlandırması yüklenemedi."}), 404

    if payload is None:
        return jsonify(
            {
                "ok": False,
                "mesaj": f"bilinmeyen/pasif ülke veya kademe yok: {mk}/{cc}",
            }
        ), 404

    return jsonify(payload)


@bp.route("/api/module-pricing/contact", methods=["POST"])
def api_module_pricing_contact():
    """Enterprise iletişim formu — kimlik yok, honeypot + saatte 5 IP limiti."""
    body = request.get_json(silent=True) or {}

    # Honeypot (website) doluysa sessiz sahte başarı — kayıt yok, rate limit tüketilmez.
    if honeypot_triggered(body.get("website")):
        return _fake_contact_success()

    errors: dict[str, str] = {}
    mk = str(body.get("module_key") or body.get("module") or "personnel").strip()
    if mk not in PUBLIC_MODULE_KEYS:
        errors["module_key"] = "geçersiz module_key"

    cc = str(body.get("country_code") or body.get("country") or "").strip().upper()
    if validate_country_code(cc):
        errors["country_code"] = "geçersiz country_code"

    company_name = str(body.get("company_name") or "").strip()
    if len(company_name) < 2 or len(company_name) > 200:
        errors["company_name"] = "geçersiz company_name"

    contact_name = str(body.get("contact_name") or "").strip()
    if len(contact_name) < 2 or len(contact_name) > 120:
        errors["contact_name"] = "geçersiz contact_name"

    email = str(body.get("email") or "").strip().lower()
    if validate_email(email):
        errors["email"] = "geçersiz email"

    phone_raw = body.get("phone")
    phone = str(phone_raw).strip() if phone_raw is not None else ""
    if phone and not _PHONE_RE.fullmatch(phone):
        errors["phone"] = "geçersiz phone"
    if not phone:
        phone = None

    message = str(body.get("message") or "").strip()
    if len(message) > 4000:
        errors["message"] = "mesaj çok uzun"
    if not message:
        message = None

    estimated_monthly_appointments = None
    try:
        estimated_personnel = _parse_nonneg_int(
            "estimated_personnel", body.get("estimated_personnel"), required=True
        )
        if mk == "randevu":
            # Randevu: personel + aylık randevu zorunlu; şube yok (0 kaydedilir)
            estimated_monthly_appointments = _parse_nonneg_int(
                "estimated_monthly_appointments",
                body.get("estimated_monthly_appointments"),
                required=True,
            )
            estimated_branches = _parse_nonneg_int(
                "estimated_branches",
                body.get("estimated_branches"),
                required=False,
            )
            if estimated_branches is None:
                estimated_branches = 0
        else:
            estimated_branches = _parse_nonneg_int(
                "estimated_branches", body.get("estimated_branches"), required=True
            )
            # Personel formu appointments göndermeyebilir
            if body.get("estimated_monthly_appointments") not in (None, ""):
                estimated_monthly_appointments = _parse_nonneg_int(
                    "estimated_monthly_appointments",
                    body.get("estimated_monthly_appointments"),
                    required=False,
                )
    except ValueError as e:
        errors["estimates"] = str(e)
        estimated_personnel = None
        estimated_branches = None
        estimated_monthly_appointments = None

    if errors:
        return jsonify({"ok": False, "mesaj": "Doğrulama hatası.", "errors": errors}), 400

    # Randevu tahmini randevu sayısını lead mesajında da sakla (mevcut şema)
    if mk == "randevu" and estimated_monthly_appointments is not None:
        appt_line = f"Tahmini aylık randevu: {estimated_monthly_appointments}"
        message = f"{appt_line}\n\n{message}" if message else appt_line

    region = fetch_one(
        """
        SELECT country_code, is_active
        FROM public.pricing_regions
        WHERE country_code = %s
        """,
        (cc,),
    )
    if not region or not region.get("is_active"):
        return jsonify({"ok": False, "mesaj": "geçersiz veya pasif ülke"}), 400

    allowed, retry_after = check_module_pricing_contact_rate()
    if not allowed:
        return (
            jsonify({"ok": False, "mesaj": "Çok fazla deneme, lütfen bekleyin."}),
            429,
            {"Retry-After": str(max(1, retry_after or 3600))},
        )

    try:
        row = execute_returning(
            """
            INSERT INTO public.module_pricing_leads (
                module_key, country_code, company_name, contact_name,
                email, phone, estimated_personnel, estimated_branches,
                message, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'new')
            RETURNING id, created_at, status
            """,
            (
                mk,
                cc,
                company_name,
                contact_name,
                email,
                phone,
                estimated_personnel,
                estimated_branches,
                message,
            ),
        )
    except Exception:
        logger.exception("api_module_pricing_contact insert")
        return jsonify({"ok": False, "mesaj": "Kayıt oluşturulamadı."}), 500

    lead_id = int(row["id"]) if row else None
    sales_to = (
        current_app.config.get("MAIL_DEFAULT_SENDER")
        or current_app.config.get("MAIL_USERNAME")
        or ""
    ).strip()
    mail_ok = False
    if sales_to and lead_id is not None:
        mod_label = _MODULE_CONTACT_LABELS.get(mk, mk)
        subject = f"[Payafin] {mod_label} Enterprise talebi #{lead_id} ({cc})"
        estimate_lines = f"Tahmini personel: {estimated_personnel}\n"
        if mk == "randevu":
            estimate_lines += (
                f"Tahmini aylık randevu: {estimated_monthly_appointments}\n"
            )
        else:
            estimate_lines += f"Tahmini şube: {estimated_branches}\n"
        body_text = (
            f"Yeni Enterprise iletişim talebi\n"
            f"────────────────────────────\n"
            f"Lead ID: {lead_id}\n"
            f"Modül: {mk} ({mod_label})\n"
            f"Ülke: {cc}\n"
            f"Şirket: {company_name}\n"
            f"Yetkili: {contact_name}\n"
            f"E-posta: {email}\n"
            f"Telefon: {phone or '—'}\n"
            f"{estimate_lines}"
            f"Mesaj:\n{message or '—'}\n"
        )
        try:
            mail_ok = bool(send_mail(sales_to, subject, body_text))
        except Exception:
            logger.exception("api_module_pricing_contact mail lead_id=%s", lead_id)
            mail_ok = False
        if not mail_ok:
            logger.warning(
                "module_pricing contact mail failed lead_id=%s to=%s",
                lead_id,
                sales_to,
            )

    return jsonify(
        {
            "ok": True,
            "mesaj": "Teşekkürler, sizinle iletişime geçeceğiz.",
            "lead_id": lead_id,
            "mail_sent": mail_ok,
        }
    )


@bp.route("/fiyatlandirma", methods=["GET"])
@platform_public_only
def fiyatlandirma_page():
    """Payafin apex — görsel fiyatlandırma sayfası (herkese açık)."""
    return render_template("marketing/fiyatlandirma.html")
