"""
WiFi Otomatik Personel Takip Servisi
-------------------------------------
Personelin telefonu ofis WiFi'sine bağlanınca otomatik "Geldi" kaydı,
ayrılınca otomatik "Gitti" kaydı oluşturur.
Hiçbir telefon uygulaması gerekmez — sadece MAC adresi kaydedilir.

Çalışma mantığı:
  Her 3 dakikada → arp -a komutu → ağdaki MAC listesi → DB güncelle
"""

import subprocess
import sqlite3
import threading
import time
import re
import socket
from pathlib import Path
from datetime import datetime, date, time as dtime

# ── Ayarlar ──────────────────────────────────────────────────────────────────
KONTROL_SURESI   = 3 * 60          # 3 dakika (saniye)
AYRILMA_SURESI   = 3 * 60 + 30     # 3.5 dk görünmeyince "ayrıldı" say
DB_PATH          = Path(__file__).parent / "erp.db"

# ── Durum callback (ERP UI'ı güncellemek için) ────────────────────────────────
_durum_callback = None   # PersonelTab tarafından set edilir

def set_durum_callback(fn):
    global _durum_callback
    _durum_callback = fn

def _notify(mesaj: str, pid: int = None, tip: str = "bilgi"):
    if _durum_callback:
        try:
            _durum_callback(mesaj, pid, tip)
        except Exception:
            pass

