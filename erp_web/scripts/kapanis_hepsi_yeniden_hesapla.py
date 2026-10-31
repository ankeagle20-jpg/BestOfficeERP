# -*- coding: utf-8 -*-
"""Hepsi (N=NULL) sözleşme-yılı bayrağı sonrası grid/panel/ekstre yeniden hesap.

Varsayılan: DRY-RUN (yazmaz).
--apply yalnız ALLOW_PROD_WRITE tanımlıysa yazar.
Mid listesi --mids-file (satır başına mid) veya --mid.
Her mid: tahsilat_degisti(mid) → grid + panel by_iso + ekstre bellek.
Repo dışı yedek (Masaüstü JSON). PII yok (yalnız mid / sayı).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


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

    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    from flask import Flask

    app = Flask("kapanis_hepsi_yeniden")
    app.app_context().push()

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
