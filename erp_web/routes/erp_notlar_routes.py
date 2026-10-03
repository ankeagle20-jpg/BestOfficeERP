"""ERP Asistan — not / hatırlatma (Faz 1).

Takvim görünümü yok. WhatsApp gönderimi pop-up'tan bağımsızdır;
APScheduler dakikada bir gonder_whatsapp_notlari çağırır.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, jsonify, render_template, request
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


def _wa_rozet(row: dict) -> str:
    if not row.get("whatsapp_gonderilsin"):
        return ""
    if row.get("whatsapp_gonderildi_mi"):
        return "gönderildi"
    hata = str(row.get("whatsapp_hata") or "").strip()
    if hata:
        return "gönderilemedi"
    return "gönderilecek"


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
        "whatsapp_rozet": _wa_rozet(row),
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


@bp.route("/api/ozet")
@giris_gerekli
def api_ozet():
    ensure_erp_notlar_tablolari()
    kapsam = str(request.args.get("kapsam") or "acik").strip().lower()
    if kapsam not in ("acik", "hepsi"):
        kapsam = "acik"
    q = str(request.args.get("q") or "").strip()
    like = "%" + q.replace("%", "") + "%"
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
        ORDER BY CASE WHEN ozet.durum IN ('bekliyor', 'ertelendi') THEN 0 ELSE 1 END,
                 COALESCE(ozet.ozet_musteri_adi, ''), ozet.id DESC
        LIMIT 500
    """
    params = _gorunur_params() + (kapsam, q, like)
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
            gorusulen_kisi
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, 'bekliyor', %s, %s,
            %s, %s, %s,
            %s
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


def gonder_whatsapp_notlari(only_ids=None) -> dict:
    """Zamanı gelmiş, henüz gönderilmemiş WhatsApp notlarını Node kuyruğuna yazar.

    Tamamlanan notlara mesaj gitmez. Servis kapalıysa whatsapp_hata dolar,
    whatsapp_gonderildi_mi false kalır; sonraki dakika tekrar dener.
    only_ids verilirse yalnız o kayıtlar işlenir (test).
    """
    ensure_erp_notlar_tablolari()
    params: list = []
    id_sql = ""
    if only_ids:
        id_sql = " AND id = ANY(%s)"
        params.append(list(only_ids))
    rows = fetch_all(
        f"""
        SELECT id, whatsapp_telefon, whatsapp_mesaj, not_metni, whatsapp_hata
        FROM erp_notlar
        WHERE whatsapp_gonderilsin = TRUE
          AND whatsapp_gonderildi_mi = FALSE
          AND hatirlatma_zamani <= NOW()
          AND durum <> 'tamamlandi'
          AND COALESCE(whatsapp_hata, '') NOT IN ('telefon yok', 'mesaj yok')
          {id_sql}
        ORDER BY hatirlatma_zamani
        LIMIT 30
        """,
        tuple(params),
    )
    from routes.whatsapp_routes import _wa_internal_headers, _wa_url

    gonderilen = 0
    hata = 0
    for row in rows or []:
        nid = row.get("id")
        tel = str(row.get("whatsapp_telefon") or "").strip()
        mesaj = str(row.get("whatsapp_mesaj") or "").strip() or str(row.get("not_metni") or "").strip()
        if not tel:
            execute(
                "UPDATE erp_notlar SET whatsapp_hata = 'telefon yok', updated_at = NOW() WHERE id = %s",
                (nid,),
            )
            hata += 1
            continue
        if not mesaj:
            execute(
                "UPDATE erp_notlar SET whatsapp_hata = 'mesaj yok', updated_at = NOW() WHERE id = %s",
                (nid,),
            )
            hata += 1
            continue
        try:
            r = requests.post(
                _wa_url("kuyruk-toplu-ekle"),
                json={"liste": [{"telefon": tel, "mesaj": mesaj}]},
                headers=_wa_internal_headers(),
                timeout=10,
            )
            body = {}
            try:
                body = r.json() if r.content else {}
            except Exception:
                body = {}
            basarisiz = r.status_code >= 400 or (
                isinstance(body, dict) and body.get("ok") is False
            )
            if basarisiz:
                execute(
                    """
                    UPDATE erp_notlar
                    SET whatsapp_hata = %s, updated_at = NOW()
                    WHERE id = %s AND whatsapp_gonderildi_mi = FALSE
                    """,
                    (f"gönderilemedi: HTTP {r.status_code}", nid),
                )
                print(f"[WARN] erp_not_whatsapp not_id={nid} http={r.status_code}")
                hata += 1
                continue
            execute(
                """
                UPDATE erp_notlar
                SET whatsapp_gonderildi_mi = TRUE,
                    whatsapp_hata = NULL,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (nid,),
            )
            gonderilen += 1
        except Exception as e:
            execute(
                """
                UPDATE erp_notlar
                SET whatsapp_hata = %s, updated_at = NOW()
                WHERE id = %s AND whatsapp_gonderildi_mi = FALSE
                """,
                (f"gönderilemedi: {type(e).__name__}", nid),
            )
            print(f"[WARN] erp_not_whatsapp not_id={nid} hata={type(e).__name__}")
            hata += 1
    return {"gonderilen": gonderilen, "hata": hata, "adet": len(rows or [])}


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
            except Exception as e:
                print(f"[WARN] erp_not_whatsapp {sch or 'public'}: {type(e).__name__}")
            finally:
                try:
                    g.pop("tenant_schema", None)
                except Exception:
                    pass
