# -*- coding: utf-8 -*-
"""Borçlandır / Tahsil et tekrar basışı.

Yalnız panel satırı silinip yeniden yazılır.
Banka, elle makbuz, ödeme linki ve kaynağı bilinmeyen satır silinmez
(eski boş kaynak: yalnız PANEL_TEKRAR_ESKI_KAYNAK_ENABLED=1 ve taraf=borc + kural A).
Açıklamadaki ay işareti tek başına silme gerekçesi değildir; kaynak şarttır.
audit_yaz açıklama ve serbest metni attığı için geri yazım yedek tablodadır.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal

from audit_log import audit_yaz
from plan_fatura_kilit import gib_sinyali, kayit_gecerli

_LOG = logging.getLogger("bestofficeerp.panel_tekrar")

PANEL_KAYNAK = "grid_toplu"
ESKI_KAYNAK_ETIKET = "eski_bos"
CAKISMA_KAYNAKLAR = frozenset({"manuel_makbuz", "banka_import", "odeme_linki"})
TABLO_TAHSIL = "tahsilatlar"
TABLO_BORC = "faturalar"


def eski_kaynak_enabled() -> bool:
    """Varsayılan kapalı. Açıkken yalnız taraf=borc kural A uygulanır."""
    raw = str(os.environ.get("PANEL_TEKRAR_ESKI_KAYNAK_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on", "evet")

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
    islem_tamam BOOLEAN NOT NULL DEFAULT FALSE,
    taraf TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""".strip()

SQL_SUTUN_TAMAM = "ALTER TABLE panel_tekrar_yedek ADD COLUMN IF NOT EXISTS islem_tamam BOOLEAN NOT NULL DEFAULT FALSE"
SQL_SUTUN_TARAF = "ALTER TABLE panel_tekrar_yedek ADD COLUMN IF NOT EXISTS taraf TEXT"

SQL_YEDEK_EKLE = """
INSERT INTO panel_tekrar_yedek (tablo_adi, kayit_id, musteri_id, ay_iso, satir_json, taraf)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING id
""".strip()

SQL_KILIT = "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)"

# Aynı ay için onaylı ikinci istek bu pencerede yedek/silme/yazımı tekrarlamaz.
KISA_KORUMA_SANIYE = 20

SQL_KISA_KORUMA = """
/* panel_kisa_koruma */
SELECT ay_iso
FROM panel_tekrar_yedek
WHERE musteri_id = %s
  AND taraf = %s
  AND islem_tamam = TRUE
  AND geri_yazildi = FALSE
  AND created_at > NOW() - INTERVAL '20 seconds'
""".strip()

SQL_ISLEM_TAMAM = """
/* panel_islem_tamam */
UPDATE panel_tekrar_yedek SET islem_tamam = TRUE WHERE id = %s AND geri_yazildi = FALSE
""".strip()

SQL_TAHSIL_SIL = """
DELETE FROM tahsilatlar
WHERE id = %s
  AND (musteri_id = %s OR customer_id = %s)
  AND COALESCE(kaynak, '') = 'grid_toplu'
RETURNING id, makbuz_no
""".strip()

