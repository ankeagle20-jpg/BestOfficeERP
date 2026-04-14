"""
Bankalar: hesap dökümü, ekstre yükleme, tahsilat eşleştirme, masraf takibi.
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from auth import giris_gerekli, admin_gerekli
from db import fetch_all, fetch_one, execute, execute_returning, db
from services.banka_ak_import import (
    akbank_sender_key,
    dataframe_hareket_satirlari,
    ham_tahsilatta_olanlari_cikar,
    onizleme_satirlari,
    read_akbank_excel,
)
try:
    # Yeni sürüm: giriş/müşteri kartı ile aynı geniş arama.
    from utils.musteri_arama import customers_arama_sql_giris_genis, customers_arama_params_giris_genis
except ImportError:
    # Geriye uyumluluk: eski deploylarda sadece dar arama yardımcıları olabilir.
    from utils.musteri_arama import customers_arama_sql_3, customers_arama_params_4

    def customers_arama_sql_giris_genis(table_alias: str = "") -> str:
        return customers_arama_sql_3(table_alias)

    def customers_arama_params_giris_genis(q: str):
        return customers_arama_params_4(q)
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


@bp.route("/yeni")
@giris_gerekli
def yeni():
    """Yeni hesap sayfasına yönlendir (aynı index, form temiz)."""
    return redirect(url_for("banka.index"))


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
    w3 = customers_arama_sql_giris_genis("")
    rows = fetch_all(
        f"SELECT id, name, musteri_adi FROM customers WHERE {w3} ORDER BY name LIMIT 30",
        customers_arama_params_giris_genis(q),
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
        musteriler = fetch_all("SELECT id, name, musteri_adi FROM customers ORDER BY name")
        eslesti = 0
        for h in rows or []:
            g = (h.get("gonderici") or "").strip().upper()
            a = (h.get("aciklama") or "").strip().upper()
            for m in musteriler or []:
                ad = (m.get("name") or "").strip().upper()
                kisa = (m.get("musteri_adi") or "").strip().upper()
                esles = False
                if ad and (ad in g or ad in a or g in ad or a in ad):
                    esles = True
                elif kisa and (kisa in g or kisa in a or g in kisa or a in kisa):
                    esles = True
                if not esles:
                    continue
                # Eşleştir: tahsilat oluştur, hareketi güncelle
                    tarih = h.get("hareket_tarihi") or date.today()
                    if hasattr(tarih, "isoformat"):
                        tarih = tarih
                    else:
                        tarih = _parse_date(str(tarih)[:10]) or date.today()
                    row = execute_returning(
                        """INSERT INTO tahsilatlar (customer_id, tutar, odeme_turu, aciklama, tahsilat_tarihi)
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
            """INSERT INTO tahsilatlar (customer_id, tutar, odeme_turu, aciklama, tahsilat_tarihi)
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


def _ensure_tahsilat_banka_referans_no():
    try:
        execute("ALTER TABLE tahsilatlar ADD COLUMN IF NOT EXISTS banka_referans_no TEXT")
    except Exception:
        pass


def _ensure_akbank_import_dosyalar():
    """Yüklenen Akbank Excel dosyalarını ERP içinde saklar."""
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS akbank_import_dosyalar (
                id SERIAL PRIMARY KEY,
                ad_gosterim TEXT NOT NULL UNIQUE,
                orijinal_filename TEXT,
                yuklenme_tarihi TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                excel_binary BYTEA NOT NULL
            )
            """
        )
    except Exception:
        pass
    try:
        execute(
            "CREATE INDEX IF NOT EXISTS ix_akbank_import_dosyalar_yuklenme ON akbank_import_dosyalar (yuklenme_tarihi DESC)"
        )
    except Exception:
        pass


def _akbank_allocate_ad_gosterim(d: date | None = None) -> str:
    """Gün bazlı kısa ad: DDMMYY; aynı gün tekrarı → _2, _3 …"""
    d = d or date.today()
    base = f"{d.day:02d}{d.month:02d}{str(d.year)[-2:]}"
    for i in range(0, 200):
        cand = base if i == 0 else f"{base}_{i + 1}"
        if not fetch_one("SELECT 1 FROM akbank_import_dosyalar WHERE ad_gosterim = %s LIMIT 1", (cand,)):
            return cand
    return f"{base}_{int(datetime.now().timestamp())}"


