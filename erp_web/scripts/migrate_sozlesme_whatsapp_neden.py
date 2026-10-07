# -*- coding: utf-8 -*-
"""sozlesme_whatsapp_gonderim neden sutunu. Yalniz public.

--uygula olmadan ve ALLOW_PROD_WRITE olmadan yazmaz.
Geri alma SQL dosyada yorumdur; bu betik DROP calistirmaz.
Sema slug'i yazdirmaz.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_SEM_RE = re.compile(r"^(public|tenant_[a-z0-9_]+)$")


def _yazma_izni() -> bool:
    return (os.environ.get("ALLOW_PROD_WRITE") or "").strip().lower() in ("1", "true", "yes", "on")


def _etiket(ad: str, sira: int) -> str:
    if ad == "public":
        return "public"
    return "kiraci#%s" % sira


def _sema_listesi(fetch_all):
    rows = fetch_all(
        """
        SELECT n.nspname AS sema
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'sozlesme_whatsapp_gonderim'
          AND c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY n.nspname
        """
    )
    out = []
    for row in rows or []:
        ad = str(row.get("sema") or "")
        if _SEM_RE.fullmatch(ad):
            out.append(ad)
    return out


def _durum(fetch_one, sema: str) -> tuple[list[str], int]:
    from psycopg2 import sql as psql

    kolon = fetch_one(
        """
        SELECT COALESCE(string_agg(column_name || ':' || data_type, ',' ORDER BY ordinal_position), '') AS kolon
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = 'sozlesme_whatsapp_gonderim'
        """,
        (sema,),
    ) or {}
    adet = fetch_one(
        psql.SQL("SELECT COUNT(*)::bigint AS n FROM {}.{}").format(
            psql.Identifier(sema),
            psql.Identifier("sozlesme_whatsapp_gonderim"),
        )
    ) or {}
    parca = [p for p in str(kolon.get("kolon") or "").split(",") if p]
    return parca, int(adet.get("n") or 0)


def _uygula_public(execute) -> None:
    execute("ALTER TABLE public.sozlesme_whatsapp_gonderim ADD COLUMN IF NOT EXISTS neden TEXT")


def _yaz(etiket: str, kolon: list[str], adet: int, on_ek: str) -> None:
    var = {p.split(":", 1)[0] for p in kolon}
    eksik = "-" if "neden" in var else "neden"
    print("%s %s satir=%s sutun=%s eksik=%s" % (on_ek, etiket, adet, ",".join(kolon), eksik))


def main() -> int:
    sql_yol = Path(__file__).with_suffix(".sql")
    sql = sql_yol.read_text(encoding="utf-8")
    if "ADD COLUMN IF NOT EXISTS neden TEXT" not in sql or "DROP TABLE" in sql.upper():
        print("SQL eksik veya genis")
        return 1
    if "public.sozlesme_whatsapp_gonderim" not in sql:
        print("SQL public degil")
        return 1
    if "--uygula" not in sys.argv and "--liste" not in sys.argv:
        print("DRY sozlesme_whatsapp neden sutun. Calistirilmadi.")
        return 0
    if "--uygula" in sys.argv and not _yazma_izni():
        print("BAGLANTI YOK. SQL dosyasi onay sonrasi ayrica uygulanir.")
        return 2

    kok = Path(__file__).resolve().parents[1]
    if str(kok) not in sys.path:
        sys.path.insert(0, str(kok))
    from db import execute, fetch_all, fetch_one

    semalar = _sema_listesi(fetch_all)
    if "public" not in semalar:
        print("TABLO_YOK")
        return 1
    kiraci = 0
    once_adet = None
    for ad in semalar:
        if ad != "public":
            kiraci += 1
            etiket = _etiket(ad, kiraci)
        else:
            etiket = "public"
        kolon, adet = _durum(fetch_one, ad)
        if ad == "public":
            once_adet = adet
        _yaz(etiket, kolon, adet, "ONCE")
    if "--liste" in sys.argv and "--uygula" not in sys.argv:
        print("LISTE sema_public=1 kiraci=%s" % kiraci)
        return 0
    _uygula_public(execute)
    kolon, adet = _durum(fetch_one, "public")
    if adet != once_adet:
        print("SATIR_FARK public")
        return 1
    if "neden" not in {p.split(":", 1)[0] for p in kolon}:
        print("SUTUN_EKSIK public")
        return 1
    _yaz("public", kolon, adet, "SONRA")
    if kiraci:
        print("ATLANDI kiraci=%s" % kiraci)
    print("UYGULANDI public=1 kiraci=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
