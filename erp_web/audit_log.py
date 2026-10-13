# -*- coding: utf-8 -*-
"""audit_log yazımı. Mevcut cari-360 tablosunun kolonlarına yazar.

Yeni kolon ve yeni tablo yok. ALTER yok. Tablo yoksa yazma yolunda,
db.py ensure_cari_360_tables ile aynı kolonlarla oluşturulur.
Başarısız yazım işlemi durdurmaz; loglanır. Telefon, TC, e-posta ve
serbest açıklama yazılmaz.
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
    "aciklama",
    "not",
    "neden",
    "mesaj",
    "icerik",
    "konu",
}

# db.py ensure_cari_360_tables ile birebir. Var olan tabloya kolon eklemez.
SQL_TABLO = """
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    tablo_adi TEXT,
    kayit_id INTEGER,
    islem TEXT,
    eski_deger TEXT,
    yeni_deger TEXT,
    user_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
""".strip()

SQL_INSERT = """
INSERT INTO audit_log (tablo_adi, kayit_id, islem, eski_deger, yeni_deger, user_id)
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
    """İzinli alanların anlık görüntüsü. Yasak anahtarlar düşer."""
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


def _kullanici_adi(kullanici) -> str:
    """Oturumdaki ad. Telefon, TC, e-posta ve serbest açıklama yazılmaz."""
    ad = str(kullanici or "").strip()[:80]
    if not ad or "@" in ad or ad.isdigit():
        return ""
    return ad


def _json_metin(govde, kullanici=None) -> str:
    temiz = {}
    for anahtar, deger in (govde or {}).items():
        ad = str(anahtar)
        if ad.lower() in _YASAK or ad.lower() == "kullanici":
            continue
        temiz[ad] = _deger(deger)
    ad = _kullanici_adi(kullanici)
    if ad:
        temiz["kullanici"] = ad
    return json.dumps(temiz, ensure_ascii=False, default=str)


def _oturum_id(kullanici) -> int | None:
    """user_id INTEGER. Metin kullanıcı adı kolona yazılmaz."""
    try:
        from flask_login import current_user

        raw = getattr(current_user, "id", None)
        if raw not in (None, ""):
            return int(raw)
    except (TypeError, ValueError):
        return None
    except Exception:
        pass
    metin = str(kullanici or "").strip()
    if metin.isdigit():
        return int(metin)
    return None


def audit_yaz(
    calistir,
    *,
    olay: str,
    varlik: str,
    kayit_id,
    musteri_id,
    kullanici: str,
    anlik: dict,
    yeni: dict | None = None,
    eski_yaz: bool = True,
) -> bool:
    """Mevcut audit_log kolonlarına bir satır ekler.

    Hata olursa False döner, çağıran işleme devam eder. PostgreSQL'de SAVEPOINT
    sayesinde bozuk audit cümlesi asıl işlemin transaction'ını düşürmez.
    """
    try:
        calistir("SAVEPOINT audit_yaz")
        calistir(SQL_TABLO)
        eski = dict(anlik or {})
        if eski_yaz and musteri_id not in (None, "") and "musteri_id" not in eski:
            eski["musteri_id"] = int(musteri_id)
        sonraki = dict(yeni or {})
        if musteri_id not in (None, "") and sonraki and "musteri_id" not in sonraki:
            sonraki["musteri_id"] = int(musteri_id)
        calistir(
            SQL_INSERT,
            (
                str(varlik or "")[:80],
                int(kayit_id) if kayit_id not in (None, "") else None,
                str(olay or "")[:80],
                _json_metin(eski, kullanici) if eski_yaz else None,
                _json_metin(sonraki, kullanici),
                _oturum_id(kullanici),
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
