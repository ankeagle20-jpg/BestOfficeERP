# -*- coding: utf-8 -*-
"""Giriş sayfası sağlığı: tam HTML + _t.js sözdizimi + gerçek sayfa yükleme.

Parça-parça fonksiyon çıkarma YOK. Literal \\n / SyntaxError'ı yakalar.
Prod yazmaz; ALLOW_PROD_WRITE kullanmaz. PII basmaz (yalnız satır no / kısa snip).
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
MAIN_ENV = Path(r"C:\Users\MSI\Desktop\Best ERP\BestOfficeERP\erp_web\.env")

# Kapıda SKIP = KALDI
_FAILS: list[str] = []
_OKS: list[str] = []


def _ok(msg: str) -> None:
    _OKS.append(msg)
    print("OK", msg, flush=True)


def _fail(msg: str) -> None:
    _FAILS.append(msg)
    print("FAIL", msg, flush=True)


def _load_env_readonly() -> None:
    os.environ.pop("ALLOW_PROD_WRITE", None)
    os.environ.pop("REEL_TUFE_ZINCIR", None)
    for k in list(os.environ.keys()):
        if k.startswith("KAPANIS_") or k.startswith("ODA_") or k.startswith("REEL_TUFE_"):
            if k != "REEL_TUFE_ZINCIR_MIDLER":
                os.environ.pop(k, None)
    if MAIN_ENV.exists():
        for line in MAIN_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in ("ALLOW_PROD_WRITE", "REEL_TUFE_ZINCIR") or k.startswith("KAPANIS_") or k.startswith("ODA_"):
                continue
            if k.startswith("REEL_TUFE_") and k != "REEL_TUFE_ZINCIR_MIDLER":
                continue
            if k and k not in os.environ:
                os.environ[k] = v.strip().strip('"').strip("'")
    os.environ.pop("ALLOW_PROD_WRITE", None)
    if "REEL_TUFE_ZINCIR_MIDLER" not in os.environ:
        os.environ["REEL_TUFE_ZINCIR_MIDLER"] = (
            "379,349,380,392,381,390,280,111,395,409,402,325,78,215,396,153,387,"
            "133,437,145,131,372,172,184,73,376,365"
        )


def _node_check(label: str, js: str) -> tuple[bool, str]:
    if not js or not js.strip():
        return True, ""
    fd, path = tempfile.mkstemp(suffix=".js", prefix="giris_saglik_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(js)
        r = subprocess.run(
            ["node", "--check", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        err = (r.stderr or r.stdout or "").strip()
        if r.returncode != 0:
            # satır no + kısa snip (PII yok)
            m = re.search(r":(\d+)\n", err)
            line_no = m.group(1) if m else "?"
            # ilk anlamlı satır
            bad_line = ""
            for ln in err.splitlines():
                if "SyntaxError" in ln or "Unexpected" in ln or ln.strip().startswith("^"):
                    continue
                if ln.strip() and not ln.startswith("    at "):
                    bad_line = ln.strip()[:80]
                    break
            return False, f"{label} line~{line_no} snip={bad_line!r} err={err[:240]}"
        return True, ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _extract_inline_scripts(html: str) -> list[str]:
    return re.findall(
        r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
        html,
        flags=re.I | re.S,
    )


def test_syntax_rendered_and_static() -> None:
    """(a)(b) Jinja işlenmiş /giris/ + _t.js + diğer giris şablonları."""
    _load_env_readonly()
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)

    try:
        from app import app
        from auth import User
        from db import fetch_one
        from flask_login import login_user
    except Exception as e:
        _fail(f"import/app: {type(e).__name__}")
        return

    try:
        admin = fetch_one(
            "SELECT id, username, role, security_stamp FROM public.users "
            "WHERE role='admin' AND COALESCE(is_active,true)=true ORDER BY id LIMIT 1"
        )
    except Exception as e:
        _fail(f"DB erişilemedi (SKIP sayılmaz=KALDI): {type(e).__name__}")
        return
    if not admin:
        _fail("admin yok (SKIP sayılmaz=KALDI)")
        return

    html = ""
    with app.test_client() as client:
        @app.route("/__saglik_login")
        def __saglik_login():
            u = User(
                id=admin["id"],
                username=admin["username"],
                full_name="s",
                role=admin["role"],
                aktif_mi=True,
                security_stamp=admin.get("security_stamp"),
            )
            login_user(u, remember=False)
            return {"ok": True}

        rlogin = client.get("/__saglik_login", headers={"Host": "127.0.0.1"})
        if rlogin.status_code >= 400:
            _fail(f"login status={rlogin.status_code}")
            return
        r = client.get("/giris/", headers={"Host": "127.0.0.1"})
        if r.status_code != 200:
            _fail(f"/giris/ status={r.status_code}")
            return
        html = r.get_data(as_text=True) or ""

    if len(html) < 1000:
        _fail(f"/giris/ body kısa bytes={len(html)}")
        return

    blocks = _extract_inline_scripts(html)
    if len(blocks) < 5:
        _fail(f"inline script sayısı {len(blocks)} < 5")
        return
    _ok(f"inline_blocks={len(blocks)}")

    for i, b in enumerate(blocks):
        ok, err = _node_check(f"inline[{i}]", b)
        if not ok:
            _fail(err)
        else:
            _ok(f"inline[{i}] node --check")
    ok, err = _node_check("inline_joined", "\n;\n".join(blocks))
    if not ok:
        _fail(err)
    else:
        _ok("inline_joined node --check")

    tjs = ROOT / "_t.js"
    if not tjs.exists():
        _fail("_t.js yok")
    else:
        ok, err = _node_check("_t.js", tjs.read_text(encoding="utf-8", errors="replace"))
        if not ok:
            _fail(err)
        else:
            _ok("_t.js node --check")

    # (b) diğer giriş şablonları: ham Jinja'yı nötrle (render edilmemiş {{ }}).
    # Asıl kapı yukarıdaki /giris/ Jinja-işlenmiş yanıttır.
    for p in sorted((ROOT / "templates" / "giris").glob("*.html")):
        if p.name == "index.html":
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        neut = re.sub(r"\{\{.*?\}\}", "null", txt, flags=re.S)
        neut = re.sub(r"\{%.*?%\}", "", neut, flags=re.S)
        neut = re.sub(r"\{#.*?#\}", "", neut, flags=re.S)
        bl = _extract_inline_scripts(neut)
        if not bl:
            continue
        for i, b in enumerate(bl):
            ok, err = _node_check(f"tpl:{p.name}[{i}]", b)
            if not ok:
                _fail(err)
            else:
                _ok(f"tpl:{p.name}[{i}] ok")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_playwright_real_page() -> None:
    """(c) Gerçek Flask + Playwright: pageerror yok, kritik fonksiyonlar tanımlı."""
    _load_env_readonly()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _fail("playwright yok (SKIP sayılmaz=KALDI)")
        return

    port = _free_port()
    env = os.environ.copy()
    env.pop("ALLOW_PROD_WRITE", None)
    code = f"""
