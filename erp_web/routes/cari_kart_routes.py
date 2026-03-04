"""
BestOffice 360° Cari Kart — Finans + CRM + Operasyon + Hukuk + Randevu + Kargo tek merkez
"""
from flask import Blueprint, render_template, request, jsonify, Response, url_for
from flask_login import current_user
from auth import giris_gerekli
from db import fetch_all, fetch_one, db as get_db
from datetime import date, datetime, timedelta
import io

bp = Blueprint("cari_kart", __name__)


def _cari_hareketler(musteri_id):
    """Fatura (borç) ve tahsilat (alacak) birleşik hareketler, yürüyen bakiye."""
    faturalar = fetch_all(
        """SELECT id, fatura_no AS belge_no, fatura_tarihi AS tarih, COALESCE(toplam, tutar, 0) AS tutar, 'Fatura' AS tur, vade_tarihi
           FROM faturalar WHERE musteri_id = %s ORDER BY fatura_tarihi, id""",
        (musteri_id,),
    )
    tahsilatlar = fetch_all(
        """SELECT id, COALESCE(makbuz_no, 'Makbuz-' || id) AS belge_no, tahsilat_tarihi AS tarih, tutar, 'Tahsilat' AS tur
           FROM tahsilatlar WHERE musteri_id = %s ORDER BY tahsilat_tarihi, id""",
        (musteri_id,),
    )
    rows = []
    for r in faturalar:
        rows.append({
            "id": r.get("id"), "belge_no": r.get("belge_no") or "", "tarih": str(r.get("tarih") or "")[:10],
            "tur": "Fatura", "borc": float(r.get("tutar") or 0), "alacak": 0,
            "vade_tarihi": str(r.get("vade_tarihi") or "")[:10] if r.get("vade_tarihi") else None,
        })
    for r in tahsilatlar:
        rows.append({
            "id": "t-" + str(r.get("id")), "belge_no": r.get("belge_no") or "", "tarih": str(r.get("tarih") or "")[:10],
            "tur": "Tahsilat", "borc": 0, "alacak": float(r.get("tutar") or 0), "vade_tarihi": None,
        })
    rows.sort(key=lambda x: (x["tarih"], x["tur"] == "Fatura" and 0 or 1))
    bakiye = 0
    for r in rows:
        bakiye = bakiye + r["borc"] - r["alacak"]
        r["bakiye"] = round(bakiye, 2)
    return rows


def _risk_skoru_360(gecikmis_gun, gecikmis_tutar, gecikme_sayisi, aging_90_plus):
    """risk_score = 100 - (gecikmiş_gün × 0.5) - (90+ gün borç × 0.01) - (gecikme_sayısı × 2); min 0 max 100."""
    skor = 100.0
    skor -= (gecikmis_gun or 0) * 0.5
    skor -= (aging_90_plus or 0) * 0.01
    skor -= (gecikme_sayisi or 0) * 2
    return max(0, min(100, round(skor, 1)))


@bp.route("/")
@giris_gerekli
def index():
    """360° Cari Kart ana sayfa: müşteri seç veya ?mid= ile doğrudan kart."""
    mid = request.args.get("mid", type=int)
    musteriler = fetch_all("SELECT id, name, tax_number, office_code, durum FROM customers ORDER BY name LIMIT 500")
    return render_template(
        "cari_kart/index.html",
        musteriler=musteriler,
        selected_mid=mid,
    )


