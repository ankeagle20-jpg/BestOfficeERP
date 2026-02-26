"""
Bankalar: hesap dökümü, ekstre yükleme, tahsilat eşleştirme, masraf takibi.
"""
from flask import Blueprint, render_template, request, jsonify
from auth import giris_gerekli, admin_gerekli
from db import fetch_all, fetch_one, execute, execute_returning
from datetime import datetime, date

bp = Blueprint("banka", __name__)


def _parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(s).strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


@bp.route("/")
@giris_gerekli
def index():
    return render_template("bankalar/index.html")


@bp.route("/api/hesaplar")
@giris_gerekli
def api_hesaplar():
    """Aktif banka hesapları listesi (dropdown için)."""
    rows = fetch_all(
        "SELECT id, banka_adi, COALESCE(hesap_adi, banka_adi) as hesap_adi, hesap_no FROM banka_hesaplar WHERE is_active = TRUE ORDER BY banka_adi"
    )
    return jsonify(rows or [])


@bp.route("/api/ozet")
@giris_gerekli
def api_ozet():
    """Toplam hareket sayısı, eşleşen sayı, bekleyen tutar (TL), toplam masraflar."""
    hesap_id = request.args.get("hesap_id")
    sql_where = ""
    params = []
    if hesap_id:
        sql_where = " AND banka_hesap_id = %s"
        params.append(hesap_id)

    toplam = fetch_one(
        f"SELECT COUNT(*) as c FROM banka_hareketleri WHERE 1=1{sql_where}", tuple(params) or None
    )
    eslesti = fetch_one(
        f"SELECT COUNT(*) as c FROM banka_hareketleri WHERE durum = 'eslesti'{sql_where}", tuple(params) or None
    )
    bekleyen = fetch_one(
        f"SELECT COALESCE(SUM(tutar), 0) as t FROM banka_hareketleri WHERE durum = 'bekleyen' AND tutar > 0{sql_where}",
        tuple(params) or None,
    )
    masraflar = fetch_one(
        f"SELECT COALESCE(SUM(ABS(tutar)), 0) as t FROM banka_hareketleri WHERE (tutar < 0 OR tip = 'giden'){sql_where}",
        tuple(params) or None,
    )
    return jsonify({
        "toplam": int((toplam or {}).get("c") or 0),
        "eslesti": int((eslesti or {}).get("c") or 0),
        "bekleyen_tutar": float((bekleyen or {}).get("t") or 0),
        "masraflar_tutar": float((masraflar or {}).get("t") or 0),
    })


@bp.route("/api/hareketler")
@giris_gerekli
def api_hareketler():
    """Banka hareketleri listesi (hesap, durum filtreli)."""
    hesap_id = request.args.get("hesap_id")
    durum = request.args.get("durum", "tumu").strip().lower()

    sql = """
    SELECT h.id, h.banka_hesap_id, h.hareket_tarihi, h.aciklama, h.gonderici, h.tutar, h.tip, h.durum,
           h.musteri_id, h.tahsilat_id, c.name as musteri_adi, b.banka_adi, b.hesap_adi
    FROM banka_hareketleri h
    LEFT JOIN banka_hesaplar b ON b.id = h.banka_hesap_id
    LEFT JOIN customers c ON c.id = h.musteri_id
    WHERE 1=1
    """
    params = []
    if hesap_id:
        sql += " AND h.banka_hesap_id = %s"
        params.append(hesap_id)
    if durum and durum != "tumu":
        sql += " AND h.durum = %s"
        params.append(durum)
    sql += " ORDER BY h.hareket_tarihi DESC, h.id DESC LIMIT 500"
    rows = fetch_all(sql, tuple(params) if params else None)
    for r in rows:
        if r.get("hareket_tarihi"):
            r["hareket_tarihi"] = r["hareket_tarihi"].isoformat()[:10] if hasattr(r["hareket_tarihi"], "isoformat") else str(r["hareket_tarihi"])[:10]
    return jsonify(rows or [])


@bp.route("/api/musteri_ara")
@giris_gerekli
def api_musteri_ara():
    """Müşteri ara (eşleştirme paneli için)."""
    q = (request.args.get("q") or "").strip()[:80]
    if not q:
        return jsonify([])
    rows = fetch_all(
        "SELECT id, name FROM customers WHERE LOWER(name) LIKE LOWER(%s) ORDER BY name LIMIT 30",
        ("%" + q + "%",),
    )
    return jsonify(rows or [])


