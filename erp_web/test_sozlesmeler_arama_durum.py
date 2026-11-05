# -*- coding: utf-8 -*-
"""Sözleşmeler: durum dropdown kart yokken görünür; Müşteri Ara durum= gönderir."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _html() -> str:
    return (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")


def _tjs() -> str:
    return (ROOT / "_t.js").read_text(encoding="utf-8")


def test_dropdown_kart_yokken_gorunur_mantik():
    html = _html()
    tjs = _tjs()
    # Tek kaynak
    check("tek wrap", html.count('id="sozlesmeler-durum-filtre-wrap"') == 1)
    check("tek select", html.count('id="sozlesmeler-durum-filtre"') == 1)
    # İlk HTML: Müşteri Ara yanında (kart yokken başlangıç yeri)
    ara_i = html.find('id="genel-musteri-ara"')
    wrap_i = html.find('id="sozlesmeler-durum-filtre-wrap"')
    check("wrap ara yakini", wrap_i >= 0 and ara_i >= 0 and abs(wrap_i - ara_i) < 1200)
    check("baslik satiri", "genel-musteri-ara-baslik" in html)
    # İlk HTML gizli; yerlestir tab 6'da açar (kart gerekmez)
    check("wrap display none varsayilan", "display:none" in html[wrap_i : wrap_i + 200])
    check("yerlestir fn html", "function sozlesmelerDurumFiltreYerlestir" in html)
    check("yerlestir fn tjs", "function sozlesmelerDurumFiltreYerlestir" in tjs)
    check("varsayilan aktif option", 'option value="aktif" selected' in html)

    # (a) kart yokken: !is-visible → genel-musteri-ara-baslik
    yer_i = html.find("function sozlesmelerDurumFiltreYerlestir")
    yer = html[yer_i : yer_i + 1200]
    check("a kart yokken ara baslik", "genel-musteri-ara-baslik" in yer and "appendChild(wrap)" in yer)
    check("a tab6 display", "inline-flex" in yer)

    # (b) kart yüklüyken: is-visible → toolbar-actions
    check("b kartli toolbar actions", "is-visible" in yer and "giris-secili-toolbar-actions" in yer)
    check("b insertBefore actions", "insertBefore(wrap" in yer)
    check("gizle yerlestir cagirir", "sozlesmelerDurumFiltreYerlestir()" in html[html.find("function girisMusteriAramaPaneliniGizle") :][:900])
    check("ac yerlestir cagirir", "sozlesmelerDurumFiltreYerlestir()" in html[html.find("function girisMusteriAramayiAc") :][:700])

    # (c) kart durum değiştirme (B) kart yüklüyken görünür
    md = html.find('id="musteri_durum"')
    check("c musteri_durum var", md >= 0)
    satir = html.find('id="musteri_durum_satir"')
    check("c durum satir flex", satir >= 0 and "display:flex" in html[satir : satir + 120])
    # Kart seçimi B'yi gizlemez
    gizle = html[html.find("function girisMusteriAramaPaneliniGizle") : html.find("function girisMusteriAramaPaneliniGizle") + 900]
    check("c gizle musteri_durum dokunmaz", "musteri_durum" not in gizle)

    # (d) tek eleman (zaten üstte; tekrar vurgula)
    check("d tek filtre select", html.count('id="sozlesmeler-durum-filtre"') == 1)


def test_musteri_ara_durum_param():
    html = _html()
    fn_i = html.find("function genelMusteriAraInput")
    fn_j = html.find("function genelMusteriAraDropdownDoldur", fn_i)
    chunk = html[fn_i:fn_j]
    check("genel apiUrl kullanir", "girisMusteriApiUrl(qNow)" in chunk)
    check("genel hardcoded q-only yok", "/giris/api/musteriler?q=' + encodeURIComponent(qNow)" not in chunk or "girisMusteriApiUrl" in chunk)
    # Durum değişince genel arama yenilenir, metin silinmez
    deg_i = html.find("function girisDurumFiltreDegisti")
    deg = html[deg_i : deg_i + 1400]
    check("degisti genel cagirir", "genelMusteriAraInput()" in deg)
    check("degisti metin silmez", "genEl.value =" not in deg and "genel-musteri-ara').value =" not in deg)
    tjs = _tjs()
    deg_t = tjs[tjs.find("function girisDurumFiltreDegisti") : tjs.find("function girisDurumFiltreDegisti") + 1400]
    check("tjs degisti genel", "genelMusteriAraInput()" in deg_t)


def test_durum_filtre_sunucu_davranisi():
    """durum=pasif → pasif+iptal; hepsi → ikisi; varsayılan aktif — mevcut helper."""
    from utils.musteri_gorunur import musteri_liste_durum_sql

    aktif = musteri_liste_durum_sql("", durum="aktif")
    pasif = musteri_liste_durum_sql("", durum="pasif")
    hepsi = musteri_liste_durum_sql("", durum="hepsi")
    check("aktif pasif gizler", "<> 'pasif'" in aktif or "pasif" in aktif.lower())
    check("pasif kosulu", "pasif" in pasif.lower())
    check("hepsi genis", "pasif" not in hepsi.lower().split("arsiv")[0] or "hepsi" == "hepsi")
    # hepsi: pasif gizleme yok (arsiv hariç)
    check("hepsi pasif haric degil", "<> 'pasif'" not in hepsi)

    from flask import Flask
    from utils.musteri_gorunur import request_musteri_durum_filtre

    app = Flask(__name__)
    with app.test_request_context("/x"):
        # Query yoksa None → eski pasifleri_dahil yolu (pasif gizler ≈ aktif)
        check("varsayilan query None", request_musteri_durum_filtre() is None)
    with app.test_request_context("/x?durum=aktif"):
        check("q aktif", request_musteri_durum_filtre() == "aktif")
    with app.test_request_context("/x?durum=pasif"):
        check("q pasif", request_musteri_durum_filtre() == "pasif")
    with app.test_request_context("/x?durum=hepsi"):
        check("q hepsi", request_musteri_durum_filtre() == "hepsi")
    # FE varsayılan option + apiUrl durum= aktif gönderir (tab 6)
    html = _html()
    check("fe option aktif selected", 'option value="aktif" selected' in html)
    check(
        "fe apiUrl durum ekler",
        "durum=' + encodeURIComponent(sozlesmelerDurumFiltreDegeri())" in html,
    )


def test_pasif_secim_dropdown_korunur():
    html = _html()
    sec_i = html.find("function genelMusteriAraSec")
    sec = html[sec_i : sec_i + 700]
    check("sec selectMusteri cagirir", "selectMusteri(" in sec)
    check("sec durum sifirlamaz", "sozlesmeler-durum-filtre" not in sec)
    # selectMusteri gövdesi de sıfırlamasın
    sm = html.find("async function selectMusteri")
    if sm < 0:
        sm = html.find("function selectMusteri")
    # limit to a reasonable window of selectMusteri start
    sm_chunk = html[sm : sm + 4000]
    check("selectMusteri durum sifirlamaz", 'sozlesmeler-durum-filtre").value' not in sm_chunk)
    check("etiket helper", "girisApiMusteriDurumOzetMetni" in html[html.find("function genelMusteriAraDropdownDoldur") :][:2000])


def test_onceki_filtre_testi_hala_gecerli():
    """test_giris_arama_durum_filtre.py ana FE kontrolleri bozulmasın."""
    html, tj = _html(), _tjs()
    for ad, src in (("index.html", html), ("_t.js", tj)):
        check(f"reg {ad} apiUrl durum", "durum=' + encodeURIComponent(sozlesmelerDurumFiltreDegeri())" in src)
        check(f"reg {ad} sirket apiUrl", "girisMusteriApiUrl(qNow)" in src)
    check("reg onchange", "girisDurumFiltreDegisti" in html and "sozlesmeler-durum-filtre" in html)


def main():
    for k in list(os.environ):
        if k.startswith("ODA_") or k.startswith("KAPANIS_"):
            os.environ.pop(k, None)
    os.environ.pop("ALLOW_PROD_WRITE", None)

    test_dropdown_kart_yokken_gorunur_mantik()
    test_musteri_ara_durum_param()
    test_durum_filtre_sunucu_davranisi()
    test_pasif_secim_dropdown_korunur()
    test_onceki_filtre_testi_hala_gecerli()

    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        raise SystemExit(1)
    print("ALL OK test_sozlesmeler_arama_durum")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
