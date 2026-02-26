# -*- coding: utf-8 -*-
"""
Personel Devam Takip, İzin Yönetimi, Personel Yönetimi
"""
from flask import Blueprint, render_template, request, jsonify
from auth import giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning
from datetime import date, datetime, timedelta

bp = Blueprint("personel", __name__)


def _parse_date(s):
    if not s:
        return None
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


@bp.route("/")
@giris_gerekli
def index():
    return render_template("personel/index.html")


# ── Personel listesi ────────────────────────────────────────────────────────

@bp.route("/api/list")
@giris_gerekli
def api_list():
    filtre = request.args.get("filtre", "aktif")  # tumu, aktif, pasif
    sql = "SELECT id, ad_soyad, pozisyon, telefon, email, giris_tarihi, mesai_baslangic, mac_adres, notlar, is_active FROM personel WHERE 1=1"
    params = []
    if filtre == "aktif":
        sql += " AND is_active = TRUE"
    elif filtre == "pasif":
        sql += " AND is_active = FALSE"
    sql += " ORDER BY ad_soyad"
    rows = fetch_all(sql, tuple(params))
    out = []
    for r in rows:
        d = dict(r)
        if d.get("giris_tarihi"):
            d["giris_tarihi"] = d["giris_tarihi"].isoformat()[:10] if hasattr(d["giris_tarihi"], "isoformat") else str(d["giris_tarihi"])[:10]
        out.append(d)
    return jsonify(out)


