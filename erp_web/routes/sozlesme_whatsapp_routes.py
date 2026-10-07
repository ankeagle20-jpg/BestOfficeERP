"""Sözleşmeler WhatsApp: girişli gönderim. Metin ve tam numara loglanmaz."""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user

from auth import giris_gerekli
from sozlesme_whatsapp import sozlesme_wa_durum, sozlesme_wa_isle, tahsilat_wa_hazir, tahsilat_wa_isle

bp = Blueprint("sozlesme_whatsapp", __name__)


def _uid() -> int:
    try:
        return int(getattr(current_user, "id", 0) or 0)
    except (TypeError, ValueError):
        return 0


@bp.route("/giris/api/sozlesme-whatsapp/kisiler")
@giris_gerekli
def api_kisiler():
    from routes.odeme_linki_routes import _musteri, _wa_kisiler

    try:
        mid = int(request.args.get("musteri_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "Önce müşteri seçin."}), 400
    if mid <= 0 or not _musteri(mid):
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı"}), 404
    return jsonify({"ok": True, "kisiler": _wa_kisiler(mid)})


@bp.route("/giris/api/sozlesme-whatsapp/gonder", methods=["POST"])
@giris_gerekli
def api_gonder():
    data = request.get_json(silent=True) or {}
    govde, kod = sozlesme_wa_isle(
        _uid(),
        data,
        request.headers.get("Origin") or "",
        request.host or "",
    )
    return jsonify(govde), kod


@bp.route("/giris/api/sozlesme-whatsapp/durum")
@giris_gerekli
def api_durum():
    govde, kod = sozlesme_wa_durum(_uid(), request.args.get("deneme") or "")
    return jsonify(govde), kod


@bp.route("/giris/api/tahsilat-whatsapp/hazir")
@giris_gerekli
def api_tahsilat_hazir():
    try:
        tid = int(request.args.get("tahsilat_id") or 0)
    except (TypeError, ValueError):
        tid = 0
    govde, kod = tahsilat_wa_hazir(tid)
    return jsonify(govde), kod


@bp.route("/giris/api/tahsilat-whatsapp/gonder", methods=["POST"])
@giris_gerekli
def api_tahsilat_gonder():
    data = request.get_json(silent=True) or {}
    govde, kod = tahsilat_wa_isle(
        _uid(),
        data,
        request.headers.get("Origin") or "",
        request.host or "",
    )
    return jsonify(govde), kod
