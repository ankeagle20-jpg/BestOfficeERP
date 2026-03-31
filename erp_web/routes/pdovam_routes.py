"""
Personel Devam Takibi — QR Kod + Tarayıcı Butonu
routes/pdovam_routes.py

app.py'ye ekle:
    from routes.pdovam_routes import bp as pdovam_bp
    app.register_blueprint(pdovam_bp, url_prefix="/pdovam")

pip install qrcode[pil]
"""

from collections import defaultdict
from flask import Blueprint, render_template, jsonify, request, send_file
from utils.devam_bulut_sync import insert_devam_bulut_satir, sync_devam_gunu_buluta
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
    """Supabase'e tek satır (toplu senkron / yardımcı)."""
    insert_devam_bulut_satir(int(personel_id), tarih, saat_str, islem)


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
        insert_devam_bulut_satir(int(personel_id), tarih, saat_str, tip)
    except Exception:
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
        d["tarih_iso"] = tarih.isoformat()
    else:
        d["tarih_tr"] = None
        d["tarih_iso"] = None
    return d


# Rapor: gün sonu varsayılan çıkış (mesai içinde hâlâ işyerinde sayılır)
VARSAYILAN_CIKIS_SAATI = "18:30:00"
MESAI_SABAH_DK = 9 * 60
VARSAYILAN_CIKIS_DK = 18 * 60 + 30


def _devam_row_personel_id(val):
    """Supabase/JSON bazen int, string veya float döndürür."""
    if val is None:
        return None
    try:
        if isinstance(val, bool):
            return None
        return int(val)
    except (TypeError, ValueError):
        try:
            return int(float(str(val).strip()))
        except (TypeError, ValueError):
            return None


def _devam_tarih_iso_key(r):
    t = r.get("tarih")
    if t is None:
        return None
    if isinstance(t, str) and len(t) >= 10:
        return t[:10]
    if hasattr(t, "isoformat"):
        try:
            return t.isoformat()[:10]
        except Exception:
            return None
    s = str(t).strip()
    return s[:10] if len(s) >= 10 else None


def _pdovam_saat_to_minutes(s):
    """HH:MM veya HH:MM:SS → gün içi dakika (0–1440)."""
    s = str(s or "").strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        h = int(parts[0].strip())
        m = int(parts[1].strip()) if len(parts) > 1 else 0
        return h * 60 + m
    except (ValueError, IndexError):
        return None


def _pdovam_minutes_clock(m):
    m = int(max(0, m))
    return f"{m // 60:02d}:{m % 60:02d}:00"


def _pdovam_dk_yazi(dk):
    if dk <= 0:
        return ""
    h = dk // 60
    m = dk % 60
    if h > 0 and m > 0:
        return f"{h} saat {m} dk"
    if h > 0:
        return f"{h} saat"
    return f"{m} dk"


def _pdovam_local_rows_to_rapor_hareketleri(rows_local, ad_map):
    """devam_kayitlari satırlarını rapordaki Supabase satır formatına çevirir.
    (personel_id, tarih_iso) anahtarları, buluttaki ham logların üzerine yazılır."""
    override_keys = set()
    events = []
    for r in rows_local or []:
        pid = int(r["personel_id"])
        kaynak = str(r.get("kaynak") or "").strip().lower()
        t = r.get("tarih")
        if isinstance(t, date):
            tarih_iso = t.isoformat()
            tarih_tr = t.strftime("%d.%m.%Y")
        else:
            try:
                dtx = datetime.strptime(str(t)[:10], "%Y-%m-%d").date()
                tarih_iso = dtx.isoformat()
                tarih_tr = dtx.strftime("%d.%m.%Y")
            except Exception:
                continue
        giris = r.get("giris_saati")
        cikis = r.get("cikis_saati")
        if not (giris or cikis):
            continue
        # QR kaydı ise ham hareketler zaten personel_hareketleri/Supabase'de mevcut.
        # Burada override edilirse gün içi çoklu giriş/çıkış görünmez.
        if kaynak == "qr":
            continue
        # Sadece gerçekten giriş/çıkış yazılmış günlerde bulut ham loglarını yerel kayıtla değiştir
        override_keys.add((pid, tarih_iso))
        ad = (r.get("ad_soyad") or "").strip() or (ad_map.get(pid) if ad_map else "") or ""
        if giris:
            events.append({
                "personel_id": pid,
                "personel_adi": ad,
                "tarih": tarih_iso,
                "tarih_tr": tarih_tr,
                "saat": str(giris)[:8],
                "islem": "giris",
            })
        if cikis:
            events.append({
                "personel_id": pid,
                "personel_adi": ad,
                "tarih": tarih_iso,
                "tarih_tr": tarih_tr,
                "saat": str(cikis)[:8],
                "islem": "cikis",
            })
    return override_keys, events


