"""
Akbank Excel → tahsilat önizleme / müşteri eşleştirme (web + CLI ortak).

Çok satırlı önizlemede müşteri eşleştirmesi CPU yoğun; varsayılan olarak
tüm mantıksal çekirdekleri kullanır (multiprocessing, spawn).

Ortam:
  AKBANK_ONIZLEME_PROCESSES=1     → paralelliği kapat (tek süreç)
  AKBANK_ONIZLEME_MAX_WORKERS=8   → en fazla 8 işçi (ör. Render’da sınırla)
  AKBANK_ONIZLEME_MIN_ROWS=32     → bundan az satırda havuz açılmaz (overhead)

Windows: AKBANK_ONIZLEME_PROCESSES ayarlı değilse önizleme tek süreç (ProcessPool Flask altında sık kilitlenir).

Önbellek: norm_text_fold, norm_loose, akbank_sender_key, _digits_only_str, _digit_haystack
  için LRU (tekrarlayan ünvan/açıklama/rakam dizilerinde O(1) yakın maliyet).
"""
from __future__ import annotations

import io
import logging
import os
import re
import sys
import unicodedata
from functools import lru_cache
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

_log_ak = logging.getLogger(__name__)


def norm_header(c: object) -> str:
    s = str(c).strip().lower()
    for a, b in (
        ("ı", "i"),
        ("ş", "s"),
        ("ğ", "g"),
        ("ü", "u"),
        ("ö", "o"),
        ("ç", "c"),
    ):
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s)
    return s


def norm_text(s: str) -> str:
    """Geriye dönük: basit boşluk sıkıştırma (Excel sütunları vb.)."""
    return re.sub(r"\s+", " ", str(s or "").lower().strip())


@lru_cache(maxsize=16384)
def norm_text_fold(s: str) -> str:
    """
    Türkçe / Unicode tutarlılığı: İ→i birleşik nokta artığı yüzünden [^a-z0-9] ile
    harf silinmesini önlemek için NFKD + birleşik işaret temizliği, sonra ASCII harf eşlemesi.
    """
    t = str(s or "").strip().casefold()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    for a, b in (
        ("ı", "i"),
        ("ş", "s"),
        ("ğ", "g"),
        ("ü", "u"),
        ("ö", "o"),
        ("ç", "c"),
    ):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=16384)
def norm_loose(s: str) -> str:
    """
    Eşleştirme için: fold + noktalama/özel karakterleri boşluk yap (TİC.NAK ≈ TIC NAK).
    Sadece harf/rakam/boşluk kalır.
    """
    t = norm_text_fold(s)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=8192)
def akbank_sender_key(aciklama: str) -> str:
    """
    Aynı göndericiden gelen farklı dekontlar için stabil anahtar (saat, uzun referans vb. atılır).
    Manuel eşleştirme hatırlama tablosu için kullanılır.
    """
    t = str(aciklama or "").strip()
    if not t:
        return ""
    # İlk köşeli parantez (genelde [Saat …])
    t = re.sub(r"^\s*\[[^\]]+\]\s*", "", t, count=1)
    # Uzun rakam / fiş no / kart gövdesi benzeri diziler
    t = re.sub(r"\d[\d\s.]{3,}\d|\d{5,}", " ", t)
    return norm_loose(t)[:480]


def _musteri_kart_etiketi(c: dict[str, Any]) -> str:
    name = str(c.get("name") or "").strip()
    ma = str(c.get("musteri_adi") or "").strip()
    if ma and norm_text_fold(ma) != norm_text_fold(name):
        return f"{name} · {ma}".strip()
    return name or f"#{c.get('id')}"


# Sadece çoğu şirket adında geçmeyen bağlaç / kanal kelimeleri (tic, dis, nakliyat vb. ünvanda olabilir)
_TOKEN_STOP = frozenset({
    "ve", "veya", "ile", "icin", "the", "and", "mbh", "fast", "havale", "transfer",
})
_MIN_TOKEN_LEN = 4
_MIN_PHRASE_LEN = 3
# İndekste daha kısa marka kelimeleri (yalnız aday kümesi; skor yine _MIN_TOKEN_LEN ile)
_INDEX_TOKEN_MIN = 3

