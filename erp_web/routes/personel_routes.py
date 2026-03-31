# -*- coding: utf-8 -*-
"""
Personel Devam Takip, İzin Yönetimi, Personel Yönetimi
"""
from flask import Blueprint, render_template, request, jsonify
from flask_login import current_user
from auth import giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning
from datetime import date, datetime, timedelta
from utils.devam_bulut_sync import sync_devam_gunu_buluta
from routes.pdovam_routes import pdovam_toplam_fark_dk_for_personel


MESAI_SABAH_DK = 9 * 60
VARSAYILAN_CIKIS_DK = 18 * 60 + 30


def _saat_to_minutes(val):
    if val is None:
        return None
    if hasattr(val, "hour"):
        try:
            return int(val.hour) * 60 + int(val.minute)
        except Exception:
            return None
    s = str(val).strip()
    if not s:
        return None
    p = s.split(":")
    if len(p) < 2:
        return None
    try:
        return int(p[0]) * 60 + int(p[1])
    except Exception:
        return None


def _dk_yazi(dk: int) -> str:
    try:
        dk = int(dk or 0)
    except Exception:
        dk = 0
    if dk <= 0:
        return "0 dk"
    h = dk // 60
    m = dk % 60
    if h > 0 and m > 0:
        return f"{h} saat {m} dk"
    if h > 0:
        return f"{h} saat"
    return f"{m} dk"


def _dk_gun_yazi(dk: int) -> str:
    try:
        dk = int(dk or 0)
    except Exception:
        dk = 0
    if dk <= 0:
        return "0 dk"
    gun = dk // (8 * 60)
    kalan = dk % (8 * 60)
    saat = kalan // 60
    dakika = kalan % 60
    if gun > 0 and saat > 0 and dakika > 0:
        return f"{gun} gün {saat} saat {dakika} dk"
    if gun > 0 and saat > 0:
        return f"{gun} gün {saat} saat"
    if gun > 0 and dakika > 0:
        return f"{gun} gün {dakika} dk"
    if gun > 0:
        return f"{gun} gün"
    if saat > 0 and dakika > 0:
        return f"{saat} saat {dakika} dk"
    if saat > 0:
        return f"{saat} saat"
    return f"{dakika} dk"


def _saat_to_gun_saat_yazi(saat_val: float) -> str:
    """Saat değerini 1 gün=8 saat kuralı ile 'X gün Y saat' yazar."""
    try:
        toplam_saat = float(saat_val or 0)
    except Exception:
        toplam_saat = 0.0
    if toplam_saat <= 0:
        return "0 dk"
    toplam_dk = int(round(toplam_saat * 60))
    return _dk_gun_yazi(toplam_dk)


def _safe_anniversary(ref: date, mm: int, dd: int) -> date:
    """ref.year içinde mm/dd; geçersizse (29 Şubat) 28 Şubat'a kırp."""
    try:
        return date(ref.year, mm, dd)
    except ValueError:
        # 29 Şubat gibi
        if mm == 2 and dd == 29:
            return date(ref.year, 2, 28)
        # fallback: ayın son günü
        for d in range(28, 0, -1):
            try:
                return date(ref.year, mm, d)
            except ValueError:
                continue
        return date(ref.year, 1, 1)


def _izin_yili_araligi(ise_giris: date | None, bugun: date) -> tuple[date, date]:
    """İşe giriş gün/ayına göre son 1 izin yılı aralığı (başlangıç yıldönümü -> bugün)."""
    if not ise_giris:
        return (bugun - timedelta(days=365), bugun)
    anniv = _safe_anniversary(bugun, ise_giris.month, ise_giris.day)
    if anniv <= bugun:
        bas = anniv
    else:
        bas = _safe_anniversary(date(bugun.year - 1, 1, 1), ise_giris.month, ise_giris.day).replace(year=bugun.year - 1)
    return (bas, bugun)


