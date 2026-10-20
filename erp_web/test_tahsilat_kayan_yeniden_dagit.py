# -*- coding: utf-8 -*-
"""Kayan yeniden dağıtım — yerel/fake. Canlı yazma yok."""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

import tahsilat_kayan as tk  # noqa: E402
import tahsilat_kayan_yeniden_dagit as cli  # noqa: E402

FAILS: list[str] = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _ac(pays):
    parts = []
    for iso, v in pays:
        parts.append(f"|AYLIK_TAH|{iso}|")
        parts.append(f"|AYLIK_PAY|{iso}={float(v):.2f}|")
    return "Banka tahsilat " + " ".join(parts)


def _row(tid, mid, tarih, tutar, kaynak, pays=None, tah=None):
    if pays:
        ac = _ac(pays)
    elif tah:
        ac = "Tahsilat " + " ".join(f"|AYLIK_TAH|{i}|" for i in tah)
    else:
        ac = "Tahsilat"
    return {
        "id": tid, "mid": mid, "tarih": tarih, "tutar": float(tutar),
        "aciklama": ac, "makbuz_no": f"M{tid}",
        "banka_referans_no": f"R{tid}", "kaynak": kaynak,
        "odeme_turu": "havale",
    }


# ------------------------------------------------------------------ 153

def _borc_153():
    borc = {}
    # 2022-12..2023-11: 360
    y, m = 2022, 12
    while (y, m) <= (2023, 11):
        borc[f"{y}-{m:02d}-01"] = 360.0
        m += 1
        if m > 12:
            m, y = 1, y + 1
    borc["2023-12-01"] = 540.0
    borc["2024-01-01"] = 540.0
    for mm in range(2, 12):
        borc[f"2024-{mm:02d}-01"] = 540.0
    borc["2024-12-01"] = 866.0
    for mm in range(1, 12):
        borc[f"2025-{mm:02d}-01"] = 866.0
    borc["2025-12-01"] = 1188.0
    for mm in range(1, 13):
        borc[f"2026-{mm:02d}-01"] = 1188.0
    return borc


def _rows_153():
    rows = []
    # sabit: 2022-12..2023-12 TAH (Ocak 2024 silinmiş — yok)
    y, m = 2022, 12
    tid = 100
    while (y, m) <= (2023, 12):
        iso = f"{y}-{m:02d}-01"
        tut = 540.0 if iso == "2023-12-01" else 360.0
        rows.append(_row(tid, 153, iso, tut, "", tah=[iso]))
        tid += 1
        m += 1
        if m > 12:
            m, y = 1, y + 1
    # kayan: canlı eski PAY (Ocak atlanmış)
    kayan = [
        (6009, "2024-02-28", 540.0, [("2024-02-01", 540.0)]),
        (6010, "2024-03-28", 540.0, [("2024-03-01", 540.0)]),
        (6011, "2024-04-25", 540.0, [("2024-04-01", 540.0)]),
        (6012, "2024-05-28", 540.0, [("2024-05-01", 540.0)]),
        (6013, "2024-06-27", 540.0, [("2024-06-01", 540.0)]),
        (6014, "2024-07-25", 540.0, [("2024-07-01", 540.0)]),
        (6015, "2024-08-27", 540.0, [("2024-08-01", 540.0)]),
        (6016, "2024-09-26", 540.0, [("2024-09-01", 540.0)]),
        (6017, "2024-11-25", 1080.0, [("2024-10-01", 540.0), ("2024-11-01", 540.0)]),
        (6018, "2025-01-17", 1732.80, [("2024-12-01", 866.0), ("2025-01-01", 866.0), ("2025-02-01", 0.80)]),
        (6019, "2025-02-20", 866.40, [("2025-02-01", 865.20), ("2025-03-01", 1.20)]),
        (6020, "2025-04-18", 1732.80, [("2025-03-01", 864.80), ("2025-04-01", 866.0), ("2025-05-01", 2.0)]),
        (6021, "2025-06-04", 1732.80, [("2025-05-01", 864.0), ("2025-06-01", 866.0), ("2025-07-01", 2.80)]),
        (6022, "2025-09-18", 2598.0, [("2025-07-01", 863.20), ("2025-08-01", 866.0), ("2025-09-01", 866.0), ("2025-10-01", 2.80)]),
        (6023, "2025-11-25", 2598.0, [("2025-10-01", 863.20), ("2025-11-01", 866.0), ("2025-12-01", 868.80)]),
        (6024, "2026-01-19", 1188.0, [("2025-12-01", 319.20), ("2026-01-01", 868.80)]),
        (6025, "2026-02-19", 1188.0, [("2026-01-01", 319.20), ("2026-02-01", 868.80)]),
        (6026, "2026-04-30", 3564.0, [("2026-02-01", 319.20), ("2026-03-01", 1188.0), ("2026-04-01", 1188.0), ("2026-05-01", 868.80)]),
        (6027, "2026-07-03", 1188.0, [("2026-05-01", 319.20), ("2026-06-01", 868.80)]),
        (6028, "2026-09-18", 4104.0, [("2026-06-01", 319.20), ("2026-07-01", 1188.0), ("2026-08-01", 1188.0), ("2026-09-01", 1188.0), ("2026-10-01", 220.80)]),
    ]
    for tid, tah, tut, pays in kayan:
        rows.append(_row(tid, 153, tah, tut, "banka_import", pays=pays))
    return rows


