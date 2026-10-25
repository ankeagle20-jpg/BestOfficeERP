"""Toplu mesaj kampanyası. Bayrak kapalıyken iş yok.

Tam telefon ve mesaj metni loga yazılmaz. İkinci telefon kullanılmaz.
Gecikme hariç tablosundan bağımsızdır.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Europe/Istanbul")
BAYRAK = "WA_TOPLU_KAMPANYA"
SABLON_MAX = 1000
TAVAN_MIN = 50
TAVAN_MAX = 250
TAVAN_VARSAYILAN = 150
PENCERE_BAS = 9
PENCERE_BIT = 20
ATLAMA = 0.25
HATA_SERI = 3
BELIRSIZ_SAAT = 2
YEDEK_HITAP = "Değerli müşterimiz"
ACIK_DURUMLAR = ("calisiyor", "durduruldu", "test_bekliyor")
GIDEN_DURUM = ("kuyrukta", "gonderildi", "belirsiz", "hata", "mukerrer")
_SEMA_RE = re.compile(r"tenant_[a-z0-9_]+")
_ANAHTAR_RE = re.compile(
    r"^szwa:d:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_LOG_YASAK = frozenset((
    "mesaj", "sablon", "metin", "telefon", "tel", "yetkili", "ad", "isim",
    "musteri", "firma",
))
_ONIZLEME_ALAN = ("alici", "yedek", "haric", "pasif", "numarasiz", "sure_dk", "gun")


def bayrak_acik() -> bool:
    return str(os.environ.get(BAYRAK, "0")).strip() == "1"


def norm_telefon(ham) -> str:
    s = re.sub(r"\D", "", str(ham or ""))
    if s.startswith("00"):
        s = s[2:]
    if s.startswith("0") and len(s) == 11:
        s = "90" + s[1:]
    elif len(s) == 10 and s.startswith("5"):
        s = "90" + s
    if re.fullmatch(r"[1-9]\d{10,14}", s):
        return s
    return ""


def numara_ozet(norm: str) -> str:
    return hashlib.sha256(str(norm or "").encode("utf-8")).hexdigest()


def maske(norm: str) -> str:
    d = re.sub(r"\D", "", str(norm or ""))
    if len(d) < 4:
        return "***"
    return "***" + d[-4:]


def guvenli_log(olay: str, **alan) -> None:
    temiz = []
    for anahtar, deger in alan.items():
        if anahtar in _LOG_YASAK:
            continue
        if anahtar == "maske":
            deger = maske(deger)
        temiz.append(f"{anahtar}={deger}")
    logger.info("wa_kampanya olay=%s %s", olay, " ".join(temiz))


def yeni_anahtar() -> str:
    return "szwa:d:" + str(uuid.uuid4())


def anahtar_gecerli(anahtar: str) -> bool:
    return bool(_ANAHTAR_RE.match(str(anahtar or "")))


def sablon_gecerli(metin) -> str | None:
    s = str(metin or "").replace("\r\n", "\n").strip()
    if not s or len(s) > SABLON_MAX:
        return None
    return s


def tavan_gecerli(n, varsayilan: bool = False) -> int | None:
    if n is None or n == "":
        return TAVAN_VARSAYILAN if varsayilan else None
    try:
        i = int(n)
    except (TypeError, ValueError):
        return None
    if TAVAN_MIN <= i <= TAVAN_MAX:
        return i
    return None


def hitap_uygula(sablon: str, yetkili) -> tuple[str, bool]:
    ad = " ".join(str(yetkili or "").split())
    yedek = not ad
    metin = str(sablon or "").replace("{yetkili}", YEDEK_HITAP if yedek else ad)
    return metin, yedek


def pencere_icinde(simdi: datetime) -> bool:
    an = simdi if simdi.tzinfo else simdi.replace(tzinfo=IST)
    saat = an.astimezone(IST).hour
    return PENCERE_BAS <= saat < PENCERE_BIT


def gun_basi_utc(simdi: datetime) -> datetime:
    an = simdi if simdi.tzinfo else simdi.replace(tzinfo=IST)
    ist = an.astimezone(IST)
    bas = ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return bas.astimezone(timezone.utc)


def sure_tahmin(alici: int, tavan: int) -> dict:
    n = max(int(alici or 0), 0)
    if n <= 0:
        return {"sure_dk": 0, "gun": 0}
    cap = max(int(tavan or 0), 1)
    return {
        "sure_dk": int(math.ceil(n / (1 - ATLAMA))),
        "gun": int(math.ceil(n / cap)),
    }


def onizleme_govde(sayilar: dict) -> dict:
    return {k: int((sayilar or {}).get(k) or 0) for k in _ONIZLEME_ALAN}


def aday_sinifla(kayitlar, sablon: str = "") -> dict:
    yer = "{yetkili}" in str(sablon or "")
    pasif = haric = numarasiz = yedek = 0
    uygun = []
    gorulen_mid = set()
    gorulen_hash = set()
    for k in sorted(list(kayitlar or []), key=lambda r: int((r or {}).get("mid") or 0)):
        if not k:
            continue
        mid = int(k.get("mid") or 0)
        if mid <= 0 or mid in gorulen_mid:
            continue
        gorulen_mid.add(mid)
        durum = str(k.get("durum") or "").strip().lower()
        if durum == "pasif":
            pasif += 1
            continue
        if durum != "aktif":
            continue
        if k.get("haric"):
            haric += 1
            continue
        norm = norm_telefon(k.get("tel"))
        if not norm:
            numarasiz += 1
            continue
        ozet = numara_ozet(norm)
        if ozet in gorulen_hash:
            continue
        gorulen_hash.add(ozet)
        ad = " ".join(str(k.get("yetkili") or "").split())
        yedek_mi = bool(yer and not ad)
        if yedek_mi:
            yedek += 1
        uygun.append({
            "mid": mid,
            "hash": ozet,
            "yedek_hitap": yedek_mi,
        })
    return {
        "alici": len(uygun),
        "yedek": yedek,
        "haric": haric,
        "pasif": pasif,
        "numarasiz": numarasiz,
        "uygun": uygun,
    }


def alici_ekle(tablo: list, satir: dict) -> str:
    for eski in tablo:
        if int(eski.get("mid") or 0) == int(satir.get("mid") or 0):
            return "mid"
        if str(eski.get("hash") or "") == str(satir.get("hash") or ""):
            return "numara"
    tablo.append(dict(satir))
    return "tamam"


def baslat_izin(test_ok, acik_baska) -> str:
    if not test_ok:
        return "test_yok"
    if acik_baska:
        return "acik_var"
    return "tamam"


def poll_karar(servis_durum, yas_asildi: bool) -> str:
    d = str(servis_durum or "")
    if d == "gonderildi":
        return "gonderildi"
    if d in ("basarisiz", "hata"):
        return "hata"
    if d == "belirsiz":
        return "belirsiz"
    if d == "yok" and yas_asildi:
        return "belirsiz"
    return "bekle"


def _yas_asildi(an, simdi: datetime) -> bool:
    if not isinstance(an, datetime):
        return False
    bas = an if an.tzinfo else an.replace(tzinfo=timezone.utc)
    su = simdi if simdi.tzinfo else simdi.replace(tzinfo=IST)
    return (su - bas) > timedelta(hours=BELIRSIZ_SAAT)


def _uygula_sonuc(model: dict, satir: dict, kod: str) -> None:
    k = model.get("kampanya") or {}
    seri = int(k.get("seri") or 0)
    if kod == "gonderildi":
        satir["durum"] = "gonderildi"
        k["seri"] = 0
    elif kod == "belirsiz":
        satir["durum"] = "belirsiz"
    elif kod == "hata":
        satir["durum"] = "hata"
        seri += 1
        k["seri"] = seri
        if seri >= HATA_SERI:
            k["durum"] = "durduruldu"
    else:
        satir["durum"] = kod
    if satir.get("durum") != "kuyrukta":
        model["ucus"] = None
    model["kampanya"] = k


def tur_uygula(model, kuyruk, sonuc_oku, rastgele, simdi) -> dict:
    """Bir tur. Servise en fazla bir iş bırakır. Modeli yerinde günceller."""
    model["kuyruk_cagri"] = int(model.get("kuyruk_cagri") or 0)
    model.setdefault("dokunulan", [])
    if model.get("bayrak") is False:
        return {"eylem": "bayrak"}
    k = model.get("kampanya") or {}
    ucus = model.get("ucus")
    if ucus and ucus.get("durum") == "kuyrukta":
        try:
            paket = sonuc_oku(ucus.get("anahtar") or "") or {}
        except Exception:
            paket = {"durum": "erisilemedi"}
        karar = poll_karar(paket.get("durum"), _yas_asildi(ucus.get("son_deneme_at"), simdi))
        if karar == "bekle":
            return {"eylem": "bekle_sonuc"}
        _uygula_sonuc(model, ucus, karar)
        model["dokunulan"].append(ucus)
        guvenli_log("sonuc", eylem=karar, mid=ucus.get("mid"), kampanya_id=k.get("id"))
        return {"eylem": karar}
    if k.get("durum") != "calisiyor":
        return {"eylem": "yok"}
    if float(rastgele) < ATLAMA:
        return {"eylem": "atla"}
    if not pencere_icinde(simdi):
        return {"eylem": "pencere"}
    if int(model.get("bugun") or 0) >= int(k.get("tavan") or 0):
        return {"eylem": "tavan"}
    bekleyen = model.get("bekleyen") or []
    if not bekleyen:
        k["durum"] = "bitti"
        model["kampanya"] = k
        return {"eylem": "bitti"}
    aday = bekleyen.pop(0)
    model["dokunulan"].append(aday)
    norm = norm_telefon(aday.get("tel"))
    if not norm:
        aday["durum"] = "gecersiz"
        guvenli_log("gecersiz", mid=aday.get("mid"), kampanya_id=k.get("id"))
        return {"eylem": "gecersiz"}
    ozet = numara_ozet(norm)
    giden = set(model.get("giden_hash") or [])
    if ozet in giden or (aday.get("hash") and aday.get("hash") in giden):
        aday["durum"] = "mukerrer"
        return {"eylem": "mukerrer"}
    metin, _yedek = hitap_uygula(k.get("sablon") or "", aday.get("yetkili"))
    anahtar = yeni_anahtar()
    aday["anahtar"] = anahtar
    aday["hash"] = ozet
    aday["durum"] = "kuyrukta"
    aday["son_deneme_at"] = simdi
    aday["deneme_artti"] = True
    model["ucus"] = aday
    model["giden_hash"] = list(giden) + [ozet]
    try:
        paket = kuyruk(norm, metin, anahtar) or {}
    except Exception:
        paket = {"durum": "belirsiz"}
    model["kuyruk_cagri"] = int(model.get("kuyruk_cagri") or 0) + 1
    model["bugun"] = int(model.get("bugun") or 0) + 1
    d = str(paket.get("durum") or "kuyrukta")
    if d == "gonderildi":
        _uygula_sonuc(model, aday, "gonderildi")
    elif d == "belirsiz":
        _uygula_sonuc(model, aday, "belirsiz")
    elif d == "hata":
        _uygula_sonuc(model, aday, "hata")
    else:
        d = "kuyrukta"
    guvenli_log(
        "tur", eylem=d, mid=aday.get("mid"), kampanya_id=k.get("id"), maske=norm,
    )
    return {"eylem": d}


def test_gonder(telefon, sablon, kuyruk, anahtar_uret=None) -> dict:
    metin_sablon = sablon_gecerli(sablon)
    if not metin_sablon:
        return {"ok": False, "neden": "sablon"}
    norm = norm_telefon(telefon)
    if not norm:
        return {"ok": False, "neden": "gecersiz"}
    metin, _yedek = hitap_uygula(metin_sablon, "")
    anahtar = (anahtar_uret or yeni_anahtar)()
    if not anahtar_gecerli(anahtar):
        return {"ok": False, "neden": "anahtar"}
    try:
        paket = kuyruk(norm, metin, anahtar) or {}
    except Exception:
        paket = {"durum": "belirsiz"}
    d = str(paket.get("durum") or "belirsiz")
    if d not in ("gonderildi", "hata", "belirsiz", "kuyrukta"):
        d = "kuyrukta"
    return {
        "ok": d in ("gonderildi", "kuyrukta"),
        "durum": d,
        "anahtar": anahtar,
        "test_ok": d == "gonderildi",
    }


def test_sonuc(anahtar, sonuc_oku) -> dict:
    if not anahtar_gecerli(anahtar):
        return {"ok": False, "neden": "anahtar", "test_ok": False}
    try:
        paket = sonuc_oku(anahtar) or {}
    except Exception:
        paket = {"durum": "erisilemedi"}
    d = str(paket.get("durum") or "")
    if d == "gonderildi":
        return {"ok": True, "durum": "gonderildi", "test_ok": True}
    if d in ("basarisiz", "hata"):
        return {"ok": False, "durum": "hata", "test_ok": False}
    if d == "belirsiz":
        return {"ok": False, "durum": "belirsiz", "test_ok": False}
    return {"ok": True, "durum": "kuyrukta", "test_ok": False}


def sema_listesi(rows) -> list:
    semalar = [None]
    for row in rows or []:
        sch = str((row or {}).get("schema_name") or "").strip()
        if sch and sch != "public" and _SEMA_RE.fullmatch(sch):
            semalar.append(sch)
    return semalar


def semalari_uygula(semalar, tur) -> None:
    for sch in semalar:
        tur(sch)


def run_wa_kampanya_job(semalar_fn=None, tur=None, rastgele_fn=None) -> dict:
    if not bayrak_acik():
        return {"atlandi": "bayrak"}
    if rastgele_fn is None:
        rastgele_fn = random.random
    if semalar_fn is not None and tur is not None:
        semalari_uygula(semalar_fn(), lambda sch: tur(sch, rastgele_fn()))
        return {"atlandi": ""}
    import app as app_mod
    from flask import g

    n = 0
    with app_mod.app.app_context():
        for sch in _semalar_db():
            try:
                if sch:
                    g.tenant_schema = sch
                else:
                    g.pop("tenant_schema", None)
                _tur_db(sch, rastgele_fn())
                n += 1
            except Exception as e:
                print(f"[WARN] wa_kampanya {sch or 'public'}: {type(e).__name__}")
            finally:
                try:
                    g.pop("tenant_schema", None)
                except Exception:
                    pass
    return {"semalar": n}


def _semalar_db():
    from db import fetch_all

    try:
        rows = fetch_all(
            """
            SELECT DISTINCT schema_name
            FROM public.tenants
            WHERE schema_name IS NOT NULL
              AND btrim(schema_name) <> ''
            """
        )
    except Exception as e:
        print(f"[WARN] wa_kampanya tenant list: {type(e).__name__}")
        rows = []
    return sema_listesi(rows)


def _tablo_var(ad: str, sch) -> bool:
    from db import fetch_one

    schema = sch or "public"
    try:
        row = fetch_one(
            """
            SELECT 1 AS ok
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, ad),
        )
    except Exception:
        return False
    return bool(row)


