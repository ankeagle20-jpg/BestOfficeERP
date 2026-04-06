#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhatsApp Web — toplu mesaj (tek sekme, Selenium, mevcut Chrome profili)

wa.me veya yeni sekme açmaz; web.whatsapp.com içinde arama kutusu + sohbet + mesaj kutusu kullanır.

Önkoşullar:
  - Bu profili kullanan başka Chrome penceresi KAPALI olmalı (kilitleme hatası önlenir).
  - İlk seferde profilde WhatsApp Web’e giriş yapılmış olmalı (QR bir kez).

ÖNERİLEN: Çift oturum uyarısını (“başka pencerede açık”) önlemek için Chrome’u SİZ başlatın, script sadece bağlansın:

  1) Tüm Chrome’ları kapatın.
  2) CMD / PowerShell:
     "%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe" ^
       --remote-debugging-port=9222 ^
       --user-data-dir="%LOCALAPPDATA%\\Google\\Chrome\\User Data" ^
       --profile-directory="Default"
  3) Açılan pencerede web.whatsapp.com — tek sekme, giriş yapılmış olsun.
  4) Script:
     python -m scripts.whatsapp_web_selenium_bulk --debugger-address 127.0.0.1:9222 --csv ...

Alternatif (script Chrome başlatır — bazen ikinci oturum uyarısı çıkar):
  python -m scripts.whatsapp_web_selenium_bulk \\
    --user-data-dir "%LOCALAPPDATA%\\Google\\Chrome\\User Data" \\
    --profile-directory "Default" \\
    --csv musteriler_whatsapp.csv

CSV (UTF-8 veya Excel için UTF-8 BOM): sütunlar phone, message — isteğe bağlı name
  phone: 905551234567 veya 05551234567
  message: Gönderilecek metin (çok satırlı için CSV’de tırnak içinde)

ERP’den liste:
  Excel’den veya SQL export ile CSV üretin; veya --from-db ile (aşağıda) veritabanından okuyun.

Not: WhatsApp arayüzü sık güncellenir; seçiciler çalışmazsa scriptteki *_SELECTORS listelerine yeni
     XPath/CSS eklemeniz gerekebilir. Tarayıcıda F12 → öğeyi inceleyerek güncelleyin.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from typing import List, Optional, Sequence, Tuple

# Proje kökü (opsiyonel --from-db)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ERP_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _ERP_ROOT)

try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        ElementClickInterceptedException,
        ElementNotInteractableException,
        StaleElementReferenceException,
        TimeoutException,
    )
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError as e:
    print("Selenium gerekli: pip install selenium", file=sys.stderr)
    raise SystemExit(1) from e

BySel = Tuple[str, str]

# WhatsApp Web — olası seçiciler (üstten aşağı denenir)
SEARCH_BOX_SELECTORS: List[BySel] = [
    (By.CSS_SELECTOR, "div[contenteditable='true'][data-tab='3']"),
    (By.CSS_SELECTOR, "[data-testid='chat-list-search'] div[contenteditable='true']"),
    (By.XPATH, "//div[@id='side']//div[@contenteditable='true' and @role='textbox']"),
    (By.XPATH, "//div[@id='side']//div[@contenteditable='true']"),
]

# Sohbet açıldıktan sonra alt mesaj kutusu
COMPOSE_BOX_SELECTORS: List[BySel] = [
    (By.CSS_SELECTOR, "footer div[contenteditable='true'][data-tab]"),
    (By.CSS_SELECTOR, "[data-testid='conversation-compose-box-input']"),
    (By.XPATH, "//footer//div[@contenteditable='true']"),
]

SEND_BUTTON_SELECTORS: List[BySel] = [
    (By.CSS_SELECTOR, "span[data-icon='send']"),
    (By.CSS_SELECTOR, "[data-testid='send']"),
    (By.XPATH, "//button[@aria-label='Gönder']"),
    (By.XPATH, "//button[@aria-label='Send']"),
]

SIDE_PANE_LOCATORS: List[BySel] = [
    (By.ID, "pane-side"),
    (By.CSS_SELECTOR, "[data-testid='chatlist']"),
]


def dismiss_whatsapp_use_here_dialog(driver: webdriver.Chrome) -> bool:
    """
    'WhatsApp başka bir pencerede açık' → Burada Kullan / Use Here.
    Aynı tarayıcıda ikinci WA sekmesi/penceresi bu diyaloğu çıkarır.
    """
    xps = [
        "//button[.//span[contains(.,'Burada Kullan')]]",
        "//button[contains(normalize-space(.),'Burada Kullan')]",
        "//span[contains(.,'Burada Kullan')]/ancestor::button[1]",
        "//button[contains(normalize-space(.),'Use Here')]",
        "//span[contains(.,'Use Here')]/ancestor::button[1]",
        "//div[@role='dialog']//button[last()]",
    ]
    for xp in xps:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    el.click()
                    time.sleep(1.0)
                    return True
                except Exception:
                    try:
                        ActionChains(driver).move_to_element(el).click().perform()
                        time.sleep(1.0)
                        return True
                    except Exception:
                        continue
        except Exception:
            continue
    return False