def test_153_senaryo():
    r = tk.tahsilat_kayan_yeniden_dagit(
        153, dry_run=True, etiket="t153", tetik="cli",
        rows=_rows_153(), borc=_borc_153(),
    )
    check("153 dry ok", r.get("ok") is True, r.get("neden"))
    check("153 yazilmadi", r.get("yazildi") is False)
    plan = r["plan"]
    check("153 bosluk eski var", bool(plan.get("bosluk_eski")), plan.get("bosluk_eski"))
    check("153 bosluk yeni yok", plan.get("bosluk_yeni") == [], plan.get("bosluk_yeni"))
    yeni = plan.get("yeni_ay_toplam") or {}
    # plan ozetinde yeni_ay_toplam yoksa ay_degisimden bak
    ay = {d["ay"]: d["yeni"] for d in plan.get("ay_degisim") or []}
    check("153 Ocak degisim yeni 540", ay.get("2024-01-01") == 540.0, ay.get("2024-01-01"))
    sk = plan.get("son_kismi_yeni") or {}
    check("153 Eylul kismi ay", sk.get("ay") == "2026-09-01", sk)
    check("153 Eylul odenen 868.80", sk.get("odenen") == 868.80, sk)
    check("153 Eylul kalan 319.20", sk.get("kalan") == 319.20, sk)
    check("153 odeme=dagitim", plan.get("odeme_toplam") == 27892.80 == plan.get("dagitim_toplam"), plan.get("odeme_toplam"))
    # Ekim-Kasım açık: yeni toplamda Ekim 0 (değişimde Ekim eski 220.8 → yeni 0 olmalı veya yok)
    check("153 Ekim yeni 0", ay.get("2026-10-01", 0) == 0, ay.get("2026-10-01"))
    # FP sabit
    r2 = tk.tahsilat_kayan_yeniden_dagit(
        153, dry_run=True, etiket="t153b", tetik="cli",
        rows=_rows_153(), borc=_borc_153(),
    )
    check("153 FP sabit", r.get("parmak_izi") == r2.get("parmak_izi"))


# ------------------------------------------------------------------ FakeDb

