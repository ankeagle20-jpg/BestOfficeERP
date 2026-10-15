# -*- coding: utf-8 -*-
"""Aylık mutabakat — salt okunur.

Dört ekranın (grid, tahsilat paneli, borç faturası, cari ekstre) aynı ay için
gördüğü rakamları yan yana koyar. Mevcut yazma/hesap fonksiyonlarına dokunmaz;
yalnız çağırır. INSERT/UPDATE/DDL yok.
"""
from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date
from typing import Any

# Yalnız okuma yardımcıları — yazma yollarına girilmez.
from db import fetch_all, fetch_one  # noqa: E402

TOL = 0.50

SINIFLAR = {
    "panel_map": "yuksek",
    "grid_kirmizi_map_tahsil": "yuksek",
    "grid_yesil_map_bos": "yuksek",
    "dondurma_tahsil": "yuksek",
    "ekstre_eksik_ay": "sunum",
    "ekstre_sisko": "sunum",
    "borc_fatura_grid": "sunum",
}


def _f2(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_ay(val) -> date:
    if isinstance(val, date):
        return date(val.year, val.month, 1)
    s = str(val or "").strip()
    if len(s) >= 7 and s[4] == "-":
        return date(int(s[0:4]), int(s[5:7]), 1)
    raise ValueError("ay YYYY-MM olmali")


def _ay_listesi(bas: date, bit: date) -> list[date]:
    out = []
    y, m = bas.year, bas.month
    while (y, m) <= (bit.year, bit.month):
        out.append(date(y, m, 1))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def _grid_durum(a: dict | None, dondurma: set, iso: str) -> str:
    key7 = iso[:7]
    if key7 in dondurma or iso[:7] in dondurma:
        return "dondurma"
    if not a:
        return "yok"
    tutar = _f2(a.get("tutar_kdv_dahil"))
    odenen = a.get("odenen_kdv_dahil")
    if odenen is None:
        odenen = a.get("odenen")
    odenen = _f2(odenen) if odenen is not None else None
    if tutar <= 0.001:
        if 0 < tutar < 0.5:
            return "placeholder"
        return "sifir"
    if bool(a.get("tahsil_edildi")):
        return "odendi"
    if odenen is not None and odenen > TOL and odenen + TOL < tutar:
        return "kismi"
    return "kirmizi"


def _borc_fatura_by_iso(musteri_id: int) -> dict[str, float]:
    import re

    rows = fetch_all(
        """
        SELECT COALESCE(toplam, 0) AS toplam, COALESCE(notlar, '') AS notlar
        FROM faturalar
        WHERE musteri_id = %s
          AND COALESCE(notlar, '') LIKE '%%|AYLIK_TUTAR|%%'
        """,
        (musteri_id,),
    ) or []
    out: dict[str, float] = {}
    for r in rows:
        m = re.search(r"\|AYLIK_TUTAR\|(\d{4}-\d{2}-\d{2})\|", str(r.get("notlar") or ""))
        if not m:
            continue
        iso = m.group(1)[:10]
        out[iso] = round(out.get(iso, 0) + _f2(r.get("toplam")), 2)
    return out


def _ekstre_tahsil_satir_hafif(mid: int, gr, bas: date, bit: date) -> dict[str, float]:
    """Ekstre Tahsilat satır tarihi — _cari_ekstre_hareketler ile aynı kuralın hafif hali.

    - fatura_id boş + kendi kaynak (manuel_makbuz/banka_import/odeme_linki): tahsilat_tarihi
      (direct_pays yolu)
    - grid_toplu / işaretli: eşleşme ayı (_ekstre_eslesme_tarihi)
    Yazmaz.
    """
    rows = fetch_all(
        """
        SELECT t.id, COALESCE(t.tutar, 0) AS tutar, COALESCE(t.aciklama, '') AS aciklama,
               t.tahsilat_tarihi, COALESCE(t.kaynak, '') AS kaynak, t.fatura_id,
               f.fatura_tarihi
        FROM tahsilatlar t
        LEFT JOIN faturalar f ON f.id = t.fatura_id
        WHERE (t.musteri_id = %s OR t.customer_id = %s)
          AND COALESCE(t.tutar, 0) > 0
        """,
        (mid, mid),
    ) or []
    kendi = set(getattr(gr, "_EKSTRE_KENDI_KAYNAK", ("manuel_makbuz", "banka_import", "odeme_linki")))
    out: dict[str, float] = {}
    bit_son = date(bit.year, bit.month, calendar.monthrange(bit.year, bit.month)[1])
    for r in rows:
        tut = _f2(r.get("tutar"))
        if tut <= 0:
            continue
        kay = str(r.get("kaynak") or "").strip().lower()
        fid = r.get("fatura_id")
        if kay in kendi and not fid:
            raw = str(r.get("tahsilat_tarihi") or "")[:10]
            try:
                td = date(int(raw[0:4]), int(raw[5:7]), int(raw[8:10]))
            except (TypeError, ValueError):
                continue
            if td < bas or td > bit_son:
                continue
            iso1 = date(td.year, td.month, 1).isoformat()
        else:
            iso = gr._ekstre_eslesme_tarihi(
                kay, r.get("tahsilat_tarihi"), r.get("fatura_tarihi"), r.get("aciklama"), None
            )
            if not iso:
                continue
            iso1 = date(int(iso[0:4]), int(iso[5:7]), 1).isoformat()
            if iso1 < bas.isoformat() or iso1 > bit.isoformat():
                continue
        out[iso1] = round(out.get(iso1, 0) + tut, 2)
    return out


def aylik_mutabakat(mid, ay_baslangic, ay_bitis, *, tam_ekstre: bool = True) -> list[dict[str, Any]]:
    """Salt okunur ay satırları. Yazmaz.

    Dönüş alanları:
      kira, tahsil_map, tahsil_panel, grid_tutar, grid_durum, grid_odenen_varsa,
      ekstre_borc, ekstre_tahsil_dagitim, ekstre_tahsil_satir,
      borc_fatura (ekstre borçtan ayrı: faturalar.|AYLIK_TUTAR| toplam)

    tam_ekstre=True: Sözleşmeler ekstre (_cari_ekstre_hareketler) — tek mid için.
    tam_ekstre=False: aynı kuralların hafif okuması — aktif tarama.
    """
    from routes import giris_routes as gr

    mid = int(mid)
    bas = _parse_ay(ay_baslangic)
    bit = _parse_ay(ay_bitis)
    if bas > bit:
        bas, bit = bit, bas

    if not fetch_one("SELECT id FROM customers WHERE id = %s", (mid,)):
        return []

    payload = gr._read_aylik_grid_cache_payload(mid) or {}
    grid_by = {}
    for a in (payload.get("aylar") if isinstance(payload, dict) else []) or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = date(int(a["yil"]), int(a["ay"]), 1).isoformat()
        except (TypeError, ValueError, KeyError):
            continue
        grid_by[iso] = a

    panel = gr._load_musteri_panel_by_iso(mid) or {}
    tahsil_map = gr._aylik_tahsil_tutar_map(mid) or {}
    try:
        dondurma = set(gr._dondurma_ay_kumesi(mid) or ())
    except Exception:
        dondurma = set()

    borc_fatura = _borc_fatura_by_iso(mid)

    # Gerçek ekstre Kira satırı = grid/reel/plan (_ekstre_ay_borcu); cache ile aynı tutar.
    ekstre_borc: dict[str, float] = {}
    ekstre_tahsil_satir: dict[str, float] = {}
    bit_son = date(bit.year, bit.month, calendar.monthrange(bit.year, bit.month)[1])
    if tam_ekstre:
        try:
            hareketler = gr._cari_ekstre_hareketler(mid, bas, bit_son, 0) or []
        except Exception:
            hareketler = []
        for h in hareketler:
            if not isinstance(h, dict):
                continue
            iso = str(h.get("tarih") or "")[:10]
            if len(iso) < 10:
                continue
            iso1 = date(int(iso[0:4]), int(iso[5:7]), 1).isoformat()
            tur = str(h.get("tur") or "")
            if tur == "Kira":
                ekstre_borc[iso1] = round(ekstre_borc.get(iso1, 0) + _f2(h.get("borc")), 2)
            elif tur == "Tahsilat":
                ekstre_tahsil_satir[iso1] = round(
                    ekstre_tahsil_satir.get(iso1, 0) + _f2(h.get("alacak")), 2
                )
    else:
        for iso, a in grid_by.items():
            v = _f2(a.get("tutar_kdv_dahil"))
            if v > 0:
                ekstre_borc[iso] = v
        ekstre_tahsil_satir = _ekstre_tahsil_satir_hafif(mid, gr, bas, bit)

    satirlar = []
    for d0 in _ay_listesi(bas, bit):
        iso = d0.isoformat()
        g = grid_by.get(iso)
        prow = panel.get(iso) or {}
        pt = _f2(prow.get("tahsil")) if isinstance(prow, dict) else 0.0
        pa = _f2(prow.get("aylik") or prow.get("brut")) if isinstance(prow, dict) else 0.0
        gt = _f2(g.get("tutar_kdv_dahil")) if g else 0.0
        go_raw = None
        if g:
            go_raw = g.get("odenen_kdv_dahil")
            if go_raw is None:
                go_raw = g.get("odenen")
        go = _f2(go_raw) if go_raw is not None else None
        mt = _f2(tahsil_map.get(iso))
        durum = _grid_durum(g, dondurma, iso)
        kira = pa if pa > TOL else (gt if gt > TOL else _f2(ekstre_borc.get(iso)))
        row = {
            "iso": iso,
            "ay": iso[:7],
            "kira": kira,
            "tahsil_map": mt,
            "tahsil_panel": pt,
            "grid_tutar": gt,
            "grid_durum": durum,
            "grid_odenen_varsa": go,
            "ekstre_borc": _f2(ekstre_borc.get(iso)),
            "ekstre_tahsil_dagitim": mt,
            "ekstre_tahsil_satir": _f2(ekstre_tahsil_satir.get(iso)),
            "borc_fatura": _f2(borc_fatura.get(iso)),
        }
        row["uyusmazliklar"] = siniflandir(row)
        satirlar.append(row)
    return satirlar


def siniflandir(row: dict) -> list[dict[str, Any]]:
    """Sabit kod + ciddiyet + sapma TL."""
    out = []
    pt = _f2(row.get("tahsil_panel"))
    mt = _f2(row.get("tahsil_map"))
    gt = _f2(row.get("grid_tutar"))
    durum = str(row.get("grid_durum") or "")
    et_satir = _f2(row.get("ekstre_tahsil_satir"))
    bf = _f2(row.get("borc_fatura"))
    eb = _f2(row.get("ekstre_borc"))

    def ekle(kod, sapma):
        out.append({
            "kod": kod,
            "ciddiyet": SINIFLAR[kod],
            "sapma_tl": round(abs(float(sapma)), 2),
        })

    if abs(pt - mt) > TOL and (pt > TOL or mt > TOL):
        ekle("panel_map", pt - mt)
    if durum == "kirmizi" and mt > TOL and gt > TOL:
        ekle("grid_kirmizi_map_tahsil", mt)
    if durum == "odendi" and mt <= TOL and gt > TOL:
        ekle("grid_yesil_map_bos", gt)
    if durum == "dondurma" and (pt > TOL or mt > TOL):
        ekle("dondurma_tahsil", max(pt, mt))
    if mt > TOL and et_satir <= TOL:
        ekle("ekstre_eksik_ay", mt)
    if et_satir > TOL and mt > TOL and et_satir > mt + max(100.0, TOL * 2):
        ekle("ekstre_sisko", et_satir - mt)
    # Fatura toplamı ile ekrandaki ekstre/grid kira farkı (sunum)
    kira_ekran = eb if eb > TOL else gt
    if bf > TOL and kira_ekran > TOL and abs(bf - kira_ekran) > TOL:
        ekle("borc_fatura_grid", bf - kira_ekran)
    return out


def tarama_aktif(ay_baslangic="2025-01", ay_bitis="2026-10", top_n=10) -> dict:
    """Aktif (arşivsiz) müşteriler. Yalnız sayı, mid, sınıf. PII yok."""
    mids = [
        int(r["id"])
        for r in (
            fetch_all(
                """
                SELECT id FROM customers
                WHERE COALESCE(is_active, TRUE) IS TRUE
                  AND COALESCE(durum, 'aktif') = 'aktif'
                  AND COALESCE(arsivli, FALSE) IS NOT TRUE
                ORDER BY id
                """
            )
            or []
        )
    ]
    sinif_adet = {k: 0 for k in SINIFLAR}
    sinif_ay = {k: 0 for k in SINIFLAR}
    sinif_sapma = {k: 0.0 for k in SINIFLAR}
    sinif_mid_sapma: dict[str, dict[int, float]] = {k: {} for k in SINIFLAR}
    mid_sapma: dict[int, float] = {}
    mid_ay: dict[int, int] = {}
    uyumsuz_mid = set()
    uyumsuz_ay = 0
    yuksek_ay = 0

    for i, mid in enumerate(mids, 1):
        if i == 1 or i % 25 == 0 or i == len(mids):
            print("tarama %d/%d mid=%s" % (i, len(mids), mid), file=sys.stderr, flush=True)
        try:
            satirlar = aylik_mutabakat(mid, ay_baslangic, ay_bitis, tam_ekstre=False)
        except Exception:
            continue
        for row in satirlar:
            uys = row.get("uyusmazliklar") or []
            if not uys:
                continue
            uyumsuz_ay += 1
            uyumsuz_mid.add(mid)
            mid_ay[mid] = mid_ay.get(mid, 0) + 1
            ay_sapma = 0.0
            for u in uys:
                kod = u["kod"]
                sap = _f2(u.get("sapma_tl"))
                sinif_adet[kod] = sinif_adet.get(kod, 0) + 1
                sinif_ay[kod] = sinif_ay.get(kod, 0) + 1
                sinif_sapma[kod] = round(sinif_sapma.get(kod, 0) + sap, 2)
                sm = sinif_mid_sapma[kod]
                sm[mid] = round(sm.get(mid, 0) + sap, 2)
                ay_sapma += sap
                if u.get("ciddiyet") == "yuksek":
                    yuksek_ay += 1
            mid_sapma[mid] = round(mid_sapma.get(mid, 0) + ay_sapma, 2)

    top = sorted(mid_sapma.items(), key=lambda x: -x[1])[: int(top_n)]
    return {
        "aktif_n": len(mids),
        "uyumsuz_musteri": len(uyumsuz_mid),
        "uyumsuz_ay": uyumsuz_ay,
        "yuksek_ay": yuksek_ay,
        "siniflar": {
            k: {
                "ciddiyet": SINIFLAR[k],
                "adet": sinif_adet[k],
                "ay_adet": sinif_ay[k],
                "sapma_tl": sinif_sapma[k],
                "ilk_10_mid": [
                    m
                    for m, _ in sorted(
                        sinif_mid_sapma[k].items(), key=lambda x: -x[1]
                    )[: int(top_n)]
                ],
            }
            for k in SINIFLAR
        },
        "top_mid": [
            {"mid": m, "sapma_tl": s, "ay_adet": mid_ay.get(m, 0)} for m, s in top
        ],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Aylık mutabakat (salt okunur)")
    p.add_argument("--mid", type=int, help="Tek müşteri matrisi")
    p.add_argument("--bas", default="2025-01")
    p.add_argument("--bit", default="2026-10")
    p.add_argument("--aktif", action="store_true", help="Tüm aktif tarama")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.aktif:
        rapor = tarama_aktif(args.bas, args.bit)
        if args.json:
            print(json.dumps(rapor, ensure_ascii=False, indent=2))
        else:
            print("aktif_n", rapor["aktif_n"])
            print("uyumsuz_musteri", rapor["uyumsuz_musteri"])
            print("uyumsuz_ay", rapor["uyumsuz_ay"])
            print("yuksek_ay", rapor["yuksek_ay"])
            for kod, bil in rapor["siniflar"].items():
                print(
                    "sinif",
                    kod,
                    bil["ciddiyet"],
                    "adet",
                    bil["adet"],
                    "ay",
                    bil["ay_adet"],
                    "sapma",
                    bil["sapma_tl"],
                    "mids",
                    ",".join(str(x) for x in bil.get("ilk_10_mid") or []),
                )
            print("top_mid")
            for t in rapor["top_mid"]:
                print(" ", t["mid"], t["sapma_tl"], t["ay_adet"])
        return 0

    if not args.mid:
        p.error("--mid veya --aktif gerekli")
    satirlar = aylik_mutabakat(args.mid, args.bas, args.bit)
    if args.json:
        print(json.dumps(satirlar, ensure_ascii=False, indent=2))
    else:
        print(
            "ay | kira | map | panel | grid | durum | odenen | ekstre_borc | "
            "ekstre_tah_satir | borc_fatura | uyusmazlik"
        )
        for r in satirlar:
            uys = ",".join(u["kod"] for u in (r.get("uyusmazliklar") or [])) or "-"
            print(
                "%s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s"
                % (
                    r["ay"],
                    r["kira"],
                    r["tahsil_map"],
                    r["tahsil_panel"],
                    r["grid_tutar"],
                    r["grid_durum"],
                    r["grid_odenen_varsa"],
                    r["ekstre_borc"],
                    r["ekstre_tahsil_satir"],
                    r["borc_fatura"],
                    uys,
                )
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
