# -*- coding: utf-8 -*-
"""Sözleşme plan değişikliği — ay bazında tutar (saf hesap).

Veritabanı, cache ve mevcut grid fonksiyonları çağrılmaz.
Plansız sonuç, sözleşmeler aylık grid çekirdeği ile aynı blokları izler:
sözleşme ayından 12'şer ay, TÜFE bir sonraki bloğa girerken uygulanır,
reel örtmesi artış yıldönümünün 12 ayıdır ve ilk sözleşme yılı atlanır.
"""
from __future__ import annotations

import calendar
import math
from datetime import date, datetime


def _tarih(val) -> date | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()[:10]
    if len(s) == 7 and s[4] == "-":
        y, m = s.split("-")
        return date(int(y), int(m), 1)
    return date.fromisoformat(s)


def _ay_basi(val) -> date:
    d = _tarih(val)
    if d is None:
        raise ValueError("tarih gerekli")
    return date(d.year, d.month, 1)


def _para(val, varsayilan=0.0) -> float:
    if val is None or val == "":
        return float(varsayilan)
    try:
        x = float(val)
    except (TypeError, ValueError):
        return float(varsayilan)
    if not math.isfinite(x):
        return float(varsayilan)
    return round(x, 2)


def _ay_ekle(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + int(n)
    ny = total // 12
    nm = total % 12 + 1
    last = calendar.monthrange(ny, nm)[1]
    return date(ny, nm, min(d.day, last))


def _gun_sigdir(y: int, m: int, gun: int) -> date:
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(int(gun), last))


def pencere_indeksi(sozlesme_basi: date, ay: date) -> int:
    """Sözleşme ayından itibaren 12 aylık blok numarası (0 = ilk yıl)."""
    bas = _ay_basi(sozlesme_basi)
    a = _ay_basi(ay)
    return ((a.year - bas.year) * 12 + (a.month - bas.month)) // 12


def _anahtar(ay: date) -> str:
    a = _ay_basi(ay)
    return f"{a.year:04d}-{a.month:02d}"


