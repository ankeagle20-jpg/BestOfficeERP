# -*- coding: utf-8 -*-
"""Liste sorgu yer tutucuları ve sentetik küme. Canlı veriye yazmaz."""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if ek and not ok else ""))
    if not ok:
        FAILS.append(name)


def _ys(sql):
    return len(re.findall(r"(?<!%)%s", sql or ""))


TABLO = [
    {"id": 1, "durum": "aktif", "gun": "2026-10-01", "oda": 205, "grup2": ["Ofis"], "hizmet": "Sanal"},
    {"id": 2, "durum": "aktif", "gun": "2026-10-01", "oda": 205, "grup2": ["Ofis"], "hizmet": "Masa"},
    {"id": 3, "durum": "aktif", "gun": "2026-10-05", "oda": None, "grup2": ["Depo"], "hizmet": "Sanal"},
    {"id": 4, "durum": "pasif", "gun": "2026-10-01", "oda": 210, "grup2": ["Ofis"], "hizmet": ""},
    {"id": 5, "durum": "pasif", "gun": "2026-03-01", "oda": None, "grup2": [], "hizmet": "Sanal"},
    {"id": 6, "durum": "aktif", "gun": "2026-10-08", "oda": 205, "grup2": ["Ofis"], "hizmet": "Sanal"},
    {"id": 7, "durum": "aktif", "gun": "2026-10-08", "oda": 205, "grup2": ["Ofis"], "hizmet": "Sanal"},
    {"id": 8, "durum": "aktif", "gun": "2026-10-09", "oda": None, "grup2": ["Ofis"], "hizmet": "Sanal"},
]


def sec(rows, **f):
    """WHERE ile aynı küme: kapsam, ay, oda, arama id, grup2, hizmet, durum, sonra çift gün."""
    kapsam = f.get("kapsam") or "tum"
    ay = f.get("ay") or []
    oda = f.get("oda")
    ids = f.get("ids")
    grup2 = [str(x) for x in (f.get("grup2") or [])]
    hizmet = [str(x) for x in (f.get("hizmet") or [])]
    durum = (f.get("durum") or "").strip().lower()
    out = []
    for r in rows:
        if kapsam == "aktif" and r["durum"] != "aktif":
            continue
        if kapsam == "pasif" and r["durum"] != "pasif":
            continue
        if ay and int(r["gun"][5:7]) not in ay:
            continue
        if oda == "hepsi" and r["oda"] is None:
            continue
        if oda == "yok" and r["oda"] is not None:
            continue
        if isinstance(oda, int) and r["oda"] != oda:
            continue
        if ids is not None and r["id"] not in ids:
            continue
        if grup2 and not any(g in r["grup2"] for g in grup2):
            continue
        if hizmet:
            ok_h = False
            for h in hizmet:
                if h == "__FR_HIZMET_BOS":
                    ok_h = r["hizmet"] in ("", "-")
                elif r["hizmet"] == h:
                    ok_h = True
                if ok_h:
                    break
            if not ok_h:
                continue
        if durum == "aktif" and r["durum"] != "aktif":
            continue
        if durum == "pasif" and r["durum"] != "pasif":
            continue
        out.append(r)
    if f.get("cift"):
        from collections import Counter

        say = Counter(r["gun"] for r in out)
        out = [r for r in out if 2 <= say[r["gun"]] <= 3]
    return [r["id"] for r in out]


def sayfa(ids, page, size):
    start = (page - 1) * size
    return ids[start : start + size]


def test_kume():
    check("suzgec yok tum", sec(TABLO) == [1, 2, 3, 4, 5, 6, 7, 8])
    check("yalniz ay", sec(TABLO, ay=[10]) == [1, 2, 3, 4, 6, 7, 8])
    check("yalniz aktif", sec(TABLO, kapsam="aktif") == [1, 2, 3, 6, 7, 8])
    check("yalniz pasif", sec(TABLO, kapsam="pasif") == [4, 5])
    check("yalniz oda", sec(TABLO, oda=205) == [1, 2, 6, 7])
    check("yalniz grup2", sec(TABLO, grup2=["Ofis"]) == [1, 2, 4, 6, 7, 8])
    check("yalniz hizmet", sec(TABLO, hizmet=["Sanal"]) == [1, 3, 5, 6, 7, 8])
    cift_ay_grup = sec(TABLO, cift=True, ay=[10], grup2=["Ofis"])
    check("cift ay grup2", cift_ay_grup == [1, 2, 4, 6, 7], str(cift_ay_grup))
    cift_hiz_oda = sec(TABLO, cift=True, hizmet=["Sanal"], oda=205)
    check("cift hizmet oda", cift_hiz_oda == [6, 7], str(cift_hiz_oda))
    ara = sec(TABLO, ids={1, 3, 6}, grup2=["Ofis"], durum="aktif")
    check("arama grup2 durum", ara == [1, 6], str(ara))
    for ad, kume in (
        ("cift ay grup2", cift_ay_grup),
        ("cift hizmet oda", cift_hiz_oda),
        ("arama grup2 durum", ara),
    ):
        p1 = sayfa(kume, 1, 2)
        check(
            ad + " sayim sayfa ayni kume",
            len(kume) == len(set(kume)) and set(p1).issubset(kume) and p1 == kume[:2],
            str(p1),
        )