def _pdovam_hareket_rows_to_events(rows_hareket, ad_map):
    """personel_hareketleri satırlarını rapor event formatına çevirir."""
    events = []
    for r in rows_hareket or []:
        pid = _devam_row_personel_id(r.get("personel_id"))
        if pid is None:
            continue
        t = r.get("tarih")
        if isinstance(t, date):
            tarih_iso = t.isoformat()
            tarih_tr = t.strftime("%d.%m.%Y")
        else:
            try:
                dtx = datetime.strptime(str(t)[:10], "%Y-%m-%d").date()
                tarih_iso = dtx.isoformat()
                tarih_tr = dtx.strftime("%d.%m.%Y")
            except Exception:
                continue
        tip = (r.get("tip") or "").strip().lower()
        if tip not in ("giris", "cikis"):
            continue
        saat = str(r.get("saat") or "").strip()[:8]
        if not saat:
            continue
        ad = (r.get("ad_soyad") or "").strip() or (ad_map.get(pid) if ad_map else "") or ""
        events.append({
            "personel_id": pid,
            "personel_adi": ad,
            "tarih": tarih_iso,
            "tarih_tr": tarih_tr,
            "saat": saat,
            "islem": tip,
        })
    return events


def _pdovam_merge_unique_events(events):
    """Aynı (personel, tarih, saat, islem) event'lerini tekilleştirir."""
    out = []
    seen = set()
    for e in events or []:
        pid = _devam_row_personel_id(e.get("personel_id"))
        tk = (e.get("tarih") or "")[:10]
        islem = (e.get("islem") or "").strip().lower()
        saat = str(e.get("saat") or "").strip()[:8]
        if pid is None or not tk or not islem or not saat:
            continue
        key = (int(pid), tk, saat, islem)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _pdovam_qr_row_fallback_events(rows_local, ad_map, hareket_key_set):
    """personel_hareketleri olmayan günlerde, QR günlük satırından fallback event üret."""
    out = []
    for r in rows_local or []:
        kaynak = str(r.get("kaynak") or "").strip().lower()
        if kaynak != "qr":
            continue
        pid = _devam_row_personel_id(r.get("personel_id"))
        if pid is None:
            continue
        t = r.get("tarih")
        if isinstance(t, date):
            tarih_iso = t.isoformat()
            tarih_tr = t.strftime("%d.%m.%Y")
        else:
            try:
                dtx = datetime.strptime(str(t)[:10], "%Y-%m-%d").date()
                tarih_iso = dtx.isoformat()
                tarih_tr = dtx.strftime("%d.%m.%Y")
            except Exception:
                continue
        if (int(pid), tarih_iso) in (hareket_key_set or set()):
            continue
        ad = (r.get("ad_soyad") or "").strip() or (ad_map.get(pid) if ad_map else "") or ""
        giris = r.get("giris_saati")
        cikis = r.get("cikis_saati")
        if giris:
            out.append({
                "personel_id": int(pid),
                "personel_adi": ad,
                "tarih": tarih_iso,
                "tarih_tr": tarih_tr,
                "saat": str(giris)[:8],
                "islem": "giris",
            })
        if cikis:
            out.append({
                "personel_id": int(pid),
                "personel_adi": ad,
                "tarih": tarih_iso,
                "tarih_tr": tarih_tr,
                "saat": str(cikis)[:8],
                "islem": "cikis",
            })
    return out


