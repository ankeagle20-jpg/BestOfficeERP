# -*- coding: utf-8 -*-
"""Demirbaş kataloğu, seri no ve DEMIRBAS_TABLOSU bayrakları.

Bayrak: DEMIRBAS_TABLOSU (varsayılan 0) → herkese açık.
Liste: DEMIRBAS_TABLOSU_MIDLER (virgüllü mid; boş/tanımsız = kimse).
SOZLESME_SABLON_V2_MIDLER ile aynı karar mantığı.
Prod'da bayrak açmayın; DDL'i ensure_musteri_demirbas_table ile ayrı onayda çalıştırın.
"""
from __future__ import annotations

import os
import re
import secrets
from typing import Any

# Katalog: (kapsam, grup_adi, esya_adi)
# kapsam: oda_ici | ortak_alan
DEMIRBAS_KATALOG: list[tuple[str, str, str]] = [
    # Çalışma Alanı
    ("oda_ici", "Çalışma Alanı", "Çalışma masası"),
    ("oda_ici", "Çalışma Alanı", "Yönetici masası"),
    ("oda_ici", "Çalışma Alanı", "Keson"),
    ("oda_ici", "Çalışma Alanı", "Çalışma koltuğu"),
    ("oda_ici", "Çalışma Alanı", "Misafir koltuğu"),
    ("oda_ici", "Çalışma Alanı", "Misafir sandalyesi"),
    # Depolama
    ("oda_ici", "Depolama", "Dosya dolabı"),
    ("oda_ici", "Depolama", "Kapaklı dolap"),
    ("oda_ici", "Depolama", "Açık raf"),
    ("oda_ici", "Depolama", "Kitaplık"),
    ("oda_ici", "Depolama", "Askılık"),
    # Teknoloji
    ("oda_ici", "Teknoloji", "Monitör"),
    ("oda_ici", "Teknoloji", "Masaüstü bilgisayar"),
    ("oda_ici", "Teknoloji", "Mini PC"),
    ("oda_ici", "Teknoloji", "Dock istasyonu"),
    ("oda_ici", "Teknoloji", "Telefon cihazı"),
    ("oda_ici", "Teknoloji", "Çoklu priz"),
    ("oda_ici", "Teknoloji", "Uzatma kablosu"),
    ("oda_ici", "Teknoloji", "HDMI kablosu"),
    ("oda_ici", "Teknoloji", "Ağ kablosu"),
    ("oda_ici", "Teknoloji", "Klima kumandası"),
    ("oda_ici", "Teknoloji", "Termostat kumandası"),
    # Aydınlatma ve Aksesuar
    ("oda_ici", "Aydınlatma ve Aksesuar", "Masa lambası"),
    ("oda_ici", "Aydınlatma ve Aksesuar", "Çöp kovası"),
    ("oda_ici", "Aydınlatma ve Aksesuar", "Yazı tahtası"),
    ("oda_ici", "Aydınlatma ve Aksesuar", "Cam yazı tahtası"),
    ("oda_ici", "Aydınlatma ve Aksesuar", "Mantar pano"),
    ("oda_ici", "Aydınlatma ve Aksesuar", "Perde"),
    ("oda_ici", "Aydınlatma ve Aksesuar", "Jaluzi"),
    ("oda_ici", "Aydınlatma ve Aksesuar", "Stor perde"),
    ("oda_ici", "Aydınlatma ve Aksesuar", "Kapı anahtarı"),
    ("oda_ici", "Aydınlatma ve Aksesuar", "Erişim kartı"),
    # Ortak Alan (oda dışı)
    ("ortak_alan", "Ortak Alan (oda dışı)", "Yazıcı"),
    ("ortak_alan", "Ortak Alan (oda dışı)", "Tarayıcı"),
    ("ortak_alan", "Ortak Alan (oda dışı)", "Kahve makinesi"),
    ("ortak_alan", "Ortak Alan (oda dışı)", "Çay makinesi"),
    ("ortak_alan", "Ortak Alan (oda dışı)", "Su sebili"),
    ("ortak_alan", "Ortak Alan (oda dışı)", "Toplantı odası TV"),
    ("ortak_alan", "Ortak Alan (oda dışı)", "Projeksiyon"),
]

