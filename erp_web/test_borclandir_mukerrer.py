# -*- coding: utf-8 -*-
"""Grid «Borçlandır» ucu (api_aylik_tutarlardan_borclandir) mükerrer koruması. Yerel; DB ve prod yok.

(a) AUTO_INV'li ay atlanır, ikinci satır oluşmaz, tahsilat silinmez, panel senkronuna girmez
(b) AYLIK_TUTAR'lı ay eskisi gibi («zaten var» dalı: yeni satır yok, zaten_vardi=True).
    Panel satırı yokken işaretle silme sorgusu çalışmaz (Commit 3: kaynak şart).
(c) kaydı olmayan ay normal yazılır
(d) atlanan aylar cevapta listelenir (atlanan_aylar + mesaj); arayüz bunu gösterir, sayfa bozulmaz
Plansız ve planlı kartta aynı davranış.
"""
import copy
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


from flask import Flask  # noqa: E402

from routes import giris_routes as gr  # noqa: E402

MID = 4242
PLAN = [{"gecerlilik_ay": "2026-03-01", "yeni_net": 1000, "kdv_oran": 20, "yeni_brut": 1200}]
PLAN_PAYLOAD = {
    "plan_var": True,
    "aylar": [
        {"yil": 2026, "ay": 3, "tutar_kdv_dahil": 1200.0},
        {"yil": 2026, "ay": 4, "tutar_kdv_dahil": 1200.0},
        {"yil": 2026, "ay": 5, "tutar_kdv_dahil": 1200.0},
    ],
}
# 2026-03: yalniz AUTO_INV var | 2026-04: AYLIK_TUTAR var | 2026-05: hic kayit yok
MEVCUT_NOTLAR = [
    "Mart 2026 otomatik kira faturasi|AUTO_INV|2026-03|",
    "Nisan 2026 kira bedeli (KDV dahil, Aylık Tutarlar)|AYLIK_TUTAR|2026-04-01|",
]
ISTEMCI = [
    {"yil": 2026, "ay": 3, "tutar_kdv_dahil": 900.0},
    {"yil": 2026, "ay": 4, "tutar_kdv_dahil": 900.0},
    {"yil": 2026, "ay": 5, "tutar_kdv_dahil": 900.0},
]


def _norm(m):
    if isinstance(m, str):
        return {"id": 1, "notlar": m, "durum": "odenmedi", "yon": "giden", "ettn": ""}
    n, du, yo = m
    return {"id": 1, "notlar": n, "durum": du, "yon": yo, "ettn": ""}


def _icerir(mevcut, kalip):
    ic = kalip.strip("%")
    return any(ic in _norm(m)["notlar"] for m in mevcut)


KOS_YAZMA = []
ESKI_YARDIMCI_CAGRILDI = []


