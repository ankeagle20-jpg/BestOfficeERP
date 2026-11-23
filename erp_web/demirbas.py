# -*- coding: utf-8 -*-
"""Demirbaş kataloğu, seri no ve DEMIRBAS_TABLOSU bayrakları.

Bayrak: DEMIRBAS_TABLOSU (varsayılan 0) → herkese açık.
Liste: DEMIRBAS_TABLOSU_MIDLER (virgüllü mid; boş/tanımsız = kimse).
SOZLESME_SABLON_V2_MIDLER ile aynı karar mantığı.
Prod'da bayrak açmayın; DDL'i ensure_musteri_demirbas_table ile ayrı onayda çalıştırın.
"""
from __future__ import annotations

import html
import os
import re
import secrets
from typing import Any

# ui-tercih anahtarları (user_ui_preferences — kullanıcı bazlı; şirket KV yok, DDL yok)
DEMIRBAS_PREF_URUN_EK = "demirbas_urunler_ek"
DEMIRBAS_PREF_URUN_GIZLI = "demirbas_urunler_gizli"
DEMIRBAS_PREF_MARKA = "demirbas_markalar"

DEMIRBAS_GRUP_LISTESI: list[str] = [
    "Çalışma Alanı",
    "Depolama",
    "Teknoloji",
    "Aydınlatma ve Aksesuar",
    "Ortak Alan (oda dışı)",
]

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


def demirbas_tr_casefold(s: str) -> str:
    """Türkçe uyumlu büyük/küçük harf duyarsız karşılaştırma anahtarı (İ/ı, I/i)."""
    out: list[str] = []
    for ch in str(s or ""):
        if ch == "İ":
            out.append("i")
        elif ch == "I":
            out.append("ı")
        else:
            out.append(ch)
    return "".join(out).casefold()


def demirbas_urun_adi_temizle(raw) -> str | None:
    """2–40 karakter, trim, HTML etiketleri temizlenir. Geçersizse None."""
    s = str(raw or "").strip()
    s = re.sub(r"<[^>]*>", "", s)
    s = html.unescape(s).strip()
    if len(s) < 2 or len(s) > 40:
        return None
    return s


def demirbas_grup_kapsam(grup: str) -> str:
    g = str(grup or "").strip()
    if g == "Ortak Alan (oda dışı)" or demirbas_tr_casefold(g).startswith("ortak alan"):
        return "ortak_alan"
    return "oda_ici"


def demirbas_katalog_fe() -> list[dict[str, str]]:
    """FE dropdown: grup + esya + kapsam (yalnız hazır; birleşik için demirbas_katalog_birlesik)."""
    return [
        {"kapsam": k, "grup": g, "esya": e, "kaynak": "hazir"}
        for (k, g, e) in DEMIRBAS_KATALOG
    ]


def demirbas_katalog_birlesik(
    *,
    ek: list | None = None,
    gizli: list | None = None,
) -> list[dict[str, str]]:
    """hazır − gizlenenler + özel eklenenler (− gizlenenler). Her öğede kaynak=hazir|ozel."""
    gizli_keys = {
        demirbas_tr_casefold(str(x or "").strip())
        for x in (gizli or [])
        if str(x or "").strip()
    }
    hazir_keys = {demirbas_tr_casefold(e) for (_k, _g, e) in DEMIRBAS_KATALOG}
    out: list[dict[str, str]] = []
    for k, g, e in DEMIRBAS_KATALOG:
        if demirbas_tr_casefold(e) in gizli_keys:
            continue
        out.append({"kapsam": k, "grup": g, "esya": e, "kaynak": "hazir"})
    for it in ek or []:
        if not isinstance(it, dict):
            continue
        ad = demirbas_urun_adi_temizle(it.get("esya") or it.get("ad"))
        if not ad:
            continue
        key = demirbas_tr_casefold(ad)
        if key in hazir_keys:
            # hazır (görünür veya gizli) ile çakışan özel kayıt yok sayılır
            continue
        if key in gizli_keys:
            continue
        if any(demirbas_tr_casefold(x["esya"]) == key for x in out):
            continue
        grup = str(it.get("grup") or "").strip()
        if grup not in DEMIRBAS_GRUP_LISTESI:
            grup = DEMIRBAS_GRUP_LISTESI[0]
        kapsam = str(it.get("kapsam") or "").strip() or demirbas_grup_kapsam(grup)
        if kapsam not in ("oda_ici", "ortak_alan"):
            kapsam = demirbas_grup_kapsam(grup)
        out.append({"kapsam": kapsam, "grup": grup, "esya": ad, "kaynak": "ozel"})
    return out


