# -*- coding: utf-8 -*-
"""Kiracı sistem ayarları. Yalnızca oturumdaki tenant şeması ve admin rolü."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from decimal import Decimal

from flask import Blueprint, abort, jsonify, render_template, request, send_file
from flask_login import current_user
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from auth import ROLLER, admin_gerekli, kullanici_guncelle, kullanici_olustur, sifre_degistir
from db import ensure_tenant_user_lookup_table, execute, fetch_all, fetch_one
from firma_profil import ensure_ayar_tablolari, firma_logo_abs, firma_profil_oku, tenant_schema_gecerli

bp = Blueprint("ayarlar", __name__, url_prefix="/ayarlar")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LOGO_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_LOGO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads", "firma_logo"))


def _tenant_row():
    schema = tenant_schema_gecerli()
    if not schema:
        abort(404)
    slug = schema[len("tenant_") :]
    row = fetch_one(
        """
        SELECT id, slug, schema_name, plan, company_name, status
        FROM public.tenants
        WHERE schema_name = %s AND slug = %s
        """,
        (schema, slug),
    )
    if not row or str(row.get("schema_name") or "") != schema:
        abort(404)
    return row


def _json_tenant():
    schema = tenant_schema_gecerli()
    if not schema:
        return None, (jsonify({"ok": False, "mesaj": "Kiracı oturumu yok."}), 404)
    try:
        return _tenant_row(), None
    except Exception:
        return None, (jsonify({"ok": False, "mesaj": "Kiracı bulunamadı."}), 404)


def _money(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return str(v.quantize(Decimal("0.01")))
    return str(v)


def _iso(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


@bp.route("/", strict_slashes=False)
@admin_gerekli
def sayfa():
    _tenant_row()
    return render_template("ayarlar/index.html", roller=ROLLER)


@bp.route("/api/profil", methods=["GET", "POST"])
@admin_gerekli
def api_profil():
    _tenant, err = _json_tenant()
    if err:
        return err
    ensure_ayar_tablolari()
    if request.method == "GET":
        row = firma_profil_oku()
        return jsonify({"ok": True, "profil": row, "logo_var": bool(firma_logo_abs())})
    data = request.get_json(silent=True) or {}
    unvan = str(data.get("unvan") or "").strip()[:300]
    if not unvan:
        return jsonify({"ok": False, "mesaj": "Ünvan zorunludur."}), 400
    execute(
        """
        INSERT INTO firma_profil (id, unvan, vergi_no, vergi_dairesi, telefon, email, adres)
        VALUES (1, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            unvan = EXCLUDED.unvan,
            vergi_no = EXCLUDED.vergi_no,
            vergi_dairesi = EXCLUDED.vergi_dairesi,
            telefon = EXCLUDED.telefon,
            email = EXCLUDED.email,
            adres = EXCLUDED.adres,
            updated_at = NOW()
        """,
        (
            unvan,
            str(data.get("vergi_no") or "").strip()[:32],
            str(data.get("vergi_dairesi") or "").strip()[:120],
            str(data.get("telefon") or "").strip()[:40],
            str(data.get("email") or "").strip()[:200],
            str(data.get("adres") or "").strip()[:500],
        ),
    )
    return jsonify({"ok": True, "mesaj": "Firma profili kaydedildi."})


@bp.route("/api/profil/logo", methods=["POST"])
@admin_gerekli
def api_profil_logo():
    tenant, err = _json_tenant()
    if err:
        return err
    ensure_ayar_tablolari()
    f = request.files.get("logo")
    if not f or not f.filename:
        return jsonify({"ok": False, "mesaj": "Logo dosyası seçin."}), 400
    ext = os.path.splitext(secure_filename(f.filename))[1].lower()
    if ext not in _LOGO_EXT:
        return jsonify({"ok": False, "mesaj": "Logo png, jpg veya webp olmalı."}), 400
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > 2 * 1024 * 1024:
        return jsonify({"ok": False, "mesaj": "Logo en fazla 2 MB olabilir."}), 400
    schema = str(tenant["schema_name"])
    folder = os.path.abspath(os.path.join(_LOGO_ROOT, schema))
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "logo" + ext)
    for old in os.listdir(folder):
        if old.startswith("logo."):
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass
    f.save(dest)
    execute(
        """
        INSERT INTO firma_profil (id, logo_path)
        VALUES (1, %s)
        ON CONFLICT (id) DO UPDATE SET logo_path = EXCLUDED.logo_path, updated_at = NOW()
        """,
        ("logo" + ext,),
    )
    return jsonify({"ok": True, "mesaj": "Logo kaydedildi."})


@bp.route("/api/profil/logo", methods=["GET"])
@admin_gerekli
def api_profil_logo_get():
    _tenant_row()
    path = firma_logo_abs()
    if not path:
        abort(404)
    return send_file(path)


@bp.route("/api/kullanicilar", methods=["GET", "POST"])
@admin_gerekli
def api_kullanicilar():
    tenant, err = _json_tenant()
    if err:
        return err
    if request.method == "GET":
        rows = fetch_all(
            """
            SELECT id, username, full_name, role, is_active
            FROM users
            ORDER BY id
            """
        ) or []
        return jsonify({"ok": True, "kullanicilar": rows, "roller": ROLLER})
    data = request.get_json(silent=True) or {}
    ad = str(data.get("ad") or "").strip()[:200]
    email = str(data.get("email") or "").strip().lower()
    rol = str(data.get("rol") or "").strip()
    sifre = str(data.get("sifre") or "")
    if not ad or not _EMAIL_RE.fullmatch(email):
        return jsonify({"ok": False, "mesaj": "Ad ve geçerli e-posta zorunludur."}), 400
    if rol not in ROLLER:
        return jsonify({"ok": False, "mesaj": "Geçersiz rol."}), 400
    if len(sifre) < 6:
        return jsonify({"ok": False, "mesaj": "Geçici şifre en az 6 karakter olmalı."}), 400
    ensure_tenant_user_lookup_table()
    mevcut_lookup = fetch_one(
        "SELECT tenant_slug FROM public.tenant_user_lookup WHERE email = %s",
        (email,),
    )
    if mevcut_lookup and str(mevcut_lookup.get("tenant_slug") or "") != str(tenant["slug"]):
        return jsonify({"ok": False, "mesaj": "Bu e-posta başka bir kiracıda kayıtlı."}), 409
    sonuc = kullanici_olustur(email, sifre, ad, rol)
    if not sonuc.get("ok"):
        return jsonify(sonuc), 400
    if not mevcut_lookup:
        execute(
            """
            INSERT INTO public.tenant_user_lookup (email, tenant_slug)
            VALUES (%s, %s)
            ON CONFLICT (email) DO NOTHING
            """,
            (email, tenant["slug"]),
        )
    return jsonify({"ok": True, "mesaj": "Kullanıcı oluşturuldu."})


@bp.route("/api/kullanicilar/<int:user_id>", methods=["POST"])
@admin_gerekli
def api_kullanici_guncelle(user_id: int):
    _tenant, err = _json_tenant()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    hedef = fetch_one("SELECT id, role FROM users WHERE id = %s", (user_id,))
    if not hedef:
        return jsonify({"ok": False, "mesaj": "Kullanıcı bulunamadı."}), 404
    if int(user_id) == int(getattr(current_user, "id", 0) or 0) and data.get("is_active") is False:
        return jsonify({"ok": False, "mesaj": "Kendi hesabınızı pasifleştiremezsiniz."}), 400
    rol = data.get("rol")
    if rol is not None and str(rol) not in ROLLER:
        return jsonify({"ok": False, "mesaj": "Geçersiz rol."}), 400
    aktif = data.get("is_active")
    if aktif is not None:
        aktif = bool(aktif)
    sonuc = kullanici_guncelle(
        user_id,
        role=str(rol) if rol else None,
        is_active=aktif,
    )
    if not sonuc.get("ok"):
        return jsonify(sonuc), 400
    return jsonify({"ok": True, "mesaj": "Kullanıcı güncellendi."})


@bp.route("/api/bildirimler", methods=["GET", "POST"])
@admin_gerekli
def api_bildirimler():
    _tenant, err = _json_tenant()
    if err:
        return err
    ensure_ayar_tablolari()
    if request.method == "GET":
        row = fetch_one("SELECT * FROM bildirim_ayar WHERE id = 1") or {}
        return jsonify(
            {
                "ok": True,
                "ayar": {
                    "gun_7": bool(row.get("gun_7")),
                    "gun_3": bool(row.get("gun_3")),
                    "gun_1": bool(row.get("gun_1")),
                    "vade_gunu": bool(row.get("vade_gunu")),
                    "gecikme_sonrasi": bool(row.get("gecikme_sonrasi")),
                    "kanal_email": bool(row.get("kanal_email")),
                },
                "kanallar": {
                    "email": {"aktif": True, "etiket": "E-posta"},
                    "sms": {"aktif": False, "etiket": "SMS", "not": "yakında"},
                    "whatsapp": {"aktif": False, "etiket": "WhatsApp", "not": "yakında"},
                },
                "gonderim": "tercih",
            }
        )
    data = request.get_json(silent=True) or {}

    def _b(key):
        return bool(data.get(key))

    execute(
        """
        INSERT INTO bildirim_ayar (
            id, gun_7, gun_3, gun_1, vade_gunu, gecikme_sonrasi, kanal_email
        ) VALUES (1, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            gun_7 = EXCLUDED.gun_7,
            gun_3 = EXCLUDED.gun_3,
            gun_1 = EXCLUDED.gun_1,
            vade_gunu = EXCLUDED.vade_gunu,
            gecikme_sonrasi = EXCLUDED.gecikme_sonrasi,
            kanal_email = EXCLUDED.kanal_email,
            updated_at = NOW()
        """,
        (
            _b("gun_7"),
            _b("gun_3"),
            _b("gun_1"),
            _b("vade_gunu"),
            _b("gecikme_sonrasi"),
            _b("kanal_email"),
        ),
    )
    return jsonify({"ok": True, "mesaj": "Bildirim tercihleri kaydedildi."})


@bp.route("/api/abonelik", methods=["GET"])
@admin_gerekli
def api_abonelik():
    tenant, err = _json_tenant()
    if err:
        return err
    tid = int(tenant["id"])
    sub = fetch_one(
        """
        SELECT plan_code, status, amount_gross, currency, next_invoice_at, billing_cycle
        FROM public.platform_tenant_subscriptions
        WHERE tenant_id = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (tid,),
    )
    faturalar = fetch_all(
        """
        SELECT id, invoice_no, status, total_gross, currency, issued_at, source
        FROM public.platform_tenant_invoices
        WHERE tenant_id = %s
        ORDER BY id DESC
        LIMIT 50
        """,
        (tid,),
    ) or []
    items = []
    for r in faturalar:
        items.append(
            {
                "id": int(r["id"]),
                "invoice_no": r.get("invoice_no"),
                "status": r.get("status"),
                "total_gross": _money(r.get("total_gross")),
                "currency": r.get("currency"),
                "issued_at": _iso(r.get("issued_at")),
                "source": r.get("source"),
                "odenebilir": str(r.get("status") or "") == "sent" and str(r.get("source") or "") == "paytr",
            }
        )
    plan = None
    if sub:
        plan = {
            "plan_code": sub.get("plan_code") or tenant.get("plan"),
            "status": sub.get("status"),
            "amount_gross": _money(sub.get("amount_gross")),
            "currency": sub.get("currency") or "TRY",
            "next_invoice_at": _iso(sub.get("next_invoice_at")),
            "billing_cycle": sub.get("billing_cycle"),
        }
    else:
        plan = {
            "plan_code": tenant.get("plan"),
            "status": tenant.get("status"),
            "amount_gross": None,
            "currency": "TRY",
            "next_invoice_at": None,
            "billing_cycle": None,
        }
    return jsonify({"ok": True, "plan": plan, "faturalar": items})


@bp.route("/api/abonelik/ode/<int:invoice_id>", methods=["POST"])
@admin_gerekli
def api_abonelik_ode(invoice_id: int):
    tenant, err = _json_tenant()
    if err:
        return err
    row = fetch_one(
        """
        SELECT id, tenant_id, status, source
        FROM public.platform_tenant_invoices
        WHERE id = %s AND tenant_id = %s
        """,
        (invoice_id, int(tenant["id"])),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Fatura bulunamadı."}), 404
    if str(row.get("status") or "") != "sent" or str(row.get("source") or "") != "paytr":
        return jsonify({"ok": False, "mesaj": "Bu fatura için ödeme başlatılamaz."}), 400
    full = fetch_one(
        """
        SELECT metadata
        FROM public.platform_tenant_invoices
        WHERE id = %s AND tenant_id = %s
        """,
        (invoice_id, int(tenant["id"])),
    )
    paid = fetch_one(
        """
        SELECT id
        FROM public.platform_tenant_payments
        WHERE invoice_id = %s AND tenant_id = %s
        LIMIT 1
        """,
        (invoice_id, int(tenant["id"])),
    )
    if paid:
        return jsonify({"ok": False, "mesaj": "Bu fatura için ödeme kaydı var."}), 409
    from routes.admin_billing_routes import _meta, _paytr_merchant_oid

    meta = _meta((full or {}).get("metadata"))
    # Aynı merchant_oid ile ikinci get-token PayTR'de boş 200 döner.
    if meta.get("paytr_init_at"):
        meta["merchant_oid"] = _paytr_merchant_oid(invoice_id)
        meta.pop("paytr_init_at", None)
        meta.pop("payment_amount_kurus", None)
        updated = execute(
            """
            UPDATE public.platform_tenant_invoices
            SET metadata = %s::jsonb, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND status = 'sent'
            """,
            (json.dumps(meta), invoice_id, int(tenant["id"])),
        )
        if not updated:
            return jsonify({"ok": False, "mesaj": "Fatura ödeme için güncellenemedi."}), 409
    from paytr_checkout_token import attach_pay_token_to_invoice_metadata

    try:
        token = attach_pay_token_to_invoice_metadata(invoice_id)
    except Exception:
        return jsonify({"ok": False, "mesaj": "Ödeme bağlantısı üretilemedi."}), 400
    url = f"https://payafin.com/billing/paytr/checkout/{invoice_id}?token={token}"
    return jsonify({"ok": True, "pay_url": url})


@bp.route("/api/guvenlik/sifre", methods=["POST"])
@admin_gerekli
def api_sifre():
    _tenant, err = _json_tenant()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    eski = str(data.get("mevcut") or "")
    yeni = str(data.get("yeni") or "")
    tekrar = str(data.get("tekrar") or "")
    if len(yeni) < 6:
        return jsonify({"ok": False, "mesaj": "Yeni şifre en az 6 karakter olmalı."}), 400
    if yeni != tekrar:
        return jsonify({"ok": False, "mesaj": "Yeni şifre tekrarı eşleşmiyor."}), 400
    uid = int(getattr(current_user, "id", 0) or 0)
    row = fetch_one("SELECT id, username, password_hash FROM users WHERE id = %s", (uid,))
    if not row or str(row.get("username") or "") != str(getattr(current_user, "username", "") or ""):
        return jsonify({"ok": False, "mesaj": "Oturum kullanıcısı doğrulanamadı."}), 403
    if not check_password_hash(str(row.get("password_hash") or ""), eski):
        return jsonify({"ok": False, "mesaj": "Mevcut şifre hatalı."}), 400
    sonuc = sifre_degistir(uid, yeni)
    if not sonuc.get("ok"):
        return jsonify(sonuc), 400
    return jsonify({"ok": True, "mesaj": "Şifre değiştirildi. Yeniden giriş gerekebilir."})