def _bytea_to_bytes(val) -> bytes:
    if val is None:
        return b""
    if isinstance(val, memoryview):
        return val.tobytes()
    if isinstance(val, bytes):
        return val
    return bytes(val)


def _musteriler_akbank_listesi():
    try:
        return fetch_all(
            """
            SELECT c.id,
                   c.name,
                   COALESCE(c.musteri_adi, '') AS musteri_adi,
                   COALESCE(c.tax_number, '') AS tax_number,
                   COALESCE(
                       NULLIF(TRIM(k.sirket_unvani), ''),
                       NULLIF(TRIM(k.unvan), ''),
                       ''
                   ) AS sirket_unvani,
                   COALESCE(NULLIF(TRIM(k.vergi_no), ''), '') AS kyc_vergi_no,
                   COALESCE(NULLIF(TRIM(k.yetkili_tcno), ''), '') AS yetkili_tcno,
                   COALESCE(NULLIF(TRIM(k.yetkili_adsoyad), ''), '') AS yetkili_adsoyad
            FROM customers c
            LEFT JOIN LATERAL (
                SELECT sirket_unvani, unvan, vergi_no, yetkili_tcno, yetkili_adsoyad
                FROM musteri_kyc
                WHERE musteri_id = c.id
                ORDER BY id DESC NULLS LAST
                LIMIT 1
            ) k ON TRUE
            ORDER BY COALESCE(c.name, '')
            """
        ) or []
    except Exception:
        return fetch_all(
            """SELECT id, name, COALESCE(musteri_adi, '') AS musteri_adi,
                      COALESCE(tax_number, '') AS tax_number,
                      '' AS sirket_unvani, '' AS kyc_vergi_no, '' AS yetkili_tcno, '' AS yetkili_adsoyad
               FROM customers ORDER BY COALESCE(name, '')"""
        ) or []


def _tahsilatta_refler_for_ham(ham: list) -> set[str]:
    refs = [str(r.get("banka_referans_no") or "").strip() for r in ham if r.get("banka_referans_no")]
    refs_u = [x for x in dict.fromkeys(refs) if x]
    if not refs_u:
        return set()
    rows = fetch_all(
        "SELECT banka_referans_no FROM tahsilatlar WHERE banka_referans_no IN %s",
        (tuple(refs_u),),
    )
    return {str(x["banka_referans_no"]) for x in (rows or []) if x.get("banka_referans_no")}


def _manual_map_for_ham(ham: list) -> dict[str, int]:
    keys_u = list(dict.fromkeys(akbank_sender_key(r.get("aciklama") or "") for r in ham))
    keys_u = [k for k in keys_u if k]
    manual_by_key: dict[str, int] = {}
    if not keys_u:
        return manual_by_key
    try:
        map_rows = fetch_all(
            "SELECT sender_key, musteri_id FROM akbank_dekont_musteri_map WHERE sender_key IN %s",
            (tuple(keys_u),),
        )
        for mr in map_rows or []:
            sk = str(mr.get("sender_key") or "")
            if not sk:
                continue
            try:
                manual_by_key[sk] = int(mr["musteri_id"])
            except (TypeError, ValueError, KeyError):
                continue
    except Exception:
        pass
    return manual_by_key


def _ham_birlestir_dedupe(ham_parcalar: list[list]) -> list:
    """Aynı fiş no tekrarını at (ilk kazanır)."""
    seen: set[str] = set()
    out: list = []
    for ham in ham_parcalar:
        for r in ham:
            ref = str(r.get("banka_referans_no") or "").strip()
            if ref:
                if ref in seen:
                    continue
                seen.add(ref)
            out.append(r)
    return out