def demirbas_panel_urunler(
    *,
    ek: list | None = None,
    gizli: list | None = None,
) -> list[dict[str, Any]]:
    """Panel: tüm hazır + özel; aktif=False → gizlenmiş (soluk, tekrar açılabilir)."""
    gizli_keys = {
        demirbas_tr_casefold(str(x or "").strip())
        for x in (gizli or [])
        if str(x or "").strip()
    }
    hazir_keys = {demirbas_tr_casefold(e) for (_k, _g, e) in DEMIRBAS_KATALOG}
    out: list[dict[str, Any]] = []
    for k, g, e in DEMIRBAS_KATALOG:
        out.append(
            {
                "kapsam": k,
                "grup": g,
                "esya": e,
                "kaynak": "hazir",
                "aktif": demirbas_tr_casefold(e) not in gizli_keys,
            }
        )
    for it in demirbas_ek_normalize(ek):
        ad = it["esya"]
        key = demirbas_tr_casefold(ad)
        if key in hazir_keys:
            continue
        if any(demirbas_tr_casefold(x["esya"]) == key for x in out):
            continue
        out.append(
            {
                "kapsam": it["kapsam"],
                "grup": it["grup"],
                "esya": ad,
                "kaynak": "ozel",
                "aktif": key not in gizli_keys,
            }
        )
    return out


def demirbas_kapsam_for_esya(esya: str, *, ek: list | None = None) -> str:
    e = (esya or "").strip()
    key = demirbas_tr_casefold(e)
    for k, _g, ad in DEMIRBAS_KATALOG:
        if demirbas_tr_casefold(ad) == key:
            return k
    for it in ek or []:
        if not isinstance(it, dict):
            continue
        ad = str(it.get("esya") or it.get("ad") or "").strip()
        if demirbas_tr_casefold(ad) != key:
            continue
        grup = str(it.get("grup") or "").strip()
        kapsam = str(it.get("kapsam") or "").strip()
        if kapsam in ("oda_ici", "ortak_alan"):
            return kapsam
        return demirbas_grup_kapsam(grup)
    return "oda_ici"


def demirbas_gizli_normalize(raw) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in raw or []:
        s = str(x or "").strip()
        if not s:
            continue
        k = demirbas_tr_casefold(s)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def demirbas_ek_normalize(raw) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        ad = demirbas_urun_adi_temizle(it.get("esya") or it.get("ad"))
        if not ad:
            continue
        k = demirbas_tr_casefold(ad)
        if k in seen:
            continue
        seen.add(k)
        grup = str(it.get("grup") or "").strip()
        if grup not in DEMIRBAS_GRUP_LISTESI:
            grup = DEMIRBAS_GRUP_LISTESI[0]
        kapsam = str(it.get("kapsam") or "").strip() or demirbas_grup_kapsam(grup)
        if kapsam not in ("oda_ici", "ortak_alan"):
            kapsam = demirbas_grup_kapsam(grup)
        out.append({"esya": ad, "grup": grup, "kapsam": kapsam})
    return out


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


DEMIRBAS_MARKA_VARSAYILAN = "Muhtelif"
DEMIRBAS_MARKA_LISTESI_TABAN = [
    "Muhtelif",
]


def demirbas_marka_listesi_birlesik(
    kayitli: list | None = None,
    *,
    gizli: list | None = None,
) -> list[str]:
    """Varsayılan taban + kayıtlı markalar − gizlenenler (TR casefold tekil)."""
    gizli_keys = {
        demirbas_tr_casefold(str(x or "").strip())
        for x in (gizli or [])
        if str(x or "").strip()
    }
    seen: set[str] = set()
    out: list[str] = []
    for m in list(DEMIRBAS_MARKA_LISTESI_TABAN) + list(kayitli or []):
        t = str(m or "").strip()
        if not t:
            continue
        k = demirbas_tr_casefold(t)
        if k in seen or k in gizli_keys:
            continue
        seen.add(k)
        out.append(DEMIRBAS_MARKA_VARSAYILAN if k == "muhtelif" else t)
    return out or [DEMIRBAS_MARKA_VARSAYILAN]


def demirbas_marka_prefs_normalize(prefs: dict | None) -> dict:
    """markalar + gizli alanlarını normalize et (Muhtelif saklanmaz / gizlenemez)."""
    prefs = prefs if isinstance(prefs, dict) else {}
    seen: set[str] = set()
    markalar: list[str] = []
    for m in prefs.get("markalar") or []:
        t = str(m or "").strip()
        if not t:
            continue
        k = demirbas_tr_casefold(t)
        if k == "muhtelif" or k in seen:
            continue
        seen.add(k)
        markalar.append(t)
    gizli = demirbas_gizli_normalize(prefs.get("gizli"))
    gizli = [
        x
        for x in gizli
        if demirbas_tr_casefold(x) != "muhtelif"
        and demirbas_tr_casefold(x) in {demirbas_tr_casefold(m) for m in markalar}
    ]
    return {"markalar": markalar, "gizli": gizli}


