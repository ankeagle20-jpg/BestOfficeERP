# -*- coding: utf-8 -*-
"""Plan değişikliği: saf hesap + depo. Canlı veritabanına bağlanmaz."""
from datetime import date, datetime, timezone
from pathlib import Path

from sozlesme_plan import ay_bazli_tutarlar
from sozlesme_plan_depo import (
    PlanCakisma,
    PlanYok,
    SQL_INDEX,
    SQL_TABLO,
    ensure_sozlesme_plan_degisiklik,
    ensure_sozlesme_plan_degisiklik_semada,
    plan_ekle,
    plan_iptal,
    plan_liste,
)

ROOT = Path(__file__).resolve().parent
FAILS = []


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def _ay(satirlar, anahtar):
    for s in satirlar:
        if s["ay"] == anahtar:
            return s
    raise KeyError(anahtar)


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


def test_plansiz_kart_tufe():
    satirlar = ay_bazli_tutarlar(_zincir())
    a = _ay(satirlar, "2023-08")
    b = _ay(satirlar, "2024-07")
    c = _ay(satirlar, "2024-08")
    d = _ay(satirlar, "2025-08")
    check("ilk blok brut", a["brut"] == 1200 and a["net"] == 1000 and a["kdv"] == 200 and a["banka"] == 1000 and a["nakit"] == 0 and a["kaynak"] == "kart")
    check("ilk blok son ay ayni", b["brut"] == 1200 and b["pencere"] == 0)
    check("ikinci blok tufe", c["brut"] == 1320 and c["net"] == 1100 and c["kdv"] == 220 and c["pencere"] == 1)
    check("ucuncu blok tufe", d["net"] == 1155 and d["brut"] == 1386 and d["kdv"] == 231)


def test_reel_ilk_yil_yok_sonraki_pencere():
    satirlar = ay_bazli_tutarlar(_zincir(reel={2023: 9999, 2024: 1500}))
    check("ilk yil reel yok", _ay(satirlar, "2023-08")["brut"] == 1200 and _ay(satirlar, "2024-07")["brut"] == 1200)
    check("reel penceresi basi", _ay(satirlar, "2024-08")["brut"] == 1500 and _ay(satirlar, "2024-08")["kaynak"] == "reel")
    check("reel penceresi sonu", _ay(satirlar, "2025-07")["brut"] == 1500)
    check("reel bitince kart zinciri", _ay(satirlar, "2025-08")["brut"] == 1386 and _ay(satirlar, "2025-08")["kaynak"] == "kart")