def _fark_toplam_dk_from_events(events: list[dict]) -> int:
    """pdovam fark mantığının özet hali: geç kalma + gün içi çıkış->giriş + erken çıkış."""
    by_day: dict[str, list[dict]] = {}
    for e in events or []:
        tk = str(e.get("tarih") or "")[:10]
        if not tk:
            continue
        islem = (e.get("islem") or "").strip().lower()
        if islem not in ("giris", "cikis"):
            continue
        m = _saat_to_minutes(e.get("saat"))
        if m is None:
            continue
        by_day.setdefault(tk, []).append({"islem": islem, "min": int(m)})

    total = 0
    for tk, evs in by_day.items():
        evs.sort(key=lambda x: (x["min"], 0 if x["islem"] == "giris" else 1))
        # ilk giriş
        i0 = None
        for i, x in enumerate(evs):
            if x["islem"] == "giris":
                i0 = i
                break
        if i0 is None:
            continue
        trimmed = evs[i0:]
        first_min = trimmed[0]["min"]
        gec_sabah = max(0, first_min - MESAI_SABAH_DK)

        inside = True
        pending = None
        izin_disari = 0
        for x in trimmed[1:]:
            if x["islem"] == "cikis":
                pending = x["min"]
                inside = False
            else:
                if not inside and pending is not None:
                    izin_disari += max(0, x["min"] - pending)
                    pending = None
                inside = True

        if pending is not None:
            cikis_min = min(pending, VARSAYILAN_CIKIS_DK)
            erken = max(0, VARSAYILAN_CIKIS_DK - cikis_min)
        else:
            erken = 0

        total += int(gec_sabah + izin_disari + erken)
    return int(total)

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


def _izin_hakki_4857(ise_baslama, dogum_tarihi, ref_tarih=None):
    """4857 sayılı İş Kanunu: kıdem ve yaşa göre yıllık ücretli izin günü.
    - 0-6 yıl: 14 gün, 6-15 yıl: 20 gün, 15+ yıl: 26 gün.
    - 18 yaş altı veya 50 yaş üstü: en az 20 gün.
    """
    ref = ref_tarih or date.today()
    if not ise_baslama:
        return 14
    # Kıdem (tam yıl): işe girişten ref tarihine kadar
    gun_farki = (ref - ise_baslama).days
    if gun_farki < 365:
        kidem_gun = 14  # 1 yıldan az da en az 14 (deneme dahil 1 yıl şartı uygulanabilir)
    else:
        kidem_yil = gun_farki // 365
        if kidem_yil < 6:
            kidem_gun = 14
        elif kidem_yil < 15:
            kidem_gun = 20
        else:
            kidem_gun = 26
    # Yaş kriteri (18 altı / 50 üstü): en az 20 gün
    if dogum_tarihi:
        yas = ref.year - dogum_tarihi.year
        if (ref.month, ref.day) < (dogum_tarihi.month, dogum_tarihi.day):
            yas -= 1
        if yas <= 18 or yas >= 50:
            return max(20, kidem_gun)
    return kidem_gun


@bp.route("/")
@giris_gerekli
def index():
    is_admin = getattr(current_user, "role", None) == "admin"
    return render_template("personel/index.html", is_admin=is_admin)


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


@bp.route("/api/personel/ad", methods=["POST"])
@giris_gerekli
def api_personel_ad_guncelle():
    try:
        data = request.json or request.form
        pid = int(data.get("personel_id") or data.get("id"))
        ad = (data.get("ad_soyad") or "").strip()
        if not ad:
            return jsonify({"ok": False, "mesaj": "Ad soyad zorunlu"}), 400
        execute("UPDATE personel SET ad_soyad=%s WHERE id=%s", (ad, pid))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/personel/sil", methods=["POST"])