def demirbas_pref_merge_json(
    pref_key: str,
    disk: dict | None,
    incoming: dict | None,
    *,
    removed: list | None = None,
) -> dict:
    """Eşzamanlı yazım: birleşim kümesi + casefold dedupe; removed ile bilinçli çıkarma."""
    disk = disk if isinstance(disk, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    rem_keys = {
        demirbas_tr_casefold(str(x or "").strip())
        for x in (removed or [])
        if str(x or "").strip()
    }

    if pref_key == DEMIRBAS_PREF_URUN_EK:
        merged = demirbas_ek_normalize(
            list(disk.get("urunler") or []) + list(incoming.get("urunler") or [])
        )
        if rem_keys:
            merged = [
                x
                for x in merged
                if demirbas_tr_casefold(x.get("esya") or "") not in rem_keys
            ]
        return {"urunler": merged}

    if pref_key == DEMIRBAS_PREF_URUN_GIZLI:
        merged = demirbas_gizli_normalize(
            list(disk.get("gizli") or []) + list(incoming.get("gizli") or [])
        )
        if rem_keys:
            merged = [x for x in merged if demirbas_tr_casefold(x) not in rem_keys]
        return {"gizli": merged}

    if pref_key == DEMIRBAS_PREF_MARKA:
        raw = list(disk.get("markalar") or []) + list(incoming.get("markalar") or [])
        seen: set[str] = set()
        markalar: list[str] = []
        for m in raw:
            t = str(m or "").strip()
            if not t:
                continue
            k = demirbas_tr_casefold(t)
            if k == "muhtelif" or k in seen or k in rem_keys:
                continue
            seen.add(k)
            markalar.append(t)
        # gizli: birleşim; removed = geri getir / kalıcı sil
        gizli_raw = list(disk.get("gizli") or []) + list(incoming.get("gizli") or [])
        gizli = demirbas_gizli_normalize(gizli_raw)
        if rem_keys:
            gizli = [x for x in gizli if demirbas_tr_casefold(x) not in rem_keys]
        # kalıcı silinen marka gizli listesinde kalmasın
        marka_keys = {demirbas_tr_casefold(m) for m in markalar}
        gizli = [
            x
            for x in gizli
            if demirbas_tr_casefold(x) != "muhtelif"
            and demirbas_tr_casefold(x) in marka_keys
        ]
        return {"markalar": markalar, "gizli": gizli}

    # bilinmeyen anahtar: incoming öncelikli
    out = dict(disk)
    out.update(incoming)
    return out


def demirbas_adet_normalize(raw, *, default: int = 1) -> int:
    """1..99 tamsayı. Sayısal değilse default."""
    try:
        if isinstance(raw, bool):
            raise ValueError("bool")
        if isinstance(raw, (int, float)):
            n = int(raw)
        else:
            s = str(raw if raw is not None else "").strip().replace(",", ".")
            if not s:
                return max(1, min(99, int(default)))
            m = re.match(r"^(\d{1,2})(?:\D|$)", s)
            if not m:
                return max(1, min(99, int(default)))
            n = int(m.group(1))
        if n < 1:
            n = 1
        if n > 99:
            n = 99
        return n
    except Exception:
        return max(1, min(99, int(default or 1)))


def demirbas_marka_normalize(raw) -> str:
    """Boş / muhtelif → Muhtelif."""
    s = str(raw or "").strip()
    if not s:
        return DEMIRBAS_MARKA_VARSAYILAN
    if demirbas_tr_casefold(s) == "muhtelif":
        return DEMIRBAS_MARKA_VARSAYILAN
    return s


def demirbas_marka_adi_temizle(raw) -> str | None:
    """Kalıcı marka ekleme: 2–40 karakter, trim, HTML temiz. Muhtelif → None."""
    s = str(raw or "").strip()
    s = re.sub(r"<[^>]*>", "", s)
    s = html.unescape(s).strip()
    if len(s) < 2 or len(s) > 40:
        return None
    if demirbas_tr_casefold(s) == "muhtelif":
        return None
    return s


def demirbas_seri_listesi_uret(
    esya_adi: str,
    adet: int,
    *,
    mevcut: list[str] | None = None,
    exists_fn=None,
    max_deneme: int = 20,
    rng=None,
) -> list[str]:
    """Adet kadar benzersiz seri. Mevcutlar korunur; fazla sonlar kesilir; eksiğe yeni üretilir."""
    n = demirbas_adet_normalize(adet)
    kept: list[str] = []
    for s in mevcut or []:
        s2 = str(s or "").strip()
        if s2 and demirbas_seri_no_gecerli(s2) and s2 not in kept:
            kept.append(s2)
        if len(kept) >= n:
            break
    if len(kept) > n:
        kept = kept[:n]
    seen = set(kept)

    def _exists(seri: str) -> bool:
        if seri in seen:
            return True
        if exists_fn is None:
            return False
        try:
            return bool(exists_fn(seri))
        except Exception:
            return False

    while len(kept) < n:
        yeni = demirbas_seri_no_uret_benzersiz(
            esya_adi, exists_fn=_exists, max_deneme=max_deneme, rng=rng
        )
        kept.append(yeni)
        seen.add(yeni)
    return kept
