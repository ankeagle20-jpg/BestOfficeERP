# -*- coding: utf-8 -*-
"""Admin: Mükerrer müşteri arşiv aracı (A4 salt görüntüleme + A5 yazma)."""
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
        hizmet_turu = (request.args.get("hizmet_turu") or "").strip()
        durum = (request.args.get("durum") or "").strip()
        payload = build_mukerrer_groups(
            guven=guven, hizmet_turu=hizmet_turu, durum=durum
        )
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


@bp.route("/api/mukerrer-arsivle", methods=["POST"])
@admin_gerekli
def api_mukerrer_arsivle():
    """A5: seçilen kopyaları arşiv meta ile işaretle (finans dokunulmaz)."""
    from services.mukerrer_arsiv_service import MukerrerArsivError, arsivle_grup

    try:
        body = request.get_json(silent=True) or {}
        sonuc = arsivle_grup(
            group_key=str(body.get("group_key") or ""),
            kanonik_id=body.get("kanonik_id"),
            arsiv_ids=body.get("arsiv_ids") or [],
            user_id=getattr(current_user, "id", None),
            onay=bool(body.get("onay")),
        )
        return jsonify(sonuc)
    except MukerrerArsivError as e:
        return jsonify({"ok": False, "mesaj": e.mesaj}), e.status
    except Exception:
        logger.exception("api_mukerrer_arsivle")
        return jsonify({"ok": False, "mesaj": "Arşivleme başarısız."}), 500


@bp.route("/api/mukerrer-geri-al", methods=["POST"])
@admin_gerekli
def api_mukerrer_geri_al():
    """A5: batch geri alma — yalnızca arsiv_nedeni=mukerrer_onay."""
    from services.mukerrer_arsiv_service import MukerrerArsivError, geri_al_batch

    try:
        body = request.get_json(silent=True) or {}
        sonuc = geri_al_batch(
            batch_id=body.get("batch_id"),
            user_id=getattr(current_user, "id", None),
            onay=bool(body.get("onay")),
        )
        return jsonify(sonuc)
    except MukerrerArsivError as e:
        return jsonify({"ok": False, "mesaj": e.mesaj}), e.status
    except Exception:
        logger.exception("api_mukerrer_geri_al")
        return jsonify({"ok": False, "mesaj": "Geri alma başarısız."}), 500


@bp.route("/api/mukerrer-arsiv-batchlar")
@admin_gerekli
def api_mukerrer_arsiv_batchlar():
    """A5: son batch listesi (geri alma UI)."""
    try:
        from services.mukerrer_arsiv_service import list_batches

        try:
            limit = int(request.args.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        batches = list_batches(limit=limit)
        return jsonify({"ok": True, "batches": batches, "n": len(batches)})
    except Exception:
        logger.exception("api_mukerrer_arsiv_batchlar")
        return jsonify({"ok": False, "mesaj": "Batch listesi alınamadı."}), 500