# ── DB ────────────────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_wifi_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS personeller (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad TEXT NOT NULL UNIQUE,
            sifre TEXT DEFAULT '1234',
            mesai_baslangic TEXT DEFAULT '09:00',
            mac_adresi TEXT,
            aktif INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS devam (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            personel_id INTEGER NOT NULL,
            tarih TEXT NOT NULL,
            giris_saati TEXT,
            cikis_saati TEXT,
            gec_kaldi INTEGER DEFAULT 0,
            gec_dakika INTEGER DEFAULT 0,
            UNIQUE(personel_id, tarih),
            FOREIGN KEY(personel_id) REFERENCES personeller(id)
        );
        CREATE TABLE IF NOT EXISTS wifi_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            personel_id INTEGER NOT NULL,
            tarih TEXT NOT NULL,
            saat TEXT NOT NULL,
            olay TEXT NOT NULL,
            FOREIGN KEY(personel_id) REFERENCES personeller(id)
        );
    """)
    # Yeni sütun ekle (migration)
    try:
        conn.execute("ALTER TABLE personeller ADD COLUMN mac_adresi TEXT")
        conn.commit()
    except Exception:
        pass
    conn.commit()
    conn.close()

# ── ARP Tarama ────────────────────────────────────────────────────────────────
def arp_tara() -> set:
    """
    Ağdaki aktif MAC adreslerini döndürür.
    Önce ping broadcast ile tüm ağı uyandırır, sonra ARP tablosunu okur.
    """
    import platform
    mac_set = set()

    # Adım 1: Kendi IP'den ağ maskesini bul
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        # Ağ broadcast: 192.168.1.x için 192.168.1.255
        parts = local_ip.split(".")
        broadcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
        subnet    = f"{parts[0]}.{parts[1]}.{parts[2]}"
    except Exception:
        local_ip  = ""
        broadcast = "192.168.1.255"
        subnet    = "192.168.1"

    # Adım 2: Ping broadcast (ARP tablosunu doldurur)
    try:
        if platform.system() == "Windows":
            subprocess.run(["ping", "-n", "1", "-w", "500", broadcast],
                           capture_output=True, timeout=3)
        else:
            subprocess.run(["ping", "-c", "1", "-W", "1", "-b", broadcast],
                           capture_output=True, timeout=3)
    except Exception:
        pass

    # Adım 3: Ağdaki tüm IP'lere hızlı ping at (thread ile paralel)
    def ping_ip(ip):
        try:
            if platform.system() == "Windows":
                subprocess.run(["ping", "-n", "1", "-w", "300", ip],
                               capture_output=True, timeout=2)
            else:
                subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                               capture_output=True, timeout=2)
        except Exception:
            pass

    threads = []
    for i in range(1, 255):
        ip = f"{subnet}.{i}"
        t = threading.Thread(target=ping_ip, args=(ip,), daemon=True)
        threads.append(t)
        t.start()
        if i % 50 == 0:  # 50'şer grup halinde
            for tt in threads[-50:]:
                tt.join(timeout=0.5)

    # Ping'lerin bitmesi için bekle
    time.sleep(1.5)

    # Adım 4: ARP tablosunu oku
    try:
        result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            m = re.search(
                r"([0-9a-f]{2}[-:][0-9a-f]{2}[-:][0-9a-f]{2}[-:][0-9a-f]{2}[-:][0-9a-f]{2}[-:][0-9a-f]{2})",
                line.lower()
            )
            if m:
                mac = m.group(1).replace("-", ":").upper()
                # FF:FF:FF:FF:FF:FF broadcast MAC'i atla
                if mac != "FF:FF:FF:FF:FF:FF":
                    mac_set.add(mac)
    except Exception as e:
        print(f"[WiFi Takip] ARP okuma hatası: {e}")

    return mac_set

def _normalize_mac(mac: str) -> str:
    """MAC adresini büyük harf ve ':' formatına normalize et."""
    if not mac:
        return ""
    return mac.upper().replace("-", ":").strip()

# ── Giriş/Çıkış Kayıt ────────────────────────────────────────────────────────
def _giris_kaydet(pid: int, ad: str, mesai_baslangic: str):
    bugun = date.today().isoformat()
    simdi = datetime.now().strftime("%H:%M")
    conn  = get_conn()
    try:
        row = conn.execute(
            "SELECT giris_saati FROM devam WHERE personel_id=? AND tarih=?",
            (pid, bugun)
        ).fetchone()
        if row and row["giris_saati"]:
            return  # Zaten girilmiş, tekrar yazma

        # Geç kalma hesapla
        try:
            sinir = datetime.strptime(mesai_baslangic or "09:00", "%H:%M").time()
            giris_t = datetime.strptime(simdi, "%H:%M").time()
            fark = int((datetime.combine(date.today(), giris_t) -
                        datetime.combine(date.today(), sinir)).total_seconds() // 60)
            gec_kaldi = 1 if fark > 5 else 0
            gec_dk    = fark if fark > 5 else 0
        except Exception:
            gec_kaldi = 0; gec_dk = 0

        conn.execute("""
            INSERT INTO devam (personel_id, tarih, giris_saati, gec_kaldi, gec_dakika)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(personel_id, tarih) DO UPDATE SET
                giris_saati = excluded.giris_saati,
                gec_kaldi   = excluded.gec_kaldi,
                gec_dakika  = excluded.gec_dakika
        """, (pid, bugun, simdi, gec_kaldi, gec_dk))

        conn.execute(
            "INSERT INTO wifi_log (personel_id, tarih, saat, olay) VALUES (?,?,?,?)",
            (pid, bugun, simdi, "giris")
        )
        conn.commit()

        if gec_kaldi:
            mesaj = f"📲 {ad} ofise geldi — {simdi} ⚠️ {gec_dk} dk geç!"
        else:
            mesaj = f"📲 {ad} ofise geldi — {simdi} ✅"
        print(f"[WiFi Takip] {mesaj}")
        _notify(mesaj, pid, "giris")

    finally:
        conn.close()

def _cikis_kaydet(pid: int, ad: str):
    bugun = date.today().isoformat()
    simdi = datetime.now().strftime("%H:%M")
    conn  = get_conn()
    try:
        row = conn.execute(
            "SELECT giris_saati, cikis_saati FROM devam WHERE personel_id=? AND tarih=?",
            (pid, bugun)
        ).fetchone()

        if not row or not row["giris_saati"]:
            return  # Girişi yoksa çıkış yazma

        if row["cikis_saati"]:
            return  # Zaten çıkış yapılmış

        conn.execute(
            "UPDATE devam SET cikis_saati=? WHERE personel_id=? AND tarih=?",
            (simdi, pid, bugun)
        )
        conn.execute(
            "INSERT INTO wifi_log (personel_id, tarih, saat, olay) VALUES (?,?,?,?)",
            (pid, bugun, simdi, "cikis")
        )
        conn.commit()

        mesaj = f"🚶 {ad} ofisten ayrıldı — {simdi}"
        print(f"[WiFi Takip] {mesaj}")
        _notify(mesaj, pid, "cikis")

    finally:
        conn.close()

# ── Ana Tarama Döngüsü ────────────────────────────────────────────────────────
class WifiTakipServisi:
    def __init__(self):
        self._running     = False
        self._thread      = None
        self._ofiste       = {}   # {pid: son_görülme_timestamp}
        self._last_scan   = {}   # {pid: bool}  son taramada görüldü mü

    def baslat(self):
        if self._running:
            return
        init_wifi_db()
        self._running = True
        self._thread  = threading.Thread(target=self._dongu, daemon=True)
        self._thread.start()
        print(f"[WiFi Takip] Servis başladı — her {KONTROL_SURESI//60} dakikada tarama")

    def durdur(self):
        self._running = False

    def ofistekiler(self) -> list:
        """Şu an ofiste olan personel listesi."""
        simdi = time.time()
        return [pid for pid, ts in self._ofiste.items() if simdi - ts < AYRILMA_SURESI + 60]

    def _dongu(self):
        while self._running:
            try:
                self._tara()
            except Exception as e:
                print(f"[WiFi Takip] Döngü hatası: {e}")
            time.sleep(KONTROL_SURESI)

    def _tara(self):
        conn = get_conn()
        personeller = conn.execute(
            "SELECT id, ad, mac_adresi, mesai_baslangic FROM personeller WHERE aktif=1 AND mac_adresi IS NOT NULL AND mac_adresi != ''"
        ).fetchall()
        conn.close()

        if not personeller:
            return

        aktif_macler = arp_tara()
        simdi_ts     = time.time()

        for p in personeller:
            pid = p["id"]
            mac = _normalize_mac(p["mac_adresi"])
            if not mac:
                continue

            goruldu = mac in aktif_macler

            if goruldu:
                self._ofiste[pid] = simdi_ts
                if not self._last_scan.get(pid, False):
                    # Yeni geldi!
                    _giris_kaydet(pid, p["ad"], p["mesai_baslangic"])
                self._last_scan[pid] = True
            else:
                # Görünmüyor — yeterince uzun süredir yoksa çıkış say
                son_gorunme = self._ofiste.get(pid, 0)
                if self._last_scan.get(pid, False):
                    # Az önce vardı, şimdi yok
                    if simdi_ts - son_gorunme > AYRILMA_SURESI:
                        _cikis_kaydet(pid, p["ad"])
                        self._last_scan[pid] = False
                        if pid in self._ofiste:
                            del self._ofiste[pid]

# ── Global Servis Instance ────────────────────────────────────────────────────
servis = WifiTakipServisi()

if __name__ == "__main__":
    print("WiFi Takip Servisi test modu")
    print("Ağdaki MAC adresleri taranıyor...")
    macler = arp_tara()
    print(f"Bulunan {len(macler)} cihaz:")
    for mac in sorted(macler):
        print(f"  {mac}")
