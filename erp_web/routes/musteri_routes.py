from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from auth import yetki_gerekli, giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning
import pandas as pd
from io import BytesIO
from datetime import date, datetime
from docx import Document
import os

# helper month names (Turkish)
MONTHS_TR = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]

bp = Blueprint("musteriler", __name__)


@bp.route("/")
@giris_gerekli
def index():
    """Müşteriler grid görünümü - desktop gibi"""
    arama = request.args.get("q", "").strip()
    if arama:
        musteriler = fetch_all(
            "SELECT * FROM customers WHERE name ILIKE %s ORDER BY name",
            (f"%{arama}%",))
    else:
        musteriler = fetch_all("SELECT * FROM customers ORDER BY name")
    
    return render_template("musteriler/index.html",
                           faturalar=musteriler, arama=arama)


@bp.route("/<int:mid>")
@giris_gerekli
def detay(mid):
    """Müşteri detay sayfası"""
    musteri = fetch_one("SELECT * FROM customers WHERE id=%s", (mid,))
    if not musteri:
        flash("Müşteri bulunamadı.", "danger")
        return redirect(url_for("musteriler.index"))
    faturalar = fetch_all(
        "SELECT * FROM faturalar WHERE musteri_id=%s ORDER BY fatura_tarihi DESC", (mid,))
    kargolar = fetch_all(
        "SELECT * FROM kargolar WHERE musteri_id=%s ORDER BY created_at DESC LIMIT 20", (mid,))
    return render_template("musteriler/detay.html",
                           musteri=musteri, faturalar=faturalar, kargolar=kargolar)


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
        f = request.files.get("file")
        if not f:
            flash("Lütfen bir Excel dosyası seçin.", "warning")
            return redirect(request.url)
        try:
            df = pd.read_excel(BytesIO(f.read()), engine="openpyxl", header=0)
        except Exception as e:
            flash(f"Excel okunamadı: {e}", "danger")
            return redirect(request.url)

        # normalize columns
        df.columns = [str(c).strip().lower() for c in df.columns]
        def find_col(keys):
            for k in keys:
                for col in df.columns:
                    if k in col:
                        return col
            return None

        name_col = find_col(["ad", "name", "ünvan", "unvan"])
        email_col = find_col(["email", "e-posta", "eposta", "mail"])
        phone_col = find_col(["telefon", "phone", "tel"])
        addr_col = find_col(["adres", "address"])
        tax_col = find_col(["vergi", "tax", "vkn", "tckn"])

        if not name_col:
            flash("Excel'de müşteri adı sütunu bulunamadı.", "danger")
            return redirect(request.url)

        inserted = 0
        for _, row in df.iterrows():
            name = str(row.get(name_col) or "").strip()
            if not name:
                continue
            email = str(row.get(email_col) or "").strip() if email_col else None
            phone = str(row.get(phone_col) or "").strip() if phone_col else None
            address = str(row.get(addr_col) or "").strip() if addr_col else None
            tax = str(row.get(tax_col) or "").strip() if tax_col else None

            # upsert by tax_number if available, else insert new
            if tax:
                existing = fetch_one("SELECT id FROM customers WHERE tax_number = %s", (tax,))
            else:
                existing = None

            if existing:
                execute(
                    "UPDATE customers SET name=%s,email=%s,phone=%s,address=%s WHERE id=%s",
                    (name, email, phone, address, existing["id"])
                )
            else:
                execute(
                    "INSERT INTO customers (name, email, phone, address, tax_number) VALUES (%s,%s,%s,%s,%s)",
                    (name, email, phone, address, tax)
                )
            inserted += 1

        flash(f"Excel'den {inserted} müşteri işlendi.", "success")
        return redirect(url_for("musteriler.index"))

    return render_template("musteriler/import.html")


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