class FakeDb:
    def __init__(self, rows):
        self.tahsilatlar = {int(r["id"]): copy.deepcopy(r) for r in rows}
        self.yedek = []
        self.yedek_tablo = False
        self.audit = []
        self.seq = 0

    def oku(self, sql, params=None):
        s = " ".join(str(sql or "").split())
        if "FROM tahsilat_marker_yedek" in s:
            mid, etiket = params
            return [copy.deepcopy(y) for y in self.yedek
                    if y["mid"] == mid and y["etiket"] == etiket and not y.get("geri_yazildi")]
        if "FROM tahsilatlar" in s:
            mid = int(params[0])
            out = [copy.deepcopy(r) for r in self.tahsilatlar.values() if int(r["mid"]) == mid]
            out.sort(key=lambda x: (x["tarih"], x["id"]))
            return out
        return []

    def calistir(self, sql, params=None):
        s = " ".join(str(sql or "").split())
        if s.startswith("CREATE TABLE"):
            if "tahsilat_marker_yedek" in s:
                self.yedek_tablo = True
            return
        if "pg_advisory_xact_lock" in s or s.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK TO")):
            return
        if "INSERT INTO audit_log" in s:
            self.audit.append(params)
            return
        if "INSERT INTO tahsilat_marker_yedek" in s:
            if not self.yedek_tablo:
                raise RuntimeError("yedek tablo yok")
            self.seq += 1
            self.yedek.append({
                "id": self.seq, "tahsilat_id": params[0], "mid": params[1],
                "eski_aciklama": params[2], "eski_marker_json": params[3],
                "yeni_aciklama": params[4], "etiket": params[5],
                "islem_tamam": False, "geri_yazildi": False,
            })
            return
        if "UPDATE tahsilat_marker_yedek SET islem_tamam" in s:
            for y in self.yedek:
                if y["tahsilat_id"] == params[0] and y["etiket"] == params[1]:
                    y["islem_tamam"] = True
            return
        if "UPDATE tahsilat_marker_yedek SET geri_yazildi" in s:
            for y in self.yedek:
                if y["id"] == params[0]:
                    y["geri_yazildi"] = True
            return
        if s.startswith("UPDATE tahsilatlar SET aciklama"):
            ac, tid = params[0], int(params[1])
            mid = int(params[2])
            r = self.tahsilatlar.get(tid)
            if not r or int(r["mid"]) != mid:
                raise RuntimeError("update_miss")
            if len(params) > 3 and str(r.get("kaynak") or "") != str(params[3]):
                raise RuntimeError("kaynak_miss")
            r["aciklama"] = ac
            return
        raise RuntimeError("bilinmeyen_sql:" + s[:80])


def _kucuk_borc():
    return {f"2025-{m:02d}-01": 100.0 for m in range(1, 9)}


def test_sabit_kayan_karisim():
    borc = _kucuk_borc()
    rows = [
        # sabit grid_toplu: Mart'a kilitli
        _row(1, 9, "2025-03-05", 100.0, "grid_toplu", pays=[("2025-03-01", 100.0)]),
        # kayan: eski marker Subat'ta yığılı; Ocak açık → Ocak'a kaymalı, Mart sabit dolu
        _row(2, 9, "2025-04-01", 100.0, "banka_import", pays=[("2025-02-01", 100.0)]),
        _row(3, 9, "2025-04-02", 100.0, "manuel_makbuz", pays=[("2025-02-01", 100.0)]),
    ]
    plan = tk.hesapla_plan_kayan(9, rows, borc=borc)
    check("karisim ok", plan.get("ok") is True, plan.get("invariant_hatalar"))
    check("karisim sabit 1", plan.get("sabit_n") == 1)
    check("karisim kayan 2", plan.get("kayan_n") == 2)
    yeni = plan.get("yeni_ay_toplam") or {}
    check("karisim Mart sabit 100", yeni.get("2025-03-01") == 100.0, yeni)
    check("karisim Ocak 100", yeni.get("2025-01-01") == 100.0, yeni)
    check("karisim Subat 100", yeni.get("2025-02-01") == 100.0, yeni)
    check("karisim grid satir planda yok", all(p["id"] != 1 for p in plan["plans"]))


