# -*- coding: utf-8 -*-
"""Ortak audit_log yazımı. Tablo yalnız bu yazma yolunda oluşturulur.

Başarısız yazım işlemi durdurmaz; loglanır. Telefon, TC ve e-posta yazılmaz.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal

_LOG = logging.getLogger("bestofficeerp.audit")

_YASAK = {
    "telefon",
    "gsm",
    "tc",
    "tckn",
    "tc_kimlik",
    "email",
    "e_posta",
    "eposta",
    "mail",
}

SQL_TABLO = """
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    olay TEXT NOT NULL,
    varlik TEXT NOT NULL,
    kayit_id INTEGER,
    musteri_id INTEGER,
    kullanici TEXT,
    anlik TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""".strip()

SQL_INSERT = """
INSERT INTO audit_log (olay, varlik, kayit_id, musteri_id, kullanici, anlik)
VALUES (%s, %s, %s, %s, %s, %s)
""".strip()


def _deger(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def anlik_al(row: dict | None, alanlar) -> dict:
    """İzinli alanların anlık görüntüsü. Yasak anahtarlar (telefon/TC/e-posta) düşer."""
    kaynak = row or {}
    out = {}
    for ad in alanlar:
        anahtar = str(ad)
        if anahtar.lower() in _YASAK or anahtar not in kaynak:
            continue
        out[anahtar] = _deger(kaynak.get(anahtar))
    if "nakit_tutar" in kaynak or "banka_tutar" in kaynak:
        nakit = kaynak.get("nakit_tutar")
        banka = kaynak.get("banka_tutar")
        if nakit not in (None, "") and banka not in (None, ""):
            out["odeme"] = "karma"
        elif nakit not in (None, ""):
            out["odeme"] = "nakit"
        else:
            out["odeme"] = "banka"
    return out


def audit_yaz(
    calistir,
    *,
    olay: str,
    varlik: str,
    kayit_id,
    musteri_id,
    kullanici: str,
    anlik: dict,
) -> bool:
    """audit_log tablosunu yazma anında açar ve satır ekler.

    Hata olursa False döner, çağıran işleme devam eder. PostgreSQL'de SAVEPOINT
    sayesinde bozuk audit cümlesi asıl işlemin transaction'ını düşürmez.
    """
    try:
        calistir("SAVEPOINT audit_yaz")
        calistir(SQL_TABLO)
        govde = {k: v for k, v in (anlik or {}).items() if str(k).lower() not in _YASAK}
        calistir(
            SQL_INSERT,
            (
                str(olay or "")[:80],
                str(varlik or "")[:80],
                int(kayit_id) if kayit_id not in (None, "") else None,
                int(musteri_id) if musteri_id not in (None, "") else None,
                str(kullanici or "")[:80],
                json.dumps(govde, ensure_ascii=False, default=str),
            ),
        )
        calistir("RELEASE SAVEPOINT audit_yaz")
        return True
    except Exception:
        _LOG.exception("audit yazilamadi")
        try:
            calistir("ROLLBACK TO SAVEPOINT audit_yaz")
        except Exception:
            _LOG.exception("audit savepoint geri alinamadi")
        return False
