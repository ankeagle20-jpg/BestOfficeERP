"""
Personel Devam Takibi — QR Kod + Tarayıcı Butonu
routes/pdovam_routes.py

app.py'ye ekle:
    from routes.pdovam_routes import bp as pdovam_bp
    app.register_blueprint(pdovam_bp, url_prefix="/pdovam")

pip install qrcode[pil]
"""

from flask import Blueprint, render_template, jsonify, request, send_file
from flask_login import login_required, current_user
from db import fetch_all, fetch_one, execute
from datetime import date, datetime
import qrcode
import io
import os
import socket

# Supabase entegrasyonu için (ENV: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
try:
    from supabase import create_client
except ImportError:  # supabase-python yüklü değilse, entegrasyon sessizce devre dışı kalır
    create_client = None

bp = Blueprint("pdovam", __name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS devam_kayitlari (
    id           SERIAL PRIMARY KEY,
    personel_id  INTEGER NOT NULL,
    ad_soyad     VARCHAR(100),
    tarih        DATE    NOT NULL DEFAULT CURRENT_DATE,
    giris_saati  TIME,
    cikis_saati  TIME,
    durum        VARCHAR(20) DEFAULT 'giris',
    gec_dakika   INTEGER DEFAULT 0,
    kaynak       VARCHAR(20) DEFAULT 'qr',
    created_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE(personel_id, tarih)
);
CREATE INDEX IF NOT EXISTS idx_devam_tarih    ON devam_kayitlari(tarih);
CREATE INDEX IF NOT EXISTS idx_devam_personel ON devam_kayitlari(personel_id);
"""


def _supabase_client():
    """Supabase client (mevcut değilse None)."""
    if not create_client:
        return None
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


def _supabase_log_devam(personel_id: int, tarih: date, saat_str: str, islem: str):
    """Supabase'e personel devam logu gönder (personel_id, tarih, saat, durum)."""
    client = _supabase_client()
    if not client:
        return
    try:
        payload = {
            "personel_id": int(personel_id),
            "tarih": tarih.isoformat(),
            "saat": saat_str,       # "HH:MM:SS"
            "islem": islem,        # "giris" veya "cikis"
        }
        # Tek tablo varsayımı: personel_devam
        client.table("personel_devam").insert(payload).execute()
    except Exception:
        # Supabase hatalarını sessizce yut (ana akışı bozmasın)
        return


def _get_client_ip():
    """İşlemlerde kullanılacak istemci IP adresi."""
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.remote_addr or "")


def _wifi_izinli_mi():
    """
    Giriş/çıkış işlemlerini sadece yerel ağdan (ofis WiFi / LAN) yapılabilir hale getir.
    Öntanımlı olarak private IP aralıklarını kabul eder.
    """
    # Eğer istek Render / bulut ortamından geliyorsa (örn. *.onrender.com),
    # ofis WiFi kısıtlamasını uygulamayalım; bulut barkodlarının amacı dışarıdan da çalışması.
    host = (request.host or "").lower()
    public_url = (os.getenv("PUBLIC_APP_URL") or "").lower()
    if "onrender.com" in host or "onrender.com" in public_url:
        return True
    ip = _get_client_ip()
    if not ip:
        return False
    private_prefixes = (
        "192.168.",  # ev/ofis router'ları
        "10.",
        "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
        "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
        "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
    )
    return ip.startswith(private_prefixes)


def gec_hesapla(giris_saati, mesai_bas) -> int:
    try:
        if isinstance(mesai_bas, str):
            p = mesai_bas.split(":")
            mesai = datetime.now().replace(hour=int(p[0]), minute=int(p[1]), second=0).time()
        else:
            mesai = mesai_bas
        t1 = datetime.combine(date.today(), mesai)
        t2 = datetime.combine(date.today(), giris_saati)
        return max(0, int((t2 - t1).total_seconds() / 60))
    except:
        return 0


# ─── PERSONEL GİRİŞ/ÇIKIŞ SAYFASI (şifresiz, sadece WiFi'den erişilir) ───