@bp.route("/api/ekstre_yukle", methods=["POST"])
@giris_gerekli
def api_ekstre_yukle():
    """Ekstre satırlarını yükle: JSON body veya CSV/Excel dosyası.
    JSON formatı: { "banka_hesap_id": 1, "satirlar": [ { "tarih": "YYYY-MM-DD", "aciklama": "", "gonderici": "", "tutar": 123.45, "tip": "gelen" } ] }
    """
    try:
        banka_hesap_id = None
        satirlar = []

        if request.is_json:
            data = request.json
            banka_hesap_id = data.get("banka_hesap_id")
            satirlar = data.get("satirlar") or []
        elif request.files:
            f = request.files.get("file")
            if not f:
                return jsonify({"ok": False, "mesaj": "Dosya seçin"}), 400
            banka_hesap_id = request.form.get("banka_hesap_id")
            if banka_hesap_id:
                banka_hesap_id = int(banka_hesap_id)
            import io
            filename = (f.filename or "").lower()
            if filename.endswith(".xlsx"):
                # Excel .xlsx: openpyxl ile oku
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(io.BytesIO(f.read()), read_only=True, data_only=True)
                    ws = wb.active
                    rows_iter = list(ws.iter_rows(values_only=True))
                    wb.close()
                    if not rows_iter:
                        return jsonify({"ok": False, "mesaj": "Excel dosyası boş"}), 400
                    headers = [str(c or "").strip().lower() for c in rows_iter[0]]
                    col_map = {"tarih": 0, "aciklama": 1, "gonderici": 2, "tutar": 3, "tip": 4}
                    for idx, h in enumerate(headers):
                        h = (h or "").replace("ı", "i").replace("ö", "o").replace("ü", "u").replace("ş", "s").replace("ç", "c").replace("ğ", "g")
                        if "tarih" in h or h == "date": col_map["tarih"] = idx
                        elif "aciklama" in h or "description" in h: col_map["aciklama"] = idx
                        elif "gonderici" in h or "gonderen" in h or "sender" in h: col_map["gonderici"] = idx
                        elif "tutar" in h or "amount" in h: col_map["tutar"] = idx
                        elif "tip" in h or "type" in h: col_map["tip"] = idx
                    for row in rows_iter[1:]:
                        if not row or len(row) < 4:
                            continue
                        def cell(i):
                            return row[i] if i < len(row) else None
                        tarih_val = cell(col_map["tarih"])
                        if tarih_val is not None and hasattr(tarih_val, "strftime"):
                            tarih = tarih_val.strftime("%Y-%m-%d")
                        else:
                            tarih = str(tarih_val or "")[:10] if tarih_val else ""
                        aciklama = str(cell(col_map["aciklama"]) or "")[:500]
                        gonderici = str(cell(col_map["gonderici"]) or "")[:200]
                        try:
                            tutar = float(cell(col_map["tutar"]) or 0)
                        except (TypeError, ValueError):
                            tutar = 0
                        tip = (str(cell(col_map["tip"]) or "gelen")).strip().lower()[:20]
                        if tip not in ("gelen", "giden", "transfer"):
                            tip = "giden" if tutar < 0 else "gelen"
                        if tutar < 0:
                            tutar = abs(tutar)
                            tip = "giden"
                        satirlar.append({"tarih": tarih, "aciklama": aciklama, "gonderici": gonderici, "tutar": tutar, "tip": tip})
                except Exception as ex:
                    return jsonify({"ok": False, "mesaj": "Excel okunamadı: " + str(ex)}), 400
            elif filename.endswith(".xls"):
                return jsonify({"ok": False, "mesaj": "Eski .xls formatı desteklenmiyor; lütfen .xlsx kullanın."}), 400
            else:
                # CSV: tarih;aciklama;gonderici;tutar;tip veya virgül
                content = f.read().decode("utf-8", errors="ignore")
                lines = content.strip().split("\n")
                if not lines:
                    return jsonify({"ok": False, "mesaj": "Dosya boş"}), 400
                sep = ";" if ";" in lines[0] else ","
                headers = [h.strip().lower() for h in lines[0].split(sep)]
                for line in lines[1:]:
                    parts = [p.strip() for p in line.split(sep)]
                    if len(parts) < 4:
                        continue
                    row = {}
                    for i, h in enumerate(headers):
                        if i < len(parts):
                            row[h] = parts[i]
                    tarih = row.get("tarih") or row.get("date")
                    aciklama = row.get("aciklama") or row.get("açıklama") or ""
                    gonderici = row.get("gonderici") or row.get("gönderici") or row.get("gonderen") or ""
                    tutar_str = row.get("tutar") or row.get("amount") or "0"
                    try:
                        tutar = float(str(tutar_str).replace(",", "."))
                    except ValueError:
                        tutar = 0
                    tip = (row.get("tip") or "gelen").strip().lower()
                    if tip not in ("gelen", "giden", "transfer"):
                        tip = "giden" if tutar < 0 else "gelen"
                    if tutar < 0:
                        tip = "giden"
                    satirlar.append({
                        "tarih": tarih,
                        "aciklama": aciklama,
                        "gonderici": gonderici,
                        "tutar": abs(tutar) if tip == "giden" else tutar,
                        "tip": tip,
                    })
        else:
            return jsonify({"ok": False, "mesaj": "JSON veya dosya gönderin"}), 400

        if not banka_hesap_id:
            return jsonify({"ok": False, "mesaj": "Banka hesabı seçin"}), 400
        if not satirlar:
            return jsonify({"ok": False, "mesaj": "En az bir satır gerekli"}), 400

        eklenen = 0
        for s in satirlar:
            t = _parse_date(s.get("tarih"))
            if not t:
                continue
            aciklama = (s.get("aciklama") or "").strip()[:500]
            gonderici = (s.get("gonderici") or "").strip()[:200]
            try:
                tutar = float(str(s.get("tutar", 0)).replace(",", "."))
            except (TypeError, ValueError):
                tutar = 0
            tip = (s.get("tip") or "gelen").strip().lower()
            if tip not in ("gelen", "giden", "transfer"):
                tip = "giden" if tutar < 0 else "gelen"
            if tutar < 0:
                tutar = abs(tutar)
                tip = "giden"
            execute(
                """INSERT INTO banka_hareketleri (banka_hesap_id, hareket_tarihi, aciklama, gonderici, tutar, tip, durum)
                   VALUES (%s, %s, %s, %s, %s, %s, 'bekleyen')""",
                (banka_hesap_id, t, aciklama, gonderici, tutar, tip),
            )
            eklenen += 1
        return jsonify({"ok": True, "eklenen": eklenen})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/oto_eslestir", methods=["POST"])
