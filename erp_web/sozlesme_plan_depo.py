# -*- coding: utf-8 -*-
"""sozlesme_plan_degisiklik tablo ensure ve ekle/iptal.

Bu modül db.execute çağırmaz ve uygulama açılışına bağlı değildir.
calistir/oku, isteğin o anki search_path'inde çalışan bir yürütücüdür.
Canlı şemada kendiliğinden çalışmaz; bir sonraki aşama bunu
musteri_reel_donem_tutar ensure'i gibi ilk kullanımda çağırabilir.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

_SEMA_RE = re.compile(r"^(public|tenant_[a-z0-9_]+)$")

SQL_TABLO = """
CREATE TABLE IF NOT EXISTS sozlesme_plan_degisiklik (
    id SERIAL PRIMARY KEY,
    musteri_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    gecerlilik_ay DATE NOT NULL,
    yeni_net NUMERIC(14, 2) NOT NULL,
    kdv_oran NUMERIC(8, 2) NOT NULL DEFAULT 20,
    yeni_brut NUMERIC(14, 2) NOT NULL,
    nakit_tutar NUMERIC(14, 2),
    banka_tutar NUMERIC(14, 2),
    olusturan TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    iptal_at TIMESTAMPTZ,
    iptal_eden TEXT,
    CONSTRAINT sozlesme_plan_gecerlilik_ay_basi CHECK (EXTRACT(DAY FROM gecerlilik_ay) = 1)
)
""".strip()

SQL_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_sozlesme_plan_degisiklik_acik
ON sozlesme_plan_degisiklik (musteri_id, gecerlilik_ay)
WHERE iptal_at IS NULL
""".strip()

SQL_AKTIF = """
SELECT id, musteri_id, gecerlilik_ay, yeni_net, kdv_oran, yeni_brut,
       nakit_tutar, banka_tutar, olusturan, created_at, iptal_at, iptal_eden
FROM sozlesme_plan_degisiklik
WHERE musteri_id = %s AND gecerlilik_ay = %s AND iptal_at IS NULL
""".strip()

SQL_BY_ID = """
SELECT id, musteri_id, gecerlilik_ay, yeni_net, kdv_oran, yeni_brut,
       nakit_tutar, banka_tutar, olusturan, created_at, iptal_at, iptal_eden
FROM sozlesme_plan_degisiklik
WHERE id = %s
""".strip()

SQL_LISTE = """
SELECT id, musteri_id, gecerlilik_ay, yeni_net, kdv_oran, yeni_brut,
       nakit_tutar, banka_tutar, olusturan, created_at, iptal_at, iptal_eden
FROM sozlesme_plan_degisiklik
WHERE musteri_id = %s AND (%s OR iptal_at IS NULL)
ORDER BY gecerlilik_ay, id
""".strip()

SQL_INSERT = """
INSERT INTO sozlesme_plan_degisiklik (
    musteri_id, gecerlilik_ay, yeni_net, kdv_oran, yeni_brut,
    nakit_tutar, banka_tutar, olusturan, created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id, musteri_id, gecerlilik_ay, yeni_net, kdv_oran, yeni_brut,
          nakit_tutar, banka_tutar, olusturan, created_at, iptal_at, iptal_eden
""".strip()

SQL_IPTAL = """
UPDATE sozlesme_plan_degisiklik
SET iptal_at = %s, iptal_eden = %s
WHERE id = %s AND iptal_at IS NULL
RETURNING id, musteri_id, gecerlilik_ay, yeni_net, kdv_oran, yeni_brut,
          nakit_tutar, banka_tutar, olusturan, created_at, iptal_at, iptal_eden
""".strip()


class PlanCakisma(Exception):
    """Aynı müşteri ve geçerlilik ayında iptalsiz kayıt var."""


class PlanYok(Exception):
    """İptal edilecek açık plan yok."""


def denetim_kaydi(*, olay: str, musteri_id: int, gecerlilik_ay: date, kim: str, zaman: datetime) -> dict:
    """Ekleme veya iptalin kim/ne zaman kaydı. Ayrı tablo açmaz; satır alanına yazılır."""
    if olay not in ("ekle", "iptal"):
        raise ValueError("olay ekle veya iptal olmali")
    kim_s = str(kim or "").strip()
    if not kim_s:
        raise ValueError("kim gerekli")
    if zaman.tzinfo is None:
        zaman = zaman.replace(tzinfo=timezone.utc)
    return {
        "olay": olay,
        "musteri_id": int(musteri_id),
        "gecerlilik_ay": gecerlilik_ay,
        "kim": kim_s,
        "zaman": zaman,
    }


def ensure_sozlesme_plan_degisiklik(calistir) -> None:
    """Mevcut search_path içinde IF NOT EXISTS. İkinci çağrı aynı SQL'i yineler."""
    calistir(SQL_TABLO)
    calistir(SQL_INDEX)


def ensure_sozlesme_plan_degisiklik_semada(calistir, sema: str) -> None:
    """Tek şema. Ad public veya tenant_* olmalı. Bu aşamada uygulama çağırmaz."""
    ad = str(sema or "").strip()
    if not _SEMA_RE.fullmatch(ad):
        raise ValueError("sema adi gecersiz")
    calistir(f"SET LOCAL search_path TO {ad}, pg_catalog")
    ensure_sozlesme_plan_degisiklik(calistir)


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