DEMIRBAS_DIGER = "Diğer (elle yaz)"
_SERI_RE = re.compile(r"^[A-Z]{2,4}[0-9]{12}$")
_TR_MAP = str.maketrans(
    {
        "ç": "C",
        "Ç": "C",
        "ğ": "G",
        "Ğ": "G",
        "ı": "I",
        "I": "I",
        "İ": "I",
        "i": "I",
        "ö": "O",
        "Ö": "O",
        "ş": "S",
        "Ş": "S",
        "ü": "U",
        "Ü": "U",
    }
)


def demirbas_tablosu_global_acik() -> bool:
    raw = str(os.environ.get("DEMIRBAS_TABLOSU") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def demirbas_tablosu_midler() -> set[int]:
    raw = os.environ.get("DEMIRBAS_TABLOSU_MIDLER")
    if raw is None:
        return set()
    out: set[int] = set()
    for part in str(raw).split(","):
        tok = part.strip()
        if not tok:
            continue
        try:
            mid = int(tok)
        except (TypeError, ValueError):
            continue
        if mid > 0:
            out.add(mid)
    return out


def demirbas_tablosu_aktif(mid=None) -> bool:
    """Tek karar noktası: global bayrak veya mid listesi."""
    if demirbas_tablosu_global_acik():
        return True
    if mid is None or mid == "":
        return False
    try:
        mid_i = int(mid)
    except (TypeError, ValueError):
        return False
    if mid_i <= 0:
        return False
    return mid_i in demirbas_tablosu_midler()


def demirbas_katalog_fe() -> list[dict[str, str]]:
    """FE dropdown: grup + esya + kapsam."""
    return [
        {"kapsam": k, "grup": g, "esya": e}
        for (k, g, e) in DEMIRBAS_KATALOG
    ]


def demirbas_kapsam_for_esya(esya: str) -> str:
    e = (esya or "").strip()
    for k, _g, ad in DEMIRBAS_KATALOG:
        if ad == e:
            return k
    return "oda_ici"


def demirbas_seri_oneki(esya_adi: str) -> str:
    """Eşya adından 2–4 harflik önek (A-Z)."""
    raw = (esya_adi or "").strip()
    if not raw:
        return "XX"
    # Kelimeler: boşluk / tire
    parts = [p for p in re.split(r"[\s\-]+", raw) if p]
    letters: list[str] = []
    if len(parts) == 1:
        w = parts[0].translate(_TR_MAP).upper()
        w = re.sub(r"[^A-Z]", "", w)
        letters = list(w[:2])
    else:
        n = 4 if len(parts) >= 4 else len(parts)
        for p in parts[:n]:
            w = p.translate(_TR_MAP).upper()
            w = re.sub(r"[^A-Z]", "", w)
            if w:
                letters.append(w[0])
    while len(letters) < 2:
        letters.append("X")
    return "".join(letters[:4])


def demirbas_seri_no_uret(esya_adi: str, *, rng=None) -> str:
    """Önek + 12 haneli rastgele sayı. rng: secrets-benzeri (test için)."""
    onek = demirbas_seri_oneki(esya_adi)
    if rng is None:
        digits = "".join(str(secrets.randbelow(10)) for _ in range(12))
    else:
        digits = "".join(str(rng.randrange(10)) for _ in range(12))
    return f"{onek}{digits}"


def demirbas_seri_no_gecerli(seri: str) -> bool:
    return bool(_SERI_RE.fullmatch(str(seri or "")))


def demirbas_seri_no_uret_benzersiz(
    esya_adi: str,
    *,
    exists_fn,
    max_deneme: int = 20,
    rng=None,
) -> str:
    """exists_fn(seri) → True ise çakışma. En fazla max_deneme."""
    last = ""
    for _ in range(max(1, int(max_deneme))):
        last = demirbas_seri_no_uret(esya_adi, rng=rng)
        try:
            if not exists_fn(last):
                return last
        except Exception:
            return last
    raise RuntimeError("demirbas_seri_no_cakisma")


def demirbas_satirlari_ek2_sirali(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Oda içi önce, ortak alan sonra; ortak alan öncesi başlık satırı eklenmez burada —
    render tarafı başlık ekler. Bu fonksiyon yalnızca sıralar."""
    oda = [r for r in rows if (r.get("kapsam") or "oda_ici") != "ortak_alan"]
    ortak = [r for r in rows if (r.get("kapsam") or "") == "ortak_alan"]
    return oda + ortak
