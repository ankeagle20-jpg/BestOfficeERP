"""
İlan Robotu Playwright sürücüsü (Browserless.io CDP / stealth).
Form verilerini (Başlık, Fiyat, EİDS No, Açıklama, Resim yolları) alıp
Sahibinden / Hepsiemlak sitelerinde otomatik form doldurma yapar.

Not: Selenium /webdriver bu Browserless shared fleet'te desteklenmiyor;
bağlantı wss://…/stealth?token=… üzerinden Playwright connect_over_cdp ile yapılır.
"""
from __future__ import annotations

import os
import sys
import time
import logging
import re
from contextlib import contextmanager
from typing import Iterator, List, Optional, Tuple
from urllib.parse import urlencode

# Proje kökü
_web_root = os.path.dirname(os.path.abspath(__file__))
if _web_root not in sys.path:
    sys.path.insert(0, _web_root)

# .env yükle
try:
    from dotenv import load_dotenv
    for p in [os.path.join(_web_root, ".env"), os.path.join(os.getcwd(), ".env")]:
        if os.path.isfile(p):
            load_dotenv(p)
            break
except ImportError:
    pass

logger = logging.getLogger("robot_surucu")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(h)


# ── Browserless / Playwright bağlantı ─────────────────────────────────────────

def _browserless_token() -> str:
    """API anahtarını config veya ortamdan oku (asla loglama)."""
    try:
        from config import Config
        tok = (getattr(Config, "BROWSERLESS_API_KEY", None) or "").strip()
        if tok:
            return tok
    except Exception:
        pass
    return (os.environ.get("BROWSERLESS_API_KEY") or "").strip()


def _browserless_ws_endpoint(token: str) -> str:
    """
    Playwright CDP WebSocket URL.
    Stealth route: Browserless bot-tespiti sertleştirmesi (undetected-chromedriver yerine).
    """
    try:
        from config import Config
        host = (getattr(Config, "BROWSERLESS_WS_HOST", None) or "").strip()
    except Exception:
        host = ""
    if not host:
        host = (os.environ.get("BROWSERLESS_WS_HOST") or "wss://production-sfo.browserless.io").strip()
    host = host.rstrip("/")
    # Zaten /stealth ile bitiyorsa tekrar ekleme
    if host.endswith("/stealth"):
        base = host
    else:
        base = f"{host}/stealth"
    # token'ı query'ye koy; URL loglanmaz
    # solveCaptchas: Sahibinden/Hepsiemlak Cloudflare / bot duvarı için (plan destekliyorsa)
    params = {"token": token, "solveCaptchas": "true"}
    return f"{base}?{urlencode(params)}"


@contextmanager
def _browser_session(headless: bool = True) -> Iterator[Tuple[object, object]]:
    """
    Browserless stealth CDP oturumu.
    Yield: (browser, page). Çıkışta browser.close().
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwright eksik (pip install playwright).") from e

    token = _browserless_token()
    if not token:
        raise RuntimeError("BROWSERLESS_API_KEY tanımlı değil. erp_web/.env içine ekleyin.")

    ws = _browserless_ws_endpoint(token)
    # Logda yalnızca host/path (token yok)
    try:
        safe = ws.split("?", 1)[0]
        logger.info("Browserless Playwright CDP: %s", safe)
    except Exception:
        logger.info("Browserless Playwright CDP bağlanıyor.")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            # viewport
            try:
                context.set_viewport_size({"width": 1280, "height": 800})
            except Exception:
                pass
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(15000)
            yield browser, page
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ── Locator yardımcıları (Selenium eşlemesi) ───────────────────────────────────

def _first_visible(page, selectors: List[str], timeout_ms: int = 2500):
    """Selenium find_element + is_displayed döngüsünün karşılığı."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if loc.is_visible(timeout=timeout_ms):
                return loc
        except Exception:
            continue
    return None


def _fill_first(page, selectors: List[str], text: str, timeout_ms: int = 2500) -> bool:
    """Selenium _safe_send_keys ≈ Playwright fill (clear + type)."""
    loc = _first_visible(page, selectors, timeout_ms=timeout_ms)
    if not loc:
        return False
    try:
        loc.click(timeout=timeout_ms)
        loc.fill(text or "")
        return True
    except Exception as e:
        logger.warning("fill başarısız (%s): %s", selectors[0] if selectors else "?", e)
        return False


def _click_first(page, selectors: List[str], timeout_ms: int = 2500) -> bool:
    loc = _first_visible(page, selectors, timeout_ms=timeout_ms)
    if not loc:
        return False
    try:
        loc.click(timeout=timeout_ms)
        return True
    except Exception as e:
        logger.warning("click başarısız: %s", e)
        return False


def _select_first(page, selectors: List[str], value_or_text: str, timeout_ms: int = 2500) -> bool:
    """Selenium Select ≈ Playwright select_option."""
    loc = _first_visible(page, selectors, timeout_ms=timeout_ms)
    if not loc:
        return False
    try:
        loc.select_option(label=str(value_or_text))
        return True
    except Exception:
        try:
            loc.select_option(value=str(value_or_text))
            return True
        except Exception as e:
            logger.warning("Select seçilemedi: %s", e)
            return False


