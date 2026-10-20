# -*- coding: utf-8 -*-
"""Kayan tahsilat yeniden dağıtımı — tek giriş noktası.

Silme sonrası "arkada açık ay, ilerisi kapalı" örüntüsünde, ay seçimli
(SABİT) kayıtlara dokunmadan KAYAN kayıtları kronolojik FIFO ile yeniden
dağıtır. Yalnız aciklama marker'ları yazılır; tutar/tarih/makbuz/ref/kaynak
DEĞİŞMEZ.

SABİT: grid_toplu + TAH-only / tek-ay TAH kayıtlar + kayan kaynak listesinde
       olmayan PAY'li kayıtlar.
KAYAN: banka_import / manuel_makbuz / odeme_linki kaynaklı PAY'li kayıtlar.

Bayrak (API/UI):
  KAYAN_YENIDEN_DAGIT_ENABLED=0 (varsayılan kapalı)
  KAYAN_YENIDEN_DAGIT_MUSTERI_IDS (ayarlı değilse ilk allowlist: 867,153)

Varsayılan DRY-RUN. Yazma: ALLOW_PROD_WRITE (CLI) veya izinli_yazma (API,
bayrak+yetki sonrası) veya calistir enjeksiyonu (test).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from typing import Any

from ay_fifo import acik_ay_dagit, borc_haritasi
from banka_marker_onarim import (
    SQL_KILIT,
    YEDEK_TABLO,
    _build_aciklama,
    _pay_list,
    _pay_map,
    _sync_panel_grid_after,
    ensure_tahsilat_marker_yedek,
)

_TAH_RE = re.compile(r"\|AYLIK_TAH\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|")
TOL = 0.02

SABIT_KAYNAK_DEFAULT = ("grid_toplu",)
KAYAN_KAYNAK_DEFAULT = ("banka_import", "manuel_makbuz", "odeme_linki")

# 1-worker / 2 thread: tek istekte plan maliyeti satır sayısıyla doğrusal.
# Aşılırsa yazma yok, dürüst mesaj (CLI da aynı sınırı kullanır).
MAX_TAHSILAT = 500
MAX_KAYAN = 500


def _prod_write_ok() -> bool:
    return str(os.environ.get("ALLOW_PROD_WRITE") or "").strip() in (
        "1", "true", "TRUE", "yes",
    )


# ---------------------------------------------------------------- bayrak

def kayan_yeniden_dagit_enabled() -> bool:
    return str(os.environ.get("KAYAN_YENIDEN_DAGIT_ENABLED") or "").strip() in (
        "1", "true", "TRUE", "yes",
    )


def kayan_yeniden_dagit_allowlist() -> set[int]:
    raw = os.environ.get("KAYAN_YENIDEN_DAGIT_MUSTERI_IDS")
    if raw is None:
        return {867, 153}
    out: set[int] = set()
    for p in str(raw).replace(";", ",").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            out.add(int(p))
        except ValueError:
            continue
    return out


def kayan_yeniden_dagit_aktif(musteri_id) -> bool:
    if not kayan_yeniden_dagit_enabled():
        return False
    try:
        mid = int(musteri_id or 0)
    except (TypeError, ValueError):
        return False
    if mid <= 0:
        return False
    return mid in kayan_yeniden_dagit_allowlist()


# ---------------------------------------------------------------- yardımcı

def _paid_map_rows(rows) -> dict[str, float]:
    """PAY toplamı; PAY'siz tek TAH → tutar o ayda (sabit kayıt gösterimi)."""
    out: dict[str, float] = defaultdict(float)
    for r in rows or []:
        ac = str(r.get("aciklama") or "")
        pays = _pay_list(ac)
        if pays:
            for iso, v in pays:
                out[iso] = round(out[iso] + v, 2)
        else:
            tahs = _TAH_RE.findall(ac)
            if len(tahs) == 1:
                try:
                    out[tahs[0]] = round(out[tahs[0]] + float(r.get("tutar") or 0), 2)
                except (TypeError, ValueError):
                    pass
    return dict(sorted(out.items()))


