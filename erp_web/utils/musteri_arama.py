"""
customers tablosunda ortak metin araması.

- Şirket ünvanı: name
- Müşteri adı (kısa/görünen): customers.musteri_adi + musteri_kyc.musteri_adi (son KYC kaydı)
- Yetkili ad soyad: yetkili_kisi
"""

from __future__ import annotations

import unicodedata

from utils.text_utils import turkish_lower


def normalize_musteri_arama_tr(q: str) -> str:
    """
    PostgreSQL ILIKE ile Türkçe büyük harf uyumu.

    Veritabanında ILIKE, ASCII dışı harflerde locale'e göre tutarsız olabiliyor;
    özellikle «DERVİŞ» gibi tam büyük aramalar «derviş» ile eşleşmeyebiliyor.
    Bu yüzden arama metnini Türkçe kurallarına uygun şekilde normalize ederiz.
    (İ/ı/I -> i; Ş/Ğ/Ü/Ö/Ç -> s/g/u/o/c; ayrıca birleştirici işaretleri temizler.)
    """
    if not q:
        return ""
    s = str(q).strip()
    # i̇ gibi kombinlenen formları tekilleştir + combining mark'ları temizle.
    try:
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    except Exception:
        pass
    # Tam Türkçe küçük harf + ASCII fold.
    return turkish_lower(s)


def _fold_sql_text(expr_sql: str) -> str:
    """
    Postgres tarafında kolonları Türkçe karşılıklarına göre ASCII'ye fold edip
    küçük harfe indirger (LIKE için case-insensitive'i kolon tarafında bitirir).
    """
    # translate: kaynak ve hedef string uzunluğu birebir olmalı.
    # İ/I/ı -> i
    # Ş/ş -> s
    # Ğ/ğ -> g
    # Ü/ü -> u
    # Ö/ö -> o
    # Ç/ç -> c
    from_chars = "\u0130I\u0131\u015E\u015F\u011E\u011F\u00DC\u00FC\u00D6\u00F6\u00C7\u00E7"
    to_chars = "iiissggu uoocc".replace(" ", "")  # "iiissgguuoocc"
    return f"lower(translate({expr_sql}, '{from_chars}', '{to_chars}'))"


def _pct(q: str) -> str:
    return f"%{normalize_musteri_arama_tr(q or '')}%"


def customers_arama_sql_3(alias: str = "") -> str:
    """name, musteri_adi, musteri_kyc.musteri_adi (EXISTS), yetkili_kisi — 4 ILIKE."""
    a = f"{alias.strip()}." if alias and alias.strip() else ""
    name_expr = f"COALESCE({a}name, '')"
    musteri_expr = f"COALESCE({a}musteri_adi, '')"
    yetkili_expr = f"COALESCE({a}yetkili_kisi, '')"
    mk_expr = "COALESCE(mk.musteri_adi, '')"
    return (
        f"({_fold_sql_text(name_expr)} LIKE %s "
        f"OR {_fold_sql_text(musteri_expr)} LIKE %s "
        f"OR EXISTS (SELECT 1 FROM musteri_kyc mk WHERE mk.musteri_id = {a}id "
        f"AND {_fold_sql_text(mk_expr)} LIKE %s) "
        f"OR {_fold_sql_text(yetkili_expr)} LIKE %s)"
    )


def customers_arama_params_4(q: str):
    p = _pct(q)
    return (p, p, p, p)


def customers_arama_sql_3_plus_tax_office() -> str:
    """Giriş / cari listeleri: çekirdek + vergi no + ofis kodu."""
    tax_expr = "COALESCE(tax_number::text, '')"
    office_expr = "COALESCE(office_code, '')"
    return (
        "("
        + customers_arama_sql_3("").strip("()")
        + f" OR {_fold_sql_text(tax_expr)} LIKE %s"
        + f" OR {_fold_sql_text(office_expr)} LIKE %s)"
    )


def customers_arama_params_6(q: str):
    p = _pct(q)
    return (p, p, p, p, p, p)


def customers_arama_sql_3_plus_phone_tax(alias: str = "c") -> str:
    """Dashboard: çekirdek + telefon + vergi no."""
    a = f"{alias.strip()}."
    name_expr = f"COALESCE({a}name, '')"
    musteri_adi_expr = f"COALESCE({a}musteri_adi, '')"
    yetkili_expr = f"COALESCE({a}yetkili_kisi, '')"
    phone_expr = f"COALESCE({a}phone, '')"
    tax_expr = f"COALESCE({a}tax_number::text, '')"
    mk_expr = "COALESCE(mk.musteri_adi, '')"
    return (
        f"({_fold_sql_text(name_expr)} LIKE %s "
        f"OR {_fold_sql_text(musteri_adi_expr)} LIKE %s "
        f"OR EXISTS (SELECT 1 FROM musteri_kyc mk WHERE mk.musteri_id = {a}id "
        f"AND {_fold_sql_text(mk_expr)} LIKE %s) "
        f"OR {_fold_sql_text(yetkili_expr)} LIKE %s "
        f"OR {_fold_sql_text(phone_expr)} LIKE %s "
        f"OR {_fold_sql_text(tax_expr)} LIKE %s)"
    )


def customers_arama_sql_3_plus_tax() -> str:
    """Fatura tahsilat API: çekirdek + vergi no."""
    tax_expr = "COALESCE(tax_number::text, '')"
    return (
        "("
        + customers_arama_sql_3("").strip("()")
        + f" OR {_fold_sql_text(tax_expr)} LIKE %s)"
    )


def customers_arama_params_5(q: str):
    p = _pct(q)
    return (p, p, p, p, p)


def customers_arama_sql_3_plus_phone() -> str:
    """Mobil liste: çekirdek + telefon."""
    phone_expr = "COALESCE(c.phone, '')"
    return (
        "("
        + customers_arama_sql_3("c").strip("()")
        + f" OR {_fold_sql_text(phone_expr)} LIKE %s)"
    )


def customers_arama_params_5_phone(q: str):
    p = _pct(q)
    return (p, p, p, p, p)


def customers_arama_sql_randevu() -> str:
    """Randevu combobox: çekirdek + telefon + notes."""
    phone_expr = "COALESCE(phone, '')"
    notes_expr = "COALESCE(notes, '')"
    return (
        "("
        + customers_arama_sql_3("").strip("()")
        + f" OR {_fold_sql_text(phone_expr)} LIKE %s"
        + f" OR {_fold_sql_text(notes_expr)} LIKE %s)"
    )


def customers_arama_params_6_randevu(q: str):
    p = _pct(q)
    return (p, p, p, p, p, p)


# Eski yanlış isimlerle import eden kodlar kırılmasın (4 = çekirdek ILIKE sayısı değil; tarihsel isim)
customers_arama_params_3 = customers_arama_params_4
customers_arama_params_4_phone = customers_arama_params_5_phone
customers_arama_params_5_randevu = customers_arama_params_6_randevu
