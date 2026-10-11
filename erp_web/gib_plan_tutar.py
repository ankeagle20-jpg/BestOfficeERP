# -*- coding: utf-8 -*-
"""GİB taslak tutarı: planlı K3 AYLIK satırında plan zinciri (yazmaz)."""
from __future__ import annotations

import logging
import re
from datetime import date

from sozlesme_plan import _kart_oranlari, _tarih, ay_bazli_tutarlar

_ISARET_AYLIK = re.compile(r"\|AYLIK_TUTAR\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|")
_LOG = logging.getLogger(__name__)


def kart_disi_neden(kyc) -> str | None:
    """nakit / hibrit / kdv — bu sınıflarda GİB plan override yok."""
    if not isinstance(kyc, dict):
        return None
    if bool(kyc.get("kira_nakit")):
        return "nakit"
    raw = kyc.get("kdv_oran")
    if raw not in (None, ""):
        try:
            kdv = float(raw)
        except (TypeError, ValueError):
            kdv = 20.0
        if abs(kdv - 20.0) > 0.001:
            return "kdv"
    try:
        net = float(kyc.get("aylik_kira") or 0)
    except (TypeError, ValueError):
        net = 0.0
    karma, _r = _kart_oranlari(net, kyc.get("kira_nakit_tutar"), kyc.get("kira_banka_tutar"))
    if karma:
        return "hibrit"
    return None


def aylik_tutar_ayi(notlar) -> str | None:
    """Tek |AYLIK_TUTAR| ayı. AUTO_INV veya işaret yok/çoksa None."""
    s = str(notlar or "")
    if "|AUTO_INV|" in s:
        return None
    bulundu = _ISARET_AYLIK.findall(s)
    if len(bulundu) != 1:
        return None
    return bulundu[0][:7]


def en_erken_plan_ayi(planlar) -> str | None:
    en = None
    for ham in planlar or []:
        if not isinstance(ham, dict) or ham.get("iptal_at"):
            continue
        d = _tarih(ham.get("gecerlilik_ay"))
        if d is None:
            continue
        key = f"{d.year:04d}-{d.month:02d}"
        if en is None or key < en:
            en = key
    return en


def satirlar_json_bos(raw) -> bool:
    if raw is None:
        return True
    s = str(raw).strip()
    if not s or s in ("[]", "null", "None"):
        return True
    try:
        import json

        v = json.loads(s) if s[:1] in "[{" else raw
        return not v
    except Exception:
        return False


def cek_gonderilen_plan_tutar(fatura, ov, execute_func, log=None) -> dict:
    """Kapı geçmiş override ile WHERE id=? AND toplam=? günceller. notlar'a yazmaz."""
    lg = log or _LOG
    out = {"yazildi": False, "neden": "", "eski_toplam": None, "yeni_toplam": None, "ay": None}
    if not isinstance(ov, dict):
        out["neden"] = "kapi_disi"
        return out
    if not satirlar_json_bos((fatura or {}).get("satirlar_json")):
        out["neden"] = "satirlar_dolu"
        return out
    try:
        fid = int((fatura or {}).get("id") or 0)
        eski = round(float((fatura or {}).get("toplam") or 0), 2)
        yeni = round(float(ov.get("toplam") or 0), 2)
        net = round(float(ov.get("birim_fiyat") or 0), 2)
        kdv = ov.get("kdv")
        kdv = round(float(kdv), 2) if kdv is not None else round(yeni - net, 2)
    except (TypeError, ValueError):
        out["neden"] = "satir_okunamadi"
        return out
    out["eski_toplam"] = eski
    out["yeni_toplam"] = yeni
    out["ay"] = ov.get("ay")
    if fid <= 0 or yeni <= 0 or net <= 0:
        out["neden"] = "satir_okunamadi"
        return out
    if abs(net + kdv - yeni) > 0.02:
        out["neden"] = "kdv_uyumsuz"
        return out
    if abs(eski - yeni) < 0.02:
        out["neden"] = "idempotent"
        lg.info(
            "gib plan tutar zaten uyumlu fatura_id=%s ay=%s eski_toplam=%s yeni_toplam=%s",
            fid,
            ov.get("ay"),
            eski,
            yeni,
        )
        return out
    n = execute_func(
        "UPDATE faturalar SET tutar = %s, kdv_tutar = %s, toplam = %s WHERE id = %s AND toplam = %s",
        (net, kdv, yeni, fid, eski),
    )
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        out["yazildi"] = True
        out["neden"] = "yazildi"
        lg.info(
            "gib plan tutar cekildi fatura_id=%s ay=%s eski_toplam=%s yeni_toplam=%s",
            fid,
            ov.get("ay"),
            eski,
            yeni,
        )
    else:
        out["neden"] = "where_tutmadi"
        lg.info(
            "gib plan tutar where tutmadi fatura_id=%s ay=%s eski_toplam=%s yeni_toplam=%s",
            fid,
            ov.get("ay"),
            eski,
            yeni,
        )
    return out


