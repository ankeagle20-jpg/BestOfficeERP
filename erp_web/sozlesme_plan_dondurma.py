# -*- coding: utf-8 -*-
"""Plan dondurma — zincir hesabından sonra 0 katmanı.

Veritabanı ve cache çağırmaz. plan_var üretmez.
Öncelik: dondurma > plan > reel > kart. Kilitli aylar (K1/K2/K0) listeye girmez.
"""
from __future__ import annotations

from datetime import date, datetime

from plan_fatura_kilit import KILITLI
from sozlesme_plan import _ay_basi, _ay_ekle, ay_bazli_tutarlar

DONDURMA_FP_SURUM = "s1"


def _ay_anahtar(val) -> str:
    if isinstance(val, datetime):
        val = val.date()
    if isinstance(val, date):
        a = date(val.year, val.month, 1)
    else:
        s = str(val or "").strip()
        if len(s) >= 7 and s[4] == "-":
            a = date(int(s[0:4]), int(s[5:7]), 1)
        else:
            raise ValueError("ay gecersiz")
    return f"{a.year:04d}-{a.month:02d}"


def ay_listesi(baslangic, ay_sayisi: int) -> list[str]:
    """Başlangıç ayından itibaren ay_sayisi kadar YYYY-MM (1..24)."""
    try:
        n = int(ay_sayisi)
    except (TypeError, ValueError) as exc:
        raise ValueError("ay sayisi 1..24 olmali") from exc
    if n < 1 or n > 24:
        raise ValueError("ay sayisi 1..24 olmali")
    bas = _ay_basi(baslangic)
    return [_ay_anahtar(_ay_ekle(bas, i)) for i in range(n)]


def aralik_cakisir(bas_a, n_a, bas_b, n_b) -> bool:
    """İki açık aralığın takvim ayları kesişiyor mu."""
    return bool(set(ay_listesi(bas_a, n_a)) & set(ay_listesi(bas_b, n_b)))


def _sinif_kaydi(siniflar, ay: str) -> dict | None:
    if not isinstance(siniflar, dict):
        return None
    kayit = siniflar.get(ay)
    if kayit is None and len(ay) == 7:
        kayit = siniflar.get(ay + "-01")
    return kayit if isinstance(kayit, dict) else None


def dondurma_onizleme(zincir: dict, planlar, baslangic, ay_sayisi: int, siniflar=None) -> dict:
    """Her ay: dondurulacak ya da atlanacak (K1/K2/K0 nedeni).

    Zincir normal hesaplanır. Atlanan ayın tutarı değişmez.
    """
    aylar = ay_listesi(baslangic, ay_sayisi)
    z = dict(zincir or {})
    bas = _ay_basi(z.get("sozlesme_tarihi"))
    son = _ay_basi(aylar[-1] + "-01")
    gerek = (son.year - bas.year) * 12 + (son.month - bas.month) + 1
    try:
        mevcut = int(z.get("ay_sayisi") or 0)
    except (TypeError, ValueError):
        mevcut = 0
    z["ay_sayisi"] = max(1, min(240, max(mevcut, gerek)))
    satirlar = {s["ay"]: s for s in ay_bazli_tutarlar(z, planlar)}
    cikti = []
    dondurulan = []
    atlanan = []
    for ay in aylar:
        ham = satirlar.get(ay) or {}
        try:
            brut = round(float(ham.get("brut") or 0), 2)
        except (TypeError, ValueError):
            brut = 0.0
        kayit = _sinif_kaydi(siniflar, ay)
        sinif = str((kayit or {}).get("sinif") or "")
        if sinif in KILITLI:
            neden = str((kayit or {}).get("neden") or sinif)
            satir = {
                "ay": ay,
                "durum": "atlanacak",
                "sinif": sinif,
                "neden": neden,
                "brut": brut,
                "brut_sonra": brut,
            }
            atlanan.append({"ay": ay, "sinif": sinif, "neden": neden})
        else:
            satir = {
                "ay": ay,
                "durum": "dondurulacak",
                "sinif": sinif or None,
                "neden": "",
                "brut": brut,
                "brut_sonra": 0.0,
            }
            dondurulan.append(ay)
        cikti.append(satir)
    return {"aylar": cikti, "dondurulan_aylar": dondurulan, "atlanan_aylar": atlanan}


def dondurma_uygula(satirlar, dondurulan_aylar) -> list:
    """Dondurulan ayları 0 yapar (kaynak=dondurma). Diğer aylar birebir kalır."""
    hedef = set()
    for ham in dondurulan_aylar or []:
        try:
            hedef.add(_ay_anahtar(ham))
        except (TypeError, ValueError):
            continue
    out = []
    for s in satirlar or []:
        satir = dict(s)
        if satir.get("ay") in hedef:
            satir["brut"] = 0.0
            satir["net"] = 0.0
            satir["kdv"] = 0.0
            satir["nakit"] = 0.0
            satir["banka"] = 0.0
            satir["kaynak"] = "dondurma"
        out.append(satir)
    return out


def acik_dondurulan_aylar(kayitlar) -> list[str]:
    """İptalsiz kayıtlardaki dondurulan aylar (sıralı, tekrarsız)."""
    gorulen = []
    for kayit in kayitlar or []:
        if not isinstance(kayit, dict) or kayit.get("iptal_at"):
            continue
        ham = kayit.get("dondurulan_aylar") or []
        if isinstance(ham, str):
            import json

            try:
                ham = json.loads(ham)
            except (TypeError, ValueError):
                ham = []
        for ay in ham:
            try:
                anahtar = _ay_anahtar(ay)
            except (TypeError, ValueError):
                continue
            if anahtar not in gorulen:
                gorulen.append(anahtar)
    return gorulen


def dondurma_parmak(acik_kayitlar) -> str:
    """Cache parmak izi parçası. Açık dondurma yoksa 'none'. compute_rev değildir."""
    parcalar = []
    for kayit in acik_kayitlar or []:
        if not isinstance(kayit, dict) or kayit.get("iptal_at"):
            continue
        try:
            bas = _ay_anahtar(kayit.get("baslangic_ay"))
            n = int(kayit.get("ay_sayisi") or 0)
            pid = int(kayit.get("id") or 0)
        except (TypeError, ValueError):
            continue
        aylar = ",".join(acik_dondurulan_aylar([kayit]))
        parcalar.append(f"{pid}:{bas}:{n}:{aylar}")
    if not parcalar:
        return "none"
    return DONDURMA_FP_SURUM + ";" + ";".join(parcalar)