@bp.route("/api/personel/kaydet", methods=["POST"])
@giris_gerekli
def api_personel_kaydet():
    try:
        data = request.json or request.form
        ad_soyad = (data.get("ad_soyad") or "").strip()
        if not ad_soyad:
            return jsonify({"ok": False, "mesaj": "Ad soyad zorunlu"}), 400
        pozisyon = (data.get("pozisyon") or "").strip()
        telefon = (data.get("telefon") or "").strip()
        email = (data.get("email") or "").strip()
        giris_tarihi = _parse_date(data.get("giris_tarihi"))
        mesai_baslangic = (data.get("mesai_baslangic") or "09:00").strip()[:5]
        mac_adres = (data.get("mac_adres") or "").strip()
        notlar = (data.get("notlar") or "").strip()
        is_active = data.get("is_active") not in (False, 0, "0", "false")

        pid = data.get("id") or data.get("personel_id")
        if pid:
            execute(
                """UPDATE personel SET ad_soyad=%s, pozisyon=%s, telefon=%s, email=%s, giris_tarihi=%s, mesai_baslangic=%s, mac_adres=%s, notlar=%s, is_active=%s WHERE id=%s""",
                (ad_soyad, pozisyon, telefon, email, giris_tarihi, mesai_baslangic, mac_adres, notlar, is_active, pid)
            )
            return jsonify({"ok": True, "id": int(pid)})
        row = execute_returning(
            """INSERT INTO personel (ad_soyad, pozisyon, telefon, email, giris_tarihi, mesai_baslangic, mac_adres, notlar, is_active)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (ad_soyad, pozisyon, telefon, email, giris_tarihi, mesai_baslangic, mac_adres, notlar, is_active)
        )
        return jsonify({"ok": True, "id": row["id"]})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/personel/bilgi")
@giris_gerekli
def api_personel_bilgi():
    pid = request.args.get("personel_id")
    if not pid:
        return jsonify({})
    r = fetch_one("SELECT * FROM personel_bilgi WHERE personel_id=%s", (pid,))
    if not r:
        return jsonify({"personel_id": int(pid), "yillik_izin_hakki": 14, "manuel_izin_gun": 0})
    d = dict(r)
    if d.get("ise_baslama_tarihi"):
        d["ise_baslama_tarihi"] = d["ise_baslama_tarihi"].isoformat()[:10] if hasattr(d["ise_baslama_tarihi"], "isoformat") else str(d["ise_baslama_tarihi"])[:10]
    return jsonify(d)


@bp.route("/api/personel/bilgi", methods=["POST"])
@giris_gerekli
def api_personel_bilgi_kaydet():
    try:
        data = request.json or request.form
        pid = int(data.get("personel_id"))
        ise_baslama = _parse_date(data.get("ise_baslama_tarihi"))
        yillik_izin_hakki = int(data.get("yillik_izin_hakki") or 14)
        manuel_izin_gun = int(data.get("manuel_izin_gun") or 0)
        unvan = (data.get("unvan") or "").strip()
        departman = (data.get("departman") or "").strip()
        tc_no = (data.get("tc_no") or "").strip()
        gec_kesinti_tipi = (data.get("gec_kesinti_tipi") or "izin").strip()
        if gec_kesinti_tipi not in ("maas", "izin"):
            gec_kesinti_tipi = "izin"

        execute(
            """INSERT INTO personel_bilgi (personel_id, ise_baslama_tarihi, yillik_izin_hakki, manuel_izin_gun, unvan, departman, tc_no, gec_kesinti_tipi)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (personel_id) DO UPDATE SET
                 ise_baslama_tarihi=EXCLUDED.ise_baslama_tarihi, yillik_izin_hakki=EXCLUDED.yillik_izin_hakki,
                 manuel_izin_gun=EXCLUDED.manuel_izin_gun, unvan=EXCLUDED.unvan, departman=EXCLUDED.departman,
                 tc_no=EXCLUDED.tc_no, gec_kesinti_tipi=EXCLUDED.gec_kesinti_tipi""",
            (pid, ise_baslama, yillik_izin_hakki, manuel_izin_gun, unvan, departman, tc_no, gec_kesinti_tipi)
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


# ── Devam (günlük listesi, aylık özet, manuel kayıt) ─────────────────────────

@bp.route("/api/devam/gunluk")
@giris_gerekli
def api_devam_gunluk():
    tarih_str = request.args.get("tarih")
    t = _parse_date(tarih_str) or date.today()
    rows = fetch_all(
        """SELECT d.id, d.personel_id, p.ad_soyad, p.mesai_baslangic, d.giris_saati, d.cikis_saati, d.gec_dakika, d.kaynak
           FROM devam_kayitlari d
           JOIN personel p ON p.id = d.personel_id
           WHERE d.tarih = %s AND p.is_active = TRUE
           ORDER BY p.ad_soyad""",
        (t,)
    )
    out = []
    for r in rows:
        d = dict(r)
        d["tarih"] = t.isoformat()
        d["durum"] = "Tam" if d.get("cikis_saati") else ("Geç " + str(d.get("gec_dakika") or 0) + " dk" if d.get("gec_dakika") else "Giriş yaptı")
        out.append(d)
    return jsonify(out)


@bp.route("/api/devam/aylik")
@giris_gerekli
def api_devam_aylik():
    yil = int(request.args.get("yil") or date.today().year)
    ay = int(request.args.get("ay") or date.today().month)
    bas = date(yil, ay, 1)
    if ay == 12:
        bitis = date(yil, 12, 31)
    else:
        bitis = date(yil, ay + 1, 1) - timedelta(days=1)

    rows = fetch_all(
        """SELECT p.id, p.ad_soyad,
                  COUNT(d.id) AS toplam_gun,
                  SUM(CASE WHEN d.gec_dakika > 0 THEN 1 ELSE 0 END) AS gec_sayisi,
                  COALESCE(SUM(d.gec_dakika), 0) AS toplam_gec_dk
           FROM personel p
           LEFT JOIN devam_kayitlari d ON d.personel_id = p.id AND d.tarih >= %s AND d.tarih <= %s AND d.giris_saati IS NOT NULL
           WHERE p.is_active = TRUE
           GROUP BY p.id, p.ad_soyad
           ORDER BY p.ad_soyad""",
        (bas, bitis)
    )
    out = []
    for r in rows:
        d = dict(r)
        gec_say = int(d.get("gec_sayisi") or 0)
        toplam_dk = int(d.get("toplam_gec_dk") or 0)
        d["gec_sayisi"] = gec_say
        d["ort_gec_dk"] = round(toplam_dk / gec_say, 1) if gec_say else 0
        out.append(d)
    return jsonify(out)


@bp.route("/api/devam/kaydet", methods=["POST"])
@giris_gerekli
def api_devam_kaydet():
    try:
        data = request.json or request.form
        pid = int(data.get("personel_id"))
        t = _parse_date(data.get("tarih")) or date.today()
        giris = (data.get("giris_saati") or "").strip()[:5]  # HH:MM
        cikis = (data.get("cikis_saati") or "").strip()[:5]

        personel = fetch_one("SELECT mesai_baslangic FROM personel WHERE id=%s", (pid,))
        mesai = (personel.get("mesai_baslangic") or "09:00") if personel else "09:00"
        gec_dakika = 0
        if giris:
            try:
                from datetime import datetime as dt
                m_saat, m_dk = map(int, mesai.split(":")[:2])
                g_saat, g_dk = map(int, giris.split(":")[:2])
                fark_dk = (g_saat * 60 + g_dk) - (m_saat * 60 + m_dk)
                if fark_dk > 5:
                    gec_dakika = fark_dk
            except Exception:
                pass

        row = fetch_one("SELECT id FROM devam_kayitlari WHERE personel_id=%s AND tarih=%s", (pid, t))
        if row:
            execute(
                "UPDATE devam_kayitlari SET giris_saati=%s, cikis_saati=%s, gec_dakika=%s, kaynak=%s WHERE id=%s",
                (giris or None, cikis or None, gec_dakika, "manuel", row["id"])
            )
        else:
            execute(
                "INSERT INTO devam_kayitlari (personel_id, tarih, giris_saati, cikis_saati, gec_dakika, kaynak) VALUES (%s,%s,%s,%s,%s,%s)",
                (pid, t, giris or None, cikis or None, gec_dakika, "manuel")
            )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


# ── İzin ────────────────────────────────────────────────────────────────────

@bp.route("/api/izin/list")
@giris_gerekli
def api_izin_list():
    pid = request.args.get("personel_id")
    yil = request.args.get("yil")
    if not pid:
        return jsonify([])
    sql = "SELECT id, personel_id, izin_turu, baslangic_tarihi, bitis_tarihi, gun_sayisi, aciklama, onay_durumu FROM personel_izin WHERE personel_id=%s"
    params = [pid]
    if yil:
        sql += " AND (EXTRACT(YEAR FROM baslangic_tarihi)=%s OR EXTRACT(YEAR FROM bitis_tarihi)=%s)"
        params.extend([yil, yil])
    sql += " ORDER BY baslangic_tarihi DESC"
    rows = fetch_all(sql, params)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("baslangic_tarihi", "bitis_tarihi"):
            if d.get(k) and hasattr(d[k], "isoformat"):
                d[k] = d[k].isoformat()[:10]
        out.append(d)
    return jsonify(out)


@bp.route("/api/izin/ozet")
@giris_gerekli
def api_izin_ozet():
    pid = request.args.get("personel_id")
    yil = int(request.args.get("yil") or date.today().year)
    if not pid:
        return jsonify({"hak": 14, "kullanilan": 0, "kalan": 14})
    bilgi = fetch_one("SELECT yillik_izin_hakki, manuel_izin_gun FROM personel_bilgi WHERE personel_id=%s", (pid,))
    hak = int(bilgi.get("yillik_izin_hakki") or 14) if bilgi else 14
    manuel = int(bilgi.get("manuel_izin_gun") or 0) if bilgi else 0
    r = fetch_one(
        """SELECT COALESCE(SUM(gun_sayisi), 0) AS toplam FROM personel_izin
           WHERE personel_id=%s AND izin_turu = 'Yıllık Ücretli İzin'
           AND (EXTRACT(YEAR FROM baslangic_tarihi)=%s OR EXTRACT(YEAR FROM bitis_tarihi)=%s)""",
        (pid, yil, yil)
    )
    kullanilan = float(r.get("toplam") or 0)
    kalan = hak + manuel - kullanilan
    return jsonify({"hak": hak, "manuel_ek": manuel, "toplam_hak": hak + manuel, "kullanilan": kullanilan, "kalan": max(0, kalan)})


@bp.route("/api/izin/kaydet", methods=["POST"])
@giris_gerekli
def api_izin_kaydet():
    try:
        data = request.json or request.form
        pid = int(data.get("personel_id"))
        izin_turu = (data.get("izin_turu") or "Yıllık Ücretli İzin").strip()
        bas = _parse_date(data.get("baslangic_tarihi"))
        bitis = _parse_date(data.get("bitis_tarihi"))
        if not bas or not bitis:
            return jsonify({"ok": False, "mesaj": "Başlangıç ve bitiş tarihi zorunlu"}), 400
        if bitis < bas:
            bitis, bas = bas, bitis
        gun_sayisi = (bitis - bas).days + 1
        aciklama = (data.get("aciklama") or "").strip()
        execute(
            "INSERT INTO personel_izin (personel_id, izin_turu, baslangic_tarihi, bitis_tarihi, gun_sayisi, aciklama) VALUES (%s,%s,%s,%s,%s,%s)",
            (pid, izin_turu, bas, bitis, gun_sayisi, aciklama)
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/izin/sil", methods=["POST"])
@giris_gerekli
def api_izin_sil():
    try:
        iid = int((request.json or request.form).get("izin_id"))
        execute("DELETE FROM personel_izin WHERE id=%s", (iid,))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


# ── Geç kalma listesi ───────────────────────────────────────────────────────

@bp.route("/api/gec/list")
@giris_gerekli
def api_gec_list():
    pid = request.args.get("personel_id")
    if not pid:
        return jsonify([])
    rows = fetch_all(
        "SELECT tarih, giris_saati, cikis_saati, gec_dakika FROM devam_kayitlari WHERE personel_id=%s AND gec_dakika > 0 ORDER BY tarih DESC LIMIT 100",
        (pid,)
    )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("tarih") and hasattr(d["tarih"], "isoformat"):
            d["tarih"] = d["tarih"].isoformat()[:10]
        out.append(d)
    return jsonify(out)


# ── Yetki alanları ───────────────────────────────────────────────────────────

MODULLER = ["musteriler", "ofisler", "faturalar", "tahsilat", "kargolar", "kira", "tufe", "personel"]

@bp.route("/api/yetki")
@giris_gerekli
def api_yetki_get():
    pid = request.args.get("personel_id")
    if not pid:
        return jsonify([])
    rows = fetch_all("SELECT modul, yetki FROM personel_yetki WHERE personel_id=%s", (pid,))
    return jsonify([dict(r) for r in rows])


@bp.route("/api/yetki", methods=["POST"])
@giris_gerekli
def api_yetki_kaydet():
    try:
        data = request.json or request.form
        pid = int(data.get("personel_id"))
        yetkiler = data.get("yetkiler") or []  # [{"modul": "faturalar", "yetki": "goruntuleme"}, ...]
        execute("DELETE FROM personel_yetki WHERE personel_id=%s", (pid,))
        for y in yetkiler:
            modul = (y.get("modul") or "").strip()
            yetki = (y.get("yetki") or "goruntuleme").strip()
            if modul and modul in MODULLER:
                execute("INSERT INTO personel_yetki (personel_id, modul, yetki) VALUES (%s,%s,%s)", (pid, modul, yetki))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
