# -*- coding: utf-8 -*-
"""Plan değiştirme uçları. Canlı veritabanına bağlanmaz."""
import logging
import os
import subprocess
from copy import deepcopy
from datetime import date
from pathlib import Path

from flask import Flask

from auth import login_manager
from routes.giris_routes import bp
from test_sozlesme_plan import Bellek
import sozlesme_plan_api as api

ROOT = Path(__file__).resolve().parent
FAILS = []


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


class Tx:
    def __init__(self):
        self.bellek = Bellek()
        self.commit = 0
        self.rollback = 0

    def __call__(self, fn):
        snap = deepcopy(self.bellek.satirlar)
        nsql = len(self.bellek.sql)
        try:
            out = fn(self.bellek.calistir, self.bellek.oku, self.bellek.oku)
        except Exception:
            self.bellek.satirlar[:] = snap
            del self.bellek.sql[nsql:]
            self.rollback += 1
            raise
        self.commit += 1
        return out


def _app():
    app = Flask(__name__)
    app.secret_key = "plan-test"
    login_manager.init_app(app)
    app.register_blueprint(bp, url_prefix="/giris")
    return app


def _govde(**ek):
    g = {
        "gecerlilik_ay": "2024-11-18",
        "yeni_net": 2000,
        "kdv_oran": 20,
        "odeme": "banka",
        "yeni_brut": 2400,
    }
    g.update(ek)
    return g


def _kur(tx, kilit=None, durum="aktif"):
    api.plan_tx = tx
    api._sema = lambda: "tenant_abc"
    api._kim = lambda: "test-kullanici"
    api._bugun = lambda: date(2026, 10, 9)
    api._kilitler = lambda mid: list(kilit or [])
    api._musteri_ve_kyc = lambda mid: (
        {"id": int(mid), "durum": durum},
        {"sozlesme_tarihi": date(2023, 8, 1), "aylik_kira": 1000, "kdv_oran": 20},
    )
    api._onizleme_zinciri = lambda mid, kyc: (
        {
            "sozlesme_tarihi": date(2023, 8, 1),
            "artis_tarihi": date(2023, 8, 1),
            "ay_sayisi": 36,
            "aylik_net": 1000,
            "kdv_oran": 20,
            "kira_nakit": False,
            "tufe": {2024: {8: 10}, 2025: {8: 5}},
        },
        [],
        None,
    )
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "7"


def _yanit(yanit):
    if isinstance(yanit, tuple):
        resp, kod = yanit
        return resp.get_json(), kod
    return yanit.get_json(), yanit.status_code


def test_kapi_ve_yetki():
    app = _app()
    client = app.test_client()
    r = client.get("/giris/api/musteri/7/plan")
    check("oturum_401", r.status_code == 401)
    kurallar = {rule.rule for rule in app.url_map.iter_rules()}
    check(
        "rotalar kayitli",
        "/giris/api/musteri/<int:mid>/plan" in kurallar
        and "/giris/api/musteri/<int:mid>/plan/onizleme" in kurallar
        and "/giris/api/musteri/<int:mid>/plan/<int:plan_id>/iptal" in kurallar,
    )
    os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
    os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)
    sayac = {"m": 0}
    eski = api._musteri_ve_kyc

    def say(mid):
        sayac["m"] += 1
        return eski(mid)

    api._musteri_ve_kyc = say
    with app.test_request_context("/giris/api/musteri/7/plan"):
        _g, kod = _yanit(api.plan_liste_yanit(7))
    check("kapi kapali 404", kod == 404 and sayac["m"] == 0)
    os.environ["PLAN_DEGISTIR_ENABLED"] = "1"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = ""
    with app.test_request_context("/giris/api/musteri/7/plan"):
        _g, kod = _yanit(api.plan_liste_yanit(7))
    check("bos liste 404", kod == 404 and sayac["m"] == 0)
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "7"
    with app.test_request_context("/giris/api/musteri/8/plan"):
        _g, kod = _yanit(api.plan_liste_yanit(8))
    check("izin disi 404", kod == 404 and sayac["m"] == 0)

    kayitlar = []

    class Tut(logging.Handler):
        def emit(self, record):
            kayitlar.append(record.getMessage())

    tut = Tut()
    api._LOG.addHandler(tut)
    api._LOG.setLevel(logging.INFO)
    os.environ["PLAN_DEGISTIR_ENABLED"] = "gizli-deger"
    os.environ["PLAN_DEGISTIR_MUSTERI_IDS"] = "424242"
    try:
        api._kapi_acik(7)
    finally:
        api._LOG.removeHandler(tut)
    birlesik = " ".join(kayitlar)
    check("kapi degeri loglanmaz", "gizli-deger" not in birlesik and "424242" not in birlesik)


