# -*- coding: utf-8 -*-
"""Müşteriler grid popup WhatsApp: önizleme + gönderim.

Gönderim yalnız sozlesme_wa_isle. Telefon/metin loglanmaz.
"""
from __future__ import annotations

import math
import time
import uuid
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Europe/Istanbul")
KILIT_SN = 60
_MESAJ_KILIT: dict[tuple[int, str, str], float] = {}

_SABLON_AD = {
    0: "Vade günü",
    3: "3 gün",
    7: "7 gün",
    15: "15 gün",
    21: "21 gün",
    30: "30 gün",
    60: "60 gün",
    90: "90 gün",
    180: "180 gün",
    365: "365 gün",
}


def kilit_temizle() -> None:
    """Test yardımcı."""
    _MESAJ_KILIT.clear()


def mesai_disi_mi(simdi: datetime | None = None) -> bool:
    """Europe/Istanbul 09:00–20:00 dışı → True (yumuşak uyarı)."""
    an = simdi or datetime.now(IST)
    if an.tzinfo is None:
        an = an.replace(tzinfo=IST)
    else:
        an = an.astimezone(IST)
    dk = int(an.hour) * 60 + int(an.minute)
    return not (9 * 60 <= dk <= 20 * 60)


def _tutar_tr(v) -> str:
    from routes.giris_routes import _tutar_tr_goster

    return _tutar_tr_goster(v)


def _gecikme_gun(payload, bugun: date) -> int:
    """asistan _wa_gecikme_gun ile aynı: en eski ödenmemiş ayın 1'inden bugüne gün."""
    if not isinstance(payload, dict):
        return 0
    ref = date(bugun.year, bugun.month, 1)
    en = None
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            yy = int(a.get("yil"))
            mm = int(a.get("ay"))
            cur = date(yy, mm, 1)
        except (TypeError, ValueError):
            continue
        if cur > ref:
            continue
        if bool(a.get("tahsil_edildi")) and not bool(a.get("kismi_tahsilat")):
            continue
        try:
            tut = float(a.get("tutar_kdv_dahil") or a.get("brut_tutar_kdv") or 0)
        except (TypeError, ValueError):
            tut = 0.0
        if tut <= 0.005:
            continue
        if en is None or cur < en:
            en = cur
    if en is None:
        return 0
    return max(0, (bugun - en).days)


