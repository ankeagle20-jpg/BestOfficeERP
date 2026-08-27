# -*- coding: utf-8 -*-
"""Admin: Payafin indirim kampanyaları yönetimi (yalnız public host)."""
from __future__ import annotations

import datetime
import logging
import re
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any

from flask import Blueprint, g, jsonify, render_template, request
from flask_login import current_user

from auth import admin_gerekli
from db import execute, execute_returning, fetch_all, fetch_one
from module_pricing_public_cache import invalidate_public_module_pricing_cache
from pricing_public_cache import invalidate_public_pricing_cache
from services.exchange_rate_service import fetch_and_store_exchange_rates

logger = logging.getLogger(__name__)

bp = Blueprint("admin_campaigns", __name__)


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_campaigns_admin(f):
    """@admin_gerekli + kiracı subdomain'inde 403 (platform-only)."""

    @wraps(f)
    def _tenant_guard(*args, **kwargs):
        if getattr(g, "tenant_schema", None):
            path = request.path or ""
            if "/api/" in path or request.is_json or (
                request.accept_mimetypes.best == "application/json"
            ):
                return _json403(
                    "Kampanya yönetimi yalnızca ana (public) host'ta kullanılabilir."
                )
            return (
                "Kampanya yönetimi yalnızca ana (public) host'ta kullanılabilir.",
                403,
            )
        return f(*args, **kwargs)

    return admin_gerekli(_tenant_guard)


def _parse_iso_datetime(value_str: str, field_name: str) -> datetime.datetime:
    """ISO formatlı veya YYYY-MM-DDTHH:MM tarih/saat dizgisini UTC datetime'a çevirir."""
    val = str(value_str or "").strip()
    if not val:
        raise ValueError(f"{field_name} gereklidir.")
    try:
        # Örnek formatlar: '2026-08-27T12:00', '2026-08-27T12:00:00', '2026-08-27T12:00:00Z'
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception as e:
        raise ValueError(f"{field_name} geçersiz tarih formatı (ISO bekleniyor).") from e


def _invalidate_all_pricing_caches():
    """Kampanya veya kur değişikliğinde tüm public fiyat cache'lerini anında temizler."""
    invalidate_public_pricing_cache()
    invalidate_public_module_pricing_cache()


def _campaign_row_to_dict(row: dict) -> dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc)
    start_d = row["start_date"]
    end_d = row["end_date"]
    is_act = bool(row["is_active"])

    # Durum tespiti: active, upcoming, expired, inactive
    if not is_act:
        status = "inactive"
        status_label = "Pasif"
    elif now < start_d:
        status = "upcoming"
        status_label = "Planlandı"
    elif now > end_d:
        status = "expired"
        status_label = "Süresi Doldu"
    else:
        status = "active"
        status_label = "Aktif"

    # Kalan gün
    days_left = None
    if status == "active":
        days_left = max(0, (end_d - now).days)

    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "code": str(row["code"]) if row.get("code") else None,
        "discount_percent": float(row["discount_percent"]),
        "applies_to": str(row["applies_to"]),
        "target_countries": list(row["target_countries"] or []),
        "start_date": start_d.isoformat() if hasattr(start_d, "isoformat") else str(start_d),
        "end_date": end_d.isoformat() if hasattr(end_d, "isoformat") else str(end_d),
        "is_active": is_act,
        "priority": int(row["priority"]),
        "status": status,
        "status_label": status_label,
        "days_left": days_left,
        "created_at": row["created_at"].isoformat() if hasattr(row.get("created_at"), "isoformat") else None,
        "updated_at": row["updated_at"].isoformat() if hasattr(row.get("updated_at"), "isoformat") else None,
    }


@bp.route("/campaigns", methods=["GET"])
@platform_campaigns_admin
def campaigns_page():
    """Kampanya yönetimi ana sayfası."""
    return render_template("admin/campaigns.html")