def test_yazma():
    app = _app()
    tx = Tx()
    resync = []
    api._resync = lambda mid: resync.append(int(mid)) or True
    _kur(tx)
    try:
        with app.test_request_context(json=_govde(musteri_id=8), method="POST"):
            _g, kod = _yanit(api.plan_ekle_yanit(7))
        check("mid uyusmaz 403", kod == 403 and tx.commit == 0 and resync == [])

        api._musteri_ve_kyc = lambda mid: (None, None)
        with app.test_request_context(json=_govde(), method="POST"):
            _g, kod = _yanit(api.plan_ekle_yanit(7))
        check("musteri yok 403", kod == 403 and tx.commit == 0)

        _kur(tx, durum="pasif")
        api._resync = lambda mid: resync.append(int(mid)) or True
        with app.test_request_context(json=_govde(), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("pasif hata", kod == 400 and govde.get("mesaj") == "Pasif müşteriye plan eklenemez." and tx.commit == 0)

        _kur(tx)
        api._resync = lambda mid: resync.append(int(mid)) or True
        with app.test_request_context(json=_govde(gecerlilik_ay="2023-07-02"), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("sozlesmeden once", kod == 400 and "sözleşme" in govde.get("mesaj", "") and tx.commit == 0)

        with app.test_request_context(json=_govde(yeni_brut=9999), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("brut tutarsiz", kod == 400 and govde.get("mesaj") == "Brüt tutar tutarsız." and tx.commit == 0)

        _kur(tx, kilit=[{"ay": "2024-11", "fatura_tutari": 999, "kaynak": "fatura_tarihi"}])
        api._resync = lambda mid: resync.append(int(mid)) or True
        with app.test_request_context(json=_govde(), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("kilit onaysiz", kod == 400 and tx.commit == 0 and govde.get("kilitlenen_aylar"))

        with app.test_request_context(json=_govde(onay_kilitli_aylar=True), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check(
            "ekle ve resync",
            kod == 200
            and govde.get("ok") is True
            and govde["plan"]["gecerlilik_ay"][:10] == "2024-11-01"
            and govde["plan"]["yeni_brut"] == 2400
            and resync == [7]
            and tx.commit == 1
            and any(s.startswith("SET LOCAL search_path TO tenant_abc,") for s in tx.bellek.sql)
            and any("CREATE TABLE IF NOT EXISTS sozlesme_plan_degisiklik" in s for s in tx.bellek.sql)
            and "audit_log" not in "\n".join(tx.bellek.sql).lower(),
        )

        with app.test_request_context(json=_govde(onay_kilitli_aylar=True), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check(
            "cift istek",
            kod == 409
            and "açık bir plan zaten var" in govde.get("mesaj", "")
            and len(tx.bellek.satirlar) == 1
            and tx.rollback == 1,
        )

        def patlat(sql, params=None):
            if str(sql).lstrip().startswith("INSERT"):
                raise RuntimeError("duplicate key uq_sozlesme_plan_degisiklik_acik")
            return tx.bellek.calistir(sql, params)

        eski_tx = api.plan_tx

        def tx_pat(fn):
            try:
                return fn(patlat, tx.bellek.oku, tx.bellek.oku)
            except Exception:
                tx.rollback += 1
                raise

        api.plan_tx = tx_pat
        once = len(tx.bellek.satirlar)
        with app.test_request_context(json=_govde(gecerlilik_ay="2025-03-01", yeni_net=2100, yeni_brut=2520, onay_kilitli_aylar=True), method="POST"):
            govde, kod = _yanit(api.plan_ekle_yanit(7))
        check("unique anlasilir", kod == 409 and "açık bir plan zaten var" in govde.get("mesaj", "") and len(tx.bellek.satirlar) == once)
        api.plan_tx = eski_tx

        resync.clear()
        api._resync = lambda mid: False
        api._kilitler = lambda mid: []
        with app.test_request_context(json=_govde(gecerlilik_ay="2025-04-01", yeni_net=2100, yeni_brut=2520), method="POST"):
            _g, kod = _yanit(api.plan_ekle_yanit(7))
        check("resync hata geri alinir", kod == 500 and tx.rollback >= 2 and all(s["gecerlilik_ay"] != date(2025, 4, 1) for s in tx.bellek.satirlar))

        api._resync = lambda mid: resync.append(int(mid)) or True
        tx.bellek.satirlar.append(
            {
                "id": 9,
                "musteri_id": 7,
                "gecerlilik_ay": date(2026, 9, 1),
                "yeni_net": 2000,
                "kdv_oran": 20,
                "yeni_brut": 2400,
                "nakit_tutar": None,
                "banka_tutar": 2000,
                "olusturan": "test-kullanici",
                "created_at": None,
                "iptal_at": None,
                "iptal_eden": None,
            }
        )
        with app.test_request_context(json={}, method="POST"):
            govde, kod = _yanit(api.plan_iptal_yanit(7, 9))
        check(
            "gecmis iptal yok",
            kod == 400
            and govde.get("mesaj") == "Geçmiş plan iptal edilemez, yeni plan değişikliği girin"
            and resync == []
            and tx.bellek.satirlar[-1]["iptal_at"] is None,
        )
        tx.bellek.satirlar.append(
            {
                "id": 11,
                "musteri_id": 7,
                "gecerlilik_ay": date(2026, 11, 1),
                "yeni_net": 2000,
                "kdv_oran": 20,
                "yeni_brut": 2400,
                "nakit_tutar": None,
                "banka_tutar": 2000,
                "olusturan": "test-kullanici",
                "created_at": None,
                "iptal_at": None,
                "iptal_eden": None,
            }
        )
        with app.test_request_context(json={}, method="POST"):
            govde, kod = _yanit(api.plan_iptal_yanit(7, 11))
        check("gelecek iptal resync", kod == 200 and resync == [7] and govde["plan"]["iptal_at"])

        with app.test_request_context(json={}, method="POST"):
            tx.bellek.satirlar.append(
                {
                    "id": 12,
                    "musteri_id": 8,
                    "gecerlilik_ay": date(2026, 12, 1),
                    "yeni_net": 1,
                    "kdv_oran": 20,
                    "yeni_brut": 1.2,
                    "nakit_tutar": None,
                    "banka_tutar": 1,
                    "olusturan": "test-kullanici",
                    "created_at": None,
                    "iptal_at": None,
                    "iptal_eden": None,
                }
            )
            resync.clear()
            _g, kod = _yanit(api.plan_iptal_yanit(7, 12))
        check("baska kart iptal 403", kod == 403 and resync == [])
    finally:
        os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
        os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)


def test_onizleme_ve_liste():
    app = _app()
    tx = Tx()
    _kur(tx)
    cagri = {"tx": 0}

    def say_tx(fn):
        cagri["tx"] += 1
        return tx(fn)

    api.plan_tx = say_tx
    try:
        with app.test_request_context(json=_govde(), method="POST"):
            govde, kod = _yanit(api.plan_onizleme_yanit(7))
        ilk = govde.get("aylar") or []
        check(
            "onizleme 24 yazmaz",
            kod == 200
            and len(ilk) == 24
            and ilk[0]["ay"] == "2024-11"
            and ilk[0]["eski_brut"] == 1320
            and ilk[0]["yeni_brut"] == 2400
            and cagri["tx"] == 0
            and govde.get("kilitlenen_aylar") == [],
        )
        import db as dbmod

        def fetch_all(sql, params=None):
            if "CREATE" in str(sql):
                raise RuntimeError("ensure")
            return [
                {
                    "id": 1,
                    "musteri_id": 7,
                    "gecerlilik_ay": date(2024, 11, 1),
                    "yeni_net": 2000,
                    "kdv_oran": 20,
                    "yeni_brut": 2400,
                    "nakit_tutar": None,
                    "banka_tutar": 2000,
                    "olusturan": "test-kullanici",
                    "created_at": None,
                    "iptal_at": None,
                    "iptal_eden": None,
                },
                {
                    "id": 2,
                    "musteri_id": 7,
                    "gecerlilik_ay": date(2025, 1, 1),
                    "yeni_net": 1,
                    "kdv_oran": 20,
                    "yeni_brut": 1.2,
                    "nakit_tutar": None,
                    "banka_tutar": 1,
                    "olusturan": "test-kullanici",
                    "created_at": None,
                    "iptal_at": date(2026, 1, 1),
                    "iptal_eden": "test-kullanici",
                },
            ]

        eski_all = dbmod.fetch_all
        dbmod.fetch_all = fetch_all
        dbmod.fetch_one = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("canli-yok"))
        try:
            with app.test_request_context("/giris/api/musteri/7/plan"):
                govde, kod = _yanit(api.plan_liste_yanit(7))
        finally:
            dbmod.fetch_all = eski_all
        check(
            "liste aktif iptal",
            kod == 200 and len(govde.get("aktif") or []) == 1 and len(govde.get("iptaller") or []) == 1 and len(govde.get("gecmis") or []) == 2,
        )
    finally:
        os.environ.pop("PLAN_DEGISTIR_ENABLED", None)
        os.environ.pop("PLAN_DEGISTIR_MUSTERI_IDS", None)


def test_js():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = html.find('id="musteri_durum"')
    parca = html[i:html.find("</select>", i)]
    check("kapali secenek yok", 'value="plan"' not in parca and "Plan değiştir" not in parca)
    check("onchange karar", "musteriDurumSecildi(this.value)" in parca and "/durum" not in (ROOT / "static" / "js" / "plan_degistir.js").read_text(encoding="utf-8"))
    js_metin = (ROOT / "static" / "js" / "plan_degistir.js").read_text(encoding="utf-8")
    kutu_blok = js_metin[js_metin.find('kutu.id = "plan_gecmis_bolum"'):][:600]
    check(
        "gecmis kutusu form-grid'de tam genislikli ayri satir",
        'kutu.style.gridColumn = "1 / -1"' in kutu_blok and "insertBefore(kutu, sel.nextSibling)" in js_metin,
    )
    check("script surumu artirildi", "js/plan_degistir.js', v=2" in html)
    taze = html.find("dnormFresh")
    taze_blok = html[taze:taze + 900] if taze >= 0 else ""
    check(
        "ikinci kart yukleme",
        "planKapisiniYenile(m.id)" in taze_blok and html.count("planKapisiniYenile(m.id)") >= 2,
    )
    aktif2 = html.find("syncDurumUI('aktif', {});")
    once2 = html[max(0, aktif2 - 800):aktif2] if aktif2 >= 0 else ""
    sonra2 = html[aktif2:aktif2 + 280] if aktif2 >= 0 else ""
    check(
        "ikinci syncDurumUI aktif dali",
        "dnormFresh" in once2 and "planKapisiniYenile(m.id)" in sonra2,
    )
    idx_aktif = 0
    aktif_yol = 0
    while True:
        bulunan = html.find("syncDurumUI('aktif'", idx_aktif)
        if bulunan < 0:
            break
        aktif_yol += 1
        pencere = html[bulunan:bulunan + 450]
        check(f"aktif yolu {aktif_yol}", "planKapisiniYenile(" in pencere)
        idx_aktif = bulunan + 1
    check("uc kart yolu", aktif_yol == 3)
    bos = html.find("syncDurumUI('aktif');\n    var csEl")
    if bos < 0:
        bos = html.find("planKapisiniYenile(null)")
    bos_blok = html[max(0, bos - 80):bos + 160] if bos >= 0 else ""
    check("bos kart temizler", "planKapisiniYenile(null)" in bos_blok)
    kod = r"""
const m = require('./static/js/plan_degistir.js');
const plan = m.planDurumKarari('plan', 'aktif', true);
const pasif = m.planDurumKarari('pasif', 'aktif', true);
const kapali = m.planDurumKarari('plan', 'aktif', false);
if (!(plan.modal && plan.kaydetDurum === false && plan.durum === 'aktif')) process.exit(2);
if (!(pasif.modal === false && pasif.kaydetDurum === true && pasif.durum === 'pasif')) process.exit(3);
if (!(kapali.goster === false && kapali.modal === false && m.planSecenekEklensin(false) === false)) process.exit(4);
if (m.planIptalEdilebilir('2026-11-01', '2026-10-09') !== true) process.exit(5);
if (m.planIptalEdilebilir('2026-09-01', '2026-10-09') !== false) process.exit(6);
process.exit(0);
"""
    r = subprocess.run(["node", "-e", kod], cwd=str(ROOT), capture_output=True, text=True)
    check("js karar", r.returncode == 0)


def main():
    test_kapi_ve_yetki()
    test_yazma()
    test_onizleme_ve_liste()
    test_js()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("plan_degistir ok")


if __name__ == "__main__":
    main()
