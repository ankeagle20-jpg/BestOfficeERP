# -*- coding: utf-8 -*-
"""Plan dondurma — zincir hesabından sonra 0 katmanı.

Veritabanı ve cache çağırmaz. plan_var üretmez.
Öncelik: dondurma > plan > reel > kart. Kilitli aylar (K1/K2/K0) listeye girmez.
"""
from __future__ import annotations

import math
from datetime import date, datetime

from plan_fatura_kilit import KILITLI
from sozlesme_plan import _ay_basi, _ay_ekle, ay_bazli_tutarlar

DONDURMA_FP_SURUM = "s1"
# Grid AYLIK_GRID_TAM_ODENDI_TOLERANS ile aynı. K1/K2/K0 bu eşikten ayrıdır.
AY_ODENDI_TOL = 0.05
NEDEN_ODENMIS = "ödenmiş (tahsilat veya fazla ödeme kredisi)"
NEDEN_KISMI = "kısmi ödenmiş"


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


def ay_odendi_mi(brut, odenen, tol=None) -> str:
    """Grid yeşil kararı. 'tam' | 'kismi' | ''.

    Tam: brüt > tol ve kalan <= tol. Brüt yaklaşık 0 iken tam sayılmaz.
    Kısmi: ödenen > 0 ve kalan > tol. plan_fatura_kilit K2 değildir.
    """
    if tol is None:
        tol = AY_ODENDI_TOL
    try:
        brut_f = float(brut or 0)
    except (TypeError, ValueError):
        brut_f = 0.0
    try:
        odenen_f = float(odenen or 0)
    except (TypeError, ValueError):
        odenen_f = 0.0
    if not math.isfinite(brut_f):
        brut_f = 0.0
    if not math.isfinite(odenen_f) or odenen_f < 0:
        odenen_f = 0.0
    try:
        tol_f = float(tol)
    except (TypeError, ValueError):
        tol_f = AY_ODENDI_TOL
    kalan = max(round(brut_f - odenen_f, 2), 0.0)
    if odenen_f > 0 and kalan > tol_f:
        return "kismi"
    if brut_f > tol_f and kalan <= tol_f:
        return "tam"
    return ""


def dondurma_ayi_odenmis(brut, odenen, tol=None) -> str | None:
    """Dondurma uygunluğu. K1/K2/K0 sınıflarına girmez; None ise ödeme engeli yok."""
    durum = ay_odendi_mi(brut, odenen, tol)
    if durum == "tam":
        return NEDEN_ODENMIS
    if durum == "kismi":
        return NEDEN_KISMI
    return None


def _odenen_deger(harita, ay: str) -> float:
    if not isinstance(harita, dict):
        return 0.0
    anahtarlar = [ay, ay + "-01"]
    parca = str(ay or "").split("-")
    if len(parca) >= 2:
        try:
            anahtarlar.append("%d-%d" % (int(parca[0]), int(parca[1])))
        except (TypeError, ValueError):
            pass
    for anahtar in anahtarlar:
        if anahtar not in harita:
            continue
        try:
            deger = float(harita.get(anahtar) or 0)
        except (TypeError, ValueError):
            return 0.0
        return deger if math.isfinite(deger) and deger > 0 else 0.0
    return 0.0


def dondurma_onizleme(zincir: dict, planlar, baslangic, ay_sayisi: int, siniflar=None, odenen_by_ay=None, tol=None) -> dict:
    """Her ay: dondurulacak ya da atlanacak.

    K1/K2/K0 nedenleri aynen kalır. Onların dışında ay_odendi_mi (grid yeşili)
    ödenmiş veya kısmi ayı atlar. odenen_by_ay adayın kendi dondurması yokmuş
    gibi hesaplanmış baseline tahsilattır; bu fonksiyon veritabanı çağırmaz.
    Atlanan ayın tutarı değişmez.
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
            neden_odeme = dondurma_ayi_odenmis(brut, _odenen_deger(odenen_by_ay, ay), tol)
            if neden_odeme:
                satir = {
                    "ay": ay,
                    "durum": "atlanacak",
                    "sinif": None,
                    "neden": neden_odeme,
                    "brut": brut,
                    "brut_sonra": brut,
                }
                atlanan.append({"ay": ay, "sinif": None, "neden": neden_odeme})
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

