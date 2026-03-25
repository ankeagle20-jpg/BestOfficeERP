#!/usr/bin/env python3
"""
erp_web/MUSTERI_KARTI_YUKLEME_LISTESI.xlsx dosyasını customers tablosuna toplu yükler.

Bağlantı: .env → DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD veya DB_PASS

Excel başlıkları (eşleşme büyük/küçük harf ve fazla boşluk toleranslı):
  İsim/Ünvan doluysa customers.name = ünvan (listedeki ad); Müşteri Adı → musteri_adi (kişi/kısa ad).
  Ünvan yoksa name = Müşteri Adı (eski davranış).
  Statü → hizmet_turu (Mükellef / Sanal→Sanal ofis / Oda→Hazır Ofis / Masa→Paylaşımlı Masa)
  Telefon1 → customers.phone: dtype=str, sütun adları strip, NFKC + BOM/NBSP temizliği,
    yalnız rakam; TR 11 hane 0… → son 10; yabancı 10–20 hane; başlık/çöp satırlar log+drop;
    Mükerrer (aynı müşteri adı + telefon) varsayılan olarak silinmez; istenirse:
    --mukerrer-at  |  Önce listelemek için: --mukerrer-rapor
    INSERT öncesi son telefon doğrulaması aynı.
  Telefon2 → customers.phone2 (aynı rakam kuralı; 10 haneden kısa ise NULL)
  E-Mail / E-Posta → customers.email (yetkili e-postası; @ yoksa NULL)
  Vergi No → tax_number (boş veya 'None' ise T.C. ile doldurulur)
  T.C. Kim. No → yetkili_tcno
  Aylık Taksit → ilk_kira_bedeli + guncel_kira_bedeli
  Sözleşme Tarihi → rent_start_date

İsim/Ünvan sütunu varsa name önce buradan alınır (şirket kayıtları listede görünür).
İsteğe bağlı: Yetkili → yetkili_kisi, Adres → ev_adres.

Sabit: vergi_dairesi = Kavaklıdere, address = kavaklıdere mah. …

Not: Birincil telefon kolonu veritabanında `phone` (Excel’deki Telefon1).

Güncelleme (mevcut kayıtlar):

    python yukle_musteri_karti_excel.py --guncelle

Eski importlardan kalan customers.phone / phone2 (boşluk, ‘Telefon 1’ metni vb.) — tek komut:

    python yukle_musteri_karti_excel.py --db-telefon-normalize

Aynı Excel dosyasını okur; müşteriyi vergi no veya T.C. (rakam eşlemesi) ile bulur
ve telefon, e-posta, ünvan, kira, sözleşme tarihi, hizmet türü vb. alanları yazar.
Notlar güncellemede değiştirilmez.
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from phone_util import canonical_tr_mobile_digits
from psycopg2.extras import execute_values

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

EXCEL_YOLU = _ROOT / "MUSTERI_KARTI_YUKLEME_LISTESI.xlsx"

SABIT_VERGI_DAIRESI = "Kavaklıdere"
SABIT_SU_ANKI_ADRES = "kavaklıdere mah. esat cad. no:12/1 çankaya /ankara"

_AYLAR_TR = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)

_STATU_MAP = {
    "mükellef": "Mükellef",
    "mukellef": "Mükellef",
    "sanal": "Sanal ofis",
    "oda": "Hazır Ofis",
    "masa": "Paylaşımlı Masa",
}


def _baslik_norm(s: object) -> str:
    t = str(s).strip().lower().replace("ı", "i")
    t = re.sub(r"\s+", " ", t)
    return t


def _sutun_bul(df: pd.DataFrame, *adaylar: str) -> str | None:
    hedefler = {_baslik_norm(a) for a in adaylar}
    for c in df.columns:
        if _baslik_norm(c) in hedefler:
            return str(c)
    return None


def _db_password() -> str:
    return (os.environ.get("DB_PASSWORD") or os.environ.get("DB_PASS") or "").strip()


def _connect():
    host = os.environ.get("DB_HOST", "").strip()
    user = os.environ.get("DB_USER", "postgres").strip()
    pw = _db_password()
    name = os.environ.get("DB_NAME", "postgres").strip()
    port = int(os.environ.get("DB_PORT", "5432") or 5432)
    if not host or not pw:
        sys.exit("DB_HOST ve DB_PASSWORD (veya DB_PASS) .env içinde tanımlı olmalı.")
    return psycopg2.connect(
        host=host,
        port=port,
        dbname=name,
        user=user,
        password=pw,
        sslmode="require",
        connect_timeout=15,
    )


def _temiz_tc_vergi(val) -> str | None:
    """Excel sayısal TC/VKN sonundaki .0 vb. kaldır."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if isinstance(val, float) and val == int(val):
            s = str(int(val))
        else:
            s = str(val).rstrip("0").rstrip(".").rstrip("0")
        return s.strip() or None
    s = str(val).strip()
    s = re.sub(r"\.0+$", "", s)
    s = re.sub(r"\s+", "", s)
    return s or None