def plan_ekle(
    calistir,
    oku,
    *,
    musteri_id: int,
    gecerlilik_ay,
    yeni_net,
    kdv_oran,
    yeni_brut,
    nakit_tutar=None,
    banka_tutar=None,
    olusturan: str,
    simdi: datetime | None = None,
) -> dict:
    """Açık plan ekler. customers.durum, aylik_kira, reel ve cache yazılmaz."""
    ensure_sozlesme_plan_degisiklik(calistir)
    ay = _ay_basi(gecerlilik_ay)
    mid = int(musteri_id)
    if mid <= 0:
        raise ValueError("musteri_id gerekli")
    net = round(float(yeni_net), 2)
    brut = round(float(yeni_brut), 2)
    kdv = round(float(kdv_oran), 2)
    if net <= 0 or brut <= 0:
        raise ValueError("tutar sifirdan buyuk olmali")
    zaman = simdi or datetime.now(timezone.utc)
    kayit = denetim_kaydi(olay="ekle", musteri_id=mid, gecerlilik_ay=ay, kim=olusturan, zaman=zaman)
    var = oku(SQL_AKTIF, (mid, ay))
    if var:
        raise PlanCakisma("bu gecerlilik ayinda acik plan var")
    row = calistir(
        SQL_INSERT,
        (
            mid,
            ay,
            net,
            kdv,
            brut,
            None if nakit_tutar in (None, "") else round(float(nakit_tutar), 2),
            None if banka_tutar in (None, "") else round(float(banka_tutar), 2),
            kayit["kim"],
            kayit["zaman"],
        ),
    )
    if not row:
        raise RuntimeError("plan eklenemedi")
    return dict(row)


def plan_degistir(
    calistir,
    oku,
    *,
    musteri_id: int,
    gecerlilik_ay,
    yeni_net,
    kdv_oran,
    yeni_brut,
    nakit_tutar=None,
    banka_tutar=None,
    olusturan: str,
    simdi: datetime | None = None,
) -> tuple[dict, dict | None]:
    """Aynı ayda açık plan varsa iptal eder (satır silinmez; iptal_at/iptal_eden dolar), ardından yenisini yazar.

    Dönen: (yeni_satir, iptal_edilen_satir veya None). Transaction'ı çağıran yönetir:
    herhangi bir adım hata verirse çağıran rollback yapar ve eski plan açık kalır.
    plan_ekle ve PlanCakisma davranışı değişmez; eşzamanlı yazımda unique index PlanCakisma/unique hatası verir.
    """
    ensure_sozlesme_plan_degisiklik(calistir)
    ay = _ay_basi(gecerlilik_ay)
    mid = int(musteri_id)
    zaman = simdi or datetime.now(timezone.utc)
    onceki = None
    var = oku(SQL_AKTIF, (mid, ay))
    if var:
        try:
            onceki = plan_iptal(calistir, oku, plan_id=int(var["id"]), iptal_eden=olusturan, simdi=zaman)
        except PlanYok as exc:
            # Başka bir istek aynı satırı bu arada iptal etti/değiştirdi: yarışı çakışma say.
            raise PlanCakisma("bu gecerlilik ayinda acik plan baska istekle degisti") from exc
    yeni = plan_ekle(
        calistir,
        oku,
        musteri_id=mid,
        gecerlilik_ay=ay,
        yeni_net=yeni_net,
        kdv_oran=kdv_oran,
        yeni_brut=yeni_brut,
        nakit_tutar=nakit_tutar,
        banka_tutar=banka_tutar,
        olusturan=olusturan,
        simdi=zaman,
    )
    return yeni, onceki


def plan_iptal(calistir, oku, *, plan_id: int, iptal_eden: str, simdi: datetime | None = None) -> dict:
    """Açık satırı iptal eder. Satır silinmez; iptal_at ve iptal_eden dolar."""
    ensure_sozlesme_plan_degisiklik(calistir)
    pid = int(plan_id)
    mevcut = oku(SQL_BY_ID, (pid,))
    if not mevcut or mevcut.get("iptal_at"):
        raise PlanYok("acik plan yok")
    zaman = simdi or datetime.now(timezone.utc)
    kayit = denetim_kaydi(
        olay="iptal",
        musteri_id=int(mevcut["musteri_id"]),
        gecerlilik_ay=mevcut["gecerlilik_ay"],
        kim=iptal_eden,
        zaman=zaman,
    )
    row = calistir(SQL_IPTAL, (kayit["zaman"], kayit["kim"], pid))
    if not row:
        raise PlanYok("acik plan yok")
    return dict(row)


def plan_liste(oku, *, musteri_id: int, iptaller: bool = False) -> list[dict]:
    """Varsayılan yalnız iptalsiz satırlar."""
    rows = oku(SQL_LISTE, (int(musteri_id), bool(iptaller)))
    if rows is None:
        return []
    if isinstance(rows, dict):
        return [dict(rows)]
    return [dict(r) for r in rows]
