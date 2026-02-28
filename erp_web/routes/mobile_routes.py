"""
Mobil ERP — Bottom nav, kritik şerit, 5 ana sekme.
Dashboard, Müşteriler, Tahsilat, Operasyon, Yönetim.
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from auth import giris_gerekli
from db import fetch_all, fetch_one, ensure_faturalar_amount_columns
from datetime import date, timedelta

bp = Blueprint("mobile", __name__, url_prefix="/m")


def _bugun():
    return date.today()


def _mobile_kritik_strip():
    """Üst şerit: kritik müşteri sayısı + toplam alacak (30+ gün geciken)."""
    ensure_faturalar_amount_columns()
    bugun = _bugun()
    otuz_gun = bugun - timedelta(days=30)
    # Müşteri bazında: en az bir fatura 30+ gün gecikmiş
    r = fetch_one("""
        SELECT COUNT(DISTINCT f.musteri_id) as n,
               COALESCE(SUM(COALESCE(f.toplam, f.tutar)), 0) as toplam
        FROM faturalar f
        WHERE COALESCE(f.durum,'') != 'odendi' AND f.vade_tarihi IS NOT NULL AND (f.vade_tarihi::date) <= %s
    """, (otuz_gun,))
    n = (r.get("n") or 0) if r else 0
    toplam = float(r.get("toplam") or 0) if r else 0
    return {"kritik_say": n, "kritik_toplam": round(toplam, 2)}


def _mobile_dashboard_data():
    """4 kart + kritik liste."""
    ensure_faturalar_amount_columns()
    bugun = _bugun()
    otuz_gun = bugun - timedelta(days=30)
    yedi_gun = bugun - timedelta(days=7)

    # Bugün beklenen tahsilat
    r = fetch_one("""
        SELECT COALESCE(SUM(COALESCE(f.toplam, f.tutar)), 0) as t FROM faturalar f
        WHERE COALESCE(f.durum,'') != 'odendi' AND (f.vade_tarihi::date) = %s
    """, (bugun,))
    bugun_tahsilat = float(r.get("t") or 0) if r else 0

    # Toplam geciken (vade < bugün)
    r = fetch_one("""
        SELECT COALESCE(SUM(COALESCE(f.toplam, f.tutar)), 0) as t FROM faturalar f
        WHERE COALESCE(f.durum,'') != 'odendi' AND f.vade_tarihi IS NOT NULL AND (f.vade_tarihi::date) < %s
    """, (bugun,))
    toplam_geciken = float(r.get("t") or 0) if r else 0

    # Kritik müşteri sayısı (30+ gün)
    r = fetch_one("""
        SELECT COUNT(DISTINCT musteri_id) as n FROM faturalar
        WHERE COALESCE(durum,'') != 'odendi' AND vade_tarihi IS NOT NULL AND (vade_tarihi::date) <= %s
    """, (otuz_gun,))
    kritik_say = (r.get("n") or 0) if r else 0

    # Bugün gelen kargo
    r = fetch_one("SELECT COUNT(*) as n FROM kargolar WHERE (tarih::date) = %s", (bugun,))
    bugun_kargo = (r.get("n") or 0) if r else 0

    # Boş ofis sayısı
    try:
        r = fetch_one("SELECT COUNT(*) as n FROM offices WHERE COALESCE(status,'') = 'bos' AND COALESCE(is_active, true) = true")
        bos_ofis_say = (r.get("n") or 0) if r else 0
    except Exception:
        bos_ofis_say = 0
    # Yayında ilan sayısı (office_rentals)
    try:
        r = fetch_one("SELECT COUNT(*) as n FROM office_rentals WHERE COALESCE(status,'') NOT IN ('','taslak')")
        yayinda_ilan = (r.get("n") or 0) if r else 0
    except Exception:
        yayinda_ilan = 0
    # Bugün yapılan ödeme sayısı (tahsilat adedi)
    try:
        r = fetch_one("SELECT COUNT(*) as n FROM tahsilatlar WHERE (tahsilat_tarihi::date) = %s", (bugun,))
        bugun_odeme_say = (r.get("n") or 0) if r else 0
    except Exception:
        bugun_odeme_say = 0

    # Kritik liste: 30+ gün geciken müşteriler (ad, toplam alacak, geciken gün)
    kritik_list = fetch_all("""
        SELECT f.musteri_id, c.name as musteri_adi, c.phone, c.office_code,
               SUM(COALESCE(f.toplam, f.tutar)) as toplam_alacak,
               MIN(f.vade_tarihi) as en_eski_vade
        FROM faturalar f
        JOIN customers c ON c.id = f.musteri_id
        WHERE COALESCE(f.durum,'') != 'odendi' AND f.vade_tarihi IS NOT NULL AND (f.vade_tarihi::date) <= %s
        GROUP BY f.musteri_id, c.name, c.phone, c.office_code
        ORDER BY MIN(f.vade_tarihi)
    """, (otuz_gun,))
    for row in (kritik_list or []):
        vd = row.get("en_eski_vade")
        if hasattr(vd, "year"):
            row["geciken_gun"] = (bugun - vd).days
        else:
            try:
                d = date(*[int(x) for x in str(vd)[:10].split("-")])
                row["geciken_gun"] = (bugun - d).days
            except Exception:
                row["geciken_gun"] = 0
        row["toplam_alacak"] = round(float(row.get("toplam_alacak") or 0), 2)

    return {
        "bugun_tahsilat": bugun_tahsilat,
        "toplam_geciken": toplam_geciken,
        "kritik_say": kritik_say,
        "bugun_kargo": bugun_kargo,
        "bos_ofis_say": bos_ofis_say,
        "yayinda_ilan": yayinda_ilan,
        "bugun_odeme_say": bugun_odeme_say,
        "kritik_list": kritik_list or [],
        "strip": _mobile_kritik_strip(),
    }


def _mobile_musteri_list(arama=""):
    """Müşteri listesi: ad, ofis, risk rengi, gecikme günü."""
    ensure_faturalar_amount_columns()
    bugun = _bugun()
    otuz_gun = bugun - timedelta(days=30)
    params = []
    sql_extra = ""
    if arama:
        sql_extra = " AND (c.name ILIKE %s OR c.phone ILIKE %s)"
        params = [f"%{arama}%", f"%{arama}%"]
    sql = "SELECT c.id, c.name, c.phone, c.office_code FROM customers c WHERE 1=1" + sql_extra + " ORDER BY c.name"
    musteriler = fetch_all(sql, params) if params else fetch_all(sql)
    # Ödenmemiş + vade
    faturalar = fetch_all("""
        SELECT musteri_id, COALESCE(toplam, tutar) as toplam, vade_tarihi
        FROM faturalar WHERE COALESCE(durum,'') != 'odendi' AND vade_tarihi IS NOT NULL
    """)
    fat_by_cid = {}
    for f in (faturalar or []):
        cid = f["musteri_id"]
        if cid not in fat_by_cid:
            fat_by_cid[cid] = []
        fat_by_cid[cid].append(f)
    # Ofis türü (musteri_kyc veya offices)
    try:
        kyc = fetch_all("SELECT DISTINCT ON (musteri_id) musteri_id, hizmet_turu FROM musteri_kyc ORDER BY musteri_id, id DESC")
        kyc_by = {r["musteri_id"]: r.get("hizmet_turu") or "—" for r in (kyc or [])}
    except Exception:
        kyc_by = {}
    out = []
    for m in (musteriler or []):
        cid = m["id"]
        fats = fat_by_cid.get(cid, [])
        toplam_alacak = sum(float(f.get("toplam") or 0) for f in fats)
        geciken_gun = 0
        for f in fats:
            vd = f.get("vade_tarihi")
            if hasattr(vd, "year"):
                d = vd
            else:
                try:
                    d = date(*[int(x) for x in str(vd)[:10].split("-")])
                except Exception:
                    continue
            if d < bugun:
                geciken_gun = max(geciken_gun, (bugun - d).days)
        if geciken_gun > 30:
            risk = "kritik"
        elif geciken_gun > 0 or toplam_alacak > 0:
            risk = "dikkat"
        else:
            risk = "stabil"
        out.append({
            "id": cid,
            "name": m.get("name") or "—",
            "phone": m.get("phone") or "—",
            "office_code": m.get("office_code") or "—",
            "hizmet_turu": kyc_by.get(cid, "—"),
            "risk": risk,
            "geciken_gun": geciken_gun,
            "toplam_alacak": round(toplam_alacak, 2),
        })
    return out


def _mobile_tahsilat_data():
    """Bugün tahsil edilecekler, 7+ gecikenler, 30+ kritikler."""
    ensure_faturalar_amount_columns()
    bugun = _bugun()
    yedi_gun = bugun - timedelta(days=7)
    otuz_gun = bugun - timedelta(days=30)
    # Bugün vadesi gelen
    bugun_list = fetch_all("""
        SELECT f.id as fatura_id, f.musteri_id, c.name as musteri_adi, c.phone, c.office_code,
               COALESCE(f.toplam, f.tutar) as toplam, f.vade_tarihi, f.fatura_no
        FROM faturalar f JOIN customers c ON c.id = f.musteri_id
        WHERE COALESCE(f.durum,'') != 'odendi' AND (f.vade_tarihi::date) = %s
        ORDER BY c.name
    """, (bugun,))
    # 7+ gün geciken (vade < 7 gün önce)
    yedi_list = fetch_all("""
        SELECT f.id as fatura_id, f.musteri_id, c.name as musteri_adi, c.phone, c.office_code,
               COALESCE(f.toplam, f.tutar) as toplam, f.vade_tarihi, f.fatura_no
        FROM faturalar f JOIN customers c ON c.id = f.musteri_id
        WHERE COALESCE(f.durum,'') != 'odendi' AND f.vade_tarihi IS NOT NULL
          AND (f.vade_tarihi::date) > %s AND (f.vade_tarihi::date) < %s
        ORDER BY f.vade_tarihi
    """, (otuz_gun, bugun))
    # 30+ kritik
    otuz_list = fetch_all("""
        SELECT f.id as fatura_id, f.musteri_id, c.name as musteri_adi, c.phone, c.office_code,
               COALESCE(f.toplam, f.tutar) as toplam, f.vade_tarihi, f.fatura_no
        FROM faturalar f JOIN customers c ON c.id = f.musteri_id
        WHERE COALESCE(f.durum,'') != 'odendi' AND (f.vade_tarihi::date) <= %s
        ORDER BY f.vade_tarihi
    """, (otuz_gun,))
    def _geciken_gun(row):
        vd = row.get("vade_tarihi")
        if not vd:
            return 0
        if hasattr(vd, "year"):
            return (bugun - vd).days
        try:
            d = date(*[int(x) for x in str(vd)[:10].split("-")])
            return (bugun - d).days
        except Exception:
            return 0
    for r in (bugun_list or []):
        r["geciken_gun"] = 0
    for r in (yedi_list or []):
        r["geciken_gun"] = _geciken_gun(r)
    for r in (otuz_list or []):
        r["geciken_gun"] = _geciken_gun(r)
    return {
        "bugun_list": bugun_list or [],
        "yedi_list": yedi_list or [],
        "otuz_list": otuz_list or [],
        "otuz_count": len(otuz_list or []),
    }


def _mobile_operasyon_data():
    """Bugün gelen kargolar, teslim bekleyenler, boş ofisler."""
    bugun = _bugun()
    kargolar_bugun = fetch_all("""
        SELECT k.id, k.tarih, k.takip_no, k.teslim_alan, k.notlar, c.name as musteri_adi
        FROM kargolar k LEFT JOIN customers c ON c.id = k.musteri_id
        WHERE (k.tarih::date) = %s ORDER BY k.created_at DESC
    """, (bugun,))
    teslim_bekleyen = fetch_all("""
        SELECT k.id, k.tarih, k.takip_no, k.teslim_alan, k.notlar, c.name as musteri_adi
        FROM kargolar k LEFT JOIN customers c ON c.id = k.musteri_id
        WHERE (COALESCE(k.teslim_alan,'') = '' OR TRIM(COALESCE(k.teslim_alan,'')) = '')
        ORDER BY k.created_at DESC LIMIT 50
    """)
    try:
        bos_ofisler = fetch_all("SELECT id, code, office_type as type, status FROM offices WHERE COALESCE(status,'') = 'bos' AND COALESCE(is_active, true) = true ORDER BY code")
    except Exception:
        bos_ofisler = []
    return {
        "kargolar_bugun": kargolar_bugun or [],
        "teslim_bekleyen": teslim_bekleyen or [],
        "bos_ofisler": bos_ofisler or [],
    }


def _mobile_yonetim_data():
    """Mini CEO: günlük ciro, aylık özet, geciken toplam, doluluk, sözleşme bitiş."""
    ensure_faturalar_amount_columns()
    bugun = _bugun()
    yil = bugun.year
    ay = bugun.month
    # Günlük tahsilat (bugün yapılan)
    r = fetch_one("""
        SELECT COALESCE(SUM(tutar), 0) as t FROM tahsilatlar
        WHERE (tahsilat_tarihi::date) = %s
    """, (bugun,))
    gunluk_ciro = float(r.get("t") or 0) if r else 0
    # Aylık tahsilat
    r = fetch_one("""
        SELECT COALESCE(SUM(tutar), 0) as t FROM tahsilatlar
        WHERE EXTRACT(YEAR FROM (tahsilat_tarihi::date)) = %s AND EXTRACT(MONTH FROM (tahsilat_tarihi::date)) = %s
    """, (yil, ay))
    aylik_ciro = float(r.get("t") or 0) if r else 0
    # Geciken toplam
    r = fetch_one("""
        SELECT COALESCE(SUM(COALESCE(f.toplam, f.tutar)), 0) as t FROM faturalar f
        WHERE COALESCE(f.durum,'') != 'odendi' AND f.vade_tarihi IS NOT NULL AND (f.vade_tarihi::date) < %s
    """, (bugun,))
    geciken_toplam = float(r.get("t") or 0) if r else 0
    # Doluluk
    try:
        r = fetch_one("SELECT COUNT(*) as n FROM offices WHERE COALESCE(is_active, true) = true")
        toplam_ofis = (r.get("n") or 0) if r else 0
        r2 = fetch_one("SELECT COUNT(*) as n FROM offices WHERE COALESCE(status,'') = 'dolu' AND COALESCE(is_active, true) = true")
        dolu = (r2.get("n") or 0) if r2 else 0
        doluluk = round(dolu / toplam_ofis * 100, 0) if toplam_ofis else 0
    except Exception:
        doluluk = 0
    # Sözleşme bitiş (30 gün içinde)
    try:
        r = fetch_one("""
            SELECT COUNT(*) as n FROM musteri_kyc k
            WHERE k.sozlesme_bitis IS NOT NULL AND k.sozlesme_bitis >= %s AND k.sozlesme_bitis <= %s
        """, (bugun, bugun + timedelta(days=30)))
        sozlesme_alarm = (r.get("n") or 0) if r else 0
    except Exception:
        sozlesme_alarm = 0
    try:
        r = fetch_one("SELECT COUNT(*) as n FROM kargolar WHERE (tarih::date) = %s", (bugun,))
        bugun_kargo = (r.get("n") or 0) if r else 0
    except Exception:
        bugun_kargo = 0
    return {
        "gunluk_ciro": gunluk_ciro,
        "aylik_ciro": aylik_ciro,
        "geciken_toplam": geciken_toplam,
        "doluluk": doluluk,
        "sozlesme_alarm": sozlesme_alarm,
        "bugun_kargo": bugun_kargo,
    }


# ── Sayfalar ─────────────────────────────────────────────────────────────────

def _bugun_tarih_gun():
    """Türkçe tarih ve gün adı."""
    from datetime import datetime
    gunler = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    aylar = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    b = _bugun()
    return f"{b.day} {aylar[b.month]} {b.year}", gunler[b.weekday()]


@bp.route("/")
@bp.route("/dashboard")
@giris_gerekli
def dashboard():
    strip = _mobile_kritik_strip()
    data = _mobile_dashboard_data()
    bugun_tarih, bugun_gun = _bugun_tarih_gun()
    return render_template("mobile/dashboard.html", **data, strip=strip, bugun_tarih=bugun_tarih, bugun_gun=bugun_gun)


@bp.route("/musteriler")
@giris_gerekli
def musteriler():
    arama = (request.args.get("q") or "").strip()
    filtre = (request.args.get("filtre") or "tamam").strip().lower()
    liste = _mobile_musteri_list(arama)
    if filtre == "kritik":
        liste = [m for m in liste if m.get("risk") == "kritik"]
    elif filtre == "bugun":
        liste = [m for m in liste if m.get("geciken_gun", 0) <= 30 and (m.get("geciken_gun", 0) > 0 or m.get("toplam_alacak", 0) > 0)]
    strip = _mobile_kritik_strip()
    return render_template("mobile/musteriler.html", liste=liste, arama=arama, filtre=filtre, strip=strip)


@bp.route("/musteriler/<int:mid>")
@giris_gerekli
def musteri_detay(mid):
    from routes.musteri_routes import _komuta_merkezi_data
    data = _komuta_merkezi_data(mid)
    if not data:
        return redirect(url_for("mobile.musteriler"))
    strip = _mobile_kritik_strip()
    return render_template("mobile/musteri_detay.html", **data, strip=strip)


@bp.route("/tahsilat")
@giris_gerekli
def tahsilat():
    data = _mobile_tahsilat_data()
    strip = _mobile_kritik_strip()
    return render_template("mobile/tahsilat.html", **data, strip=strip)


@bp.route("/operasyon")
@giris_gerekli
def operasyon():
    data = _mobile_operasyon_data()
    strip = _mobile_kritik_strip()
    return render_template("mobile/operasyon.html", **data, strip=strip)


@bp.route("/yonetim")
@giris_gerekli
def yonetim():
    data = _mobile_yonetim_data()
    strip = _mobile_kritik_strip()
    return render_template("mobile/yonetim.html", **data, strip=strip)


@bp.route("/ilan-robotu")
@giris_gerekli
def ilan_robotu():
    """İlan Robotu — mobilde masaüstü sayfasına yönlendir veya özet göster."""
    strip = _mobile_kritik_strip()
    return render_template("mobile/ilan_robotu.html", strip=strip)


# ── API (kritik şerit, liste) ─────────────────────────────────────────────────

@bp.route("/api/kritik-strip")
@giris_gerekli
def api_kritik_strip():
    return jsonify(_mobile_kritik_strip())