def test_tek_odeme_noop_ve_idempotent():
    borc = _kucuk_borc()
    rows = [_row(1, 9, "2025-02-01", 100.0, "banka_import", pays=[("2025-01-01", 100.0)])]
    r = tk.tahsilat_kayan_yeniden_dagit(9, dry_run=True, etiket="x", rows=rows, borc=borc)
    check("tek noop", r.get("degisiklik_yok") is True, r)

    # apply → ikinci dry idempotent
    rows2 = [
        _row(1, 9, "2025-03-01", 100.0, "", tah=["2025-01-01"]),
        _row(2, 9, "2025-04-01", 100.0, "banka_import", pays=[("2025-03-01", 100.0)]),
        _row(3, 9, "2025-04-02", 100.0, "banka_import", pays=[("2025-03-01", 100.0)]),
    ]
    db = FakeDb(rows2)
    dry = tk.tahsilat_kayan_yeniden_dagit(
        9, dry_run=True, etiket="e1", oku=db.oku, calistir=db.calistir, borc=borc)
    check("idem dry degisti", (dry.get("plan") or {}).get("n_degisti", 0) > 0, dry.get("plan"))
    ap = tk.tahsilat_kayan_yeniden_dagit(
        9, dry_run=False, etiket="e1", oku=db.oku, calistir=db.calistir,
        borc=borc, beklenen_fp=dry.get("parmak_izi"))
    check("idem apply ok", ap.get("ok") and ap.get("yazildi"), ap)
    check("idem yedek", len(db.yedek) == (ap.get("plan") or {}).get("n_degisti"), db.yedek)
    check("idem audit", len(db.audit) >= 1)
    # sabit satır aciklama aynı
    check("idem sabit ayni", db.tahsilatlar[1]["aciklama"] == rows2[0]["aciklama"])
    # tutar/tarih/kaynak sabit
    for tid in (2, 3):
        check(f"idem tutar {tid}", db.tahsilatlar[tid]["tutar"] == 100.0)
    dry2 = tk.tahsilat_kayan_yeniden_dagit(
        9, dry_run=True, etiket="e2", oku=db.oku, calistir=db.calistir, borc=borc)
    check("idem ikinci dry noop", dry2.get("degisiklik_yok") is True, dry2.get("plan"))

    # geri alma
    g = tk.geri_yukle_kayan(9, "e1", oku=db.oku, calistir=db.calistir)
    check("geri ok", g.get("ok") is True, g)
    check("geri 2 aciklama", db.tahsilatlar[2]["aciklama"] == rows2[1]["aciklama"])
    check("geri 3 aciklama", db.tahsilatlar[3]["aciklama"] == rows2[2]["aciklama"])


def test_ayni_gun_ayni_tutar():
    borc = _kucuk_borc()
    rows = [
        _row(11, 9, "2025-05-01", 100.0, "banka_import", pays=[("2025-04-01", 100.0)]),
        _row(12, 9, "2025-05-01", 100.0, "banka_import", pays=[("2025-04-01", 100.0)]),
    ]
    # ref'ler farklı (R11/R12) — ikisi de sayılmalı
    plan = tk.hesapla_plan_kayan(9, rows, borc=borc)
    yeni = plan.get("yeni_ay_toplam") or {}
    check("ayni gun Ocak 100", yeni.get("2025-01-01") == 100.0, yeni)
    check("ayni gun Subat 100", yeni.get("2025-02-01") == 100.0, yeni)
    check("ayni gun toplam 200", plan.get("dagitim_toplam") == 200.0)


def test_fp_uyusmazligi_ve_sinir():
    borc = _kucuk_borc()
    rows = [_row(2, 9, "2025-04-01", 100.0, "banka_import", pays=[("2025-03-01", 100.0)])]
    r = tk.tahsilat_kayan_yeniden_dagit(
        9, dry_run=False, etiket="x", rows=rows, borc=borc,
        beklenen_fp="yanlis", izinli_yazma=True)
    check("fp dur", r.get("ok") is False and r.get("neden") == "plan_degisti", r.get("neden"))

    cok = [_row(i + 1, 9, "2025-04-01", 10.0, "banka_import",
                pays=[("2025-03-01", 10.0)]) for i in range(tk.MAX_TAHSILAT + 1)]
    r2 = tk.tahsilat_kayan_yeniden_dagit(9, dry_run=True, etiket="x", rows=cok, borc=borc)
    check("sinir dur", r2.get("ok") is False and r2.get("neden") == "sinir_asildi", r2.get("neden"))
    check("sinir mesaj", "500" in (r2.get("mesaj") or "") or "500" in str((r2.get("plan") or {}).get("mesaj") or ""), r2)