def _wa_tab_readiness_score(driver: webdriver.Chrome) -> int:
    """
    Geçerli sekmede WA arayüzü ne kadar hazır? 2 = sohbet listesi görünür, 1 = sadece web.whatsapp.com URL.
    (Çoklu WA sekmesinde yanlışlıkla 'yükleniyor' sekmesini tutmayı önler.)
    """
    u = (driver.current_url or "").lower()
    if "web.whatsapp.com" not in u:
        return 0
    for by, sel in SIDE_PANE_LOCATORS:
        try:
            for el in driver.find_elements(by, sel):
                try:
                    if el.is_displayed():
                        return 2
                except Exception:
                    continue
        except Exception:
            continue
    return 1


def keep_single_window_prefer_whatsapp(driver: webdriver.Chrome) -> None:
    """
    Yalnızca FAZLA web.whatsapp.com sekmelerini kapatır; ERP vb. diğer sekmelere dokunmaz.
    Birden fazla WA varsa ÖNCE hazır oturumu (sol liste görünür) tutar; aksi halde yüklü sekmeyi
    silip boş/yükleniyor sekmeyi bırakma hatası oluşurdu (window_handles sırası rastgele).
    """
    handles = list(driver.window_handles)
    if len(handles) <= 1:
        if handles:
            driver.switch_to.window(handles[0])
        return
    wa_handles: List[str] = []
    for h in handles:
        try:
            driver.switch_to.window(h)
            u = (driver.current_url or "").lower()
            if "web.whatsapp.com" in u:
                wa_handles.append(h)
        except Exception:
            continue
    if len(wa_handles) == 1:
        driver.switch_to.window(wa_handles[0])
        return
    if len(wa_handles) > 1:
        best: Optional[Tuple[int, str]] = None  # (score, handle) — yüksek skor kazanır
        for h in wa_handles:
            try:
                driver.switch_to.window(h)
                sc = _wa_tab_readiness_score(driver)
                if best is None or sc > best[0]:
                    best = (sc, h)
            except Exception:
                continue
        keep = (best[1] if best else None) or wa_handles[0]
        for h in wa_handles:
            if h == keep:
                continue
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass
        try:
            driver.switch_to.window(keep)
        except Exception:
            if driver.window_handles:
                driver.switch_to.window(driver.window_handles[0])
        return
    try:
        driver.switch_to.window(handles[0])
    except Exception:
        pass


def normalize_phone_tr(raw: str) -> str:
    """Rakamlar; TR cep için 90 ile başlayan uluslararası format."""
    if not raw:
        return ""
    d = re.sub(r"\D", "", str(raw).strip())
    if not d:
        return ""
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("0"):
        d = d[1:]
    if len(d) == 10 and d[0] == "5":
        d = "90" + d
    if len(d) == 11 and d.startswith("5"):
        d = "90" + d
    return d


def load_rows_csv(path: str) -> List[dict]:
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV başlık satırı yok.")
        fields = {h.strip().lower(): h for h in reader.fieldnames}
        pcol = fields.get("phone") or fields.get("telefon") or fields.get("tel")
        mcol = fields.get("message") or fields.get("mesaj") or fields.get("text")
        if not pcol or not mcol:
            raise ValueError("CSV’de phone ve message (veya mesaj) sütunları gerekli.")
        ncol = fields.get("name") or fields.get("ad") or fields.get("musteri_adi")
        for r in reader:
            phone = (r.get(pcol) or "").strip()
            msg = (r.get(mcol) or "").strip()
            name = (r.get(ncol) or "").strip() if ncol else ""
            if phone and msg:
                rows.append({"phone": normalize_phone_tr(phone), "message": msg, "name": name})
    return rows


def load_rows_from_db(limit: int) -> List[dict]:
    os.environ.setdefault("FLASK_APP", "app.py")
    from db import fetch_all  # type: ignore

    q = """
        SELECT c.phone, c.name AS name,
               CONCAT('Merhaba ', COALESCE(NULLIF(TRIM(c.name), ''), 'Müşteri'),
                      ',\n\nBestOffice üzerinden iletişime geçiyorum.') AS message
        FROM customers c
        WHERE c.phone IS NOT NULL AND TRIM(COALESCE(c.phone, '')) <> ''
        ORDER BY c.id
        LIMIT %s
    """
    out: List[dict] = []
    for r in fetch_all(q, (limit,)) or []:
        p = normalize_phone_tr(r.get("phone") or "")
        m = (r.get("message") or "").strip()
        if p and m:
            out.append({"phone": p, "message": m, "name": (r.get("name") or "").strip()})
    return out