def _personel_row_to_json_serializable(row):
    """time/datetime alanlarını string yaparak JSON'da kullanılabilir dict döndürür.
    Ayrıca giris/cikis saatlerinden toplam ve net mesai süresini (saat) hesaplar.
    """
    if not row:
        return None
    d = dict(row)
    giris = d.get("giris_saati")
    cikis = d.get("cikis_saati")
    # Toplam ve net mesai süresi (saat + dakika)
    d["toplam_saat"] = None
    d["net_saat"] = None
    d["toplam_saat_saat"] = None
    d["toplam_saat_dakika"] = None
    d["net_saat_saat"] = None
    d["net_saat_dakika"] = None
    if giris is not None and cikis is not None and hasattr(giris, "hour") and hasattr(cikis, "hour"):
        try:
            t1 = datetime.combine(date.today(), giris)
            t2 = datetime.combine(date.today(), cikis)
            seconds = max(0, int((t2 - t1).total_seconds()))
            toplam_dakika = seconds // 60
            # Toplam süre
            d["toplam_saat"] = round(toplam_dakika / 60.0, 2)
            d["toplam_saat_saat"] = int(toplam_dakika // 60)
            d["toplam_saat_dakika"] = int(toplam_dakika % 60)
            # Net süre (1.5 saat = 90 dk mola düşülmüş)
            net_dakika = max(0, toplam_dakika - 90)
            d["net_saat"] = round(net_dakika / 60.0, 2)
            d["net_saat_saat"] = int(net_dakika // 60)
            d["net_saat_dakika"] = int(net_dakika % 60)
        except Exception:
            pass
    # Saat alanlarını HH:MM string'e çevir
    for key in ("mesai_baslangic", "mesai_bitis", "giris_saati", "cikis_saati"):
        v = d.get(key)
        if v is not None and hasattr(v, "strftime"):
            d[key] = v.strftime("%H:%M") if hasattr(v, "hour") else str(v)
    # Tarih alanını (devam kaydı tarihi) okunur string'e çevir
    tarih = d.get("tarih")
    if tarih is not None and hasattr(tarih, "strftime"):
        d["tarih_tr"] = tarih.strftime("%d.%m.%Y")
    else:
        d["tarih_tr"] = None
    return d


@bp.route("/")
def pdovam_anasayfa():
    """Personelin telefondan açacağı sayfa — login gerektirmez."""
    # Tarih aralığı: baslangic ve bitis (YYYY-MM-DD). Eski ?tarih= için de destek.
    bas_str = request.args.get("bas") or request.args.get("tarih")
    bit_str = request.args.get("bit") or bas_str
    pid_str = request.args.get("personel_id")
    try:
        secili_pid = int(pid_str) if pid_str else None
    except (TypeError, ValueError):
        secili_pid = None
    try:
        today = date.today()
        bas_tarih = datetime.strptime((bas_str or today.isoformat())[:10], "%Y-%m-%d").date()
        if bit_str:
            bit_tarih = datetime.strptime(bit_str[:10], "%Y-%m-%d").date()
        else:
            bit_tarih = bas_tarih
    except ValueError:
        bas_tarih = bit_tarih = date.today()
    # Eğer kullanıcı yanlışlıkla ters girdiyse, yer değiştir
    if bit_tarih < bas_tarih:
        bas_tarih, bit_tarih = bit_tarih, bas_tarih
    rows = fetch_all(
        """
        SELECT p.id, p.ad_soyad, p.mesai_baslangic, p.mesai_bitis,
               d.tarih,
               d.giris_saati, d.cikis_saati, d.durum, d.gec_dakika
        FROM personel p
        LEFT JOIN devam_kayitlari d
            ON d.personel_id = p.id AND d.tarih BETWEEN %s AND %s
        WHERE p.is_active = true
        ORDER BY p.ad_soyad, d.tarih
        """,
        (bas_tarih, bit_tarih),
    )
    rows = rows or []
    seen_ids = set()
    tum_personeller = []
    for r in rows:
        pid = r["id"]
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        tum_personeller.append({"id": pid, "ad_soyad": r["ad_soyad"]})
    personeller_all = [_personel_row_to_json_serializable(r) for r in rows]
    if secili_pid:
        personeller_list = [r for r in personeller_all if r.get("id") == secili_pid]
    else:
        personeller_list = personeller_all
    personeller_json = personeller_all  # hem template hem JS aynı veriyi kullanıyor
    if bas_tarih == bit_tarih:
        bugun = bas_tarih.strftime("%d.%m.%Y")
        gun_adi = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"][bas_tarih.weekday()]
    else:
        bugun = f"{bas_tarih.strftime('%d.%m.%Y')} - {bit_tarih.strftime('%d.%m.%Y')}"
        gun_adi = "Tarih aralığı"
    is_admin = current_user.is_authenticated and getattr(current_user, "role", None) == "admin"
    bulut_base = (os.getenv("PUBLIC_APP_URL") or "https://bestofficeerp.onrender.com").strip().rstrip("/")
    # Görselleri her zaman mevcut sunucudan yükle (yerelde hızlı, Render'da aynı origin)
    qr_img_base = request.url_root.rstrip("/") or _server_base_url().rstrip("/")

    # Supabase devam logları (bulut raporu)
    devam_kayitlari = []
    client = _supabase_client()
    if client:
        try:
            query = client.table("personel_devam").select("*")
            query = query.gte("tarih", bas_tarih.isoformat()).lte("tarih", bit_tarih.isoformat())
            if secili_pid:
                query = query.eq("personel_id", secili_pid)
            result = query.order("tarih", desc=False).order("saat", desc=False).execute()
            data = getattr(result, "data", None) or getattr(result, "model", None) or []
            ad_map = {p["id"]: p["ad_soyad"] for p in tum_personeller}
            for row in data:
                r = dict(row)
                r["personel_adi"] = ad_map.get(r.get("personel_id")) or ""
                devam_kayitlari.append(r)
        except Exception:
            devam_kayitlari = []
    return render_template("pdovam/giris.html",
                           personeller=personeller_list,
                           personeller_json=personeller_json,
                           tum_personeller=tum_personeller,
                           bugun=bugun,
                           gun_adi=gun_adi,
                           bas_iso=bas_tarih.isoformat(),
                           bit_iso=bit_tarih.isoformat(),
                           secili_personel_id=secili_pid,
                           is_admin=is_admin,
                           bulut_base=bulut_base,
                           qr_img_base=qr_img_base,
                           devam_kayitlari=devam_kayitlari)


@bp.route("/isle/<int:personel_id>", methods=["GET", "POST"])
def pdovam_isle(personel_id):
    """GET: Kişiye özel QR ile açılınca isim + tek buton (Giriş/Çıkış). POST: JSON giriş/çıkış."""
    if request.method == "GET":
        p = fetch_one("SELECT id, ad_soyad FROM personel WHERE id=%s AND is_active = true", (personel_id,))
        if not p:
            return render_template("pdovam/isle_tek.html", personel_adi=None, personel_id=personel_id, buton_metni=None, hata="Personel bulunamadı")
        bugun = date.today()
        kayit = fetch_one(
            "SELECT durum, cikis_saati FROM devam_kayitlari WHERE personel_id=%s AND tarih=%s",
            (personel_id, bugun),
        )
        if not kayit:
            buton_metni = "Giriş Yap"
        elif kayit.get("durum") == "giris":
            buton_metni = "Çıkış Yap"
        else:
            buton_metni = "Çıkış yapıldı"  # disabled
        return render_template(
            "pdovam/isle_tek.html",
            personel_adi=p["ad_soyad"],
            personel_id=personel_id,
            buton_metni=buton_metni,
            hata=None,
        )

    # POST: giriş / çıkış
    if not _wifi_izinli_mi():
        return jsonify({"ok": False, "mesaj": "Bu işlem sadece ofis WiFi / yerel ağından yapılabilir."}), 403

    p = fetch_one("SELECT * FROM personel WHERE id=%s AND is_active = true", (personel_id,))
    if not p:
        return jsonify({"ok": False, "mesaj": "Personel bulunamadı"})

    simdi = datetime.now()
    bugun = date.today()
    kayit = fetch_one("""
        SELECT * FROM devam_kayitlari
        WHERE personel_id=%s AND tarih=%s
    """, (personel_id, bugun))

    if not kayit:
        # İLK GİRİŞ
        gec = gec_hesapla(simdi.time(), p.get("mesai_baslangic") or "09:00")
        giris_str = simdi.strftime("%H:%M:%S")
        execute("""
            INSERT INTO devam_kayitlari
                (personel_id, ad_soyad, tarih, giris_saati, durum, gec_dakika, kaynak)
            VALUES (%s, %s, %s, %s, 'giris', %s, 'qr')
            ON CONFLICT (personel_id, tarih) DO NOTHING
        """, (personel_id, p["ad_soyad"], bugun, giris_str, gec))
        _supabase_log_devam(personel_id, bugun, giris_str, "giris")

        mesaj = f"✅ Giriş kaydedildi — {simdi.strftime('%H:%M')}"
        if gec > 0:
            mesaj += f" ({gec} dk geç)"
        return jsonify({"ok": True, "islem": "giris", "saat": simdi.strftime("%H:%M"), "mesaj": mesaj, "gec": gec})

    elif kayit["durum"] == "giris":
        # ÇIKIŞ
        cikis_str = simdi.strftime("%H:%M:%S")
        execute("""
            UPDATE devam_kayitlari
            SET cikis_saati=%s, durum='cikis'
            WHERE personel_id=%s AND tarih=%s
        """, (cikis_str, personel_id, bugun))
        _supabase_log_devam(personel_id, bugun, cikis_str, "cikis")
        return jsonify({"ok": True, "islem": "cikis", "saat": simdi.strftime("%H:%M"),
                        "mesaj": f"🚪 Çıkış kaydedildi — {simdi.strftime('%H:%M')}"})

    else:
        return jsonify({"ok": True, "islem": "zaten_cikis",
                        "mesaj": f"Bugün çıkış zaten kaydedildi ({str(kayit['cikis_saati'])[:5]})"})


# ─── YÖNETİM API'LERİ (login gerekir) ───

@bp.route("/api/gunluk")
@login_required
def api_gunluk():
    tarih = request.args.get("tarih", date.today().isoformat())
    rows = fetch_all("""
        SELECT p.ad_soyad, p.mesai_baslangic, p.mesai_bitis,
               d.giris_saati, d.cikis_saati, d.durum, d.gec_dakika, d.kaynak,
               p.id as personel_id
        FROM personel p
        LEFT JOIN devam_kayitlari d
            ON d.personel_id = p.id AND d.tarih = %s
        WHERE p.is_active = true
        ORDER BY p.ad_soyad
    """, (tarih,))
    return jsonify({"ok": True, "tarih": tarih, "data": [dict(r) for r in (rows or [])]})


@bp.route("/api/aylik")
@login_required
def api_aylik():
    yil = request.args.get("yil", date.today().year)
    ay  = request.args.get("ay",  date.today().month)
    rows = fetch_all("""
        SELECT p.ad_soyad,
               COUNT(d.id) FILTER (WHERE d.durum IN ('giris','cikis')) as toplam_gun,
               COUNT(d.id) FILTER (WHERE d.gec_dakika > 0)             as gec_sayisi,
               COALESCE(SUM(d.gec_dakika),0)                           as toplam_gec_dk
        FROM personel p
        LEFT JOIN devam_kayitlari d
            ON d.personel_id = p.id
            AND EXTRACT(YEAR  FROM d.tarih) = %s
            AND EXTRACT(MONTH FROM d.tarih) = %s
        WHERE p.is_active = true
        GROUP BY p.id, p.ad_soyad
        ORDER BY p.ad_soyad
    """, (yil, ay))
    return jsonify({"ok": True, "data": [dict(r) for r in (rows or [])]})


@bp.route("/api/manuel", methods=["POST"])
@login_required
def api_manuel():
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"ok": False, "mesaj": "Yetkisiz"}), 403
    d = request.get_json() or {}
    pid   = int(d["personel_id"])
    tarih = d.get("tarih", date.today().isoformat())
    giris = d.get("giris_saati")
    cikis = d.get("cikis_saati")
    p = fetch_one("SELECT ad_soyad FROM personel WHERE id=%s", (pid,))
    execute("""
        INSERT INTO devam_kayitlari
            (personel_id, ad_soyad, tarih, giris_saati, cikis_saati, durum, kaynak)
        VALUES (%s, %s, %s, %s, %s,
            CASE WHEN %s IS NOT NULL THEN 'cikis' ELSE 'giris' END, 'manuel')
        ON CONFLICT (personel_id, tarih) DO UPDATE SET
            giris_saati = COALESCE(%s, devam_kayitlari.giris_saati),
            cikis_saati = COALESCE(%s, devam_kayitlari.cikis_saati),
            durum = CASE WHEN %s IS NOT NULL THEN 'cikis' ELSE devam_kayitlari.durum END,
            kaynak = 'manuel'
    """, (pid, p["ad_soyad"] if p else "", tarih, giris, cikis,
          cikis, giris, cikis, cikis))
    return jsonify({"ok": True, "mesaj": "Manuel kayıt güncellendi."})