def test_bayrak():
    eski_e = os.environ.pop("KAYAN_YENIDEN_DAGIT_ENABLED", None)
    eski_i = os.environ.pop("KAYAN_YENIDEN_DAGIT_MUSTERI_IDS", None)
    try:
        check("bayrak varsayilan kapali", tk.kayan_yeniden_dagit_enabled() is False)
        check("allowlist ilk 867,153", tk.kayan_yeniden_dagit_allowlist() == {867, 153})
        check("kapaliyken aktif degil", tk.kayan_yeniden_dagit_aktif(153) is False)
        os.environ["KAYAN_YENIDEN_DAGIT_ENABLED"] = "1"
        check("acik 153 aktif", tk.kayan_yeniden_dagit_aktif(153) is True)
        check("acik 999 degil", tk.kayan_yeniden_dagit_aktif(999) is False)
        os.environ["KAYAN_YENIDEN_DAGIT_MUSTERI_IDS"] = "42"
        check("allowlist ozel", tk.kayan_yeniden_dagit_aktif(42) is True and tk.kayan_yeniden_dagit_aktif(153) is False)
    finally:
        if eski_e is None:
            os.environ.pop("KAYAN_YENIDEN_DAGIT_ENABLED", None)
        else:
            os.environ["KAYAN_YENIDEN_DAGIT_ENABLED"] = eski_e
        if eski_i is None:
            os.environ.pop("KAYAN_YENIDEN_DAGIT_MUSTERI_IDS", None)
        else:
            os.environ["KAYAN_YENIDEN_DAGIT_MUSTERI_IDS"] = eski_i


def test_placeholder_kaynagi():
    src = (ROOT / "banka_marker_onarim.py").read_text(encoding="utf-8")
    check("placeholder guard var", "_aylik_grid_should_skip_placeholder_collapse" in src)
    check("sync kullanilir", "_sync_panel_grid_after" in Path(tk.__file__).read_text(encoding="utf-8"))
    check("force collapse yok", "force_placeholder_collapse=True" not in Path(tk.__file__).read_text(encoding="utf-8"))


def test_api_yetkisiz_ve_bayrak():
    from flask import Flask

    import tahsilat_kayan as _tk
    from routes import giris_routes as gr

    app = Flask(__name__)
    try:
        from flask_login import LoginManager
        lm = LoginManager(app)

        @lm.user_loader
        def _load(_uid):  # test: kimse yuklenmez → anonim
            return None
    except Exception:
        pass
    app.register_blueprint(gr.bp, url_prefix="/giris")
    c = app.test_client()
    rv = c.post("/giris/api/tahsilat-kayan/onizleme", json={"musteri_id": 153})
    check("yetkisiz 401", rv.status_code == 401, rv.status_code)

    # bayrak kapalı → __wrapped__ 404
    eski_e = os.environ.pop("KAYAN_YENIDEN_DAGIT_ENABLED", None)
    try:
        f = gr.api_tahsilat_kayan_onizleme.__wrapped__
        with app.test_request_context(
            "/giris/api/tahsilat-kayan/onizleme", method="POST", json={"musteri_id": 153}
        ):
            resp = f()
            kod = resp[1] if isinstance(resp, tuple) else resp.status_code
        check("bayrak kapali 404", kod == 404, kod)
    finally:
        if eski_e is not None:
            os.environ["KAYAN_YENIDEN_DAGIT_ENABLED"] = eski_e


def test_fe_kayan():
    html = (ROOT / "templates/giris/index.html").read_text(encoding="utf-8")
    check("fe banner", 'id="kayanBanner"' in html and "arkada açık ay kaldı" in html)
    check("fe modal", 'id="kayanModal"' in html and "kayanModalGovde" in html)
    check("fe fn kontrol", "function kayanBannerKontrol" in html)
    check("fe fn onizleme", "function kayanOnizlemeAc" in html)
    check("fe fn uygula", "function kayanUygula" in html)
    check("fe 404 gizle", "r.status === 404" in html)
    check("fe hook tahsilden", "kayanBannerKontrol(mid)" in html)
    check("fe hook rapor", "kayanBannerKontrol(midSil)" in html)
    check("fe banner gizli default", ".kayan-banner{display:none" in html)


