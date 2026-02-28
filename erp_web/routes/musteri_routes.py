from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from auth import yetki_gerekli, giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning
import pandas as pd
from io import BytesIO
from datetime import date, datetime, timedelta
from docx import Document
import os
import sys

# Web kökü (gemini_helper import için)
_web_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _web_root not in sys.path:
    sys.path.insert(0, _web_root)
try:
    from gemini_helper import analiz_yap as gemini_analiz_yap, GEMINI_AVAILABLE
except ImportError:
    GEMINI_AVAILABLE = False
    def gemini_analiz_yap(*args, **kwargs):
        return False, "Gemini modülü yüklenemedi."

# helper month names (Turkish)
MONTHS_TR = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]

bp = Blueprint("musteriler", __name__)


@bp.route("/")
@giris_gerekli
def index():
    """Müşteriler grid görünümü - desktop gibi"""
    arama = request.args.get("q", "").strip()
    tum_yillar_odenmis = request.args.get("tum_yillar_odenmis") == "1"
    if tum_yillar_odenmis:
        # Sadece tüm faturaları ödenmiş müşteriler (ödeneği kalmamış)
        musteriler = fetch_all("""
            SELECT c.* FROM customers c
            WHERE NOT EXISTS (
                SELECT 1 FROM faturalar f
                WHERE f.musteri_id = c.id AND (f.durum IS NULL OR f.durum != 'odendi')
            )
            ORDER BY c.name
        """)
    elif arama:
        musteriler = fetch_all(
            "SELECT * FROM customers WHERE name ILIKE %s ORDER BY name",
            (f"%{arama}%",))
    else:
        musteriler = fetch_all("SELECT * FROM customers ORDER BY name")
    
    import_sonuc = request.args.get("import_sonuc")
    imported = request.args.get("imported", type=int)
    import_hatalar = request.args.get("import_hatalar", type=int) or 0
    return render_template(
        "musteriler/index.html",
        faturalar=musteriler,
        arama=arama,
        tum_yillar_odenmis=tum_yillar_odenmis,
        import_sonuc=import_sonuc,
        imported=imported or 0,
        import_hatalar=import_hatalar,
    )


