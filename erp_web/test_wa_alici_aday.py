"""Aday numara birleşimi. Sahte numaralar. Tam numara ve ad yazılmaz."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from odeme_linki import wa_kisi_secenekleri

ROOT = Path(__file__).resolve().parent


def check(ad, ok):
    if not ok:
        raise SystemExit("FAIL " + ad)
    print("ok", ad)


def _son4(tel):
    rakam = "".join(ch for ch in str(tel or "") if ch.isdigit())
    return rakam[-4:]


def _sonlar(liste):
    return [_son4(k.get("telefon")) for k in liste]


def test_birlesim_mukerrer():
    kisi = wa_kisi_secenekleri(
        [{"birincil": True, "sira": 0, "ad_soyad": "Aday", "tel": "05550001111", "tel2": "905550001111"}],
        {"yetkili_adsoyad": "Aday", "yetkili_tel": "05550001111", "yetkili_tel2": "05550003333"},
        "05550001111",
        "Aday",
        "05550004444",
    )
    check("tekil sira", _sonlar(kisi) == ["1111", "3333", "4444"])
    check("ilk yetkili", kisi[0]["etiket"] == "Aday · Yetkili")
    check("kyc kaldi", kisi[1]["etiket"] == "KYC 2. telefon")
    check("kart2 kaldi", kisi[2]["etiket"] == "Kart 2. telefon")
    check("iki yetkili yok", all("2. Yetkili" not in k["etiket"] for k in kisi))


def test_phone2():
    kisi = wa_kisi_secenekleri([], None, "05550001111", "", "05550002222")
    check("phone2 var", _sonlar(kisi) == ["1111", "2222"])
    check("phone2 etiket", [k["etiket"] for k in kisi] == ["Kart telefonu", "Kart 2. telefon"])


def test_kyc_yaninda():
    kisi = wa_kisi_secenekleri(
        [{"birincil": True, "sira": 1, "ad_soyad": "Aday", "tel": "05550001111"}],
        {"yetkili_tel": "05550002222", "yetkili_tel2": ""},
        "",
        "",
        "",
    )
    check("kyc eklendi", _sonlar(kisi) == ["1111", "2222"] and kisi[1]["etiket"] == "KYC telefonu")


def test_tavan_ve_sinir():
    kisi = wa_kisi_secenekleri(
        [
            {"birincil": True, "sira": 0, "ad_soyad": "Aday", "tel": "05550001111", "tel2": "05550002222"},
            {"birincil": False, "sira": 1, "ad_soyad": "Diger", "tel": "05550003333"},
        ],
        {"yetkili_tel": "05550004444", "yetkili_tel2": "05550005555"},
        "05550006666",
        "",
        "05550007777",
    )
    check("dort tavan", _sonlar(kisi) == ["1111", "2222", "3333", "4444"])
    js = (ROOT / "static" / "js" / "wa-alici.js").read_text(encoding="utf-8")
    soz = (ROOT / "static" / "js" / "sozlesme-whatsapp.js").read_text(encoding="utf-8")
    gonder = soz[soz.find("function gonderSecili"): soz.find("function webAc", soz.find("function gonderSecili"))]
    check(
        "js tavan",
        "liste.length >= 4" in js and "kartlar.length >= 4" in soz
        and "liste.length > 3" in gonder and "En fazla 3 alıcı" in soz
        and "secili: kartlar.length === 0" in soz,
    )


def test_etiketler():
    kisi = wa_kisi_secenekleri(
        [{"birincil": True, "sira": 0, "ad_soyad": "Aday", "tel": "05550001111", "tel2": "05550002222"}],
        {"yetkili_tel": "05550003333"},
        "05550004444",
        "Aday",
        "05550005555",
    )
    et = [k["etiket"] for k in kisi]
    check(
        "etiket takimi",
        et == ["Aday · Yetkili", "Aday · 2. telefon", "KYC telefonu", "Kart telefonu"]
        and "2. Yetkili" not in " ".join(et),
    )


def test_form_yedek():
    giris = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    i = giris.find("function girisMusteriWhatsAppAc")
    fn = giris[i: giris.find("\nfunction ", i + 10)]
    soz = (ROOT / "static" / "js" / "sozlesme-whatsapp.js").read_text(encoding="utf-8")
    check(
        "form phone2",
        "getElementById('phone2')" in fn and "telefon2: tel2" in fn and "selectedId" in fn,
    )
    check(
        "sunucu oncelik",
        "function formAday" in soz and "opts.telefon2" in soz
        and "!(j.kisiler && j.kisiler.length)" in soz
        and "acik.musteriId > 0" in soz,
    )


def test_log_temiz():
    kayit = logging.getLogger("odeme_linki")
    satirlar = []

    class _H(logging.Handler):
        def emit(self, record):
            satirlar.append(record.getMessage())

    handler = _H()
    kayit.addHandler(handler)
    kayit.setLevel(logging.INFO)
    try:
        wa_kisi_secenekleri(
            [{"birincil": True, "ad_soyad": "AdayGizli", "tel": "05550001111", "tel2": "05550002222"}],
            {"yetkili_adsoyad": "AdayGizli", "yetkili_tel": "05550003333"},
            "05550004444",
            "AdayGizli",
            "05550005555",
        )
        birlesik = "\n".join(satirlar)
        check("log yok", "AdayGizli" not in birlesik and "05550001111" not in birlesik and "905550001111" not in birlesik)
    finally:
        kayit.removeHandler(handler)


def _calistir(adlar):
    for ad in adlar:
        fn = globals().get(ad)
        if not callable(fn):
            check(ad, False)
            continue
        fn()


if __name__ == "__main__":
    secili = [a for a in sys.argv[1:] if a.startswith("test_")]
    if not secili:
        secili = [a for a in globals() if a.startswith("test_")]
    _calistir(secili)
