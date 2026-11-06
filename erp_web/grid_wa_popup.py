# -*- coding: utf-8 -*-
"""Müşteriler grid popup WhatsApp: önizleme + gönderim.

Gönderim yalnız sozlesme_wa_isle. Telefon/metin loglanmaz.
"""
from __future__ import annotations

import hashlib
import math
import time
import uuid
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Europe/Istanbul")
KILIT_SN = 60
# Anahtar: (mid, tutar_turu, sablon_id, tel_ozet) — alıcı dahil; 2. numara 1.'yi engellemez.
_MESAJ_KILIT: dict[tuple[int, str, str, str], float] = {}

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

_ALICI_IZIN = frozenset({"1", "2", "ozel"})


def kilit_temizle() -> None:
    """Test yardımcı."""
    _MESAJ_KILIT.clear()


def _tel_ozet(norm: str) -> str:
    """Kilit anahtarı için kısa özet; loglanmaz."""
    return hashlib.sha256(str(norm or "").encode("utf-8")).hexdigest()[:16]


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
               NULLIF(BTRIM(COALESCE(mk.yetkili_tel_aciklama, '')), '') AS phone_kime,
               NULLIF(BTRIM(COALESCE(mk.yetkili_tel2_aciklama, '')), '') AS phone2_kime,
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
            "phone_kime": str(row.get("phone_kime") or "").strip(),
            "phone2_kime": str(row.get("phone2_kime") or "").strip(),
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
        "phone_kime": str(row.get("phone_kime") or "").strip(),
        "phone2_kime": str(row.get("phone2_kime") or "").strip(),
        "payload": payload,
    }


def _telefonlar(phone, phone2) -> list:
    from routes.erp_notlar_routes import _wa_telefon_listesi

    return _wa_telefon_listesi(phone, phone2)


def _alici_onizleme_kartlari(phone, phone2, ad1: str = "", ad2: str = "") -> list:
    """Yalnız grid-wa önizleme: açık biçimli numara + sahip adı. Asistan maskesini değiştirmez."""
    from odeme_linki import normalize_wa_telefon
    from routes.erp_notlar_routes import _wa_bicim

    out: list[dict] = []
    n1 = normalize_wa_telefon(phone)
    n2 = normalize_wa_telefon(phone2)
    if n1:
        out.append({
            "slot": "1",
            "numara_goster": _wa_bicim(n1) or "",
            "ad": str(ad1 or "").strip(),
        })
    if n2 and n2 != n1:
        out.append({
            "slot": "2",
            "numara_goster": _wa_bicim(n2) or "",
            "ad": str(ad2 or "").strip(),
        })
    return out


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
    numaralar = _alici_onizleme_kartlari(
        borc.get("phone"),
        borc.get("phone2"),
        borc.get("phone_kime") or "",
        borc.get("phone2_kime") or "",
    )
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
        "numaralar": numaralar,
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


def _alicilar_oku(data: dict) -> tuple[list[str] | None, str]:
    """alicilar dizisi veya eski alici_secimi → tekil slot listesi."""
    ham = data.get("alicilar")
    if ham is None:
        eski = data.get("alici_secimi")
        if eski is None or str(eski).strip() == "":
            return None, "Alıcı seçilmedi"
        ham = [eski]
    if not isinstance(ham, (list, tuple)):
        return None, "Geçersiz alıcı listesi"
    if len(ham) == 0:
        return None, "Alıcı seçilmedi"
    if len(ham) > 3:
        return None, "En fazla 3 alıcı"
    out: list[str] = []
    gorulen: set[str] = set()
    for x in ham:
        tip = str(x or "").strip().lower()
        if tip in ("birinci", "bir"):
            tip = "1"
        elif tip in ("ikinci", "iki"):
            tip = "2"
        elif tip in ("serbest", "baska"):
            tip = "ozel"
        if tip not in _ALICI_IZIN:
            return None, "Geçersiz alıcı"
        if tip in gorulen:
            continue
        gorulen.add(tip)
        out.append(tip)
    if not out:
        return None, "Alıcı seçilmedi"
    return out, ""


def _alici_listesi_coz(tels: list, slotlar: list[str], ozel_numara) -> tuple[list[tuple[str, str]] | None, str, bool]:
    """[(alici_tip, norm_tel), ...] numara tekilleştirilmiş; (liste, hata, tekillestirildi)."""
    ham: list[tuple[str, str]] = []
    for tip in slotlar:
        tel, tip2, hata = _alici_coz(tels, tip, ozel_numara)
        if hata:
            return None, hata, False
        ham.append((tip2, str(tel)))
    tekil: list[tuple[str, str]] = []
    gorulen: set[str] = set()
    for tip, tel in ham:
        if tel in gorulen:
            continue
        gorulen.add(tel)
        tekil.append((tip, tel))
    return tekil, "", len(tekil) < len(ham)


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