def sinifla(
    row: dict,
    *,
    sabit_kaynak=SABIT_KAYNAK_DEFAULT,
    kayan_kaynak=KAYAN_KAYNAK_DEFAULT,
) -> str:
    """"sabit" | "kayan". Yalnız kayan kaynak + PAY → kayan."""
    kay = str(row.get("kaynak") or "")
    if kay in tuple(sabit_kaynak or ()):
        return "sabit"
    pays = _pay_list(str(row.get("aciklama") or ""))
    if kay in tuple(kayan_kaynak or ()) and pays:
        return "kayan"
    # TAH-only / tek-ay TAH / markersiz / kayan listede olmayan PAY → sabit
    return "sabit"


def bosluk_bul(borc: dict, paid: dict) -> list[dict]:
    """Arkada açık (kalan>tol), ileride kapalı ay örüntüsü."""
    gaps: list[dict] = []
    isos = sorted(k for k, v in (borc or {}).items() if float(v or 0) > TOL)
    for i, iso in enumerate(isos):
        b = round(float(borc.get(iso) or 0), 2)
        p = round(float(paid.get(iso) or 0), 2)
        kalan = round(max(b - p, 0.0), 2)
        if kalan <= TOL:
            continue
        for iso2 in isos[i + 1:]:
            b2 = round(float(borc.get(iso2) or 0), 2)
            p2 = round(float(paid.get(iso2) or 0), 2)
            if b2 > TOL and round(max(b2 - p2, 0.0), 2) <= TOL:
                gaps.append({"ay": iso, "kalan": kalan, "sonraki_kapali": iso2})
                break
    return gaps


def _son_kismi(borc: dict, paid: dict) -> dict | None:
    """En son kısmi ödenmiş ay (0 < ödenen < brüt). Yoksa en son açık ay."""
    isos = sorted(k for k, v in (borc or {}).items() if float(v or 0) > TOL)
    son_acik = None
    for iso in reversed(isos):
        b = round(float(borc.get(iso) or 0), 2)
        p = round(float(paid.get(iso) or 0), 2)
        kalan = round(max(b - p, 0.0), 2)
        if kalan <= TOL:
            continue
        if son_acik is None:
            son_acik = {"ay": iso, "odenen": p, "kalan": kalan, "brut": b}
        if 0 < p < b - TOL:
            return {"ay": iso, "odenen": p, "kalan": kalan, "brut": b}
    return son_acik