def uygula_gib_basari_tutar(
    fatura,
    kyc,
    *,
    ay_sinif,
    plan_satir,
    planlar,
    execute_func,
    log=None,
) -> dict:
    """GİB başarı sonrası: kapı içiyse tutar/kdv/toplam çek. Yazma yalnız WHERE id+toplam."""
    lg = log or _LOG
    if not satirlar_json_bos((fatura or {}).get("satirlar_json")):
        return {"yazildi": False, "neden": "satirlar_dolu", "eski_toplam": None, "yeni_toplam": None, "ay": None}
    ov = plan_gib_override(
        fatura, kyc, ay_sinif=ay_sinif, plan_satir=plan_satir, planlar=planlar, log=lg
    )
    if not ov:
        return {"yazildi": False, "neden": "kapi_disi", "eski_toplam": None, "yeni_toplam": None, "ay": None}
    return cek_gonderilen_plan_tutar(fatura, ov, execute_func, log=lg)


def plan_gib_override(fatura, kyc, *, ay_sinif, plan_satir, planlar, log=None) -> dict | None:
    """K3 + aktif plan + AYLIK ise {birim_fiyat, kdv_orani, toplam, kdv, ay}. Aksi None."""
    lg = log or _LOG
    neden = kart_disi_neden(kyc)
    if neden:
        lg.warning("gib plan tutar atlandi: %s", neden)
        return None
    ay = aylik_tutar_ayi((fatura or {}).get("notlar"))
    if not ay:
        return None
    if str(ay_sinif or "") != "K3":
        return None
    if not planlar:
        return None
    gecer = en_erken_plan_ayi(planlar)
    if not gecer or ay < gecer:
        return None
    if not isinstance(plan_satir, dict):
        return None
    if plan_satir.get("kaynak") not in ("plan", "plan_tufe"):
        return None
    try:
        net = round(float(plan_satir.get("net") or 0), 2)
        brut = round(float(plan_satir.get("brut") or 0), 2)
        kdv = plan_satir.get("kdv")
        kdv = round(float(kdv), 2) if kdv is not None else round(brut - net, 2)
    except (TypeError, ValueError):
        return None
    if net <= 0 or brut <= 0:
        return None
    if abs(net + kdv - brut) > 0.02:
        return None
    return {
        "birim_fiyat": net,
        "kdv_orani": 20,
        "toplam": brut,
        "kdv": kdv,
        "ay": ay,
    }


def yukle_plan_gib_satir(fatura, kyc_kismi=None):
    """Canlı okuma: önizleme ile aynı plan/kilit kaynakları. Yazmaz. Hata → None."""
    try:
        mid = int((fatura or {}).get("musteri_id") or 0)
    except (TypeError, ValueError):
        return None
    if mid <= 0:
        return None
    try:
        from routes.giris_routes import (
            _ekstre_plan_brut_haritasi,
            _musteri_kyc_grup_for_aylik_grid,
            _plan_fatura_siniflari_oku,
            _plan_paket_yukle,
            _plan_zincir,
            _planli_reel_haritasi,
            _tufe_map_by_year_month_cached,
        )
    except Exception:
        _LOG.exception("gib plan tutar import")
        return None
    try:
        kyc = _musteri_kyc_grup_for_aylik_grid(mid) or dict(kyc_kismi or {})
        paket = _plan_paket_yukle([mid]).get(mid) or {}
        planlar = paket.get("planlar") or []
        if not planlar:
            return None
        ay = aylik_tutar_ayi((fatura or {}).get("notlar"))
        if not ay:
            return None
        sin = (_plan_fatura_siniflari_oku([mid]) or {}).get(mid) or {}
        ay_sinif = (sin.get(ay) or {}).get("sinif")
        tufe = _tufe_map_by_year_month_cached()
        reel = (_planli_reel_haritasi([mid]) or {}).get(mid) or {}
        y, m = int(ay[:4]), int(ay[5:7])
        bitis = date(y, m, 1)
        _ekstre_plan_brut_haritasi(mid, kyc, tufe, reel, bitis)
        bas = None
        try:
            from routes.giris_routes import _aylik_grid_coerce_date

            bas = _aylik_grid_coerce_date((kyc or {}).get("sozlesme_tarihi"))
        except Exception:
            bas = None
        if bas is None:
            return None
        ay_sayisi = (y - bas.year) * 12 + (m - bas.month) + 1
        if ay_sayisi < 1:
            return None
        zincir = _plan_zincir(
            kyc, tufe, ay_sayisi, reel, paket.get("faturali"), paket.get("belge")
        )
        if not zincir:
            return None
        satirlar = ay_bazli_tutarlar(zincir, planlar)
        plan_satir = None
        for row in satirlar:
            if row.get("ay") == ay:
                plan_satir = row
                break
        return plan_gib_override(
            fatura, kyc, ay_sinif=ay_sinif, plan_satir=plan_satir, planlar=planlar
        )
    except Exception:
        _LOG.exception("gib plan tutar okuma")
        return None
