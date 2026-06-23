# -*- coding: utf-8 -*-
"""
Personel Devam Takip, İzin Yönetimi, Personel Yönetimi
"""
import io
import os
import sys

from flask import Blueprint, render_template, request, jsonify, Response
from flask_login import current_user
from auth import giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning
from datetime import date, datetime, timedelta
from utils.devam_bulut_sync import sync_devam_gunu_buluta
from routes.pdovam_routes import pdovam_toplam_fark_dk_for_personel

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from izin_form_pdf import izin_formu_olustur


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


def _tr_tarih(val) -> str:
    """date/datetime/str → dd.mm.yyyy; boşsa ''."""
    if not val:
        return ""
    if hasattr(val, "strftime"):
        return val.strftime("%d.%m.%Y")
    s = str(val)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return s


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
    for item in out:
        for k in ("giris_saati", "cikis_saati"):
            if item.get(k) and hasattr(item[k], "isoformat"):
                item[k] = str(item[k])[:5]
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
    sql = "SELECT id, personel_id, izin_turu, baslangic_tarihi, bitis_tarihi, gun_sayisi, saat_sayisi, aciklama, onay_durumu, created_at FROM personel_izin WHERE personel_id=%s"
    params = [pid]
    if yil:
        sql += " AND (EXTRACT(YEAR FROM baslangic_tarihi::date)=%s OR EXTRACT(YEAR FROM bitis_tarihi::date)=%s)"
        params.extend([yil, yil])
    sql += " ORDER BY baslangic_tarihi ASC, created_at ASC"
    rows = fetch_all(sql, params)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("baslangic_tarihi", "bitis_tarihi"):
            if d.get(k) and hasattr(d[k], "isoformat"):
                d[k] = d[k].isoformat()[:10]
        if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return jsonify(out)