def _set_files_first(page, selectors: List[str], paths: List[str], timeout_ms: int = 2500) -> int:
    """Selenium file send_keys ≈ set_input_files. Dönüş: yüklenen dosya sayısı."""
    existing = [os.path.abspath(p) for p in paths if p and os.path.isfile(p)]
    if not existing:
        return 0
    existing = existing[:10]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            n = loc.count()
            if n == 0:
                continue
            uploaded = 0
            for idx, path in enumerate(existing):
                target = loc.nth(idx) if idx < n else loc.first
                target.set_input_files(path, timeout=timeout_ms)
                uploaded += 1
                logger.info("Resim yüklendi: %s", os.path.basename(path))
            return uploaded
        except Exception as e:
            logger.warning("Resim yükleme atlandı (%s): %s", sel, e)
            continue
    return 0


def _probe_login_fields(page, email_sels: List[str], pass_sels: List[str]) -> Tuple[bool, str]:
    """Giriş formu alanlarının görünürlüğünü doğrula (kimlik bilgisi yazmadan)."""
    # Cloudflare / "Just a moment..." sonrası form gelene kadar bekle
    try:
        page.wait_for_function(
            "() => !/just a moment/i.test(document.title || '')",
            timeout=45000,
        )
    except Exception:
        pass
    email_ok = _first_visible(page, email_sels, timeout_ms=15000) is not None
    pass_ok = _first_visible(page, pass_sels, timeout_ms=8000) is not None
    title = (page.title() or "").strip()
    if email_ok and pass_ok:
        return True, f"Giriş formu alanları (e-posta/şifre) görünür. title={title[:80]!r}"
    missing = []
    if not email_ok:
        missing.append("email")
    if not pass_ok:
        missing.append("password")
    cf = "cloudflare_challenge" if re.search(r"just a moment", title, re.I) else "no_cf_title"
    return False, f"Giriş formu eksik alanlar: {','.join(missing)} ({cf}) title={title[:80]!r}"


# ── Platform akışları ──────────────────────────────────────────────────────────

SAHIBINDEN_EMAIL_SELS = [
    "input[name='email']",
    "input[type='email']",
    "input[name='username']",
    "#email",
    "#username",
]
SAHIBINDEN_PASS_SELS = [
    "input[name='password']",
    "input[type='password']",
    "#password",
]
HEPSIEMLAK_EMAIL_SELS = [
    "input[type='email']",
    "input[name='email']",
    "#email",
]
HEPSIEMLAK_PASS_SELS = [
    "input[type='password']",
    "input[name='password']",
    "#password",
]