def _kur(**opt):
    from routes.faturalar_routes import (
        _firma_ozet_liste_sorgusu_kur,
        _firma_ozet_oda_sql,
        _firma_ozet_sunucu_suzgec_sql,
        _fatura_rapor_giris_yili_sql,
        _fatura_rapor_musteri_where_sql,
    )

    ay = list(opt.get("ay") or [])
    ay_sql, ay_p = "", []
    if ay:
        ay_sql = " AND (EXTRACT(MONTH FROM (c.created_at)::date))::int IN (" + ", ".join(["%s"] * len(ay)) + ")"
        ay_p = list(ay)
    ho_sql, ho_p = _firma_ozet_oda_sql("" if opt.get("oda") is None else str(opt.get("oda")), excel_mode=bool(opt.get("excel")))
    yil_sql, yil_p = _fatura_rapor_giris_yili_sql(opt.get("yillar"))
    ids = opt.get("ids")
    ar_sql, ar_p = "", []
    if ids is not None:
        ar_sql = " AND c.id = ANY(%s::bigint[])"
        ar_p = [list(ids)]
    duzenli = opt.get("duzenli")
    mk_sql, mk_p = "", []
    if duzenli:
        mk_sql = "AND COALESCE(NULLIF(LOWER(TRIM(mk.duzenli_fatura)), ''), 'duzenle') = %s"
        mk_p = [duzenli]
    sz_sql, sz_p = _firma_ozet_sunucu_suzgec_sql(
        grup2=opt.get("grup2") or [],
        hizmet=opt.get("hizmet") or [],
        durum=opt.get("durum") or "",
    )
    kapsam = opt.get("kapsam") or ""
    where = _fatura_rapor_musteri_where_sql(kapsam == "pasif", kapsam == "tum", kapsam == "aktif")
    kur = _firma_ozet_liste_sorgusu_kur(
        musteri_where=where,
        mk_df_sql=mk_sql,
        mk_df_params=mk_p,
        giri_ay_sql=ay_sql,
        giri_ay_params=ay_p,
        ho_sql=ho_sql,
        ho_params=ho_p,
        giri_yil_sql=yil_sql,
        giri_yil_params=yil_p,
        arama_sql=ar_sql,
        arama_params=ar_p,
        suzgec_sql=sz_sql,
        suzgec_params=sz_p,
        cift_olanlar=bool(opt.get("cift")),
        hizli=True,
        firma_guncel_sql="0",
    )
    kur["where"] = where
    kur["suzgec_params"] = sz_p
    return kur