def plan_parmak_izi(mid: int, plans: list[dict]) -> str:
    src = []
    for p in plans or []:
        pays = tuple(
            (iso, round(float(v), 2))
            for iso, v in sorted((p.get("yeni_pay_map") or {}).items())
        )
        src.append((int(p["id"]), pays))
    raw = json.dumps(
        {"mid": int(mid), "plans": src}, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------- plan

def hesapla_plan_kayan(
    mid: int,
    rows: list[dict],
    *,
    borc: dict | None = None,
    sabit_kaynak=SABIT_KAYNAK_DEFAULT,
    kayan_kaynak=KAYAN_KAYNAK_DEFAULT,
    sozlesme_basi: str | None = None,
) -> dict:
    """Saf hesap. rows: mid'in TÜM pozitif tahsilatları."""
    mid = int(mid)
    rows = list(rows or [])
    if not rows:
        return {"ok": False, "neden": "bos_rows", "plans": []}
    mids = {int(r["mid"]) for r in rows if r.get("mid") is not None}
    if mids and (len(mids) != 1 or mid not in mids):
        return {"ok": False, "neden": "coklu_veya_yanlis_mid", "plans": []}
    if len(rows) > MAX_TAHSILAT:
        return {
            "ok": False,
            "neden": "sinir_asildi",
            "mesaj": (
                f"Bu müşteri için {len(rows)} tahsilat kaydı var; yeniden dağıtım "
                f"sınırı {MAX_TAHSILAT}. İşlem yapılmadı."
            ),
            "plans": [],
        }

    sabit: list[dict] = []
    kayan: list[dict] = []
    for r in rows:
        if sinifla(r, sabit_kaynak=sabit_kaynak, kayan_kaynak=kayan_kaynak) == "kayan":
            kayan.append(r)
        else:
            sabit.append(r)
    if len(kayan) > MAX_KAYAN:
        return {
            "ok": False, "neden": "sinir_asildi",
            "mesaj": f"Kayan kayıt sayısı {len(kayan)}; sınır {MAX_KAYAN}. İşlem yapılmadı.",
            "plans": [],
        }

    if borc is not None:
        borc_map = borc if isinstance(borc, dict) else borc_haritasi(borc)
        taban = sozlesme_basi
    else:
        from routes.faturalar_routes import _gorunen_borc_satirlari, _sozlesme_ay_basi

        borc_map = _gorunen_borc_satirlari(mid)
        if not isinstance(borc_map, dict):
            borc_map = borc_haritasi(borc_map)
        taban = sozlesme_basi if sozlesme_basi is not None else _sozlesme_ay_basi(mid)
    borc_map = dict(borc_haritasi(borc_map))

    sabit_onceki = [
        {
            "id": int(r["id"]),
            "tarih": str(r.get("tarih") or "")[:10],
            "tutar": float(r.get("tutar") or 0),
            "aciklama": r.get("aciklama") or "",
        }
        for r in sorted(sabit, key=lambda x: (str(x.get("tarih") or ""), int(x["id"])))
    ]

    kayan_s = sorted(
        kayan, key=lambda x: (str(x.get("tarih") or "")[:10], int(x["id"]))
    )
    batch: list[dict] = []
    plans: list[dict] = []
    for r in kayan_s:
        tid = int(r["id"])
        tah = str(r.get("tarih") or "")[:10]
        tutar = round(float(r.get("tutar") or 0), 2)
        eski_ac = r.get("aciklama") or ""
        onceki = list(sabit_onceki) + batch
        yeni_pay = acik_ay_dagit(
            borc_map, tutar, tah, onceki=onceki, sozlesme_basi=taban
        )
        yeni_pay = list(yeni_pay or [])
        yeni_ac = _build_aciklama(eski_ac, yeni_pay)
        plans.append({
            "id": tid,
            "mid": mid,
            "tarih": tah,
            "tutar": tutar,
            "makbuz_no": r.get("makbuz_no"),
            "banka_referans_no": r.get("banka_referans_no"),
            "kaynak": r.get("kaynak"),
            "odeme_turu": r.get("odeme_turu"),
            "eski_aciklama": eski_ac,
            "yeni_aciklama": yeni_ac,
            "eski_pay": _pay_list(eski_ac),
            "yeni_pay": yeni_pay,
            "eski_pay_map": _pay_map(eski_ac),
            "yeni_pay_map": {iso: float(v) for iso, v in yeni_pay},
            "degisti": (eski_ac or "").strip() != (yeni_ac or "").strip(),
        })
        batch.append({
            "id": tid,
            "tarih": tah,
            "tutar": tutar,
            "aciklama": yeni_ac,
            "pay": list(yeni_pay),
        })

    odeme_toplam = round(sum(float(r.get("tutar") or 0) for r in kayan_s), 2)
    dagitim_toplam = round(
        sum(sum(float(v) for _, v in p["yeni_pay"]) for p in plans), 2
    )
    hatalar: list[str] = []
    if kayan_s and abs(odeme_toplam - dagitim_toplam) > TOL:
        hatalar.append(f"dagitim_toplam {dagitim_toplam} != odeme {odeme_toplam}")
    for p in plans:
        for iso, v in p["yeni_pay_map"].items():
            cap = round(float(borc_map.get(iso) or 0), 2)
            # ay bazında toplam aşım aşağıda toplu kontrol edilir
            _ = (iso, v, cap)

    eski_paid = _paid_map_rows(rows)
    yeni_rows = list(sabit) + [
        {**next(r for r in kayan_s if int(r["id"]) == p["id"]),
         "aciklama": p["yeni_aciklama"]}
        for p in plans
    ]
    yeni_paid = _paid_map_rows(yeni_rows)

    # Ay toplam aşımı (yeni)
    for iso, v in yeni_paid.items():
        cap = round(float(borc_map.get(iso) or 0), 2)
        if cap > 0 and v > cap + TOL:
            hatalar.append(f"ay_asim {iso}: {v}>{cap}")

    ay_degisim = []
    for iso in sorted(set(eski_paid) | set(yeni_paid)):
        e = round(float(eski_paid.get(iso) or 0), 2)
        y = round(float(yeni_paid.get(iso) or 0), 2)
        if abs(e - y) > TOL:
            ay_degisim.append({"ay": iso, "eski": e, "yeni": y})

    return {
        "ok": not hatalar,
        "neden": None if not hatalar else "invariant",
        "invariant_hatalar": hatalar,
        "mid": mid,
        "plans": plans,
        "n_degisti": sum(1 for p in plans if p["degisti"]),
        "sabit_n": len(sabit),
        "kayan_n": len(kayan_s),
        "sabit_ids": [int(r["id"]) for r in sabit],
        "kayan_ids": [int(r["id"]) for r in kayan_s],
        "odeme_toplam": odeme_toplam,
        "dagitim_toplam": dagitim_toplam,
        "eski_ay_toplam": eski_paid,
        "yeni_ay_toplam": yeni_paid,
        "ay_degisim": ay_degisim,
        "bosluk_eski": bosluk_bul(borc_map, eski_paid),
        "bosluk_yeni": bosluk_bul(borc_map, yeni_paid),
        "son_kismi_yeni": _son_kismi(borc_map, yeni_paid),
        "borc": borc_map,
    }


def _load_all_rows(mid: int, oku) -> list[dict]:
    rows = oku(
        """
        SELECT id,
               COALESCE(musteri_id, customer_id) AS mid,
               ROUND(COALESCE(tutar, 0)::numeric, 2) AS tutar,
               LEFT(tahsilat_tarihi::text, 10) AS tarih,
               COALESCE(aciklama, '') AS aciklama,
               makbuz_no,
               banka_referans_no,
               COALESCE(kaynak, '') AS kaynak,
               odeme_turu
        FROM tahsilatlar
        WHERE COALESCE(musteri_id, customer_id) = %s
          AND COALESCE(tutar, 0) > 0
        ORDER BY LEFT(tahsilat_tarihi::text, 10), id
        """,
        (int(mid),),
    ) or []
    out = []
    for r in rows:
        if int(r["mid"]) != int(mid):
            raise RuntimeError(f"coklu_mid id={r.get('id')}")
        out.append(r)
    return out


def _plan_ozet(plan: dict, fp: str) -> dict:
    return {
        "ok": plan.get("ok"),
        "neden": plan.get("neden"),
        "mesaj": plan.get("mesaj"),
        "invariant_hatalar": plan.get("invariant_hatalar"),
        "n_degisti": plan.get("n_degisti"),
        "sabit_n": plan.get("sabit_n"),
        "kayan_n": plan.get("kayan_n"),
        "odeme_toplam": plan.get("odeme_toplam"),
        "dagitim_toplam": plan.get("dagitim_toplam"),
        "ay_degisim": plan.get("ay_degisim"),
        "bosluk_eski": plan.get("bosluk_eski"),
        "bosluk_yeni": plan.get("bosluk_yeni"),
        "son_kismi_yeni": plan.get("son_kismi_yeni"),
        "parmak_izi": fp,
        "satirlar": [
            {
                "id": p["id"],
                "tarih": p["tarih"],
                "tutar": p["tutar"],
                "kaynak": p.get("kaynak"),
                "eski_pay": p["eski_pay_map"],
                "yeni_pay": p["yeni_pay_map"],
                "degisti": p["degisti"],
            }
            for p in plan.get("plans") or []
        ],
    }


# ---------------------------------------------------------------- ana giriş

def tahsilat_kayan_yeniden_dagit(
    mid: int,
    *,
    dry_run: bool = True,
    etiket: str,
    tetik: str = "buton",
    sabit_kaynak=SABIT_KAYNAK_DEFAULT,
    kayan_kaynak=KAYAN_KAYNAK_DEFAULT,
    beklenen_fp: str | None = None,
    oku=None,
    calistir=None,
    borc: dict | None = None,
    rows: list[dict] | None = None,
    force_fail: bool = False,
    izinli_yazma: bool = False,
    sync_after: bool = True,
) -> dict:
    """Tek giriş noktası. dry_run → yazma yok."""
    mid = int(mid)
    out: dict[str, Any] = {
        "mid": mid,
        "dry_run": bool(dry_run),
        "etiket": etiket,
        "tetik": tetik,
        "yazildi": False,
        "degisiklik_yok": False,
    }
    if tetik not in ("buton", "silme", "cli"):
        out.update({"ok": False, "neden": "tetik_gecersiz"})
        return out

    if rows is not None:
        tum = list(rows)
    else:
        if oku is None:
            from db import fetch_all

            def oku(sql, params=None):  # noqa: F811
                return fetch_all(sql, params)
        try:
            tum = _load_all_rows(mid, oku)
        except Exception as ex:
            return {**out, "ok": False, "neden": f"okuma:{type(ex).__name__}",
                    "mesaj": "Kayıtlar okunamadı."}

    if not tum:
        out.update({
            "ok": True, "degisiklik_yok": True,
            "mesaj": "Tahsilat kaydı yok.",
            "plan": {"n_degisti": 0, "kayan_n": 0, "sabit_n": 0, "parmak_izi": plan_parmak_izi(mid, [])},
        })
        return out

    plan = hesapla_plan_kayan(
        mid, tum, borc=borc,
        sabit_kaynak=sabit_kaynak, kayan_kaynak=kayan_kaynak,
    )
    fp = plan_parmak_izi(mid, plan.get("plans") or [])
    out["plan"] = _plan_ozet(plan, fp)
    out["parmak_izi"] = fp
    if not plan.get("ok"):
        out["ok"] = False
        out["neden"] = plan.get("neden") or "plan_hata"
        if plan.get("mesaj"):
            out["mesaj"] = plan["mesaj"]
        return out

    if plan.get("n_degisti", 0) == 0:
        out["ok"] = True
        out["degisiklik_yok"] = True
        out["mesaj"] = "Değişiklik yok; ödemeler zaten en eski açık aydan dağılmış."
        return out

    if dry_run:
        out["ok"] = True
        out["mesaj"] = "Önizleme; yazılmadı."
        return out

    if beklenen_fp and str(beklenen_fp).strip() != fp:
        out["ok"] = False
        out["neden"] = "plan_degisti"
        out["mesaj"] = "Önizlemeden sonra kayıtlar değişti. Lütfen yeniden önizleyin."
        return out

    yaz = izinli_yazma or (calistir is not None) or _prod_write_ok()
    if not yaz:
        out["ok"] = True
        out["yazildi"] = False
        out["mesaj"] = "Yazma izni yok (ALLOW_PROD_WRITE / API yetkisi) — yazılmadı."
        return out

    if calistir is not None:
        return _apply_calistir(mid, plan, etiket, tetik, calistir, out, force_fail=force_fail)

    # --- canlı DB ---
    from audit_log import audit_yaz
    from db import db

    try:
        with db() as conn:
            cur = conn.cursor()

            def _cal(sql, params=None):
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)
                return cur

            ensure_tahsilat_marker_yedek(lambda sql, params=None: _cal(sql, params))
            cur.execute(SQL_KILIT, (f"tahsilat_kayan:{mid}",))
            if force_fail:
                raise RuntimeError("force_fail")
            for p in plan["plans"]:
                if not p["degisti"]:
                    continue
                cur.execute(
                    f"""
                    INSERT INTO {YEDEK_TABLO}
                        (tahsilat_id, mid, eski_aciklama, eski_marker_json,
                         yeni_aciklama, etiket, islem_tamam)
                    VALUES (%s, %s, %s, %s, %s, %s, FALSE)
                    RETURNING id
                    """,
                    (
                        p["id"], mid, p["eski_aciklama"],
                        json.dumps(p["eski_pay_map"], ensure_ascii=False),
                        p["yeni_aciklama"], etiket,
                    ),
                )
                yid = (cur.fetchone() or {}).get("id")
                cur.execute(
                    """
                    UPDATE tahsilatlar
                    SET aciklama = %s
                    WHERE id = %s
                      AND COALESCE(musteri_id, customer_id) = %s
                      AND COALESCE(kaynak, '') = %s
                    """,
                    (p["yeni_aciklama"], p["id"], mid, str(p.get("kaynak") or "")),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"update_rowcount id={p['id']} n={cur.rowcount}")
                cur.execute(
                    """
                    SELECT ROUND(COALESCE(tutar,0)::numeric,2) AS tutar,
                           LEFT(tahsilat_tarihi::text,10) AS tarih,
                           banka_referans_no, makbuz_no, kaynak, odeme_turu
                    FROM tahsilatlar WHERE id = %s
                    """,
                    (p["id"],),
                )
                chk = cur.fetchone() or {}
                if round(float(chk.get("tutar") or 0), 2) != p["tutar"]:
                    raise RuntimeError("tutar_degisti")
                if str(chk.get("tarih") or "")[:10] != p["tarih"]:
                    raise RuntimeError("tarih_degisti")
                if str(chk.get("banka_referans_no") or "") != str(p.get("banka_referans_no") or ""):
                    raise RuntimeError("ref_degisti")
                if str(chk.get("makbuz_no") or "") != str(p.get("makbuz_no") or ""):
                    raise RuntimeError("makbuz_degisti")
                if str(chk.get("kaynak") or "") != str(p.get("kaynak") or ""):
                    raise RuntimeError("kaynak_degisti")
                audit_yaz(
                    (lambda sql, params=None: _cal(sql, params)),
                    olay="tahsilat_kayan_yeniden_dagit",
                    varlik="tahsilatlar",
                    kayit_id=p["id"],
                    musteri_id=mid,
                    kullanici=f"tahsilat_kayan:{tetik}",
                    anlik={"etiket": etiket, "eski_pay": p["eski_pay_map"], "yedek_id": yid},
                    yeni={"yeni_pay": p["yeni_pay_map"], "etiket": etiket, "parmak_izi": fp},
                )
                cur.execute(
                    f"UPDATE {YEDEK_TABLO} SET islem_tamam = TRUE WHERE id = %s",
                    (yid,),
                )
        sync = _sync_panel_grid_after(mid) if sync_after else {"skipped": True, "reason": "sync_after_kapali"}
        out["ok"] = True
        out["yazildi"] = True
        out["panel_sync"] = sync
        return out
    except Exception as ex:
        out["ok"] = False
        out["yazildi"] = False
        out["neden"] = type(ex).__name__
        out["mesaj"] = "Hiçbir değişiklik kaydedilmedi."
        out["hata"] = str(ex)[:200]
        return out


