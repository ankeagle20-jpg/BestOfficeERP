# -*- coding: utf-8 -*-
"""Ekstre: direct_pay id ay satırında ikinci kez alacak yazılmasın. Canlı yazma yok."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:240]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_filtre_birim():
    from routes import giris_routes as gr

    atla, keep = gr._ekstre_ay_satir_direct_pay_filtre([1657], {1657})
    check("tek id direct → atla", atla is True and keep == [])

    atla, keep = gr._ekstre_ay_satir_direct_pay_filtre([10, 1657], {1657})
    check("kismi filtre", atla is False and keep == [10])

    atla, keep = gr._ekstre_ay_satir_direct_pay_filtre([10], {1657})
    check("ilgisiz id", atla is False and keep == [10])

    atla, keep = gr._ekstre_ay_satir_direct_pay_filtre([], {1657})
    check("bos uniq", atla is False and keep == [])

    atla, keep = gr._ekstre_ay_satir_direct_pay_filtre([1657], set())
    check("bos direct set", atla is False and keep == [1657])

    # Marker ayı + boş uniq: grid/map çift sayım engeli (6008 → 2026-01..03)
    atla, keep = gr._ekstre_ay_satir_direct_pay_filtre(
        [],
        {6008},
        iso="2026-01-01",
        direct_marker_isos={"2026-01-01", "2026-02-01", "2026-03-01"},
    )
    check("bos uniq + marker ay → atla", atla is True and keep == [])

    atla, keep = gr._ekstre_ay_satir_direct_pay_filtre(
        [],
        {6008},
        iso="2026-04-01",
        direct_marker_isos={"2026-01-01"},
    )
    check("bos uniq + ilgisiz ay", atla is False and keep == [])

    isos = gr._ekstre_direct_pay_marker_isos(
        [
            {
                "id": 6008,
                "tahsilat_aciklama": (
                    "|AYLIK_TAH|2026-01-01| |AYLIK_PAY|2026-01-01=3163.00|"
                    " |AYLIK_TAH|2026-02-01| |AYLIK_PAY|2026-02-01=4398.00|"
                ),
            }
        ]
    )
    check(
        "marker_isos cikar",
        isos == {"2026-01-01", "2026-02-01"},
        str(isos),
    )


def test_kaynak_guard_mevcut():
    src = Path("routes/giris_routes.py").read_text(encoding="utf-8")
    check(
        "direct_pay_ids set",
        "direct_pay_ids: set[int] = set()" in src,
    )
    check(
        "filtre cagrisi",
        "_ekstre_ay_satir_direct_pay_filtre(" in src
        and "direct_marker_isos=direct_pay_marker_isos" in src,
    )


def test_326_hayalet_grid_regresyon():
    """Eski senaryo: grid Mart 848,58 + direct 3600 → alacak toplamı 3600."""
    from routes import giris_routes as gr
    from db import fetch_one

    mid = 326
    row = fetch_one(
        "SELECT id, ROUND(tutar::numeric,2) tutar FROM tahsilatlar WHERE id=1657"
    )
    check("tahsilat 1657 var", bool(row) and float(row["tutar"]) == 3600.0, str(row))

    bas, bit = gr._cari_ekstre_varsayilan_bas_bit(mid)
    rows = gr._cari_ekstre_hareketler(
        mid, bas, bit, 0, tahsilat_borca_hizala=True
    ) or []
    al = round(sum(float(h.get("alacak") or 0) for h in rows), 2)
    synth = [
        h
        for h in rows
        if abs(float(h.get("alacak") or 0) - 848.58) < 0.02
        and str(h.get("tarih") or "").startswith("2026-03-01")
    ]
    check("326 alacak=3600", abs(al - 3600.0) < 0.02, str(al))
    check("326 synth848 yok", len(synth) == 0, str(len(synth)))

    # id bazlı değişmez: satır alacakları (o id'ye yazılan) ≤ tutar
    by_tid: dict[int, float] = {}
    for h in rows:
        ids = h.get("tahsilat_ids") or []
        a = round(float(h.get("alacak") or 0), 2)
        if a <= 0.01 or not ids:
            continue
        # Tek id satırlarında tam tutar; çoklu id'de paylaştırma yok → her birine üst sınır kontrolü
        for raw in ids:
            try:
                tid = int(raw)
            except (TypeError, ValueError):
                continue
            if tid <= 0:
                continue
            by_tid[tid] = round(by_tid.get(tid, 0.0) + a, 2)

    t1657 = float(row["tutar"])
    check(
        "326 id1657 alacak<=tutar",
        by_tid.get(1657, 0.0) <= t1657 + 0.02,
        f"{by_tid.get(1657)} > {t1657}",
    )


def test_degismez_id_alacak_ust_sinir_ornek():
    """Avans/çoklu ay: her id için ekstre alacak toplamı ≤ tahsilat tutarı (sentetik)."""
    from routes import giris_routes as gr

    # Simüle: direct 3600 yazıldı; ay satırı aynı id ile 848,58 üretmeye çalışır → atlanır.
    direct_ids = {1657}
    atla, _ = gr._ekstre_ay_satir_direct_pay_filtre([1657], direct_ids)
    check("degismez atla", atla is True)

    # Çoklu ay dağılımı (farklı id'ler / fifo): atlama yok
    atla2, keep2 = gr._ekstre_ay_satir_direct_pay_filtre([9001, 9002], {1657})
    check("fifo idleri korunur", atla2 is False and keep2 == [9001, 9002])


if __name__ == "__main__":
    test_filtre_birim()
    test_kaynak_guard_mevcut()
    test_degismez_id_alacak_ust_sinir_ornek()
    test_326_hayalet_grid_regresyon()
    if FAILS:
        print("FAILS", FAILS)
        sys.exit(1)
    print("CASE ekstre direct_pay cift_sayim ok")