_GENERIC_WORD = frozenset({
    "danismanlik", "danismanligi", "insaat", "insaati", "sanayi", "ticaret", "limited",
    "sirketi", "anonim", "ltd", "sti", "as", "ao", "aojv", "otas", "lojistik", "tic",
    "ve", "ofis", "hizmetleri", "hizmet", "nakliyat", "nakliye", "dis", "los", "taahhut",
    "proje", "yapi", "gayrimenkul", "dagitim", "ticari", "genel", "turizm", "gida",
    "enerji", "tekstil", "metalurji", "mobilya", "malzemeleri", "orman", "urunleri",
    "muhendislik", "reklam", "bilisim", "temizlik", "guvenlik", "merkezi", "sube",
    "iletisim", "musavirlik", "muhasebe", "holding", "yonetim", "otel", "cafe",
    "restoran", "market", "magaza", "doviz", "consulting", "group", "international",
})


def _hay_tokens_for_index(hay: str) -> set[str]:
    return {t for t in hay.split() if len(t) >= _INDEX_TOKEN_MIN}


def _metinden_indeks_tokenlari(raw: str) -> set[str]:
    """Ünvan/ad alanlarından anlamlı tokenlar (indeks anahtarı)."""
    full = norm_loose(raw)
    if not full:
        return set()
    out: set[str] = set()
    for t in full.split():
        if len(t) < _INDEX_TOKEN_MIN or t in _TOKEN_STOP or t in _GENERIC_WORD:
            continue
        out.add(t)
    return out


@dataclass
class AkbankMusteriIndeks:
    """Satır başına O(aday) eşleştirme: token + VKN/TC ters indeks."""

    token_to_cids: dict[str, set[int]] = field(default_factory=dict)
    vkn_to_cids: dict[str, set[int]] = field(default_factory=dict)
    tc_to_cids: dict[str, set[int]] = field(default_factory=dict)
    must_map: dict[int, dict[str, Any]] = field(default_factory=dict)


def build_akbank_musteri_indeks(musteriler: list[dict[str, Any]]) -> AkbankMusteriIndeks:
    tok: dict[str, set[int]] = defaultdict(set)
    vkn_m: dict[str, set[int]] = defaultdict(set)
    tc_m: dict[str, set[int]] = defaultdict(set)
    must_map: dict[int, dict[str, Any]] = {}

    for c in musteriler:
        try:
            cid = int(c.get("id"))
        except (TypeError, ValueError):
            continue
        must_map[cid] = c

        unvan = (c.get("sirket_unvani") or "").strip()
        if len(unvan) >= _MIN_PHRASE_LEN:
            for t in _metinden_indeks_tokenlari(unvan):
                tok[t].add(cid)

        for key in ("musteri_adi", "name", "yetkili_adsoyad"):
            raw = (c.get(key) or "").strip()
            if len(raw) >= _MIN_PHRASE_LEN:
                for t in _metinden_indeks_tokenlari(raw):
                    tok[t].add(cid)

        vkn = _digits_only(c.get("kyc_vergi_no")) or _digits_only(c.get("tax_number"))
        if len(vkn) >= 10:
            vkn_m[vkn].add(cid)

        tc = _digits_only(c.get("yetkili_tcno"))
        if len(tc) == 11:
            tc_m[tc].add(cid)

    return AkbankMusteriIndeks(
        token_to_cids={k: set(v) for k, v in tok.items()},
        vkn_to_cids={k: set(v) for k, v in vkn_m.items()},
        tc_to_cids={k: set(v) for k, v in tc_m.items()},
        must_map=must_map,
    )


def _aday_musteri_idleri(hay: str, digit_hay: str, idx: AkbankMusteriIndeks) -> set[int]:
    """
    VKN/TC: kayıtlı VKN/TC sayısı digit_hay üzerindeki kayan pencere sayısından azsa
    ters tarama (her VKN için 'in digit_hay') daha az iş yapar.
    """
    out: set[int] = set()
    if hay:
        for t in _hay_tokens_for_index(hay):
            s = idx.token_to_cids.get(t)
            if s:
                out |= s
    dh = digit_hay
    ln = len(dh)
    win10 = max(0, ln - 9)
    win11 = max(0, ln - 10)
    nv = len(idx.vkn_to_cids)
    nt = len(idx.tc_to_cids)
    if ln >= 10 and nv:
        if nv < win10:
            for sub, s in idx.vkn_to_cids.items():
                if sub in dh:
                    out |= s
        else:
            for i in range(win10):
                sub = dh[i : i + 10]
                s = idx.vkn_to_cids.get(sub)
                if s:
                    out |= s
    if ln >= 11 and nt:
        if nt < win11:
            for sub, s in idx.tc_to_cids.items():
                if sub in dh:
                    out |= s
        else:
            for i in range(win11):
                sub = dh[i : i + 11]
                s = idx.tc_to_cids.get(sub)
                if s:
                    out |= s
    return out


