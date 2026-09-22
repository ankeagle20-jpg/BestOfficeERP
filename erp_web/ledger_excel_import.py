# -*- coding: utf-8 -*-
"""Payafin Cari (ledger) — Excel party import yardımcıları (Aşama A0).

Müşteri import_excel ile aynı fuzzy kolon deseni; ledger_parties alanlarına özel.
Yazma / HTTP yok — yalnızca başlık eşleme + hücre/tip normalize.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

# ledger_parties alanı → Excel başlık anahtarları (küçük harf; substring eşleme sırası önemli)
LEDGER_PARTY_COLUMN_KEYS: dict[str, tuple[str, ...]] = {
    "name": (
        # Kısa / tam adlar önce (exact match ile Ad vs Firma ayrılır)
        "ad",
        "adi",
        "adı",
        "name",
        "müşteri adı",
        "musteri adi",
        "müşteri",
        "musteri",
        "ünvan",
        "unvan",
        "firma",
        "cari",
        "başlık",
        "baslik",
    ),
    "type": (
        "şahıs/şirket",
        "sahis/sirket",
        "kişi türü",
        "kisi turu",
        "cari tip",
        "cari tür",
        "type",
        "türü",
        "turu",
        "tür",
        "tur",
        "tip",
    ),
    "phone": (
        "telefon",
        "phone",
        "mobil",
        "gsm",
        "cep",
        "tel",
    ),
    "email": (
        "e-posta",
        "eposta",
        "e posta",
        "email",
        "mail",
    ),
    "country": (
        "ülke",
        "ulke",
        "country",
        "ulke kodu",
        "ülke kodu",
    ),
    "notes": (
        "açıklama",
        "aciklama",
        "notes",
        "notlar",
        "not",
    ),
}

# type hücresi → person | company
_TYPE_PERSON_TOKENS = frozenset(
    {
        "person",
        "şahıs",
        "sahis",
        "kişi",
        "kisi",
        "birey",
        "individual",
        "gerçek",
        "gercek",
    }
)
_TYPE_COMPANY_TOKENS = frozenset(
    {
        "company",
        "şirket",
        "sirket",
        "firma",
        "tüzel",
        "tuzel",
        "ltd",
        "a.ş",
        "a.s",
        "aş",
        "as",
    }
)


def normalize_header(value: Any) -> str:
    """Excel sütun başlığını karşılaştırma için normalize et."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    if not s or s.lower() == "nan":
        return ""
    # Yaygın boşluk / NBSP
    s = s.replace("\u00a0", " ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s


def normalize_headers(columns: Iterable[Any]) -> list[str]:
    """Başlık listesini normalize et; boşlara unnamed_N ver."""
    out: list[str] = []
    for i, c in enumerate(columns):
        s = normalize_header(c)
        out.append(s or f"unnamed_{i}")
    return out


def find_col(columns: Iterable[str], keys: Iterable[str]) -> Optional[str]:
    """Fuzzy kolon bul.

    1) Tam eşleşme (col == key) — örn. Ad, Firma yan yana iken Ad kazanır
    2) Substring (key in col) — müşteri import_excel deseni
    """
    cols = list(columns)
    key_list = [(k or "").strip().lower() for k in keys if (k or "").strip()]
    for key in key_list:
        for col in cols:
            if col == key:
                return col
    for key in key_list:
        for col in cols:
            if key in col:
                return col
    return None


def map_ledger_party_columns(
    columns: Iterable[Any],
) -> dict[str, Optional[str]]:
    """Normalize edilmiş başlıklardan ledger_parties alan → kolon adı haritası.

    Dönüş: name/type/phone/email/country/notes → kolon adı veya None.
    """
    cols = normalize_headers(columns)
    used: set[str] = set()
    mapping: dict[str, Optional[str]] = {}
    for field, keys in LEDGER_PARTY_COLUMN_KEYS.items():
        # Aynı fiziksel kolon birden fazla alana gitmesin
        remaining = [c for c in cols if c not in used]
        found = find_col(remaining, keys)
        if found is not None:
            used.add(found)
        mapping[field] = found
    return mapping


def cell_value(row: Mapping[Any, Any] | Any, col: Optional[str]) -> Optional[str]:
    """Satırdan hücreyi güvenli string olarak al (pandas Series / dict)."""
    if not col:
        return None
    try:
        if hasattr(row, "get"):
            v = row.get(col)
        else:
            v = row[col]  # type: ignore[index]
    except Exception:
        return None
    if v is None:
        return None
    try:
        # pandas NA
        import math

        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    try:
        import pandas as pd  # type: ignore

        if pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def normalize_party_type(value: Any) -> Optional[str]:
    """Hücre değerini person|company yap; tanınmazsa None (çağıran varsayılan person kullanır)."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s == "nan":
        return None
    # Tam eşleşme
    if s in _TYPE_PERSON_TOKENS:
        return "person"
    if s in _TYPE_COMPANY_TOKENS:
        return "company"
    # Substring (örn. "Şahıs kişi", "Anonim Şirket")
    for tok in _TYPE_COMPANY_TOKENS:
        if tok in s:
            return "company"
    for tok in _TYPE_PERSON_TOKENS:
        if tok in s:
            return "person"
    return None


def row_to_party_fields(
    row: Mapping[Any, Any] | Any,
    colmap: Mapping[str, Optional[str]],
    *,
    default_type: str = "person",
    default_country: Optional[str] = None,
) -> Optional[dict[str, Optional[str]]]:
    """Bir satırı ledger party alan dict'ine çevir. name yoksa None."""
    name = cell_value(row, colmap.get("name"))
    if not name:
        return None
    raw_type = cell_value(row, colmap.get("type"))
    ptype = normalize_party_type(raw_type) or (
        default_type if default_type in ("person", "company") else "person"
    )
    phone = cell_value(row, colmap.get("phone"))
    email = cell_value(row, colmap.get("email"))
    country = cell_value(row, colmap.get("country")) or default_country
    notes = cell_value(row, colmap.get("notes"))
    return {
        "name": name,
        "type": ptype,
        "phone": phone,
        "email": email,
        "country": country,
        "notes": notes,
    }
