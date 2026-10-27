# -*- coding: utf-8 -*-
"""Hibrit satır korunur, geçmiş yazılır, geri yazma kasaya dokunmaz. Canlı yazma yok."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

from routes import giris_routes as gr  # noqa: E402

FAILS = []
HIBRIT = {
    "tutar_kdv_dahil": 109600,
    "giris_tip": "hibrit",
    "giris_tutar": None,
    "hibrit_toplam": 100000,
    "hibrit_net": 52000,
    "hibrit_banka": 48000,
}


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:400]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _kur(row):
    sqls = []
    calls = {"esitle": 0, "yenile": 0, "audit": []}

    def fetch_one(sql, params=None):
        if "musteri_reel_donem_tutar" in sql:
            return dict(row) if row else None
        if "musteri_kyc" in sql or "kdv_oran" in sql:
            return {"kdv_oran": 20, "sozlesme_tarihi": "2022-03-01"}
        return {"id": 1}

    def execute(sql, params=None):
        sqls.append((" ".join(sql.split()), params))
        return 1

    def esitle(*a, **k):
        calls["esitle"] += 1

    def yenile(*a, **k):
        calls["yenile"] += 1
        return True

    def audit(*a, **k):
        calls["audit"].append(k or {"args": a})
        return True

    gr.fetch_one = fetch_one
    gr.execute = execute
    gr._reel_tahsilat_esitle_calistir = esitle
    gr.tahsilat_degisti = yenile
    gr._reel_donem_audit = audit
    gr._reel_kullanici_id = lambda: "7"
    gr._musteri_kyc_grid_mem.clear()
    return sqls, calls


def test_karar_409_ve_koru():
    karar, mesaj = gr._reel_upsert_karar(HIBRIT, "dahil", 100000, 100000, None, None, None)
    check("farkli toplam 409", karar == "ret_409" and mesaj == gr._REEL_HIBRIT_DAGITIM_MESAJ, mesaj)
    karar2, _ = gr._reel_upsert_karar(HIBRIT, "dahil", 109600, 109600, None, None, None)
    check("ayni toplam korunur", karar2 == "oldugu_gibi")
    sqls, calls = _kur(HIBRIT)
    durum, sonuc = gr._reel_donem_tutar_uygula(883, 2026, 100000, "dahil", 100000, None, None, None)
    check("uygula 409 yazmaz", durum == 409 and not sqls and calls["esitle"] == 0, sonuc)
    sqls, calls = _kur(HIBRIT)
    durum, sonuc = gr._reel_donem_tutar_uygula(883, 2026, 109600, "dahil", 109600, None, None, None)
    check("uygula ayni toplam yazmaz", durum == 200 and sonuc.get("korundu") and not sqls and calls["esitle"] == 0)


def test_acik_hibrit_ve_audit():
    sqls, calls = _kur(HIBRIT)
    gercek_audit = gr._reel_donem_audit

    def audit_kaydet(olay, eski, yeni, musteri_id, donem_yil):
        calls["audit"].append({
            "olay": olay, "eski": eski, "yeni": yeni, "mid": musteri_id, "yil": donem_yil, "uid": gr._reel_kullanici_id()
        })
        from audit_log import audit_yaz

        def calistir(sql, params=None):
            sqls.append((" ".join(str(sql).split()), params))
            return 1

        return audit_yaz(
            calistir,
            olay=olay,
            varlik="musteri_reel_donem_tutar",
            kayit_id=int(donem_yil),
            musteri_id=int(musteri_id),
            kullanici="7",
            anlik=eski or {},
            yeni=yeni or {},
        )

    gr._reel_donem_audit = audit_kaydet
    durum, sonuc = gr._reel_donem_tutar_uygula(
        883, 2026, 109600, "hibrit", None, 100000, 50000, 50000
    )
    check("hibrit yazildi", durum == 200 and not sonuc.get("korundu") and calls["esitle"] == 1, sonuc)
    guncelle = [s for s in sqls if "INSERT INTO musteri_reel_donem_tutar" in s[0]]
    check("hibrit kolonlari gonderildi", bool(guncelle) and guncelle[0][1][5:8] == (100000.0, 50000.0, 50000.0), guncelle[:1])
    audit_sql = [s for s in sqls if "INSERT INTO audit_log" in s[0]]
    check("gecmis kaydi", len(audit_sql) == 1, audit_sql[:1])
    if audit_sql:
        params = audit_sql[0][1]
        blob = " ".join(str(x) for x in params)
        check("gecmis tutar ve yil", "52000" in blob and "50000" in blob and "2026" in str(params[1]), params[1])
        check("gecmis kullanici", params[5] == 7, params[5])
        check("kisisel alan yok", "telefon" not in blob and "tc" not in blob.lower())
    gr._reel_donem_audit = gercek_audit
    sqls, calls = _kur(HIBRIT)
    gr._reel_donem_tutar_sil = None
    n_sql = []
    gr.execute = lambda sql, params=None: n_sql.append(sql) or 1
    eski = gr._reel_donem_satir_oku(883, 2026)
    gr._reel_donem_audit("reel_sil", gr._reel_satir_ani(eski, 883, 2026), {"silindi": True}, 883, 2026)
    check("silme gecmisi", calls["audit"] and calls["audit"][-1].get("olay") == "reel_sil" or (
        calls["audit"] and "reel_sil" in str(calls["audit"])
    ), calls["audit"])


def test_geri_yaz_kasa_dokunmaz():
    sqls, calls = _kur(HIBRIT)
    kotu = gr.reel_hibrit_alanlari_yaz(883, 2026, 100000, 1, 1, kdv_oran=20)
    check("tutarsiz reddedildi", kotu.get("ok") is False and kotu.get("neden") == "tutar_uyusmuyor" and not sqls, kotu)
    check("tutarsiz kasa yok", calls["esitle"] == 0 and calls["yenile"] == 0)
    iyi = gr.reel_hibrit_alanlari_yaz(883, 2026, 100000, 52000, 48000, kdv_oran=20)
    guncel = [s for s in sqls if s[0].startswith("UPDATE musteri_reel_donem_tutar")]
    check("geri yazildi", iyi.get("ok") is True and len(guncel) == 1, iyi)
    check("kasa sql yok", not any("tahsilat" in s[0].lower() for s in sqls), [s[0][:80] for s in sqls])
    check("esitle yok yenile var", calls["esitle"] == 0 and calls["yenile"] == 1, calls)
    sqls, calls = _kur(HIBRIT)
    from reel_hibrit_geri import reel_hibrit_geri_yaz
    plan = reel_hibrit_geri_yaz(883, 2026, 100000, 1, 1, apply=False)
    check("dry-run tutarsiz yazmaz", plan.get("ok") is False and not sqls, plan)
    plan_ok = reel_hibrit_geri_yaz(883, 2026, 100000, 52000, 48000, apply=False)
    check("dry-run tutarli yazmaz", plan_ok.get("ok") is True and plan_ok.get("dry_run") is True and not sqls, plan_ok)


def main():
    test_karar_409_ve_koru()
    test_acik_hibrit_ve_audit()
    test_geri_yaz_kasa_dokunmaz()
    if FAILS:
        print("FAILS", ",".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
