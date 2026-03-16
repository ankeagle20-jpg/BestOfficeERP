# -*- coding: utf-8 -*-
"""
Toplantı Odası ve Randevu Takip Sistemi
Çakışma kontrolü, başlangıç<bitiş kontrolü, ücret hesaplama, durum renkleri.
"""
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, current_app
from flask_login import login_required
from db import fetch_all, fetch_one, execute, execute_returning
from datetime import datetime, date, time, timedelta
from decimal import Decimal

bp = Blueprint("randevu", __name__, url_prefix="/randevu")


def _get_musait_slotlar(tarih_str, oda_adi, tip=""):
    """Tarih ve oda için müsait 30 dk slotları döner (iç/public API ortak)."""
    try:
        gun = datetime.strptime(tarih_str, "%Y-%m-%d").date()
    except ValueError:
        return None, "Geçersiz tarih"
    bas_ts = datetime.combine(gun, time(8, 0))
    bitis_ts = datetime.combine(gun, time(20, 0))
    oda = (oda_adi or "").strip() or ODALAR[0]
    try:
        if tip == "gorusme":
            rows = fetch_all("""
                SELECT baslangic_zamani, bitis_zamani, randevu_tarihi, saat, sure_dakika
                FROM randevular
                WHERE COALESCE(NULLIF(TRIM(oda_adi), ''), oda) = %s
                  AND COALESCE(durum, '') != 'İptal'
                  AND randevu_tipi = 'gorusme'
                  AND (
                    (baslangic_zamani IS NOT NULL AND (baslangic_zamani::date) = %s)
                    OR (randevu_tarihi = %s)
                  )
            """, (oda, gun, gun))
        else:
            # Toplantılar sekmesi: aynı odadaki TÜM randevular (randevu + gorusme) slotları kapatsın
            rows = fetch_all("""
                SELECT baslangic_zamani, bitis_zamani, randevu_tarihi, saat, sure_dakika
                FROM randevular
                WHERE COALESCE(NULLIF(TRIM(oda_adi), ''), oda) = %s
                  AND COALESCE(durum, '') != 'İptal'
                  AND (
                    (baslangic_zamani IS NOT NULL AND (baslangic_zamani::date) = %s)
                    OR (randevu_tarihi = %s)
                  )
            """, (oda, gun, gun))
    except Exception:
        rows = fetch_all("""
            SELECT baslangic_zamani, bitis_zamani, randevu_tarihi, saat, sure_dakika
            FROM randevular
            WHERE COALESCE(NULLIF(TRIM(oda_adi), ''), oda) = %s
              AND COALESCE(durum, '') != 'İptal'
              AND (
                (baslangic_zamani IS NOT NULL AND (baslangic_zamani::date) = %s)
                OR (randevu_tarihi = %s)
              )
        """, (oda, gun, gun))

    def _naive_on_day(dt, fallback_date):
        if dt is None:
            return None
        if hasattr(dt, "date") and hasattr(dt, "time"):
            return datetime.combine(fallback_date, dt.time())
        return None

    dolu_araliklar = []
    for r in rows:
        b = r.get("baslangic_zamani")
        e = r.get("bitis_zamani")
        if b is None and r.get("randevu_tarihi") and r.get("saat"):
            b = datetime.combine(r["randevu_tarihi"], r["saat"] if hasattr(r["saat"], "hour") else time(9, 0))
            e = b + timedelta(minutes=int(r.get("sure_dakika") or 30))
        elif b is not None:
            b = _naive_on_day(b, gun)
            e = _naive_on_day(e, gun) if e is not None else (b + timedelta(minutes=int(r.get("sure_dakika") or 30)) if b else None)
        else:
            continue
        if b is None or e is None:
            continue
        dolu_araliklar.append((b, e))
    slot_dk = 30
    slotlar = []
    t = bas_ts
    while t < bitis_ts:
        bitis_slot = t + timedelta(minutes=slot_dk)
        cakisma = any(s[0] < bitis_slot and s[1] > t for s in dolu_araliklar)
        if not cakisma:
            slotlar.append({"start": t.strftime("%H:%M"), "end": bitis_slot.strftime("%H:%M")})
        t = bitis_slot
    return slotlar, oda

DURUMLAR = ["Beklemede", "Onaylandı", "Tamamlandı", "İptal"]
DURUM_RENK = {"Beklemede": "yellow", "Onaylandı": "green", "Tamamlandı": "cyan", "İptal": "red"}
ODALAR = ["turkuaz toplantı salonu", "hazır oda", "makam odası", "masa kullanımı"]


def _parse_ts(s):
    """'YYYY-MM-DDTHH:MM' veya 'YYYY-MM-DD HH:MM' -> datetime."""
    if not s:
        return None
    s = (s or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:19], fmt) if len(s) >= 10 else datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@bp.route("/")
@login_required
def index():
    """Randevu Takip ana sayfa: takvim + liste."""
    return render_template("randevu/index.html", durumlar=DURUMLAR, durum_renk=DURUM_RENK)


@bp.route("/calcom")
@login_required
def calcom():
    """Cal.com inline embed sayfası — Dashboard Randevu butonu buraya açılır."""
    import os
    # Cal.com inline embed — kullanıcılar bu ekrandan doğrudan randevu alır
    cal_link = os.environ.get("CAL_COM_CAL_LINK", "adem-dogan-cqdixt/30min")
    return render_template("randevu/calcom.html", cal_com_cal_link=cal_link)


