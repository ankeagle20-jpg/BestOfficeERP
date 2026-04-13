"""
customers tablosunda ortak metin araması.

- Şirket ünvanı: name
- Müşteri adı (kısa/görünen): customers.musteri_adi + musteri_kyc.musteri_adi (son KYC kaydı)
- Yetkili ad soyad: yetkili_kisi

Geniş arama (`customers_arama_sql_giris_genis`): ayrıca vergi dairesi, adresler, telefonlar,
e-postalar, T.C., KYC alanları (ünvan, ikametgah, yetkili iletişim vb.). Telefonda ayrıca
sorgudaki rakamlar (en az 3 hane) format fark etmeksizin cep / cep 2 / KYC yetkili hatlar ile eşleşir.
"""

from __future__ import annotations

import re
import unicodedata

from utils.text_utils import turkish_lower

# Telefon kısmi arama: sorgudan çıkan rakam sayısı bu eşikten azsa devreye girmez (her şeyi eşleştirmeyi önler)
_GIRIS_GENIS_TELEFON_RAKAM_MIN = 3


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


def _ilike_pct_escaped(q: str) -> str:
    """ILIKE ... ESCAPE '\\' için % ve _ jokerlerini güvenli kaçış."""
    n = normalize_musteri_arama_tr(q or "")
    esc = n.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def musteri_arama_ilike_pattern_email_duz(q: str) -> str:
    """
    api_musteriler yedek e-posta araması: NFD/turkish_lower kullanmaz (ASCII e-posta eşleşmesi için).
    Tam genişlik @ (U+FF20) düz @ yapılır.
    """
    s = (q or "").strip().replace("\uff20", "@").lower()
    esc = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _sql_email_ws_squeeze_lower(expr_sql: str) -> str:
    """E-posta: tüm boşlukları at + küçük harf (DB / sorgu format farkını kapatır)."""
    return f"regexp_replace(lower({expr_sql}), '[[:space:]]+', '', 'g')"


def _giris_genis_eposta_sql(table_alias: str) -> str:
    """
    E-posta araması: translate+LIKE bazen @/. veya _ ile sürpriz üretir; ILIKE + strpos ile yedekler.
    strpos tarafında boşluklar sıkıştırılır (ör. «zafer @ gmail.com» ile «zafer@gmail.com»).
    7× %s — (iğne,iğne,iğne,iğne, ilike, ilike, ilike).
    """
    a = f"{table_alias.strip()}." if table_alias and table_alias.strip() else ""
    id_ref = f"{a}id"
    mail_cust = f"TRIM(COALESCE({a}email, ''))"
    mk_yetkili_mail = "TRIM(COALESCE(mk.yetkili_email, ''))"
    mk_sirket_mail = "TRIM(COALESCE(mk.email, ''))"
    needle_c = "regexp_replace(lower(TRIM(%s)), '[[:space:]]+', '', 'g')"
    return (
        "("
        f"(TRIM(%s) <> '' AND ("
        f"strpos({_sql_email_ws_squeeze_lower(mail_cust)}, {needle_c}) > 0 OR "
        f"EXISTS (SELECT 1 FROM musteri_kyc mk WHERE mk.musteri_id = {id_ref} AND ("
        f"strpos({_sql_email_ws_squeeze_lower(mk_yetkili_mail)}, {needle_c}) > 0 OR "
        f"strpos({_sql_email_ws_squeeze_lower(mk_sirket_mail)}, {needle_c}) > 0"
        f")))) OR "
        f"{mail_cust} ILIKE %s ESCAPE '\\' OR "
        f"EXISTS (SELECT 1 FROM musteri_kyc mk WHERE mk.musteri_id = {id_ref} AND ("
        f"TRIM(COALESCE(mk.yetkili_email, '')) ILIKE %s ESCAPE '\\' OR "
        f"TRIM(COALESCE(mk.email, '')) ILIKE %s ESCAPE '\\'"
        f"))"
        ")"
    )


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
    """Eski dar arama: çekirdek + vergi no + ofis kodu (6 placeholder). Dashboard ile karıştırma."""
    tax_expr = "COALESCE(tax_number::text, '')"
    office_expr = "COALESCE(office_code::text, '')"
    return (
        "("
        + customers_arama_sql_3("").strip("()")
        + f" OR {_fold_sql_text(tax_expr)} LIKE %s"
        + f" OR {_fold_sql_text(office_expr)} LIKE %s)"
    )


