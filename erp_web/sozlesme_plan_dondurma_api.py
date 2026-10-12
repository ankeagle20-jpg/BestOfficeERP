# -*- coding: utf-8 -*-
"""Plan dondurma uçları. Kapı plan değiştir ile aynı. Okuma ensure çalıştırmaz."""
from __future__ import annotations

import logging

from flask import jsonify, request

from auth import giris_gerekli
from sozlesme_plan_dondurma import ay_listesi, dondurma_onizleme
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


def _odenen_haritasi(mid, zincir, planlar, aday_aylar):
    """Baseline tahsilat: aday aylar dondurulmamış sayılır, diğer açık dondurmalar düşülür.

    Grid yeşili ile aynı FIFO (_aylik_tahsil_tutar_map) ve aynı brüt (zincir).
    """
    from sozlesme_plan import ay_bazli_tutarlar
    from routes.giris_routes import _aylik_tahsil_tutar_map

    remaining = {}
    for satir in ay_bazli_tutarlar(dict(zincir or {}), planlar):
        try:
            brut = round(float(satir.get("brut") or 0), 2)
        except (TypeError, ValueError):
            continue
        ay = str(satir.get("ay") or "")
        if brut > 0 and len(ay) == 7:
            remaining[ay + "-01"] = brut
    ham = _aylik_tahsil_tutar_map(
        int(mid),
        remaining_by_iso=remaining,
        haric_dondurma_aylar=set(aday_aylar or ()),
    )
    out = {}
    for iso, tut in (ham or {}).items():
        try:
            out[str(iso)[:7]] = round(float(tut or 0), 2)
        except (TypeError, ValueError):
            continue
    return out


def _eski_dondurma_aylari(mid, data) -> set:
    """Düzenlenen açık kaydın ayları. Okuma; ensure yok. Kayıt yoksa boş küme."""
    ham = (data or {}).get("duzenlenen_dondurma_id")
    if ham in (None, ""):
        return set()
    try:
        pid = int(ham)
    except (TypeError, ValueError):
        return set()
    try:
        from db import fetch_one

        row = fetch_one(SQL_BY_ID, (pid,))
    except Exception:
        _LOG.exception("eski dondurma okunamadi")
        return set()
    if not row or row.get("iptal_at") or row.get("silindi_at"):
        return set()
    if int(row.get("musteri_id") or 0) != int(mid):
        return set()
    from sozlesme_plan_dondurma_depo import _json_list

    return {str(a)[:7] for a in _json_list(row.get("dondurulan_aylar")) if a}


def _onizleme_hesap(mid, kyc, bas, n, haric_aylar=None):
    siniflar = _siniflar(mid)
    zincir, planlar = _zincir_ve_plan(mid, kyc)
    if not zincir:
        raise ValueError("Sözleşme zinciri hesaplanamadı.")
    aday = ay_listesi(bas, n)
    haric = {str(a)[:7] for a in aday} | {str(a)[:7] for a in (haric_aylar or set())}
    odenen = _odenen_haritasi(mid, zincir, planlar, haric)
    from routes.giris_routes import AYLIK_GRID_TAM_ODENDI_TOLERANS

    return dondurma_onizleme(
        zincir, planlar, bas, n, siniflar, odenen, AYLIK_GRID_TAM_ODENDI_TOLERANS
    )


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
        onizleme = _onizleme_hesap(mid, kyc, bas, n, _eski_dondurma_aylari(mid, data))
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
        onizleme = _onizleme_hesap(mid, kyc, bas, n, _eski_dondurma_aylari(mid, data))
    except ValueError as exc:
        return jsonify({"ok": False, "mesaj": str(exc)}), 400
    except Exception:
        _LOG.exception("dondurma kayit onizleme")
        return jsonify({"ok": False, "mesaj": "Dondurma hesaplanamadı."}), 500
    if not onizleme["dondurulan_aylar"]:
        return jsonify({"ok": False, "mesaj": "Dondurulacak ay yok.", "atlanan_aylar": onizleme["atlanan_aylar"]}), 400

    def islem(calistir, oku_bir, oku_cok):
        from audit_log import anlik_al, audit_yaz

        _ensure_sema(calistir)
        anlik = None
        kaynak = data.get("duzenlenen_dondurma_id")
        if kaynak not in (None, ""):
            mevcut = oku_bir(SQL_BY_ID, (int(kaynak),))
            if isinstance(mevcut, list):
                mevcut = mevcut[0] if mevcut else None
            if not mevcut or mevcut.get("iptal_at") or mevcut.get("silindi_at"):
                raise DondurmaYok("acik dondurma yok")
            if int(mevcut.get("musteri_id") or 0) != int(mid):
                raise PermissionError("mid")
            anlik = anlik_al(
                mevcut,
                ("id", "musteri_id", "baslangic_ay", "ay_sayisi", "dondurulan_aylar"),
            )
            dondurma_iptal(calistir, oku_bir, dondurma_id=int(kaynak), iptal_eden=_kim())
        row = dondurma_ekle(
            calistir,
            oku_cok,
            musteri_id=int(mid),
            baslangic_ay=bas,
            ay_sayisi=n,
            dondurulan_aylar=onizleme["dondurulan_aylar"],
            atlanan_aylar=onizleme["atlanan_aylar"],
            olusturan=_kim(),
        )
        if anlik:
            audit_yaz(
                calistir,
                olay="dondurma_duzenle",
                varlik="sozlesme_plan_dondurma",
                kayit_id=anlik.get("id"),
                musteri_id=mid,
                kullanici=_kim(),
                anlik=anlik,
                yeni=anlik_al(
                    row,
                    ("id", "musteri_id", "baslangic_ay", "ay_sayisi", "dondurulan_aylar"),
                ),
            )
        return row

    try:
        row = plan_tx(islem)
    except PermissionError:
        return _yok()
    except DondurmaYok:
        return jsonify({"ok": False, "mesaj": "Açık dondurma yok."}), 404
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
        from audit_log import anlik_al, audit_yaz

        anlik = anlik_al(
            mevcut,
            ("id", "musteri_id", "baslangic_ay", "ay_sayisi", "dondurulan_aylar"),
        )
        row = dondurma_iptal(calistir, oku_bir, dondurma_id=int(dondurma_id), iptal_eden=_kim())
        yeni = dict(anlik)
        yeni["durum"] = "iptal"
        audit_yaz(
            calistir,
            olay="dondurma_kaldir",
            varlik="sozlesme_plan_dondurma",
            kayit_id=anlik.get("id"),
            musteri_id=mid,
            kullanici=_kim(),
            anlik=anlik,
            yeni=yeni,
        )
        return row

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