@giris_gerekli
def api_oto_eslestir():
    """Bekleyen hareketleri müşteri adına göre otomatik eşleştir (gonderici/aciklama ile name benzerliği)."""
    try:
        data = request.json or {}
        hesap_id = data.get("hesap_id")
        sql = """
        SELECT h.id, h.gonderici, h.aciklama, h.tutar, h.hareket_tarihi
        FROM banka_hareketleri h
        WHERE h.durum = 'bekleyen' AND h.tutar > 0
        """
        params = []
        if hesap_id:
            sql += " AND h.banka_hesap_id = %s"
            params.append(hesap_id)
        rows = fetch_all(sql, tuple(params) if params else None)
        musteriler = fetch_all("SELECT id, name FROM customers ORDER BY name")
        eslesti = 0
        for h in rows or []:
            g = (h.get("gonderici") or "").strip().upper()
            a = (h.get("aciklama") or "").strip().upper()
            for m in musteriler or []:
                ad = (m.get("name") or "").strip().upper()
                if not ad:
                    continue
                if ad in g or ad in a or g in ad or a in ad:
                    # Eşleştir: tahsilat oluştur, hareketi güncelle
                    tarih = h.get("hareket_tarihi") or date.today()
                    if hasattr(tarih, "isoformat"):
                        tarih = tarih
                    else:
                        tarih = _parse_date(str(tarih)[:10]) or date.today()
                    row = execute_returning(
                        """INSERT INTO tahsilatlar (musteri_id, tutar, odeme_turu, aciklama, tahsilat_tarihi)
                           VALUES (%s, %s, 'banka', %s, %s) RETURNING id""",
                        (m["id"], float(h.get("tutar") or 0), "Banka eşleşme: " + (h.get("aciklama") or "")[:100], tarih),
                    )
                    if row:
                        execute(
                            "UPDATE banka_hareketleri SET durum = 'eslesti', musteri_id = %s, tahsilat_id = %s WHERE id = %s",
                            (m["id"], row["id"], h["id"]),
                        )
                        eslesti += 1
                    break
        return jsonify({"ok": True, "eslesti": eslesti})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/eslestir", methods=["POST"])
