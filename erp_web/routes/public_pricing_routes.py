# -*- coding: utf-8 -*-
"""Public: Payafin fiyatlandırma salt-okuma API (kimlik doğrulama yok)."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from module_pricing_public_cache import get_public_module_pricing
from pricing_public_cache import get_public_pricing
from routes.signup_routes import platform_public_only

logger = logging.getLogger(__name__)

bp = Blueprint("public_pricing", __name__)


def _country_query_param() -> str:
    cc = (request.args.get("country") or request.args.get("country_code") or "TR").strip().upper()
    if not cc or len(cc) != 2 or not cc.isalpha():
        raise ValueError("geçersiz country")
    return cc


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


@bp.route("/fiyatlandirma", methods=["GET"])
@platform_public_only
def fiyatlandirma_page():
    """Payafin apex — görsel fiyatlandırma sayfası (herkese açık)."""
    return render_template("marketing/fiyatlandirma.html")