def _json_akbank_analyze_ham(ham: list, ozet: dict, kayit_dosya: dict | None) -> dict:
    """Tahsilatta olan satırları çıkarır; kalan için önizleme (mükerrer satır yok)."""
    _ensure_tahsilat_banka_referans_no()
    _ensure_akbank_dekont_musteri_map()
    mevcut = _tahsilatta_refler_for_ham(ham)
    ham_goster, cikarilan = ham_tahsilatta_olanlari_cikar(ham, mevcut)
    ozet_out = dict(ozet)
    ozet_out["tahsilatta_gizlenen"] = cikarilan
    musteriler = _musteriler_akbank_listesi()
    manual_by_key = _manual_map_for_ham(ham_goster)
    satirlar = onizleme_satirlari(ham_goster, musteriler, set(), manual_by_key)
    out: dict = {"ok": True, "ozet": ozet_out, "satirlar": satirlar}
    if kayit_dosya:
        out["kayit_dosya"] = kayit_dosya
    return out


def _ensure_akbank_dekont_musteri_map():
    """Manuel seçilen dekont gönderici anahtarı → müşteri (sonraki Excel analizlerinde öncelik)."""
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS akbank_dekont_musteri_map (
                sender_key TEXT PRIMARY KEY,
                musteri_id INTEGER NOT NULL,
                ornek_aciklama TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    except Exception:
        pass
    try:
        execute(
            "CREATE INDEX IF NOT EXISTS ix_akbank_dekont_map_musteri ON akbank_dekont_musteri_map (musteri_id)"
        )
    except Exception:
        pass


@bp.route("/akbank-tahsilat-import", strict_slashes=False)
@giris_gerekli
def akbank_tahsilat_import_sayfa():
    """Akbank Excel → önizleme, müşteri eşleştirme, onaylı tahsilat kaydı."""
    return render_template("bankalar/akbank_tahsilat_import.html")


_AKBANK_IMPORT_MAX_BYTES = 20 * 1024 * 1024


def _iso_or_str(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    return str(v)


@bp.route("/api/akbank-tahsilat/analyze", methods=["POST"])
@giris_gerekli
def api_akbank_tahsilat_analyze():
    """Yeni Excel yükle: dosyayı ERP'ye kaydet + önizleme (cariye işlenmiş fişler listelenmez)."""
    f = request.files.get("file")
    if not f or not getattr(f, "filename", ""):
        return jsonify({"ok": False, "mesaj": "Excel dosyası seçin (.xlsx)."}), 400
    if not str(f.filename).lower().endswith((".xlsx", ".xls")):
        return jsonify({"ok": False, "mesaj": "Yalnızca .xlsx / .xls desteklenir."}), 400
    raw = f.read()
    if len(raw) > _AKBANK_IMPORT_MAX_BYTES:
        return jsonify({"ok": False, "mesaj": "Dosya çok büyük (en fazla 20 MB)."}), 400
    try:
        df = read_akbank_excel(raw)
        ham, ozet = dataframe_hareket_satirlari(df)
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "mesaj": f"Excel okunamadı: {e}"}), 400

    _ensure_tahsilat_banka_referans_no()
    _ensure_akbank_import_dosyalar()
    _ensure_akbank_dekont_musteri_map()

    ad = _akbank_allocate_ad_gosterim()
    fname = (getattr(f, "filename", None) or "")[:500]
    try:
        ins = execute_returning(
            """
            INSERT INTO akbank_import_dosyalar (ad_gosterim, orijinal_filename, excel_binary)
            VALUES (%s, %s, %s)
            RETURNING id, ad_gosterim, yuklenme_tarihi
            """,
            (ad, fname or None, raw),
        )
    except Exception as e:
        return jsonify({"ok": False, "mesaj": f"Dosya kaydedilemedi: {e}"}), 400

    if not ins:
        return jsonify({"ok": False, "mesaj": "Dosya kaydı dönmedi."}), 400

    kayit = {
        "id": ins["id"],
        "ad_gosterim": ins["ad_gosterim"],
        "yuklenme_tarihi": _iso_or_str(ins.get("yuklenme_tarihi")),
        "orijinal_filename": fname,
    }
    out = _json_akbank_analyze_ham(ham, ozet, kayit)
    return jsonify(out)


