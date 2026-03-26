"""
customers tablosunda ortak metin araması.

- Şirket ünvanı: name
- Müşteri adı (kısa/görünen): musteri_adi
- Yetkili ad soyad: yetkili_kisi
"""

from __future__ import annotations


def normalize_musteri_arama_tr(q: str) -> str:
    """
    PostgreSQL ILIKE ile Türkçe büyük harf uyumu.

    Veritabanında ILIKE, ASCII dışı harflerde locale'e göre tutarsız olabiliyor;
    özellikle «DERVİŞ» gibi tam büyük aramalar «derviş» ile eşleşmeyebiliyor.
    Arama metnini Türkçe kurallarına uygun tek küçük harf formuna indirgeriz
    (İ→i, ASCII I→ı, diğerleri .lower()).
    """
    if not q:
        return ""
    s = q.strip()
    out: list[str] = []
    for ch in s:
        if ch == "\u0130":  # LATIN CAPITAL LETTER I WITH DOT ABOVE (İ)
            out.append("i")
        elif ch == "I":  # ASCII büyük I → Türkçe ı
            out.append("\u0131")
        else:
            out.append(ch.lower())
    return "".join(out)


def _pct(q: str) -> str:
    return f"%{normalize_musteri_arama_tr(q or '')}%"


def customers_arama_sql_3(alias: str = "") -> str:
    """Üç alanda ILIKE; alias ör. 'c' → c.name, c.musteri_adi, c.yetkili_kisi."""
    a = f"{alias.strip()}." if alias and alias.strip() else ""
    return (
        f"(COALESCE({a}name, '') ILIKE %s "
        f"OR COALESCE({a}musteri_adi, '') ILIKE %s "
        f"OR COALESCE({a}yetkili_kisi, '') ILIKE %s)"
    )


def customers_arama_params_3(q: str):
    p = _pct(q)
    return (p, p, p)


def customers_arama_sql_3_plus_tax_office() -> str:
    """Giriş / cari listeleri: 3 alan + vergi no + ofis kodu."""
    return (
        "(" + customers_arama_sql_3("").strip("()") + " "
        "OR COALESCE(tax_number::text, '') ILIKE %s "
        "OR COALESCE(office_code, '') ILIKE %s)"
    )


def customers_arama_params_5(q: str):
    p = _pct(q)
    return (p, p, p, p, p)


def customers_arama_sql_3_plus_phone_tax(alias: str = "c") -> str:
    """Dashboard: 3 alan + telefon + vergi no."""
    a = f"{alias.strip()}."
    return (
        f"(COALESCE({a}name, '') ILIKE %s OR COALESCE({a}musteri_adi, '') ILIKE %s "
        f"OR COALESCE({a}yetkili_kisi, '') ILIKE %s OR COALESCE({a}phone, '') ILIKE %s "
        f"OR COALESCE({a}tax_number::text, '') ILIKE %s)"
    )


def customers_arama_sql_3_plus_tax() -> str:
    """Fatura tahsilat API: 3 alan + vergi no."""
    return (
        "(" + customers_arama_sql_3("").strip("()") + " "
        "OR COALESCE(tax_number::text, '') ILIKE %s)"
    )


def customers_arama_params_4(q: str):
    p = _pct(q)
    return (p, p, p, p)


def customers_arama_sql_3_plus_phone() -> str:
    """Mobil liste: 3 alan + telefon."""
    return (
        "(" + customers_arama_sql_3("c").strip("()") + " OR COALESCE(c.phone, '') ILIKE %s)"
    )


def customers_arama_params_4_phone(q: str):
    p = _pct(q)
    return (p, p, p, p)


def customers_arama_sql_randevu() -> str:
    """Randevu combobox: 3 alan + telefon + notes."""
    return (
        "(" + customers_arama_sql_3("").strip("()") + " "
        "OR COALESCE(phone, '') ILIKE %s OR COALESCE(notes, '') ILIKE %s)"
    )


def customers_arama_params_5_randevu(q: str):
    p = _pct(q)
    return (p, p, p, p, p)