def _komuta_merkezi_data(mid):
    """Müşteri komuta merkezi için tüm veriyi topla: hero, risk, 12 ay, kargo, TÜFE, WhatsApp metni."""
    bugun = date.today()
    musteri = fetch_one("SELECT * FROM customers WHERE id=%s", (mid,))
    if not musteri:
        return None
    kyc = fetch_one(
        "SELECT sozlesme_tarihi, sozlesme_bitis, hizmet_turu, aylik_kira FROM musteri_kyc WHERE musteri_id=%s ORDER BY id DESC LIMIT 1",
        (mid,),
    )
    sozlesme_baslangic = kyc.get("sozlesme_tarihi") if kyc else None
    sozlesme_bitis = kyc.get("sozlesme_bitis") if kyc else None
    sozlesme_bas_str = _tarih_str(sozlesme_baslangic)
    sozlesme_bitis_str = _tarih_str(sozlesme_bitis)
    kalan_gun = None
    if sozlesme_bitis:
        d = sozlesme_bitis if hasattr(sozlesme_bitis, "year") else _parse_date_str(str(sozlesme_bitis)[:10])
        if d:
            kalan_gun = (d - bugun).days

    # Tahsilat: toplam ödenen, son ödeme tarihi (musteri_id veya customer_id)
    tah = fetch_one(
        """SELECT COALESCE(SUM(tutar), 0) as toplam_odenen,
                  MAX(tahsilat_tarihi) as son_odeme_tarihi
           FROM tahsilatlar WHERE musteri_id = %s OR customer_id = %s""",
        (mid, mid),
    )
    toplam_odenen = float(tah.get("toplam_odenen") or 0)
    son_odeme_tarihi = tah.get("son_odeme_tarihi")
    son_odeme_str = _tarih_str(son_odeme_tarihi) or "—"

    # Faturalar: toplam borç, gecikme, bu ayki borç (bu ay vadesi gelen ödenmemiş)
    faturalar_odenmemis = fetch_all("""
        SELECT id, COALESCE(toplam, tutar) as toplam, vade_tarihi, fatura_no
        FROM faturalar WHERE musteri_id=%s AND COALESCE(durum,'') != 'odendi'
    """, (mid,))
    toplam_borc = sum(float(f.get("toplam") or 0) for f in (faturalar_odenmemis or []))
    geciken_gun = 0
    bu_ay = bugun.month
    bu_yil = bugun.year
    bu_ayki_borc = 0
    for f in (faturalar_odenmemis or []):
        vd = _parse_vade(f.get("vade_tarihi"))
        if vd and vd < bugun:
            geciken_gun = max(geciken_gun, (bugun - vd).days)
        if vd and vd.month == bu_ay and vd.year == bu_yil:
            bu_ayki_borc += float(f.get("toplam") or 0)

    # Risk: son 6 ay gecikme sayısı, ortalama gecikme, puan
    alti_ay_once = bugun - timedelta(days=180)
    faturalar_son6 = fetch_all("""
        SELECT vade_tarihi, durum FROM faturalar
        WHERE musteri_id=%s AND vade_tarihi IS NOT NULL AND (vade_tarihi::date) >= %s
        ORDER BY vade_tarihi
    """, (mid, alti_ay_once))
    gecikme_sayisi_6ay = 0
    gecikme_gunleri = []
    for f in (faturalar_son6 or []):
        vd = _parse_vade(f.get("vade_tarihi"))
        if not vd:
            continue
        if (f.get("durum") or "").lower() != "odendi":
            if vd < bugun:
                gecikme_sayisi_6ay += 1
                gecikme_gunleri.append((bugun - vd).days)
    ortalama_gecikme = int(sum(gecikme_gunleri) / len(gecikme_gunleri)) if gecikme_gunleri else 0
    # Risk puanı 0-100: yüksek = iyi (az risk)
    risk_puan = max(0, min(100, 100 - gecikme_sayisi_6ay * 15 - ortalama_gecikme * 2))
    if risk_puan >= 70:
        risk_durum = "stabil"
        risk_aksiyon = "Şu an için bir aksiyona gerek yok."
    elif risk_puan >= 40:
        risk_durum = "dikkat"
        risk_aksiyon = "Hatırlatma mesajı gönderin."
    else:
        risk_durum = "kritik"
        risk_aksiyon = "Hukuki uyarı ve takip önerilir."

    # 12 aylık detay: her ay için tutar, ödeme tarihi, gecikme günü, durum
    tum_faturalar = fetch_all("""
        SELECT id, COALESCE(toplam, tutar) as toplam, vade_tarihi, durum
        FROM faturalar WHERE musteri_id=%s AND vade_tarihi IS NOT NULL
        ORDER BY vade_tarihi
    """, (mid,))
    # Tahsilatları fatura_id'ye göre al (son tahsilat tarihi)
    tahsilatlar = fetch_all(
        "SELECT fatura_id, tahsilat_tarihi FROM tahsilatlar WHERE musteri_id=%s OR customer_id=%s ORDER BY tahsilat_tarihi DESC",
        (mid, mid),
    )
    fatura_odeme = {}
    for t in (tahsilatlar or []):
        fid = t.get("fatura_id")
        if fid and fid not in fatura_odeme:
            fatura_odeme[fid] = t.get("tahsilat_tarihi")
    aylik_detay = []
    cari_yil = bugun.year
    for ay in range(1, 13):
        ay_faturalar = [f for f in (tum_faturalar or []) if _parse_vade(f.get("vade_tarihi")) and _parse_vade(f.get("vade_tarihi")).month == ay and _parse_vade(f.get("vade_tarihi")).year == cari_yil]
        if not ay_faturalar:
            aylik_detay.append({"ay_adi": MONTHS_TR[ay - 1], "tutar": 0, "odeme_tarihi": None, "gecikme_gun": None, "durum": "gelecek", "fatura_id": None})
            continue
        f = ay_faturalar[0]
        vd = _parse_vade(f.get("vade_tarihi"))
        tutar = float(f.get("toplam") or 0)
        odendi = (f.get("durum") or "").lower() == "odendi"
        odeme_tarihi = fatura_odeme.get(f.get("id")) if odendi else None
        gecikme_gun = (bugun - vd).days if vd and not odendi and vd < bugun else None
        if odendi:
            durum = "odendi"
        elif vd and vd < bugun:
            durum = "gecikti"
        elif vd and (vd == bugun or (vd - bugun).days <= 7):
            durum = "bugun_yakin"
        else:
            durum = "gelecek"
        aylik_detay.append({
            "ay_adi": MONTHS_TR[ay - 1],
            "tutar": tutar,
            "odeme_tarihi": odeme_tarihi,
            "odeme_tarihi_str": _tarih_str(odeme_tarihi) if odeme_tarihi else "—",
            "gecikme_gun": gecikme_gun,
            "durum": durum,
            "fatura_id": f.get("id"),
        })

    # Son 6 ay tahsilat toplamı (grafik için)
    son_6_ay_chart = []
    for i in range(5, -1, -1):
        d = bugun - timedelta(days=30 * i)
        ay_bas = d.replace(day=1)
        if i == 0:
            ay_son = bugun
        else:
            nxt = ay_bas.month % 12 + 1
            y = ay_bas.year + (1 if nxt == 1 else 0)
            ay_son = date(y, nxt, 1) - timedelta(days=1)
        row = fetch_one(
            """SELECT COALESCE(SUM(tutar), 0) as t FROM tahsilatlar
               WHERE (musteri_id = %s OR customer_id = %s) AND (tahsilat_tarihi::date) >= %s AND (tahsilat_tarihi::date) <= %s""",
            (mid, mid, ay_bas, ay_son),
        )
        son_6_ay_chart.append({"ay": MONTHS_TR[ay_bas.month - 1][:3], "tutar": float(row.get("t") or 0)})

    # Kargolar timeline
    kargolar_raw = fetch_all(
        "SELECT id, tarih, takip_no, kargo_firmasi, teslim_alan, notlar, created_at FROM kargolar WHERE musteri_id=%s ORDER BY created_at DESC LIMIT 30",
        (mid,),
    )
    kargolar = []
    for k in (kargolar_raw or []):
        teslim = (k.get("teslim_alan") or "").strip()
        durum = "Teslim alındı" if teslim else "Bekliyor"
        created = k.get("created_at")
        saat_str = created.strftime("%H:%M") if hasattr(created, "strftime") else (str(created)[11:16] if created else "—")
        kargolar.append({
            "id": k.get("id"),
            "tarih": _tarih_str(k.get("tarih")) or "—",
            "barkod_takip": k.get("takip_no") or "—",
            "notlar": k.get("notlar") or "—",
            "teslim_durum": durum,
            "saat": saat_str,
            "kargo_firmasi": k.get("kargo_firmasi") or "—",
        })

    # TÜFE: sözleşme bitiş yılı için son oran
    tufe_oran = 0
    artis_sonrasi_kira = None
    aylik_kira = float(kyc.get("aylik_kira") or 0) if kyc else 0
    if sozlesme_bitis and aylik_kira > 0:
        yil = sozlesme_bitis.year if hasattr(sozlesme_bitis, "year") else int(str(sozlesme_bitis)[:4])
        r = fetch_one("SELECT oran FROM tufe_verileri WHERE year = %s ORDER BY month DESC LIMIT 1", (yil,))
        tufe_oran = float(r.get("oran") or 0) if r else 0
        artis_sonrasi_kira = round(aylik_kira * (1 + tufe_oran / 100), 2)

    # WhatsApp metni (gecikme gününe göre)
    if geciken_gun <= 0:
        whatsapp_metin = f"Sayın {musteri.get('name') or 'Müşteri'}, {musteri.get('office_code') or ''} numaralı ofisinizle ilgili herhangi bir kira gecikmesi söz konusu değildir. Teşekkür ederiz."
    elif geciken_gun <= 7:
        whatsapp_metin = f"Merhaba, unutkanlık olmuş olabilir; ödeme hatırlatması yapıyoruz. Lütfen kalan tutarı ({toplam_borc:,.2f} ₺) zamanında ödeyiniz."
    elif geciken_gun <= 30:
        whatsapp_metin = f"Merhaba, hizmet devamı için ödemenizin yapılması gerekmektedir. Lütfen kalan tutarı {toplam_borc:,.2f} ₺ ödeyiniz."
    else:
        whatsapp_metin = f"Ödeme yapılmadığı takdirde hukuki işlem başlatılacaktır. Lütfen derhal {toplam_borc:,.2f} ₺ tutarındaki bakiyeyi ödeyiniz."

    # Ödeme durumu rozeti
    if geciken_gun > 30:
        odeme_rozet = "kritik"
    elif geciken_gun > 0 or toplam_borc > 0:
        odeme_rozet = "dikkat"
    else:
        odeme_rozet = "stabil"

    return {
        "musteri": musteri,
        "kyc": kyc,
        "sozlesme_bas_str": sozlesme_bas_str or "—",
        "sozlesme_bitis_str": sozlesme_bitis_str or "—",
        "kalan_gun": kalan_gun,
        "toplam_odenen": toplam_odenen,
        "toplam_borc": toplam_borc,
        "bu_ayki_borc": bu_ayki_borc,
        "son_odeme_str": son_odeme_str,
        "geciken_gun": geciken_gun,
        "risk_gecikme_sayisi_6ay": gecikme_sayisi_6ay,
        "risk_ortalama_gecikme": ortalama_gecikme,
        "risk_puan": risk_puan,
        "risk_durum": risk_durum,
        "risk_aksiyon": risk_aksiyon,
        "aylik_detay": aylik_detay,
        "son_6_ay_chart": son_6_ay_chart,
        "kargolar": kargolar,
        "tufe_oran": tufe_oran,
        "artis_sonrasi_kira": artis_sonrasi_kira,
        "aylik_kira": aylik_kira,
        "whatsapp_metin": whatsapp_metin,
        "odeme_rozet": odeme_rozet,
        "hizmet_turu": (kyc.get("hizmet_turu") or "—") if kyc else "—",
    }


def _tarih_str(d):
    """date veya string -> dd.mm.yyyy"""
    if not d:
        return None
    if hasattr(d, "strftime"):
        return d.strftime("%d.%m.%Y")
    s = str(d)[:10]
    return s[8:10] + "." + s[5:7] + "." + s[0:4] if len(s) >= 10 else s


def _parse_date_str(s):
    try:
        parts = str(s)[:10].split("-")
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        pass
    return None


