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
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Python < 3.9
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

CREATE TABLE IF NOT EXISTS personel_hareketleri (
    id           SERIAL PRIMARY KEY,
    personel_id  INTEGER NOT NULL,
    tarih        DATE    NOT NULL,
    saat         TIME    NOT NULL,
    tip          VARCHAR(20) NOT NULL,  -- 'giris', 'cikis', ileride 'izin_giris', 'izin_cikis' vb.
    kaynak       VARCHAR(20) DEFAULT 'qr',
    created_at   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hareket_tarih    ON personel_hareketleri(tarih);
CREATE INDEX IF NOT EXISTS idx_hareket_personel ON personel_hareketleri(personel_id);
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


def _log_hareket(personel_id: int, tarih: date, saat_str: str, tip: str, kaynak: str = "qr") -> None:
    """Her giriş/çıkış için detaylı hareket logu.

    Şimdilik sadece 'giris' ve 'cikis' kullanıyoruz; ileride izinler için genişletilebilir.
    """
    try:
        execute(
            """
            INSERT INTO personel_hareketleri (personel_id, tarih, saat, tip, kaynak)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (int(personel_id), tarih, saat_str, tip, kaynak),
        )
    except Exception:
        # Hareket logundaki hata ana akışı bozmasın
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


def _turkey_now():
    """Türkiye saatine göre şu an (giriş/çıkış kayıtları için)."""
    if ZoneInfo:
        return datetime.now(ZoneInfo("Europe/Istanbul"))
    return datetime.now()


def _get_client_ip():
    """İşlemlerde kullanılacak istemci IP adresi."""
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.remote_addr or "")


def _wifi_izinli_mi():
    """
    Giriş/çıkış işlemlerini sadece yerel ağdan (ofis WiFi / LAN) yapılabilir hale getir.
    Öntanımlı olarak private IP aralıklarını kabul eder.
    """
    # Ortam değişkeni ile kuralı tamamen kapat (ör. Render'da):
    if (os.getenv("PDOVAM_WIFI_KONTROLU_KAPAT") or "").lower() in ("1", "true", "yes"):
        return True
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

    # Gün içi izin süreleri için hareketler tablosu
    hareket_rows = fetch_all(
        """
        SELECT personel_id, tarih, saat, tip
        FROM personel_hareketleri
        WHERE tarih BETWEEN %s AND %s
        """,
        (bas_tarih, bit_tarih),
    ) or []
    izin_dakika_map = {}
    toplam_izin_dk = 0

    def _to_minutes(val):
        if hasattr(val, "hour"):
            try:
                return val.hour * 60 + val.minute
            except Exception:
                return None
        s = str(val or "")
        parts = s.split(":")
        if len(parts) < 2:
            return None
        try:
            h = int(parts[0])
            m = int(parts[1])
            return h * 60 + m
        except Exception:
            return None

    hareket_by_key = {}
    for h in hareket_rows:
        key = (h["personel_id"], h["tarih"])
        hareket_by_key.setdefault(key, []).append(h)

    for key, events in hareket_by_key.items():
        # O günkü tüm hareketler; saat artan sırada gez
        try:
            events.sort(key=lambda e: e["saat"])
        except Exception:
            continue
        izin_dk = 0
        n = len(events)
        for i, ev in enumerate(events):
            if (ev.get("tip") or "").lower() != "cikis":
                continue
            if i >= n - 1:
                # Son çıkış: gün sonu; izin sayma
                continue
            nxt = events[i + 1]
            if (nxt.get("tip") or "").lower() != "giris":
                continue
            m1 = _to_minutes(ev.get("saat"))
            m2 = _to_minutes(nxt.get("saat"))
            if m1 is None or m2 is None or m2 <= m1:
                continue
            diff = m2 - m1
            if diff > 0:
                izin_dk += diff
        if izin_dk > 0:
            izin_dakika_map[key] = izin_dk
            toplam_izin_dk += izin_dk
    seen_ids = set()
    tum_personeller = []
    for r in rows:
        pid = r["id"]
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        tum_personeller.append({"id": pid, "ad_soyad": r["ad_soyad"]})
    personeller_all = [_personel_row_to_json_serializable(r) for r in rows]
    # Her personel + gün için izin süresini ekle
    for d in personeller_all:
        pid = d.get("id")
        tarih = d.get("tarih")
        izin_dk = izin_dakika_map.get((pid, tarih), 0)
        d["gun_izin_dk"] = izin_dk
        if izin_dk > 0:
            h = izin_dk // 60
            m = izin_dk % 60
            if h > 0 and m > 0:
                d["gun_izin_str"] = f"{h} saat {m} dk"
            elif h > 0:
                d["gun_izin_str"] = f"{h} saat"
            else:
                d["gun_izin_str"] = f"{m} dk"
        else:
            d["gun_izin_str"] = ""
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

    # Supabase devam logları (bulut raporu) + geri dönüş olarak ana veritabanı
    devam_kayitlari = []
    toplam_gec_dk = 0
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
                # Geç kalma farkı (09:00 üstü girişler) — sadece 'giris' kayıtlarında
                fark = None
                try:
                    if (r.get("islem") or "") == "giris":
                        saat_str = str(r.get("saat") or "")[:5]
                        if saat_str:
                            gh, gm = [int(x) for x in saat_str.split(":")]
                            giris_t = datetime.now().replace(hour=gh, minute=gm, second=0, microsecond=0)
                            mesai_t = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
                            if giris_t > mesai_t:
                                diff_min = int((giris_t - mesai_t).total_seconds() // 60)
                                if diff_min > 0:
                                    toplam_gec_dk += diff_min
                                    h = diff_min // 60
                                    m = diff_min % 60
                                    if h > 0 and m > 0:
                                        fark = f"{h} saat {m} dk"
                                    elif h > 0:
                                        fark = f"{h} saat"
                                    else:
                                        fark = f"{m} dk"
                except Exception:
                    fark = None
                r["fark"] = fark
                # İzin süresi: aynı personel + tarih için hesaplanan değer
                try:
                    from datetime import datetime as _dt
                    tval = r.get("tarih")
                    if isinstance(tval, str):
                        dt = _dt.fromisoformat(tval[:10]).date()
                    else:
                        dt = tval
                    izin_dk = izin_dakika_map.get((int(r.get("personel_id") or 0), dt), 0)
                except Exception:
                    izin_dk = 0
                if izin_dk > 0:
                    h = izin_dk // 60
                    m = izin_dk % 60
                    if h > 0 and m > 0:
                        r["izin_sure"] = f"{h} saat {m} dk"
                    elif h > 0:
                        r["izin_sure"] = f"{h} saat"
                    else:
                        r["izin_sure"] = f"{m} dk"
                else:
                    r["izin_sure"] = ""
                devam_kayitlari.append(r)
        except Exception:
            devam_kayitlari = []

    # Eğer Supabase boş ya da yapılandırılamadıysa, doğrudan devam_kayitlari tablosundan rapor üret
    if not devam_kayitlari:
        params = [bas_tarih, bit_tarih]
        extra = ""
        if secili_pid:
            extra = " AND personel_id = %s"
            params.append(secili_pid)
        rows_devam = fetch_all(
            f"""
            SELECT personel_id, ad_soyad, tarih, giris_saati, cikis_saati, durum
            FROM devam_kayitlari
            WHERE tarih BETWEEN %s AND %s{extra}
            ORDER BY tarih, personel_id
            """,
            tuple(params),
        ) or []
        from datetime import time as _time_cls

        for r in rows_devam:
            t = r.get("tarih")
            if isinstance(t, date):
                tarih_iso = t.isoformat()
                tarih_tr = t.strftime("%d.%m.%Y")
            else:
                try:
                    dt = datetime.strptime(str(t)[:10], "%Y-%m-%d").date()
                    tarih_iso = dt.isoformat()
                    tarih_tr = dt.strftime("%d.%m.%Y")
                except Exception:
                    tarih_iso = str(t)
                    tarih_tr = str(t)
            # Geç kalma farkı (09:00 üstü girişler) hesapla
            # Giriş logu
            giris = r.get("giris_saati")
            cikis = r.get("cikis_saati")
            fark_gec = None
            try:
                if giris:
                    if isinstance(giris, str):
                        gh, gm, *_ = [int(x) for x in str(giris).split(":")]
                        giris_t = _time_cls(gh, gm)
                    else:
                        giris_t = giris
                    dt_giris = datetime.combine(date.today(), giris_t)
                    mesai_t = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
                    mesai_dt = datetime.combine(date.today(), mesai_t.time())
                    if dt_giris > mesai_dt:
                        diff_min = int((dt_giris - mesai_dt).total_seconds() // 60)
                        if diff_min > 0:
                            toplam_gec_dk += diff_min
                            h = diff_min // 60
                            m = diff_min % 60
                            if h > 0 and m > 0:
                                fark_gec = f"{h} saat {m} dk"
                            elif h > 0:
                                fark_gec = f"{h} saat"
                            else:
                                fark_gec = f"{m} dk"
            except Exception:
                fark_gec = None

            if giris:
                devam_kayitlari.append({
                    "personel_id": r.get("personel_id"),
                    "personel_adi": r.get("ad_soyad"),
                    "tarih": tarih_iso,
                    "tarih_tr": tarih_tr,
                    "saat": str(giris)[:8],
                    "islem": "giris",
                    "fark": fark_gec,
                    "izin_sure": "",
                })
            # Çıkış logu
            if cikis:
                # Bu gün için izin süresi varsa ekle
                izin_dk = izin_dakika_map.get((r.get("personel_id"), t), 0)
                if izin_dk > 0:
                    h2 = izin_dk // 60
                    m2 = izin_dk % 60
                    if h2 > 0 and m2 > 0:
                        izin_str = f"{h2} saat {m2} dk"
                    elif h2 > 0:
                        izin_str = f"{h2} saat"
                    else:
                        izin_str = f"{m2} dk"
                else:
                    izin_str = ""
                devam_kayitlari.append({
                    "personel_id": r.get("personel_id"),
                    "personel_adi": r.get("ad_soyad"),
                    "tarih": tarih_iso,
                    "tarih_tr": tarih_tr,
                    "saat": str(cikis)[:8],
                    "islem": "cikis",
                    "fark": None,
                    "izin_sure": izin_str,
                })

    # Toplam farkı saat/dakika string'ine ve gün/saat özetine çevir
    if toplam_gec_dk > 0:
        tf_h = toplam_gec_dk // 60
        tf_m = toplam_gec_dk % 60
        if tf_h > 0 and tf_m > 0:
            toplam_fark_str = f"{tf_h} saat {tf_m} dk"
        elif tf_h > 0:
            toplam_fark_str = f"{tf_h} saat"
        else:
            toplam_fark_str = f"{tf_m} dk"
    else:
        toplam_fark_str = ""

    if toplam_gec_dk > 0:
        gun = toplam_gec_dk // (8 * 60)  # 8 saat = 1 gün
        kalan_dk = toplam_gec_dk % (8 * 60)
        saat = kalan_dk // 60
        if gun > 0 and saat > 0:
            toplam_gun_str = f"{gun} gün {saat} saat"
        elif gun > 0:
            toplam_gun_str = f"{gun} gün"
        elif saat > 0:
            toplam_gun_str = f"{saat} saat"
        else:
            toplam_gun_str = ""
    else:
        toplam_gun_str = ""

    if toplam_izin_dk > 0:
        iz_h = toplam_izin_dk // 60
        iz_m = toplam_izin_dk % 60
        if iz_h > 0 and iz_m > 0:
            toplam_izin_str = f"{iz_h} saat {iz_m} dk"
        elif iz_h > 0:
            toplam_izin_str = f"{iz_h} saat"
        else:
            toplam_izin_str = f"{iz_m} dk"
        iz_gun = toplam_izin_dk // (8 * 60)
        if iz_gun > 0:
            toplam_izin_gun_str = f"{iz_gun} gün"
        else:
            toplam_izin_gun_str = ""
    else:
        toplam_izin_str = ""
        toplam_izin_gun_str = ""

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
                           devam_kayitlari=devam_kayitlari,
                           toplam_fark=toplam_fark_str,
                           toplam_gun=toplam_gun_str,
                           toplam_izin=toplam_izin_str,
                           toplam_izin_gun=toplam_izin_gun_str)


@bp.route("/isle/<int:personel_id>", methods=["GET", "POST"])
def pdovam_isle(personel_id):
    """GET: Kişiye özel QR ile açılınca isim + tek buton (Giriş/Çıkış). POST: JSON giriş/çıkış."""
    if request.method == "GET":
        p = fetch_one("SELECT id, ad_soyad FROM personel WHERE id=%s AND is_active = true", (personel_id,))
        if not p:
            return render_template("pdovam/isle_tek.html", personel_adi=None, personel_id=personel_id, buton_metni=None, hata="Personel bulunamadı")
        bugun = _turkey_now().date()
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

    simdi = _turkey_now()
    bugun = simdi.date()
    kayit = fetch_one(
        """
        SELECT * FROM devam_kayitlari
        WHERE personel_id=%s AND tarih=%s
        """,
        (personel_id, bugun),
    )

    saat_full = simdi.strftime("%H:%M:%S")

    if not kayit:
        # İLK GİRİŞ
        gec = gec_hesapla(simdi.time(), p.get("mesai_baslangic") or "09:00")
        giris_str = saat_full
        execute("""
            INSERT INTO devam_kayitlari
                (personel_id, ad_soyad, tarih, giris_saati, durum, gec_dakika, kaynak)
            VALUES (%s, %s, %s, %s, 'giris', %s, 'qr')
            ON CONFLICT (personel_id, tarih) DO NOTHING
        """, (personel_id, p["ad_soyad"], bugun, giris_str, gec))
        _log_hareket(personel_id, bugun, giris_str, "giris")
        _supabase_log_devam(personel_id, bugun, giris_str, "giris")

        mesaj = f"✅ Giriş kaydedildi — {simdi.strftime('%H:%M')}"
        if gec > 0:
            mesaj += f" ({gec} dk geç)"
        return jsonify({"ok": True, "islem": "giris", "saat": simdi.strftime("%H:%M"), "mesaj": mesaj, "gec": gec})

    elif kayit["durum"] == "giris":
        # ÇIKIŞ
        cikis_str = saat_full
        execute("""
            UPDATE devam_kayitlari
            SET cikis_saati=%s, durum='cikis'
            WHERE personel_id=%s AND tarih=%s
        """, (cikis_str, personel_id, bugun))
        _log_hareket(personel_id, bugun, cikis_str, "cikis")
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


@bp.route("/api/kayit")
@login_required
def api_kayit():
    """Tek devam kaydı getir (rapor düzenleme modalı için)."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"ok": False, "mesaj": "Yetkisiz"}), 403
    pid = request.args.get("personel_id")
    tarih = request.args.get("tarih")
    if not pid or not tarih:
        return jsonify({"ok": False, "mesaj": "personel_id ve tarih gerekli"}), 400
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "Geçersiz personel_id"}), 400
    row = fetch_one(
        "SELECT personel_id, ad_soyad, tarih, giris_saati, cikis_saati, durum FROM devam_kayitlari WHERE personel_id=%s AND tarih=%s",
        (pid, tarih[:10]),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı"}), 404
    out = {
        "personel_id": row["personel_id"],
        "personel_adi": row.get("ad_soyad"),
        "tarih": row["tarih"].isoformat() if hasattr(row["tarih"], "isoformat") else str(row["tarih"])[:10],
        "giris_saati": None,
        "cikis_saati": None,
    }
    for key in ("giris_saati", "cikis_saati"):
        v = row.get(key)
        if v is not None:
            if hasattr(v, "strftime"):
                out[key] = v.strftime("%H:%M") if hasattr(v, "hour") else str(v)[:5]
            else:
                out[key] = str(v)[:5]
    return jsonify(out)


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


@bp.route("/api/saat-utc-duzelt", methods=["POST"])
@login_required
def api_saat_utc_duzelt():
    """Bir kerelik: Veritabanındaki giriş/çıkış saatlerini UTC → Türkiye (UTC+3) düzeltir."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"ok": False, "mesaj": "Yetkisiz"}), 403
    try:
        # devam_kayitlari: TIME + 3 saat
        r1 = execute(
            "UPDATE devam_kayitlari SET giris_saati = giris_saati + INTERVAL '3 hours' WHERE giris_saati IS NOT NULL"
        )
        r2 = execute(
            "UPDATE devam_kayitlari SET cikis_saati = cikis_saati + INTERVAL '3 hours' WHERE cikis_saati IS NOT NULL"
        )
        # Supabase personel_devam: saat metni "HH:MM:SS" → +3 saat
        client = _supabase_client()
        supabase_guncellenen = 0
        if client:
            try:
                result = client.table("personel_devam").select("id, saat").execute()
                data = getattr(result, "data", None) or []
                for row in data:
                    sid = row.get("id")
                    saat_str = (row.get("saat") or "").strip()[:12]
                    if not sid or not saat_str:
                        continue
                    parts = saat_str.replace(",", ".").split(":")
                    h = int(parts[0]) if len(parts) > 0 else 0
                    m = int(parts[1]) if len(parts) > 1 else 0
                    s = int(float(parts[2])) if len(parts) > 2 else 0
                    from datetime import time, timedelta
                    t = datetime.combine(date.today(), time(h, m, s)) + timedelta(hours=3)
                    new_str = t.strftime("%H:%M:%S")
                    client.table("personel_devam").update({"saat": new_str}).eq("id", sid).execute()
                    supabase_guncellenen += 1
            except Exception:
                pass
        return jsonify({
            "ok": True,
            "mesaj": "Saatler Türkiye saatine (UTC+3) güncellendi.",
            "supabase_guncellenen": supabase_guncellenen,
        })
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


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