def _metin(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s or None


def _email_al(val) -> str | None:
    """Excel e-posta hücresi → customers.email; geçersiz veya @ yoksa NULL."""
    s = _metin(val)
    if not s:
        return None
    s = _gorsel_metin_normalize(s)
    s = re.sub(r"\s+", "", s)
    if "@" not in s or len(s) < 5:
        return None
    return s.lower()


def _telefon_db(val) -> str | None:
    """DB’ye gidecek telefon: yalnız rakam; TR 0+kırpma / yabancı uzun."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, str) and val.strip() == "":
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    d = _rakam_dizisi(str(val))
    return canonical_tr_mobile_digits(d)


def _excel_hucre_metin(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip()
    if s.lower() in ("nan", "none", "<na>"):
        return ""
    return s


def _gorsel_metin_normalize(s: str) -> str:
    """BOM, NBSP, Excel ‘gizli’ boşlukları; NFKC ile birleşik karakterler."""
    if not s:
        return ""
    t = unicodedata.normalize("NFKC", s)
    t = (
        t.replace("\ufeff", "")
        .replace("\u00a0", " ")
        .replace("\u2007", " ")
        .replace("\u202f", " ")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
    )
    return t.strip()


def _rakam_dizisi(s: str) -> str:
    return re.sub(r"\D", "", _gorsel_metin_normalize(s))


def _satir_harf_var(s: str) -> bool:
    return any(c.isalpha() for c in s)


_TRASH_TEL_RE = re.compile(
    r"^(tel|tele|telf|telof|telef|phone|fon)[oóö]?n?\s*\d?\s*$",
    re.IGNORECASE,
)


def _telefon_cop_metin_mi(s: str) -> bool:
    compact = re.sub(r"\s+", "", s.lower())
    if compact in {
        "telefon1",
        "telefon2",
        "telofon1",
        "telofon2",
        "telefon",
        "telofon",
        "phone1",
        "phone2",
        "telfon1",
        "telfon2",
    }:
        return True
    if _TRASH_TEL_RE.match(compact) or _TRASH_TEL_RE.match(s.strip()):
        return True
    return False


def _telefon1_sert_filtre(
    df: pd.DataFrame,
    col_tel1: str,
    col_musteri: str,
    *,
    mukerrer_at: bool = False,
) -> pd.DataFrame:
    """Telefon1: Unicode/boşluk, yalnız rakam; harf/çöp satırları logla sil.
    mukerrer_at=True ise aynı (müşteri adı + telefon) satırlarından ilki kalır, diğerleri atılır."""
    idx_to_excel = {idx: i + 2 for i, idx in enumerate(df.index)}

    raw = df[col_tel1].map(_excel_hucre_metin)
    raw_vis = raw.map(_gorsel_metin_normalize)
    digits_only = raw_vis.map(_rakam_dizisi)
    phone = digits_only.map(canonical_tr_mobile_digits)

    # satır bazlı gerekçe (vektör yerine döngü — doğru log için)
    drop_idx: list[tuple[object, str]] = []
    for idx in df.index:
        r = raw_vis.loc[idx]
        dig_full = digits_only.loc[idx]
        ph = phone.loc[idx]
        sebep: str | None = None
        if not r:
            sebep = "Telefon1 boş"
        else:
            if _telefon_cop_metin_mi(r):
                sebep = "başlık / çöp metin (telefon kelimesi)"
            elif _satir_harf_var(r):
                sebep = "Telefon1 içinde harf var"
            elif len(dig_full) < 10:
                sebep = "rakam sayısı 10 haneden kısa"
            elif ph is None or not str(ph).isdigit():
                sebep = "geçerli telefon (rakam formatı) üretilemedi"
        if sebep:
            drop_idx.append((idx, sebep))
            ex = idx_to_excel.get(idx, "?")
            print(
                f"Şu sebeple silindi: {sebep} | Excel satır ~{ex} | [Satır İçeriği]: {raw.loc[idx]!r}",
                file=sys.stderr,
            )

    if drop_idx:
        df = df.drop(index=[d[0] for d in drop_idx]).copy()
        raw = df[col_tel1].map(_excel_hucre_metin)
        raw_vis = raw.map(_gorsel_metin_normalize)
        digits_only = raw_vis.map(_rakam_dizisi)
        phone = digits_only.map(canonical_tr_mobile_digits)

    def _hane_str(x) -> str:
        if x is None:
            return ""
        try:
            if pd.isna(x):
                return ""
        except (TypeError, ValueError):
            pass
        return str(x)

    df["phone"] = phone.map(_hane_str)
    df[col_tel1] = df["phone"]
    df.drop(columns=["phone"], errors="ignore", inplace=True)

    if mukerrer_at:
        df["customer_name"] = df[col_musteri].map(_excel_hucre_metin).str.strip()
        df["phone"] = df[col_tel1].astype(str)
        dup_mask = df.duplicated(subset=["customer_name", "phone"], keep="first")
        for idx in df.loc[dup_mask].index:
            ex = df.at[idx, "_excel_satir"] if "_excel_satir" in df.columns else idx_to_excel.get(idx, "?")
            r_ad = df.loc[idx, "customer_name"]
            r_ph = df.loc[idx, "phone"]
            print(
                "Şu sebeple silindi: mükerrer (müşteri adı + telefon) | Excel satır "
                f"{ex} | [Satır İçeriği]: müşteri={r_ad!r} telefon={r_ph!r}",
                file=sys.stderr,
            )
        df = df.drop_duplicates(subset=["customer_name", "phone"], keep="first")
        df.drop(columns=["phone", "customer_name"], errors="ignore", inplace=True)

    return df


def _mukerrer_rapor_yaz(df: pd.DataFrame, cm: dict[str, str]) -> None:
    """Aynı Müşteri Adı + Telefon1 birleşiminin tüm satırlarını listeler (_excel_satir varsa kullanır)."""
    m_col, t_col = cm["musteri_adi"], cm["tel1"]
    m = df[m_col].map(_excel_hucre_metin).str.strip()
    t = df[t_col].astype(str)
    keys = list(zip(m, t))
    by_key: dict[tuple[str, str], list[int]] = {}
    for i, k in enumerate(keys):
        ex = int(df.iloc[i]["_excel_satir"]) if "_excel_satir" in df.columns else i + 2
        by_key.setdefault(k, []).append(ex)
    dups = [(k, v) for k, v in by_key.items() if len(v) > 1]
    if not dups:
        print("Mükerrer yok: aynı (Müşteri Adı + Telefon1) çiftine birden fazla satır yok.")
        return
    print(f"Mükerrer grupları ({len(dups)} adet):\n")
    for (ad, tel), satirlar in sorted(dups, key=lambda x: (x[0][0], x[0][1])):
        print(f"  müşteri={ad!r}  telefon={tel!r}")
        print(f"    Excel satırları: {sorted(satirlar)}")


def _telefon2_temizle(df: pd.DataFrame, col2: str | None) -> pd.DataFrame:
    if not col2 or col2 not in df.columns:
        return df
    raw2 = df[col2].map(_excel_hucre_metin).map(_gorsel_metin_normalize)
    d2 = raw2.map(_rakam_dizisi)
    norm2 = d2.map(lambda d: canonical_tr_mobile_digits(d) if len(d) >= 10 else None)
    df[col2] = norm2.where(norm2.notna(), other=pd.NA)
    return df


def _tarih(val) -> date | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if hasattr(val, "date") and callable(getattr(val, "date")) and not isinstance(val, date):
        try:
            d = val.date()
            return d if isinstance(d, date) else None
        except Exception:
            pass
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = str(val).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _tutar(val) -> Decimal | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        if isinstance(val, str):
            v = val.replace(",", ".").replace(" ", "").strip()
            if not v:
                return None
            return Decimal(v)
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


def _vergi_no_bos(val) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    if not s:
        return True
    return s.lower() in ("none", "null", "nan")


def _rakam_anahtar(val) -> str | None:
    """Eşleştirme için yalnızca rakamlar (VKN/TC farklı formatlarında)."""
    if val is None:
        return None
    s = re.sub(r"\D", "", str(val).strip())
    return s or None


def _rakam_anahtarlari_ham(raw) -> list[str]:
    """Bir hücreden olası tüm eşleşme anahtarları (tireli VKN: her parça ayrı)."""
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    out: list[str] = []
    k = _rakam_anahtar(s)
    if k:
        out.append(k)
    if "-" in s:
        for part in s.split("-"):
            p = _rakam_anahtar(part)
            if p and p not in out:
                out.append(p)
    return out


def _notes_yetkili_tc(notes) -> str | None:
    if notes is None:
        return None
    m = re.search(r"(?i)yetkili\s*tc\s*:\s*([0-9\s]+)", str(notes))
    if not m:
        return None
    return _rakam_anahtar(m.group(1))


def _indeks_ekle(by_key: dict[str, int], ambiguous: set[str], k: str | None, rid: int) -> None:
    if not k or k in ambiguous:
        return
    if k in by_key:
        if by_key[k] != rid:
            ambiguous.add(k)
            del by_key[k]
    else:
        by_key[k] = rid


def _map_statu(val) -> str | None:
    s = _metin(val)
    if not s:
        return None
    key = s.strip().lower().replace("ı", "i")
    return _STATU_MAP.get(key, s)


def _satir_tuple(
    row: pd.Series,
    cm: dict[str, str],
) -> tuple:
    """cm: mantıksal ad → DataFrame sütun adı."""
    def al(anahtar: str):
        c = cm.get(anahtar)
        return row[c] if c is not None and c in row.index else None

    musteri_adi = _metin(al("musteri_adi"))
    unvan = _metin(al("unvan"))
    # Liste / arama: şirkette name = ünvan; Müşteri Adı çoğunlukla kişi adı (BurakKutoğlu vb.)
    if unvan:
        name = unvan
    else:
        name = musteri_adi or "İsimsiz"
    yetkili = _metin(al("yetkili")) if cm.get("yetkili") else None
    ev_adr = _metin(al("adres")) if cm.get("adres") else None

    tc = _temiz_tc_vergi(al("tc"))
    vkn = _temiz_tc_vergi(al("vergi"))
    tax_number = vkn if not _vergi_no_bos(vkn) else tc

    tel1 = _telefon_db(al("tel1"))
    tel2 = _telefon_db(al("tel2"))
    email = _email_al(al("email")) if cm.get("email") else None

    aylik = _tutar(al("aylik"))
    kira_ilk = aylik if aylik is not None else Decimal("0")
    kira_guncel = aylik if aylik is not None else Decimal("0")
    soz_bas = _tarih(al("sozlesme"))
    hizmet = _map_statu(al("statu"))

    return (
        name,
        musteri_adi,
        tax_number,
        tel1,
        tel2,
        email,
        SABIT_SU_ANKI_ADRES,
        ev_adr,
        SABIT_VERGI_DAIRESI,
        yetkili,
        tc,
        hizmet,
        soz_bas,
        kira_ilk,
        kira_guncel,
        None,
        "aktif",
    )


def _musteri_indeksi(cur) -> tuple[dict[str, int], set[str]]:
    """tax_number, yetkili_tcno ve notes içi Yetkili TC → id. Çakışan anahtarlar ambiguous."""
    cur.execute(
        "SELECT id, tax_number, yetkili_tcno, notes FROM customers",
    )
    by_key: dict[str, int] = {}
    ambiguous: set[str] = set()
    for rid, tax, ytc, notes in cur.fetchall():
        for raw in (tax, ytc):
            for k in _rakam_anahtarlari_ham(raw):
                _indeks_ekle(by_key, ambiguous, k, rid)
        ntc = _notes_yetkili_tc(notes)
        if ntc:
            _indeks_ekle(by_key, ambiguous, ntc, rid)
    return by_key, ambiguous


def _musteri_id_bul(
    by_key: dict[str, int],
    ambiguous: set[str],
    tax_number,
    tc,
) -> int | None:
    """Önce TC (11 hane), sonra vergi — ortak VKN’li kayıtlarda TC ayırt eder."""
    sira: list[str] = []
    tc_k = _rakam_anahtar(tc)
    if tc_k and len(tc_k) == 11:
        sira.append(tc_k)
    for k in _rakam_anahtarlari_ham(tax_number):
        if k not in sira:
            sira.append(k)
    if tc_k and len(tc_k) != 11 and tc_k not in sira:
        sira.append(tc_k)
    for k in sira:
        if k in ambiguous:
            continue
        cid = by_key.get(k)
        if cid is not None:
            return cid
    return None


def _sozlesme_yil_ay(soz: date | None) -> tuple[int | None, str | None]:
    if not soz:
        return None, None
    return soz.year, _AYLAR_TR[soz.month - 1]


def _satirlari_telefon_kilitle(rows: list[tuple]) -> list[tuple]:
    """INSERT/UPDATE öncesi: phone yalnız rakam; geçersiz birincil telefon satırını at."""
    out: list[tuple] = []
    for i, t in enumerate(rows):
        lst = list(t)
        p1 = _telefon_db(lst[3])
        p2 = _telefon_db(lst[4])
        if not p1:
            print(
                "Şu sebeple silindi: DB öncesi telefon doğrulama (birincil boş veya geçersiz) | "
                f"satır ~{i + 2} | [Satır İçeriği]: {lst[3]!r}",
                file=sys.stderr,
            )
            continue
        lst[3] = p1
        lst[4] = p2
        out.append(tuple(lst))
    return out


def _db_telefon_normalize(cur) -> int:
    """Mevcut customers.phone / phone2 — boşluk ve harfleri temizler; çöp metin → NULL."""
    cur.execute("SELECT id, phone, phone2 FROM customers")
    toplam = 0
    for rid, p1, p2 in cur.fetchall():
        n1 = _telefon_db(p1)
        n2 = _telefon_db(p2)
        cur.execute(
            "UPDATE customers SET phone = %s, phone2 = %s WHERE id = %s",
            (n1, n2, rid),
        )
        toplam += 1
    return toplam


def _ensure_customer_columns() -> None:
    from db import (
        ensure_customers_cari_columns,
        ensure_customers_durum,
        ensure_customers_excel_columns,
        ensure_customers_kapanis_tarihi,
        ensure_customers_musteri_adi,
        ensure_customers_notes,
        ensure_customers_rent_columns,
    )

    for fn in (
        ensure_customers_notes,
        ensure_customers_musteri_adi,
        ensure_customers_rent_columns,
        ensure_customers_excel_columns,
        ensure_customers_cari_columns,
        ensure_customers_durum,
        ensure_customers_kapanis_tarihi,
    ):
        try:
            fn()
        except Exception as ex:
            print("ensure uyarı:", fn.__name__, ex, file=sys.stderr)


def main() -> None:
    _ensure_customer_columns()

    if "--db-telefon-normalize" in sys.argv:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                n = _db_telefon_normalize(cur)
            conn.commit()
            print(f"Tamam: {n} müşteri kaydında phone / phone2 normalize edildi.")
        finally:
            conn.close()
        sys.exit(0)

    if not EXCEL_YOLU.is_file():
        sys.exit(f"Dosya bulunamadı: {EXCEL_YOLU}")

    df = pd.read_excel(
        EXCEL_YOLU,
        engine="openpyxl",
        header=0,
        dtype=str,
    )
    df.columns = df.columns.astype(str).str.strip()
    df.reset_index(drop=True, inplace=True)
    df["_excel_satir"] = df.index + 2

    cm: dict[str, str] = {}
    zorunlu = [
        ("musteri_adi", ("Müşteri Adı", "Musteri Adi")),
        ("statu", ("Statü", "Statu")),
        ("tel1", ("Telefon1", "Telefon 1")),
        ("vergi", ("Vergi No",)),
        ("tc", ("T.C. Kim. No", "T.C Kim. No", "TC Kim. No")),
        ("aylik", ("Aylık Taksit", "Aylik Taksit")),
        ("sozlesme", ("Sözleşme Tarihi", "Sozlesme Tarihi")),
    ]
    for key, adaylar in zorunlu:
        c = _sutun_bul(df, *adaylar)
        if not c:
            sys.exit(f"Excel'de zorunlu sütun bulunamadı: {adaylar[0]} (benzer başlıklar denendi)")
        cm[key] = c

    c_t2 = _sutun_bul(df, "Telefon2", "Telefon 2")
    if c_t2:
        cm["tel2"] = c_t2

    mukerrer_at = "--mukerrer-at" in sys.argv
    df = _telefon1_sert_filtre(df, cm["tel1"], cm["musteri_adi"], mukerrer_at=mukerrer_at)
    df = _telefon2_temizle(df, cm.get("tel2"))

    c_unvan = _sutun_bul(df, "İsim/Ünvan", "Isim/Unvan", "Ünvan", "Unvan")
    if c_unvan:
        cm["unvan"] = c_unvan

    c_yet = _sutun_bul(df, "Yetkili")
    if c_yet:
        cm["yetkili"] = c_yet

    c_adr = _sutun_bul(df, "Adres")
    if c_adr:
        cm["adres"] = c_adr

    c_mail = _sutun_bul(
        df,
        "E-Mail",
        "E-mail",
        "E-Posta",
        "E-posta",
        "Email",
        "Eposta",
        "E Posta",
    )
    if c_mail:
        cm["email"] = c_mail

    if "--mukerrer-rapor" in sys.argv or "--mükerrer-rapor" in sys.argv:
        _mukerrer_rapor_yaz(df, cm)
        sys.exit(0)

    df.drop(columns=["_excel_satir"], errors="ignore", inplace=True)

    rows = []
    for i in range(len(df)):
        try:
            rows.append(_satir_tuple(df.iloc[i], cm))
        except Exception as e:
            print(f"Satır {i + 2} atlandı: {e}", file=sys.stderr)

    if not rows:
        sys.exit("Yüklenecek satır yok.")

    rows = _satirlari_telefon_kilitle(rows)
    if not rows:
        sys.exit("Yüklenecek satır yok (telefon doğrulaması sonrası).")

    guncelle = "--guncelle" in sys.argv or "-g" in sys.argv

    conn = _connect()
    try:
        with conn.cursor() as cur:
            if guncelle:
                by_key, ambiguous = _musteri_indeksi(cur)
                if ambiguous:
                    print(
                        f"Uyarı: {len(ambiguous)} vergi/TC anahtarı birden fazla müşteride; bu anahtarlar atlanır.",
                        file=sys.stderr,
                    )
                sql_up = """
                    UPDATE customers SET
                        name = %s,
                        musteri_adi = %s,
                        tax_number = %s,
                        phone = %s,
                        phone2 = %s,
                        email = %s,
                        address = %s,
                        ev_adres = %s,
                        vergi_dairesi = %s,
                        yetkili_kisi = %s,
                        yetkili_tcno = %s,
                        hizmet_turu = %s,
                        rent_start_date = %s,
                        rent_start_year = %s,
                        rent_start_month = %s,
                        ilk_kira_bedeli = %s,
                        guncel_kira_bedeli = %s
                    WHERE id = %s
                """
                guncellenen = 0
                bulunamayan: list[tuple[int, str | None, str | None]] = []
                for idx, t in enumerate(rows):
                    (
                        name,
                        musteri_adi,
                        tax_number,
                        tel1,
                        tel2,
                        email,
                        address,
                        ev_adr,
                        vergi_dairesi,
                        yetkili,
                        tc,
                        hizmet,
                        soz_bas,
                        kira_ilk,
                        kira_guncel,
                        _notes,
                        _durum,
                    ) = t
                    cid = _musteri_id_bul(by_key, ambiguous, tax_number, tc)
                    if cid is None:
                        bulunamayan.append((idx + 2, tax_number, tc))
                        continue
                    yil, ay_ad = _sozlesme_yil_ay(soz_bas)
                    cur.execute(
                        sql_up,
                        (
                            name,
                            musteri_adi,
                            tax_number,
                            tel1,
                            tel2,
                            email,
                            address,
                            ev_adr,
                            vergi_dairesi,
                            yetkili,
                            tc,
                            hizmet,
                            soz_bas,
                            yil,
                            ay_ad,
                            kira_ilk,
                            kira_guncel,
                            cid,
                        ),
                    )
                    guncellenen += cur.rowcount or 0
                conn.commit()
                print(f"Tamam: {guncellenen} kayıt güncellendi (Excel: {len(rows)} satır).")
                if bulunamayan:
                    print(f"Eşleşmeyen satır: {len(bulunamayan)} (Excel satır no, tax_number, tc)", file=sys.stderr)
                    for satir_no, tn, tcno in bulunamayan[:25]:
                        print(f"  satır {satir_no}: tax={tn!r} tc={tcno!r}", file=sys.stderr)
                    if len(bulunamayan) > 25:
                        print(f"  ... ve {len(bulunamayan) - 25} satır daha", file=sys.stderr)
            else:
                sql = """
                    INSERT INTO customers (
                        name, musteri_adi, tax_number, phone, phone2, email, address, ev_adres,
                        vergi_dairesi, yetkili_kisi, yetkili_tcno, hizmet_turu, rent_start_date,
                        ilk_kira_bedeli, guncel_kira_bedeli, notes, durum
                    ) VALUES %s
                """
                execute_values(cur, sql, rows, page_size=500)
                conn.commit()
                print(f"Tamam: {len(rows)} kayıt customers tablosuna yüklendi.")
    except Exception as e:
        conn.rollback()
        print(f"Veritabanı hatası: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
