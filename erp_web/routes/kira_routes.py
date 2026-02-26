from flask import Blueprint, render_template, request, jsonify, send_file
from auth import yetki_gerekli
from db import fetch_all, fetch_one, execute
import sys
import os
import tempfile

# Proje kökü (kira_senaryo.py burada)
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "../..")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
         "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

bp = Blueprint("kira", __name__)


def _tufe_yukle():
    """DB'den TÜFE verilerini yükle ve kira_senaryo.AYLIK_YILLIK'e yaz."""
    try:
        from kira_senaryo import AYLIK_YILLIK
        tufe_rows = fetch_all("SELECT year, month, oran FROM tufe_verileri")
        for r in tufe_rows:
            if r.get("month") in AYLAR:
                ay_idx = AYLAR.index(r["month"]) + 1
                AYLIK_YILLIK[(r["year"], ay_idx)] = float(r.get("oran") or 0)
    except Exception:
        pass


@bp.route("/")
@yetki_gerekli("kira_senaryo")
def index():
    musteriler = fetch_all("SELECT id, name FROM customers ORDER BY name")
    return render_template("kira/index.html", musteriler=musteriler)


@bp.route("/hesapla", methods=["POST"])
@yetki_gerekli("kira_senaryo")
def hesapla():
    """TÜFE bazlı kira hesaplama API."""
    data = request.get_json() or {}
    try:
        baslangic = float(data.get("baslangic", 0))
        gun = int(data.get("gun", 1))
        ay = int(data.get("ay", 1))
        yil = int(data.get("yil", 2020))
        assert baslangic > 0
    except (TypeError, ValueError, AssertionError):
        return jsonify({"hata": "Geçersiz parametre"}), 400

    try:
        from kira_senaryo import hesapla as _hesapla
        _tufe_yukle()
        sonuc = _hesapla(baslangic, gun, ay, yil)
        return jsonify(sonuc)
    except ImportError:
        return jsonify({"hata": "Kira senaryo modülü bulunamadı"}), 500


@bp.route("/excel")
@yetki_gerekli("kira_senaryo")
def excel_cikti():
    """Hesaplama sonucunu Excel olarak indir."""
    try:
        baslangic = float(request.args.get("baslangic", 0))
        gun = int(request.args.get("gun", 1))
        ay = int(request.args.get("ay", 1))
        yil = int(request.args.get("yil", 2020))
        musteri_adi = (request.args.get("musteri_adi") or "Musteri").strip()[:80]
        assert baslangic > 0
    except (TypeError, ValueError, AssertionError):
        return jsonify({"hata": "Geçersiz parametre"}), 400

    path = None
    try:
        from kira_senaryo import hesapla as _hesapla, excel_olustur
        _tufe_yukle()
        sonuc = _hesapla(baslangic, gun, ay, yil)
        if sonuc.get("hata"):
            return jsonify({"hata": sonuc["hata"]}), 400
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        excel_olustur(sonuc, musteri_adi, path)
        return send_file(
            path,
            as_attachment=True,
            download_name=f"KiraArtis_{musteri_adi.replace(' ', '_')[:40]}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except ImportError:
        return jsonify({"hata": "Excel modülü (openpyxl/kira_senaryo) bulunamadı"}), 500
    except Exception as e:
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except Exception:
                pass
        return jsonify({"hata": str(e)}), 500