def _apply_calistir(mid, plan, etiket, tetik, calistir, out, *, force_fail=False) -> dict:
    from audit_log import audit_yaz

    try:
        ensure_tahsilat_marker_yedek(calistir)
        calistir(SQL_KILIT, (f"tahsilat_kayan:{mid}",))
        if force_fail:
            raise RuntimeError("force_fail")
        for p in plan["plans"]:
            if not p["degisti"]:
                continue
            calistir(
                f"""INSERT INTO {YEDEK_TABLO}
                    (tahsilat_id, mid, eski_aciklama, eski_marker_json,
                     yeni_aciklama, etiket, islem_tamam)
                VALUES (%s, %s, %s, %s, %s, %s, FALSE)""",
                (
                    p["id"], mid, p["eski_aciklama"],
                    json.dumps(p["eski_pay_map"], ensure_ascii=False),
                    p["yeni_aciklama"], etiket,
                ),
            )
            calistir(
                "UPDATE tahsilatlar SET aciklama = %s WHERE id = %s "
                "AND COALESCE(musteri_id, customer_id) = %s "
                "AND COALESCE(kaynak, '') = %s",
                (p["yeni_aciklama"], p["id"], mid, str(p.get("kaynak") or "")),
            )
            audit_yaz(
                calistir,
                olay="tahsilat_kayan_yeniden_dagit",
                varlik="tahsilatlar",
                kayit_id=p["id"],
                musteri_id=mid,
                kullanici=f"tahsilat_kayan:{tetik}",
                anlik={"etiket": etiket, "eski_pay": p["eski_pay_map"]},
                yeni={"yeni_pay": p["yeni_pay_map"], "etiket": etiket},
            )
            calistir(
                f"UPDATE {YEDEK_TABLO} SET islem_tamam = TRUE "
                "WHERE tahsilat_id = %s AND etiket = %s",
                (p["id"], etiket),
            )
        out["ok"] = True
        out["yazildi"] = True
        return out
    except Exception as ex:
        out["ok"] = False
        out["yazildi"] = False
        out["neden"] = type(ex).__name__
        out["mesaj"] = "Hiçbir değişiklik kaydedilmedi."
        out["hata"] = str(ex)[:200]
        return out


