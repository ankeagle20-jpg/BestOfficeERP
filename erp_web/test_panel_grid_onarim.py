# -*- coding: utf-8 -*-
"""Commit F: panel_grid_onarim dry-run / yazma kapısı / yedek geri."""
import json
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import panel_grid_onarim as onr  # noqa: E402

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_dry_run_varsayilan_yazmaz():
    os.environ.pop("ALLOW_PROD_WRITE", None)
    with mock.patch.object(onr, "fetch_one", side_effect=[
        {"by_iso": json.dumps({"2025-01-01": {"tahsil": 100, "aylik": 100, "kalan": 0}}), "updated_at": None},
        {"payload": json.dumps({"aylar": [{"yil": 2025, "ay": 1}]}), "updated_at": None},
    ]), mock.patch.object(
        onr, "db"
    ) as mdb:
        # dry path should not enter db() write
        from routes import giris_routes as gr
        with mock.patch.object(gr, "_read_aylik_grid_cache_payload", return_value={"aylar": [{"yil": 2025, "ay": 1, "tutar_kdv_dahil": 100}]}), mock.patch.object(
            gr, "_build_aylik_grid_cache_payload", return_value={"aylar": [{"yil": 2025, "ay": 1, "tutar_kdv_dahil": 100}]}
        ), mock.patch.object(
            gr, "_panel_by_iso_from_tahsil_map", return_value={"2025-01-01": {"tahsil": 100, "aylik": 100, "kalan": 0}}
        ), mock.patch.object(
            gr, "_aylik_tahsil_tutar_map", return_value={"2025-01-01": 100.0}
        ), mock.patch.object(
            gr, "_apply_panel_by_iso_to_grid_payload", return_value=None
        ):
            r = onr.onar_mid(867, dry_run=True, etiket="t")
    check("dry yazilmadi", r.get("yazildi") is False, r)
    check("dry panel sonra", r.get("panel_ay_sonra") == 1, r)
    check("db yazma yok", mdb.call_count == 0, mdb.call_count)


def test_apply_without_allow_yazmaz():
    os.environ.pop("ALLOW_PROD_WRITE", None)
    with mock.patch.object(onr, "fetch_one", return_value=None), mock.patch.object(onr, "db") as mdb:
        from routes import giris_routes as gr
        with mock.patch.object(gr, "_read_aylik_grid_cache_payload", return_value={}), mock.patch.object(
            gr, "_build_aylik_grid_cache_payload", return_value={"aylar": []}
        ), mock.patch.object(
            gr, "_panel_by_iso_from_tahsil_map", return_value={}
        ), mock.patch.object(
            gr, "_aylik_tahsil_tutar_map", return_value={}
        ), mock.patch.object(
            gr, "_apply_panel_by_iso_to_grid_payload", return_value=None
        ):
            r = onr.onar_mid(867, dry_run=False, etiket="t")
    check("allow yok yazilmadi", r.get("yazildi") is False, r)
    check("allow yok db yok", mdb.call_count == 0, mdb.call_count)


def test_geri_allow_yok():
    os.environ.pop("ALLOW_PROD_WRITE", None)
    r = onr.geri_yukle(867, "x")
    check("geri reddedildi", r.get("ok") is False and "ALLOW" in str(r.get("neden")), r)


def test_kaynak_tahsilat_dokunmaz():
    src = Path(onr.__file__).read_text(encoding="utf-8")
    for yasak in ("INSERT INTO tahsilatlar", "UPDATE tahsilatlar", "DELETE FROM tahsilatlar", "INSERT INTO faturalar", "UPDATE faturalar"):
        check("yok " + yasak, yasak not in src)


def test_dry_run_cli():
    from routes import giris_routes as gr
    with mock.patch.object(onr, "onar_mid", return_value={
        "mid": 867, "dry_run": True, "panel_ay_once": 2, "grid_ay_once": 3,
        "yazildi": False, "panel_ay_sonra": 2, "grid_ay_sonra": 3,
    }) as m:
        rc = onr.main(["--mids", "867", "--dry-run"])
    check("cli rc0", rc == 0)
    check("cli dry", m.call_args.kwargs.get("dry_run") is True or m.call_args[1].get("dry_run") is True, m.call_args)