@bp.route("/api/campaigns", methods=["GET"])
@platform_campaigns_admin
def api_campaigns_list():
    """Tüm kampanyaları listeler."""
    try:
        rows = fetch_all(
            """
            SELECT id, name, code, discount_percent, applies_to,
                   target_countries, start_date, end_date, is_active,
                   priority, created_at, updated_at
            FROM public.discount_campaigns
            ORDER BY is_active DESC, priority DESC, end_date DESC, id DESC
            """
        ) or []

        items = [_campaign_row_to_dict(r) for r in rows]

        # Sayaçlar
        counts = {
            "all": len(items),
            "active": sum(1 for x in items if x["status"] == "active"),
            "upcoming": sum(1 for x in items if x["status"] == "upcoming"),
            "expired": sum(1 for x in items if x["status"] in ("expired", "inactive")),
        }

        filter_param = str(request.args.get("filter") or "all").strip().lower()
        if filter_param == "active":
            filtered = [x for x in items if x["status"] == "active"]
        elif filter_param == "upcoming":
            filtered = [x for x in items if x["status"] == "upcoming"]
        elif filter_param == "expired":
            filtered = [x for x in items if x["status"] in ("expired", "inactive")]
        else:
            filtered = items

        return jsonify({"ok": True, "campaigns": filtered, "counts": counts})
    except Exception:
        logger.exception("api_campaigns_list")
        return jsonify({"ok": False, "mesaj": "Kampanyalar listelenemedi."}), 500


@bp.route("/api/campaigns", methods=["POST"])
@platform_campaigns_admin
def api_campaigns_create():
    """Yeni kampanya oluşturur."""
    body = request.get_json(silent=True) or {}
    try:
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 120:
            raise ValueError("Kampanya adı 1-120 karakter arasında olmalıdır.")

        code_raw = body.get("code")
        code = str(code_raw).strip().upper() if code_raw and str(code_raw).strip() else None
        if code and len(code) > 40:
            raise ValueError("Kupon kodu en fazla 40 karakter olabilir.")

        try:
            disc_pct = Decimal(str(body.get("discount_percent") or "0"))
            if disc_pct <= Decimal("0") or disc_pct > Decimal("100"):
                raise ValueError()
        except Exception:
            raise ValueError("İndirim yüzdesi 0'dan büyük, en fazla 100 olmalıdır.")

        applies_to = str(body.get("applies_to") or "all").strip().lower()
        if not re.match(r"^[a-z0-9_:]+$", applies_to):
            raise ValueError("Geçersiz applies_to formatı.")

        countries_raw = body.get("target_countries")
        if isinstance(countries_raw, list):
            target_countries = [str(c).strip().upper() for c in countries_raw if str(c).strip()]
        elif isinstance(countries_raw, str):
            target_countries = [c.strip().upper() for c in countries_raw.split(",") if c.strip()]
        else:
            target_countries = ["ALL"]

        if not target_countries:
            target_countries = ["ALL"]

        start_date = _parse_iso_datetime(body.get("start_date"), "Başlangıç tarihi")
        end_date = _parse_iso_datetime(body.get("end_date"), "Bitiş tarihi")

        if end_date <= start_date:
            raise ValueError("Bitiş tarihi başlangıç tarihinden sonra olmalıdır.")

        is_active = bool(body.get("is_active", True))
        priority = int(body.get("priority") or 10)

        row = execute_returning(
            """
            INSERT INTO public.discount_campaigns
                (name, code, discount_percent, applies_to, target_countries,
                 start_date, end_date, is_active, priority, created_at, updated_at)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id, name, code, discount_percent, applies_to,
                      target_countries, start_date, end_date, is_active,
                      priority, created_at, updated_at
            """,
            (
                name,
                code,
                str(disc_pct),
                applies_to,
                target_countries,
                start_date,
                end_date,
                is_active,
                priority,
            ),
        )

        if not row:
            raise RuntimeError("Kampanya kaydedilemedi.")

        logger.info(f"Yeni kampanya oluşturuldu: id={row['id']}, name='{name}'")
        _invalidate_all_pricing_caches()
        return jsonify({"ok": True, "campaign": _campaign_row_to_dict(row)})

    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("api_campaigns_create")
        return jsonify({"ok": False, "mesaj": f"Kampanya oluşturulamadı: {e}"}), 500