def kos(plan_var, satirlar=None, mevcut=None):
    mevcut = list(MEVCUT_NOTLAR if mevcut is None else mevcut)
    satirlar = copy.deepcopy(ISTEMCI if satirlar is None else satirlar)
    eski = {n: getattr(gr, n) for n in (
        "fetch_one", "fetch_all", "execute", "ensure_faturalar_amount_columns", "_plan_paket_yukle",
        "_read_aylik_grid_cache_payload", "sync_musteri_panel_borclu_from_satirlar",
        "_invalidate_aylik_grid_payload_mem", "_defer_aylik_grid_cache_rebuild")}
    import fatura_belge_no
    eski_no = fatura_belge_no.next_dahili_no
    insert, silme, senkron, yazma = [], [], [], []
    sayac = {"n": 0}

    def no():
        sayac["n"] += 1
        return "D-%d" % sayac["n"]

    def fetch_one(sql, params=None):
        if "FROM customers" in sql:
            return {"id": MID, "name": "Test"}
        if "FROM musteri_kyc" in sql:
            return None
        if "WHERE fatura_no" in sql:
            return {"id": 9000 + sayac["n"]}
        if "tahsil_marker_var" in sql:  # mevcut AYLIK_TUTAR «zaten var» sorgusu
            return ({"id": 5, "fatura_no": "X", "ettn": None, "durum": "odenmedi", "tahsil_marker_var": False}
                    if _icerir(mevcut, params[4]) else None)
        if params is not None and len(params) == 3:  # eski ortak _ay_has_aylik_borc_faturasi (artik bu uctan cagrilmaz)
            ESKI_YARDIMCI_CAGRILDI.append(params)
            return {"id": 1} if (_icerir(mevcut, params[1]) or _icerir(mevcut, params[2])) else None
        if params is not None and sql.strip().startswith("SELECT id FROM faturalar WHERE musteri_id"):
            return {"id": 2} if _icerir(mevcut, params[1]) else None  # yeni AYLIK ayirici sorgu
        return None

    def fetch_all(sql, params=None):
        if "panel_tekrar_oku" in sql:
            return []
        if "DELETE FROM tahsilatlar" in sql:
            silme.append(params)
            return []
        if "FROM faturalar" in sql and params is not None and len(params) == 3:  # dar gecerli-kayit yardimcisi
            return [_norm(m) for m in mevcut if _icerir([m], params[1]) or _icerir([m], params[2])]
        return []

    fatura_belge_no.next_dahili_no = no
    gr.ensure_faturalar_amount_columns = lambda: None
    gr.fetch_one = fetch_one
    gr.fetch_all = fetch_all
    gr.execute = lambda sql, params=None: (yazma.append(sql), insert.append(params) if "INSERT INTO faturalar" in sql else None)[1]
    gr.sync_musteri_panel_borclu_from_satirlar = lambda mid, s: senkron.append(copy.deepcopy(s)) or {}
    gr._invalidate_aylik_grid_payload_mem = lambda mid: None
    gr._defer_aylik_grid_cache_rebuild = lambda mid: None
    gr._plan_paket_yukle = lambda mids: {int(m): {"planlar": PLAN if plan_var else [], "faturali": [], "belge": {}} for m in mids}
    gr._read_aylik_grid_cache_payload = lambda mid: copy.deepcopy(PLAN_PAYLOAD) if plan_var else {"aylar": PLAN_PAYLOAD["aylar"]}
    app = Flask(__name__)
    f = gr.api_aylik_tutarlardan_borclandir.__wrapped__
    try:
        with app.test_request_context("/x", method="POST", json={"musteri_id": MID, "kira_nakit": False, "satirlar": satirlar}):
            yanit = f()
        KOS_YAZMA[:] = yazma
        return yanit.get_json(), insert, silme, senkron
    finally:
        for n, v in eski.items():
            setattr(gr, n, v)
        fatura_belge_no.next_dahili_no = eski_no


def ay_say(insert, ay_iso):
    return sum(1 for p in insert if p[7].isoformat() == ay_iso)


def silme_say(silme, ay_iso):
    return sum(1 for p in silme if ay_iso in str(p[2]))