@giris_gerekli
def api_eslestir():
    """Seçili hareketi müşteriye eşleştir ve tahsilat oluştur."""
    try:
        data = request.json or request.form
        hareket_id = data.get("hareket_id")
        musteri_id = data.get("musteri_id")
        if not hareket_id or not musteri_id:
            return jsonify({"ok": False, "mesaj": "Hareket ve müşteri seçin"}), 400
        hareket_id = int(hareket_id)
        musteri_id = int(musteri_id)
        h = fetch_one("SELECT id, tutar, hareket_tarihi, aciklama, durum FROM banka_hareketleri WHERE id = %s", (hareket_id,))
        if not h:
            return jsonify({"ok": False, "mesaj": "Hareket bulunamadı"}), 404
        if h.get("durum") == "eslesti":
            return jsonify({"ok": False, "mesaj": "Bu hareket zaten eşleşmiş"}), 400
        tutar = float(h.get("tutar") or 0)
        if tutar <= 0:
            return jsonify({"ok": False, "mesaj": "Sadece gelen ödemeler eşleştirilebilir"}), 400
        tarih = h.get("hareket_tarihi") or date.today()
        if hasattr(tarih, "isoformat"):
            pass
        else:
            tarih = _parse_date(str(tarih)[:10]) or date.today()
        aciklama = "Banka eşleşme: " + (h.get("aciklama") or "")[:100]
        row = execute_returning(
            """INSERT INTO tahsilatlar (musteri_id, tutar, odeme_turu, aciklama, tahsilat_tarihi)
               VALUES (%s, %s, 'banka', %s, %s) RETURNING id""",
            (musteri_id, tutar, aciklama, tarih),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "Tahsilat kaydı oluşturulamadı"}), 500
        execute(
            "UPDATE banka_hareketleri SET durum = 'eslesti', musteri_id = %s, tahsilat_id = %s WHERE id = %s",
            (musteri_id, row["id"], hareket_id),
        )
        return jsonify({"ok": True, "tahsilat_id": row["id"]})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/eslestirme_iptal", methods=["POST"])
