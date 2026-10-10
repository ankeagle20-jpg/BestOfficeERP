# -*- coding: utf-8 -*-
"""Plan değiştir: faturalı ay kilit sınıflandırması.

Saf modül. DB'ye dokunmaz, hiçbir şey yazmaz. Girdi: fatura satırı sözlükleri
(musteri_id, id, notlar, toplam, fatura_tarihi, durum, yon, ettn) ve isteğe bağlı
tahsilat haritaları.

Sınıflar (ay bazında):
  K1  GİB'e gönderilmiş: ettn dolu, ya da notta GİB İMZALANDI / GİB durum: imzali /
      GİB durum: taslak / GİB ETTN: etiketi var. KİLİTLİ.
  K2  K1 değil, o ayda tahsilat var (ay haritası > 0 ya da fatura_id bağlı tahsilat). KİLİTLİ.
  K0  Belirsiz: işaretsiz satır, aynı ayda birden çok kayıt, çok aylı işaret, tahsilat
      bilgisi okunamadı. KİLİTLİ.
  K3  Serbest: ERP içi işaretli (|AYLIK_TUTAR| / |AUTO_INV|), GİB'siz, tahsilatsız. KİLİT DEĞİL.

Geçersiz sayılan (hiçbir sınıfa girmeyen) kayıtlar: gelen fatura, iptal/taslak durum,
|GIB_NO_TASINDI|, "GİB durum: iptal" notu, GİB izi taşımayan "ERP durum: taslak".
Tek başına GİB biçimli fatura_no (GIB + 13 hane) kilit sinyali DEĞİLDİR.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime

K1 = "K1"
K2 = "K2"
K0 = "K0"
K3 = "K3"
KILITLI = (K1, K2, K0)
_ONCELIK = {K1: 0, K2: 1, K0: 2, K3: 3}
_ESIK = 0.004

_ERP_TASLAK = re.compile(r"ERP\s+durum\s*:\s*taslak", re.IGNORECASE)
_GIB_TASLAK = re.compile(r"G.?B\s+durum\s*:\s*taslak", re.IGNORECASE)
_GIB_IPTAL = re.compile(r"G.?B\s+durum\s*:\s*iptal", re.IGNORECASE)
_GIB_IMZA = re.compile(r"G.?B\s+IMZALANDI|G.?B\s+durum\s*:\s*imzal[ıi]?\b", re.IGNORECASE)
_GIB_ETTN_ETIKET = re.compile(r"G.?B\s+ETTN\s*:", re.IGNORECASE)
_ISARET_AYLIK = re.compile(r"\|AYLIK_TUTAR\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|")
_ISARET_AUTO = re.compile(r"\|AUTO_INV\|([0-9]{4}-[0-9]{2})\|")

NEDEN_ETTN = "ettn dolu"
NEDEN_IMZA = "GİB imzalı notu"
NEDEN_GIB_TASLAK = "GİB durum: taslak notu"
NEDEN_ETTN_ETIKET = "GİB ETTN etiketi (ettn kolonu boş)"
NEDEN_TAHSILAT_AY = "tahsilat var (ay haritası)"
NEDEN_TAHSILAT_FATURA = "fatura bağlı tahsilat var"
NEDEN_ISARETSIZ = "işaretsiz kayıt (yıllık/elle kesilmiş olabilir)"
NEDEN_COK_AYLI = "çok aylı işaret (yıllık olabilir)"
NEDEN_COK_KAYIT = "aynı ayda birden çok kayıt"
NEDEN_TAHSILAT_YOK_BILGI = "tahsilat bilgisi okunamadı"
NEDEN_K3 = "ERP içi borç kaydı, GİB'siz, tahsilatsız"


def not_norm(notlar) -> str:
    return str(notlar or "").replace("İ", "I").replace("ı", "i").replace("\ufffd", "I")


def _ettn_dolu(row) -> bool:
    return bool(str((row or {}).get("ettn") or "").strip())


def gib_sinyali(row) -> str | None:
    """K1 nedeni; GİB izi yoksa None. Sıra: ettn, imza, GİB taslak, ETTN etiketi."""
    if not isinstance(row, dict):
        return None
    if _ettn_dolu(row):
        return NEDEN_ETTN
    norm = not_norm(row.get("notlar"))
    if _GIB_IMZA.search(norm):
        return NEDEN_IMZA
    if _GIB_TASLAK.search(norm):
        return NEDEN_GIB_TASLAK
    if _GIB_ETTN_ETIKET.search(norm):
        return NEDEN_ETTN_ETIKET
    return None


def kayit_gecerli(row) -> bool:
    """Kilit değerlendirmesine giren giden fatura kaydı mı."""
    if not isinstance(row, dict):
        return False
    durum = str(row.get("durum") or "").strip().lower()
    if durum in ("iptal", "taslak"):
        return False
    yon = str(row.get("yon") or "giden").strip().lower()
    if yon == "gelen":
        return False
    notlar = str(row.get("notlar") or "")
    if "|GIB_NO_TASINDI|" in notlar:
        return False
    norm = not_norm(notlar)
    if _GIB_IPTAL.search(norm):
        return False
    if _ERP_TASLAK.search(norm) and gib_sinyali(row) is None:
        return False
    return True


def _tarih_ay(ft) -> str | None:
    if isinstance(ft, datetime):
        ft = ft.date()
    if isinstance(ft, date):
        return f"{ft.year:04d}-{ft.month:02d}"
    s = str(ft or "").strip()[:10]
    if len(s) >= 7 and s[4] == "-":
        try:
            y, m = int(s[0:4]), int(s[5:7])
        except ValueError:
            return None
        if 1 <= m <= 12:
            return f"{y:04d}-{m:02d}"
    return None


def ay_kaynakli(row) -> list[tuple[str, str]]:
    """(ay, kaynak). İşaret varsa yalnız işaret; yoksa fatura_tarihi."""
    notlar = str((row or {}).get("notlar") or "")
    isaret = set()
    for iso in _ISARET_AYLIK.findall(notlar):
        isaret.add(iso[:7])
    for ym in _ISARET_AUTO.findall(notlar):
        isaret.add(ym)
    if isaret:
        return [(ay, "isaret") for ay in sorted(isaret)]
    ay = _tarih_ay((row or {}).get("fatura_tarihi"))
    return [(ay, "fatura_tarihi")] if ay else []


def kayit_sinifla(row) -> dict | None:
    """Satır düzeyi sınıf. None: geçersiz kayıt.

    Dönen: {"sinif": K1|K0|K3, "neden", "aylar": [(ay, kaynak)], "tutar"}.
    K2 ve "aynı ayda birden çok kayıt" ay düzeyinde ay_siniflari içinde belirlenir.
    """
    if not kayit_gecerli(row):
        return None
    try:
        tutar = float((row or {}).get("toplam") or 0)
    except (TypeError, ValueError):
        tutar = 0.0
    if not math.isfinite(tutar):
        tutar = 0.0
    aylar = ay_kaynakli(row)
    gib = gib_sinyali(row)
    if gib:
        sinif, neden = K1, gib
    elif aylar and aylar[0][1] != "isaret":
        sinif, neden = K0, NEDEN_ISARETSIZ
    elif len(aylar) > 1:
        sinif, neden = K0, NEDEN_COK_AYLI
    else:
        sinif, neden = K3, NEDEN_K3
    return {"sinif": sinif, "neden": neden, "aylar": aylar, "tutar": tutar}


def _mid(row):
    try:
        m = int((row or {}).get("musteri_id") or 0)
    except (TypeError, ValueError):
        return 0
    return m if m > 0 else 0


def _fid(row):
    try:
        return int((row or {}).get("id") or 0)
    except (TypeError, ValueError):
        return 0


def ay_siniflari(rows, tahsil_ay=None, fid_tahsil=None, tahsil_bilinmiyor=None) -> dict:
    """mid -> {ay: {"ay","sinif","neden","fatura_tutari","kaynak","kayit_sayisi"}}.

    tahsil_ay: {mid: {YYYY-MM: ödenen}}. fid_tahsil: {fatura_id: ödenen}.
    tahsil_bilinmiyor: tahsilat bilgisi okunamayan müşteri kümesi; K3 adayı K0'a çekilir.
    Öncelik: K1 > K2 > K0 > K3.
    """
    tahsil_ay = tahsil_ay or {}
    fid_tahsil = fid_tahsil or {}
    bilinmiyor = set(tahsil_bilinmiyor or ())
    birikim: dict[int, dict] = {}
    for row in rows or []:
        mid = _mid(row)
        if not mid:
            continue
        s = kayit_sinifla(row)
        if s is None:
            continue
        fid = _fid(row)
        fid_odenen = 0.0
        try:
            fid_odenen = float(fid_tahsil.get(fid) or 0) if fid else 0.0
        except (TypeError, ValueError):
            fid_odenen = 0.0
        for ay, kaynak in s["aylar"]:
            slot = birikim.setdefault(mid, {}).setdefault(
                ay, {"adaylar": [], "tutar": 0.0, "kaynak": kaynak, "kayit": 0, "fid_odenen": 0.0}
            )
            slot["adaylar"].append((s["sinif"], s["neden"]))
            slot["tutar"] = round(float(slot["tutar"]) + s["tutar"], 2)
            slot["kayit"] += 1
            slot["fid_odenen"] += fid_odenen
            if kaynak == "isaret":
                slot["kaynak"] = "isaret"
    out: dict[int, dict] = {}
    for mid, aylar in birikim.items():
        harita = tahsil_ay.get(mid) or {}
        for ay, slot in aylar.items():
            k1 = [n for (c, n) in slot["adaylar"] if c == K1]
            k0 = [n for (c, n) in slot["adaylar"] if c == K0]
            ay_odenen = 0.0
            try:
                ay_odenen = float(harita.get(ay) or 0)
            except (TypeError, ValueError):
                ay_odenen = 0.0
            if k1:
                sinif, neden = K1, k1[0]
            elif ay_odenen > _ESIK:
                sinif, neden = K2, NEDEN_TAHSILAT_AY
            elif slot["fid_odenen"] > _ESIK:
                sinif, neden = K2, NEDEN_TAHSILAT_FATURA
            elif k0:
                sinif, neden = K0, k0[0]
            elif slot["kayit"] > 1:
                sinif, neden = K0, NEDEN_COK_KAYIT
            elif mid in bilinmiyor:
                sinif, neden = K0, NEDEN_TAHSILAT_YOK_BILGI
            else:
                sinif, neden = K3, NEDEN_K3
            if sinif != K1 and slot["kayit"] > 1 and sinif == K2:
                neden = neden + "; " + NEDEN_COK_KAYIT
            out.setdefault(mid, {})[ay] = {
                "ay": ay,
                "sinif": sinif,
                "neden": neden,
                "fatura_tutari": round(float(slot["tutar"]), 2),
                "kaynak": slot["kaynak"],
                "kayit_sayisi": int(slot["kayit"]),
            }
    return out


def kilitli_mi(sinif) -> bool:
    return sinif in KILITLI


def kilit_listesi(siniflar_mid: dict) -> list[dict]:
    """Tek müşteri: kilitli (K1/K2/K0) aylar, ay sırasıyla."""
    return [
        {
            "ay": v["ay"],
            "fatura_tutari": v["fatura_tutari"],
            "kaynak": v["kaynak"],
            "sinif": v["sinif"],
            "neden": v["neden"],
        }
        for _ay, v in sorted((siniflar_mid or {}).items())
        if v["sinif"] in KILITLI
    ]


def serbest_liste(siniflar_mid: dict) -> list[dict]:
    """Tek müşteri: K3 aylar, ay sırasıyla."""
    return [
        {
            "ay": v["ay"],
            "fatura_tutari": v["fatura_tutari"],
            "kaynak": v["kaynak"],
            "sinif": v["sinif"],
            "neden": v["neden"],
        }
        for _ay, v in sorted((siniflar_mid or {}).items())
        if v["sinif"] == K3
    ]