def _sonuc_satir(alici: str, durum: str, neden: str = "") -> dict:
    return {"alici": alici, "durum": durum, "neden": str(neden or "")}


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
    """Metin ve tutar sunucuda; gönderim sozlesme_wa_isle (alıcı başına)."""
    from sozlesme_whatsapp import deneme_gecerli, node_yolu_acik, sozlesme_wa_isle

    if not (node_fn or node_yolu_acik)():
        return {"ok": False, "kapali": True, "mesaj": "WhatsApp servisi kapalı", "wa_acik": False}, 403

    data = data or {}
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

    slotlar, hata_slot = _alicilar_oku(data)
    if hata_slot:
        return {"ok": False, "mesaj": hata_slot}, 400

    tels = _telefonlar(borc.get("phone"), borc.get("phone2"))
    hedefler, hata_tel, tekillestirildi = _alici_listesi_coz(tels, slotlar, data.get("ozel_numara"))
    if hata_tel:
        return {"ok": False, "mesaj": hata_tel}, 400

    mono = now_mono or time.monotonic
    simdi_m = float(mono())
    fn = gonder_fn or sozlesme_wa_isle
    sonuclar: list[dict] = []
    calls_ok = 0
    calls_fail = 0
    http_kod = 200

    for _idx, (alici_tip, tel) in enumerate(hedefler):
        kilit_anahtar = (int(mid), tur, str(sid), _tel_ozet(tel))
        once = _MESAJ_KILIT.get(kilit_anahtar)
        if once is not None and (simdi_m - once) < KILIT_SN:
            sat = _sonuc_satir(alici_tip, "engellendi", "Çok sık gönderim; biraz bekleyin")
            sonuclar.append(sat)
            calls_fail += 1
            _audit(uid, mid, {
                "tutar_turu": tur,
                "tutar": round(tutar_f, 2),
                "sablon_id": sid,
                "alici_secimi": alici_tip,
                "sonuc": "engellendi",
                "http": 409,
            }, calistir=audit_fn)
            if calls_ok == 0:
                http_kod = 409
            continue

        if alici_tip == "ozel":
            try:
                from sozlesme_whatsapp import serbest_numara_audit

                serbest_numara_audit(int(uid), int(mid), tel, tels)
            except Exception:
                pass

        # Alıcı başına benzersiz deneme — deneme anahtarı diğer alıcıyı engellemesin
        deneme = str(uuid.uuid4())
        # İstemci deneme verdiyse yalnızca ilk alıcıda kullan; sonrakiler yeni UUID
        if _idx == 0:
            kim = deneme_gecerli(data.get("deneme"))
            if kim:
                deneme = kim

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
            sonuclar.append(_sonuc_satir(alici_tip, "hata", "Gönderilemedi"))
            calls_fail += 1
            _audit(uid, mid, {
                "tutar_turu": tur,
                "tutar": round(tutar_f, 2),
                "sablon_id": sid,
                "alici_secimi": alici_tip,
                "sonuc": "hata",
            }, calistir=audit_fn)
            http_kod = 500
            continue

        ok = bool((govde or {}).get("ok"))
        if ok or int(kod) in (200, 201):
            _MESAJ_KILIT[kilit_anahtar] = simdi_m
            durum = "gonderildi"
            neden = str((govde or {}).get("mesaj") or "")
            calls_ok += 1
            sonuc_audit = "ok"
            if (govde or {}).get("tekrar"):
                durum = "engellendi"
                neden = str((govde or {}).get("mesaj") or "Tekrar")
                sonuc_audit = "tekrar"
                calls_ok -= 1
                calls_fail += 1
        else:
            durum = "engellendi" if (govde or {}).get("tekrar") or int(kod) == 409 else "hata"
            neden = str((govde or {}).get("mesaj") or "Gönderilemedi")
            calls_fail += 1
            sonuc_audit = "tekrar" if (govde or {}).get("tekrar") else "hata"
            if http_kod == 200:
                http_kod = int(kod) if int(kod) >= 400 else 400

        sonuclar.append(_sonuc_satir(alici_tip, durum, neden))
        _audit(uid, mid, {
            "tutar_turu": tur,
            "tutar": round(tutar_f, 2),
            "sablon_id": sid,
            "alici_secimi": alici_tip,
            "sonuc": sonuc_audit,
            "http": int(kod),
        }, calistir=audit_fn)

    hepsi_ok = calls_ok > 0 and calls_fail == 0
    kismi = calls_ok > 0 and calls_fail > 0
    if hepsi_ok:
        http_kod = 200
    elif kismi:
        http_kod = 200
    elif calls_fail and http_kod == 200:
        http_kod = 400

    mesaj = "Gönderildi" if hepsi_ok else (
        "Kısmi gönderim" if kismi else (sonuclar[0].get("neden") if sonuclar else "Gönderilemedi")
    )
    out = {
        "ok": hepsi_ok or kismi,
        "mesaj": mesaj,
        "durum": "gonderiliyor" if (hepsi_ok or kismi) else "hata",
        "uyarilar": _uyarilar(borc, simdi),
        "tutar_turu": tur,
        "sablon_id": sid,
        "sonuclar": sonuclar,
        "alici_adet": len(sonuclar),
        "tekillestirildi": bool(tekillestirildi),
    }
    # Geriye uyumluluk: tek alıcıda eski alan
    if len(sonuclar) == 1:
        out["alici_secimi"] = sonuclar[0].get("alici")
        if sonuclar[0].get("durum") == "engellendi" and "sık" in str(sonuclar[0].get("neden") or "").lower():
            out["ok"] = False
            http_kod = 409
    return out, int(http_kod)
