"""Tek PDF font kaydı: Liberation Sans (SIL OFL 1.1).

Dört kesim erp_web/static/fonts altındadır. Arial dosyası kullanılmaz.
Kayıt ilk PDF üretiminde yapılır; açılış yalnızca dosya varlığını loglar.
"""
import logging
import os
import threading

log = logging.getLogger(__name__)

FONT = "LiberationSans"
FONT_BOLD = "LiberationSans-Bold"
FONT_ITALIC = "LiberationSans-Italic"
FONT_BOLD_ITALIC = "LiberationSans-BoldItalic"

_FACES = (
    (FONT, "LiberationSans-Regular.ttf"),
    (FONT_BOLD, "LiberationSans-Bold.ttf"),
    (FONT_ITALIC, "LiberationSans-Italic.ttf"),
    (FONT_BOLD_ITALIC, "LiberationSans-BoldItalic.ttf"),
)

_lock = threading.Lock()
_registered = False


class PdfFontError(RuntimeError):
    """Font dosyası yok veya kayıt başarısız. Helvetica yedeği yoktur."""


def fonts_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")


def missing_font_files():
    root = fonts_dir()
    return [name for _reg, name in _FACES if not os.path.isfile(os.path.join(root, name))]


def log_font_status():
    """Açılış kontrolü. Eksik font uygulamayı ve /healthz yolunu durdurmaz."""
    try:
        missing = missing_font_files()
        if missing:
            msg = "PDF font eksik (Liberation Sans): " + ", ".join(missing)
            log.error(msg)
            print("[BOOT] " + msg)
        else:
            msg = "PDF font hazır: Liberation Sans, 4 kesim"
            log.info(msg)
            print("[BOOT] " + msg)
    except Exception:
        log.exception("PDF font kontrolü yazılamadı")


def ensure_registered():
    """Dört kesimi bir kez kaydeder. Dosya yoksa PdfFontError."""
    global _registered
    if _registered:
        return
    with _lock:
        if _registered:
            return
        missing = missing_font_files()
        if missing:
            raise PdfFontError(
                "Liberation Sans dosyası yok: "
                + ", ".join(missing)
                + ". PDF üretilemedi."
            )
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        root = fonts_dir()
        known = set(pdfmetrics.getRegisteredFontNames())
        for reg_name, filename in _FACES:
            if reg_name in known:
                continue
            pdfmetrics.registerFont(TTFont(reg_name, os.path.join(root, filename)))
            known.add(reg_name)
        pdfmetrics.registerFontFamily(
            FONT,
            normal=FONT,
            bold=FONT_BOLD,
            italic=FONT_ITALIC,
            boldItalic=FONT_BOLD_ITALIC,
        )
        _registered = True
