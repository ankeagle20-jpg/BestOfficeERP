"""
İlan Robotu Selenium sürücüsü.
Form verilerini (Başlık, Fiyat, EİDS No, Açıklama, Resim yolları) alıp
Sahibinden / Hepsiemlak gibi sitelere undetected-chromedriver ile otomatik ilan girişi yapar.
"""
import os
import sys
import time
import logging
from typing import List, Optional

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


def _get_driver():
    """undetected-chromedriver ile Chrome açarak bot tespitini azaltır."""
    try:
        import undetected_chromedriver as uc
    except ImportError:
        logger.warning("undetected_chromedriver yok, standart webdriver kullanılıyor.")
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opt = Options()
        opt.add_argument("--no-sandbox")
        opt.add_argument("--disable-dev-shm-usage")
        return webdriver.Chrome(options=opt)
    opts = uc.ChromeOptions()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return uc.Chrome(options=opts)


def _safe_send_keys(element, text: str, clear_first: bool = True):
    """Input/textarea'ya güvenli yazma."""
    from selenium.webdriver.common.keys import Keys
    if not element:
        return
    element.click()
    time.sleep(0.2)
    if clear_first:
        element.send_keys(Keys.CONTROL + "a")
        element.send_keys(Keys.BACKSPACE)
    if text:
        element.send_keys(text)


def _safe_select_option(select_el, value_or_text):
    """Select kutusunda değer veya metne göre seçim."""
    from selenium.webdriver.support.ui import Select
    if not select_el:
        return
    try:
        sel = Select(select_el)
        for opt in sel.options:
            if opt.get_attribute("value") == str(value_or_text) or (opt.text and str(value_or_text) in opt.text):
                sel.select_by_visible_text(opt.text)
                return
    except Exception as e:
        logger.warning("Select seçilemedi: %s", e)


