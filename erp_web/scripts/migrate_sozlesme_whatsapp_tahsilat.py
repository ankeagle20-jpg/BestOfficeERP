# -*- coding: utf-8 -*-
"""sozlesme_whatsapp_gonderim tahsilat sutunlari.

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
_ISTENEN = (
    ("tahsilat_id", "integer"),
    ("makbuz_no", "text"),
)


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


def _uygula(execute, sema: str) -> None:
    from psycopg2 import sql as psql

    tablo = psql.SQL("{}.{}").format(psql.Identifier(sema), psql.Identifier("sozlesme_whatsapp_gonderim"))
    execute(
        psql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS tahsilat_id INTEGER").format(tablo)
    )
    execute(
        psql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS makbuz_no TEXT").format(tablo)
    )
    execute(
        psql.SQL(
            "CREATE INDEX IF NOT EXISTS sozlesme_whatsapp_gonderim_tahsilat ON {} (tahsilat_id)"
        ).format(tablo)
    )


def _yaz(etiket: str, kolon: list[str], adet: int, on_ek: str) -> None:
    var = {p.split(":", 1)[0] for p in kolon}
    eksik = [ad for ad, _tur in _ISTENEN if ad not in var]
    print("%s %s satir=%s sutun=%s eksik=%s" % (on_ek, etiket, adet, ",".join(kolon), ",".join(eksik) or "-"))


def main() -> int:
    sql_yol = Path(__file__).with_suffix(".sql")
    sql = sql_yol.read_text(encoding="utf-8")
    if "ADD COLUMN IF NOT EXISTS" not in sql or "DROP TABLE" in sql.upper():
        print("SQL eksik veya genis")
        return 1
    if "--uygula" not in sys.argv and "--liste" not in sys.argv:
        print("DRY sozlesme_whatsapp tahsilat sutun. Calistirilmadi.")
        return 0
    if "--uygula" in sys.argv and not _yazma_izni():
        print("BAGLANTI YOK. SQL dosyasi onay sonrasi ayrica uygulanir.")
        return 2

    kok = Path(__file__).resolve().parents[1]
    if str(kok) not in sys.path:
        sys.path.insert(0, str(kok))
    from db import execute, fetch_all, fetch_one

    semalar = _sema_listesi(fetch_all)
    if not semalar:
        print("TABLO_YOK")
        return 1
    kiraci = 0
    once = []
    for ad in semalar:
        if ad != "public":
            kiraci += 1
            etiket = _etiket(ad, kiraci)
        else:
            etiket = "public"
        kolon, adet = _durum(fetch_one, ad)
        once.append((ad, etiket, adet))
        _yaz(etiket, kolon, adet, "ONCE")
    if "--liste" in sys.argv and "--uygula" not in sys.argv:
        print("LISTE sema_public=%s kiraci=%s" % (sum(1 for a, _, _ in once if a == "public"), kiraci))
        return 0
    for ad, _etiket, _adet in once:
        _uygula(execute, ad)
    kiraci = 0
    for ad, etiket, eski in once:
        kolon, adet = _durum(fetch_one, ad)
        if adet != eski:
            print("SATIR_FARK %s" % etiket)
            return 1
        var = {p.split(":", 1)[0] for p in kolon}
        if any(ad not in var for ad, _tur in _ISTENEN):
            print("SUTUN_EKSIK %s" % etiket)
            return 1
        _yaz(etiket, kolon, adet, "SONRA")
    print("UYGULANDI public=%s kiraci=%s" % (sum(1 for a, _, _ in once if a == "public"), sum(1 for a, _, _ in once if a != "public")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
