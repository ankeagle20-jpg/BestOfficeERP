"""ERP Asistan — not / hatırlatma (Faz 1).

Takvim görünümü yok. WhatsApp gönderimi pop-up'tan bağımsızdır;
APScheduler dakikada bir gonder_whatsapp_notlari çağırır.
"""
from __future__ import annotations

import json
import re
import threading
import time
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
    COALESCE(NULLIF(BTRIM(c.phone), ''), NULLIF(BTRIM(c.phone2), '')) AS musteri_telefon,
    s.sozlesme_no,
    s.musteri_id AS sozlesme_musteri_id,
    f.fatura_no,
    f.musteri_id AS fatura_musteri_id,
    o.code AS office_number,
    o.customer_id AS oda_musteri_id,
    COALESCE(NULLIF(BTRIM(u.full_name), ''), NULLIF(BTRIM(u.username), ''), '') AS olusturan_ad
"""


def _notlari_getir(
    extra_sql: str,
    extra_params: tuple,
    limit: int = 100,
    sira: str = "hatirlatma",
    offset: int = 0,
) -> list:
    ensure_erp_notlar_tablolari()
    params = _gorunur_params() + tuple(extra_params)
    order = "n.created_at DESC, n.id DESC" if sira == "yeni" else "n.hatirlatma_zamani ASC, n.id ASC"
    off = max(0, int(offset or 0))
    sql = (
        f"SELECT {_LISTE_COLS} {_LISTE_FROM} WHERE {_gorunur_sql()} {extra_sql} "
        f"ORDER BY {order} LIMIT {int(limit)} OFFSET {off}"
    )
    try:
        return fetch_all(sql, params)
    except Exception as e:
        print(f"[WARN] erp_notlar liste join: {type(e).__name__}")
        extra_sade = extra_sql.replace("c.name", "n.not_metni")
        sql2 = (
            f"SELECT n.* FROM erp_notlar n WHERE {_gorunur_sql()} {extra_sade} "
            f"ORDER BY {order} LIMIT {int(limit)} OFFSET {off}"
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


def _wa_var_maske(raw) -> tuple[bool, str]:
    from odeme_linki import normalize_wa_telefon

    tel = normalize_wa_telefon(raw)
    if not tel:
        return False, ""
    return True, "**" + tel[-2:]


def _ozet_serialize(row: dict) -> dict:
    kopya = dict(row)
    hid = row.get("hedef_id")
    kopya["iliski_tip"] = "musteri"
    kopya["iliski_id"] = hid
    kopya["musteri_adi"] = row.get("ozet_musteri_adi") or row.get("musteri_adi")
    out = _serialize(kopya)
    out["musteri_id"] = hid
    var, maske = _wa_var_maske(row.get("musteri_telefon"))
    out["wa_var"] = var
    out["wa_maske"] = maske
    for anahtar in (
        "whatsapp_telefon",
        "whatsapp_mesaj",
        "whatsapp_hata",
        "musteri_telefon",
    ):
        out.pop(anahtar, None)
    return out


def _musteri_notlari(mid: int) -> list:
    extra = f"""
      AND ({_HEDEF_SQL}) = %s
    """
    return _notlari_getir(extra, (mid,), limit=200, sira="yeni")


_GECMIS_SAYFA = 50


def _gecmis_olcu(limit, ofset) -> tuple[int, int]:
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = _GECMIS_SAYFA
    try:
        off = int(ofset)
    except (TypeError, ValueError):
        off = 0
    if lim < 1:
        lim = 1
    if lim > _GECMIS_SAYFA:
        lim = _GECMIS_SAYFA
    if off < 0:
        off = 0
    return lim, off


def _serialize_gecmis(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "created_etiket": _dt_etiket(row.get("created_at")),
        "hatirlatma_etiket": _dt_etiket(row.get("hatirlatma_zamani")),
        "durum": row.get("durum") or "",
        "olusturan_ad": str(row.get("olusturan_ad") or "").strip(),
        "gorusulen_kisi": str(row.get("gorusulen_kisi") or "").strip(),
        "not_metni": row.get("not_metni") or "",
    }


def musteri_gecmis_paket(mid: int, limit=50, ofset=0) -> dict:
    """Bir müşterinin görüşme notları, en yeni üstte. Tek sorgu; sayfa boyu en fazla 50."""
    lim, off = _gecmis_olcu(limit, ofset)
    extra = f"""
      AND ({_HEDEF_SQL}) = %s
    """
    rows = _notlari_getir(extra, (int(mid),), limit=lim + 1, sira="yeni", offset=off)
    devam = len(rows) > lim
    return {
        "ok": True,
        "notlar": [_serialize_gecmis(r) for r in rows[:lim]],
        "devam": devam,
        "ofset": off,
    }


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


_GORUSME_TARIH = frozenset({"hepsi", "bugun", "dun", "bu_hafta", "bu_ay", "son_7", "son_30", "ozel"})
_HATIRLATMA_TARIH = frozenset({"hepsi", "bugun", "yarin", "sonraki_hafta", "sonraki_ay", "sonraki_30", "ozel"})


def _ozet_tarih_sec(tarih: str, turu: str) -> str:
    """Türe ait olmayan kayıtlı seçim sessizce Hepsi olur."""
    sec = str(tarih or "hepsi").strip().lower() or "hepsi"
    izin = _HATIRLATMA_TARIH if turu == "hatirlatma" else _GORUSME_TARIH
    if sec not in izin:
        return "hepsi"
    return sec


def _ay_basi(gun: date, kayma: int) -> date:
    ay = gun.month - 1 + kayma
    return date(gun.year + ay // 12, ay % 12 + 1, 1)


def _ozet_tarih_penceresi(tarih: str, bas_raw, bitis_raw, bugun: date | None = None):
    """İstanbul günü. Dönüş: (başlangıç, bitiş hariç, hata).

    Bitiş ertesi gün 00:00 olduğu için seçilen günün 23:59:59'u aralığa girer.
    Görüşme pencereleri (bu hafta/ay bugünde biter) aynı kalır.
    """
    sec = str(tarih or "hepsi").strip().lower()
    if bugun is None:
        bugun = datetime.now(IST).date()
    if sec in ("", "hepsi"):
        return None, None, None
    if sec == "bugun":
        return _gun_basi(bugun), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "dun":
        dun = bugun - timedelta(days=1)
        return _gun_basi(dun), _gun_basi(bugun), None
    if sec == "yarin":
        yarin = bugun + timedelta(days=1)
        return _gun_basi(yarin), _gun_basi(yarin + timedelta(days=1)), None
    if sec == "bu_hafta":
        pazartesi = bugun - timedelta(days=bugun.weekday())
        return _gun_basi(pazartesi), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "bu_ay":
        return _gun_basi(bugun.replace(day=1)), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "son_7":
        return _gun_basi(bugun - timedelta(days=6)), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "son_30":
        return _gun_basi(bugun - timedelta(days=29)), _gun_basi(bugun + timedelta(days=1)), None
    if sec == "sonraki_hafta":
        pazartesi = bugun - timedelta(days=bugun.weekday()) + timedelta(days=7)
        return _gun_basi(pazartesi), _gun_basi(pazartesi + timedelta(days=7)), None
    if sec == "sonraki_ay":
        bas_gun = _ay_basi(bugun, 1)
        return _gun_basi(bas_gun), _gun_basi(_ay_basi(bas_gun, 1)), None
    if sec == "sonraki_30":
        return _gun_basi(bugun), _gun_basi(bugun + timedelta(days=30)), None
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


def _ozet_grid_payload_oku(mids: list) -> dict | None:
    """Grid önbelleğini tek sorguda okur.

    Dönüş None: tablo yok veya okunamadı. Tablo açılmaz.
    Dönüş sözlük: musteri_id -> payload; kaydı olmayan id anahtarda yoktur.
    """
    try:
        row = fetch_one("SELECT to_regclass(%s) AS t", ("musteri_aylik_grid_cache",))
    except Exception as e:
        print(f"[WARN] erp_notlar grid tablo: {type(e).__name__}")
        return None
    if not (row and row.get("t")):
        return None
    try:
        from routes.giris_routes import _read_aylik_grid_cache_payload_batch

        return _read_aylik_grid_cache_payload_batch(mids, mem_ttl=0.0) or {}
    except Exception as e:
        print(f"[WARN] erp_notlar grid oku: {type(e).__name__}")
        return None


def _ozet_grid_borc_ekle(notlar: list) -> tuple[list, int, float]:
    """Kayıtlı grid payload'ından Müşteriler raporu ile aynı özet.

    Tablo veya satır yoksa sayı uydurmaz. Müşteri döngüsünde sorgu yok.
    Dondurulan ay gridde 0 olduğu için toplama ve geciken sayıya girmez.
    """
    mids: list[int] = []
    seen: set[int] = set()
    for n in notlar or []:
        try:
            mid = int((n or {}).get("musteri_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid > 0 and mid not in seen:
            seen.add(mid)
            mids.append(mid)
    t0 = time.perf_counter()
    payloadlar = _ozet_grid_payload_oku(mids) if mids else {}
    hesap = None
    if payloadlar:
        from routes.giris_routes import _firma_ozet_ozet_from_grid_cache_payload

        hesap = _firma_ozet_ozet_from_grid_cache_payload
    bugun = date.today()
    for n in notlar or []:
        try:
            mid = int((n or {}).get("musteri_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        ham = (payloadlar or {}).get(mid) if payloadlar is not None else None
        ozet = None
        if ham and hesap:
            try:
                ozet = hesap(ham, bugun.year, bugun.month)
            except Exception:
                ozet = None
        if not isinstance(ozet, dict):
            n["grid_yok"] = True
            n["toplam_borc"] = None
            n["geciken_ay"] = None
            continue
        try:
            borc = round(float(ozet.get("toplam_borc") or 0), 2)
        except (TypeError, ValueError):
            borc = 0.0
        try:
            ay = int(ozet.get("geciken_ay") or 0)
        except (TypeError, ValueError):
            ay = 0
        if borc < 0 or borc != borc:
            borc = 0.0
        if ay < 0:
            ay = 0
        n["grid_yok"] = False
        n["toplam_borc"] = borc
        n["geciken_ay"] = ay
    gecen_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return notlar, len(mids), gecen_ms


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
    sec = _ozet_tarih_sec(request.args.get("tarih"), turu)
    bas, bitis_haric, tarih_hata = _ozet_tarih_penceresi(
        sec,
        request.args.get("bas"),
        request.args.get("bitis"),
    )
    if tarih_hata:
        return jsonify({"ok": False, "mesaj": tarih_hata}), 400
    tarih_sql = ""
    tarih_params: tuple = ()
    if sec != "hepsi":
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
    notlar, grid_musteri, grid_ms = _ozet_grid_borc_ekle([_ozet_serialize(r) for r in rows])
    return jsonify({
        "ok": True,
        "notlar": notlar,
        "grid_musteri": grid_musteri,
        "grid_ms": grid_ms,
    })


@bp.route("/api/musteri/<int:mid>/notlar")
@giris_gerekli
def api_musteri_notlar(mid: int):
    if str(request.args.get("sayfa") or "").strip() == "1":
        return jsonify(musteri_gecmis_paket(mid, request.args.get("limit"), request.args.get("ofset")))
    rows = _musteri_notlari(mid)
    return jsonify({"ok": True, "notlar": [_serialize(r) for r in rows]})


_WA_DENEME: dict[str, float] = {}
_LINK_KILIT: dict[int, float] = {}
_SABLON_AD = {
    0: "Vade günü",
    3: "3 gün",
    7: "7 gün",
    15: "15 gün",
    21: "21 gün",
    30: "30 gün",
    60: "60 gün",
    90: "90 gün",
    180: "180 gün",
    365: "365 gün",
}


def _wa_musteri(mid: int):
    return fetch_one(
        """
        SELECT id,
               COALESCE(NULLIF(BTRIM(name), ''), '') AS name,
               COALESCE(NULLIF(BTRIM(phone), ''), NULLIF(BTRIM(phone2), '')) AS telefon
        FROM customers
        WHERE id = %s
        """,
        (int(mid),),
    )


def _wa_grid_ozet(mid: int, bugun: date | None = None):
    """Sütunlarla aynı salt okuma. Tablo yoksa veya satır yoksa grid_yok."""
    bugun = bugun or datetime.now(IST).date()
    paket = _ozet_grid_payload_oku([int(mid)])
    if not isinstance(paket, dict):
        return {"grid_yok": True, "toplam_borc": None, "geciken_ay": None, "gun": None}
    ham = paket.get(int(mid))
    if not ham:
        return {"grid_yok": True, "toplam_borc": None, "geciken_ay": None, "gun": None}
    from routes.giris_routes import _firma_ozet_ozet_from_grid_cache_payload

    ozet = _firma_ozet_ozet_from_grid_cache_payload(ham, bugun.year, bugun.month)
    if not isinstance(ozet, dict):
        return {"grid_yok": True, "toplam_borc": None, "geciken_ay": None, "gun": None}
    return {
        "grid_yok": False,
        "toplam_borc": ozet.get("toplam_borc"),
        "geciken_ay": ozet.get("geciken_ay"),
        "geciken_tutar": _wa_geciken_tutar(ham, bugun),
        "gun": _wa_gecikme_gun(ham, bugun),
    }


def _wa_geciken_tutar(payload, bugun: date) -> float:
    """Referans ayından önceki ödenmemiş grid tutarı. İçinde bulunulan ay toplam borçtadır."""
    from routes.giris_routes import AYLIK_GRID_TAM_ODENDI_TOLERANS

    if not isinstance(payload, dict):
        return 0.0
    ref = date(bugun.year, bugun.month, 1)
    tol = float(AYLIK_GRID_TAM_ODENDI_TOLERANS)
    toplam = 0.0
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            cur = date(int(a.get("yil")), int(a.get("ay")), 1)
        except (TypeError, ValueError):
            continue
        if cur >= ref:
            continue
        try:
            tut = float(a.get("tutar_kdv_dahil") or a.get("brut_tutar_kdv") or 0)
        except (TypeError, ValueError):
            tut = 0.0
        if tut != tut:
            continue
        tahsil = bool(a.get("tahsil_edildi"))
        try:
            kalan = float(a.get("kalan_tutar_kdv") or 0)
        except (TypeError, ValueError):
            kalan = 0.0
        try:
            odenen = float(a.get("odenen_tutar_kdv") or 0) if a.get("odenen_tutar_kdv") is not None else 0.0
        except (TypeError, ValueError):
            odenen = 0.0
        kismi = bool(a.get("kismi_tahsilat")) or (odenen > tol and kalan > tol)
        if tahsil and not kismi:
            continue
        add = min(tut, max(kalan, 0.0)) if kismi else tut
        if add > tol:
            toplam += add
    return round(toplam, 2)


def _wa_gecikme_gun(payload, bugun: date) -> int:
    """En eski ödenmemiş grid ayının 1'inden bugüne gün. Dondurulan ay tutarı 0 olduğu için düşer."""
    if not isinstance(payload, dict):
        return 0
    ref = date(bugun.year, bugun.month, 1)
    en = None
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            yy = int(a.get("yil"))
            mm = int(a.get("ay"))
            cur = date(yy, mm, 1)
        except (TypeError, ValueError):
            continue
        if cur > ref:
            continue
        if bool(a.get("tahsil_edildi")) and not bool(a.get("kismi_tahsilat")):
            continue
        try:
            tut = float(a.get("tutar_kdv_dahil") or a.get("brut_tutar_kdv") or 0)
        except (TypeError, ValueError):
            tut = 0.0
        if tut <= 0.005:
            continue
        if en is None or cur < en:
            en = cur
    if en is None:
        return 0
    return max(0, (bugun - en).days)