def _eslestir_musteri_cekirdek(
    hay: str,
    digit_hay: str,
    must_map: dict[int, dict[str, Any]],
    id_sirasi: list[int],
    hay_words: frozenset[str],
) -> dict[str, Any]:
    """Verilen müşteri id listesi üzerinde tam skor (sıra korunur)."""
    per_id: dict[int, tuple[int, int, str]] = {}
    for cid in id_sirasi:
        c = must_map.get(cid)
        if c is None:
            continue
        sig = _musteri_en_iyi_sinyal(c, hay, digit_hay, hay_words)
        if sig is not None:
            per_id[cid] = sig
    if not per_id:
        return {
            "status": "unknown",
            "musteri_id": None,
            "musteri_label": None,
            "candidates": [],
        }
    vkn_sort = {cid: _vkn_in_aciklama(must_map[cid], digit_hay) for cid in per_id}
    ranked = sorted(
        per_id.items(),
        key=lambda x: (x[1][0], -x[1][1], -vkn_sort[x[0]], x[0]),
    )
    top_id, (top_pri, top_score, top_label) = ranked[0]
    cands = [
        {
            "id": i,
            "label": t[2],
            "name": str(must_map.get(i, {}).get("name") or ""),
        }
        for i, t in ranked[:12]
    ]
    return {
        "status": "matched",
        "musteri_id": top_id,
        "musteri_label": top_label,
        "candidates": cands[:3],
    }


def _best_unvan_match(raw: str, hay: str, hay_words: frozenset[str]) -> tuple[int, str] | None:
    """
    Şirket ünvanı: tam ifade VEYA en az 2 anlamlı (genel olmayan) kelime dekontta kelime olarak,
    VEYA ünvanda anlamlı tek token kaldıysa (ör. OFİSBİR A.Ş.),
    VEYA çok kelimeli ünvanda ilk anlamlı kelime marka gibi eşleşiyorsa (İZALP İNŞAAT…).
    Tek genel kelime (danismanlik, insaat…) ile eşleşme yok sayılır.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    full = norm_loose(raw)
    if len(full) >= _MIN_PHRASE_LEN and full in hay:
        return (len(full), raw)

    tokens = [t for t in full.split() if len(t) >= _MIN_TOKEN_LEN and t not in _TOKEN_STOP]
    ng = [t for t in tokens if t not in _GENERIC_WORD]
    hit = [t for t in ng if t in hay_words]

    if len(hit) >= 2:
        return (sum(len(x) for x in hit), raw)
    if len(hit) == 1 and len(ng) == 1:
        return (len(hit[0]), raw)
    if len(hit) == 1 and len(ng) >= 2 and hit[0] == ng[0] and len(hit[0]) >= 5:
        return (len(hit[0]), raw)
    return None


def _best_kisi_adi_match(raw: str, hay: str, hay_words: frozenset[str]) -> tuple[int, str] | None:
    """
    Müşteri adı / yetkili: tam ifade VEYA en az 2 anlamlı kelime aynı anda dekontta.
    Tek kelime (soyad çakışması: ADEM DOGAN vs EBRU … DOĞAN) ile eşleşme yapılmaz.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    full = norm_loose(raw)
    if len(full) >= _MIN_PHRASE_LEN and full in hay:
        return (len(full), raw)

    tokens = [t for t in full.split() if len(t) >= _MIN_TOKEN_LEN and t not in _TOKEN_STOP]
    ng = [t for t in tokens if t not in _GENERIC_WORD]
    hit = [t for t in ng if t in hay_words]

    if len(hit) >= 2:
        return (sum(len(x) for x in hit), raw)
    if len(hit) == 1 and len(ng) == 1 and len(hit[0]) >= 5:
        return (len(hit[0]), raw)
    return None


