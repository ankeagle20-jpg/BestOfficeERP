"""
Ofisbir İlan Robotu — Hazır/Sanal Ofis ilan girişi ve AI metin üretimi.
"""
import os
import sys
import threading
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from auth import giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning, ensure_office_rentals

_web_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _web_root not in sys.path:
    sys.path.insert(0, _web_root)
try:
    from gemini_helper import analiz_yap as gemini_analiz_yap, GEMINI_AVAILABLE
except ImportError:
    GEMINI_AVAILABLE = False
    def gemini_analiz_yap(*args, **kwargs):
        return False, "Gemini modülü yüklenemedi."

def _run_robot_async(platform: str, baslik: str, fiyat: str, eids_no: str, aciklama: str, resim_yollari: list):
    """Arka planda robotu çalıştırır (thread)."""
    try:
        from robot_surucu import run_platform
        ok, mesaj = run_platform(platform, baslik, fiyat, eids_no or None, aciklama, resim_yollari or [], headless=False)
        if not ok:
            import logging
            logging.getLogger("ilan_robotu").warning("Robot %s: %s", platform, mesaj)
    except Exception as e:
        import logging
        logging.getLogger("ilan_robotu").exception("Robot %s hata: %s", platform, e)

bp = Blueprint("ilan_robotu", __name__)


OFIS_TURleri = ["Sanal Ofis", "Hazır Ofis", "Paylaşımlı Masa"]


@bp.route("/")
@giris_gerekli
def index():
    """İlan Robotu form sayfası."""
    ensure_office_rentals()
    ilanlar = fetch_all(
        """SELECT id, baslik, ofis_turu, il, ilce, adres, aylik_fiyat, eids_yetki_no, aciklama, aciklama_ai,
           yasal_adres, sekreterya_karsilama, posta_takibi, toplanti_odasi,
           sinirsiz_cay_kahve, fiber_internet, numara_0850_tahsisi, anlik_bildirim_sistemi,
           misafir_agirlama, mutfak_erisimi, temizlik_hizmeti, status, created_at
           FROM office_rentals ORDER BY id DESC LIMIT 50"""
    )
    return render_template(
        "ilan_robotu/ilan_robotu.html",
        ofis_turleri=OFIS_TURleri,
        ilanlar=ilanlar or [],
        gemini_available=GEMINI_AVAILABLE,
    )


@bp.route("/api/ai-metin", methods=["POST"])
@giris_gerekli
def api_ai_metin():
    """Form verilerine göre Gemini ile ilan metni üretir (Prestijli İş Adresi + Düşük Maliyet vurgulu)."""
    data = request.get_json(silent=True) or request.form
    ofis_turu = (data.get("ofis_turu") or "").strip()
    baslik = (data.get("baslik") or "").strip()
    il = (data.get("il") or "").strip()
    ilce = (data.get("ilce") or "").strip()
    adres = (data.get("adres") or "").strip()
    aylik_fiyat = (data.get("aylik_fiyat") or "").strip()
    def _bool(v):
        return v in (True, 1, "1", "on", "true")
    yasal_adres = _bool(data.get("yasal_adres"))
    sekreterya = _bool(data.get("sekreterya_karsilama"))
    posta_takibi = _bool(data.get("posta_takibi"))
    toplanti_odasi = _bool(data.get("toplanti_odasi"))
    sinirsiz_cay = _bool(data.get("sinirsiz_cay_kahve"))
    fiber = _bool(data.get("fiber_internet"))
    numara_0850 = _bool(data.get("numara_0850_tahsisi"))
    anlik_bildirim = _bool(data.get("anlik_bildirim_sistemi"))
    misafir = _bool(data.get("misafir_agirlama"))
    mutfak = _bool(data.get("mutfak_erisimi"))
    temizlik = _bool(data.get("temizlik_hizmeti"))

    context = (
        f"Ofis türü: {ofis_turu or 'Belirtilmedi'}\n"
        f"Başlık: {baslik or '—'}\n"
        f"Lokasyon: {il} {ilce}\nAdres: {adres or '—'}\n"
        f"Aylık fiyat: {aylik_fiyat or '—'} TL\n"
        f"Dahil hizmetler: Yasal Adres={yasal_adres}, Sekreterya={sekreterya}, Posta Takibi={posta_takibi}, Toplantı Odası={toplanti_odasi}\n"
        f"Hizmete dahil ücretsiz: Sınırsız Çay-Kahve={sinirsiz_cay}, Fiber İnternet={fiber}, 0850 Numara={numara_0850}, "
        f"Anlık Bildirim={anlik_bildirim}, Misafir Ağırlama={misafir}, Mutfak={mutfak}, Temizlik={temizlik}"
    )
    soru = (
        "Bu bilgilere göre (Ofis türü, başlık, lokasyon, dahil hizmetler) **prestijli iş adresi** ve **düşük maliyet** vurgulu, "
        "profesyonel ve satış odaklı bir ilan açıklaması yaz. 2–3 paragraf, Türkçe, net olsun. Emlak ilanı tonunda yaz. "
        "Metnin en sonuna, ilanı arayanların bulması için uygun 8–15 adet #etiket (hashtag) ekle; Türkçe ve konuya uygun olsun "
        "(ör: #sanalofis #istanbul #merkeziadres gibi)."
    )
    ok, metin = gemini_analiz_yap(context, soru)
    if ok:
        return jsonify({"ok": True, "metin": metin})
    return jsonify({"ok": False, "hata": metin}), 400


