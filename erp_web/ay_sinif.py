"""Ekstre, önbellek ve panel tutarlılığı. Sabit hesap listesi yok.

A: ekstre, önbellek ve panel uyumlu; parçalı tahsilat borca sığıyor.
B: önbellek veya panel, ekstre borcundan farklı.
C: parçalı tahsilat borca sığmıyor veya ayın 1'i borç satırı yok.
"""
from __future__ import annotations

from collections import defaultdict

from ay_fifo import TOL, dagit_seri

UYARI_METNI = (
    "Bu hesapların aylık tutarı ekstre borcuyla uyuşmuyor veya "
    "tahsilat borç satırına sığmıyor. Gecikme tutarı bu gönderimde kullanılmasın."
)


def _yakin(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= TOL
    except (TypeError, ValueError):
        return False


def sinif_hesapla(borc, cache, panel, tahsilatlar) -> str:
    """borc/cache/panel: ay başı → tutar. tahsilatlar: id, tarih, tutar, payli."""
    borc_map = {str(k): round(float(v), 2) for k, v in (borc or {}).items() if float(v or 0) > 0}
    if not borc_map:
        return "C"
    seri = []
    payli = []
    for i, row in enumerate(tahsilatlar or []):
        try:
            tutar = round(float(row.get("tutar") or 0), 2)
            tarih = str(row.get("tarih") or "")[:10]
            rid = int(row.get("id") if row.get("id") is not None else i)
        except (TypeError, ValueError):
            continue
        if tutar <= 0 or len(tarih) != 10:
            continue
        item = {"id": rid, "tarih": tarih, "tutar": tutar}
        seri.append(item)
        if row.get("payli"):
            payli.append(item)
    if payli:
        parcalar = dagit_seri(borc_map, seri)
        by_id = {item["id"]: parca for item, parca in zip(seri, parcalar)}
        for item in payli:
            yerlesen = round(sum(t for _iso, t in (by_id.get(item["id"]) or [])), 2)
            if yerlesen + TOL < item["tutar"]:
                return "C"
    for iso, inv in borc_map.items():
        if iso in (cache or {}) and not _yakin(cache[iso], inv):
            return "B"
        if iso in (panel or {}) and not _yakin(panel[iso], inv):
            return "B"
    return "A"


def gonderime_izin(sinif: str, onay: bool, kilitli: bool) -> tuple[bool, str]:
    """Toplu gecikme gönderimi. Tekil WhatsApp ve ödeme linki bu kapıdan geçmez."""
    if kilitli:
        return False, "kilit"
    if sinif in ("B", "C") and not onay:
        return False, "uyari"
    return True, ""


def _ay_basi_iso(yil, ay) -> str | None:
    try:
        yi, ai = int(yil), int(ay)
    except (TypeError, ValueError):
        return None
    if ai < 1 or ai > 12:
        return None
    return f"{yi:04d}-{ai:02d}-01"


def _cache_brut(payload) -> dict[str, float]:
    if isinstance(payload, str):
        import json
        try:
            payload = json.loads(payload)
        except Exception:
            return {}
    aylar = payload if isinstance(payload, list) else (payload or {}).get("aylar") if isinstance(payload, dict) else []
    out = {}
    if not isinstance(aylar, list):
        return out
    for a in aylar:
        if not isinstance(a, dict):
            continue
        iso = _ay_basi_iso(a.get("yil"), a.get("ay"))
        if not iso:
            continue
        try:
            out[iso] = round(float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0), 2)
        except (TypeError, ValueError):
            continue
    return out


def _panel_aylik(raw) -> dict[str, float]:
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for iso, prow in raw.items():
        if not isinstance(prow, dict) or len(str(iso)) < 10:
            continue
        ay = str(iso)[:8] + "01"
        try:
            out[ay] = round(float(prow.get("aylik") or 0), 2)
        except (TypeError, ValueError):
            continue
    return out


def siniflari_yukle(mids) -> dict[int, str]:
    """Listede görünen hesaplar için dört okuma. Yazmaz."""
    from db import fetch_all

    temiz = []
    for mid in mids or []:
        try:
            i = int(mid)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in temiz:
            temiz.append(i)
    if not temiz:
        return {}
    borc = defaultdict(dict)
    for r in fetch_all(
        """
        SELECT musteri_id AS mid,
               LEFT(fatura_tarihi::text, 8) || '01' AS iso,
               ROUND(SUM(COALESCE(toplam, tutar, 0))::numeric, 2) AS tutar
        FROM faturalar
        WHERE musteri_id = ANY(%s)
          AND SUBSTRING(fatura_tarihi::text, 9, 2) = '01'
          AND COALESCE(toplam, tutar, 0) > 0
          AND COALESCE(notlar, '') NOT LIKE %s
        GROUP BY musteri_id, LEFT(fatura_tarihi::text, 8)
        """,
        (temiz, "%|GIB_NO_TASINDI|%"),
    ) or []:
        if r.get("mid") is None:
            continue
        borc[int(r["mid"])][str(r["iso"])] = float(r["tutar"])
    cache = {}
    for r in fetch_all(
        "SELECT musteri_id AS mid, payload FROM musteri_aylik_grid_cache WHERE musteri_id = ANY(%s)",
        (temiz,),
    ) or []:
        if r.get("mid") is None:
            continue
        cache[int(r["mid"])] = _cache_brut(r.get("payload"))
    panel = {}
    for r in fetch_all(
        "SELECT musteri_id AS mid, by_iso FROM musteri_tahsilat_panel_detay WHERE musteri_id = ANY(%s)",
        (temiz,),
    ) or []:
        if r.get("mid") is None:
            continue
        panel[int(r["mid"])] = _panel_aylik(r.get("by_iso"))
    tah = defaultdict(list)
    for r in fetch_all(
        """
        SELECT id,
               COALESCE(musteri_id, customer_id) AS mid,
               LEFT(tahsilat_tarihi::text, 10) AS tarih,
               ROUND(COALESCE(tutar, 0)::numeric, 2) AS tutar,
               (COALESCE(aciklama, '') LIKE %s) AS payli
        FROM tahsilatlar
        WHERE COALESCE(musteri_id, customer_id) = ANY(%s)
          AND COALESCE(tutar, 0) > 0
        """,
        ("%|AYLIK_PAY|%", temiz),
    ) or []:
        if r.get("mid") is None:
            continue
        tah[int(r["mid"])].append(r)
    return {
        mid: sinif_hesapla(borc.get(mid) or {}, cache.get(mid) or {}, panel.get(mid) or {}, tah.get(mid) or [])
        for mid in temiz
    }


def kilitli_musteriler(mids) -> set[int]:
    """Aşama 3 yazması sürerken dolu olan kilit. Tablo yoksa boş küme."""
    from db import fetch_all

    temiz = []
    for mid in mids or []:
        try:
            i = int(mid)
        except (TypeError, ValueError):
            continue
        if i > 0:
            temiz.append(i)
    if not temiz:
        return set()
    try:
        var = fetch_all("SELECT to_regclass('ay_dagitim_kilit') AS ad")
    except Exception:
        return set()
    if not var or not (var[0] or {}).get("ad"):
        return set()
    rows = fetch_all(
        """
        SELECT musteri_id AS mid
        FROM ay_dagitim_kilit
        WHERE musteri_id = ANY(%s)
          AND until_at > NOW()
        """,
        (temiz,),
    ) or []
    return {int(r["mid"]) for r in rows if r.get("mid") is not None}