def test_fe_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        check("playwright atlandi", True, "kurulu degil")
        return
    html = (ROOT / "templates/giris/index.html").read_text(encoding="utf-8")
    i = html.find("/* kayan-yeniden-dagit-css */")
    j = html.find(".sozlesmeler-aylik-makbuz-alt {", i)
    css = html[i:j] if i >= 0 and j > i else ""
    bi = html.find('<div id="kayanBanner"')
    bj = html.find("{% endblock %}", bi)
    snippet = html[bi:bj] if bi >= 0 else ""
    ji = html.find("/* kayan-yeniden-dagit-js */")
    jj = html.find("function sozlesmeAylikTahsildenCikar", ji)
    js = html[ji:jj] if ji >= 0 else ""
    check("pw parcalar", bool(css and snippet and js))
    page_html = f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body>{snippet}
<script>
var selectedId = 153;
var __fetch = [];
window.fetch = function(url, opts) {{
  __fetch.push(url);
  if (String(url).indexOf('onizleme') >= 0) {{
    return Promise.resolve({{ status: 200, json: function() {{ return Promise.resolve({{
      ok: true, bosluk_var: true, kayan_n: 2, n_degisti: 1, parmak_izi: 'fp-test',
      etiket: 'kayan_test', odeme_toplam: 200, dagitim_toplam: 200,
      ay_degisim: [{{ay: '2024-01-01', eski: 0, yeni: 540}}],
      son_kismi: {{ay: '2026-09-01', odenen: 868.8, kalan: 319.2}}
    }}); }} }});
  }}
  return Promise.resolve({{ status: 200, json: function() {{ return Promise.resolve({{ ok: true, mesaj: 'dagitildi' }}); }} }});
}};
{js}
kayanBannerKontrol(153);
</script></body></html>"""
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True, channel="chrome")
            page = browser.new_page(viewport={"width": 900, "height": 700})
            page.set_content(page_html, wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            vis = page.locator("#kayanBanner").is_visible()
            check("pw banner gorunur", vis)
            page.locator(".kayan-onizle-btn").click()
            page.wait_for_timeout(200)
            modal_vis = page.locator("#kayanModal").is_visible()
            check("pw modal gorunur", modal_vis)
            txt = page.locator("#kayanModalGovde").inner_text()
            check("pw modal tablo", "2024-01" in txt and "200" in txt, txt[:120])
            page.locator("#kayanUygulaBtn").click()
            page.wait_for_timeout(300)
            banner_after = page.locator("#kayanBanner").is_visible()
            check("pw uygula banner gizli", banner_after is False)
            browser.close()
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "BrowserType.launch" in msg:
            check("playwright atlandi", True, "chromium yok")
        else:
            check("pw calisti", False, msg[:180])


def test_cli_dry_default():
    called = {}
    orig = cli.tahsilat_kayan_yeniden_dagit

    def fake(mid, **kw):
        called.update(kw)
        called["mid"] = mid
        return {"ok": True, "mid": mid, "dry_run": kw.get("dry_run"), "yazildi": False, "plan": {}}

    cli.tahsilat_kayan_yeniden_dagit = fake
    try:
        rc = cli.main(["--mid", "153"])
        check("cli rc", rc == 0)
        check("cli dry default", called.get("dry_run") is True, called)
        check("cli tetik", called.get("tetik") == "cli")
    finally:
        cli.tahsilat_kayan_yeniden_dagit = orig


if __name__ == "__main__":
    test_153_senaryo()
    test_sabit_kayan_karisim()
    test_tek_odeme_noop_ve_idempotent()
    test_ayni_gun_ayni_tutar()
    test_fp_uyusmazligi_ve_sinir()
    test_bayrak()
    test_placeholder_kaynagi()
    test_api_yetkisiz_ve_bayrak()
    test_fe_kayan()
    test_fe_playwright()
    test_cli_dry_default()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
