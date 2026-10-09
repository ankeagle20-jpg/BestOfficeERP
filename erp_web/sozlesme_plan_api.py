# -*- coding: utf-8 -*-
"""Plan değiştirme yazma uçları. Kapı kapalıyken 404. Okuma ensure çalıştırmaz."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal

from flask import jsonify, request
from flask_login import current_user

from auth import giris_gerekli
from sozlesme_plan import _kart_parca, ay_bazli_tutarlar, plan_ekle_on_kontrol
from sozlesme_plan_depo import (
    PlanCakisma,
    PlanYok,
    ensure_sozlesme_plan_degisiklik,
    ensure_sozlesme_plan_degisiklik_semada,
    plan_ekle,
    plan_iptal,
    plan_liste,
)

_LOG = logging.getLogger(__name__)
_TOL = 0.02


def _kapi_acik(musteri_id) -> bool:
    """İki ortam anahtarı. Değerler loglanmaz. Varsayılan kapalı; boş liste kimseyi açmaz."""
    etkin = str(os.environ.get("PLAN_DEGISTIR_ENABLED", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
        "evet",
    )
    if not etkin:
        return False
    izin = set()
    for parca in str(os.environ.get("PLAN_DEGISTIR_MUSTERI_IDS", "")).split(","):
        p = parca.strip()
        if not p:
            continue
        try:
            izin.add(int(p))
        except ValueError:
            continue
    if not izin:
        return False
    try:
        return int(musteri_id) in izin
    except (TypeError, ValueError):
        return False


def _bugun() -> date:
    return date.today()


MSG_ODEMELI_ONAY = "Ödemesi olan geçmiş aylar onaylanmadan kaydedilemez."


def _bu_ay_basi() -> date:
    b = _bugun()
    return date(b.year, b.month, 1)


def _gecmis_ay_mi(ay: date) -> bool:
    """Geçerlilik ayı bulunulan aydan önceyse True. Yalnız bilgi/ödeme etkisi için; plan girişini ENGELLEMEZ
    (sözleşme başlangıcından sonraki her ay serbest)."""
    return ay < _bu_ay_basi()


def _tahsil_haritasi(mid) -> dict:
    """Ay -> ödenen toplam (YYYY-MM anahtarlı). Yalnız okur."""
    from routes.giris_routes import _aylik_tahsil_tutar_map

    out: dict = {}
    for iso, tutar in (_aylik_tahsil_tutar_map(int(mid)) or {}).items():
        anahtar = str(iso)[:7]
        try:
            out[anahtar] = round(out.get(anahtar, 0.0) + float(tutar or 0), 2)
        except (TypeError, ValueError):
            continue
    return out


def odemeli_etki(zincir: dict, mevcut, yeni: dict, ay: date, tahsil: dict, bu_ay: date) -> list:
    """Geçmiş aya plan: brütü değişen aylardan ödeme kaydı olanlar. Yazmaz.

    Her kayıt: ay, eski_brut, yeni_brut, odenen, yeni_kalan, fazla_odeme (ödenen > yeni brüt ise fark).
    """
    ay_sayisi = (bu_ay.year - ay.year) * 12 + (bu_ay.month - ay.month) + 25
    adet = max(24, min(240, ay_sayisi))
    out = []
    for a in onizleme_olustur(zincir, mevcut, yeni, ay, adet):
        if not _brut_degisti(a):
            continue
        try:
            odenen = round(float((tahsil or {}).get(str(a.get("ay"))[:7]) or 0), 2)
        except (TypeError, ValueError):
            continue
        if odenen <= 0.004:
            continue
        yeni_brut = round(float(a.get("yeni_brut") or 0), 2)
        out.append(
            {
                "ay": a.get("ay"),
                "eski_brut": a.get("eski_brut"),
                "yeni_brut": a.get("yeni_brut"),
                "odenen": odenen,
                "yeni_kalan": round(max(yeni_brut - odenen, 0.0), 2),
                "fazla_odeme": round(max(odenen - yeni_brut, 0.0), 2),
            }
        )
    return out


def _brut_degisti(a: dict) -> bool:
    e, n = a.get("eski_brut"), a.get("yeni_brut")
    if e is None or n is None:
        return e != n
    try:
        return abs(float(e) - float(n)) > 0.004
    except (TypeError, ValueError):
        return e != n


def gorunur_degisiklik_bilgisi(zincir: dict, mevcut, yeni: dict, ay: date, aylar: list) -> dict:
    """24 aylık önizlemede eskiden farklı en az bir ay var mı; ilk değişen ay (yoksa 10 yıla kadar aranır)."""
    for a in aylar:
        if _brut_degisti(a):
            return {"gorunur": True, "ilk_degisen_ay": a.get("ay")}
    uzun = onizleme_olustur(zincir, mevcut, yeni, ay, 120)
    ilk = next((a.get("ay") for a in uzun if _brut_degisti(a)), None)
    return {"gorunur": False, "ilk_degisen_ay": ilk}


def gorunur_degisiklik_uyarisi(bilgi: dict, faturali: bool) -> dict | None:
    if bilgi.get("gorunur"):
        return None
    ilk = bilgi.get("ilk_degisen_ay")
    if ilk and faturali:
        mesaj = (
            "Bu plan seçilen geçerlilik ayından itibaren faturalı aylar nedeniyle şu an görünür bir "
            f"değişiklik yaratmıyor; ilk değişen ay: {ilk}"
        )
    elif ilk:
        mesaj = f"Bu plan seçilen geçerlilik ayından itibaren şu an görünür bir değişiklik yaratmıyor; ilk değişen ay: {ilk}"
    else:
        mesaj = "Bu plan seçilen geçerlilik ayından itibaren görünür bir değişiklik yaratmıyor."
    return {"kod": "gorunur_degisiklik_yok", "mesaj": mesaj}


def _sema():
    from db import _tenant_schema_for_request

    return _tenant_schema_for_request()


def _kim() -> str:
    ad = getattr(current_user, "username", None) or getattr(current_user, "full_name", None) or ""
    ad = str(ad).strip()
    return (ad or "kullanici")[:80]


def _ay_basi(val) -> date:
    if isinstance(val, datetime):
        val = val.date()
    if isinstance(val, date):
        return date(val.year, val.month, 1)
    s = str(val or "").strip()
    if len(s) >= 7 and s[4] == "-":
        try:
            y, m = int(s[0:4]), int(s[5:7])
        except ValueError as exc:
            raise ValueError("Geçerlilik ayı geçersiz.") from exc
        if 1 <= m <= 12:
            return date(y, m, 1)
    raise ValueError("Geçerlilik ayı geçersiz.")


def _para(val) -> float:
    if isinstance(val, Decimal):
        val = float(val)
    return round(float(val), 2)


def _json_plan(row: dict) -> dict:
    out = {}
    for k, v in (row or {}).items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _mid_uyusmaz(mid, data) -> bool:
    if not isinstance(data, dict) or "musteri_id" not in data:
        return False
    ham = data.get("musteri_id")
    if ham in (None, ""):
        return False
    try:
        return int(ham) != int(mid)
    except (TypeError, ValueError):
        return True


def _musteri_ve_kyc(mid):
    from db import fetch_one

    row = fetch_one("SELECT id, durum FROM customers WHERE id = %s", (int(mid),))
    if not row or int(row.get("id") or 0) != int(mid):
        return None, None
    kyc = fetch_one(
        """
        SELECT sozlesme_tarihi, kira_artis_tarihi, aylik_kira, kdv_oran,
               kira_nakit, kira_nakit_tutar, kira_banka_tutar
        FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1
        """,
        (int(mid),),
    )
    return row, kyc or {}


def _kilitler(mid) -> list:
    from db import fetch_all
    from routes.giris_routes import _plan_fatura_kilit_listesi

    try:
        rows = fetch_all(
            """
            SELECT musteri_id, COALESCE(notlar, '') AS notlar,
                   COALESCE(toplam, tutar, 0) AS toplam,
                   fatura_tarihi, COALESCE(durum, '') AS durum,
                   COALESCE(yon, 'giden') AS yon
            FROM faturalar
            WHERE musteri_id = %s
              AND COALESCE(durum, '') NOT IN ('iptal', 'taslak')
            """,
            (int(mid),),
        ) or []
    except Exception:
        _LOG.exception("plan fatura okuma")
        return []
    return list((_plan_fatura_kilit_listesi(rows).get(int(mid)) or []))


def _sozlesme_basi(kyc):
    from routes.giris_routes import _aylik_grid_coerce_date

    return _aylik_grid_coerce_date((kyc or {}).get("sozlesme_tarihi")) or _aylik_grid_coerce_date(
        (kyc or {}).get("rent_start_date")
    )


def plan_tutar_hesapla(data: dict) -> dict:
    """Sunucu brütü. İstemci brütü varsa yuvarlama payı dışında reddeder."""
    try:
        net = _para(data.get("yeni_net"))
        kdv = _para(data.get("kdv_oran"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Tutarlar pozitif olmalıdır.") from exc
    if net <= 0 or kdv < 0:
        raise ValueError("Tutarlar pozitif olmalıdır.")
    odeme = str(data.get("odeme") or data.get("odeme_tipi") or "banka").strip().lower()
    if odeme not in ("nakit", "banka", "karma"):
        raise ValueError("Ödeme tipi nakit, banka veya karma olmalıdır.")
    nakit = data.get("nakit_tutar")
    banka = data.get("banka_tutar")
    if odeme == "nakit":
        parca = _kart_parca(net, kdv, True, None)
    elif odeme == "karma":
        try:
            nakit_f = _para(nakit)
            banka_f = _para(banka)
        except (TypeError, ValueError) as exc:
            raise ValueError("Karma ödemede paylar gerekli.") from exc
        if nakit_f <= 0 or banka_f <= 0 or abs((nakit_f + banka_f) - net) > _TOL:
            raise ValueError("Karma payları aylık net ile tutmuyor.")
        parca = _kart_parca(net, kdv, False, nakit_f / net)
    else:
        parca = _kart_parca(net, kdv, False, None)
    if data.get("yeni_brut") not in (None, ""):
        try:
            gelen = _para(data.get("yeni_brut"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Brüt tutar tutarsız.") from exc
        if abs(gelen - parca["brut"]) > _TOL:
            raise ValueError("Brüt tutar tutarsız.")
    if parca["brut"] <= 0:
        raise ValueError("Tutarlar pozitif olmalıdır.")
    return {
        "yeni_net": parca["net"],
        "kdv_oran": kdv,
        "yeni_brut": parca["brut"],
        "nakit_tutar": parca["nakit"] if odeme != "banka" else None,
        "banka_tutar": parca["banka"] if odeme != "nakit" else None,
        "odeme": odeme,
    }


def onizleme_olustur(zincir: dict, mevcut, yeni_plan: dict, bas: date, adet: int = 24) -> list:
    """Geçerlilik ayından itibaren eski ve yeni brüt. Yazmaz."""
    z = dict(zincir or {})
    soz = z.get("sozlesme_tarihi")
    if isinstance(soz, datetime):
        soz = soz.date()
    if isinstance(soz, date):
        gerek = (bas.year - soz.year) * 12 + (bas.month - soz.month) + int(adet)
        if int(z.get("ay_sayisi") or 0) < gerek:
            z["ay_sayisi"] = gerek
    eskiler = {s["ay"]: s for s in ay_bazli_tutarlar(z, mevcut or [])}
    yeniler = {s["ay"]: s for s in ay_bazli_tutarlar(z, list(mevcut or []) + [yeni_plan])}
    out = []
    y, m = bas.year, bas.month
    for _ in range(int(adet)):
        anahtar = f"{y:04d}-{m:02d}"
        e = eskiler.get(anahtar) or {}
        n = yeniler.get(anahtar) or {}
        out.append(
            {
                "ay": anahtar,
                "eski_brut": e.get("brut"),
                "yeni_brut": n.get("brut"),
                "eski_net": e.get("net"),
                "yeni_net": n.get("net"),
            }
        )
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _onizleme_zinciri(mid, kyc):
    from routes.giris_routes import (
        _plan_kutu,
        _plan_paket_yukle,
        _plan_zincir,
        _planli_reel_haritasi,
        _tufe_map_by_year_month_cached,
    )

    paket = _plan_paket_yukle([mid]).get(int(mid)) or {"planlar": [], "faturali": [], "belge": {}}
    reel = (_planli_reel_haritasi([mid]) or {}).get(int(mid)) or {}
    try:
        tufe = _tufe_map_by_year_month_cached()
    except Exception:
        tufe = {}
    bas = _sozlesme_basi(kyc)
    zincir = None
    if bas:
        zincir = _plan_zincir(kyc, tufe, 24, reel, paket.get("faturali"), paket.get("belge"))
    return zincir, list(paket.get("planlar") or []), _plan_kutu


def plan_tx(fn):
    """Tek transaction. Dış katman commit eder; hata olursa rollback."""
    from db import db

    with db() as conn:
        cur = conn.cursor()

        def calistir(sql, params=None):
            cur.execute(sql, params or ())
            if cur.description:
                row = cur.fetchone()
                return dict(row) if row else None
            return None

        def oku_bir(sql, params=None):
            cur.execute(sql, params or ())
            row = cur.fetchone()
            return dict(row) if row else None

        def oku_cok(sql, params=None):
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]

        return fn(calistir, oku_bir, oku_cok)


def _ensure_sema(calistir):
    sema = _sema()
    if sema:
        ensure_sozlesme_plan_degisiklik_semada(calistir, sema)
    else:
        ensure_sozlesme_plan_degisiklik(calistir)


def _resync(mid) -> bool:
    from routes.giris_routes import _plan_kutu_sifirla, resync_panel_and_grid_after_plan_change

    _plan_kutu_sifirla()
    return bool(resync_panel_and_grid_after_plan_change(int(mid)))


def _kapali():
    return jsonify({"ok": False}), 404


def _yok():
    return jsonify({"ok": False, "mesaj": "Bu müşteri bu oturumda yok."}), 403


def _pasif():
    return jsonify({"ok": False, "mesaj": "Pasif müşteriye plan eklenemez."}), 400


def plan_liste_yanit(mid):
    if not _kapi_acik(mid):
        return _kapali()
    musteri, _kyc = _musteri_ve_kyc(mid)
    if not musteri:
        return _yok()
    try:
        _bas = _sozlesme_basi(_kyc)
        soz_ay = f"{_bas.year:04d}-{_bas.month:02d}" if _bas else None
    except Exception:
        soz_ay = None
    try:
        from db import fetch_all

        satirlar = plan_liste(lambda sql, params=None: fetch_all(sql, params or ()), musteri_id=int(mid), iptaller=True)
    except Exception:
        _LOG.exception("plan liste")
        satirlar = []
    aktif = [_json_plan(s) for s in satirlar if not s.get("iptal_at")]
    iptaller = [_json_plan(s) for s in satirlar if s.get("iptal_at")]
    return jsonify(
        {
            "ok": True,
            "aktif": aktif,
            "iptaller": iptaller,
            "gecmis": [_json_plan(s) for s in satirlar],
            "sozlesme_baslangic": soz_ay,
        }
    )


def plan_onizleme_yanit(mid):
    if not _kapi_acik(mid):
        return _kapali()
    data = request.get_json(silent=True) or {}
    if _mid_uyusmaz(mid, data):
        return _yok()
    musteri, kyc = _musteri_ve_kyc(mid)
    if not musteri:
        return _yok()
    if str(musteri.get("durum") or "").strip().lower() == "pasif":
        return _pasif()
    try:
        tutar = plan_tutar_hesapla(data)
        ay = _ay_basi(data.get("gecerlilik_ay") or _bugun())
    except ValueError as exc:
        return jsonify({"ok": False, "mesaj": str(exc)}), 400
    bas = _sozlesme_basi(kyc)
    if bas and ay < date(bas.year, bas.month, 1):
        return jsonify({"ok": False, "mesaj": "Geçerlilik ayı sözleşme başlangıcından önce olamaz."}), 400
    kilit = _kilitler(mid)
    kontrol = plan_ekle_on_kontrol(ay, kilit)
    try:
        zincir, mevcut, _kutu = _onizleme_zinciri(mid, kyc)
    except Exception:
        _LOG.exception("plan onizleme")
        zincir, mevcut = None, []
    if not zincir:
        return jsonify({"ok": False, "mesaj": "Sözleşme bilgisi yok."}), 400
    yeni = {
        "gecerlilik_ay": ay,
        "yeni_net": tutar["yeni_net"],
        "kdv_oran": tutar["kdv_oran"],
        "yeni_brut": tutar["yeni_brut"],
        "nakit_tutar": tutar["nakit_tutar"],
        "banka_tutar": tutar["banka_tutar"],
    }
    aylar = onizleme_olustur(zincir, mevcut, yeni, ay, 24)
    degisim = gorunur_degisiklik_bilgisi(zincir, mevcut, yeni, ay, aylar)
    uyarilar = list(kontrol.get("uyarilar") or [])
    gorunmez = gorunur_degisiklik_uyarisi(degisim, bool((zincir or {}).get("faturali_aylar")))
    if gorunmez:
        uyarilar.append(gorunmez)
    gecmis_plan = _gecmis_ay_mi(ay)
    odemeli = []
    if gecmis_plan:
        try:
            odemeli = odemeli_etki(zincir, mevcut, yeni, ay, _tahsil_haritasi(mid), _bu_ay_basi())
        except Exception:
            _LOG.exception("plan odemeli etki")
            return jsonify({"ok": False, "mesaj": "Ödeme etkisi hesaplanamadı."}), 500
    return jsonify(
        {
            "ok": True,
            "gecerlilik_ay": ay.isoformat(),
            "yeni_net": tutar["yeni_net"],
            "kdv_oran": tutar["kdv_oran"],
            "yeni_brut": tutar["yeni_brut"],
            "nakit_tutar": tutar["nakit_tutar"],
            "banka_tutar": tutar["banka_tutar"],
            "odeme": tutar["odeme"],
            "aylar": aylar,
            "uyarilar": uyarilar,
            "gorunur_degisiklik": bool(degisim.get("gorunur")),
            "ilk_degisen_ay": degisim.get("ilk_degisen_ay"),
            "kilitlenen_aylar": kontrol.get("kilitlenen_aylar") or [],
            "faturali": bool(kontrol.get("faturali")),
            "onerilen_ay": kontrol.get("onerilen_ay"),
            "gecmis_plan": bool(gecmis_plan),
            "odemeli_aylar": odemeli,
            "fazla_odeme": any(float(o.get("fazla_odeme") or 0) > 0.004 for o in odemeli),
        }
    )


def plan_ekle_yanit(mid):
    if not _kapi_acik(mid):
        return _kapali()
    data = request.get_json(silent=True) or {}
    if _mid_uyusmaz(mid, data):
        return _yok()
    musteri, kyc = _musteri_ve_kyc(mid)
    if not musteri:
        return _yok()
    if str(musteri.get("durum") or "").strip().lower() == "pasif":
        return _pasif()
    try:
        tutar = plan_tutar_hesapla(data)
        ay = _ay_basi(data.get("gecerlilik_ay"))
    except ValueError as exc:
        return jsonify({"ok": False, "mesaj": str(exc)}), 400
    bas = _sozlesme_basi(kyc)
    if bas and ay < date(bas.year, bas.month, 1):
        return jsonify({"ok": False, "mesaj": "Geçerlilik ayı sözleşme başlangıcından önce olamaz."}), 400
    kilit = _kilitler(mid)
    kontrol = plan_ekle_on_kontrol(ay, kilit)
    if kontrol.get("kilitlenen_aylar") and data.get("onay_kilitli_aylar") is not True:
        return (
            jsonify(
                {
                    "ok": False,
                    "mesaj": "Kilitli aylar onaylanmadan kaydedilemez.",
                    "kilitlenen_aylar": kontrol.get("kilitlenen_aylar") or [],
                }
            ),
            400,
        )
    if _gecmis_ay_mi(ay):
        # Geçmiş aya plan serbest; ancak ödemesi olan aylar etkileniyorsa ayrı onay şart (sunucuda da).
        try:
            zincir, mevcut, _kutu = _onizleme_zinciri(mid, kyc)
            yeni_plan = {
                "gecerlilik_ay": ay,
                "yeni_net": tutar["yeni_net"],
                "kdv_oran": tutar["kdv_oran"],
                "yeni_brut": tutar["yeni_brut"],
                "nakit_tutar": tutar["nakit_tutar"],
                "banka_tutar": tutar["banka_tutar"],
            }
            odemeli = odemeli_etki(zincir, mevcut, yeni_plan, ay, _tahsil_haritasi(mid), _bu_ay_basi()) if zincir else []
        except Exception:
            _LOG.exception("plan odemeli etki")
            return jsonify({"ok": False, "mesaj": "Ödeme etkisi hesaplanamadı."}), 500
        if odemeli and data.get("onay_odemeli_aylar") is not True:
            return jsonify({"ok": False, "mesaj": MSG_ODEMELI_ONAY, "odemeli_aylar": odemeli}), 400

    def islem(calistir, oku_bir, _oku_cok):
        _ensure_sema(calistir)
        row = plan_ekle(
            calistir,
            oku_bir,
            musteri_id=int(mid),
            gecerlilik_ay=ay,
            yeni_net=tutar["yeni_net"],
            kdv_oran=tutar["kdv_oran"],
            yeni_brut=tutar["yeni_brut"],
            nakit_tutar=tutar["nakit_tutar"],
            banka_tutar=tutar["banka_tutar"],
            olusturan=_kim(),
        )
        if not _resync(int(mid)):
            raise RuntimeError("yenileme")
        return row

    try:
        row = plan_tx(islem)
    except PlanCakisma:
        return jsonify({"ok": False, "mesaj": "Bu geçerlilik ayında açık bir plan zaten var."}), 409
    except Exception as exc:
        metin = str(exc).lower()
        if "uq_sozlesme_plan" in metin or "unique" in metin:
            return jsonify({"ok": False, "mesaj": "Bu geçerlilik ayında açık bir plan zaten var."}), 409
        if not (isinstance(exc, RuntimeError) and str(exc) == "yenileme"):
            _LOG.exception("plan kayit")
        return jsonify({"ok": False, "mesaj": "Plan kaydedilemedi."}), 500
    return jsonify({"ok": True, "plan": _json_plan(row)})


def plan_iptal_yanit(mid, plan_id):
    if not _kapi_acik(mid):
        return _kapali()
    data = request.get_json(silent=True) or {}
    if _mid_uyusmaz(mid, data):
        return _yok()
    musteri, _kyc = _musteri_ve_kyc(mid)
    if not musteri:
        return _yok()

    def islem(calistir, oku_bir, _oku_cok):
        from sozlesme_plan_depo import SQL_BY_ID

        _ensure_sema(calistir)
        mevcut = oku_bir(SQL_BY_ID, (int(plan_id),))
        if not mevcut or mevcut.get("iptal_at"):
            raise PlanYok("acik plan yok")
        if int(mevcut.get("musteri_id") or 0) != int(mid):
            raise PermissionError("mid")
        row = plan_iptal(calistir, oku_bir, plan_id=int(plan_id), iptal_eden=_kim())
        if not _resync(int(mid)):
            raise RuntimeError("yenileme")
        return row

    try:
        row = plan_tx(islem)
    except PermissionError:
        return _yok()
    except ValueError as exc:
        return jsonify({"ok": False, "mesaj": str(exc)}), 400
    except PlanYok:
        return jsonify({"ok": False, "mesaj": "Açık plan yok."}), 404
    except Exception:
        _LOG.exception("plan iptal")
        return jsonify({"ok": False, "mesaj": "Plan iptal edilemedi."}), 500
    return jsonify({"ok": True, "plan": _json_plan(row)})


def plan_oku_liste(mid):
    """Tablo yoksa boş liste. Ensure çağırmaz."""
    from db import fetch_all

    return plan_liste(lambda sql, params=None: fetch_all(sql, params or ()), musteri_id=int(mid), iptaller=True)


def plan_rotalarini_bagla(bp):
    @bp.route("/api/musteri/<int:mid>/plan/onizleme", methods=["POST"])
    @giris_gerekli
    def api_musteri_plan_onizleme(mid):
        return plan_onizleme_yanit(mid)

    @bp.route("/api/musteri/<int:mid>/plan/<int:plan_id>/iptal", methods=["POST"])
    @giris_gerekli
    def api_musteri_plan_iptal(mid, plan_id):
        return plan_iptal_yanit(mid, plan_id)

    @bp.route("/api/musteri/<int:mid>/plan", methods=["GET", "POST"])
    @giris_gerekli
    def api_musteri_plan(mid):
        if request.method == "GET":
            return plan_liste_yanit(mid)
        return plan_ekle_yanit(mid)