def _tufe_son_pozitif(year_map) -> float:
    if not isinstance(year_map, dict) or not year_map:
        return 0.0
    best_m, best_o = 0, 0.0
    for mk, ow in year_map.items():
        try:
            mi = int(mk)
            ovv = float(ow or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= mi <= 12 and ovv > 0 and math.isfinite(ovv) and mi > best_m:
            best_m, best_o = mi, ovv
    return best_o


def _tufe_yil(tufe: dict, yil: int) -> dict:
    if not isinstance(tufe, dict):
        return {}
    inner = tufe.get(yil)
    if inner is None:
        inner = tufe.get(str(yil))
    return inner if isinstance(inner, dict) else {}


def _tufe_ay(inner: dict, ay: int):
    if not isinstance(inner, dict):
        return None
    if ay in inner:
        return inner.get(ay)
    return inner.get(str(ay))


def tufe_oran_bloga_giris(tufe: dict, sonraki_yil: int, artis_ay: int, baslangic_yil: int) -> float:
    """_aylik_grid_contract_core ile aynı TÜFE yedek sırası.

    sonraki_yil: bloğun takvim yılı (sözleşme yılı + blok indeksi).
    """
    inner = _tufe_yil(tufe, sonraki_yil)
    raw = _tufe_ay(inner, artis_ay)
    try:
        oran = float(raw or 0)
    except (TypeError, ValueError):
        oran = 0.0
    if (not oran or not math.isfinite(oran)) and sonraki_yil > baslangic_yil:
        prev = _tufe_yil(tufe, sonraki_yil - 1)
        raw_p = _tufe_ay(prev, artis_ay)
        try:
            oran2 = float(raw_p or 0)
        except (TypeError, ValueError):
            oran2 = 0.0
        if oran2 > 0 and math.isfinite(oran2):
            oran = oran2
    if not oran or not math.isfinite(oran):
        o3 = _tufe_son_pozitif(inner)
        if o3 > 0 and math.isfinite(o3):
            oran = o3
    if (not oran or not math.isfinite(oran)) and sonraki_yil > baslangic_yil:
        o4 = _tufe_son_pozitif(_tufe_yil(tufe, sonraki_yil - 1))
        if o4 > 0 and math.isfinite(o4):
            oran = o4
    return oran if (oran and math.isfinite(oran) and oran > 0) else 0.0


def _kart_oranlari(kart_net: float, nakit, banka) -> tuple[bool, float]:
    n = _para(nakit)
    b = _para(banka)
    if kart_net <= 0 or n <= 0 or b <= 0:
        return False, 0.0
    tol = max(0.02, abs(kart_net) * 0.01 + 1e-9)
    if abs((n + b) - kart_net) > tol:
        return False, 0.0
    return True, n / kart_net


def _kart_parca(net: float, kdv_oran: float, kira_nakit: bool, r_n: float | None) -> dict:
    kdv_mult = 1.0 + float(kdv_oran) / 100.0
    if r_n is not None:
        nakit = round(net * r_n, 2)
        banka = round(net - nakit, 2)
        brut = round(nakit + banka * kdv_mult, 2)
    elif kira_nakit:
        nakit = round(net, 2)
        banka = 0.0
        brut = round(net, 2)
    else:
        nakit = 0.0
        banka = round(net, 2)
        brut = round(net * kdv_mult, 2)
    kdv = round(brut - nakit - banka, 2)
    return {
        "net": round(net, 2),
        "brut": brut,
        "kdv": kdv,
        "nakit": nakit,
        "banka": banka,
    }


def _reel_parca(val, kdv_oran: float, kira_nakit: bool, r_n: float | None) -> dict:
    if isinstance(val, dict) and (
        val.get("net") is not None or val.get("nakit") is not None or val.get("banka") is not None
    ):
        brut = _para(val.get("brut"))
        nakit = _para(val.get("nakit"))
        banka = _para(val.get("banka"))
        net = _para(val.get("net"))
        if net <= 0 and (nakit > 0 or banka > 0):
            net = round(nakit + banka, 2)
        return {
            "net": net,
            "brut": brut,
            "kdv": round(brut - nakit - banka, 2),
            "nakit": nakit,
            "banka": banka,
        }
    brut = _para(val.get("brut") if isinstance(val, dict) else val)
    if r_n is not None:
        kdv_mult = 1.0 + float(kdv_oran) / 100.0
        faktor = r_n + (1.0 - r_n) * kdv_mult
        net = round(brut / faktor, 2) if faktor else 0.0
        parca = _kart_parca(net, kdv_oran, False, r_n)
        parca["brut"] = brut
        parca["kdv"] = round(brut - parca["nakit"] - parca["banka"], 2)
        return parca
    if kira_nakit:
        return {"net": brut, "brut": brut, "kdv": 0.0, "nakit": brut, "banka": 0.0}
    kdv_mult = 1.0 + float(kdv_oran) / 100.0
    net = round(brut / kdv_mult, 2) if kdv_mult else 0.0
    parca = _kart_parca(net, kdv_oran, False, None)
    parca["brut"] = brut
    parca["kdv"] = round(brut - net, 2)
    return parca


def _reel_harita(bas: date, artis: date, reel: dict) -> dict[tuple[int, int], object]:
    """İlk sözleşme yılını atlar. Anahtar (yıl, ay), değer ham reel kaydı."""
    out: dict[tuple[int, int], object] = {}
    if not isinstance(reel, dict):
        return out
    sirali = []
    for k, v in reel.items():
        try:
            yil = int(k)
        except (TypeError, ValueError):
            continue
        if yil == bas.year:
            continue
        sirali.append((yil, v))
    sirali.sort()
    for yil, ham in sirali:
        baslangic = _gun_sigdir(yil, artis.month, artis.day)
        for i in range(12):
            d = _ay_ekle(baslangic, i)
            out[(d.year, d.month)] = ham
    return out


def _net_bloklari(bas_yil: int, artis_ay: int, kart_net: float, tufe: dict, blok_sayisi: int) -> dict[int, float]:
    current = round(float(kart_net), 2)
    out = {0: current}
    for blok in range(1, blok_sayisi):
        oran = tufe_oran_bloga_giris(tufe, bas_yil + blok, artis_ay, bas_yil)
        if oran > 0:
            current = round(current * (1.0 + oran / 100.0), 2)
        out[blok] = current
    return out


def _norm_plan(ham: dict, kart: dict) -> dict | None:
    if not isinstance(ham, dict) or ham.get("iptal_at"):
        return None
    gecer = _ay_basi(ham.get("gecerlilik_ay"))
    net = _para(ham.get("yeni_net"))
    if net <= 0:
        raise ValueError("yeni_net sifirdan buyuk olmali")
    kdv = ham.get("kdv_oran")
    kdv_oran = _para(kart["kdv_oran"] if kdv is None or kdv == "" else kdv)
    nakit = ham.get("nakit_tutar")
    banka = ham.get("banka_tutar")
    return {
        "gecerlilik_ay": gecer,
        "yeni_net": net,
        "kdv_oran": kdv_oran,
        "yeni_brut": None if ham.get("yeni_brut") in (None, "") else _para(ham.get("yeni_brut")),
        "nakit_tutar": None if nakit in (None, "") else _para(nakit),
        "banka_tutar": None if banka in (None, "") else _para(banka),
    }


def _plan_parca(plan: dict, net: float, kart: dict, giris_ayi: bool) -> dict:
    """Giriş ayında kayıtlı brüt durur. Sonraki blokta net bileşiklenir, pay oranı korunur."""
    kdv_oran = float(plan["kdv_oran"])
    kendi = plan["nakit_tutar"] is not None or plan["banka_tutar"] is not None
    if not kendi:
        karma, r_n = _kart_oranlari(kart["kart_net"], kart["nakit"], kart["banka"])
        r = r_n if karma else None
        parca = _kart_parca(net, kdv_oran, kart["kira_nakit"] and r is None, r)
    else:
        n0 = 0.0 if plan["nakit_tutar"] is None else float(plan["nakit_tutar"])
        b0 = 0.0 if plan["banka_tutar"] is None else float(plan["banka_tutar"])
        taban = round(n0 + b0, 2)
        if taban <= 0:
            parca = _kart_parca(net, kdv_oran, False, None)
        elif giris_ayi and abs(taban - float(plan["yeni_net"])) <= max(0.02, abs(plan["yeni_net"]) * 0.01):
            nakit, banka = round(n0, 2), round(b0, 2)
            if plan["yeni_brut"] is not None:
                brut = float(plan["yeni_brut"])
            else:
                brut = round(nakit + banka * (1.0 + kdv_oran / 100.0), 2)
            parca = {
                "net": round(float(plan["yeni_net"]), 2),
                "brut": brut,
                "kdv": round(brut - nakit - banka, 2),
                "nakit": nakit,
                "banka": banka,
            }
        else:
            r_n = n0 / taban
            parca = _kart_parca(net, kdv_oran, False, r_n)
    if giris_ayi and plan["yeni_brut"] is not None and not kendi:
        parca = dict(parca)
        parca["brut"] = float(plan["yeni_brut"])
        parca["kdv"] = round(parca["brut"] - parca["nakit"] - parca["banka"], 2)
    return parca


class AyListesi(list):
    """Ay satırları ve fatura belge uyarıları. Liste gibi dolaşılır."""

    uyarilar: list

    def __init__(self, items, uyarilar=None):
        super().__init__(items)
        self.uyarilar = list(uyarilar or [])


def _reel_acik_kilit(yil: int, bas_yil: int, reel_brut: float, onceki_brut) -> bool:
    """Ekstre ile aynı: ilk yıl kilit değil; önceki brütle aynı kopya kilit değil."""
    if yil <= bas_yil:
        return False
    if onceki_brut is None:
        return True
    try:
        prev = float(onceki_brut)
        mv = float(reel_brut)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(prev) or prev <= 0 or not math.isfinite(mv) or mv < 0:
        return math.isfinite(mv) and mv >= 0
    if abs(mv - prev) <= 0.02:
        return False
    return True


def _reel_brut_deger(val) -> float:
    if isinstance(val, dict):
        return _para(val.get("brut"))
    return _para(val)


def _satir(ay: date, bas: date, parca: dict, kaynak: str) -> dict:
    return {
        "ay": _anahtar(ay),
        "yil": ay.year,
        "ay_no": ay.month,
        "pencere": pencere_indeksi(bas, ay),
        "brut": parca["brut"],
        "net": parca["net"],
        "kdv": parca["kdv"],
        "nakit": parca["nakit"],
        "banka": parca["banka"],
        "kaynak": kaynak,
    }


def ay_bazli_tutarlar(zincir: dict, planlar: list | None = None) -> list[dict]:
    """Ay bazında brüt, net, KDV ve nakit/banka payı.

    zincir: sozlesme_tarihi, ay_sayisi, aylik_net, kdv_oran, kira_nakit,
    nakit_tutar, banka_tutar, artis_tarihi, tufe, reel, faturali_aylar.
    planlar: gecerlilik_ay, yeni_net, kdv_oran, yeni_brut, nakit_tutar,
    banka_tutar, iptal_at. İptalli satır yok sayılır.
    """
    if not isinstance(zincir, dict):
        raise ValueError("zincir gerekli")
    bas_gun = _tarih(zincir.get("sozlesme_tarihi"))
    if bas_gun is None:
        raise ValueError("sozlesme_tarihi gerekli")
    bas = _ay_basi(bas_gun)
    artis = _tarih(zincir.get("artis_tarihi")) or bas_gun
    try:
        ay_sayisi = int(zincir.get("ay_sayisi") or 0)
    except (TypeError, ValueError):
        ay_sayisi = 0
    if ay_sayisi < 1 or ay_sayisi > 240:
        raise ValueError("ay_sayisi 1..240 olmali")
    kart_net = _para(zincir.get("aylik_net"))
    if kart_net <= 0:
        raise ValueError("aylik_net sifirdan buyuk olmali")
    kdv_oran = _para(20 if zincir.get("kdv_oran") in (None, "") else zincir.get("kdv_oran"))
    kira_nakit = bool(zincir.get("kira_nakit"))
    karma, r_n = _kart_oranlari(kart_net, zincir.get("nakit_tutar"), zincir.get("banka_tutar"))
    kart = {
        "kart_net": kart_net,
        "kdv_oran": kdv_oran,
        "kira_nakit": kira_nakit,
        "nakit": zincir.get("nakit_tutar"),
        "banka": zincir.get("banka_tutar"),
    }
    tufe = zincir.get("tufe") if isinstance(zincir.get("tufe"), dict) else {}
    blok_sayisi = (ay_sayisi + 11) // 12
    netler = _net_bloklari(bas.year, artis.month, kart_net, tufe, blok_sayisi)
    reel_ham = zincir.get("reel") if isinstance(zincir.get("reel"), dict) else {}
    reel_map = _reel_harita(bas, artis, reel_ham)
    reel_yillar: dict[int, object] = {}
    for k, v in reel_ham.items():
        try:
            yil = int(k)
        except (TypeError, ValueError):
            continue
        if yil != bas.year:
            reel_yillar[yil] = v
    faturali = set()
    for ham in zincir.get("faturali_aylar") or []:
        faturali.add(_anahtar(_tarih(ham) or _ay_basi(ham)))
    belge = {}
    for ham_k, ham_v in (zincir.get("fatura_belge") or {}).items():
        try:
            belge[_anahtar(_tarih(ham_k) or _ay_basi(ham_k))] = _para(ham_v)
        except (TypeError, ValueError):
            continue
    uyarilar = []
    normlar = []
    for ham in planlar or []:
        plan = _norm_plan(ham, kart)
        if plan is not None:
            normlar.append(plan)
    normlar.sort(key=lambda p: (p["gecerlilik_ay"],))
    r_kart = r_n if karma else None
    out = []
    for i in range(ay_sayisi):
        ay = _ay_ekle(bas, i)
        blok = i // 12
        eski_parca = _kart_parca(netler[blok], kdv_oran, kira_nakit and r_kart is None, r_kart)
        eski_kaynak = "kart"
        ham_reel = reel_map.get((ay.year, ay.month))
        if ham_reel is not None:
            eski_parca = _reel_parca(ham_reel, kdv_oran, kira_nakit and r_kart is None, r_kart)
            eski_kaynak = "reel"
        anahtar = _anahtar(ay)
        if anahtar in faturali:
            belge_brut = belge.get(anahtar)
            if belge_brut is not None and abs(belge_brut - float(eski_parca["brut"])) > 0.02:
                uyarilar.append({
                    "ay": anahtar,
                    "zincir_brut": eski_parca["brut"],
                    "belge_brut": belge_brut,
                })
            out.append(_satir(ay, bas, eski_parca, "fatura"))
            continue
        yurur = None
        for plan in normlar:
            if plan["gecerlilik_ay"] <= ay:
                yurur = plan
        if yurur is None:
            out.append(_satir(ay, bas, eski_parca, eski_kaynak))
            continue
        p_blok = pencere_indeksi(bas, yurur["gecerlilik_ay"])
        if blok == p_blok:
            parca = _plan_parca(yurur, yurur["yeni_net"], kart, True)
            out.append(_satir(ay, bas, parca, "plan"))
            continue
        prev_brut = _plan_parca(yurur, yurur["yeni_net"], kart, True)["brut"]
        kilit = False
        net = float(yurur["yeni_net"])
        kaynak = "plan_tufe"
        for w in range(p_blok + 1, blok + 1):
            donem_yil = bas.year + w
            oran = tufe_oran_bloga_giris(tufe, donem_yil, artis.month, bas.year)
            yil_reel = reel_yillar.get(donem_yil)
            reel_brut = _reel_brut_deger(yil_reel) if yil_reel is not None else None
            if reel_brut is not None and reel_brut > 0 and _reel_acik_kilit(donem_yil, bas.year, reel_brut, prev_brut):
                prev_brut = reel_brut
                kilit = True
                kaynak = "reel"
                continue
            if kilit:
                if oran > 0:
                    nxt = round(prev_brut * (1.0 + oran / 100.0), 2)
                    if math.isfinite(nxt) and nxt > 0:
                        prev_brut = nxt
                kaynak = "reel_tufe"
                continue
            if oran > 0:
                net = round(net * (1.0 + oran / 100.0), 2)
            prev_brut = _plan_parca(yurur, net, kart, False)["brut"]
            kaynak = "plan_tufe"
        if kaynak == "reel":
            parca = _reel_parca(reel_yillar[bas.year + blok], kdv_oran, kira_nakit and r_kart is None, r_kart)
            parca["brut"] = round(prev_brut, 2)
        elif kaynak == "reel_tufe":
            parca = _reel_parca(prev_brut, kdv_oran, kira_nakit and r_kart is None, r_kart)
        else:
            parca = _plan_parca(yurur, net, kart, False)
        out.append(_satir(ay, bas, parca, kaynak))
    return AyListesi(out, uyarilar)


def _plan_ay_alti(ay_anahtar: str, planlar) -> bool:
    try:
        ay = _ay_basi(ay_anahtar)
    except (TypeError, ValueError):
        return False
    for ham in planlar or []:
        if not isinstance(ham, dict) or ham.get("iptal_at"):
            continue
        try:
            gecer = _ay_basi(ham.get("gecerlilik_ay"))
        except (TypeError, ValueError):
            continue
        if gecer <= ay:
            return True
    return False


def tahsilat_yil_borc(aylik_tek, ay_brutlari=None, plan_var=False):
    """Plansız yıl satırı tek aylık × 12. Planlı yıl, ay ay brütlerin toplamı."""
    if not plan_var:
        try:
            return round(float(aylik_tek) * 12, 2)
        except (TypeError, ValueError):
            return 0.0
    toplam = 0.0
    for ham in ay_brutlari or []:
        try:
            toplam += float(ham)
        except (TypeError, ValueError):
            continue
    return round(toplam, 2)


def bildirge_yil_tablosu(satirlar, yil) -> list:
    """Takvim yılının ay netleri. Plan varsa o aydan itibaren plan neti; öncesi eski zincir."""
    try:
        yil = int(yil)
    except (TypeError, ValueError):
        return []
    out = []
    for satir in satirlar or []:
        try:
            if int(satir.get("yil") or 0) != yil:
                continue
            out.append(
                {
                    "ay": satir.get("ay"),
                    "net": round(float(satir.get("net") or 0), 2),
                    "brut": round(float(satir.get("brut") or 0), 2),
                }
            )
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def _kilit_kaydi_ekle(kayitlar, ay, tutar, kaynak):
    for kayit in kayitlar:
        if kayit.get("ay") != ay:
            continue
        if tutar is not None:
            once = kayit.get("fatura_tutari")
            taban = float(once or 0) if once is not None else 0.0
            kayit["fatura_tutari"] = round(taban + float(tutar), 2)
        if kaynak == "isaret":
            kayit["kaynak"] = "isaret"
        return
    kayitlar.append({"ay": ay, "fatura_tutari": tutar, "kaynak": kaynak})


def plan_ekle_on_kontrol(gecerlilik_ay, faturali_aylar) -> dict:
    """Geçerlilik faturalı aya denk gelirse ilk faturasız ayı önerir. Reddetmez.

    faturali_aylar: ay dizeleri veya {ay, fatura_tutari, kaynak} kayıtları.
    kaynak: isaret | fatura_tarihi. Geçerlilik tarihi kendiliğinden kaymaz.
    """
    gecer = _ay_basi(gecerlilik_ay)
    kilit = set()
    kayitlar = []
    for ham in faturali_aylar or []:
        try:
            if isinstance(ham, dict):
                ay = _anahtar(_tarih(ham.get("ay")) or _ay_basi(ham.get("ay")))
                kaynak = ham.get("kaynak") if ham.get("kaynak") in ("isaret", "fatura_tarihi") else "isaret"
                tutar = ham.get("fatura_tutari")
                if tutar is not None:
                    tutar = round(float(tutar), 2)
            else:
                ay = _anahtar(_tarih(ham) or _ay_basi(ham))
                kaynak = "isaret"
                tutar = None
        except (TypeError, ValueError):
            continue
        kilit.add(ay)
        _kilit_kaydi_ekle(kayitlar, ay, tutar, kaynak)
    kayitlar.sort(key=lambda k: k.get("ay") or "")
    anahtar = _anahtar(gecer)
    if anahtar not in kilit:
        return {
            "faturali": False,
            "gecerlilik_ay": anahtar,
            "onerilen_ay": anahtar,
            "uyarilar": [],
            "kilitlenen_aylar": kayitlar,
        }
    onerilen = None
    imlec = gecer
    for _ in range(240):
        imlec = _ay_ekle(imlec, 1)
        ad = _anahtar(imlec)
        if ad not in kilit:
            onerilen = ad
            break
    mesaj = "Gecerlilik ayi faturali."
    if onerilen:
        mesaj = mesaj + " Ilk faturasiz ay " + onerilen + "."
    return {
        "faturali": True,
        "gecerlilik_ay": anahtar,
        "onerilen_ay": onerilen,
        "uyarilar": [{"ay": anahtar, "mesaj": mesaj}],
        "kilitlenen_aylar": kayitlar,
    }


def plan_uygulanacak_aylar(zincir: dict, planlar: list | None) -> tuple[dict, list]:
    """Planın değiştirdiği aylar. Anahtar YYYY-MM. Plansızda boş; payload'a dokunulmaz."""
    if not planlar:
        return {}, []
    rows = ay_bazli_tutarlar(zincir, planlar)
    out = {}
    for row in rows:
        kaynak = row.get("kaynak")
        if kaynak in ("plan", "plan_tufe", "reel_tufe"):
            out[row["ay"]] = row
        elif kaynak == "reel" and _plan_ay_alti(row["ay"], planlar):
            out[row["ay"]] = row
    return out, list(getattr(rows, "uyarilar", []) or [])