# Eski boş kaynak: Python kapısı marker+ayın1i doğrular; SQL yalnız boş kaynak şartı.
SQL_TAHSIL_SIL_ESKI = """
DELETE FROM tahsilatlar
WHERE id = %s
  AND (musteri_id = %s OR customer_id = %s)
  AND COALESCE(kaynak, '') = ''
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

# tahsilat_tarihi canlı şemalarda TEXT. Aralık date'e dökülmez.
# YYYY-MM-DD uymayan metin bu dala girmez; sorgu hata vermez.
SQL_TAHSIL_TARIH_ARALIK = """
(
    LEFT(btrim(COALESCE(tahsilat_tarihi::text, '')), 10) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    AND LEFT(btrim(COALESCE(tahsilat_tarihi::text, '')), 10) >= %s::text
    AND LEFT(btrim(COALESCE(tahsilat_tarihi::text, '')), 10) < %s::text
)
""".strip()

_ISO_GUN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def tahsil_tarih_sinifi(val) -> str:
    """bos | iso | bozuk. Boş ve NULL bozuk değildir; ay işareti yolu durur."""
    if val is None:
        return "bos"
    if isinstance(val, datetime):
        val = val.date()
    if isinstance(val, date):
        return "iso"
    s = str(val).strip()
    if not s:
        return "bos"
    head = s[:10]
    if not _ISO_GUN.match(head):
        return "bozuk"
    try:
        date.fromisoformat(head)
    except ValueError:
        return "bozuk"
    return "iso"


def tahsil_satirlari_ayir(rows):
    """Bozuk tarih silme listesine girmez. Dönüş: temiz satırlar, adet."""
    temiz = []
    bozuk_n = 0
    for row in rows or []:
        if not isinstance(row, dict) or tahsil_tarih_sinifi(row.get("tahsilat_tarihi")) == "bozuk":
            bozuk_n += 1
            continue
        temiz.append(row)
    return temiz, bozuk_n


def onay_metni(panel_n: int, diger_m: int) -> str:
    return (
        "Bu ay için panelden yapılmış %s tahsilat/borç satırı silinip yeniden yazılacak. "
        "Başka kaynaktan %s satır var, onlara dokunulmayacak."
    ) % (int(panel_n), int(diger_m))


def kaynak_bos_mu(kaynak) -> bool:
    """NULL ve '' boş sayılır; 'grid_toplu' vb. dolu. Tip farkı korunur (geri yazım)."""
    if kaynak is None:
        return True
    return str(kaynak).strip() == ""


def _tarih_gun_iso(val) -> str | None:
    if isinstance(val, datetime):
        val = val.date()
    if isinstance(val, date):
        return val.isoformat()
    s = str(val or "").strip()
    if not s:
        return None
    head = s[:10]
    if not _ISO_GUN.match(head):
        return None
    try:
        date.fromisoformat(head)
    except ValueError:
        return None
    return head


def eski_panel_silinebilir_mi(row, anahtar: str) -> bool:
    """Kural A: boş kaynak + marker ayı + tahsilat_tarihi = o ayın 1'i."""
    if not isinstance(row, dict) or not kaynak_bos_mu(row.get("kaynak")):
        return False
    anahtar = str(anahtar or "")[:7]
    if len(anahtar) < 7:
        return False
    if anahtar not in _isaret_aylari(row.get("aciklama")):
        return False
    gun = _tarih_gun_iso(row.get("tahsilat_tarihi"))
    return gun == f"{anahtar}-01"


def panel_yedeklenebilir_mi(kaynak, *, taraf="borc", row=None, anahtar=None) -> bool:
    if panel_kaynak_mi(kaynak):
        return True
    if not eski_kaynak_enabled() or str(taraf or "") != "borc":
        return False
    if row is None:
        return kaynak_bos_mu(kaynak)
    if anahtar:
        return eski_panel_silinebilir_mi(row, anahtar)
    for a in tahsil_aylari(row):
        if eski_panel_silinebilir_mi(row, a):
            return True
    return False


def _satir_ozet(row, anahtar: str, *, kaynak_etiket: str) -> dict:
    yil, ay = int(anahtar[0:4]), int(anahtar[5:7])
    try:
        tutar = float(row.get("tutar") or 0)
    except (TypeError, ValueError):
        tutar = 0.0
    gun = _tarih_gun_iso(row.get("tahsilat_tarihi")) or ""
    ham_k = row.get("kaynak")
    if kaynak_etiket == ESKI_KAYNAK_ETIKET:
        goster = ESKI_KAYNAK_ETIKET
    elif ham_k is None or str(ham_k).strip() == "":
        goster = "(boş)"
    else:
        goster = str(ham_k).strip()
    return {
        "id": _satir_id(row),
        "yil": yil,
        "ay": ay,
        "ay_iso": anahtar,
        "tutar": tutar,
        "tahsilat_tarihi": gun,
        "kaynak": goster,
    }


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