def test_cap_402_map_ustunde_tavan():
    """map 1540 / kira 881 → panele 881 yazılır."""
    by = {
        "2026-08-01": {"aylik": 881.0, "tahsil": 1540.18, "kalan": 0.0, "tahsil_tarih": "2026-09-08"},
        "2026-09-01": {"aylik": 881.0, "tahsil": 1540.18, "kalan": 0.0, "tahsil_tarih": "2026-09-08"},
    }
    out = onr._cap_panel_by_iso_for_onarim(by, dondurma_aylar=set())
    check("402 agu tavan", out["2026-08-01"]["tahsil"] == 881.0, out["2026-08-01"])
    check("402 eyl tavan", out["2026-09-01"]["tahsil"] == 881.0, out["2026-09-01"])
    check("402 kalan 0", out["2026-08-01"]["kalan"] == 0.0)


def test_cap_111_panel_sifir_tahsilat_var():
    """panel 0 iken map/tahsil brüt altındaysa doğru dolar."""
    by = {
        "2025-06-01": {"aylik": 2500.0, "tahsil": 0.0, "kalan": 2500.0},
        "2025-07-01": {"aylik": 2500.0, "tahsil": 1200.5, "kalan": 1299.5},
    }
    # onarım panel_from_map sonrası tahsil zaten map'ten gelir
    by2 = {
        "2025-06-01": {"aylik": 2500.0, "tahsil": 2500.0, "kalan": 0.0},
        "2025-07-01": {"aylik": 2500.0, "tahsil": 1200.5, "kalan": 1299.5},
    }
    out = onr._cap_panel_by_iso_for_onarim(by2, dondurma_aylar=set())
    check("111 tam ay", out["2025-06-01"]["tahsil"] == 2500.0, out)
    check("111 kismi", out["2025-07-01"]["tahsil"] == 1200.5, out)
    check("111 kalan", out["2025-07-01"]["kalan"] == 1299.5, out)


def test_cap_kira_sifir_yazilmaz():
    by = {
        "2026-01-01": {"aylik": 0.0, "tahsil": 500.0, "kalan": 0.0},
        "2026-02-01": {"aylik": 800.0, "tahsil": 800.0, "kalan": 0.0},
    }
    out = onr._cap_panel_by_iso_for_onarim(by, dondurma_aylar={"2026-03"})
    check("kira0 yok", "2026-01-01" not in out, out)
    check("kira var", out.get("2026-02-01", {}).get("tahsil") == 800.0, out)
    by_don = {
        "2026-03-01": {"aylik": 800.0, "tahsil": 800.0, "kalan": 0.0},
        "2026-04-01": {"aylik": 800.0, "tahsil": 900.0, "kalan": 0.0},
    }
    out2 = onr._cap_panel_by_iso_for_onarim(by_don, dondurma_aylar={"2026-03"})
    check("dondurma yok", "2026-03-01" not in out2, out2)
    check("dondurma sonrasi tavan", out2["2026-04-01"]["tahsil"] == 800.0, out2)


def test_geri_cap_kullanmaz():
    src = Path(onr.__file__).read_text(encoding="utf-8")
    i = src.find("def geri_yukle")
    j = src.find("\ndef main", i)
    body = src[i:j]
    check("geri cap yok", "_cap_panel_by_iso_for_onarim" not in body, body[:80])


def test_sync_bos_panel_grid_kalinti_sifir():
    """Boş panel: brut≈0.01 / odenen kalıntısı map=0 → odenen=0, te=False."""
    payload = {
        "aylar": [
            {
                "yil": 2026,
                "ay": 6,
                "brut_tutar_kdv": 0.01,
                "odenen_tutar_kdv": 0.01,
                "kalan_tutar_kdv": 0.0,
                "tahsil_edildi": True,
                "kismi_tahsilat": False,
            },
            {
                "yil": 2026,
                "ay": 7,
                "brut_tutar_kdv": 1200.0,
                "odenen_tutar_kdv": 1200.0,
                "kalan_tutar_kdv": 0.0,
                "tahsil_edildi": True,
                "kismi_tahsilat": False,
            },
        ]
    }
    onr._sync_grid_payload_after_onarim(payload, {}, {})
    a0, a1 = payload["aylar"][0], payload["aylar"][1]
    check("0.01 odenen sifir", a0.get("odenen_tutar_kdv") == 0.0, a0)
    check("0.01 te false", a0.get("tahsil_edildi") is False, a0)
    check("brut ay hayalet sifir", a1.get("odenen_tutar_kdv") == 0.0, a1)
    check("brut ay te false", a1.get("tahsil_edildi") is False, a1)
    check("brut ay kalan", a1.get("kalan_tutar_kdv") == 1200.0, a1)