@giris_gerekli
def api_personel_sil():
    """Personeli sil (ilgili kayıtları da temizler)."""
    try:
        data = request.json or request.form
        pid = data.get("id") or data.get("personel_id")
        if not pid:
            return jsonify({"ok": False, "mesaj": "Personel id gerekli"}), 400
        pid = int(pid)
        for sql in (
            "DELETE FROM personel_izin WHERE personel_id=%s",
            "DELETE FROM personel_bilgi WHERE personel_id=%s",
            "DELETE FROM personel_yetki WHERE personel_id=%s",
        ):
            try:
                execute(sql, (pid,))
            except Exception:
                pass
        try:
            execute("DELETE FROM devam_kayitlari WHERE personel_id=%s", (pid,))
        except Exception:
            pass
        execute("DELETE FROM personel WHERE id=%s", (pid,))
        return jsonify({"ok": True})
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
        return jsonify({"personel_id": int(pid), "yillik_izin_hakki": 14, "yillik_izin_hakki_4857": 14, "manuel_izin_gun": 0})
    d = dict(r)
    if d.get("ise_baslama_tarihi"):
        d["ise_baslama_tarihi"] = d["ise_baslama_tarihi"].isoformat()[:10] if hasattr(d["ise_baslama_tarihi"], "isoformat") else str(d["ise_baslama_tarihi"])[:10]
    if d.get("dogum_tarihi") and hasattr(d["dogum_tarihi"], "isoformat"):
        d["dogum_tarihi"] = d["dogum_tarihi"].isoformat()[:10]
    ise_baslama = r.get("ise_baslama_tarihi")
    dogum = r.get("dogum_tarihi")
    d["yillik_izin_hakki_4857"] = _izin_hakki_4857(ise_baslama, dogum)
    return jsonify(d)