import os,sys
os.environ.pop('ALLOW_PROD_WRITE', None)
sys.path.insert(0, r'{ROOT.as_posix()}')
os.chdir(r'{ROOT.as_posix()}')
from app import app
from auth import User
from flask_login import login_user, current_user
from db import fetch_one
_admin = fetch_one("SELECT id, username, role, security_stamp FROM public.users WHERE role='admin' AND COALESCE(is_active,true)=true ORDER BY id LIMIT 1")
@app.before_request
def _auto():
    try:
        a=current_user.is_authenticated
        if (a() if callable(a) else a):
            return None
    except Exception:
        pass
    try:
        u=User(id=_admin['id'], username=_admin['username'], full_name='s', role=_admin['role'], aktif_mi=True, security_stamp=_admin.get('security_stamp'))
        login_user(u, remember=False)
    except Exception:
        pass
    return None
app.run(host='127.0.0.1', port={port}, debug=False, use_reloader=False, threaded=True)
"""
    log_path = Path(tempfile.gettempdir()) / f"giris_saglik_flask_{port}.log"
    logf = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
        text=True,
    )
    import urllib.request

    ready = False
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/giris/", timeout=2)
            ready = True
            break
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.4)
    if not ready:
        _fail("Flask boot başarısız (SKIP sayılmaz=KALDI)")
        try:
            proc.terminate()
            logf.close()
        except Exception:
            pass
        return

    urls = [
        f"http://127.0.0.1:{port}/giris/?tab=sozlesmeler",
        f"http://127.0.0.1:{port}/giris/",
    ]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
            for url in urls:
                pageerrors: list[str] = []
                console_bad: list[str] = []
                api_hits: list[dict] = []
                ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                page = ctx.new_page()
                page.on("pageerror", lambda e: pageerrors.append(str(e)[:300]))
                page.on(
                    "console",
                    lambda msg: console_bad.append((msg.text or "")[:200])
                    if msg.type == "error"
                    and (
                        "Uncaught" in (msg.text or "")
                        or "SyntaxError" in (msg.text or "")
                    )
                    else None,
                )

                def on_resp(resp):
                    try:
                        path = urlparse(resp.url).path
                        if "musteriler" in path:
                            api_hits.append({"status": resp.status, "path": path})
                    except Exception:
                        pass

                page.on("response", on_resp)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                except Exception as e:
                    _fail(f"goto {urlparse(url).path} {type(e).__name__}")
                    ctx.close()
                    continue
                page.wait_for_timeout(5000)

                fns = page.evaluate(
                    """() => {
                      const names = ['musteriAra','selectMusteri','switchTab',
                        'girisAylikSatirYilPanelDoldur','girisTahsilatYilAyKiraTutar',
                        'sozlesmelerAylikHizliYukle'];
                      const out = {};
                      for (const n of names) {
                        try { out[n] = typeof window[n]; } catch(e) { out[n] = 'err'; }
                      }
                      const ara = document.getElementById('header-arama-input')
                        || document.getElementById('arama-input');
                      const liste = document.getElementById('musteri-liste');
                      return {
                        fns: out,
                        hasAra: !!ara,
                        hasListe: !!liste,
                        bodyLen: (document.body && document.body.innerText || '').trim().length
                      };
                    }"""
                )
                for n, t in (fns.get("fns") or {}).items():
                    if t != "function":
                        _fail(f"{urlparse(url).path} typeof {n}={t}")
                    else:
                        _ok(f"{urlparse(url).path} {n}=function")

                if pageerrors:
                    _fail(f"{urlparse(url).path} pageerror={pageerrors[0][:120]}")
                else:
                    _ok(f"{urlparse(url).path} pageerror=0")
                if console_bad:
                    _fail(f"{urlparse(url).path} console={console_bad[0][:120]}")
                else:
                    _ok(f"{urlparse(url).path} console_syntax=0")

                api_direct = page.evaluate(
                    """async () => {
                      try {
                        const r = await fetch('/giris/api/musteriler', {credentials:'same-origin'});
                        const j = await r.json();
                        return {status: r.status, n: Array.isArray(j)?j.length:0};
                      } catch(e) { return {err: String(e).slice(0,120)}; }
                    }"""
                )
                if not isinstance(api_direct, dict) or api_direct.get("status") != 200:
                    _fail(f"api/musteriler {api_direct}")
                else:
                    _ok(f"api/musteriler status=200 n={api_direct.get('n')}")

                if not fns.get("hasAra"):
                    _fail(f"{urlparse(url).path} arama kutusu yok")
                else:
                    # arama yazınca istek / filtre
                    saw = {"hit": False}

                    def on_req(req):
                        if "musteriler" in (req.url or ""):
                            saw["hit"] = True

                    page.on("request", on_req)
                    page.fill("#header-arama-input, #arama-input", "a")
                    page.wait_for_timeout(2500)
                    if saw["hit"] or any(h.get("status") == 200 for h in api_hits):
                        _ok(f"{urlparse(url).path} arama_istek")
                    else:
                        # DOM süzme yedek: liste içeriği değişti mi
                        _fail(f"{urlparse(url).path} arama istek/süzme yok")
                ctx.close()
            browser.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            logf.close()
        except Exception:
            pass


def main() -> int:
    print("=== test_giris_sayfa_sagligi ===", flush=True)
    print("ALLOW", os.environ.get("ALLOW_PROD_WRITE"), flush=True)
    test_syntax_rendered_and_static()
    # sözdizimi kırıldıysa Playwright yine de çalışsın (mutasyon FAIL kanıtı için)
    # ama syntax FAIL varsa Playwright pageerror beklenir
    test_playwright_real_page()
    print("---", flush=True)
    print("ok_n", len(_OKS), "fail_n", len(_FAILS), flush=True)
    if _FAILS:
        for f in _FAILS[:20]:
            print("FAIL_ITEM", f, flush=True)
        return 1
    if not _OKS:
        print("FAIL boş sonuç", flush=True)
        return 1
    print("PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