@giris_gerekli
def api_eslestirme_iptal():
    """Eşleşmeyi iptal et: tahsilatı sil, hareketi bekleyen yap."""
    try:
        data = request.json or request.form
        hareket_id = data.get("hareket_id")
        if not hareket_id:
            return jsonify({"ok": False, "mesaj": "Hareket seçin"}), 400
        hareket_id = int(hareket_id)
        h = fetch_one("SELECT id, tahsilat_id, durum FROM banka_hareketleri WHERE id = %s", (hareket_id,))
        if not h:
            return jsonify({"ok": False, "mesaj": "Hareket bulunamadı"}), 404
        if h.get("durum") != "eslesti":
            return jsonify({"ok": False, "mesaj": "Sadece eşleşmiş hareket iptal edilebilir"}), 400
        tid = h.get("tahsilat_id")
        if tid:
            execute("DELETE FROM tahsilatlar WHERE id = %s", (tid,))
        execute(
            "UPDATE banka_hareketleri SET durum = 'bekleyen', musteri_id = NULL, tahsilat_id = NULL WHERE id = %s",
            (hareket_id,),
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/masraflar")
@giris_gerekli
def api_masraflar():
    """Toplam masraflar (giden hareketler). Filtre: hesap_id, yil, ay."""
    hesap_id = request.args.get("hesap_id")
    yil = request.args.get("yil")
    ay = request.args.get("ay")
    sql = """
    SELECT COALESCE(SUM(ABS(h.tutar)), 0) as toplam, COUNT(*) as adet
    FROM banka_hareketleri h
    WHERE (h.tutar < 0 OR h.tip = 'giden')
    """
    params = []
    if hesap_id:
        sql += " AND h.banka_hesap_id = %s"
        params.append(hesap_id)
    if yil:
        try:
            y = int(yil)
            sql += " AND EXTRACT(YEAR FROM h.hareket_tarihi) = %s"
            params.append(y)
        except ValueError:
            pass
    if ay:
        try:
            a = int(ay)
            sql += " AND EXTRACT(MONTH FROM h.hareket_tarihi) = %s"
            params.append(a)
        except ValueError:
            pass
    r = fetch_one(sql, tuple(params) if params else None)
    return jsonify({
        "toplam": float((r or {}).get("toplam") or 0),
        "adet": int((r or {}).get("adet") or 0),
    })


@bp.route("/api/masraflar_raporu")
@giris_gerekli
def api_masraflar_raporu():
    """Hesap ve/veya dönem bazlı masraf raporu (satır listesi + toplam)."""
    hesap_id = request.args.get("hesap_id")
    yil = request.args.get("yil")
    ay = request.args.get("ay")
    sql = """
    SELECT h.id, h.hareket_tarihi, h.aciklama, h.gonderici, h.tutar, h.tip, b.banka_adi, b.hesap_adi
    FROM banka_hareketleri h
    LEFT JOIN banka_hesaplar b ON b.id = h.banka_hesap_id
    WHERE (h.tutar < 0 OR h.tip = 'giden')
    """
    params = []
    if hesap_id:
        sql += " AND h.banka_hesap_id = %s"
        params.append(hesap_id)
    if yil:
        try:
            sql += " AND EXTRACT(YEAR FROM h.hareket_tarihi) = %s"
            params.append(int(yil))
        except ValueError:
            pass
    if ay:
        try:
            sql += " AND EXTRACT(MONTH FROM h.hareket_tarihi) = %s"
            params.append(int(ay))
        except ValueError:
            pass
    sql += " ORDER BY h.hareket_tarihi DESC, h.id DESC LIMIT 1000"
    rows = fetch_all(sql, tuple(params) if params else None)
    toplam = sum(float(r.get("tutar") or 0) for r in (rows or []))
    for r in rows or []:
        if r.get("hareket_tarihi"):
            r["hareket_tarihi"] = r["hareket_tarihi"].isoformat()[:10] if hasattr(r["hareket_tarihi"], "isoformat") else str(r["hareket_tarihi"])[:10]
    return jsonify({
        "toplam": abs(toplam),
        "adet": len(rows or []),
        "satirlar": rows or [],
    })


@bp.route("/api/hesaplar_tumu")
@giris_gerekli
@admin_gerekli
def api_hesaplar_tumu():
    """Tüm banka hesapları (admin, pasif dahil)."""
    rows = fetch_all(
        "SELECT id, banka_adi, hesap_adi, hesap_no, iban, is_active FROM banka_hesaplar ORDER BY banka_adi, id"
    )
    for r in rows or []:
        r["is_active"] = bool(r.get("is_active"))
    return jsonify(rows or [])


@bp.route("/api/hesap/kaydet", methods=["POST"])
@giris_gerekli
@admin_gerekli
def api_hesap_kaydet():
    """Banka hesabı ekle veya güncelle (admin)."""
    try:
        data = request.json or request.form
        pid = data.get("id") or data.get("hesap_id")
        banka_adi = (data.get("banka_adi") or "").strip()
        if not banka_adi:
            return jsonify({"ok": False, "mesaj": "Banka adı zorunlu"}), 400
        hesap_adi = (data.get("hesap_adi") or "").strip()
        hesap_no = (data.get("hesap_no") or "").strip()
        iban = (data.get("iban") or "").strip()
        is_active = data.get("is_active") not in (False, 0, "0", "false")
        if pid:
            execute(
                """UPDATE banka_hesaplar SET banka_adi=%s, hesap_adi=%s, hesap_no=%s, iban=%s, is_active=%s WHERE id=%s""",
                (banka_adi, hesap_adi or None, hesap_no or None, iban or None, is_active, int(pid)),
            )
            return jsonify({"ok": True, "id": int(pid)})
        row = execute_returning(
            """INSERT INTO banka_hesaplar (banka_adi, hesap_adi, hesap_no, iban, is_active)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (banka_adi, hesap_adi or None, hesap_no or None, iban or None, is_active),
        )
        return jsonify({"ok": True, "id": row["id"]})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