def _durust_mesaj(plan_parca: dict) -> str:
    """Önizleme / modal için dürüst özet. PII yok."""
    grid = plan_parca.get("silinecek_grid") or []
    eski = plan_parca.get("silinecek_eski") or []
    degis = plan_parca.get("degismeyecek") or []
    borc_n = int(plan_parca.get("sil_borc_n") or 0)
    parcalar = []
    if grid or eski or borc_n:
        n_sil_ay = len({f"{r.get('yil')}-{r.get('ay')}" for r in (grid + eski)})
        if n_sil_ay or borc_n:
            parcalar.append(
                "Silinecek: %s ay tahsilat (%s grid_toplu, %s eski kayıt)%s."
                % (
                    n_sil_ay,
                    len(grid),
                    len(eski),
                    ("; %s panel borç" % borc_n) if borc_n else "",
                )
            )
        if grid or eski:
            parcalar.append(
                "Yalnız bu silinecek aylarda grid kırmızı görünür; ekstrede o ayların tahsilat satırı olmaz."
            )
        if eski:
            parcalar.append(
                "Eski kayıt (kaynağı bilinmiyor): yedek alınır, geri yazılabilir. Ayrı onay gerekir."
            )
    else:
        parcalar.append("Hiçbir tahsilat silinmeyecek.")
    if degis:
        nedenler = []
        for d in degis[:12]:
            nedenler.append(
                "%04d-%02d (%s)" % (int(d.get("yil") or 0), int(d.get("ay") or 0), d.get("neden") or "?")
            )
        ekstra = "" if len(degis) <= 12 else " …"
        parcalar.append("DEĞİŞMEYECEK %s ay: %s%s." % (len(degis), ", ".join(nedenler), ekstra))
    if not grid and not eski and not borc_n and not degis:
        parcalar.append("Seçilen aylar zaten borçlu veya dokunulacak panel satırı yok.")
    return " ".join(parcalar)