def dondurma_sil_yanit(mid, dondurma_id):
    if not _kapi_acik(mid):
        return _kapali()
    data = request.get_json(silent=True) or {}
    if _mid_uyusmaz(mid, data):
        return _yok()
    musteri, _kyc = _musteri_ve_kyc(mid)
    if not musteri:
        return _yok()

    def islem(calistir, oku_bir, _oku_cok):
        from audit_log import anlik_al, audit_yaz
        from sozlesme_plan_dondurma_depo import dondurma_sil

        _ensure_sema(calistir)
        mevcut = oku_bir(SQL_BY_ID, (int(dondurma_id),))
        if isinstance(mevcut, list):
            mevcut = mevcut[0] if mevcut else None
        if not mevcut or mevcut.get("silindi_at"):
            raise DondurmaYok("dondurma yok")
        if int(mevcut.get("musteri_id") or 0) != int(mid):
            raise PermissionError("mid")
        anlik = anlik_al(
            mevcut,
            ("id", "musteri_id", "baslangic_ay", "ay_sayisi", "dondurulan_aylar"),
        )
        row, _acikti = dondurma_sil(calistir, oku_bir, dondurma_id=int(dondurma_id), silen=_kim())
        yeni = dict(anlik)
        yeni["durum"] = "silindi"
        audit_yaz(
            calistir,
            olay="dondurma_sil",
            varlik="sozlesme_plan_dondurma",
            kayit_id=anlik.get("id"),
            musteri_id=mid,
            kullanici=_kim(),
            anlik=anlik,
            yeni=yeni,
        )
        return row

    try:
        row = plan_tx(islem)
    except PermissionError:
        return _yok()
    except DondurmaYok:
        return jsonify({"ok": False, "mesaj": "Dondurma yok."}), 404
    except Exception:
        _LOG.exception("dondurma sil")
        return jsonify({"ok": False, "mesaj": "Dondurma gizlenemedi."}), 500
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

    @bp.route("/api/musteri/<int:mid>/plan/dondurma/<int:dondurma_id>/sil", methods=["POST"])
    @giris_gerekli
    def api_musteri_plan_dondurma_sil(mid, dondurma_id):
        return dondurma_sil_yanit(mid, dondurma_id)

    @bp.route("/api/musteri/<int:mid>/plan/dondurma", methods=["GET", "POST"])
    @giris_gerekli
    def api_musteri_plan_dondurma(mid):
        if request.method == "GET":
            return dondurma_liste_yanit(mid)
        return dondurma_ekle_yanit(mid)