def _parse_iso_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _izin_ozet_hesapla(pid, yil, bas=None, bit=None):
    """Yıllık izin özeti (api_izin_ozet ile aynı mantık)."""
    pid = int(pid)
    ozluk = fetch_one(
        "SELECT izin_hakedis_gun, izin_kalan_gun, izin_kalan_saat, ise_giris_tarihi FROM personel_ozluk WHERE personel_id=%s",
        (pid,),
    )
    bilgi = fetch_one(
        "SELECT yillik_izin_hakki, manuel_izin_gun, ise_baslama_tarihi, dogum_tarihi FROM personel_bilgi WHERE personel_id=%s",
        (pid,),
    )

    ise_bas = bilgi.get("ise_baslama_tarihi") if bilgi else None
    if not ise_bas:
        p_row = fetch_one("SELECT giris_tarihi FROM personel WHERE id=%s", (pid,))
        if p_row and p_row.get("giris_tarihi"):
            ise_bas = p_row["giris_tarihi"]
    dogum = bilgi.get("dogum_tarihi") if bilgi else None

    # Öncelik: personel_ozluk.izin_hakedis_gun (elle girilmiş > 0)
    ozluk_hak = None
    if ozluk and ozluk.get("izin_hakedis_gun") is not None and str(ozluk.get("izin_hakedis_gun")).strip() != "":
        try:
            ozluk_hak = int(ozluk.get("izin_hakedis_gun"))
        except (TypeError, ValueError):
            ozluk_hak = None
    hak_4857 = _izin_hakki_4857(ise_bas, dogum)
    if ozluk_hak is not None and ozluk_hak > 0:
        # Elle girilen değer ile 4857 hesabından büyük olanı al
        # (4857 minimum hak, altına düşemez)
        hak = max(ozluk_hak, hak_4857)
    else:
        hak = hak_4857
    manuel = int(bilgi.get("manuel_izin_gun") or 0) if bilgi else 0
    r = fetch_one(
        """SELECT COALESCE(SUM(gun_sayisi), 0) AS kullanilan_gun,
                  COALESCE(SUM(saat_sayisi), 0) AS kullanilan_saat_raw
           FROM personel_izin
           WHERE personel_id=%s AND izin_turu IN ('Yıllık Ücretli İzin', 'Saatlik İzin')
           AND (EXTRACT(YEAR FROM baslangic_tarihi::date)=%s OR EXTRACT(YEAR FROM bitis_tarihi::date)=%s)""",
        (pid, yil, yil),
    )
    kullanilan_gun_raw = float(r.get("kullanilan_gun") or 0)
    kullanilan_saat_raw = int(r.get("kullanilan_saat_raw") or 0)
    kullanilan_saat_toplam = int(kullanilan_gun_raw * 8) + kullanilan_saat_raw
    kullanilan_gun_net = kullanilan_saat_toplam // 8
    kullanilan_saat = kullanilan_saat_toplam % 8
    kullanilan = kullanilan_saat_toplam / 8.0
    toplam_hak = hak + manuel
    kalan_hesap = max(0.0, toplam_hak - kullanilan)

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

    gec_row = fetch_one(
        """SELECT COALESCE(SUM(gec_dakika), 0) AS toplam_gec_dk
           FROM devam_kayitlari
           WHERE personel_id=%s
           AND EXTRACT(YEAR FROM tarih)=%s
           AND gec_dakika > 0""",
        (pid, yil),
    )
    gec_sure_dk = int(gec_row.get("toplam_gec_dk") or 0)
    # Kalan izin: 1 gün = 8 saat. Yıllık geç kalma (dk) izin bakiyesinden düşülür.
    kalan_saat_toplam = max(0.0, float(kalan) * 8.0 - (float(gec_sure_dk) / 60.0))
    kalan_net_gun = max(0.0, kalan_saat_toplam / 8.0)
    kalan_gun_net = int(kalan_saat_toplam // 8)
    kalan_saat = int(kalan_saat_toplam % 8)
    return {
        "hak": hak,
        "manuel_ek": devreden,
        "toplam_hak": toplam_hak,
        "kullanilan": kullanilan,
        "kullanilan_gun_net": kullanilan_gun_net,
        "kullanilan_saat": kullanilan_saat,
        "kalan": max(0, kalan),
        "kalan_gun_net": kalan_gun_net,
        "kalan_saat": kalan_saat,
        "kalan_net_gun": kalan_net_gun,
        "kalan_net_text": _saat_to_gun_saat_yazi(kalan_saat_toplam),
        "gec_sure_dk": gec_sure_dk,
        "gec_sure_saat": _dk_yazi(gec_sure_dk),
        "gec_sure_gun": _dk_gun_yazi(gec_sure_dk),
        "gec_dakika_yil": gec_sure_dk,
        "gec_gun_net": int(gec_sure_dk // 480),
        "gec_saat_net": int((gec_sure_dk % 480) // 60),
        "tarih_bas": date(yil, 1, 1).isoformat(),
        "tarih_bit": date(yil, 12, 31).isoformat(),
    }


def _izin_pdf_data_from_row(row: dict, izin_bakiye: dict) -> dict:
    bitis = row.get("bitis_tarihi")
    ise_donme = ""
    if bitis:
        try:
            d = bitis if hasattr(bitis, "day") else datetime.strptime(str(bitis)[:10], "%Y-%m-%d").date()
            ise_donme = _tr_tarih(d + timedelta(days=1))
        except Exception:
            pass

    tur = row.get("izin_turu") or ""
    gun = row.get("gun_sayisi") or 0
    saat_sayisi = row.get("saat_sayisi") or 0
    try:
        yari_gun = tur == "Yarım Gün İzin" or float(gun) <= 0.5
    except (TypeError, ValueError):
        yari_gun = tur == "Yarım Gün İzin"

    return {
        "firma_adi": "BestOffice ERP",
        "doc_no": f"İK-{row['izin_id']:04d}",
        "bugun": date.today().strftime("%d.%m.%Y"),
        "personel_ad": row.get("ad_soyad") or "",
        "tc_no": row.get("tc_no") or "",
        "departman": row.get("departman") or "",
        "unvan": row.get("unvan") or row.get("pozisyon") or "",
        "ise_baslama": _tr_tarih(row.get("ise_baslama_tarihi") or row.get("giris_tarihi")),
        "izin_turu": tur,
        "baslangic": _tr_tarih(row.get("baslangic_tarihi")),
        "bitis": _tr_tarih(bitis),
        "ise_donme": ise_donme,
        "gun_sayisi": gun,
        "saat_sayisi": saat_sayisi,
        "yari_gun": yari_gun,
        "aciklama": row.get("aciklama") or "",
        "adres": row.get("ikametgah") or "",
        "telefon": row.get("personel_telefon") or row.get("cep_telefon") or "",
        "izin_bakiye": izin_bakiye,
    }


@bp.route("/api/izin/ozet")
@giris_gerekli
def api_izin_ozet():
    pid = request.args.get("personel_id")
    yil = int(request.args.get("yil") or date.today().year)
    if not pid:
        return jsonify({"hak": 14, "kullanilan": 0, "kalan": 14})
    bas = _parse_iso_date(request.args.get("bas"))
    bit = _parse_iso_date(request.args.get("bit"))
    return jsonify(_izin_ozet_hesapla(pid, yil, bas=bas, bit=bit))


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
        if izin_turu == "Saatlik İzin":
            if not bas:
                return jsonify({"ok": False, "mesaj": "Tarih zorunlu"}), 400
            try:
                saat_sayisi = int(data.get("saat_sayisi") or 0)
            except (TypeError, ValueError):
                saat_sayisi = 0
            if saat_sayisi < 1 or saat_sayisi > 8:
                return jsonify({"ok": False, "mesaj": "Saat 1-8 arası olmalı"}), 400
            bitis = bas
            gun_sayisi = 0
        else:
            if not bas or not bitis:
                return jsonify({"ok": False, "mesaj": "Başlangıç ve bitiş tarihi zorunlu"}), 400
            if bitis < bas:
                bitis, bas = bas, bitis
            gun_sayisi = (bitis - bas).days + 1
            saat_sayisi = 0
        aciklama = (data.get("aciklama") or "").strip()
        execute(
            "INSERT INTO personel_izin (personel_id, izin_turu, baslangic_tarihi, bitis_tarihi, gun_sayisi, saat_sayisi, aciklama) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (pid, izin_turu, bas, bitis, gun_sayisi, saat_sayisi, aciklama)
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


@bp.route("/api/izin/guncelle", methods=["POST"])
@giris_gerekli
def api_izin_guncelle():
    try:
        data = request.json or request.form
        iid = int(data.get("izin_id"))
        pid = int(data.get("personel_id"))
        izin_turu = (data.get("izin_turu") or "Yıllık Ücretli İzin").strip()
        bas = _parse_date(data.get("baslangic_tarihi"))
        bitis = _parse_date(data.get("bitis_tarihi"))
        if izin_turu == "Saatlik İzin":
            if not bas:
                return jsonify({"ok": False, "mesaj": "Tarih zorunlu"}), 400
            try:
                saat_sayisi = float(data.get("saat_sayisi") or 0)
            except (TypeError, ValueError):
                saat_sayisi = 0
            if saat_sayisi < 1 or saat_sayisi > 8:
                return jsonify({"ok": False, "mesaj": "Saat 1-8 arası olmalı"}), 400
            bitis = bas
            gun_sayisi = 0
        else:
            if not bas or not bitis:
                return jsonify({"ok": False, "mesaj": "Başlangıç ve bitiş tarihi zorunlu"}), 400
            if bitis < bas:
                bitis, bas = bas, bitis
            gun_sayisi = (bitis - bas).days + 1
            saat_sayisi = 0
        aciklama = (data.get("aciklama") or "").strip()
        row = fetch_one(
            "SELECT id FROM personel_izin WHERE id=%s AND personel_id=%s",
            (iid, pid),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "İzin kaydı bulunamadı"}), 404
        execute(
            """
            UPDATE personel_izin
            SET izin_turu=%s, baslangic_tarihi=%s, bitis_tarihi=%s,
                gun_sayisi=%s, saat_sayisi=%s, aciklama=%s
            WHERE id=%s AND personel_id=%s
            """,
            (izin_turu, bas, bitis, gun_sayisi, saat_sayisi, aciklama, iid, pid),
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/izin/otomatik-hesapla", methods=["POST"])
@giris_gerekli
def api_izin_otomatik_hesapla():
    """Test / manuel tetikleme: QR hareketlerinden otomatik izin hesabı."""
    try:
        from services.izin_otomatik import gunden_otomatik_izin_hesapla

        data = request.get_json(silent=True) or request.form or {}
        tarih_raw = data.get("tarih")
        if tarih_raw:
            hedef = _parse_iso_date(tarih_raw)
            if not hedef:
                return jsonify({"ok": False, "mesaj": "Geçersiz tarih (YYYY-MM-DD)"}), 400
        else:
            hedef = date.today() - timedelta(days=1)
        sonuc = gunden_otomatik_izin_hesapla(hedef)
        return jsonify({"ok": True, **sonuc})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/izin-pdf/<int:izin_id>")
@giris_gerekli
def api_izin_pdf(izin_id):
    row = fetch_one(
        """
        SELECT
            pi.id              AS izin_id,
            pi.personel_id,
            pi.izin_turu,
            pi.baslangic_tarihi,
            pi.bitis_tarihi,
            pi.gun_sayisi,
            pi.saat_sayisi,
            pi.aciklama,
            p.ad_soyad,
            p.telefon          AS personel_telefon,
            p.giris_tarihi,
            p.pozisyon,
            pb.tc_no,
            pb.departman,
            pb.unvan,
            pb.ise_baslama_tarihi,
            po.ikametgah,
            po.cep_telefon
        FROM personel_izin pi
        JOIN personel p ON p.id = pi.personel_id
        LEFT JOIN personel_bilgi pb ON pb.personel_id = pi.personel_id
        LEFT JOIN personel_ozluk po ON po.personel_id = pi.personel_id
        WHERE pi.id = %s
        """,
        (izin_id,),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "İzin kaydı bulunamadı."}), 404

    pid = int(row["personel_id"])
    bas_tarih = row.get("baslangic_tarihi")
    if hasattr(bas_tarih, "year"):
        yil = bas_tarih.year
    else:
        try:
            yil = datetime.strptime(str(bas_tarih)[:10], "%Y-%m-%d").year
        except Exception:
            yil = date.today().year

    ozet = _izin_ozet_hesapla(pid, yil)
    tahmini = request.args.get("tahmini", "0") == "1"
    kalan = ozet["kalan"]
    izin_bakiye = {
        "toplam_hak": ozet["toplam_hak"],
        "yillik_kullanilan": round(float(ozet["kullanilan"]), 1),
        "kalan": kalan,
        "tahmini": tahmini,
    }
    if tahmini:
        tah_dk_param = request.args.get("tah_dk")
        if tah_dk_param:
            try:
                tah_dk = int(tah_dk_param)
                gec_gun_equiv = tah_dk / 480.0
                kalan_tahmini = max(
                    0.0,
                    float(ozet["toplam_hak"]) - float(ozet["kullanilan"]) - gec_gun_equiv,
                )
                izin_bakiye["kalan"] = kalan_tahmini
                izin_bakiye["kalan_tahmini"] = kalan_tahmini
                izin_bakiye["tah_dk"] = tah_dk
            except (ValueError, TypeError):
                pass
        else:
            gec_gun_net = int(ozet.get("gec_gun_net") or 0)
            gec_saat_net = int(ozet.get("gec_saat_net") or 0)
            gec_gun_equiv = gec_gun_net + (gec_saat_net / 8.0)
            kalan_tahmini = max(
                0.0,
                float(ozet["toplam_hak"]) - float(ozet["kullanilan"]) - gec_gun_equiv,
            )
            izin_bakiye["kalan"] = kalan_tahmini
            izin_bakiye["gec_gun_net"] = gec_gun_net
            izin_bakiye["gec_saat_net"] = gec_saat_net
            izin_bakiye["kalan_tahmini"] = kalan_tahmini
    kalan_dk_param = request.args.get("kalan_dk")
    if kalan_dk_param:
        try:
            kalan_dk = int(kalan_dk_param)
            kalan_gun = kalan_dk // 480
            kalan_saat = (kalan_dk % 480) // 60
            if kalan_saat > 0:
                izin_bakiye["kalan_str"] = f"{kalan_gun} gün {kalan_saat} saat"
            else:
                izin_bakiye["kalan_str"] = f"{kalan_gun} gün"
        except (ValueError, TypeError):
            pass
    data = _izin_pdf_data_from_row(dict(row), izin_bakiye)
    buf = io.BytesIO()
    try:
        izin_formu_olustur(data, buf)
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500

    pdf_bytes = buf.getvalue()
    import unicodedata
    ad_raw = str(row.get("ad_soyad") or "izin")
    ad_slug = unicodedata.normalize('NFKD', ad_raw)
    ad_slug = ''.join(c for c in ad_slug if not unicodedata.combining(c))
    ad_slug = ad_slug.replace(' ', '_')
    # Sadece ASCII alfanumerik + alt çizgi bırak
    import re
    ad_slug = re.sub(r'[^A-Za-z0-9_]', '', ad_slug) or "izin"
    fname = f"izin_{ad_slug}_{izin_id}.pdf"
    indir = request.args.get("indir", "").lower() in ("1", "true", "yes")
    disposition = "attachment" if indir else "inline"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{fname}"',
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ── Geç kalma listesi ───────────────────────────────────────────────────────

_TR_AYLAR = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


def _ay_key(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def _ay_adi(y: int, m: int) -> str:
    return f"{_TR_AYLAR[m - 1]} {y}"


def _gec_dakika_to_gun_saat_dk(toplam_dakika: int) -> dict:
    """8 saat = 1 gün (480 dk)."""
    td = max(0, int(toplam_dakika or 0))
    return {
        "toplam_dakika": td,
        "gun": td // 480,
        "saat": (td % 480) // 60,
        "dakika": td % 60,
    }


def _iter_ay_aralik(bas: date, adet: int):
    cur = bas.replace(day=1)
    for _ in range(adet):
        yield cur.year, cur.month
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)


def _is_is_gunu(d: date) -> bool:
    """Pazartesi–Cumartesi (Pazar hariç)."""
    return d.weekday() < 6


def _ay_is_gunleri(y: int, m: int) -> list:
    """Ayın tüm Pzt–Cmt günleri."""
    if m == 12:
        son = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        son = date(y, m + 1, 1) - timedelta(days=1)
    cur = date(y, m, 1)
    out = []
    while cur <= son:
        if _is_is_gunu(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _dk_to_hhmm(dk: int) -> str:
    dk = max(0, int(dk))
    return f"{dk // 60:02d}:{dk % 60:02d}"


def _gec_hesapla_dk(giris_dk: int, mesai_dk: int) -> int:
    """api_devam_kaydet ile aynı: fark > 5 ise geç, değilse 0."""
    fark = giris_dk - mesai_dk
    if fark <= 5:
        return 0
    return fark


def _gec_row_serialize(d: dict) -> dict:
    if d.get("tarih") and hasattr(d["tarih"], "isoformat"):
        d["tarih"] = d["tarih"].isoformat()[:10]
    if d.get("giris_saati") and hasattr(d["giris_saati"], "isoformat"):
        d["giris_saati"] = str(d["giris_saati"])[:5]
    if d.get("cikis_saati") and hasattr(d["cikis_saati"], "isoformat"):
        d["cikis_saati"] = str(d["cikis_saati"])[:5]
    return d


@bp.route("/api/gec/list")
@giris_gerekli
def api_gec_list():
    pid = request.args.get("personel_id")
    if not pid:
        return jsonify([])
    ay = request.args.get("ay")
    doldur = str(request.args.get("doldur") or "").lower() in ("1", "true", "yes")
    if ay:
        parts = str(ay).strip().split("-")
        if len(parts) < 2:
            return jsonify([])
        try:
            y, m = int(parts[0]), int(parts[1])
            bas_ay_gun = date(y, m, 1)
            if m == 12:
                son_ay_gun = date(y + 1, 1, 1) - timedelta(days=1)
            else:
                son_ay_gun = date(y, m + 1, 1) - timedelta(days=1)
        except (TypeError, ValueError):
            return jsonify([])
        rows = fetch_all(
            """SELECT id, tarih, giris_saati, cikis_saati, gec_dakika
               FROM devam_kayitlari
               WHERE personel_id=%s AND tarih >= %s AND tarih <= %s
               ORDER BY tarih ASC""",
            (pid, bas_ay_gun, son_ay_gun),
        )
        if not doldur:
            out = []
            for r in rows:
                d = _gec_row_serialize(dict(r))
                d["gercek"] = True
                out.append(d)
            return jsonify(out)

        personel = fetch_one("SELECT mesai_baslangic FROM personel WHERE id=%s", (pid,))
        mesai_dk = _saat_to_minutes((personel or {}).get("mesai_baslangic") or "09:00") or 9 * 60
        kayit_map = {}
        giris_dk_list = []
        for r in rows:
            d = dict(r)
            t = d["tarih"]
            key = t.isoformat()[:10] if hasattr(t, "isoformat") else str(t)[:10]
            kayit_map[key] = d
            g_dk = _saat_to_minutes(d.get("giris_saati"))
            if g_dk is not None:
                giris_dk_list.append(g_dk)
        ort_giris_dk = round(sum(giris_dk_list) / len(giris_dk_list)) if giris_dk_list else mesai_dk
        ort_giris_str = _dk_to_hhmm(ort_giris_dk)
        ort_gec = _gec_hesapla_dk(ort_giris_dk, mesai_dk)
        out = []
        for gun in _ay_is_gunleri(y, m):
            key = gun.isoformat()
            if key in kayit_map:
                d = _gec_row_serialize(dict(kayit_map[key]))
                d["gercek"] = True
            else:
                d = _gec_row_serialize({
                    "id": None,
                    "tarih": key,
                    "giris_saati": ort_giris_str,
                    "cikis_saati": None,
                    "gec_dakika": ort_gec,
                })
                d["gercek"] = False
            out.append(d)
        return jsonify(out)
    else:
        rows = fetch_all(
            "SELECT id, tarih, giris_saati, cikis_saati, gec_dakika FROM devam_kayitlari WHERE personel_id=%s AND gec_dakika > 0 ORDER BY tarih DESC LIMIT 100",
            (pid,),
        )
    out = []
    for r in rows:
        d = _gec_row_serialize(dict(r))
        d["gercek"] = True
        out.append(d)
    return jsonify(out)


@bp.route("/api/gec/guncelle", methods=["POST"])
@giris_gerekli
def api_gec_guncelle():
    try:
        data = request.json or request.form
        rid = int(data.get("id"))
        pid = int(data.get("personel_id"))
        gec_dakika = int(data.get("gec_dakika") or 0)
        giris = (data.get("giris_saati") or "").strip()[:8]
        if gec_dakika < 0:
            return jsonify({"ok": False, "mesaj": "Geç kalma negatif olamaz"}), 400
        row = fetch_one(
            "SELECT id FROM devam_kayitlari WHERE id=%s AND personel_id=%s",
            (rid, pid),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı"}), 404
        execute(
            "UPDATE devam_kayitlari SET gec_dakika=%s, giris_saati=%s WHERE id=%s AND personel_id=%s",
            (gec_dakika, giris or None, rid, pid),
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/gec/sil", methods=["POST"])
@giris_gerekli
def api_gec_sil():
    try:
        data = request.json or request.form
        rid = int(data.get("id"))
        pid = int(data.get("personel_id"))
        row = fetch_one(
            "SELECT id FROM devam_kayitlari WHERE id=%s AND personel_id=%s",
            (rid, pid),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı"}), 404
        execute("DELETE FROM devam_kayitlari WHERE id=%s AND personel_id=%s", (rid, pid))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400


@bp.route("/api/gec/aylik-grid")
@giris_gerekli
def api_gec_aylik_grid():
    pid_raw = request.args.get("personel_id")
    if not pid_raw:
        return jsonify({"ok": False, "mesaj": "personel_id zorunlu"}), 400
    pid = int(pid_raw)

    bas_param = _parse_date(request.args.get("baslangic_tarihi"))
    if bas_param:
        bas = bas_param
    else:
        bilgi = fetch_one(
            "SELECT ise_baslama_tarihi FROM personel_bilgi WHERE personel_id=%s",
            (pid,),
        )
        bas = None
        if bilgi and bilgi.get("ise_baslama_tarihi"):
            v = bilgi["ise_baslama_tarihi"]
            bas = v if hasattr(v, "year") else _parse_date(v)
        if not bas:
            p = fetch_one("SELECT giris_tarihi FROM personel WHERE id=%s", (pid,))
            if p and p.get("giris_tarihi"):
                v = p["giris_tarihi"]
                bas = v if hasattr(v, "year") else _parse_date(v)
        if not bas:
            today = date.today()
            bas = date(today.year, today.month, 1)

    bas_ay_raw = bas.replace(day=1)
    bugun = date.today()
    # İşe başlama tarihinin bir önceki yılının aynı ayından başla
    onceki_yil_bas = bas_ay_raw.replace(year=bugun.year - 1)
    # Eğer bu ay bugünden büyükse (henüz gelmemiş), bir yıl daha geri
    if onceki_yil_bas > bugun.replace(day=1):
        onceki_yil_bas = onceki_yil_bas.replace(year=onceki_yil_bas.year - 1)
    bas_ay = onceki_yil_bas
    bitis_ay = bugun.replace(day=1)
    ay_sayisi = (bitis_ay.year - bas_ay.year) * 12 + (bitis_ay.month - bas_ay.month) + 1
    ay_sayisi = min(ay_sayisi, 60)

    if bugun.month == 12:
        bit_exclusive = date(bugun.year + 1, 1, 1)
    else:
        bit_exclusive = date(bugun.year, bugun.month + 1, 1)

    rows = fetch_all(
        """
        SELECT DATE_TRUNC('month', tarih)::date AS ay,
               COALESCE(SUM(gec_dakika), 0) AS toplam_dakika,
               COUNT(*) AS kayit_gun_sayisi
        FROM devam_kayitlari
        WHERE personel_id = %s
          AND tarih >= %s
          AND tarih < %s
        GROUP BY 1
        ORDER BY 1
        """,
        (pid, bas_ay, bit_exclusive),
    ) or []

    ay_map = {}
    for r in rows:
        ay_val = r["ay"]
        if hasattr(ay_val, "year"):
            key = _ay_key(ay_val.year, ay_val.month)
        else:
            d = _parse_date(str(ay_val)[:10])
            key = _ay_key(d.year, d.month) if d else None
        if key:
            ay_map[key] = {
                "toplam_dakika": int(r.get("toplam_dakika") or 0),
                "kayit_gun_sayisi": int(r.get("kayit_gun_sayisi") or 0),
            }

    aylar = []
    for ay_y, ay_m in _iter_ay_aralik(bas_ay, ay_sayisi):
        key = _ay_key(ay_y, ay_m)
        rec = ay_map.get(key, {"toplam_dakika": 0, "kayit_gun_sayisi": 0})
        gercek_toplam_dakika = rec["toplam_dakika"]
        kayit_gun_sayisi = rec["kayit_gun_sayisi"]
        if ay_y == bugun.year and ay_m == bugun.month:
            is_gunu_sayisi = sum(
                1 for g in _ay_is_gunleri(ay_y, ay_m)
                if g <= bugun
            )
        else:
            is_gunu_sayisi = len(_ay_is_gunleri(ay_y, ay_m))
        if kayit_gun_sayisi > 0:
            ort_gunluk = gercek_toplam_dakika / kayit_gun_sayisi
            tahmini_toplam_dakika = round(ort_gunluk * is_gunu_sayisi)
        else:
            tahmini_toplam_dakika = 0
        gercek_parts = _gec_dakika_to_gun_saat_dk(gercek_toplam_dakika)
        tahmini_parts = _gec_dakika_to_gun_saat_dk(tahmini_toplam_dakika)
        aylar.append({
            "ay": key,
            "ay_adi": _ay_adi(ay_y, ay_m),
            **gercek_parts,
            "toplam_dakika": gercek_toplam_dakika,
            "gercek_toplam_dakika": gercek_toplam_dakika,
            "tahmini_toplam_dakika": tahmini_toplam_dakika,
            "kayit_gun_sayisi": kayit_gun_sayisi,
            "is_gunu_sayisi": is_gunu_sayisi,
            "tahmini_gun": tahmini_parts["gun"],
            "tahmini_saat": tahmini_parts["saat"],
            "tahmini_dakika": tahmini_parts["dakika"],
        })

    return jsonify({
        "ok": True,
        "personel_id": pid,
        "baslangic_tarihi": bas_ay.isoformat(),
        "bitis_tarihi": (bit_exclusive - timedelta(days=1)).isoformat(),
        "aylar": aylar,
    })


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