def karar_ver(aylar, tahsil_rows, borc_rows, *, haric=None, taraf="borc") -> dict:
    """Saf karar. Yazmaz.

    taraf=borc: panel tahsilat ve bağlı olmayan panel borç silinebilir.
    taraf=tahsil: yalnız panel tahsilat (grid_toplu) silinebilir — eski kaynak yolu YOK.
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
    sil_tahsil_grid = []
    sil_tahsil_eski = []
    sil_borc = []
    silinecek_grid = []
    silinecek_eski = []
    degismeyecek = []
    gorulen_tahsil = set()
    gorulen_borc = set()
    gorulen_diger = set()
    atla_tam = {}
    bagli = []
    uyarilar = []
    eski_acik = eski_kaynak_enabled() and str(taraf or "") == "borc"

    for anahtar in aktif:
        yil, ay = int(anahtar[0:4]), int(anahtar[5:7])
        panel_tah = []
        eski_tah = []
        diger = []
        cakisma_var = False
        for row in tahsil_by_ay[anahtar]:
            rid = _satir_id(row)
            k_raw = row.get("kaynak")
            k_str = str(k_raw or "").strip()
            if panel_kaynak_mi(k_raw):
                panel_tah.append(row)
            elif k_str in CAKISMA_KAYNAKLAR:
                cakisma_var = True
                diger.append(row)
                gorulen_diger.add(rid)
            elif eski_acik and eski_panel_silinebilir_mi(row, anahtar):
                eski_tah.append(row)
            else:
                diger.append(row)
                gorulen_diger.add(rid)
        if cakisma_var and eski_tah:
            for row in eski_tah:
                diger.append(row)
                gorulen_diger.add(_satir_id(row))
            eski_tah = []
            uyarilar.append({"yil": yil, "ay": ay, "neden": "cakisma_koru"})
            degismeyecek.append({
                "yil": yil, "ay": ay, "neden": "cakisma_makbuz_banka",
            })
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
        if not panel_tah and not eski_tah and not silinecek_borc and diger:
            if any(b["yil"] == yil and b["ay"] == ay for b in bagli):
                neden = "bagli_borc"
            elif diger and all(
                kaynak_bos_mu(r.get("kaynak"))
                and anahtar in _isaret_aylari(r.get("aciklama"))
                and not eski_panel_silinebilir_mi(r, anahtar)
                for r in diger
            ):
                neden = "tarih_uyusmaz"
            else:
                neden = "baska_kaynak"
            atla_tam[anahtar] = {"yil": yil, "ay": ay, "neden": neden}
            uyarilar.append({"yil": yil, "ay": ay, "neden": neden})
            if not any(d.get("yil") == yil and d.get("ay") == ay for d in degismeyecek):
                degismeyecek.append({"yil": yil, "ay": ay, "neden": neden})
            continue
        if diger:
            uyarilar.append({"yil": yil, "ay": ay, "neden": "diger_durur", "adet": len(diger)})
            if not panel_tah and not eski_tah:
                pass
            elif any(str(r.get("kaynak") or "").strip() in CAKISMA_KAYNAKLAR or kaynak_bos_mu(r.get("kaynak")) for r in diger):
                if not any(d.get("yil") == yil and d.get("ay") == ay for d in degismeyecek):
                    # Aynı ayda silinenler varken duran satırlar — özet için
                    pass
        for row in panel_tah:
            rid = _satir_id(row)
            if rid in gorulen_tahsil:
                continue
            gorulen_tahsil.add(rid)
            sil_tahsil.append(row)
            sil_tahsil_grid.append(row)
            silinecek_grid.append(_satir_ozet(row, anahtar, kaynak_etiket=PANEL_KAYNAK))
        for row in eski_tah:
            rid = _satir_id(row)
            if rid in gorulen_tahsil:
                continue
            gorulen_tahsil.add(rid)
            sil_tahsil.append(row)
            sil_tahsil_eski.append(row)
            silinecek_eski.append(_satir_ozet(row, anahtar, kaynak_etiket=ESKI_KAYNAK_ETIKET))
        for row in silinecek_borc:
            rid = _satir_id(row)
            if rid in gorulen_borc:
                continue
            gorulen_borc.add(rid)
            sil_borc.append(row)

    # Tarihi ayın 1'i olmayan boş kaynaklılar vb. için istenen aylarda silinmeyenleri listele
    for anahtar in aktif:
        if anahtar in atla_tam:
            continue
        yil, ay = int(anahtar[0:4]), int(anahtar[5:7])
        if any(d.get("yil") == yil and d.get("ay") == ay for d in degismeyecek):
            continue
        silinen_ids = {_satir_id(r) for r in sil_tahsil}
        for row in tahsil_by_ay[anahtar]:
            rid = _satir_id(row)
            if rid in silinen_ids:
                continue
            if panel_kaynak_mi(row.get("kaynak")):
                continue
            neden = "baska_kaynak"
            if kaynak_bos_mu(row.get("kaynak")):
                if anahtar in _isaret_aylari(row.get("aciklama")) and not eski_panel_silinebilir_mi(row, anahtar):
                    neden = "tarih_uyusmaz"
                elif not eski_acik:
                    neden = "baska_kaynak"
            if not any(d.get("yil") == yil and d.get("ay") == ay and d.get("neden") == neden for d in degismeyecek):
                degismeyecek.append({"yil": yil, "ay": ay, "neden": neden})

    panel_n = len(sil_tahsil) + len(sil_borc)
    parca = {
        "silinecek_grid": silinecek_grid,
        "silinecek_eski": silinecek_eski,
        "degismeyecek": degismeyecek,
        "sil_borc_n": len(sil_borc),
    }
    mesaj = _durust_mesaj(parca) if (panel_n or degismeyecek) else ""
    if panel_n and not mesaj:
        mesaj = onay_metni(panel_n, len(gorulen_diger))
    return {
        "panel_n": panel_n,
        "diger_m": len(gorulen_diger),
        "mesaj_onay": mesaj,
        "sil_tahsil": sil_tahsil,
        "sil_tahsil_grid": sil_tahsil_grid,
        "sil_tahsil_eski": sil_tahsil_eski,
        "silinecek_grid": silinecek_grid,
        "silinecek_eski": silinecek_eski,
        "degismeyecek": degismeyecek,
        "sil_borc": sil_borc,
        "atla_tam": atla_tam,
        "bagli": bagli,
        "uyarilar": uyarilar,
        "onay_gerekli": panel_n > 0,
        "onay_eski_kayit_gerekli": len(sil_tahsil_eski) > 0,
        "eski_kaynak_enabled": bool(eski_acik),
    }


def plan_eski_filtrele(plan: dict, onay_eski: bool) -> dict:
    """onay_eski yoksa eski kaynaklıları sil listesinden çıkar (SİLMEZ)."""
    if not isinstance(plan, dict):
        return plan
    if onay_eski:
        return plan
    eski_ids = {_satir_id(r) for r in (plan.get("sil_tahsil_eski") or [])}
    if not eski_ids:
        return plan
    plan["sil_tahsil"] = [r for r in (plan.get("sil_tahsil") or []) if _satir_id(r) not in eski_ids]
    plan["sil_tahsil_eski"] = []
    plan["silinecek_eski"] = []
    plan["panel_n"] = len(plan.get("sil_tahsil") or []) + len(plan.get("sil_borc") or [])
    plan["onay_eski_kayit_gerekli"] = False
    return plan


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


def kilit_al(calistir, musteri_id, taraf: str) -> None:
    """Aynı müşteri ve taraf için eşzamanlı tekrar basışı sıraya koyar. İşlem bitince kilit kalkar."""
    _calistir(
        calistir,
        SQL_KILIT,
        ("panel_tekrar:%s:%s" % (int(musteri_id), str(taraf or "")),),
    )


def kisa_koruma_aylari(calistir, liste_al, musteri_id, taraf: str) -> set:
    """Son birkaç saniyede tamamlanmış aylar. İkinci onaylı istek bunları yeniden silmez."""
    _calistir(calistir, SQL_KISA_KORUMA, (int(musteri_id), str(taraf or "")))
    rows = liste_al() or []
    out = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("ay_iso")
        if raw is None:
            continue
        metin = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)
        if len(metin) >= 7 and metin[4] == "-":
            out.add(metin[:7])
    return out


def korumayi_uygula(plan: dict, korunan, istenen_aylar=None) -> dict:
    """Korunan ay silme listesinden çıkar ve yeniden yazımı atlar. Sessiz geri yazma yok."""
    kume = {str(a)[:7] for a in (korunan or []) if a}
    if not kume:
        return plan
    atla = plan.setdefault("atla_tam", {})
    for ay in list(istenen_aylar or []) + list(kume):
        ay = str(ay)[:7]
        if ay in kume and ay not in atla:
            atla[ay] = {"neden": "ayni_islem", "ay": ay}

    def _aylar(row, tahsil):
        if tahsil:
            return [a[:7] for a in tahsil_aylari(row)]
        borc_ay = _borc_ayi(row) or ""
        return [borc_ay[:7]] if borc_ay else []

    sil_t = []
    for row in plan.get("sil_tahsil") or []:
        aylar = _aylar(row, True)
        if any(a in kume for a in aylar):
            continue
        sil_t.append(row)
    sil_b = []
    for row in plan.get("sil_borc") or []:
        aylar = _aylar(row, False)
        if any(a in kume for a in aylar):
            continue
        sil_b.append(row)
    plan["sil_tahsil"] = sil_t
    plan["sil_borc"] = sil_b
    return plan


def islem_tamamla(calistir, yedek_ids) -> None:
    for yid in yedek_ids or []:
        try:
            _calistir(calistir, SQL_ISLEM_TAMAM, (int(yid),))
        except (TypeError, ValueError):
            continue


def panel_satirlari_sil(calistir, oku, *, tahsil_rows, borc_rows, musteri_id, kullanici, taraf="borc") -> dict:
    """Yedek + audit, sonra sil. Kaynak şartı SQL'de de durur. Kayıt zaten yoksa ikinci kez yedeklemez."""
    _calistir(calistir, SQL_TABLO)
    _calistir(calistir, SQL_SUTUN_TAMAM)
    _calistir(calistir, SQL_SUTUN_TARAF)
    taraf_adi = str(taraf or "borc")
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
            (tablo, rid, int(musteri_id), ay_iso, _json_satir(govde), taraf_adi),
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
            if panel_kaynak_mi(row.get("kaynak")):
                ozet["kaynak"] = PANEL_KAYNAK
            elif kaynak_bos_mu(row.get("kaynak")):
                ozet["kaynak"] = ESKI_KAYNAK_ETIKET
            else:
                ozet["kaynak"] = str(row.get("kaynak") or "")[:40]
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
        if not panel_yedeklenebilir_mi(row.get("kaynak"), taraf=taraf_adi, row=row):
            raise RuntimeError("panel disi tahsilat yedeklenmez")
        rid = _satir_id(row)
        if not _oku_satir(oku, "SELECT id FROM tahsilatlar WHERE id = %s", (rid,)):
            continue
        aylar = tahsil_aylari(row)
        _yedekle(TABLO_TAHSIL, row, TAHSIL_KOLON, aylar[0] if aylar else "")
        if panel_kaynak_mi(row.get("kaynak")):
            sil_sql = SQL_TAHSIL_SIL
        elif kaynak_bos_mu(row.get("kaynak")):
            sil_sql = SQL_TAHSIL_SIL_ESKI
        else:
            raise RuntimeError("panel disi tahsilat yedeklenmez")
        silinen = _oku_satir(oku, sil_sql, (_satir_id(row), int(musteri_id), int(musteri_id)))
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
        rid = _satir_id(row)
        if not _oku_satir(oku, "SELECT id FROM faturalar WHERE id = %s", (rid,)):
            continue
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
