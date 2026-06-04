# -*- coding: utf-8 -*-
"""
GİB durumunu HTML filigran kontrolüyle yeniden hesaplayıp ERP notlarını günceller.

Kullanım (proje kökünde):
    python erp_web/scripts/gib_durum_yeniden_hesapla.py --bas 2026-04-01 --bit 2026-04-30
    python erp_web/scripts/gib_durum_yeniden_hesapla.py --bas 2026-04-01 --bit 2026-04-30 --uygula

Mantık:
    GİB e-Arşiv fatura HTML'inde
        • «İPTAL EDİLMİŞTİR» filigranı varsa  → İPTAL
        • «İMZASIZ» filigranı varsa            → TASLAK
        • Hiçbir filigran yoksa (geçerli HTML) → İMZALI

Varsayılan: «kuru» (DRY-RUN) — sadece hangi kayıtların düzeleceğini gösterir.
--uygula geçerken `_fatura_gib_bilgilerini_yaz` çağrılarak notlar güncellenir
ve GİB HTML'i ilgili faturanın önbelleğine yazılır.
"""

import argparse
import os
import sys
import time
from datetime import datetime

_erp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _erp not in sys.path:
    sys.path.insert(0, _erp)
os.chdir(_erp)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from db import fetch_all  # noqa: E402

from gib_earsiv import BestOfficeGIBManager, gib_fatura_html_watermark_etiket  # noqa: E402
from routes.faturalar_routes import (  # noqa: E402
    _fatura_gib_bilgilerini_yaz,
    _gib_portal_html_indir_ve_kaydet,
    _gib_portal_html_cache_oku,
)


_ETIKET_TO_ASAMA = {"İptal": "iptal", "İmzasız": "taslak", "İmzalı": "imzali"}


def _mevcut_asama(notlar: str) -> str:
    n = (notlar or "")
    if "GİB İMZALANDI" in n:
        return "imzali"
    nn = n.replace("İ", "I").replace("ı", "i")
    if "GIB durum: iptal" in nn.replace("  ", " "):
        return "iptal"
    if "GIB durum: taslak" in nn.replace("  ", " "):
        return "taslak"
    return "bilinmiyor"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bas", required=True, help="Başlangıç (YYYY-MM-DD)")
    ap.add_argument("--bit", required=True, help="Bitiş (YYYY-MM-DD)")
    ap.add_argument("--uygula", action="store_true", help="DB'ye yaz (default: kuru)")
    ap.add_argument("--gecikme-ms", type=int, default=120, help="Her HTML çekimi arasında bekleme (ms)")
    a = ap.parse_args()
    try:
        bas = datetime.strptime(a.bas[:10], "%Y-%m-%d").date()
        bit = datetime.strptime(a.bit[:10], "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit("Tarih biçimi YYYY-MM-DD olmalı.")
    if bas > bit:
        bas, bit = bit, bas

    gib = BestOfficeGIBManager()
    if not gib.is_available():
        raise SystemExit("GİB modülü kullanılamıyor (login env / istemci eksik).")

    rows = fetch_all(
        """
        SELECT id, fatura_no, ettn, fatura_tarihi, notlar
        FROM faturalar
        WHERE (fatura_tarihi::date) >= %s
          AND (fatura_tarihi::date) <= %s
          AND BTRIM(COALESCE(ettn::text, '')) <> ''
        ORDER BY fatura_tarihi DESC, id DESC
        """,
        (bas, bit),
    )

    sayim = {"imzali": 0, "taslak": 0, "iptal": 0, "bilinmiyor": 0, "guncellenen": 0}
    for r in rows or []:
        ettn = (r.get("ettn") or "").strip()
        if not ettn:
            continue
        # Önce önbellekten dene; yoksa canlı çek.
        html = _gib_portal_html_cache_oku(int(r["id"])) or ""
        if not html or len(html) < 200:
            try:
                html = gib.fatura_html_getir(ettn, days_back=370) or ""
            except Exception as ex:
                print(f"[hata] id={r['id']} ettn={ettn[:8]}…  HTML alınamadı: {ex}")
                continue
            if a.gecikme_ms > 0:
                time.sleep(a.gecikme_ms / 1000.0)
        wm = gib_fatura_html_watermark_etiket(html or "")
        if not wm:
            sayim["bilinmiyor"] += 1
            print(
                f"[atla ] id={r['id']} no={r['fatura_no']} ettn={ettn[:8]}…  durum tespit edilemedi"
            )
            continue
        yeni_asama = _ETIKET_TO_ASAMA.get(wm, "bilinmiyor")
        sayim[yeni_asama] = sayim.get(yeni_asama, 0) + 1
        eski_asama = _mevcut_asama(r.get("notlar"))
        if eski_asama == yeni_asama:
            continue
        print(
            f"[düzelt] id={r['id']} no={r['fatura_no']} ettn={ettn[:8]}… "
            f"{eski_asama} → {yeni_asama}"
        )
        if a.uygula:
            _fatura_gib_bilgilerini_yaz(
                r["id"], r.get("ettn"), r.get("fatura_no"), gib_asama=yeni_asama
            )
            try:
                _gib_portal_html_indir_ve_kaydet(int(r["id"]), ettn, gib)
            except Exception:
                pass
            sayim["guncellenen"] += 1

    print()
    print("Özet:", sayim)
    if not a.uygula:
        print("Kuru çalıştırma. DB'ye yazmak için --uygula ekleyin.")


if __name__ == "__main__":
    main()
