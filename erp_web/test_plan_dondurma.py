# -*- coding: utf-8 -*-
"""Plan dondurma: hesap, depo, uç. Canlı veritabanına bağlanmaz."""
import os
from datetime import date, datetime, timezone

from flask import Flask

from sozlesme_plan import ay_bazli_tutarlar
from sozlesme_plan_dondurma import (
    acik_dondurulan_aylar,
    dondurma_onizleme,
    dondurma_parmak,
    dondurma_uygula,
)
from sozlesme_plan_dondurma_depo import (
    DondurmaCakisma,
    SQL_INDEX,
    SQL_TABLO,
    dondurma_ekle,
    dondurma_iptal,
    dondurma_liste,
)
import sozlesme_plan_api as plan_api
import sozlesme_plan_dondurma_api as da
from routes.giris_routes import (
    AYLIK_GRID_COMPUTE_REV,
    _aylik_grid_dondurma_ayni,
    _aylik_grid_freshness_parse_parts,
)

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _zincir(**ek):
    z = {
        "sozlesme_tarihi": date(2023, 8, 1),
        "artis_tarihi": date(2023, 8, 1),
        "ay_sayisi": 36,
        "aylik_net": 1000,
        "kdv_oran": 20,
        "kira_nakit": False,
        "tufe": {2024: {8: 10}, 2025: {8: 5}},
    }
    z.update(ek)
    return z


def _ay(satirlar, anahtar):
    for s in satirlar:
        if s["ay"] == anahtar:
            return s
    raise KeyError(anahtar)


class Bellek:
    def __init__(self):
        self.sql = []
        self.satirlar = []
        self.seq = 1

    def calistir(self, sql, params=None):
        self.sql.append(sql)
        bas = sql.lstrip()
        if bas.startswith("CREATE") or bas.startswith("SET LOCAL"):
            return None
        if bas.startswith("INSERT"):
            mid, ay, n, dondur, atlanan, kim, zaman = params
            row = {
                "id": self.seq,
                "musteri_id": mid,
                "baslangic_ay": ay,
                "ay_sayisi": n,
                "dondurulan_aylar": dondur,
                "atlanan_aylar": atlanan,
                "sozlesme_uzat": False,
                "eski_sozlesme_bitis": None,
                "eski_kira_suresi_ay": None,
                "olusturan": kim,
                "created_at": zaman,
                "iptal_at": None,
                "iptal_eden": None,
            }
            self.seq += 1
            self.satirlar.append(row)
            return dict(row)
        if bas.startswith("UPDATE"):
            zaman, kim, pid = params
            for s in self.satirlar:
                if s["id"] == pid and s["iptal_at"] is None:
                    s["iptal_at"] = zaman
                    s["iptal_eden"] = kim
                    return dict(s)
            return None
        return None

    def oku(self, sql, params=None):
        self.sql.append(sql)
        if "iptal_at IS NULL" in sql and "ORDER BY baslangic_ay" in sql and "%s OR" not in sql:
            mid = params[0]
            return [dict(s) for s in self.satirlar if s["musteri_id"] == mid and s["iptal_at"] is None]
        if "WHERE id = %s" in sql:
            pid = params[0]
            for s in self.satirlar:
                if s["id"] == pid:
                    return dict(s)
            return None
        if "ORDER BY baslangic_ay" in sql:
            mid, iptaller = params
            rows = []
            for s in self.satirlar:
                if s["musteri_id"] != mid:
                    continue
                if not iptaller and s["iptal_at"] is not None:
                    continue
                rows.append(dict(s))
            return rows
        return None


def test_plansiz_diger_aylar_ayni():
    z = _zincir()
    ham = ay_bazli_tutarlar(z)
    oniz = dondurma_onizleme(z, None, "2024-08", 2, {})
    check("iki ay dondurulacak", oniz["dondurulan_aylar"] == ["2024-08", "2024-09"])
    sonra = dondurma_uygula(ham, oniz["dondurulan_aylar"])
    check("plan_var yok", all("plan_var" not in s for s in sonra))
    for a, b in zip(ham, sonra):
        if a["ay"] in ("2024-08", "2024-09"):
            check(a["ay"] + " sifir", b["brut"] == 0 and b["net"] == 0 and b["kaynak"] == "dondurma")
        else:
            check(a["ay"] + " birebir", a == b)


