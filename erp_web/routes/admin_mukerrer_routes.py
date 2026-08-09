# -*- coding: utf-8 -*-
"""Admin: Mükerrer müşteri arşiv aracı (A4 salt görüntüleme)."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from auth import admin_gerekli

logger = logging.getLogger(__name__)

bp = Blueprint("admin_mukerrer", __name__)


@bp.route("/mukerrer-arsiv")
@admin_gerekli
def mukerrer_arsiv_page():
    return render_template(
        "admin/mukerrer_arsiv.html",
        username=getattr(current_user, "username", "") or "",
    )


@bp.route("/api/mukerrer-analiz")
@admin_gerekli
def api_mukerrer_analiz():
    """Salt okunur JSON — yalnızca COK_YUKSEK / YUKSEK grupları."""
    try:
        from services.mukerrer_analiz_service import build_mukerrer_groups

        guven = (request.args.get("guven") or "hepsi").strip()
        payload = build_mukerrer_groups(guven=guven)
        # Savunma: tehlikeli tier sızmasın
        safe_groups = []
        for g in payload.get("groups") or []:
            if (g.get("tier") or "") not in ("COK_YUKSEK", "YUKSEK"):
                continue
            safe_groups.append(g)
        payload["groups"] = safe_groups
        meta = payload.setdefault("meta", {})
        meta["returned_n"] = len(safe_groups)
        meta["readonly"] = True
        return jsonify(payload)
    except Exception:
        logger.exception("api_mukerrer_analiz")
        return jsonify({"ok": False, "mesaj": "Mükerrer analizi hesaplanamadı."}), 500
