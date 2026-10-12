# -*- coding: utf-8 -*-
"""sozlesme_plan_dondurma tablo ensure ve ekle/iptal/liste.

ensure yalnız yazmada. liste/okuma CREATE çağırmaz.
calistir/oku isteğin search_path'inde çalışır.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone

from sozlesme_plan_dondurma import aralik_cakisir, ay_listesi, dondurma_parmak

_SEMA_RE = re.compile(r"^(public|tenant_[a-z0-9_]+)$")

SQL_TABLO = """
CREATE TABLE IF NOT EXISTS sozlesme_plan_dondurma (
    id SERIAL PRIMARY KEY,
    musteri_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    baslangic_ay DATE NOT NULL,
    ay_sayisi INTEGER NOT NULL,
    dondurulan_aylar TEXT NOT NULL DEFAULT '[]',
    atlanan_aylar TEXT NOT NULL DEFAULT '[]',
    sozlesme_uzat BOOLEAN NOT NULL DEFAULT FALSE,
    eski_sozlesme_bitis DATE,
    eski_kira_suresi_ay INTEGER,
    olusturan TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    iptal_at TIMESTAMPTZ,
    iptal_eden TEXT,
    CONSTRAINT sozlesme_plan_dondurma_ay_basi CHECK (EXTRACT(DAY FROM baslangic_ay) = 1),
    CONSTRAINT sozlesme_plan_dondurma_ay_sayisi CHECK (ay_sayisi BETWEEN 1 AND 24)
)
""".strip()

SQL_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_sozlesme_plan_dondurma_acik
ON sozlesme_plan_dondurma (musteri_id, baslangic_ay)
WHERE iptal_at IS NULL
""".strip()

_KOLONLAR = """
id, musteri_id, baslangic_ay, ay_sayisi, dondurulan_aylar, atlanan_aylar,
sozlesme_uzat, eski_sozlesme_bitis, eski_kira_suresi_ay,
olusturan, created_at, iptal_at, iptal_eden
""".strip()

SQL_ACIK = f"""
SELECT {_KOLONLAR}
FROM sozlesme_plan_dondurma
WHERE musteri_id = %s AND iptal_at IS NULL
ORDER BY baslangic_ay, id
""".strip()

SQL_LISTE = f"""
SELECT {_KOLONLAR}
FROM sozlesme_plan_dondurma
WHERE musteri_id = %s AND (%s OR iptal_at IS NULL)
ORDER BY baslangic_ay, id
""".strip()

SQL_BY_ID = f"""
SELECT {_KOLONLAR}
FROM sozlesme_plan_dondurma
WHERE id = %s
""".strip()

SQL_INSERT = f"""
INSERT INTO sozlesme_plan_dondurma (
    musteri_id, baslangic_ay, ay_sayisi, dondurulan_aylar, atlanan_aylar,
    sozlesme_uzat, olusturan, created_at
) VALUES (%s, %s, %s, %s, %s, FALSE, %s, %s)
RETURNING {_KOLONLAR}
""".strip()

SQL_IPTAL = f"""
UPDATE sozlesme_plan_dondurma
SET iptal_at = %s, iptal_eden = %s
WHERE id = %s AND iptal_at IS NULL
RETURNING {_KOLONLAR}
""".strip()

_SQL_KOLON = """
SELECT column_name FROM information_schema.columns
WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s
""".strip()

SQL_BY_ID_SIL = f"""
SELECT {_KOLONLAR}, silindi_at, silen
FROM sozlesme_plan_dondurma
WHERE id = %s
""".strip()

SQL_SIL = f"""
UPDATE sozlesme_plan_dondurma
SET silindi_at = %s, silen = %s
WHERE id = %s AND silindi_at IS NULL
RETURNING {_KOLONLAR}, silindi_at, silen
""".strip()


class DondurmaCakisma(Exception):
    """Açık dondurma aralığı kesişiyor."""