def geri_yukle_kayan(mid: int, etiket: str, *, oku=None, calistir=None) -> dict:
    """Yedekten (tahsilat_marker_yedek) eski aciklama'yı geri yazar.

    Kaynak kısıtı: yalnız bu etiketle yedeklenmiş satırlar; mid eşleşmesi şart.
    """
    mid = int(mid)
    if not _prod_write_ok() and calistir is None:
        return {"mid": mid, "ok": False, "neden": "ALLOW_PROD_WRITE yok"}
    if oku is None:
        from db import fetch_all

        def oku(sql, params=None):  # noqa: F811
            return fetch_all(sql, params)

    rows = oku(
        f"""
        SELECT id, tahsilat_id, eski_aciklama
        FROM {YEDEK_TABLO}
        WHERE mid = %s AND etiket = %s AND COALESCE(geri_yazildi, FALSE) = FALSE
        ORDER BY id ASC
        """,
        (mid, etiket),
    ) or []
    if not rows:
        return {"mid": mid, "ok": False, "neden": "yedek yok"}

    if calistir is not None:
        try:
            for y in rows:
                calistir(
                    "UPDATE tahsilatlar SET aciklama = %s WHERE id = %s "
                    "AND COALESCE(musteri_id, customer_id) = %s",
                    (y["eski_aciklama"], y["tahsilat_id"], mid),
                )
                calistir(
                    f"UPDATE {YEDEK_TABLO} SET geri_yazildi = TRUE WHERE id = %s",
                    (y["id"],),
                )
            return {"mid": mid, "ok": True, "n": len(rows), "etiket": etiket}
        except Exception as ex:
            return {
                "mid": mid, "ok": False,
                "mesaj": "Hiçbir değişiklik kaydedilmedi.",
                "hata": str(ex)[:200],
            }

    from db import db

    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute(SQL_KILIT, (f"tahsilat_kayan:{mid}",))
            for y in rows:
                cur.execute(
                    """
                    UPDATE tahsilatlar SET aciklama = %s
                    WHERE id = %s AND COALESCE(musteri_id, customer_id) = %s
                    """,
                    (y["eski_aciklama"], y["tahsilat_id"], mid),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"geri_update id={y['tahsilat_id']}")
                cur.execute(
                    f"UPDATE {YEDEK_TABLO} SET geri_yazildi = TRUE WHERE id = %s",
                    (y["id"],),
                )
        try:
            _sync_panel_grid_after(mid)
        except Exception:
            pass
        return {"mid": mid, "ok": True, "n": len(rows), "etiket": etiket}
    except Exception as ex:
        return {
            "mid": mid, "ok": False,
            "mesaj": "Hiçbir değişiklik kaydedilmedi.",
            "hata": str(ex)[:200],
        }