@bp.route("/kaydet", methods=["POST"])
@giris_gerekli
def kaydet():
    """Formu office_rentals tablosuna kaydeder."""
    ofis_turu = (request.form.get("ofis_turu") or "").strip()
    if not ofis_turu:
        flash("Ofis türü seçin.", "warning")
        return redirect(url_for("ilan_robotu.index"))
    baslik = (request.form.get("baslik") or "").strip()
    il = (request.form.get("il") or "").strip()
    ilce = (request.form.get("ilce") or "").strip()
    adres = (request.form.get("adres") or "").strip()
    try:
        aylik_fiyat = float((request.form.get("aylik_fiyat") or "0").replace(",", "."))
    except ValueError:
        aylik_fiyat = 0
    yasal_adres = request.form.get("yasal_adres") in ("1", "on")
    sekreterya = request.form.get("sekreterya_karsilama") in ("1", "on")
    posta_takibi = request.form.get("posta_takibi") in ("1", "on")
    toplanti_odasi = request.form.get("toplanti_odasi") in ("1", "on")
    sinirsiz_cay = request.form.get("sinirsiz_cay_kahve") in ("1", "on")
    fiber = request.form.get("fiber_internet") in ("1", "on")
    numara_0850 = request.form.get("numara_0850_tahsisi") in ("1", "on")
    anlik_bildirim = request.form.get("anlik_bildirim_sistemi") in ("1", "on")
    misafir = request.form.get("misafir_agirlama") in ("1", "on")
    mutfak = request.form.get("mutfak_erisimi") in ("1", "on")
    temizlik = request.form.get("temizlik_hizmeti") in ("1", "on")
    aciklama = (request.form.get("aciklama") or "").strip()
    aciklama_ai = (request.form.get("aciklama_ai") or "").strip()
    eids_yetki_no = (request.form.get("eids_yetki_no") or "").strip() or None
    rid = request.form.get("id", type=int)

    if rid:
        execute(
            """UPDATE office_rentals SET
               ofis_turu=%s, baslik=%s, il=%s, ilce=%s, adres=%s, aylik_fiyat=%s,
               yasal_adres=%s, sekreterya_karsilama=%s, posta_takibi=%s, toplanti_odasi=%s,
               sinirsiz_cay_kahve=%s, fiber_internet=%s, numara_0850_tahsisi=%s, anlik_bildirim_sistemi=%s,
               misafir_agirlama=%s, mutfak_erisimi=%s, temizlik_hizmeti=%s,
               aciklama=%s, aciklama_ai=%s, eids_yetki_no=%s, updated_at=NOW()
               WHERE id=%s""",
            (ofis_turu, baslik, il, ilce, adres, aylik_fiyat,
             yasal_adres, sekreterya, posta_takibi, toplanti_odasi,
             sinirsiz_cay, fiber, numara_0850, anlik_bildirim, misafir, mutfak, temizlik,
             aciklama, aciklama_ai, eids_yetki_no, rid),
        )
        flash("İlan güncellendi.", "success")
    else:
        execute(
            """INSERT INTO office_rentals (
               ofis_turu, baslik, il, ilce, adres, aylik_fiyat,
               yasal_adres, sekreterya_karsilama, posta_takibi, toplanti_odasi,
               sinirsiz_cay_kahve, fiber_internet, numara_0850_tahsisi, anlik_bildirim_sistemi,
               misafir_agirlama, mutfak_erisimi, temizlik_hizmeti,
               aciklama, aciklama_ai, eids_yetki_no
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (ofis_turu, baslik, il, ilce, adres, aylik_fiyat,
             yasal_adres, sekreterya, posta_takibi, toplanti_odasi,
             sinirsiz_cay, fiber, numara_0850, anlik_bildirim, misafir, mutfak, temizlik,
             aciklama, aciklama_ai, eids_yetki_no),
        )
        flash("İlan taslağı kaydedildi.", "success")
    return redirect(url_for("ilan_robotu.index"))


@bp.route("/yayinla/<platform>", methods=["POST"])
@giris_gerekli
def yayinla(platform):
    """
    İlanı seçilen platforma (sahibinden, hepsiemlak) Selenium robotu ile yükler.
    Body: JSON { ilan_id?: number } veya form. ilan_id yoksa form alanları (baslik, fiyat, eids_no, aciklama, resim_yollari) kullanılır.
    Robot arka planda thread ile çalışır; tarayıcı açılır.
    """
    if platform not in ("sahibinden", "hepsiemlak"):
        return jsonify({"ok": False, "mesaj": "Geçersiz platform."}), 400
    data = request.get_json(silent=True) or request.form
    ilan_id = data.get("ilan_id") or request.form.get("ilan_id")
    if isinstance(ilan_id, str) and ilan_id.isdigit():
        ilan_id = int(ilan_id)
    baslik, fiyat, eids_no, aciklama = "", "", "", ""
    resim_yollari = []
    if ilan_id:
        row = fetch_one(
            """SELECT baslik, aylik_fiyat, eids_yetki_no, aciklama, aciklama_ai
               FROM office_rentals WHERE id=%s""",
            (int(ilan_id),),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "İlan bulunamadı."}), 404
        baslik = (row.get("baslik") or "").strip()
        fiyat = str(row.get("aylik_fiyat") or "")
        eids_no = (row.get("eids_yetki_no") or "").strip() or ""
        aciklama = (row.get("aciklama") or row.get("aciklama_ai") or "").strip()
    else:
        baslik = (data.get("baslik") or "").strip()
        fiyat = str(data.get("fiyat") or data.get("aylik_fiyat") or "")
        eids_no = (data.get("eids_no") or data.get("eids_yetki_no") or "").strip()
        aciklama = (data.get("aciklama") or "").strip()
        r = data.get("resim_yollari")
        if isinstance(r, list):
            resim_yollari = [x for x in r if x]
        elif isinstance(r, str) and r:
            resim_yollari = [p.strip() for p in r.replace(";", ",").split(",") if p.strip()]
    t = threading.Thread(
        target=_run_robot_async,
        args=(platform, baslik, fiyat, eids_no, aciklama, resim_yollari),
        daemon=True,
    )
    t.start()
    return jsonify({
        "ok": True,
        "mesaj": "Yayınlama başlatıldı. Tarayıcı açılıyor; giriş ve form doldurma robot tarafından yapılacak.",
    }), 202


@bp.route("/<int:rid>/sil", methods=["POST"])
@giris_gerekli
def sil(rid):
    """İlan taslağını siler."""
    execute("DELETE FROM office_rentals WHERE id=%s", (rid,))
    flash("İlan silindi.", "info")
    return redirect(url_for("ilan_robotu.index"))
