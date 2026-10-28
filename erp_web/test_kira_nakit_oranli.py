# -*- coding: utf-8 -*-
"""KIRA_NAKIT_ORANLI_ARTIS: bayrak kapalıyken eski sonuç, açıkken sütunlar aynı oranda.
Python ile yıl tablosu JS'i aynı turu atar. Ağ ve canlı yazma yok.
"""
import json
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ.pop("KIRA_NAKIT_ORANLI_ARTIS", None)
logging.disable(logging.WARNING)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from routes.giris_routes import (  # noqa: E402
    _aylik_grid_contract_core,
    kira_ay_brut_pozitif_korunur,
    kira_nakit_oranli_artis_aktif,
    kira_yil_oranli_adim,
    kira_yil_sutunlari,
    kira_yil_zinciri,
)

HTML = ROOT / "templates" / "giris" / "index.html"
FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:500]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def near(a, b, tol=0.02):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _fn(src, name):
    key = "function " + name + "("
    i = src.find(key)
    if i < 0:
        raise SystemExit("yok " + name)
    j = src.find("{", i)
    depth = 0
    k = j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
        k += 1
    raise SystemExit("kapanmadi " + name)


def kyc(**kw):
    d = {
        "sozlesme_tarihi": date(2022, 3, 1),
        "sozlesme_bitis": date(2024, 3, 1),
        "aylik_kira": 10000,
        "kdv_oran": 20,
        "kira_nakit": False,
        "kira_nakit_tutar": 0,
        "kira_banka_tutar": 10000,
        "kira_artis_tarihi": date(2022, 3, 1),
        "durum": "pasif",
        "_kira_reel_detay": {},
    }
    d.update(kw)
    return d


def harita(flag, **kw):
    if flag:
        os.environ["KIRA_NAKIT_ORANLI_ARTIS"] = "1"
    else:
        os.environ.pop("KIRA_NAKIT_ORANLI_ARTIS", None)
    try:
        core = _aylik_grid_contract_core(kyc(**kw), {2023: {3: 20}, 2024: {3: 20}})
    finally:
        os.environ.pop("KIRA_NAKIT_ORANLI_ARTIS", None)
    return (core or {}).get("yillik_map") or {}


