# -*- coding: utf-8 -*-
"""Pasif kapanis sonrasi bos donem/yil satiri gizleme — temiz ortam birim testleri.

Prod yazma yok. PII yok. KAPANIS_*/ODA_*/REEL_TUFE_*/TAHSILAT_* tanimsiz.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)

from pasif_donem_satir_gorunum import (  # noqa: E402
    donem_baslangic,
    donem_yil_satiri_gosterilsin,
    filtre_donem_yillari,
    gorunum_son_borc_ayi,
)
from kira_kapanis_cap import hepsi_sozlesme_yili_aktif  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_aktif_degismez():
    kyc = {
        "durum": "aktif",
        "kapanis_tarihi": date(2026, 3, 12),
        "kapanis_sonrasi_borc_ay": 0,
        "rent_start_date": date(2024, 6, 12),
        "kira_artis_tarihi": date(2025, 6, 12),
    }
    years = [2024, 2025, 2026]
    check("aktif_filtre_ayni", filtre_donem_yillari(years, kyc) == years)
    check("aktif_son_none", gorunum_son_borc_ayi(kyc) is None)


def test_pasif_bos_donem_gizli():
    # mid 377 tipi: N=0, N0 bayragi test ortaminda tanimsiz → N=0 son=kapanis ayi
    # Canli olcumde N0=1 ile son=2026-02; burada bayraksiz N=0 → 2026-03.
    # Donem 2026 bas=Haziran > her iki son da → gizlenmeli (veri yok).
    os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)
    kyc = {
        "durum": "pasif",
        "iptal": False,
        "kapanis_tarihi": date(2026, 3, 12),
        "kapanis_sonrasi_borc_ay": 0,
        "rent_start_date": date(2024, 6, 12),
        "kira_artis_tarihi": date(2025, 6, 12),
    }
    years = [2024, 2025, 2026]
    out = filtre_donem_yillari(years, kyc)
    check("pasif_bos_2026_gizli", 2026 not in out, str(out))
    check("pasif_2024_2025_kalir", out == [2024, 2025], str(out))
    # N0 canli sim: son=Subat → 2025 donemi (Haz25..May26) bas<=son → kalir
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    try:
        out_n0 = filtre_donem_yillari(years, kyc)
        check("pasif_n0_2026_gizli", 2026 not in out_n0, str(out_n0))
        check("pasif_n0_2025_kalir", 2025 in out_n0, str(out_n0))
        son = gorunum_son_borc_ayi(kyc)
        check("pasif_n0_son_2026_02", son == date(2026, 2, 1), str(son))
    finally:
        os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)


def test_veri_varsa_gorunur():
    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2026, 3, 12),
        "kapanis_sonrasi_borc_ay": 0,
        "rent_start_date": date(2024, 6, 12),
        "kira_artis_tarihi": date(2025, 6, 12),
    }
    os.environ["KAPANIS_N0_KAPANIS_AYI_HARIC"] = "1"
    try:
        check(
            "reel_only_gizli",
            donem_yil_satiri_gosterilsin(2026, kyc, reel_map={2026: 1071.54}) is False,
        )
        check(
            "panel_istisna",
            donem_yil_satiri_gosterilsin(
                2026, kyc, panel_by_iso={"2026-07-01": {"tahsil": 100.0, "aylik": 100.0}}
            )
            is True,
        )
        check(
            "fatura_istisna",
            donem_yil_satiri_gosterilsin(2026, kyc, fatura_ym=["2026-08"]) is True,
        )
        check(
            "marker_istisna",
            donem_yil_satiri_gosterilsin(2026, kyc, marker_by_iso={"2026-09-01": 50.0}) is True,
        )
        check(
            "bos_hala_gizli",
            donem_yil_satiri_gosterilsin(2026, kyc) is False,
        )
    finally:
        os.environ.pop("KAPANIS_N0_KAPANIS_AYI_HARIC", None)


def test_hepsi_donem_sonu_bayrak_kapali():
    check("hepsi_bayrak_kapali", hepsi_sozlesme_yili_aktif() is False)
    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2023, 8, 15),
        "kapanis_sonrasi_borc_ay": None,
        "rent_start_date": date(2022, 12, 1),
        "kira_artis_tarihi": date(2022, 12, 1),
    }
    son = gorunum_son_borc_ayi(kyc)
    # giris Ara → yil sonu Kas; kap Ağu-2023 → Kas-2023
    check("hepsi_son_2023_11", son == date(2023, 11, 1), str(son))
    # 2023 donem bas=Ara-2023 > Kas-2023 → gizli (veri yok)
    check("hepsi_2023_gizli", donem_yil_satiri_gosterilsin(2023, kyc) is False)
    # yalniz reel kaydi gostermeye yetmez
    check(
        "hepsi_2023_reel_gizli",
        donem_yil_satiri_gosterilsin(2023, kyc, reel_map={2023: 1.0}) is False,
    )
    check(
        "hepsi_2023_marker_kalir",
        donem_yil_satiri_gosterilsin(2023, kyc, marker_by_iso={"2024-01-01": 10.0}) is True,
    )


def test_n_ay_ve_z_bagimsiz():
    kyc = {
        "durum": "pasif",
        "kapanis_tarihi": date(2025, 2, 1),
        "kapanis_sonrasi_borc_ay": 1,
        "rent_start_date": date(2024, 11, 1),
        "kira_artis_tarihi": date(2024, 11, 1),
    }
    son = gorunum_son_borc_ayi(kyc)
    check("n1_son_2025_03", son == date(2025, 3, 1), str(son))
    # 2025 donem Kas25..Eki26 bas > son → gizli
    check("n1_2025_gizli", donem_yil_satiri_gosterilsin(2025, kyc) is False)
    # Z bayragi bu kurali degistirmez
    os.environ["REEL_TUFE_ZINCIR"] = "1"
    try:
        check("z1_ayni_gizli", donem_yil_satiri_gosterilsin(2025, kyc) is False)
        check(
            "z1_reel_gizli",
            donem_yil_satiri_gosterilsin(2025, kyc, reel_map={2025: 10.0}) is False,
        )
        check(
            "z1_panel_kalir",
            donem_yil_satiri_gosterilsin(
                2025, kyc, panel_by_iso={"2025-12-01": {"tahsil": 10.0}}
            )
            is True,
        )
    finally:
        os.environ.pop("REEL_TUFE_ZINCIR", None)
    os.environ.pop("TAHSILAT_DONEM_GORUNUM", None)
    check("donem_gorunum_etkisiz", donem_yil_satiri_gosterilsin(2025, kyc) is False)


def test_fe_kaynak_ayna():
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")
    idx = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    for name in (
        "girisHepsiSozlesmeYiliSonBorcAyi",
        "girisPasifGorunumSonBorcAyInclusive",
        "girisPasifDonemYilDonemBas",
        "girisPasifDonemYilindaVeriVarMi",
        "girisPasifDonemYilSatiriGosterilsinMi",
    ):
        check(f"tjs_{name}", f"function {name}(" in tjs)
        check(f"idx_{name}", f"function {name}(" in idx)
    check(
        "tjs_filtre_cagrisi",
        "girisPasifDonemYilSatiriGosterilsinMi(y)" in tjs,
    )
    check(
        "idx_filtre_cagrisi",
        "girisPasifDonemYilSatiriGosterilsinMi(y)" in idx,
    )
    # literal ters boli+n yok (yeni helper bolgesinde)
    i = tjs.find("function girisHepsiSozlesmeYiliSonBorcAyi")
    j = tjs.find("function girisAylikSatirYilSelectDoldur")
    chunk = tjs[i:j] if i >= 0 and j > i else ""
    check("literal_bs_n_yok", (chr(92) + "n") not in chunk)


def test_fe_node_kural():
    """Saf JS kurali: 377 tipi gizli; veri varsa gorunur; aktif degismez; mutasyon."""
    tjs = (ROOT / "_t.js").read_text(encoding="utf-8")

    def _extract(name: str) -> str:
        m = re.search(r"function " + re.escape(name) + r"\([^)]*\)\s*\{", tjs)
        if not m:
            raise RuntimeError("missing " + name)
        i = m.start()
        depth = 0
        j = m.end() - 1
        while j < len(tjs):
            if tjs[j] == "{":
                depth += 1
            elif tjs[j] == "}":
                depth -= 1
                if depth == 0:
                    return tjs[i : j + 1]
            j += 1
        raise RuntimeError("unclosed " + name)

    names = [
        "girisHepsiSozlesmeYiliSonBorcAyi",
        "girisPasifGorunumSonBorcAyInclusive",
        "girisPasifDonemYilDonemBas",
        "girisPasifDonemYilindaVeriVarMi",
        "girisPasifDonemYilSatiriGosterilsinMi",
        "kapanisSonrasiBorcAyDegeri",
    ]
    body = "\n".join(_extract(n) for n in names)
    harness = r"""