@bp.route("/api/list")
@login_required
def api_list():
    """Tarih aralığına göre randevu listesi (takvim için)."""
    bas = request.args.get("bas")  # YYYY-MM-DD
    bitis = request.args.get("bitis")
    if not bas or not bitis:
        return jsonify({"error": "bas ve bitis (YYYY-MM-DD) gerekli"}), 400
    try:
        bas_d = datetime.strptime(bas, "%Y-%m-%d")
        bitis_d = datetime.strptime(bitis, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        return jsonify({"error": "Geçersiz tarih formatı"}), 400
    rows = fetch_all("""
        SELECT r.id, r.musteri_id, r.oda_adi, r.oda,
               r.baslangic_zamani, r.bitis_zamani,
               r.randevu_tarihi, r.saat, r.sure_dakika,
               r.toplam_ucret, r.pakete_dahil_mi, r.durum, r.notlar,
               c.name AS musteri_adi, c.phone
        FROM randevular r
        LEFT JOIN customers c ON c.id = r.musteri_id
        WHERE (r.baslangic_zamani IS NOT NULL AND r.baslangic_zamani >= %s AND r.baslangic_zamani < %s)
           OR (r.baslangic_zamani IS NULL AND r.randevu_tarihi IS NOT NULL AND r.randevu_tarihi >= %s AND r.randevu_tarihi < %s)
        ORDER BY COALESCE(r.baslangic_zamani, (r.randevu_tarihi + COALESCE(r.saat, '09:00'::time))::timestamptz)
    """, (bas_d, bitis_d, bas_d.date(), bitis_d.date()))
    out = []
    for r in rows:
        start = r.get("baslangic_zamani")
        if not start and r.get("randevu_tarihi") and r.get("saat"):
            start = datetime.combine(
                r["randevu_tarihi"] if hasattr(r["randevu_tarihi"], "date") else r["randevu_tarihi"],
                r["saat"] if hasattr(r["saat"], "hour") else time(9, 0)
            )
        end = r.get("bitis_zamani")
        if not end and start and r.get("sure_dakika"):
            start_dt = start if isinstance(start, datetime) else datetime.combine(start.date() if hasattr(start, "date") else date.today(), start.time() if hasattr(start, "time") else time(9, 0))
            end = start_dt + timedelta(minutes=int(r["sure_dakika"] or 60))
        oda = r.get("oda_adi") or r.get("oda") or ""
        out.append({
            "id": r["id"],
            "musteri_id": r["musteri_id"],
            "musteri_adi": r.get("musteri_adi") or "",
            "oda_adi": oda,
            "baslangic_zamani": start.isoformat() if start else None,
            "bitis_zamani": end.isoformat() if end else None,
            "toplam_ucret": float(r.get("toplam_ucret") or 0),
            "pakete_dahil_mi": bool(r.get("pakete_dahil_mi")),
            "durum": r.get("durum") or "Beklemede",
            "notlar": r.get("notlar") or "",
        })
    return jsonify(out)


@bp.route("/api/musteriler")
@login_required
def api_musteriler():
    """Müşteri/firma arama (combobox). name, phone ve notes alanlarında arar."""
    q = (request.args.get("q") or "").strip()[:80]
    if not q:
        rows = fetch_all("SELECT id, name, phone FROM customers ORDER BY name LIMIT 30")
    else:
        pattern = "%" + q + "%"
        rows = fetch_all(
            """SELECT id, name, phone FROM customers
               WHERE LOWER(name) LIKE LOWER(%s)
                  OR phone LIKE %s
                  OR LOWER(COALESCE(notes, '')) LIKE LOWER(%s)
               ORDER BY name LIMIT 30""",
            (pattern, pattern, pattern),
        )
    return jsonify([{"id": r["id"], "name": r.get("name") or "", "phone": r.get("phone") or ""} for r in rows])


@bp.route("/randevu-al")
@login_required
def randevu_al():
    """Kendi randevu sayfamız: takvim + müsait slotlar + form (ERP teması)."""
    return render_template("randevu/randevu_al.html")


@bp.route("/api/musait-slotlar")
@login_required
def api_musait_slotlar():
    """Seçilen tarih ve oda için müsait 30 dk'lık slotları döner (08:00–17:00). tip=gorusme ise sadece görüşmeler dikkate alınır."""
    tarih_str = request.args.get("tarih")
    oda_adi = (request.args.get("oda_adi") or "").strip()
    tip = (request.args.get("tip") or "").strip().lower()
    if tip not in ("gorusme", ""):
        tip = ""
    if not tarih_str:
        return jsonify({"error": "tarih (YYYY-MM-DD) gerekli"}), 400
    slotlar, oda = _get_musait_slotlar(tarih_str, oda_adi, tip)
    if slotlar is None:
        return jsonify({"error": oda}), 400
    return jsonify({"slotlar": slotlar, "tarih": tarih_str, "oda_adi": oda_adi or oda})


@bp.route("/api/gun-randevulari")
@login_required
def api_gun_randevulari():
    """Seçilen tarih ve oda için o günkü randevuları döner. tip=gorusme ise sadece görüşmeler."""
    tarih_str = request.args.get("tarih")
    oda_adi = (request.args.get("oda_adi") or "").strip() or ODALAR[0]
    tip = (request.args.get("tip") or "").strip().lower()
    if tip not in ("gorusme", ""):
        tip = ""
    if not tarih_str:
        return jsonify({"randevular": []})
    try:
        gun = datetime.strptime(tarih_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"randevular": []})
    try:
        if tip == "gorusme":
            # Görüşmeler sekmesi: sadece gorusme tipindekiler
            rows = fetch_all("""
                SELECT r.id, r.baslangic_zamani, r.bitis_zamani, r.randevu_tarihi, r.saat, r.sure_dakika,
                       c.name AS musteri_adi
                FROM randevular r
                LEFT JOIN customers c ON c.id = r.musteri_id
                WHERE COALESCE(NULLIF(TRIM(r.oda_adi), ''), r.oda) = %s
                  AND COALESCE(r.durum, '') != 'İptal'
                  AND r.randevu_tipi = 'gorusme'
                  AND (
                    (r.baslangic_zamani IS NOT NULL AND (r.baslangic_zamani::date) = %s)
                    OR (r.randevu_tarihi = %s)
                  )
                ORDER BY COALESCE(r.baslangic_zamani, (r.randevu_tarihi + COALESCE(r.saat, '09:00'::time))::timestamptz)
            """, (oda_adi, gun, gun))
        else:
            # Toplantılar sekmesi: aynı odadaki TÜM randevular (randevu+gorusme) listelensin ki çakışmalar görülsün
            rows = fetch_all("""
                SELECT r.id, r.baslangic_zamani, r.bitis_zamani, r.randevu_tarihi, r.saat, r.sure_dakika,
                       c.name AS musteri_adi
                FROM randevular r
                LEFT JOIN customers c ON c.id = r.musteri_id
                WHERE COALESCE(NULLIF(TRIM(r.oda_adi), ''), r.oda) = %s
                  AND COALESCE(r.durum, '') != 'İptal'
                  AND (
                    (r.baslangic_zamani IS NOT NULL AND (r.baslangic_zamani::date) = %s)
                    OR (r.randevu_tarihi = %s)
                  )
                ORDER BY COALESCE(r.baslangic_zamani, (r.randevu_tarihi + COALESCE(r.saat, '09:00'::time))::timestamptz)
            """, (oda_adi, gun, gun))
    except Exception:
        rows = fetch_all("""
            SELECT r.id, r.baslangic_zamani, r.bitis_zamani, r.randevu_tarihi, r.saat, r.sure_dakika,
                   c.name AS musteri_adi
            FROM randevular r
            LEFT JOIN customers c ON c.id = r.musteri_id
            WHERE COALESCE(NULLIF(TRIM(r.oda_adi), ''), r.oda) = %s
              AND COALESCE(r.durum, '') != 'İptal'
              AND (
                (r.baslangic_zamani IS NOT NULL AND (r.baslangic_zamani::date) = %s)
                OR (r.randevu_tarihi = %s)
              )
            ORDER BY COALESCE(r.baslangic_zamani, (r.randevu_tarihi + COALESCE(r.saat, '09:00'::time))::timestamptz)
        """, (oda_adi, gun, gun))
    out = []
    for r in rows:
        b = r.get("baslangic_zamani")
        e = r.get("bitis_zamani")
        if b is None and r.get("randevu_tarihi") and r.get("saat"):
            b = datetime.combine(r["randevu_tarihi"], r["saat"] if hasattr(r["saat"], "hour") else time(9, 0))
            e = b + timedelta(minutes=int(r.get("sure_dakika") or 30))
        elif b is not None:
            b = datetime.combine(gun, b.time()) if hasattr(b, "time") else b
            if e is not None and hasattr(e, "time"):
                e = datetime.combine(gun, e.time())
            else:
                e = b + timedelta(minutes=int(r.get("sure_dakika") or 30))
        else:
            continue
        bas_str = b.strftime("%H:%M") if hasattr(b, "strftime") else "09:00"
        bitis_str = e.strftime("%H:%M") if hasattr(e, "strftime") else "10:00"
        sure_dakika = int((e - b).total_seconds() // 60) if e and b else 0
        sure_saat = sure_dakika / 60.0
        if sure_saat == int(sure_saat):
            sure_metin = "{} saat".format(int(sure_saat))
        else:
            sure_metin = "{} saat".format(str(sure_saat).replace(".", ","))
        out.append({
            "id": r.get("id"),
            "musteri_adi": (r.get("musteri_adi") or "").strip() or "—",
            "baslangic": bas_str,
            "bitis": bitis_str,
            "sure_metin": sure_metin,
        })
    return jsonify({"randevular": out})


@bp.route("/api/aylik-doluluk")
@login_required
def api_aylik_doluluk():
    """Aydaki her gün için toplam dolu saat. Seçili oda. tip=gorusme ise sadece görüşmeler."""
    yil = request.args.get("yil")
    ay = request.args.get("ay")
    oda_adi = (request.args.get("oda_adi") or "").strip() or ODALAR[0]
    tip = (request.args.get("tip") or "").strip().lower()
    if tip not in ("gorusme", ""):
        tip = ""
    try:
        yil = int(yil or 0)
        ay = int(ay or 0)
        if not (1 <= ay <= 12) or not yil:
            return jsonify({"error": "yil ve ay (1-12) gerekli"}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "Geçersiz yil/ay"}), 400
    from calendar import monthrange
    _, son_gun = monthrange(yil, ay)
    bas_tarih = date(yil, ay, 1)
    bitis_tarih = date(yil, ay, son_gun)
    try:
        if tip == "gorusme":
            rows = fetch_all("""
                SELECT
                  COALESCE((baslangic_zamani::date), randevu_tarihi) AS gun,
                  COALESCE(
                    EXTRACT(EPOCH FROM (bitis_zamani - baslangic_zamani)) / 3600,
                    (COALESCE(sure_dakika, 30) / 60.0)
                  ) AS saat
                FROM randevular
                WHERE COALESCE(NULLIF(TRIM(oda_adi), ''), oda) = %s
                  AND COALESCE(durum, '') != 'İptal'
                  AND randevu_tipi = 'gorusme'
                  AND (
                    (baslangic_zamani IS NOT NULL AND (baslangic_zamani::date) >= %s AND (baslangic_zamani::date) <= %s)
                    OR (randevu_tarihi IS NOT NULL AND randevu_tarihi >= %s AND randevu_tarihi <= %s)
                  )
            """, (oda_adi, bas_tarih, bitis_tarih, bas_tarih, bitis_tarih))
        else:
            rows = fetch_all("""
                SELECT
                  COALESCE((baslangic_zamani::date), randevu_tarihi) AS gun,
                  COALESCE(
                    EXTRACT(EPOCH FROM (bitis_zamani - baslangic_zamani)) / 3600,
                    (COALESCE(sure_dakika, 30) / 60.0)
                  ) AS saat
                FROM randevular
                WHERE COALESCE(NULLIF(TRIM(oda_adi), ''), oda) = %s
                  AND COALESCE(durum, '') != 'İptal'
                  AND (randevu_tipi IS NULL OR randevu_tipi = 'randevu')
                  AND (
                    (baslangic_zamani IS NOT NULL AND (baslangic_zamani::date) >= %s AND (baslangic_zamani::date) <= %s)
                    OR (randevu_tarihi IS NOT NULL AND randevu_tarihi >= %s AND randevu_tarihi <= %s)
                  )
            """, (oda_adi, bas_tarih, bitis_tarih, bas_tarih, bitis_tarih))
    except Exception:
        rows = fetch_all("""
            SELECT
              COALESCE((baslangic_zamani::date), randevu_tarihi) AS gun,
              COALESCE(
                EXTRACT(EPOCH FROM (bitis_zamani - baslangic_zamani)) / 3600,
                (COALESCE(sure_dakika, 30) / 60.0)
              ) AS saat
            FROM randevular
            WHERE COALESCE(NULLIF(TRIM(oda_adi), ''), oda) = %s
              AND COALESCE(durum, '') != 'İptal'
              AND (
                (baslangic_zamani IS NOT NULL AND (baslangic_zamani::date) >= %s AND (baslangic_zamani::date) <= %s)
                OR (randevu_tarihi IS NOT NULL AND randevu_tarihi >= %s AND randevu_tarihi <= %s)
              )
        """, (oda_adi, bas_tarih, bitis_tarih, bas_tarih, bitis_tarih))
    by_date = {}
    for r in rows:
        g = r.get("gun")
        if g is None:
            continue
        g = g.isoformat()[:10] if hasattr(g, "isoformat") else str(g)[:10]
        s = float(r.get("saat") or 0)
        by_date[g] = round(by_date.get(g, 0) + s, 2)
    return jsonify(by_date)