def test_plan_ayni_pencerede_reeli_ezer():
    plan = [{"gecerlilik_ay": date(2024, 11, 15), "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    satirlar = ay_bazli_tutarlar(_zincir(reel={2024: 1500}), plan)
    check("plandan once reel", _ay(satirlar, "2024-10")["brut"] == 1500 and _ay(satirlar, "2024-10")["kaynak"] == "reel")
    p = _ay(satirlar, "2024-11")
    check("gecerlilik ayi plan", p["brut"] == 2400 and p["net"] == 2000 and p["kdv"] == 400 and p["kaynak"] == "plan")
    check("pencere sonu plan", _ay(satirlar, "2025-07")["brut"] == 2400 and _ay(satirlar, "2025-07")["kaynak"] == "plan")
    n = _ay(satirlar, "2025-08")
    check("sonraki yildonumu yeni netten tufe", n["net"] == 2100 and n["brut"] == 2520 and n["kdv"] == 420 and n["kaynak"] == "plan_tufe")


def test_sonraki_yil_reel_kazanir():
    plan = [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    satirlar = ay_bazli_tutarlar(_zincir(reel={2024: 1500, 2025: 1800}), plan)
    check("plan penceresi reel degil", _ay(satirlar, "2024-12")["kaynak"] == "plan")
    bas = _ay(satirlar, "2025-08")
    son = _ay(satirlar, "2026-07")
    check("sonraki yil tum pencere reel", bas["brut"] == 1800 and bas["kaynak"] == "reel" and son["brut"] == 1800 and son["kaynak"] == "reel")


def test_coklu_plan():
    planlar = [
        {"gecerlilik_ay": "2025-03-01", "yeni_net": 2500, "kdv_oran": 20, "yeni_brut": 3000},
        {"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400},
    ]
    satirlar = ay_bazli_tutarlar(_zincir(), planlar)
    check("birinci plan", _ay(satirlar, "2024-11")["brut"] == 2400 and _ay(satirlar, "2025-02")["brut"] == 2400)
    check("ikinci plan ayni pencere", _ay(satirlar, "2025-03")["brut"] == 3000 and _ay(satirlar, "2025-07")["net"] == 2500)
    check("yildonumu son plandan", _ay(satirlar, "2025-08")["net"] == 2625 and _ay(satirlar, "2025-08")["brut"] == 3150)


def test_hibrit_ve_varsayilan_pay():
    kart = _zincir(nakit_tutar=400, banka_tutar=600)
    plansiz = ay_bazli_tutarlar(kart)
    a = _ay(plansiz, "2023-08")
    b = _ay(plansiz, "2024-08")
    check("hibrit kart", a["brut"] == 1120 and a["nakit"] == 400 and a["banka"] == 600 and a["kdv"] == 120)
    check("hibrit tufe pay", b["net"] == 1100 and b["nakit"] == 440 and b["banka"] == 660 and b["brut"] == 1232 and b["kdv"] == 132)
    plan = [{
        "gecerlilik_ay": "2024-11-01",
        "yeni_net": 2000,
        "kdv_oran": 20,
        "yeni_brut": 2320,
        "nakit_tutar": 400,
        "banka_tutar": 1600,
    }]
    h = _ay(ay_bazli_tutarlar(kart, plan), "2024-11")
    check("plan hibrit", h["brut"] == 2320 and h["nakit"] == 400 and h["banka"] == 1600 and h["kdv"] == 320)
    varsayilan = _ay(ay_bazli_tutarlar(kart, [{
        "gecerlilik_ay": "2024-11-01",
        "yeni_net": 2000,
        "kdv_oran": 20,
        "yeni_brut": 2240,
    }]), "2024-11")
    check("plan payi kart orani", varsayilan["nakit"] == 800 and varsayilan["banka"] == 1200 and varsayilan["brut"] == 2240)


def test_nakit_kart():
    satirlar = ay_bazli_tutarlar(_zincir(kira_nakit=True))
    a = _ay(satirlar, "2023-08")
    b = _ay(satirlar, "2024-08")
    check("nakit kdv yok", a["brut"] == 1000 and a["kdv"] == 0 and a["nakit"] == 1000 and a["banka"] == 0)
    check("nakit tufe", b["brut"] == 1100 and b["kdv"] == 0 and b["nakit"] == 1100)


def test_ilk_yil_icinde_plan():
    plan = [{"gecerlilik_ay": "2023-11-01", "yeni_net": 800, "kdv_oran": 20, "yeni_brut": 960}]
    satirlar = ay_bazli_tutarlar(_zincir(reel={2023: 5000, 2024: 1500}), plan)
    check("ilk yil plan oncesi kart", _ay(satirlar, "2023-10")["brut"] == 1200)
    check("ilk yil plan", _ay(satirlar, "2023-11")["brut"] == 960 and _ay(satirlar, "2024-07")["brut"] == 960)
    check("ilk yil reel hala yok", _ay(satirlar, "2023-12")["kaynak"] == "plan")
    check("sonraki yil reel plani ezer", _ay(satirlar, "2024-08")["brut"] == 1500 and _ay(satirlar, "2024-08")["kaynak"] == "reel")


def test_fatura_ayi_sabit():
    plan = [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    satirlar = ay_bazli_tutarlar(_zincir(reel={2024: 1500}, faturali_aylar=["2024-11-01", "2025-08"]), plan)
    kilit = _ay(satirlar, "2024-11")
    komsu = _ay(satirlar, "2024-12")
    sonraki = _ay(satirlar, "2025-08")
    check("faturali ay eski brut", kilit["brut"] == 1500 and kilit["kaynak"] == "fatura")
    check("komsu ay plan", komsu["brut"] == 2400 and komsu["kaynak"] == "plan")
    check("faturali sonraki yil kart brut", sonraki["brut"] == 1386 and sonraki["kaynak"] == "fatura")


def test_iptal_plan_yok_sayilir():
    plan = [{
        "gecerlilik_ay": "2024-11-01",
        "yeni_net": 2000,
        "kdv_oran": 20,
        "yeni_brut": 2400,
        "iptal_at": "2026-01-01",
    }]
    a = ay_bazli_tutarlar(_zincir(), plan)
    b = ay_bazli_tutarlar(_zincir())
    check("iptal plan plansiz ile ayni", [(x["ay"], x["brut"], x["kaynak"]) for x in a] == [(x["ay"], x["brut"], x["kaynak"]) for x in b])


def test_grid_cekirdegi_ile_ayni():
    from routes.giris_routes import (
        _aylik_grid_contract_core,
        _aylik_grid_single_month_kdv_from_core,
        _reel_ay_key_tutar_map_db_flat_only,
    )

    def kyc(**ek):
        row = {
            "sozlesme_tarihi": date(2023, 8, 1),
            "sozlesme_bitis": date(2026, 8, 1),
            "kira_artis_tarihi": date(2023, 8, 1),
            "aylik_kira": 1000,
            "kdv_oran": 20,
            "kira_nakit": False,
            "durum": "aktif",
        }
        row.update(ek)
        return row

    def karsilastir(ad, zincir, row, tufe):
        core = _aylik_grid_contract_core(row, tufe)
        satirlar = ay_bazli_tutarlar(zincir)
        for s in satirlar:
            if s["kaynak"] != "kart":
                continue
            beklenen = _aylik_grid_single_month_kdv_from_core(core, s["yil"], s["ay_no"])
            if round(beklenen, 2) != s["brut"]:
                return False
        return True

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}}
    check(
        "plansiz brut grid cekirdegi",
        karsilastir("duz", _zincir(), kyc(), tufe),
    )
    check(
        "tufe yedegi grid cekirdegi",
        karsilastir(
            "yedek",
            _zincir(tufe={2024: {8: 10.0}}),
            kyc(),
            {2024: {8: 10.0}},
        ),
    )
    hibrit_z = _zincir(nakit_tutar=400, banka_tutar=600)
    check(
        "hibrit brut grid cekirdegi",
        karsilastir(
            "hibrit",
            hibrit_z,
            kyc(kira_nakit_tutar=400, kira_banka_tutar=600),
            tufe,
        ),
    )
    check(
        "nakit brut grid cekirdegi",
        karsilastir("nakit", _zincir(kira_nakit=True), kyc(kira_nakit=True), tufe),
    )
    ham = {2023: 9999, 2024: 1500}
    manual = {k: v for k, v in ham.items() if int(k) != 2023}
    reel = _reel_ay_key_tutar_map_db_flat_only(date(2023, 8, 1), 8, 1, manual)
    satirlar = ay_bazli_tutarlar(_zincir(reel={2023: 9999, 2024: 1500}))
    ayni = True
    for s in satirlar:
        anahtar = f"{s['yil']}-{s['ay_no']}"
        if anahtar in reel:
            if s["brut"] != round(float(reel[anahtar]), 2) or s["kaynak"] != "reel":
                ayni = False
        elif s["kaynak"] == "reel":
            ayni = False
    check("reel ortmesi grid anahtarlari", ayni and "2023-8" not in reel and reel.get("2024-8") == 1500)


class Bellek:
    """Yerel sözlük. Ağ ve canlı şema yok."""

    def __init__(self):
        self.sql = []
        self.satirlar = []
        self.seq = 1

    def calistir(self, sql, params=None):
        self.sql.append(sql)
        bas = sql.lstrip()
        if bas.startswith("CREATE TABLE") or bas.startswith("CREATE UNIQUE INDEX") or bas.startswith("SET LOCAL"):
            return None
        if bas.startswith("INSERT"):
            mid, ay, net, kdv, brut, nakit, banka, kim, zaman = params
            for s in self.satirlar:
                if s["musteri_id"] == mid and s["gecerlilik_ay"] == ay and s["iptal_at"] is None:
                    raise PlanCakisma("index")
            row = {
                "id": self.seq,
                "musteri_id": mid,
                "gecerlilik_ay": ay,
                "yeni_net": net,
                "kdv_oran": kdv,
                "yeni_brut": brut,
                "nakit_tutar": nakit,
                "banka_tutar": banka,
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
        bas = sql.lstrip()
        if "iptal_at IS NULL" in sql and "gecerlilik_ay = %s" in sql:
            mid, ay = params
            for s in self.satirlar:
                if s["musteri_id"] == mid and s["gecerlilik_ay"] == ay and s["iptal_at"] is None:
                    return dict(s)
            return None
        if "WHERE id = %s" in sql and "SET iptal_at" not in sql:
            pid = params[0]
            for s in self.satirlar:
                if s["id"] == pid:
                    return dict(s)
            return None
        if "ORDER BY gecerlilik_ay" in sql:
            mid, iptaller = params
            rows = []
            for s in self.satirlar:
                if s["musteri_id"] != mid:
                    continue
                if not iptaller and s["iptal_at"] is not None:
                    continue
                rows.append(dict(s))
            rows.sort(key=lambda r: (r["gecerlilik_ay"], r["id"]))
            return rows
        return None


def test_depo():
    bellek = Bellek()
    ensure_sozlesme_plan_degisiklik(bellek.calistir)
    ensure_sozlesme_plan_degisiklik(bellek.calistir)
    check("ensure if not exists", SQL_TABLO in bellek.sql and SQL_INDEX in bellek.sql)
    check("ensure ikinci kez ayni", bellek.sql.count(SQL_TABLO) == 2 and bellek.sql.count(SQL_INDEX) == 2)
    check("kismi tekil index", "WHERE iptal_at IS NULL" in SQL_INDEX and "musteri_id, gecerlilik_ay" in SQL_INDEX)
    an = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    bir = plan_ekle(
        bellek.calistir,
        bellek.oku,
        musteri_id=1,
        gecerlilik_ay="2024-11-18",
        yeni_net=2000,
        kdv_oran=20,
        yeni_brut=2400,
        nakit_tutar=400,
        banka_tutar=1600,
        olusturan="test-kullanici",
        simdi=an,
    )
    check("ay basi ve denetim", bir["gecerlilik_ay"] == date(2024, 11, 1) and bir["olusturan"] == "test-kullanici" and bir["created_at"] == an)
    cakisma = False
    try:
        plan_ekle(
            bellek.calistir,
            bellek.oku,
            musteri_id=1,
            gecerlilik_ay=date(2024, 11, 1),
            yeni_net=2100,
            kdv_oran=20,
            yeni_brut=2520,
            olusturan="test-kullanici",
            simdi=an,
        )
    except PlanCakisma:
        cakisma = True
    check("acik cift kayit yok", cakisma)
    iki = plan_ekle(
        bellek.calistir,
        bellek.oku,
        musteri_id=1,
        gecerlilik_ay="2025-08-01",
        yeni_net=2200,
        kdv_oran=20,
        yeni_brut=2640,
        olusturan="test-kullanici",
        simdi=an,
    )
    check("farkli ay ikinci plan", iki["id"] != bir["id"] and len(plan_liste(bellek.oku, musteri_id=1)) == 2)
    iptal_an = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
    iptal = plan_iptal(bellek.calistir, bellek.oku, plan_id=bir["id"], iptal_eden="test-iptal", simdi=iptal_an)
    check("iptal denetim", iptal["iptal_at"] == iptal_an and iptal["iptal_eden"] == "test-iptal")
    check("liste iptali gizler", len(plan_liste(bellek.oku, musteri_id=1)) == 1)
    check("liste iptali gosterir", len(plan_liste(bellek.oku, musteri_id=1, iptaller=True)) == 2)
    tekrar = plan_ekle(
        bellek.calistir,
        bellek.oku,
        musteri_id=1,
        gecerlilik_ay="2024-11-01",
        yeni_net=2300,
        kdv_oran=20,
        yeni_brut=2760,
        olusturan="test-kullanici",
        simdi=an,
    )
    check("iptal sonrasi ayni ay", tekrar["id"] != bir["id"])
    yok = False
    try:
        plan_iptal(bellek.calistir, bellek.oku, plan_id=bir["id"], iptal_eden="test-iptal", simdi=iptal_an)
    except PlanYok:
        yok = True
    check("ikinci iptal yok", yok)
    blob = "\n".join(bellek.sql).lower()
    check(
        "kira reel cache durum yazilmaz",
        "musteri_reel_donem_tutar" not in blob
        and "aylik_kira" not in blob
        and "musteri_aylik_grid_cache" not in blob
        and "update customers" not in blob,
    )
    sema_hata = False
    try:
        ensure_sozlesme_plan_degisiklik_semada(bellek.calistir, "public;drop")
    except ValueError:
        sema_hata = True
    check("sema adi kilitli", sema_hata)
    ensure_sozlesme_plan_degisiklik_semada(bellek.calistir, "public")
    check("public search_path", any(s.startswith("SET LOCAL search_path TO public,") for s in bellek.sql))


def test_reel_sonrasi_tufe():
    """Kilitli reel yılından sonraki yıl, plan netinden değil o brütten TÜFE ile gider."""
    plan = [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    tufe = {2024: {8: 10}, 2025: {8: 5}, 2026: {8: 5}}
    satirlar = ay_bazli_tutarlar(
        _zincir(ay_sayisi=48, tufe=tufe, reel={2024: 1500, 2025: 1800}),
        plan,
    )
    check("kilit yili reel", _ay(satirlar, "2025-08")["brut"] == 1800 and _ay(satirlar, "2026-07")["kaynak"] == "reel")
    son = _ay(satirlar, "2026-08")
    check(
        "reel sonrasi tufe",
        son["brut"] == 1890 and son["kaynak"] == "reel_tufe" and son["brut"] != 2646,
    )


def test_fatura_belge_uyarisi():
    plan = [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    satirlar = ay_bazli_tutarlar(
        _zincir(
            reel={2024: 1500},
            faturali_aylar=["2024-11-01"],
            fatura_belge={"2024-11": 999},
        ),
        plan,
    )
    kilit = _ay(satirlar, "2024-11")
    uyari = [u for u in satirlar.uyarilar if u["ay"] == "2024-11"]
    check("belge tutari tutari degistirmez", kilit["brut"] == 1500 and kilit["kaynak"] == "fatura")
    check("belge farki yalniz uyari", len(uyari) == 1 and uyari[0]["zincir_brut"] == 1500 and uyari[0]["belge_brut"] == 999)


def test_artis_ayi_farkli_grid():
    """Artış ayı sözleşme ayından farklıysa plansız hesap bugünkü grid ile aynı mı."""
    from routes.giris_routes import (
        _aylik_grid_contract_core,
        _aylik_grid_single_month_kdv_from_core,
        _reel_ay_key_tutar_map_db_flat_only,
    )

    tufe = {2024: {2: 10.0}, 2025: {2: 5.0}}
    zincir = _zincir(artis_tarihi=date(2023, 2, 15), tufe=tufe, reel={2024: 1500})
    row = {
        "sozlesme_tarihi": date(2023, 8, 1),
        "sozlesme_bitis": date(2026, 8, 1),
        "kira_artis_tarihi": date(2023, 2, 15),
        "aylik_kira": 1000,
        "kdv_oran": 20,
        "kira_nakit": False,
        "durum": "aktif",
    }
    core = _aylik_grid_contract_core(row, tufe)
    manual = {2024: 1500}
    reel = _reel_ay_key_tutar_map_db_flat_only(date(2023, 8, 1), 2, 15, manual)
    satirlar = ay_bazli_tutarlar(zincir)
    ayni = True
    for s in satirlar:
        anahtar = f"{s['yil']}-{s['ay_no']}"
        if anahtar in reel:
            if s["brut"] != round(float(reel[anahtar]), 2):
                ayni = False
        else:
            beklenen = _aylik_grid_single_month_kdv_from_core(core, s["yil"], s["ay_no"])
            if round(float(beklenen or 0), 2) != s["brut"]:
                ayni = False
    check("artis ayi farkli plansiz grid ile ayni", ayni)
    return ayni


def _payload_ozet(payload):
    return [
        (
            a["yil"],
            a["ay"],
            a["tutar_kdv_dahil"],
            a["brut_tutar_kdv"],
            a["kalan_tutar_kdv"],
            a["tahsil_edildi"],
        )
        for a in payload["aylar"]
    ]


def _kyc_grid(**ek):
    row = {
        "sozlesme_tarihi": date(2023, 8, 1),
        "sozlesme_bitis": date(2030, 8, 1),
        "kira_artis_tarihi": date(2023, 8, 1),
        "aylik_kira": 1000,
        "kdv_oran": 20,
        "kira_nakit": False,
        "durum": "aktif",
    }
    row.update(ek)
    return row


def test_plansiz_grid_birebir():
    from routes import giris_routes as gr

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}}
    kartlar = [
        ("duz", _kyc_grid(), tufe),
        ("tufe", _kyc_grid(), {2024: {8: 10.0}}),
        ("hibrit", _kyc_grid(kira_nakit_tutar=400, kira_banka_tutar=600), tufe),
        ("nakit", _kyc_grid(kira_nakit=True), tufe),
        ("ilk_yil", _kyc_grid(sozlesme_tarihi=date(2026, 3, 1), sozlesme_bitis=date(2027, 3, 1), kira_artis_tarihi=date(2026, 3, 1)), tufe),
    ]
    for ad, kyc, tm in kartlar:
        eski = gr._aylik_grid_compute(1, kyc, tm, plan_katmani=False)
        yeni = gr._aylik_grid_compute(1, kyc, tm, planlar=[])
        check("plansiz " + ad, eski is not None and _payload_ozet(eski) == _payload_ozet(yeni) and "plan_uyarilar" not in yeni)
    eski_r = gr._aylik_grid_compute(1, _kyc_grid(), tufe, plan_katmani=False)
    kopya = [dict(a) for a in eski_r["aylar"]]
    eski_r["aylar"][0]["tutar_kdv_dahil"] = 1500
    gr._plan_katmani_payloada(eski_r, _kyc_grid(), tufe, planlar=[])
    check("bos plan reel hucresine dokunmaz", eski_r["aylar"][0]["tutar_kdv_dahil"] == 1500)
    eski_r["aylar"] = kopya


def _reel_boya(payload, bas, artis_ay, artis_gun, manual):
    from routes.giris_routes import _reel_ay_key_tutar_map_db_flat_only

    reel = _reel_ay_key_tutar_map_db_flat_only(bas, artis_ay, artis_gun, manual)
    for a in payload["aylar"]:
        k = f"{a['yil']}-{a['ay']}"
        if k in reel:
            a["tutar_kdv_dahil"] = round(float(reel[k]), 2)
            a["brut_tutar_kdv"] = a["tutar_kdv_dahil"]
    return payload


def test_planli_grid_katmani():
    from routes import giris_routes as gr

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}, 2026: {8: 5.0}}
    kyc = _kyc_grid()
    plan = [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    eski = _reel_boya(
        gr._aylik_grid_compute(2, kyc, tufe, plan_katmani=False),
        date(2023, 8, 1),
        8,
        1,
        {2024: 1500},
    )
    gr._plan_katmani_payloada(eski, kyc, tufe, planlar=plan, reel={2024: 1500})
    hucre = {(a["yil"], a["ay"]): a["tutar_kdv_dahil"] for a in eski["aylar"]}
    check("plandan once eski", hucre[(2024, 10)] == 1500)
    check("gecerlilik ve sonrasi yeni", hucre[(2024, 11)] == 2400 and hucre[(2025, 7)] == 2400)
    check("yildonumu yeni netten", hucre[(2025, 8)] == 2520)
    kilitli = gr._aylik_grid_compute(2, kyc, tufe, plan_katmani=False)
    gr._plan_katmani_payloada(
        kilitli, kyc, tufe, planlar=plan, reel={2024: 1500, 2025: 1800}
    )
    hk = {(a["yil"], a["ay"]): a["tutar_kdv_dahil"] for a in kilitli["aylar"]}
    check("ileri reel kilidi", hk[(2025, 8)] == 1800 and hk[(2026, 8)] == 1890)
    coklu = gr._aylik_grid_compute(2, kyc, tufe, plan_katmani=False)
    gr._plan_katmani_payloada(
        coklu,
        kyc,
        tufe,
        planlar=[
            {"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400},
            {"gecerlilik_ay": "2025-03-01", "yeni_net": 2500, "kdv_oran": 20, "yeni_brut": 3000},
        ],
    )
    hc = {(a["yil"], a["ay"]): a["tutar_kdv_dahil"] for a in coklu["aylar"]}
    check("coklu plan grid", hc[(2024, 11)] == 2400 and hc[(2025, 3)] == 3000 and hc[(2025, 8)] == 3150)
    hibrit = _kyc_grid(kira_nakit_tutar=400, kira_banka_tutar=600)
    ph = gr._aylik_grid_compute(2, hibrit, tufe, plan_katmani=False)
    gr._plan_katmani_payloada(
        ph,
        hibrit,
        tufe,
        planlar=[{
            "gecerlilik_ay": "2024-11-01",
            "yeni_net": 2000,
            "kdv_oran": 20,
            "yeni_brut": 2320,
            "nakit_tutar": 400,
            "banka_tutar": 1600,
        }],
    )
    check("hibrit plan grid", {(a["yil"], a["ay"]): a["tutar_kdv_dahil"] for a in ph["aylar"]}[(2024, 11)] == 2320)
    ilk = gr._aylik_grid_compute(2, kyc, tufe, plan_katmani=False)
    gr._plan_katmani_payloada(
        ilk,
        kyc,
        tufe,
        planlar=[{"gecerlilik_ay": "2023-11-01", "yeni_net": 800, "kdv_oran": 20, "yeni_brut": 960}],
        reel={2023: 9999, 2024: 1500},
    )
    hi = {(a["yil"], a["ay"]): a["tutar_kdv_dahil"] for a in ilk["aylar"]}
    check("ilk yil ici plan", hi[(2023, 10)] == 1200 and hi[(2023, 11)] == 960 and hi[(2024, 8)] == 1500)
    fat = _reel_boya(
        gr._aylik_grid_compute(2, kyc, tufe, plan_katmani=False),
        date(2023, 8, 1),
        8,
        1,
        {2024: 1500},
    )
    gr._plan_katmani_payloada(
        fat,
        kyc,
        tufe,
        planlar=plan,
        reel={2024: 1500},
        faturali_aylar=["2024-11"],
        fatura_belge={"2024-11": 999},
    )
    hf = {(a["yil"], a["ay"]): a["tutar_kdv_dahil"] for a in fat["aylar"]}
    uy = [u for u in (fat.get("plan_uyarilar") or []) if u.get("ay") == "2024-11"]
    check("faturali ay sabit", hf[(2024, 11)] == 1500 and hf[(2024, 12)] == 2400 and len(uy) == 1)


def test_cache_yalniz_o_kart():
    from routes import giris_routes as gr

    gorulen = []

    def _build(mid, tufe_map=None, kyc_row=None, manual_reel_by_year=None, **ek):
        gorulen.append(int(mid))
        return {"musteri_id": int(mid), "aylar": []}

    def _persist(mid, payload):
        gorulen.append(int(mid))
        return payload

    eski_b, eski_p = gr._build_aylik_grid_cache_payload, gr._persist_grid_cache_with_panel
    eski_skip, eski_shadow = gr._aylik_grid_cache_skip_refresh_enabled, gr._aylik_grid_freshness_shadow_log_enabled
    gr._build_aylik_grid_cache_payload = _build
    gr._persist_grid_cache_with_panel = _persist
    gr._aylik_grid_cache_skip_refresh_enabled = lambda: False
    gr._aylik_grid_freshness_shadow_log_enabled = lambda: False
    try:
        gr._upsert_aylik_grid_cache(7)
    finally:
        gr._build_aylik_grid_cache_payload = eski_b
        gr._persist_grid_cache_with_panel = eski_p
        gr._aylik_grid_cache_skip_refresh_enabled = eski_skip
        gr._aylik_grid_freshness_shadow_log_enabled = eski_shadow
    check("cache damgasi yalniz o kart", gorulen == [7, 7])


def test_plan_okuma_toplu_ve_hata():
    import time
    from routes import giris_routes as gr

    gr._plan_kutu_sifirla()
    sayac = {"one": 0, "all": 0}

    def fetch_one(sql, params=None):
        sayac["one"] += 1
        if "to_regclass" in str(sql):
            return {"t": None}
        raise AssertionError("fazla tekli sorgu")

    def fetch_all(sql, params=None):
        sayac["all"] += 1
        raise AssertionError("tablo yokken satir sorgusu")

    eski_one, eski_all = gr.fetch_one, gr.fetch_all
    gr.fetch_one, gr.fetch_all = fetch_one, fetch_all
    try:
        mids = list(range(1, 201))
        t0 = time.perf_counter()
        bir = gr._plan_paket_yukle(mids)
        iki = gr._plan_paket_yukle(mids)
        sure_yok = (time.perf_counter() - t0) * 1000
    finally:
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
        gr._plan_kutu_sifirla()
    check("tablo yok tek kontrol", sayac["one"] == 1 and sayac["all"] == 0 and all(not p["planlar"] for p in bir.values()) and iki)
    print("perf tablo_yok_ms", round(sure_yok, 2), "sorgu", sayac["one"])

    gr._plan_kutu_sifirla()
    sayac2 = {"one": 0, "all": 0}

    def fetch_one_var(sql, params=None):
        sayac2["one"] += 1
        if "to_regclass" in str(sql):
            return {"t": "sozlesme_plan_degisiklik"}
        raise AssertionError(sql)

    def fetch_all_bos(sql, params=None):
        sayac2["all"] += 1
        if "ANY" not in str(sql):
            raise AssertionError("tekil plan sorgusu")
        return []

    gr.fetch_one, gr.fetch_all = fetch_one_var, fetch_all_bos
    try:
        t0 = time.perf_counter()
        gr._plan_paket_yukle(list(range(1, 201)))
        ilk_one, ilk_all = sayac2["one"], sayac2["all"]
        gr._plan_paket_yukle(list(range(1, 201)))
        sure_var = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        for _ in range(200):
            ay_bazli_tutarlar(_zincir())
        sure_hesap = (time.perf_counter() - t1) * 1000
    finally:
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
        gr._plan_kutu_sifirla()
    check(
        "toplu plan tek sorgu",
        ilk_one == 1 and ilk_all == 1 and sayac2["one"] == 1 and sayac2["all"] == 1,
    )
    print("perf tablo_var_ms", round(sure_var, 2), "hesap_200_ms", round(sure_hesap, 2))

    gr._plan_kutu_sifirla()

    def fetch_pat(sql, params=None):
        raise RuntimeError("okuma")

    gr.fetch_one = fetch_pat
    gr.fetch_all = fetch_pat
    try:
        kyc = _kyc_grid()
        tm = {2024: {8: 10.0}}
        eski = gr._aylik_grid_compute(3, kyc, tm, plan_katmani=False)
        yeni = gr._aylik_grid_compute(3, kyc, tm)
    finally:
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
        gr._plan_kutu_sifirla()
    check("okuma hatasi grid eski", _payload_ozet(eski) == _payload_ozet(yeni))


def _karsilastir_kart(ad, kyc, zincir, planlar, artis_ay, artis_gun, manual):
    from ay_fifo import acik_ay_dagit, gorunen_borc_biles
    from sozlesme_plan import plan_uygulanacak_aylar
    from routes.giris_routes import (
        _aylik_grid_compute,
        _ekstre_ay_borcu,
        _plan_katmani_payloada,
        _reel_ay_key_tutar_map_db_flat_only,
    )

    tufe = zincir["tufe"]
    payload = _aylik_grid_compute(2, kyc, tufe, plan_katmani=False)
    if manual:
        _reel_boya(payload, date(2023, 8, 1), artis_ay, artis_gun, manual)
    _plan_katmani_payloada(
        payload,
        kyc,
        tufe,
        planlar=planlar,
        reel=manual or {},
        faturali_aylar=zincir.get("faturali_aylar"),
        fatura_belge=zincir.get("fatura_belge"),
    )
    grid = {}
    for a in payload["aylar"]:
        if a["yil"] > 2026 or (a["yil"] == 2026 and a["ay"] > 8):
            continue
        grid[f"{a['yil']:04d}-{a['ay']:02d}-01"] = round(float(a["tutar_kdv_dahil"]), 2)
    harita, _uy = plan_uygulanacak_aylar(zincir, planlar)
    plan_map = {ay: round(float(row["brut"]), 2) for ay, row in harita.items()}
    reel = {}
    if manual:
        reel = _reel_ay_key_tutar_map_db_flat_only(date(2023, 8, 1), artis_ay, artis_gun, manual)
    gorunen, _yedek = gorunen_borc_biles(grid, reel, None, sozlesme_basi="2023-08-01")
    fark = []
    for iso, brut in grid.items():
        y, m = int(iso[0:4]), int(iso[5:7])
        ekstre = _ekstre_ay_borcu(y, m, 0, grid, None, {}, reel, artis_ay, tufe, 1000, plan_map)
        fifo_t = gorunen.get(iso)
        dagitim = acik_ay_dagit(gorunen, brut, iso, allowlist=[iso], sozlesme_basi="2023-08-01")
        dagitilan = round(sum(t for _ay, t in dagitim), 2)
        if (
            round(float(ekstre or 0), 2) != brut
            or round(float(fifo_t or 0), 2) != brut
            or dagitilan != brut
        ):
            fark.append((iso[:7], brut, ekstre, fifo_t, dagitilan))
    return fark


def test_grid_ekstre_fifo_fark():
    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}, 2026: {8: 5.0}}
    plan = [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    kartlar = [
        ("duz", _kyc_grid(), _zincir(tufe={}), plan, 8, 1, None),
        ("tufe", _kyc_grid(), _zincir(tufe=tufe, ay_sayisi=48), plan, 8, 1, None),
        (
            "hibrit",
            _kyc_grid(kira_nakit_tutar=400, kira_banka_tutar=600),
            _zincir(tufe=tufe, nakit_tutar=400, banka_tutar=600, ay_sayisi=48),
            [{
                "gecerlilik_ay": "2024-11-01",
                "yeni_net": 2000,
                "kdv_oran": 20,
                "yeni_brut": 2320,
                "nakit_tutar": 400,
                "banka_tutar": 1600,
            }],
            8,
            1,
            None,
        ),
        (
            "nakit",
            _kyc_grid(kira_nakit=True),
            _zincir(tufe=tufe, kira_nakit=True, ay_sayisi=48),
            [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 0, "yeni_brut": 2000}],
            8,
            1,
            None,
        ),
        (
            "reel",
            _kyc_grid(),
            _zincir(tufe=tufe, ay_sayisi=48, reel={2024: 1500, 2025: 1800}),
            plan,
            8,
            1,
            {2024: 1500, 2025: 1800},
        ),
        (
            "ilk_yil",
            _kyc_grid(),
            _zincir(tufe=tufe, ay_sayisi=48, reel={2024: 1500}),
            [{"gecerlilik_ay": "2023-11-01", "yeni_net": 800, "kdv_oran": 20, "yeni_brut": 960}],
            8,
            1,
            {2024: 1500},
        ),
        (
            "coklu",
            _kyc_grid(),
            _zincir(tufe=tufe, ay_sayisi=48),
            [
                {"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400},
                {"gecerlilik_ay": "2025-03-01", "yeni_net": 2500, "kdv_oran": 20, "yeni_brut": 3000},
            ],
            8,
            1,
            None,
        ),
    ]
    toplam = []
    for ad, kyc, zincir, planlar, artis_ay, artis_gun, manual in kartlar:
        fark = _karsilastir_kart(ad, kyc, zincir, planlar, artis_ay, artis_gun, manual)
        print("asama4", ad, "fark", len(fark))
        for satir in fark[:8]:
            print("asama4", ad, satir[0], "grid", satir[1], "ekstre", satir[2], "fifo", satir[3], "dagitim", satir[4])
        toplam.extend(fark)
        check("grid ekstre dagitim " + ad, fark == [])
    check("sentetik fark sifir", toplam == [])


def test_plansiz_ekstre_ayni_ve_hiz():
    import time
    from routes import giris_routes as gr

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}}
    grid = {"2024-08-01": 1320.0, "2024-10-01": 1500.0}
    reel = {"2024-10": 1500.0}
    ayni = True
    for y, m in ((2024, 8), (2024, 10)):
        eski = gr._ekstre_borc_tutar_for_month(y, m, 0, grid, None, {}, reel, 8, tufe, 1000)
        iso = f"{y:04d}-{m:02d}-01"
        eski = gr._ekstre_hucre_borc(iso, eski, grid, reel)
        yeni = gr._ekstre_ay_borcu(y, m, 0, grid, None, {}, reel, 8, tufe, 1000, {})
        if round(float(eski), 2) != round(float(yeni), 2):
            ayni = False
    check("plansiz ekstre birebir", ayni)
    sayac = {"n": 0}
    eski_one, eski_all = gr.fetch_one, gr.fetch_all

    def _say(sql, params=None):
        sayac["n"] += 1
        return None

    gr.fetch_one = _say
    gr.fetch_all = _say
    try:
        t0 = time.perf_counter()
        for _ in range(300):
            eski = gr._ekstre_borc_tutar_for_month(2024, 8, 0, grid, None, {}, reel, 8, tufe, 1000)
            gr._ekstre_hucre_borc("2024-08-01", eski, grid, reel)
        sure_eski = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        for _ in range(300):
            gr._ekstre_ay_borcu(2024, 8, 0, grid, None, {}, reel, 8, tufe, 1000, {})
        sure_yeni = (time.perf_counter() - t1) * 1000
    finally:
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
    check("plansiz ekstre sorgu yok", sayac["n"] == 0)
    print("perf ekstre_eski_ms", round(sure_eski, 2), "ekstre_yeni_ms", round(sure_yeni, 2), "sorgu", sayac["n"])
    gr._plan_kutu_sifirla()
    sayac2 = {"one": 0, "all": 0}

    def fetch_one_bos(sql, params=None):
        sayac2["one"] += 1
        if "to_regclass" in str(sql):
            return {"t": "sozlesme_plan_degisiklik"}
        return None

    def fetch_all_bos(sql, params=None):
        sayac2["all"] += 1
        return []

    gr.fetch_one, gr.fetch_all = fetch_one_bos, fetch_all_bos
    try:
        bos = gr._ekstre_plan_brut_haritasi(5, _kyc_grid(), tufe, {}, date(2026, 8, 1))
        ilk = (sayac2["one"], sayac2["all"])
        gr._ekstre_plan_brut_haritasi(5, _kyc_grid(), tufe, {}, date(2026, 8, 1))
    finally:
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
        gr._plan_kutu_sifirla()
    print("perf plansiz_harita_ilk", ilk, "ikinci", (sayac2["one"], sayac2["all"]))
    check("plansiz harita fatura sorgusu yok", bos == {} and ilk == (1, 1) and sayac2["all"] == 1)


def test_planli_ekstre_sorgu():
    from routes import giris_routes as gr

    gr._plan_kutu_sifirla()
    sayac = {"one": 0, "all": 0}

    def fetch_one(sql, params=None):
        sayac["one"] += 1
        if "to_regclass" in str(sql):
            return {"t": "sozlesme_plan_degisiklik"}
        return None

    def fetch_all(sql, params=None):
        sayac["all"] += 1
        if "sozlesme_plan_degisiklik" in str(sql):
            return [{
                "musteri_id": 4,
                "gecerlilik_ay": date(2024, 11, 1),
                "yeni_net": 2000,
                "kdv_oran": 20,
                "yeni_brut": 2400,
                "nakit_tutar": None,
                "banka_tutar": None,
            }]
        if "FROM faturalar" in str(sql):
            return [{
                "musteri_id": 4,
                "notlar": "",
                "toplam": 999,
                "fatura_tarihi": date(2024, 11, 15),
                "durum": "odenmedi",
                "yon": "giden",
            }]
        return []

    eski_one, eski_all = gr.fetch_one, gr.fetch_all
    gr.fetch_one, gr.fetch_all = fetch_one, fetch_all
    try:
        harita = gr._ekstre_plan_brut_haritasi(
            4,
            _kyc_grid(),
            {2024: {8: 10.0}, 2025: {8: 5.0}},
            {2024: 1500},
            date(2026, 8, 1),
        )
        ilk_one, ilk_all = sayac["one"], sayac["all"]
        gr._ekstre_plan_brut_haritasi(
            4,
            _kyc_grid(),
            {2024: {8: 10.0}},
            {},
            date(2026, 8, 1),
        )
    finally:
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
        gr._plan_kutu_sifirla()
    print("perf planli_ek_sorgu", "tekli", ilk_one, "toplu", ilk_all, "ikinci_tekli", sayac["one"], "ikinci_toplu", sayac["all"])
    check(
        "planli ekstre uc sorgu sonra sifir",
        ilk_one == 1 and ilk_all == 2 and sayac["one"] == 1 and sayac["all"] == 2 and harita.get("2024-12") == 2400,
    )
    check("fatura tarihi ayi kilitler", "2024-11" not in harita)


def test_fatura_kaydi_ve_on_kontrol():
    from sozlesme_plan import plan_ekle_on_kontrol
    from routes.giris_routes import _plan_fatura_aylari

    rows = [
        {"musteri_id": 1, "durum": "odenmedi", "yon": "giden", "toplam": 1500, "notlar": "|AYLIK_TUTAR|2024-10-01|", "fatura_tarihi": date(2024, 10, 1)},
        {"musteri_id": 1, "durum": "iptal", "yon": "giden", "toplam": 10, "notlar": "|AYLIK_TUTAR|2024-09-01|", "fatura_tarihi": date(2024, 9, 1)},
        {"musteri_id": 1, "durum": "odenmedi", "yon": "giden", "toplam": 10, "notlar": "ERP durum: taslak", "fatura_tarihi": date(2024, 8, 1)},
        {"musteri_id": 1, "durum": "odenmedi", "yon": "gelen", "toplam": 10, "notlar": "", "fatura_tarihi": date(2024, 7, 1)},
        {"musteri_id": 1, "durum": "odenmedi", "yon": "giden", "toplam": 999, "notlar": "GİB İMZALANDI", "fatura_tarihi": date(2024, 11, 15)},
        {"musteri_id": 1, "durum": "odendi", "yon": "giden", "toplam": 5, "notlar": "|GIB_NO_TASINDI|x|", "fatura_tarihi": date(2024, 6, 1)},
    ]
    faturali, belge = _plan_fatura_aylari(rows)
    aylar = set(faturali.get(1) or [])
    check(
        "fatura kaydi aylari",
        aylar == {"2024-10", "2024-11"} and belge[1]["2024-11"] == 999 and belge[1]["2024-10"] == 1500,
    )
    bos = plan_ekle_on_kontrol("2024-12-01", aylar)
    dolu = plan_ekle_on_kontrol("2024-11-01", ["2024-11", "2024-12"])
    check("faturasiz ay uyari yok", bos["faturali"] is False and bos["uyarilar"] == [] and bos["onerilen_ay"] == "2024-12")
    check(
        "faturali ay oneri",
        dolu["faturali"] is True and dolu["onerilen_ay"] == "2025-01" and len(dolu["uyarilar"]) == 1,
    )


def _son_ay_net_yazimi(aylar, today, kdv_oran=20.0):
    """Toplu borç döngüsünün guncel_kira yazımı. Döngünün kendisi değiştirilmez."""
    last = 0.0
    sinir = today.replace(day=1)
    for a in aylar or []:
        try:
            if date(int(a.get("yil")), int(a.get("ay")), 1) <= sinir:
                last = float(a.get("tutar_kdv_dahil") or last or 0)
        except (TypeError, ValueError):
            continue
    if last <= 0:
        return None, 0.0
    return round(last / (1 + kdv_oran / 100.0), 2), round(last, 2)


def test_asama5_okuyucular():
    import time
    from sozlesme_plan import ay_bazli_tutarlar, bildirge_yil_tablosu, plan_ekle_on_kontrol, tahsilat_yil_borc
    from routes import giris_routes as gr

    tufe = {2024: {8: 10.0}, 2025: {8: 5.0}}
    plan = [{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 20, "yeni_brut": 2400}]
    reel = {"2024-11": 1500.0}
    grid = {"2024-11-01": 2400.0, "2024-10-01": 1320.0}

    eski_reel = gr._ekstre_hucre_alacak(
        "2024-11-01", grid, {"2024-11-01": 100}, None, ayda_tahsilat_var=True, reel_ay_map=reel
    )
    eski_yok = gr._ekstre_hucre_alacak(
        "2024-11-01", grid, {}, None, ayda_tahsilat_var=False, reel_ay_map=reel
    )
    eski_grid = gr._ekstre_hucre_alacak(
        "2024-10-01", grid, {"2024-10-01": 10}, None, ayda_tahsilat_var=True, reel_ay_map={}
    )
    check("plansiz alacak reel hedef", eski_reel == 1500 and eski_yok == 0 and eski_grid == 1320)

    tam = gr._ekstre_hucre_alacak(
        "2024-11-01", grid, {"2024-11-01": 2400}, None,
        ayda_tahsilat_var=True, reel_ay_map=reel, plan_ay_brut=2400, tahsil_tutar=2400,
    )
    kismi = gr._ekstre_hucre_alacak(
        "2024-11-01", grid, {"2024-11-01": 100}, None,
        ayda_tahsilat_var=True, reel_ay_map=reel, plan_ay_brut=2400, tahsil_tutar=100,
    )
    sifir = gr._ekstre_hucre_alacak(
        "2024-11-01", grid, {}, None,
        ayda_tahsilat_var=False, reel_ay_map=reel, plan_ay_brut=2400, tahsil_tutar=0,
    )
    check("planli alacak reelden buyuk degil kucuk hedef", tam == 2400 and kismi == 100 and sifir == 0)

    aylar = [
        ("2024-10-01", 1320.0, None, 1320.0),
        ("2024-11-01", 2400.0, 2400.0, 2400.0),
        ("2024-12-01", 2400.0, 2400.0, 500.0),
    ]
    borc = 0.0
    alacak = 0.0
    tahsil = 0.0
    for iso, brut, plan_b, nakit in aylar:
        borc += brut
        tahsil += nakit
        hucre = {iso: brut}
        odenen = {iso: nakit}
        alacak += gr._ekstre_hucre_alacak(
            iso, hucre, odenen, None,
            ayda_tahsilat_var=nakit > 0.05,
            reel_ay_map={"2024-11": 1500.0, "2024-12": 1500.0},
            plan_ay_brut=plan_b,
            tahsil_tutar=nakit if plan_b is not None else None,
        ) or 0
    bakiye = round(borc - alacak, 2)
    check("ekstre bakiye grid eksi tahsilat", bakiye == round(borc - tahsil, 2) and bakiye == 1900)

    check("plansiz yil carpi 12", tahsilat_yil_borc(1320, [2400, 2400, 1320], False) == round(1320 * 12, 2))
    kirik = [1500, 1500, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400, 2400]
    check("planli yil ay ay", tahsilat_yil_borc(1320, kirik, True) == round(sum(kirik), 2))
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    borc_js = html[html.find("function girisTahsilatYilBorcTek"):html.find("function girisTahsilatYilBorcTek") + 280]
    check(
        "yil js plan varsa toplar",
        "girisPlanYilToplam" in borc_js and "planToplam != null" in borc_js and "* 12" in borc_js and "plan_var" in html,
    )

    duz = bildirge_yil_tablosu(ay_bazli_tutarlar(_zincir(tufe=tufe)), 2024)
    planli = bildirge_yil_tablosu(ay_bazli_tutarlar(_zincir(tufe=tufe), plan), 2024)
    duz_net = {s["ay"]: s["net"] for s in duz}
    plan_net = {s["ay"]: s["net"] for s in planli}
    check(
        "plansiz bildirge yil",
        duz_net.get("2024-07") == 1000 and duz_net.get("2024-08") == 1100 and duz_net.get("2024-12") == 1100,
    )
    check(
        "planli bildirge kirilim",
        plan_net.get("2024-10") == 1100 and plan_net.get("2024-11") == 2000 and plan_net.get("2024-12") == 2000,
    )
    pdf_eski = gr.build_kira_bildirgesi_pdf("Sentetik", "2023-08-01", "2024-11-01", 1100, 20)
    pdf_tek = gr.build_kira_bildirgesi_pdf("Sentetik", "2023-08-01", "2024-11-01", 1100, 20, ay_tablosu=[{"ay": "2024-11", "net": 2000}, {"ay": "2024-12", "net": 2000}])
    pdf_kirik = gr.build_kira_bildirgesi_pdf(
        "Sentetik", "2023-08-01", "2024-11-01", 1100, 20,
        ay_tablosu=[{"ay": "2024-10", "net": 1100, "brut": 1320}, {"ay": "2024-11", "net": 2000, "brut": 2400}],
    )
    import re
    sayfa = lambda b: len(re.findall(br"/Type /Page(?!s)", b))
    check("bildirge plansiz sayfa ayni", sayfa(pdf_eski) == sayfa(pdf_tek) == 1 and sayfa(pdf_kirik) == 2)

    gr._plan_kutu_sifirla()
    sayac = {"one": 0, "all": 0, "fatura": 0, "kyc": 0}
    eski_one, eski_all = gr.fetch_one, gr.fetch_all

    def fetch_one(sql, params=None):
        sayac["one"] += 1
        if "to_regclass" in str(sql):
            return {"t": "sozlesme_plan_degisiklik"}
        return None

    def fetch_all(sql, params=None):
        sayac["all"] += 1
        if "sozlesme_plan_degisiklik" in str(sql):
            return [{
                "musteri_id": 2,
                "gecerlilik_ay": date(2024, 11, 1),
                "yeni_net": 2000,
                "kdv_oran": 20,
                "yeni_brut": 2400,
                "nakit_tutar": None,
                "banka_tutar": None,
            }]
        return []

    gr.fetch_one, gr.fetch_all = fetch_one, fetch_all
    try:
        satirlar = [{"musteri_id": i, "guncel": 1320.0} for i in range(1, 9)]
        kyc = {i: _kyc_grid() for i in range(1, 9)}
        t0 = time.perf_counter()
        g1 = gr.geciken_satir_guncelleri(satirlar, date(2024, 12, 15), kyc, tufe, {})
        ilk = (sayac["one"], sayac["all"])
        g2 = gr.geciken_satir_guncelleri(satirlar, date(2024, 12, 15), kyc, tufe, {})
        sure = (time.perf_counter() - t0) * 1000
    finally:
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
        gr._plan_kutu_sifirla()
    check("geciken plansiz ayni", g1[0] == 1320 and g2[0] == 1320)
    check("geciken planli ay brut", g1[1] == 2400 and g2[1] == 2400)
    check("geciken toplu sonra sifir", ilk == (1, 2) and sayac == {"one": 1, "all": 2, "fatura": 0, "kyc": 0})
    print("perf geciken_ms", round(sure, 2), "sorgu_ilk", ilk, "sorgu_ikinci", (sayac["one"], sayac["all"]))

    from routes import faturalar_routes as fr

    fr_one = fr.fetch_one
    gr.fetch_one, gr.fetch_all = fetch_one, fetch_all
    gr._tufe_eski = getattr(gr, "_tufe_map_by_year_month_cached")
    gr._tufe_map_by_year_month_cached = lambda: tufe

    def fatura_one(sql, params=None):
        s = str(sql)
        if "ilk_kira_bedeli" in s:
            sayac["kyc"] += 1
            return {"ilk_kira_bedeli": 1000}
        if "musteri_kyc" in s:
            sayac["kyc"] += 1
            return {
                "sozlesme_tarihi": date(2023, 8, 1),
                "kira_artis_tarihi": date(2023, 8, 1),
                "aylik_kira": 1000,
                "kdv_oran": 20,
                "kira_nakit": False,
                "kira_nakit_tutar": None,
                "kira_banka_tutar": None,
            }
        if "FROM faturalar" in s or "faturalar" in s:
            sayac["fatura"] += 1
            return {"toplam": 1800}
        return None

    fr.fetch_one = fatura_one
    fr._auto_month_amount_from_cache = lambda mid, gun: 0.0
    try:
        gr._plan_kutu_sifirla()
        sayac["one"] = sayac["all"] = sayac["fatura"] = sayac["kyc"] = 0
        gr.fetch_one, gr.fetch_all = fetch_one, fetch_all
        gr._plan_paket_yukle([1])
        yuk = (sayac["one"], sayac["all"])
        plansiz_tutar = fr._auto_month_amount_resolved(1, date(2024, 12, 1))
        plansiz_sorgu = (sayac["one"], sayac["all"], sayac["fatura"], sayac["kyc"])
        gr._plan_kutu_sifirla()
        sayac["one"] = sayac["all"] = sayac["fatura"] = sayac["kyc"] = 0

        def fetch_all_plan(sql, params=None):
            sayac["all"] += 1
            if "sozlesme_plan_degisiklik" in str(sql):
                return [{
                    "musteri_id": 2,
                    "gecerlilik_ay": date(2024, 11, 1),
                    "yeni_net": 2000,
                    "kdv_oran": 20,
                    "yeni_brut": 2400,
                    "nakit_tutar": None,
                    "banka_tutar": None,
                }]
            return []

        gr.fetch_all = fetch_all_plan
        gr._plan_paket_yukle([2])
        gr._planli_reel_haritasi([2])
        on = (sayac["one"], sayac["all"])
        planli_tutar = fr._auto_month_amount_resolved(2, date(2024, 12, 1))
        sonra = (sayac["one"], sayac["all"], sayac["fatura"], sayac["kyc"])
    finally:
        fr.fetch_one = fr_one
        gr.fetch_one, gr.fetch_all = eski_one, eski_all
        gr._tufe_map_by_year_month_cached = gr._tufe_eski
        gr._plan_kutu_sifirla()
    check("plansiz otomatik eski yedek", plansiz_tutar == 1800 and plansiz_sorgu[2] == 1 and plansiz_sorgu[3] == 0)
    check("planli otomatik plan brut", planli_tutar == 2400 and sonra[2] == 0 and sonra[0] == on[0] and sonra[1] == on[1])
    print("perf otomatik plansiz", plansiz_sorgu, "planli_oncesi", on, "planli_sonra", sonra, "yuk", yuk)

    bugun = date(2024, 12, 9)
    once = date(2024, 10, 9)
    kdv_payload = gr._aylik_grid_compute(2, _kyc_grid(), tufe, planlar=plan, reel={})
    nakit_payload = gr._aylik_grid_compute(
        2,
        _kyc_grid(kira_nakit=True),
        tufe,
        planlar=[{"gecerlilik_ay": "2024-11-01", "yeni_net": 2000, "kdv_oran": 0, "yeni_brut": 2000}],
        reel={},
    )
    hibrit_payload = gr._aylik_grid_compute(
        2,
        _kyc_grid(kira_nakit_tutar=400, kira_banka_tutar=600),
        tufe,
        planlar=[{
            "gecerlilik_ay": "2024-11-01",
            "yeni_net": 2000,
            "kdv_oran": 20,
            "yeni_brut": 2320,
            "nakit_tutar": 400,
            "banka_tutar": 1600,
        }],
        reel={},
    )
    kdv_net, kdv_brut = _son_ay_net_yazimi(kdv_payload["aylar"], bugun)
    nakit_net, nakit_brut = _son_ay_net_yazimi(nakit_payload["aylar"], bugun)
    hibrit_net, hibrit_brut = _son_ay_net_yazimi(hibrit_payload["aylar"], bugun)
    once_net, once_brut = _son_ay_net_yazimi(kdv_payload["aylar"], once)
    check("toplu borc kdv ay dogru", kdv_brut == 2400 and kdv_net == 2000)
    check("toplu borc plandan once eski", once_brut == 1320 and once_net == 1100)
    check("toplu borc nakit risk", nakit_brut == 2000 and nakit_net == round(2000 / 1.2, 2) and nakit_net != 2000)
    check("toplu borc hibrit risk", hibrit_brut == 2320 and hibrit_net != 2000)
    print("toplu_borc kdv", kdv_net, "nakit", nakit_net, "hibrit", hibrit_net, "once", once_net)

    import odeme_linki
    cagri = {}

    def fake_bas_bit(mid):
        return date(2024, 11, 1), date(2024, 12, 31)

    def fake_reel(mid):
        return {}

    def fake_ekstre(mid, bas, bit, aylik, **kw):
        cagri["hizala"] = kw.get("tahsilat_borca_hizala")
        return [{"borc": borc, "alacak": alacak, "bakiye": bakiye}]

    gr._cari_ekstre_varsayilan_bas_bit = fake_bas_bit
    gr._musteri_reel_donem_manual_dict_from_db = fake_reel
    gr._cari_ekstre_hareketler = fake_ekstre
    link = odeme_linki.ekstre_kalan(3)
    check("link tutari ekstre bakiyesi", link == bakiye and cagri.get("hizala") is True)

    seen = []
    eski_inv = gr._invalidate_aylik_grid_payload_mem
    eski_build = gr._build_aylik_grid_cache_payload
    eski_panel = gr._panel_by_iso_from_tahsil_map
    eski_save = gr._save_musteri_panel_by_iso
    eski_up = gr._upsert_aylik_grid_cache
    gr._invalidate_aylik_grid_payload_mem = lambda mid: seen.append(("inv", mid))
    gr._build_aylik_grid_cache_payload = lambda mid: seen.append(("build", mid)) or {"aylar": []}
    gr._panel_by_iso_from_tahsil_map = lambda *a, **k: {}
    gr._save_musteri_panel_by_iso = lambda mid, by, **k: seen.append(("save", mid))
    gr._upsert_aylik_grid_cache = lambda mid: seen.append(("up", mid))
    try:
        tamam = gr.resync_panel_and_grid_after_plan_change(4)
    finally:
        gr._invalidate_aylik_grid_payload_mem = eski_inv
        gr._build_aylik_grid_cache_payload = eski_build
        gr._panel_by_iso_from_tahsil_map = eski_panel
        gr._save_musteri_panel_by_iso = eski_save
        gr._upsert_aylik_grid_cache = eski_up
    check("resync yalniz o kart", tamam is True and seen == [("inv", 4), ("build", 4), ("save", 4), ("up", 4)])
    depo = (ROOT / "sozlesme_plan_depo.py").read_text(encoding="utf-8")
    routes = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    check(
        "resync henuz cagrilmiyor",
        "resync_panel_and_grid_after_plan_change" not in depo
        and routes.count("resync_panel_and_grid_after_plan_change") == 1,
    )

    from routes.giris_routes import _plan_fatura_kilit_listesi
    rows = [
        {"musteri_id": 1, "durum": "odenmedi", "yon": "giden", "toplam": 1500, "notlar": "|AYLIK_TUTAR|2024-10-01|", "fatura_tarihi": date(2024, 10, 1)},
        {"musteri_id": 1, "durum": "odenmedi", "yon": "giden", "toplam": 999, "notlar": "GİB İMZALANDI", "fatura_tarihi": date(2024, 11, 15)},
    ]
    kilit = _plan_fatura_kilit_listesi(rows).get(1) or []
    by_ay = {k["ay"]: k for k in kilit}
    kontrol = plan_ekle_on_kontrol("2024-11-01", kilit)
    check(
        "kilit kaynaklari",
        by_ay.get("2024-10", {}).get("kaynak") == "isaret"
        and by_ay.get("2024-10", {}).get("fatura_tutari") == 1500
        and by_ay.get("2024-11", {}).get("kaynak") == "fatura_tarihi"
        and by_ay.get("2024-11", {}).get("fatura_tutari") == 999,
    )
    check(
        "kilit otomatik kaymaz",
        kontrol["gecerlilik_ay"] == "2024-11"
        and kontrol["onerilen_ay"] == "2024-12"
        and kontrol["faturali"] is True
        and len(kontrol["kilitlenen_aylar"]) == 2,
    )


def test_baglanti_yok():
    routes = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
    depo = (ROOT / "sozlesme_plan_depo.py").read_text(encoding="utf-8")
    hesap = (ROOT / "sozlesme_plan.py").read_text(encoding="utf-8")
    check("okuma ensure cagirmaz", "ensure_sozlesme_plan_degisiklik" not in routes)
    check("yazan route yok", "plan_ekle" not in routes and "plan_iptal" not in routes)
    check("depo db execute yok", "import db" not in depo and "execute(" not in depo)
    check("hesap db yok", "import db" not in hesap and "musteri_aylik_grid_cache" not in hesap)


def main():
    test_plansiz_kart_tufe()
    test_reel_ilk_yil_yok_sonraki_pencere()
    test_plan_ayni_pencerede_reeli_ezer()
    test_sonraki_yil_reel_kazanir()
    test_coklu_plan()
    test_hibrit_ve_varsayilan_pay()
    test_nakit_kart()
    test_ilk_yil_icinde_plan()
    test_fatura_ayi_sabit()
    test_iptal_plan_yok_sayilir()
    test_grid_cekirdegi_ile_ayni()
    test_reel_sonrasi_tufe()
    test_fatura_belge_uyarisi()
    artis_ayni = test_artis_ayi_farkli_grid()
    print("karar2_artis_ayi", "uyusuyor" if artis_ayni else "uyusmuyor")
    test_plansiz_grid_birebir()
    test_planli_grid_katmani()
    test_cache_yalniz_o_kart()
    test_plan_okuma_toplu_ve_hata()
    test_grid_ekstre_fifo_fark()
    test_plansiz_ekstre_ayni_ve_hiz()
    test_planli_ekstre_sorgu()
    test_fatura_kaydi_ve_on_kontrol()
    test_depo()
    test_baglanti_yok()
    test_asama5_okuyucular()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("sozlesme_plan ok")


if __name__ == "__main__":
    main()
