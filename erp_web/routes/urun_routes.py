"""
Ürünler: ürün listesi, ekleme/güncelleme/silme, ürün çoğalt, otomatik stok kodu.
"""
from flask import Blueprint, render_template, request, jsonify
from auth import giris_gerekli, admin_gerekli
from db import fetch_all, fetch_one, execute, execute_returning

bp = Blueprint("urunler", __name__)


@bp.route("/")
@giris_gerekli
def index():
    return render_template("urunler/index.html")


@bp.route("/api/list")
@giris_gerekli
def api_list():
    """Tüm ürünler (opsiyonel arama)."""
    q = (request.args.get("q") or "").strip()[:100]
    sql = """
    SELECT id, urun_adi, stok_kodu, birim_fiyat, stok_miktari, birim, aciklama, is_active
    FROM urunler WHERE 1=1
    """
    params = []
    if q:
        sql += " AND (LOWER(urun_adi) LIKE LOWER(%s) OR LOWER(stok_kodu) LIKE LOWER(%s))"
        params.extend(("%" + q + "%", "%" + q + "%"))
    sql += " ORDER BY stok_kodu"
    rows = fetch_all(sql, tuple(params) if params else None)
    for r in rows or []:
        r["birim_fiyat"] = float(r.get("birim_fiyat") or 0)
        r["stok_miktari"] = float(r.get("stok_miktari") or 0)
        r["is_active"] = bool(r.get("is_active"))
    return jsonify(rows or [])


def _next_stok_kodu():
    """Mevcut sayısal stok kodlarından sonraki 4 haneli kodu döndür (0001, 0002, ...)."""
    try:
        r = fetch_one(
            "SELECT stok_kodu FROM urunler WHERE stok_kodu ~ '^[0-9]+$' ORDER BY CAST(stok_kodu AS INTEGER) DESC LIMIT 1"
        )
        if not r or not r.get("stok_kodu"):
            return "0001"
        n = int(r["stok_kodu"])
        return str(n + 1).zfill(4)
    except Exception:
        r2 = fetch_one("SELECT COUNT(*) as c FROM urunler")
        c = int((r2 or {}).get("c") or 0)
        return str(c + 1).zfill(4)


@bp.route("/api/next_kod")
@giris_gerekli
def api_next_kod():
    """Otomatik sonraki stok kodu: 4 haneli (0001, 0002, ...)."""
    return jsonify({"stok_kodu": _next_stok_kodu()})


@bp.route("/api/save", methods=["POST"])
@giris_gerekli
def api_save():
    """Ürün ekle veya güncelle."""
    try:
        data = request.json or request.form
        urun_adi = (data.get("urun_adi") or "").strip()
        if not urun_adi:
            return jsonify({"ok": False, "mesaj": "Ürün adı zorunlu"}), 400
        stok_kodu = (data.get("stok_kodu") or "").strip()
        if not stok_kodu:
            return jsonify({"ok": False, "mesaj": "Stok kodu zorunlu"}), 400
        try:
            birim_fiyat = float(str(data.get("birim_fiyat", 0)).replace(",", ".") or 0)
        except (TypeError, ValueError):
            birim_fiyat = 0
        try:
            stok_miktari = float(str(data.get("stok_miktari", 0)).replace(",", ".") or 0)
        except (TypeError, ValueError):
            stok_miktari = 0
        birim = (data.get("birim") or "adet").strip()[:20]
        aciklama = (data.get("aciklama") or "").strip()[:500]
        is_active = data.get("is_active") not in (False, 0, "0", "false")
        pid = data.get("id")

        if pid:
            existing = fetch_one("SELECT id FROM urunler WHERE stok_kodu = %s AND id != %s", (stok_kodu, int(pid)))
            if existing:
                return jsonify({"ok": False, "mesaj": "Bu stok kodu başka üründe kullanılıyor"}), 400
            execute(
                """UPDATE urunler SET urun_adi=%s, stok_kodu=%s, birim_fiyat=%s, stok_miktari=%s, birim=%s, aciklama=%s, is_active=%s WHERE id=%s""",
                (urun_adi, stok_kodu, birim_fiyat, stok_miktari, birim, aciklama or None, is_active, int(pid)),
            )
            return jsonify({"ok": True, "id": int(pid)})
        existing = fetch_one("SELECT id FROM urunler WHERE stok_kodu = %s", (stok_kodu,))
        if existing:
            return jsonify({"ok": False, "mesaj": "Bu stok kodu zaten var"}), 400
        row = execute_returning(
            """INSERT INTO urunler (urun_adi, stok_kodu, birim_fiyat, stok_miktari, birim, aciklama, is_active)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (urun_adi, stok_kodu, birim_fiyat, stok_miktari, birim, aciklama or None, is_active),
        )
        return jsonify({"ok": True, "id": row["id"]})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/delete", methods=["POST"])
@giris_gerekli
@admin_gerekli
def api_delete():
    """Ürün sil (sadece admin)."""
    try:
        data = request.json or request.form
        pid = data.get("id")
        if not pid:
            return jsonify({"ok": False, "mesaj": "Ürün seçin"}), 400
        execute("DELETE FROM urunler WHERE id = %s", (int(pid),))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/duplicate", methods=["POST"])
@giris_gerekli
def api_duplicate():
    """Ürün çoğalt: kaynak ürünü kopyala, yeni stok kodu ver (otomatik veya gönderilen)."""
    try:
        data = request.json or request.form
        kaynak_id = data.get("kaynak_id") or data.get("id")
        if not kaynak_id:
            return jsonify({"ok": False, "mesaj": "Çoğaltılacak ürün seçin"}), 400
        kaynak_id = int(kaynak_id)
        yeni_stok_kodu = (data.get("yeni_stok_kodu") or "").strip()
        yeni_urun_adi = (data.get("yeni_urun_adi") or "").strip()

        kaynak = fetch_one(
            "SELECT id, urun_adi, stok_kodu, birim_fiyat, stok_miktari, birim, aciklama FROM urunler WHERE id = %s",
            (kaynak_id,),
        )
        if not kaynak:
            return jsonify({"ok": False, "mesaj": "Ürün bulunamadı"}), 404

        if not yeni_stok_kodu:
            yeni_stok_kodu = _next_stok_kodu()
        if fetch_one("SELECT id FROM urunler WHERE stok_kodu = %s", (yeni_stok_kodu,)):
            return jsonify({"ok": False, "mesaj": "Bu stok kodu zaten kullanılıyor; farklı bir kod girin veya otomatik kullanın."}), 400

        yeni_ad = yeni_urun_adi or (kaynak.get("urun_adi") or "") + " (kopya)"
        birim_fiyat = float(kaynak.get("birim_fiyat") or 0)
        stok_miktari = float(kaynak.get("stok_miktari") or 0)
        birim = (kaynak.get("birim") or "adet")[:20]
        aciklama = (kaynak.get("aciklama") or "")[:500]

        row = execute_returning(
            """INSERT INTO urunler (urun_adi, stok_kodu, birim_fiyat, stok_miktari, birim, aciklama, is_active)
               VALUES (%s, %s, %s, %s, %s, %s, TRUE) RETURNING id""",
            (yeni_ad, yeni_stok_kodu, birim_fiyat, stok_miktari, birim, aciklama or None),
        )
        return jsonify({"ok": True, "id": row["id"], "stok_kodu": yeni_stok_kodu})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