class DondurmaYok(Exception):
    """İptal edilecek açık dondurma yok."""


def ensure_sozlesme_plan_dondurma(calistir) -> None:
    calistir(SQL_TABLO)
    calistir(SQL_INDEX)


def ensure_dondurma_silindi_kolonlari(calistir) -> None:
    """silindi_at / silen yalnız silme yazmasında."""
    calistir("ALTER TABLE sozlesme_plan_dondurma ADD COLUMN IF NOT EXISTS silindi_at TIMESTAMPTZ")
    calistir("ALTER TABLE sozlesme_plan_dondurma ADD COLUMN IF NOT EXISTS silen TEXT")


def kolon_var_mi(oku, tablo: str, kolon: str) -> bool:
    try:
        rows = oku(_SQL_KOLON, (tablo, kolon))
    except Exception:
        return False
    if not rows:
        return False
    if isinstance(rows, dict):
        return True
    try:
        return len(rows) > 0
    except TypeError:
        return False


def _silinmeyen(sql: str) -> str:
    return sql.replace("ORDER BY", "AND silindi_at IS NULL\nORDER BY", 1)


def ensure_sozlesme_plan_dondurma_semada(calistir, sema: str) -> None:
    ad = str(sema or "").strip()
    if not _SEMA_RE.fullmatch(ad):
        raise ValueError("sema adi gecersiz")
    calistir(f"SET LOCAL search_path TO {ad}, pg_catalog")
    ensure_sozlesme_plan_dondurma(calistir)


def _ay_basi(val) -> date:
    if isinstance(val, datetime):
        val = val.date()
    if isinstance(val, date):
        return date(val.year, val.month, 1)
    s = str(val).strip()[:10]
    if len(s) == 7:
        y, m = s.split("-")
        return date(int(y), int(m), 1)
    d = date.fromisoformat(s)
    return date(d.year, d.month, 1)


def _json_list(ham):
    if ham is None or ham == "":
        return []
    if isinstance(ham, list):
        return ham
    try:
        val = json.loads(ham)
    except (TypeError, ValueError):
        return []
    return val if isinstance(val, list) else []


def _satir(row) -> dict:
    if not row:
        return {}
    out = dict(row)
    out["dondurulan_aylar"] = _json_list(out.get("dondurulan_aylar"))
    out["atlanan_aylar"] = _json_list(out.get("atlanan_aylar"))
    return out


def _satirlar(rows) -> list[dict]:
    if rows is None:
        return []
    if isinstance(rows, dict):
        return [_satir(rows)]
    return [_satir(r) for r in rows]


def dondurma_liste(oku, *, musteri_id: int, iptaller: bool = False) -> list[dict]:
    """Ensure çağırmaz. Tablo yoksa çağıran yakalar. Kolon yoksa silindi filtresi yok."""
    sql = _silinmeyen(SQL_LISTE) if kolon_var_mi(oku, "sozlesme_plan_dondurma", "silindi_at") else SQL_LISTE
    return _satirlar(oku(sql, (int(musteri_id), bool(iptaller))))


def dondurma_acik(oku, *, musteri_id: int) -> list[dict]:
    """Ensure çağırmaz."""
    sql = _silinmeyen(SQL_ACIK) if kolon_var_mi(oku, "sozlesme_plan_dondurma", "silindi_at") else SQL_ACIK
    return _satirlar(oku(sql, (int(musteri_id),)))


