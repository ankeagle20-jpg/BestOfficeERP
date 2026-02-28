from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from auth import yetki_gerekli
from db import fetch_all, execute
from datetime import date

bp = Blueprint("tufe", __name__)

AYLAR = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
          "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]


@bp.route("/")
@yetki_gerekli("tufe")
def index():
    yil = int(request.args.get("yil", date.today().year))
    rows = fetch_all(
        "SELECT month, oran FROM tufe_verileri WHERE year=%s ORDER BY month", (yil,)
    )
    data = {r["month"]: float(r["oran"]) for r in rows}
    bugun = date.today()
    return render_template("tufe/index.html",
                           yil=yil, data=data, aylar=AYLAR,
                           bugun_yil=bugun.year, bugun_ay=bugun.month)


@bp.route("/kaydet", methods=["POST"])
@yetki_gerekli("tufe")
def kaydet():
    yil = int(request.form.get("yil", date.today().year))
    bugun = date.today()
    kaydedilen = 0
    for i, ay_adi in enumerate(AYLAR, 1):
        # Gelecek ayları reddet
        if yil == bugun.year and i > bugun.month:
            continue
        if yil > bugun.year:
            continue
        val = request.form.get(f"ay_{i}", "").strip().replace(",", ".")
        if not val:
            continue
        try:
            oran = float(val)
            execute(
                """INSERT INTO tufe_verileri (year, month, oran)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (year,month) DO UPDATE SET oran=EXCLUDED.oran""",
                (yil, ay_adi, oran),
            )
            kaydedilen += 1
        except:
            pass
    flash(f"✓ {yil} yılı {kaydedilen} ay kaydedildi.", "success")
    return redirect(url_for("tufe.index", yil=yil))


@bp.route("/sil", methods=["POST"])
@yetki_gerekli("tufe")
def sil():
    yil = int(request.form.get("yil"))
    aylar = request.form.getlist("aylar")
    for ay_adi in aylar:
        execute("DELETE FROM tufe_verileri WHERE year=%s AND month=%s", (yil, ay_adi))
    flash(f"{len(aylar)} ay verisi silindi.", "info")
    return redirect(url_for("tufe.index", yil=yil))


@bp.route("/tcmb-cek")
@yetki_gerekli("tufe")
def tcmb_cek():
    """TCMB'den TÜFE verilerini çek ve DB'ye kaydet."""
    try:
        import urllib.request, re
        url = (
            "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/"
            "Main+Menu/Istatistikler/Enflasyon+Verileri/Tuketici+Fiyatlari"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=12).read().decode(
            "utf-8", "ignore"
        )
        pattern = r"\|\s*(\d{2})-(\d{4})\s*\|\s*([\d.]+)\s*\|"
        matches = re.findall(pattern, html)
        say = 0
        bugun = date.today()
        for ay_s, yil_s, oran_s in matches:
            ay = int(ay_s)
            yil = int(yil_s)
            oran = float(oran_s)
            if yil > bugun.year:
                continue
            if yil == bugun.year and ay > bugun.month:
                continue
            ay_adi = AYLAR[ay - 1]
            execute(
                """INSERT INTO tufe_verileri (year, month, oran)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (year,month) DO UPDATE SET oran=EXCLUDED.oran""",
                (yil, ay_adi, oran),
            )
            say += 1
        return jsonify({"ok": True, "guncellenen": say})
    except Exception as e:
        return jsonify({"ok": False, "hata": str(e)})