@bp.route("/api/odalar")
@login_required
def api_odalar():
    """Oda listesi ve saatlik ücret. Sabit liste: turkuaz, hazır oda, makam odası, masa kullanımı."""
    for oda in ODALAR:
        try:
            execute(
                "INSERT INTO toplanti_odasi_fiyat (oda_adi, saatlik_ucret) VALUES (%s, 500) ON CONFLICT (oda_adi) DO NOTHING",
                (oda,),
            )
        except Exception:
            pass
    placeholders = ",".join(["%s"] * len(ODALAR))
    rows = fetch_all(
        "SELECT oda_adi, saatlik_ucret FROM toplanti_odasi_fiyat WHERE oda_adi IN (" + placeholders + ")",
        tuple(ODALAR),
    )
    by_adi = {r["oda_adi"]: r for r in rows}
    ordered = []
    for o in ODALAR:
        ordered.append(by_adi.get(o) or {"oda_adi": o, "saatlik_ucret": 500})
    return jsonify([{"oda_adi": r["oda_adi"], "saatlik_ucret": float(r.get("saatlik_ucret") or 0)} for r in ordered])


# ─── Müşteri self-service: giriş yapmadan randevu alma (paylaşılabilir link) ───
@bp.route("/book")
def book():
    """Paylaşılabilir randevu sayfası — giriş gerekmez. ?oda=... ile oda önseçili olabilir."""
    oda_param = (request.args.get("oda") or "").strip()
    return render_template("randevu/book.html", odalar=ODALAR, secili_oda=oda_param or None)