@bp.route("/<int:mid>")
@giris_gerekli
def detay(mid):
    """Müşteri detay sayfası - Komuta Merkezi (tam ekran)."""
    data = _komuta_merkezi_data(mid)
    if not data:
        flash("Müşteri bulunamadı.", "danger")
        return redirect(url_for("musteriler.index"))
    faturalar = fetch_all(
        "SELECT * FROM faturalar WHERE musteri_id=%s ORDER BY fatura_tarihi DESC", (mid,))
    data["faturalar"] = faturalar
    return render_template("musteriler/detay.html", **data)


def _parse_vade(v):
    """vade_tarihi (date veya text) -> date veya None."""
    if not v:
        return None
    if hasattr(v, "year"):
        return v
    try:
        s = str(v)[:10]
        return date(*[int(x) for x in s.split("-")])
    except Exception:
        return None


@bp.route("/api/ara")
@giris_gerekli
def api_ara():
    """İsme göre müşteri ara (autocomplete). ?q= ile sorgu."""
    q = (request.args.get("q") or "").strip()
    if not q or len(q) < 1:
        return jsonify([])
    rows = fetch_all(
        "SELECT id, name FROM customers WHERE name ILIKE %s ORDER BY name LIMIT 25",
        (f"%{q}%",),
    )
    return jsonify(rows or [])


@bp.route("/<int:mid>/api/popup")
@giris_gerekli
def api_popup(mid):
    """Tek müşteri için detay popup verisi: ödeme durumu, 12 ay grid, kargo geçmişi, sözleşme bitiş."""
    musteri = fetch_one("SELECT * FROM customers WHERE id=%s", (mid,))
    if not musteri:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404
    bugun = date.today()

    # KYC / sözleşme bitiş
    kyc = fetch_one(
        "SELECT sozlesme_bitis, hizmet_turu FROM musteri_kyc WHERE musteri_id=%s ORDER BY id DESC LIMIT 1",
        (mid,),
    )
    sozlesme_bitis = kyc.get("sozlesme_bitis") if kyc else None
    sozlesme_str = ""
    sozlesme_yakin = False  # 1 ay kala kırmızı
    if sozlesme_bitis:
        if hasattr(sozlesme_bitis, "strftime"):
            sozlesme_str = sozlesme_bitis.strftime("%d.%m.%Y")
            d = sozlesme_bitis
        else:
            sozlesme_str = str(sozlesme_bitis)[:10]
            try:
                d = date(*[int(x) for x in str(sozlesme_bitis)[:10].split("-")])
            except Exception:
                d = None
        if d and bugun <= d <= bugun + timedelta(days=31):
            sozlesme_yakin = True

    # Ödenmemiş faturalar
    faturalar_odenmemis = fetch_all("""
        SELECT id, COALESCE(toplam, tutar) as toplam, vade_tarihi, durum
        FROM faturalar WHERE musteri_id=%s AND COALESCE(durum,'') != 'odendi' AND vade_tarihi IS NOT NULL
    """, (mid,))
    toplam_alacak = sum(float(f.get("toplam") or 0) for f in (faturalar_odenmemis or []))
    geciken_gun = 0
    for f in (faturalar_odenmemis or []):
        vd = _parse_vade(f.get("vade_tarihi"))
        if vd and vd < bugun:
            geciken_gun = max(geciken_gun, (bugun - vd).days)
    if geciken_gun > 30:
        odeme_durumu = "kritik"
    elif geciken_gun > 0 or (faturalar_odenmemis and len(faturalar_odenmemis) > 0):
        odeme_durumu = "yakin"
    else:
        odeme_durumu = "tam"

    # Tüm faturalar (12 ay grid)
    tum_faturalar = fetch_all("""
        SELECT COALESCE(toplam, tutar) as toplam, vade_tarihi, durum
        FROM faturalar WHERE musteri_id=%s AND vade_tarihi IS NOT NULL
    """, (mid,))
    fat_ay = {}
    for f in (tum_faturalar or []):
        vd = _parse_vade(f.get("vade_tarihi"))
        if not vd:
            continue
        ay = vd.month
        if ay not in fat_ay:
            fat_ay[ay] = []
        fat_ay[ay].append(f)
    aylik_durum = []
    for ay in range(1, 13):
        durum = "gelecek"
        for f in fat_ay.get(ay, []):
            vd = _parse_vade(f.get("vade_tarihi"))
            if not vd:
                continue
            if (f.get("durum") or "").lower() == "odendi":
                durum = "odendi"
                break
            if vd < bugun:
                durum = "gecikti"
                break
            if vd == bugun or (vd - bugun).days <= 7:
                durum = "bugun_yakin"
                break
            durum = "gelecek"
            break
        aylik_durum.append(durum)

    # Kargolar (geçmiş + son durum)
    kargolar_raw = fetch_all(
        "SELECT id, tarih, takip_no, kargo_firmasi, teslim_alan, notlar, created_at FROM kargolar WHERE musteri_id=%s ORDER BY created_at DESC LIMIT 30",
        (mid,),
    )
    kargolar = []
    for k in (kargolar_raw or []):
        teslim = (k.get("teslim_alan") or "").strip()
        durum = "Teslim" if teslim else "Bekliyor"
        created = k.get("created_at")
        saat_str = created.strftime("%H:%M") if hasattr(created, "strftime") else (str(created)[11:16] if created else "—")
        tarih_str = k.get("tarih")
        if hasattr(tarih_str, "strftime"):
            tarih_str = tarih_str.strftime("%d.%m.%Y")
        else:
            tarih_str = str(tarih_str)[:10] if tarih_str else "—"
        kargolar.append({
            "id": k.get("id"),
            "tarih": tarih_str,
            "takip_no": k.get("takip_no") or "—",
            "teslim_alan": teslim or "—",
            "durum": durum,
            "saat": saat_str,
            "kargo_firmasi": k.get("kargo_firmasi") or "—",
        })
    kargo_bekleyen = any((k.get("durum") or "") == "Bekliyor" for k in kargolar)
    son_kargo_durum = kargolar[0]["durum"] if kargolar else "—"

    # Son faturalar (tahsilat / ekstre için)
    faturalar_list = fetch_all("""
        SELECT id, fatura_no, COALESCE(toplam, tutar) as toplam, fatura_tarihi, vade_tarihi, durum
        FROM faturalar WHERE musteri_id=%s ORDER BY COALESCE(vade_tarihi, fatura_tarihi) DESC NULLS LAST LIMIT 15
    """, (mid,))
    faturalar_json = []
    for f in (faturalar_list or []):
        vt = f.get("vade_tarihi")
        ft = f.get("fatura_tarihi")
        faturalar_json.append({
            "id": f.get("id"),
            "fatura_no": f.get("fatura_no") or "—",
            "toplam": float(f.get("toplam") or 0),
            "fatura_tarihi": ft.strftime("%d.%m.%Y") if hasattr(ft, "strftime") else (str(ft)[:10] if ft else "—"),
            "vade_tarihi": vt.strftime("%d.%m.%Y") if hasattr(vt, "strftime") else (str(vt)[:10] if vt else "—"),
            "durum": (f.get("durum") or "—"),
        })

    return jsonify({
        "ok": True,
        "musteri": {
            "id": musteri["id"],
            "name": musteri.get("name") or "—",
            "phone": musteri.get("phone") or "—",
            "email": musteri.get("email") or "—",
            "tax_number": musteri.get("tax_number") or "—",
            "address": musteri.get("address") or "—",
            "office_code": musteri.get("office_code") or "—",
            "notes": musteri.get("notes") or "—",
        },
        "faturalar": faturalar_json,
        "odeme_durumu": odeme_durumu,
        "toplam_alacak": round(toplam_alacak, 2),
        "geciken_gun": geciken_gun,
        "aylik_durum": aylik_durum,
        "sozlesme_bitis_str": sozlesme_str or "—",
        "sozlesme_yakin": sozlesme_yakin,
        "hizmet_turu": (kyc.get("hizmet_turu") or "—") if kyc else "—",
        "kargo_bekleyen": kargo_bekleyen,
        "son_kargo_durum": son_kargo_durum,
        "kargolar": kargolar,
    })