def find_visible(driver, wait: WebDriverWait, specs: Sequence[BySel], clickable: bool = False):
    last_err = None
    for by, sel in specs:
        try:
            cond = EC.element_to_be_clickable((by, sel)) if clickable else EC.visibility_of_element_located((by, sel))
            el = wait.until(cond)
            if el and el.is_displayed():
                return el
        except (TimeoutException, StaleElementReferenceException) as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise TimeoutException("Hiçbir seçici eşleşmedi.")


def wait_whatsapp_ready(driver, timeout: int = 180) -> None:
    wait = WebDriverWait(driver, timeout)
    # Oturum açık: sol liste / pane-side
    find_visible(driver, wait, SIDE_PANE_LOCATORS, clickable=False)
    time.sleep(1.5)


def focus_search_box(driver, wait: WebDriverWait):
    el = find_visible(driver, wait, SEARCH_BOX_SELECTORS, clickable=True)
    try:
        el.click()
    except (ElementClickInterceptedException, ElementNotInteractableException):
        ActionChains(driver).move_to_element(el).click().perform()
    return el


def clear_contenteditable(el) -> None:
    el.click()
    try:
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.BACKSPACE)
    except Exception:
        pass


def open_chat_by_phone(driver, wait: WebDriverWait, phone_digits: str, step_pause: float) -> bool:
    """Arama kutusuna numara yazar; ilk uygun sonuca Enter veya tıklar. Tek sekmede kalır."""
    search = focus_search_box(driver, wait)
    clear_contenteditable(search)
    time.sleep(0.2)
    # Numara: uluslararası, + işareti olmadan
    search.send_keys(phone_digits)
    time.sleep(step_pause)

    try:
        WebDriverWait(driver, 18).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='listitem']"))
        )
    except TimeoutException:
        pass

    items = driver.find_elements(By.XPATH, "//div[@role='listitem']")
    for c in items[:12]:
        if not c.is_displayed():
            continue
        try:
            txt = (c.text or "").lower()
            if "arşiv" in txt or "archived" in txt:
                continue
        except Exception:
            pass
        try:
            c.click()
            time.sleep(step_pause)
            return True
        except Exception:
            continue

    try:
        search.send_keys(Keys.ENTER)
        time.sleep(step_pause)
        return True
    except Exception:
        return False


def send_message_in_open_chat(driver, wait: WebDriverWait, text: str, step_pause: float) -> None:
    box = find_visible(driver, wait, COMPOSE_BOX_SELECTORS, clickable=True)
    try:
        box.click()
    except Exception:
        ActionChains(driver).move_to_element(box).click().perform()
    clear_contenteditable(box)
    time.sleep(0.15)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        box.send_keys(line)
        if i < len(lines) - 1:
            box.send_keys(Keys.SHIFT, Keys.ENTER)
    time.sleep(0.2)

    sent = False
    for by, sel in SEND_BUTTON_SELECTORS:
        try:
            btn = driver.find_element(by, sel)
            if btn.is_displayed():
                btn.click()
                sent = True
                break
        except Exception:
            continue
    if not sent:
        box.send_keys(Keys.ENTER)
    time.sleep(step_pause)


def build_driver_attached(debugger_address: str, chromedriver_path: Optional[str]) -> webdriver.Chrome:
    """Zaten açık Chrome’a bağlanır (tek süreç = tek WA oturumu). user-data-dir burada verilmez."""
    opts = Options()
    opts.add_experimental_option("debuggerAddress", debugger_address.strip())
    service = Service(chromedriver_path) if chromedriver_path else Service()
    drv = webdriver.Chrome(service=service, options=opts)
    drv.set_page_load_timeout(120)
    return drv


def build_driver(user_data_dir: str, profile_directory: str, chromedriver_path: Optional[str]) -> webdriver.Chrome:
    opts = Options()
    opts.add_argument(f"--user-data-dir={user_data_dir}")
    opts.add_argument(f"--profile-directory={profile_directory}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-popup-blocking")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    service = Service(chromedriver_path) if chromedriver_path else Service()
    drv = webdriver.Chrome(service=service, options=opts)
    drv.set_page_load_timeout(120)
    return drv


