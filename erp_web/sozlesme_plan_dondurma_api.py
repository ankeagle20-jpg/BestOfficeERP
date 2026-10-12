# -*- coding: utf-8 -*-
"""Plan dondurma uçları. Kapı plan değiştir ile aynı. Okuma ensure çalıştırmaz."""
from __future__ import annotations

import logging

from flask import jsonify, request

from auth import giris_gerekli
from sozlesme_plan_dondurma import dondurma_onizleme
from sozlesme_plan_dondurma_depo import (
    DondurmaCakisma,
    DondurmaYok,
    SQL_BY_ID,
    dondurma_ekle,
    dondurma_iptal,
    dondurma_liste,
    ensure_sozlesme_plan_dondurma,
    ensure_sozlesme_plan_dondurma_semada,
)
from sozlesme_plan_api import (
    _ay_basi,
    _json_plan,
    _kapi_acik,
    _kim,
    _mid_uyusmaz,
    _musteri_ve_kyc,
    _sema,
    _siniflar,
    plan_tx,
)

_LOG = logging.getLogger(__name__)

NOT_DONDURMA = (
    "Dondurma; grid, borçlandırma/tahsilat panelleri, ekstre ve ödeme tutarlarını 0 yapar. "
    "Cari kart özeti ve fatura satırları (borç kayıtları) eski tutarda kalır."
)


def _kapali():
    return jsonify({"ok": False}), 404


def _yok():
    return jsonify({"ok": False, "mesaj": "Bu müşteri bu oturumda yok."}), 403


def _pasif():
    return jsonify({"ok": False, "mesaj": "Pasif müşteriye dondurma eklenemez."}), 400


def _ensure_sema(calistir):
    sema = _sema()
    if sema:
        ensure_sozlesme_plan_dondurma_semada(calistir, sema)
    else:
        ensure_sozlesme_plan_dondurma(calistir)


def _sozlesme_basi(kyc):
    if not kyc:
        return None
    ham = kyc.get("sozlesme_tarihi") or kyc.get("rent_start_date")
    if not ham:
        return None
    try:
        return _ay_basi(ham)
    except (TypeError, ValueError):
        return None


def _zincir_ve_plan(mid, kyc):
    from sozlesme_plan_api import _onizleme_zinciri

    zincir, planlar, _kutu = _onizleme_zinciri(mid, kyc)
    return zincir, planlar


def _govde(onizleme: dict) -> dict:
    return {
        "ok": True,
        "aylar": onizleme["aylar"],
        "dondurulan_aylar": onizleme["dondurulan_aylar"],
        "atlanan_aylar": onizleme["atlanan_aylar"],
        "not": NOT_DONDURMA,
    }


def dondurma_onizleme_yanit(mid):
    if not _kapi_acik(mid):
        return _kapali()
    data = request.get_json(silent=True) or {}
    if _mid_uyusmaz(mid, data):
        return _yok()
    musteri, kyc = _musteri_ve_kyc(mid)
    if not musteri:
        return _yok()
    try:
        bas = _ay_basi(data.get("baslangic_ay"))
        n = int(data.get("ay_sayisi"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "Başlangıç ayı ve ay sayısı (1-24) gerekli."}), 400
    soz = _sozlesme_basi(kyc)
    if soz and bas < soz:
        return jsonify({"ok": False, "mesaj": "Başlangıç ayı sözleşme başlangıcından önce olamaz."}), 400
    try:
        siniflar = _siniflar(mid)
        zincir, planlar = _zincir_ve_plan(mid, kyc)
        if not zincir:
            return jsonify({"ok": False, "mesaj": "Sözleşme zinciri hesaplanamadı."}), 400
        onizleme = dondurma_onizleme(zincir, planlar, bas, n, siniflar)
    except ValueError as exc:
        return jsonify({"ok": False, "mesaj": str(exc)}), 400
    except Exception:
        _LOG.exception("dondurma onizleme")
        return jsonify({"ok": False, "mesaj": "Önizleme hesaplanamadı."}), 500
    return jsonify(_govde(onizleme))


def dondurma_liste_yanit(mid):
    if not _kapi_acik(mid):
        return _kapali()
    musteri, _kyc = _musteri_ve_kyc(mid)
    if not musteri:
        return _yok()
    try:
        from db import fetch_all

        satirlar = dondurma_liste(
            lambda sql, params=None: fetch_all(sql, params or ()),
            musteri_id=int(mid),
            iptaller=True,
        )
    except Exception:
        _LOG.exception("dondurma liste")
        satirlar = []
    aktif = [_json_plan(s) for s in satirlar if not s.get("iptal_at")]
    return jsonify(
        {
            "ok": True,
            "aktif": aktif,
            "gecmis": [_json_plan(s) for s in satirlar],
            "not": NOT_DONDURMA,
        }
    )