@bp.route("/api/personel/bilgi", methods=["POST"])
@giris_gerekli
def api_personel_bilgi_kaydet():
    try:
        data = request.json or request.form
        pid = int(data.get("personel_id"))
        ise_baslama = _parse_date(data.get("ise_baslama_tarihi"))
        dogum_tarihi = _parse_date(data.get("dogum_tarihi"))
        yillik_izin_hakki = int(data.get("yillik_izin_hakki") or 0) or _izin_hakki_4857(ise_baslama, dogum_tarihi)
        manuel_izin_gun = int(data.get("manuel_izin_gun") or 0)
        unvan = (data.get("unvan") or "").strip()
        departman = (data.get("departman") or "").strip()
        tc_no = (data.get("tc_no") or "").strip()
        gec_kesinti_tipi = (data.get("gec_kesinti_tipi") or "izin").strip()
        if gec_kesinti_tipi not in ("maas", "izin"):
            gec_kesinti_tipi = "izin"

        execute(
            """INSERT INTO personel_bilgi (personel_id, ise_baslama_tarihi, dogum_tarihi, yillik_izin_hakki, manuel_izin_gun, unvan, departman, tc_no, gec_kesinti_tipi)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (personel_id) DO UPDATE SET
                 ise_baslama_tarihi=EXCLUDED.ise_baslama_tarihi, dogum_tarihi=EXCLUDED.dogum_tarihi, yillik_izin_hakki=EXCLUDED.yillik_izin_hakki,
                 manuel_izin_gun=EXCLUDED.manuel_izin_gun, unvan=EXCLUDED.unvan, departman=EXCLUDED.departman,
                 tc_no=EXCLUDED.tc_no, gec_kesinti_tipi=EXCLUDED.gec_kesinti_tipi""",
            (pid, ise_baslama, dogum_tarihi, yillik_izin_hakki, manuel_izin_gun, unvan, departman, tc_no, gec_kesinti_tipi)
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


# ── Özlük / detay bilgileri (sadece admin) ───────────────────────────────────

def _ozluk_columns():
    return (
        "tc_kimlik", "dogum_tarihi", "dogum_yeri", "medeni_durum", "esi_calisiyor", "cocuk_sayisi",
        "cinsiyet", "kan_grubu", "ikametgah", "cep_telefon", "mac_adres", "email", "acil_kisi",
        "ise_giris_tarihi", "izin_hakedis_gun", "izin_hakedis_saat", "izin_kalan_gun", "izin_kalan_saat",
        "departman", "unvan", "gorev_tanimi", "calisma_sekli", "ucret_bilgisi",
        "iban", "yemek_yol_yardim", "ogrenim_durumu", "mezun_okul_bolum", "yabanci_dil",
        "adli_sicil", "saglik_raporu", "ikametgah_belgesi", "diploma", "nufus_kayit", "askerlik_durum", "notlar"
    )


@bp.route("/api/personel/ozluk")
@giris_gerekli
def api_personel_ozluk():
    """Özlük kaydını getir (sadece admin)."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"ok": False, "mesaj": "Yetkisiz"}), 403
    pid = request.args.get("personel_id")
    if not pid:
        return jsonify({})
    try:
        pid = int(pid)
    except ValueError:
        return jsonify({})
    r = fetch_one("SELECT * FROM personel_ozluk WHERE personel_id=%s", (pid,))
    if not r:
        return jsonify({"personel_id": pid, "izin_hakedis_gun": 14, "izin_hakedis_saat": 112})
    d = dict(r)
    # İzin hakediş: işe giriş yoksa default 14 gün / 112 saat (14*8); varsa 4857'ye göre gün, saat = gün*8
    ise_giris = d.get("ise_giris_tarihi")
    dogum = d.get("dogum_tarihi")
    if ise_giris:
        gun = _izin_hakki_4857(ise_giris, dogum)
        d["izin_hakedis_gun"] = gun
        d["izin_hakedis_saat"] = gun * 8
    else:
        d["izin_hakedis_gun"] = 14
        d["izin_hakedis_saat"] = 112
    for k in ("dogum_tarihi", "ise_giris_tarihi"):
        if d.get(k) and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()[:10]
    return jsonify(d)


@bp.route("/api/personel/ozluk", methods=["POST"])
@giris_gerekli
def api_personel_ozluk_kaydet():
    """Özlük kaydı oluştur/güncelle (sadece admin)."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"ok": False, "mesaj": "Yetkisiz"}), 403
    try:
        data = request.get_json(silent=True) or request.form
        pid = int(data.get("personel_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "personel_id gerekli"}), 400
    cols = _ozluk_columns()
    vals = []
    for c in cols:
        v = data.get(c)
        if c in ("dogum_tarihi", "ise_giris_tarihi"):
            v = _parse_date(v) if v else None
        elif c in ("cocuk_sayisi", "izin_hakedis_gun", "izin_hakedis_saat", "izin_kalan_gun", "izin_kalan_saat"):
            try:
                v = int(v) if v not in (None, "") else None
            except (ValueError, TypeError):
                v = None
        else:
            v = (v or "").strip() or None
        vals.append(v)
    placeholders = ", ".join("%s" for _ in cols)
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
    execute(
        f"""INSERT INTO personel_ozluk (personel_id, {", ".join(cols)})
            VALUES (%s, {placeholders})
            ON CONFLICT (personel_id) DO UPDATE SET {updates}, updated_at=NOW()""",
        (pid, *vals)
    )
    return jsonify({"ok": True})


@bp.route("/api/personel/ozluk/sil", methods=["POST"])
@giris_gerekli
def api_personel_ozluk_sil():
    """Özlük kaydını sil (sadece admin)."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"ok": False, "mesaj": "Yetkisiz"}), 403
    try:
        data = request.get_json(silent=True) or request.form
        pid = int(data.get("personel_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "personel_id gerekli"}), 400
    execute("DELETE FROM personel_ozluk WHERE personel_id=%s", (pid,))
    return jsonify({"ok": True})


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
            pn = fetch_one("SELECT ad_soyad FROM personel WHERE id=%s", (pid,))
            ad = (pn.get("ad_soyad") or "") if pn else ""
            execute(
                "INSERT INTO devam_kayitlari (personel_id, ad_soyad, tarih, giris_saati, cikis_saati, gec_dakika, kaynak) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (pid, ad, t, giris or None, cikis or None, gec_dakika, "manuel")
            )
        row_sb = fetch_one(
            "SELECT giris_saati, cikis_saati, tarih FROM devam_kayitlari WHERE personel_id=%s AND tarih=%s",
            (pid, t),
        )
        if row_sb:
            try:
                sync_devam_gunu_buluta(pid, row_sb["tarih"], row_sb.get("giris_saati"), row_sb.get("cikis_saati"))
            except Exception:
                pass
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
        sql += " AND (EXTRACT(YEAR FROM baslangic_tarihi::date)=%s OR EXTRACT(YEAR FROM bitis_tarihi::date)=%s)"
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
    ozluk = fetch_one(
        "SELECT izin_hakedis_gun, izin_kalan_gun, izin_kalan_saat, ise_giris_tarihi FROM personel_ozluk WHERE personel_id=%s",
        (pid,),
    )
    bilgi = fetch_one(
        "SELECT yillik_izin_hakki, manuel_izin_gun, ise_baslama_tarihi, dogum_tarihi FROM personel_bilgi WHERE personel_id=%s",
        (pid,)
    )
    p0 = fetch_one("SELECT giris_tarihi FROM personel WHERE id=%s", (pid,))

    # Öncelik: personel_ozluk.izin_hakedis_gun
    if ozluk and ozluk.get("izin_hakedis_gun") is not None and str(ozluk.get("izin_hakedis_gun")).strip() != "":
        hak = int(ozluk.get("izin_hakedis_gun"))
    # Sonra personel_bilgi.yillik_izin_hakki
    elif bilgi and bilgi.get("yillik_izin_hakki") is not None and str(bilgi.get("yillik_izin_hakki")).strip() != "":
        hak = int(bilgi.get("yillik_izin_hakki"))
    else:
        hak = _izin_hakki_4857(bilgi.get("ise_baslama_tarihi") if bilgi else None, bilgi.get("dogum_tarihi") if bilgi else None) if bilgi else 14
    manuel = int(bilgi.get("manuel_izin_gun") or 0) if bilgi else 0
    r = fetch_one(
        """SELECT COALESCE(SUM(gun_sayisi), 0) AS toplam FROM personel_izin
           WHERE personel_id=%s AND izin_turu = 'Yıllık Ücretli İzin'
           AND (EXTRACT(YEAR FROM baslangic_tarihi::date)=%s OR EXTRACT(YEAR FROM bitis_tarihi::date)=%s)""",
        (pid, yil, yil)
    )
    kullanilan = float(r.get("toplam") or 0)
    toplam_hak = hak + manuel
    kalan_hesap = max(0, toplam_hak - kullanilan)

    # Özlükte kalan gün/saat girildiyse onu öncelikle göster
    kalan_ozluk = None
    if ozluk:
        kg = ozluk.get("izin_kalan_gun")
        ks = ozluk.get("izin_kalan_saat")
        if kg not in (None, "") or ks not in (None, ""):
            try:
                kalan_ozluk = float(kg or 0) + (float(ks or 0) / 8.0)
            except (TypeError, ValueError):
                kalan_ozluk = None
    kalan = kalan_ozluk if kalan_ozluk is not None else kalan_hesap

    # Devreden/ek hak: manuel_ek ile ozluk-kalan farkını birlikte dikkate al
    devreden = manuel
    try:
        ekstra_from_kalan = max(0.0, float(kalan) - float(max(0, hak - kullanilan)))
        devreden = max(devreden, ekstra_from_kalan)
    except (TypeError, ValueError):
        pass
    # Geç Süre: giriş/çıkış raporundaki Fark mantığına göre;
    # tarih aralığı verilmezse son 1 ay, verilirse o aralık.
    bas_raw = request.args.get("bas")
    bit_raw = request.args.get("bit")

    def _parse_iso(s):
        if not s:
            return None
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    today = date.today()
    bas = _parse_iso(bas_raw)
    bit = _parse_iso(bit_raw)
    if bas and bit:
        if bit < bas:
            bas, bit = bit, bas
    elif bas and not bit:
        bit = today
    elif not bas and bit:
        bas = bit - timedelta(days=30)
    else:
        bit = today
        bas = today - timedelta(days=30)
    try:
        gec_sure_dk = pdovam_toplam_fark_dk_for_personel(int(pid), bas, bit)
    except Exception:
        gec_sure_dk = 0
    # Kalan izin: 1 gün = 8 saat. Geç süre (fark) izin bakiyesinden düşülür.
    kalan_saat_toplam = max(0.0, float(kalan) * 8.0 - (float(gec_sure_dk) / 60.0))
    kalan_net_gun = max(0.0, kalan_saat_toplam / 8.0)
    return jsonify({
        "hak": hak,
        "manuel_ek": devreden,
        "toplam_hak": toplam_hak,
        "kullanilan": kullanilan,
        "kalan": max(0, kalan),
        "kalan_net_gun": kalan_net_gun,
        "kalan_net_text": _saat_to_gun_saat_yazi(kalan_saat_toplam),
        "gec_sure_dk": gec_sure_dk,
        "gec_sure_saat": _dk_yazi(gec_sure_dk),
        "gec_sure_gun": _dk_gun_yazi(gec_sure_dk),
        "tarih_bas": bas.isoformat(),
        "tarih_bit": bit.isoformat(),
    })


@bp.route("/api/izin/ozet/liste")
@giris_gerekli
def api_izin_ozet_liste():
    """
    Aktif/pasif/tümü tüm personeller için izin özet listesi.
    UI'da "Süre" tablosunu besler.
    """
    filtre = request.args.get("filtre", "aktif")  # tumu, aktif, pasif
    yil = int(request.args.get("yil") or date.today().year)

    # Geç süre için tarih aralığı: ?bas / ?bit verilmezse son 1 ay
    bas_raw = request.args.get("bas")
    bit_raw = request.args.get("bit")

    def _parse_iso_local(s):
        if not s:
            return None
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    today = date.today()
    bas = _parse_iso_local(bas_raw)
    bit = _parse_iso_local(bit_raw)
    if bas and bit:
        if bit < bas:
            bas, bit = bit, bas
    elif bas and not bit:
        bit = today
    elif not bas and bit:
        bas = bit - timedelta(days=30)
    else:
        bit = today
        bas = today - timedelta(days=30)

    sql = """
      SELECT p.id, p.ad_soyad,
             pb.yillik_izin_hakki, pb.manuel_izin_gun,
             pb.ise_baslama_tarihi, pb.dogum_tarihi
      FROM personel p
      LEFT JOIN personel_bilgi pb ON pb.personel_id = p.id
      WHERE 1=1
    """
    params = []
    if filtre == "aktif":
        sql += " AND p.is_active = TRUE"
    elif filtre == "pasif":
        sql += " AND p.is_active = FALSE"
    sql += " ORDER BY p.ad_soyad"

    rows = fetch_all(sql, tuple(params)) or []
    if not rows:
        return jsonify([])

    pids = [int(r.get("id")) for r in rows if r.get("id") is not None]
    if not pids:
        return jsonify([])

    ozluk_rows = fetch_all(
        """
        SELECT personel_id, izin_hakedis_gun, izin_kalan_gun, izin_kalan_saat
        FROM personel_ozluk
        WHERE personel_id = ANY(%s)
        """,
        (pids,),
    ) or []
    ozluk_map = {int(o["personel_id"]): o for o in ozluk_rows if o.get("personel_id") is not None}

    kullanilan_rows = fetch_all(
        """
        SELECT personel_id, COALESCE(SUM(gun_sayisi), 0) AS toplam
        FROM personel_izin
        WHERE personel_id = ANY(%s)
          AND izin_turu = 'Yıllık Ücretli İzin'
          AND (EXTRACT(YEAR FROM baslangic_tarihi::date)=%s OR EXTRACT(YEAR FROM bitis_tarihi::date)=%s)
        GROUP BY personel_id
        """,
        (pids, yil, yil),
    ) or []
    kullanilan_map = {int(k["personel_id"]): float(k.get("toplam") or 0) for k in kullanilan_rows if k.get("personel_id") is not None}

    out = []
    for r in rows:
        pid = int(r.get("id"))
        ad_soyad = r.get("ad_soyad") or ""
        ozluk = ozluk_map.get(pid) or {}

        pb_var_mi = any(
            r.get(k) is not None and str(r.get(k)).strip() != ""
            for k in ("yillik_izin_hakki", "manuel_izin_gun", "ise_baslama_tarihi", "dogum_tarihi")
        )
        yillik_izin_hakki = r.get("yillik_izin_hakki")
        manuel = int(r.get("manuel_izin_gun") or 0) if pb_var_mi else 0

        if ozluk.get("izin_hakedis_gun") not in (None, ""):
            hak = int(ozluk.get("izin_hakedis_gun"))
        elif pb_var_mi and yillik_izin_hakki is not None and str(yillik_izin_hakki).strip() != "":
            hak = int(yillik_izin_hakki)
        else:
            hak = _izin_hakki_4857(r.get("ise_baslama_tarihi"), r.get("dogum_tarihi")) if pb_var_mi else 14

        kullanilan = float(kullanilan_map.get(pid, 0))
        toplam_hak = hak + manuel
        kalan_hesap = max(0, toplam_hak - kullanilan)

        kalan_ozluk = None
        kg = ozluk.get("izin_kalan_gun")
        ks = ozluk.get("izin_kalan_saat")
        if kg not in (None, "") or ks not in (None, ""):
            try:
                kalan_ozluk = float(kg or 0) + (float(ks or 0) / 8.0)
            except (TypeError, ValueError):
                kalan_ozluk = None

        kalan = kalan_ozluk if kalan_ozluk is not None else kalan_hesap
        devreden = manuel
        try:
            ekstra_from_kalan = max(0.0, float(kalan) - float(max(0, hak - kullanilan)))
            devreden = max(devreden, ekstra_from_kalan)
        except (TypeError, ValueError):
            pass

        # Geç Süre ve net kalan (tüm personeller için, Bahar'daki mantıkla)
        try:
            gec_sure_dk = pdovam_toplam_fark_dk_for_personel(pid, bas, bit)
        except Exception:
            gec_sure_dk = 0
        try:
            kalan_saat_toplam = max(0.0, float(kalan) * 8.0 - (float(gec_sure_dk) / 60.0))
            kalan_net_gun = max(0.0, kalan_saat_toplam / 8.0)
        except Exception:
            kalan_saat_toplam = float(kalan or 0) * 8.0
            kalan_net_gun = kalan

        out.append({
            "personel_id": pid,
            "ad_soyad": ad_soyad,
            "hak": hak,
            "manuel_ek": devreden,
            "toplam_hak": toplam_hak,
            "kullanilan": kullanilan,
            "kalan": kalan,
            "gec_sure_dk": gec_sure_dk,
            "gec_sure_saat": _dk_yazi(gec_sure_dk),
            "gec_sure_gun": _dk_gun_yazi(gec_sure_dk),
            "kalan_net_gun": kalan_net_gun,
            "kalan_net_text": _saat_to_gun_saat_yazi(kalan_saat_toplam),
            "tarih_bas": bas.isoformat(),
            "tarih_bit": bit.isoformat(),
        })
    return jsonify(out)


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