def prepare_whatsapp_tab(driver: webdriver.Chrome, url: str) -> None:
    """Çoklu-sekme uyarısını kapat, fazla WA sekmelerini birleştir, gerekirse URL’ye git."""
    for _ in range(6):
        dismiss_whatsapp_use_here_dialog(driver)
        time.sleep(0.25)
    keep_single_window_prefer_whatsapp(driver)
    cur = (driver.current_url or "").lower()
    if "web.whatsapp.com" not in cur:
        driver.get(url)
        time.sleep(1.0)
    for _ in range(5):
        dismiss_whatsapp_use_here_dialog(driver)
        time.sleep(0.35)
    keep_single_window_prefer_whatsapp(driver)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="WhatsApp Web toplu mesaj (Selenium, tek sekme)")
    ap.add_argument(
        "--user-data-dir",
        default=None,
        help=r'Chrome kullanıcı verisi (--debugger-address kullanıyorsanız gerekmez)',
    )
    ap.add_argument(
        "--debugger-address",
        default=None,
        metavar="HOST:PORT",
        help="Örn. 127.0.0.1:9222 — Chrome --remote-debugging-port ile önceden açılmış olsun (önerilir, tek WA oturumu)",
    )
    ap.add_argument("--profile-directory", default="Default", help="Profil klasörü (Default, Profile 1, …) — sadece --user-data-dir ile")
    ap.add_argument("--csv", help="Müşteri CSV: phone, message [, name]")
    ap.add_argument("--from-db", action="store_true", help="Veritabanından phone+name ile varsayılan mesaj (LIMIT)")
    ap.add_argument("--db-limit", type=int, default=50, help="--from-db satır limiti")
    ap.add_argument("--pause", type=float, default=12.0, help="Her mesaj sonrası bekleme (saniye)")
    ap.add_argument("--step-pause", type=float, default=1.2, help="Arama/sonuç adımları arası kısa bekleme")
    ap.add_argument("--chromedriver", help="chromedriver.exe yolu (PATH’teyse boş bırakın)")
    ap.add_argument("--url", default="https://web.whatsapp.com/", help="Açılacak adres")
    ap.add_argument("--dry-run", action="store_true", help="Sadece listeyi yaz, tarayıcı açma")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.from_db and args.csv:
        print("Ya --csv ya da --from-db kullanın.", file=sys.stderr)
        return 2

    rows: List[dict] = []
    if args.csv:
        rows = load_rows_csv(args.csv)
    elif args.from_db:
        rows = load_rows_from_db(args.db_limit)
    else:
        print("--csv veya --from-db gerekli.", file=sys.stderr)
        return 2

    if not rows:
        print("Gönderilecek satır yok.")
        return 0

    print(f"{len(rows)} müşteri yüklendi.")
    if args.dry_run:
        for i, r in enumerate(rows, 1):
            print(f"  {i}. {r.get('name') or '-'} | {r['phone']} | {r['message'][:60]}...")
        return 0

    use_attach = bool(args.debugger_address and str(args.debugger_address).strip())
    if not use_attach:
        print(
            "\n*** UYARI: --debugger-address YOK → script ayrı bir Chrome süreci başlatır.\n"
            "    Normal kullandığın Chrome’da WhatsApp zaten açıksa, İKİNCİ bir WA penceresi\n"
            "    açılır; müşteri başına değil, bu yüzden çift oturum uyarısı görürsün.\n"
            "    Tek pencere için: Chrome’u --remote-debugging-port=9222 ile başlat, WA’yı orada\n"
            "    aç, script’e ekle: --debugger-address 127.0.0.1:9222\n",
            flush=True,
        )
    if use_attach:
        drv = build_driver_attached(args.debugger_address.strip(), args.chromedriver)
        attach_mode = True
    else:
        if not args.user_data_dir or not str(args.user_data_dir).strip():
            print("Ya --debugger-address 127.0.0.1:9222 ya da --user-data-dir verin.", file=sys.stderr)
            return 2
        user_data = os.path.expandvars(os.path.expanduser(args.user_data_dir.strip().strip('"')))
        if not os.path.isdir(user_data):
            print(f"user-data-dir bulunamadı: {user_data}", file=sys.stderr)
            return 1
        drv = build_driver(user_data, args.profile_directory, args.chromedriver)
        attach_mode = False

    wait = WebDriverWait(drv, 25)
    try:
        prepare_whatsapp_tab(drv, args.url)
        wait_whatsapp_ready(drv, timeout=240)

        for i, r in enumerate(rows, 1):
            dismiss_whatsapp_use_here_dialog(drv)
            keep_single_window_prefer_whatsapp(drv)
            label = r.get("name") or r["phone"]
            print(f"[{i}/{len(rows)}] {label} ({r['phone']}) …", flush=True)
            try:
                ok = open_chat_by_phone(drv, wait, r["phone"], args.step_pause)
                if not ok:
                    print(f"    ! Sohbet açılamadı, atlanıyor.")
                    continue
                send_message_in_open_chat(drv, wait, r["message"], args.step_pause)
                print(f"    Gönderildi.")
            except Exception as ex:
                print(f"    ! Hata: {ex}")
            time.sleep(max(0.5, args.pause))

    finally:
        if attach_mode:
            print("Chrome penceresi açık bırakıldı (--debugger-address).")
        else:
            drv.quit()

    print("Bitti.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
