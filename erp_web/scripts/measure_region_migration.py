# -*- coding: utf-8 -*-
"""Render bölge göçü önce/sonra süre ölçümü (salt HTTP, yazma yok).

Örnekler:
  python scripts/measure_region_migration.py --base https://bestofficeerp.onrender.com
  python scripts/measure_region_migration.py --base https://bestofficeerp-fra.onrender.com
  python scripts/measure_region_migration.py --base https://adem.payafin.com \\
      --user sebiladem@hotmail.com --password '...' --passes 3

Ortam değişkenleri (opsiyonel):
  MEASURE_USER, MEASURE_PASSWORD
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
import urllib3

urllib3.disable_warnings()

DEFAULT_PATHS = (
    "/healthz",
    "/ledger/",
    "/ledger/api/assets",
    "/ledger/api/parties",
    "/ledger/api/summary?display_currency=TRY",
)


def timed_request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float = 120,
    **kwargs: Any,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    out: dict[str, Any] = {"method": method.upper(), "url": url}
    try:
        r = session.request(method, url, timeout=timeout, **kwargs)
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        out["status"] = r.status_code
        out["bytes"] = len(r.content or b"")
        ct = (r.headers.get("content-type") or "")[:60]
        out["content_type"] = ct
        if "json" in ct:
            try:
                j = r.json()
                out["ok"] = j.get("ok")
                if "parties" in j:
                    out["count"] = j.get("count") or len(j.get("parties") or [])
                if "assets" in j:
                    out["count"] = j.get("count") or len(j.get("assets") or [])
            except Exception:
                pass
        elif "html" in ct:
            text = r.text or ""
            out["has_pc_app"] = "pc-app" in text
            out["has_login_form"] = 'name="password"' in text and "pc-app" not in text
        return out
    except Exception as e:
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        out["error"] = str(e)[:240]
        return out


def login(
    session: requests.Session, base: str, user: str, password: str
) -> dict[str, Any]:
    meta = timed_request(
        session,
        "POST",
        base.rstrip("/") + "/login",
        data={"username": user, "password": password},
        allow_redirects=True,
    )
    final = ""
    try:
        # last response URL via a cheap GET if needed — use history from session
        pass
    except Exception:
        pass
    # Re-fetch to capture redirect target roughly
    meta["login_ok"] = meta.get("status") in (200, 302, 303) and not meta.get("error")
    # Heuristic: after login, /ledger/ should not be login page
    probe = timed_request(session, "GET", base.rstrip("/") + "/ledger/")
    meta["post_login_ledger"] = {
        "status": probe.get("status"),
        "has_pc_app": probe.get("has_pc_app"),
        "has_login_form": probe.get("has_login_form"),
        "ms": probe.get("ms"),
    }
    meta["login_ok"] = bool(probe.get("has_pc_app")) and not bool(
        probe.get("has_login_form")
    )
    meta["final_hint"] = final
    return meta


def measure_pass(session: requests.Session, base: str, paths: list[str]) -> dict:
    base = base.rstrip("/")
    steps = []
    # healthz + HTML sequential; APIs parallel (FE Promise.all benzeri)
    for p in paths:
        if p.startswith("/ledger/api/"):
            continue
        steps.append(timed_request(session, "GET", base + p))

    api_paths = [p for p in paths if p.startswith("/ledger/api/")]
    api_wall = None
    apis: list[dict] = []
    if api_paths:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=min(4, len(api_paths))) as ex:
            apis = list(
                ex.map(
                    lambda p: timed_request(session, "GET", base + p),
                    api_paths,
                )
            )
        api_wall = round((time.perf_counter() - t0) * 1000, 1)
        steps.extend(apis)

    page_ms = next(
        (s.get("ms") or 0 for s in steps if s.get("url", "").endswith("/ledger/") or "/ledger/?" in s.get("url", "")),
        0,
    )
    # fallback: any html ledger
    if not page_ms:
        page_ms = next(
            (
                s.get("ms") or 0
                for s in steps
                if "/ledger" in (s.get("url") or "") and "api" not in (s.get("url") or "")
            ),
            0,
        )

    return {
        "steps": steps,
        "api_parallel_wall_ms": api_wall,
        "boot_like_ms": round(page_ms + (api_wall or 0), 1) if api_paths else None,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render region migration latency probe")
    p.add_argument(
        "--base",
        required=True,
        help="Örn. https://bestofficeerp-fra.onrender.com veya https://adem.payafin.com",
    )
    p.add_argument("--user", default=os.environ.get("MEASURE_USER", ""))
    p.add_argument("--password", default=os.environ.get("MEASURE_PASSWORD", ""))
    p.add_argument("--passes", type=int, default=3)
    p.add_argument(
        "--paths",
        default=",".join(DEFAULT_PATHS),
        help="Virgülle ayrılmış path listesi",
    )
    p.add_argument(
        "--out",
        default="",
        help="JSON çıktı yolu (boş = stdout + opsiyonel dosya)",
    )
    p.add_argument("--insecure", action="store_true", help="TLS doğrulamayı kapat")
    args = p.parse_args(argv)

    base = args.base.strip().rstrip("/")
    paths = [x.strip() for x in args.paths.split(",") if x.strip()]
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "BestOfficeERP-measure-region-migration/1.0"}
    )
    session.verify = not args.insecure

    report: dict[str, Any] = {
        "base": base,
        "passes_requested": args.passes,
        "paths": paths,
        "login": None,
        "passes": [],
    }

    # Always hit healthz first (even if not in paths)
    hz = timed_request(session, "GET", base + "/healthz", timeout=60)
    report["healthz"] = hz
    print(f"healthz status={hz.get('status')} ms={hz.get('ms')} body_err={hz.get('error')}", flush=True)

    if args.user and args.password:
        report["login"] = login(session, base, args.user, args.password)
        print(
            f"login_ok={report['login'].get('login_ok')} ms={report['login'].get('ms')}",
            flush=True,
        )
    else:
        print("login skipped ( --user / --password veya MEASURE_* verin )", flush=True)

    for i in range(1, max(1, args.passes) + 1):
        norm = []
        for pth in paths:
            if pth.rstrip("/") == "/ledger":
                norm.append(f"/ledger/?_cb={int(time.time())}_{i}")
            else:
                norm.append(pth)
        m = measure_pass(session, base, norm)
        m["pass"] = i
        report["passes"].append(m)
        print(
            f"pass {i}: boot_like={m.get('boot_like_ms')} api_wall={m.get('api_parallel_wall_ms')}",
            flush=True,
        )
        time.sleep(0.4)

    warm = [x for x in report["passes"] if x.get("pass", 0) >= 2] or report["passes"]
    boot_vals = [x["boot_like_ms"] for x in warm if x.get("boot_like_ms") is not None]
    report["summary"] = {
        "warm_avg_boot_like_ms": round(sum(boot_vals) / len(boot_vals), 1)
        if boot_vals
        else None,
        "healthz_ms": hz.get("ms"),
        "healthz_ok": hz.get("status") == 200,
    }
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)

    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    out = args.out.strip()
    if not out:
        # default beside script
        stamp = time.strftime("%Y%m%d_%H%M%S")
        host = base.replace("https://", "").replace("http://", "").replace("/", "_")
        out = str(
            Path(__file__).resolve().parent
            / f"_measure_region_{host}_{stamp}.json"
        )
    Path(out).write_text(text, encoding="utf-8")
    print(f"wrote {out}", flush=True)
    print(text)
    return 0 if report["summary"].get("healthz_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
