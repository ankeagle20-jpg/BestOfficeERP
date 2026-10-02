# -*- coding: utf-8 -*-
"""Fatura belge numarası.

Resmi GIB2026… numarası burada üretilmez. Onu yalnız GİB yanıtı yazar.
Taslak: TASLAK-YYYY-#### (ekranda «Taslak»).
Dahili kira/borç: DAHILI-YYYY-#### (GİB serisine girmez).

İki seri de belge_no_sayac satırında tek INSERT … ON CONFLICT UPDATE
ile artar. PostgreSQL aynı (seri, yıl) satırını kilitler; eşzamanlı
çağrılar aynı numarayı alamaz.
"""
from __future__ import annotations

from datetime import datetime

from db import execute, fetch_one


def fatura_no_gorunen(no) -> str:
    s = str(no or "").strip()
    if not s or s.upper().startswith("TASLAK"):
        return "Taslak"
    return s


def _ensure_belge_sayac() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS belge_no_sayac (
            seri TEXT NOT NULL,
            yil INTEGER NOT NULL,
            son INTEGER NOT NULL,
            PRIMARY KEY (seri, yil)
        )
        """
    )
    eski = fetch_one(
        """
        SELECT 1 AS ok
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = 'dahili_belge_sayac'
        """
    )
    if not eski:
        return
    execute(
        """
        INSERT INTO belge_no_sayac (seri, yil, son)
        SELECT 'DAHILI', yil, son FROM dahili_belge_sayac
        ON CONFLICT (seri, yil) DO NOTHING
        """
    )


def _next_kilitli_no(seri: str) -> str:
    """seri: TASLAK veya DAHILI. Dönüş: SERI-YYYY-0001."""
    kod = str(seri or "").strip().upper()
    if kod not in ("TASLAK", "DAHILI"):
        raise ValueError("seri TASLAK veya DAHILI olmalı")
    yil = int(datetime.now().year)
    _ensure_belge_sayac()
    row = fetch_one(
        """
        INSERT INTO belge_no_sayac (seri, yil, son)
        VALUES (%s, %s, 1)
        ON CONFLICT (seri, yil)
        DO UPDATE SET son = belge_no_sayac.son + 1
        RETURNING son
        """,
        (kod, yil),
    ) or {}
    n = int(row.get("son") or 0)
    if n < 1:
        raise RuntimeError("belge_no_sayac artışı boş döndü")
    return f"{kod}-{yil}-{n:04d}"


def yeni_taslak_no() -> str:
    return _next_kilitli_no("TASLAK")


def next_dahili_no() -> str:
    return _next_kilitli_no("DAHILI")
