"""ERP Asistan — not / hatırlatma (Faz 1).

Takvim görünümü yok. WhatsApp gönderimi pop-up'tan bağımsızdır;
APScheduler dakikada bir gonder_whatsapp_notlari çağırır.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, current_app, g, jsonify, render_template, request
from flask_login import current_user

from auth import giris_gerekli
from db import _tenant_schema_for_request, execute, execute_returning, fetch_all, fetch_one

bp = Blueprint("erp_notlar", __name__, url_prefix="/erp-notlar")

IST = ZoneInfo("Europe/Istanbul")

KATEGORILER = (
    "Müşteri Görüşmesi",
    "Ödeme Takibi",
    "Sözleşme Yenileme",
    "Personel",
    "Genel Not",
)
ILISKI_TIPLERI = ("musteri", "sozlesme", "fatura", "oda")
DURUMLAR = ("bekliyor", "ertelendi", "tamamlandi")
KOLON_VARSAYILAN = (
    "musteri",
    "kategori",
    "gorusme",
    "hatirlatma",
    "gorusen",
    "gorusulen",
    "aciklama",
    "durum",
)
KOLON_ETIKET = {
    "musteri": "Müşteri",
    "kategori": "Kategori",
    "gorusme": "Görüşme Tarihi",
    "hatirlatma": "Hatırlatma Tarihi",
    "gorusen": "Görüşen Kişi",
    "gorusulen": "Görüşülen Kişi",
    "aciklama": "Açıklama",
    "durum": "Durum",
}
KOLON_OZET_VARSAYILAN = (
    "musteri",
    "gorusme",
    "hatirlatma",
    "aciklama",
    "durum",
    "gorusen",
    "gorusulen",
)
KOLON_OZET_ETIKET = {
    "musteri": "Müşteri",
    "gorusme": "Son Görüşme Tarihi",
    "hatirlatma": "Son Hatırlatma Tarihi",
    "aciklama": "Son Açıklama",
    "durum": "Durum",
    "gorusen": "Görüşen Kişi",
    "gorusulen": "Görüşülen Kişi",
}
_KOLON_KUMESI = {"not": KOLON_VARSAYILAN, "ozet": KOLON_OZET_VARSAYILAN}
_KOLON_ALAN = {
    "not": "erp_asistan_kolon_sirasi",
    "ozet": "erp_asistan_ozet_kolon_sirasi",
}
_HAZIR_SEMALAR: set[str] = set()


def ensure_erp_notlar_tablolari() -> None:
    """İstek şemasında (yoksa public) tabloları oluşturur. Idempotent."""
    schema = _tenant_schema_for_request() or "public"
    if schema in _HAZIR_SEMALAR:
        return
    execute(
        """
        CREATE TABLE IF NOT EXISTS erp_notlar (
            id SERIAL PRIMARY KEY,
            olusturan_kullanici_id INTEGER,
            kategori TEXT NOT NULL,
            iliski_tip TEXT,
            iliski_id INTEGER,
            not_metni TEXT NOT NULL,
            hatirlatma_zamani TIMESTAMPTZ NOT NULL,
            durum TEXT NOT NULL DEFAULT 'bekliyor',
            gorunurluk TEXT NOT NULL DEFAULT 'kisisel',
            departman TEXT,
            whatsapp_gonderilsin BOOLEAN NOT NULL DEFAULT FALSE,
            whatsapp_telefon TEXT,
            whatsapp_mesaj TEXT,
            whatsapp_gonderildi_mi BOOLEAN NOT NULL DEFAULT FALSE,
            whatsapp_hata TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS erp_not_alicilar (
            not_id INTEGER NOT NULL REFERENCES erp_notlar(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (not_id, user_id)
        )
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_erp_notlar_hatirlatma
        ON erp_notlar (durum, hatirlatma_zamani)
        """
    )
    try:
        execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS departman TEXT")
        execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS erp_asistan_kolon_sirasi TEXT")
        execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS erp_asistan_ozet_kolon_sirasi TEXT")
    except Exception as e:
        print(f"[WARN] erp_notlar users.departman ({schema}): {type(e).__name__}")
    try:
        execute("ALTER TABLE erp_notlar ADD COLUMN IF NOT EXISTS gorusulen_kisi TEXT")
    except Exception as e:
        print(f"[WARN] erp_notlar gorusulen_kisi ({schema}): {type(e).__name__}")
    for stmt in (
        "ALTER TABLE erp_notlar ADD COLUMN IF NOT EXISTS whatsapp_gonderim_durumu TEXT",
        "ALTER TABLE erp_notlar ADD COLUMN IF NOT EXISTS whatsapp_gonderim_deneme INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE erp_notlar ADD COLUMN IF NOT EXISTS whatsapp_son_deneme_at TIMESTAMPTZ",
        "ALTER TABLE erp_notlar ADD COLUMN IF NOT EXISTS whatsapp_gonderildi_at TIMESTAMPTZ",
    ):
        try:
            execute(stmt)
        except Exception as e:
            print(f"[WARN] erp_notlar wa kolon ({schema}): {type(e).__name__}")
    try:
        execute(
            """
            UPDATE erp_notlar
            SET whatsapp_gonderim_durumu = 'gonderildi',
                whatsapp_gonderildi_at = COALESCE(whatsapp_gonderildi_at, updated_at)
            WHERE whatsapp_gonderildi_mi IS TRUE
              AND COALESCE(whatsapp_gonderim_durumu, '') = ''
            """
        )
        execute(
            """
            UPDATE erp_notlar
            SET whatsapp_gonderim_durumu = 'gonderilmeyecek'
            WHERE whatsapp_gonderilsin IS TRUE
              AND whatsapp_gonderildi_mi IS NOT TRUE
              AND COALESCE(whatsapp_gonderim_durumu, '') = ''
            """
        )
    except Exception as e:
        print(f"[WARN] erp_notlar wa gecis ({schema}): {type(e).__name__}")
    _HAZIR_SEMALAR.add(schema)


def _kolon_ekran(raw) -> str:
    ekran = str(raw or "not").strip().lower()
    return ekran if ekran in _KOLON_KUMESI else "not"


def _kolon_gecerli(raw, ekran: str = "not") -> list | None:
    kume = _KOLON_KUMESI[_kolon_ekran(ekran)]
    if not isinstance(raw, list) or len(raw) != len(kume):
        return None
    temiz = [str(x) for x in raw]
    if sorted(temiz) != sorted(kume):
        return None
    return temiz


def _kolon_sirasi_getir(ekran: str = "not") -> list:
    ensure_erp_notlar_tablolari()
    ekran = _kolon_ekran(ekran)
    alan = _KOLON_ALAN[ekran]
    try:
        row = fetch_one(
            f"SELECT {alan} FROM users WHERE id = %s",
            (_uid(),),
        )
        ham = json.loads((row or {}).get(alan) or "")
    except Exception:
        ham = None
    return _kolon_gecerli(ham, ekran) or list(_KOLON_KUMESI[ekran])


def _uid() -> int:
    return int(current_user.id)


def _benim_departman() -> str:
    try:
        row = fetch_one(
            "SELECT COALESCE(departman, '') AS departman FROM users WHERE id = %s",
            (_uid(),),
        )
    except Exception:
        return ""
    return str((row or {}).get("departman") or "").strip()


def _gorunur_sql() -> str:
    return """
    (
      n.olusturan_kullanici_id = %s
      OR (
        n.gorunurluk = 'ekip'
        AND (
          EXISTS (
            SELECT 1 FROM erp_not_alicilar a
            WHERE a.not_id = n.id AND a.user_id = %s
          )
          OR (
            COALESCE(n.departman, '') <> ''
            AND n.departman = %s
          )
        )
      )
    )
    """


def _gorunur_params() -> tuple:
    uid = _uid()
    return (uid, uid, _benim_departman())


def _dt_iso(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=IST)
        return value.astimezone(IST).isoformat(timespec="minutes")
    return str(value)


def _dt_etiket(value) -> str:
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    local = value.astimezone(IST)
    return local.strftime("%d.%m.%Y %H:%M")


def _parse_dt(raw) -> datetime | None:
    s = str(raw or "").strip().replace(" ", "T")
    if not s:
        return None
    if len(s) == 16:
        s += ":00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def _dt_gun(value) -> str:
    if not isinstance(value, datetime):
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return value.astimezone(IST).strftime("%Y-%m-%d")


def _musteri_hedef_id(row: dict):
    tip = str(row.get("iliski_tip") or "")
    if tip == "musteri" and row.get("iliski_id"):
        return int(row["iliski_id"])
    for key in ("sozlesme_musteri_id", "fatura_musteri_id", "oda_musteri_id"):
        if row.get(key):
            return int(row[key])
    return None


def _detay_url(row: dict) -> str:
    """Müşteri veya sözleşmenin müşterisi → Giriş / Sözleşmeler kartı."""
    mid = _musteri_hedef_id(row)
    if mid:
        return f"/giris/?mid={mid}&tab=sozlesmeler"
    nid = row.get("id")
    if nid:
        return f"/erp-notlar/?vurgu={nid}"
    return "/erp-notlar/?durum=bekliyor"


def _iliski_etiket(row: dict) -> str:
    tip = str(row.get("iliski_tip") or "")
    if tip == "musteri":
        ad = str(row.get("musteri_adi") or "").strip()
        return ad or (f"Müşteri #{row.get('iliski_id')}" if row.get("iliski_id") else "")
    if tip == "sozlesme":
        no = str(row.get("sozlesme_no") or "").strip()
        return f"Sözleşme {no}" if no else f"Sözleşme #{row.get('iliski_id') or ''}"
    if tip == "fatura":
        no = str(row.get("fatura_no") or "").strip()
        return f"Fatura {no}" if no else f"Fatura #{row.get('iliski_id') or ''}"
    if tip == "oda":
        no = str(row.get("office_number") or "").strip()
        return f"Oda {no}" if no else f"Oda #{row.get('iliski_id') or ''}"
    return ""


_WA_ETIKET = {
    "gonderildi": "Gönderildi",
    "gonderiliyor": "Gönderiliyor",
    "bekliyor": "Gönderiliyor",
    "basarisiz": "Gönderilemedi",
    "basarisiz_kalici": "Gönderilemedi",
    "gonderilmeyecek": "Gönderilmeyecek",
    "belirsiz": "Gönderim doğrulanamadı, WhatsApp'ı kontrol edin",
}


def _wa_durum_etiket(row: dict) -> str:
    if not row.get("whatsapp_gonderilsin"):
        return ""
    durum = str(row.get("whatsapp_gonderim_durumu") or "").strip()
    if durum in _WA_ETIKET:
        return _WA_ETIKET[durum]
    if row.get("whatsapp_gonderildi_mi"):
        return "Gönderildi"
    return "Gönderilmeyecek"


def _wa_uyari(row: dict) -> str:
    durum = str(row.get("whatsapp_gonderim_durumu") or "").strip()
    if durum == "belirsiz":
        return "Gönderim doğrulanamadı, WhatsApp'ı kontrol edin"
    hata = str(row.get("whatsapp_hata") or "").strip()
    if durum in ("basarisiz", "basarisiz_kalici") and hata == "baglanti":
        return "WhatsApp servisi şu an kapalı"
    if durum in ("basarisiz", "basarisiz_kalici"):
        return "Gönderilemedi"
    return ""


def _wa_rozet(row: dict) -> str:
    return _wa_durum_etiket(row)


def _serialize(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "kategori": row.get("kategori") or "",
        "iliski_tip": row.get("iliski_tip") or "",
        "iliski_id": row.get("iliski_id"),
        "iliski_etiket": _iliski_etiket(row),
        "not_metni": row.get("not_metni") or "",
        "hatirlatma_zamani": _dt_iso(row.get("hatirlatma_zamani")),
        "hatirlatma_etiket": _dt_etiket(row.get("hatirlatma_zamani")),
        "hatirlatma_gun": _dt_gun(row.get("hatirlatma_zamani")),
        "created_at": _dt_iso(row.get("created_at")),
        "created_etiket": _dt_etiket(row.get("created_at")),
        "gorusulen_kisi": str(row.get("gorusulen_kisi") or "").strip(),
        "olusturan_ad": str(row.get("olusturan_ad") or "").strip(),
        "detay_url": _detay_url(row),
        "durum": row.get("durum") or "",
        "gorunurluk": row.get("gorunurluk") or "",
        "departman": row.get("departman") or "",
        "whatsapp_gonderilsin": bool(row.get("whatsapp_gonderilsin")),
        "whatsapp_telefon": row.get("whatsapp_telefon") or "",
        "whatsapp_gonderildi_mi": bool(row.get("whatsapp_gonderildi_mi")),
        "whatsapp_hata": row.get("whatsapp_hata") or "",
        "whatsapp_gonderim_durumu": row.get("whatsapp_gonderim_durumu") or "",
        "whatsapp_rozet": _wa_rozet(row),
        "whatsapp_durum_etiket": _wa_durum_etiket(row),
        "whatsapp_uyari": _wa_uyari(row),
        "olusturan_kullanici_id": row.get("olusturan_kullanici_id"),
    }


_LISTE_FROM = """
    FROM erp_notlar n
    LEFT JOIN customers c
      ON n.iliski_tip = 'musteri' AND c.id = n.iliski_id
    LEFT JOIN sozlesmeler s
      ON n.iliski_tip = 'sozlesme' AND s.id = n.iliski_id
    LEFT JOIN faturalar f
      ON n.iliski_tip = 'fatura' AND f.id = n.iliski_id
    LEFT JOIN offices o
      ON n.iliski_tip = 'oda' AND o.id = n.iliski_id
    LEFT JOIN users u
      ON u.id = n.olusturan_kullanici_id
"""

_LISTE_COLS = """
    n.*,
    c.name AS musteri_adi,
    c.phone AS musteri_telefon,
    s.sozlesme_no,
    s.musteri_id AS sozlesme_musteri_id,
    f.fatura_no,
    f.musteri_id AS fatura_musteri_id,
    o.code AS office_number,
    o.customer_id AS oda_musteri_id,
    COALESCE(NULLIF(BTRIM(u.full_name), ''), NULLIF(BTRIM(u.username), ''), '') AS olusturan_ad
"""


def _notlari_getir(extra_sql: str, extra_params: tuple, limit: int = 100, sira: str = "hatirlatma") -> list:
    ensure_erp_notlar_tablolari()
    params = _gorunur_params() + tuple(extra_params)
    order = "n.created_at DESC, n.id DESC" if sira == "yeni" else "n.hatirlatma_zamani ASC, n.id ASC"
    sql = (
        f"SELECT {_LISTE_COLS} {_LISTE_FROM} WHERE {_gorunur_sql()} {extra_sql} "
        f"ORDER BY {order} LIMIT {int(limit)}"
    )
    try:
        return fetch_all(sql, params)
    except Exception as e:
        print(f"[WARN] erp_notlar liste join: {type(e).__name__}")
        extra_sade = extra_sql.replace("c.name", "n.not_metni")
        sql2 = (
            f"SELECT n.* FROM erp_notlar n WHERE {_gorunur_sql()} {extra_sade} "
            f"ORDER BY {order} LIMIT {int(limit)}"
        )
        return fetch_all(sql2, params)


@bp.route("/")
@giris_gerekli
def liste():
    ensure_erp_notlar_tablolari()
    gorunum = str(request.args.get("gorunum") or "liste").strip().lower()
    if gorunum not in ("liste", "takvim"):
        gorunum = "liste"
    durum = str(request.args.get("durum") or "bekliyor").strip().lower()
    if durum not in DURUMLAR and durum != "hepsi":
        durum = "bekliyor"
    q = str(request.args.get("q") or "").strip()
    vurgu = request.args.get("vurgu")
    extra = []
    params: list = []
    if durum in DURUMLAR:
        extra.append("AND n.durum = %s")
        params.append(durum)
    if q:
        extra.append(
            "AND (n.not_metni ILIKE %s OR n.kategori ILIKE %s OR COALESCE(c.name, '') ILIKE %s)"
        )
        like = "%" + q.replace("%", "") + "%"
        params.extend([like, like, like])
    rows = _notlari_getir(" ".join(extra), tuple(params), limit=200, sira="yeni")
    return render_template(
        "erp_notlar/liste.html",
        notlar=[_serialize(r) for r in rows],
        kolonlar=_kolon_sirasi_getir(),
        kolon_etiket=KOLON_ETIKET,
        durum=durum,
        q=q,
        vurgu=str(vurgu or ""),
        gorunum=gorunum,
    )


@bp.route("/api/meta")
@giris_gerekli
def api_meta():
    ensure_erp_notlar_tablolari()
    kisiler = []
    try:
        kisiler = fetch_all(
            """
            SELECT id, COALESCE(full_name, '') AS full_name,
                   COALESCE(departman, '') AS departman
            FROM users
            WHERE COALESCE(is_active, TRUE) = TRUE
            ORDER BY full_name, id
            """
        )
    except Exception:
        kisiler = fetch_all(
            """
            SELECT id, COALESCE(full_name, '') AS full_name, '' AS departman
            FROM users
            WHERE COALESCE(is_active, TRUE) = TRUE
            ORDER BY full_name, id
            """
        )
    departmanlar = set()
    for k in kisiler:
        d = str(k.get("departman") or "").strip()
        if d:
            departmanlar.add(d)
    try:
        for row in fetch_all(
            """
            SELECT DISTINCT departman
            FROM personel
            WHERE COALESCE(btrim(departman), '') <> ''
            """
        ):
            d = str(row.get("departman") or "").strip()
            if d:
                departmanlar.add(d)
    except Exception:
        pass
    return jsonify(
        {
            "ok": True,
            "kategoriler": list(KATEGORILER),
            "kisiler": [
                {
                    "id": k.get("id"),
                    "ad": k.get("full_name") or f"Kullanıcı #{k.get('id')}",
                    "departman": k.get("departman") or "",
                }
                for k in kisiler
            ],
            "departmanlar": sorted(departmanlar),
        }
    )


@bp.route("/api/kolonlar", methods=["GET", "POST"])
@giris_gerekli
def api_kolonlar():
    data = request.get_json(silent=True) or {}
    ekran = _kolon_ekran(request.args.get("ekran") or data.get("ekran"))
    if request.method == "POST":
        sira = _kolon_gecerli(data.get("siralama"), ekran)
        if not sira:
            return jsonify({"ok": False, "mesaj": "Kolon sırası geçersiz"}), 400
        ensure_erp_notlar_tablolari()
        execute(
            f"UPDATE users SET {_KOLON_ALAN[ekran]} = %s WHERE id = %s",
            (json.dumps(sira, ensure_ascii=False), _uid()),
        )
        return jsonify({"ok": True, "ekran": ekran, "siralama": sira})
    return jsonify({"ok": True, "ekran": ekran, "siralama": _kolon_sirasi_getir(ekran)})


@bp.route("/api/musteri/<int:mid>")
@giris_gerekli
def api_musteri(mid: int):
    ensure_erp_notlar_tablolari()
    row = fetch_one(
        "SELECT id, name, phone FROM customers WHERE id = %s",
        (mid,),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı"}), 404
    return jsonify(
        {
            "ok": True,
            "id": row.get("id"),
            "ad": row.get("name") or "",
            "telefon": row.get("phone") or "",
        }
    )


_HEDEF_SQL = """
CASE
  WHEN n.iliski_tip = 'musteri' THEN n.iliski_id
  WHEN n.iliski_tip = 'sozlesme' THEN s.musteri_id
  WHEN n.iliski_tip = 'fatura' THEN f.musteri_id
  WHEN n.iliski_tip = 'oda' THEN o.customer_id
END
"""


def _ozet_serialize(row: dict) -> dict:
    kopya = dict(row)
    hid = row.get("hedef_id")
    kopya["iliski_tip"] = "musteri"
    kopya["iliski_id"] = hid
    kopya["musteri_adi"] = row.get("ozet_musteri_adi") or row.get("musteri_adi")
    out = _serialize(kopya)
    out["musteri_id"] = hid
    return out


def _musteri_notlari(mid: int) -> list:
    extra = f"""
      AND ({_HEDEF_SQL}) = %s
    """
    return _notlari_getir(extra, (mid,), limit=200, sira="yeni")


_OZET_TARIH_KOLON = {
    "gorusme": "ozet.created_at",
    "hatirlatma": "ozet.hatirlatma_zamani",
}


def _gun_basi(gun: date) -> datetime:
    return datetime(gun.year, gun.month, gun.day, tzinfo=IST)


def _gun_cozumle(raw) -> date | None:
    s = str(raw or "").strip()[:10]
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _ozet_tarih_penceresi(tarih: str, bas_raw, bitis_raw):
    """İstanbul günü. Dönüş: (başlangıç, bitiş hariç, hata).

    Bitiş ertesi gün 00:00 olduğu için seçilen günün 23:59:59'u aralığa girer.
    """
    sec = str(tarih or "hepsi").strip().lower()
    bugun = datetime.now(IST).date()
    if sec in ("", "hepsi"):
        return None, None, None
    if sec == "bugun":
        return _gun_basi(bugun), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "dun":
        dun = bugun - timedelta(days=1)
        return _gun_basi(dun), _gun_basi(bugun), None
    if sec == "bu_hafta":
        pazartesi = bugun - timedelta(days=bugun.weekday())
        return _gun_basi(pazartesi), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "bu_ay":
        return _gun_basi(bugun.replace(day=1)), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "son_7":
        return _gun_basi(bugun - timedelta(days=6)), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "son_30":
        return _gun_basi(bugun - timedelta(days=29)), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "ozel":
        bas = _gun_cozumle(bas_raw)
        bitis = _gun_cozumle(bitis_raw)
        if bas and bitis and bas > bitis:
            return None, None, "Başlangıç tarihi bitişten sonra olamaz"
        return (
            _gun_basi(bas) if bas else None,
            _gun_basi(bitis + timedelta(days=1)) if bitis else None,
            None,
        )
    return None, None, None


@bp.route("/api/ozet")
@giris_gerekli
def api_ozet():
    ensure_erp_notlar_tablolari()
    kapsam = str(request.args.get("kapsam") or "acik").strip().lower()
    if kapsam not in ("acik", "hepsi"):
        kapsam = "acik"
    q = str(request.args.get("q") or "").strip()
    like = "%" + q.replace("%", "") + "%"
    turu = str(request.args.get("tarih_turu") or "gorusme").strip().lower()
    if turu not in _OZET_TARIH_KOLON:
        turu = "gorusme"
    kolon = _OZET_TARIH_KOLON[turu]
    bas, bitis_haric, tarih_hata = _ozet_tarih_penceresi(
        request.args.get("tarih"),
        request.args.get("bas"),
        request.args.get("bitis"),
    )
    if tarih_hata:
        return jsonify({"ok": False, "mesaj": tarih_hata}), 400
    tarih_sql = ""
    tarih_params: tuple = ()
    if str(request.args.get("tarih") or "hepsi").strip().lower() not in ("", "hepsi"):
        tarih_sql = f"""
          AND {kolon} IS NOT NULL
          AND (%s::timestamptz IS NULL OR {kolon} >= %s::timestamptz)
          AND (%s::timestamptz IS NULL OR {kolon} < %s::timestamptz)
        """
        tarih_params = (bas, bas, bitis_haric, bitis_haric)
    sql = f"""
        SELECT * FROM (
            SELECT DISTINCT ON ({_HEDEF_SQL})
                {_LISTE_COLS},
                {_HEDEF_SQL} AS hedef_id,
                cm.name AS ozet_musteri_adi
            {_LISTE_FROM}
            LEFT JOIN customers cm ON cm.id = ({_HEDEF_SQL})
            WHERE {_gorunur_sql()}
              AND ({_HEDEF_SQL}) IS NOT NULL
            ORDER BY {_HEDEF_SQL}, n.created_at DESC, n.id DESC
        ) ozet
        WHERE (%s = 'hepsi' OR ozet.durum IN ('bekliyor', 'ertelendi'))
          AND (%s = '' OR COALESCE(ozet.ozet_musteri_adi, '') ILIKE %s)
          {tarih_sql}
        ORDER BY CASE WHEN ozet.durum IN ('bekliyor', 'ertelendi') THEN 0 ELSE 1 END,
                 COALESCE(ozet.ozet_musteri_adi, ''), ozet.id DESC
        LIMIT 500
    """
    params = _gorunur_params() + (kapsam, q, like) + tarih_params
    try:
        rows = fetch_all(sql, params)
    except Exception as e:
        print(f"[WARN] erp_notlar ozet: {type(e).__name__}")
        return jsonify({"ok": False, "mesaj": "Özet alınamadı"}), 500
    return jsonify({"ok": True, "notlar": [_ozet_serialize(r) for r in rows]})


@bp.route("/api/musteri/<int:mid>/notlar")
@giris_gerekli
def api_musteri_notlar(mid: int):
    rows = _musteri_notlari(mid)
    return jsonify({"ok": True, "notlar": [_serialize(r) for r in rows]})


@bp.route("/api/liste")
@giris_gerekli
def api_liste():
    durum = str(request.args.get("durum") or "").strip().lower()
    iliski_tip = str(request.args.get("iliski_tip") or "").strip().lower()
    iliski_id = request.args.get("iliski_id")
    q = str(request.args.get("q") or "").strip()
    bas = _parse_dt(str(request.args.get("bas") or "")[:10])
    bitis = _parse_dt(str(request.args.get("bitis") or "")[:10])
    extra = []
    params: list = []
    if bas:
        extra.append("AND n.hatirlatma_zamani >= %s")
        params.append(bas)
    if bitis:
        extra.append("AND n.hatirlatma_zamani < %s")
        params.append(bitis + timedelta(days=1))
    if durum in DURUMLAR:
        extra.append("AND n.durum = %s")
        params.append(durum)
    if iliski_tip in ILISKI_TIPLERI and str(iliski_id or "").isdigit():
        extra.append("AND n.iliski_tip = %s AND n.iliski_id = %s")
        params.extend([iliski_tip, int(iliski_id)])
    if q:
        extra.append(
            "AND (n.not_metni ILIKE %s OR COALESCE(c.name, '') ILIKE %s OR n.kategori ILIKE %s)"
        )
        like = "%" + q.replace("%", "") + "%"
        params.extend([like, like, like])
    limit = 500 if (bas or bitis) else 100
    sira = "hatirlatma" if (bas or bitis) else "yeni"
    rows = _notlari_getir(" ".join(extra), tuple(params), limit=limit, sira=sira)
    return jsonify({"ok": True, "notlar": [_serialize(r) for r in rows]})


@bp.route("/api/bekleyenler")
@giris_gerekli
def api_bekleyenler():
    """Hatırlatma zamanı gelmiş, bekleyen veya ertelenmiş notlar.

    Ertele sonrası durum 'ertelendi' olur; yeni saat gelince kart yine çıkar.
    """
    rows = _notlari_getir(
        """
        AND n.durum IN ('bekliyor', 'ertelendi')
        AND n.hatirlatma_zamani <= NOW()
        """,
        (),
        limit=20,
    )
    return jsonify({"ok": True, "notlar": [_serialize(r) for r in rows]})


@bp.route("/api/olustur", methods=["POST"])
@giris_gerekli
def api_olustur():
    ensure_erp_notlar_tablolari()
    data = request.get_json(silent=True) or {}
    kategori = str(data.get("kategori") or "").strip()
    if kategori not in KATEGORILER:
        return jsonify({"ok": False, "mesaj": "Kategori geçersiz"}), 400
    metin = str(data.get("not_metni") or "").strip()
    if not metin:
        return jsonify({"ok": False, "mesaj": "Not metni gerekli"}), 400
    hat = _parse_dt(data.get("hatirlatma_zamani"))
    if not hat:
        return jsonify({"ok": False, "mesaj": "Tarih ve saat gerekli"}), 400
    gorunurluk = str(data.get("gorunurluk") or "kisisel").strip().lower()
    if gorunurluk not in ("kisisel", "ekip"):
        gorunurluk = "kisisel"
    iliski_tip = str(data.get("iliski_tip") or "").strip().lower()
    if iliski_tip not in ILISKI_TIPLERI:
        iliski_tip = ""
    iliski_id = data.get("iliski_id")
    try:
        iliski_id = int(iliski_id) if iliski_id not in (None, "") else None
    except (TypeError, ValueError):
        iliski_id = None
    departman = str(data.get("departman") or "").strip()[:120]
    user_ids = []
    for raw in data.get("kullanici_ids") or []:
        try:
            user_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    user_ids = sorted(set(user_ids))
    if gorunurluk == "kisisel":
        departman = ""
        user_ids = []
    elif not user_ids and not departman:
        return jsonify(
            {"ok": False, "mesaj": "Ekip notu için kullanıcı veya departman seçin"}
        ), 400
    gorusulen = str(data.get("gorusulen_kisi") or "").strip()[:200]
    wa = bool(data.get("whatsapp_gonderilsin"))
    telefon = re.sub(r"[^\d+]", "", str(data.get("whatsapp_telefon") or ""))[:32]
    wa_mesaj = str(data.get("whatsapp_mesaj") or "").strip()
    if wa and not telefon:
        return jsonify({"ok": False, "mesaj": "WhatsApp için telefon gerekli"}), 400
    row = execute_returning(
        """
        INSERT INTO erp_notlar (
            olusturan_kullanici_id, kategori, iliski_tip, iliski_id, not_metni,
            hatirlatma_zamani, durum, gorunurluk, departman,
            whatsapp_gonderilsin, whatsapp_telefon, whatsapp_mesaj,
            whatsapp_gonderim_durumu, gorusulen_kisi
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, 'bekliyor', %s, %s,
            %s, %s, %s,
            %s, %s
        )
        RETURNING id
        """,
        (
            _uid(),
            kategori,
            iliski_tip or None,
            iliski_id,
            metin,
            hat,
            gorunurluk,
            departman or None,
            wa,
            telefon or None,
            wa_mesaj or None,
            "bekliyor" if wa else "gonderilmeyecek",
            gorusulen or None,
        ),
    )
    nid = (row or {}).get("id")
    if nid and user_ids:
        for uid in user_ids:
            execute(
                """
                INSERT INTO erp_not_alicilar (not_id, user_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (nid, uid),
            )
    if wa and nid:
        _wa_arkaplan_baslat(int(nid))
    return jsonify({"ok": True, "id": nid})


def _guncelle_gorunur(nid: int, set_sql: str, set_params: tuple) -> bool:
    ensure_erp_notlar_tablolari()
    sql = (
        f"UPDATE erp_notlar n SET {set_sql}, updated_at = NOW() "
        f"WHERE n.id = %s AND {_gorunur_sql()}"
    )
    params = tuple(set_params) + (nid,) + _gorunur_params()
    return execute(sql, params) > 0


def _not_tek(nid: int):
    rows = _notlari_getir("AND n.id = %s", (nid,), limit=1)
    return rows[0] if rows else None


@bp.route("/api/<int:nid>", methods=["GET"])
@giris_gerekli
def api_detay(nid: int):
    row = _not_tek(nid)
    if not row:
        return jsonify({"ok": False, "mesaj": "Not bulunamadı"}), 404
    return jsonify({"ok": True, "not": _serialize(row)})


@bp.route("/api/<int:nid>/guncelle", methods=["POST"])
@giris_gerekli
def api_guncelle(nid: int):
    data = request.get_json(silent=True) or {}
    metin = str(data.get("not_metni") or "").strip()
    if not metin:
        return jsonify({"ok": False, "mesaj": "Açıklama gerekli"}), 400
    hat = _parse_dt(data.get("hatirlatma_zamani"))
    if not hat:
        return jsonify({"ok": False, "mesaj": "Hatırlatma tarihi gerekli"}), 400
    kisi = str(data.get("gorusulen_kisi") or "").strip()[:200]
    if not _guncelle_gorunur(
        nid,
        "not_metni = %s, hatirlatma_zamani = %s, gorusulen_kisi = %s",
        (metin, hat, kisi or None),
    ):
        return jsonify({"ok": False, "mesaj": "Not bulunamadı"}), 404
    row = _not_tek(nid)
    return jsonify({"ok": True, "not": _serialize(row) if row else {}})


@bp.route("/api/<int:nid>/tamamla", methods=["POST"])
@giris_gerekli
def api_tamamla(nid: int):
    if not _guncelle_gorunur(nid, "durum = 'tamamlandi'", ()):
        return jsonify({"ok": False, "mesaj": "Not bulunamadı"}), 404
    return jsonify({"ok": True})


@bp.route("/api/<int:nid>/ertele", methods=["POST"])
@giris_gerekli
def api_ertele(nid: int):
    data = request.get_json(silent=True) or {}
    secim = str(data.get("sure") or data.get("dakika") or "").strip().lower()
    now = datetime.now(IST)
    if secim in ("5", "5dk"):
        yeni = now + timedelta(minutes=5)
    elif secim in ("15", "15dk"):
        yeni = now + timedelta(minutes=15)
    elif secim in ("60", "1sa", "1saat"):
        yeni = now + timedelta(hours=1)
    elif secim in ("yarin", "yarın"):
        yeni = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        return jsonify({"ok": False, "mesaj": "Erteleme süresi geçersiz"}), 400
    if not _guncelle_gorunur(
        nid,
        "durum = 'ertelendi', hatirlatma_zamani = %s",
        (yeni,),
    ):
        return jsonify({"ok": False, "mesaj": "Not bulunamadı"}), 404
    return jsonify({"ok": True, "hatirlatma_zamani": _dt_iso(yeni)})


_WA_KAPALI = (
    "gonderildi",
    "gonderiliyor",
    "gonderilmeyecek",
    "basarisiz_kalici",
    "belirsiz",
)


def _wa_otuz_doldu(created_at) -> bool:
    if not created_at:
        return False
    an = created_at
    if getattr(an, "tzinfo", None) is None:
        an = an.replace(tzinfo=IST)
    return datetime.now(IST) - an >= timedelta(minutes=30)


def _wa_sonraki_basarisiz(deneme, created_at) -> tuple[str, int]:
    yeni = int(deneme or 0) + 1
    if yeni >= 5 or _wa_otuz_doldu(created_at):
        return "basarisiz_kalici", yeni
    return "basarisiz", yeni


def _wa_sahiplen(nid: int):
    return execute_returning(
        """
        UPDATE erp_notlar
        SET whatsapp_gonderim_durumu = 'gonderiliyor',
            whatsapp_son_deneme_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
          AND COALESCE(whatsapp_gonderim_durumu, '') <> ALL (%s)
        RETURNING id, whatsapp_telefon, whatsapp_mesaj, not_metni,
                  whatsapp_gonderim_deneme, created_at
        """,
        (nid, list(_WA_KAPALI)),
    )


def _wa_gonderildi_yaz(nid: int) -> None:
    execute(
        """
        UPDATE erp_notlar
        SET whatsapp_gonderim_durumu = 'gonderildi',
            whatsapp_gonderildi_mi = TRUE,
            whatsapp_gonderildi_at = NOW(),
            whatsapp_hata = NULL,
            updated_at = NOW()
        WHERE id = %s AND whatsapp_gonderim_durumu = 'gonderiliyor'
        """,
        (nid,),
    )


def _wa_belirsiz_yaz(nid: int) -> None:
    execute(
        """
        UPDATE erp_notlar
        SET whatsapp_gonderim_durumu = 'belirsiz',
            whatsapp_hata = 'yanit_yok',
            updated_at = NOW()
        WHERE id = %s AND whatsapp_gonderim_durumu = 'gonderiliyor'
        """,
        (nid,),
    )


def _wa_basarisiz_yaz(nid: int, deneme, created_at, hata_turu: str) -> str:
    durum, yeni = _wa_sonraki_basarisiz(deneme, created_at)
    execute(
        """
        UPDATE erp_notlar
        SET whatsapp_gonderim_durumu = %s,
            whatsapp_gonderim_deneme = %s,
            whatsapp_hata = %s,
            updated_at = NOW()
        WHERE id = %s AND whatsapp_gonderim_durumu = 'gonderiliyor'
        """,
        (durum, yeni, hata_turu[:40], nid),
    )
    return durum


def _wa_node_gonder(nid: int, telefon: str, mesaj: str, schema: str | None, anahtar: str | None = None):
    """Dönüş: ('ok'|'belirsiz'|'hata', hata_turu)."""
    from routes.whatsapp_routes import _wa_internal_headers, _wa_url

    anahtar = (str(anahtar or "").strip() or f"{schema or 'public'}:{int(nid)}")[:120]
    try:
        r = requests.post(
            _wa_url("kuyruk-toplu-ekle"),
            json={"liste": [{"telefon": telefon, "mesaj": mesaj, "anahtar": anahtar}]},
            headers=_wa_internal_headers(),
            timeout=(3, 12),
        )
    except requests.exceptions.ConnectTimeout:
        return "hata", "baglanti"
    except requests.exceptions.ConnectionError:
        return "hata", "baglanti"
    except requests.exceptions.Timeout:
        return "belirsiz", "yanit_yok"
    except Exception as e:
        print(f"[WARN] erp_not_whatsapp not_id={nid} hata={type(e).__name__}")
        return "belirsiz", "yanit_yok"
    body = {}
    try:
        body = r.json() if r.content else {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if r.status_code >= 400 or body.get("ok") is False:
        print(f"[WARN] erp_not_whatsapp not_id={nid} http={r.status_code}")
        return "hata", "http"
    return "ok", ""


def _wa_numara_kayitli(telefon: str):
    """Dönüş: ('kayitli'|'yok'|'hata', neden). Telefon ve mesaj loglanmaz."""
    from routes.whatsapp_routes import _wa_internal_headers, _wa_url

    try:
        r = requests.post(
            _wa_url("numara-kontrol"),
            json={"telefon": telefon},
            headers=_wa_internal_headers(),
            timeout=(3, 8),
        )
    except requests.exceptions.ConnectTimeout:
        return "hata", "baglanti"
    except requests.exceptions.ConnectionError:
        return "hata", "baglanti"
    except requests.exceptions.Timeout:
        return "hata", "yanit_yok"
    except Exception as e:
        print(f"[WARN] wa numara hata={type(e).__name__}")
        return "hata", "yanit_yok"
    body = {}
    try:
        body = r.json() if r.content else {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if r.status_code >= 400 or body.get("ok") is False:
        neden = str(body.get("neden") or "http")[:32]
        if neden not in ("gecersiz", "hazir_degil", "hata", "http", "baglanti", "yanit_yok"):
            neden = "http"
        return "hata", neden
    if body.get("kayitli") is True:
        return "kayitli", ""
    return "yok", ""


def _wa_gonder_bir(nid: int, schema: str | None = None) -> str:
    row = _wa_sahiplen(nid)
    if not row:
        return "atlandi"
    tel = str(row.get("whatsapp_telefon") or "").strip()
    mesaj = str(row.get("whatsapp_mesaj") or "").strip() or str(row.get("not_metni") or "").strip()
    if not tel:
        _wa_basarisiz_yaz(nid, 4, row.get("created_at"), "telefon_yok")
        return "basarisiz_kalici"
    if not mesaj:
        _wa_basarisiz_yaz(nid, 4, row.get("created_at"), "mesaj_yok")
        return "basarisiz_kalici"
    sonuc, tur = _wa_node_gonder(nid, tel, mesaj, schema)
    if sonuc == "ok":
        _wa_gonderildi_yaz(nid)
        return "gonderildi"
    if sonuc == "belirsiz":
        _wa_belirsiz_yaz(nid)
        return "belirsiz"
    return _wa_basarisiz_yaz(nid, row.get("whatsapp_gonderim_deneme"), row.get("created_at"), tur or "http")


def _wa_arkaplan(app, schema, nid: int) -> None:
    with app.app_context():
        try:
            if schema:
                g.tenant_schema = schema
            else:
                g.pop("tenant_schema", None)
            _wa_gonder_bir(nid, schema)
        except Exception as e:
            print(f"[WARN] erp_not_whatsapp arkaplan not_id={nid} hata={type(e).__name__}")
        finally:
            try:
                g.pop("tenant_schema", None)
            except Exception:
                pass


def _wa_arkaplan_baslat(nid: int) -> None:
    schema = _tenant_schema_for_request()
    app = current_app._get_current_object()
    threading.Thread(
        target=_wa_arkaplan,
        args=(app, schema, nid),
        daemon=True,
        name=f"wa-not-{nid}",
    ).start()


def _wa_takili_toparla(only_ids=None) -> None:
    id_sql = ""
    params: list = []
    if only_ids:
        id_sql = " AND id = ANY(%s)"
        params.append(list(only_ids))
    execute(
        f"""
        UPDATE erp_notlar
        SET whatsapp_gonderim_deneme = COALESCE(whatsapp_gonderim_deneme, 0) + 1,
            whatsapp_gonderim_durumu = CASE
                WHEN COALESCE(whatsapp_gonderim_deneme, 0) + 1 >= 5
                  OR created_at < NOW() - INTERVAL '30 minutes'
                THEN 'basarisiz_kalici'
                ELSE 'basarisiz'
            END,
            whatsapp_hata = 'surec_kesildi',
            updated_at = NOW()
        WHERE whatsapp_gonderim_durumu = 'gonderiliyor'
          AND whatsapp_son_deneme_at IS NOT NULL
          AND whatsapp_son_deneme_at < NOW() - INTERVAL '3 minutes'
          {id_sql}
        """,
        tuple(params),
    )


def gonder_whatsapp_notlari(only_ids=None) -> dict:
    """Yalnız tekrar deneme. Hatırlatma saatine bakmaz.

    basarisiz, deneme < 5, son 30 dakikada açılmış ve son denemeden
    en az 2 dakika geçmiş notlar. 5. deneme veya 30 dakika sonra kalıcı.
    """
    ensure_erp_notlar_tablolari()
    _wa_takili_toparla(only_ids)
    params: list = []
    id_sql = ""
    if only_ids:
        id_sql = " AND id = ANY(%s)"
        params.append(list(only_ids))
    execute(
        f"""
        UPDATE erp_notlar
        SET whatsapp_gonderim_durumu = 'basarisiz_kalici',
            updated_at = NOW()
        WHERE whatsapp_gonderim_durumu = 'basarisiz'
          AND (
            COALESCE(whatsapp_gonderim_deneme, 0) >= 5
            OR created_at < NOW() - INTERVAL '30 minutes'
          )
          {id_sql}
        """,
        tuple(params),
    )
    rows = fetch_all(
        f"""
        SELECT id
        FROM erp_notlar
        WHERE whatsapp_gonderim_durumu = 'basarisiz'
          AND COALESCE(whatsapp_gonderim_deneme, 0) < 5
          AND created_at >= NOW() - INTERVAL '30 minutes'
          AND (
            whatsapp_son_deneme_at IS NULL
            OR whatsapp_son_deneme_at <= NOW() - INTERVAL '2 minutes'
          )
          {id_sql}
        ORDER BY created_at
        LIMIT 30
        """,
        tuple(params),
    )
    schema = _tenant_schema_for_request()
    gonderilen = 0
    hata = 0
    belirsiz = 0
    for row in rows or []:
        sonuc = _wa_gonder_bir(int(row["id"]), schema)
        if sonuc == "gonderildi":
            gonderilen += 1
        elif sonuc == "belirsiz":
            belirsiz += 1
        elif sonuc != "atlandi":
            hata += 1
    return {
        "gonderilen": gonderilen,
        "hata": hata,
        "belirsiz": belirsiz,
        "adet": len(rows or []),
    }


def run_erp_not_whatsapp_job() -> None:
    """Public ve kayıtlı tenant şemalarında dakikalık WhatsApp taraması."""
    import app as app_mod
    from flask import g

    semalar = [None]
    try:
        rows = fetch_all(
            """
            SELECT DISTINCT schema_name
            FROM public.tenants
            WHERE schema_name IS NOT NULL
              AND btrim(schema_name) <> ''
            """
        )
        for row in rows or []:
            sch = str((row or {}).get("schema_name") or "").strip()
            if sch and sch != "public" and re.fullmatch(r"tenant_[a-z0-9_]+", sch):
                semalar.append(sch)
    except Exception as e:
        print(f"[WARN] erp_not_whatsapp tenant list: {type(e).__name__}")

    with app_mod.app.app_context():
        for sch in semalar:
            try:
                if sch:
                    g.tenant_schema = sch
                else:
                    g.pop("tenant_schema", None)
                # Tablo yoksa her kiracıda her dakika DDL çalışmasın.
                if sch:
                    var = fetch_one(
                        """
                        SELECT 1 AS ok
                        FROM information_schema.tables
                        WHERE table_schema = %s AND table_name = 'erp_notlar'
                        """,
                        (sch,),
                    )
                    if not var:
                        continue
                gonder_whatsapp_notlari()
                if not sch:
                    try:
                        from odeme_linki import whatsapp_yeniden_dene

                        whatsapp_yeniden_dene()
                    except Exception as oe:
                        print(f"[WARN] odeme_link_whatsapp {type(oe).__name__}")
            except Exception as e:
                print(f"[WARN] erp_not_whatsapp {sch or 'public'}: {type(e).__name__}")
            finally:
                try:
                    g.pop("tenant_schema", None)
                except Exception:
                    pass
