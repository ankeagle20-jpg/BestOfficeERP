# -*- coding: utf-8 -*-
"""
Merkezi banka Excel işlemcisi: farklı banka formatlarını StandardTransaction'a dönüştürür.
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Iterable, List, Optional, Union

import pandas as pd

# Banka türü sabitleri (upload_bank_excel bank_type ile eşleşir)
BANK_AKBANK = "AKBANK"
BANK_TURKIYE_FINANS = "TURKIYE_FINANS"


@dataclass
class StandardTransaction:
    """Tüm bankalar için ortak hareket modeli (veritabanına yazıma hazır)."""

    date: datetime
    description: str
    amount: float
    balance: float
    reference_no: str
    bank_name: str

    def as_dict(self) -> dict:
        """ORM / raw SQL insert için sözlük (datetime ISO string)."""
        d = asdict(self)
        d["date"] = self.date.isoformat(sep=" ")
        return d


def _norm_header(x: Any) -> str:
    """Excel başlıkları — Türkçe İ/i ve birleşik nokta artıkları dahil tutarlı anahtar."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip()
    s = s.replace("\ufeff", "").replace("\xa0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold()
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


def _first_col(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    cols = {_norm_header(c): c for c in df.columns}
    for want in candidates:
        w = _norm_header(want)
        if w in cols:
            return cols[w]
    for c in df.columns:
        cn = _norm_header(c)
        for want in candidates:
            wn = _norm_header(want)
            if wn in cn or cn in wn:
                return c
    return None


def _parse_tutar_magnitude_tr(val: Any) -> float:
    """Tutar hücresinden mutlak değer (Türkçe 1.234,56)."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return abs(float(val))
    s = str(val).strip()
    s = s.replace(" ", "").replace("TL", "").replace("\u20ba", "").replace("TRY", "")
    s = s.replace(".", "").replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s == ".":
        return 0.0
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


def _parse_tutar_signed_tr(val: Any) -> float:
    """Tutar — işaret korunur (Türkiye Finans vb.)."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace(" ", "").replace("TL", "").replace("\u20ba", "").replace("TRY", "")
    if s.startswith("-"):
        neg = True
        s = s[1:]
    s = s.replace(".", "").replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s == ".":
        return 0.0
    try:
        v = float(s)
        return -abs(v) if neg else v
    except ValueError:
        return 0.0


def _akbank_borc_mu(val: Any) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    s = str(val).strip().upper()
    return s == "B" or s.startswith("BORC") or "BORÇ" in s


def _to_datetime_cell(val: Any) -> Optional[datetime]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    ts = pd.to_datetime(val, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def _combine_tarih_saat(tarih: Any, saat: Any) -> Optional[datetime]:
    d = _to_datetime_cell(tarih)
    if d is None:
        return None
    if saat is None or (isinstance(saat, float) and pd.isna(saat)):
        return d
    if isinstance(saat, (datetime, pd.Timestamp)):
        tpart = saat.time()
        return datetime.combine(d.date(), tpart)
    if isinstance(saat, (int, float)) and not isinstance(saat, bool):
        # Excel saat kesri
        try:
            whole = int(saat)
            frac = float(saat) - whole
            if abs(frac) < 1e-9:
                return d
            secs = int(round(frac * 86400))
            return d.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(seconds=secs)
        except (ValueError, OverflowError):
            pass
    s = str(saat).strip()
    if not s:
        return d
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            tpart = datetime.strptime(s, fmt).time()
            return datetime.combine(d.date(), tpart)
        except ValueError:
            continue
    return d


def _parse_tf_islem_tarihi(val: Any) -> Optional[datetime]:
    """İşlem Tarihi: DD.MM.YYYY HH:MM (veya sadece tarih)."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    dt = _to_datetime_cell(val)
    if dt is not None:
        return dt
    s = str(val).strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_balance(val: Any) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    return _parse_tutar_signed_tr(val)


def _normalize_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Tek satır başlık + MultiIndex (birleşik hücre) için sütun adlarını tek anahtara indirger."""
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        newcols: list[str] = []
        for tup in out.columns:
            parts: list[str] = []
            for p in tup:
                if p is None or (isinstance(p, float) and pd.isna(p)):
                    continue
                s = str(p).strip()
                if not s or s.lower() == "nan":
                    continue
                parts.append(s)
            newcols.append(_norm_header(" ".join(parts)))
        out.columns = newcols
    else:
        out.columns = [_norm_header(c) for c in out.columns]
    return out


def _excel_header_signals_joined(joined: str) -> bool:
    """Ekstre tablosu olabilecek başlık satırı (gevşek eşleşme)."""
    return any(
        k in joined
        for k in (
            "tarih",
            "islem",
            "valor",
            "tutar",
            "borc",
            "alacak",
            "bakiye",
            "dekont",
            "fis",
            "aciklama",
        )
    )


def _cell_to_ref_str(val: Any) -> str:
    """Excel sayı olarak gelen fiş/dekont no → metin (bilimsel gösterim kaçınması)."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if isinstance(val, float):
        try:
            if abs(val) < 1e18 and abs(val - round(val)) < 1e-9 * max(1.0, abs(val)):
                return str(int(round(val)))
        except (ValueError, OverflowError):
            pass
    s = str(val).strip()
    if len(s) > 2 and s.endswith(".0") and s[:-2].replace("-", "").isdigit():
        return s[:-2]
    return s


def _to_bytesio(file: Union[str, Path, bytes, bytearray, BinaryIO, Any]) -> BinaryIO:
    if isinstance(file, (str, Path)):
        with open(file, "rb") as f:
            return io.BytesIO(f.read())
    if isinstance(file, (bytes, bytearray)):
        return io.BytesIO(bytes(file))
    if hasattr(file, "read") and callable(getattr(file, "read")):
        data = file.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        bio = io.BytesIO(data)
        if hasattr(file, "seek"):
            try:
                file.seek(0)
            except Exception:
                pass
        return bio
    raise TypeError("file: path, bytes veya read() destekleyen nesne olmalı")


def _read_excel_find_header(bio: BinaryIO, max_skip: int = 45, sheet_name: object = 0) -> pd.DataFrame:
    last_err: Optional[Exception] = None
    for skip in range(max_skip + 1):
        bio.seek(0)
        try:
            df = pd.read_excel(
                bio,
                engine="openpyxl",
                sheet_name=sheet_name,
                header=0,
                skiprows=skip,
            )
        except Exception as e:
            last_err = e
            continue
        if df is None or df.empty:
            continue
        df = _normalize_dataframe_columns(df)
        joined = " ".join(str(c) for c in df.columns)
        if _excel_header_signals_joined(joined):
            return df
    bio.seek(0)
    if last_err:
        raise ValueError("Excel okunamadı veya başlık satırı bulunamadı") from last_err
    raise ValueError("Excel başlık satırı bulunamadı")


class BankProcessor:
    """Banka tipine göre DataFrame satırlarını StandardTransaction listesine çevirir."""

    def process(self, df: pd.DataFrame, bank_type: str) -> List[StandardTransaction]:
        t = (bank_type or "").strip().upper().replace("İ", "I")
        aliases_tf = {BANK_TURKIYE_FINANS, "TF", "TURKIYE FINANS", "TÜRKİYE FİNANS"}
        if t == BANK_AKBANK or t == "AK BANK":
            return self._process_akbank(df)
        if t in aliases_tf:
            return self._process_turkiye_finans(df)
        raise ValueError(f"Desteklenmeyen bank_type: {bank_type!r}. Kullan: {BANK_AKBANK}, {BANK_TURKIYE_FINANS}")

    def _process_akbank(self, df: pd.DataFrame) -> List[StandardTransaction]:
        c_tarih = _first_col(df, ["tarih"])
        c_saat = _first_col(df, ["saat"])
        c_tutar = _first_col(df, ["tutar"])
        c_ba = _first_col(df, ["borç/alacak", "borc/alacak", "borç alacak", "b/a"])
        c_ref = _first_col(df, ["fiş/dekont no", "fis/dekont no", "fiş dekont no", "dekont no"])
        c_aciklama = _first_col(df, ["açıklama", "aciklama"])
        c_bakiye = _first_col(df, ["bakiye", "kalan bakiye", "hesap bakiyesi"])

        out: List[StandardTransaction] = []
        for _, row in df.iterrows():
            if c_tarih is None:
                continue
            tarih = row.get(c_tarih)
            if tarih is None or (isinstance(tarih, float) and pd.isna(tarih)):
                continue
            saat = row.get(c_saat) if c_saat else None
            dt = _combine_tarih_saat(tarih, saat)
            if dt is None:
                continue
            mag = _parse_tutar_magnitude_tr(row.get(c_tutar) if c_tutar else None)
            borc = _akbank_borc_mu(row.get(c_ba) if c_ba else None)
            amount = -mag if borc else mag
            desc = ""
            if c_aciklama:
                v = row.get(c_aciklama)
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    desc = str(v).strip()
            ref = ""
            if c_ref:
                v = row.get(c_ref)
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    ref = str(v).strip()
            bal = _parse_balance(row.get(c_bakiye)) if c_bakiye else 0.0
            out.append(
                StandardTransaction(
                    date=dt,
                    description=desc,
                    amount=amount,
                    balance=bal,
                    reference_no=ref,
                    bank_name="Akbank",
                )
            )
        return out

    def _process_turkiye_finans(self, df: pd.DataFrame) -> List[StandardTransaction]:
        # TF kolonları (Akbank eşlemesi): İşlem Tarihi → "Tarih + Saat" veya Tarih+Saat;
        # İşlem Referansı → Fiş/Dekont No; tutar tek sütun veya Borç/Alacak ayrımı.
        c_ts_birlesik = _first_col(df, ["tarih + saat", "tarih+saat", "tarih ve saat", "tarih / saat"])
        if c_ts_birlesik:
            c_tarih = c_ts_birlesik
            c_saat_tf: Optional[str] = None
            tarih_birlesik = True
        else:
            c_tarih = _first_col(
                df,
                [
                    "işlem tarihi",
                    "islem tarihi",
                    "işlem zamanı",
                    "islem zamani",
                    "valor tarihi",
                    "valor",
                    "tarih",
                ],
            )
            c_saat_tf = _first_col(df, ["saat"])
            tarih_birlesik = False

        c_tutar = _first_col(
            df,
            [
                "tutar",
                "işlem tutarı",
                "islem tutari",
                "net tutar",
                "hareket tutarı",
                "hareket tutari",
                "tutar (tl)",
            ],
        )
        c_alacak = _first_col(df, ["alacak", "alacak tutarı", "alacak tutari", "alacak (tl)", "credit"])
        c_borc = _first_col(df, ["borç", "borc", "borç tutarı", "borc tutari", "borç (tl)", "debit"])
        c_ref = _first_col(
            df,
            [
                "fiş/dekont no",
                "fis/dekont no",
                "fiş dekont no",
                "fis dekont no",
                "dekont no",
                "işlem referansı",
                "islem referansi",
                "referans",
                "referans no",
                "islem no",
            ],
        )
        c_aciklama = _first_col(
            df,
            ["açıklama", "aciklama", "işlem açıklaması", "islem aciklamasi", "detay", "açıklama / detay"],
        )
        c_bakiye = _first_col(df, ["bakiye", "hesap bakiyesi", "kalan bakiye"])

        if c_tarih is None or (c_tutar is None and c_alacak is None and c_borc is None):
            return []

        out: List[StandardTransaction] = []
        for _, row in df.iterrows():
            raw_t = row.get(c_tarih)
            if raw_t is None or (isinstance(raw_t, float) and pd.isna(raw_t)):
                continue
            if tarih_birlesik:
                dt = _parse_tf_islem_tarihi(raw_t) or _to_datetime_cell(raw_t)
            elif c_saat_tf:
                dt = _combine_tarih_saat(raw_t, row.get(c_saat_tf))
            else:
                dt = _parse_tf_islem_tarihi(raw_t) or _to_datetime_cell(raw_t)
            if dt is None:
                continue

            amount: Optional[float] = None
            if c_tutar:
                v = row.get(c_tutar)
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    t0 = _parse_tutar_signed_tr(v)
                    if t0 != 0.0:
                        amount = t0
            if amount is None and (c_alacak is not None or c_borc is not None):
                a = _parse_tutar_magnitude_tr(row.get(c_alacak)) if c_alacak else 0.0
                b = _parse_tutar_magnitude_tr(row.get(c_borc)) if c_borc else 0.0
                if a > 0.0 and b <= 0.0:
                    amount = float(a)
                elif b > 0.0 and a <= 0.0:
                    amount = -float(b)
                elif a > 0.0 and b > 0.0:
                    amount = float(a) - float(b)
            if amount is None:
                continue

            desc = ""
            if c_aciklama:
                v = row.get(c_aciklama)
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    desc = str(v).strip()
            ref = ""
            if c_ref:
                ref = _cell_to_ref_str(row.get(c_ref))
            bal = _parse_balance(row.get(c_bakiye)) if c_bakiye else 0.0
            out.append(
                StandardTransaction(
                    date=dt,
                    description=desc,
                    amount=amount,
                    balance=bal,
                    reference_no=ref,
                    bank_name="Türkiye Finans",
                )
            )
        return out


_default_processor = BankProcessor()


def standard_transactions_to_tahsilat_ham(
    transactions: List[StandardTransaction],
) -> tuple[List[dict], dict]:
    """
    `banka_ak_import` tahsilat önizleme boru hattı için ham satırlar (Akbank dataframe_hareket_satirlari ile uyumlu).
    Yalnızca pozitif tutar (gelen) ve dolu dekont/referans satırları tahsilat adayıdır.
    """
    ozet: dict = {
        "excel_satir": 0,
        "a_degil": 0,
        "ref_bos": 0,
        "tutar_sifir": 0,
        "tarih_yok": 0,
        "islenen": 0,
    }
    satirlar: List[dict] = []
    for sira, t in enumerate(transactions, start=1):
        ozet["excel_satir"] += 1
        ref = (t.reference_no or "").strip()
        if not ref:
            ozet["ref_bos"] += 1
            continue
        amt = float(t.amount)
        if amt <= 0:
            ozet["a_degil"] += 1
            continue
        d = t.date
        if d is None:
            ozet["tarih_yok"] += 1
            continue
        tarih_d = d.date() if isinstance(d, datetime) else d
        if tarih_d is None:
            ozet["tarih_yok"] += 1
            continue
        tarih_str = tarih_d.isoformat() if hasattr(tarih_d, "isoformat") else str(tarih_d)[:10]
        acik = (t.description or "").strip()
        ozet["islenen"] += 1
        satirlar.append(
            {
                "sira": sira,
                "excel_index": str(sira),
                "tarih": tarih_str,
                "saat": "",
                "tutar": round(amt, 2),
                "aciklama": acik,
                "banka_referans_no": ref,
            }
        )
    return satirlar, ozet


def _tf_sheet_best_transactions(bio: BinaryIO, sheet_name: object) -> List[StandardTransaction]:
    """Bir sheet için: skiprows ile başlık, olmazsa header=N satırı dene; en çok işlem satırını döndür."""
    best_local: List[StandardTransaction] = []
    try:
        df0 = _read_excel_find_header(bio, sheet_name=sheet_name)
        cand0 = _default_processor.process(df0, BANK_TURKIYE_FINANS)
        if len(cand0) > len(best_local):
            best_local = cand0
    except Exception:
        pass
    if len(best_local) > 0:
        return best_local
    for hr in range(0, 55):
        try:
            bio.seek(0)
            raw = pd.read_excel(bio, engine="openpyxl", sheet_name=sheet_name, header=hr)
            if raw is None or raw.empty or len(raw.columns) < 2:
                continue
            dfn = _normalize_dataframe_columns(raw)
            joined = " ".join(str(c) for c in dfn.columns)
            if not _excel_header_signals_joined(joined):
                continue
            cand = _default_processor.process(dfn, BANK_TURKIYE_FINANS)
            if len(cand) > len(best_local):
                best_local = cand
        except Exception:
            continue
    return best_local


def upload_bank_excel(file: Union[str, Path, bytes, BinaryIO, Any], bank_type: str) -> List[StandardTransaction]:
    """
    Excel dosyasını okuyup bank_type'a göre StandardTransaction listesi döndürür.
    file: dosya yolu, bytes veya read() destekleyen nesne (ör. Flask FileStorage).
    """
    bio = _to_bytesio(file)
    bt = (bank_type or "").strip().upper().replace("İ", "I")
    aliases_tf = {BANK_TURKIYE_FINANS, "TF", "TURKIYE FINANS", "TÜRKİYE FİNANS"}
    if bt not in aliases_tf:
        df = _read_excel_find_header(bio)
        return _default_processor.process(df, bank_type)

    best: List[StandardTransaction] = []
    try:
        bio.seek(0)
        xls = pd.ExcelFile(bio, engine="openpyxl")
        for sname in xls.sheet_names:
            cand = _tf_sheet_best_transactions(bio, sname)
            if len(cand) > len(best):
                best = cand
        return best
    except Exception:
        df = _read_excel_find_header(bio)
        return _default_processor.process(df, bank_type)


def bulk_upsert_banka_hareketleri(
    transactions: List[StandardTransaction],
    banka_hesap_id: int,
    *,
    batch_size: int = 400,
) -> dict[str, int]:
    """
    StandardTransaction kayıtlarını Supabase PostgreSQL `banka_hareketleri` tablosuna toplu yazar.

    Dolu `referans_no` (dekont / işlem referansı) veritabanında zaten varsa satır eklenmez;
    PostgreSQL kısmi UNIQUE indeks + ON CONFLICT DO NOTHING ile hata verilmez, satır atlanır.
    Boş referanslar indekse dahil değildir; aynı satırın referansı yoksa tekrar yüklemede yinelenme olabilir.

    Dönüş sözlüğü: toplam (girdi satırı), eklenen (gerçekten INSERT olan), atlanan (toplam - eklenen).

    Not: İlk çağrıda `db.ensure_banka_hareketleri_import_columns()` ile gerekli sütun ve indeks oluşturulur.
    """
    from psycopg2.extras import execute_values

    from db import db, ensure_banka_hareketleri_import_columns

    ensure_banka_hareketleri_import_columns()

    if banka_hesap_id is None or int(banka_hesap_id) < 1:
        raise ValueError("banka_hesap_id geçerli bir pozitif tamsayı olmalıdır.")

    if not transactions:
        return {"toplam": 0, "eklenen": 0, "atlanan": 0}

    rows: List[tuple] = []
    for t in transactions:
        tip = "giden" if float(t.amount) < 0 else "gelen"
        tutar = abs(float(t.amount))
        d = t.date
        hareket_tarihi = d.date() if isinstance(d, datetime) else d
        ref_raw = (t.reference_no or "").strip()
        referans_no = ref_raw if ref_raw else None
        aciklama = (t.description or "").strip()[:500]
        bakiye_ekstre = float(t.balance)
        kaynak = (t.bank_name or "").strip()[:200] or None
        rows.append(
            (
                int(banka_hesap_id),
                hareket_tarihi,
                aciklama,
                "",
                tutar,
                tip,
                "bekleyen",
                referans_no,
                bakiye_ekstre,
                kaynak,
            )
        )

    inserted_total = 0
    n = len(rows)
    with db() as conn:
        cur = conn.cursor()
        for i in range(0, n, max(1, batch_size)):
            chunk = rows[i : i + max(1, batch_size)]
            execute_values(
                cur,
                """INSERT INTO banka_hareketleri (
                    banka_hesap_id, hareket_tarihi, aciklama, gonderici, tutar, tip, durum,
                    referans_no, bakiye_ekstre, kaynak_banka_adi
                ) VALUES %s
                ON CONFLICT (referans_no)
                WHERE referans_no IS NOT NULL AND btrim(referans_no) <> ''
                DO NOTHING""",
                chunk,
            )
            inserted_total += cur.rowcount or 0

    return {"toplam": n, "eklenen": inserted_total, "atlanan": n - inserted_total}
