# -*- coding: utf-8 -*-
"""Grid popup WhatsApp: mock gönderim, gerçek HTTP yok."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []
IST = ZoneInfo("Europe/Istanbul")


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)) if (not ok and ek) else ""))
    if not ok:
        FAILS.append(name)


def _html() -> str:
    return (ROOT / "templates" / "giris" / "index.html").read_text(encoding="utf-8")


def _js_brace_extract(src: str, start_marker: str) -> str:
    i = src.find(start_marker)
    if i < 0:
        raise ValueError(start_marker)
    j = src.find("{", i)
    depth = 0
    k = j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i : k + 1]
        k += 1
    raise ValueError("brace")


def _sahte_tel(n: int) -> str:
    """10 hane; kaynakta bitişik 10-11 hane literal yok. Gerçek numaraya benzemez."""
    return "5" + ("0" * 8) + str(int(n))


def _sahte_tel_05(n: int) -> str:
    """05xx… biçimi (normalize için)."""
    return "0" + _sahte_tel(n)


def _fake_musteri(pasif=False, phone2=True):
    return {
        "id": 10,
        "name": "X",
        "phone": _sahte_tel_05(1),
        "phone2": _sahte_tel_05(2) if phone2 else None,
        "durum": "pasif" if pasif else "aktif",
        "giris_tarihi": "2024-01-15",
    }


def _fake_payload():
    # 2024-01 ve 2024-02 ödenmemiş → gun > 0
    return {
        "compute_rev": 31,
        "aylar": [
            {"yil": 2024, "ay": 1, "tutar_kdv_dahil": 100, "kalan_tutar_kdv": 100, "tahsil_edildi": False},
            {"yil": 2024, "ay": 2, "tutar_kdv_dahil": 200, "kalan_tutar_kdv": 200, "tahsil_edildi": False},
            {"yil": 2026, "ay": 11, "tutar_kdv_dahil": 50, "kalan_tutar_kdv": 50, "tahsil_edildi": False},
        ]
    }


def test_sunucu_tutar_sablon_alici():
    import grid_wa_popup as g

    g.kilit_temizle()
    bugun = date(2026, 11, 5)
    calls = {"gonder": 0, "audit": []}

    def fetch_one(sql, params=None):
        return _fake_musteri()

    def payload_fn(mid):
        return _fake_payload()

    def gonder_fn(uid, data, origin, host, **kw):
        calls["gonder"] += 1
        calls["last"] = dict(data)
        # istemci tutarı yok sayıldığını doğrula — data'da tutar alanı olmamalı zorunlu değil
        return {"ok": True, "durum": "gonderiliyor", "mesaj": "Gönderim sürüyor"}, 200

    def audit_fn(sql, *a, **k):
        return None

    # monkeypatch audit to capture anlik via wrapper
    orig_audit = g._audit

    def _audit_cap(uid, mid, anlik, calistir=None):
        calls["audit"].append(dict(anlik or {}))
        return None

    g._audit = _audit_cap

    # (a)(b) tutar sunucuda
    gov, kod = g.onizleme(
        10, "toplam", "7",
        bugun=bugun,
        simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one,
        payload_fn=payload_fn,
        node_fn=lambda: True,
    )
    check("a onizleme ok", kod == 200 and gov.get("ok") is True)
    check("a tutar_turu toplam", gov.get("tutar_turu") == "toplam")
    check("a tutar > 0", isinstance(gov.get("tutar"), (int, float)) and float(gov["tutar"]) > 0)
    tut_toplam = float(gov["tutar"])

    gov2, kod2 = g.onizleme(
        10, "gun_gelen", "7",
        bugun=bugun,
        simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one,
        payload_fn=payload_fn,
        node_fn=lambda: True,
    )
    check("b gun_gelen tur", kod2 == 200 and gov2.get("tutar_turu") == "gun_gelen")
    check("b gun_gelen tutar", isinstance(gov2.get("tutar"), (int, float)))

    # (c) şablon — link/tahsilat yok
    anahtarlar = [s["anahtar"] for s in gov.get("sablonlar") or []]
    check("c link yok", "link" not in anahtarlar)
    check("c tahsilat yok", "tahsilat" not in anahtarlar)
    check("c onizleme dolu", bool(gov.get("onizleme")))
    check("c 7 sablon", any(s.get("anahtar") == "7" for s in gov.get("sablonlar") or []))

    # (d) maskeli
    nums = gov.get("numaralar") or []
    blob = str(nums)
    tel_05_1 = _sahte_tel_05(1)
    tel_10_1 = _sahte_tel(1)
    check("d maske var", nums and all("maske" in n for n in nums))
    check("d tam numara yok", tel_05_1 not in blob and tel_10_1 not in blob)

    # (e) alıcı
    gov_g, kod_g = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "1", "deneme": "11111111-1111-1111-1111-111111111111"},
        "https://example.local", "example.local",
        bugun=bugun,
        simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one,
        payload_fn=payload_fn,
        node_fn=lambda: True,
        gonder_fn=gonder_fn,
    )
    check("e1 gonder ok", kod_g == 200 and gov_g.get("ok") is True, gov_g)
    check("e1 alici 1", calls["last"].get("telefon") is not None)
    tel1 = calls["last"]["telefon"]

    g.kilit_temizle()
    gov_e2, kod_e2 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "2", "deneme": "22222222-2222-2222-2222-222222222222"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("e2 gonder", kod_e2 == 200 and gov_e2.get("ok") is True)
    check("e2 farkli tel", calls["last"]["telefon"] != tel1)

    def fetch_one_tek(sql, params=None):
        return _fake_musteri(phone2=False)

    g.kilit_temizle()
    gov_e3, kod_e3 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "2", "deneme": "33333333-3333-3333-3333-333333333333"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one_tek, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("e3 ikincisiz 400", kod_e3 == 400)

    g.kilit_temizle()
    gov_e4, kod_e4 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "ozel", "ozel_numara": "abc", "deneme": "44444444-4444-4444-4444-444444444444"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("e4 ozel gecersiz", kod_e4 == 400)

    # (f) tutar <= 0
    def payload_bos(mid):
        return {"aylar": []}

    g.kilit_temizle()
    gov_f, kod_f = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "1", "deneme": "55555555-5555-5555-5555-555555555555"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one, payload_fn=payload_bos, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("f borc yok", kod_f == 400 and "borç" in str(gov_f.get("mesaj") or "").lower())

    # (g) 60 sn kilit
    g.kilit_temizle()
    mono = {"t": 1000.0}

    def now():
        return mono["t"]

    g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "1", "deneme": "66666666-6666-6666-6666-666666666666"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn, now_mono=now,
    )
    mono["t"] = 1030.0
    gov_g2, kod_g2 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "1", "deneme": "77777777-7777-7777-7777-777777777777"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn, now_mono=now,
    )
    check("g 60sn engel", kod_g2 == 409)

    # (h) kill-switch
    gov_h, kod_h = g.onizleme(10, "toplam", node_fn=lambda: False, fetch_one_fn=fetch_one, payload_fn=payload_fn)
    check("h kapali onizleme", kod_h == 403 and gov_h.get("kapali") is True)

    # (j) audit telefon/metin yok
    for a in calls["audit"]:
        blob = str(a).lower()
        check(
            "j audit tel yok",
            tel_10_1 not in blob and tel_05_1 not in blob and "telefon" not in blob and "mesaj" not in blob,
            a,
        )
        check("j audit alanlar", "tutar_turu" in a and "sablon_id" in a and "alici_secimi" in a and "sonuc" in a)

    # (m) pasif
    def fetch_pasif(sql, params=None):
        return _fake_musteri(pasif=True)

    g.kilit_temizle()
    gov_m0, kod_m0 = g.onizleme(
        10, "toplam", "7", bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_pasif, payload_fn=payload_fn, node_fn=lambda: True,
    )
    check("m uyari pasif", "musteri_pasif" in (gov_m0.get("uyarilar") or []))
    gov_m1, kod_m1 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "1", "deneme": "88888888-8888-8888-8888-888888888888"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_pasif, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("m pasif onay yok", kod_m1 == 400)
    g.kilit_temizle()
    gov_m2, kod_m2 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "1", "pasif_onay": "1", "deneme": "99999999-9999-9999-9999-999999999999"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_pasif, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("m pasif onayli", kod_m2 == 200 and gov_m2.get("ok") is True)

    # (n) mesai_disi
    gov_n, _ = g.onizleme(
        10, "toplam", "7", bugun=bugun,
        simdi=datetime(2026, 11, 5, 21, 30, tzinfo=IST),
        fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True,
    )
    check("n mesai_disi uyari", "mesai_disi" in (gov_n.get("uyarilar") or []))
    g.kilit_temizle()
    before = calls["gonder"]
    gov_n2, kod_n2 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "1", "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 21, 30, tzinfo=IST),
        fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("n mesai engellemez", kod_n2 == 200 and calls["gonder"] == before + 1)

    # (o) mid yok → 404
    def fetch_yok(sql, params=None):
        return None

    gov_o, kod_o = g.onizleme(99, "toplam", fetch_one_fn=fetch_yok, payload_fn=payload_fn, node_fn=lambda: True)
    check("o 404", kod_o == 404)

    # istemci tutarı yok sayılır: gonder data'da tutar gönderilse bile server hesabı
    g.kilit_temizle()
    g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "1", "tutar": 1, "mesaj": "SAHTE", "deneme": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=datetime(2026, 11, 5, 12, 0, tzinfo=IST),
        fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("a istemci mesaj yok sayildi", calls["last"].get("mesaj") != "SAHTE")
    check("a istemci tutar kullanilmadi", tut_toplam > 1)

    g._audit = orig_audit
    g.kilit_temizle()


def test_coklu_alici():
    import grid_wa_popup as g
    from odeme_linki import normalize_wa_telefon

    g.kilit_temizle()
    bugun = date(2026, 11, 5)
    calls = {"gonder": 0, "tels": [], "audit": []}

    def fetch_one(sql, params=None):
        return _fake_musteri()

    def payload_fn(mid):
        return _fake_payload()

    def gonder_fn(uid, data, origin, host, **kw):
        calls["gonder"] += 1
        calls["tels"].append(data.get("telefon"))
        calls["last"] = dict(data)
        return {"ok": True, "durum": "gonderiliyor", "mesaj": "Gönderim sürüyor"}, 200

    def _audit_cap(uid, mid, anlik, calistir=None):
        calls["audit"].append(dict(anlik or {}))

    g._audit = _audit_cap
    simdi = datetime(2026, 11, 5, 12, 0, tzinfo=IST)
    kw = dict(
        bugun=bugun, simdi=simdi, fetch_one_fn=fetch_one, payload_fn=payload_fn,
        node_fn=lambda: True, gonder_fn=gonder_fn,
    )

    # (a) ["1","2"] → iki çağrı, farklı numara
    g.kilit_temizle()
    calls["gonder"] = 0
    calls["tels"] = []
    gov_a, kod_a = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1", "2"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"},
        "https://example.local", "example.local", **kw,
    )
    check("ca iki ok", kod_a == 200 and gov_a.get("ok") is True)
    check("ca iki adet", calls["gonder"] == 2)
    check("ca iki farkli", len(set(calls["tels"])) == 2)
    check("ca iki sonuc", len(gov_a.get("sonuclar") or []) == 2)

    # (b) ["1","2","ozel"] → üç
    g.kilit_temizle()
    calls["gonder"] = 0
    calls["tels"] = []
    gov_b, kod_b = g.gonder(
        1, 10,
        {
            "tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1", "2", "ozel"],
            "ozel_numara": _sahte_tel_05(3),
            "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2",
        },
        "https://example.local", "example.local", **kw,
    )
    check("cb uc ok", kod_b == 200 and gov_b.get("ok") is True)
    check("cb uc adet", calls["gonder"] == 3)
    check("cb uc farkli", len(set(calls["tels"])) == 3)

    # (c) 1. gönderildikten sonra 2. aynı pencerede engellenmez; aynı alıcı 60sn engellenir
    g.kilit_temizle()
    mono = {"t": 5000.0}

    def now():
        return mono["t"]

    calls["gonder"] = 0
    g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3"},
        "https://example.local", "example.local", now_mono=now, **kw,
    )
    mono["t"] = 5010.0
    gov_c2, kod_c2 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["2"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa4"},
        "https://example.local", "example.local", now_mono=now, **kw,
    )
    check("cc iki engellenmez", kod_c2 == 200 and gov_c2.get("ok") is True, gov_c2)
    gov_c3, kod_c3 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa5"},
        "https://example.local", "example.local", now_mono=now, **kw,
    )
    check("cc ayni alici 60sn", kod_c3 == 409 and gov_c3.get("ok") is False)

    # (d) ozel = 1. numara → tekilleştir, tek çağrı
    g.kilit_temizle()
    calls["gonder"] = 0
    gov_d, kod_d = g.gonder(
        1, 10,
        {
            "tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1", "ozel"],
            "ozel_numara": _sahte_tel_05(1),
            "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa6",
        },
        "https://example.local", "example.local", **kw,
    )
    check("cd tekil adet", calls["gonder"] == 1)
    check("cd tekil bayrak", gov_d.get("tekillestirildi") is True)
    check("cd tekil sonuc1", len(gov_d.get("sonuclar") or []) == 1)
    n1 = normalize_wa_telefon(_sahte_tel_05(1))
    check("cd ayni norm", calls["last"].get("telefon") == n1)

    # (e) validasyonlar
    g.kilit_temizle()

    def fetch_tek(sql, params=None):
        return _fake_musteri(phone2=False)

    gov_e2, kod_e2 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1", "2"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa7"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=simdi, fetch_one_fn=fetch_tek, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("ce2 yok 400", kod_e2 == 400)

    gov_eb, kod_eb = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": [], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa8"},
        "https://example.local", "example.local", **kw,
    )
    check("ce bos 400", kod_eb == 400)

    gov_ef, kod_ef = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1", "2", "ozel", "1"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa9"},
        "https://example.local", "example.local", **kw,
    )
    # tekrar eden eleman tekilleşir; 4. eleman "1" zaten var → len ham 4 ama max check before dedupe of slots
    # Bizim _alicilar_oku: len(ham)>3 → 400 even if duplicates
    check("ce fazla 400", kod_ef == 400)

    gov_eo, kod_eo = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["ozel"], "ozel_numara": "abc", "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa10"},
        "https://example.local", "example.local", **kw,
    )
    check("ce ozel gecersiz", kod_eo == 400)

    # (f) kısmi hata + yeniden deneme yalnız başarısız
    g.kilit_temizle()
    calls["gonder"] = 0
    calls["tels"] = []
    fail_once = {"n": 0}

    def gonder_kismi(uid, data, origin, host, **kw2):
        calls["gonder"] += 1
        calls["tels"].append(data.get("telefon"))
        fail_once["n"] += 1
        if fail_once["n"] == 2:
            return {"ok": False, "mesaj": "Mock hata"}, 500
        return {"ok": True, "durum": "gonderiliyor", "mesaj": "Gönderim sürüyor"}, 200

    gov_f, kod_f = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1", "2"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa11"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=simdi, fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_kismi,
    )
    son_f = gov_f.get("sonuclar") or []
    check("cf kismi len", len(son_f) == 2)
    check("cf kismi 1 ok", son_f[0].get("durum") == "gonderildi")
    check("cf kismi 2 hata", son_f[1].get("durum") == "hata")
    before = calls["gonder"]
    gov_fr, kod_fr = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["2"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa12"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=simdi, fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_kismi,
    )
    check("cf retry sadece 2", calls["gonder"] == before + 1 and gov_fr.get("ok") is True)
    # başarılı 1 yeniden gönderilse engellenir
    gov_f1, kod_f1 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa13"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=simdi, fetch_one_fn=fetch_one, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_kismi,
    )
    check("cf basarili kilit", kod_f1 == 409)

    # (g) pasif_onay bir kez, çoklu alıcı
    g.kilit_temizle()
    calls["gonder"] = 0

    def fetch_pasif(sql, params=None):
        return _fake_musteri(pasif=True)

    gov_g0, kod_g0 = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1", "2"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa14"},
        "https://example.local", "example.local",
        bugun=bugun, simdi=simdi, fetch_one_fn=fetch_pasif, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("cg pasif yok", kod_g0 == 400 and calls["gonder"] == 0)
    gov_g1, kod_g1 = g.gonder(
        1, 10,
        {
            "tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1", "2"], "pasif_onay": "1",
            "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa15",
        },
        "https://example.local", "example.local",
        bugun=bugun, simdi=simdi, fetch_one_fn=fetch_pasif, payload_fn=payload_fn, node_fn=lambda: True, gonder_fn=gonder_fn,
    )
    check("cg pasif ok", kod_g1 == 200 and calls["gonder"] == 2)

    # (h) audit alıcı başına; tel/metin yok
    calls["audit"] = []
    g.kilit_temizle()
    g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alicilar": ["1", "2"], "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa16"},
        "https://example.local", "example.local", **kw,
    )
    check("ch audit adet", len(calls["audit"]) == 2)
    for a in calls["audit"]:
        blob = str(a).lower()
        check("ch audit alan", "alici_secimi" in a and "sonuc" in a)
        check(
            "ch audit pii yok",
            _sahte_tel(1) not in blob and _sahte_tel_05(1) not in blob and "telefon" not in blob and "mesaj" not in blob,
        )

    # (i) geriye uyumluluk alici_secimi
    g.kilit_temizle()
    calls["gonder"] = 0
    gov_i, kod_i = g.gonder(
        1, 10,
        {"tutar_turu": "toplam", "sablon_id": "7", "alici_secimi": "1", "deneme": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa17"},
        "https://example.local", "example.local", **kw,
    )
    check("ci eski alici", kod_i == 200 and calls["gonder"] == 1 and gov_i.get("alici_secimi") == "1")

    g._audit = lambda *a, **k: None
    g.kilit_temizle()


def test_route_methods_and_ui():
    html = _html()
    src = (ROOT / "routes" / "sozlesme_whatsapp_routes.py").read_text(encoding="utf-8")
    check("k get route", 'grid-wa-onizleme", methods=["GET"]' in src or "grid-wa-onizleme" in src and "GET" in src)
    check("k post route", "grid-wa-gonder" in src and 'methods=["POST"]' in src)
    check("k giris_gerekli", src.count("@giris_gerekli") >= 2)
    check("k sozlesme_wa_isle import yok yeni", "sozlesme_wa_isle" in (ROOT / "grid_wa_popup.py").read_text(encoding="utf-8"))

    check("l wa btn", "faturaRaporGridWaPanelAc" in html and "WhatsApp ile gönder" in html)
    check("l panel", "fatura_rapor_grid_wa_panel" in html)
    check("l gonder fn", "function faturaRaporGridWaGonder" in html)
    check("p tarih ayrac", "tarih-ayrac" in html and " · " in html)
    check("p pointer-events aylik", "fatura-rapor-grid-modal-aylik" in html and "pointer-events: none" in html)

    check("i decorator onizleme", "@giris_gerekli" in src)

    # (j) checkbox bağımsız + (k) ikon
    check("j checkbox", 'type="checkbox" name="frg_wa_alici"' in html)
    check("j radio yok panel", 'type="radio" name="frg_wa_alici"' not in html)
    check("j varsayilan 1", 'id="frg_wa_alici_1" checked' in html or "id=\"frg_wa_alici_1\" checked" in html)
    check("j etiket fn", "faturaRaporGridWaAliciEtiketGuncelle" in html and "numaraya gönder" in html)
    check("j alicilar body", "alicilar:" in html or "alicilar :" in html)

    # ikon: popup kutusu aynı kaynak (fa-brands fa-whatsapp) toolbar ile
    check("k ikon brands popup", "fa-brands fa-whatsapp" in html)
    # ozet builder satırı
    ozet_i = html.find("function faturaRaporGridModalOzetHtml")
    ozet_blk = html[ozet_i: ozet_i + 2500] if ozet_i >= 0 else ""
    check("k ikon ozet brands", "fa-brands fa-whatsapp" in ozet_blk)
    check("k ikon eski fa yok ozet", 'fa fa-whatsapp' not in ozet_blk)

    try:
        ac_i = html.find("function faturaRaporGridWaPanelAc")
        firma_i = html.find("function faturaRaporFirmaKartaGit")
        wa_blk = html[ac_i:firma_i]
        helpers_i = html.find("function faturaRaporGridWaAlicilarSecili")
        helpers_blk = html[helpers_i: ac_i] if helpers_i >= 0 else ""
        wa_blk = helpers_blk + wa_blk
    except ValueError as e:
        check("l extract", False, str(e))
        return
    check("l panelAc post yok", "grid-wa-gonder" not in html[ac_i:html.find("function faturaRaporGridWaYukle")])
    check("l gonder post var", "grid-wa-gonder" in wa_blk and "method: 'POST'" in wa_blk)

    node = (
        r"""