def test_dry_bos_panel_grid_ay_sonra_net():
    """onar_mid dry: panel boşalsa bile grid_ay_sonra dolu (eskiden None)."""
    os.environ.pop("ALLOW_PROD_WRITE", None)
    grid_aylar = [
        {
            "yil": 2026,
            "ay": 6,
            "brut_tutar_kdv": 0.01,
            "tutar_kdv_dahil": 0.01,
            "odenen_tutar_kdv": 0.01,
            "tahsil_edildi": True,
        }
    ]
    payload = {"aylar": [dict(grid_aylar[0])], "musteri_id": 2}
    synced = {}
    real_sync = onr._sync_grid_payload_after_onarim

    def _wrap_sync(pl, by, tmap, **kw):
        real_sync(pl, by, tmap, **kw)
        synced["aylar"] = json.loads(json.dumps(pl.get("aylar") or []))
        synced["by_len"] = len(by or {})

    with mock.patch.object(
        onr,
        "fetch_one",
        side_effect=[
            {
                "by_iso": json.dumps(
                    {"2026-06-01": {"tahsil": 1500, "aylik": 0.01, "kalan": 0}}
                ),
                "updated_at": None,
            },
            {"payload": json.dumps(payload), "updated_at": None},
        ],
    ), mock.patch.object(onr, "db") as mdb:
        from routes import giris_routes as gr

        with mock.patch.object(
            gr, "_read_aylik_grid_cache_payload", return_value=payload
        ), mock.patch.object(
            gr, "_build_aylik_grid_cache_payload", return_value=payload
        ), mock.patch.object(
            gr, "_panel_by_iso_from_tahsil_map", return_value={}
        ), mock.patch.object(
            gr, "_aylik_tahsil_tutar_map", return_value={}
        ), mock.patch.object(
            onr, "_sync_grid_payload_after_onarim", side_effect=_wrap_sync
        ):
            r = onr.onar_mid(2, dry_run=True, etiket="t_bos")
    check("bos panel sonra 0", r.get("panel_ay_sonra") == 0, r)
    check("grid ay sonra net", r.get("grid_ay_sonra") == 1, r)
    check("yazilmadi", r.get("yazildi") is False, r)
    check("db yok", mdb.call_count == 0, mdb.call_count)
    check("sync by bos", synced.get("by_len") == 0, synced)
    a0 = (synced.get("aylar") or [{}])[0]
    check("kalinti sifir", a0.get("odenen_tutar_kdv") == 0.0 and a0.get("tahsil_edildi") is False, a0)


def test_geri_hala_cap_siz():
    """--geri yolu değişmedi; ALLOW yokken reddeder (regresyon)."""
    os.environ.pop("ALLOW_PROD_WRITE", None)
    r = onr.geri_yukle(2, "onarim_dryrun_20261016T140000Z")
    check("geri hala allow ister", r.get("ok") is False, r)


def test_brut_cokus_yasak_326():
    """Cache dolu → build 0.01: çöküş ayları yakalanır."""
    once = {
        "aylar": [
            {"yil": 2026, "ay": 3, "tutar_kdv_dahil": 848.58, "brut_tutar_kdv": 848.58},
            {"yil": 2024, "ay": 1, "tutar_kdv_dahil": 420.0, "brut_tutar_kdv": 420.0},
        ]
    }
    build = {
        "aylar": [
            {"yil": 2026, "ay": 3, "tutar_kdv_dahil": 0.01, "brut_tutar_kdv": 0.01},
            {"yil": 2024, "ay": 1, "tutar_kdv_dahil": 0.01, "brut_tutar_kdv": 0.01},
        ]
    }
    bad = onr._brut_cokus_yasak(once, build)
    check("326 cokus 2 ay", set(bad) == {"2026-03", "2024-01"}, bad)


