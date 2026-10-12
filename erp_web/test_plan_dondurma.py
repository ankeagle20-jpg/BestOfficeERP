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
    check("parmak dolu", dondurma_parmak([bir]).startswith("s1;"))


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
    check("eski onbellek D yok dondurma yok", _aylik_grid_dondurma_ayni("", "none"))
    check("eski onbellek dondurma varsa yenile", not _aylik_grid_dondurma_ayni("", "s1;1:2024-08:1:2024-08"))


def _kyc():
    return {
        "sozlesme_tarihi": date(2023, 8, 1),
        "sozlesme_bitis": date(2026, 8, 1),
        "kira_artis_tarihi": date(2023, 8, 1),
        "aylik_kira": 1000,
        "kdv_oran": 20,
        "kira_nakit": False,
        "durum": "aktif",
    }


def _hucre(payload, yil, ay):
    for a in payload.get("aylar") or []:
        if int(a.get("yil")) == yil and int(a.get("ay")) == ay:
            return a
    return None


def test_on_kontrol_ab():
    """Parmak izi ve plan+dondurma birlikte. plan_degistir dondurma satirina dokunmaz."""
    from sozlesme_plan_dondurma import DONDURMA_FP_SURUM

    bellek = Bellek()
    an = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    check("parmak bos none", dondurma_parmak([]) == "none")
    bir = dondurma_ekle(
        bellek.calistir, bellek.oku, musteri_id=867, baslangic_ay="2024-08-01", ay_sayisi=1,
        dondurulan_aylar=["2024-08"], atlanan_aylar=[], olusturan="test", simdi=an,
    )
    dolu = dondurma_parmak(dondurma_liste(bellek.oku, musteri_id=867))
    check("olustur D degisir", dolu.startswith(DONDURMA_FP_SURUM + ";") and dolu != "none")
    check("planli plansiz ayni D", dolu == dondurma_parmak([bir]))
    bellek.calistir("SELECT 1 FROM sozlesme_plan_degisiklik WHERE musteri_id = %s", (867,))
    sonra = dondurma_parmak(dondurma_liste(bellek.oku, musteri_id=867))
    check("plan degistir D korunur", sonra == dolu and len(dondurma_liste(bellek.oku, musteri_id=867)) == 1)
    dondurma_iptal(bellek.calistir, bellek.oku, dondurma_id=bir["id"], iptal_eden="test", simdi=an)
    check("iptal D none", dondurma_parmak(dondurma_liste(bellek.oku, musteri_id=867)) == "none")

    from routes import giris_routes as gr

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}}
    plan = [{"gecerlilik_ay": date(2024, 11, 1), "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    eski_kume = gr._dondurma_ay_kumesi
    gr._dondurma_ay_kumesi = lambda mid: set()
    try:
        zincir = gr._aylik_grid_compute(867, _kyc(), tufe, planlar=plan)
        gr._dondurma_ay_kumesi = lambda mid: {"2024-08", "2025-01"} if int(mid) == 867 else set()
        dolu_p = gr._aylik_grid_compute(867, _kyc(), tufe, planlar=plan)
        dis = _hucre(dolu_p, 2024, 8)
        ic = _hucre(dolu_p, 2025, 1)
        sonraki = _hucre(dolu_p, 2025, 2)
        ham_son = _hucre(zincir, 2025, 2)
        ham_dis = _hucre(zincir, 2024, 8)
        check("plan disi dondurma 0", dis and float(dis.get("tutar_kdv_dahil") or 0) == 0 and float(ham_dis.get("tutar_kdv_dahil") or 0) > 0)
        check("plan ici dondurma 0", ic and float(ic.get("tutar_kdv_dahil") or 0) == 0 and float(_hucre(zincir, 2025, 1).get("tutar_kdv_dahil") or 0) > 0)
        check("sonraki ay plan zinciri", sonraki and ham_son and float(sonraki.get("tutar_kdv_dahil")) == float(ham_son.get("tutar_kdv_dahil")) and float(sonraki.get("tutar_kdv_dahil")) > 0)
        check("dondurma plan_var acmaz", dolu_p.get("plan_var") is True)
    finally:
        gr._dondurma_ay_kumesi = eski_kume


def test_tuketiciler():
    from routes import giris_routes as gr

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}}
    kyc = _kyc()
    eski = gr._dondurma_ay_kumesi
    gr._dondurma_ay_kumesi = lambda mid: set()
    try:
        a = gr._aylik_grid_compute(867, kyc, tufe, planlar=[], plan_katmani=False)
        b = gr._aylik_grid_compute(867, kyc, tufe, planlar=[])
        ka = [(x.get("yil"), x.get("ay"), x.get("tutar_kdv_dahil")) for x in a["aylar"]]
        kb = [(x.get("yil"), x.get("ay"), x.get("tutar_kdv_dahil")) for x in b["aylar"]]
        check("dondurmasiz grid birebir", ka == kb and "dondurma_var" not in b)
        tam = gr._aylik_remaining_brut_by_iso_from_kyc(kyc, tufe)
        gr._dondurma_ay_kumesi = lambda mid: {"2024-08"} if int(mid) == 867 else set()
        kirp = gr._aylik_remaining_brut_by_iso_from_kyc(kyc, tufe, 867)
        check("fifo sifir aya dagitmaz", "2024-08-01" in tam and "2024-08-01" not in kirp)
        check("fifo diger aylar ayni", {k: v for k, v in tam.items() if k != "2024-08-01"} == kirp)
        grid = gr._aylik_grid_compute(867, kyc, tufe, planlar=[])
        h = _hucre(grid, 2024, 8)
        h2 = _hucre(grid, 2024, 9)
        ham = _hucre(a, 2024, 9)
        check("grid dondurulan ay 0", h and float(h.get("tutar_kdv_dahil") or 0) == 0 and float(h.get("kalan_tutar_kdv") or 0) == 0)
        check("grid sonraki ay zincir", h2 and ham and float(h2.get("tutar_kdv_dahil")) == float(ham.get("tutar_kdv_dahil")))
        gr._plan_kutu_sifirla()
        gr._plan_kutu().setdefault("dondurma", {})[11] = {"2024-12"}
        gr._plan_kutu()["dondurma_tablo"] = True
        eski_paket = gr._plan_paket_yukle
        gr._plan_paket_yukle = lambda mids: {int(i): {"planlar": [], "faturali": [], "belge": {}} for i in mids}
        try:
            g = gr.geciken_satir_guncelleri(
                [{"musteri_id": 11, "guncel": 1320.0}], date(2024, 12, 15), {11: kyc}, tufe, {}
            )
            check("wa geciken dondurma 0", g == [0], g)
        finally:
            gr._plan_paket_yukle = eski_paket
            gr._plan_kutu_sifirla()
        eski_ek = {
            "fetch_one": gr.fetch_one,
            "fetch_all": gr.fetch_all,
            "kyc": gr._musteri_kyc_grup_for_aylik_grid,
            "tufe": gr._tufe_map_by_year_month_cached,
            "tah": gr._ekstre_tahsil_rows_for_musteri,
            "meta": gr._aylik_tutar_fatura_meta_by_iso,
            "reel": gr._reel_ay_key_tutar_map_musteri,
            "grid": gr._read_aylik_grid_cache_payload,
        }
        gr.fetch_one = lambda sql, params=None: {}
        gr.fetch_all = lambda sql, params=None: []
        gr._musteri_kyc_grup_for_aylik_grid = lambda mid: kyc
        gr._tufe_map_by_year_month_cached = lambda: tufe
        gr._ekstre_tahsil_rows_for_musteri = lambda mid: []
        gr._aylik_tutar_fatura_meta_by_iso = lambda mid: {}
        gr._reel_ay_key_tutar_map_musteri = lambda *a, **k: {}
        gr._read_aylik_grid_cache_payload = lambda mid: {"aylar": []}
        try:
            hareket = gr._cari_ekstre_hareketler(867, date(2024, 8, 1), date(2024, 9, 30), 1000, use_reel_cells=False)
            kira = {h.get("tarih"): h.get("borc") for h in hareket if h.get("tur") == "Kira"}
            check("ekstre dondurulan ay 0", kira.get("2024-08-01") == 0, kira.get("2024-08-01"))
            check("ekstre sonraki ay durur", kira.get("2024-09-01") not in (None, 0), kira.get("2024-09-01"))
        finally:
            for ad, fn in eski_ek.items():
                setattr(gr, {"fetch_one": "fetch_one", "fetch_all": "fetch_all", "kyc": "_musteri_kyc_grup_for_aylik_grid", "tufe": "_tufe_map_by_year_month_cached", "tah": "_ekstre_tahsil_rows_for_musteri", "meta": "_aylik_tutar_fatura_meta_by_iso", "reel": "_reel_ay_key_tutar_map_musteri", "grid": "_read_aylik_grid_cache_payload"}[ad], fn)
    finally:
        gr._dondurma_ay_kumesi = eski

    from gib_plan_tutar import yukle_plan_gib_satir

    gr._dondurma_ay_kumesi = lambda mid: {"2024-08"}
    try:
        ov = yukle_plan_gib_satir({"musteri_id": 867, "notlar": "x|AYLIK_TUTAR|2024-08-01|"})
        check("gib dondurma 0 belge", ov and ov.get("dondurma") is True and ov.get("toplam") == 0 and ov.get("birim_fiyat") == 0)
        check("gib mevcut satir yazilmaz", ov.get("dondurma") is True)
    finally:
        gr._dondurma_ay_kumesi = eski