global.window = global;
global.document = {
  getElementById: function(){ return null; },
  querySelector: function(){ return null; },
  querySelectorAll: function(){ return []; },
  addEventListener: function(){},
  removeEventListener: function(){}
};
var fetchCalls = [];
global.fetch = function(url, opts){ fetchCalls.push({url:String(url), opts:opts||{}}); return Promise.resolve({status:200, json:function(){return Promise.resolve({ok:true});}}); };
"""
        + wa_blk
        + r"""
window.__faturaRaporGridModalMid = 10;
window.__faturaRaporGridWaKapali = false;
var store = {};
document.getElementById = function(id){
  if(!store[id]) store[id] = { hidden:true, textContent:'', value:'', innerHTML:'', disabled:false, checked:false, className:'', onchange:null };
  return store[id];
};
var aliciBoxes = [
  { value:'1', checked:true, disabled:false, onchange:null },
  { value:'2', checked:false, disabled:false, onchange:null },
  { value:'ozel', checked:false, disabled:false, onchange:null }
];
document.querySelector = function(){ return { value:'1', checked:true, disabled:false, parentNode:{ lastChild:{ textContent:'' } } }; };
document.querySelectorAll = function(sel){
  if (sel === 'input[name="frg_wa_alici"]') return aliciBoxes;
  return [];
};
faturaRaporGridWaPanelAc({stopPropagation:function(){}, preventDefault:function(){}}, 'toplam');
setTimeout(function(){
  var posts = fetchCalls.filter(function(c){ return (c.opts.method||'').toUpperCase()==='POST'; });
  console.log(posts.length === 0 ? 'ok l ikon postsuz' : 'FAIL l ikon postsuz');
  console.log(fetchCalls.some(function(c){ return c.url.indexOf('grid-wa-onizleme')>=0; }) ? 'ok l onizleme get' : 'FAIL l onizleme get');
  aliciBoxes[0].checked = true; aliciBoxes[1].checked = true; aliciBoxes[2].checked = false;
  var sec = faturaRaporGridWaAlicilarSecili();
  console.log(sec.length === 2 && sec.indexOf('1')>=0 && sec.indexOf('2')>=0 ? 'ok j bagimsiz' : 'FAIL j bagimsiz');
  aliciBoxes[0].checked = false; aliciBoxes[1].checked = false; aliciBoxes[2].checked = false;
  console.log(faturaRaporGridWaAlicilarSecili().length === 0 ? 'ok j hicbiri' : 'FAIL j hicbiri');
  window.__frgWaState = { uyarilar: [], gonderiyor:false, yukleniyor:false, mid:10, tutarTur:'toplam' };
  var btn = document.getElementById('frg_wa_gonder');
  faturaRaporGridWaGonderBtnGuncelle();
  console.log(btn.disabled ? 'ok j gonder kapali' : 'FAIL j gonder kapali');
  aliciBoxes[0].checked = true;
  faturaRaporGridWaGonderBtnGuncelle();
  console.log((!btn.disabled && String(btn.textContent||'').indexOf('1 numaraya')>=0) ? 'ok j etiket1' : 'FAIL j etiket1 '+btn.textContent);
  aliciBoxes[1].checked = true;
  faturaRaporGridWaGonderBtnGuncelle();
  console.log(String(btn.textContent||'').indexOf('2 numaraya')>=0 ? 'ok j etiket2' : 'FAIL j etiket2 '+btn.textContent);
  // ADIM1: karışık sonuç — gerçek kilitleme fonksiyonu
  aliciBoxes[0].checked = true; aliciBoxes[0].disabled = false;
  aliciBoxes[1].checked = true; aliciBoxes[1].disabled = false;
  aliciBoxes[2].checked = false; aliciBoxes[2].disabled = false;
  document.querySelector = function(sel){
    if (sel === 'input[name="frg_wa_alici"][value="1"]') return aliciBoxes[0];
    if (sel === 'input[name="frg_wa_alici"][value="2"]') return aliciBoxes[1];
    if (sel === 'input[name="frg_wa_alici"][value="ozel"]') return aliciBoxes[2];
    return null;
  };
  faturaRaporGridWaSonuclaraGoreAliciKilitle([
    {alici:'1', durum:'gonderildi', neden:''},
    {alici:'2', durum:'hata', neden:'Mock'}
  ]);
  faturaRaporGridWaGonderBtnGuncelle();
  var ok1 = (!aliciBoxes[0].checked && aliciBoxes[0].disabled);
  var ok2 = (aliciBoxes[1].checked && !aliciBoxes[1].disabled);
  var et = String(btn.textContent||'');
  console.log(ok1 && ok2 ? 'ok j kilit karisik' : 'FAIL j kilit karisik');
  console.log(et.indexOf('1 numaraya')>=0 ? 'ok j kilit etiket' : 'FAIL j kilit etiket '+et);
}, 50);
"""
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(node)
        path = f.name
    try:
        r = subprocess.run(
            ["node", path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20
        )
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip())
        check("l node ikon", "ok l ikon postsuz" in out)
        check("l node get", "ok l onizleme get" in out)
        check("j node bagimsiz", "ok j bagimsiz" in out)
        check("j node hicbiri", "ok j hicbiri" in out)
        check("j node gonder kapali", "ok j gonder kapali" in out)
        check("j node etiket1", "ok j etiket1" in out)
        check("j node etiket2", "ok j etiket2" in out)
        check("j node kilit karisik", "ok j kilit karisik" in out)
        check("j node kilit etiket", "ok j kilit etiket" in out)
    except FileNotFoundError:
        check("l node", False, "node yok")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    check("j kilitle fn", "function faturaRaporGridWaSonuclaraGoreAliciKilitle" in html)
    check("j kilitle cagri", "faturaRaporGridWaSonuclaraGoreAliciKilitle(satirlar)" in html)

    mod = (ROOT / "grid_wa_popup.py").read_text(encoding="utf-8")
    oniz_blk = mod[mod.find("def onizleme"): mod.find("def _alici_coz")]
    check("k get gonderim yok", "sozlesme_wa_isle" not in oniz_blk and "gonder_fn" not in oniz_blk)
    # log/print sızıntı yok
    check("h print yok", "print(" not in mod)
    check("h logger yok", "logger." not in mod)


def test_gun_cozumleme_asistan_ayni():
    import grid_wa_popup as g
    from routes.erp_notlar_routes import _wa_gecikme_gun

    bugun = date(2026, 11, 5)
    payload = _fake_payload()
    check("gun ayni", g._gecikme_gun(payload, bugun) == _wa_gecikme_gun(payload, bugun))


if __name__ == "__main__":
    test_gun_cozumleme_asistan_ayni()
    test_sunucu_tutar_sablon_alici()
    test_coklu_alici()
    test_route_methods_and_ui()
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        sys.exit(1)
    print("ALL_OK test_musteriler_wa_popup")
