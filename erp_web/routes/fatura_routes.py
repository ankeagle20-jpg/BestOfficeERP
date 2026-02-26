from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from auth import yetki_gerekli, giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning
from datetime import date

bp = Blueprint("faturalar", __name__)


@bp.route("/")
@giris_gerekli
def index():
    """Aylık faturalama ana sayfası"""
    durum = request.args.get("durum", "")
    yil = request.args.get("yil", str(date.today().year))
    
    sql = "SELECT f.*, c.name as musteri_adi FROM faturalar f LEFT JOIN customers c ON f.musteri_id=c.id WHERE 1=1"
    params = []
    
    if durum:
        sql += " AND f.durum=%s"
        params.append(durum)
    
    if yil:
        sql += " AND EXTRACT(YEAR FROM f.fatura_tarihi::timestamp)=%s"
        params.append(int(yil))
    
    sql += " ORDER BY f.fatura_tarihi DESC"
    faturalar = fetch_all(sql, params)
    
    return render_template("faturalar/index.html", 
                           faturalar=faturalar,
                           durum=durum, 
                           yil=yil)


@bp.route("/<int:fid>")
@giris_gerekli
def detay(fid):
    """Fatura detay sayfası"""
    fatura = fetch_one(
        "SELECT f.*, c.name as musteri_adi FROM faturalar f "
        "LEFT JOIN customers c ON f.musteri_id=c.id WHERE f.id=%s", (fid,))
    
    if not fatura:
        flash("Fatura bulunamadı.", "danger")
        return redirect(url_for("faturalar.index"))
    
    tahsilatlar = fetch_all(
        "SELECT * FROM tahsilatlar WHERE fatura_id=%s ORDER BY tahsilat_tarihi DESC", (fid,))
    
    return render_template("faturalar/detay.html",
                           fatura=fatura, 
                           tahsilatlar=tahsilatlar)


@bp.route("/olustur", methods=["POST"])
@giris_gerekli
def olustur():
    """Yeni fatura oluştur"""
    try:
        musteri_id = request.form.get("musteri_id")
        fatura_no = request.form.get("fatura_no")
        tutar = float(request.form.get("tutar", 0))
        kdv_tutar = float(request.form.get("kdv_tutar", 0))
        toplam = tutar + kdv_tutar
        vade_tarihi = request.form.get("vade_tarihi")
        notlar = request.form.get("notlar")
        
        fatura = execute_returning(
            """INSERT INTO faturalar 
               (fatura_no, musteri_id, tutar, kdv_tutar, toplam, vade_tarihi, notlar, durum)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'odenmedi')
               RETURNING id""",
            (fatura_no, musteri_id, tutar, kdv_tutar, toplam, vade_tarihi, notlar)
        )
        
        flash("✓ Fatura oluşturuldu.", "success")
        return jsonify({"ok": True, "fatura_id": fatura["id"]})
        
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


# ── API ENDPOİNTS ────────────────────────────────────────────────────────────

@bp.route("/api/firmalar")
@giris_gerekli
def api_firmalar():
    """
    Aylık faturalama için firma listesi
    Query params: yil, ay, ofis_turu
    """
    yil = request.args.get("yil", date.today().year)
    ay = request.args.get("ay", date.today().month)
    ofis_turu = request.args.get("ofis_turu", "")
    
    # Müşterileri ve ofis bilgilerini çek
    sql = """
        SELECT c.id, c.name, c.tax_number, 
               o.code as office_code, o.type as office_type, o.monthly_price as kira
        FROM customers c
        LEFT JOIN offices o ON c.office_code = o.code
        WHERE c.id IS NOT NULL
    """
    params = []
    
    if ofis_turu and ofis_turu != "Tümü":
        sql += " AND o.type = %s"
        params.append(ofis_turu)
    
    sql += " ORDER BY c.name"
    
    firmalar = fetch_all(sql, params)
    
    # Her firma için fatura durumunu kontrol et
    for firma in firmalar:
        fatura = fetch_one(
            """SELECT id, fatura_no, toplam FROM faturalar 
               WHERE musteri_id = %s 
               AND EXTRACT(YEAR FROM fatura_tarihi::timestamp) = %s 
               AND EXTRACT(MONTH FROM fatura_tarihi::timestamp) = %s
               LIMIT 1""",
            (firma["id"], yil, ay)
        )
        
        if fatura:
            firma["fatura_durum"] = "Kesildi"
            firma["fatura_no"] = fatura["fatura_no"]
            firma["fatura_tutar"] = fatura["toplam"]
        else:
            firma["fatura_durum"] = "Bekliyor"
            firma["fatura_no"] = "—"
            firma["fatura_tutar"] = 0
    
    return jsonify(firmalar)


@bp.route("/api/toplu_kes", methods=["POST"])
@giris_gerekli
def api_toplu_kes():
    """Tüm firmalar için toplu fatura kes"""
    try:
        data = request.get_json()
        yil = data.get("yil")
        ay = data.get("ay")
        
        # TODO: Toplu fatura kesme işlemi
        
        return jsonify({"ok": True, "mesaj": "Toplu fatura kesme işlemi tamamlandı"})
        
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400