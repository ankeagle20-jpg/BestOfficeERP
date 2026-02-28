from flask import Blueprint, render_template, request, jsonify
from auth import giris_gerekli, admin_gerekli
from db import fetch_all, fetch_one, execute, execute_returning
from datetime import datetime, date

bp = Blueprint("tahsilat", __name__)


def _parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


@bp.route("/")
@giris_gerekli
def index():
    return render_template("tahsilat/index.html")


@bp.route("/api/ozet")
@giris_gerekli
def api_ozet():
    """Nakit, Banka, Toplam tahsilat özeti (tarih aralığına göre)."""
    baslangic = _parse_date(request.args.get("baslangic"))
    bitis = _parse_date(request.args.get("bitis"))
    if not baslangic:
        baslangic = date.today()
    if not bitis:
        bitis = date.today()
    if baslangic > bitis:
        baslangic, bitis = bitis, baslangic

    nakit = fetch_one(
        "SELECT COALESCE(SUM(tutar), 0) as total FROM tahsilatlar WHERE odeme_turu = 'nakit' AND tahsilat_tarihi >= %s AND tahsilat_tarihi <= %s",
        (baslangic, bitis)
    )
    banka = fetch_one(
        "SELECT COALESCE(SUM(tutar), 0) as total FROM tahsilatlar WHERE odeme_turu = 'banka' AND tahsilat_tarihi >= %s AND tahsilat_tarihi <= %s",
        (baslangic, bitis)
    )
    toplam = fetch_one(
        "SELECT COALESCE(SUM(tutar), 0) as total FROM tahsilatlar WHERE tahsilat_tarihi >= %s AND tahsilat_tarihi <= %s",
        (baslangic, bitis)
    )
    return jsonify({
        "nakit": float(nakit.get("total") or 0),
        "banka": float(banka.get("total") or 0),
        "toplam": float(toplam.get("total") or 0),
    })


@bp.route("/api/borclular")
@giris_gerekli
def api_borclular():
    """
    Return debtors with this_month and total outstanding and last payment.
    """
    sql = """
    SELECT c.id, c.name,
      COALESCE( (SELECT SUM(f.toplam) FROM faturalar f WHERE f.musteri_id = c.id AND f.durum != 'odendi'), 0 ) as toplam_fatura,
      COALESCE( (SELECT SUM(t.tutar) FROM tahsilatlar t WHERE t.musteri_id = c.id), 0 ) as toplam_tahsilat,
      COALESCE( (SELECT MAX(t.tahsilat_tarihi) FROM tahsilatlar t WHERE t.musteri_id = c.id), NULL ) as son_tahsilat
    FROM customers c
    ORDER BY c.name
    """
    rows = fetch_all(sql)
    result = []
    today = date.today()
    ay_bas = today.replace(day=1)
    for r in rows:
        toplam_fat = float(r.get("toplam_fatura") or 0)
        toplam_tah = float(r.get("toplam_tahsilat") or 0)
        toplam = round(toplam_fat - toplam_tah, 2)
        if toplam <= 0:
            continue
        result.append({
            "id": r["id"],
            "name": r["name"],
            "this_month": 0.0,
            "total_due": toplam,
            "last_payment": str(r.get("son_tahsilat") or "")[:10] if r.get("son_tahsilat") else None
        })
    return jsonify(result)


@bp.route("/api/faturalar_odenmemis")
@giris_gerekli
def api_faturalar_odenmemis():
    """Seçili müşteriye ait ödenmemiş faturalar (fatura_id bağlamak için)."""
    musteri_id = request.args.get("musteri_id")
    if not musteri_id:
        return jsonify([])
    rows = fetch_all(
        "SELECT id, fatura_no, toplam, fatura_tarihi, vade_tarihi FROM faturalar WHERE musteri_id = %s AND durum != 'odendi' ORDER BY fatura_tarihi DESC",
        (musteri_id,)
    )
    return jsonify(rows)


@bp.route("/api/tahsilat/save", methods=["POST"])
@giris_gerekli
def api_tahsilat_save():
    try:
        data = request.json or request.form
        musteri_id = int(data.get("musteri_id"))
        tutar = float(str(data.get("tutar", 0)).replace(",", ".") or 0)
        if tutar <= 0:
            return jsonify({"ok": False, "mesaj": "Tutar 0'dan büyük olmalıdır."}), 400
        odeme = (data.get("odeme") or "nakit").lower()
        if odeme not in ("nakit", "banka"):
            odeme = "nakit"
        aciklama = (data.get("aciklama") or "").strip()
        tarih_str = data.get("tarih") or datetime.today().strftime("%Y-%m-%d")
        tarih = _parse_date(tarih_str) or date.today()
        fatura_id = data.get("fatura_id")
        if fatura_id:
            try:
                fatura_id = int(fatura_id)
            except (TypeError, ValueError):
                fatura_id = None

        row = execute_returning(
            """INSERT INTO tahsilatlar (customer_id, fatura_id, tutar, odeme_turu, aciklama, tahsilat_tarihi)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (musteri_id, fatura_id, tutar, odeme, aciklama, tarih)
        )
        if fatura_id:
            execute("UPDATE faturalar SET durum = 'odendi' WHERE id = %s", (fatura_id,))
        return jsonify({"ok": True, "id": row.get("id")})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/tahsilat/list")
@giris_gerekli
def api_tahsilat_list():
    musteri_id = request.args.get("musteri_id")
    baslangic = _parse_date(request.args.get("baslangic"))
    bitis = _parse_date(request.args.get("bitis"))

    sql = """SELECT t.id, t.musteri_id, c.name as musteri_adi, t.tutar, t.odeme_turu, t.aciklama, t.tahsilat_tarihi, t.fatura_id
             FROM tahsilatlar t LEFT JOIN customers c ON t.musteri_id = c.id WHERE 1=1"""
    params = []
    if musteri_id:
        sql += " AND t.musteri_id = %s"
        params.append(musteri_id)
    if baslangic:
        sql += " AND t.tahsilat_tarihi >= %s"
        params.append(baslangic)
    if bitis:
        sql += " AND t.tahsilat_tarihi <= %s"
        params.append(bitis)
    sql += " ORDER BY t.tahsilat_tarihi DESC LIMIT 200"
    rows = fetch_all(sql, params)
    return jsonify(rows)


@bp.route("/api/tahsilat/delete", methods=["POST"])
@admin_gerekli
def api_tahsilat_delete():
    """Sadece admin silebilir."""
    try:
        tid = int(request.form.get("tahsilat_id") or request.json.get("tahsilat_id"))
        execute("DELETE FROM tahsilatlar WHERE id = %s", (tid,))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400