@bp.route("/api/360/<int:mid>")
@giris_gerekli
def api_360(mid):
    """Tek müşteri için 360° özet + hareketler + aging + randevu + kargo + profil."""
    cust = fetch_one("SELECT * FROM customers WHERE id = %s", (mid,))
    if not cust:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404
    bugun = date.today()
    # Ödenmemiş faturalar
    faturalar_odenmemis = fetch_all(
        """SELECT id, fatura_no, fatura_tarihi, vade_tarihi, COALESCE(toplam, tutar, 0) AS toplam
           FROM faturalar WHERE musteri_id = %s AND COALESCE(durum, '') != 'odendi'""",
        (mid,),
    )
    toplam_borc = sum(float(f.get("toplam") or 0) for f in faturalar_odenmemis)
    gecikmis_gun = 0
    min_vade = None
    for f in faturalar_odenmemis:
        vd = f.get("vade_tarihi")
        if vd:
            try:
                if not hasattr(vd, "year"):
                    vd = datetime.strptime(str(vd)[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if vd < bugun:
                gun = (bugun - vd).days
                if gun > gecikmis_gun:
                    gecikmis_gun = gun
            if min_vade is None or vd < min_vade:
                min_vade = vd
    if min_vade and min_vade < bugun:
        gecikmis_gun = (bugun - min_vade).days
    bu_ay_bas = bugun.replace(day=1)
    bu_ay_son = (bu_ay_bas.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    bu_ay_tahsilat = fetch_one(
        """SELECT COALESCE(SUM(tutar), 0) AS t FROM tahsilatlar
           WHERE musteri_id = %s AND tahsilat_tarihi::date >= %s AND tahsilat_tarihi::date <= %s""",
        (mid, bu_ay_bas, bu_ay_son),
    )
    bu_ay_tahsilat = float(bu_ay_tahsilat.get("t", 0) or 0) if bu_ay_tahsilat else 0
    bu_ay_fatura = fetch_one(
        """SELECT COALESCE(SUM(COALESCE(toplam, tutar, 0)), 0) AS t FROM faturalar
           WHERE musteri_id = %s AND fatura_tarihi::date >= %s AND fatura_tarihi::date <= %s""",
        (mid, bu_ay_bas, bu_ay_son),
    )
    bu_ay_fatura = float(bu_ay_fatura.get("t", 0) or 0) if bu_ay_fatura else 0
    son_odeme = fetch_one(
        """SELECT MAX(tahsilat_tarihi) AS dt FROM tahsilatlar WHERE musteri_id = %s""",
        (mid,),
    )
    son_odeme_tarihi = str(son_odeme.get("dt") or "")[:10] if son_odeme and son_odeme.get("dt") else None
    aging_0_30 = aging_31_60 = aging_61_90 = aging_91 = 0
    for f in faturalar_odenmemis:
        vd = f.get("vade_tarihi")
        if not vd:
            continue
        try:
            if not hasattr(vd, "year"):
                vd = datetime.strptime(str(vd)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        gun = (bugun - vd).days
        tutar = float(f.get("toplam") or 0)
        if gun <= 30:
            aging_0_30 += tutar
        elif gun <= 60:
            aging_31_60 += tutar
        elif gun <= 90:
            aging_61_90 += tutar
        else:
            aging_91 += tutar
    gecikme_sayisi = 0
    for f in faturalar_odenmemis:
        vd = f.get("vade_tarihi")
        if vd and (hasattr(vd, "year") and vd < bugun or (str(vd)[:10] < str(bugun))):
            gecikme_sayisi += 1
    risk_skoru = _risk_skoru_360(gecikmis_gun, toplam_borc, gecikme_sayisi, aging_91)
    # Son 6 ay ödeme davranışı (ay bazlı tahsilatlar)
    altı_ay_once = bugun.replace(day=1) - timedelta(days=180)
    odeme_rows = fetch_all(
        """
        SELECT to_char(tahsilat_tarihi::date, 'YYYY-MM') AS ym,
               DATE_TRUNC('month', tahsilat_tarihi::date) AS ay,
               COALESCE(SUM(tutar),0) AS tutar
          FROM tahsilatlar
         WHERE musteri_id = %s AND tahsilat_tarihi::date >= %s
         GROUP BY ym, ay
         ORDER BY ay
        """,
        (mid, altı_ay_once),
    )
    odeme_davranisi = [
        {"etiket": r.get("ym"), "tutar": float(r.get("tutar") or 0)}
        for r in (odeme_rows or [])
    ]
    # Ortalama ödeme süresi (gün): tahsilat_tarihi - vade_tarihi
    ort_odeme = fetch_one(
        """
        SELECT AVG((t.tahsilat_tarihi::date - f.vade_tarihi::date)) AS gun
          FROM tahsilatlar t
          JOIN faturalar f ON t.fatura_id = f.id
         WHERE t.musteri_id = %s
           AND t.tahsilat_tarihi IS NOT NULL
           AND f.vade_tarihi IS NOT NULL
        """,
        (mid,),
    )
    ort_odeme_gun = None
    try:
        if ort_odeme and ort_odeme.get("gun") is not None:
            ort_odeme_gun = float(ort_odeme["gun"])
    except Exception:
        ort_odeme_gun = None
    hareketler = _cari_hareketler(mid)
    profil = fetch_one("SELECT * FROM customer_financial_profile WHERE musteri_id = %s", (mid,))
    risk_limit = float(profil.get("risk_limit") or 0) if profil else 0
    risk_limit_kullanim = (toplam_borc / risk_limit * 100) if risk_limit and risk_limit > 0 else 0
    sozlesme_bitis = None
    sozlesme_bitis_gun = None
    kyc = fetch_one("SELECT sozlesme_bitis FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1", (mid,))
    if kyc and kyc.get("sozlesme_bitis"):
        try:
            sb = kyc["sozlesme_bitis"]
            if hasattr(sb, "year"):
                sozlesme_bitis = str(sb)[:10]
                sozlesme_bitis_gun = (sb - bugun).days
            else:
                sozlesme_bitis = str(sb)[:10]
                sozlesme_bitis_gun = (datetime.strptime(sozlesme_bitis, "%Y-%m-%d").date() - bugun).days
        except Exception:
            pass
    randevular = fetch_all("SELECT * FROM randevular WHERE musteri_id = %s ORDER BY randevu_tarihi DESC LIMIT 50", (mid,))
    kargolar = fetch_all("SELECT * FROM kargolar WHERE musteri_id = %s ORDER BY tarih DESC LIMIT 50", (mid,))
    belgeler = fetch_all("SELECT * FROM cari_belgeler WHERE musteri_id = %s ORDER BY created_at DESC", (mid,))
    iletisim = fetch_all("SELECT * FROM iletisim_log WHERE musteri_id = %s ORDER BY created_at DESC LIMIT 100", (mid,))
    is_admin = getattr(current_user, "role", None) == "admin"
    payload = {
        "ok": True,
        "musteri": {
            "id": cust.get("id"), "name": cust.get("name"), "tax_number": cust.get("tax_number"),
            "phone": cust.get("phone"), "email": cust.get("email"), "address": cust.get("address"),
            "office_code": cust.get("office_code"), "durum": cust.get("durum") or "aktif",
            "vergi_dairesi": cust.get("vergi_dairesi"), "mersis_no": cust.get("mersis_no"),
            "nace_kodu": cust.get("nace_kodu"), "ofis_tipi": cust.get("ofis_tipi"),
        },
        "ozet": {
            "guncel_bakiye": round(toplam_borc, 2),
            "gecikmis_tutar": round(toplam_borc, 2),
            "gecikmis_gun": gecikmis_gun,
            "bu_ay_fatura": round(bu_ay_fatura, 2),
            "bu_ayki_tahsilat": round(bu_ay_tahsilat, 2),
            "son_odeme_tarihi": son_odeme_tarihi,
            "ortalama_odeme_suresi": ort_odeme_gun,
            "risk_skoru": risk_skoru,
            "aging_0_30": round(aging_0_30, 2),
            "aging_31_60": round(aging_31_60, 2),
            "aging_61_90": round(aging_61_90, 2),
            "aging_91_plus": round(aging_91, 2),
            "risk_limit_kullanim": round(risk_limit_kullanim, 1),
            "sozlesme_bitis": sozlesme_bitis,
            "sozlesme_bitis_gun": sozlesme_bitis_gun,
        },
        "hareketler": hareketler,
        "randevular": [dict(r) for r in randevular] if randevular else [],
        "kargolar": [dict(r) for r in kargolar] if kargolar else [],
        "belgeler": [dict(r) for r in belgeler] if belgeler else [],
        "iletisim_log": [dict(r) for r in iletisim] if iletisim else [],
        "odeme_davranisi": odeme_davranisi,
        "finansal_profil": None,
    }
    if profil:
        payload["finansal_profil"] = {
            "tahmini_odeme_gunu": profil.get("tahmini_odeme_gunu"),
            "yillik_karlilik_endeksi": float(profil.get("yillik_karlilik_endeksi") or 0),
            "hukuki_esk_puan": profil.get("hukuki_esk_puan"),
            "mutabakat_tarihi": str(profil.get("mutabakat_tarihi"))[:10] if profil.get("mutabakat_tarihi") else None,
            "vade_gunu": profil.get("vade_gunu"),
        }
        if is_admin:
            payload["finansal_profil"]["ic_not"] = profil.get("ic_not")
            payload["finansal_profil"]["hukuki_surec"] = profil.get("hukuki_surec")
    return jsonify(payload)


@bp.route("/api/musteriler")
@giris_gerekli
def api_musteriler():
    """Müşteri listesi (arama için)."""
    q = request.args.get("q", "").strip()
    if q:
        rows = fetch_all(
            "SELECT id, name, tax_number, office_code, durum FROM customers WHERE name ILIKE %s OR tax_number ILIKE %s ORDER BY name LIMIT 100",
            (f"%{q}%", f"%{q}%"),
        )
    else:
        rows = fetch_all("SELECT id, name, tax_number, office_code, durum FROM customers ORDER BY name LIMIT 200")
    return jsonify(rows or [])
