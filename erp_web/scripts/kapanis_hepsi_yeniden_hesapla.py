# -*- coding: utf-8 -*-
"""Hepsi (N=NULL) sözleşme-yılı bayrağı sonrası grid/panel/ekstre yeniden hesap.

Varsayılan: DRY-RUN (yazmaz).
--apply yalnız ALLOW_PROD_WRITE tanımlıysa yazar.
Mid listesi --mids-file (satır başına mid) veya --mid.
Her mid: tahsilat_degisti(mid) → grid + panel by_iso + ekstre bellek.
Repo dışı yedek (Masaüstü JSON). PII yok (yalnız mid / sayı).
Bu script'i --apply ile bu komutta ÇALIŞTIRMAYIN (salt dry-run ok).
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
            s = line.strip()
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
                "son_borc_ayi": son.isoformat() if son else None,
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

    ok_n = 0
    fail = []
    for mid in mids:
        try:
            if tahsilat_degisti(mid):
                ok_n += 1
            else:
                fail.append({"mid": mid, "err": "tahsilat_degisti False"})
        except Exception as ex:
            fail.append({"mid": mid, "err": str(ex).split("\n")[0][:120]})

    try:
        from audit_log import audit_yaz

        audit_yaz(
            "kapanis_hepsi_yeniden_hesapla",
            {
                "mid_n": len(mids),
                "ok_n": ok_n,
                "fail_n": len(fail),
                "flag": flag,
                "yedek": str(yol),
            },
        )
    except Exception as ex:
        print("audit_uyari", str(ex)[:80])

    _j({"ok_n": ok_n, "fail_n": len(fail), "fail_sample": fail[:10]})
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
