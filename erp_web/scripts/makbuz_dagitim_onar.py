# -*- coding: utf-8 -*-
"""manuel_makbuz |AYLIK_PAY| onarımı — kronolojik tam FIFO (allowlist yok).

Varsayılan: DRY-RUN (yazmaz).
--apply yalnız ALLOW_PROD_WRITE tanımlıysa ve --mid verilmişse yazar.
Kapsam dışı: grid_toplu, eşitle/GRID_TAH_PATTERN, banka_import, odeme_linki.
PII yazılmaz (yalnız mid, makbuz id/no, tarih, tutar, ay).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_PAY_RE = re.compile(
    r"\|AYLIK_PAY\|([0-9]{4}-[0-9]{2}-[0-9]{2})=([0-9]+(?:\.[0-9]+)?)"
)
_TAH_RE = re.compile(r"\|AYLIK_TAH\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|")
_KAPSAM_DISI_KAYNAK = frozenset({"grid_toplu", "banka_import", "odeme_linki"})


def _prod_write_ok() -> bool:
    return (os.environ.get("ALLOW_PROD_WRITE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _pay_list(aciklama: str | None) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for m in _PAY_RE.finditer(str(aciklama or "")):
        try:
            out.append((m.group(1), round(float(m.group(2)), 2)))
        except (TypeError, ValueError):
            continue
    return out


def _pay_sum(pays) -> float:
    return round(sum(float(p) for _, p in (pays or [])), 2)


def _j(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, default=str))


def _is_esitle_grid(kaynak: str, aciklama: str, tahsil_eden) -> bool:
    kay = str(kaynak or "").strip().lower()
    if kay in _KAPSAM_DISI_KAYNAK or kay == "manuel_makbuz":
        return False
    te_bos = tahsil_eden is None or str(tahsil_eden).strip() == ""
    if not te_bos:
        return False
    try:
        from routes.giris_routes import GRID_TAH_PATTERN
    except Exception:
        return False
    return bool(GRID_TAH_PATTERN.match(str(aciklama or "").strip()))


def _classify(row: dict) -> str:
    kay = str(row.get("kaynak") or "").strip().lower()
    if kay == "manuel_makbuz":
        return "manuel_makbuz"
    if kay in _KAPSAM_DISI_KAYNAK:
        return kay or "kapsam_disi"
    if _is_esitle_grid(kay, row.get("aciklama") or "", row.get("tahsil_eden")):
        return "esitle_grid_pattern"
    return kay or "kapsam_disi"


def load_tahsilatlar(mid: int, fetch_all) -> list[dict]:
    rows = fetch_all(
        """
        SELECT id,
               COALESCE(musteri_id, customer_id) AS mid,
               makbuz_no,
               ROUND(COALESCE(tutar, 0)::numeric, 2) AS tutar,
               LEFT(tahsilat_tarihi::text, 10) AS tarih,
               COALESCE(aciklama, '') AS aciklama,
               COALESCE(kaynak, '') AS kaynak,
               odeme_turu,
               tahsil_eden
        FROM tahsilatlar
        WHERE COALESCE(musteri_id, customer_id) = %s
          AND COALESCE(tutar, 0) > 0
        ORDER BY LEFT(tahsilat_tarihi::text, 10) NULLS LAST, id
        """,
        (int(mid),),
    ) or []
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = int(item["id"])
        item["mid"] = int(item["mid"])
        item["tutar"] = round(float(item.get("tutar") or 0), 2)
        item["tarih"] = str(item.get("tarih") or "")[:10]
        item["cls"] = _classify(item)
        out.append(item)
    return out


def hesapla_plan(
    mid: int,
    rows: list[dict],
    *,
    borc: dict | None = None,
) -> dict:
    """manuel_makbuz satırlarını kronolojik tam FIFO ile yeniden dağıt (yazmaz)."""
    from routes.faturalar_routes import (
        _gorunen_borc_satirlari,
        yeniden_hesapla_makbuz_dagitim_aciklama,
    )

    mid = int(mid)
    manuel = [r for r in rows if r.get("cls") == "manuel_makbuz"]
    diger = [r for r in rows if r.get("cls") != "manuel_makbuz"]
    manuel = sorted(
        manuel, key=lambda r: (str(r.get("tarih") or "")[:10], int(r.get("id") or 0))
    )
    if borc is None:
        borc_map = _gorunen_borc_satirlari(mid)
    else:
        borc_map = dict(borc)

    diger_onceki = [
        {
            "id": int(r["id"]),
            "tarih": str(r.get("tarih") or "")[:10],
            "tutar": round(float(r.get("tutar") or 0), 2),
            "aciklama": r.get("aciklama") or "",
        }
        for r in diger
    ]
    batch: list[dict] = []
    plans: list[dict] = []
    for r in manuel:
        tid = int(r["id"])
        tah = str(r.get("tarih") or "")[:10]
        tutar = round(float(r.get("tutar") or 0), 2)
        eski_ac = r.get("aciklama") or ""
        eski_pay = _pay_list(eski_ac)
        onceki = list(diger_onceki) + list(batch)
        yeni_ac = yeniden_hesapla_makbuz_dagitim_aciklama(
            mid,
            tutar=tutar,
            tahsilat_tarihi=tah,
            aciklama_eski=eski_ac,
            haric_tahsilat_id=tid,
            kaynak="manuel_makbuz",
            odeme_turu=r.get("odeme_turu"),
            tam_fifo=True,
            onceki_tahsilatlar=onceki,
            borc_satirlari=borc_map,
        )
        if yeni_ac is None:
            return {
                "ok": False,
                "neden": "yeniden_hesapla_none",
                "id": tid,
                "plans": plans,
            }
        yeni_pay = _pay_list(yeni_ac)
        yeni_sum = _pay_sum(yeni_pay)
        if abs(yeni_sum - tutar) > 0.011:
            return {
                "ok": False,
                "neden": "marker_toplam_esit_degil",
                "id": tid,
                "tutar": tutar,
                "yeni_sum": yeni_sum,
                "yeni_pay": yeni_pay,
                "plans": plans,
            }
        # Yalnız PAY dağılımı değişince UPDATE (kozmetik metin farkı sayılmaz).
        degisti = list(eski_pay) != list(yeni_pay)
        plans.append(
            {
                "id": tid,
                "no": r.get("makbuz_no"),
                "tarih": tah,
                "tutar": tutar,
                "eski_pay": eski_pay,
                "yeni_pay": yeni_pay,
                "eski_sum": _pay_sum(eski_pay),
                "yeni_sum": yeni_sum,
                "degisti": degisti,
                "eski_aciklama": eski_ac,
                "yeni_aciklama": yeni_ac,
            }
        )
        batch.append(
            {
                "id": tid,
                "tarih": tah,
                "tutar": tutar,
                "aciklama": yeni_ac,
            }
        )

    return {
        "ok": True,
        "neden": None,
        "mid": mid,
        "plans": plans,
        "n_manuel": len(manuel),
        "n_diger": len(diger),
        "n_degisti": sum(1 for p in plans if p["degisti"]),
        "update_ids": [p["id"] for p in plans if p["degisti"]],
        "diger_ids": [int(r["id"]) for r in diger],
        "borc": {k: round(float(v), 2) for k, v in sorted((borc_map or {}).items())},
    }


def _panel_snapshot(mid: int, fetch_one) -> dict:
    prow = fetch_one(
        "SELECT by_iso, updated_at FROM musteri_tahsilat_panel_detay WHERE musteri_id=%s",
        (int(mid),),
    )
    by_iso = {}
    if prow and prow.get("by_iso"):
        raw = prow["by_iso"]
        by_iso = json.loads(raw) if isinstance(raw, str) else (raw or {})
    grow = fetch_one(
        "SELECT payload, updated_at FROM musteri_aylik_grid_cache WHERE musteri_id=%s",
        (int(mid),),
    )
    payload = {}
    if grow and grow.get("payload"):
        raw = grow["payload"]
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return {
        "by_iso": by_iso or {},
        "payload": payload or {},
        "panel_updated": (prow or {}).get("updated_at"),
        "grid_updated": (grow or {}).get("updated_at"),
    }


def _brut_by_iso(payload: dict, by_iso: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for a in (payload or {}).get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = date(int(a["yil"]), int(a["ay"]), 1).isoformat()
            brut = round(
                float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0), 2
            )
        except Exception:
            continue
        if brut > 0:
            out[iso] = brut
    for iso, cell in (by_iso or {}).items():
        if iso in out:
            continue
        try:
            pa = round(float((cell or {}).get("aylik") or 0), 2)
        except (TypeError, ValueError):
            pa = 0.0
        if pa > 0:
            out[str(iso)[:10]] = pa
    return out


def _beklenen_panel(
    plans: list[dict],
    diger: list[dict],
    brut_by: dict[str, float],
    *,
    yil: int = 2026,
    ay_bas: int = 4,
    ay_bit: int = 12,
) -> dict[str, dict]:
    """Kural A: TAH-only (PAY yok) ay → tahsil=brut. PAY toplamı aksi halde."""
    pay_tot: dict[str, float] = defaultdict(float)
    tah_only: set[str] = set()
    for r in diger:
        ac = str(r.get("aciklama") or "")
        pays = _pay_list(ac)
        tahs = _TAH_RE.findall(ac)
        if pays:
            for iso, p in pays:
                pay_tot[iso] = round(pay_tot[iso] + p, 2)
        elif tahs:
            for iso in tahs:
                tah_only.add(iso)
    for pl in plans:
        for iso, p in pl.get("yeni_pay") or []:
            pay_tot[iso] = round(pay_tot[iso] + float(p), 2)
    out = {}
    for m in range(ay_bas, ay_bit + 1):
        iso = f"{yil}-{m:02d}-01"
        brut = round(float(brut_by.get(iso) or 0), 2)
        if iso in tah_only and iso not in pay_tot:
            tahsil = brut
        else:
            tahsil = round(min(pay_tot.get(iso, 0.0), brut), 2) if brut > 0 else round(
                pay_tot.get(iso, 0.0), 2
            )
        kalan = round(max(brut - tahsil, 0), 2) if brut > 0 else 0.0
        if tahsil <= 0.01:
            durum = "borclu" if brut > 0.01 else "bos"
        elif kalan <= 0.05:
            durum = "odendi"
        else:
            durum = "kismi"
        out[iso] = {
            "aylik": brut,
            "tahsil": tahsil,
            "kalan": kalan,
            "durum": durum,
        }
    return out


def _borc_ozet(by_iso_cells: dict, bugun: date) -> dict:
    kalan = 0.0
    gec = 0
    ay_basi_bugun = bugun.replace(day=1)
    for iso, cell in sorted((by_iso_cells or {}).items()):
        try:
            d = date(int(iso[:4]), int(iso[5:7]), 1)
        except Exception:
            continue
        if d > ay_basi_bugun:
            continue
        try:
            k = round(float((cell or {}).get("kalan") or 0), 2)
        except (TypeError, ValueError):
            k = 0.0
        if k > 0.05:
            kalan = round(kalan + k, 2)
            gec += 1
    return {"vadesi_gelmis_kalan": kalan, "geciken_ay": gec, "bugun": bugun.isoformat()}


def _yedek_yolu(mid: int) -> Path:
    gun = datetime.now().strftime("%Y%m%d")
    desktop = Path.home() / "Desktop"
    return desktop / f"yedek_mid{int(mid)}_marker_{gun}.json"


def _yedek_al(mid: int, rows: list[dict], snap: dict, yol: Path) -> Path:
    govde = {
        "mid": int(mid),
        "alindi": datetime.now().isoformat(timespec="seconds"),
        "tahsilatlar": [
            {
                "id": int(r["id"]),
                "makbuz_no": r.get("makbuz_no"),
                "tarih": r.get("tarih"),
                "tutar": r.get("tutar"),
                "kaynak": r.get("kaynak"),
                "cls": r.get("cls"),
                "aciklama": r.get("aciklama"),
            }
            for r in rows
        ],
        "musteri_tahsilat_panel_detay": {"by_iso": snap.get("by_iso")},
        "musteri_aylik_grid_cache": {"payload": snap.get("payload")},
    }
    yol.write_text(json.dumps(govde, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    return yol


def _audit(mid: int, plans: list[dict], execute) -> None:
    from audit_log import audit_yaz

    ozet = []
    for p in plans:
        if not p.get("degisti"):
            continue
        ozet.append(
            {
                "id": p["id"],
                "eski_pay": p.get("eski_pay"),
                "yeni_pay": p.get("yeni_pay"),
            }
        )
    audit_yaz(
        execute,
        olay="makbuz_dagitim_onar",
        varlik="tahsilatlar",
        kayit_id=int(mid),
        musteri_id=int(mid),
        kullanici="makbuz_dagitim_onar",
        anlik={"mid": int(mid), "n": len(ozet), "ids": [x["id"] for x in ozet]},
        yeni={"ozet": ozet},
    )


def _dogrula_sonrasi(mid: int, plan: dict, bugun: date, fetch_all, fetch_one) -> dict:
    rows = load_tahsilatlar(mid, fetch_all)
    by_id = {int(r["id"]): r for r in rows}
    uyusmaz = []
    for p in plan.get("plans") or []:
        r = by_id.get(int(p["id"]))
        if not r:
            uyusmaz.append({"id": p["id"], "neden": "satir_yok"})
            continue
        got = _pay_list(r.get("aciklama"))
        if got != list(p.get("yeni_pay") or []):
            uyusmaz.append(
                {"id": p["id"], "neden": "marker", "beklenen": p.get("yeni_pay"), "gercek": got}
            )
        if abs(_pay_sum(got) - float(p["tutar"])) > 0.011:
            uyusmaz.append(
                {
                    "id": p["id"],
                    "neden": "toplam",
                    "tutar": p["tutar"],
                    "pay_sum": _pay_sum(got),
                }
            )
    # kapsam dışı değişmedi mi — yedek ile kıyas script tarafında; burada marker sum
    snap = _panel_snapshot(mid, fetch_one)
    brut = _brut_by_iso(snap["payload"], snap["by_iso"])
    bek = _beklenen_panel(
        plan["plans"],
        [r for r in rows if r.get("cls") != "manuel_makbuz"],
        brut,
    )
    panel_fark = []
    for iso, cell in sorted(bek.items()):
        cur = snap["by_iso"].get(iso) or {}
        try:
            ct = round(float(cur.get("tahsil") or 0), 2)
            ck = round(float(cur.get("kalan") or 0), 2)
        except (TypeError, ValueError):
            ct, ck = 0.0, 0.0
        if abs(ct - cell["tahsil"]) > 0.05 or abs(ck - cell["kalan"]) > 0.05:
            panel_fark.append(
                {
                    "ay": iso,
                    "bek_tahsil": cell["tahsil"],
                    "bek_kalan": cell["kalan"],
                    "ger_tahsil": ct,
                    "ger_kalan": ck,
                }
            )
    borc = _borc_ozet(bek, bugun)
    return {
        "ok": not uyusmaz and not panel_fark,
        "uyusmaz_marker": uyusmaz,
        "panel_fark": panel_fark,
        "borc": borc,
        "beklenen_panel": bek,
        "panel_by_iso": {
            iso: {
                "tahsil": round(float((snap["by_iso"].get(iso) or {}).get("tahsil") or 0), 2),
                "kalan": round(float((snap["by_iso"].get(iso) or {}).get("kalan") or 0), 2),
            }
            for iso in sorted(bek)
        },
    }


def _print_dry_run(mid: int, plan: dict, rows: list[dict], snap: dict, bugun: date) -> None:
    print("=== KAPSAM_DISI ===", plan.get("n_diger"))
    for r in rows:
        if r.get("cls") == "manuel_makbuz":
            continue
        _j(
            {
                "id": r["id"],
                "no": r.get("makbuz_no"),
                "td": r.get("tarih"),
                "T": r.get("tutar"),
                "cls": r.get("cls"),
                "pays": _pay_list(r.get("aciklama")),
            }
        )
    print("=== MANUEL eski vs yeni ===")
    for p in plan.get("plans") or []:
        _j(
            {
                "id": p["id"],
                "no": p.get("no"),
                "td": p["tarih"],
                "T": p["tutar"],
                "eski": p["eski_pay"],
                "yeni": p["yeni_pay"],
                "T_eq_yeni": abs(p["tutar"] - p["yeni_sum"]) < 0.011,
                "degisti": p["degisti"],
            }
        )
    brut = _brut_by_iso(snap["payload"], snap["by_iso"])
    bek = _beklenen_panel(
        plan["plans"],
        [r for r in rows if r.get("cls") != "manuel_makbuz"],
        brut,
    )
    print("=== PANEL onceki vs beklenen (2026-04..12) ===")
    for iso, cell in sorted(bek.items()):
        cur = snap["by_iso"].get(iso) or {}
        try:
            pt = round(float(cur.get("tahsil") or 0), 2)
            pk = round(float(cur.get("kalan") or 0), 2)
        except (TypeError, ValueError):
            pt, pk = 0.0, 0.0
        _j(
            {
                "ay": iso,
                "brut": cell["aylik"],
                "panel_tahsil": pt,
                "panel_kalan": pk,
                "bek_tahsil": cell["tahsil"],
                "bek_kalan": cell["kalan"],
                "bek_durum": cell["durum"],
                "fark_tahsil": round(pt - cell["tahsil"], 2),
                "fark_kalan": round(pk - cell["kalan"], 2),
            }
        )
    print("=== BORC ===")
    _j({"mevcut": _borc_ozet(snap["by_iso"], bugun), "beklenen": _borc_ozet(bek, bugun)})
    print("=== UPDATE PLANI ===")
    _j(
        {
            "mid": mid,
            "n_update": plan.get("n_degisti"),
            "ids": plan.get("update_ids"),
            "tahsilat_degisti": f"tahsilat_degisti({mid})",
            "yedek": str(_yedek_yolu(mid)),
        }
    )


def _hedef_872_uyum(plan: dict) -> dict:
    hedef = {
        5274: [("2026-07-01", 51799.00)],
        5525: [("2026-07-01", 0.80), ("2026-08-01", 51799.00)],
        5584: [("2026-08-01", 0.80), ("2026-09-01", 51799.00)],
        6063: [("2026-09-01", 0.80), ("2026-10-01", 51799.00)],
    }
    sonuc = []
    hepsi = True
    for p in plan.get("plans") or []:
        tid = int(p["id"])
        if tid not in hedef:
            continue
        ok = list(p.get("yeni_pay") or []) == hedef[tid]
        if not ok:
            hepsi = False
        sonuc.append(
            {"id": tid, "beklenen": hedef[tid], "hesaplanan": p.get("yeni_pay"), "uyum": ok}
        )
    # Kasım spill yok
    kasim = False
    for p in plan.get("plans") or []:
        for iso, _ in p.get("yeni_pay") or []:
            if str(iso).startswith("2026-11"):
                kasim = True
    return {"hepsi_uyum": hepsi and not kasim, "kasim_tasima": kasim, "satirlar": sonuc}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="manuel_makbuz dağıtım onarımı (tam FIFO)")
    ap.add_argument("--mid", type=int, required=True, help="tek musteri_id (zorunlu)")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="yaz; yalnız ALLOW_PROD_WRITE + --mid ile",
    )
    ap.add_argument(
        "--bugun",
        default="2026-10-30",
        help="vadesi gelmiş borç hesabı için tarih YYYY-MM-DD",
    )
    args = ap.parse_args(argv)

    mid = int(args.mid)
    if mid <= 0:
        print("HATA mid gecersiz")
        return 2

    if args.apply and not _prod_write_ok():
        print("HATA: --apply icin ALLOW_PROD_WRITE gerekli")
        return 2

    # Dry-run'da prod write kapalı kalsın
    if not args.apply:
        os.environ.pop("ALLOW_PROD_WRITE", None)

    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    from flask import Flask

    app = Flask("makbuz_dagitim_onar")
    app.app_context().push()
    from db import execute, fetch_all, fetch_one
    import db as dbmod

    bugun = date.fromisoformat(str(args.bugun)[:10])

    if not args.apply:
        with dbmod.db() as conn:
            conn.cursor().execute("SET LOCAL transaction_read_only = on")

    rows = load_tahsilatlar(mid, fetch_all)
    plan = hesapla_plan(mid, rows)
    if not plan.get("ok"):
        print("DUR", plan.get("neden"), {k: plan.get(k) for k in ("id", "tutar", "yeni_sum")})
        return 1

    snap = _panel_snapshot(mid, fetch_one)
    print("=== DRY-RUN mid=%s apply=%s ===" % (mid, bool(args.apply)))
    _print_dry_run(mid, plan, rows, snap, bugun)

    if mid == 872:
        print("=== HEDEF mid=872 ===")
        _j(_hedef_872_uyum(plan))

    if not args.apply:
        print("DRY-RUN tamam (yazilmadi). n_update=%s" % plan.get("n_degisti"))
        return 0

    # APPLY
    yol = _yedek_yolu(mid)
    _yedek_al(mid, rows, snap, yol)
    print("YEDEK", str(yol))

    from routes.faturalar_routes import (
        makbuz_aciklama_sadece_guncelle,
        yeniden_hesapla_makbuz_dagitim_aciklama,
    )
    from routes.giris_routes import tahsilat_degisti

    # Sıralı: her adımda DB onceki güncel; tam_fifo (allowlist yok)
    for p in plan["plans"]:
        if not p.get("degisti"):
            continue
        tid = int(p["id"])
        row = next(r for r in rows if int(r["id"]) == tid)
        yeni = yeniden_hesapla_makbuz_dagitim_aciklama(
            mid,
            tutar=float(p["tutar"]),
            tahsilat_tarihi=p["tarih"],
            aciklama_eski=row.get("aciklama"),
            haric_tahsilat_id=tid,
            kaynak="manuel_makbuz",
            odeme_turu=row.get("odeme_turu"),
            tam_fifo=True,
        )
        if yeni is None:
            print("DUR yeniden_hesapla_none id=%s" % tid)
            return 1
        if abs(_pay_sum(_pay_list(yeni)) - float(p["tutar"])) > 0.011:
            print("DUR marker_toplam id=%s" % tid)
            return 1
        if list(_pay_list(yeni)) != list(p.get("yeni_pay") or []):
            print("DUR plan_sapma id=%s" % tid)
            _j({"beklenen": p.get("yeni_pay"), "canli": _pay_list(yeni)})
            return 1
        if not makbuz_aciklama_sadece_guncelle(tid, yeni):
            print("DUR update_fail id=%s" % tid)
            return 1
        # sonraki tur için rows aciklama güncelle
        row["aciklama"] = yeni

    _audit(mid, plan["plans"], execute)
    tahsilat_degisti(mid)

    dog = _dogrula_sonrasi(mid, plan, bugun, fetch_all, fetch_one)
    print("=== DOGRULAMA ===")
    _j(
        {
            "ok": dog.get("ok"),
            "uyusmaz_marker": dog.get("uyusmaz_marker"),
            "panel_fark": dog.get("panel_fark"),
            "borc": dog.get("borc"),
        }
    )
    if not dog.get("ok"):
        print("DUR dogrulama uyusmazligi (self-repair yok)")
        return 1
    print("APPLY OK mid=%s n_update=%s" % (mid, plan.get("n_degisti")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