def test_326_apply_brut_cokus_yazmaz():
    """Apply: plan brüt çöküşü → yazmaz, net hata."""
    os.environ["ALLOW_PROD_WRITE"] = "1"
    once_pl = {
        "aylar": [
            {
                "yil": 2026,
                "ay": 3,
                "tutar_kdv_dahil": 848.58,
                "brut_tutar_kdv": 848.58,
                "odenen_tutar_kdv": 848.58,
            }
        ]
    }
    bad_pl = {
        "aylar": [
            {
                "yil": 2026,
                "ay": 3,
                "tutar_kdv_dahil": 0.01,
                "brut_tutar_kdv": 0.01,
                "odenen_tutar_kdv": 0.0,
            }
        ]
    }
    fake_plan = {
        "mid": 326,
        "kaynak": "build",
        "panel_once": {"by_iso": "{}", "updated_at": None},
        "grid_once": {"payload": json.dumps(once_pl), "updated_at": None},
        "panel_once_by": {},
        "payload_once": once_pl,
        "by_iso": {},
        "payload": bad_pl,
        "degisiklikler": [{"ay": "2026-03", "alan": "grid_brut", "eski": 848.58, "yeni": 0.01}],
        "fingerprint": "x",
        "kira_once": 848.58,
        "kira_sonra": 0.0,
        "brut_cokus_aylar": ["2026-03"],
    }
    with mock.patch.object(onr, "_onarim_hesapla", return_value=fake_plan), mock.patch.object(
        onr, "db"
    ) as mdb:
        r = onr.onar_mid(326, dry_run=False, etiket="t326")
    os.environ.pop("ALLOW_PROD_WRITE", None)
    check("326 yazilmadi", r.get("yazildi") is False, r)
    check("326 ok false", r.get("ok") is False, r)
    check("326 mesaj", "hiçbir değişiklik kaydedilmedi" in str(r.get("mesaj") or ""), r)
    check("326 db yok", mdb.call_count == 0, mdb.call_count)


def test_plan_uyusmazligi_yazmaz():
    """İkinci hesap fingerprint farklı → yazmadan iptal."""
    os.environ["ALLOW_PROD_WRITE"] = "1"
    pl = {
        "aylar": [
            {
                "yil": 2025,
                "ay": 1,
                "tutar_kdv_dahil": 100.0,
                "brut_tutar_kdv": 100.0,
                "odenen_tutar_kdv": 100.0,
            }
        ]
    }
    by = {"2025-01-01": {"aylik": 100.0, "tahsil": 100.0, "kalan": 0.0}}
    base = {
        "mid": 148,
        "kaynak": "cache",
        "panel_once": {"by_iso": json.dumps(by), "updated_at": None},
        "grid_once": {"payload": json.dumps(pl), "updated_at": None},
        "panel_once_by": by,
        "payload_once": pl,
        "by_iso": by,
        "payload": pl,
        "degisiklikler": [],
        "fingerprint": "fp-a",
        "kira_once": 100.0,
        "kira_sonra": 100.0,
        "brut_cokus_aylar": [],
    }
    alt = dict(base)
    alt["fingerprint"] = "fp-b"
    with mock.patch.object(
        onr, "_onarim_hesapla", side_effect=[base, alt]
    ), mock.patch.object(onr, "db") as mdb:
        r = onr.onar_mid(148, dry_run=False, etiket="tplan")
    os.environ.pop("ALLOW_PROD_WRITE", None)
    check("plan yazilmadi", r.get("yazildi") is False, r)
    check("plan neden", r.get("neden") == "plan_uyusmuyor", r)
    check("plan db yok", mdb.call_count == 0, mdb.call_count)