@bp.route("/api/akbank-tahsilat/dosyalar", methods=["GET"])
@giris_gerekli
def api_akbank_tahsilat_dosyalar():
    """Kayıtlı Excel listesi (içerik dönmez)."""
    _ensure_akbank_import_dosyalar()
    rows = fetch_all(
        """
        SELECT id, ad_gosterim, orijinal_filename, yuklenme_tarihi,
               octet_length(excel_binary) AS boyut_octet
        FROM akbank_import_dosyalar
        ORDER BY yuklenme_tarihi DESC
        LIMIT 300
        """
    ) or []
    out = []
    for r in rows:
        d = dict(r)
        d["yuklenme_tarihi"] = _iso_or_str(d.get("yuklenme_tarihi"))
        out.append(d)
    return jsonify({"ok": True, "dosyalar": out})


@bp.route("/api/akbank-tahsilat/analyze-kayitli", methods=["POST"])
@giris_gerekli
def api_akbank_tahsilat_analyze_kayitli():
    """Bir veya birden fazla kayıtlı dosyayı aç; içerik birleştirilir (aynı fiş no tekil)."""
    data = request.get_json(silent=True) or {}
    ids = data.get("file_ids")
    if not isinstance(ids, list) or not ids:
        return jsonify({"ok": False, "mesaj": "En az bir kayıtlı dosya seçin."}), 400
    clean_ids: list[int] = []
    for x in ids:
        try:
            clean_ids.append(int(x))
        except (TypeError, ValueError):
            continue
    clean_ids = list(dict.fromkeys(clean_ids))
    if not clean_ids:
        return jsonify({"ok": False, "mesaj": "Geçersiz dosya seçimi."}), 400

    _ensure_akbank_import_dosyalar()
    rows = fetch_all(
        "SELECT id, ad_gosterim, excel_binary FROM akbank_import_dosyalar WHERE id IN %s",
        (tuple(clean_ids),),
    ) or []
    found = {int(r["id"]): r for r in rows}
    if len(found) != len(clean_ids):
        return jsonify({"ok": False, "mesaj": "Bazı dosyalar bulunamadı."}), 400

    hams: list = []
    ozet_top = {"excel_satir": 0, "a_degil": 0, "ref_bos": 0, "tutar_sifir": 0, "tarih_yok": 0, "islenen": 0}
    meta_list: list[dict] = []
    for fid in clean_ids:
        row = found[fid]
        raw = _bytea_to_bytes(row.get("excel_binary"))
        try:
            df = read_akbank_excel(raw)
            ham, ozet = dataframe_hareket_satirlari(df)
        except Exception as e:
            return jsonify({"ok": False, "mesaj": f"Dosya okunamadı ({row.get('ad_gosterim')}): {e}"}), 400
        hams.append(ham)
        for k in ozet_top:
            ozet_top[k] = ozet_top.get(k, 0) + int(ozet.get(k, 0) or 0)
        meta_list.append({"id": row["id"], "ad_gosterim": row["ad_gosterim"]})

    ham_birlesik = _ham_birlestir_dedupe(hams)
    out = _json_akbank_analyze_ham(ham_birlesik, ozet_top, {"kayitli_dosyalar": meta_list})
    return jsonify(out)