def main():
    html = HTML.read_text(encoding="utf-8")
    check("lf", "\r\n" not in html)
    check("bayrak varsayilan kapali", kira_nakit_oranli_artis_aktif() is False)
    check("bayrak sayfaya iner", "window.KIRA_NAKIT_ORANLI_ARTIS" in html)

    s1 = kira_yil_sutunlari(10000, 0, 20)
    check("1 kdv banka toplam 12000", near(s1["kdv_dahil"], 12000) and near(s1["banka"], 12000) and near(s1["toplam"], 12000), s1)
    s2 = kira_yil_sutunlari(10000, 5000, 20)
    check("2 banka kdv 6000 toplam 11000", near(s2["banka"], 6000) and near(s2["kdv_dahil"], 6000) and near(s2["toplam"], 11000) and near(s2["banka_matrah"], 5000), s2)
    s3 = kira_yil_sutunlari(600, 0, 20)
    check("3 720", near(s3["kdv_dahil"], 720) and near(s3["banka"], 720) and near(s3["toplam"], 720) and near(s3["reel"], 720), s3)
    s4 = kira_yil_sutunlari(0, 5000, 20, reel=11000)
    check("4 reel", near(s4["banka"], 6000) and near(s4["kdv_dahil"], 6000) and near(s4["aylik"], 10000) and near(s4["toplam"], 11000), s4)
    s5 = kira_yil_sutunlari(92000, 52000, 20)
    check("5 hibrit sutun", near(s5["nakit"], 52000) and near(s5["banka"], 48000) and near(s5["kdv_dahil"], 48000) and near(s5["toplam"], 100000) and near(s5["aylik"], 92000), s5)

    adim = kira_yil_oranli_adim(10000, 5000, 20, 20)
    check(
        "7 ertesi yil",
        near(adim["aylik"], 12000) and near(adim["nakit"], 6000) and near(adim["banka_matrah"], 6000)
        and near(adim["banka"], 7200) and near(adim["kdv_dahil"], 7200) and near(adim["toplam"], 13200),
        adim,
    )

    kapali = harita(False, kira_nakit_tutar=5000, kira_banka_tutar=5000, kira_nakit=True)
    acik = harita(True, kira_nakit_tutar=5000, kira_banka_tutar=5000, kira_nakit=True)
    check("7 grid 2022", near(acik.get(2022), 11000), acik)
    check("7 grid 2023", near(acik.get(2023), 13200), acik)
    check("7 bayrak kapali eski split", near(kapali.get(2022), 11000) and near(kapali.get(2023), 13200), kapali)

    sifir_k = harita(False, kira_nakit_tutar=0, kira_nakit=False, aylik_kira=300)
    sifir_a = harita(True, kira_nakit_tutar=0, kira_nakit=False, aylik_kira=300)
    check("nakit 0 bayrak acik ayni", sifir_k == sifir_a and bool(sifir_k), (sifir_k, sifir_a))

    # Nakit var ama karta denk değil: kapalı yol KDV'siz kalır, açık yol banka payına KDV ekler.
    eski = harita(False, kira_nakit_tutar=5000, kira_banka_tutar=4000, kira_nakit=True)
    yeni = harita(True, kira_nakit_tutar=5000, kira_banka_tutar=4000, kira_nakit=True)
    check("bayrak kapali kart tutari", near(eski.get(2022), 10000), eski)
    check("bayrak acik banka kdv", near(yeni.get(2022), 11000) and not near(yeni.get(2022), eski.get(2022)), yeni)

    zincir = kira_yil_zinciri(
        2022, 2025, 10000, 5000, 20,
        lambda y: 20,
        {2024: {"tip": "hibrit", "hibrit_net": 52000, "hibrit_banka": 40000}},
    )
    check("hibrit taban 2024", near(zincir[2024]["toplam"], 100000) and near(zincir[2024]["nakit"], 52000), zincir.get(2024))
    check("hibrit sonrasi 2025", near(zincir[2025]["aylik"], 110400) and near(zincir[2025]["nakit"], 62400) and near(zincir[2025]["banka"], 57600) and near(zincir[2025]["toplam"], 120000), zincir.get(2025))

    belirsiz = kira_yil_zinciri(
        2024, 2025, 40000, 10000, 20,
        lambda y: 0,
        {2025: {"tip": "dahil", "tutar": 75250}},
    )
    check("dahil belirsiz nakit", belirsiz[2025]["belirsiz"] is True and near(belirsiz[2025]["nakit"], 10000) and near(belirsiz[2025]["toplam"], 75250), belirsiz.get(2025))
    tavan = kira_yil_zinciri(2024, 2025, 1000, 900, 20, lambda y: 0, {2025: {"tip": "dahil", "tutar": 500}})
    check("dahil nakit toplami asmaz", near(tavan[2025]["nakit"], 500) and near(tavan[2025]["toplam"], 500), tavan.get(2025))

    dolu = {"brut_tutar_kdv": 100}
    bos = {"brut_tutar_kdv": 0}
    check("pozitif brut korunur", kira_ay_brut_pozitif_korunur(dolu["brut_tutar_kdv"]) and not kira_ay_brut_pozitif_korunur(bos["brut_tutar_kdv"]))
    src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check("grid dolgusu pozitif brutu atlar", "if kira_ay_brut_pozitif_korunur(brut_cur, tol):" in src)

    senaryolar = []
    for i, (ay, nk, oran, kdv, reel) in enumerate([
        (10000, 0, 0, 20, None),
        (10000, 5000, 20, 20, None),
        (600, 0, 0, 20, None),
        (0, 5000, 0, 20, 11000),
        (92000, 52000, 0, 20, None),
        (10000, 5000, 20, 20, None),
        (12500, 2500, 15, 20, None),
        (300, 0, 68.5, 20, None),
        (505.50, 0, 38.1, 20, None),
        (698.10, 0, 0, 20, None),
        (540, 0, 10, 20, 540),
        (837.72, 0, 12, 20, None),
        (3333, 1111, 7, 20, None),
        (3333, 1111, 7, 10, None),
        (8000, 8000, 5, 20, None),
        (8000, 9000, 5, 20, None),
        (1500, 400, 0, 1, None),
        (1500, 400, 33, 18, None),
        (22000, 7500, 2.5, 20, None),
        (22000, 7500, 2.5, 8, None),
        (100, 1, 50, 20, None),
        (99999.99, 12345.67, 3, 20, None),
        (4500, 4500, 0, 0, None),
        (4500, 0, 11, 1, None),
        (0, 0, 20, 20, None),
        (10.01, 3.33, 13, 20, None),
        (10.01, 3.33, 13, 20, 20),
        (76000, 36000, 9, 20, None),
        (76000, 36000, 9, 20, 90000),
        (1, 0, 100, 20, None),
        (2, 1, 0.5, 18, None),
        (123456.78, 1000.01, 1.25, 20, None),
    ]):
        py = kira_yil_sutunlari(ay, nk, kdv, reel=reel) if not oran else kira_yil_oranli_adim(ay, nk, oran, kdv)
        senaryolar.append({"i": i, "ay": ay, "nk": nk, "oran": oran, "kdv": kdv, "reel": reel, "py": py})
    check("en az 30 senaryo", len(senaryolar) >= 30, len(senaryolar))

    js = "\n".join(_fn(html, n) for n in ("kiraRound2", "kiraYilSutunlari", "kiraYilOranliAdim"))
    js_path = ROOT / "_tmp_kira_oranli.js"
    payload = json.dumps([{k: s[k] for k in ("i", "ay", "nk", "oran", "kdv", "reel")} for s in senaryolar])
    harness = js + "\nvar sen=" + payload + ";\nvar out=sen.map(function(s){var r=s.oran?kiraYilOranliAdim(s.ay,s.nk,s.oran,s.kdv):kiraYilSutunlari(s.ay,s.nk,s.kdv,s.reel);return {i:s.i,aylik:r.aylik,nakit:r.nakit,banka_matrah:r.banka_matrah,banka:r.banka,kdv_dahil:r.kdv_dahil,toplam:r.toplam};});\nconsole.log(JSON.stringify(out));\n"
    js_path.write_text(harness, encoding="utf-8", newline="\n")
    try:
        chk = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True, encoding="utf-8")
        check("node --check", chk.returncode == 0, chk.stderr)
        run = subprocess.run(["node", str(js_path)], capture_output=True, text=True, encoding="utf-8")
        check("node calisti", run.returncode == 0, run.stderr or run.stdout)
        js_out = json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else []
        ayni = 0
        for py_s, js_s in zip(senaryolar, js_out):
            p = py_s["py"]
            ok = all(near(p[k], js_s.get(k)) for k in ("aylik", "nakit", "banka_matrah", "banka", "kdv_dahil", "toplam"))
            if ok:
                ayni += 1
            else:
                check("senaryo " + str(py_s["i"]), False, (p, js_s))
        check("python js ayni", ayni == len(senaryolar), ayni)
    finally:
        js_path.unlink(missing_ok=True)
        os.environ.pop("KIRA_NAKIT_ORANLI_ARTIS", None)
        os.environ.pop("ALLOW_PROD_WRITE", None)

    if FAILS:
        print("FAILS", ",".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