def test_normal_apply_yazar_audit():
    """148 kalıbı: brüt korunur, apply yazar + audit."""
    os.environ["ALLOW_PROD_WRITE"] = "1"
    pl = {
        "aylar": [
            {
                "yil": 2025,
                "ay": 6,
                "tutar_kdv_dahil": 1200.0,
                "brut_tutar_kdv": 1200.0,
                "odenen_tutar_kdv": 0.0,
                "kalan_tutar_kdv": 1200.0,
                "tahsil_edildi": False,
            }
        ]
    }
    by = {"2025-06-01": {"aylik": 1200.0, "tahsil": 500.0, "kalan": 700.0}}
    pl2 = json.loads(json.dumps(pl))
    pl2["aylar"][0]["odenen_tutar_kdv"] = 500.0
    pl2["aylar"][0]["kalan_tutar_kdv"] = 700.0
    pl2["aylar"][0]["kismi_tahsilat"] = True
    fp = onr._plan_fingerprint(by, pl2)
    plan = {
        "mid": 148,
        "kaynak": "cache",
        "panel_once": {"by_iso": "{}", "updated_at": None},
        "grid_once": {"payload": json.dumps(pl), "updated_at": None},
        "panel_once_by": {},
        "payload_once": pl,
        "by_iso": by,
        "payload": pl2,
        "degisiklikler": [{"ay": "2025-06", "alan": "grid_odenen", "eski": 0, "yeni": 500}],
        "fingerprint": fp,
        "kira_once": 1200.0,
        "kira_sonra": 1200.0,
        "brut_cokus_aylar": [],
    }

    class _Cur:
        def __init__(self):
            self.ops = []
            self._payload = json.dumps(pl2)

        def execute(self, sql, params=None):
            self.ops.append(str(sql)[:80])
            self._last = (sql, params)

        def fetchone(self):
            if "musteri_aylik_grid_cache" in str(self._last[0]) and "SELECT" in str(self._last[0]).upper():
                return (self._payload,)
            return None

    class _CM:
        def __enter__(self):
            self.cur = _Cur()
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return self.cur

    audits = []
    cm = _CM()
    with mock.patch.object(onr, "_onarim_hesapla", return_value=plan), mock.patch.object(
        onr, "db", return_value=cm
    ), mock.patch.object(onr, "_ensure_yedek_tables"), mock.patch.object(
        onr, "_ekstre_borc_toplam", return_value=5000.0
    ), mock.patch(
        "audit_log.audit_yaz", side_effect=lambda *a, **k: audits.append(k) or True
    ):
        from routes import giris_routes as gr

        with mock.patch.object(gr, "_invalidate_aylik_grid_payload_mem"):
            r = onr.onar_mid(148, dry_run=False, etiket="t148")
    os.environ.pop("ALLOW_PROD_WRITE", None)
    check("normal yazildi", r.get("yazildi") is True, r)
    check("normal ok", r.get("ok") is True, r)
    check("audit cagrildi", len(audits) >= 1, audits)
    if audits:
        check("audit olay", audits[0].get("olay") == "panel_grid_onarim", audits[0])


def test_kira_txn_rollback():
    """Txn içinde kira düşerse yazıldı=False (rollback yolu)."""
    os.environ["ALLOW_PROD_WRITE"] = "1"
    pl = {
        "aylar": [
            {
                "yil": 2025,
                "ay": 1,
                "tutar_kdv_dahil": 100.0,
                "brut_tutar_kdv": 100.0,
                "odenen_tutar_kdv": 50.0,
            }
        ]
    }
    by = {"2025-01-01": {"aylik": 100.0, "tahsil": 50.0, "kalan": 50.0}}
    fp = onr._plan_fingerprint(by, pl)
    plan = {
        "mid": 119,
        "kaynak": "cache",
        "panel_once": {"by_iso": json.dumps(by), "updated_at": None},
        "grid_once": {"payload": json.dumps(pl), "updated_at": None},
        "panel_once_by": by,
        "payload_once": pl,
        "by_iso": by,
        "payload": pl,
        "degisiklikler": [],
        "fingerprint": fp,
        "kira_once": 100.0,
        "kira_sonra": 100.0,
        "brut_cokus_aylar": [],
    }
    bad_written = json.dumps(
        {
            "aylar": [
                {
                    "yil": 2025,
                    "ay": 1,
                    "tutar_kdv_dahil": 0.01,
                    "brut_tutar_kdv": 0.01,
                    "odenen_tutar_kdv": 0,
                }
            ]
        }
    )

    class _Cur:
        def execute(self, sql, params=None):
            self._sql = sql

        def fetchone(self):
            return (bad_written,)

    class _CM:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _Cur()

    with mock.patch.object(onr, "_onarim_hesapla", return_value=plan), mock.patch.object(
        onr, "db", return_value=_CM()
    ), mock.patch.object(onr, "_ensure_yedek_tables"), mock.patch.object(
        onr, "_ekstre_borc_toplam", return_value=100.0
    ):
        r = onr.onar_mid(119, dry_run=False, etiket="t119")
    os.environ.pop("ALLOW_PROD_WRITE", None)
    check("kira rollback yazilmaz", r.get("yazildi") is False, r)
    check("kira rollback mesaj", "kaydedilmedi" in str(r.get("mesaj") or ""), r)