@bp.route("/api/campaigns/<int:campaign_id>", methods=["PUT"])
@platform_campaigns_admin
def api_campaigns_update(campaign_id: int):
    """Kampanya günceller."""
    body = request.get_json(silent=True) or {}
    try:
        existing = fetch_one(
            "SELECT id FROM public.discount_campaigns WHERE id = %s",
            (campaign_id,),
        )
        if not existing:
            return jsonify({"ok": False, "mesaj": "Kampanya bulunamadı."}), 404

        name = str(body.get("name") or "").strip()
        if not name or len(name) > 120:
            raise ValueError("Kampanya adı 1-120 karakter arasında olmalıdır.")

        code_raw = body.get("code")
        code = str(code_raw).strip().upper() if code_raw and str(code_raw).strip() else None
        if code and len(code) > 40:
            raise ValueError("Kupon kodu en fazla 40 karakter olabilir.")

        try:
            disc_pct = Decimal(str(body.get("discount_percent") or "0"))
            if disc_pct <= Decimal("0") or disc_pct > Decimal("100"):
                raise ValueError()
        except Exception:
            raise ValueError("İndirim yüzdesi 0'dan büyük, en fazla 100 olmalıdır.")

        applies_to = str(body.get("applies_to") or "all").strip().lower()
        if not re.match(r"^[a-z0-9_:]+$", applies_to):
            raise ValueError("Geçersiz applies_to formatı.")

        countries_raw = body.get("target_countries")
        if isinstance(countries_raw, list):
            target_countries = [str(c).strip().upper() for c in countries_raw if str(c).strip()]
        elif isinstance(countries_raw, str):
            target_countries = [c.strip().upper() for c in countries_raw.split(",") if c.strip()]
        else:
            target_countries = ["ALL"]

        if not target_countries:
            target_countries = ["ALL"]

        start_date = _parse_iso_datetime(body.get("start_date"), "Başlangıç tarihi")
        end_date = _parse_iso_datetime(body.get("end_date"), "Bitiş tarihi")

        if end_date <= start_date:
            raise ValueError("Bitiş tarihi başlangıç tarihinden sonra olmalıdır.")

        is_active = bool(body.get("is_active", True))
        priority = int(body.get("priority") or 10)

        row = execute_returning(
            """
            UPDATE public.discount_campaigns
            SET name = %s,
                code = %s,
                discount_percent = %s,
                applies_to = %s,
                target_countries = %s,
                start_date = %s,
                end_date = %s,
                is_active = %s,
                priority = %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, name, code, discount_percent, applies_to,
                      target_countries, start_date, end_date, is_active,
                      priority, created_at, updated_at
            """,
            (
                name,
                code,
                str(disc_pct),
                applies_to,
                target_countries,
                start_date,
                end_date,
                is_active,
                priority,
                campaign_id,
            ),
        )

        _invalidate_all_pricing_caches()
        return jsonify({"ok": True, "campaign": _campaign_row_to_dict(row)})

    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("api_campaigns_update")
        return jsonify({"ok": False, "mesaj": f"Kampanya güncellenemedi: {e}"}), 500


@bp.route("/api/campaigns/<int:campaign_id>/toggle", methods=["POST"])
@platform_campaigns_admin
def api_campaigns_toggle(campaign_id: int):
    """Kampanyayı tek tıkla aktif/pasif yapar."""
    try:
        row = execute_returning(
            """
            UPDATE public.discount_campaigns
            SET is_active = NOT is_active, updated_at = NOW()
            WHERE id = %s
            RETURNING id, name, code, discount_percent, applies_to,
                      target_countries, start_date, end_date, is_active,
                      priority, created_at, updated_at
            """,
            (campaign_id,),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "Kampanya bulunamadı."}), 404

        _invalidate_all_pricing_caches()
        return jsonify({
            "ok": True,
            "campaign": _campaign_row_to_dict(row),
            "mesaj": f"Kampanya {'aktif edildi' if row['is_active'] else 'durduruldu'}.",
        })
    except Exception as e:
        logger.exception("api_campaigns_toggle")
        return jsonify({"ok": False, "mesaj": "İşlem gerçekleştirilemedi."}), 500