def run_sahibinden(
    baslik: str,
    fiyat: str,
    eids_no: Optional[str],
    aciklama: str,
    resim_yollari: Optional[List[str]] = None,
    headless: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Sahibinden.com'a giriş yapıp ilan formunu doldurur (Playwright + Browserless).
    dry_run=True: giriş sayfasına gider, form alanlarını doğrular; giriş/doldurma yapmaz.
    """
    resim_yollari = resim_yollari or []
    email = os.environ.get("SAHIBINDEN_EMAIL", "").strip()
    password = os.environ.get("SAHIBINDEN_PASSWORD", "").strip()
    if not dry_run and (not email or not password):
        return False, "SAHIBINDEN_EMAIL ve SAHIBINDEN_PASSWORD .env dosyasında tanımlanmalı."

    try:
        with _browser_session(headless=headless) as (_browser, page):
            login_url = "https://secure.sahibinden.com/giris"
            logger.info("Sahibinden giriş sayfasına gidiliyor: %s", login_url)
            page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)

            if dry_run:
                ok, msg = _probe_login_fields(page, SAHIBINDEN_EMAIL_SELS, SAHIBINDEN_PASS_SELS)
                logger.info("Sahibinden dry_run ok=%s", ok)
                return ok, f"dry_run sahibinden: {msg}"

            if not _fill_first(page, SAHIBINDEN_EMAIL_SELS, email):
                return False, "Sahibinden giriş formu bulunamadı (e-posta alanı)."
            if not _fill_first(page, SAHIBINDEN_PASS_SELS, password):
                return False, "Sahibinden giriş formu bulunamadı (şifre alanı)."
            time.sleep(0.5)
            if not _click_first(
                page,
                ["button[type='submit']", "input[type='submit']", ".login-btn", "#submit"],
            ):
                # Enter ile dene
                try:
                    page.locator(SAHIBINDEN_PASS_SELS[0]).first.press("Enter")
                except Exception:
                    pass
            time.sleep(3)

            ilan_url = "https://www.sahibinden.com/ilan/ver"
            logger.info("İlan verme sayfasına gidiliyor.")
            page.goto(ilan_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)

            if _fill_first(
                page,
                [
                    "input[name='title']",
                    "input[name='baslik']",
                    "#title",
                    "#baslik",
                    "input[placeholder*='Başlık']",
                ],
                baslik or "",
            ):
                logger.info("Başlık yazıldı.")

            # Fiyat: name içinde price/fiyat olan görünür input
            fiyat_sels = [
                "input[name='price']",
                "input[name='fiyat']",
                "#price",
                "#fiyat",
                "input[placeholder*='Fiyat']",
            ]
            if _fill_first(page, fiyat_sels, str(fiyat or "").replace(",", ".")):
                logger.info("Fiyat yazıldı.")

            if _fill_first(
                page,
                [
                    "textarea[name='description']",
                    "textarea[name='aciklama']",
                    "#description",
                    "#aciklama",
                    "textarea",
                ],
                aciklama or "",
            ):
                logger.info("Açıklama yazıldı.")

            if eids_no:
                if _fill_first(
                    page,
                    [
                        "input[name='eids']",
                        "input[name='eids_yetki']",
                        "#eids",
                        "input[placeholder*='EİDS']",
                    ],
                    eids_no,
                ):
                    logger.info("EİDS no yazıldı.")

            if resim_yollari:
                _set_files_first(
                    page,
                    ["input[type='file']", "input[name='file']", "input[accept*='image']"],
                    resim_yollari,
                )

            try:
                _select_first(
                    page,
                    ["select[name='room']", "select[name='oda_sayisi']", "#room"],
                    "1",
                )
                _fill_first(page, ["input[name='m2']", "input[name='area']", "#m2"], "")
            except Exception as e:
                logger.debug("Oda/m2 alanları atlandı: %s", e)

            logger.info("Form doldurma tamamlandı.")
            return True, "Form dolduruldu. Tarayıcıda son kontrolü yapıp ilanı yayınlayabilirsiniz."

    except Exception as e:
        logger.exception("Sahibinden robot hatası")
        return False, str(e)


def run_hepsiemlak(
    baslik: str,
    fiyat: str,
    eids_no: Optional[str],
    aciklama: str,
    resim_yollari: Optional[List[str]] = None,
    headless: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Hepsiemlak.com'a giriş yapıp ilan formunu doldurur (Playwright + Browserless).
    dry_run=True: giriş sayfası + alan doğrulama; giriş yok.
    """
    resim_yollari = resim_yollari or []
    email = os.environ.get("HEPSIEMLAK_EMAIL", "").strip()
    password = os.environ.get("HEPSIEMLAK_PASSWORD", "").strip()
    if not dry_run and (not email or not password):
        return False, "HEPSIEMLAK_EMAIL ve HEPSIEMLAK_PASSWORD .env dosyasında tanımlanmalı."

    try:
        with _browser_session(headless=headless) as (_browser, page):
            login_url = "https://www.hepsiemlak.com/giris"
            logger.info("Hepsiemlak giriş sayfasına gidiliyor.")
            page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)

            if dry_run:
                ok, msg = _probe_login_fields(page, HEPSIEMLAK_EMAIL_SELS, HEPSIEMLAK_PASS_SELS)
                logger.info("Hepsiemlak dry_run ok=%s", ok)
                return ok, f"dry_run hepsiemlak: {msg}"

            _fill_first(page, HEPSIEMLAK_EMAIL_SELS, email)
            _fill_first(page, HEPSIEMLAK_PASS_SELS, password)
            time.sleep(0.5)
            _click_first(page, ["button[type='submit']", "input[type='submit']"])
            time.sleep(3)

            ilan_url = "https://www.hepsiemlak.com/ilan-ver"
            page.goto(ilan_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)

            _fill_first(
                page,
                ["input[name='title']", "#title", "input[placeholder*='Başlık']"],
                baslik or "",
            )
            _fill_first(
                page,
                ["input[name='price']", "#price", "input[placeholder*='Fiyat']"],
                str(fiyat or "").replace(",", "."),
            )
            _fill_first(
                page,
                ["textarea[name='description']", "#description", "textarea"],
                aciklama or "",
            )
            if eids_no:
                _fill_first(page, ["input[name='eids']", "#eids"], eids_no)
            if resim_yollari:
                _set_files_first(page, ["input[type='file']"], resim_yollari)

            logger.info("Hepsiemlak form doldurma tamamlandı.")
            return True, "Form dolduruldu. Tarayıcıda son kontrolü yapıp ilanı yayınlayabilirsiniz."

    except Exception as e:
        logger.exception("Hepsiemlak robot hatası")
        return False, str(e)


def run_platform(
    platform: str,
    baslik: str,
    fiyat: str,
    eids_no: Optional[str],
    aciklama: str,
    resim_yollari: Optional[List[str]] = None,
    headless: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """platform: 'sahibinden' | 'hepsiemlak'."""
    if platform == "sahibinden":
        return run_sahibinden(
            baslik, fiyat, eids_no, aciklama, resim_yollari, headless=headless, dry_run=dry_run
        )
    if platform == "hepsiemlak":
        return run_hepsiemlak(
            baslik, fiyat, eids_no, aciklama, resim_yollari, headless=headless, dry_run=dry_run
        )
    return False, f"Bilinmeyen platform: {platform}"