@bp.route("/giris", methods=["GET"])
@giris_gerekli
def giris():
    """Ayrıntılı müşteri giriş / KYC ekranı (masaüstü gibi)"""
    return render_template("musteriler/giris.html")


@bp.route("/ekle", methods=["GET", "POST"])
@giris_gerekli
def ekle():
    """Yeni müşteri ekle"""
    if request.method == "POST":
        row = execute_returning(
            """INSERT INTO customers (name, email, phone, address, notes)
               VALUES (%s,%s,%s,%s,%s) RETURNING id""",
            (request.form.get("name"),
             request.form.get("email"),
             request.form.get("phone"),
             request.form.get("address"),
             request.form.get("notes")))
        flash("✓ Müşteri eklendi.", "success")
        return redirect(url_for("musteriler.detay", mid=row["id"]))
    return render_template("musteriler/ekle.html")


@bp.route("/import", methods=["GET", "POST"])
@giris_gerekli
def import_excel():
    """Excel'den müşteri içeri aktar"""
    if request.method == "POST":
        try:
            f = request.files.get("file")
            if not f or not f.filename:
                flash("Lütfen bir Excel dosyası seçin.", "warning")
                return redirect(request.url)
            raw = f.read()
            if not raw:
                flash("Dosya boş veya okunamadı.", "danger")
                return redirect(request.url)
            try:
                fn = (f.filename or "").lower()
                if fn.endswith(".xls") and not fn.endswith(".xlsx"):
                    try:
                        df = pd.read_excel(BytesIO(raw), header=0)
                    except Exception:
                        flash("Eski .xls formatı desteklenmiyor. Dosyayı .xlsx olarak kaydedip tekrar deneyin.", "danger")
                        return redirect(request.url)
                else:
                    df = pd.read_excel(BytesIO(raw), engine="openpyxl", header=0)
            except ImportError:
                flash("Excel desteği için openpyxl yüklü değil. Kurulum: pip install openpyxl", "danger")
                return redirect(request.url)
            except Exception as e:
                flash(f"Excel okunamadı: {e}", "danger")
                return redirect(request.url)

            if df.empty or len(df) == 0:
                flash("Excel dosyasında veri satırı yok.", "warning")
                return redirect(request.url)

            cols = []
            for i, c in enumerate(df.columns):
                s = (str(c).strip().lower() if c is not None else "") or f"unnamed_{i}"
                cols.append(s)
            df.columns = cols

            def find_col(keys):
                for k in keys:
                    for col in df.columns:
                        if k in col:
                            return col
                return None

            name_col = find_col([
                "ad", "name", "ünvan", "unvan", "firma", "müşteri", "musteri", "adı", "adi",
                "cari", "baslik", "başlık", "musteri adi", "müşteri adı"
            ])
            email_col = find_col(["email", "e-posta", "eposta", "mail"])
            phone_col = find_col(["telefon", "phone", "tel", "gsm", "cep"])
            addr_col = find_col(["adres", "address"])
            tax_col = find_col(["vergi", "tax", "vkn", "tckn", "vergi no", "vergino"])

            if not name_col:
                flash("Excel'de müşteri adı sütunu bulunamadı. Sütunlardan biri şunlardan biri olmalı: Ad, Unvan, Firma, Name, Müşteri Adı.", "danger")
                return redirect(request.url)

            def _cell(row, col):
                if not col:
                    return None
                v = row.get(col)
                if v is None:
                    return None
                try:
                    if pd.isna(v):
                        return None
                except (TypeError, ValueError):
                    pass
                s = str(v).strip()
                if not s or s.lower() == "nan":
                    return None
                return s

            inserted = 0
            errors = []
            for idx, row in df.iterrows():
                name = _cell(row, name_col)
                if not name:
                    continue
                email = _cell(row, email_col)
                phone = _cell(row, phone_col)
                address = _cell(row, addr_col)
                tax = _cell(row, tax_col)

                try:
                    if tax:
                        existing = fetch_one("SELECT id FROM customers WHERE tax_number = %s", (tax,))
                    else:
                        existing = None

                    if existing:
                        execute(
                            "UPDATE customers SET name=%s,email=%s,phone=%s,address=%s WHERE id=%s",
                            (name, email or None, phone or None, address or None, existing["id"])
                        )
                    else:
                        execute(
                            "INSERT INTO customers (name, email, phone, address, tax_number) VALUES (%s,%s,%s,%s,%s)",
                            (name, email or None, phone or None, address or None, tax or None)
                        )
                    inserted += 1
                except Exception as e:
                    errors.append(f"Satır {idx + 2}: {name[:30]} — {e}")

            if errors:
                flash(
                    f"Excel aktarımı tamamlandı: {inserted} müşteri aktarıldı. {len(errors)} satırda hata oluştu. Detay: " + "; ".join(errors[:3]) + ("..." if len(errors) > 3 else ""),
                    "warning",
                )
                return redirect(
                    url_for("musteriler.index", import_sonuc="uyari", imported=inserted, import_hatalar=len(errors))
                )
            flash(f"Excel aktarımı başarılı: {inserted} müşteri içe aktarıldı.", "success")
            return redirect(
                url_for("musteriler.index", import_sonuc="ok", imported=inserted, import_hatalar=0)
            )
        except Exception as e:
            flash(f"Aktarım sırasında hata oluştu: {e}", "danger")
            return redirect(request.url)

    return render_template("musteriler/import.html")


@bp.route("/export")
@giris_gerekli
def export_excel():
    """Müşteri listesini Excel olarak dışa aktar"""
    tum_yillar_odenmis = request.args.get("tum_yillar_odenmis") == "1"
    if tum_yillar_odenmis:
        rows = fetch_all("""
            SELECT c.* FROM customers c
            WHERE NOT EXISTS (
                SELECT 1 FROM faturalar f
                WHERE f.musteri_id = c.id AND (f.durum IS NULL OR f.durum != 'odendi')
            )
            ORDER BY c.name
        """)
    else:
        rows = fetch_all("SELECT * FROM customers ORDER BY name")
    if not rows:
        flash("Dışa aktarılacak müşteri yok.", "warning")
        return redirect(url_for("musteriler.index"))
    df = pd.DataFrame(rows)
    for col in list(df.columns):
        try:
            df[col] = df[col].apply(
                lambda x: x.isoformat()[:10] if hasattr(x, "isoformat") and x is not None else (x if x is not None else "")
            )
        except Exception:
            pass
    buf = BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"musteriler_{date.today().isoformat()}.xlsx",
    )


@bp.route("/gemini-analiz")
@giris_gerekli
def gemini_analiz():
    """Gemini AI Analiz sayfası"""
    return render_template("musteriler/gemini_analiz.html", gemini_available=GEMINI_AVAILABLE)