def test_yer_tutucu():
    from routes.faturalar_routes import (
        _firma_ozet_build_rows_firma_sql,
        _firma_ozet_sql_paging_queries,
        _fatura_rapor_musteri_where_sql,
    )

    for ad, args in (
        ("tum", (False, True, False)),
        ("aktif", (False, False, True)),
        ("pasif", (True, False, False)),
        ("varsayilan", (False, False, False)),
    ):
        w = _fatura_rapor_musteri_where_sql(*args)
        check("kapsam yer tutucusuz " + ad, _ys(w) == 0, w[:80])

    bos = _kur()
    duz = _firma_ozet_build_rows_firma_sql(
        musteri_where=bos["where"],
        mk_df_sql="",
        giri_ay_sql="",
        ho_sql="",
        arama_sql="",
        firma_guncel_sql="0",
        hizli=True,
    )
    check("suzgec yokken sql ayni", bos["rows_sql"] == duz and bos["rows_params"] == [] and bos["yil_params"] == [])

    def dogrula(ad, kur):
        no = re.sub(r"\s+ORDER\s+BY\s+2\s*$", "", kur["rows_sql"].rstrip(), flags=re.I)
        totals, page = _firma_ozet_sql_paging_queries(no, False)
        ok = (
            _ys(kur["rows_sql"]) == len(kur["rows_params"])
            and _ys(totals) == len(kur["rows_params"])
            and _ys(page) == len(kur["rows_params"]) + 2
            and no.strip() in totals
            and no.strip() in page
            and "LIMIT %s OFFSET %s" in page
            and "LIMIT %s" not in totals
        )
        check(ad + " yer tutucu", ok, "sql=%s params=%s" % (_ys(kur["rows_sql"]), len(kur["rows_params"])))
        return ok

    yalnizlar = [
        ("yalniz ay", dict(ay=[10])),
        ("yalniz oda", dict(oda=205)),
        ("yalniz oda hepsi", dict(oda="hepsi")),
        ("yalniz arama", dict(ids=[1, 6])),
        ("yalniz grup2", dict(grup2=["Ofis"])),
        ("yalniz hizmet", dict(hizmet=["Sanal"])),
        ("yalniz durum", dict(durum="aktif")),
        ("yalniz cift", dict(cift=True)),
        ("yalniz kapsam tum", dict(kapsam="tum")),
        ("yalniz kapsam aktif", dict(kapsam="aktif")),
    ]
    for ad, opt in yalnizlar:
        dogrula(ad, _kur(**opt))

    k1 = _kur(cift=True, ay=[10], grup2=["Ofis"])
    dogrula("cift ay grup2", k1)
    check(
        "cift ay grup2 sira",
        k1["rows_params"] == [10, 10, ["Ofis"], ["Ofis"], ["Ofis"], ["Ofis"]],
        str(k1["rows_params"]),
    )
    k2 = _kur(cift=True, hizmet=["Sanal"], oda=205)
    dogrula("cift hizmet oda", k2)
    check(
        "cift hizmet oda sira",
        k2["rows_params"] == [205, 205, ["Sanal"], ["Sanal"], ["Sanal"], ["Sanal"]],
        str(k2["rows_params"]),
    )
    k3 = _kur(ids=[1, 3, 6], grup2=["Ofis"], durum="aktif")
    dogrula("arama grup2 durum", k3)
    check(
        "arama grup2 durum sira",
        k3["rows_params"] == [[1, 3, 6], ["Ofis"], ["Ofis"]],
        str(k3["rows_params"]),
    )
    tam = _kur(
        cift=True,
        ay=[10],
        oda=205,
        ids=[1, 2],
        grup2=["Ofis"],
        hizmet=["Sanal"],
        durum="pasif",
        kapsam="tum",
        yillar=[2026],
        duzenli="fatura_aylik",
    )
    dogrula("tum kombinasyon", tam)
    beklenen = [
        "fatura_aylik",
        10,
        205,
        2026,
        [1, 2],
        "fatura_aylik",
        10,
        205,
        [1, 2],
        ["Ofis"],
        ["Ofis"],
        ["Sanal"],
        ["Sanal"],
        ["Ofis"],
        ["Ofis"],
        ["Sanal"],
        ["Sanal"],
    ]
    check("tum kombinasyon sira", tam["rows_params"] == beklenen, str(tam["rows_params"]))
    check(
        "yil listesi yer tutucu",
        len(tam["yil_params"]) == 4 + _ys(tam["cift_sql"]),
        "yil_params=%s cift=%s" % (len(tam["yil_params"]), _ys(tam["cift_sql"])),
    )


def test_onbellek():
    from flask import Flask

    from routes.faturalar_routes import (
        _firma_ozet_resp_cache_get,
        _firma_ozet_resp_cache_key,
        _firma_ozet_resp_cache_set,
    )

    app = Flask(__name__)

    def anahtar(q):
        with app.test_request_context("/faturalar/api/fatura-rapor?" + q):
            return _firma_ozet_resp_cache_key()

    k_tum = anahtar("gorunum=firma_ozet&page=1&page_size=50&tum_musteriler=1")
    k_aktif = anahtar("gorunum=firma_ozet&page=1&page_size=50&sadece_aktif=1")
    k_pasif = anahtar("gorunum=firma_ozet&page=1&page_size=50&pasifleri_dahil=1")
    k_grup = anahtar("gorunum=firma_ozet&page=1&page_size=50&tum_musteriler=1&grup2=Ofis")
    k_durum = anahtar("gorunum=firma_ozet&page=1&page_size=50&tum_musteriler=1&durum=pasif")
    check("kapsam anahtarlari ayri", len({k_tum, k_aktif, k_pasif, k_grup, k_durum}) == 5)
    _firma_ozet_resp_cache_set(k_tum, {"marker": "tum", "total_count": 58})
    _firma_ozet_resp_cache_set(k_aktif, {"marker": "aktif", "total_count": 13})
    check("tum onbellek kendi sayfasi", (_firma_ozet_resp_cache_get(k_tum) or {}).get("marker") == "tum")
    check("aktif tum sayfasini donmez", (_firma_ozet_resp_cache_get(k_aktif) or {}).get("marker") == "aktif")
    check("grup2 tum sayfasini donmez", _firma_ozet_resp_cache_get(k_grup) is None)
    check("durum tum sayfasini donmez", _firma_ozet_resp_cache_get(k_durum) is None)
    check("pasif tum sayfasini donmez", _firma_ozet_resp_cache_get(k_pasif) is None)


if __name__ == "__main__":
    test_kume()
    test_yer_tutucu()
    test_onbellek()
    if FAILS:
        print("FAIL", ", ".join(FAILS))
        sys.exit(1)
    print("fatura_rapor_param ok")
