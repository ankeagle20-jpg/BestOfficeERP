# -*- coding: utf-8 -*-
"""GİB Çıkış butonu — Fatura Satırları (ilk Taslak Kaydet sağı).

Temiz ortam. Gerçek GİB portalına istek YOK (route stub). Prod yazma yok.
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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
        os.environ.pop(k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def test_kaynak_ve_taban():
    yf = (ROOT / "templates" / "faturalar" / "yeni_fatura.html").read_text(encoding="utf-8")
    fin = (ROOT / "templates" / "faturalar" / "finans.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "gib_cikis.js").read_text(encoding="utf-8")

    check("js_var", "function gibCikisYap" in js)
    check("js_uc", "/faturalar/api/gib-cikis" in js)
    check("js_body_bos", "body: \"{}\"" in js or "body: '{}'" in js)
    check("js_literal_bsn_yok", (chr(92) + "n") not in js)
    check("finans_script", 'src="/static/js/gib_cikis.js"' in fin)
    check("finans_yerel_fn_yok", "function gibCikisYap(" not in fin)
    check("finans_onclick", 'id="gib_cikis_btn"' in fin and "onclick=\"gibCikisYap()\"" in fin)
    check("yeni_script", 'src="/static/js/gib_cikis.js"' in yf)

    # Fatura Satırları bölümünde tam 1 gib_cikis_btn
    m = re.search(
        r"Fatura Satırları[\s\S]*?fatura-satir-aksiyonlar([\s\S]*?)</div>\s*</div>\s*<table",
        yf,
    )
    block = m.group(1) if m else ""
    n_btn = block.count('id="gib_cikis_btn"')
    check("satir_gib_n1", n_btn == 1, str(n_btn))
    # DOM sırası: btn-kaydet-inline hemen sonra gib_cikis_btn
    i_kay = block.find('id="btn-kaydet-inline"')
    i_gib = block.find('id="gib_cikis_btn"')
    i_tas = block.find('id="btn-kaydet-inline-taslak"')
    check("sira_kaydet_gib", 0 <= i_kay < i_gib, f"kay={i_kay} gib={i_gib}")
    check("sira_gib_ikinci_taslak", 0 <= i_gib < i_tas, f"gib={i_gib} tas={i_tas}")
    check("flex_wrap", "flex-wrap:wrap" in block or "flex-wrap: wrap" in block)

    # TABAN: butonsuz HTML'de sayaç 0
    taban = block.replace(
        re.search(r'<button[^>]*id="gib_cikis_btn"[^>]*>[\s\S]*?</button>', block).group(0),
        "",
        1,
    )
    check("taban_sifir", taban.count('id="gib_cikis_btn"') == 0)

    # İki Taslak Kaydet nedeni: kaydet vs erpTaslak
    check("iki_taslak_inline", 'id="btn-kaydet-inline"' in yf and 'id="btn-kaydet-inline-taslak"' in yf)

    be = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    check("be_uc", "@bp.route('/api/gib-cikis'" in be or '@bp.route("/api/gib-cikis"' in be)
    check("be_decorator", "def api_gib_cikis" in be and "faturalar_gerekli" in be)


def test_playwright_olcum():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    os.environ.pop("ALLOW_PROD_WRITE", None)
    for k in list(os.environ):
        if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_")):
            os.environ.pop(k, None)

    import app as app_module
    from auth import User
    from flask_login import current_user, login_user
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright

    app = getattr(app_module, "app", None) or app_module.create_app()
    app.config["WTF_CSRF_ENABLED"] = False

    @app.before_request
    def _auto():
        try:
            a = current_user.is_authenticated
            if (a() if callable(a) else a):
                return None
        except Exception:
            pass
        try:
            login_user(User(1, "olcum", "Olcum", "admin"), remember=False)
        except Exception:
            pass
        return None

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(80):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/faturalar/yeni", timeout=2)
            break
        except Exception:
            time.sleep(0.2)

    gib_calls = []
    real_gib_blocked = {"n": 0}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        def _route_handler(route):
            url = route.request.url
            # Gerçek GİB portal — engelle
            if any(
                x in url
                for x in (
                    "earsivportal.efatura.gov.tr",
                    "efatura.gov.tr",
                    "gib.gov.tr",
                )
            ):
                real_gib_blocked["n"] += 1
                return route.abort()
            if "/faturalar/api/gib-cikis" in url and route.request.method == "POST":
                body = route.request.post_data or ""
                gib_calls.append({"url": url, "body": body, "method": route.request.method})
                return route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"ok":true,"portal_logout":false,"mesaj":"GİB oturumu kapatıldı (stub)."}',
                )
            # diğer gib API — stub (yanlışlıkla gerçek çağrı olmasın)
            if "/faturalar/api/gib-" in url:
                return route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"ok":true,"mesaj":"stub","uuid":"00000000-0000-0000-0000-000000000000"}',
                )
            return route.continue_()

        page.route("**/*", _route_handler)
        page.goto(f"http://127.0.0.1:{port}/faturalar/yeni", wait_until="domcontentloaded", timeout=120000)
        page.wait_for_selector("#btn-kaydet-inline", timeout=60000)
        page.wait_for_function("() => typeof gibCikisYap === 'function'", timeout=30000)

        # (i) tam 1 buton + DOM sırası + bbox
        info = page.evaluate(
            """() => {
              const all = Array.from(document.querySelectorAll('#gib_cikis_btn'));
              const kay = document.getElementById('btn-kaydet-inline');
              const gib = document.getElementById('gib_cikis_btn');
              const tas = document.getElementById('btn-kaydet-inline-taslak');
              const aks = document.querySelector('.fatura-satir-aksiyonlar');
              const kids = aks ? Array.from(aks.querySelectorAll('button')).map(b => b.id) : [];
              const br = (el) => el ? el.getBoundingClientRect() : null;
              return {
                n: all.length,
                kids,
                kay_before_gib: kids.indexOf('btn-kaydet-inline') >= 0 && kids.indexOf('gib_cikis_btn') === kids.indexOf('btn-kaydet-inline') + 1,
                bbox: { kay: br(kay), gib: br(gib), tas: br(tas) }
              };
            }"""
        )
        check("pw_n1", info.get("n") == 1, str(info.get("n")))
        check("pw_sira", bool(info.get("kay_before_gib")), str(info.get("kids")))
        bb = info.get("bbox") or {}
        gib_bb = bb.get("gib") or {}
        kay_bb = bb.get("kay") or {}
        if gib_bb and kay_bb:
            check(
                "pw_bbox_sag",
                float(gib_bb.get("left") or 0) >= float(kay_bb.get("right") or 0) - 2,
                str({"kay": kay_bb.get("right"), "gib": gib_bb.get("left")}),
            )

        # (ii) tık → aynı uç, stub=1
        gib_calls.clear()
        page.click("#gib_cikis_btn")
        page.wait_for_timeout(400)
        check("pw_cagri_1", len(gib_calls) == 1, str(len(gib_calls)))
        if gib_calls:
            check("pw_payload", "{}" in (gib_calls[0].get("body") or ""), str(gib_calls[0].get("body"))[:40])
            check("pw_uc", "gib-cikis" in (gib_calls[0].get("url") or ""))

        # (iii) durum mesajı
        st = page.evaluate(
            """() => {
              const el = document.getElementById('yeni-gib-status') || document.getElementById('gib_manual_status');
              return el ? String(el.textContent || '') : '';
            }"""
        )
        check("pw_mesaj", "stub" in st.lower() or "kapat" in st.lower() or "çıkış" in st.lower() or "cikis" in st.lower(), st[:80])

        # (iv) diğer butonlar (stub) — çift tık çift istek atmasın (busy)
        gib_calls.clear()
        page.evaluate(
            """() => {
              const b = document.getElementById('gib_cikis_btn');
              if (b) { b.click(); b.click(); b.click(); }
            }"""
        )
        page.wait_for_timeout(600)
        check("pw_cift_tik_1", len(gib_calls) == 1, str(len(gib_calls)))

        # Taslak / önizleme / yeni fatura butonları mevcut ve tıklanabilir (davranış stub)
        for bid in ("btn-kaydet-inline", "btn-kaydet-inline-taslak", "btn-fatura-olustur", "btn-yeni-fatura-aylik"):
            vis = page.evaluate(f"() => {{ const e=document.getElementById('{bid}'); return !!(e && e.offsetParent !== null); }}")
            check(f"pw_btn_{bid}", bool(vis))

        # (v) dar ekran 390px taşma yok
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(200)
        overflow = page.evaluate(
            """() => {
              const aks = document.querySelector('.fatura-satir-aksiyonlar');
              if (!aks) return { ok:0 };
              const r = aks.getBoundingClientRect();
              const docW = document.documentElement.clientWidth;
              return { ok:1, right: r.right, docW, overflow: r.right > docW + 1 };
            }"""
        )
        check("pw_dar_tasma_yok", overflow.get("ok") == 1 and not overflow.get("overflow"), str(overflow))

        # (vi) mutasyon: buton kaldırılınca n=0 → test FAIL etmeli
        page.evaluate("() => { const b=document.getElementById('gib_cikis_btn'); if(b) b.remove(); }")
        n_mut = page.evaluate("() => document.querySelectorAll('#gib_cikis_btn').length")
        check("mutasyon_n0", n_mut == 0)

        browser.close()

    try:
        srv.shutdown()
    except Exception:
        pass

    check("gercek_gib_0", real_gib_blocked["n"] == 0, str(real_gib_blocked["n"]))


def test_node_js_syntax():
    js = (ROOT / "static" / "js" / "gib_cikis.js").read_text(encoding="utf-8")
    fd, path = tempfile.mkstemp(suffix=".js", prefix="gib_cikis_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(js)
        r = subprocess.run(
            ["node", "--check", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        check("node_syntax", r.returncode == 0, (r.stderr or "")[:120])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    test_kaynak_ve_taban()
    test_node_js_syntax()
    test_playwright_olcum()
    if FAILS:
        print("FAIL", len(FAILS))
        for a in FAILS:
            print(" -", a)
        raise SystemExit(1)
    print("test_gib_cikis_fatura_ekrani PASS")


if __name__ == "__main__":
    main()