@bp.route("/api/sil", methods=["POST"])
@login_required
def api_sil():
    """Belirli personelin belirli tarihteki devam kaydını sil (varsayılan: bugün)."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"ok": False, "mesaj": "Yetkisiz"}), 403
    d = request.get_json() or {}
    try:
        pid = int(d.get("personel_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "personel_id gerekli"}), 400
    tarih = d.get("tarih") or date.today().isoformat()
    execute("DELETE FROM devam_kayitlari WHERE personel_id=%s AND tarih=%s", (pid, tarih))
    return jsonify({"ok": True})


@bp.route("/api/supabase-senkron")
@login_required
def api_supabase_senkron():
    """Yerel devam_kayitlari tablosundaki geçmiş hareketleri Supabase personel_devam tablosuna bas."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"ok": False, "mesaj": "Yetkisiz"}), 403
    client = _supabase_client()
    if not client:
        return jsonify({"ok": False, "mesaj": "Supabase yapılandırılmamış (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)."}), 500

    bas_str = request.args.get("bas")
    bit_str = request.args.get("bit") or bas_str
    if not bas_str:
        return jsonify({"ok": False, "mesaj": "bas parametresi (YYYY-MM-DD) gerekli"}), 400
    try:
        bas_tarih = datetime.strptime(bas_str[:10], "%Y-%m-%d").date()
        if bit_str:
            bit_tarih = datetime.strptime(bit_str[:10], "%Y-%m-%d").date()
        else:
            bit_tarih = bas_tarih
    except ValueError:
        return jsonify({"ok": False, "mesaj": "Tarih formatı hatalı (YYYY-MM-DD)"}), 400
    if bit_tarih < bas_tarih:
        bas_tarih, bit_tarih = bit_tarih, bas_tarih

    rows = fetch_all(
        """
        SELECT personel_id, tarih, giris_saati, cikis_saati, durum
        FROM devam_kayitlari
        WHERE tarih BETWEEN %s AND %s
        ORDER BY tarih, personel_id
        """,
        (bas_tarih, bit_tarih),
    ) or []

    adet = 0
    for r in rows:
        t = r.get("tarih")
        if not isinstance(t, date):
            try:
                t = datetime.strptime(str(t)[:10], "%Y-%m-%d").date()
            except Exception:
                continue
        giris = r.get("giris_saati")
        cikis = r.get("cikis_saati")
        if giris:
            _supabase_log_devam(r["personel_id"], t, str(giris), "giris")
            adet += 1
        if cikis:
            _supabase_log_devam(r["personel_id"], t, str(cikis), "cikis")
            adet += 1

    return jsonify({"ok": True, "mesaj": "Senkron tamamlandı.", "tarih_araligi": [bas_tarih.isoformat(), bit_tarih.isoformat()], "toplam_hareket": adet, "satir_sayisi": len(rows)})


