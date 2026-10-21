"""Sözleşme/asistan alıcı seçimi. Sahte servis. Numara ve metin yazılmaz."""
from __future__ import annotations

import json
from pathlib import Path

import routes.erp_notlar_routes as en
from odeme_linki import wa_kisi_secenekleri

ROOT = Path(__file__).resolve().parent


def check(ad, ok, detay=""):
    if not ok:
        raise SystemExit(f"FAIL {ad} {detay}")
    print("ok", ad)


BIR = "905550001111"
IKI = "905550002222"
DENEME = "dddddddd-dddd-4ddd-8ddd-ddddddddddd1"


def _musteri(_mid=7):
    return {"id": 7, "name": "••••", "telefon": BIR, "telefonlar": [BIR, IKI]}


def test_liste_ve_secim():
    check("yetkili_once", en._wa_telefon_listesi("05550001111", "05550002222")[0].endswith("1111"))
    check("tekil", len(en._wa_telefon_listesi("05550001111", "905550001111")) == 1)
    check("ikinci_gecersiz", len(en._wa_telefon_listesi("05550001111", "abc")) == 1)
    check("ikisi", len(en._wa_telefon_listesi("05550001111", "05550002222")) == 2)
    sec, hata = en._wa_secilen([BIR, IKI], {})
    check("secim_yok_hepsi", hata == "" and [s for s, _t in sec] == [1, 2])
    sec, hata = en._wa_secilen([BIR, IKI], {"secim": [2]})
    check("yalniz_ikinci", hata == "" and [s for s, _t in sec] == [2])
    sec, hata = en._wa_secilen([BIR, IKI], {"secim": []})
    check("hicbiri", hata == "En az bir numara seçin" and sec == [])
    kisi = wa_kisi_secenekleri([{"birincil": True, "sira": 0, "ad_soyad": "A", "tel": "05550001111", "tel2": "905550001111"}])
    check("kisi_tekil", len(kisi) == 1)
    secenek = en._wa_numara_secenekleri([BIR, IKI])
    ham = json.dumps(secenek)
    check("maske_secili", secenek[0]["secili"] is True and secenek[1]["secili"] is False)
    check("maske_son4", secenek[0]["maske"].endswith("1111") and BIR not in ham and IKI not in ham)


def test_gonderim():
    cagri = []

    def gonder(_uid, data, _o, _h):
        cagri.append((int(str(data.get("telefon") or "")[-1:]), data.get("deneme"), data.get("onay")))
        tel = str(data.get("telefon") or "")
        if tel == IKI and gonder.belirsiz:
            return {"ok": True, "durum": "belirsiz"}, 200
        if gonder.limit and tel == IKI:
            return {"ok": False, "mesaj": "Bugünkü gönderim sınırınız doldu."}, 429
        return {"ok": True, "durum": "gonderiliyor"}, 200

    gonder.belirsiz = False
    gonder.limit = False
    eski = (en._wa_musteri, en._wa_on_dk_var, en._WA_DENEME)
    en._wa_musteri = _musteri
    en._wa_on_dk_var = lambda *_a, **_k: False
    en._WA_DENEME.clear()
    try:
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": DENEME, "secim": [1],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("yalniz_bir", kod == 200 and len(cagri) == 1 and cagri[0][0] == 1)
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd2",
            "secim": [2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("yalniz_iki", kod == 200 and len(cagri) == 1 and cagri[0][0] == 2)
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd3",
            "secim": [1, 2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("ikisi_iki_cagri", kod == 200 and len(cagri) == 2 and cagri[0][1] != cagri[1][1])
        ham = json.dumps(govde)
        check("govdede_numara_yok", BIR not in ham and IKI not in ham)
        cagri.clear()
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd4",
            "secim": [],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        check("hic_cagri_yok", kod == 400 and cagri == [] and "seçin" in govde.get("mesaj", ""))
        gonder.belirsiz = True
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd5",
            "secim": [1, 2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        numaralar = govde.get("numaralar") or []
        check(
            "kismi_belirsiz",
            kod == 200 and numaralar[0].get("durum") == "gonderiliyor" and numaralar[1].get("durum") == "belirsiz",
        )
        gonder.belirsiz = False
        gonder.limit = True
        en._WA_DENEME.clear()
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd6",
            "secim": [1, 2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        numaralar = govde.get("numaralar") or []
        check(
            "kismi_limit",
            numaralar[0].get("durum") == "gonderiliyor"
            and "sınır" in (numaralar[1].get("mesaj") or "")
            and "sınır" in (govde.get("mesaj") or ""),
        )

        gonder.limit = False

        def yalniz_birinci(_mid, _metin, telefon=None):
            return str(telefon or "").endswith("1111")

        en._wa_on_dk_var = yalniz_birinci
        en._WA_DENEME.clear()
        once = len(cagri)
        govde, kod = en.asistan_wa_gonder(5, 7, {
            "onay": True, "mesaj": "m", "sablon": "ozel", "deneme": "dddddddd-dddd-4ddd-8ddd-ddddddddddd7",
            "secim": [1, 2],
        }, "http://yerel", "yerel", gonder_fn=gonder)
        numaralar = {n.get("sira"): n for n in (govde.get("numaralar") or [])}
        check(
            "mukerrer_numara",
            kod == 200 and numaralar[1].get("durum") == "tekrar" and numaralar[2].get("durum") == "gonderiliyor"
            and len(cagri) == once + 1,
        )
    finally:
        en._wa_musteri, en._wa_on_dk_var, en._WA_DENEME = eski
        en._WA_DENEME.clear()


def test_arayuz_kaynak():
    soz = (ROOT / "static" / "js" / "sozlesme-whatsapp.js").read_text(encoding="utf-8")
    asi = (ROOT / "static" / "js" / "erp-asistan.js").read_text(encoding="utf-8")
    check("soz_kutu", "sozlesme-wa-numaralar" in soz and "En az bir numara seçin" in soz)
    check("soz_tahsilat_ayri", "if (!acik.tahsilatId)" in soz and "tahsilat-whatsapp/gonder" in soz)
    check("soz_maske", "function maske4" in soz and "Kuyruğa alındı" in soz)
    check("asi_kutu", "erp-asistan-wa-numaralar" in asi and "secim" in asi)
    check("asi_etiket", "Kuyruğa alındı" in asi and "Gönderim sürüyor" in asi)


def main():
    test_liste_ve_secim()
    test_gonderim()
    test_arayuz_kaynak()
    print("CASE alici secim ok")


if __name__ == "__main__":
    main()