def test_borclandir_dondurma():
    import copy
    from routes import giris_routes as gr
    import test_borclandir_mukerrer as tb

    eski = gr._dondurma_ay_kumesi
    gr._dondurma_ay_kumesi = lambda mid: {"2026-05", "2026-06"}
    try:
        satirlar = copy.deepcopy(tb.ISTEMCI) + [{"yil": 2026, "ay": 6, "tutar_kdv_dahil": 900.0}]
        j, ins, sil, _sen = tb.kos(True, satirlar=satirlar)
        check("borc dondurma atlanan", "2026-05" in (j.get("atlanan_aylar") or []) and "2026-06" in (j.get("atlanan_aylar") or []), j.get("atlanan_aylar"))
        check("borc dondurma yazilmadi", tb.ay_say(ins, "2026-05-01") == 0 and tb.ay_say(ins, "2026-06-01") == 0)
        check("borc dondurma uyari", "Dondurulan aylar atlandı" in (j.get("mesaj") or ""), j.get("mesaj"))
        check("borc kismi gib ay silinmedi", tb.silme_say(sil, "2026-05-01") == 0 and tb.silme_say(sil, "2026-06-01") == 0)
    finally:
        gr._dondurma_ay_kumesi = eski

    eski_one = gr.fetch_one
    def fetch_iptal(sql, params=None):
        if "tahsil_marker_var" in str(sql):
            return {"id": 9, "fatura_no": "X", "ettn": "", "durum": "iptal", "yon": "giden", "notlar": "iptal|AYLIK_TUTAR|2026-07-01|", "tahsil_marker_var": False}
        if "FROM customers" in str(sql):
            return {"id": 4242, "name": "Test"}
        if "FROM musteri_kyc" in str(sql):
            return None
        if "WHERE fatura_no" in str(sql):
            return {"id": 77}
        return None
    # iptal kaydi «var» sayilmaz: gecerli helper ve zaten-var dali
    from plan_fatura_kilit import kayit_gecerli
    check("iptal gecerli sayilmaz", kayit_gecerli({"durum": "iptal", "yon": "giden", "notlar": "", "ettn": ""}) is False)
    gr.fetch_one = eski_one


def main():
    test_plansiz_diger_aylar_ayni()
    test_tufe_sonraki_ay()
    test_planli_ustte_sifir()
    test_kilit_atlanir()
    test_iptal_geri()
    test_depo()
    test_api()
    test_parmak_izi()
    test_on_kontrol_ab()
    test_tuketiciler()
    test_borclandir_dondurma()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("plan_dondurma ok")


if __name__ == "__main__":
    main()