def customers_arama_params_6(q: str):
    """`customers_arama_sql_3_plus_phone_tax` ile uyumlu 6 placeholder."""
    p = _pct(q)
    return (p, p, p, p, p, p)


# Geniş müşteri araması: formdaki tüm ana iletişim / kimlik alanları + KYC satırları
_kyc_arama_kolonlari_ensure_done = False


def _ensure_musteri_kyc_arama_kolonlari_lazy() -> None:
    """musteri_kyc'de geniş arama için gerekli kolonlar (eski veritabanlarında eksik olabiliyordu)."""
    global _kyc_arama_kolonlari_ensure_done
    if _kyc_arama_kolonlari_ensure_done:
        return
    try:
        from db import ensure_musteri_kyc_arama_kolonlari

        ensure_musteri_kyc_arama_kolonlari()
    except Exception:
        return
    _kyc_arama_kolonlari_ensure_done = True


_GIRIS_GENIS_MK_ALANLARI = (
    "musteri_adi",
    "vergi_dairesi",
    "yeni_adres",
    "yetkili_ikametgah",
    "yetkili_adsoyad",
    "yetkili_tcno",
    "yetkili_tel",
    "yetkili_tel2",
    "yetkili_email",
    "email",
    "sirket_unvani",
    "unvan",
    "vergi_no",
    "notlar",
)


def _mk_kyc_coalesce_text(col: str) -> str:
    """TRIM(COALESCE(mk.col::text, '')) — boşluklu kayıtların e-posta / telefon eşleşmesi için."""
    return f"TRIM(COALESCE(mk.{col}::text, ''))"


def _telefon_arama_digits(q: str) -> str:
    """Arama kutusundan yalnız rakamlar (0532 111 22 33 ↔ 5321112233)."""
    return re.sub(r"\D", "", str(q or ""))


def _giris_genis_telefon_rakam_sql(table_alias: str) -> str:
    """
    Metin LIKE ile tutmayan formatlı numaralar için: kolon ve sorgu rakamlara indirgenir.
    5 adet %s — hepsi aynı «rakam dizisi» parametresi (char_length >= min + 4 LIKE).
    """
    a = f"{table_alias.strip()}." if table_alias and table_alias.strip() else ""
    id_ref = f"{a}id"

    def _rx(expr: str) -> str:
        return f"regexp_replace(TRIM(COALESCE({expr}, '')), '[^0-9]', '', 'g')"

    c1 = _rx(f"{a}phone")
    c2 = _rx(f"{a}phone2")
    m1 = _rx("mk.yetkili_tel")
    m2 = _rx("mk.yetkili_tel2")
    return (
        f"(char_length(TRIM(%s)) >= {_GIRIS_GENIS_TELEFON_RAKAM_MIN} AND ("
        f"{c1} LIKE ('%%' || TRIM(%s) || '%%') OR "
        f"{c2} LIKE ('%%' || TRIM(%s) || '%%') OR "
        f"EXISTS (SELECT 1 FROM musteri_kyc mk WHERE mk.musteri_id = {id_ref} AND ("
        f"{m1} LIKE ('%%' || TRIM(%s) || '%%') OR "
        f"{m2} LIKE ('%%' || TRIM(%s) || '%%')"
        f"))))"
    )