@bp.route("/api/campaigns/<int:campaign_id>/extend", methods=["POST"])
@platform_campaigns_admin
def api_campaigns_extend(campaign_id: int):
    """Kampanyanın bitiş tarihini X gün uzatır (varsayılan 30 gün)."""
    body = request.get_json(silent=True) or {}
    days = int(body.get("days") or 30)
    if days <= 0:
        return jsonify({"ok": False, "mesaj": "Uzatma gün sayısı pozitif olmalıdır."}), 400

    try:
        row = execute_returning(
            """
            UPDATE public.discount_campaigns
            SET end_date = end_date + (%s * INTERVAL '1 day'),
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, name, code, discount_percent, applies_to,
                      target_countries, start_date, end_date, is_active,
                      priority, created_at, updated_at
            """,
            (days, campaign_id),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "Kampanya bulunamadı."}), 404

        _invalidate_all_pricing_caches()
        return jsonify({
            "ok": True,
            "campaign": _campaign_row_to_dict(row),
            "mesaj": f"Kampanya süresi {days} gün uzatıldı.",
        })
    except Exception as e:
        logger.exception("api_campaigns_extend")
        return jsonify({"ok": False, "mesaj": "Kampanya süresi uzatılamadı."}), 500


@bp.route("/api/campaigns/<int:campaign_id>", methods=["DELETE"])
@platform_campaigns_admin
def api_campaigns_delete(campaign_id: int):
    """Kampanyayı siler."""
    try:
        row = execute_returning(
            """
            DELETE FROM public.discount_campaigns
            WHERE id = %s
            RETURNING id, name
            """,
            (campaign_id,),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "Kampanya bulunamadı."}), 404

        logger.info(f"Kampanya silindi: id={row['id']}, name='{row['name']}'")
        _invalidate_all_pricing_caches()
        return jsonify({"ok": True, "mesaj": "Kampanya başarıyla silindi."})
    except Exception as e:
        logger.exception("api_campaigns_delete")
        return jsonify({"ok": False, "mesaj": "Kampanya silinemedi."}), 500


@bp.route("/api/exchange-rates/status", methods=["GET"])
@platform_campaigns_admin
def api_exchange_rates_status():
    """Döviz kurları durumunu ve ana kurları döner."""
    try:
        sample_rows = fetch_all(
            """
            SELECT base_currency, target_currency, rate, fetched_at, source
            FROM public.exchange_rates
            WHERE base_currency = 'USD' AND target_currency IN ('TRY', 'EUR', 'GBP', 'AZN', 'AED', 'SAR')
            ORDER BY target_currency
            """
        ) or []

        latest_row = fetch_one(
            """
            SELECT MAX(fetched_at) AS last_fetched, COUNT(*) AS total_rates
            FROM public.exchange_rates
            """
        )

        last_dt = latest_row.get("last_fetched") if latest_row else None
        rates_dict = {
            r["target_currency"]: {
                "rate": float(r["rate"]),
                "fetched_at": r["fetched_at"].isoformat() if hasattr(r["fetched_at"], "isoformat") else str(r["fetched_at"]),
                "source": r.get("source") or "DB",
            }
            for r in sample_rows
        }

        return jsonify({
            "ok": True,
            "last_fetched": last_dt.isoformat() if hasattr(last_dt, "isoformat") else str(last_dt),
            "total_rates": int(latest_row.get("total_rates") or 0) if latest_row else 0,
            "sample_rates": rates_dict,
        })
    except Exception as e:
        logger.exception("api_exchange_rates_status")
        return jsonify({"ok": False, "mesaj": "Döviz kuru durumu alınamadı."}), 500


@bp.route("/api/exchange-rates/refresh", methods=["POST"])
@platform_campaigns_admin
def api_exchange_rates_refresh():
    """Döviz kurlarını open.er-api.com üzerinden canlı olarak günceller."""
    try:
        res = fetch_and_store_exchange_rates()
        if not res.get("ok"):
            return jsonify({"ok": False, "mesaj": f"Döviz kurları güncellenemedi: {res.get('error')}"}), 502

        _invalidate_all_pricing_caches()
        # Güncel durumu çek
        status_resp = api_exchange_rates_status()
        status_data = status_resp.get_json() if hasattr(status_resp, "get_json") else status_resp[0].get_json()

        return jsonify({
            "ok": True,
            "mesaj": f"Döviz kurları başarıyla güncellendi ({res.get('rates_count')} kur kaydedildi).",
            "rates_count": res.get("rates_count"),
            "status": status_data,
        })
    except Exception as e:
        logger.exception("api_exchange_rates_refresh")
        return jsonify({"ok": False, "mesaj": "Döviz kuru güncelleme işlemi başarısız oldu."}), 500