def _wa_iki_satir(metin: str) -> str:
    satirlar = [s.strip() for s in str(metin or "").splitlines() if s.strip()]
    return " ".join(satirlar[:2])[:180]


def _wa_aktif_link_oku(mid: int):
    """Son ödeme linki satırı. Adres üretilmez, tablo açılmaz."""
    from odeme_linki import ofisbir_istegi

    if not ofisbir_istegi():
        return None
    try:
        row = fetch_one(
            """
            SELECT tutar_kurus, aciklama, durum, expires_at, whatsapp_mesaj
            FROM public.odeme_linkleri
            WHERE musteri_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (int(mid),),
        )
    except Exception:
        return {"_okunamadi": True}
    return row or None


def _wa_kayitli_odeme_url(mesaj: str) -> str:
    from odeme_linki import _URL_RE, public_odeme_base

    kok = public_odeme_base().rstrip("/") + "/odeme/"
    bulunan = []
    for ham in _URL_RE.findall(str(mesaj or "")):
        url = ham.rstrip(".,)")
        if url.startswith(kok):
            bulunan.append(url)
    if len(bulunan) != 1:
        return ""
    return bulunan[0]


def _wa_tutar_uyumlu(toplam, kurus) -> bool:
    from decimal import ROUND_HALF_UP, Decimal

    try:
        beklenen = int((Decimal(str(toplam)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return int(kurus) == beklenen
    except Exception:
        return False


def _wa_link_suresi_dolmus(exp) -> bool:
    from datetime import timezone

    if not isinstance(exp, datetime):
        return False
    bitis = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
    return bitis < datetime.now(timezone.utc)


def _wa_link_onizleme_guvenli(kurus, exp) -> str:
    from odeme_linki import _tarih_ist, tutar_tl

    try:
        tutar = tutar_tl(int(kurus))
    except (TypeError, ValueError):
        return ""
    metin = "Tutar " + tutar + " TL"
    tarih = _tarih_ist(exp)
    if tarih:
        metin += " · Geçerlilik " + tarih
    if "http" in metin.lower() or "/odeme/" in metin:
        return ""
    return metin


def _wa_link_kalem(ad: str, row, grid=None) -> dict:
    def paket(ipucu, oniz="", metin="", engel=True):
        return {
            "metin": metin,
            "engel": engel,
            "ipucu": ipucu,
            "rozet": "",
            "onizleme": oniz,
            "hover": oniz,
        }

    if not row:
        return paket("Aktif ödeme linki yok")
    if row.get("_okunamadi"):
        return paket("")
    durum_ham = row.get("durum")
    exp = row.get("expires_at")
    if durum_ham is None or str(durum_ham).strip() == "" or not isinstance(exp, datetime):
        return paket("")
    durum = str(durum_ham).strip()
    if durum not in ("bekliyor", "odendi", "suresi_doldu", "iptal", "basarisiz"):
        return paket("")
    from odeme_linki import link_wa_gonderilebilir, ofis_imzasi, tutar_tl, wa_varsayilan_mesaj

    oniz = _wa_link_onizleme_guvenli(row.get("tutar_kurus"), exp)
    if not link_wa_gonderilebilir(durum, exp):
        if durum in ("odendi", "iptal", "basarisiz"):
            ipucu = "Aktif ödeme linki yok"
        elif durum == "suresi_doldu" or _wa_link_suresi_dolmus(exp):
            ipucu = "Link süresi dolmuş"
        else:
            ipucu = ""
        return paket(ipucu, oniz)
    ozet = grid or {}
    if ozet.get("grid_yok") or not _wa_tutar_uyumlu(ozet.get("toplam_borc"), row.get("tutar_kurus")):
        return paket("tutar güncel değil", oniz)
    url = _wa_kayitli_odeme_url(row.get("whatsapp_mesaj"))
    if not url:
        return paket("Aktif ödeme linki yok", oniz)
    try:
        tutar = tutar_tl(int(row.get("tutar_kurus")))
    except (TypeError, ValueError):
        return paket("")
    metin = wa_varsayilan_mesaj(ad, row.get("aciklama"), tutar, url, exp, ofis_imzasi())
    return paket("", oniz, metin, False)


def _wa_son_tahsilat_oku(mid: int):
    try:
        return fetch_one(
            """
            SELECT NULLIF(BTRIM(makbuz_no), '') AS makbuz_no, tutar, tahsilat_tarihi
            FROM tahsilatlar
            WHERE COALESCE(customer_id, musteri_id) = %s
              AND tutar IS NOT NULL
              AND tahsilat_tarihi IS NOT NULL
              AND NULLIF(BTRIM(makbuz_no), '') IS NOT NULL
            ORDER BY tahsilat_tarihi DESC, id DESC
            LIMIT 1
            """,
            (int(mid),),
        )
    except Exception:
        return None


def _wa_tahsilat_kalem(row) -> dict:
    bos = {"metin": "", "engel": True, "ipucu": "", "rozet": "", "onizleme": ""}
    if not row:
        return bos
    no = str(row.get("makbuz_no") or "").strip()
    if not no or row.get("tutar") is None or not row.get("tahsilat_tarihi"):
        return bos
    from sozlesme_whatsapp import tahsilat_metin

    metin = tahsilat_metin(no, row.get("tutar"), row.get("tahsilat_tarihi"), False)
    return {
        "metin": metin,
        "engel": False,
        "ipucu": "",
        "rozet": "Son tahsilat bilgisiyle",
        "onizleme": _wa_iki_satir(metin),
    }


def asistan_wa_onizleme(mid: int, bugun: date | None = None) -> tuple[dict, int]:
    row = _wa_musteri(mid)
    if not row:
        return {"ok": False, "mesaj": "Müşteri bulunamadı"}, 404
    var, maske = _wa_var_maske(row.get("telefon"))
    if not var:
        return {"ok": False, "mesaj": "Telefon yok", "wa_var": False}, 400
    grid = _wa_grid_ozet(mid, bugun)
    from routes.giris_routes import _tutar_tr_goster
    from routes.whatsapp_routes import SABLONLAR, _en_yakin_esik

    ad = str(row.get("name") or "").strip() or "Müşteri"
    tutar = "—"
    gun = 0
    if not grid["grid_yok"]:
        tutar = _tutar_tr_goster(grid.get("toplam_borc"))
        gun = int(grid.get("gun") or 0)
    onerilen = ""
    if not grid["grid_yok"]:
        esik = _en_yakin_esik(gun)
        if esik is not None:
            onerilen = str(esik)
    liste = []
    for anahtar in sorted(SABLONLAR):
        ham = SABLONLAR[anahtar]
        dolu = "" if grid["grid_yok"] else ham.format(isim=ad, tutar=tutar, gun=gun)
        liste.append({
            "anahtar": str(anahtar),
            "etiket": _SABLON_AD.get(anahtar, str(anahtar)),
            "grup": "gecikme",
            "baslik": "Gecikme hatırlatma",
            "metin": dolu,
            "onizleme": _wa_iki_satir(dolu),
            "borc": True,
            "engel": bool(grid["grid_yok"]),
            "ipucu": "Borç hesaplanmadı" if grid["grid_yok"] else "",
            "rozet": "",
            "onerilen": str(anahtar) == onerilen,
        })
    if onerilen:
        liste.sort(key=lambda s: 0 if s["onerilen"] else 1)
    link = _wa_link_kalem(ad, _wa_aktif_link_oku(mid), grid)
    liste.append({
        "anahtar": "link",
        "etiket": "Ödeme linki",
        "grup": "link",
        "baslik": "Ödeme linki",
        "metin": link["metin"],
        "onizleme": link["onizleme"],
        "hover": link["hover"],
        "borc": False,
        "engel": link["engel"],
        "ipucu": link["ipucu"],
        "rozet": "",
        "onerilen": False,
    })
    makbuz = _wa_tahsilat_kalem(_wa_son_tahsilat_oku(mid))
    liste.append({
        "anahtar": "tahsilat",
        "etiket": "Tahsilat makbuzu",
        "grup": "tahsilat",
        "baslik": "Tahsilat bilgilendirme",
        "metin": makbuz["metin"],
        "onizleme": makbuz["onizleme"],
        "borc": False,
        "engel": makbuz["engel"],
        "ipucu": makbuz["ipucu"],
        "rozet": makbuz["rozet"],
        "onerilen": False,
    })
    return {
        "ok": True,
        "unvan": ad,
        "maske": maske,
        "grid_yok": bool(grid["grid_yok"]),
        "toplam_borc": grid.get("toplam_borc"),
        "geciken_tutar": grid.get("geciken_tutar"),
        "geciken_ay": grid.get("geciken_ay"),
        "onerilen": onerilen,
        "sablonlar": liste,
    }, 200


def asistan_wa_gonder(uid: int, mid: int, data: dict, origin: str, host: str, gonder_fn=None) -> tuple[dict, int]:
    """Mevcut sözleşme gönderim yolunu tek müşteri için çağırır. Metin loglanmaz."""
    row = _wa_musteri(mid)
    if not row:
        return {"ok": False, "durum": "hata", "mesaj": "Müşteri bulunamadı"}, 404
    var, _maske = _wa_var_maske(row.get("telefon"))
    if not var:
        return {"ok": False, "durum": "hata", "mesaj": "Telefon yok"}, 400
    if not bool((data or {}).get("onay")):
        return {"ok": False, "durum": "hata", "mesaj": "Onay gerekli"}, 400
    metin = str((data or {}).get("mesaj") or "").strip()
    if not metin:
        return {"ok": False, "durum": "hata", "mesaj": "Mesaj boş"}, 400
    from sozlesme_whatsapp import MESAJ_LIMIT, deneme_gecerli, sozlesme_wa_isle

    if len(metin) > MESAJ_LIMIT:
        return {"ok": False, "durum": "hata", "mesaj": "Mesaj çok uzun"}, 400
    sablon = str((data or {}).get("sablon") or "ozel").strip().lower()
    if sablon == "link":
        kalem = _wa_link_kalem(str(row.get("name") or ""), _wa_aktif_link_oku(mid), _wa_grid_ozet(mid))
        if kalem["engel"]:
            return {"ok": False, "durum": "hata", "mesaj": kalem["ipucu"] or "Aktif ödeme linki yok"}, 409
    elif sablon == "tahsilat":
        if _wa_tahsilat_kalem(_wa_son_tahsilat_oku(mid))["engel"]:
            return {"ok": False, "durum": "hata", "mesaj": "Tahsilat bilgisi yok"}, 409
    elif sablon != "ozel":
        grid = _wa_grid_ozet(mid)
        if grid["grid_yok"]:
            return {"ok": False, "durum": "hata", "mesaj": "Borç hesaplanmadı, bu şablon gönderilemez"}, 409
    deneme = deneme_gecerli((data or {}).get("deneme"))
    if not deneme:
        return {"ok": False, "durum": "hata", "mesaj": "İstek reddedildi"}, 400
    simdi = time.monotonic()
    once = _WA_DENEME.get(deneme)
    if once is not None and simdi - once < 15:
        return {"ok": False, "durum": "hata", "mesaj": "Gönderim zaten başladı"}, 409
    _WA_DENEME[deneme] = simdi
    fn = gonder_fn or sozlesme_wa_isle
    try:
        govde, kod = fn(
            int(uid),
            {
                "buton": "ust",
                "musteri_id": int(mid),
                "mesaj": metin,
                "telefon": row.get("telefon"),
                "onay": True,
                "deneme": deneme,
            },
            origin,
            host,
        )
    except Exception:
        _WA_DENEME.pop(deneme, None)
        return {"ok": False, "durum": "hata", "mesaj": "Gönderilemedi"}, 502
    return _wa_sonuc_sade(govde or {}, int(kod), metin)


def _wa_sonuc_sade(govde: dict, kod: int, metin: str) -> tuple[dict, int]:
    ham = str(govde.get("mesaj") or "")
    if ham == metin:
        ham = ""
    if govde.get("geri_dus") or govde.get("qr"):
        return {"ok": False, "durum": "hata", "mesaj": ham or "WhatsApp servisi bağlı değil"}, 200
    durum = str(govde.get("durum") or "")
    if kod == 429:
        return {"ok": False, "durum": "hata", "mesaj": ham or "Gönderim sınırı doldu"}, 429
    if kod == 409:
        return {"ok": False, "durum": "hata", "mesaj": ham or "Az önce gönderildi"}, 409
    if durum == "kuyrukta":
        return {"ok": True, "durum": "kuyrukta", "mesaj": "Kuyruğa alındı"}, 200
    if durum == "gonderildi":
        return {"ok": True, "durum": "gonderildi", "mesaj": "Gönderildi"}, 200
    if durum == "gonderiliyor":
        return {"ok": True, "durum": "gonderiliyor", "mesaj": "Gönderim sürüyor"}, 200
    if kod >= 400 or not govde.get("ok", True):
        return {"ok": False, "durum": "hata", "mesaj": ham or "Gönderilemedi"}, kod if kod >= 400 else 400
    return {"ok": False, "durum": "hata", "mesaj": "Gönderilemedi"}, 400


def _wa_ayni_tutar_link_oku(mid: int, kurus: int):
    """Aynı tutarda açık link. Adres üretilmez, tablo açılmaz."""
    from odeme_linki import ofisbir_istegi

    if not ofisbir_istegi():
        return None
    try:
        return fetch_one(
            """
            SELECT id, tutar_kurus, expires_at, whatsapp_mesaj
            FROM public.odeme_linkleri
            WHERE musteri_id = %s
              AND tutar_kurus = %s
              AND durum = 'bekliyor'
              AND expires_at > NOW()
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (int(mid), int(kurus)),
        )
    except Exception:
        return {"_okunamadi": True}