def _musteri_ozet_metni():
    """Müşteri verilerinden analiz için kısa özet metni üretir."""
    toplam = fetch_one("SELECT COUNT(*) AS n FROM customers")
    n = (toplam or {}).get("n") or 0
    kira = fetch_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(ilk_kira_bedeli), 0) AS toplam FROM customers WHERE ilk_kira_bedeli IS NOT NULL"
    )
    kira_n = (kira or {}).get("n") or 0
    kira_toplam = float((kira or {}).get("toplam") or 0)
    odendi = fetch_one(
        "SELECT COUNT(DISTINCT musteri_id) AS n FROM faturalar WHERE durum = %s",
        ("odendi",),
    )
    odenen_musteri = (odendi or {}).get("n") or 0
    bekleyen = fetch_one(
        "SELECT COUNT(DISTINCT musteri_id) AS n FROM faturalar WHERE durum IS NULL OR durum != %s",
        ("odendi",),
    )
    bekleyen_musteri = (bekleyen or {}).get("n") or 0
    son_5 = fetch_all(
        "SELECT name, ilk_kira_bedeli, rent_start_date FROM customers ORDER BY id DESC LIMIT 5"
    )
    satirlar = [
        f"Toplam müşteri sayısı: {n}",
        f"Kira bedeli girilmiş müşteri: {kira_n}, toplam aylık kira (ilk bedel): {kira_toplam:,.2f} TL",
        f"Faturası ödenmiş müşteri sayısı: {odenen_musteri}",
        f"Ödenmemiş fatura olan müşteri sayısı: {bekleyen_musteri}",
        "",
        "Son eklenen 5 müşteri (ad, ilk kira, başlangıç tarihi):",
    ]
    for m in (son_5 or []):
        ad = (m.get("name") or "").strip() or "—"
        k = m.get("ilk_kira_bedeli")
        k_str = f"{float(k):,.2f} TL" if k is not None else "—"
        t = m.get("rent_start_date")
        t_str = str(t)[:10] if t else "—"
        satirlar.append(f"  - {ad} | {k_str} | {t_str}")
    return "\n".join(satirlar)


@bp.route("/api/gemini-analiz", methods=["POST"])
@giris_gerekli
def api_gemini_analiz():
    """Müşteri verisi özeti + isteğe bağlı kullanıcı sorusu ile Gemini'den analiz alır."""
    data = request.get_json(silent=True) or request.form
    soru = (data.get("soru") or data.get("prompt") or "").strip()
    context = _musteri_ozet_metni()
    ok, metin = gemini_analiz_yap(context, soru)
    if ok:
        return jsonify({"ok": True, "metin": metin})
    return jsonify({"ok": False, "hata": metin}), 400


@bp.route("/<int:mid>/duzenle", methods=["GET", "POST"])
@giris_gerekli
def duzenle(mid):
    """Müşteri düzenle"""
    musteri = fetch_one("SELECT * FROM customers WHERE id=%s", (mid,))
    if not musteri:
        return redirect(url_for("musteriler.index"))
    if request.method == "POST":
        execute(
            """UPDATE customers SET name=%s,email=%s,phone=%s,address=%s,notes=%s
               WHERE id=%s""",
            (request.form.get("name"), request.form.get("email"),
             request.form.get("phone"), request.form.get("address"),
             request.form.get("notes"), mid))
        flash("✓ Müşteri güncellendi.", "success")
        return redirect(url_for("musteriler.detay", mid=mid))
    return render_template("musteriler/duzenle.html", musteri=musteri)


@bp.route("/<int:mid>/sil", methods=["POST"])
@giris_gerekli
def sil(mid):
    """Müşteri sil"""
    execute("DELETE FROM customers WHERE id=%s", (mid,))
    flash("Müşteri silindi.", "info")
    return redirect(url_for("musteriler.index"))


# ── API ENDPOİNTS ────────────────────────────────────────────────────────────

@bp.route("/api/liste")
@giris_gerekli
def api_liste():
    """Müşteri listesi JSON (dropdown için)"""
    musteriler = fetch_all("SELECT id, name FROM customers ORDER BY name")
    return jsonify(musteriler)


@bp.route("/api/list_full")
@giris_gerekli
def api_list_full():
    """Tam müşteri listesi JSON"""
    rows = fetch_all(
        "SELECT id, name, email, phone, address, office_code, notes, created_at FROM customers ORDER BY name"
    )
    return jsonify(rows)


# ── Giriş / KYC API ──────────────────────────────────────────────────────────

@bp.route("/api/musteri/ara")
@giris_gerekli
def api_musteri_ara():
    """Mevcut müşteriye bağlamak için arama (q=)"""
    q = (request.args.get("q") or "").strip()
    if not q:
        rows = fetch_all("SELECT id, name FROM customers ORDER BY name LIMIT 50")
    else:
        rows = fetch_all(
            "SELECT id, name FROM customers WHERE name ILIKE %s ORDER BY name LIMIT 30",
            (f"%{q}%",)
        )
    return jsonify(rows)


@bp.route("/api/kyc/getir")
@giris_gerekli
def api_kyc_getir():
    """Müşteriye ait KYC kaydını getir"""
    musteri_id = request.args.get("musteri_id")
    if not musteri_id:
        return jsonify(None)
    row = fetch_one(
        "SELECT * FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
        (musteri_id,),
    )

    # Eğer henüz KYC kaydı yoksa, en azından customers tablosundaki temel bilgileri döndür
    if not row:
        cust = fetch_one(
            "SELECT id, name, email, phone, address, tax_number FROM customers WHERE id = %s",
            (musteri_id,),
        )
        if not cust:
            return jsonify(None)
        # frontend'deki formu doldurmak için alan isimlerini eşleştir
        return jsonify(
            {
                "musteri_id": cust.get("id"),
                "sirket_unvani": cust.get("name"),
                "unvan": cust.get("name"),
                "email": cust.get("email"),
                "yetkili_email": cust.get("email"),
                "yetkili_tel": cust.get("phone"),
                "yeni_adres": cust.get("address"),
                "vergi_no": cust.get("tax_number"),
            }
        )

    # Tarih ve sayısal alanları JSON uyumlu yap
    out = dict(row)
    for k in list(out.keys()):
        v = out[k]
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()[:10] if v else None
        elif hasattr(v, "days"):  # timedelta
            out[k] = str(v)
    return jsonify(out)


