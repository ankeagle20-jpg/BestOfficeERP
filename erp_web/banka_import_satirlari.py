# -*- coding: utf-8 -*-
"""Banka Excel import: normalleştirilmiş satır kaydı (bayraklı).

Bayrak BANKA_IMPORT_DB_SATIR=1 iken yüklenen Excel, parse edildiği anda
satır satır `banka_import_satirlari` tablosuna yazılır; rapor (A2) bu
tablodan okur. Bayrak kapalıyken hiçbir şey çağrılmaz (eski yol aynen).

Kurallar:
- "Aktarıldı" durumu SAKLANMAZ; rapor tahsilatlar'da banka_referans_no
  varlığından canlı türetir. Tabloda yalnız parse + öneri önbelleği var.
- Öneri (müşteri eşleştirme) parse sonrası bir kez, mevcut algoritmayla
  hesaplanır ve `oneri_musteri_versiyonu` ile saklanır. Commit anında
  yeniden doğrulama route katmanında aynen yapılır (değişmez).
- 6278ff9 güvencesi: satir_anahtari sira içerir; ref'siz aynı
  tarih/tutarlı iki ayrı satır ikisi de saklanır. Dosyalar arası dedupe
  bu katmanda YOK (ham birleştirme katmanında "ilk kazanır" kalır).
- Dosya silinince satırlar FK ON DELETE CASCADE ile gider.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

_LOG = logging.getLogger(__name__)

TABLO = "banka_import_satirlari"
DOSYA_TABLO = "akbank_import_dosyalar"
PARSE_SURUMU = 1
PARCA = 200

SQL_TABLO = f"""
CREATE TABLE IF NOT EXISTS {TABLO} (
    id SERIAL PRIMARY KEY,
    dosya_id INTEGER NOT NULL REFERENCES {DOSYA_TABLO}(id) ON DELETE CASCADE,
    banka TEXT,
    yon TEXT,
    tarih DATE,
    saat TEXT,
    sira INTEGER,
    tutar NUMERIC(14, 2),
    aciklama TEXT,
    banka_referans_no TEXT,
    satir_anahtari TEXT NOT NULL,
    oneri_musteri_id INTEGER,
    oneri_label TEXT,
    oneri_status TEXT,
    oneri_kaynak TEXT,
    oneri_musteri_versiyonu TEXT,
    parse_surumu INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dosya_id, satir_anahtari)
)
""".strip()

SQL_INDEKSLER = (
    f"CREATE INDEX IF NOT EXISTS ix_{TABLO}_dosya ON {TABLO} (dosya_id)",
    f"CREATE INDEX IF NOT EXISTS ix_{TABLO}_ref ON {TABLO} (banka_referans_no)",
    f"CREATE INDEX IF NOT EXISTS ix_{TABLO}_tarih ON {TABLO} (tarih)",
)

SQL_DOSYA_PARSE_KOLONLARI = (
    f"ALTER TABLE {DOSYA_TABLO} ADD COLUMN IF NOT EXISTS parse_durumu TEXT",
    f"ALTER TABLE {DOSYA_TABLO} ADD COLUMN IF NOT EXISTS parse_hata TEXT",
)

_SUTUNLAR = (
    "dosya_id", "banka", "yon", "tarih", "saat", "sira", "tutar", "aciklama",
    "banka_referans_no", "satir_anahtari", "oneri_musteri_id", "oneri_label",
    "oneri_status", "oneri_kaynak", "oneri_musteri_versiyonu", "parse_surumu",
)


def db_satir_aktif() -> bool:
    return str(os.environ.get("BANKA_IMPORT_DB_SATIR") or "").strip().lower() in (
        "1", "true", "yes", "evet",
    )


def ensure_banka_import_satirlari(calistir) -> None:
    """Lazy DDL. calistir: (sql, params=None) çalıştıran callable."""
    if not callable(calistir):
        raise TypeError("calistir callable olmali")
    calistir(SQL_TABLO)
    for sql in SQL_INDEKSLER:
        calistir(sql)
    for sql in SQL_DOSYA_PARSE_KOLONLARI:
        calistir(sql)


def validate_ddl_pglast() -> dict:
    """pglast ile DDL kolon doğrulaması (testler kullanır; DB gerekmez)."""
    from pglast import parse_sql

    bare = SQL_TABLO.replace("IF NOT EXISTS ", "")
    stmts = parse_sql(bare)
    if not stmts:
        raise ValueError("pglast: bos parse")
    create = stmts[0].stmt
    cols = [e.colname for e in (create.tableElts or ()) if hasattr(e, "colname") and e.colname]
    expected = {
        "id", "dosya_id", "banka", "yon", "tarih", "saat", "sira", "tutar",
        "aciklama", "banka_referans_no", "satir_anahtari", "oneri_musteri_id",
        "oneri_label", "oneri_status", "oneri_kaynak",
        "oneri_musteri_versiyonu", "parse_surumu", "created_at",
    }
    return {"table": create.relation.relname, "cols": cols,
            "missing": sorted(expected - set(cols))}


def satir_anahtari(ref, tarih, tutar, sira) -> str:
    """Dosya içi tekil anahtar. sira DAHİL: ref'siz aynı tarih/tutarlı
    iki ayrı satır (6278ff9) ikisi de saklanır."""
    ham = f"{ref or ''}|{tarih or ''}|{float(tutar or 0):.2f}|{sira}"
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()[:40]


def musteri_versiyonu(oku) -> str:
    """Önerinin hesaplandığı müşteri listesi sürümü: adet + max(updated_at)."""
    rows = oku("SELECT COUNT(*) AS n, MAX(updated_at) AS mx FROM customers") or []
    r = rows[0] if rows else {}
    ham = f"{r.get('n') or 0}:{r.get('mx') or ''}"
    return hashlib.sha1(ham.encode("utf-8")).hexdigest()[:16]


def _satir_degerleri(dosya_id, banka, onizleme_row, versiyon):
    r = onizleme_row or {}
    em = r.get("eslestirme") or {}
    ref = str(r.get("banka_referans_no") or "").strip()
    sira = r.get("sira")
    try:
        sira_i = int(sira)
    except (TypeError, ValueError):
        sira_i = 0
    mid_oneri = r.get("musteri_id_oneri") or em.get("musteri_id")
    try:
        mid_oneri = int(mid_oneri) if mid_oneri is not None else None
    except (TypeError, ValueError):
        mid_oneri = None
    return (
        int(dosya_id),
        str(banka or "")[:40] or None,
        str(r.get("yon") or r.get("tip") or "")[:16] or None,
        str(r.get("tarih") or "")[:10] or None,
        str(r.get("saat") or "")[:20] or None,
        sira_i,
        round(float(r.get("tutar") or 0), 2),
        str(r.get("aciklama") or ""),
        ref or None,
        satir_anahtari(ref, r.get("tarih"), r.get("tutar"), sira_i),
        mid_oneri,
        str(r.get("musteri_label_oneri") or em.get("musteri_label") or "")[:300] or None,
        str(r.get("ui_status") or em.get("status") or "")[:24] or None,
        str(em.get("kaynak") or "")[:24] or None,
        str(versiyon or "")[:32] or None,
        PARSE_SURUMU,
    )


def satirlari_kaydet(cur, *, dosya_id, banka, onizlemeler, versiyon) -> int:
    """Tek dosyanın satırlarını (önerileriyle) yazar; eskileri siler.

    Çağıranın transaction'ı içinde çalışır. Önce DELETE (dosya_id),
    sonra toplu INSERT — tekrar çağrı idempotenttir.
    """
    def _cal(sql, params=None):
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        return cur

    ensure_banka_import_satirlari(_cal)
    cur.execute(f"DELETE FROM {TABLO} WHERE dosya_id = %s", (int(dosya_id),))
    rows = [
        _satir_degerleri(dosya_id, banka, r, versiyon) for r in (onizlemeler or [])
    ]
    for bas in range(0, len(rows), PARCA):
        parca = rows[bas:bas + PARCA]
        degerler = ", ".join(
            "(" + ", ".join(["%s"] * len(_SUTUNLAR)) + ")" for _ in parca
        )
        params = []
        for v in parca:
            params.extend(v)
        cur.execute(
            f"INSERT INTO {TABLO} ({', '.join(_SUTUNLAR)}) VALUES {degerler}",
            tuple(params),
        )
    return len(rows)


def satir_durumu(oku, dosya_id) -> dict:
    """Dosyanın kayıtlı satır durumu: {adet, parse_surumu, versiyonlar}."""
    rows = oku(
        f"SELECT COUNT(*) AS n, MAX(parse_surumu) AS ps, "
        f"MAX(oneri_musteri_versiyonu) AS v FROM {TABLO} WHERE dosya_id = %s",
        (int(dosya_id),),
    ) or []
    r = rows[0] if rows else {}
    return {
        "adet": int(r.get("n") or 0),
        "parse_surumu": r.get("ps"),
        "versiyon": r.get("v"),
        "guncel": bool(r.get("n")) and int(r.get("ps") or 0) >= PARSE_SURUMU,
    }


def dosya_parse_isaretle(calistir, dosya_id, durum, hata=None) -> None:
    calistir(
        f"UPDATE {DOSYA_TABLO} SET parse_durumu = %s, parse_hata = %s WHERE id = %s",
        (str(durum)[:16], (str(hata)[:300] if hata else None), int(dosya_id)),
    )


def parse_ve_kaydet_dosya(cur, *, dosya_id, banka, ham, musteriler,
                          manual_by_key, versiyon) -> int:
    """Ham satırlardan öneri hesapla + kaydet (mevcut algoritma aynen)."""
    from services.banka_ak_import import onizleme_satirlari

    onizlemeler = onizleme_satirlari(ham, musteriler, set(), manual_by_key or {})
    return satirlari_kaydet(
        cur, dosya_id=dosya_id, banka=banka,
        onizlemeler=onizlemeler, versiyon=versiyon,
    )


def ozet_json() -> str:
    return json.dumps({"parse_surumu": PARSE_SURUMU, "tablo": TABLO})
