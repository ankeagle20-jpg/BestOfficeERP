#!/usr/bin/env python3
"""
BestOffice ERP — Akbank Excel tahsilat içe aktarma.

Kullanım:
  python import_bank_data.py yol/ekstre.xlsx
  python import_bank_data.py yol/ekstre.xlsx --musteri-id 42

Sütunlar (Akbank dışa aktarım): Tarih, Saat, Tutar, Açıklama, Borç/Alacak, Fiş/Dekont No
Yalnızca Borç/Alacak = 'A' (alacak) satırları işlenir.
Fiş/Dekont No, tahsilatlar.banka_referans_no ile mükerrer kontrol edilir.

.env: DATABASE_URL veya SUPABASE_DB_URL / DB_* (erp_web ile aynı).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ERP_WEB = ROOT / "erp_web"
if str(ERP_WEB) not in sys.path:
    sys.path.insert(0, str(ERP_WEB))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    load_dotenv(ERP_WEB / ".env")
except ImportError:
    pass

try:
    import pandas as pd
except ImportError as e:
    print("pandas gerekli: pip install pandas openpyxl", file=sys.stderr)
    raise SystemExit(1) from e

try:
    import psycopg2
    from psycopg2 import extras as pgx
except ImportError as e:
    print("psycopg2 gerekli: pip install psycopg2-binary", file=sys.stderr)
    raise SystemExit(1) from e


def _norm_header(c: object) -> str:
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


def _parse_tutar_tr(val: object) -> float:
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


def _parse_tarih(val: object) -> date | None:
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


def _read_akbank_excel(path: Path) -> pd.DataFrame:
    last_err: Exception | None = None
    for skip in range(0, 18):
        try:
            df = pd.read_excel(path, engine="openpyxl", skiprows=skip, header=0)
            if df.empty:
                continue
            cols_norm = [_norm_header(c) for c in df.columns]
            if any("tarih" in c for c in cols_norm) and any(
                "borc" in c or "alacak" in c or "fis" in c or "dekont" in c for c in cols_norm
            ):
                df.columns = cols_norm
                return df
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise RuntimeError(f"Excel okunamadı (skiprows denendi): {last_err}") from last_err
    raise RuntimeError("Akbank ekstre başlığı bulunamadı (Tarih / Borç-Alacak vb.).")


def _pick_tutar_column(df: pd.DataFrame) -> str | None:
    """Birleşik hücrede boş kalan 'Tutar' yanındaki tutar sütununu seç."""
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
            ser = df[c].apply(_parse_tutar_tr)
            if ser.sum() > 0 or ser.max() > 0:
                return c
    best = None
    best_sum = -1.0
    for c in candidates:
        s = df[c].apply(_parse_tutar_tr).sum()
        if s > best_sum:
            best_sum = s
            best = c
    return best


def _col(df: pd.DataFrame, *names: str) -> str | None:
    for n in names:
        for c in df.columns:
            if str(c).strip().lower() == n:
                return c
    for n in names:
        for c in df.columns:
            if n in str(c).lower():
                return c
    return None


def _ensure_banka_referans_column(cur) -> None:
    cur.execute("ALTER TABLE tahsilatlar ADD COLUMN IF NOT EXISTS banka_referans_no TEXT")


def _existing_refs(cur, refs: list[str]) -> set[str]:
    if not refs:
        return set()
    cur.execute(
        "SELECT banka_referans_no FROM tahsilatlar WHERE banka_referans_no IN %s",
        (tuple(refs),),
    )
    return {str(r["banka_referans_no"]) for r in cur.fetchall() if r.get("banka_referans_no")}


def main() -> int:
    p = argparse.ArgumentParser(description="Akbank Excel → tahsilatlar")
    p.add_argument("excel", type=Path, help="Akbank .xlsx dosyası")
    p.add_argument("--musteri-id", type=int, default=None, help="Tüm satırlar için müşteri id (opsiyonel)")
    p.add_argument("--dry-run", action="store_true", help="Veritabanına yazma, sadece özet")
    args = p.parse_args()

    path = args.excel.expanduser().resolve()
    if not path.is_file():
        print(f"Dosya bulunamadı: {path}", file=sys.stderr)
        return 1

    try:
        df = _read_akbank_excel(path)
    except Exception as e:
        print(f"HATA (Excel): {e}", file=sys.stderr)
        return 1

    col_tarih = _col(df, "tarih")
    col_saat = _col(df, "saat")
    col_tutar = _pick_tutar_column(df)
    col_aciklama = _col(df, "aciklama", "açiklama")
    col_ba = _col(df, "borç/alacak", "borc/alacak", "borc / alacak")
    col_fis = _col(df, "fiş/dekont no", "fis/dekont no", "fiş dekont no", "fis dekont no", "dekont no")

    missing = [n for n, c in [
        ("tarih", col_tarih),
        ("tutar", col_tutar),
        ("borc/alacak", col_ba),
        ("aciklama", col_aciklama),
        ("fis/dekont no", col_fis),
    ] if c is None]
    if missing:
        print(f"Eksik sütunlar: {missing}. Bulunan: {list(df.columns)}", file=sys.stderr)
        return 1

    rows_in = 0
    skipped_not_a = 0
    skipped_no_ref = 0
    skipped_zero = 0
    skipped_bad_date = 0
    to_insert: list[tuple] = []

    for _, row in df.iterrows():
        rows_in += 1
        ba = str(row[col_ba] if col_ba else "").strip().upper()
        if ba != "A":
            skipped_not_a += 1
            continue
        ref_raw = row[col_fis] if col_fis else None
        if ref_raw is None or (isinstance(ref_raw, float) and pd.isna(ref_raw)):
            skipped_no_ref += 1
            continue
        ref = str(ref_raw).strip()
        if not ref:
            skipped_no_ref += 1
            continue
        tutar = _parse_tutar_tr(row[col_tutar])
        if tutar <= 0:
            skipped_zero += 1
            continue
        d = _parse_tarih(row[col_tarih])
        if not d:
            skipped_bad_date += 1
            continue
        acik = str(row[col_aciklama] if col_aciklama else "").strip()
        if col_saat and col_saat in df.columns:
            sa = row[col_saat]
            if sa is not None and not (isinstance(sa, float) and pd.isna(sa)):
                sa_s = str(sa).strip()
                if sa_s:
                    acik = f"[Saat {sa_s}] {acik}".strip()

        to_insert.append((args.musteri_id, tutar, "havale", d, acik, ref))

    added = 0
    skipped_dup = 0

    try:
        from db import get_conn
    except Exception as e:
        print(f"HATA (db modülü): {e}. erp_web yolunun sys.path'te olduğundan emin olun.", file=sys.stderr)
        return 1

    if args.dry_run:
        refs = [t[5] for t in to_insert]
        uniq_refs = list(dict.fromkeys(refs))
        dup_db = 0
        conn_dry = None
        try:
            conn_dry = get_conn()
            cur = conn_dry.cursor(cursor_factory=pgx.RealDictCursor)
            _ensure_banka_referans_column(cur)
            ex = _existing_refs(cur, uniq_refs)
            dup_db = sum(1 for r in refs if r in ex)
            conn_dry.commit()
        except Exception as e:
            if conn_dry is not None:
                try:
                    conn_dry.rollback()
                except Exception:
                    pass
            print(f"Dry-run DB okuması atlandı: {e}")
        finally:
            if conn_dry is not None:
                try:
                    conn_dry.close()
                except Exception:
                    pass
        would_add = len(to_insert) - dup_db
        print("--- Dry-run özeti ---")
        print(f"İşlenebilir satır (A + tutar>0 + ref): {len(to_insert)}")
        print(f"Veritabanında zaten var (mükerrer): {dup_db}")
        print(f"Eklenecek olurdu            : {would_add}")
        print(f"Excel satırı (toplam)       : {rows_in}")
        print(f"Borç/Alacak != 'A'          : {skipped_not_a}")
        print(f"Referans boş                : {skipped_no_ref}")
        print(f"Tutar <= 0                  : {skipped_zero}")
        print(f"Tarih okunamadı             : {skipped_bad_date}")
        return 0

    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=pgx.RealDictCursor)
        _ensure_banka_referans_column(cur)

        refs_all = [t[5] for t in to_insert]
        existing = _existing_refs(cur, list(dict.fromkeys(refs_all)))

        insert_sql = """
            INSERT INTO tahsilatlar (
                musteri_id, customer_id, fatura_id, tutar, odeme_turu,
                aciklama, tahsilat_tarihi, makbuz_no, banka_referans_no
            ) VALUES (%s, NULL, NULL, %s, %s, %s, %s, NULL, %s)
        """

        for musteri_id, tutar, odeme, tah_tarih, aciklama, ref in to_insert:
            if ref in existing:
                skipped_dup += 1
                continue
            cur.execute(
                insert_sql,
                (musteri_id, tutar, odeme, aciklama, tah_tarih, ref),
            )
            existing.add(ref)
            added += 1

        conn.commit()
    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        print(f"HATA (PostgreSQL): {e}", file=sys.stderr)
        return 1
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    print("--- Akbank Excel içe aktarma özeti ---")
    print(f"Eklenen kayıt        : {added}")
    print(f"Mükerrer (atlandı)  : {skipped_dup}")
    print(f"Borç/Alacak != 'A'  : {skipped_not_a}")
    print(f"Referans boş        : {skipped_no_ref}")
    print(f"Tutar <= 0          : {skipped_zero}")
    print(f"Tarih okunamadı     : {skipped_bad_date}")
    print(f"Excel satırı (toplam): {rows_in}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nİptal.", file=sys.stderr)
        raise SystemExit(130) from None
