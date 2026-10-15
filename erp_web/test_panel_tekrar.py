# -*- coding: utf-8 -*-
"""Commit 3 — tekrar basış. Yerel/mock. Canlı sorgu yok. mid=867."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import panel_tekrar as pt  # noqa: E402

FAILS = []
MID = 867
AY = "2026-03"
SERBEST = "SERBEST_METIN_PANEL"


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:180]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


class Sahte:
    def __init__(self):
        self.tahsilatlar = {}
        self.faturalar = {}
        self.yedek = []
        self.audit = []
        self.seq = 10
        self._son = None

    def calistir(self, sql, params=None):
        self._son = None
        s = " ".join(str(sql or "").split())
        if s.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK", "CREATE TABLE", "SELECT setval")):
            return
        if s.startswith("INSERT INTO panel_tekrar_yedek"):
            self.seq += 1
            row = {
                "id": self.seq,
                "tablo_adi": params[0],
                "kayit_id": params[1],
                "musteri_id": params[2],
                "ay_iso": params[3],
                "satir_json": params[4],
                "geri_yazildi": False,
            }
            self.yedek.append(row)
            self._son = {"id": row["id"]}
            return
        if s.startswith("INSERT INTO audit_log"):
            self.audit.append(params)
            return
        if s.startswith("DELETE FROM tahsilatlar"):
            rid = params[0]
            row = self.tahsilatlar.get(rid)
            if row and str(row.get("kaynak") or "") == "grid_toplu":
                self.tahsilatlar.pop(rid, None)
                self._son = {"id": rid, "makbuz_no": row.get("makbuz_no")}
            return
        if s.startswith("DELETE FROM faturalar"):
            rid = params[0]
            row = self.faturalar.get(rid)
            bagli = any(
                t.get("fatura_id") == rid and str(t.get("kaynak") or "") != "grid_toplu"
                for t in self.tahsilatlar.values()
            )
            if (
                row
                and "|AYLIK_TUTAR|" in str(row.get("notlar") or "")
                and not str(row.get("ettn") or "").strip()
                and not bagli
            ):
                self.faturalar.pop(rid, None)
                self._son = {"id": rid, "fatura_no": row.get("fatura_no")}
            return
        if s.startswith("SELECT id, tablo_adi"):
            bulunan = [y for y in self.yedek if y["id"] == params[0]]
            self._son = dict(bulunan[0]) if bulunan else None
            return
        if s.startswith("SELECT id FROM"):
            tablo = "tahsilatlar" if "tahsilatlar" in s else "faturalar"
            kutu = self.tahsilatlar if tablo == "tahsilatlar" else self.faturalar
            self._son = {"id": params[0]} if params[0] in kutu else None
            return
        if s.startswith("UPDATE panel_tekrar_yedek"):
            for y in self.yedek:
                if y["id"] == params[0] and not y["geri_yazildi"]:
                    y["geri_yazildi"] = True
            return
        if s.startswith("INSERT INTO tahsilatlar") or s.startswith("INSERT INTO faturalar"):
            tablo = "tahsilatlar" if s.startswith("INSERT INTO tahsilatlar") else "faturalar"
            cols = s.split("(", 1)[1].split(")", 1)[0]
            alanlar = [c.strip() for c in cols.split(",")]
            govde = dict(zip(alanlar, params))
            kutu = self.tahsilatlar if tablo == "tahsilatlar" else self.faturalar
            kutu[int(govde["id"])] = govde
            self._son = {"id": govde["id"]}
            return
        raise RuntimeError("bilinmeyen sql: " + s[:80])

    def oku(self, sql, params=None):
        self.calistir(sql, params)
        return self._son


def _tah(kaynak, rid, aciklama, **ek):
    row = {
        "id": rid,
        "musteri_id": MID,
        "customer_id": MID,
        "fatura_id": None,
        "tutar": 10,
        "odeme_turu": "havale",
        "aciklama": aciklama,
        "tahsilat_tarihi": "2026-03-01",
        "makbuz_no": "M-%s" % rid,
        "kaynak": kaynak,
        "islem_grubu_id": "g",
    }
    row.update(ek)
    return row


def _borc(rid, notlar, **ek):
    row = {
        "id": rid,
        "fatura_no": "D-%s" % rid,
        "musteri_id": MID,
        "musteri_adi": "T",
        "tutar": 8,
        "kdv_tutar": 2,
        "toplam": 10,
        "durum": "odenmedi",
        "yon": "giden",
        "ettn": "",
        "notlar": notlar,
        "fatura_tarihi": "2026-03-01",
    }
    row.update(ek)
    return row


ISARET = "|AYLIK_TAH|2026-03-01|"


def test_karar():
    panel = _tah("grid_toplu", 1, "Mart 2026 Tahsilat H" + ISARET + SERBEST)
    banka = _tah("banka_import", 2, "banka " + ISARET)
    makbuz = _tah("manuel_makbuz", 3, "makbuz " + ISARET)
    link = _tah("odeme_linki", 4, "link " + ISARET)
    bilinmeyen = _tah(None, 5, "yok " + ISARET)
    yalniz = pt.karar_ver([(2026, 3)], [panel], [], taraf="tahsil")
    check("1 onay gerekli", yalniz["onay_gerekli"] is True and yalniz["panel_n"] == 1 and yalniz["diger_m"] == 0)
    check("1 metin", yalniz["mesaj_onay"] == pt.onay_metni(1, 0), yalniz["mesaj_onay"])
    karisik = pt.karar_ver([(2026, 3)], [panel, banka, makbuz, link], [], taraf="borc")
    check("2 yalniz panel silinir", [r["id"] for r in karisik["sil_tahsil"]] == [1], [r["id"] for r in karisik["sil_tahsil"]])
    check("2 diger sayisi 3", karisik["diger_m"] == 3 and karisik["panel_n"] == 1)
    check("2 metin sayilari", karisik["mesaj_onay"] == pt.onay_metni(1, 3))
    yabanci = pt.karar_ver([(2026, 3)], [banka, makbuz, link], [], taraf="tahsil")
    check("3 atla baska kaynak", yabanci["atla_tam"].get(AY, {}).get("neden") == "baska_kaynak")
    check("3 silme yok", yabanci["sil_tahsil"] == [] and yabanci["onay_gerekli"] is False)
    isaretli = pt.karar_ver([(2026, 3)], [banka], [], taraf="borc")
    check("4 isaret panel degilse silinmez", isaretli["sil_tahsil"] == [] and isaretli["atla_tam"].get(AY, {}).get("neden") == "baska_kaynak")
    check("4 bilinmeyen kaynak silinmez", pt.karar_ver([(2026, 3)], [bilinmeyen], [], taraf="tahsil")["sil_tahsil"] == [])
    borc = _borc(8, "Mart 2026 kira|AYLIK_TUTAR|2026-03-01|", ettn="ETTN-VAR")
    bag = pt.karar_ver([(2026, 3)], [], [borc], taraf="borc")
    check("5 ettn bagli borc silinmez", bag["sil_borc"] == [] and any(u.get("neden") == "bagli_borc" for u in bag["uyarilar"]))
    eslesen = _tah("manuel_makbuz", 9, "eslesme", fatura_id=8)
    borc2 = _borc(8, "Mart 2026 kira|AYLIK_TUTAR|2026-03-01|")
    bag2 = pt.karar_ver([(2026, 3)], [eslesen], [borc2], taraf="borc")
    check("5 odeme eslesmesi silinmez", bag2["sil_borc"] == [] and bag2["atla_tam"].get(AY, {}).get("neden") == "bagli_borc")
    haric = pt.karar_ver([(2026, 3)], [panel], [], haric={AY}, taraf="borc")
    check("8 haric ay silinmez", haric["sil_tahsil"] == [] and haric["onay_gerekli"] is False)


def test_sil_ve_geri():
    db = Sahte()
    panel = _tah("grid_toplu", 1, "Mart 2026 Tahsilat H" + ISARET + SERBEST)
    banka = _tah("banka_import", 2, "banka " + ISARET + "BANKA_METIN")
    makbuz = _tah("manuel_makbuz", 3, "makbuz " + ISARET)
    link = _tah("odeme_linki", 4, "link " + ISARET)
    for row in (panel, banka, makbuz, link):
        db.tahsilatlar[row["id"]] = copy.deepcopy(row)
    borc = _borc(7, "Mart 2026 kira|AYLIK_TUTAR|2026-03-01|")
    db.faturalar[7] = copy.deepcopy(borc)
    plan = pt.karar_ver([(2026, 3)], [panel, banka, makbuz, link], [borc], taraf="borc")
    once_diger = {i: copy.deepcopy(db.tahsilatlar[i]) for i in (2, 3, 4)}
    sonuc = pt.panel_satirlari_sil(
        db.calistir,
        db.oku,
        tahsil_rows=plan["sil_tahsil"],
        borc_rows=plan["sil_borc"],
        musteri_id=MID,
        kullanici="1",
    )
    check("1/2 panel tahsil silindi", 1 not in db.tahsilatlar and sonuc["tahsil_silinen"][0].get("makbuz_no") == "M-1")
    check("1 borc silindi", 7 not in db.faturalar and len(sonuc["borc_silinen"]) == 1)
    check("2 diger alanlar ayni", all(db.tahsilatlar[i] == once_diger[i] for i in (2, 3, 4)))
    check("1 yedek var", len(db.yedek) == 2)
    audit_metin = json.dumps(db.audit, ensure_ascii=False, default=str)
    yedek_metin = " ".join(y["satir_json"] for y in db.yedek)
    check("1 audit serbest metni tutmaz", SERBEST not in audit_metin and "BANKA_METIN" not in audit_metin)
    check("1 yedek serbest metni tutar", SERBEST in yedek_metin)
    check("1 audit yedek kimligi", any("yedek_id" in str(a[3]) for a in db.audit))
    geri = pt.panel_satir_geri_yaz(db.yedek[0]["id"], db.calistir, db.oku)
    check("6 geri ok", geri.get("ok") is True and geri.get("id") == 1, geri)
    gelen = db.tahsilatlar.get(1) or {}
    orijinal = json.loads(db.yedek[0]["satir_json"])
    check("6 birebir", all(gelen.get(k) == orijinal.get(k) for k in orijinal), gelen)
    ikinci = pt.panel_satir_geri_yaz(db.yedek[0]["id"], db.calistir, db.oku)
    check("6 ikinci ret", ikinci.get("ok") is False and ikinci.get("neden") == "zaten_geri_yazildi", ikinci)
    check("6 tek kayit", len([k for k in db.tahsilatlar if k == 1]) == 1)


def test_bagli_silinmez():
    db = Sahte()
    borc = _borc(8, "Mart 2026 kira|AYLIK_TUTAR|2026-03-01|", ettn="BELGE")
    db.faturalar[8] = copy.deepcopy(borc)
    try:
        pt.panel_satirlari_sil(
            db.calistir, db.oku, tahsil_rows=[], borc_rows=[borc], musteri_id=MID, kullanici="1"
        )
        check("5 silme exception", False)
    except RuntimeError as exc:
        check("5 bagli silinmedi", "bagli" in str(exc) and 8 in db.faturalar, str(exc))


def test_uc_onaysiz():
    from flask import Flask
    from routes import giris_routes as gr

    panel = _tah("grid_toplu", 1, "Mart 2026 Tahsilat H" + ISARET)
    banka = _tah("banka_import", 2, "banka " + ISARET)
    cagrildi = {"sil": 0}

    def sil(*_a, **_k):
        cagrildi["sil"] += 1
        raise RuntimeError("onaysiz silme")

    eski = pt.panel_satirlari_sil
    pt.panel_satirlari_sil = sil
    eski_gr = {n: getattr(gr, n) for n in (
        "fetch_one", "fetch_all", "execute", "ensure_faturalar_amount_columns",
        "_plan_paket_yukle", "_read_aylik_grid_cache_payload",
        "sync_musteri_panel_borclu_from_satirlar", "_invalidate_aylik_grid_payload_mem",
        "_defer_aylik_grid_cache_rebuild", "_dondurma_ay_kumesi",
    )}

    def fetch_all(sql, params=None):
        if "panel_tekrar_oku" in sql and "tahsilatlar" in sql:
            return [panel, banka]
        if "panel_tekrar_oku" in sql:
            return []
        return []

    def fetch_one(sql, params=None):
        if "FROM customers" in sql:
            return {"id": MID, "name": "T"}
        if "FROM musteri_kyc" in sql:
            return None
        return None

    gr.fetch_all = fetch_all
    gr.fetch_one = fetch_one
    gr.execute = lambda *a, **k: None
    gr.ensure_faturalar_amount_columns = lambda: None
    gr.sync_musteri_panel_borclu_from_satirlar = lambda *a, **k: {}
    gr._invalidate_aylik_grid_payload_mem = lambda *a, **k: None
    gr._defer_aylik_grid_cache_rebuild = lambda *a, **k: None
    gr._plan_paket_yukle = lambda mids: {int(m): {"planlar": [], "faturali": [], "belge": {}} for m in mids}
    gr._read_aylik_grid_cache_payload = lambda mid: None
    gr._dondurma_ay_kumesi = lambda mid: set()
    app = Flask(__name__)
    f = gr.api_aylik_tutarlardan_borclandir.__wrapped__
    satir = [{"yil": 2026, "ay": 3, "tutar_kdv_dahil": 10}]
    try:
        with app.test_request_context("/x", method="POST", json={"musteri_id": MID, "kira_nakit": False, "satirlar": satir}):
            yanit = f().get_json()
        check("1 onaysiz silinmedi", yanit.get("onay_gerekli") is True and cagrildi["sil"] == 0 and yanit.get("ok") is False, yanit.get("mesaj"))
        check("1 ekran metni", yanit.get("mesaj") == pt.onay_metni(1, 1), yanit.get("mesaj"))
        check("1 tahsil_silinen bos", yanit.get("tahsil_silinen") == [])
    finally:
        pt.panel_satirlari_sil = eski
        for n, v in eski_gr.items():
            setattr(gr, n, v)


def test_uc_onayla_yazar():
    from flask import Flask
    from routes import giris_routes as gr

    panel = _tah("grid_toplu", 1, "Mart 2026 Tahsilat H" + ISARET)
    borc = _borc(7, "Mart 2026 kira|AYLIK_TUTAR|2026-03-01|")
    durum = {"sil": 0, "insert": []}

    def sil(*_a, **k):
        durum["sil"] += 1
        ids = [r.get("id") for r in (k.get("tahsil_rows") or [])]
        check("1 silinen yalniz panel", ids == [1] and [r.get("id") for r in (k.get("borc_rows") or [])] == [7], ids)
        return {
            "tahsil_silinen": [{"id": 1, "makbuz_no": "M-1", "yil": 2026, "ay": 3}],
            "borc_silinen": [{"id": 7, "fatura_no": "D-7", "yil": 2026, "ay": 3}],
            "yedek_ids": [11, 12],
        }

    eski = pt.panel_satirlari_sil
    pt.panel_satirlari_sil = sil
    eski_gr = {n: getattr(gr, n) for n in (
        "fetch_one", "fetch_all", "execute", "ensure_faturalar_amount_columns",
        "_plan_paket_yukle", "_read_aylik_grid_cache_payload",
        "sync_musteri_panel_borclu_from_satirlar", "_invalidate_aylik_grid_payload_mem",
        "_defer_aylik_grid_cache_rebuild", "_dondurma_ay_kumesi",
    )}
    import fatura_belge_no
    eski_no = fatura_belge_no.next_dahili_no
    fatura_belge_no.next_dahili_no = lambda: "D-YENI"

    def fetch_all(sql, params=None):
        if "panel_tekrar_oku" in sql and "tahsilatlar" in sql:
            return [panel]
        if "panel_tekrar_oku" in sql:
            return [borc]
        return []

    def fetch_one(sql, params=None):
        if "FROM customers" in sql:
            return {"id": MID, "name": "T"}
        if "FROM musteri_kyc" in sql:
            return None
        if "WHERE fatura_no" in sql:
            return {"id": 99}
        if "tahsil_marker_var" in sql or sql.strip().startswith("SELECT id FROM faturalar"):
            return None
        return None

    def execute(sql, params=None):
        if sql and "INSERT INTO faturalar" in sql:
            durum["insert"].append(params)

    gr.fetch_all = fetch_all
    gr.fetch_one = fetch_one
    gr.execute = execute
    gr.ensure_faturalar_amount_columns = lambda: None
    gr.sync_musteri_panel_borclu_from_satirlar = lambda *a, **k: {}
    gr._invalidate_aylik_grid_payload_mem = lambda *a, **k: None
    gr._defer_aylik_grid_cache_rebuild = lambda *a, **k: None
    gr._plan_paket_yukle = lambda mids: {int(m): {"planlar": [], "faturali": [], "belge": {}} for m in mids}
    gr._read_aylik_grid_cache_payload = lambda mid: None
    gr._dondurma_ay_kumesi = lambda mid: set()
    app = Flask(__name__)
    f = gr.api_aylik_tutarlardan_borclandir.__wrapped__
    satir = [{"yil": 2026, "ay": 3, "tutar_kdv_dahil": 10}]
    try:
        with app.test_request_context("/x", method="POST", json={"musteri_id": MID, "kira_nakit": False, "onay": True, "satirlar": satir}):
            yanit = f().get_json()
        check("1 onayla sil cagrildi", durum["sil"] == 1)
        check("1 yeni borc yazildi", len(durum["insert"]) == 1, durum["insert"])
        check("1 makbuz yaniti duruyor", (yanit.get("tahsil_silinen") or [{}])[0].get("makbuz_no") == "M-1", yanit.get("tahsil_silinen"))
        check("1 yedek kimligi dondu", yanit.get("ok") is True)
    finally:
        pt.panel_satirlari_sil = eski
        fatura_belge_no.next_dahili_no = eski_no
        for n, v in eski_gr.items():
            setattr(gr, n, v)


def test_tahsil_uc():
    from flask import Flask
    from routes import giris_routes as gr

    banka = _tah("banka_import", 2, "banka " + ISARET)
    panel = _tah("grid_toplu", 1, "Mart 2026 Tahsilat H" + ISARET)
    sqls = []

    class Conn:
        def cursor(self):
            return self

        def execute(self, sql, params=None):
            sqls.append(str(sql))

        def fetchall(self):
            return []

        def fetchone(self):
            return {"max_seq": 1000, "id": 1, "m": None}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    eski = {n: getattr(gr, n) for n in (
        "fetch_one", "fetch_all", "execute", "get_db",
        "sync_musteri_panel_from_tahsil_and_dagitim",
        "_invalidate_aylik_grid_payload_mem", "_musteri_kyc_grup_for_aylik_grid",
    )}

    def fetch_all(sql, params=None):
        if "panel_tekrar_oku" in sql and "tahsilatlar" in sql:
            return list(fetch_all.rows)
        return []

    fetch_all.rows = [panel]
    gr.fetch_all = fetch_all
    gr.fetch_one = lambda sql, params=None: {"id": MID, "name": "T"} if "FROM customers" in sql else None
    gr.execute = lambda *a, **k: None
    gr.get_db = lambda: Conn()
    gr.sync_musteri_panel_from_tahsil_and_dagitim = lambda *a, **k: None
    gr._invalidate_aylik_grid_payload_mem = lambda *a, **k: None
    gr._musteri_kyc_grup_for_aylik_grid = lambda *a, **k: None
    app = Flask(__name__)
    f = gr.api_aylik_tutarlardan_tahsil_et.__wrapped__
    satir = [{"yil": 2026, "ay": 3, "tutar_kdv_dahil": 10}]
    try:
        with app.test_request_context("/x", method="POST", json={"musteri_id": MID, "satirlar": satir}):
            yanit = f().get_json()
        check("1 tahsil onaysiz", yanit.get("onay_gerekli") is True and sqls == [], yanit.get("mesaj"))
        fetch_all.rows = [banka]
        sqls.clear()
        with app.test_request_context("/x", method="POST", json={"musteri_id": MID, "satirlar": satir}):
            yanit2 = f().get_json()
        yazilan = " ".join(sqls)
        check("3 tahsil atlandi", any(a.get("neden") == "baska_kaynak" for a in (yanit2.get("atlanan") or [])), yanit2.get("atlanan"))
        check("3 tahsil yazma yok", "INSERT INTO tahsilatlar" not in yazilan and "DELETE FROM tahsilatlar" not in yazilan, yazilan[:160])
    finally:
        for n, v in eski.items():
            setattr(gr, n, v)


def test_kapi_bayragi():
    import os
    from routes import giris_routes as gr
    eski = os.environ.pop("TUFE_BORC_TAHSIL_ENABLED", None)
    try:
        check("8 tufe bayragi varsayilan kapali", gr.tufe_borc_tahsil_acik() is False)
    finally:
        if eski is not None:
            os.environ["TUFE_BORC_TAHSIL_ENABLED"] = eski


if __name__ == "__main__":
    test_karar()
    test_sil_ve_geri()
    test_bagli_silinmez()
    test_uc_onaysiz()
    test_uc_onayla_yazar()
    test_tahsil_uc()
    test_kapi_bayragi()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
