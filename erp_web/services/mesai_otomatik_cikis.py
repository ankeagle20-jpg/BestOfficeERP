# -*- coding: utf-8 -*-
"""Mesai bitişinde otomatik GERÇEK çıkış kaydı (AŞAMA 3).

Ürün kararı (AŞAMA 0):
  Otomatik çıkış SADECE açık giriş VARSA yazılır; hiç giriş YOKSA çıkış YAZILMAZ.

Flag: MESAI_OTOMATIK_CIKIS_ENABLED (varsayılan KAPALI).
Kaynak etiketi: otomatik_mesai (idempotency + gece izin hesabı ile ayırt).

Bu modül personel_izin'e YAZMAZ — izin muhasebesi gece izin_otomatik'te kalır.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, time
from typing import Any

from db import execute, fetch_all, fetch_one

log = logging.getLogger(__name__)

KAYNAK = "otomatik_mesai"
VARSAYILAN_BIT = "18:30"
VARSAYILAN_BIT_DK = 18 * 60 + 30


def mesai_otomatik_cikis_enabled() -> bool:
    """Ortam: MESAI_OTOMATIK_CIKIS_ENABLED — varsayılan False."""
    v = (os.getenv("MESAI_OTOMATIK_CIKIS_ENABLED") or "").strip().lower()
    return v in ("1", "true", "evet", "yes", "on")


def _saat_to_dk(val) -> int | None:
    if val is None:
        return None
    if hasattr(val, "hour"):
        try:
            return int(val.hour) * 60 + int(val.minute)
        except Exception:
            return None
    s = str(val).strip()
    if not s:
        return None
    p = s.split(":")
    if len(p) < 2:
        return None
    try:
        return int(p[0]) * 60 + int(p[1])
    except ValueError:
        return None


def _dk_to_saat_str(dk: int) -> str:
    dk = max(0, min(24 * 60 - 1, int(dk)))
    h, m = divmod(dk, 60)
    return f"{h:02d}:{m:02d}:00"


def _turkey_now(now: datetime | None = None) -> datetime:
    if now is not None:
        return now
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Istanbul"))
    except Exception:
        return datetime.now()


def _personel_mesai_bitis_dk(personel: dict) -> int:
    dk = _saat_to_dk(personel.get("mesai_bitis"))
    return int(dk) if dk is not None else VARSAYILAN_BIT_DK


def _gun_hareketlerini_al(personel_id: int, tarih: date) -> list[dict]:
    rows = fetch_all(
        """
        SELECT id, saat, tip, kaynak
        FROM personel_hareketleri
        WHERE personel_id = %s AND tarih = %s
          AND tip IN ('giris', 'cikis')
        ORDER BY saat ASC, id ASC
        """,
        (int(personel_id), tarih),
    ) or []
    out = []
    for r in rows:
        tip = (r.get("tip") or "").strip().lower()
        if tip not in ("giris", "cikis"):
            continue
        dk = _saat_to_dk(r.get("saat"))
        if dk is None:
            continue
        out.append(
            {
                "id": r.get("id"),
                "dk": dk,
                "tip": tip,
                "kaynak": (r.get("kaynak") or "").strip(),
                "saat": r.get("saat"),
            }
        )
    return out


def _otomatik_cikis_zaten_var(personel_id: int, tarih: date) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS x
        FROM personel_hareketleri
        WHERE personel_id = %s AND tarih = %s
          AND tip = 'cikis' AND COALESCE(kaynak, '') = %s
        LIMIT 1
        """,
        (int(personel_id), tarih, KAYNAK),
    )
    return row is not None


def _devam_kaydi(personel_id: int, tarih: date) -> dict | None:
    return fetch_one(
        """
        SELECT id, durum, giris_saati, cikis_saati, ad_soyad
        FROM devam_kayitlari
        WHERE personel_id = %s AND tarih = %s
        """,
        (int(personel_id), tarih),
    )


def karar_otomatik_cikis(
    *,
    hareketler: list[dict],
    devam: dict | None,
    now_dk: int,
    mesai_bitis_dk: int,
    otomatik_cikis_var: bool,
) -> dict[str, Any]:
    """Saf karar (DB yazmaz). Test edilebilir.

    Returns: {yaz: bool, neden: str, cikis_dk?: int}
    """
    if otomatik_cikis_var:
        return {"yaz": False, "neden": "idempotent_otomatik_cikis_var"}

    if now_dk < int(mesai_bitis_dk):
        return {"yaz": False, "neden": "mesai_bitmedi"}

    if not hareketler:
        # Ürün kararı: hiç giriş yoksa çıkış yazılmaz
        return {"yaz": False, "neden": "hareket_yok"}

    giris_var = any(e.get("tip") == "giris" for e in hareketler)
    if not giris_var:
        return {"yaz": False, "neden": "giris_yok"}

    if devam and (devam.get("durum") or "").strip().lower() == "cikis":
        return {"yaz": False, "neden": "devam_zaten_cikis"}

    last = hareketler[-1]
    if last.get("tip") != "giris":
        return {"yaz": False, "neden": "son_hareket_acik_giris_degil"}

    return {
        "yaz": True,
        "neden": "acik_giris_mesai_bitti",
        "cikis_dk": int(mesai_bitis_dk),
    }


