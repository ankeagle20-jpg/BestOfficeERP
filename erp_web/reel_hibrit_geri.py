# -*- coding: utf-8 -*-
"""Reel yıl hibrit dağılımını geri yazar. Tahsilat ve kasa tutarına dokunmaz."""
import json
from datetime import datetime
from pathlib import Path

YEDEK_KOK = Path(r"C:\Users\MSI\Desktop\Best ERP")


def _plan(mid, yil, toplam, net, banka):
    from routes.giris_routes import (
        _musteri_kyc_grup_for_aylik_grid,
        _reel_donem_satir_oku,
        _reel_para,
        _reel_para_ayni,
        _reel_satir_ani,
        _reel_hibrit_formul_makul,
        reel_hibrit_kdv_dahil_tutar,
    )

    eski = _reel_donem_satir_oku(int(mid), int(yil))
    if not eski:
        return {"ok": False, "neden": "satir_yok"}
    kyc = _musteri_kyc_grup_for_aylik_grid(int(mid)) or {}
    try:
        kdv = float(kyc.get("kdv_oran") if kyc.get("kdv_oran") is not None else 20)
    except (TypeError, ValueError):
        kdv = 20.0
    net_p = _reel_para(net)
    banka_p = _reel_para(banka)
    toplam_p = _reel_para(toplam)
    kayitli = _reel_para(eski.get("tutar_kdv_dahil"))
    if net_p is None or banka_p is None or toplam_p is None or net_p <= 0 or banka_p <= 0:
        return {"ok": False, "neden": "tutar_uyusmuyor", "tutar_kdv_dahil": kayitli}
    if not _reel_para_ayni(toplam_p, round(net_p + banka_p, 2)):
        return {
            "ok": False,
            "neden": "tutar_uyusmuyor",
            "tutar_kdv_dahil": kayitli,
            "beklenen_toplam": round(net_p + banka_p, 2),
        }
    beklenen = reel_hibrit_kdv_dahil_tutar(net_p, banka_p, kdv)
    if not _reel_hibrit_formul_makul(beklenen):
        return {
            "ok": False,
            "neden": "tutar_uyusmuyor",
            "eski_tutar_kdv_dahil": kayitli,
            "yeni_tutar_kdv_dahil": beklenen,
        }
    fark = round(beklenen - (kayitli or 0), 2)
    return {
        "ok": True,
        "musteri_id": int(mid),
        "donem_yil": int(yil),
        "eski_tutar_kdv_dahil": kayitli,
        "yeni_tutar_kdv_dahil": beklenen,
        "aylik_borc_farki": fark,
        "tutar_kdv_dahil": beklenen,
        "hibrit_toplam": toplam_p,
        "hibrit_net": net_p,
        "hibrit_banka": banka_p,
        "kdv_oran": kdv,
        "eski": _reel_satir_ani(eski, mid, yil),
    }


def reel_hibrit_geri_yaz(mid, yil, toplam, net, banka, *, apply=False):
    """dry-run varsayılan. apply hibrit kolonlarını ve formüldeki tutar_kdv_dahil değerini yazar. Kasa eşitlemez."""
    plan = _plan(mid, yil, toplam, net, banka)
    plan["dry_run"] = not apply
    if not plan.get("ok") or not apply:
        return plan
    YEDEK_KOK.mkdir(parents=True, exist_ok=True)
    damga = datetime.now().strftime("%Y%m%d%H%M%S")
    yedek = YEDEK_KOK / f"yedek_reel_hibrit_{int(mid)}_{int(yil)}_{damga}.json"
    yedek.write_text(
        json.dumps(plan["eski"], ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    from routes.giris_routes import reel_hibrit_alanlari_yaz

    sonuc = reel_hibrit_alanlari_yaz(
        int(mid),
        int(yil),
        plan["hibrit_toplam"],
        plan["hibrit_net"],
        plan["hibrit_banka"],
        kdv_oran=plan["kdv_oran"],
    )
    sonuc["yedek"] = str(yedek)
    sonuc["dry_run"] = False
    return sonuc