def dondurma_ekle(
    calistir,
    oku,
    *,
    musteri_id: int,
    baslangic_ay,
    ay_sayisi: int,
    dondurulan_aylar,
    atlanan_aylar,
    olusturan: str,
    simdi: datetime | None = None,
) -> dict:
    """Açık aralık kesişirse DondurmaCakisma. Sözleşme bitişine dokunmaz."""
    ensure_sozlesme_plan_dondurma(calistir)
    mid = int(musteri_id)
    if mid <= 0:
        raise ValueError("musteri_id gerekli")
    bas = _ay_basi(baslangic_ay)
    n = int(ay_sayisi)
    ay_listesi(bas, n)
    kim = str(olusturan or "").strip()
    if not kim:
        raise ValueError("olusturan gerekli")
    if not dondurulan_aylar:
        raise ValueError("dondurulacak ay yok")
    for mevcut in dondurma_acik(oku, musteri_id=mid):
        if aralik_cakisir(bas, n, mevcut["baslangic_ay"], int(mevcut["ay_sayisi"])):
            raise DondurmaCakisma("acik dondurma araligi kesisiyor")
    zaman = simdi or datetime.now(timezone.utc)
    if zaman.tzinfo is None:
        zaman = zaman.replace(tzinfo=timezone.utc)
    row = calistir(
        SQL_INSERT,
        (
            mid,
            bas,
            n,
            json.dumps(list(dondurulan_aylar), ensure_ascii=False),
            json.dumps(list(atlanan_aylar or []), ensure_ascii=False),
            kim[:80],
            zaman,
        ),
    )
    if not row:
        raise RuntimeError("dondurma eklenemedi")
    return _satir(row)


def dondurma_iptal(calistir, oku, *, dondurma_id: int, iptal_eden: str, simdi: datetime | None = None) -> dict:
    """Açık satırı iptal eder. Satır silinmez."""
    ensure_sozlesme_plan_dondurma(calistir)
    pid = int(dondurma_id)
    mevcut = _satir(oku(SQL_BY_ID, (pid,)))
    if not mevcut or mevcut.get("iptal_at"):
        raise DondurmaYok("acik dondurma yok")
    kim = str(iptal_eden or "").strip()
    if not kim:
        raise ValueError("iptal_eden gerekli")
    zaman = simdi or datetime.now(timezone.utc)
    if zaman.tzinfo is None:
        zaman = zaman.replace(tzinfo=timezone.utc)
    row = calistir(SQL_IPTAL, (zaman, kim[:80], pid))
    if not row:
        raise DondurmaYok("acik dondurma yok")
    return _satir(row)


def dondurma_sil(calistir, oku, *, dondurma_id: int, silen: str, simdi: datetime | None = None) -> tuple[dict, bool]:
    """Kalıcı silmez. Açık satırı önce iptal eder, sonra geçmişten gizler."""
    ensure_sozlesme_plan_dondurma(calistir)
    ensure_dondurma_silindi_kolonlari(calistir)
    pid = int(dondurma_id)
    mevcut = _satir(oku(SQL_BY_ID_SIL, (pid,)))
    if not mevcut or mevcut.get("silindi_at"):
        raise DondurmaYok("dondurma yok")
    kim = str(silen or "").strip()
    if not kim:
        raise ValueError("silen gerekli")
    zaman = simdi or datetime.now(timezone.utc)
    if zaman.tzinfo is None:
        zaman = zaman.replace(tzinfo=timezone.utc)
    acikti = not mevcut.get("iptal_at")
    if acikti:
        dondurma_iptal(calistir, oku, dondurma_id=pid, iptal_eden=kim, simdi=zaman)
    row = calistir(SQL_SIL, (zaman, kim[:80], pid))
    if not row:
        raise DondurmaYok("dondurma yok")
    return _satir(row), acikti


def dondurma_imza_db(musteri_id) -> str:
    """Okuma. CREATE yok. Tablo yoksa 'none' (parmak izi sabit)."""
    try:
        mid = int(musteri_id)
    except (TypeError, ValueError):
        return "none"
    if mid <= 0:
        return "none"
    try:
        from db import fetch_all

        rows = fetch_all(SQL_ACIK, (mid,)) or []
    except Exception as exc:
        metin = str(exc).lower()
        if "sozlesme_plan_dondurma" in metin or "does not exist" in metin or "undefined" in metin:
            return "none"
        return "ERR"
    return dondurma_parmak(rows)