def _sozlesme_gun_oku(row: dict | None) -> int:
    if not row:
        return 0
    for key in ("giris_tarihi", "sozlesme_tarihi", "baslangic_tarihi"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            s = str(raw).strip()[:10]
            if len(s) == 10:
                g = int(date.fromisoformat(s).day)
                if 1 <= g <= 31:
                    return g
        except (TypeError, ValueError):
            continue
    return 0


def _musteri_yukle(mid: int, fetch_one_fn=None) -> dict | None:
    fn = fetch_one_fn
    if fn is None:
        from db import fetch_one

        fn = fetch_one
    row = fn(
        """
        SELECT c.id,
               COALESCE(NULLIF(BTRIM(c.name), ''), '') AS name,
               NULLIF(BTRIM(c.phone), '') AS phone,
               NULLIF(BTRIM(c.phone2), '') AS phone2,
               LOWER(BTRIM(COALESCE(c.durum, ''))) AS durum,
               NULLIF(BTRIM(COALESCE(mk.sozlesme_tarihi::text, '')), '') AS giris_tarihi
        FROM customers c
        LEFT JOIN musteri_kyc mk ON mk.musteri_id = c.id
        WHERE c.id = %s
        LIMIT 1
        """,
        (int(mid),),
    )
    return row if row else None


def _payload_oku(mid: int, oku_fn=None):
    if oku_fn is not None:
        return oku_fn(int(mid))
    from routes.erp_notlar_routes import _ozet_grid_payload_oku

    paket = _ozet_grid_payload_oku([int(mid)])
    if not isinstance(paket, dict):
        return None
    return paket.get(int(mid))


def borc_hesapla(mid: int, *, bugun: date | None = None, fetch_one_fn=None, payload_fn=None) -> dict:
    """Sunucu tutarları: ham + gun_gelen. İstemci tutarı yok."""
    bugun = bugun or datetime.now(IST).date()
    row = _musteri_yukle(mid, fetch_one_fn=fetch_one_fn)
    if not row:
        return {"ok": False, "kod": 404, "mesaj": "Müşteri bulunamadı"}
    payload = _payload_oku(mid, oku_fn=payload_fn)
    from routes.giris_routes import _firma_ozet_ozet_from_grid_cache_payload
    from routes.faturalar_routes import _firma_ozet_gun_bazli_borc_uygula_satirlar

    ozet = None
    if isinstance(payload, dict):
        ozet = _firma_ozet_ozet_from_grid_cache_payload(payload, bugun.year, bugun.month)
    if not isinstance(ozet, dict) or ozet.get("toplam_borc") is None:
        return {
            "ok": True,
            "grid_yok": True,
            "ham": None,
            "gun_gelen": None,
            "gun": 0,
            "sozlesme_gun": _sozlesme_gun_oku(row),
            "pasif": str(row.get("durum") or "") == "pasif",
            "name": str(row.get("name") or "").strip() or "Müşteri",
            "phone": row.get("phone"),
            "phone2": row.get("phone2"),
            "payload": payload,
        }
    sg = _sozlesme_gun_oku(row)
    it = {
        "toplam_borc": ozet.get("toplam_borc"),
        "borc_hesaplanamadi": False,
        "sozlesme_gun": sg,
        "borc_ref_ay": ozet.get("borc_ref_ay"),
        "geciken_ay": ozet.get("geciken_ay"),
    }
    _firma_ozet_gun_bazli_borc_uygula_satirlar([it], aktif=False, bugun=bugun)
    gun = _gecikme_gun(payload, bugun)
    return {
        "ok": True,
        "grid_yok": False,
        "ham": it.get("toplam_borc_ham"),
        "gun_gelen": it.get("toplam_borc_gun_gelen"),
        "gun": gun,
        "sozlesme_gun": sg,
        "pasif": str(row.get("durum") or "") == "pasif",
        "name": str(row.get("name") or "").strip() or "Müşteri",
        "phone": row.get("phone"),
        "phone2": row.get("phone2"),
        "payload": payload,
    }


def _telefonlar(phone, phone2) -> list:
    from routes.erp_notlar_routes import _wa_telefon_listesi

    return _wa_telefon_listesi(phone, phone2)


def _numara_kartlari(tels: list) -> list:
    from routes.erp_notlar_routes import _wa_numara_kartlari

    return _wa_numara_kartlari(tels)


def _tutar_sec(borc: dict, tutar_turu: str):
    tur = str(tutar_turu or "").strip().lower()
    if tur == "toplam":
        return borc.get("ham"), "toplam"
    if tur in ("gun_gelen", "gunu_gelen", "gelen"):
        return borc.get("gun_gelen"), "gun_gelen"
    return None, ""


def _sablon_metin(sablon_id, isim: str, tutar_yazi: str, gun: int) -> tuple[str, str]:
    from routes.whatsapp_routes import SABLONLAR

    try:
        anahtar = int(sablon_id)
    except (TypeError, ValueError):
        return "", ""
    ham = SABLONLAR.get(anahtar)
    if not ham:
        return "", ""
    try:
        dolu = ham.format(isim=isim, tutar=tutar_yazi, gun=int(gun or 0))
    except Exception:
        dolu = ""
    return str(anahtar), dolu


def _uyarilar(borc: dict, simdi: datetime | None) -> list:
    out = []
    if borc.get("pasif"):
        out.append("musteri_pasif")
    if mesai_disi_mi(simdi):
        out.append("mesai_disi")
    return out


def onizleme(
    mid: int,
    tutar_turu: str,
    sablon_id=None,
    *,
    bugun: date | None = None,
    simdi: datetime | None = None,
    fetch_one_fn=None,
    payload_fn=None,
    node_fn: Callable[[], bool] | None = None,
) -> tuple[dict, int]:
    from sozlesme_whatsapp import node_yolu_acik

    if not (node_fn or node_yolu_acik)():
        return {"ok": False, "kapali": True, "mesaj": "WhatsApp servisi kapalı", "wa_acik": False}, 403

    borc = borc_hesapla(mid, bugun=bugun, fetch_one_fn=fetch_one_fn, payload_fn=payload_fn)
    if not borc.get("ok"):
        return {"ok": False, "mesaj": borc.get("mesaj") or "Müşteri bulunamadı"}, int(borc.get("kod") or 404)

    tutar, tur = _tutar_sec(borc, tutar_turu)
    if not tur:
        return {"ok": False, "mesaj": "Geçersiz tutar türü"}, 400

    tels = _telefonlar(borc.get("phone"), borc.get("phone2"))
    uyarilar = _uyarilar(borc, simdi)
    from routes.whatsapp_routes import SABLONLAR

    tutar_yazi = "—" if tutar is None else _tutar_tr(tutar)
    gun = int(borc.get("gun") or 0)
    isim = borc.get("name") or "Müşteri"
    liste = []
    for anahtar in sorted(SABLONLAR):
        _sid, dolu = _sablon_metin(anahtar, isim, tutar_yazi, gun)
        liste.append({
            "anahtar": str(anahtar),
            "etiket": _SABLON_AD.get(anahtar, str(anahtar)),
            "metin": "" if borc.get("grid_yok") else dolu,
            "engel": bool(borc.get("grid_yok")),
        })

    secili = str(sablon_id).strip() if sablon_id is not None and str(sablon_id).strip() != "" else (
        liste[0]["anahtar"] if liste else ""
    )
    onizleme_metin = ""
    for s in liste:
        if s["anahtar"] == secili:
            onizleme_metin = s.get("metin") or ""
            break

    try:
        tutar_sayi = None if tutar is None else round(float(tutar), 2)
    except (TypeError, ValueError):
        tutar_sayi = None

    return {
        "ok": True,
        "wa_acik": True,
        "tutar_turu": tur,
        "tutar": tutar_sayi,
        "grid_yok": bool(borc.get("grid_yok")),
        "uyarilar": uyarilar,
        "numaralar": _numara_kartlari(tels),
        "numara_adet": len(tels),
        "sablonlar": liste,
        "sablon_id": secili,
        "onizleme": onizleme_metin,
        "varsayilan_alici": "1",
    }, 200


def _alici_coz(tels: list, alici_secimi: str, ozel_numara) -> tuple[str | None, str, str]:
    """(norm_tel, alici_tipi, hata)."""
    sec = str(alici_secimi or "1").strip().lower()
    if sec in ("1", "birinci", "bir"):
        if not tels:
            return None, "1", "Telefon yok"
        return tels[0], "1", ""
    if sec in ("2", "ikinci", "iki"):
        if len(tels) < 2:
            return None, "2", "2. numara yok"
        return tels[1], "2", ""
    if sec in ("ozel", "serbest", "baska"):
        from odeme_linki import normalize_wa_telefon

        norm = normalize_wa_telefon(ozel_numara)
        if not norm:
            return None, "ozel", "Telefon numarası geçersiz"
        return norm, "ozel", ""
    return None, "", "Geçersiz alıcı"


def _audit(uid: int, mid: int, anlik: dict, calistir=None) -> None:
    try:
        from audit_log import audit_yaz
        from db import execute

        fn = calistir or execute
        audit_yaz(
            fn,
            olay="grid_wa_gonder",
            varlik="grid_wa_popup",
            kayit_id=int(mid),
            musteri_id=int(mid),
            kullanici=str(uid),
            anlik=dict(anlik or {}),
        )
    except Exception:
        pass


def gonder(
    uid: int,
    mid: int,
    data: dict,
    origin: str,
    host: str,
    *,
    bugun: date | None = None,
    simdi: datetime | None = None,
    fetch_one_fn=None,
    payload_fn=None,
    node_fn: Callable[[], bool] | None = None,
    gonder_fn=None,
    audit_fn=None,
    now_mono: Callable[[], float] | None = None,
) -> tuple[dict, int]:
    """Metin ve tutar sunucuda; gönderim sozlesme_wa_isle."""
    from sozlesme_whatsapp import deneme_gecerli, node_yolu_acik, sozlesme_wa_isle

    if not (node_fn or node_yolu_acik)():
        return {"ok": False, "kapali": True, "mesaj": "WhatsApp servisi kapalı", "wa_acik": False}, 403

    data = data or {}
    # İstemci tutar/metin yok sayılır
    tutar_turu = str(data.get("tutar_turu") or "").strip()
    sablon_id = data.get("sablon_id")
    if sablon_id is None:
        sablon_id = data.get("sablon")

    borc = borc_hesapla(mid, bugun=bugun, fetch_one_fn=fetch_one_fn, payload_fn=payload_fn)
    if not borc.get("ok"):
        return {"ok": False, "mesaj": borc.get("mesaj") or "Müşteri bulunamadı"}, int(borc.get("kod") or 404)

    tutar, tur = _tutar_sec(borc, tutar_turu)
    if not tur:
        return {"ok": False, "mesaj": "Geçersiz tutar türü"}, 400
    try:
        tutar_f = float(tutar) if tutar is not None else 0.0
    except (TypeError, ValueError):
        tutar_f = 0.0
    if not math.isfinite(tutar_f) or tutar_f <= 0:
        return {"ok": False, "mesaj": "Gönderilecek borç yok"}, 400

    if borc.get("pasif"):
        onay = data.get("pasif_onay")
        if str(onay).strip() not in ("1", "true", "True", "evet", "on"):
            return {"ok": False, "mesaj": "Pasif müşteri için onay gerekli", "uyarilar": ["musteri_pasif"]}, 400

    sid, metin = _sablon_metin(
        sablon_id,
        borc.get("name") or "Müşteri",
        _tutar_tr(tutar_f),
        int(borc.get("gun") or 0),
    )
    if not sid or not metin:
        return {"ok": False, "mesaj": "Şablon bulunamadı"}, 400

    tels = _telefonlar(borc.get("phone"), borc.get("phone2"))
    tel, alici_tip, hata = _alici_coz(tels, data.get("alici_secimi"), data.get("ozel_numara"))
    if hata:
        return {"ok": False, "mesaj": hata}, 400

    kilit_anahtar = (int(mid), tur, str(sid))
    mono = now_mono or time.monotonic
    simdi_m = float(mono())
    once = _MESAJ_KILIT.get(kilit_anahtar)
    if once is not None and (simdi_m - once) < KILIT_SN:
        return {"ok": False, "mesaj": "Çok sık gönderim; biraz bekleyin"}, 409

    deneme = deneme_gecerli(data.get("deneme"))
    if not deneme:
        deneme = str(uuid.uuid4())

    if alici_tip == "ozel":
        try:
            from sozlesme_whatsapp import serbest_numara_audit

            serbest_numara_audit(int(uid), int(mid), tel, tels)
        except Exception:
            pass

    fn = gonder_fn or sozlesme_wa_isle
    try:
        govde, kod = fn(
            int(uid),
            {
                "buton": "ust",
                "musteri_id": int(mid),
                "mesaj": metin,
                "telefon": tel,
                "onay": True,
                "deneme": deneme,
                "serbest": alici_tip == "ozel",
            },
            origin,
            host,
        )
    except Exception:
        _audit(uid, mid, {
            "tutar_turu": tur,
            "tutar": round(tutar_f, 2),
            "sablon_id": sid,
            "alici_secimi": alici_tip,
            "sonuc": "hata",
        }, calistir=audit_fn)
        return {"ok": False, "mesaj": "Gönderilemedi"}, 500

    ok = bool((govde or {}).get("ok"))
    if ok or int(kod) in (200, 201):
        _MESAJ_KILIT[kilit_anahtar] = simdi_m

    sonuc = "ok" if ok else "hata"
    if (govde or {}).get("tekrar"):
        sonuc = "tekrar"
    _audit(uid, mid, {
        "tutar_turu": tur,
        "tutar": round(tutar_f, 2),
        "sablon_id": sid,
        "alici_secimi": alici_tip,
        "sonuc": sonuc,
        "http": int(kod),
    }, calistir=audit_fn)

    mesaj = str((govde or {}).get("mesaj") or ("Gönderildi" if ok else "Gönderilemedi"))
    out = {
        "ok": ok,
        "mesaj": mesaj,
        "durum": (govde or {}).get("durum") or ("gonderiliyor" if ok else "hata"),
        "uyarilar": _uyarilar(borc, simdi),
        "tutar_turu": tur,
        "sablon_id": sid,
        "alici_secimi": alici_tip,
    }
    if (govde or {}).get("tekrar"):
        out["tekrar"] = True
    return out, int(kod)