def _wa_diger_gecerli_tutarlar(mid: int, kurus: int) -> list:
    """Başka tutardaki açık linkler. Adres okunmaz, tablo açılmaz."""
    from odeme_linki import ofisbir_istegi

    if not ofisbir_istegi():
        return []
    try:
        rows = fetch_all(
            """
            SELECT DISTINCT tutar_kurus
            FROM public.odeme_linkleri
            WHERE musteri_id = %s
              AND tutar_kurus <> %s
              AND durum = 'bekliyor'
              AND expires_at > NOW()
            ORDER BY tutar_kurus
            """,
            (int(mid), int(kurus)),
        )
    except Exception:
        return []
    out = []
    for row in rows or []:
        try:
            out.append(int(row.get("tutar_kurus")))
        except (TypeError, ValueError):
            continue
    return out


def _asistan_link_audit(uid: int, mid: int, link_id: int, kurus: int) -> None:
    from audit_log import audit_yaz
    from db import execute as calistir

    audit_yaz(
        calistir,
        olay="odeme_linki_olustur",
        varlik="odeme_linkleri",
        kayit_id=int(link_id),
        musteri_id=int(mid),
        kullanici=str(int(uid)),
        anlik={"musteri_id": int(mid)},
        yeni={"id": int(link_id), "tutar_kurus": int(kurus), "musteri_id": int(mid)},
        eski_yaz=False,
    )