function parseTarihStr(s) {
  var m = String(s||'').match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return new Date(+m[1], +m[2]-1, +m[3]);
}
var document = { getElementById: function(){ return null; } };
var window = { KAPANIS_N0_KAPANIS_AYI_HARIC: 1 };
function girisFormMusteriIptalMi(){ return false; }
function girisTarihiFormdanISO(){ return '2024-06-12'; }
function formKapanisSonrasiBorcAy(){ return 0; }
""" + body + r"""
var fails = [];
function assert(n, c){ if(!c) fails.push(n); }
// aktif
assert('aktif', girisPasifDonemYilSatiriGosterilsinMi(2026, {
  durum:'aktif', kapanis_tarihi:'2026-03-12', kapanis_sonrasi_borc_ay:0,
  giris_iso:'2024-06-12', basStr:'2024-06-12', artisAy:6
}) === true);
// 377 bos 2026 gizli
assert('377_gizli', girisPasifDonemYilSatiriGosterilsinMi(2026, {
  durum:'pasif', kapanis_tarihi:'2026-03-12', kapanis_sonrasi_borc_ay:0,
  giris_iso:'2024-06-12', basStr:'2024-06-12', artisAy:6
}) === false);
// 2025 kalir (donem Haz25 <= son Sub26)
assert('2025_kalir', girisPasifDonemYilSatiriGosterilsinMi(2025, {
  durum:'pasif', kapanis_tarihi:'2026-03-12', kapanis_sonrasi_borc_ay:0,
  giris_iso:'2024-06-12', basStr:'2024-06-12', artisAy:6
}) === true);
// yalniz reel → gizli
assert('reel_only_gizli', girisPasifDonemYilSatiriGosterilsinMi(2026, {
  durum:'pasif', kapanis_tarihi:'2026-03-12', kapanis_sonrasi_borc_ay:0,
  giris_iso:'2024-06-12', basStr:'2024-06-12', artisAy:6,
  reelMap:{2026: 100}
}) === false);
// marker/tahsil istisna
assert('veri_kalir', girisPasifDonemYilSatiriGosterilsinMi(2026, {
  durum:'pasif', kapanis_tarihi:'2026-03-12', kapanis_sonrasi_borc_ay:0,
  giris_iso:'2024-06-12', basStr:'2024-06-12', artisAy:6,
  markerSet:{has:function(k){ return String(k).indexOf('2026-7')>=0 || String(k).indexOf('2026-07')>=0; }}
}) === true);
// Hepsi donem sonu
assert('hepsi_gizli', girisPasifDonemYilSatiriGosterilsinMi(2023, {
  durum:'pasif', kapanis_tarihi:'2023-08-15', kapanis_sonrasi_borc_ay:null,
  giris_iso:'2022-12-01', basStr:'2022-12-01', artisAy:12
}) === false);
if (fails.length) { console.log('FAIL', fails.join(',')); process.exit(1); }
console.log('fe_node_ok');
"""
    fd, path = tempfile.mkstemp(suffix=".js", prefix="pasif_donem_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(harness)
        r = subprocess.run(
            ["node", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        check("fe_node_kural", r.returncode == 0, (r.stderr or r.stdout or "")[:200])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    # Mutasyon: kural hep true → 377 gizli assert FAIL etmeli
    mut = harness.replace(
        "if (bas.getTime() <= son.getTime()) return true;\n    if (girisPasifDonemYilindaVeriVarMi(donemYil, opts)) return true;\n    return false;",
        "if (bas.getTime() <= son.getTime()) return true;\n    if (girisPasifDonemYilindaVeriVarMi(donemYil, opts)) return true;\n    return true;",
        1,
    )
    fd2, path2 = tempfile.mkstemp(suffix=".js", prefix="pasif_donem_mut_")
    try:
        with os.fdopen(fd2, "w", encoding="utf-8", newline="\n") as f:
            f.write(mut)
        r2 = subprocess.run(
            ["node", path2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        check("mutasyon_377_fail", r2.returncode != 0, "mutasyon gecmemeliydi")
    finally:
        try:
            os.unlink(path2)
        except OSError:
            pass


def test_donem_bas_formulu():
    check("bas_haz", donem_baslangic(2026, 6) == date(2026, 6, 1))
    check("bas_ara", donem_baslangic(2023, 12) == date(2023, 12, 1))


def main():
    test_aktif_degismez()
    test_pasif_bos_donem_gizli()
    test_veri_varsa_gorunur()
    test_hepsi_donem_sonu_bayrak_kapali()
    test_n_ay_ve_z_bagimsiz()
    test_fe_kaynak_ayna()
    test_fe_node_kural()
    test_donem_bas_formulu()
    if FAILS:
        print("FAIL", len(FAILS))
        for a in FAILS:
            print(" -", a)
        raise SystemExit(1)
    print("test_pasif_kapanis_sonrasi_donem_satiri PASS")


if __name__ == "__main__":
    main()