def _yaz_otomatik_cikis(
    personel: dict,
    tarih: date,
    cikis_dk: int,
    devam: dict | None,
) -> None:
    pid = int(personel["id"])
    ad = (personel.get("ad_soyad") or (devam or {}).get("ad_soyad") or "").strip() or None
    cikis_str = _dk_to_saat_str(cikis_dk)

    execute(
        """
        INSERT INTO personel_hareketleri (personel_id, tarih, saat, tip, kaynak)
        VALUES (%s, %s, %s, 'cikis', %s)
        """,
        (pid, tarih, cikis_str, KAYNAK),
    )

    if devam:
        execute(
            """
            UPDATE devam_kayitlari
            SET cikis_saati = %s, durum = 'cikis'
            WHERE personel_id = %s AND tarih = %s
            """,
            (cikis_str, pid, tarih),
        )
    else:
        # Devam satırı yoksa tutarlılık için oluştur (giriş saati: ilk giris hareketi)
        hareketler = _gun_hareketlerini_al(pid, tarih)
        giris_str = None
        for e in hareketler:
            if e.get("tip") == "giris":
                giris_str = _dk_to_saat_str(int(e["dk"]))
                break
        if giris_str is None:
            giris_str = cikis_str
        execute(
            """
            INSERT INTO devam_kayitlari
                (personel_id, ad_soyad, tarih, giris_saati, cikis_saati, durum, gec_dakika, kaynak)
            VALUES (%s, %s, %s, %s, %s, 'cikis', 0, %s)
            ON CONFLICT (personel_id, tarih) DO UPDATE
              SET cikis_saati = EXCLUDED.cikis_saati, durum = 'cikis'
            """,
            (pid, ad, tarih, giris_str, cikis_str, KAYNAK),
        )


def personel_icin_tick(personel: dict, now: datetime | None = None) -> dict[str, Any]:
    """Tek personel için bir tick. Flag kontrolü çağıranda."""
    pid = int(personel["id"])
    simdi = _turkey_now(now)
    bugun = simdi.date()
    now_dk = simdi.hour * 60 + simdi.minute
    bit_dk = _personel_mesai_bitis_dk(personel)

    otomatik_var = _otomatik_cikis_zaten_var(pid, bugun)
    hareketler = _gun_hareketlerini_al(pid, bugun)
    devam = _devam_kaydi(pid, bugun)

    karar = karar_otomatik_cikis(
        hareketler=hareketler,
        devam=devam,
        now_dk=now_dk,
        mesai_bitis_dk=bit_dk,
        otomatik_cikis_var=otomatik_var,
    )
    out = {
        "personel_id": pid,
        "tarih": bugun.isoformat(),
        "now_dk": now_dk,
        "mesai_bitis_dk": bit_dk,
        **karar,
    }
    if not karar.get("yaz"):
        return out

    try:
        _yaz_otomatik_cikis(personel, bugun, int(karar["cikis_dk"]), devam)
        out["yazildi"] = True
    except Exception as exc:
        log.exception("mesai otomatik çıkış yazım hatası pid=%s", pid)
        out["yaz"] = False
        out["yazildi"] = False
        out["hata"] = str(exc)
        out["neden"] = "yazim_hatasi"
    return out


def run_mesai_otomatik_cikis_tick(now: datetime | None = None) -> dict[str, Any]:
    """Tüm aktif personeller — flag kapalıysa no-op."""
    ozet: dict[str, Any] = {
        "enabled": mesai_otomatik_cikis_enabled(),
        "islenen": 0,
        "yazilan": 0,
        "atlanan": 0,
        "detay": [],
    }
    if not ozet["enabled"]:
        ozet["neden"] = "flag_kapali"
        return ozet

    personeller = fetch_all(
        """
        SELECT id, ad_soyad, mesai_baslangic, mesai_bitis
        FROM personel
        WHERE is_active = TRUE
        ORDER BY id
        """
    ) or []

    for p in personeller:
        ozet["islenen"] += 1
        try:
            sonuc = personel_icin_tick(p, now=now)
            if sonuc.get("yaz") and sonuc.get("yazildi"):
                ozet["yazilan"] += 1
            else:
                ozet["atlanan"] += 1
            ozet["detay"].append(sonuc)
        except Exception as exc:
            log.exception("mesai otomatik çıkış tick hatası pid=%s", p.get("id"))
            ozet["atlanan"] += 1
            ozet["detay"].append({"personel_id": p.get("id"), "hata": str(exc)})

    if ozet["yazilan"]:
        log.info("Mesai otomatik çıkış: yazilan=%s islenen=%s", ozet["yazilan"], ozet["islenen"])
    return ozet


def run_mesai_otomatik_cikis_job() -> None:
    """APScheduler wrapper."""
    try:
        run_mesai_otomatik_cikis_tick()
    except Exception:
        log.exception("mesai_otomatik_cikis job hata")