def pdovam_toplam_fark_dk_for_personel(pid: int, bas_tarih, bit_tarih) -> int:
    """
    Belirli bir personel ve tarih aralığı için, giriş/çıkış raporu sekmesindekiyle
    tamamen aynı Fark mantığına göre toplam fark dakikasını döndürür.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return 0

    p = fetch_one("SELECT ad_soyad FROM personel WHERE id=%s", (pid,))
    ad = (p.get("ad_soyad") or "").strip() if p else ""
    ad_map = {pid: ad}

    rows_local = fetch_all(
        """
        SELECT personel_id, ad_soyad, tarih, giris_saati, cikis_saati, kaynak
        FROM devam_kayitlari
        WHERE personel_id=%s AND tarih BETWEEN %s AND %s
        ORDER BY tarih, personel_id
        """,
        (pid, bas_tarih, bit_tarih),
    ) or []

    override_keys, local_events = _pdovam_local_rows_to_rapor_hareketleri(rows_local, ad_map)

    rows_hareket = fetch_all(
        """
        SELECT h.personel_id, h.tarih, h.saat, h.tip, p.ad_soyad
        FROM personel_hareketleri h
        JOIN personel p ON p.id = h.personel_id
        WHERE h.personel_id=%s AND h.tarih BETWEEN %s AND %s
        ORDER BY h.tarih, h.saat
        """,
        (pid, bas_tarih, bit_tarih),
    ) or []
    hareket_events = _pdovam_hareket_rows_to_events(rows_hareket, ad_map)

    hareket_key_set = set()
    for ev in hareket_events:
        ev_pid = _devam_row_personel_id(ev.get("personel_id"))
        ev_tk = (ev.get("tarih") or "")[:10]
        if ev_pid is not None and ev_tk:
            hareket_key_set.add((int(ev_pid), ev_tk))

    qr_fallback_events = _pdovam_qr_row_fallback_events(rows_local, ad_map, hareket_key_set)

    devam_raw = []
    client = _supabase_client()
    if client:
        try:
            query = (
                client.table("personel_devam")
                .select("*")
                .eq("personel_id", pid)
                .gte("tarih", bas_tarih.isoformat())
                .lte("tarih", bit_tarih.isoformat())
                .order("tarih", desc=False)
                .order("saat", desc=False)
            )
            result = query.execute()
            data = getattr(result, "data", None) or getattr(result, "model", None) or []
            for row in data:
                r = dict(row)
                row_pid = _devam_row_personel_id(r.get("personel_id"))
                tk = _devam_tarih_iso_key(r)
                if row_pid is not None and tk and (row_pid, tk) in override_keys:
                    continue
                r["personel_adi"] = ad_map.get(row_pid, ad) if row_pid is not None else ad
                devam_raw.append(r)

            filtered_hareket = []
            for ev in hareket_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                    continue
                filtered_hareket.append(ev)
            for ev in qr_fallback_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                    continue
                filtered_hareket.append(ev)
            devam_raw.extend(filtered_hareket)
            devam_raw.extend(local_events)
        except Exception:
            tmp = []
            for ev in hareket_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                    continue
                tmp.append(ev)
            for ev in qr_fallback_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                    continue
                tmp.append(ev)
            devam_raw = tmp + list(local_events)
    else:
        tmp = []
        for ev in hareket_events:
            ev_pid = _devam_row_personel_id(ev.get("personel_id"))
            ev_tk = (ev.get("tarih") or "")[:10]
            if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                continue
            tmp.append(ev)
        for ev in qr_fallback_events:
            ev_pid = _devam_row_personel_id(ev.get("personel_id"))
            ev_tk = (ev.get("tarih") or "")[:10]
            if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                continue
            tmp.append(ev)
        devam_raw = tmp + list(local_events)

    devam_raw = _pdovam_merge_unique_events(devam_raw)
    kons = _consolidate_pdovam_gunluk(devam_raw)
    return sum(int(r.get("fark_toplam_dk") or 0) for r in (kons or []))


def _consolidate_pdovam_gunluk(rows):
    """Her personel + gün tek satır.

    - Mesai 09:00: daha erken girişte geç kalma 0 (first_min < 9:00 ise gec_sabah_dk = 0).
    - Gün içi çıkış→tekrar giriş arası dakikalar izin_disari_dk.
    - Son kayıt gerçek çıkışsa: 18:30 − çıkış = erken_cikis_izin_dk (tam gün mesaiye göre eksik kalan süre).
    - Son çıkış yoksa çıkış 18:30 varsayılan; erken izin 0.
    """
    groups = defaultdict(list)
    for r in rows or []:
        pid = _devam_row_personel_id(r.get("personel_id"))
        tk = _devam_tarih_iso_key(r)
        if pid is None or not tk:
            continue
        islem = (r.get("islem") or "").strip().lower()
        if islem not in ("giris", "cikis"):
            continue
        sm = _pdovam_saat_to_minutes(r.get("saat"))
        if sm is None:
            continue
        groups[(pid, tk)].append({
            "islem": islem,
            "min": sm,
            "saat_raw": str(r.get("saat") or "").strip(),
            "personel_adi": (r.get("personel_adi") or "").strip(),
        })

    out = []
    for (pid, tk) in sorted(groups.keys(), key=lambda k: (k[1], k[0])):
        evs = groups[(pid, tk)]
        evs.sort(key=lambda e: (e["min"], 0 if e["islem"] == "giris" else 1))

        i0 = None
        for i, e in enumerate(evs):
            if e["islem"] == "giris":
                i0 = i
                break
        if i0 is None:
            continue
        trimmed = evs[i0:]

        first = trimmed[0]
        first_min = first["min"]
        graw = (first.get("saat_raw") or "").strip()
        if graw and ":" in graw:
            giris_display = (graw + ":00")[:8] if graw.count(":") == 1 and len(graw) <= 5 else graw[:8]
        else:
            giris_display = _pdovam_minutes_clock(first_min)

        gec_sabah_dk = max(0, first_min - MESAI_SABAH_DK)

        inside = True
        pending = None
        last_cikis_raw = None
        izin_disari_dk = 0

        for e in trimmed[1:]:
            if e["islem"] == "cikis":
                if inside:
                    pending = e["min"]
                    inside = False
                    last_cikis_raw = e.get("saat_raw") or last_cikis_raw
                elif pending is not None:
                    pending = e["min"]
                    last_cikis_raw = e.get("saat_raw") or last_cikis_raw
                else:
                    pending = e["min"]
                    inside = False
                    last_cikis_raw = e.get("saat_raw") or last_cikis_raw
            else:
                if not inside and pending is not None:
                    izin_disari_dk += max(0, e["min"] - pending)
                    pending = None
                    inside = True
                elif not inside and pending is None:
                    inside = True

        if pending is not None:
            cikis_min_raw = pending
            # Gün sonu raporunda 18:30 üstünü kırp:
            # personel 19:00'da çıkmış olsa da rapor çıkışı 18:30 görünür,
            # fark sadece gün içi çıkış->giriş aralarından ve (varsa) erken çıkıştan gelir.
            cikis_min = min(cikis_min_raw, VARSAYILAN_CIKIS_DK)
            cikis_varsayilan = False
            craw = (last_cikis_raw or "").strip()
            if cikis_min_raw > VARSAYILAN_CIKIS_DK:
                cikis_display = VARSAYILAN_CIKIS_SAATI
            elif craw and ":" in craw:
                cikis_display = (craw + ":00")[:8] if craw.count(":") == 1 and len(craw) <= 5 else craw[:8]
            else:
                cikis_display = _pdovam_minutes_clock(cikis_min)
            # Mesai bitişi 18:30 kabulü: gerçek çıkıştan 18:30’a kadar eksik süre = izin
            erken_cikis_izin_dk = max(0, VARSAYILAN_CIKIS_DK - cikis_min)
        else:
            cikis_min = VARSAYILAN_CIKIS_DK
            cikis_varsayilan = True
            cikis_display = VARSAYILAN_CIKIS_SAATI
            erken_cikis_izin_dk = 0

        # 09:00 öncesi giriş: geç kalma 0 (mesai 9’da başlar)
        izin_toplam_dk = izin_disari_dk + erken_cikis_izin_dk
        fark_toplam_dk = gec_sabah_dk + izin_toplam_dk
        fark_str = _pdovam_dk_yazi(fark_toplam_dk) if fark_toplam_dk > 0 else "—"

        ad = ""
        for e in trimmed:
            if e.get("personel_adi"):
                ad = e["personel_adi"]
                break

        try:
            tarih_tr = datetime.strptime(tk, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            tarih_tr = tk

        detay_lines = []
        if fark_toplam_dk > 0:
            detay_lines.append("Toplam: " + fark_str)
        if gec_sabah_dk > 0:
            detay_lines.append("Geç kalma (mesai 09:00’a göre): " + _pdovam_dk_yazi(gec_sabah_dk))
        if izin_disari_dk > 0:
            detay_lines.append("Gün içi dışarı (çıkış → tekrar giriş): " + _pdovam_dk_yazi(izin_disari_dk))
        if erken_cikis_izin_dk > 0:
            detay_lines.append("Erken çıkış (18:30’a kadar eksik mesai): " + _pdovam_dk_yazi(erken_cikis_izin_dk))
        if not detay_lines:
            detay_lines.append("Bu gün için hesaplanan fark yok.")

        out.append({
            "personel_id": pid,
            "personel_adi": ad,
            "tarih": tk,
            "tarih_tr": tarih_tr,
            "saat": giris_display,
            "rapor_birlestirilmis": True,
            "rapor_giris": giris_display,
            "rapor_cikis": cikis_display,
            "cikis_varsayilan": cikis_varsayilan,
            "islem": "gunluk",
            "fark": fark_str,
            "fark_toplam_dk": fark_toplam_dk,
            "fark_detay_lines": detay_lines,
            "gec_sabah_dk": gec_sabah_dk,
            "izin_disari_dk": izin_disari_dk,
            "erken_cikis_izin_dk": erken_cikis_izin_dk,
            "izin_toplam_dk": izin_toplam_dk,
        })
    return out


def _pdovam_fark_gun_sonrasi(personel_id: int, t: date, ad_soyad: str):
    """Tek personel + gün için rapor sekmesiyle aynı fark özeti (QR / manuel sonrası liste)."""
    pid = int(personel_id)
    ad_map = {pid: (ad_soyad or "").strip()}
    rows_local = fetch_all(
        """
        SELECT personel_id, ad_soyad, tarih, giris_saati, cikis_saati, kaynak
        FROM devam_kayitlari WHERE personel_id=%s AND tarih=%s
        """,
        (pid, t),
    ) or []
    override_keys, local_events = _pdovam_local_rows_to_rapor_hareketleri(rows_local, ad_map)
    rows_hareket = fetch_all(
        """
        SELECT personel_id, tarih, saat, tip
        FROM personel_hareketleri WHERE personel_id=%s AND tarih=%s
        ORDER BY tarih, saat
        """,
        (pid, t),
    ) or []
    hareket_events = _pdovam_hareket_rows_to_events(rows_hareket, ad_map)
    hareket_key_set = set()
    for ev in hareket_events:
        ev_pid = _devam_row_personel_id(ev.get("personel_id"))
        ev_tk = (ev.get("tarih") or "")[:10]
        if ev_pid is not None and ev_tk:
            hareket_key_set.add((int(ev_pid), ev_tk))
    qr_fallback_events = _pdovam_qr_row_fallback_events(rows_local, ad_map, hareket_key_set)
    devam_raw = []
    tk = t.isoformat()
    client = _supabase_client()
    if client:
        try:
            res = (
                client.table("personel_devam")
                .select("*")
                .eq("personel_id", pid)
                .eq("tarih", tk)
                .execute()
            )
            data = getattr(res, "data", None) or []
            for row in data:
                r = dict(row)
                row_pid = _devam_row_personel_id(r.get("personel_id"))
                if row_pid is not None and (row_pid, tk) in override_keys:
                    continue
                r["personel_adi"] = ad_map.get(row_pid, ad_soyad or "")
                devam_raw.append(r)
            # manual override varsa o gün ham hareketleri bırakma
            filtered_hareket = []
            for ev in hareket_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and (ev_pid, ev_tk) in override_keys:
                    continue
                filtered_hareket.append(ev)
            for ev in qr_fallback_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and (ev_pid, ev_tk) in override_keys:
                    continue
                filtered_hareket.append(ev)
            devam_raw.extend(filtered_hareket)
            devam_raw.extend(local_events)
        except Exception:
            filtered_hareket = []
            for ev in hareket_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and (ev_pid, ev_tk) in override_keys:
                    continue
                filtered_hareket.append(ev)
            for ev in qr_fallback_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and (ev_pid, ev_tk) in override_keys:
                    continue
                filtered_hareket.append(ev)
            devam_raw = list(filtered_hareket) + list(local_events)
    else:
        filtered_hareket = []
        for ev in hareket_events:
            ev_pid = _devam_row_personel_id(ev.get("personel_id"))
            ev_tk = (ev.get("tarih") or "")[:10]
            if ev_pid is not None and (ev_pid, ev_tk) in override_keys:
                continue
            filtered_hareket.append(ev)
        for ev in qr_fallback_events:
            ev_pid = _devam_row_personel_id(ev.get("personel_id"))
            ev_tk = (ev.get("tarih") or "")[:10]
            if ev_pid is not None and (ev_pid, ev_tk) in override_keys:
                continue
            filtered_hareket.append(ev)
        devam_raw = list(filtered_hareket) + list(local_events)
    devam_raw = _pdovam_merge_unique_events(devam_raw)
    cons = _consolidate_pdovam_gunluk(devam_raw)
    if not cons:
        return "—", ["Bu gün için hesaplanan fark yok."]
    c0 = cons[0]
    return (c0.get("fark") or "—"), (c0.get("fark_detay_lines") or ["Bu gün için hesaplanan fark yok."])


@bp.route("/api/fark-gun")
def api_fark_gun():
    """Tek personel + gün: rapor sekmesiyle aynı Fark (QR / Düzenle sonrası liste güncellemesi)."""
    pid_raw = request.args.get("personel_id")
    tarih_str = request.args.get("tarih")
    if not pid_raw or not tarih_str:
        return jsonify({"ok": False, "mesaj": "personel_id ve tarih gerekli"}), 400
    try:
        pid = int(pid_raw)
        t = datetime.strptime(str(tarih_str).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return jsonify({"ok": False, "mesaj": "Geçersiz parametre"}), 400
    p = fetch_one("SELECT ad_soyad FROM personel WHERE id=%s AND is_active = true", (pid,))
    if not p:
        return jsonify({"ok": False, "mesaj": "Personel yok"}), 404
    ad = (p.get("ad_soyad") or "").strip()
    f, lines = _pdovam_fark_gun_sonrasi(pid, t, ad)
    return jsonify({"ok": True, "fark": f, "fark_detay_lines": lines})


@bp.route("/api/hareket-son")
def api_hareket_son():
    """Tek personel için son hareketleri getir (en yeni 10 kayıt)."""
    pid_raw = request.args.get("personel_id")
    if not pid_raw:
        return jsonify({"ok": False, "mesaj": "personel_id gerekli"}), 400
    try:
        pid = int(pid_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "Geçersiz personel_id"}), 400
    rows = fetch_all(
        """
        SELECT tarih, saat, tip
        FROM personel_hareketleri
        WHERE personel_id=%s
        ORDER BY tarih DESC, saat DESC
        LIMIT 10
        """,
        (pid,),
    ) or []
    out = []
    for r in rows:
        t = r.get("tarih")
        if hasattr(t, "strftime"):
            tarih_iso = t.isoformat()
            tarih_tr = t.strftime("%d.%m.%Y")
        else:
            try:
                dx = datetime.strptime(str(t)[:10], "%Y-%m-%d").date()
                tarih_iso = dx.isoformat()
                tarih_tr = dx.strftime("%d.%m.%Y")
            except Exception:
                tarih_iso = str(t or "")[:10]
                tarih_tr = tarih_iso
        s = r.get("saat")
        saat = s.strftime("%H:%M:%S") if hasattr(s, "strftime") else str(s or "")[:8]
        tip = (r.get("tip") or "").strip().lower()
        out.append({
            "tarih": tarih_iso,
            "tarih_tr": tarih_tr,
            "saat": saat,
            "tip": tip,
        })
    return jsonify({"ok": True, "data": out})


@bp.route("/")
def pdovam_anasayfa():
    """Personelin telefondan açacağı sayfa — login gerektirmez."""
    # Tarih aralığı: baslangic ve bitis (YYYY-MM-DD). Eski ?tarih= için de destek.
    bas_raw = request.args.get("bas") or request.args.get("tarih")
    bit_raw = request.args.get("bit")
    pid_str = request.args.get("personel_id")
    try:
        secili_pid = int(pid_str) if pid_str else None
    except (TypeError, ValueError):
        secili_pid = None

    def _parse_iso_date(s):
        if s is None or not str(s).strip():
            return None
        try:
            return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    today = _turkey_now().date()
    ayin_bir = today.replace(day=1)
    bas_parsed = _parse_iso_date(bas_raw)
    bit_parsed = _parse_iso_date(bit_raw)

    # Varsayılan (rapor sekmesi / ilk açılış): ayın 1'i — bugün. ?bas/?bit veya ?tarih= ile özelleştirilebilir.
    if bas_parsed and bit_parsed:
        bas_tarih, bit_tarih = bas_parsed, bit_parsed
    elif bas_parsed and not bit_parsed:
        bas_tarih = bit_tarih = bas_parsed
    elif not bas_parsed and bit_parsed:
        bas_tarih = today
        bit_tarih = bit_parsed
    else:
        bas_tarih = ayin_bir
        bit_tarih = today
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
    # personeller_list / personeller_json: fark eşlemesinden sonra (aşağıda)
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

    # Yerel devam_kayitlari (Personel ekranı / Düzenle) + Supabase (QR logları) birleşimi:
    # Aynı personel+günde yerel satır varsa bulut ham logları o gün için yok sayılır; böylece düzeltmeler raporda da görünür.
    ad_map_rapor = {p["id"]: p["ad_soyad"] for p in tum_personeller}
    params_loc = [bas_tarih, bit_tarih]
    extra_loc = ""
    if secili_pid:
        extra_loc = " AND personel_id = %s"
        params_loc.append(secili_pid)
    rows_local = fetch_all(
        f"""
        SELECT personel_id, ad_soyad, tarih, giris_saati, cikis_saati, kaynak
        FROM devam_kayitlari
        WHERE tarih BETWEEN %s AND %s{extra_loc}
        ORDER BY tarih, personel_id
        """,
        tuple(params_loc),
    ) or []
    override_keys, local_events = _pdovam_local_rows_to_rapor_hareketleri(rows_local, ad_map_rapor)
    rows_hareket = fetch_all(
        f"""
        SELECT h.personel_id, h.tarih, h.saat, h.tip, p.ad_soyad
        FROM personel_hareketleri h
        JOIN personel p ON p.id = h.personel_id
        WHERE h.tarih BETWEEN %s AND %s{extra_loc}
        ORDER BY h.tarih, h.saat
        """,
        tuple(params_loc),
    ) or []
    hareket_events = _pdovam_hareket_rows_to_events(rows_hareket, ad_map_rapor)
    hareket_key_set = set()
    for ev in hareket_events:
        ev_pid = _devam_row_personel_id(ev.get("personel_id"))
        ev_tk = (ev.get("tarih") or "")[:10]
        if ev_pid is not None and ev_tk:
            hareket_key_set.add((int(ev_pid), ev_tk))
    qr_fallback_events = _pdovam_qr_row_fallback_events(rows_local, ad_map_rapor, hareket_key_set)

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

            for row in data:
                r = dict(row)
                row_pid = _devam_row_personel_id(r.get("personel_id"))
                if secili_pid is not None and row_pid != int(secili_pid):
                    continue
                tk = _devam_tarih_iso_key(r)
                if row_pid is not None and tk and (row_pid, tk) in override_keys:
                    continue
                r["personel_adi"] = ad_map_rapor.get(row_pid) if row_pid is not None else ""
                devam_kayitlari.append(r)
            for ev in hareket_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                    continue
                devam_kayitlari.append(ev)
            for ev in qr_fallback_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                    continue
                devam_kayitlari.append(ev)
            devam_kayitlari.extend(local_events)
        except Exception:
            tmp = []
            for ev in hareket_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                    continue
                tmp.append(ev)
            for ev in qr_fallback_events:
                ev_pid = _devam_row_personel_id(ev.get("personel_id"))
                ev_tk = (ev.get("tarih") or "")[:10]
                if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                    continue
                tmp.append(ev)
            devam_kayitlari = tmp + list(local_events)
    else:
        tmp = []
        for ev in hareket_events:
            ev_pid = _devam_row_personel_id(ev.get("personel_id"))
            ev_tk = (ev.get("tarih") or "")[:10]
            if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                continue
            tmp.append(ev)
        for ev in qr_fallback_events:
            ev_pid = _devam_row_personel_id(ev.get("personel_id"))
            ev_tk = (ev.get("tarih") or "")[:10]
            if ev_pid is not None and ev_tk and (ev_pid, ev_tk) in override_keys:
                continue
            tmp.append(ev)
        devam_kayitlari = tmp + list(local_events)
    devam_kayitlari = _pdovam_merge_unique_events(devam_kayitlari)

    devam_kayitlari = _consolidate_pdovam_gunluk(devam_kayitlari)
    toplam_gec_dk = sum((r.get("fark_toplam_dk") or 0) for r in devam_kayitlari)

    # Giriş/Çıkış sekmesi tablosu: raporla aynı fark (tek sütun)
    _fmap = {}
    for row in devam_kayitlari:
        pid = row.get("personel_id")
        tk = (row.get("tarih") or "")[:10]
        if pid is not None and tk:
            _fmap[(int(pid), tk)] = row
    for d in personeller_all:
        tid = d.get("id")
        tiso = d.get("tarih_iso")
        if not tiso and d.get("tarih") is not None:
            tv = d.get("tarih")
            tiso = tv.isoformat()[:10] if hasattr(tv, "isoformat") else str(tv)[:10]
        if tid is None or not tiso:
            d["fark_rapor"] = "—"
            d["fark_detay_lines"] = ["Bu gün için hesaplanan fark yok."]
            continue
        c = _fmap.get((int(tid), tiso[:10]))
        if c:
            d["fark_rapor"] = c.get("fark") or "—"
            d["fark_detay_lines"] = list(c.get("fark_detay_lines") or [])
        else:
            d["fark_rapor"] = "—"
            d["fark_detay_lines"] = ["Bu gün için hesaplanan fark yok."]
    if secili_pid:
        personeller_list = [r for r in personeller_all if r.get("id") == secili_pid]
    else:
        personeller_list = personeller_all
    personeller_json = personeller_all

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
        dak = kalan_dk % 60
        if gun > 0 and saat > 0:
            toplam_gun_str = f"{gun} gün {saat} saat"
        elif gun > 0 and dak > 0:
            toplam_gun_str = f"{gun} gün {dak} dk"
        elif gun > 0:
            toplam_gun_str = f"{gun} gün"
        elif saat > 0 and dak > 0:
            toplam_gun_str = f"{saat} saat {dak} dk"
        elif saat > 0:
            toplam_gun_str = f"{saat} saat"
        elif kalan_dk > 0:
            toplam_gun_str = f"{kalan_dk} dk"
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
            # Aynı gün içinde tekrar girişe izin ver (öğle/izin dönüşü vb.)
            buton_metni = "Giriş Yap"
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
        return jsonify({
            "ok": True,
            "islem": "giris",
            "saat": simdi.strftime("%H:%M"),
            "tarih": bugun.isoformat(),
            "tarih_tr": bugun.strftime("%d.%m.%Y"),
            "mesaj": mesaj,
            "gec": gec,
        })

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
        return jsonify({
            "ok": True,
            "islem": "cikis",
            "saat": simdi.strftime("%H:%M"),
            "tarih": bugun.isoformat(),
            "tarih_tr": bugun.strftime("%d.%m.%Y"),
            "mesaj": f"🚪 Çıkış kaydedildi — {simdi.strftime('%H:%M')}",
        })

    else:
        # Gün içinde tekrar giriş (çoklu giriş/çıkış döngüsü)
        gec = gec_hesapla(simdi.time(), p.get("mesai_baslangic") or "09:00")
        giris_str = saat_full
        execute("""
            UPDATE devam_kayitlari
            SET durum='giris', kaynak='qr', cikis_saati=NULL
            WHERE personel_id=%s AND tarih=%s
        """, (personel_id, bugun))
        _log_hareket(personel_id, bugun, giris_str, "giris")
        _supabase_log_devam(personel_id, bugun, giris_str, "giris")
        mesaj = f"✅ Giriş kaydedildi — {simdi.strftime('%H:%M')}"
        if gec > 0:
            mesaj += f" ({gec} dk geç)"
        return jsonify({
            "ok": True,
            "islem": "giris",
            "saat": simdi.strftime("%H:%M"),
            "tarih": bugun.isoformat(),
            "tarih_tr": bugun.strftime("%d.%m.%Y"),
            "mesaj": mesaj,
            "gec": gec,
        })


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
    row = fetch_one(
        "SELECT giris_saati, cikis_saati, tarih FROM devam_kayitlari WHERE personel_id=%s AND tarih=%s",
        (pid, str(tarih)[:10]),
    )
    if row:
        sync_devam_gunu_buluta(pid, row["tarih"], row.get("giris_saati"), row.get("cikis_saati"))
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
    sync_devam_gunu_buluta(pid, str(tarih)[:10], None, None)
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