@bp.route("/api/schema-kur")
@login_required
def api_schema_kur():
    try:
        for s in SCHEMA_SQL.split(";"):
            s = s.strip()
            if s:
                execute(s)
        return jsonify({"ok": True, "mesaj": "Tablo oluşturuldu!"})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)})


# ─── QR KOD ÜRETİCİ ───

def _server_base_url():
    """QR için kullanılacak sunucu adresi.
    PUBLIC_APP_URL tanımlıysa onu kullan (PC kapalıyken de çalışan bulut adresi).
    Değilse request.host; localhost ise yerel IP denenir.
    """
    public_url = (os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
    if public_url:
        return public_url
    host = request.host.split(":")[0]
    port = None
    if ":" in request.host:
        try:
            port = request.host.split(":")[1]
        except Exception:
            port = None
    if host in ("127.0.0.1", "localhost"):
        try:
            ip = socket.gethostbyname(socket.gethostname())
            host = ip
        except Exception:
            pass
    if port:
        return f"http://{host}:{port}"
    return f"http://{host}"

@bp.route("/qr/tek")
def qr_tek():
    """Tek QR: /pdovam/ sayfasına gider; herkes aynı QR'ı okutup listeden kendi isimlerini seçerek giriş/çıkış yapar."""
    host = _server_base_url().rstrip("/")
    # Ortak cihazlar için liste modu: ?ortak=1 ile aç
    url = f"{host}/pdovam/?ortak=1"
    qr = qrcode.QRCode(version=2, box_size=10, border=4,
                       error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0d1f30", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name="pdovam_tek_qr.png")


@bp.route("/qr/<int:personel_id>")
def qr_uret(personel_id):
    """Personele özel QR kod PNG döndürür."""
    host = _server_base_url().rstrip("/")
    url  = f"{host}/pdovam/isle/{personel_id}"

    qr = qrcode.QRCode(version=2, box_size=10, border=4,
                        error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0d1f30", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png",
                     download_name=f"qr_personel_{personel_id}.png")


def _bulut_base():
    """Bulut (Render) adresi — QR içeriği bu adrese gider."""
    return (os.getenv("PUBLIC_APP_URL") or "https://bestofficeerp.onrender.com").strip().rstrip("/")


@bp.route("/qr/bulut/tek")
def qr_bulut_tek():
    """Bulut sekmesi: Tek QR görseli — içerik Render adresine gider, görsel mevcut sunucudan hızlı yüklenir."""
    host = _bulut_base()
    url = f"{host}/pdovam/?ortak=1"
    qr = qrcode.QRCode(version=2, box_size=10, border=4,
                       error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0d1f30", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name="pdovam_bulut_tek_qr.png")


@bp.route("/qr/bulut/<int:personel_id>")
def qr_bulut_personel(personel_id):
    """Bulut sekmesi: Kişiye özel QR görseli — içerik Render adresine gider."""
    host = _bulut_base()
    url = f"{host}/pdovam/isle/{personel_id}"
    qr = qrcode.QRCode(version=2, box_size=10, border=4,
                       error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0d1f30", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name=f"qr_bulut_{personel_id}.png")


@bp.route("/qr-yazdir")
@login_required
def qr_yazdir():
    """Tek QR (ofis girişi) + her personel için ayrı QR kartı. Kalabalık ofislerde kişiye özel kart kullanın."""
    personeller = fetch_all("SELECT id, ad_soyad FROM personel WHERE is_active = true ORDER BY ad_soyad")
    host = _server_base_url().rstrip("/")
    return render_template("pdovam/qr_yazdir.html",
                           personeller=personeller or [],
                           host=host)