def dondurma_ekle_yanit(mid):
    if not _kapi_acik(mid):
        return _kapali()
    data = request.get_json(silent=True) or {}
    if _mid_uyusmaz(mid, data):
        return _yok()
    musteri, kyc = _musteri_ve_kyc(mid)
    if not musteri:
        return _yok()
    if str(musteri.get("durum") or "").strip().lower() == "pasif":
        return _pasif()
    try:
        bas = _ay_basi(data.get("baslangic_ay"))
        n = int(data.get("ay_sayisi"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "Başlangıç ayı ve ay sayısı (1-24) gerekli."}), 400
    soz = _sozlesme_basi(kyc)
    if soz and bas < soz:
        return jsonify({"ok": False, "mesaj": "Başlangıç ayı sözleşme başlangıcından önce olamaz."}), 400
    try:
        siniflar = _siniflar(mid)
        zincir, planlar = _zincir_ve_plan(mid, kyc)
        if not zincir:
            return jsonify({"ok": False, "mesaj": "Sözleşme zinciri hesaplanamadı."}), 400
        onizleme = dondurma_onizleme(zincir, planlar, bas, n, siniflar)
    except ValueError as exc:
        return jsonify({"ok": False, "mesaj": str(exc)}), 400
    except Exception:
        _LOG.exception("dondurma kayit onizleme")
        return jsonify({"ok": False, "mesaj": "Dondurma hesaplanamadı."}), 500
    if not onizleme["dondurulan_aylar"]:
        return jsonify({"ok": False, "mesaj": "Dondurulacak ay yok.", "atlanan_aylar": onizleme["atlanan_aylar"]}), 400

    def islem(calistir, oku_bir, oku_cok):
        _ensure_sema(calistir)
        return dondurma_ekle(
            calistir,
            oku_cok,
            musteri_id=int(mid),
            baslangic_ay=bas,
            ay_sayisi=n,
            dondurulan_aylar=onizleme["dondurulan_aylar"],
            atlanan_aylar=onizleme["atlanan_aylar"],
            olusturan=_kim(),
        )

    try:
        row = plan_tx(islem)
    except DondurmaCakisma:
        return jsonify({"ok": False, "mesaj": "Bu aralıkta açık bir dondurma zaten var."}), 409
    except ValueError as exc:
        return jsonify({"ok": False, "mesaj": str(exc)}), 400
    except Exception as exc:
        metin = str(exc).lower()
        if "uq_sozlesme_plan_dondurma" in metin or "unique" in metin:
            return jsonify({"ok": False, "mesaj": "Bu aralıkta açık bir dondurma zaten var."}), 409
        _LOG.exception("dondurma kayit")
        return jsonify({"ok": False, "mesaj": "Dondurma kaydedilemedi."}), 500
    govde = _govde(onizleme)
    govde["dondurma"] = _json_plan(row)
    return jsonify(govde)


def dondurma_iptal_yanit(mid, dondurma_id):
    if not _kapi_acik(mid):
        return _kapali()
    data = request.get_json(silent=True) or {}
    if _mid_uyusmaz(mid, data):
        return _yok()
    musteri, _kyc = _musteri_ve_kyc(mid)
    if not musteri:
        return _yok()

    def islem(calistir, oku_bir, _oku_cok):
        _ensure_sema(calistir)
        mevcut = oku_bir(SQL_BY_ID, (int(dondurma_id),))
        if not mevcut or mevcut.get("iptal_at"):
            raise DondurmaYok("acik dondurma yok")
        if int(mevcut.get("musteri_id") or 0) != int(mid):
            raise PermissionError("mid")
        return dondurma_iptal(calistir, oku_bir, dondurma_id=int(dondurma_id), iptal_eden=_kim())

    try:
        row = plan_tx(islem)
    except PermissionError:
        return _yok()
    except DondurmaYok:
        return jsonify({"ok": False, "mesaj": "Açık dondurma yok."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "mesaj": str(exc)}), 400
    except Exception:
        _LOG.exception("dondurma iptal")
        return jsonify({"ok": False, "mesaj": "Dondurma kaldırılamadı."}), 500
    return jsonify({"ok": True, "dondurma": _json_plan(row)})


def dondurma_rotalarini_bagla(bp):
    @bp.route("/api/musteri/<int:mid>/plan/dondurma/onizleme", methods=["POST"])
    @giris_gerekli
    def api_musteri_plan_dondurma_onizleme(mid):
        return dondurma_onizleme_yanit(mid)

    @bp.route("/api/musteri/<int:mid>/plan/dondurma/<int:dondurma_id>/iptal", methods=["POST"])
    @giris_gerekli
    def api_musteri_plan_dondurma_iptal(mid, dondurma_id):
        return dondurma_iptal_yanit(mid, dondurma_id)

    @bp.route("/api/musteri/<int:mid>/plan/dondurma", methods=["GET", "POST"])
    @giris_gerekli
    def api_musteri_plan_dondurma(mid):
        if request.method == "GET":
            return dondurma_liste_yanit(mid)
        return dondurma_ekle_yanit(mid)