def sunucu():
    for plan_var, et in ((False, "plansiz"), (True, "planli")):
        j, ins, sil, sen = kos(plan_var)
        brut_mart = 1200.0 if plan_var else 900.0
        check("%s: yanit ok" % et, j.get("ok") is True)
        # (a) AUTO_INV'li ay
        check("%s (a): AUTO_INV'li Mart icin yeni satir YOK" % et, ay_say(ins, "2026-03-01") == 0, ins)
        check("%s (a): atlanan_aylar == ['2026-03']" % et, j.get("atlanan_aylar") == ["2026-03"], j.get("atlanan_aylar"))
        check("%s (a): atlanan listesinde neden auto_inv_zaten_var" % et,
              any(x.get("neden") == "auto_inv_zaten_var" and x.get("ay") == 3 for x in j.get("atlanan", [])), j.get("atlanan"))
        check("%s (a): Mart icin tahsilat silinmedi" % et, silme_say(sil, "2026-03-01") == 0, sil)
        check("%s (a): Mart panel senkronuna girmedi" % et,
              len(sen) == 1 and all(not (s["yil"] == 2026 and s["ay"] == 3) for s in sen[0]), sen)
        check("%s (a): olusturulan listesinde Mart yok" % et, all(x.get("ay") != 3 for x in j.get("olusturulan", [])))
        # (d) mesaj
        check("%s (d): mesajda 'Şu aylar zaten borçlandırılmış, atlandı: 2026-03'" % et,
              "Şu aylar zaten borçlandırılmış, atlandı: 2026-03" in j.get("mesaj", ""), j.get("mesaj"))
        # (b) AYLIK_TUTAR'li ay: eski dal
        ap = [x for x in j.get("olusturulan", []) if x.get("ay") == 4]
        check("%s (b): AYLIK_TUTAR'li Nisan: zaten_vardi, yeni satir YOK" % et,
              len(ap) == 1 and ap[0].get("zaten_vardi") is True and ay_say(ins, "2026-04-01") == 0, (ap, ins))
        check("%s (b): Nisan panel satiri yokken silme sorgusu yok" % et, silme_say(sil, "2026-04-01") == 0, sil)
        check("%s (b): Nisan atlanan_aylar'da DEGIL" % et, "2026-04" not in (j.get("atlanan_aylar") or []))
        check("%s (b): Nisan panel senkronunda" % et, any(s["ay"] == 4 for s in sen[0]), sen)
        # (c) kaydi olmayan ay
        mayis = [p for p in ins if p[7].isoformat() == "2026-05-01"]
        check("%s (c): kaydi olmayan Mayis normal yazildi (tek satir, tutar dogru)" % et,
              len(mayis) == 1 and float(mayis[0][5]) == brut_mart, mayis)
        check("%s (c): Mayis olusturulan (zaten_vardi=False)" % et,
              any(x.get("ay") == 5 and x.get("zaten_vardi") is not True for x in j.get("olusturulan", [])))
        check("%s (c): Mayis panel satiri yokken silme sorgusu yok" % et, silme_say(sil, "2026-05-01") == 0)
        check("%s: toplam yazilan satir sayisi 1 (yalniz Mayis)" % et, len(ins) == 1, ins)
        # Mart tutari plan/plansiz fark etmeksizin hic yazilmadi
        check("%s: mevcut satira dokunma yok (UPDATE / faturalar DELETE yok)" % et, not any(("UPDATE" in q.upper() or "DELETE FROM FATURALAR" in q.upper()) and "musteri_aylik_grid_cache" not in q for q in KOS_YAZMA), KOS_YAZMA)

    # Eski davranis korunur: hicbir ay AUTO_INV'li degilse cevap anahtarlari/mesaj eskisi gibi
    j, ins, sil, sen = kos(False, satirlar=[ISTEMCI[1], ISTEMCI[2]])
    check("AUTO_INV'siz istek: atlanan_aylar bos, mesajda atlandi cumlesi yok",
          j.get("atlanan_aylar") == [] and "zaten borçlandırılmış" not in j.get("mesaj", ""), j.get("mesaj"))
    check("AUTO_INV'siz istek: panel senkronu tum satirlarla", len(sen) == 1 and len(sen[0]) == 2, sen)
    # Tum aylar AUTO_INV'li: hicbir sey yazilmaz
    j, ins, sil, sen = kos(False, satirlar=[ISTEMCI[0]])
    check("yalniz AUTO_INV'li ay istenirse: yazma yok, silme yok, ok=True, mesajda ay var",
          j.get("ok") is True and ins == [] and sil == [] and "2026-03" in j.get("mesaj", ""), (ins, sil, j.get("mesaj")))
    # AUTO_INV'li ayda ayrica AYLIK_TUTAR da varsa (867 benzeri): eski AYLIK dali (atlanmaz)
    j, ins, sil, sen = kos(False, satirlar=[ISTEMCI[0]], mevcut=MEVCUT_NOTLAR + ["Mart 2026 kira|AYLIK_TUTAR|2026-03-01|"])
    ap = [x for x in j.get("olusturulan", []) if x.get("ay") == 3]
    check("AUTO_INV + AYLIK_TUTAR ayni ay: eski dal (zaten_vardi), atlanan_aylar bos",
          len(ap) == 1 and ap[0].get("zaten_vardi") is True and j.get("atlanan_aylar") == [] and ins == [], (j, ins))