@bp.route("/api/public/odalar")
def api_public_odalar():
    """Oda listesi (public — giriş gerekmez)."""
    for oda in ODALAR:
        try:
            execute(
                "INSERT INTO toplanti_odasi_fiyat (oda_adi, saatlik_ucret) VALUES (%s, 500) ON CONFLICT (oda_adi) DO NOTHING",
                (oda,),
            )
        except Exception:
            pass
    placeholders = ",".join(["%s"] * len(ODALAR))
    rows = fetch_all(
        "SELECT oda_adi, saatlik_ucret FROM toplanti_odasi_fiyat WHERE oda_adi IN (" + placeholders + ")",
        tuple(ODALAR),
    )
    by_adi = {r["oda_adi"]: r for r in rows}
    ordered = [by_adi.get(o) or {"oda_adi": o, "saatlik_ucret": 500} for o in ODALAR]
    return jsonify([{"oda_adi": r["oda_adi"], "saatlik_ucret": float(r.get("saatlik_ucret") or 0)} for r in ordered])


@bp.route("/api/public/slotlar")
def api_public_slotlar():
    """Müsait slotlar (public — sadece randevu tipi, gorusme dahil değil)."""
    tarih_str = request.args.get("tarih")
    oda_adi = (request.args.get("oda_adi") or "").strip()
    if not tarih_str:
        return jsonify({"error": "tarih (YYYY-MM-DD) gerekli"}), 400
    slotlar, oda = _get_musait_slotlar(tarih_str, oda_adi, tip="randevu")
    if slotlar is None:
        return jsonify({"error": oda}), 400
    return jsonify({"slotlar": slotlar, "tarih": tarih_str, "oda_adi": oda_adi or oda})