def test_tufe_sonraki_ay():
    z = _zincir()
    ham = ay_bazli_tutarlar(z)
    sonra = dondurma_uygula(ham, ["2024-08"])
    check("tufe ayi sifir", _ay(sonra, "2024-08")["kaynak"] == "dondurma" and _ay(sonra, "2024-08")["brut"] == 0)
    check("sonraki ay zincir", _ay(sonra, "2024-09") == _ay(ham, "2024-09"))
    check("sonraki yil zincir", _ay(sonra, "2025-08") == _ay(ham, "2025-08"))


def test_planli_ustte_sifir():
    plan = [{"gecerlilik_ay": date(2024, 11, 1), "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    z = _zincir(reel={2024: 1500})
    ham = ay_bazli_tutarlar(z, plan)
    sonra = dondurma_uygula(ham, ["2025-01"])
    check("plan ayi sifir", _ay(sonra, "2025-01")["brut"] == 0 and _ay(sonra, "2025-01")["kaynak"] == "dondurma")
    check("kalan plan ayi", _ay(sonra, "2025-02") == _ay(ham, "2025-02") and _ay(ham, "2025-02")["kaynak"] == "plan")
    check("plan oncesi reel", _ay(sonra, "2024-10") == _ay(ham, "2024-10"))


def test_kilit_atlanir():
    sinif = {
        "2024-09": {"sinif": "K2", "neden": "tahsilat var (ay haritası)"},
        "2024-10": {"sinif": "K1", "neden": "ettn dolu"},
        "2024-11": {"sinif": "K0", "neden": "belirsiz"},
    }
    oniz = dondurma_onizleme(_zincir(), None, "2024-08", 4, sinif)
    by = {s["ay"]: s for s in oniz["aylar"]}
    check("agustos dondur", by["2024-08"]["durum"] == "dondurulacak")
    check("k2 kismi atla", by["2024-09"]["durum"] == "atlanacak" and by["2024-09"]["sinif"] == "K2" and "tahsilat" in by["2024-09"]["neden"])
    check("k1 atla", by["2024-10"]["durum"] == "atlanacak" and by["2024-10"]["sinif"] == "K1")
    check("k0 atla", by["2024-11"]["durum"] == "atlanacak" and by["2024-11"]["sinif"] == "K0")
    check("kilit dondurulan listede yok", oniz["dondurulan_aylar"] == ["2024-08"])
    ham = ay_bazli_tutarlar(_zincir())
    sonra = dondurma_uygula(ham, oniz["dondurulan_aylar"])
    check("k2 ay tutari durur", _ay(sonra, "2024-09") == _ay(ham, "2024-09"))


def test_iptal_geri():
    ham = list(ay_bazli_tutarlar(_zincir()))
    kayit = {"id": 1, "iptal_at": None, "baslangic_ay": date(2024, 8, 1), "ay_sayisi": 1, "dondurulan_aylar": ["2024-08"]}
    sonra = dondurma_uygula(ham, acik_dondurulan_aylar([kayit]))
    check("dondurmus", _ay(sonra, "2024-08")["brut"] == 0)
    kayit["iptal_at"] = "2026-10-12"
    geri = dondurma_uygula(ham, acik_dondurulan_aylar([kayit]))
    check("iptal birebir", geri == [dict(s) for s in ham])


def test_depo():
    bellek = Bellek()
    an = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    nsql = len(bellek.sql)
    dondurma_liste(bellek.oku, musteri_id=7, iptaller=True)
    check("liste create yok", not any(s.lstrip().startswith("CREATE") for s in bellek.sql[nsql:]))
    bir = dondurma_ekle(
        bellek.calistir,
        bellek.oku,
        musteri_id=7,
        baslangic_ay="2024-08-15",
        ay_sayisi=3,
        dondurulan_aylar=["2024-08", "2024-09"],
        atlanan_aylar=[{"ay": "2024-10", "sinif": "K2", "neden": "tahsilat"}],
        olusturan="test-kullanici",
        simdi=an,
    )
    check("ensure yazmada", SQL_TABLO in bellek.sql and SQL_INDEX in bellek.sql)
    check("denetim", bir["olusturan"] == "test-kullanici" and bir["baslangic_ay"] == date(2024, 8, 1) and bir["created_at"] == an)
    check("kismi tekil", "WHERE iptal_at IS NULL" in SQL_INDEX)
    cakisti = False
    try:
        dondurma_ekle(
            bellek.calistir,
            bellek.oku,
            musteri_id=7,
            baslangic_ay="2024-10-01",
            ay_sayisi=2,
            dondurulan_aylar=["2024-10"],
            atlanan_aylar=[],
            olusturan="test-kullanici",
            simdi=an,
        )
    except DondurmaCakisma:
        cakisti = True
    check("ortusen ret", cakisti and len(dondurma_liste(bellek.oku, musteri_id=7)) == 1)
    iptal = dondurma_iptal(bellek.calistir, bellek.oku, dondurma_id=bir["id"], iptal_eden="test-iptal", simdi=an)
    check("iptal eden", iptal["iptal_eden"] == "test-iptal" and iptal["iptal_at"] == an)
    check("liste gizler", dondurma_liste(bellek.oku, musteri_id=7) == [])
    check("parmak none", dondurma_parmak([]) == "none")
    check("parmak dolu", dondurma_parmak([bir]).startswith("s0;"))


def _yanit(yanit):
    if isinstance(yanit, tuple):
        resp, kod = yanit
        return resp.get_json(), kod
    return yanit.get_json(), yanit.status_code


def test_api():
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "867"
    bellek = Bellek()
    da.plan_tx = lambda fn: fn(bellek.calistir, bellek.oku, bellek.oku)
    da._kim = lambda: "test-kullanici"
    da._sema = lambda: None
    da._siniflar = lambda mid: {"2024-09": {"sinif": "K2", "neden": "tahsilat var (ay haritası)"}}
    plan_api._onizleme_zinciri = lambda mid, kyc: (_zincir(), [], None)
    da._musteri_ve_kyc = lambda mid: (
        {"id": int(mid), "durum": "aktif"},
        {"sozlesme_tarihi": date(2023, 8, 1), "aylik_kira": 1000, "kdv_oran": 20},
    )
    app = Flask(__name__)
    with app.test_request_context("/x", method="POST", json={"baslangic_ay": "2024-08", "ay_sayisi": 2, "musteri_id": 1}):
        govde, kod = _yanit(da.dondurma_ekle_yanit(867))
    check("mid uyusmaz", kod == 403)
    with app.test_request_context("/x", method="POST", json={"baslangic_ay": "2024-08", "ay_sayisi": 2}):
        govde, kod = _yanit(da.dondurma_onizleme_yanit(867))
    check("onizleme k2", kod == 200 and govde["dondurulan_aylar"] == ["2024-08"] and govde["atlanan_aylar"][0]["sinif"] == "K2")
    with app.test_request_context("/x", method="POST", json={"baslangic_ay": "2024-08", "ay_sayisi": 2}):
        govde, kod = _yanit(da.dondurma_ekle_yanit(867))
    check("kaydet", kod == 200 and govde["dondurma"]["olusturan"] == "test-kullanici" and govde["dondurma"]["dondurulan_aylar"] == ["2024-08"])
    da._musteri_ve_kyc = lambda mid: ({"id": int(mid), "durum": "pasif"}, {"sozlesme_tarihi": date(2023, 8, 1), "aylik_kira": 1000})
    once = len(bellek.satirlar)
    with app.test_request_context("/x", method="POST", json={"baslangic_ay": "2025-01", "ay_sayisi": 1}):
        govde, kod = _yanit(da.dondurma_ekle_yanit(867))
    check("pasif ret", kod == 400 and len(bellek.satirlar) == once)
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "7"
    with app.test_request_context("/x", method="POST", json={"baslangic_ay": "2025-01", "ay_sayisi": 1}):
        _govde, kod = _yanit(da.dondurma_ekle_yanit(867))
    check("kapi 867 disi", kod == 404)
    os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
    os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)


def test_parmak_izi():
    eski = _aylik_grid_freshness_parse_parts("T=a|R=b|P=c|K=kyc")
    yeni = _aylik_grid_freshness_parse_parts("T=a|R=b|P=c|K=kyc|D=none")
    dolu = _aylik_grid_freshness_parse_parts("T=a|R=b|P=c|K=kyc|D=s0;1:2024-08:1:2024-08")
    check("eski K ayni", eski["K"] == "kyc" and eski["D"] == "")
    check("yeni D ayrilir", yeni["K"] == "kyc" and yeni["D"] == "none")
    check("eski none sayilir", _aylik_grid_dondurma_ayni("", "none"))
    check("dondurma farki", not _aylik_grid_dondurma_ayni("none", dolu["D"]))
    check("compute_rev 31", AYLIK_GRID_COMPUTE_REV == 31)


def main():
    test_plansiz_diger_aylar_ayni()
    test_tufe_sonraki_ay()
    test_planli_ustte_sifir()
    test_kilit_atlanir()
    test_iptal_geri()
    test_depo()
    test_api()
    test_parmak_izi()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("plan_dondurma ok")


if __name__ == "__main__":
    main()
