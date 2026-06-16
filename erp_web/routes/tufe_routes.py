from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from auth import yetki_gerekli
from db import fetch_all, fetch_one, execute
from datetime import date

bp = Blueprint("tufe", __name__)

AYLAR = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
          "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]


def _parse_secili_yillar():
    """URL: yillar=... (boş = hiç yıl yok), yil= (tek), veya yok + sim_ay → []."""
    bugun = date.today()
    args = request.args
    if "yillar" in args:
        raw = (args.get("yillar") or "").strip()
        if not raw:
            return []
        out = []
        for p in raw.split(","):
            p = p.strip()
            if p.isdigit():
                y = int(p)
                if 2000 <= y <= 2100:
                    out.append(y)
        return sorted(set(out))
    y1 = args.get("yil")
    if y1 and str(y1).strip().isdigit():
        y = int(y1)
        if 2000 <= y <= 2100:
            return [y]
    sim_a = (args.get("sim_ay") or "").strip()
    if sim_a in AYLAR:
        return []
    return [bugun.year]


@bp.route("/")
@yetki_gerekli("tufe")
def index():
    bugun = date.today()
    secili_yillar = _parse_secili_yillar()
    veri_yillar = {}
    for y in secili_yillar:
        rows = fetch_all(
            "SELECT month, oran FROM tufe_verileri WHERE year=%s ORDER BY month",
            (y,),
        )
        veri_yillar[y] = {r["month"]: float(r["oran"]) for r in (rows or [])}
    checkbox_yillar = list(range(2000, bugun.year + 2))
    yillar_query = ",".join(str(y) for y in secili_yillar)

    sim_ay_arg = (request.args.get("sim_ay") or "").strip()
    sim_tutar_raw = (request.args.get("sim_tutar") or "").strip().replace(",", ".")
    sim_ay_secili = sim_ay_arg if sim_ay_arg in AYLAR else ""
    sim_tutar_str = ""
    sim_modu = False
    if sim_ay_arg in AYLAR and sim_tutar_raw and secili_yillar:
        try:
            st = float(sim_tutar_raw)
            if st > 0:
                sim_modu = True
                sim_tutar_str = sim_tutar_raw
        except ValueError:
            pass

    ay_oran_satirlar = None
    tufe_hide_grids = bool(sim_modu)
    if sim_ay_arg in AYLAR and not sim_modu:
        tufe_hide_grids = True
        if not secili_yillar:
            rows = fetch_all(
                "SELECT year, oran FROM tufe_verileri WHERE month=%s ORDER BY year ASC",
                (sim_ay_arg,),
            )
            ay_oran_satirlar = [
                {"yil": int(r["year"]), "oran": float(r["oran"])} for r in (rows or [])
            ]
        else:
            yl = list(secili_yillar)
            ph = ",".join(["%s"] * len(yl))
            rows = fetch_all(
                f"SELECT year, oran FROM tufe_verileri WHERE month=%s AND year IN ({ph}) ORDER BY year ASC",
                [sim_ay_arg] + yl,
            )
            ay_oran_satirlar = [
                {"yil": int(r["year"]), "oran": float(r["oran"])} for r in (rows or [])
            ]

    return render_template(
        "tufe/index.html",
        secili_yillar=secili_yillar,
        veri_yillar=veri_yillar,
        aylar=AYLAR,
        checkbox_yillar=checkbox_yillar,
        yillar_query=yillar_query,
        bugun_yil=bugun.year,
        bugun_ay=bugun.month,
        sim_modu=sim_modu,
        sim_tutar_str=sim_tutar_str,
        sim_ay_secili=sim_ay_secili,
        ay_oran_satirlar=ay_oran_satirlar,
        tufe_hide_grids=tufe_hide_grids,
    )


@bp.route("/kaydet", methods=["POST"])
@yetki_gerekli("tufe")
def kaydet():
    bugun = date.today()
    yillar = sorted(
        {
            int(x)
            for x in request.form.getlist("yillar_secili")
            if str(x).strip().isdigit() and 2000 <= int(x) <= 2100
        }
    )
    if not yillar:
        flash("Kayıt için en az bir yıl seçili olmalı.", "error")
        return redirect(url_for("tufe.index"))
    kaydedilen = 0
    for yil in yillar:
        for i, ay_adi in enumerate(AYLAR, 1):
            if yil == bugun.year and i > bugun.month:
                continue
            if yil > bugun.year:
                continue
            val = request.form.get(f"y{yil}_ay_{i}", "").strip().replace(",", ".")
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
            except Exception:
                pass
    flash(
        f"✓ {len(yillar)} yıl için toplam {kaydedilen} ay kaydedildi.",
        "success",
    )
    return redirect(
        url_for("tufe.index", yillar=",".join(str(y) for y in yillar))
    )


@bp.route("/sil", methods=["POST"])
@yetki_gerekli("tufe")
def sil():
    yillar = sorted(
        {
            int(x)
            for x in request.form.getlist("yillar_secili")
            if str(x).strip().isdigit() and 2000 <= int(x) <= 2100
        }
    )
    if not yillar:
        flash("Silinecek yıl bulunamadı.", "error")
        return redirect(url_for("tufe.index"))
    silinen = 0
    for yil in yillar:
        for i, ay_adi in enumerate(AYLAR, 1):
            val = request.form.get(f"y{yil}_ay_{i}", "").strip()
            if not val:
                continue
            execute(
                "DELETE FROM tufe_verileri WHERE year=%s AND month=%s",
                (yil, ay_adi),
            )
            silinen += 1
    flash(f"{silinen} ay kaydı silindi.", "info")
    return redirect(
        url_for("tufe.index", yillar=",".join(str(y) for y in yillar))
    )


@bp.route("/api/ay-ozeti")
@yetki_gerekli("tufe")
def api_ay_ozeti():
    """Tek bir ay adı için tüm yıllardaki TÜFE oranları (kronolojik)."""
    ay = (request.args.get("ay") or "").strip()
    if ay not in AYLAR:
        return jsonify({"ok": False, "hata": "Geçersiz ay"}), 400
    rows = fetch_all(
        "SELECT year, oran FROM tufe_verileri WHERE month=%s ORDER BY year ASC",
        (ay,),
    )
    satirlar = [
        {"yil": int(r["year"]), "oran": float(r["oran"])} for r in (rows or [])
    ]
    return jsonify({"ok": True, "ay": ay, "satirlar": satirlar})


@bp.route("/api/yillar-ay-oranlari")
@yetki_gerekli("tufe")
def api_yillar_ay_oranlari():
    """Seçilen yıllar + ay için veritabanındaki TÜFE oranları (simülasyon)."""
    ay = (request.args.get("ay") or "").strip()
    if ay not in AYLAR:
        return jsonify({"ok": False, "hata": "Geçersiz ay"}), 400
    raw = (request.args.get("yillar") or "").strip()
    yillar = []
    for p in raw.split(","):
        p = p.strip()
        if p.isdigit():
            yi = int(p)
            if 2000 <= yi <= 2100:
                yillar.append(yi)
    yillar = sorted(set(yillar))
    if not yillar:
        return jsonify({"ok": False, "hata": "En az bir yıl girin"}), 400
    oranlar = {}
    for y in yillar:
        r = fetch_one(
            "SELECT oran FROM tufe_verileri WHERE year=%s AND month=%s",
            (y, ay),
        )
        oranlar[str(y)] = float(r["oran"]) if r else None
    return jsonify({"ok": True, "ay": ay, "yillar": yillar, "oranlar": oranlar})


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
