# -*- coding: utf-8 -*-
"""WA kuyruk: okuma asimi 30 sn, /send zaman asiminda 'belirsiz' sinifi,
not akisinda 'Kuyruga alindi' etiketi, idempotency anahtar guvencesi."""
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)
os.environ["BESTOFFICE_HTTP_FILE_LOG"] = "0"

FAILS = []


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:200]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


class SahteYan:
    def __init__(self, govde, kod=200, content=None):
        self.status_code = kod
        self._g = govde
        self.content = content if content is not None else b'{"ok": true}'

    def json(self):
        return self._g


def _post_kaydedici(kayit, davranis="ok"):
    import requests as rq

    gercek = rq.post

    def sahte(url, **kw):
        kayit.append(kw)
        if davranis == "timeout":
            raise rq.exceptions.ReadTimeout("yavas")
        return SahteYan({"ok": True, "oge": [{"durum": "bekliyor"}]})

    rq.post = sahte
    return gercek


def _geri_yukle():
    import requests as rq
    import importlib

    importlib.reload(rq)


def test_zaman_asimi_sozlesme():
    import sozlesme_whatsapp as sw

    kayit = []
    gercek = _post_kaydedici(kayit)
    try:
        out = sw._kuyruk_varsayilan("905000000000", "merhaba", "szwa:d:test")
    finally:
        import requests as rq

        rq.post = gercek
    check("sozlesme_kuyruk_durum", out.get("durum") == "kuyrukta", out)
    check("sozlesme_timeout_30", kayit and kayit[0].get("timeout") == (3, 30),
          kayit[0].get("timeout") if kayit else None)


def test_anahtar_idempotency():
    import sozlesme_whatsapp as sw

    deneme = str(uuid.uuid4())
    a1 = sw.deneme_anahtar(deneme)
    a2 = sw.deneme_anahtar(deneme)
    check("anahtar_deterministik", a1 == a2 and a1.startswith("szwa:d:"), a1)

    # Timeout sonrasi tekrar ayni anahtarla gitmeli (servis tekrar eler).
    import requests as rq

    giden = []

    def sahte(url, **kw):
        giden.append((kw.get("json") or {}).get("liste") or [])
        raise rq.exceptions.ReadTimeout("yavas")

    gercek = rq.post
    rq.post = sahte
    try:
        out1 = sw._kuyruk_varsayilan("905000000000", "m", a1)
        out2 = sw._kuyruk_varsayilan("905000000000", "m", a1)
    finally:
        rq.post = gercek
    k1 = giden[0][0].get("anahtar") if giden and giden[0] else None
    k2 = giden[1][0].get("anahtar") if len(giden) > 1 else None
    check("timeout_belirsiz", out1.get("durum") == "belirsiz" and out2.get("durum") == "belirsiz",
          (out1, out2))
    check("tekrar_ayni_anahtar", k1 == a1 and k2 == a1, (k1, k2))


def test_not_akisi():
    from routes import erp_notlar_routes as en
    import requests as rq

    kayit = []
    gercek = _post_kaydedici(kayit)
    try:
        sonuc, tur = en._wa_node_gonder(42, "905000000000", "m", "public")
    finally:
        rq.post = gercek
    check("not_kuyruk_ok", (sonuc, tur) == ("ok", ""), (sonuc, tur))
    check("not_timeout_30", kayit and kayit[0].get("timeout") == (3, 30),
          kayit[0].get("timeout") if kayit else None)
    # anahtar: {schema}:{nid} — tekrar ayni uretilir (servis tarafi tekrar kabul eder)
    liste = (kayit[0].get("json") or {}).get("liste") or []
    check("not_anahtar_bicimi", liste and liste[0].get("anahtar") == "public:42", liste)

    row = {"whatsapp_gonderilsin": True, "whatsapp_gonderim_durumu": "gonderildi",
           "whatsapp_gonderildi_mi": True}
    check("etiket_kuyruga_alindi", en._wa_durum_etiket(row) == "Kuyruğa alındı",
          en._wa_durum_etiket(row))
    row2 = {"whatsapp_gonderilsin": True, "whatsapp_gonderim_durumu": "",
            "whatsapp_gonderildi_mi": True}
    check("etiket_fallback_kuyruk", en._wa_durum_etiket(row2) == "Kuyruğa alındı",
          en._wa_durum_etiket(row2))
    row3 = {"whatsapp_gonderilsin": True, "whatsapp_gonderim_durumu": "belirsiz"}
    check("etiket_belirsiz_ayni", "doğrulanamadı" in en._wa_durum_etiket(row3),
          en._wa_durum_etiket(row3))


def test_toplu_send_belirsiz():
    import requests as rq
    from flask import Flask
    from auth import User, login_manager

    import ay_sinif
    ay_sinif.siniflari_yukle = lambda mids: {}
    ay_sinif.kilitli_musteriler = lambda mids: set()
    ay_sinif.gonderime_izin = lambda s, o, k: (True, "")

    from routes import whatsapp_routes
    # Fonksiyon-ici import modulu okur; modul duzeyi requests'i degistir.

    def sahte_post(url, **kw):
        raise rq.exceptions.ReadTimeout("yavas")

    gercek = rq.post
    rq.post = sahte_post

    @login_manager.user_loader
    def _load(_uid):
        return User(id=1, username="t", full_name="T", role="admin",
                    aktif_mi=True, security_stamp="test-stamp")

    try:
        app = Flask(__name__)
        app.secret_key = "test"
        login_manager.init_app(app)
        app.register_blueprint(whatsapp_routes.bp)
        client = app.test_client()
        with client.session_transaction() as s:
            s["_user_id"] = "1"
            s["_fresh"] = True
        r = client.post("/whatsapp/api/gonder", json={
            "liste": [{"musteri_id": 7, "telefon": "905000000000", "mesaj": "m"}],
        })
    finally:
        rq.post = gercek
    govde = r.get_json(silent=True) or {}
    check("send_http_200", r.status_code == 200, r.status_code)
    check("send_belirsiz_sinif", govde.get("durum") == "belirsiz", govde)
    check("send_mesaj_sinif", "doğrulanamadı" in str(govde.get("mesaj") or ""), govde)
    check("send_ok_false", govde.get("ok") is False, govde)


def main():
    test_zaman_asimi_sozlesme()
    test_anahtar_idempotency()
    test_not_akisi()
    test_toplu_send_belirsiz()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
