# -*- coding: utf-8 -*-
"""Gece yarısı QR hareketlerinden otomatik izin hesabı."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from db import execute, fetch_all, fetch_one

log = logging.getLogger(__name__)

OTOMATIK_ACIKLAMA = "Otomatik - QR"
MIN_IZIN_DK = 30
VARSAYILAN_BAS = "09:00"
VARSAYILAN_BIT = "18:30"


def _saat_to_dk(val) -> int | None:
    if val is None:
        return None
    if hasattr(val, "hour"):
        return int(val.hour) * 60 + int(val.minute)
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


def _dk_to_saat_decimal(dk: int) -> Decimal:
    return (Decimal(dk) / Decimal(60)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _personel_mesai_dk(personel: dict) -> tuple[int, int]:
    bas = _saat_to_dk(personel.get("mesai_baslangic")) or _saat_to_dk(VARSAYILAN_BAS)
    bit = _saat_to_dk(personel.get("mesai_bitis")) or _saat_to_dk(VARSAYILAN_BIT)
    return int(bas or 0), int(bit or 0)


def _gun_hareketlerini_al(personel_id: int, tarih: date) -> list[dict]:
    rows = fetch_all(
        """
        SELECT saat, tip
        FROM personel_hareketleri
        WHERE personel_id = %s AND tarih = %s
          AND tip IN ('giris', 'cikis')
        ORDER BY saat ASC, id ASC
        """,
        (int(personel_id), tarih),
    ) or []
    out = []
    for r in rows:
        dk = _saat_to_dk(r.get("saat"))
        tip = (r.get("tip") or "").strip().lower()
        if dk is None or tip not in ("giris", "cikis"):
            continue
        out.append({"dk": dk, "tip": tip})
    return out


def _manuel_izin_var_mi(personel_id: int, tarih: date) -> bool:
    row = fetch_one(
        """
        SELECT 1
        FROM personel_izin
        WHERE personel_id = %s
          AND baslangic_tarihi::date = %s::date
          AND COALESCE(aciklama, '') != %s
        LIMIT 1
        """,
        (int(personel_id), tarih, OTOMATIK_ACIKLAMA),
    )
    return row is not None


def personel_gun_izin_dk_hesapla(hareketler: list[dict], mesai_bitis_dk: int) -> int:
    """Gün içi dışarı + erken çıkış toplam dakikası."""
    i0 = next((i for i, e in enumerate(hareketler) if e["tip"] == "giris"), None)
    if i0 is None:
        return 0
    evs = hareketler[i0:]

    inside = True
    pending_cikis: int | None = None
    izin_disari_dk = 0

    for e in evs[1:]:
        if e["tip"] == "cikis":
            if inside:
                pending_cikis = e["dk"]
                inside = False
            else:
                pending_cikis = e["dk"]
        else:
            if not inside and pending_cikis is not None:
                izin_disari_dk += max(0, e["dk"] - pending_cikis)
                pending_cikis = None
            inside = True

    erken_cikis_dk = 0
    if pending_cikis is not None:
        cikis_kirp = min(pending_cikis, mesai_bitis_dk)
        erken_cikis_dk = max(0, mesai_bitis_dk - cikis_kirp)

    return int(izin_disari_dk + erken_cikis_dk)


def _otomatik_kayit_bul(personel_id: int, tarih: date):
    return fetch_one(
        """
        SELECT id, izin_turu, gun_sayisi, saat_sayisi
        FROM personel_izin
        WHERE personel_id = %s
          AND baslangic_tarihi::date = %s::date
          AND aciklama = %s
        LIMIT 1
        """,
        (int(personel_id), tarih, OTOMATIK_ACIKLAMA),
    )


def _otomatik_kayit_yaz(personel_id: int, tarih: date, payload: dict) -> None:
    mevcut = _otomatik_kayit_bul(personel_id, tarih)
    if mevcut:
        execute(
            """
            UPDATE personel_izin
            SET izin_turu=%s, bitis_tarihi=%s, gun_sayisi=%s,
                saat_sayisi=%s, onay_durumu='onaylandi'
            WHERE id=%s
            """,
            (
                payload["izin_turu"],
                tarih,
                payload["gun_sayisi"],
                payload["saat_sayisi"],
                mevcut["id"],
            ),
        )
    else:
        execute(
            """
            INSERT INTO personel_izin
              (personel_id, izin_turu, baslangic_tarihi, bitis_tarihi,
               gun_sayisi, saat_sayisi, aciklama, onay_durumu)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'onaylandi')
            """,
            (
                int(personel_id),
                payload["izin_turu"],
                tarih,
                tarih,
                payload["gun_sayisi"],
                payload["saat_sayisi"],
                OTOMATIK_ACIKLAMA,
            ),
        )


def _otomatik_kayit_sil(personel_id: int, tarih: date) -> None:
    execute(
        """
        DELETE FROM personel_izin
        WHERE personel_id = %s AND baslangic_tarihi::date = %s::date AND aciklama = %s
        """,
        (int(personel_id), tarih, OTOMATIK_ACIKLAMA),
    )


def personel_icin_gun_hesapla(personel: dict, tarih: date) -> dict[str, Any]:
    pid = int(personel["id"])
    _, mesai_bitis_dk = _personel_mesai_dk(personel)
    hareketler = _gun_hareketlerini_al(pid, tarih)

    if _manuel_izin_var_mi(pid, tarih):
        return {"yaz": False, "toplam_izin_dk": 0, "neden": "manuel_izin_var"}

    giris_var = any(e["tip"] == "giris" for e in hareketler)
    if not giris_var:
        return {
            "yaz": True,
            "izin_turu": "Yıllık Ücretli İzin",
            "gun_sayisi": 1,
            "saat_sayisi": 0,
            "toplam_izin_dk": 8 * 60,
            "neden": "giris_yok",
        }

    toplam_dk = personel_gun_izin_dk_hesapla(hareketler, mesai_bitis_dk)
    if toplam_dk <= MIN_IZIN_DK:
        return {"yaz": False, "toplam_izin_dk": toplam_dk, "neden": "esik_alti"}

    return {
        "yaz": True,
        "izin_turu": "Saatlik İzin",
        "gun_sayisi": 0,
        "saat_sayisi": float(_dk_to_saat_decimal(toplam_dk)),
        "toplam_izin_dk": toplam_dk,
        "neden": "saatlik",
    }


def gunden_otomatik_izin_hesapla(tarih: date | None = None) -> dict:
    """Verilen gün (varsayılan: dün) için tüm aktif personelleri işler."""
    if tarih is None:
        tarih = date.today() - timedelta(days=1)

    personeller = fetch_all(
        """
        SELECT id, ad_soyad, mesai_baslangic, mesai_bitis
        FROM personel
        WHERE is_active = TRUE
        ORDER BY id
        """
    ) or []

    ozet = {
        "tarih": tarih.isoformat(),
        "islenen": 0,
        "yazilan": 0,
        "silinen": 0,
        "atlanan": 0,
        "detay": [],
    }

    for p in personeller:
        ozet["islenen"] += 1
        try:
            sonuc = personel_icin_gun_hesapla(p, tarih)
            pid = int(p["id"])
            neden = sonuc.get("neden")

            if neden == "manuel_izin_var":
                ozet["atlanan"] += 1
                ozet["detay"].append({"personel_id": pid, **sonuc})
                continue

            if sonuc.get("yaz"):
                _otomatik_kayit_yaz(pid, tarih, sonuc)
                ozet["yazilan"] += 1
            else:
                _otomatik_kayit_sil(pid, tarih)
                if neden == "esik_alti":
                    ozet["silinen"] += 1

            ozet["detay"].append({"personel_id": pid, **sonuc})
        except Exception as exc:
            log.exception("Otomatik izin hatası personel_id=%s tarih=%s", p.get("id"), tarih)
            ozet["detay"].append({"personel_id": p.get("id"), "hata": str(exc)})

    log.info("Otomatik izin tamamlandı: %s", ozet)
    return ozet


def run_gece_otomatik_izin_job() -> None:
    """APScheduler wrapper — dün'ü işler."""
    dun = date.today() - timedelta(days=1)
    gunden_otomatik_izin_hesapla(dun)