def test_tek_yol_cache_oncelik():
    """Dry ve hesap: cache varken build çağrılmaz (invalidate yok)."""
    os.environ.pop("ALLOW_PROD_WRITE", None)
    cache_pl = {
        "aylar": [
            {
                "yil": 2025,
                "ay": 1,
                "tutar_kdv_dahil": 500.0,
                "brut_tutar_kdv": 500.0,
                "odenen_tutar_kdv": 0,
            }
        ]
    }
    builds = []
    with mock.patch.object(
        onr,
        "fetch_one",
        side_effect=[
            {"by_iso": "{}", "updated_at": None},
            {"payload": json.dumps(cache_pl), "updated_at": None},
        ],
    ):
        from routes import giris_routes as gr

        with mock.patch.object(
            gr, "_read_aylik_grid_cache_payload", return_value=cache_pl
        ), mock.patch.object(
            gr,
            "_build_aylik_grid_cache_payload",
            side_effect=lambda *a, **k: builds.append(1) or cache_pl,
        ), mock.patch.object(
            gr, "_panel_by_iso_from_tahsil_map", return_value={}
        ), mock.patch.object(
            gr, "_aylik_tahsil_tutar_map", return_value={}
        ), mock.patch.object(
            gr, "_dondurma_ay_kumesi", return_value=set()
        ):
            plan = onr._onarim_hesapla(2, gr=gr)
    check("kaynak cache", plan.get("kaynak") == "cache", plan)
    check("build cagrilmaz", len(builds) == 0, builds)


def test_idempotent_fingerprint():
    pl = {
        "aylar": [
            {
                "yil": 2025,
                "ay": 1,
                "tutar_kdv_dahil": 10.0,
                "brut_tutar_kdv": 10.0,
                "odenen_tutar_kdv": 10.0,
            }
        ]
    }
    by = {"2025-01-01": {"tahsil": 10.0, "aylik": 10.0, "kalan": 0}}
    a = onr._plan_fingerprint(by, pl)
    b = onr._plan_fingerprint(by, pl)
    check("idempotent fp", a == b, a)


if __name__ == "__main__":
    for fn in (
        test_dry_run_varsayilan_yazmaz,
        test_apply_without_allow_yazmaz,
        test_geri_allow_yok,
        test_kaynak_tahsilat_dokunmaz,
        test_dry_run_cli,
        test_cap_402_map_ustunde_tavan,
        test_cap_111_panel_sifir_tahsilat_var,
        test_cap_kira_sifir_yazilmaz,
        test_geri_cap_kullanmaz,
        test_sync_bos_panel_grid_kalinti_sifir,
        test_dry_bos_panel_grid_ay_sonra_net,
        test_geri_hala_cap_siz,
        test_brut_cokus_yasak_326,
        test_326_apply_brut_cokus_yazmaz,
        test_plan_uyusmazligi_yazmaz,
        test_normal_apply_yazar_audit,
        test_kira_txn_rollback,
        test_tek_yol_cache_oncelik,
        test_idempotent_fingerprint,
    ):
        print("---", fn.__name__)
        fn()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