def run_sahibinden(
    baslik: str,
    fiyat: str,
    eids_no: Optional[str],
    aciklama: str,
    resim_yollari: Optional[List[str]] = None,
    headless: bool = False,
) -> tuple[bool, str]:
    """
    Sahibinden.com'a giriş yapıp ilan formunu doldurur.
    Döner: (başarılı_mı, mesaj)
    """
    resim_yollari = resim_yollari or []
    email = os.environ.get("SAHIBINDEN_EMAIL", "").strip()
    password = os.environ.get("SAHIBINDEN_PASSWORD", "").strip()
    if not email or not password:
        return False, "SAHIBINDEN_EMAIL ve SAHIBINDEN_PASSWORD .env dosyasında tanımlanmalı."

    driver = None
    try:
        driver = _get_driver()
        if headless:
            driver.set_window_size(1280, 800)
        driver.implicitly_wait(10)
        wait = None
        try:
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By
            wait = WebDriverWait(driver, 15)
        except ImportError:
            return False, "Selenium modülü eksik (pip install selenium)."

        # Giriş sayfası
        login_url = "https://secure.sahibinden.com/giris"
        logger.info("Sahibinden giriş sayfasına gidiliyor: %s", login_url)
        driver.get(login_url)
        time.sleep(2)

        # E-posta / şifre alanları (siteler sık değiştirdiği için genel seçiciler)
        email_el = None
        pass_el = None
        for sel in ["input[name='email']", "input[type='email']", "input[name='username']", "#email", "#username"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    email_el = el
                    break
            except Exception:
                continue
        for sel in ["input[name='password']", "input[type='password']", "#password"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    pass_el = el
                    break
            except Exception:
                continue

        if not email_el or not pass_el:
            return False, "Sahibinden giriş formu bulunamadı (e-posta/şifre alanları değişmiş olabilir)."

        _safe_send_keys(email_el, email)
        _safe_send_keys(pass_el, password)
        time.sleep(0.5)
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit'], .login-btn, #submit")
            btn.click()
        except Exception:
            pass_el.send_keys("\n")
        time.sleep(3)

        # İlan verme sayfası (kategori: İş Yeri / Büro - Ofis vb. — gerçek URL site yapısına göre güncellenmeli)
        # Örnek: https://www.sahibinden.com/ilan/ver (veya siteye göre ilan verme linki)
        ilan_url = "https://www.sahibinden.com/ilan/ver"
        logger.info("İlan verme sayfasına gidiliyor.")
        driver.get(ilan_url)
        time.sleep(2)

        # Form alanlarını doldur (Sahibinden’in gerçek seçicileri site güncellemesine göre düzenlenmeli)
        # Başlık
        for sel in ["input[name='title']", "input[name='baslik']", "#title", "#baslik", "input[placeholder*='Başlık']"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    _safe_send_keys(el, baslik or "")
                    logger.info("Başlık yazıldı.")
                    break
            except Exception:
                continue

        # Fiyat
        for sel in ["input[name='price']", "input[name='fiyat']", "#price", "#fiyat", "input[placeholder*='Fiyat']", "input[type='number']"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed() and "fiyat" in (el.get_attribute("name") or "").lower() or "price" in (el.get_attribute("name") or "").lower():
                    _safe_send_keys(el, str(fiyat or "").replace(",", "."))
                    logger.info("Fiyat yazıldı.")
                    break
            except Exception:
                continue

        # Açıklama
        for sel in ["textarea[name='description']", "textarea[name='aciklama']", "#description", "#aciklama", "textarea"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    _safe_send_keys(el, aciklama or "")
                    logger.info("Açıklama yazıldı.")
                    break
            except Exception:
                continue

        # EİDS yetki no (varsa; site bu alanı destekliyorsa)
        if eids_no:
            for sel in ["input[name='eids']", "input[name='eids_yetki']", "#eids", "input[placeholder*='EİDS']"]:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    if el and el.is_displayed():
                        _safe_send_keys(el, eids_no)
                        logger.info("EİDS no yazıldı.")
                        break
                except Exception:
                    continue

        # Resim yükleme (varsa; site upload alanına path veya URL ile)
        if resim_yollari:
            for sel in ["input[type='file']", "input[name='file']", "input[accept*='image']"]:
                try:
                    file_inputs = driver.find_elements(By.CSS_SELECTOR, sel)
                    for idx, path in enumerate(resim_yollari[:10]):  # En fazla 10 resim
                        path = (path or "").strip()
                        if not path or not os.path.isfile(path):
                            continue
                        if idx < len(file_inputs):
                            file_inputs[idx].send_keys(os.path.abspath(path))
                            logger.info("Resim yüklendi: %s", path)
                except Exception as e:
                    logger.warning("Resim yükleme atlandı: %s", e)
                break

        # Oda sayısı / m2 vb. (Sahibinden’de kategoriye göre alanlar değişir; örnek seçiciler)
        # Gerçek sayfa yapısına göre aşağıdaki name/id’ler güncellenmeli.
        try:
            # Örnek: oda sayısı
            for sel in ["select[name='room']", "select[name='oda_sayisi']", "#room"]:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    if el and el.is_displayed():
                        _safe_select_option(el, "1")  # Varsayılan
                        break
                except Exception:
                    continue
            # Örnek: m2
            for sel in ["input[name='m2']", "input[name='area']", "#m2"]:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    if el and el.is_displayed():
                        _safe_send_keys(el, "")
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.debug("Oda/m2 alanları atlandı: %s", e)

        logger.info("Form doldurma tamamlandı. Son adımı (yayınla) kullanıcı tarayıcıdan yapabilir.")
        return True, "Form dolduruldu. Tarayıcıda son kontrolü yapıp ilanı yayınlayabilirsiniz."

    except Exception as e:
        logger.exception("Sahibinden robot hatası")
        return False, str(e)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_hepsiemlak(
    baslik: str,
    fiyat: str,
    eids_no: Optional[str],
    aciklama: str,
    resim_yollari: Optional[List[str]] = None,
    headless: bool = False,
) -> tuple[bool, str]:
    """
    Hepsiemlak.com'a giriş yapıp ilan formunu doldurur.
    Döner: (başarılı_mı, mesaj)
    """
    resim_yollari = resim_yollari or []
    email = os.environ.get("HEPSIEMLAK_EMAIL", "").strip()
    password = os.environ.get("HEPSIEMLAK_PASSWORD", "").strip()
    if not email or not password:
        return False, "HEPSIEMLAK_EMAIL ve HEPSIEMLAK_PASSWORD .env dosyasında tanımlanmalı."

    driver = None
    try:
        driver = _get_driver()
        if headless:
            driver.set_window_size(1280, 800)
        driver.implicitly_wait(10)
        try:
            from selenium.webdriver.common.by import By
        except ImportError:
            return False, "Selenium modülü eksik (pip install selenium)."

        # Giriş sayfası (Hepsiemlak gerçek URL’i siteye göre güncellenmeli)
        login_url = "https://www.hepsiemlak.com/giris"
        logger.info("Hepsiemlak giriş sayfasına gidiliyor.")
        driver.get(login_url)
        time.sleep(2)

        for sel in ["input[type='email']", "input[name='email']", "#email"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    _safe_send_keys(el, email)
                    break
            except Exception:
                continue
        for sel in ["input[type='password']", "input[name='password']", "#password"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    _safe_send_keys(el, password)
                    break
            except Exception:
                continue
        time.sleep(0.5)
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit']")
            btn.click()
        except Exception:
            pass
        time.sleep(3)

        # İlan verme sayfası
        ilan_url = "https://www.hepsiemlak.com/ilan-ver"
        driver.get(ilan_url)
        time.sleep(2)

        # Başlık, fiyat, açıklama (site seçicileri güncellenmeli)
        for sel in ["input[name='title']", "#title", "input[placeholder*='Başlık']"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    _safe_send_keys(el, baslik or "")
                    break
            except Exception:
                continue
        for sel in ["input[name='price']", "#price", "input[placeholder*='Fiyat']"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    _safe_send_keys(el, str(fiyat or "").replace(",", "."))
                    break
            except Exception:
                continue
        for sel in ["textarea[name='description']", "#description", "textarea"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    _safe_send_keys(el, aciklama or "")
                    break
            except Exception:
                continue
        if eids_no:
            for sel in ["input[name='eids']", "#eids"]:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    if el and el.is_displayed():
                        _safe_send_keys(el, eids_no)
                        break
                except Exception:
                    continue
        if resim_yollari:
            try:
                for inp in driver.find_elements(By.CSS_SELECTOR, "input[type='file']")[:10]:
                    for path in resim_yollari[:10]:
                        path = (path or "").strip()
                        if path and os.path.isfile(path):
                            inp.send_keys(os.path.abspath(path))
                            break
            except Exception as e:
                logger.warning("Hepsiemlak resim yükleme: %s", e)

        logger.info("Hepsiemlak form doldurma tamamlandı.")
        return True, "Form dolduruldu. Tarayıcıda son kontrolü yapıp ilanı yayınlayabilirsiniz."

    except Exception as e:
        logger.exception("Hepsiemlak robot hatası")
        return False, str(e)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_platform(platform: str, baslik: str, fiyat: str, eids_no: Optional[str], aciklama: str, resim_yollari: Optional[List[str]] = None, headless: bool = False) -> tuple[bool, str]:
    """platform: 'sahibinden' | 'hepsiemlak'."""
    if platform == "sahibinden":
        return run_sahibinden(baslik, fiyat, eids_no, aciklama, resim_yollari, headless=headless)
    if platform == "hepsiemlak":
        return run_hepsiemlak(baslik, fiyat, eids_no, aciklama, resim_yollari, headless=headless)
    return False, f"Bilinmeyen platform: {platform}"
