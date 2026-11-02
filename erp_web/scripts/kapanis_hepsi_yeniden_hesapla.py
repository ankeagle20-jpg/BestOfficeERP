# -*- coding: utf-8 -*-
"""Hepsi (N=NULL) sözleşme-yılı bayrağı sonrası grid/panel/ekstre yeniden hesap.

Varsayılan: DRY-RUN (yazmaz).
--apply yalnız ALLOW_PROD_WRITE tanımlıysa yazar.
Mid listesi --mids-file (satır başına mid) veya --mid.
Her mid: tahsilat_degisti(mid) → grid + panel by_iso + ekstre bellek.
Repo dışı yedek (Masaüstü JSON). PII yok (yalnız mid / sayı).

--sadece-kirp: rebuild YOK; disk grid sağlıklıysa _aylik_grid_trim_payload_to_son_borc
ile B2 sınırına kırp. Panel/tahsilat/marker dokunulmaz. Varsayılan kapalı.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TOL = 0.05


def _prod_write_ok() -> bool:
    return (os.environ.get("ALLOW_PROD_WRITE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _j(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, default=str))


def _parse_mids(args) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    if args.mid:
        for part in str(args.mid).split(","):
            part = part.strip()
            if not part:
                continue
            mid = int(part)
            if mid > 0 and mid not in seen:
                seen.add(mid)
                out.append(mid)
    if args.mids_file:
        p = Path(args.mids_file)
        if not p.is_file():
            raise SystemExit("HATA: mids-file yok: %s" % p)
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip().lstrip("\ufeff")
            if not s or s.startswith("#"):
                continue
            mid = int(s.split()[0])
            if mid > 0 and mid not in seen:
                seen.add(mid)
                out.append(mid)
    return out


def _yedek_yolu(tag: str) -> Path:
    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        desktop = Path.home()
    gun = date.today().isoformat()
    return desktop / f"yedek_kapanis_hepsi_{tag}_{gun}.json"


def _son_iso(son) -> str | None:
    if son is None:
        return None
    try:
        return son.isoformat()
    except Exception:
        return str(son)[:10] or None


def _audit_mid(execute, mid: int, anlik: dict, yeni: dict) -> bool:
    """Mid başına tek audit satırı. Hata yutulur (False)."""
    from audit_log import audit_yaz

    return bool(
        audit_yaz(
            execute,
            olay="kapanis_hepsi_yeniden_hesapla",
            varlik="musteri_aylik_grid_cache",
            kayit_id=int(mid),
            musteri_id=int(mid),
            kullanici="kapanis_hepsi_yeniden_hesapla",
            anlik=dict(anlik or {}),
            yeni=dict(yeni or {}),
        )
    )


def _parse_payload(raw) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None
    return None


def _coerce_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _ay1(d: date) -> date:
    return date(d.year, d.month, 1)


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _ay_cur(a: dict) -> date | None:
    try:
        return date(int(a.get("yil")), int(a.get("ay")), 1)
    except (TypeError, ValueError):
        return None


def _payload_brut_n_sum(payload: dict | None) -> tuple[int, float]:
    if not isinstance(payload, dict):
        return 0, 0.0
    n, s = 0, 0.0
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        if _ay_cur(a) is None:
            continue
        t = _f(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil"))
        n += 1
        s = round(s + t, 2)
    return n, s


def _pre_kap_rows(
    payload: dict | None, kap: date | None, *, kap_dahil: bool = True
) -> list[tuple]:
    """Kapanış öncesi satırlar: varsayılan kap ayı dahil; kap_dahil=False → kap ayı hariç."""
    if not isinstance(payload, dict) or not kap:
        return []
    sinir = _ay1(kap)
    rows = []
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        cur = _ay_cur(a)
        if cur is None:
            continue
        if kap_dahil:
            if cur > sinir:
                continue
        else:
            if cur >= sinir:
                continue
        brut = _f(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil"))
        odenen = _f(a.get("odenen_tutar_kdv"))
        kalan_raw = a.get("kalan_tutar_kdv")
        kalan = _f(kalan_raw) if kalan_raw is not None else round(brut - odenen, 2)
        te = bool(a.get("tahsil_edildi"))
        rows.append((cur.isoformat(), brut, odenen, kalan, te))
    rows.sort(key=lambda x: x[0])
    return rows


def _pre_kap_ay_tl(
    payload: dict | None, kap: date | None, *, kap_dahil: bool = True
) -> tuple[int, float]:
    """Kapanış öncesi borçlu ay/TL. Varsayılan kap ayı dahil; kap_dahil=False → hariç."""
    if not isinstance(payload, dict) or not kap:
        return 0, 0.0
    sinir = _ay1(kap)
    n, tl = 0, 0.0
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        cur = _ay_cur(a)
        if cur is None:
            continue
        if kap_dahil:
            if cur > sinir:
                continue
        else:
            if cur >= sinir:
                continue
        brut = _f(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil"))
        if brut <= _TOL:
            continue
        odenen = _f(a.get("odenen_tutar_kdv"))
        kalan_raw = a.get("kalan_tutar_kdv")
        kalan = _f(kalan_raw) if kalan_raw is not None else round(brut - odenen, 2)
        te = bool(a.get("tahsil_edildi"))
        if not (kalan > _TOL or (not te and odenen <= _TOL)):
            continue
        add = max(kalan, round(brut - odenen, 2))
        n += 1
        tl = round(tl + add, 2)
    return n, tl


def _after_kap_ay_tl(
    payload: dict | None, kap: date | None, lim_son: date | None = None
) -> tuple[int, float]:
    if not isinstance(payload, dict) or not kap:
        return 0, 0.0
    sinir = _ay1(kap)
    n, tl = 0, 0.0
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        cur = _ay_cur(a)
        if cur is None or cur <= sinir:
            continue
        if lim_son is not None and cur > _ay1(lim_son):
            continue
        brut = _f(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil"))
        if brut <= _TOL:
            continue
        odenen = _f(a.get("odenen_tutar_kdv"))
        kalan_raw = a.get("kalan_tutar_kdv")
        kalan = _f(kalan_raw) if kalan_raw is not None else round(brut - odenen, 2)
        te = bool(a.get("tahsil_edildi"))
        if not (kalan > _TOL or (not te and odenen <= _TOL)):
            continue
        add = max(kalan, round(brut - odenen, 2))
        n += 1
        tl = round(tl + add, 2)
    return n, tl


def sadece_kirp_hesapla(
    disk_payload: dict | None,
    son: date | None,
    kap: date | None,
    kyc: dict | None,
    *,
    is_placeholder,
    is_healthy,
    trim_fn,
) -> dict[str, Any]:
    """Disk → trim sonucu (yazmadan). skip/sebep veya trimmed + metrikler.

    Pure: test edilebilir. Mevcut tahsilat_degisti yoluna dokunmaz.
    """
    out: dict[str, Any] = {
        "skip": False,
        "sebep": None,
        "trimmed": None,
        "need_write": False,
    }
    if not isinstance(disk_payload, dict):
        out["skip"] = True
        out["sebep"] = "disk_yok"
        return out
    if is_placeholder(disk_payload) or not is_healthy(disk_payload):
        out["skip"] = True
        out["sebep"] = "disk_sagliksiz_veya_placeholder"
        return out
    if son is None:
        out["skip"] = True
        out["sebep"] = "son_borc_yok"
        return out

    old_n, old_sum = _payload_brut_n_sum(disk_payload)
    # Aday A (N0 haric + N=0): kapanış ayı bilinçli kırpılır → koruma kap ayı HARİÇ.
    # Bayrak kapalı / N≠0: eski davranış (kap ayı dahil pre koruması).
    from kira_kapanis_cap import _parse_n, n0_kapanis_ayi_haric_aktif

    n_val = _parse_n((kyc or {}).get("kapanis_sonrasi_borc_ay"))
    kap_dahil_pre = not (n0_kapanis_ayi_haric_aktif() and n_val == 0)
    pre0 = _pre_kap_rows(disk_payload, kap, kap_dahil=kap_dahil_pre)
    pre0_ay, pre0_tl = _pre_kap_ay_tl(disk_payload, kap, kap_dahil=kap_dahil_pre)
    a0_ay, a0_tl = _after_kap_ay_tl(disk_payload, kap, lim_son=None)

    trimmed = trim_fn(disk_payload, son, kyc)
    if trimmed is None:
        out["skip"] = True
        out["sebep"] = "trim_none"
        out["old_n"] = old_n
        out["old_sum"] = old_sum
        return out

    pre1 = _pre_kap_rows(trimmed, kap, kap_dahil=kap_dahil_pre)
    pre1_ay, pre1_tl = _pre_kap_ay_tl(trimmed, kap, kap_dahil=kap_dahil_pre)
    if pre0 != pre1 or pre0_ay != pre1_ay or abs(pre0_tl - pre1_tl) > _TOL:
        out["skip"] = True
        out["sebep"] = "pre_kap_degisti"
        out["pre_old"] = {"ay": pre0_ay, "tl": pre0_tl, "n_row": len(pre0)}
        out["pre_new"] = {"ay": pre1_ay, "tl": pre1_tl, "n_row": len(pre1)}
        out["old_n"] = old_n
        out["old_sum"] = old_sum
        return out

    new_n, new_sum = _payload_brut_n_sum(trimmed)
    b2_ay, b2_tl = _after_kap_ay_tl(trimmed, kap, lim_son=son)
    # kırpılan: eski after-kap (uncapped) − yeni after-kap (B2 penceresi / kalan)
    # pratikte: old_n - new_n aylar (son sonrası atılan)
    dropped_n = max(0, old_n - new_n)
    dropped_tl = round(max(0.0, old_sum - new_sum), 2)

    identical = old_n == new_n and abs(old_sum - new_sum) <= _TOL
    out.update(
        {
            "old_n": old_n,
            "old_sum": old_sum,
            "new_n": new_n,
            "new_sum": new_sum,
            "pre_ay": pre0_ay,
            "pre_tl": pre0_tl,
            "A_ay": a0_ay,
            "A_tl": a0_tl,
            "B2_ay": b2_ay,
            "B2_tl": b2_tl,
            "kirpilan_ay": dropped_n,
            "kirpilan_tl": dropped_tl,
            "trimmed": trimmed,
            "need_write": not identical,
            "idempotent_already": identical,
        }
    )
    return out


def _run_sadece_kirp(mids: list[int], *, do_apply: bool) -> int:
    from db import execute, fetch_one
    from kira_kapanis_cap import (
        hepsi_sozlesme_yili_aktif,
        kira_son_borc_ayi,
        n0_kapanis_ayi_haric_aktif,
    )
    from routes import giris_routes as gr
    from routes.giris_routes import (
        _aylik_grid_payload_is_healthy,
        _aylik_grid_payload_is_placeholder,
        _aylik_grid_trim_payload_to_son_borc,
        _musteri_kyc_grup_for_aylik_grid,
    )

    flag = hepsi_sozlesme_yili_aktif()
    flag_n0 = n0_kapanis_ayi_haric_aktif()
    rows = []
    skip_n = 0
    need_n = 0
    pre_fark = []
    sum_a = sum_b2 = 0.0
    sum_kirp_ay = 0
    sum_kirp_tl = 0.0

    for mid in mids:
        kyc = _musteri_kyc_grup_for_aylik_grid(mid) or {}
        son = kira_son_borc_ayi(kyc)
        kap = _coerce_date(kyc.get("kapanis_tarihi"))
        row = fetch_one(
            "SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id=%s",
            (int(mid),),
        )
        disk = _parse_payload((row or {}).get("payload"))
        h = sadece_kirp_hesapla(
            disk,
            son,
            kap,
            kyc,
            is_placeholder=_aylik_grid_payload_is_placeholder,
            is_healthy=_aylik_grid_payload_is_healthy,
            trim_fn=_aylik_grid_trim_payload_to_son_borc,
        )
        rec = {
            "mid": mid,
            "kap": str(kap or "")[:10],
            "son_borc_ayi": _son_iso(son),
            "skip": h.get("skip"),
            "sebep": h.get("sebep"),
            "old_n": h.get("old_n"),
            "old_sum": h.get("old_sum"),
            "new_n": h.get("new_n"),
            "new_sum": h.get("new_sum"),
            "pre_ay": h.get("pre_ay"),
            "pre_tl": h.get("pre_tl"),
            "A_tl": h.get("A_tl"),
            "B2_tl": h.get("B2_tl"),
            "kirpilan_ay": h.get("kirpilan_ay"),
            "kirpilan_tl": h.get("kirpilan_tl"),
            "need_write": h.get("need_write"),
        }
        if h.get("sebep") == "pre_kap_degisti":
            pre_fark.append(rec)
            rec["pre_old"] = h.get("pre_old")
            rec["pre_new"] = h.get("pre_new")
        if h.get("skip"):
            skip_n += 1
        else:
            sum_a = round(sum_a + float(h.get("A_tl") or 0), 2)
            sum_b2 = round(sum_b2 + float(h.get("B2_tl") or 0), 2)
            if h.get("need_write"):
                need_n += 1
                sum_kirp_ay += int(h.get("kirpilan_ay") or 0)
                sum_kirp_tl = round(sum_kirp_tl + float(h.get("kirpilan_tl") or 0), 2)
        rows.append(rec)

    _j(
        {
            "mode": ("APPLY" if do_apply else "DRY-RUN") + "-SADECE-KIRP",
            "flag_KAPANIS_HEPSI_SOZLESME_YILI": flag,
            "flag_KAPANIS_N0_KAPANIS_AYI_HARIC": flag_n0,
            "mid_n": len(mids),
            "need_write_n": need_n,
            "skip_n": skip_n,
            "pre_kap_fark_n": len(pre_fark),
            "pre_kap_fark_mids": [r["mid"] for r in pre_fark],
            "sum_kirpilan_ay": sum_kirp_ay,
            "sum_kirpilan_tl": sum_kirp_tl,
            "sum_A_tl": sum_a,
            "sum_B2_tl": sum_b2,
            "rows": rows,
        }
    )

    if not do_apply:
        print(
            "DRY-RUN sadece-kirp tamam (yazilmadi). --apply + ALLOW_PROD_WRITE ile yazilir."
        )
        return 1 if pre_fark else 0

    # APPLY: yalnız need_write olanları yaz; skip olanlara dokunma
    yedek = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tag": "sadece_kirp",
        "flag": flag,
        "mids": mids,
        "rows": rows,
        "grid_payload": {},
    }
    # disk yedek (tüm mid)
    for mid in mids:
        row = fetch_one(
            "SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id=%s",
            (int(mid),),
        )
        yedek["grid_payload"][str(mid)] = (row or {}).get("payload")
    yol = _yedek_yolu("sadece_kirp")
    yol.write_text(json.dumps(yedek, ensure_ascii=False, default=str), encoding="utf-8")
    print("yedek", yol, "bytes", yol.stat().st_size)
    if yol.stat().st_size <= 0:
        print("DUR: yedek bos")
        return 1

    ok_n = 0
    fail = []
    audit_ok_n = 0
    for mid, rec in zip(mids, rows):
        if rec.get("skip"):
            continue
        if not rec.get("need_write"):
            ok_n += 1  # zaten kirpili = idempotent başarı
            continue
        kyc = _musteri_kyc_grup_for_aylik_grid(mid) or {}
        son = kira_son_borc_ayi(kyc)
        kap = _coerce_date(kyc.get("kapanis_tarihi"))
        row = fetch_one(
            "SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id=%s",
            (int(mid),),
        )
        disk = _parse_payload((row or {}).get("payload"))
        h = sadece_kirp_hesapla(
            disk,
            son,
            kap,
            kyc,
            is_placeholder=_aylik_grid_payload_is_placeholder,
            is_healthy=_aylik_grid_payload_is_healthy,
            trim_fn=_aylik_grid_trim_payload_to_son_borc,
        )
        if h.get("skip") or not h.get("trimmed"):
            fail.append({"mid": mid, "err": h.get("sebep") or "trim_fail"})
            continue
        anlik = {
            "mid": int(mid),
            "mod": "sadece_kirp",
            "son_borc_ayi": _son_iso(son),
            "kap": str(kap or "")[:10],
            "N": kyc.get("kapanis_sonrasi_borc_ay"),
            "ay_n": h.get("old_n"),
        }
        try:
            gr._ensure_aylik_grid_cache_table()
            execute(
                """
                INSERT INTO musteri_aylik_grid_cache (musteri_id, payload, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (musteri_id)
                DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (int(mid), json.dumps(h["trimmed"], ensure_ascii=False)),
            )
            try:
                gr._aylik_grid_mem_set(int(mid), h["trimmed"])
            except Exception:
                pass
            try:
                gr._invalidate_aylik_grid_payload_mem(int(mid))
            except Exception:
                pass
            ok_n += 1
        except Exception as ex:
            fail.append({"mid": mid, "err": str(ex).split("\n")[0][:120]})
            continue
        yeni = {
            "mid": int(mid),
            "mod": "sadece_kirp",
            "son_borc_ayi": _son_iso(son),
            "kap": str(kap or "")[:10],
            "N": kyc.get("kapanis_sonrasi_borc_ay"),
            "ay_n": h.get("new_n"),
        }
        try:
            if _audit_mid(execute, int(mid), anlik, yeni):
                audit_ok_n += 1
        except Exception as ex:
            print("audit_uyari mid=%s %s" % (mid, str(ex)[:80]))

    _j(
        {
            "ok_n": ok_n,
            "fail_n": len(fail),
            "fail_sample": fail[:10],
            "audit_ok_n": audit_ok_n,
        }
    )
    return 0 if not fail else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Hepsi sözleşme-yılı: mid bazlı yeniden hesap")
    ap.add_argument("--mid", help="tek veya virgüllü mid listesi")
    ap.add_argument("--mids-file", help="satır başına mid")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="yaz; yalnız ALLOW_PROD_WRITE ile",
    )
    ap.add_argument(
        "--force-flag",
        action="store_true",
        help="dry-run sırasında KAPANIS_HEPSI_SOZLESME_YILI=1 simüle et (yazmaz)",
    )
    ap.add_argument(
        "--sadece-kirp",
        action="store_true",
        default=False,
        help="rebuild yok; disk grid'i son_borc'a kırp (varsayılan kapalı)",
    )
    args = ap.parse_args()
    mids = _parse_mids(args)
    if not mids:
        print("HATA: --mid veya --mids-file gerekli")
        return 2

    if args.apply and not _prod_write_ok():
        print("HATA: --apply icin ALLOW_PROD_WRITE gerekli")
        return 2
    if not args.apply:
        os.environ.pop("ALLOW_PROD_WRITE", None)

    if args.force_flag and not args.apply:
        os.environ["KAPANIS_HEPSI_SOZLESME_YILI"] = "1"
    if args.apply and args.sadece_kirp:
        # apply yolunda bayrak açık olmalı (son_borc Hepsi)
        os.environ.setdefault("KAPANIS_HEPSI_SOZLESME_YILI", "1")

    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    from flask import Flask

    app = Flask("kapanis_hepsi_yeniden")
    app.app_context().push()

    # --- yeni mod: mevcut yolu hiç değiştirmeden erken dallan ---
    if args.sadece_kirp:
        return _run_sadece_kirp(mids, do_apply=bool(args.apply))

    from kira_kapanis_cap import hepsi_sozlesme_yili_aktif, kira_son_borc_ayi
    from routes.giris_routes import _musteri_kyc_grup_for_aylik_grid, tahsilat_degisti

    flag = hepsi_sozlesme_yili_aktif()
    plan = []
    for mid in mids:
        kyc = _musteri_kyc_grup_for_aylik_grid(mid) or {}
        son = kira_son_borc_ayi(kyc)
        plan.append(
            {
                "mid": mid,
                "durum": kyc.get("durum"),
                "N": kyc.get("kapanis_sonrasi_borc_ay"),
                "kap": str(kyc.get("kapanis_tarihi") or "")[:10],
                "son_borc_ayi": _son_iso(son),
            }
        )

    _j(
        {
            "mode": "APPLY" if args.apply else "DRY-RUN",
            "flag_KAPANIS_HEPSI_SOZLESME_YILI": flag,
            "mid_n": len(mids),
            "plan_sample": plan[:20],
        }
    )

    if not args.apply:
        print("DRY-RUN tamam (yazilmadi). --apply + ALLOW_PROD_WRITE ile yazilir.")
        return 0

    yedek = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "flag": flag,
        "mids": mids,
        "plan": plan,
    }
    yol = _yedek_yolu("apply")
    yol.write_text(json.dumps(yedek, ensure_ascii=False, indent=2), encoding="utf-8")
    print("yedek", yol)

    from db import execute, fetch_one

    def _grid_ay_n(mid: int) -> int | None:
        try:
            row = fetch_one(
                "SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id=%s",
                (int(mid),),
            )
            payload = (row or {}).get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            aylar = (payload or {}).get("aylar") if isinstance(payload, dict) else None
            return len(aylar) if isinstance(aylar, list) else None
        except Exception:
            return None

    plan_by = {int(p["mid"]): p for p in plan}
    ok_n = 0
    fail = []
    audit_ok_n = 0
    for mid in mids:
        once = plan_by.get(int(mid)) or {}
        anlik = {
            "mid": int(mid),
            "son_borc_ayi": once.get("son_borc_ayi"),
            "kap": once.get("kap"),
            "N": once.get("N"),
            "ay_n": _grid_ay_n(int(mid)),
        }
        try:
            if tahsilat_degisti(mid):
                ok_n += 1
            else:
                fail.append({"mid": mid, "err": "tahsilat_degisti False"})
        except Exception as ex:
            fail.append({"mid": mid, "err": str(ex).split("\n")[0][:120]})
            continue
        try:
            kyc2 = _musteri_kyc_grup_for_aylik_grid(mid) or {}
            son2 = kira_son_borc_ayi(kyc2)
            yeni = {
                "mid": int(mid),
                "son_borc_ayi": _son_iso(son2),
                "kap": str(kyc2.get("kapanis_tarihi") or "")[:10],
                "N": kyc2.get("kapanis_sonrasi_borc_ay"),
                "ay_n": _grid_ay_n(int(mid)),
            }
            if _audit_mid(execute, int(mid), anlik, yeni):
                audit_ok_n += 1
        except Exception as ex:
            print("audit_uyari mid=%s %s" % (mid, str(ex)[:80]))

    _j(
        {
            "ok_n": ok_n,
            "fail_n": len(fail),
            "fail_sample": fail[:10],
            "audit_ok_n": audit_ok_n,
        }
    )
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
