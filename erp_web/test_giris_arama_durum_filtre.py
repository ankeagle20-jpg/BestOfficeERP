# -*- coding: utf-8 -*-
"""Giriş arama: durum=aktif|pasif|hepsi Şirket/müşteri adı autocomplete ile uyumlu.
Canlı DB / ALLOW_PROD_WRITE yok — SQL yardımcı + FE kaynak + api_musteriler mock.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from flask import Flask
from utils.musteri_gorunur import (
    musteri_liste_durum_sql,
    musteri_liste_gorunur_sql,
    request_musteri_durum_filtre,
)

ROOT = Path(__file__).resolve().parent
FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _fe_kaynaklar() -> tuple[str, str]:
    html = (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")
    tj = (ROOT / "_t.js").read_text(encoding="utf-8")
    return html, tj


def test_sql_filtreleri() -> None:
    aktif = musteri_liste_durum_sql("", durum="aktif")
    pasif = musteri_liste_durum_sql("", durum="pasif")
    hepsi = musteri_liste_durum_sql("", durum="hepsi")
    varsay = musteri_liste_gorunur_sql("", pasifleri_dahil=False)
    check("a sql aktif pasif gizler", "<> 'pasif'" in aktif)
    check("b sql pasif yalnız pasif", "= 'pasif'" in pasif and "<> 'pasif'" not in pasif)
    check("c sql hepsi pasif kosulu yok", "= 'pasif'" not in hepsi and "<> 'pasif'" not in hepsi)
    check("d sql varsayilan aktif gibi", "<> 'pasif'" in varsay)
    check("arsiv her durumda", all("arsivli" in x for x in (aktif, pasif, hepsi, varsay)))


def test_request_durum_varsayilan() -> None:
    app = Flask(__name__)
    with app.test_request_context("/x"):
        check("d durum yok None", request_musteri_durum_filtre() is None)
    with app.test_request_context("/x?durum="):
        check("d durum bos None", request_musteri_durum_filtre() is None)
    with app.test_request_context("/x?durum=xyz"):
        check("d durum gecersiz None", request_musteri_durum_filtre() is None)
    with app.test_request_context("/x?durum=AKTIF"):
        check("f buyuk harf aktif", request_musteri_durum_filtre() == "aktif")
    with app.test_request_context("/x?durum=Pasif"):
        check("f buyuk harf pasif", request_musteri_durum_filtre() == "pasif")
    with app.test_request_context("/x?durum=hepsi"):
        check("c durum hepsi", request_musteri_durum_filtre() == "hepsi")


def test_api_musteriler_mock_filtre() -> None:
    """Mock fetch_all: durum parametresine göre SQL parçası + satır etiketleri."""
    captured: list[str] = []

    rows_by_key = {
        "aktif": [
            {
                "id": 1, "name": "A Aktif Co", "musteri_adi": "Ali", "musteri_no": "1",
                "tax_number": "", "phone": "", "email": "", "office_code": "", "yetkili_kisi": "",
                "durum": "aktif", "is_active": True, "iptal": False,
                "rent_start_date": None, "kyc_sozlesme_tarihi": None,
            },
        ],
        "pasif": [
            {
                "id": 2, "name": "P Pasif Co", "musteri_adi": "Ayse", "musteri_no": "2",
                "tax_number": "", "phone": "", "email": "", "office_code": "", "yetkili_kisi": "",
                "durum": "pasif", "is_active": False, "iptal": False,
                "rent_start_date": None, "kyc_sozlesme_tarihi": None,
            },
            {
                "id": 3, "name": "I Iptal Co", "musteri_adi": "Veli", "musteri_no": "3",
                "tax_number": "", "phone": "", "email": "", "office_code": "", "yetkili_kisi": "",
                "durum": "pasif", "is_active": False, "iptal": True,
                "rent_start_date": None, "kyc_sozlesme_tarihi": None,
            },
        ],
        "hepsi": [
            {
                "id": 1, "name": "A Aktif Co", "musteri_adi": "Ali", "musteri_no": "1",
                "tax_number": "", "phone": "", "email": "", "office_code": "", "yetkili_kisi": "",
                "durum": "aktif", "is_active": True, "iptal": False,
                "rent_start_date": None, "kyc_sozlesme_tarihi": None,
            },
            {
                "id": 2, "name": "P Pasif Co", "musteri_adi": "Ayse", "musteri_no": "2",
                "tax_number": "", "phone": "", "email": "", "office_code": "", "yetkili_kisi": "",
                "durum": "pasif", "is_active": False, "iptal": False,
                "rent_start_date": None, "kyc_sozlesme_tarihi": None,
            },
        ],
    }

    def fake_fetch_all(sql, params=None):
        s = str(sql)
        captured.append(s)
        if "= 'pasif'" in s and "<> 'pasif'" not in s:
            return list(rows_by_key["pasif"])
        if "<> 'pasif'" in s:
            return list(rows_by_key["aktif"])
        return list(rows_by_key["hepsi"])

    import routes.giris_routes as gr

    app = Flask(__name__)
    app.register_blueprint(gr.bp, url_prefix="/giris")

    with mock.patch.object(gr, "fetch_all", side_effect=fake_fetch_all), \
            mock.patch("db.ensure_customers_arsivli", mock.Mock()), \
            mock.patch.object(gr, "customers_iptal_column_ready", return_value=True), \
            mock.patch.object(
                gr,
                "customers_arama_sql_params_giris_genis_tokens",
                return_value=("1=1", []),
            ):
        from routes.giris_routes import api_musteriler as view

        def run(qs):
            captured.clear()
            with app.test_request_context("/giris/api/musteriler?" + qs):
                f = view
                for _ in range(5):
                    if hasattr(f, "__wrapped__"):
                        f = f.__wrapped__
                    else:
                        break
                out = f()
                return out.get_json(), list(captured)

        try:
            data_a, sqls_a = run("q=Co&durum=aktif")
        except Exception as ex:
            check("api mock skip (auth)", False, str(ex)[:120])
            return

        ids_a = [r["id"] for r in (data_a or [])]
        check("a durum=aktif pasif yok", 2 not in ids_a and 3 not in ids_a and 1 in ids_a)
        check("a sql aktif", any("<> 'pasif'" in s for s in sqls_a))

        data_p, sqls_p = run("q=Co&durum=pasif")
        ids_p = [r["id"] for r in (data_p or [])]
        check("b durum=pasif pasif+iptal", 2 in ids_p and 3 in ids_p and 1 not in ids_p)
        check("b sql pasif", any("= 'pasif'" in s for s in sqls_p))

        data_h, sqls_h = run("q=Co&durum=hepsi")
        ids_h = [r["id"] for r in (data_h or [])]
        check("c durum=hepsi ikisi", 1 in ids_h and 2 in ids_h)
        check("c sql hepsi arsiv", any("arsivli" in s for s in sqls_h))

        data_y, sqls_y = run("q=Co")
        ids_y = [r["id"] for r in (data_y or [])]
        check("d parametre yok aktif gibi", 1 in ids_y and 2 not in ids_y)
        check("d sql varsayilan pasif gizler", any("<> 'pasif'" in s for s in sqls_y))

        data_g, _ = run("q=Co&durum=XYZ")
        ids_g = [r["id"] for r in (data_g or [])]
        check("d gecersiz aktif gibi", 1 in ids_g and 2 not in ids_g)

        ipt = next(r for r in data_p if r["id"] == 3)
        pas = next(r for r in data_p if r["id"] == 2)
        check("g iptal bayragi", bool(ipt.get("iptal")) is True)
        check("g pasif durum", str(pas.get("durum") or "").lower() == "pasif")

        src = (ROOT / "routes" / "giris_routes.py").read_text(encoding="utf-8")
        fn = src[src.find("def api_musteriler"): src.find("def _musteri_serialize_val")]
        check(
            "e baska tablo yok",
            "FROM customers" in fn
            and "tenant_" not in fn.lower().split("from customers")[0][-80:],
        )
        check("e gorunur filtre", "musteri_liste_durum_sql" in fn or "musteri_liste_gorunur_sql" in fn)
        check("f arama helper ayni", "customers_arama_sql_params_giris_genis_tokens" in fn)


def test_fe_durum_param_gonderir() -> None:
    html, tj = _fe_kaynaklar()
    for ad, src in (("index.html", html), ("_t.js", tj)):
        check(f"fe {ad} apiUrl durum", "durum=' + encodeURIComponent(sozlesmelerDurumFiltreDegeri())" in src)
        check(f"fe {ad} sirket apiUrl", "girisMusteriApiUrl(qNow)" in src)
        check(f"fe {ad} madi apiUrl", "girisMusteriApiUrl(qNow)" in src)
        check(f"fe {ad} degisti helper", "function girisDurumFiltreDegisti()" in src)
        check(
            f"fe {ad} sirket ternary",
            "typeof girisMusteriApiUrl === 'function' ? girisMusteriApiUrl(qNow)" in src
            or "typeof girisMusteriApiUrl === 'function') ? girisMusteriApiUrl(qNow)" in src,
        )
    check("fe onchange degisti", "girisDurumFiltreDegisti" in html and "sozlesmeler-durum-filtre" in html)
    check("fe etiket sinifi", "girisApiMusteriDurumSinifi" in html and "girisApiMusteriDurumOzetMetni" in html)
    check("fe iptal sinifi", "sirket-durum-etiket--iptal" in html or "girisApiMusteriIptalMi" in html)
    i = html.find("function girisDurumFiltreDegisti")
    block = html[i : i + 900]
    check("fe metin silmez", "nameEl.value =" not in block and "maEl.value =" not in block)


def test_diger_arama_kutulari() -> None:
    html, _ = _fe_kaynaklar()
    check("header musteriAra apiUrl", "girisMusteriApiUrl(arama)" in html or "var url = girisMusteriApiUrl" in html)
    check("pasifleri checkbox duruyor", 'id="giris-pasifleri-dahil"' in html)


def main() -> None:
    test_sql_filtreleri()
    test_request_durum_varsayilan()
    test_api_musteriler_mock_filtre()
    test_fe_durum_param_gonderir()
    test_diger_arama_kutulari()
    if FAILS:
        print("FAIL", len(FAILS))
        for ad in FAILS:
            print(" -", ad)
        raise SystemExit(1)
    print("test_giris_arama_durum_filtre ok")


if __name__ == "__main__":
    main()