def asistan_odeme_linki(uid: int, mid: int, data: dict, olustur_fn=None) -> tuple[dict, int]:
    """Mevcut link üretimini çağırır. Mesaj göndermez, tahsilat yazmaz."""
    from odeme_linki import (
        kurus_from_tutar,
        link_olustur,
        ofis_imzasi,
        ozellik_acik,
        public_odeme_base,
        tutar_tl,
        wa_varsayilan_mesaj,
        _tarih_ist,
    )

    if not ozellik_acik():
        return {"ok": False, "mesaj": "Müşteri bulunamadı"}, 404
    row = _wa_musteri(mid)
    if not row:
        return {"ok": False, "mesaj": "Müşteri bulunamadı"}, 404
    secim = str((data or {}).get("secim") or "").strip().lower()
    if secim not in ("toplam", "geciken", "ozel"):
        return {"ok": False, "mesaj": "Tutar seçilmedi"}, 400
    grid = _wa_grid_ozet(mid)
    if secim in ("toplam", "geciken") and grid.get("grid_yok"):
        return {"ok": False, "mesaj": "Borç hesaplanmadı"}, 409
    try:
        if secim == "toplam":
            kurus = kurus_from_tutar(grid.get("toplam_borc"))
        elif secim == "geciken":
            kurus = kurus_from_tutar(grid.get("geciken_tutar"))
        else:
            kurus = kurus_from_tutar((data or {}).get("tutar"))
    except (TypeError, ValueError):
        return {"ok": False, "mesaj": "Tutar pozitif olmalı"}, 400
    if not grid.get("grid_yok"):
        try:
            tavan = kurus_from_tutar(grid.get("toplam_borc"))
        except (TypeError, ValueError):
            tavan = 0
        if kurus > tavan and not bool((data or {}).get("ust_onay")):
            return {"ok": False, "mesaj": "Tutar toplam borcun üstünde", "ust_onay": True}, 409
    tutar = tutar_tl(kurus)
    mevcut = _wa_ayni_tutar_link_oku(mid, kurus)
    if isinstance(mevcut, dict) and mevcut.get("_okunamadi"):
        return {"ok": False, "mesaj": "Ödeme linki oluşturulamadı"}, 409
    if mevcut:
        url = _wa_kayitli_odeme_url(mevcut.get("whatsapp_mesaj"))
        if not url:
            tarih = _tarih_ist(mevcut.get("expires_at")) or "—"
            return {
                "ok": False,
                "mesaj": (
                    "Bu tutarda geçerli bir link zaten var (son geçerlilik "
                    + tarih
                    + ") ama adresi saklı değil; mükerrer ödeme riski nedeniyle yenisi üretilmedi. "
                    "Farklı tutar seçebilir veya linki Sözleşmeler ekranından yönetebilirsin."
                ),
            }, 409
        tarih = _tarih_ist(mevcut.get("expires_at"))
        metin = wa_varsayilan_mesaj(
            str(row.get("name") or ""),
            "Ödeme",
            tutar,
            url,
            mevcut.get("expires_at"),
            ofis_imzasi(),
        )
        return {
            "ok": True,
            "yeniden": True,
            "id": int(mevcut["id"]),
            "tutar": tutar,
            "gecerlilik": tarih,
            "metin": metin,
            "mesaj": "Mevcut geçerli link kullanıldı (tutar " + tutar + " ₺, son geçerlilik " + tarih + ")",
        }, 200
    if not bool((data or {}).get("onay")):
        mesaj = tutar + " ₺ tutarında ödeme linki oluşturulacak. Onaylıyor musun?"
        diger = _wa_diger_gecerli_tutarlar(mid, kurus)
        if len(diger) == 1:
            mesaj += " Bu müşteride " + tutar_tl(diger[0]) + " ₺ tutarında başka geçerli bir link de var."
        elif len(diger) > 1:
            parca = " ve ".join(tutar_tl(k) + " ₺" for k in diger)
            mesaj += " Bu müşteride " + parca + " tutarlarında başka geçerli linkler de var."
        return {
            "ok": False,
            "onay_gerekli": True,
            "tutar": tutar,
            "mesaj": mesaj,
        }, 409
    simdi = time.monotonic()
    once = _LINK_KILIT.get(int(mid))
    if once is not None and simdi - once < 15:
        return {"ok": False, "mesaj": "Link oluşturma zaten başladı"}, 409
    _LINK_KILIT[int(mid)] = simdi
    fn = olustur_fn or link_olustur
    try:
        token, kayit = fn(int(mid), int(kurus), "Ödeme", 7, int(uid) or None, None)
    except Exception:
        _LINK_KILIT.pop(int(mid), None)
        return {"ok": False, "mesaj": "Ödeme linki oluşturulamadı"}, 502
    url = public_odeme_base().rstrip("/") + "/odeme/" + str(token or "")
    metin = wa_varsayilan_mesaj(
        str(row.get("name") or ""),
        str(kayit.get("aciklama") or "Ödeme"),
        tutar,
        url,
        kayit.get("expires_at"),
        ofis_imzasi(),
    )
    _asistan_link_audit(uid, mid, int(kayit["id"]), kurus)
    return {
        "ok": True,
        "yeniden": False,
        "id": int(kayit["id"]),
        "tutar": tutar,
        "gecerlilik": _tarih_ist(kayit.get("expires_at")),
        "metin": metin,
        "mesaj": "",
    }, 200


@bp.route("/api/musteri/<int:mid>/whatsapp")
@giris_gerekli
def api_musteri_whatsapp_onizleme(mid: int):
    govde, kod = asistan_wa_onizleme(mid)
    return jsonify(govde), kod


@bp.route("/api/musteri/<int:mid>/whatsapp", methods=["POST"])
@giris_gerekli
def api_musteri_whatsapp_gonder(mid: int):
    govde, kod = asistan_wa_gonder(
        int(getattr(current_user, "id", 0) or 0),
        mid,
        request.get_json(silent=True) or {},
        request.headers.get("Origin") or "",
        request.host or "",
    )
    return jsonify(govde), kod


@bp.route("/api/musteri/<int:mid>/odeme-linki", methods=["POST"])
@giris_gerekli
def api_musteri_odeme_linki(mid: int):
    govde, kod = asistan_odeme_linki(
        int(getattr(current_user, "id", 0) or 0),
        mid,
        request.get_json(silent=True) or {},
    )
    return jsonify(govde), kod


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