def ensure_tablolar() -> None:
    from db import execute

    execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_toplu_haric (
            musteri_id INTEGER PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS wa_kampanya (
            id BIGSERIAL PRIMARY KEY,
            durum TEXT NOT NULL DEFAULT 'taslak',
            sablon TEXT NOT NULL,
            gunluk_tavan INTEGER NOT NULL DEFAULT 150,
            ust_uste_hata INTEGER NOT NULL DEFAULT 0,
            test_ok BOOLEAN NOT NULL DEFAULT FALSE,
            test_anahtar TEXT,
            olusturan_user_id INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS wa_kampanya_tek_acik
        ON wa_kampanya ((1))
        WHERE durum IN ('calisiyor', 'durduruldu', 'test_bekliyor')
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS wa_kampanya_alici (
            id BIGSERIAL PRIMARY KEY,
            kampanya_id BIGINT NOT NULL REFERENCES wa_kampanya(id) ON DELETE CASCADE,
            musteri_id INTEGER NOT NULL,
            telefon_hash TEXT NOT NULL,
            durum TEXT NOT NULL DEFAULT 'bekliyor',
            deneme INTEGER NOT NULL DEFAULT 0,
            szwa_anahtar TEXT,
            son_deneme_at TIMESTAMPTZ,
            yedek_hitap BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (kampanya_id, musteri_id),
            UNIQUE (kampanya_id, telefon_hash)
        )
        """
    )


def kampanya_sayilari(oku=None) -> dict:
    if oku is None:
        from db import fetch_one
        oku = fetch_one
    bos = {"acik": 0, "bekleyen": 0, "durdurulan": 0}
    try:
        k = oku(
            """
            SELECT COUNT(*) FILTER (WHERE durum = 'calisiyor') AS acik,
                   COUNT(*) FILTER (WHERE durum = 'durduruldu') AS durdurulan
            FROM wa_kampanya
            """
        ) or {}
        b = oku(
            """
            SELECT COUNT(*) AS n
            FROM wa_kampanya_alici a
            JOIN wa_kampanya k ON k.id = a.kampanya_id
            WHERE k.durum = 'calisiyor'
              AND a.durum IN ('bekliyor', 'kuyrukta')
            """
        ) or {}
    except Exception:
        return bos
    return {
        "acik": int(k.get("acik") or 0),
        "bekleyen": int(b.get("n") or 0),
        "durdurulan": int(k.get("durdurulan") or 0),
    }


def _aday_sql() -> str:
    return """
        SELECT c.id AS mid,
               CASE WHEN lower(btrim(COALESCE(c.durum, ''))) = 'aktif' THEN 'aktif'
                    WHEN lower(btrim(COALESCE(c.durum, ''))) = 'pasif' THEN 'pasif'
                    ELSE 'diger' END AS durum,
               EXISTS (
                   SELECT 1 FROM whatsapp_toplu_haric h WHERE h.musteri_id = c.id
               ) AS haric,
               COALESCE(NULLIF(btrim(y.tel), ''), NULLIF(btrim(c.phone), '')) AS tel,
               COALESCE(NULLIF(btrim(y.ad_soyad), ''), '') AS yetkili
        FROM customers c
        LEFT JOIN LATERAL (
            SELECT tel, ad_soyad
            FROM musteri_yetkililer
            WHERE musteri_id = c.id
            ORDER BY sira ASC NULLS LAST, id ASC
            LIMIT 1
        ) y ON TRUE
    """


def aday_satirlari():
    from db import fetch_all

    try:
        return fetch_all(_aday_sql())
    except Exception as e:
        print(f"[WARN] wa_kampanya aday: {type(e).__name__}")
        return []


def kart_telefon(mid: int, oku=None) -> dict:
    if oku is None:
        from db import fetch_one
        oku = fetch_one
    try:
        row = oku(
            """
            SELECT CASE WHEN lower(btrim(COALESCE(c.durum, ''))) = 'pasif' THEN ''
                        ELSE COALESCE(NULLIF(btrim(y.tel), ''), NULLIF(btrim(c.phone), ''))
                   END AS tel,
                   COALESCE(NULLIF(btrim(y.ad_soyad), ''), '') AS yetkili
            FROM customers c
            LEFT JOIN LATERAL (
                SELECT tel, ad_soyad
                FROM musteri_yetkililer
                WHERE musteri_id = c.id
                ORDER BY sira ASC NULLS LAST, id ASC
                LIMIT 1
            ) y ON TRUE
            WHERE c.id = %s
            """,
            (int(mid),),
        ) or {}
    except Exception:
        return {"tel": "", "yetkili": ""}
    return {"tel": row.get("tel") or "", "yetkili": row.get("yetkili") or ""}


def _kuyruk_bir(telefon: str, mesaj: str, anahtar: str) -> dict:
    import sozlesme_whatsapp as sw

    return sw._kuyruk_varsayilan(telefon, mesaj, anahtar)


def _sonuc_bir(anahtar: str) -> dict:
    import sozlesme_whatsapp as sw

    return sw._sonuc_varsayilan(anahtar)


def _tur_db(sch, rastgele, simdi=None) -> None:
    from db import execute, fetch_all, fetch_one

    if not _tablo_var("wa_kampanya", sch):
        return
    simdi = simdi or datetime.now(IST)
    k = fetch_one(
        """
        SELECT id, durum, sablon, gunluk_tavan, ust_uste_hata
        FROM wa_kampanya
        WHERE durum IN ('calisiyor', 'durduruldu')
        ORDER BY id DESC
        LIMIT 1
        """
    )
    if not k:
        return
    ucus_row = fetch_one(
        """
        SELECT id, musteri_id, telefon_hash, durum, szwa_anahtar, son_deneme_at
        FROM wa_kampanya_alici
        WHERE kampanya_id = %s AND durum = 'kuyrukta'
        ORDER BY id
        LIMIT 1
        """,
        (int(k["id"]),),
    )
    ucus = None
    if ucus_row:
        ucus = {
            "id": ucus_row.get("id"),
            "mid": ucus_row.get("musteri_id"),
            "hash": ucus_row.get("telefon_hash"),
            "durum": "kuyrukta",
            "anahtar": ucus_row.get("szwa_anahtar"),
            "son_deneme_at": ucus_row.get("son_deneme_at"),
        }
    bekleyen = []
    if ucus is None and str(k.get("durum") or "") == "calisiyor":
        row = fetch_one(
            """
            SELECT id, musteri_id, telefon_hash
            FROM wa_kampanya_alici
            WHERE kampanya_id = %s AND durum = 'bekliyor'
            ORDER BY id
            LIMIT 1
            """,
            (int(k["id"]),),
        )
        if row:
            kart = kart_telefon(int(row["musteri_id"]))
            bekleyen.append({
                "id": row.get("id"),
                "mid": row.get("musteri_id"),
                "hash": row.get("telefon_hash"),
                "tel": kart.get("tel") or "",
                "yetkili": kart.get("yetkili") or "",
                "durum": "bekliyor",
            })
    giden_rows = fetch_all(
        """
        SELECT telefon_hash
        FROM wa_kampanya_alici
        WHERE kampanya_id = %s
          AND durum IN ('kuyrukta', 'gonderildi', 'belirsiz', 'hata', 'mukerrer')
        """,
        (int(k["id"]),),
    ) or []
    bugun_row = fetch_one(
        """
        SELECT COUNT(*) AS n
        FROM wa_kampanya_alici
        WHERE kampanya_id = %s
          AND durum IN ('kuyrukta', 'gonderildi', 'belirsiz', 'hata')
          AND son_deneme_at >= %s
        """,
        (int(k["id"]), gun_basi_utc(simdi)),
    ) or {}
    model = {
        "bayrak": True,
        "kampanya": {
            "id": k.get("id"),
            "durum": k.get("durum"),
            "sablon": k.get("sablon") or "",
            "tavan": int(k.get("gunluk_tavan") or TAVAN_VARSAYILAN),
            "seri": int(k.get("ust_uste_hata") or 0),
        },
        "bugun": int(bugun_row.get("n") or 0),
        "ucus": ucus,
        "bekleyen": bekleyen,
        "giden_hash": [r.get("telefon_hash") for r in giden_rows if r.get("telefon_hash")],
    }
    tur_uygula(model, _kuyruk_bir, _sonuc_bir, rastgele, simdi)
    execute(
        """
        UPDATE wa_kampanya
        SET durum = %s, ust_uste_hata = %s, updated_at = NOW()
        WHERE id = %s
        """,
        (
            model["kampanya"].get("durum"),
            int(model["kampanya"].get("seri") or 0),
            int(k["id"]),
        ),
    )
    yazilan = set()
    for satir in model.get("dokunulan") or []:
        if not satir or not satir.get("id") or satir["id"] in yazilan:
            continue
        yazilan.add(satir["id"])
        execute(
            """
            UPDATE wa_kampanya_alici
            SET durum = %s,
                telefon_hash = COALESCE(NULLIF(%s, ''), telefon_hash),
                szwa_anahtar = COALESCE(%s, szwa_anahtar),
                son_deneme_at = COALESCE(%s, son_deneme_at),
                deneme = deneme + CASE WHEN %s THEN 1 ELSE 0 END
            WHERE id = %s AND kampanya_id = %s
            """,
            (
                satir.get("durum") or "bekliyor",
                satir.get("hash") or "",
                satir.get("anahtar"),
                satir.get("son_deneme_at"),
                bool(satir.get("deneme_artti")),
                int(satir["id"]),
                int(k["id"]),
            ),
        )


def _kampanya_oku(kid: int):
    from db import fetch_one

    return fetch_one(
        """
        SELECT id, durum, sablon, gunluk_tavan, ust_uste_hata, test_ok, test_anahtar
        FROM wa_kampanya
        WHERE id = %s
        """,
        (int(kid),),
    )


def _acik_baska(haric_id=None) -> bool:
    from db import fetch_one

    row = fetch_one(
        """
        SELECT id
        FROM wa_kampanya
        WHERE durum IN ('calisiyor', 'durduruldu', 'test_bekliyor')
          AND (%s IS NULL OR id <> %s)
        LIMIT 1
        """,
        (haric_id, haric_id),
    )
    return bool(row)


def taslak_kaydet(sablon, tavan, kid=None, uid=None) -> dict:
    from db import execute, execute_returning

    ensure_tablolar()
    metin = sablon_gecerli(sablon)
    cap = tavan_gecerli(tavan, varsayilan=True)
    if not metin or cap is None:
        return {"ok": False, "neden": "gecersiz"}
    if kid:
        row = _kampanya_oku(int(kid))
        if not row:
            return {"ok": False, "neden": "yok"}
        if str(row.get("durum") or "") not in ("taslak", "test_bekliyor"):
            return {"ok": False, "neden": "durum"}
        durum = "taslak"
        execute(
            """
            UPDATE wa_kampanya
            SET sablon = %s, gunluk_tavan = %s, durum = %s,
                test_ok = FALSE, test_anahtar = NULL, updated_at = NOW()
            WHERE id = %s
            """,
            (metin, cap, durum, int(kid)),
        )
        return {"ok": True, "id": int(kid), "test_ok": False, "durum": durum}
    yeni = execute_returning(
        """
        INSERT INTO wa_kampanya (durum, sablon, gunluk_tavan, olusturan_user_id)
        VALUES ('taslak', %s, %s, %s)
        RETURNING id
        """,
        (metin, cap, uid),
    )
    if not yeni:
        return {"ok": False, "neden": "kayit"}
    return {"ok": True, "id": int(yeni.get("id")), "test_ok": False, "durum": "taslak"}


def onizleme_istegi(kid: int) -> dict:
    ensure_tablolar()
    row = _kampanya_oku(int(kid))
    if not row:
        return {"ok": False, "neden": "yok"}
    sinif = aday_sinifla(aday_satirlari(), row.get("sablon") or "")
    govde = onizleme_govde({**sinif, **sure_tahmin(sinif["alici"], row.get("gunluk_tavan"))})
    return {"ok": True, **govde}


def _test_yaz(kid: int, anahtar: str, test_ok: bool) -> dict:
    from db import execute

    if not test_ok:
        execute(
            """
            UPDATE wa_kampanya
            SET test_anahtar = %s, test_ok = FALSE, updated_at = NOW()
            WHERE id = %s AND durum IN ('taslak', 'test_bekliyor')
            """,
            (anahtar, int(kid)),
        )
        return {"ok": True}
    try:
        execute(
            """
            UPDATE wa_kampanya
            SET test_ok = TRUE, durum = 'test_bekliyor', test_anahtar = %s,
                updated_at = NOW()
            WHERE id = %s AND durum IN ('taslak', 'test_bekliyor')
            """,
            (anahtar, int(kid)),
        )
    except Exception:
        return {"ok": False, "neden": "acik_var"}
    return {"ok": True}


def test_gonder_istegi(kid: int, telefon) -> dict:
    ensure_tablolar()
    row = _kampanya_oku(int(kid))
    if not row:
        return {"ok": False, "neden": "yok"}
    if str(row.get("durum") or "") not in ("taslak", "test_bekliyor"):
        return {"ok": False, "neden": "durum"}
    if str(row.get("durum") or "") != "test_bekliyor" and _acik_baska(int(kid)):
        return {"ok": False, "neden": "acik_var"}
    anahtar = yeni_anahtar()
    sonuc = test_gonder(telefon, row.get("sablon") or "", _kuyruk_bir, lambda: anahtar)
    if sonuc.get("neden"):
        return sonuc
    yaz = _test_yaz(int(kid), sonuc.get("anahtar") or anahtar, bool(sonuc.get("test_ok")))
    if not yaz.get("ok"):
        return {"ok": False, "neden": yaz.get("neden") or "kayit", "test_ok": False}
    guvenli_log("test", kampanya_id=int(kid), durum=sonuc.get("durum"))
    return sonuc


def test_sonuc_istegi(kid: int, anahtar: str) -> dict:
    ensure_tablolar()
    row = _kampanya_oku(int(kid))
    if not row or str(row.get("test_anahtar") or "") != str(anahtar or ""):
        return {"ok": False, "neden": "anahtar", "test_ok": False}
    sonuc = test_sonuc(anahtar, _sonuc_bir)
    if sonuc.get("test_ok"):
        yaz = _test_yaz(int(kid), anahtar, True)
        if not yaz.get("ok"):
            return {"ok": False, "neden": yaz.get("neden") or "kayit", "test_ok": False}
    guvenli_log("test_sonuc", kampanya_id=int(kid), durum=sonuc.get("durum"))
    return sonuc


def baslat_istegi(kid: int) -> dict:
    from db import execute

    ensure_tablolar()
    row = _kampanya_oku(int(kid))
    if not row:
        return {"ok": False, "neden": "yok"}
    if str(row.get("durum") or "") not in ("taslak", "test_bekliyor"):
        return {"ok": False, "neden": "durum"}
    karar = baslat_izin(bool(row.get("test_ok")), _acik_baska(int(kid)))
    if karar != "tamam":
        return {"ok": False, "neden": karar}
    sinif = aday_sinifla(aday_satirlari(), row.get("sablon") or "")
    if not sinif["uygun"]:
        return {"ok": False, "neden": "alici_yok"}
    execute(
        "DELETE FROM wa_kampanya_alici WHERE kampanya_id = %s",
        (int(kid),),
    )
    for u in sinif["uygun"]:
        execute(
            """
            INSERT INTO wa_kampanya_alici
                (kampanya_id, musteri_id, telefon_hash, durum, yedek_hitap)
            VALUES (%s, %s, %s, 'bekliyor', %s)
            """,
            (int(kid), int(u["mid"]), u["hash"], bool(u.get("yedek_hitap"))),
        )
    try:
        execute(
            """
            UPDATE wa_kampanya
            SET durum = 'calisiyor', test_anahtar = NULL, updated_at = NOW()
            WHERE id = %s AND test_ok = TRUE
              AND durum IN ('taslak', 'test_bekliyor')
            """,
            (int(kid),),
        )
    except Exception:
        return {"ok": False, "neden": "acik_var"}
    guvenli_log("baslat", kampanya_id=int(kid), alici=sinif["alici"])
    return {"ok": True, "alici": sinif["alici"]}


def durdur_istegi(kid: int) -> dict:
    from db import execute

    ensure_tablolar()
    row = _kampanya_oku(int(kid))
    if not row:
        return {"ok": False, "neden": "yok"}
    if str(row.get("durum") or "") != "calisiyor":
        return {"ok": False, "neden": "durum"}
    execute(
        """
        UPDATE wa_kampanya
        SET durum = 'durduruldu', updated_at = NOW()
        WHERE id = %s AND durum = 'calisiyor'
        """,
        (int(kid),),
    )
    guvenli_log("durdur", kampanya_id=int(kid))
    return {"ok": True, "durum": "durduruldu"}


def devam_istegi(kid: int) -> dict:
    from db import execute

    ensure_tablolar()
    row = _kampanya_oku(int(kid))
    if not row:
        return {"ok": False, "neden": "yok"}
    if str(row.get("durum") or "") != "durduruldu":
        return {"ok": False, "neden": "durum"}
    execute(
        """
        UPDATE wa_kampanya
        SET durum = 'calisiyor', ust_uste_hata = 0, updated_at = NOW()
        WHERE id = %s AND durum = 'durduruldu'
        """,
        (int(kid),),
    )
    guvenli_log("devam", kampanya_id=int(kid))
    return {"ok": True, "durum": "calisiyor"}


def durum_istegi() -> dict:
    from db import fetch_one

    ensure_tablolar()
    row = fetch_one(
        """
        SELECT id, durum, gunluk_tavan, test_ok, ust_uste_hata
        FROM wa_kampanya
        WHERE durum IN ('taslak', 'test_bekliyor', 'calisiyor', 'durduruldu')
        ORDER BY id DESC
        LIMIT 1
        """
    )
    if not row:
        return {"ok": True, "kampanya": None}
    return {
        "ok": True,
        "kampanya": {
            "id": int(row.get("id")),
            "durum": row.get("durum"),
            "tavan": int(row.get("gunluk_tavan") or TAVAN_VARSAYILAN),
            "test_ok": bool(row.get("test_ok")),
            "seri": int(row.get("ust_uste_hata") or 0),
        },
    }


def haric_liste() -> dict:
    from db import fetch_all

    ensure_tablolar()
    try:
        rows = fetch_all("SELECT musteri_id FROM whatsapp_toplu_haric ORDER BY musteri_id")
    except Exception:
        rows = []
    return {"ok": True, "kapali": [int(r.get("musteri_id")) for r in rows or [] if r.get("musteri_id")]}


def haric_yaz(mid, kapali: bool) -> dict:
    from db import execute

    ensure_tablolar()
    try:
        n = int(mid)
    except (TypeError, ValueError):
        return {"ok": False, "neden": "gecersiz"}
    if n <= 0:
        return {"ok": False, "neden": "gecersiz"}
    if kapali:
        execute(
            """
            INSERT INTO whatsapp_toplu_haric (musteri_id)
            VALUES (%s)
            ON CONFLICT (musteri_id) DO NOTHING
            """,
            (n,),
        )
    else:
        execute("DELETE FROM whatsapp_toplu_haric WHERE musteri_id = %s", (n,))
    guvenli_log("haric", mid=n, kapali=bool(kapali))
    return {"ok": True, "mid": n, "kapali": bool(kapali)}