def _vkn_in_aciklama(c: dict[str, Any], digit_hay: str) -> int:
    v = _digits_only(c.get("kyc_vergi_no")) or _digits_only(c.get("tax_number"))
    return 1 if len(v) >= 10 and v in digit_hay else 0


def parse_tutar_tr(val: object) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    s = str(val).strip().replace(" ", "").replace("TL", "").replace("₺", "")
    if not s or s == "-":
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


def parse_tarih(val: object) -> date | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if hasattr(val, "date") and callable(getattr(val, "date", None)):
        try:
            d = val.date()  # type: ignore[union-attr]
            if isinstance(d, date):
                return d
        except Exception:
            pass
    s = str(val).strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).date()
    except Exception:
        return None


def read_akbank_excel(source: Path | bytes) -> pd.DataFrame:
    last_err: Exception | None = None
    for skip in range(0, 18):
        try:
            if isinstance(source, bytes):
                df = pd.read_excel(io.BytesIO(source), engine="openpyxl", skiprows=skip, header=0)
            else:
                df = pd.read_excel(source, engine="openpyxl", skiprows=skip, header=0)
            if df.empty:
                continue
            cols_norm = [norm_header(c) for c in df.columns]
            if any("tarih" in c for c in cols_norm) and any(
                "borc" in c or "alacak" in c or "fis" in c or "dekont" in c for c in cols_norm
            ):
                df.columns = cols_norm
                return df
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise RuntimeError(f"Excel okunamadı: {last_err}") from last_err
    raise RuntimeError("Akbank ekstre başlığı bulunamadı.")


def pick_tutar_column(df: pd.DataFrame) -> str | None:
    candidates = []
    for c in df.columns:
        cn = str(c).lower()
        if "bakiye" in cn:
            continue
        if "tutar" in cn or cn.startswith("unnamed"):
            non_null = df[c].dropna()
            if non_null.empty:
                continue
            sample = non_null.head(20).astype(str)
            if sample.str.contains(r"\d", regex=True).any():
                candidates.append(c)
    if not candidates:
        return None
    for c in candidates:
        if str(c) == "tutar" or str(c).startswith("tutar"):
            ser = df[c].apply(parse_tutar_tr)
            if ser.sum() > 0 or (ser.max() or 0) > 0:
                return c
    best = None
    best_sum = -1.0
    for c in candidates:
        s = df[c].apply(parse_tutar_tr).sum()
        if s > best_sum:
            best_sum = s
            best = c
    return best


def col(df: pd.DataFrame, *names: str) -> str | None:
    for n in names:
        for c in df.columns:
            if str(c).strip().lower() == n:
                return c
    for n in names:
        for c in df.columns:
            if n in str(c).lower():
                return c
    return None