@bp.route("/api/kyc/kaydet", methods=["POST"])
@giris_gerekli
def api_kyc_kaydet():
    """Giriş formundan KYC kaydı kaydet / güncelle"""
    try:
        data = request.json or request.form
        musteri_id = data.get("musteri_id")
        sirket_unvani = (data.get("sirket_unvani") or data.get("unvan") or "").strip()
        vergi_no = (data.get("vergi_no") or "").strip()
        vergi_dairesi = (data.get("vergi_dairesi") or "").strip()
        mersis_no = (data.get("mersis_no") or "").strip()
        ticaret_sicil_no = (data.get("ticaret_sicil_no") or "").strip()
        kurulus_tarihi = data.get("kurulus_tarihi")
        faaliyet_konusu = (data.get("faaliyet_konusu") or "").strip()
        nace_kodu = (data.get("nace_kodu") or "").strip()
        eski_adres = (data.get("eski_adres") or "").strip()
        yeni_adres = (data.get("yeni_adres") or "").strip()
        sube_merkez = (data.get("sube_merkez") or "Merkez").strip()
        yetkili_adsoyad = (data.get("yetkili_adsoyad") or "").strip()
        yetkili_tcno = (data.get("yetkili_tcno") or "").strip()
        yetkili_dogum = data.get("yetkili_dogum")
        yetkili_ikametgah = (data.get("yetkili_ikametgah") or "").strip()
        yetkili_tel = (data.get("yetkili_tel") or "").strip()
        yetkili_tel2 = (data.get("yetkili_tel2") or "").strip()
        yetkili_email = (data.get("yetkili_email") or "").strip()
        email = (data.get("email") or "").strip()
        hizmet_turu = (data.get("hizmet_turu") or "Sanal Ofis").strip()
        aylik_kira = float(str(data.get("aylik_kira") or 0).replace(",", ".") or 0)
        yillik_kira = float(str(data.get("yillik_kira") or 0).replace(",", ".") or 0)
        sozlesme_no = (data.get("sozlesme_no") or "").strip()
        sozlesme_tarihi = data.get("sozlesme_tarihi")
        sozlesme_bitis = data.get("sozlesme_bitis")
        notlar = (data.get("notlar") or "").strip()

        def parse_date(s):
            if not s:
                return None
            s = str(s).strip()[:10]
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
                try:
                    from datetime import datetime
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
            return None

        kurulus_tarihi = parse_date(kurulus_tarihi)
        yetkili_dogum = parse_date(yetkili_dogum)
        sozlesme_tarihi = parse_date(sozlesme_tarihi)
        sozlesme_bitis = parse_date(sozlesme_bitis)

        evrak_imza_sirkuleri = 1 if data.get("evrak_imza_sirkuleri") in (True, 1, "1", "on") else 0
        evrak_vergi_levhasi = 1 if data.get("evrak_vergi_levhasi") in (True, 1, "1", "on") else 0
        evrak_ticaret_sicil = 1 if data.get("evrak_ticaret_sicil") in (True, 1, "1", "on") else 0
        evrak_faaliyet_belgesi = 1 if data.get("evrak_faaliyet_belgesi") in (True, 1, "1", "on") else 0
        evrak_kimlik_fotokopi = 1 if data.get("evrak_kimlik_fotokopi") in (True, 1, "1", "on") else 0
        evrak_ikametgah = 1 if data.get("evrak_ikametgah") in (True, 1, "1", "on") else 0
        evrak_kase = 1 if data.get("evrak_kase") in (True, 1, "1", "on") else 0

        zorunlu = ["sirket_unvani", "vergi_no", "vergi_dairesi", "yeni_adres", "yetkili_adsoyad", "yetkili_tcno"]
        dolu = sum(1 for k in zorunlu if (data.get(k) or "").strip())
        tamamlanma_yuzdesi = int(dolu / len(zorunlu) * 100) if zorunlu else 0

        mevcut = fetch_one("SELECT id FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1", (musteri_id,)) if musteri_id else None

        if mevcut:
            execute(
                """UPDATE musteri_kyc SET
                   sirket_unvani=%s, unvan=%s, vergi_no=%s, vergi_dairesi=%s, mersis_no=%s, ticaret_sicil_no=%s,
                   kurulus_tarihi=%s, faaliyet_konusu=%s, nace_kodu=%s, eski_adres=%s, yeni_adres=%s, sube_merkez=%s,
                   yetkili_adsoyad=%s, yetkili_tcno=%s, yetkili_dogum=%s, yetkili_ikametgah=%s,
                   yetkili_tel=%s, yetkili_tel2=%s, yetkili_email=%s, email=%s,
                   hizmet_turu=%s, aylik_kira=%s, yillik_kira=%s, sozlesme_no=%s, sozlesme_tarihi=%s, sozlesme_bitis=%s,
                   evrak_imza_sirkuleri=%s, evrak_vergi_levhasi=%s, evrak_ticaret_sicil=%s, evrak_faaliyet_belgesi=%s,
                   evrak_kimlik_fotokopi=%s, evrak_ikametgah=%s, evrak_kase=%s, notlar=%s, tamamlanma_yuzdesi=%s, updated_at=NOW()
                   WHERE id=%s""",
                (sirket_unvani, sirket_unvani, vergi_no, vergi_dairesi, mersis_no, ticaret_sicil_no,
                 kurulus_tarihi, faaliyet_konusu, nace_kodu, eski_adres, yeni_adres, sube_merkez,
                 yetkili_adsoyad, yetkili_tcno, yetkili_dogum, yetkili_ikametgah,
                 yetkili_tel, yetkili_tel2, yetkili_email, email,
                 hizmet_turu, aylik_kira, yillik_kira, sozlesme_no, sozlesme_tarihi, sozlesme_bitis,
                 evrak_imza_sirkuleri, evrak_vergi_levhasi, evrak_ticaret_sicil, evrak_faaliyet_belgesi,
                 evrak_kimlik_fotokopi, evrak_ikametgah, evrak_kase, notlar, tamamlanma_yuzdesi, mevcut["id"])
            )
            kyc_id = mevcut["id"]
            if musteri_id:
                execute(
                    "UPDATE customers SET name=%s, email=%s, phone=%s, address=%s, tax_number=%s WHERE id=%s",
                    (sirket_unvani or None, email or None, yetkili_tel or None, yeni_adres or None, vergi_no or None, musteri_id)
                )
        else:
            if musteri_id:
                execute(
                    "UPDATE customers SET name=%s, email=%s, phone=%s, address=%s, tax_number=%s WHERE id=%s",
                    (sirket_unvani or None, email or None, yetkili_tel or None, yeni_adres or None, vergi_no or None, musteri_id)
                )
            else:
                # Yeni müşteri oluştur
                cust = execute_returning(
                    """INSERT INTO customers (name, email, phone, address, notes, tax_number)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (sirket_unvani or "Yeni Müşteri", email or None, yetkili_tel or None, yeni_adres or None, notlar or None, vergi_no or None)
                )
                musteri_id = cust["id"] if cust else None
            row = execute_returning(
                """INSERT INTO musteri_kyc (
                   musteri_id, sirket_unvani, unvan, vergi_no, vergi_dairesi, mersis_no, ticaret_sicil_no,
                   kurulus_tarihi, faaliyet_konusu, nace_kodu, eski_adres, yeni_adres, sube_merkez,
                   yetkili_adsoyad, yetkili_tcno, yetkili_dogum, yetkili_ikametgah,
                   yetkili_tel, yetkili_tel2, yetkili_email, email,
                   hizmet_turu, aylik_kira, yillik_kira, sozlesme_no, sozlesme_tarihi, sozlesme_bitis,
                   evrak_imza_sirkuleri, evrak_vergi_levhasi, evrak_ticaret_sicil, evrak_faaliyet_belgesi,
                   evrak_kimlik_fotokopi, evrak_ikametgah, evrak_kase, notlar, tamamlanma_yuzdesi
                ) VALUES (
                   %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) RETURNING id""",
                (musteri_id, sirket_unvani, sirket_unvani, vergi_no, vergi_dairesi, mersis_no, ticaret_sicil_no,
                 kurulus_tarihi, faaliyet_konusu, nace_kodu, eski_adres, yeni_adres, sube_merkez,
                 yetkili_adsoyad, yetkili_tcno, yetkili_dogum, yetkili_ikametgah,
                 yetkili_tel, yetkili_tel2, yetkili_email, email,
                 hizmet_turu, aylik_kira, yillik_kira, sozlesme_no, sozlesme_tarihi, sozlesme_bitis,
                 evrak_imza_sirkuleri, evrak_vergi_levhasi, evrak_ticaret_sicil, evrak_faaliyet_belgesi,
                 evrak_kimlik_fotokopi, evrak_ikametgah, evrak_kase, notlar, tamamlanma_yuzdesi)
            )
            kyc_id = row["id"] if row else None
        return jsonify({"ok": True, "kyc_id": kyc_id})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/kyc/sozlesme")
@giris_gerekli
def api_kyc_sozlesme():
    """
    Seçili müşteri için KYC bilgileriyle Word sözleşmesi üret ve indir.
    Aynı müşteride tekrar çağrıldığında aynı sözleşme numarasını (yıl bazlı artan) kullanır.
    """
    musteri_id = request.args.get("musteri_id")
    if not musteri_id:
        flash("Müşteri seçilmedi.", "danger")
        return redirect(url_for("musteriler.giris"))

    kyc = fetch_one(
        "SELECT * FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
        (musteri_id,),
    )
    if not kyc:
        flash("Bu müşteri için KYC bilgisi bulunamadı. Önce Kaydet yapın.", "danger")
        return redirect(url_for("musteriler.giris"))

    # Sözleşme tarihi ve numarası
    soz_tarih = kyc.get("sozlesme_tarihi") or date.today()
    if isinstance(soz_tarih, str):
        try:
            soz_tarih = datetime.strptime(soz_tarih[:10], "%Y-%m-%d").date()
        except Exception:
            soz_tarih = date.today()
    yil = soz_tarih.year
    soz_no = (kyc.get("sozlesme_no") or "").strip()

    if not soz_no:
        # O yıla ait son sözleşme numarasını bul → bir artır
        like_pattern = f"SZL-{yil}-%"
        last = fetch_one(
            "SELECT sozlesme_no FROM musteri_kyc WHERE sozlesme_no LIKE %s ORDER BY sozlesme_no DESC LIMIT 1",
            (like_pattern,),
        )
        next_seq = 1
        if last and last.get("sozlesme_no"):
            try:
                parca = str(last["sozlesme_no"]).split("-")[-1]
                next_seq = int(parca) + 1
            except Exception:
                next_seq = 1
        soz_no = f"SZL-{yil}-{next_seq:04d}"
        execute(
            "UPDATE musteri_kyc SET sozlesme_no=%s, sozlesme_tarihi=%s WHERE id=%s",
            (soz_no, soz_tarih, kyc["id"]),
        )

    # Sözleşme metni için kullanılacak bilgiler
    unvan = kyc.get("sirket_unvani") or kyc.get("unvan") or ""
    vergi_no = kyc.get("vergi_no") or ""
    vergi_dairesi = kyc.get("vergi_dairesi") or ""
    mersis_no = kyc.get("mersis_no") or "......................................................."
    ticaret_sicil_no = kyc.get("ticaret_sicil_no") or "......................................................."
    faaliyet_konusu = kyc.get("faaliyet_konusu") or "......................................................."
    merkez_adresi = kyc.get("yeni_adres") or kyc.get("eski_adres") or ""

    yet_ad = kyc.get("yetkili_adsoyad") or ""
    yet_tc = kyc.get("yetkili_tcno") or ""
    yet_dogum = ""
    yd = kyc.get("yetkili_dogum")
    if yd:
        if hasattr(yd, "strftime"):
            yet_dogum = yd.strftime("%d.%m.%Y")
        else:
            try:
                yet_dogum = datetime.strptime(str(yd)[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
            except Exception:
                yet_dogum = str(yd)
    yet_ikamet = kyc.get("yetkili_ikametgah") or "......................................................."
    yet_tel = kyc.get("yetkili_tel") or ""
    yet_tel2 = kyc.get("yetkili_tel2") or ""
    yet_email = kyc.get("yetkili_email") or kyc.get("email") or ""

    hizmet_turu = (kyc.get("hizmet_turu") or "Sanal Ofis").upper()
    aylik = float(kyc.get("aylik_kira") or 0)
    yillik = float(kyc.get("yillik_kira") or 0)
    if not yillik and aylik:
        yillik = aylik * 12

    def tl_fmt(v):
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    doc = Document()

    baslik = doc.add_heading("HAZIR OFİS / SANAL OFİS\nADRES KULLANIM VE HİZMET SÖZLEŞMESİ", level=0)
    baslik.alignment = 1  # center

    doc.add_paragraph(f"Sözleşme No: {soz_no}  |  Tarih: {soz_tarih.strftime('%d.%m.%Y')}")

    doc.add_heading("MADDE 1 – TARAFLAR", level=1)
    doc.add_paragraph(
        "Hizmet Veren:\n"
        "Ofisbir Ofis ve Danışmanlık Hizmetleri A.Ş.\n"
        "Adres: Kavaklıdere Mah. Esat Caddesi No:12 İç Kapı No:1 Çankaya/Ankara\n"
        "Vergi No: 6340871926\n"
        '(İşbu sözleşmede "OFİSBİR" olarak anılacaktır.)\n'
    )
    doc.add_paragraph(
        "HİZMET ALAN (ŞİRKET BİLGİLERİ)\n"
        f"Unvan: {unvan}\n"
        f"Vergi No: {vergi_no}\n"
        f"Vergi Dairesi: {vergi_dairesi}\n"
        f"MERSİS No: {mersis_no}\n"
        f"Ticaret Sicil No: {ticaret_sicil_no}\n"
        f"Faaliyet Konusu: {faaliyet_konusu}\n"
        f"Merkez Adresi: {merkez_adresi}\n\n"
        "YETKİLİ KİŞİ BİLGİLERİ\n"
        f"Ad Soyad: {yet_ad}\n"
        f"T.C. Kimlik No: {yet_tc}\n"
        f"Doğum Tarihi: {yet_dogum or '.......................................................'}\n"
        f"İkamet Adresi: {yet_ikamet}\n"
        f"Cep Telefonu: {yet_tel}\n"
        f"Cep Telefonu 2: {yet_tel2 or '.......................................................'}\n"
        f"E-Posta: {yet_email or '.......................................................'}\n"
        "Islak İmza: .......................................................\n\n"
        "ORTAKLIK BİLGİLERİ\n"
        "Ortak 1 Ad Soyad / Unvan: .......................................................\n"
        "Pay Oranı (%): .......................................................\n\n"
        "Ortak 2 Ad Soyad / Unvan: .......................................................\n"
        "Pay Oranı (%): .......................................................\n\n"
        "Ortak 3 Ad Soyad / Unvan: .......................................................\n"
        "Pay Oranı (%): .......................................................\n\n"
        "Yabancı Ortak Varsa:\n"
        "Ad Soyad: .......................................................\n"
        "Uyruğu: .......................................................\n"
        "Pasaport No: .......................................................\n\n"
        '(İşbu sözleşmede "MÜŞTERİ" olarak anılacaktır.)\n'
        "Müşteri adına imza atan yetkili, şirket ile birlikte müştereken ve müteselsilen sorumludur.\n"
    )

    doc.add_heading("MADDE 2 – HİZMET TÜRÜ", level=1)
    sanal_sec = "☑" if "SANAL" in hizmet_turu else "☐"
    hazir_sec = "☑" if "HAZIR" in hizmet_turu else "☐"
    doc.add_paragraph(
        "Taraflar aşağıdaki hizmet türlerinden birini seçmiştir:\n"
        f"{sanal_sec} SANAL OFİS HİZMETİ\n"
        f"{hazir_sec} HAZIR OFİS HİZMETİ\n"
        "(Seçilen hizmet türü sözleşmenin ayrılmaz parçasıdır.)\n"
    )

    doc.add_heading("BÖLÜM A – SANAL OFİS HİZMETİ", level=1)
    doc.add_heading("MADDE 3A – KAPSAM", level=2)
    doc.add_paragraph(
        "Sanal ofis hizmeti; yasal adres tahsisi, posta/kargo/tebligat teslim alma ve sekreterya "
        "bilgilendirme hizmetlerini kapsar.\n"
        "Bu sözleşme kira sözleşmesi değildir. Taşınmaz üzerinde kiracılık hakkı doğurmaz. Ancak "
        "MÜŞTERİ'ye sözleşme süresince yasal adres kullanım hakkı verir.\n"
        "İşbu sözleşme kapsamında MÜŞTERİ'ye yasal adres kullanım hakkı verilmiş olup, bu adres "
        "vergi mevzuatı çerçevesinde işyeri adresi olarak bildirilebilir.\n"
    )
    doc.add_heading("MADDE 4A – Fiziki Kullanım", level=2)
    doc.add_paragraph(
        "Sanal ofis müşterisi sürekli masa veya oda kullanım hakkına sahip değildir. "
        "Ofise eşya bırakamaz ve ticari mal bulunduramaz.\n"
    )
    doc.add_heading("MADDE 5A – Haciz Güvencesi", level=2)
    doc.add_paragraph(
        "MÜŞTERİ, ofis adresinde kendisine ait mal bulunmadığını, ofisteki tüm demirbaşların "
        "OFİSBİR'e ait olduğunu ve haciz halinde OFİSBİR'in üçüncü kişi olduğunu kabul eder.\n"
        "Haciz bildirgesi gelmesi halinde sözleşme kendiliğinden feshedilir.\n"
    )

    doc.add_heading("ORTAK HÜKÜMLER", level=1)
    doc.add_heading("MADDE 6 – HİZMET BEDELİ", level=2)
    doc.add_paragraph(
        f"Yıllık Hizmet Bedeli: {tl_fmt(yillik)} TL + KDV\n"
        f"Aylık Hizmet Bedeli: {tl_fmt(aylik)} TL + KDV\n\n"
        "Ödemeler aylık olarak OFİSBİR'in bildirdiği banka hesabına yapılacaktır.\n"
        "İki aylık ödeme gecikmesi halinde sözleşme tek taraflı feshedilebilir.\n"
    )

    doc.add_heading("MADDE 7 – ERKEN FESİH", level=2)
    doc.add_paragraph(
        "MÜŞTERİ, sözleşme süresi dolmadan ayrılmak isterse yazılı bildirim yapmak kaydıyla "
        "sözleşmesini feshedebilir.\n"
        "Erken fesih halinde 2 (iki) aylık hizmet bedeli tutarında erken fesih bedeli ödemeyi kabul eder.\n"
        "Bu bedel makul cezai şart niteliğindedir.\n"
    )

    doc.add_heading("MADDE 8 – OTOMATİK YENİLEME", level=2)
    doc.add_paragraph(
        "Sözleşme bitiminden 15 gün önce yazılı fesih yapılmazsa 1 yıl süreyle aynı şartlarla yenilenir.\n"
    )

    doc.add_heading("MADDE 9 – MÜTESELSİL SORUMLULUK", level=2)
    doc.add_paragraph(
        "Şirket yetkilisi işbu sözleşmeden doğan borçlardan şirket ile birlikte müştereken ve "
        "müteselsilen sorumludur.\n"
    )

    doc.add_heading("MADDE 10 – YETKİLİ MAHKEME", level=2)
    doc.add_paragraph(
        "İşbu sözleşmeden doğacak uyuşmazlıklarda Ankara Mahkemeleri ve İcra Daireleri yetkilidir.\n"
    )

    doc.add_heading("MADDE 11 – YÜRÜRLÜK", level=2)
    doc.add_paragraph(
        f"İşbu sözleşme {soz_tarih.strftime('%d.%m.%Y')} tarihinde iki nüsha olarak düzenlenmiş ve "
        "imza altına alınmıştır.\n"
    )

    doc.add_paragraph("\nOFİSBİR\t\tMÜŞTERİ\t\tYetkili (Müteselsil Sorumlu)\n\n\n")
    doc.add_paragraph("İmza:\t\tİmza:\t\tİmza:\n")
    doc.add_paragraph(f"Sözleşme No: {soz_no}  |  {soz_tarih.strftime('%d.%m.%Y')}")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(base_dir, "uploads", "sozlesmeler")
    os.makedirs(out_dir, exist_ok=True)

    safe_unvan = (unvan or "Musteri").replace("/", "-").replace("\\", "-").strip()
    filename = f"Sozlesme_{soz_no}_{safe_unvan[:40].replace(' ', '_')}.docx"
    filepath = os.path.join(out_dir, filename)

    doc.save(filepath)

    return send_file(filepath, as_attachment=True, download_name=filename)


@bp.route("/api/rent_progression")
@giris_gerekli
def api_rent_progression():
    """
    Müşterilerin yıllara göre kira artış verileri
    JSON: {rows: [{id, name, tax_number, ilk_kira_bedeli, current_rent, years: {2023:1234.0}}], years: [2021,2022...]}
    """
    customers = fetch_all(
        "SELECT id, name, tax_number, rent_start_date, rent_start_year, rent_start_month, ilk_kira_bedeli FROM customers ORDER BY name"
    )
    tufe_rows = fetch_all("SELECT year, month, oran FROM tufe_verileri")
    tufe = {}
    for r in tufe_rows:
        try:
            y = int(r.get("year"))
            m = int(r.get("month"))
            tufe[(y,m)] = float(r.get("oran") or 0)
        except Exception:
            continue

    result = []
    years_set = set()
    today = date.today()

    def parse_month(m):
        if m is None:
            return 1
        try:
            return int(m)
        except Exception:
            s = str(m).strip()
            if not s:
                return 1
            for idx, name in enumerate(MONTHS_TR, start=1):
                if name.lower() in s.lower():
                    return idx
            return 1

    def parse_year(y, rent_start_date):
        if y:
            try:
                return int(y)
            except Exception:
                pass
        if rent_start_date:
            s = str(rent_start_date)
            parts = s.split(".")
            if len(parts) >= 3:
                try:
                    return int(parts[2])
                except Exception:
                    pass
        return today.year

    for c in customers:
        initial = float(c.get("ilk_kira_bedeli") or 0)
        start_year = parse_year(c.get("rent_start_year"), c.get("rent_start_date"))
        start_month = parse_month(c.get("rent_start_month"))

        rent = initial
        years_dict = {}
        for y in range(start_year, today.year + 1):
            if y == start_year:
                years_dict[y] = round(rent,2)
                years_set.add(y)
                continue
            rate = tufe.get((y, start_month))
            if rate:
                rent = rent * (1.0 + float(rate)/100.0)
            years_dict[y] = round(rent,2)
            years_set.add(y)

        current_rent = round(rent,2)

        result.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "tax_number": c.get("tax_number"),
            "ilk_kira_bedeli": initial,
            "current_rent": current_rent,
            "years": years_dict
        })

    return jsonify({"rows": result, "years": sorted(list(years_set))})