@bp.route("/api/akbank-tahsilat/musteriler")
@giris_gerekli
def api_akbank_tahsilat_musteriler():
    """Manuel seçim için arama — giriş / müşteri kartı ile aynı geniş alan araması (min 2 karakter)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    w3 = customers_arama_sql_giris_genis("c")
    rows = fetch_all(
        f"""SELECT c.id, c.name, COALESCE(c.musteri_adi, '') AS musteri_adi,
                   COALESCE(NULLIF(TRIM(k.sirket_unvani), ''), NULLIF(TRIM(k.unvan), ''), '') AS sirket_unvani
            FROM customers c
            LEFT JOIN LATERAL (
                SELECT sirket_unvani, unvan FROM musteri_kyc WHERE musteri_id = c.id ORDER BY id DESC NULLS LAST LIMIT 1
            ) k ON TRUE
            WHERE {w3}
            ORDER BY c.name NULLS LAST
            LIMIT 50""",
        customers_arama_params_giris_genis(q),
    )
    return jsonify(rows or [])


@bp.route("/api/akbank-tahsilat/gonderici-kaydet", methods=["POST"])
@giris_gerekli
def api_akbank_tahsilat_gonderici_kaydet():
    """Manuel müşteri seçimini tahsilat kaydı olmadan kalıcılaştırır (sonraki Excel analizinde önerilir)."""
    data = request.get_json(silent=True) or {}
    aciklama = (data.get("aciklama") or "").strip()
    try:
        mid = int(data.get("musteri_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "Geçersiz müşteri."}), 400
    if not aciklama:
        return jsonify({"ok": False, "mesaj": "Açıklama gerekli."}), 400
    sk = akbank_sender_key(aciklama)
    if not sk:
        return jsonify({"ok": False, "mesaj": "Bu açıklamadan gönderici anahtarı çıkarılamadı."}), 400
    if not fetch_one("SELECT id FROM customers WHERE id = %s LIMIT 1", (mid,)):
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 400
    _ensure_akbank_dekont_musteri_map()
    try:
        execute(
            """
            INSERT INTO akbank_dekont_musteri_map (sender_key, musteri_id, ornek_aciklama, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (sender_key) DO UPDATE SET
                musteri_id = EXCLUDED.musteri_id,
                ornek_aciklama = EXCLUDED.ornek_aciklama,
                updated_at = NOW()
            """,
            (sk, mid, aciklama[:2000]),
        )
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    return jsonify({"ok": True, "sender_key": sk})


@bp.route("/api/akbank-tahsilat/commit", methods=["POST"])
@giris_gerekli
def api_akbank_tahsilat_commit():
    """Aşama 2: Onaylanan satırları tahsilatlar tablosuna yazar."""
    data = request.get_json(silent=True) or {}
    items = data.get("satirlar")
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "mesaj": "Kaydedilecek satır yok."}), 400

    _ensure_tahsilat_banka_referans_no()
    _ensure_akbank_dekont_musteri_map()
    eklendi = 0
    atlandi = 0
    hatalar: list[str] = []

    with db() as conn:
        cur = conn.cursor()
        for it in items:
            if not it.get("onay"):
                continue
            ref = str(it.get("banka_referans_no") or "").strip()
            if not ref:
                atlandi += 1
                continue
            try:
                mid = int(it.get("musteri_id"))
            except (TypeError, ValueError):
                atlandi += 1
                hatalar.append(f"Ref {ref}: geçersiz müşteri.")
                continue
            try:
                tutar = float(it.get("tutar"))
            except (TypeError, ValueError):
                atlandi += 1
                continue
            if tutar <= 0:
                atlandi += 1
                continue
            aciklama = (it.get("aciklama") or "").strip() or "Banka tahsilat"
            tah_str = (it.get("tahsilat_tarihi") or it.get("tarih") or "")[:10]
            if len(tah_str) < 10:
                atlandi += 1
                continue
            cur.execute(
                "SELECT 1 FROM tahsilatlar WHERE banka_referans_no = %s LIMIT 1",
                (ref,),
            )
            if cur.fetchone():
                atlandi += 1
                hatalar.append(f"Ref {ref}: mükerrer (atlandı).")
                continue
            cur.execute(
                "SELECT id FROM customers WHERE id = %s LIMIT 1",
                (mid,),
            )
            if not cur.fetchone():
                atlandi += 1
                hatalar.append(f"Ref {ref}: müşteri yok (id={mid}).")
                continue
            cur.execute(
                """INSERT INTO tahsilatlar (
                    musteri_id, customer_id, fatura_id, tutar, odeme_turu,
                    aciklama, tahsilat_tarihi, makbuz_no, banka_referans_no
                ) VALUES (%s, NULL, NULL, %s, %s, %s, %s::date, NULL, %s)""",
                (mid, round(tutar, 2), "havale", aciklama, tah_str, ref),
            )
            eklendi += 1
            if it.get("manuel_musteri") and aciklama:
                sk = akbank_sender_key(aciklama)
                if sk:
                    cur.execute(
                        """
                        INSERT INTO akbank_dekont_musteri_map (sender_key, musteri_id, ornek_aciklama, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (sender_key) DO UPDATE SET
                            musteri_id = EXCLUDED.musteri_id,
                            ornek_aciklama = EXCLUDED.ornek_aciklama,
                            updated_at = NOW()
                        """,
                        (sk, mid, aciklama[:2000]),
                    )

    return jsonify({
        "ok": True,
        "eklendi": eklendi,
        "atlandi": atlandi,
        "uyarilar": hatalar[:30],
    })


@bp.route("/api/embed-prototype/status")
@giris_gerekli
def api_embed_prototype_status():
    """ONNX embedding prototipi: model yolu, yükleme durumu, hızlandırıcılar."""
    from pathlib import Path

    from services.embedding_onnx_minilm import embedding_model_dir, get_embedder
    from utils.compute_device import accelerator_summary

    d = embedding_model_dir()
    emb = get_embedder()
    return jsonify({
        "ok": True,
        "model_dir": str(d) if d else None,
        "default_model_path": str(
            Path(__file__).resolve().parent.parent / "models" / "multilingual-minilm-l12-onnx"
        ),
        "loaded": emb is not None,
        "accelerators": accelerator_summary(),
    })


@bp.route("/api/embed-prototype/score", methods=["POST"])
@giris_gerekli
def api_embed_prototype_score():
    """Deknot açıklamasına göre seçilen müşteri id’leri arasında kosinüs benzerliği sıralaması."""
    from services.embedding_akbank_prototype import musteri_embed_label
    from services.embedding_onnx_minilm import get_embedder

    emb = get_embedder()
    if emb is None:
        return jsonify({
            "ok": False,
            "mesaj": "ONNX model yüklü değil. scripts/export_multilingual_minilm_onnx.py ve EMBEDDING_ONNX_DIR.",
        }), 503
    data = request.get_json() or {}
    q = (data.get("aciklama") or data.get("query") or "").strip()
    mids_raw = data.get("musteri_ids") or []
    if not q or not mids_raw:
        return jsonify({"ok": False, "mesaj": "aciklama ve musteri_ids (dizi) gerekli."}), 400
    try:
        mids = [int(x) for x in mids_raw]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "musteri_ids sayı listesi olmalı."}), 400
    if not mids:
        return jsonify({"ok": False, "mesaj": "musteri_ids boş."}), 400
    try:
        topk = int(data.get("topk") or 10)
    except (TypeError, ValueError):
        topk = 10
    topk = max(1, min(topk, 50))

    rows = fetch_all(
        """
        SELECT c.id, c.name, c.musteri_adi,
               COALESCE(NULLIF(TRIM(k.sirket_unvani), ''), '') AS sirket_unvani,
               COALESCE(NULLIF(TRIM(k.yetkili_adsoyad), ''), '') AS yetkili_adsoyad
        FROM customers c
        LEFT JOIN LATERAL (
            SELECT sirket_unvani, yetkili_adsoyad
            FROM musteri_kyc
            WHERE musteri_id = c.id
            ORDER BY id DESC
            LIMIT 1
        ) k ON TRUE
        WHERE c.id = ANY(%s)
        """,
        (mids,),
    ) or []
    by_id = {int(r["id"]): r for r in rows if r.get("id") is not None}
    labels = []
    ids = []
    for mid in mids:
        r = by_id.get(mid)
        if r is None:
            continue
        labels.append(musteri_embed_label(r))
        ids.append(mid)
    if len(labels) < 1:
        return jsonify({"ok": False, "mesaj": "Geçerli müşteri kaydı bulunamadı."}), 400
    top = emb.rank_query(q, labels, ids, topk=topk)
    return jsonify({"ok": True, "top": top, "scored_count": len(labels)})