@lru_cache(maxsize=8192)
def _digits_only_str(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def _digits_only(s: object) -> str:
    return _digits_only_str(str(s or ""))


@lru_cache(maxsize=8192)
def _digit_haystack(aciklama: str) -> str:
    """Açıklamadaki tüm rakamlar (VKN/TCKN alt dizisi kontrolü için)."""
    return "".join(ch for ch in str(aciklama or "") if ch.isdigit())


# Eşleştirme önceliği: küçük sayı = önce (şirket ünvanı → müşteri adı → vergi no → TC)
_PRI_UNVAN = 1
_PRI_MUSTERI_ADI = 2
_PRI_VERGI = 3
_PRI_TC = 4


def _musteri_en_iyi_sinyal(
    c: dict[str, Any],
    hay: str,
    digit_hay: str,
    hay_words: frozenset[str],
) -> tuple[int, int, str] | None:
    """
    Tek müşteri için (öncelik, skor, etiket) veya eşleşme yoksa None.
    Öncelik düşük = güçlü; skor = eşleşme uzunluğu (aynı öncelikte büyük olan kazanır).
    """
    best: tuple[int, int, str] | None = None

    def consider(pri: int, score: int, label: str) -> None:
        nonlocal best
        if score <= 0:
            return
        if best is None or pri < best[0] or (pri == best[0] and score > best[1]):
            best = (pri, score, label)

    # 1) Şirket ünvanı (KYC) — tam metin veya güçlü kelime parçaları
    unvan = (c.get("sirket_unvani") or "").strip()
    if len(unvan) >= _MIN_PHRASE_LEN:
        hit = _best_unvan_match(unvan, hay, hay_words)
        if hit:
            consider(_PRI_UNVAN, hit[0], hit[1])

    # 2) Müşteri adı / kart adı (ünvan ile aynı loose metinse atla)
    seen_loose: set[str] = set()
    if unvan:
        seen_loose.add(norm_loose(unvan))
    for key in ("musteri_adi", "name", "yetkili_adsoyad"):
        raw = (c.get(key) or "").strip()
        if len(raw) < _MIN_PHRASE_LEN:
            continue
        nl = norm_loose(raw)
        if len(nl) < _MIN_PHRASE_LEN or nl in seen_loose:
            continue
        hit = _best_kisi_adi_match(raw, hay, hay_words)
        if hit:
            seen_loose.add(nl)
            consider(_PRI_MUSTERI_ADI, hit[0], hit[1])

    # 3) Vergi no: KYC vergi_no veya customers.tax_number (en az 10 hane)
    vkn = _digits_only(c.get("kyc_vergi_no")) or _digits_only(c.get("tax_number"))
    if len(vkn) >= 10 and vkn in digit_hay:
        consider(_PRI_VERGI, len(vkn), f"VKN {vkn}")

    # 4) TC kimlik (KYC yetkili_tcno, 11 hane)
    tc = _digits_only(c.get("yetkili_tcno"))
    if len(tc) == 11 and tc in digit_hay:
        consider(_PRI_TC, 11, f"TC {tc}")

    return best


def eslestir_musteri(
    aciklama: str,
    musteriler: list[dict[str, Any]],
    indeks: AkbankMusteriIndeks | None = None,
) -> dict[str, Any]:
    """
    Açıklamada geçen bilgileri müşteri kartı (KYC + customers) ile eşleştirir.
    indeks verilirse önce token/VKN-TC aday kümesinde arar (toplu önizlemede çok daha hızlı).
    """
    acik = str(aciklama or "")
    hay = norm_loose(acik)
    digit_hay = _digit_haystack(acik)
    hay_words = frozenset(w for w in hay.split() if w)
    if not hay and not digit_hay:
        return {
            "status": "unknown",
            "musteri_id": None,
            "musteri_label": None,
            "candidates": [],
        }

    def _id_list_tumu() -> tuple[dict[int, dict[str, Any]], list[int]]:
        mm: dict[int, dict[str, Any]] = {}
        ids: list[int] = []
        for c in musteriler:
            try:
                cid = int(c.get("id"))
            except (TypeError, ValueError):
                continue
            mm[cid] = c
            ids.append(cid)
        return mm, ids

    if indeks is not None:
        cand = _aday_musteri_idleri(hay, digit_hay, indeks)
        if cand:
            r = _eslestir_musteri_cekirdek(hay, digit_hay, indeks.must_map, sorted(cand), hay_words)
            if r["status"] == "matched":
                return r
        _, ids_all = _id_list_tumu()
        return _eslestir_musteri_cekirdek(hay, digit_hay, indeks.must_map, ids_all, hay_words)

    mm, ids_all = _id_list_tumu()
    return _eslestir_musteri_cekirdek(hay, digit_hay, mm, ids_all, hay_words)


def dataframe_hareket_satirlari(df: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Borç/Alacak = A satırlarını dict listesine çevirir.
    Dönüş: (satirlar, ozet_sayac)
    """
    col_tarih = col(df, "tarih")
    col_saat = col(df, "saat")
    col_tutar = pick_tutar_column(df)
    col_aciklama = col(df, "aciklama", "açiklama")
    col_ba = col(df, "borç/alacak", "borc/alacak", "borc / alacak")
    col_fis = col(df, "fiş/dekont no", "fis/dekont no", "fiş dekont no", "fis dekont no", "dekont no")
    eksik = [n for n, c in [
        ("tarih", col_tarih),
        ("tutar", col_tutar),
        ("borc/alacak", col_ba),
        ("aciklama", col_aciklama),
        ("fis/dekont no", col_fis),
    ] if c is None]
    if eksik:
        raise ValueError(f"Eksik sütunlar: {eksik}. Mevcut: {list(df.columns)}")

    ozet = {"excel_satir": 0, "a_degil": 0, "ref_bos": 0, "tutar_sifir": 0, "tarih_yok": 0, "islenen": 0}
    satirlar: list[dict[str, Any]] = []

    for sira, (idx, row) in enumerate(df.iterrows(), start=1):
        ozet["excel_satir"] += 1
        ba = str(row[col_ba] if col_ba else "").strip().upper()
        if ba != "A":
            ozet["a_degil"] += 1
            continue
        ref_raw = row[col_fis] if col_fis else None
        if ref_raw is None or (isinstance(ref_raw, float) and pd.isna(ref_raw)):
            ozet["ref_bos"] += 1
            continue
        ref = str(ref_raw).strip()
        if not ref:
            ozet["ref_bos"] += 1
            continue
        tutar = parse_tutar_tr(row[col_tutar])
        if tutar <= 0:
            ozet["tutar_sifir"] += 1
            continue
        d = parse_tarih(row[col_tarih])
        if not d:
            ozet["tarih_yok"] += 1
            continue
        acik = str(row[col_aciklama] if col_aciklama else "").strip()
        saat_str = ""
        if col_saat and col_saat in df.columns:
            sa = row[col_saat]
            if sa is not None and not (isinstance(sa, float) and pd.isna(sa)):
                saat_str = str(sa).strip()
        if saat_str:
            acik = f"[Saat {saat_str}] {acik}".strip()
        ozet["islenen"] += 1
        satirlar.append({
            "sira": sira,
            "excel_index": int(idx) if isinstance(idx, (int, float)) else str(idx),
            "tarih": d.isoformat(),
            "saat": saat_str,
            "tutar": round(tutar, 2),
            "aciklama": acik,
            "banka_referans_no": ref,
        })
    return satirlar, ozet


def ham_tahsilatta_olanlari_cikar(
    ham_satirlar: list[dict[str, Any]],
    tahsilatta_refler: set[str],
) -> tuple[list[dict[str, Any]], int]:
    """
    banka_referans_no tahsilatlar'da kayıtlı satırları listeden çıkarır (tekrar gösterme).
    Dönüş: (kalan_ham, cikarilan_adet)
    """
    if not tahsilatta_refler:
        return ham_satirlar, 0
    out: list[dict[str, Any]] = []
    cik = 0
    for r in ham_satirlar:
        ref = str(r.get("banka_referans_no") or "").strip()
        if ref and ref in tahsilatta_refler:
            cik += 1
            continue
        out.append(r)
    return out, cik


# --- Çok çekirdekli önizleme (ProcessPool, spawn; Windows + Gunicorn uyumlu) ---
_ONIZLEME_MP_STATE: tuple[list[dict[str, Any]], AkbankMusteriIndeks, dict[int, dict[str, Any]], dict[str, int]] | None = None


def _onizleme_mp_init(musteriler: list[dict[str, Any]], manual_by_key: dict[str, int]) -> None:
    global _ONIZLEME_MP_STATE
    must_by_id: dict[int, dict[str, Any]] = {}
    for c in musteriler:
        try:
            must_by_id[int(c.get("id"))] = c
        except (TypeError, ValueError):
            continue
    indeks = build_akbank_musteri_indeks(musteriler)
    _ONIZLEME_MP_STATE = (musteriler, indeks, must_by_id, dict(manual_by_key))


def _onizleme_mp_worker_chunk(
    ham_chunk: list[dict[str, Any]],
    mevcut_refler: set[str],
) -> list[dict[str, Any]]:
    global _ONIZLEME_MP_STATE
    if _ONIZLEME_MP_STATE is None:
        return []
    musteriler, indeks, must_by_id, manual_by_key = _ONIZLEME_MP_STATE
    return [
        _onizleme_satir_tek(r, must_by_id, indeks, musteriler, mevcut_refler, manual_by_key)
        for r in ham_chunk
    ]


def _onizleme_worker_count(n_rows: int) -> int:
    """İşçi sayısı: varsayılan tüm CPU; AKBANK_ONIZLEME_PROCESSES=1 → 1 (paralel kapalı)."""
    raw = os.environ.get("AKBANK_ONIZLEME_PROCESSES", "").strip().lower()
    if raw == "1" or raw == "off" or raw == "false":
        return 1
    # Windows + Flask/Werkzeug: ProcessPool önizlemede sık sonsuz beklemeye düşer; açıkça çoklu istenmediyse tek süreç.
    if sys.platform == "win32":
        if not raw or raw in ("0", "auto"):
            return 1
        if raw.isdigit():
            return max(1, int(raw))
        return 1
    cpu = os.cpu_count() or 1
    if raw and raw not in ("0", "auto", ""):
        try:
            w = max(1, int(raw))
        except ValueError:
            w = cpu
    else:
        w = cpu
    cap_s = os.environ.get("AKBANK_ONIZLEME_MAX_WORKERS", "").strip()
    if cap_s.isdigit():
        w = min(w, max(1, int(cap_s)))
    return max(1, min(w, max(1, n_rows)))


def _onizleme_satir_tek(
    r: dict[str, Any],
    must_by_id: dict[int, dict[str, Any]],
    indeks: AkbankMusteriIndeks,
    musteriler: list[dict[str, Any]],
    mevcut_refler: set[str],
    manual_by_key: dict[str, int],
) -> dict[str, Any]:
    ref = str(r.get("banka_referans_no") or "").strip()
    dup = ref in mevcut_refler
    sk = akbank_sender_key(r.get("aciklama") or "")
    mid_man = manual_by_key.get(sk) if sk else None
    mid_int: int | None = None
    if mid_man is not None:
        try:
            mid_int = int(mid_man)
        except (TypeError, ValueError):
            mid_int = None
    c_man = must_by_id.get(mid_int) if mid_int is not None else None
    if c_man is not None:
        lab = _musteri_kart_etiketi(c_man)
        em = {
            "status": "matched",
            "musteri_id": mid_int,
            "musteri_label": lab,
            "candidates": [{"id": mid_int, "label": lab, "name": str(c_man.get("name") or "")}],
            "kaynak": "manuel_hatirlat",
        }
    else:
        em = {**eslestir_musteri(r.get("aciklama") or "", musteriler, indeks), "kaynak": "otomatik"}
    if dup:
        ui_status = "duplicate"
    elif em["status"] == "matched":
        ui_status = "matched"
    elif em["status"] == "ambiguous":
        ui_status = "ambiguous"
    else:
        ui_status = "unknown"
    return {
        **r,
        "eslestirme": em,
        "sender_key": sk,
        "ui_status": ui_status,
        "musteri_id_oneri": em.get("musteri_id"),
        "musteri_label_oneri": em.get("musteri_label"),
    }


def onizleme_satirlari(
    ham_satirlar: list[dict[str, Any]],
    musteriler: list[dict[str, Any]],
    mevcut_refler: set[str],
    manual_by_key: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    manual_by_key = manual_by_key or {}
    n = len(ham_satirlar)
    if n == 0:
        return []

    min_rows = max(8, int(os.environ.get("AKBANK_ONIZLEME_MIN_ROWS", "32")))
    workers = _onizleme_worker_count(n)

    if workers < 2 or n < min_rows:
        must_by_id: dict[int, dict[str, Any]] = {}
        for c in musteriler:
            try:
                must_by_id[int(c.get("id"))] = c
            except (TypeError, ValueError):
                continue
        indeks = build_akbank_musteri_indeks(musteriler)
        out = [
            _onizleme_satir_tek(r, must_by_id, indeks, musteriler, mevcut_refler, manual_by_key)
            for r in ham_satirlar
        ]
    else:
        chunk_sz = max(1, (n + workers - 1) // workers)
        chunks: list[list[dict[str, Any]]] = [
            ham_satirlar[i : i + chunk_sz] for i in range(0, n, chunk_sz)
        ]
        import multiprocessing

        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(
            processes=min(workers, len(chunks)),
            initializer=_onizleme_mp_init,
            initargs=(musteriler, manual_by_key),
        ) as pool:
            parts: list[list[dict[str, Any]]] = pool.starmap(
                _onizleme_mp_worker_chunk,
                [(ch, mevcut_refler) for ch in chunks],
            )
        out = []
        for p in parts:
            out.extend(p)

    try:
        from services.embedding_akbank_prototype import (
            augment_preview_rows_with_embeddings,
            embed_candidate_cap,
            embed_prototype_enabled,
            embed_prototype_max_rows,
        )

        if embed_prototype_enabled():
            augment_preview_rows_with_embeddings(
                out,
                musteriler,
                max_rows=embed_prototype_max_rows(),
                candidate_cap=embed_candidate_cap(),
            )
    except Exception as e:
        _log_ak.warning("AKBANK embedding prototype: %s", e)

    return out
