# -*- coding: utf-8 -*-
"""Borçlandır / Tahsil et tekrar basışı.

Yalnız panel satırı silinip yeniden yazılır.
Banka, elle makbuz, ödeme linki ve kaynağı bilinmeyen satır silinmez.
Açıklamadaki ay işareti tek başına silme gerekçesi değildir; kaynak şarttır.
audit_yaz açıklama ve serbest metni attığı için geri yazım yedek tablodadır.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal

from audit_log import audit_yaz
from plan_fatura_kilit import gib_sinyali, kayit_gecerli

_LOG = logging.getLogger("bestofficeerp.panel_tekrar")

PANEL_KAYNAK = "grid_toplu"
TABLO_TAHSIL = "tahsilatlar"
TABLO_BORC = "faturalar"

TAHSIL_KOLON = (
    "id",
    "musteri_id",
    "customer_id",
    "fatura_id",
    "tutar",
    "odeme_turu",
    "aciklama",
    "tahsilat_tarihi",
    "makbuz_no",
    "cek_detay",
    "havale_banka",
    "banka_referans_no",
    "tahsil_eden",
    "islem_grubu_id",
    "kaynak",
    "created_at",
)
BORC_KOLON = (
    "id",
    "fatura_no",
    "musteri_id",
    "musteri_adi",
    "tutar",
    "kdv_tutar",
    "toplam",
    "durum",
    "fatura_tarihi",
    "vade_tarihi",
    "notlar",
    "sevk_adresi",
    "ettn",
    "yon",
    "satirlar_json",
    "created_at",
)

SQL_TABLO = """
CREATE TABLE IF NOT EXISTS panel_tekrar_yedek (
    id SERIAL PRIMARY KEY,
    tablo_adi TEXT NOT NULL,
    kayit_id INTEGER NOT NULL,
    musteri_id INTEGER,
    ay_iso DATE,
    satir_json TEXT NOT NULL,
    geri_yazildi BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""".strip()

SQL_YEDEK_EKLE = """
INSERT INTO panel_tekrar_yedek (tablo_adi, kayit_id, musteri_id, ay_iso, satir_json)
VALUES (%s, %s, %s, %s, %s)
RETURNING id
""".strip()

SQL_TAHSIL_SIL = """
DELETE FROM tahsilatlar
WHERE id = %s
  AND (musteri_id = %s OR customer_id = %s)
  AND COALESCE(kaynak, '') = 'grid_toplu'
RETURNING id, makbuz_no
""".strip()

SQL_BORC_SIL = """
DELETE FROM faturalar
WHERE id = %s
  AND musteri_id = %s
  AND COALESCE(notlar, '') LIKE '%%|AYLIK_TUTAR|%%'
  AND COALESCE(ettn, '') = ''
  AND NOT EXISTS (
        SELECT 1 FROM tahsilatlar t
        WHERE t.fatura_id = faturalar.id
          AND COALESCE(t.kaynak, '') <> 'grid_toplu'
  )
RETURNING id, fatura_no
""".strip()

SQL_YEDEK_OKU = """
SELECT id, tablo_adi, kayit_id, musteri_id, ay_iso, satir_json, geri_yazildi
FROM panel_tekrar_yedek
WHERE id = %s
""".strip()

SQL_GERI_BAYRAK = """
UPDATE panel_tekrar_yedek
SET geri_yazildi = TRUE
WHERE id = %s AND geri_yazildi = FALSE
""".strip()


def onay_metni(panel_n: int, diger_m: int) -> str:
    return (
        "Bu ay için panelden yapılmış %s tahsilat/borç satırı silinip yeniden yazılacak. "
        "Başka kaynaktan %s satır var, onlara dokunulmayacak."
    ) % (int(panel_n), int(diger_m))


def _ay_iso(yil, ay) -> str:
    return date(int(yil), int(ay), 1).isoformat()


def _ay_anahtar(iso_gun: str) -> str | None:
    s = str(iso_gun or "").strip()[:10]
    if len(s) < 7 or s[4] != "-":
        return None
    try:
        y, m = int(s[0:4]), int(s[5:7])
    except ValueError:
        return None
    if m < 1 or m > 12:
        return None
    return f"{y:04d}-{m:02d}"


def _tarih_ay(val) -> str | None:
    if isinstance(val, datetime):
        val = val.date()
    if isinstance(val, date):
        return f"{val.year:04d}-{val.month:02d}"
    return _ay_anahtar(str(val or ""))


def _isaret_aylari(aciklama) -> list[str]:
    import re

    text = str(aciklama or "")
    bulunan = []
    for iso in re.findall(r"\|AYLIK_TAH\|(\d{4}-\d{2}-\d{2})\|", text):
        anahtar = _ay_anahtar(iso)
        if anahtar and anahtar not in bulunan:
            bulunan.append(anahtar)
    for iso in re.findall(r"\|AYLIK_PAY\|(\d{4}-\d{2}-\d{2})=", text):
        anahtar = _ay_anahtar(iso)
        if anahtar and anahtar not in bulunan:
            bulunan.append(anahtar)
    return bulunan


def tahsil_aylari(row) -> list[str]:
    """Ay işareti varsa yalnız o aylar. İşaret yoksa tahsilat tarihi ayı."""
    isaret = _isaret_aylari((row or {}).get("aciklama"))
    if isaret:
        return isaret
    anahtar = _tarih_ay((row or {}).get("tahsilat_tarihi"))
    return [anahtar] if anahtar else []


def panel_kaynak_mi(kaynak) -> bool:
    return str(kaynak or "").strip() == PANEL_KAYNAK


def _borc_ayi(row) -> str | None:
    import re

    m = re.search(r"\|AYLIK_TUTAR\|(\d{4}-\d{2}-\d{2})\|", str((row or {}).get("notlar") or ""))
    if not m:
        return None
    return _ay_anahtar(m.group(1))


def borc_bag_nedeni(row, tahsil_rows) -> str | None:
    """Bağlı panel borç silinmez. Dönüş: ettn, belge veya odeme_eslesme."""
    if not isinstance(row, dict):
        return "belge"
    if str(row.get("ettn") or "").strip():
        return "ettn"
    if gib_sinyali(row):
        return "belge"
    if "|GIB_NO_TASINDI|" in str(row.get("notlar") or ""):
        return "belge"
    try:
        fid = int(row.get("id") or 0)
    except (TypeError, ValueError):
        fid = 0
    if fid <= 0:
        return None
    for tah in tahsil_rows or []:
        if not isinstance(tah, dict):
            continue
        try:
            bag = int(tah.get("fatura_id") or 0)
        except (TypeError, ValueError):
            bag = 0
        if bag == fid and not panel_kaynak_mi(tah.get("kaynak")):
            return "odeme_eslesme"
    return None


def _satir_id(row) -> int:
    try:
        return int((row or {}).get("id") or 0)
    except (TypeError, ValueError):
        return 0


def karar_ver(aylar, tahsil_rows, borc_rows, *, haric=None, taraf="borc") -> dict:
    """Saf karar. Yazmaz.

    taraf=borc: panel tahsilat ve bağlı olmayan panel borç silinebilir.
    taraf=tahsil: yalnız panel tahsilat silinebilir.
    haric: dondurma / AUTO_INV gibi bu turda hiç dokunulmayan YYYY-MM.
    """
    istenen = []
    for cift in aylar or []:
        try:
            yil, ay = int(cift[0]), int(cift[1])
        except (TypeError, ValueError, IndexError):
            continue
        if ay < 1 or ay > 12:
            continue
        anahtar = f"{yil:04d}-{ay:02d}"
        if anahtar not in istenen:
            istenen.append(anahtar)
    haric_kume = set(haric or ())
    aktif = [a for a in istenen if a not in haric_kume]
    aktif_kume = set(aktif)

    tahsil_by_ay = {a: [] for a in aktif}
    for row in tahsil_rows or []:
        if not isinstance(row, dict) or _satir_id(row) <= 0:
            continue
        for anahtar in tahsil_aylari(row):
            if anahtar in tahsil_by_ay:
                tahsil_by_ay[anahtar].append(row)

    borc_by_ay = {a: [] for a in aktif}
    for row in borc_rows or []:
        if not isinstance(row, dict) or _satir_id(row) <= 0:
            continue
        if not kayit_gecerli(row):
            continue
        anahtar = _borc_ayi(row)
        if anahtar in borc_by_ay:
            borc_by_ay[anahtar].append(row)

    sil_tahsil = []
    sil_borc = []
    gorulen_tahsil = set()
    gorulen_borc = set()
    gorulen_diger = set()
    atla_tam = {}
    bagli = []
    uyarilar = []

    for anahtar in aktif:
        yil, ay = int(anahtar[0:4]), int(anahtar[5:7])
        panel_tah = []
        diger = []
        for row in tahsil_by_ay[anahtar]:
            rid = _satir_id(row)
            if panel_kaynak_mi(row.get("kaynak")):
                panel_tah.append(row)
            else:
                diger.append(row)
                gorulen_diger.add(rid)
        silinecek_borc = []
        for row in borc_by_ay[anahtar]:
            neden = borc_bag_nedeni(row, tahsil_rows)
            if neden:
                bagli.append({"yil": yil, "ay": ay, "neden": "bagli_borc", "bag": neden})
                uyarilar.append({"yil": yil, "ay": ay, "neden": "bagli_borc"})
            elif taraf == "borc":
                silinecek_borc.append(row)
        if taraf != "borc":
            silinecek_borc = []
        if not panel_tah and not silinecek_borc and diger:
            neden = "bagli_borc" if any(b["yil"] == yil and b["ay"] == ay for b in bagli) else "baska_kaynak"
            atla_tam[anahtar] = {"yil": yil, "ay": ay, "neden": neden}
            uyarilar.append({"yil": yil, "ay": ay, "neden": neden})
            continue
        if diger:
            uyarilar.append({"yil": yil, "ay": ay, "neden": "diger_durur", "adet": len(diger)})
        for row in panel_tah:
            rid = _satir_id(row)
            if rid in gorulen_tahsil:
                continue
            gorulen_tahsil.add(rid)
            sil_tahsil.append(row)
        for row in silinecek_borc:
            rid = _satir_id(row)
            if rid in gorulen_borc:
                continue
            gorulen_borc.add(rid)
            sil_borc.append(row)

    panel_n = len(sil_tahsil) + len(sil_borc)
    return {
        "panel_n": panel_n,
        "diger_m": len(gorulen_diger),
        "mesaj_onay": onay_metni(panel_n, len(gorulen_diger)) if panel_n else "",
        "sil_tahsil": sil_tahsil,
        "sil_borc": sil_borc,
        "atla_tam": atla_tam,
        "bagli": bagli,
        "uyarilar": uyarilar,
        "onay_gerekli": panel_n > 0,
    }


def _json_satir(row: dict) -> str:
    def _cevir(v):
        if isinstance(v, Decimal):
            return format(v, "f")
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, date):
            return v.isoformat()
        return v

    govde = {k: _cevir(v) for k, v in (row or {}).items()}
    return json.dumps(govde, ensure_ascii=False, default=str)


def _oku_satir(oku, sql, params):
    row = oku(sql, params)
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return None


def _calistir(calistir, sql, params=None):
    if params is None:
        calistir(sql)
    else:
        calistir(sql, params)


def panel_satirlari_sil(calistir, oku, *, tahsil_rows, borc_rows, musteri_id, kullanici) -> dict:
    """Yedek + audit, sonra sil. Kaynak şartı SQL'de de durur. Kopyasız silme yok."""
    _calistir(calistir, SQL_TABLO)
    tahsil_silinen = []
    borc_silinen = []
    yedek_ids = []

    def _yedekle(tablo, row, kolonlar, ay):
        rid = _satir_id(row)
        if rid <= 0:
            raise RuntimeError("yedeklenecek id yok")
        govde = {}
        for ad in kolonlar:
            if ad in row:
                govde[ad] = row.get(ad)
        if "id" not in govde:
            govde["id"] = rid
        ay_iso = None
        if ay:
            ay_iso = date(int(ay[0:4]), int(ay[5:7]), 1)
        yedek = _oku_satir(
            oku,
            SQL_YEDEK_EKLE,
            (tablo, rid, int(musteri_id), ay_iso, _json_satir(govde)),
        )
        if not yedek or not yedek.get("id"):
            raise RuntimeError("yedek yazilamadi")
        yedek_id = int(yedek["id"])
        ozet = {
            "yedek_id": yedek_id,
            "tablo": tablo,
            "kayit_id": rid,
            "ay": ay or "",
        }
        if tablo == TABLO_TAHSIL:
            ozet["kaynak"] = PANEL_KAYNAK
            if row.get("makbuz_no") not in (None, ""):
                ozet["makbuz_no"] = str(row.get("makbuz_no"))[:40]
        elif row.get("fatura_no") not in (None, ""):
            ozet["fatura_no"] = str(row.get("fatura_no"))[:40]
        audit_yaz(
            calistir,
            olay="panel_tekrar_sil",
            varlik=tablo,
            kayit_id=rid,
            musteri_id=musteri_id,
            kullanici=kullanici,
            anlik=ozet,
            yeni={"durum": "silindi", "yedek_id": yedek_id},
        )
        yedek_ids.append(yedek_id)
        return yedek_id

    for row in tahsil_rows or []:
        if not panel_kaynak_mi(row.get("kaynak")):
            raise RuntimeError("panel disi tahsilat yedeklenmez")
        aylar = tahsil_aylari(row)
        _yedekle(TABLO_TAHSIL, row, TAHSIL_KOLON, aylar[0] if aylar else "")
        silinen = _oku_satir(oku, SQL_TAHSIL_SIL, (_satir_id(row), int(musteri_id), int(musteri_id)))
        if not silinen:
            raise RuntimeError("panel tahsilati silinmedi")
        ay0 = tahsil_aylari(row)
        parca = {"id": silinen.get("id"), "makbuz_no": silinen.get("makbuz_no")}
        if ay0:
            parca["yil"] = int(ay0[0][0:4])
            parca["ay"] = int(ay0[0][5:7])
        tahsil_silinen.append(parca)

    for row in borc_rows or []:
        if borc_bag_nedeni(row, tahsil_rows):
            raise RuntimeError("bagli borc silinmez")
        ay = _borc_ayi(row) or ""
        _yedekle(TABLO_BORC, row, BORC_KOLON, ay)
        silinen = _oku_satir(oku, SQL_BORC_SIL, (_satir_id(row), int(musteri_id)))
        if not silinen:
            raise RuntimeError("panel borcu silinmedi")
        parca = {"id": silinen.get("id"), "fatura_no": silinen.get("fatura_no")}
        if ay:
            parca["yil"] = int(ay[0:4])
            parca["ay"] = int(ay[5:7])
        borc_silinen.append(parca)

    return {
        "tahsil_silinen": tahsil_silinen,
        "borc_silinen": borc_silinen,
        "yedek_ids": yedek_ids,
    }


def _seq_duzelt(calistir, tablo: str) -> None:
    if tablo not in (TABLO_TAHSIL, TABLO_BORC):
        return
    _calistir(calistir, "SAVEPOINT panel_seq")
    try:
        _calistir(
            calistir,
            "SELECT setval(pg_get_serial_sequence('%s', 'id'), (SELECT COALESCE(MAX(id), 1) FROM %s))"
            % (tablo, tablo),
        )
        _calistir(calistir, "RELEASE SAVEPOINT panel_seq")
    except Exception:
        _LOG.exception("panel sira duzeltilemedi")
        try:
            _calistir(calistir, "ROLLBACK TO SAVEPOINT panel_seq")
        except Exception:
            _LOG.exception("panel sira savepoint geri alinamadi")


def panel_satir_geri_yaz(yedek_id, calistir, oku) -> dict:
    """Yedekten aynı değerlerle geri koyar. İkinci çağrı reddedilir. Arayüz yok."""
    try:
        yid = int(yedek_id)
    except (TypeError, ValueError):
        return {"ok": False, "neden": "yedek_yok"}
    yedek = _oku_satir(oku, SQL_YEDEK_OKU, (yid,))
    if not yedek:
        return {"ok": False, "neden": "yedek_yok"}
    if yedek.get("geri_yazildi") in (True, "t", "true", 1):
        return {"ok": False, "neden": "zaten_geri_yazildi"}
    tablo = str(yedek.get("tablo_adi") or "")
    kolonlar = TAHSIL_KOLON if tablo == TABLO_TAHSIL else BORC_KOLON if tablo == TABLO_BORC else None
    if not kolonlar:
        return {"ok": False, "neden": "tablo"}
    try:
        govde = json.loads(yedek.get("satir_json") or "{}")
    except (TypeError, ValueError):
        return {"ok": False, "neden": "yedek_bozuk"}
    if not isinstance(govde, dict) or _satir_id(govde) <= 0:
        return {"ok": False, "neden": "yedek_bozuk"}
    var = _oku_satir(oku, "SELECT id FROM %s WHERE id = %%s" % tablo, (_satir_id(govde),))
    if var:
        return {"ok": False, "neden": "kayit_duruyor"}
    alanlar = [ad for ad in kolonlar if ad in govde]
    if "id" not in alanlar:
        return {"ok": False, "neden": "yedek_bozuk"}
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (
        tablo,
        ", ".join(alanlar),
        ", ".join(["%s"] * len(alanlar)),
    )
    _calistir(calistir, sql, tuple(govde.get(ad) for ad in alanlar))
    _calistir(calistir, SQL_GERI_BAYRAK, (yid,))
    kontrol = _oku_satir(oku, SQL_YEDEK_OKU, (yid,))
    if not kontrol or kontrol.get("geri_yazildi") not in (True, "t", "true", 1):
        raise RuntimeError("bayrak yazilamadi")
    _seq_duzelt(calistir, tablo)
    return {"ok": True, "id": _satir_id(govde), "tablo": tablo, "yedek_id": yid}