@bp.route("/api/public/ekle", methods=["POST"])
def api_public_ekle():
    """Müşteri self-service: ad/email/telefon ile randevu oluştur (giriş gerekmez)."""
    data = request.get_json() or request.form
    oda_adi = (data.get("oda_adi") or data.get("oda") or "").strip()
    bas_str = data.get("baslangic_zamani") or data.get("baslangic")
    bitis_str = data.get("bitis_zamani") or data.get("bitis")
    ad_soyad = (data.get("ad_soyad") or data.get("name") or "").strip()[:200]
    email = (data.get("email") or "").strip()[:200]
    telefon = (data.get("telefon") or data.get("phone") or "").strip()[:50]
    notlar = (data.get("notlar") or data.get("notes") or "").strip()[:500]
    if not ad_soyad:
        return jsonify({"ok": False, "error": "Ad soyad gerekli."}), 400
    if not oda_adi:
        return jsonify({"ok": False, "error": "Oda seçiniz."}), 400
    baslangic = _parse_ts(bas_str)
    bitis = _parse_ts(bitis_str)
    if not baslangic or not bitis:
        return jsonify({"ok": False, "error": "Başlangıç ve bitiş tarih/saat giriniz."}), 400
    if baslangic >= bitis:
        return jsonify({"ok": False, "error": "Başlangıç bitişten önce olmalı."}), 400
    if baslangic < datetime.now():
        return jsonify({"ok": False, "error": "Geriye dönük randevu girilemez."}), 400
    if _cakisma_var(oda_adi, baslangic, bitis, None):
        return jsonify({"ok": False, "error": "Bu saatler için oda dolu."}), 400
    cust = execute_returning(
        """INSERT INTO customers (name, phone, email, notes) VALUES (%s, %s, %s, %s) RETURNING id""",
        (ad_soyad, telefon or None, email or None, notlar or None),
    )
    if not cust or not cust.get("id"):
        return jsonify({"ok": False, "error": "Kayıt oluşturulamadı."}), 500
    musteri_id = cust["id"]
    fiyat = fetch_one("SELECT saatlik_ucret FROM toplanti_odasi_fiyat WHERE oda_adi = %s", (oda_adi,))
    saatlik = Decimal(str(fiyat["saatlik_ucret"])) if fiyat and fiyat.get("saatlik_ucret") is not None else Decimal("500")
    sure_saat = (bitis - baslangic).total_seconds() / 3600
    toplam_ucret = (saatlik * Decimal(str(sure_saat))).quantize(Decimal("0.01"))
    randevu_tarihi = baslangic.date() if hasattr(baslangic, "date") else baslangic
    saat = baslangic.time() if hasattr(baslangic, "time") else None
    sure_dakika = int((bitis - baslangic).total_seconds() // 60)
    try:
        row = execute_returning("""
            INSERT INTO randevular (musteri_id, oda_adi, oda, randevu_tarihi, saat, sure_dakika, baslangic_zamani, bitis_zamani, toplam_ucret, pakete_dahil_mi, durum, notlar, randevu_tipi)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, 'Beklemede', %s, 'randevu')
            RETURNING id, baslangic_zamani, bitis_zamani
        """, (musteri_id, oda_adi, oda_adi, randevu_tarihi, saat, sure_dakika, baslangic, bitis, toplam_ucret, notlar))
    except Exception:
        row = execute_returning("""
            INSERT INTO randevular (musteri_id, oda_adi, oda, randevu_tarihi, saat, sure_dakika, baslangic_zamani, bitis_zamani, toplam_ucret, pakete_dahil_mi, durum, notlar)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, 'Beklemede', %s)
            RETURNING id, baslangic_zamani, bitis_zamani
        """, (musteri_id, oda_adi, oda_adi, randevu_tarihi, saat, sure_dakika, baslangic, bitis, toplam_ucret, notlar))
    if not row:
        return jsonify({"ok": False, "error": "Randevu eklenemedi."}), 500
    rid = row["id"]
    bas_fmt = baslangic.strftime("%d.%m.%Y %H:%M") if baslangic else ""
    bit_fmt = bitis.strftime("%H:%M") if bitis else ""
    if email:
        try:
            from mail_utils import send_randevu_onay
            send_randevu_onay(email, ad_soyad, oda_adi, bas_fmt, bit_fmt, randevu_id=rid)
        except Exception:
            pass
    return jsonify({"ok": True, "id": rid, "message": "Randevunuz alındı. Onay e-postası gönderildi."})


@bp.route("/iptal/<int:rid>")
def iptal_sayfa(rid):
    """Randevu iptal sayfası (e-postadaki link — giriş gerekmez)."""
    r = fetch_one("SELECT r.id, r.oda_adi, r.oda, r.baslangic_zamani, r.bitis_zamani, r.durum, c.name FROM randevular r LEFT JOIN customers c ON c.id = r.musteri_id WHERE r.id = %s", (rid,))
    if not r or (r.get("durum") or "") == "İptal":
        return render_template("randevu/iptal.html", bulunamadi=True)
    return render_template("randevu/iptal.html", randevu=r, bulunamadi=False)


@bp.route("/api/public/iptal/<int:rid>", methods=["POST"])
def api_public_iptal(rid):
    """Randevu iptal (e-postadaki link veya self-service — giriş gerekmez)."""
    r = fetch_one("SELECT id, musteri_id, oda_adi, oda, baslangic_zamani, bitis_zamani, durum FROM randevular WHERE id = %s", (rid,))
    if not r:
        return jsonify({"ok": False, "error": "Randevu bulunamadı."}), 404
    if (r.get("durum") or "") == "İptal":
        return jsonify({"ok": True, "message": "Randevu zaten iptal."})
    cust = fetch_one("SELECT email, name FROM customers WHERE id = %s", (r["musteri_id"],)) if r.get("musteri_id") else None
    oda = r.get("oda_adi") or r.get("oda") or ""
    bas_fmt = r["baslangic_zamani"].strftime("%d.%m.%Y %H:%M") if r.get("baslangic_zamani") else ""
    bit_fmt = r["bitis_zamani"].strftime("%H:%M") if r.get("bitis_zamani") else ""
    execute("UPDATE randevular SET durum = 'İptal' WHERE id = %s", (rid,))
    if cust and cust.get("email"):
        try:
            from mail_utils import send_randevu_iptal
            send_randevu_iptal(cust["email"], cust.get("name") or "", oda, bas_fmt, bit_fmt)
        except Exception:
            pass
    return jsonify({"ok": True, "message": "Randevu iptal edildi."})


@bp.route("/api/odalar/guncelle", methods=["POST"], endpoint="api_odalar_guncelle")
@login_required
def api_odalar_guncelle():
    """Oda saatlik ücretini güncelle."""
    data = request.get_json() or {}
    oda_adi = (data.get("oda_adi") or "").strip()
    try:
        saatlik = Decimal(str(data.get("saatlik_ucret", 0)))
    except Exception:
        return jsonify({"ok": False, "error": "Geçersiz ücret."}), 400
    if not oda_adi:
        return jsonify({"ok": False, "error": "Oda adı gerekli."}), 400
    execute(
        "INSERT INTO toplanti_odasi_fiyat (oda_adi, saatlik_ucret) VALUES (%s, %s) ON CONFLICT (oda_adi) DO UPDATE SET saatlik_ucret = EXCLUDED.saatlik_ucret",
        (oda_adi, saatlik)
    )
    return jsonify({"ok": True, "oda_adi": oda_adi, "saatlik_ucret": float(saatlik)})


def _cakisma_var(oda_adi, baslangic, bitis, haric_id=None):
    """Aynı oda ve zaman aralığında başka randevu var mı?"""
    sql = """
        SELECT 1 FROM randevular
        WHERE COALESCE(NULLIF(TRIM(oda_adi), ''), oda) = %s
          AND COALESCE(durum, '') != 'İptal'
          AND (
            COALESCE(baslangic_zamani, (randevu_tarihi + COALESCE(saat, '09:00'::time))::timestamptz) < %s
            AND COALESCE(bitis_zamani, (randevu_tarihi + COALESCE(saat, '09:00'::time))::timestamptz + (COALESCE(sure_dakika, 60) || ' minutes')::interval) > %s
          )
    """
    params = [oda_adi, bitis, baslangic]
    if haric_id is not None:
        sql += " AND id != %s"
        params.append(haric_id)
    row = fetch_one(sql, tuple(params))
    return row is not None


@bp.route("/api/ekle", methods=["POST"])
@login_required
def api_ekle():
    """Yeni randevu ekle: başlangıç<bitiş, çakışma, ücret hesaplama."""
    data = request.get_json() or {}
    try:
        musteri_id = int(data.get("musteri_id") or 0)
    except (TypeError, ValueError):
        musteri_id = None
    oda_adi = (data.get("oda_adi") or "").strip() or (data.get("oda") or "").strip()
    bas_str = data.get("baslangic_zamani") or data.get("baslangic")
    bitis_str = data.get("bitis_zamani") or data.get("bitis")
    pakete_dahil = data.get("pakete_dahil_mi", False)
    notlar = (data.get("notlar") or "").strip()[:500]
    randevu_tipi = (data.get("randevu_tipi") or "randevu").strip().lower()
    if randevu_tipi not in ("randevu", "gorusme"):
        randevu_tipi = "randevu"

    gorusme_kisi = (data.get("gorusme_kisi_adi") or data.get("gorusme_kisi") or "").strip()[:200]
    gorusme_sirket = (data.get("gorusme_sirket") or "").strip()[:200]
    gorusme_telefon = (data.get("gorusme_telefon") or "").strip()[:50]

    if randevu_tipi == "gorusme" and (not musteri_id or musteri_id <= 0):
        if not gorusme_kisi:
            return jsonify({"ok": False, "error": "Görüşülecek kişi adını giriniz (Kişi bilgisi)."}), 400
        cust = execute_returning(
            """INSERT INTO customers (name, phone, notes) VALUES (%s, %s, %s) RETURNING id""",
            (gorusme_kisi, gorusme_telefon or None, ("Şirket: " + gorusme_sirket) if gorusme_sirket else None),
        )
        if not cust or not cust.get("id"):
            return jsonify({"ok": False, "error": "Görüşme kişisi kaydı oluşturulamadı."}), 500
        musteri_id = cust["id"]
    elif not musteri_id or musteri_id <= 0:
        return jsonify({"ok": False, "error": "Müşteri seçiniz."}), 400

    if not oda_adi:
        return jsonify({"ok": False, "error": "Oda seçiniz."}), 400

    baslangic = _parse_ts(bas_str)
    bitis = _parse_ts(bitis_str)
    if not baslangic or not bitis:
        return jsonify({"ok": False, "error": "Başlangıç ve bitiş tarih/saat giriniz."}), 400
    if baslangic >= bitis:
        return jsonify({"ok": False, "error": "Başlangıç saati bitiş saatinden önce olmalıdır."}), 400
    if baslangic < datetime.now():
        return jsonify({"ok": False, "error": "Geriye dönük randevu girilemez."}), 400

    if _cakisma_var(oda_adi, baslangic, bitis, None):
        return jsonify({"ok": False, "error": "Bu saatler arasında oda rezerve edilmiştir."}), 400

    # Ücret: pakete_dahil_mi ise 0, değilse süre * saatlik ücret
    toplam_ucret = Decimal("0")
    if not pakete_dahil:
        fiyat = fetch_one("SELECT saatlik_ucret FROM toplanti_odasi_fiyat WHERE oda_adi = %s", (oda_adi,))
        saatlik = Decimal(str(fiyat["saatlik_ucret"])) if fiyat and fiyat.get("saatlik_ucret") is not None else Decimal("500")
        sure_saat = (bitis - baslangic).total_seconds() / 3600
        toplam_ucret = (saatlik * Decimal(str(sure_saat))).quantize(Decimal("0.01"))

    randevu_tarihi = baslangic.date() if hasattr(baslangic, "date") else baslangic
    saat = baslangic.time() if hasattr(baslangic, "time") else None
    sure_dakika = int((bitis - baslangic).total_seconds() // 60) if bitis and baslangic else 30
    try:
        row = execute_returning("""
            INSERT INTO randevular (musteri_id, oda_adi, oda, randevu_tarihi, saat, sure_dakika, baslangic_zamani, bitis_zamani, toplam_ucret, pakete_dahil_mi, durum, notlar, randevu_tipi)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Beklemede', %s, %s)
            RETURNING id, baslangic_zamani, bitis_zamani, toplam_ucret, durum
        """, (musteri_id, oda_adi, oda_adi, randevu_tarihi, saat, sure_dakika, baslangic, bitis, toplam_ucret, bool(pakete_dahil), notlar, randevu_tipi))
    except Exception:
        row = execute_returning("""
            INSERT INTO randevular (musteri_id, oda_adi, oda, randevu_tarihi, saat, sure_dakika, baslangic_zamani, bitis_zamani, toplam_ucret, pakete_dahil_mi, durum, notlar)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Beklemede', %s)
            RETURNING id, baslangic_zamani, bitis_zamani, toplam_ucret, durum
        """, (musteri_id, oda_adi, oda_adi, randevu_tarihi, saat, sure_dakika, baslangic, bitis, toplam_ucret, bool(pakete_dahil), notlar))
    if not row:
        return jsonify({"ok": False, "error": "Kayıt eklenemedi."}), 500
    first_id = row["id"]
    recurrence_rule = (data.get("recurrence_rule") or "").strip().lower()
    recurrence_end_str = (data.get("recurrence_end_date") or "").strip()
    recurrence_end = None
    if recurrence_end_str:
        try:
            recurrence_end = datetime.strptime(recurrence_end_str[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    if recurrence_rule in ("weekly", "monthly") and recurrence_end and hasattr(baslangic, "date") and recurrence_end > baslangic.date():
        delta = timedelta(days=7) if recurrence_rule == "weekly" else timedelta(days=30)
        if recurrence_rule == "monthly":
            cur_bas, cur_bit = baslangic, bitis
            while True:
                cur_bas += delta
                cur_bit += delta
                if cur_bas.date() > recurrence_end:
                    break
                if cur_bas < datetime.now():
                    continue
                if _cakisma_var(oda_adi, cur_bas, cur_bit, None):
                    continue
                rt = cur_bas.date()
                st = cur_bas.time() if hasattr(cur_bas, "time") else None
                sd = int((cur_bit - cur_bas).total_seconds() // 60)
                try:
                    execute("""INSERT INTO randevular (musteri_id, oda_adi, oda, randevu_tarihi, saat, sure_dakika, baslangic_zamani, bitis_zamani, toplam_ucret, pakete_dahil_mi, durum, notlar, randevu_tipi, parent_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Beklemede', %s, %s, %s)""",
                        (musteri_id, oda_adi, oda_adi, rt, st, sd, cur_bas, cur_bit, toplam_ucret, bool(pakete_dahil), notlar, randevu_tipi, first_id))
                except Exception:
                    execute("""INSERT INTO randevular (musteri_id, oda_adi, oda, randevu_tarihi, saat, sure_dakika, baslangic_zamani, bitis_zamani, toplam_ucret, pakete_dahil_mi, durum, notlar, parent_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Beklemede', %s, %s)""",
                        (musteri_id, oda_adi, oda_adi, rt, st, sd, cur_bas, cur_bit, toplam_ucret, bool(pakete_dahil), notlar, first_id))
        else:
            cur_bas, cur_bit = baslangic, bitis
            while True:
                cur_bas += delta
                cur_bit += delta
                if cur_bas.date() > recurrence_end:
                    break
                if cur_bas < datetime.now():
                    continue
                if _cakisma_var(oda_adi, cur_bas, cur_bit, None):
                    continue
                rt = cur_bas.date()
                st = cur_bas.time() if hasattr(cur_bas, "time") else None
                sd = int((cur_bit - cur_bas).total_seconds() // 60)
                try:
                    execute("""INSERT INTO randevular (musteri_id, oda_adi, oda, randevu_tarihi, saat, sure_dakika, baslangic_zamani, bitis_zamani, toplam_ucret, pakete_dahil_mi, durum, notlar, randevu_tipi, parent_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Beklemede', %s, %s, %s)""",
                        (musteri_id, oda_adi, oda_adi, rt, st, sd, cur_bas, cur_bit, toplam_ucret, bool(pakete_dahil), notlar, randevu_tipi, first_id))
                except Exception:
                    execute("""INSERT INTO randevular (musteri_id, oda_adi, oda, randevu_tarihi, saat, sure_dakika, baslangic_zamani, bitis_zamani, toplam_ucret, pakete_dahil_mi, durum, notlar, parent_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Beklemede', %s, %s)""",
                        (musteri_id, oda_adi, oda_adi, rt, st, sd, cur_bas, cur_bit, toplam_ucret, bool(pakete_dahil), notlar, first_id))
    cust = fetch_one("SELECT email, name FROM customers WHERE id = %s", (musteri_id,))
    if cust and cust.get("email"):
        try:
            from mail_utils import send_randevu_onay
            bas_fmt = baslangic.strftime("%d.%m.%Y %H:%M") if baslangic else ""
            bit_fmt = bitis.strftime("%H:%M") if bitis else ""
            send_randevu_onay(cust["email"], cust.get("name") or "", oda_adi, bas_fmt, bit_fmt, randevu_id=first_id)
        except Exception:
            pass
    try:
        from mail_utils import trigger_randevu_webhook
        trigger_randevu_webhook("randevu.created", {"id": first_id, "musteri_id": musteri_id, "oda_adi": oda_adi, "baslangic_zamani": baslangic.isoformat() if baslangic else None, "bitis_zamani": bitis.isoformat() if bitis else None})
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "id": first_id,
        "toplam_ucret": float(row["toplam_ucret"]),
        "durum": row["durum"],
    })


@bp.route("/api/mevcut-randevu")
@login_required
def api_mevcut_randevu():
    """Seçilen tarih + oda + müşteri için o güne ait (iptal olmayan) tek randevu varsa döner."""
    tarih_str = request.args.get("tarih")
    oda_adi = (request.args.get("oda_adi") or "").strip() or ODALAR[0]
    musteri_id = request.args.get("musteri_id")
    if not tarih_str or not musteri_id:
        return jsonify({})
    try:
        gun = datetime.strptime(tarih_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({})
    try:
        musteri_id = int(musteri_id)
    except (TypeError, ValueError):
        return jsonify({})
    row = fetch_one("""
        SELECT id, baslangic_zamani, bitis_zamani, randevu_tarihi, saat, sure_dakika
        FROM randevular
        WHERE musteri_id = %s
          AND COALESCE(NULLIF(TRIM(oda_adi), ''), oda) = %s
          AND COALESCE(durum, '') != 'İptal'
          AND (
            (baslangic_zamani IS NOT NULL AND (baslangic_zamani::date) = %s)
            OR (randevu_tarihi = %s)
          )
        ORDER BY COALESCE(baslangic_zamani, (randevu_tarihi + COALESCE(saat, '09:00'::time))::timestamptz)
        LIMIT 1
    """, (musteri_id, oda_adi, gun, gun))
    if not row:
        return jsonify({})
    bas = row.get("baslangic_zamani")
    bitis = row.get("bitis_zamani")
    if not bas and row.get("randevu_tarihi") and row.get("saat"):
        bas = datetime.combine(row["randevu_tarihi"], row["saat"] if hasattr(row["saat"], "hour") else time(9, 0))
        bitis = bas + timedelta(minutes=int(row.get("sure_dakika") or 30))
    return jsonify({
        "id": row["id"],
        "baslangic_zamani": bas.isoformat() if bas else None,
        "bitis_zamani": bitis.isoformat() if bitis else None,
    })


@bp.route("/api/saat-guncelle/<int:rid>", methods=["POST"])
@login_required
def api_saat_guncelle(rid):
    """Randevunun başlangıç/bitiş saatini günceller (aynı gün taşıma)."""
    data = request.get_json() or {}
    bas_str = data.get("baslangic_zamani") or data.get("baslangic")
    bitis_str = data.get("bitis_zamani") or data.get("bitis")
    baslangic = _parse_ts(bas_str)
    bitis = _parse_ts(bitis_str)
    if not baslangic or not bitis:
        return jsonify({"ok": False, "error": "Başlangıç ve bitiş giriniz."}), 400
    if baslangic >= bitis:
        return jsonify({"ok": False, "error": "Başlangıç bitişten önce olmalı."}), 400
    if baslangic < datetime.now():
        return jsonify({"ok": False, "error": "Geriye dönük randevu girilemez."}), 400
    r = fetch_one("SELECT id, oda_adi, oda, pakete_dahil_mi FROM randevular WHERE id = %s AND COALESCE(durum, '') != 'İptal'", (rid,))
    if not r:
        return jsonify({"ok": False, "error": "Randevu bulunamadı."}), 404
    oda = (r.get("oda_adi") or r.get("oda") or "").strip()
    if _cakisma_var(oda, baslangic, bitis, haric_id=rid):
        return jsonify({"ok": False, "error": "Bu saatler arasında oda dolu."}), 400
    toplam_ucret = Decimal("0")
    if not r.get("pakete_dahil_mi"):
        fiyat = fetch_one("SELECT saatlik_ucret FROM toplanti_odasi_fiyat WHERE oda_adi = %s", (oda,))
        saatlik = Decimal(str(fiyat["saatlik_ucret"])) if fiyat and fiyat.get("saatlik_ucret") is not None else Decimal("500")
        sure_saat = (bitis - baslangic).total_seconds() / 3600
        toplam_ucret = (saatlik * Decimal(str(sure_saat))).quantize(Decimal("0.01"))
    randevu_tarihi = baslangic.date() if hasattr(baslangic, "date") else baslangic
    saat = baslangic.time() if hasattr(baslangic, "time") else None
    sure_dakika = int((bitis - baslangic).total_seconds() // 60)
    execute("""
        UPDATE randevular
        SET baslangic_zamani = %s, bitis_zamani = %s, randevu_tarihi = %s, saat = %s, sure_dakika = %s, toplam_ucret = %s
        WHERE id = %s
    """, (baslangic, bitis, randevu_tarihi, saat, sure_dakika, toplam_ucret, rid))
    return jsonify({"ok": True, "id": rid})


@bp.route("/api/sil/<int:rid>", methods=["POST"])
@login_required
def api_sil(rid):
    """Randevuyu tamamen siler (iptal / kayıt silme). İptal e-postası gönderilir."""
    r = fetch_one("SELECT musteri_id, oda_adi, oda, baslangic_zamani, bitis_zamani FROM randevular WHERE id = %s", (rid,))
    if not r:
        return jsonify({"ok": False, "error": "Randevu bulunamadı."}), 404
    cust = fetch_one("SELECT email, name FROM customers WHERE id = %s", (r["musteri_id"],)) if r and r.get("musteri_id") else None
    oda = (r.get("oda_adi") or r.get("oda") or "") if r else ""
    bas_fmt = r["baslangic_zamani"].strftime("%d.%m.%Y %H:%M") if r and r.get("baslangic_zamani") else ""
    bit_fmt = r["bitis_zamani"].strftime("%H:%M") if r and r.get("bitis_zamani") else ""
    execute("DELETE FROM randevular WHERE id = %s", (rid,))
    if cust and cust.get("email"):
        try:
            from mail_utils import send_randevu_iptal
            send_randevu_iptal(cust["email"], cust.get("name") or "", oda, bas_fmt, bit_fmt)
        except Exception:
            pass
    try:
        from mail_utils import trigger_randevu_webhook
        trigger_randevu_webhook("randevu.deleted", {"id": rid, "oda_adi": oda})
    except Exception:
        pass
    return jsonify({"ok": True, "id": rid})


@bp.route("/cron/hatirlatma")
def cron_hatirlatma():
    """Yarının randevularına hatırlatma e-postası gönderir. Cron ile günlük çağrılabilir (örn. 09:00)."""
    from datetime import date as date_type
    yarin = date_type.today() + timedelta(days=1)
    rows = fetch_all("""
        SELECT r.id, r.oda_adi, r.oda, r.baslangic_zamani, r.bitis_zamani, r.reminder_sent,
               c.email, c.name
        FROM randevular r
        JOIN customers c ON c.id = r.musteri_id
        WHERE COALESCE(r.durum, '') != 'İptal'
          AND (r.baslangic_zamani::date = %s OR r.randevu_tarihi = %s)
          AND (r.reminder_sent IS NULL OR r.reminder_sent = FALSE)
          AND c.email IS NOT NULL AND c.email != ''
    """, (yarin, yarin))
    sent = 0
    for r in rows:
        try:
            from mail_utils import send_randevu_hatirlatma
            oda = r.get("oda_adi") or r.get("oda") or ""
            bas = r["baslangic_zamani"].strftime("%d.%m.%Y %H:%M") if r.get("baslangic_zamani") else ""
            bit = r["bitis_zamani"].strftime("%H:%M") if r.get("bitis_zamani") else ""
            if send_randevu_hatirlatma(r["email"], r.get("name") or "", oda, bas, bit):
                execute("UPDATE randevular SET reminder_sent = TRUE WHERE id = %s", (r["id"],))
                sent += 1
        except Exception:
            pass
    return jsonify({"ok": True, "sent": sent, "date": str(yarin)})


@bp.route("/api/guncelle/<int:rid>", methods=["POST"])
@login_required
def api_guncelle(rid):
    """Randevu güncelle (durum vb.). Tamamlandı -> Faturalandırılacak Hizmetler listesine ekle."""
    data = request.get_json() or {}
    durum = (data.get("durum") or "").strip()
    if durum not in DURUMLAR:
        return jsonify({"ok": False, "error": "Geçersiz durum."}), 400

    execute("UPDATE randevular SET durum = %s WHERE id = %s", (durum, rid))
    if durum == "Tamamlandı":
        execute("UPDATE randevular SET faturalandi = FALSE WHERE id = %s", (rid,))
        # Faturalandırılacak Hizmetler listesine ekle (zaten varsa tekrar ekleme)
        r = fetch_one("SELECT musteri_id, toplam_ucret, oda_adi FROM randevular WHERE id = %s", (rid,))
        if r:
            existing = fetch_one(
                "SELECT 1 FROM faturalandirilacak_hizmetler WHERE kaynak = 'randevu' AND kaynak_id = %s AND islendi = FALSE",
                (rid,)
            )
            if not existing:
                aciklama = "Randevu: " + (r.get("oda_adi") or r.get("oda") or "Toplantı odası")
                execute(
                    """INSERT INTO faturalandirilacak_hizmetler (kaynak, kaynak_id, musteri_id, aciklama, tutar)
                       VALUES ('randevu', %s, %s, %s, %s)""",
                    (rid, r.get("musteri_id"), aciklama, r.get("toplam_ucret") or 0)
                )
    return jsonify({"ok": True, "durum": durum})