def _giris_genis_cust_search_exprs(table_alias: str = "") -> list[tuple[str, str]]:
    """Geniş aramada customers tarafı kolonları (alan adı, SQL ifadesi)."""
    a = f"{table_alias.strip()}." if table_alias and table_alias.strip() else ""
    return [
        ("name", f"TRIM(COALESCE({a}name, ''))"),
        ("musteri_adi", f"TRIM(COALESCE({a}musteri_adi, ''))"),
        ("yetkili_kisi", f"TRIM(COALESCE({a}yetkili_kisi, ''))"),
        ("tax_number", f"TRIM(COALESCE({a}tax_number::text, ''))"),
        ("office_code", f"TRIM(COALESCE({a}office_code::text, ''))"),
        ("vergi_dairesi", f"TRIM(COALESCE({a}vergi_dairesi, ''))"),
        ("address", f"TRIM(COALESCE({a}address, ''))"),
        ("ev_adres", f"TRIM(COALESCE({a}ev_adres, ''))"),
        ("phone", f"TRIM(COALESCE({a}phone, ''))"),
        ("phone2", f"TRIM(COALESCE({a}phone2, ''))"),
        ("email", f"TRIM(COALESCE({a}email, ''))"),
        ("yetkili_tcno", f"TRIM(COALESCE({a}yetkili_tcno::text, ''))"),
        ("musteri_no", f"TRIM(COALESCE({a}musteri_no::text, ''))"),
        ("notes", f"TRIM(COALESCE({a}notes, ''))"),
    ]


def customers_arama_sql_giris_genis(table_alias: str = "") -> str:
    """
    Müşteri kartı + Giriş üst arama + Cari kart listesi için tam metin araması.
    table_alias: örn. \"c\" → kolonlar c.name, EXISTS ... mk.musteri_id = c.id
    """
    a = f"{table_alias.strip()}." if table_alias and table_alias.strip() else ""
    id_ref = f"{a}id"
    mk_parts = [
        f"{_fold_sql_text(_mk_kyc_coalesce_text(col))} LIKE %s" for col in _GIRIS_GENIS_MK_ALANLARI
    ]
    mk_sql = " OR ".join(mk_parts)
    exists_mk = f"EXISTS (SELECT 1 FROM musteri_kyc mk WHERE mk.musteri_id = {id_ref} AND ({mk_sql}))"
    cust_cols = _giris_genis_cust_search_exprs(table_alias)
    parts = [_fold_sql_text(expr) + " LIKE %s" for _key, expr in cust_cols]
    parts.append(exists_mk)
    parts.append(_giris_genis_telefon_rakam_sql(table_alias))
    parts.append(_giris_genis_eposta_sql(table_alias))
    return "(" + " OR ".join(parts) + ")"


def customers_arama_params_giris_genis(q: str):
    _ensure_musteri_kyc_arama_kolonlari_lazy()
    p = _pct(q)
    n = len(_GIRIS_GENIS_MK_ALANLARI) + len(_giris_genis_cust_search_exprs())
    digits = _telefon_arama_digits(q)
    needle = normalize_musteri_arama_tr(q or "")
    p_ilike = _ilike_pct_escaped(q)
    # Telefon rakam bloğu: 5× aynı parametre (uzunluk eşiği + 4 LIKE)
    # E-posta bloğu: 4× iğne (strpos + boşluk kontrolü) + 3× kaçışlı ILIKE
    return (p,) * n + (digits, digits, digits, digits, digits) + (needle, needle, needle, needle, p_ilike, p_ilike, p_ilike)


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
    """Randevu combobox: geniş arama + notlar."""
    notes_expr = "COALESCE(notes, '')"
    return "(" + customers_arama_sql_giris_genis("").strip("()") + f" OR {_fold_sql_text(notes_expr)} LIKE %s)"


def customers_arama_params_6_randevu(q: str):
    p = _pct(q)
    return customers_arama_params_giris_genis(q) + (p,)


# Eski yanlış isimlerle import eden kodlar kırılmasın (4 = çekirdek ILIKE sayısı değil; tarihsel isim)
customers_arama_params_3 = customers_arama_params_4
customers_arama_params_4_phone = customers_arama_params_5_phone
customers_arama_params_5_randevu = customers_arama_params_6_randevu