def gecersiz_kayitlar():
    """Gecersiz AUTO_INV kayitlari (iptal/taslak/gelen/GIB_NO_TASINDI/GIB durum: iptal) ayi atlatmaz; gecerli atlatir."""
    mart = "Mart 2026 otomatik kira faturasi|AUTO_INV|2026-03|"
    senaryolar = [
        ("iptal durumlu AUTO_INV", [(mart, "iptal", "giden")], False),
        ("taslak durumlu AUTO_INV", [(mart, "taslak", "giden")], False),
        ("gelen yonlu AUTO_INV", [(mart, "odenmedi", "gelen")], False),
        ("|GIB_NO_TASINDI| isaretli AUTO_INV", [(mart + "|GIB_NO_TASINDI|", "odenmedi", "giden")], False),
        ("'GİB durum: iptal' notlu AUTO_INV", [(mart + " GİB durum: iptal", "odenmedi", "giden")], False),
        ("gecerli AUTO_INV (odenmedi)", [(mart, "odenmedi", "giden")], True),
        ("gecerli AUTO_INV (odendi)", [(mart, "odendi", "giden")], True),
        ("iptal + gecerli AUTO_INV birlikte", [(mart, "iptal", "giden"), (mart, "odenmedi", "giden")], True),
    ]
    for plan_var, et in ((False, "plansiz"), (True, "planli")):
        for ad, mevcut, atlanmali in senaryolar:
            ESKI_YARDIMCI_CAGRILDI.clear()
            j, ins, sil, sen = kos(plan_var, satirlar=[ISTEMCI[0]], mevcut=mevcut)
            yazildi = ay_say(ins, "2026-03-01") == 1
            if atlanmali:
                ok = j.get("atlanan_aylar") == ["2026-03"] and not yazildi and silme_say(sil, "2026-03-01") == 0
            else:
                ok = j.get("atlanan_aylar") == [] and yazildi and silme_say(sil, "2026-03-01") == 0 \
                    and "zaten borçlandırılmış" not in j.get("mesaj", "")
            check("%s: %s -> %s" % (et, ad, "atlanir" if atlanmali else "atlanmaz, yazilir"), ok, (j.get("atlanan_aylar"), ins, sil))
    check("bu uc eski ortak yardimciyi cagirmiyor (davranisi degismedi)", not ESKI_YARDIMCI_CAGRILDI, ESKI_YARDIMCI_CAGRILDI)
    # ortak yardimci aynen: iptal AUTO_INV'li ayda hala True (toplu borclandirma/otomatik job davranisi)
    eski = gr.fetch_one
    gr.fetch_one = lambda sql, params=None: {"id": 1}
    try:
        check("ortak _ay_has_aylik_borc_faturasi degismedi (kayit varsa True)", gr._ay_has_aylik_borc_faturasi(MID, 2026, 3) is True)
    finally:
        gr.fetch_one = eski


def arayuz():
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8", errors="replace")
    n = len(re.findall(r"res\.j\.atlanan_aylar", html))
    check("arayuz: iki Borçlandır işleyicisi atlanan_aylar'ı okuyor", n >= 4, n)
    check("arayuz: 'zaten borçlandırılmış, atlandı' metni var", html.count("zaten borçlandırılmış, atlandı") >= 2)
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("SKIP playwright yok")
        return
    with sync_playwright() as pw:
        try:
            tarayici = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:  # pragma: no cover
            print("SKIP tarayici yok:", str(exc)[:80])
            return
        s = tarayici.new_page()
        hatalar = []
        s.on("pageerror", lambda e: hatalar.append(str(e)[:160]))
        s.set_content("<html><body></body></html>")
        # index.html betik sozdizimi: tum satir ici <script> bloklari derlenebilir mi
        bloklar = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)
        bozuk = []
        for i, b in enumerate(bloklar):
            if "{{" in b or "{%" in b:
                continue  # Jinja iceren blok burada derlenemez
            ok = s.evaluate("(src) => { try { new Function(src); return true; } catch (e) { return String(e).slice(0, 120); } }", b)
            if ok is not True:
                bozuk.append((i, ok))
        check("arayuz: Jinja'siz satir ici betik bloklari derleniyor", not bozuk, bozuk[:3])
        tarayici.close()


if __name__ == "__main__":
    sunucu()
    gecersiz_kayitlar()
    arayuz()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
