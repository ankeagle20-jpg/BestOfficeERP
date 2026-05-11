from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    flash,
    send_file,
    Response,
    abort,
    current_app,
)
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, date, timedelta
from db import (
    db,
    fetch_all,
    fetch_one,
    execute,
    execute_returning,
    ensure_faturalar_amount_columns,
    ensure_auto_invoice_tables,
    ensure_customers_durum,
    ensure_customers_is_active,
    ensure_duzenli_fatura_secenekleri_table,
    ensure_musteri_kyc_columns,
    ensure_musteri_kyc_odeme_duzeni,
    ensure_musteri_kyc_hazir_ofis_oda_no,
    ensure_musteri_kyc_latest_lookup_index,
    ensure_user_ui_preferences_table,
    ensure_customers_rent_columns,
    ensure_customers_hazir_ofis_oda,
    ensure_customers_bizim_hesap,
    ensure_customers_grup2_secimleri,
    ensure_grup2_etiketleri_table,
    ensure_customers_balance_trigger,
    ensure_tahsilatlar_columns,
    sql_expr_fatura_not_gib_taslak,
    sql_expr_fatura_erp_taslak,
    sql_expr_fatura_gib_imzalanmis,
)
from utils.text_utils import turkish_lower
from utils.musteri_arama import (
    customers_arama_sql_giris_genis,
    customers_arama_params_giris_genis,
    customers_arama_sql_params_giris_genis_tokens,
)
import os
import io
import re
import json
import uuid
import concurrent.futures
from urllib.parse import urlencode
import logging
import math
import time
from reportlab.lib.pagesizes import A5, A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def _fatura_pdf_debug():
    return os.environ.get("FATURA_PDF_DEBUG", "").lower() in ("1", "true", "yes")


def _register_arial():
    """Türkçe karakter için Arial fontlarını kaydet (fatura/makbuz PDF)."""
    if getattr(_register_arial, "_done", False):
        return
    win = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "C:\\Windows"
    fonts_dir = os.path.join(win, "Fonts")
    for f in ("arial.ttf", "Arial.ttf", "ARIAL.TTF"):
        p = os.path.join(fonts_dir, f)
        if os.path.isfile(p):
            try:
                pdfmetrics.registerFont(TTFont("Arial", p))
                break
            except Exception:
                pass
    for f in ("arialbd.ttf", "Arial Bold.ttf"):
        p = os.path.join(fonts_dir, f)
        if os.path.isfile(p):
            try:
                pdfmetrics.registerFont(TTFont("Arial-Bold", p))
                break
            except Exception:
                pass
    if "Arial" not in pdfmetrics.getRegisteredFontNames():
        for path in ["/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
            if os.path.isfile(path):
                try:
                    pdfmetrics.registerFont(TTFont("Arial", path))
                    break
                except Exception:
                    pass
    _register_arial._done = True


def _resolve_ettn_for_pdf(raw_ettn, fatura_id=None):
    """
    ETTN: Sadece geçerli UUID string'i olduğu gibi (büyük harf) kullanılır.
    Aksi halde (boş, Önizleme-ETTN, yazım hatası, ASCII O vb.) sentetik UUID — metin eşleştirmeye güvenilmez.
    Kayıtlı fatura (id var): uuid5 → aynı faturada PDF her seferinde aynı; önizlemede (id yok): uuid4.
    """
    v = (raw_ettn or "").strip()
    if v:
        try:
            cleaned = v.replace("{", "").replace("}", "").strip()
            return str(uuid.UUID(cleaned)).upper()
        except (ValueError, TypeError, AttributeError):
            pass
    if fatura_id is not None:
        try:
            return str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"bestoffice-erp:fatura:{int(fatura_id)}")
            ).upper()
        except (TypeError, ValueError):
            pass
    return str(uuid.uuid4()).upper()


def _normalize_vergi_dairesi(vd):
    """Vergi dairesi adını sadeleştir: 'KAVAKLIDERE VERGİ DAİRESİ MÜD.' -> 'KAVAKLIDERE'."""
    s = str(vd or "").strip()
    if not s:
        return "—"
    # En güvenlisi: "VERGI" kökünden sonrasını at (Unicode farklılıklarına dayanıklı).
    su = s.upper().replace("İ", "I")
    idx = su.find("VERG")
    if idx > 0:
        s = s[:idx].strip()
    # Kalan olası resmi ekleri temizle.
    s = re.sub(r"\bDA[İI]RES[İI]\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bM[ÜU]D(?:[ÜU]RL[ÜU][ĞG][ÜU]|\.?)\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bBA[ŞS]KANLI[ĞG][İI]\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,.-")
    return s or "—"


def _resolve_gib_logo_path():
    """GİB logosu: cwd + __file__ tabanlı adaylar; sonuç işlem ömrü boyunca önbelleklenir."""
    if getattr(_resolve_gib_logo_path, "_done", False):
        return getattr(_resolve_gib_logo_path, "_path", None)
    dbg = _fatura_pdf_debug()
    forced = os.path.join(os.getcwd(), "BestOfficeERP", "assets", "gib_logo.png")
    if dbg:
        print(
            f"[fatura PDF] GIB zorunlu yol: {forced} "
            f"exists={os.path.exists(forced)} isfile={os.path.isfile(forced)}"
        )
    if os.path.isfile(forced):
        _resolve_gib_logo_path._done = True
        _resolve_gib_logo_path._path = forced
        return forced
    cwd = os.getcwd()
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(cwd, "BestOfficeERP", "assets", "gib_logo.png"),
        os.path.join(cwd, "assets", "gib_logo.png"),
        os.path.abspath(os.path.join(here, "..", "..", "assets", "gib_logo.png")),
        os.path.abspath(os.path.join(here, "..", "static", "gib_logo.png")),
    ]
    for name in ("gib_logo.PNG", "gib_logo.jpg", "gib_logo.jpeg"):
        candidates.append(os.path.join(cwd, "BestOfficeERP", "assets", name))
        candidates.append(os.path.abspath(os.path.join(here, "..", "..", "assets", name)))
    seen = set()
    result = None
    for p in candidates:
        if not p or p in seen:
            continue
        seen.add(p)
        ex = os.path.isfile(p)
        if dbg:
            print(f"[fatura PDF] GIB logo yolu: {p} exists={ex}")
        if ex:
            result = p
            break
    if result is None and dbg:
        print("[fatura PDF] GIB logo bulunamadı (tüm adaylar denendi).")
    _resolve_gib_logo_path._done = True
    _resolve_gib_logo_path._path = result
    return result


# GİB logosu: en-boy + tek ImageReader örneği (her PDF’de dosyayı yeniden açmayı önler)
_gib_logo_aspect_cache = {}
_gib_logo_reader_cache = {}


def _gib_logo_reader(gib_path):
    if not gib_path:
        return None
    r = _gib_logo_reader_cache.get(gib_path)
    if r is not None:
        return r
    try:
        from reportlab.lib.utils import ImageReader

        r = ImageReader(gib_path)
        _gib_logo_reader_cache[gib_path] = r
        return r
    except Exception:
        return None


def _gib_logo_aspect_ratio(gib_path):
    if not gib_path:
        return 1.0
    cached = _gib_logo_aspect_cache.get(gib_path)
    if cached is not None:
        return cached
    reader = _gib_logo_reader(gib_path)
    if reader is None:
        ar = 1.0
    else:
        try:
            iw, ih = reader.getSize()
            ar = iw / float(ih or 1)
        except Exception:
            ar = 1.0
    _gib_logo_aspect_cache[gib_path] = ar
    return ar


def _gib_dogrulama_qr_url(fatura, sender_vkn, alici_vkn, ettn):
    """
    e-Arşiv / e-Fatura önizleme için GİB portal tabanlı doğrulama URL'si (QR içeriği).
    Kamuya açık sorgu ekranı: earsivportal.efatura.gov.tr
    """
    def _dig(x):
        return re.sub(r"\D", "", str(x or ""))

    try:
        toplam = float(fatura.get("genel_toplam") or fatura.get("toplam") or 0)
    except (TypeError, ValueError):
        toplam = 0.0
    try:
        kdv = float(fatura.get("kdv_toplam") or fatura.get("kdv_tutar") or 0)
    except (TypeError, ValueError):
        kdv = 0.0
    tarih = str(fatura.get("fatura_tarihi") or "")[:10]
    q = {
        "ettn": str(ettn or "").strip(),
        "vknoGonderen": _dig(sender_vkn),
        "vknoAlici": _dig(alici_vkn),
        "faturaNo": str(fatura.get("fatura_no") or "").strip(),
        "tarih": tarih,
        "tutar": f"{toplam:.2f}",
        "kdvToplam": f"{kdv:.2f}",
    }
    base = "https://earsivportal.efatura.gov.tr/earsiv-fatura-sorgula"
    url = f"{base}?{urlencode(q)}"
    if len(url) > 450:
        url = f"{base}?{urlencode({'ettn': q['ettn'], 'vknoGonderen': q['vknoGonderen'], 'vknoAlici': q['vknoAlici'], 'tarih': tarih})}"
    return url


bp = Blueprint('faturalar', __name__, url_prefix='/faturalar')

# Tahsilat makbuzu firma bilgileri (.env'den FIRMA_VERGI_DAIRESI, FIRMA_VERGI_NO eklenebilir)
FIRMA_UNVAN = os.environ.get("FIRMA_UNVAN", "OFİSBİR OFİS VE DANIŞMANLIK HİZMETLERİ ANONİM ŞİRKETİ")
FIRMA_ADRES = os.environ.get("FIRMA_ADRES", "KAVAKLIDERE MAH. ESAT CADDESİ NO:12 KAPI NO:1 ÇANKAYA / Ankara / Türkiye")
FIRMA_TELEFON = os.environ.get("FIRMA_TELEFON", "+90 (532) 549 79 10")
FIRMA_WEB = os.environ.get("FIRMA_WEB", "www.ofisbir.com.tr")
FIRMA_EMAIL = os.environ.get("FIRMA_EMAIL", "info@ofisbir.com.tr")
FIRMA_VERGI_DAIRESI = os.environ.get("FIRMA_VERGI_DAIRESI", "Kavaklıdere")
FIRMA_VERGI_NO = os.environ.get("FIRMA_VERGI_NO", "6340871926")
FIRMA_AKBANK_IBAN = os.environ.get("FIRMA_AKBANK_IBAN", "TR590004600153888000173206")
UPLOAD_MUSTERI_DOSYALARI = "uploads/musteri_dosyalari"
# GİB portal HTML: imza sonrası bir kez indirilir; önizlemede tekrar GİB çağrılmaz.
GIB_PORTAL_HTML_CACHE_DIR = "uploads/gib_portal_html"

AYLAR = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 
         'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']

_TAHSIL_EDEN_COL_READY = False


def _ensure_tahsil_eden_column():
    global _TAHSIL_EDEN_COL_READY
    if _TAHSIL_EDEN_COL_READY:
        return
    try:
        ensure_tahsilatlar_columns()
        execute("ALTER TABLE tahsilatlar ADD COLUMN IF NOT EXISTS tahsil_eden TEXT")
    except Exception:
        pass
    _TAHSIL_EDEN_COL_READY = True


def tutar_yaziya(tutar):
    """Tutarı Türkçe yazıya çevirir (örn: 1200 -> 'Bin İki Yüz Türk Lirası'; 1000-1999 arası 'Bir Bin' değil 'Bin')."""
    if tutar is None:
        return "Sıfır Türk Lirası"
    try:
        t = round(float(tutar), 2)
    except (TypeError, ValueError):
        return str(tutar) + " Türk Lirası"
    if t == 0:
        return "Sıfır Türk Lirası"

    birler = ["", "Bir", "İki", "Üç", "Dört", "Beş", "Altı", "Yedi", "Sekiz", "Dokuz"]
    onlar = ["", "On", "Yirmi", "Otuz", "Kırk", "Elli", "Altmış", "Yetmiş", "Seksen", "Doksan"]
    yuzler = ["", "Yüz", "İki Yüz", "Üç Yüz", "Dört Yüz", "Beş Yüz", "Altı Yüz", "Yedi Yüz", "Sekiz Yüz", "Dokuz Yüz"]
    scale_names = ["", "Bin", "Milyon", "Milyar", "Trilyon"]

    def three_digits(d):
        if d <= 0:
            return ""
        y, o, b = d // 100, (d // 10) % 10, d % 10
        chunks = []
        if y:
            chunks.append(yuzler[y])
        if o:
            chunks.append(onlar[o])
        if b:
            chunks.append(birler[b])
        return " ".join(chunks).strip()

    k = int(t)
    kuruş = int(round((t - k) * 100))
    if kuruş >= 100:
        k += 1
        kuruş = 0

    if k == 0:
        yazi = "Sıfır"
    else:
        gruplar = []
        kk = k
        while kk > 0:
            gruplar.append(kk % 1000)
            kk //= 1000
        parts_out = []
        for i in range(len(gruplar) - 1, -1, -1):
            d = gruplar[i]
            if d == 0:
                continue
            word = three_digits(d)
            if i == 0:
                parts_out.append(word)
            else:
                scale = scale_names[i] if i < len(scale_names) else "?"
                if i == 1 and d == 1:
                    parts_out.append("Bin")
                elif d == 1:
                    parts_out.append("Bir " + scale)
                else:
                    parts_out.append(word + " " + scale)
        yazi = " ".join(parts_out).replace("  ", " ").strip()

    yazi = yazi or "Sıfır"
    return yazi + " Türk Lirası" + (f" {kuruş:02}/100" if kuruş else "")


def _turkish_upper(s):
    """Türkçe büyük harf: i->İ, ı->I sonra upper."""
    return s.replace("i", "İ").replace("ı", "I").upper()


def tutar_yaziya_gib(tutar):
    """GİB e-Arşiv formatı: YALNIZ:#BİRYÜZ...TÜRKLİRASI80/100# (büyük harf, bitişik)."""
    if tutar is None:
        return "YALNIZ:#SIFIRTÜRKLİRASIDIR#"
    try:
        t = float(tutar)
    except (TypeError, ValueError):
        return "YALNIZ:#" + str(tutar) + "TÜRKLİRASIDIR#"
    s = tutar_yaziya(tutar)
    if not s or s.strip() == "":
        return "YALNIZ:#SIFIRTÜRKLİRASIDIR#"
    s = s.strip()
    kuruş_suffix = ""
    if " " in s and "/" in s:
        parts = s.rsplit(" ", 1)
        if len(parts) == 2 and "/" in parts[1]:
            kuruş_suffix = parts[1]
            s = parts[0]
    if s.endswith(" Türk Lirası"):
        s = s[:-len(" Türk Lirası")].strip()
    main = _turkish_upper(s).replace(" ", "")
    if kuruş_suffix:
        return "YALNIZ:#" + main + "TÜRKLİRASI" + kuruş_suffix + "#"
    return "YALNIZ:#" + main + "TÜRKLİRASIDIR#"


def _parse_amount_flexible(v):
    """100.000,00 / 100000,00 / 100000.00 / 100000 biçimlerini güvenli parse eder."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:
            return 0.0
    s = str(v).strip().replace(" ", "")
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # TR biçim: 100.000,00
            s = s.replace(".", "").replace(",", ".")
        else:
            # EN biçim: 100,000.00
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


# Sadece rakam makbuzlar (>=1000). Eski '^10[0-9]+$' 1100+ satırları dışarıda bırakıyordu → hep 1000/çakışma.
_MAKBUZ_MAX_SEQ_SQL = """
        SELECT COALESCE(MAX(x.n), 999) AS max_seq
        FROM (
            SELECT CAST(TRIM(makbuz_no) AS BIGINT) AS n
            FROM tahsilatlar
            WHERE makbuz_no IS NOT NULL
              AND TRIM(makbuz_no) ~ '^[0-9]+$'
              AND LENGTH(TRIM(makbuz_no)) <= 18
        ) x
        WHERE x.n >= 1000
        """


def get_next_makbuz_no():
    """Makbuz serisi sadece 1000 ile başlayan numaralardan ilerler (1000,1001,...)."""
    row = fetch_one(_MAKBUZ_MAX_SEQ_SQL) or {}
    try:
        seq = int(row.get("max_seq") or 999) + 1
    except Exception:
        seq = 1000
    if seq < 1000:
        seq = 1000
    return str(seq)


def _next_makbuz_no_with_cursor(cur):
    """Açık transaction + cursor ile MAX(makbuz); get_next_makbuz_no ile aynı mantık."""
    cur.execute(_MAKBUZ_MAX_SEQ_SQL)
    row = cur.fetchone() or {}
    try:
        seq = int(row.get("max_seq") or 999) + 1
    except Exception:
        seq = 1000
    if seq < 1000:
        seq = 1000
    return str(seq)


def _makbuz_no_used_cursor(cur, mn):
    cur.execute(
        "SELECT id FROM tahsilatlar WHERE TRIM(COALESCE(makbuz_no, '')) = %s LIMIT 1",
        (mn,),
    )
    return cur.fetchone() is not None


def _tahsilat_icin_makbuz_no_sec_cursor(cur, istenen_makbuz_no=None):
    """tahsilat_ekle ile aynı bağlantıda: kilit + INSERT öncesi tek görünürlük."""
    aday = _normalize_makbuz_no(istenen_makbuz_no)
    if aday and not _makbuz_no_used_cursor(cur, aday):
        return aday
    out = _next_makbuz_no_with_cursor(cur)
    _guard = 0
    while out and _makbuz_no_used_cursor(cur, out) and _guard < 10000:
        _guard += 1
        try:
            out = str(int(out) + 1)
        except Exception:
            break
    return out


def _normalize_makbuz_no(raw):
    s = str(raw or "").strip()
    if not s:
        return ""
    return re.sub(r"\s+", "", s)[:40]


def _makbuz_no_kullanildi_mi(makbuz_no):
    mn = _normalize_makbuz_no(makbuz_no)
    if not mn:
        return False
    row = fetch_one(
        "SELECT id FROM tahsilatlar WHERE TRIM(COALESCE(makbuz_no, '')) = %s LIMIT 1",
        (mn,),
    )
    return bool((row or {}).get("id"))


def _tahsilat_icin_makbuz_no_sec(istenen_makbuz_no=None):
    aday = _normalize_makbuz_no(istenen_makbuz_no)
    if aday and not _makbuz_no_kullanildi_mi(aday):
        return aday
    return get_next_makbuz_no()


def _tarih_saat_str(tarih_val, created_at=None):
    """tahsilat_tarihi ve created_at'ten '27.02.2026 Saat: 14:35' formatı."""
    tarih_str = "—"
    saat_str = ""
    if created_at:
        if hasattr(created_at, "strftime"):
            saat_str = created_at.strftime("%H:%M")
        else:
            try:
                dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                saat_str = dt.strftime("%H:%M")
            except Exception:
                pass
    if tarih_val:
        if hasattr(tarih_val, "strftime"):
            tarih_str = tarih_val.strftime("%d.%m.%Y")
        else:
            try:
                s = str(tarih_val)[:10]
                if len(s) == 10 and s[4] == "-":
                    y, m, d = s.split("-")
                    tarih_str = f"{d}.{m}.{y}"
                else:
                    tarih_str = s
            except Exception:
                tarih_str = str(tarih_val)[:10]
    if saat_str:
        return f"{tarih_str} Saat: {saat_str}"
    return tarih_str


_TAHSIL_AYLAR_TR = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)


def _ay_ref_iso_list_from_tahsilat_payload(data):
    """JSON'dan ay referansları: ay_ref_iso_list + tekil ay_ref_iso; YYYY-MM-DD, tekrarsız, sıralı."""
    if not isinstance(data, dict):
        return []
    seen = set()
    out = []
    raw_list = data.get("ay_ref_iso_list")
    if isinstance(raw_list, list):
        for item in raw_list:
            s = str(item or "").strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", s) and s not in seen:
                seen.add(s)
                out.append(s)
    single = (data.get("ay_ref_iso") or "").strip()
    if single and re.match(r"^\d{4}-\d{2}-\d{2}$", single) and single not in seen:
        seen.add(single)
        out.append(single)
    out.sort()
    return out


def _aciklama_with_aylik_markers(aciklama_text, iso_list):
    text = (aciklama_text or "").strip()
    for iso in iso_list or []:
        marker = f"|AYLIK_TAH|{iso}|"
        if marker not in text:
            text = f"{text} {marker}".strip()
    return text


def _aciklama_with_aylik_pay_tokens(aciklama_text, pay_items):
    """Dağıtım tutarını açıklamaya |AYLIK_PAY|YYYY-MM-DD=1234.56| olarak yazar."""
    text = (aciklama_text or "").strip()
    if not pay_items:
        return text
    for iso, tut in (pay_items or []):
        try:
            iso_s = datetime.strptime(str(iso)[:10], "%Y-%m-%d").date().isoformat()
            v = round(float(tut or 0), 2)
        except Exception:
            continue
        if v <= 0:
            continue
        tok = f"|AYLIK_PAY|{iso_s}={v:.2f}|"
        if tok not in text:
            text = f"{text} {tok}".strip()
    return text


def _acik_aylik_tutar_ay_set(musteri_id: int) -> set[str]:
    """Müşterinin açık |AYLIK_TUTAR| faturalarındaki ay anahtarları (YYYY-MM-DD)."""
    rows = fetch_all(
        """
        SELECT COALESCE(notlar, '') AS notlar
        FROM faturalar
        WHERE musteri_id = %s
          AND COALESCE(notlar, '') LIKE '%%|AYLIK_TUTAR|%%'
          AND COALESCE(durum, '') != 'odendi'
        """,
        (musteri_id,),
    ) or []
    out: set[str] = set()
    for r in rows:
        nt = str((r or {}).get("notlar") or "")
        for iso in re.findall(r"\|AYLIK_TUTAR\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|", nt):
            if re.match(r"^\d{4}-\d{2}-\d{2}$", iso):
                out.add(iso)
    return out


def _auto_allocate_oldest_unpaid_months(musteri_id, tahsil_tutar, borc_listesi=None, start_iso: str | None = None):
    """
    Elle tahsilatta ay işaretlenmemişse, cache'teki en eski borçlu aylardan dağıtım yap.
    Çıktı: (iso_list, [(iso, pay), ...])
    """
    try:
        mid = int(musteri_id or 0)
        total = round(float(tahsil_tutar or 0), 2)
    except (TypeError, ValueError):
        return [], []
    if mid <= 0 or total <= 0:
        return [], []
    start_iso_s = str(start_iso or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", start_iso_s):
        start_iso_s = ""
    acik_ay_set = _acik_aylik_tutar_ay_set(mid)
    unpaid_cache_set = set()
    try:
        row_u = fetch_one("SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id = %s", (mid,))
        payload_u_raw = (row_u or {}).get("payload")
        if payload_u_raw:
            payload_u = json.loads(payload_u_raw) if isinstance(payload_u_raw, str) else payload_u_raw
            aylar_u = payload_u if isinstance(payload_u, list) else ((payload_u or {}).get("aylar") or [])
            if isinstance(aylar_u, list):
                for a in aylar_u:
                    if not isinstance(a, dict):
                        continue
                    if not bool(a.get("acik_aylik_borc_faturasi")):
                        continue
                    try:
                        yy = int(a.get("yil"))
                        mm = int(a.get("ay"))
                    except (TypeError, ValueError):
                        continue
                    if mm < 1 or mm > 12:
                        continue
                    kalan_u = a.get("kalan_tutar_kdv")
                    if kalan_u is None:
                        try:
                            brut_u = float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0)
                        except (TypeError, ValueError):
                            brut_u = 0.0
                        try:
                            odenen_u = float(a.get("odenen_tutar_kdv") or 0)
                        except (TypeError, ValueError):
                            odenen_u = 0.0
                        kalan_u = round(max(brut_u - odenen_u, 0), 2)
                    try:
                        kalan_u_v = round(float(kalan_u or 0), 2)
                    except (TypeError, ValueError):
                        kalan_u_v = 0.0
                    if kalan_u_v <= 0.01:
                        continue
                    unpaid_cache_set.add(date(yy, mm, 1).isoformat())
    except Exception:
        unpaid_cache_set = set()
    # 1) İstemciden gelen canlı grid borç listesi (tercihli kaynak)
    borclu = []
    if isinstance(borc_listesi, list):
        raw_rows = []
        for it in borc_listesi:
            if not isinstance(it, dict):
                continue
            iso = str(it.get("iso") or "").strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", iso):
                continue
            if start_iso_s and iso < start_iso_s:
                continue
            if unpaid_cache_set and iso not in unpaid_cache_set:
                continue
            try:
                kalan_v = round(float(it.get("kalan") or 0), 2)
            except (TypeError, ValueError):
                kalan_v = 0.0
            if kalan_v <= 0.01:
                continue
            try:
                odenen_v = round(float(it.get("odenen") or 0), 2)
            except (TypeError, ValueError):
                odenen_v = 0.0
            raw_rows.append({
                "iso": iso,
                "kalan": kalan_v,
                "acik_borc": bool(it.get("acik_borc")),
                "odenen": odenen_v,
            })
        # İstemciden gelen canlı grid borç listesi birincil kaynak:
        # açık borç işaretli aylar varsa sadece oradan dağıt.
        if raw_rows:
            acik_rows = [r for r in raw_rows if r["acik_borc"]]
            pick_rows = acik_rows if acik_rows else raw_rows
            pick_rows.sort(key=lambda r: r["iso"])
            borclu = [(r["iso"], r["kalan"]) for r in pick_rows]
    if borclu:
        rem = total
        pay_items = []
        for iso, kalan_v in borclu:
            if rem <= 0.004:
                break
            pay = min(kalan_v, rem)
            pay = round(pay, 2)
            if pay <= 0:
                continue
            pay_items.append((iso, pay))
            rem = round(rem - pay, 2)
        return [iso for iso, _ in pay_items], pay_items

    # 2) Sunucu cache fallback
    try:
        row = fetch_one("SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id = %s", (mid,))
        payload_raw = (row or {}).get("payload")
        if not payload_raw:
            return [], []
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        aylar = payload if isinstance(payload, list) else ((payload or {}).get("aylar") or [])
        if not isinstance(aylar, list):
            return [], []
        acik_odak_var_cache = any(bool((a or {}).get("acik_aylik_borc_faturasi")) for a in aylar if isinstance(a, dict))
        sifir_odenen_var_cache = any(
            isinstance(a, dict) and float(a.get("odenen_tutar_kdv") or 0) <= 0.01
            for a in aylar
            if isinstance(a, dict)
        )
        borclu = []
        for a in aylar:
            if not isinstance(a, dict):
                continue
            if acik_odak_var_cache and not bool(a.get("acik_aylik_borc_faturasi")):
                continue
            try:
                yy = int(a.get("yil"))
                mm = int(a.get("ay"))
            except (TypeError, ValueError):
                continue
            if mm < 1 or mm > 12:
                continue
            iso = date(yy, mm, 1).isoformat()
            if start_iso_s and iso < start_iso_s:
                continue
            if acik_ay_set and iso not in acik_ay_set:
                continue
            try:
                odenen_v_cache = round(float(a.get("odenen_tutar_kdv") or 0), 2)
            except (TypeError, ValueError):
                odenen_v_cache = 0.0
            if sifir_odenen_var_cache and odenen_v_cache > 0.01:
                continue
            # Kritik: oldest dağıtımda asla "aylık tutarın tamamı"na düşme.
            # Sadece gerçek kalan borcu kullan (kalan_tutar_kdv yoksa brut-odenen hesapla).
            kalan = a.get("kalan_tutar_kdv")
            if kalan is None:
                try:
                    brut_v = float(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil") or 0)
                except (TypeError, ValueError):
                    brut_v = 0.0
                try:
                    odenen_v = float(a.get("odenen_tutar_kdv") or 0)
                except (TypeError, ValueError):
                    odenen_v = 0.0
                kalan = round(max(brut_v - odenen_v, 0), 2)
            try:
                kalan_v = round(float(kalan or 0), 2)
            except (TypeError, ValueError):
                kalan_v = 0.0
            if kalan_v <= 0.01:
                continue
            borclu.append((iso, kalan_v))
        borclu.sort(key=lambda x: x[0])
        rem = total
        pay_items = []
        for iso, kalan_v in borclu:
            if rem <= 0.004:
                break
            pay = round(min(kalan_v, rem), 2)
            if pay <= 0:
                continue
            pay_items.append((iso, pay))
            rem = round(rem - pay, 2)
        return [iso for iso, _ in pay_items], pay_items
    except Exception:
        return [], []


def _tahsil_rapor_yil_ay_coerce(val):
    """date/datetime veya 'YYYY-MM-DD' / 'YYYY-MM-DD HH:...' string → (yıl, ay) veya None."""
    if val is None:
        return None
    if hasattr(val, "year") and hasattr(val, "month"):
        try:
            return int(val.year), int(val.month)
        except (TypeError, ValueError):
            return None
    s = str(val).strip()[:10]
    if len(s) >= 7 and s[4:5] == "-":
        try:
            y = int(s[0:4])
            mo = int(s[5:7])
            if 1 <= mo <= 12:
                return y, mo
        except ValueError:
            pass
    return None


def _tahsilat_rapor_aciklama_ay_metni(aciklama, fatura_tarihi=None, tahsilat_tarihi=None):
    """Tahsilat açıklamasındaki |AYLIK_TAH|YYYY-MM-DD| işaretçilerinden ay listesi (Türkçe)."""
    text = str(aciklama or "")
    matches = re.findall(r"\|AYLIK_TAH\|(\d{4})-(\d{2})-\d{2}\|", text)
    seen = set()
    keys = []
    for y, mo in matches:
        k = f"{y}-{mo}"
        if k not in seen:
            seen.add(k)
            try:
                keys.append((y, int(mo, 10)))
            except ValueError:
                continue
    keys.sort(key=lambda t: (t[0], t[1]))
    if keys:
        parts = []
        for y, mo in keys:
            if 1 <= mo <= 12:
                parts.append(f"{_TAHSIL_AYLAR_TR[mo - 1]} {y}")
        if parts:
            return ", ".join(parts)
    plain = re.sub(r"\|AYLIK_TAH\|\d{4}-\d{2}-\d{2}\|", " ", text)
    plain = " ".join(plain.split()).strip()
    if plain:
        return plain
    for alt in (fatura_tarihi, tahsilat_tarihi):
        ym = _tahsil_rapor_yil_ay_coerce(alt)
        if ym:
            y, mo = ym
            if 1 <= mo <= 12:
                return f"{_TAHSIL_AYLAR_TR[mo - 1]} {y}"
    return "—"


# A5: 148mm x 210mm (ReportLab A5 = 420x595 pt)
A5_W_MM, A5_H_MM = 148, 210
MARGIN_MM, RIGHT_MM = 15, A5_W_MM - 15

# A4 ölçüleri (fatura için)
A4_W_MM, A4_H_MM = 210, 297


def _wrap_header(canvas, text, max_width_pt, font_name, font_size=9):
    """Başlık metnini sütun genişliğine göre satırlara böler (GİB tarzı okunaklı)."""
    if not text or max_width_pt <= 0:
        return [text or ""]
    canvas.setFont(font_name, font_size)
    words = text.replace("/", " / ").split()
    lines = []
    current = []
    current_w = 0
    for w in words:
        w_w = canvas.stringWidth(w + " ", font_name, font_size)
        if current and current_w + w_w > max_width_pt:
            lines.append(" ".join(current))
            current = [w]
            current_w = w_w
        else:
            current.append(w)
            current_w += w_w
    if current:
        lines.append(" ".join(current))
    return lines if lines else [text]


def _get_cek_list(tahsilat):
    """tahsilat içinden cek_detay'ı liste olarak döner."""
    raw = tahsilat.get("cek_detay")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw) if isinstance(raw, str) else []
    except (json.JSONDecodeError, TypeError):
        return []


def build_makbuz_pdf(tahsilat, musteri_adi, fatura_no=None, banka_hesaplar=None):
    """Tahsilat makbuzu (klasik form görünümü) PDF bytes döndürür."""
    _register_arial()
    font_name = "Arial" if "Arial" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    font_bold = "Arial-Bold" if "Arial-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"
    buf = io.BytesIO()
    from reportlab.lib.pagesizes import landscape
    w_pt, h_pt = landscape(A4)  # tek makbuz şablon ölçüsü
    c = canvas.Canvas(buf, pagesize=A4)  # çıktı: 1 A4 sayfada 2 makbuz
    c.setTitle("Tahsilat Makbuzu")
    h = h_pt
    c.beginForm("makbuz_form", 0, 0, w_pt, h_pt)

    def _fmt_tr(val):
        try:
            x = float(val or 0)
        except Exception:
            x = 0.0
        return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def _fmt_date_tr(v):
        s = str(v or "").strip()
        if not s:
            return datetime.now().strftime("%d.%m.%Y")
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            yy, mm_, dd = s[:10].split("-")
            return f"{dd}.{mm_}.{yy}"
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", s):
            return s
        return datetime.now().strftime("%d.%m.%Y")

    def _resolve_receipt_logo():
        here = os.path.dirname(os.path.abspath(__file__))
        cands = []
        for nm in (
            "Ofisbir Logo.jpg", "Ofisbir Logo.png",
            "ofisbir_logo.png", "ofisbir_logo.jpg",
            "ofisbir.png", "logo.png", "logo.jpg",
        ):
            cands.append(os.path.abspath(os.path.join(here, "..", "..", "assets", nm)))
            cands.append(os.path.abspath(os.path.join(here, "..", "static", nm)))
        for pth in cands:
            if os.path.isfile(pth):
                return pth
        return None

    x_left = 12 * mm
    x_right = w_pt - 12 * mm
    y_top = h - 14 * mm

    # Üst sol: logo (yoksa metin yok — sadece firma satırları görünür)
    logo_x = x_left
    logo_y = y_top - 18 * mm
    logo_w = 50 * mm
    logo_h = 18 * mm
    logo_path = _resolve_receipt_logo()
    if logo_path:
        try:
            c.drawImage(logo_path, logo_x, logo_y, width=logo_w, height=logo_h, preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    c.setFont(font_name, 8)
    firma_satir_1 = "Ofisbir Ofis ve Danışmanlık A.Ş."
    firma_satir_2 = "Kavaklıdere Mah. Esat Cad. No:12/1 06660 Çankaya/ANKARA"
    firma_satir_3 = f"Tel: {FIRMA_TELEFON} | {FIRMA_WEB} | {FIRMA_EMAIL} | VKN: {FIRMA_VERGI_NO}"
    c.drawString(x_left, logo_y - 4 * mm, firma_satir_1)
    c.drawString(x_left, logo_y - 8 * mm, firma_satir_2)
    c.drawString(x_left, logo_y - 12 * mm, firma_satir_3)
    if FIRMA_AKBANK_IBAN:
        c.drawString(x_left, logo_y - 16 * mm, f"AKBANK IBAN: {FIRMA_AKBANK_IBAN}")

    # Üst sağ: başlık + sıra no + tarih + tutar kutuları — sayfanın sağ kenarına yapışık
    label_w = 28 * mm
    value_w = 46 * mm
    box_right_edge = x_right                 # kutuların sağ kenarı sayfanın sağ kenarıyla aynı
    box_x = box_right_edge - (label_w + value_w)
    right_block_x = box_x                    # sıra no / tarih kutu blokunun sol kenarına hizalı
    c.setFont(font_bold, 14)
    c.drawRightString(box_right_edge, y_top, "TAHSİLAT MAKBUZU")
    c.setFont(font_name, 9)
    makbuz_no = str(tahsilat.get("makbuz_no") or get_next_makbuz_no())
    tarih_txt = _fmt_date_tr(tahsilat.get("tahsilat_tarihi"))
    saat_txt = ""
    _created_at = tahsilat.get("created_at")
    if _created_at:
        if hasattr(_created_at, "strftime"):
            saat_txt = _created_at.strftime("%H:%M")
        else:
            try:
                _dt = datetime.fromisoformat(str(_created_at).replace("Z", "+00:00"))
                saat_txt = _dt.strftime("%H:%M")
            except Exception:
                saat_txt = ""
    if not saat_txt:
        saat_txt = datetime.now().strftime("%H:%M")
    tarih_saat_txt = f"{tarih_txt} {saat_txt}"

    tutar = float(tahsilat.get("tutar") or 0)
    odeme = (tahsilat.get("odeme_turu") or "nakit").lower()
    cek_list = _get_cek_list(tahsilat) or []
    cek_tutar = sum(_parse_amount_flexible(c.get("tutar")) for c in cek_list)
    # Karma senaryo: toplam tutar varsa ve çek toplamı da varsa, kalan nakit kabul edilir.
    if cek_tutar > 0:
        toplam_tutar = max(tutar, cek_tutar)
        nakit_tutar = max(toplam_tutar - cek_tutar, 0.0)
    else:
        nakit_tutar = tutar if odeme == "nakit" else 0.0
        cek_tutar = tutar if odeme == "cek" else 0.0
        toplam_tutar = tutar

    box_y_top = y_top - 8 * mm
    row_h = 7 * mm
    row_inner_h = row_h - 1.2 * mm
    box_rows = (
        ("SIRA NO", makbuz_no),
        ("TARİH", tarih_saat_txt),
        ("NAKİT", _fmt_tr(nakit_tutar)),
        ("ÇEK", _fmt_tr(cek_tutar)),
        ("TOPLAM", _fmt_tr(toplam_tutar)),
    )
    c.setFont(font_bold, 8)
    for idx, (lbl, val) in enumerate(box_rows):
        rect_y = box_y_top - (idx + 1) * row_h + 0.8 * mm
        # drawString baseline verdiği için yazıları biraz yukarı taşıyoruz (çizgiye yapışmasın)
        text_y = rect_y + (row_inner_h / 2) - 1.5 * mm
        c.setFillColorRGB(0.24, 0.68, 0.88)
        c.rect(box_x, rect_y, label_w, row_inner_h, stroke=1, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.drawCentredString(box_x + label_w / 2, text_y, lbl)
        c.setFillColorRGB(1, 1, 1)
        c.rect(box_x + label_w, rect_y, value_w, row_inner_h, stroke=1, fill=0)
        c.setFillColorRGB(0, 0, 0)
        c.setFont(font_name, 9)
        c.drawRightString(box_x + label_w + value_w - 2 * mm, text_y, val)
        c.setFont(font_bold, 8)

    # Orta satırlar: kutu bloğunun altına dinamik bırak (ödeyen firma metni kutulara çarpmasın)
    box_bottom_y = box_y_top - (len(box_rows) * row_h) + 0.8 * mm
    line_top = min(y_top - 48 * mm, box_bottom_y - 6 * mm)
    c.setFont(font_name, 10)
    c.drawString(x_left, line_top, "Ödeyen Firma:")
    c.line(x_left + 24 * mm, line_top - 0.8 * mm, x_right, line_top - 0.8 * mm)
    c.setFont(font_bold, 9)
    c.drawString(x_left + 25 * mm, line_top + 0.2 * mm, (musteri_adi or "—")[:120])
    c.setFont(font_name, 10)

    line2 = line_top - 9 * mm
    _yazi_raw = (tutar_yaziya(tutar) or "").strip()
    _yazi_no_lira = _yazi_raw.replace("Türk Lirası", "").strip()
    _tr_lower = str.maketrans({"İ": "i", "I": "ı"})
    _kurus_yazi = ""
    _m = re.search(r"(\d{1,2})/100$", _yazi_no_lira)
    if _m:
        _k = int(_m.group(1))
        _yazi_no_lira = _yazi_no_lira[:_m.start()].strip()
        if _k:
            _kurus_word = tutar_yaziya(_k).replace("Türk Lirası", "").strip()
            _kurus_yazi = " " + _kurus_word.translate(_tr_lower).lower().strip() + " kuruş"
    _tl_part = re.sub(r"\s+", " ", _yazi_no_lira.translate(_tr_lower).lower()).strip()
    yazi_full = (_tl_part + " TL" + _kurus_yazi).strip()
    prefix_yalniz = "Hesabınıza kaydedilmek üzere yalnız,"
    suffix_tahsil = "tahsil edilmiştir."
    c.setFont(font_name, 10)
    c.drawString(x_left, line2, prefix_yalniz)
    prefix_w = c.stringWidth(prefix_yalniz, font_name, 10)
    suffix_w = c.stringWidth(suffix_tahsil, font_name, 10)
    yaz_x = x_left + prefix_w + 3 * mm                # "yalnız,"dan ~3mm sonra başlasın
    yaz_son = x_right - suffix_w - 3 * mm             # "tahsil edilmiştir."dan ~3mm önce bitsin
    if yaz_son < yaz_x + 20 * mm:
        yaz_son = yaz_x + 20 * mm
    c.line(yaz_x, line2 - 0.8 * mm, yaz_son, line2 - 0.8 * mm)
    c.setFont(font_bold, 9)
    c.drawString(yaz_x + 1 * mm, line2 + 0.2 * mm, yazi_full[:200])
    c.setFont(font_name, 10)
    c.drawRightString(x_right, line2, suffix_tahsil)
    c.line(x_left, line2 - 6 * mm, x_right, line2 - 6 * mm)

    # Çek tablo alanı
    table_top = line2 - 16 * mm
    c.setFont(font_name, 8)
    c.drawString(x_left, table_top, "Tahsil şartıyla alınan çekler")

    tx = x_left
    ty = table_top - 4 * mm
    tw = x_right - x_left
    cols = [0.22, 0.17, 0.15, 0.15, 0.15, 0.16]
    headers = ["BANKA", "ŞUBE", "HESAP NO", "ÇEK NO", "ÇEK TARİHİ", "TUTARI"]
    col_x = [tx]
    for ratio in cols[:-1]:
        col_x.append(col_x[-1] + tw * ratio)
    col_x.append(tx + tw)
    header_h = 7 * mm
    row_h2 = 8 * mm
    row_count = 5

    c.setFillColorRGB(0.24, 0.68, 0.88)
    c.rect(tx, ty - header_h, tw, header_h, stroke=1, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font_bold, 8)
    for i, htxt in enumerate(headers):
        cx = (col_x[i] + col_x[i + 1]) / 2
        c.drawCentredString(cx, ty - 4.9 * mm, htxt)
    c.setFillColorRGB(0, 0, 0)

    total_h = header_h + row_count * row_h2
    c.rect(tx, ty - total_h, tw, total_h, stroke=1, fill=0)
    for xv in col_x[1:-1]:
        c.line(xv, ty, xv, ty - total_h)
    for r in range(row_count):
        yy = ty - header_h - (r + 1) * row_h2
        c.line(tx, yy, tx + tw, yy)

    c.setFont(font_name, 8)
    for idx, row in enumerate(cek_list[:row_count]):
        yy = ty - header_h - idx * row_h2 - 5.2 * mm
        vals = [
            str(row.get("banka") or "")[:20],
            str(row.get("sube") or "")[:16],
            str(row.get("hesap_no") or "")[:16],
            str(row.get("cek_no") or "")[:16],
            str(row.get("vade") or "")[:12],
            _fmt_tr(row.get("tutar") or 0),
        ]
        for i, v in enumerate(vals):
            if i == len(vals) - 1:
                c.drawRightString(col_x[i + 1] - 2 * mm, yy, v)
            else:
                c.drawString(col_x[i] + 1.5 * mm, yy, v)

    cek_adet = len(cek_list)
    foot_y = ty - total_h - 7 * mm
    c.setFont(font_name, 9)
    c.drawString(x_left, foot_y, f"Toplam {cek_adet} adet çek alınmıştır.")
    tahsil_eden_txt = str(tahsilat.get("tahsil_eden") or "").strip()
    label_x = x_right - 78 * mm
    c.drawString(label_x, foot_y, "Tahsilatı Yapan:")
    if tahsil_eden_txt:
        c.setFont(font_bold, 9)
        c.drawRightString(x_right, foot_y + 0.2 * mm, tahsil_eden_txt[:45])
        c.setFont(font_name, 9)

    # Açıklama + ilgili fatura (alt not)
    aciklama = (tahsilat.get("aciklama") or "").strip()
    if aciklama or fatura_no:
        note_y = foot_y - 8 * mm
        c.setFont(font_name, 8)
        if aciklama:
            c.drawString(x_left, note_y, f"Açıklama: {aciklama[:90]}")
            note_y -= 4 * mm
        if fatura_no:
            c.drawString(x_left, note_y, f"İlgili Fatura: {fatura_no}")

    c.endForm()

    # 1 A4'e iki kopya: üst ve alt (kesip müşteri + dosya kopyası)
    page_w_pt, page_h_pt = A4
    scale = page_w_pt / w_pt
    copy_h_pt = h_pt * scale
    for y_off in (0, copy_h_pt):
        c.saveState()
        c.translate(0, y_off)
        c.scale(scale, scale)
        c.doForm("makbuz_form")
        c.restoreState()

    # Kesim rehberi (hafif kesikli çizgi)
    c.setStrokeColorRGB(0.65, 0.65, 0.65)
    c.setDash(3, 2)
    c.line(8 * mm, copy_h_pt, page_w_pt - 8 * mm, copy_h_pt)
    c.setDash()
    c.setStrokeColorRGB(0, 0, 0)

    c.save()
    buf.seek(0)
    return buf.getvalue()


def _pdf_irsaliye_modu(fatura):
    """Belge irsaliye akışından mı? (API bool, notlar etiketi veya fatura_tipi yedekleri)."""
    if not isinstance(fatura, dict):
        return False
    v = fatura.get("irsaliye_modu")
    if v is True:
        return True
    if v is False or v is None:
        pass
    else:
        if str(v).strip().lower() in ("1", "true", "yes", "on"):
            return True
    notlar = fatura.get("notlar")
    if notlar and "IRSALIYE_MODU" in str(notlar):
        return True
    ft = turkish_lower(str(fatura.get("fatura_tipi") or "")).replace(" ", "").replace("-", "")
    if "irsaliye" in ft:
        return True
    return False


def build_fatura_pdf(fatura, musteri, satirlar, preview=False):
    """e-Arşiv tarzı A4 fatura / e-irsaliye PDF.

    preview=True: kayıt öncesi form önizlemesi — QR üretimi atlanır (daha hızlı).
    """
    _register_arial()
    font_name = "Arial" if "Arial" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    font_bold = "Arial-Bold" if "Arial-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"
    buf = io.BytesIO()
    w_pt, h_pt = A4
    irsaliye_modu = _pdf_irsaliye_modu(fatura)
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("İrsaliye Önizleme" if irsaliye_modu else "Fatura Önizleme")

    # Kenarlar
    margin = 10 * mm
    right = (A4_W_MM - 10) * mm

    # Tarih: gönderici üst çift çizgisinin 3 mm üstü, sol hiza = şirket unvanı (left_x) — çizim sender_top sonrası
    now = datetime.now()
    ust_tarih = fatura.get("fatura_tarihi_str") or now.strftime("%d.%m.%Y %H:%M")

    # --- Kurumsal üst/alt bloklar (örnek görsel gibi çift çizgi) ---
    def _double_hr(y_pt, line_w=0.8, gap_pt=1.6):
        """Tam sayfa genişliğinde çift yatay çizgi."""
        c.setLineWidth(line_w)
        c.line(margin, y_pt, right, y_pt)
        c.line(margin, y_pt - gap_pt, right, y_pt - gap_pt)

    def _double_hr_segment(x1_pt, x2_pt, y_pt, line_w=0.8, gap_pt=1.6):
        """Belirli bir yatay aralıkta (ör: sadece şirket bloğu genişliğinde) çift çizgi."""
        c.setLineWidth(line_w)
        c.line(x1_pt, y_pt, x2_pt, y_pt)
        c.line(x1_pt, y_pt - gap_pt, x2_pt, y_pt - gap_pt)

    def _wrap_text(text, max_w_pt, font_nm, font_sz):
        """Kelime bazlı wrap + break-word."""
        t = (text or "").strip()
        if not t:
            return [""]
        c.setFont(font_nm, font_sz)
        words = t.split()
        if not words:
            return [t]
        out = []
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip() if cur else w
            if c.stringWidth(cand, font_nm, font_sz) <= max_w_pt:
                cur = cand
                continue
            if cur:
                out.append(cur)
                cur = ""
            if c.stringWidth(w, font_nm, font_sz) <= max_w_pt:
                cur = w
            else:
                chunk = ""
                for ch in w:
                    cand2 = chunk + ch
                    if c.stringWidth(cand2, font_nm, font_sz) <= max_w_pt:
                        chunk = cand2
                    else:
                        if chunk:
                            out.append(chunk)
                        chunk = ch
                cur = chunk
        if cur:
            out.append(cur)
        return out or [t]

    def _sanitize_addr_text(text):
        """Adres bloğunda placeholder satırları tek satırda topla."""
        out = []
        placeholders = []
        for raw in str(text or "").split("\n"):
            ln = (raw or "").strip()
            if not ln:
                continue
            n = (
                ln.lower()
                .replace("ı", "i")
                .replace("İ", "i")
                .replace("ş", "s")
                .replace("ğ", "g")
                .replace("ü", "u")
                .replace("ö", "o")
                .replace("ç", "c")
                .replace(".", "")
                .replace(":", "")
                .strip()
            )
            # Değeri girilmemiş placeholder satırları biriktir: No:, Kapı No:, / Türkiye, Web Sitesi:
            if n in ("no", "kapi no", "/ turkiye", "turkiye", "web sitesi", "web sitesi /"):
                placeholders.append(ln)
                continue
            out.append(ln)
        if placeholders:
            out.append(" ".join(placeholders))
        return "\n".join(out)

    def _draw_labeled_line(x_pt, y_pt, label, value, label_w_pt, value_max_w_pt=None):
        c.setFont(font_name, 8)
        c.drawString(x_pt, y_pt, (label or "")[:30])
        if value_max_w_pt:
            v_lines = _wrap_text(value, value_max_w_pt, font_name, 8)
            c.drawString(x_pt + label_w_pt, y_pt, (v_lines[0] if v_lines else "")[:95])
        else:
            c.drawString(x_pt + label_w_pt, y_pt, (value or "")[:95])

    company = (fatura.get("current_user_company") or fatura.get("company") or {}) if isinstance(fatura, dict) else {}
    sender_unvan = (company.get("unvan") or FIRMA_UNVAN or "").strip()
    sender_adres = _sanitize_addr_text((company.get("adres") or FIRMA_ADRES or "").strip())
    sender_tel = (company.get("telefon") or FIRMA_TELEFON or "").strip() or "—"
    sender_web = (company.get("web") or FIRMA_WEB or "").strip() or "—"
    sender_vd = _normalize_vergi_dairesi(company.get("vergi_dairesi") or FIRMA_VERGI_DAIRESI or "")
    sender_vkn = (company.get("vkn") or FIRMA_VERGI_NO or "").strip() or "—"

    client = (fatura.get("active_invoice_client") or fatura.get("client") or {}) if isinstance(fatura, dict) else {}
    # mevcut musteri dict'i ile birleştir (varsa client öncelikli)
    must = {}
    must.update(musteri or {})
    must.update(client or {})
    alici_unvan = (must.get("unvan") or must.get("sirket_unvani") or must.get("name") or fatura.get("musteri_adi") or "Müşteri").strip()
    alici_adres = _sanitize_addr_text((must.get("yeni_adres") or must.get("address") or "").strip()) or "—"
    sevk_adresi_pdf = (
        str(fatura.get("sevk_adresi") or fatura.get("sevk_adres") or must.get("sevk_adresi") or "")
        .replace("\r\n", "\n")
        .strip()
    )
    alici_vd = _normalize_vergi_dairesi(must.get("vergi_dairesi") or "")
    alici_vkn = str(must.get("vergi_no") or must.get("tax_number") or "—").strip()
    pdf_ettn = _resolve_ettn_for_pdf(fatura.get("ettn"), fatura.get("id"))

    # Gönderici bloğu (sol şirket | gutter GİB logosu | sağ meta/QR)
    sender_top = h_pt - 14 * mm
    gutter = 10 * mm
    left_col_w = (right - margin) * 0.40
    right_col_w = (right - margin) - left_col_w - gutter
    left_x = margin
    right_x = margin + left_col_w + gutter
    gutter_pad = 1.5 * mm
    gutter_left = left_x + left_col_w + gutter_pad
    gutter_right = right_x - gutter_pad
    gutter_cx = (gutter_left + gutter_right) / 2.0

    alici_block_pad_x = 2 * mm
    alici_inner_right = left_x + left_col_w - alici_block_pad_x

    # Orta üst: GİB logosu + belge türü — koordinatlar burada; çizim kalemler öncesi (üst katman)
    gib_path = _resolve_gib_logo_path()
    _gib_forced = os.path.join(os.getcwd(), "BestOfficeERP", "assets", "gib_logo.png")
    if _fatura_pdf_debug():
        print(
            f"[fatura PDF] GIB zorunlu yol: {_gib_forced} "
            f"exists={os.path.exists(_gib_forced)} isfile={os.path.isfile(_gib_forced)}"
        )
    if os.path.isfile(_gib_forced):
        gib_path = _gib_forced
    if irsaliye_modu:
        doc_subtitle = "e-İRSALİYE"
    else:
        ft_low = turkish_lower(str(fatura.get("fatura_tipi") or ""))
        if "efatura" in ft_low.replace(" ", "") or ft_low in ("efatura", "e-fatura"):
            doc_subtitle = "e-FATURA"
        else:
            doc_subtitle = "e-Arşiv Fatura"
    doc_subtitle_pdf = doc_subtitle

    # GİB logosu: sender_bottom sonrası OFİSBİR üst çizgisi … VKN alt çift çizgi arası + sayfa ortası
    _dhr_gap_pt = 1.6
    # Çift çizgi altından sonraki blok üst çizgisine kadar boşluk (≈ eski sender_bottom - 18); SAYIN/SEVK aynı
    BLOCK_GAP_BELOW_DOUBLE_PT = 18 - _dhr_gap_pt
    BLOCK_GAP_MM = BLOCK_GAP_BELOW_DOUBLE_PT  # eski isim kaldıysa NameError önleme (pt cinsinden aynı değer)
    ar = _gib_logo_aspect_ratio(gib_path)
    GIB_LOGO_H_FIXED = 25 * mm
    GIB_LOGO_H_MAX = GIB_LOGO_H_FIXED  # geriye uyum (eski kod / kopyalar)
    GIB_LOGO_LEFT_FROM_PAGE = 85 * mm  # sayfa sol kenarından logo solu (statik)
    GIB_LOGO_SIDE_CLEAR = 10 * mm  # gönderici sağı ve QR solu ile minimum boşluk
    GIB_LOGO_GAP_AFTER_SENDER = 20 * mm  # eski sabit adı (yerel/karışık kopya NameError önleme)
    SUBTITLE_BELOW_LOGO_MM = 3 * mm
    logo_h = GIB_LOGO_H_FIXED
    logo_draw_w = GIB_LOGO_H_FIXED * ar
    logo_ll_x = 0.0
    logo_ll_y = 0.0
    sub_baseline = 0.0

    # QR kare boyutu (ETTN hizalı meta üstünde)
    qr_size = min(26 * mm, max(20 * mm, right_col_w * 0.88))
    qr_x = 0.0
    qr_y = 0.0

    qr_pil_image = None
    if not preview:
        try:
            import qrcode
            import qrcode.constants

            qr_data = _gib_dogrulama_qr_url(fatura, sender_vkn, alici_vkn, pdf_ettn)
            try:
                qr = qrcode.QRCode(
                    version=1,
                    box_size=3,
                    border=0,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                )
                qr.add_data(qr_data)
                qr.make(fit=True)
                qr_pil_image = qr.make_image(fill_color="black", back_color="white")
                if _fatura_pdf_debug():
                    print(f"[fatura PDF] QR PIL (v1, M), veri uzunluğu={len(qr_data)}")
            except Exception as e_v1:
                if _fatura_pdf_debug():
                    print(f"[fatura PDF] QR v1 başarısız ({e_v1}), auto version.")
                qr = qrcode.QRCode(
                    version=None,
                    box_size=3,
                    border=0,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                )
                qr.add_data(qr_data)
                qr.make(fit=True)
                qr_pil_image = qr.make_image(fill_color="black", back_color="white")
                if _fatura_pdf_debug():
                    print(f"[fatura PDF] QR PIL (auto, M), veri uzunluğu={len(qr_data)}")
        except Exception as e_qr:
            if _fatura_pdf_debug():
                print(f"[fatura PDF] QR üretilemedi: {e_qr}")
            qr_pil_image = None

    # Tarih: üst çift çizginin 3 mm üstü, unvan ile aynı sol hiza (overlay’de tekrar basılır)
    date_header_y = sender_top + 3 * mm
    c.setFont(font_name, 8)
    c.drawString(left_x, date_header_y, ust_tarih)

    _double_hr_segment(left_x, left_x + left_col_w, sender_top)
    sender_y = sender_top - 12

    # Sol sütun: şirket bilgileri (wrap'li)
    c.setFont(font_bold, 9.5)
    for ln in _wrap_text(sender_unvan, left_col_w, font_bold, 9.5)[:2]:
        c.drawString(left_x, sender_y, ln[:110])
        sender_y -= 12
    c.setFont(font_name, 8)
    addr_lines = []
    for raw_ln in sender_adres.split("\\n"):
        raw_ln = (raw_ln or "").strip()
        if not raw_ln:
            continue
        addr_lines.extend(_wrap_text(raw_ln, left_col_w, font_name, 8))
    for ln in addr_lines[:3]:
        c.drawString(left_x, sender_y, ln[:120])
        sender_y -= 11

    label_w = 28 * mm
    value_w = max(20, left_col_w - label_w)
    _draw_labeled_line(left_x, sender_y, "Tel:", sender_tel, label_w, value_max_w_pt=value_w)
    sender_y -= 11
    _draw_labeled_line(left_x, sender_y, "Web Sitesi:", sender_web, label_w, value_max_w_pt=value_w)
    sender_y -= 11
    _draw_labeled_line(left_x, sender_y, "Vergi Dairesi:", sender_vd, label_w, value_max_w_pt=value_w)
    sender_y -= 11
    # VKN satırı için y konumunu ayrı değişkende tut (çizgiyi buna yapıştıracağız)
    vkn_y = sender_y
    _draw_labeled_line(left_x, vkn_y, "VKN:", sender_vkn, label_w, value_max_w_pt=value_w)

    # Sağ meta tablo: start_y sonrası çizilir; logo/QR üst katman _flush_gib_overlay ile
    try:
        from datetime import datetime as _dt
        ft_raw = fatura.get("fatura_tarihi") or now.strftime("%Y-%m-%d")
        try:
            dt_ft = _dt.strptime(str(ft_raw)[:10], "%Y-%m-%d")
        except Exception:
            dt_ft = now
        irs_tarih = fatura.get("irsaliye_tarihi") or dt_ft.strftime("%d-%m-%Y")
        irs_saat = fatura.get("irsaliye_saati") or now.strftime("%H:%M:%S")
        sevk_tarih = fatura.get("sevk_tarihi") or irs_tarih
        sevk_saat = fatura.get("sevk_saati") or irs_saat
        yil = dt_ft.year
        import re as _re
        num_raw = "".join(_re.findall(r"\d", str(fatura.get("fatura_no") or ""))) or "1"
        num_val = int(num_raw[-9:]) if num_raw else 1
        irs_no = f"GRS{yil}{num_val:09d}"
    except Exception:
        dt_ft = now
        irs_tarih = now.strftime("%d-%m-%Y")
        irs_saat = now.strftime("%H:%M:%S")
        sevk_tarih = irs_tarih
        sevk_saat = irs_saat
        irs_no = "GRS" + now.strftime("%Y") + "000000001"

    fat_tarih_dm = dt_ft.strftime("%d-%m-%Y")
    fat_saat = (fatura.get("fatura_saati") or fatura.get("irsaliye_saati") or now.strftime("%H:%M:%S"))
    fat_tip_disp = (str(fatura.get("fatura_tipi") or "SATIŞ")).strip().upper() or "SATIŞ"
    fat_no_goster = (str(fatura.get("fatura_no") or "").strip() or irs_no)[:32]

    if irsaliye_modu:
        meta_lines = [
            ("Özelleştirme No", "TR1.2.1"),
            ("Senaryo", "TEMELIRSALIYE"),
            ("İrsaliye Tipi", (fatura.get("irsaliye_tipi") or "SEVK")),
            ("İrsaliye No", irs_no),
            ("İrsaliye Tarihi", irs_tarih),
            ("İrsaliye Zamanı", irs_saat),
            ("Sevk Tarihi", sevk_tarih),
            ("Sevk Zamanı", sevk_saat),
        ]
    else:
        meta_lines = [
            ("Özelleştirme No", "TR1.2"),
            ("Senaryo", "EARSIVFATURA"),
            ("Fatura Tipi", fat_tip_disp[:24]),
            ("Fatura No", fat_no_goster),
            ("Fatura Tarihi", fat_tarih_dm),
            ("Fatura Zamanı", str(fat_saat)[:16]),
        ]

    # Gönderici bloğu alt çizgisi: sadece sol sütunda; meta kutu sağda olduğu için
    # kutu altı (box_top - box_height) ile sınırlamak VKN ile çizgi arasında büyük boşluk yaratıyordu.
    # Üst çift çizginin ilk çizgisi VKN baseline'ının ~2 pt (~0,7 mm) altında.
    gap_below_vkn_pt = 2
    sender_bottom = vkn_y - gap_below_vkn_pt
    _double_hr_segment(left_x, left_x + left_col_w, sender_bottom)

    # GİB: yükseklik 25 mm; dikey orta = üst çift (sender_top) … alt çift alt çizgisi (sender_bottom - gap)
    band_high_y = sender_top
    band_low_y = sender_bottom - _dhr_gap_pt
    logo_cy = (band_high_y + band_low_y) / 2.0
    logo_w_nat = logo_h * ar
    max_logo_w = (right - margin) * 0.46
    logo_draw_w = min(logo_w_nat, max_logo_w)

    # Alıcı bloğu (header ile aynı genişlikte, sol sınırlı)
    # Boşluk: BLOCK_GAP_BELOW_DOUBLE_PT (GİB bölümünde tanımlı)
    SAYIN_TOP_PAD_BELOW_DOUBLE_PT = 14
    sender_hr_lower_y = sender_bottom - _dhr_gap_pt
    y_pt = sender_hr_lower_y - BLOCK_GAP_BELOW_DOUBLE_PT
    y_sayin_hr_top = y_pt
    _double_hr_segment(left_x, left_x + left_col_w, y_pt)
    y_pt -= SAYIN_TOP_PAD_BELOW_DOUBLE_PT
    c.setFont(font_bold, 9.5)
    c.drawString(margin, y_pt, "SAYIN")
    y_pt -= 12
    c.setFont(font_bold, 8.5)
    c.drawString(margin, y_pt, alici_unvan[:95])
    y_pt -= 12
    c.setFont(font_name, 8)
    for ln in str(alici_adres).split("\\n"):
        if not ln.strip():
            continue
        c.drawString(margin, y_pt, ln.strip()[:105])
        y_pt -= 11
    _draw_labeled_line(margin, y_pt, "Vergi Dairesi:", alici_vd, label_w)
    y_pt -= 11
    _draw_labeled_line(margin, y_pt, "VKN/TCKN:", alici_vkn, label_w)
    y_pt -= 10
    sayin_bottom_hr_top = y_pt
    sayin_inner_top_y = y_sayin_hr_top - _dhr_gap_pt
    sayin_inner_height_pt = sayin_inner_top_y - sayin_bottom_hr_top
    _double_hr_segment(left_x, left_x + left_col_w, y_pt)

    # Sevk adresi (alıcı adresinden farklı; boşsa blok çizilmez)
    # Üst boşluk: gönderici–SAYIN ile aynı (BLOCK_GAP_BELOW_DOUBLE_PT)
    ettn_anchor_hr_top = sayin_bottom_hr_top
    if sevk_adresi_pdf:
        sevk_val_x = margin
        wrap_max = max(40, alici_inner_right - sevk_val_x)
        sevk_lines = []
        for raw_ln in sevk_adresi_pdf.split("\n"):
            raw_ln = (raw_ln or "").strip()
            if not raw_ln:
                continue
            sevk_lines.extend(_wrap_text(raw_ln, wrap_max, font_name, 8))
        if sevk_lines:
            sayin_hr_lower_y = sayin_bottom_hr_top - _dhr_gap_pt
            y_sevk_open_top = sayin_hr_lower_y - BLOCK_GAP_BELOW_DOUBLE_PT
            _double_hr_segment(left_x, left_x + left_col_w, y_sevk_open_top)
            y_sevk_title = y_sevk_open_top - SAYIN_TOP_PAD_BELOW_DOUBLE_PT
            c.setFont(font_bold, 8.5)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(left_x, y_sevk_title, "SEVK ADRESİ:")
            last_txt_baseline = y_sevk_title
            c.setFont(font_name, 8)
            y_sevk_line = y_sevk_title - 12
            for ln in sevk_lines:
                c.drawString(sevk_val_x, y_sevk_line, (ln or "")[:220])
                last_txt_baseline = y_sevk_line
                y_sevk_line -= 11
            top_sevk_inner_y = y_sevk_open_top - _dhr_gap_pt
            y_close_for_min_height = top_sevk_inner_y - sayin_inner_height_pt
            y_close_after_text = last_txt_baseline - 5 * mm
            y_sevk_close_top = min(y_close_after_text, y_close_for_min_height)
            _double_hr_segment(left_x, left_x + left_col_w, y_sevk_close_top)
            ettn_anchor_hr_top = y_sevk_close_top

    # ETTN satırı (kurumsal tasarım: alıcı / sevk bloğu alt çizgisinin hemen altında; QR ile aynı kod)
    ettn = pdf_ettn
    ettn_baseline_y = ettn_anchor_hr_top - 14
    ettn_fs = 8.5
    ettn_lbl = "ETTN:"
    c.setFont(font_bold, ettn_fs)
    ettn_lbl_w = c.stringWidth(ettn_lbl, font_bold, ettn_fs)
    c.drawString(margin, ettn_baseline_y, ettn_lbl)
    c.setFont(font_name, ettn_fs)
    ettn_val_x = margin + ettn_lbl_w + c.stringWidth(" ", font_name, ettn_fs)
    c.drawString(ettn_val_x, ettn_baseline_y, str(ettn)[:48])

    # Mal/Hizmet tablosu — GİB tarzı: başlık üst border, çok satırlı okunaklı başlıklar
    table_width = right - margin
    # Tablo üst çizgisi ETTN'e yapışık: önceki min(..., h_pt - 95mm) tabloyu sayfada aşağı sabitliyor,
    # ETTN ile tablo arasında büyük beyaz boşluk bırakıyordu.
    descent_below_baseline_pt = 2.5
    gap_ettn_to_table_top_pt = 2.5
    start_y = ettn_baseline_y - descent_below_baseline_pt - gap_ettn_to_table_top_pt
    header_row_h = 26   # pt, başlıkları daha kompakt tut
    row_h = 10.5        # pt, veri + boş satırlar
    cell_pad_vert = 3   # pt, satır altı boşluk
    data_text_offset = 0.5 * mm
    summary_row_h = 10.5   # pt, özet kutusu satırları
    GAP_TABLE_TO_SUMMARY = 1 * mm
    GAP_SUMMARY_TO_YALNIZ = 2.5 * mm
    header_line_h = 10  # pt, başlık satırı aralığı
    header_font_ascent = 8   # 9pt font yaklaşık ascent (dikey ortalama için)
    c.setFont(font_bold, 9)
    # GİB e-Arşiv fatura başlıkları (dar sütunlarda kısa metin, taşma olmasın)
    cols = [
        "Sıra No",
        "Mal Hizmet",
        "Miktar",
        "Birim",
        "Birim Fiyat",
        "İsk./Artt. Oranı",
        "İsk./Artt. Tutarı",
        "KDV Oranı",
        "KDV Tutarı",
        "Mal Hizmet Tutarı",
    ]
    # Sütun oranları (toplam 180): uzun ürün adında "Mal Hizmet" kolonu dinamik genişlesin.
    w_ratios = [8, 46, 11, 11, 18, 13, 21, 12, 20, 20]
    try:
        max_name_len = 0
        for srow in (satirlar or []):
            nm = str((srow or {}).get("ad") or (srow or {}).get("hizmet_urun_adi") or "").strip()
            if len(nm) > max_name_len:
                max_name_len = len(nm)
        # 34 karakter üzerini uzun kabul et; Mal/Hizmet'e +8'e kadar alan ver.
        extra = min(8, max(0, int((max_name_len - 34) / 6)))
        if extra > 0:
            w_ratios[1] += extra
            # Alanı toplamdan düşmek için daraltılabilir kolonlar: tutar/iskonto/kdv.
            for idx in (9, 8, 6, 5, 4, 7):
                if extra <= 0:
                    break
                min_w = 11 if idx in (5, 7) else 14
                can_take = max(0, w_ratios[idx] - min_w)
                take = min(extra, can_take)
                if take > 0:
                    w_ratios[idx] -= take
                    extra -= take
    except Exception:
        pass
    widths = [table_width * (r / 180.0) for r in w_ratios]
    table_right = margin + table_width

    # Sağ meta + QR: son satır baseline = ETTN ile aynı Y; sağ kenar = table_right (start_y ile konum yok)
    meta_row_min_pt = 11.5
    meta_font_size = 6.5
    box_height = max(28 * mm, len(meta_lines) * meta_row_min_pt)
    box_width = min(62 * mm, max(28 * mm, table_right - right_x))
    box_left = table_right - box_width
    n_meta = len(meta_lines)
    row_h_meta = box_height / float(n_meta)
    box_top = ettn_baseline_y + row_h_meta * (n_meta - 0.5) + 0.32 * meta_font_size
    qr_meta_gap = 2 * mm
    qr_y_min = box_top + qr_meta_gap
    # QR üst kenarı = gönderici üst çift çizgisi (sender_top); sağ = table_right
    qr_size = max(16 * mm, sender_top - qr_y_min)
    qr_y = sender_top - qr_size
    if qr_y < qr_y_min:
        qr_y = qr_y_min
        qr_size = max(16 * mm, sender_top - qr_y)
    qr_x = table_right - qr_size

    # GİB logo x/y: qr_size burada kesinleşti; 85 mm + 10 mm gönderici/QR payı
    _sender_block_right = left_x + left_col_w
    _logo_x_min = _sender_block_right + GIB_LOGO_SIDE_CLEAR
    _logo_x_max = qr_x - GIB_LOGO_SIDE_CLEAR - logo_draw_w
    if _logo_x_max < _logo_x_min:
        logo_ll_x = _logo_x_min
    else:
        logo_ll_x = max(_logo_x_min, min(GIB_LOGO_LEFT_FROM_PAGE, _logo_x_max))
    logo_ll_y = logo_cy - logo_h / 2.0
    sub_baseline = logo_ll_y - SUBTITLE_BELOW_LOGO_MM

    box_pad_x = 2.2 * mm
    gutter_cols = 1.4 * mm
    label_col_frac = 0.42
    label_col_right = box_left + box_width * label_col_frac
    lbl_max_w = label_col_right - box_left - box_pad_x - gutter_cols
    val_max_w = (box_left + box_width - box_pad_x) - label_col_right - gutter_cols
    val_draw_right_x = box_left + box_width - box_pad_x

    def _meta_fit_sizes(txt, max_w_pt, sizes_tuple):
        t = str(txt or "")
        for fs in sizes_tuple:
            c.setFont(font_name, fs)
            if c.stringWidth(t, font_name, fs) <= max_w_pt:
                return t, fs
        fs = sizes_tuple[-1]
        c.setFont(font_name, fs)
        ell = "…"
        if c.stringWidth(ell, font_name, fs) > max_w_pt:
            return "", fs
        tt = t
        while len(tt) > 0 and c.stringWidth(tt + ell, font_name, fs) > max_w_pt:
            tt = tt[:-1]
        return (tt + ell) if tt else "", fs

    c.rect(box_left, box_top - box_height, box_width, box_height)
    for idx, (lbl, val) in enumerate(meta_lines):
        y0 = box_top - row_h_meta * idx
        c.line(box_left, y0, box_left + box_width, y0)
        cell_mid_y = y0 - row_h_meta / 2
        baseline_y = cell_mid_y - 0.32 * meta_font_size

        if idx == n_meta - 1:
            baseline_y = ettn_baseline_y
            c.setFont(font_name, meta_font_size)
            c.drawString(box_left + box_pad_x, baseline_y, (lbl or "")[:28])
            c.drawRightString(val_draw_right_x, baseline_y, (str(val) or "")[:22])
            continue

        s_lbl, fs_l = _meta_fit_sizes(lbl, lbl_max_w, (meta_font_size, 6.0))
        c.setFont(font_name, fs_l)
        c.drawString(box_left + box_pad_x, baseline_y, s_lbl)

        s_val, fs_v = _meta_fit_sizes(val, val_max_w, (meta_font_size, 6.0, 5.5))
        c.setFont(font_name, fs_v)
        c.drawRightString(val_draw_right_x, baseline_y, s_val)

    # Başlık üst border (GİB gibi)
    c.line(margin, start_y, table_right, start_y)

    cell_pad_h = 5   # yatay boşluk, yazı dikey sınırlara değmesin
    x = margin
    for i, col in enumerate(cols):
        w_pt = widths[i]
        max_w = max(18, w_pt - cell_pad_h * 2)  # taşma olmaması için sıkı sığdırma
        lines = _wrap_header(c, col, max_w, font_bold, 9)
        block_h = len(lines) * header_line_h
        # Blok merkezi = satır merkezi; üst/alt çizgiye değmesin
        first_baseline = start_y - (header_row_h / 2) - header_font_ascent + (block_h / 2)
        for j, line in enumerate(lines):
            # Hücre içinde soldan padding, taşan satır çizilmesin
            c.drawString(x + cell_pad_h, first_baseline - j * header_line_h, line)
        x += w_pt
    c.line(margin, start_y - header_row_h, table_right, start_y - header_row_h)

    # Veri satırları — ilk satır (1 Sanal ofis...) daha yüksek, yazı üst/alt çizgiye değmesin
    c.setFont(font_name, 8.5)
    y_line = start_y - header_row_h - 5
    sira = 1
    pad = 5

    def _wrap_cell_text(text, max_width_pt, font_nm, font_sz):
        """Hücre metni: normal wrap + break-word (uzun kelimeyi böler)."""
        t = (text or "").strip()
        if not t:
            return [""]
        c.setFont(font_nm, font_sz)
        words = t.split()
        if not words:
            return [t]
        lines = []
        cur = ""
        for w in words:
            candidate = (cur + " " + w).strip() if cur else w
            if c.stringWidth(candidate, font_nm, font_sz) <= max_width_pt:
                cur = candidate
                continue
            if cur:
                lines.append(cur)
                cur = ""
            # break-word: tek kelime bile sığmıyorsa parçala
            if c.stringWidth(w, font_nm, font_sz) <= max_width_pt:
                cur = w
            else:
                chunk = ""
                for ch in w:
                    cand2 = chunk + ch
                    if c.stringWidth(cand2, font_nm, font_sz) <= max_width_pt:
                        chunk = cand2
                    else:
                        if chunk:
                            lines.append(chunk)
                        chunk = ch
                cur = chunk
        if cur:
            lines.append(cur)
        return lines or [t]

    def _force_lines_to_max_width(lines, max_width_pt, font_nm, font_sz):
        """Kelime sarması bazen genişliği aşar (Türkçe glif / font ölçümü); satırı güvenli biçimde böl.
        Mümkünse boşluk/tire üzerinden bölünür (AĞUSTOS → AĞUSTO|S gibi hatalı harf kırığını önler)."""
        c.setFont(font_nm, font_sz)
        mw = max(8.0, float(max_width_pt) - 3.0)
        out = []
        for ln in list(lines or [""]):
            s = ln or ""
            while s:
                if c.stringWidth(s, font_nm, font_sz) <= mw:
                    out.append(s)
                    break
                lo, hi = 1, len(s)
                best = 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if c.stringWidth(s[:mid], font_nm, font_sz) <= mw:
                        best = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1
                take = max(1, best)
                # Kelime ortasında kırma: geriye doğru boşluk, tire veya em-dash ara
                if take < len(s) and s[take - 1] not in (" ", "\t"):
                    cut = -1
                    for sep in (" ", "\t", "—", "-", "/"):
                        p = s.rfind(sep, 0, take)
                        if p >= 1:
                            cand = s[:p].rstrip()
                            if cand and c.stringWidth(cand, font_nm, font_sz) <= mw:
                                cut = p
                                break
                    if cut > 0:
                        take = cut
                chunk = s[:take].rstrip()
                if not chunk:
                    chunk = s[:1]
                    take = 1
                out.append(chunk)
                s = s[take:].lstrip()
        return out if out else [""]

    # Logo + alt başlık + QR + tarih: en son (z-order); tarih/ başlık kaybolmasın diye tekrar
    def _flush_gib_overlay():
        c.saveState()
        try:
            c.setFillColorRGB(0, 0, 0)
            c.setFont(font_name, 9)
            c.drawString(left_x, date_header_y, ust_tarih)
            if gib_path and os.path.isfile(gib_path):
                try:
                    _gib_r = _gib_logo_reader(gib_path)
                    if _gib_r is not None:
                        c.drawImage(
                            _gib_r,
                            logo_ll_x,
                            logo_ll_y,
                            width=logo_draw_w,
                            height=logo_h,
                            preserveAspectRatio=True,
                            mask="auto",
                        )
                    else:
                        c.drawImage(
                            gib_path,
                            logo_ll_x,
                            logo_ll_y,
                            width=logo_draw_w,
                            height=logo_h,
                            preserveAspectRatio=True,
                            mask="auto",
                        )
                    if _fatura_pdf_debug():
                        print(f"[fatura PDF] GIB logo drawImage tamam: {gib_path}")
                except Exception as ex_logo:
                    if _fatura_pdf_debug():
                        print(f"[fatura PDF] GIB logo drawImage hata: {ex_logo}")
            else:
                if _fatura_pdf_debug():
                    print("[fatura PDF] GIB logo dosyası yok")
            c.setFillColorRGB(0, 0, 0)
            c.setStrokeColorRGB(0, 0, 0)
            c.setFont(font_bold, 12)
            sub_txt = doc_subtitle_pdf or ""
            logo_cx = logo_ll_x + logo_draw_w / 2.0
            _sr = left_x + left_col_w
            _half_avail = min(logo_cx - _sr - GIB_LOGO_SIDE_CLEAR, qr_x - GIB_LOGO_SIDE_CLEAR - logo_cx)
            _max_tw = max(24 * mm, 2.0 * max(0, _half_avail))
            while sub_txt and c.stringWidth(sub_txt, font_bold, 12) > _max_tw:
                sub_txt = sub_txt[:-1]
            if sub_txt != doc_subtitle_pdf and sub_txt:
                sub_txt = sub_txt.rstrip() + "…"
            display_sub = sub_txt or (doc_subtitle_pdf or "")[:28]
            c.drawCentredString(logo_cx, sub_baseline, display_sub)
            if qr_pil_image is not None:
                try:
                    c.drawInlineImage(
                        qr_pil_image,
                        qr_x,
                        qr_y,
                        width=qr_size,
                        height=qr_size,
                        preserveAspectRatio=True,
                    )
                    if _fatura_pdf_debug():
                        print("[fatura PDF] QR drawInlineImage tamam")
                except Exception as ex_qr:
                    if _fatura_pdf_debug():
                        print(f"[fatura PDF] QR drawInlineImage hata: {ex_qr}, ImageReader deneniyor.")
                    try:
                        from reportlab.lib.utils import ImageReader

                        _b = io.BytesIO()
                        qr_pil_image.save(_b, format="PNG")
                        _b.seek(0)
                        c.drawImage(
                            ImageReader(_b),
                            qr_x,
                            qr_y,
                            width=qr_size,
                            height=qr_size,
                            preserveAspectRatio=True,
                            mask="auto",
                        )
                        if _fatura_pdf_debug():
                            print("[fatura PDF] QR drawImage (PNG buffer) tamam")
                    except Exception as ex2:
                        if _fatura_pdf_debug():
                            print(f"[fatura PDF] QR drawImage yedek hata: {ex2}")
            else:
                if _fatura_pdf_debug():
                    print("[fatura PDF] QR PIL yok — çizilmedi (konsola bakın)")
        finally:
            c.restoreState()

    _flush_gib_overlay()

    for s in satirlar:
        if y_line < 50 * mm:
            c.showPage()
            y_line = h_pt - 50 * mm
            c.setFont(font_name, 9)
        miktar = float(s.get("miktar") or 0)
        birim_fiyat = float(s.get("birim_fiyat") or 0)
        isk_oran = float(s.get("iskonto_orani") or 0)
        isk_tutar_giris = s.get("iskonto_tutar")
        kdv = float(s.get("kdv_orani") or 0)
        brut = miktar * birim_fiyat
        if isk_tutar_giris is not None and float(isk_tutar_giris) > 0:
            isk_tutar = min(float(isk_tutar_giris), brut)
        else:
            isk_tutar = brut * isk_oran / 100.0
        # Yüzde kolonu için gerçek oranı her zaman brüt ve iskonto tutarından hesapla (16,68 yerine 1668 hatasını engelle)
        isk_yuzde = (isk_tutar / brut * 100.0) if brut > 0 else isk_oran
        net = brut - isk_tutar
        kdv_tutar = net * kdv / 100.0
        mal_tutar = net

        mal_hizmet_text = (s.get("ad") or s.get("hizmet_urun_adi") or "")[:200]
        # "Mal Hizmet" sütunu: wrap + satır yüksekliği her satırda metne göre (üst/alt padding + satır aralığı)
        mal_col_idx = 1
        mal_col_w = widths[mal_col_idx]
        mal_max_w = max(10, mal_col_w - (pad * 2) - 6)  # clip + metin ölçümü için pay
        mal_lines = _wrap_cell_text(mal_hizmet_text, mal_max_w, font_name, 9)
        mal_lines = _force_lines_to_max_width(mal_lines, mal_max_w, font_name, 9)
        # 9pt font: baseline aralığı + descender; alt/üst çizgiye değmesin
        mal_line_leading = 12.5  # pt (satır başına toplam dikey pay)
        mal_pad_top = 10  # üst border + büyük harf ascent
        mal_pad_bottom = 9  # alt border ile son satır descender arası
        min_row_h_pt = row_h
        n_mal = max(1, len(mal_lines))
        current_row_h = max(
            min_row_h_pt,
            mal_pad_top + mal_pad_bottom + n_mal * mal_line_leading,
        )

        values = [
            str(sira),
            "",  # Mal Hizmet'i aşağıda çok satır çizeceğiz
            f"{miktar:.2f}",
            s.get("birim") or "Adet",
            f"{birim_fiyat:.2f}",
            f"{isk_yuzde:.2f}",
            f"{isk_tutar:.2f}",
            f"{kdv:.2f}",
            f"{kdv_tutar:.2f}",
            f"{mal_tutar:.2f}",
        ]
        x = margin
        row_top = y_line
        # Diğer sütunlar: satır içinde dikey ortala
        draw_y_center = row_top - (current_row_h / 2) - 3
        for i, val in enumerate(values):
            w_pt = widths[i]
            if i == mal_col_idx:
                # Çok satırlı "Mal Hizmet" — clip ile komşu sütuna taşmayı kes; yükseklik padding + leading ile hesaplı
                cell_bottom_y = row_top - current_row_h
                c.saveState()
                _clip = c.beginPath()
                _clip.rect(x, cell_bottom_y, w_pt, current_row_h)
                c.clipPath(_clip, stroke=0, fill=0)
                # İlk baseline: üst iç boşluktan sonra (~9pt font ascent payı)
                y_txt = row_top - mal_pad_top
                for ln in mal_lines:
                    c.drawString(x + pad, y_txt, (ln or "")[:200])
                    y_txt -= mal_line_leading
                c.restoreState()
            else:
                if i in (0, 2, 4, 5, 6, 8, 9):
                    c.drawRightString(x + w_pt - pad, draw_y_center, val)
                else:
                    c.drawString(x + pad, draw_y_center, val)
            x += w_pt
        c.line(margin, row_top - current_row_h, table_right, row_top - current_row_h)
        y_line = row_top - current_row_h
        sira += 1

    # Veri sonrası boş ızgara satırları (GİB tarzı dolu tablo görünümü)
    _n_sum_rows = 5
    _yalniz_h_pre = 14  # pt
    _box_h_sm = _n_sum_rows * summary_row_h + 8
    # Özet kutusunu sayfanın alt bandına sabitle: tek satır olsa bile tablo ızgarası aşağıya kadar dolsun.
    _summary_box_y_fixed = 8 * mm + _yalniz_h_pre + GAP_SUMMARY_TO_YALNIZ
    _summary_top_fixed = _summary_box_y_fixed + _box_h_sm
    _table_bottom_min = _summary_top_fixed + GAP_TABLE_TO_SUMMARY
    # Boş ızgara: tek satırda bile ortadaki alan dolu görünsün.
    # Hedef satır sayısına göre boş satır yüksekliğini adaptif belirle.
    TARGET_EMPTY_ROWS = 14 if preview else 24
    available_h = max(0.0, float(y_line - _table_bottom_min))
    if available_h > 0:
        empty_row_h = max(5.8, min(float(row_h), available_h / float(TARGET_EMPTY_ROWS)))
    else:
        empty_row_h = max(5.8, min(float(row_h), 8.0))
    c.saveState()
    c.setLineWidth(0.5)
    try:
        _extra_empty = 0
        # Önizlemede de ızgara sayfa sonuna kadar dolsun.
        _extra_empty_cap = 800
        while y_line - empty_row_h >= _table_bottom_min and y_line - empty_row_h >= 50 * mm:
            ny = y_line - empty_row_h
            c.line(margin, ny, table_right, ny)
            y_line = ny
            _extra_empty += 1
            if _extra_empty >= _extra_empty_cap:
                break
    finally:
        c.restoreState()

    # Tablo sonu: son yatay çizgi ile aynı y
    table_bottom = y_line

    genel_toplam = float(fatura.get("genel_toplam") or fatura.get("toplam") or 0)
    ara_toplam = float(fatura.get("ara_toplam") or 0)
    toplam_isk = float(fatura.get("toplam_iskonto") or 0)
    kdv_toplam = float(fatura.get("kdv_toplam") or 0)
    summary_box_w = table_width * 0.45
    summary_box_x = margin + table_width - summary_box_w
    label_w_pt = summary_box_w * 0.68
    val_w_pt = summary_box_w - label_w_pt

    def _fmt_tl(v):
        return f"{float(v):,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")

    labels_vals = [
        ("Mal Hizmet Toplam Tutarı", ara_toplam),
        ("Toplam İskonto", toplam_isk),
        ("Hesaplanan KDV", kdv_toplam),
        ("Vergiler Dahil Toplam Tutar", genel_toplam),
        ("Ödenecek Tutar", genel_toplam),
    ]
    n_summary_rows = len(labels_vals)
    row_heights = [(n_summary_rows * summary_row_h + 8) / float(n_summary_rows)] * n_summary_rows
    box_h = sum(row_heights)
    # Özet kutusu sabit alt konumda.
    box_y = _summary_box_y_fixed
    summary_top = box_y + box_h
    yalniz_box_h = 14  # pt
    yalniz_box_y = box_y - GAP_SUMMARY_TO_YALNIZ - yalniz_box_h

    # Dikey sütun çizgileri (başlıktan kalem tablosu sonuna)
    x_pos = margin
    c.line(x_pos, start_y, x_pos, table_bottom)
    for w in widths:
        x_pos += w
        c.line(x_pos, start_y, x_pos, table_bottom)

    # Alt toplam — ReportLab Table: tam dış kutu + ızgara (sol kenar dahil)
    summary_data = [[(lbl or "")[:55], _fmt_tl(val)] for lbl, val in labels_vals]
    sum_table = Table(
        summary_data,
        colWidths=[label_w_pt, val_w_pt],
        rowHeights=row_heights,
    )
    sum_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    sum_table.wrapOn(c, summary_box_w, box_h)
    sum_table.drawOn(c, summary_box_x, box_y)

    # GİB: Yazı ile tutar — özet kutusunun altına ~2,5 mm, tam genişlik kutu
    yalniz_text = tutar_yaziya_gib(genel_toplam)
    yalniz_font_size = 8.5
    c.setFont(font_name, yalniz_font_size)
    c.rect(margin, yalniz_box_y, table_width, yalniz_box_h)
    # Metni kutu içinde sola hizalı, dikey ortada (kenara yapışmasın)
    yalniz_pad_left = 2 * mm
    yalniz_baseline = yalniz_box_y + (yalniz_box_h / 2) - (yalniz_font_size * 0.35)
    c.drawString(margin + yalniz_pad_left, yalniz_baseline, (yalniz_text or "")[:120])

    c.save()
    return buf.getvalue()


def faturalar_gerekli(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated_function


def _opt_customer_id(v):
    """Form/JSON'dan gelen müşteri id: boş string veya geçersiz → None (PG integer hatasını önler)."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _row_serializable(row):
    """Dict satırındaki date/time/datetime değerlerini string yapar (tojson hatasını önler)."""
    if not row:
        return row
    d = dict(row)
    for k, v in list(d.items()):
        if v is None:
            continue
        if hasattr(v, "strftime"):
            d[k] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "hour") else v.strftime("%Y-%m-%d")
    return d


def _fatura_satirlar_hesapla(fatura):
    """Fatura kaydından satır kırılımı (PDF önizleme ile aynı mantık)."""
    toplam = float(fatura.get("toplam") or 0)
    satirlar = []
    try:
        raw_j = fatura.get("satirlar_json")
        if raw_j:
            raw = json.loads(raw_j) if isinstance(raw_j, str) else raw_j
            for s in (raw if isinstance(raw, list) else []):
                miktar = float(s.get("miktar") or 0)
                birim_fiyat = float(s.get("birim_fiyat") or 0)
                isk_oran = float(s.get("iskonto_orani") or 0)
                isk_tutar_giris = s.get("iskonto_tutar")
                kdv_oran = float(s.get("kdv_orani") or 0)
                brut = miktar * birim_fiyat
                if isk_tutar_giris is not None and float(isk_tutar_giris or 0) > 0:
                    isk_tutar = min(float(isk_tutar_giris), brut)
                else:
                    isk_tutar = brut * isk_oran / 100.0
                net = brut - isk_tutar
                kdv_tutar = net * kdv_oran / 100.0
                satirlar.append({
                    "ad": (s.get("ad") or s.get("mal_hizmet") or "Hizmet").strip() or "Hizmet",
                    "miktar": miktar,
                    "birim": (s.get("birim") or "Adet").strip() or "Adet",
                    "birim_fiyat": birim_fiyat,
                    "iskonto_orani": isk_oran,
                    "iskonto_tutar": isk_tutar,
                    "kdv_orani": kdv_oran,
                    "kdv_tutar": kdv_tutar,
                    "mal_tutar": net,
                    "satir_toplam": net + kdv_tutar,
                })
    except Exception:
        pass
    if not satirlar:
        tutar = float(fatura.get("tutar") or 0)
        kdv_tutar = float(fatura.get("kdv_tutar") or 0)
        satirlar = [{
            "ad": "Hizmet",
            "miktar": 1,
            "birim": "Ay",
            "birim_fiyat": tutar,
            "iskonto_orani": 0,
            "iskonto_tutar": 0,
            "kdv_orani": (kdv_tutar / tutar * 100) if tutar else 20,
            "kdv_tutar": kdv_tutar,
            "mal_tutar": tutar,
            "satir_toplam": toplam,
        }]
    return satirlar


def _fatura_rapor_aktif_musteri_sql_durum_kosulu():
    """customers.durum: pasif / kapalı kayıtları rapor kapsamı dışı bırak."""
    return """
        (
            c.durum IS NULL
            OR TRIM(COALESCE(c.durum, '')) = ''
            OR LOWER(TRIM(c.durum)) NOT IN (
                'pasif', 'terk', 'kapandi', 'kapandı', 'kapalı', 'kapali', 'kapanmış', 'kapanmis'
            )
        )
    """


def _fatura_rapor_query_truthy(val) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on", "evet")


def _fatura_rapor_duzenli_fatura_norm(raw) -> str:
    s = str(raw or "").strip().lower()
    if not s or s in ("tum", "tumu", "all", "hepsi"):
        return ""
    s = s.replace("-", "_")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s


# Tekil müşteri listesi: giriş tarihi (KYC söz. / rent_start / created_at) — SELECT ve ay filtresinde aynı ifade
_FIRMA_OZET_GIRIS_TARIHI_SQL = """
COALESCE(
    CASE
        WHEN mk.sozlesme_tarihi IS NULL THEN NULL
        WHEN BTRIM(mk.sozlesme_tarihi::text) = '' THEN NULL
        WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
            THEN (SUBSTRING(BTRIM(mk.sozlesme_tarihi::text) FROM 1 FOR 10))::date
        WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{1,2}\\.[0-9]{1,2}\\.[0-9]{4}'
            THEN TO_DATE(
                REGEXP_REPLACE(BTRIM(mk.sozlesme_tarihi::text), ' .*$', ''),
                'DD.MM.YYYY'
            )
        WHEN BTRIM(mk.sozlesme_tarihi::text) ~ '^[0-9]{1,2}-[0-9]{1,2}-[0-9]{4}'
            THEN TO_DATE(
                REGEXP_REPLACE(BTRIM(mk.sozlesme_tarihi::text), ' .*$', ''),
                'DD-MM-YYYY'
            )
        ELSE NULL
    END,
    c.rent_start_date::date,
    c.created_at::date
)
""".strip()


def _fatura_rapor_giris_aylari_parse(raw) -> list | None:
    """giri_aylar=1,3,12 → benzersiz 1..12. Boş / geçersiz → None (filtre yok)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    seen: set[int] = set()
    out: list[int] = []
    for part in s.replace(";", ",").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            m = int(p)
        except ValueError:
            continue
        if m < 1 or m > 12 or m in seen:
            continue
        seen.add(m)
        out.append(m)
    out.sort()
    return out or None


def _fatura_rapor_musteri_where_sql(pasifleri_dahil: bool, tum_musteriler: bool) -> str:
    """Müşteri kartı listesi için WHERE parçası (fatura yok satırları).

    - Varsayılan: yalnız aktif kart (is_active) ve customers.durum pasif benzeri değil.
    - Pasifler Dahil: pasif durum ve is_active=FALSE kayıtlar da listelenir (API pasifte ikisini de günceller).
    - Tüm Müşteriler: kapsam süzgeci yok (TRUE).
    """
    if tum_musteriler:
        return "TRUE"
    if pasifleri_dahil:
        # Önceki hata: yalnızca durum süzgecini kaldırıp is_active=TRUE bırakıyordu; pasif kartlar hiç görünmüyordu.
        return "TRUE"
    durum_sql = _fatura_rapor_aktif_musteri_sql_durum_kosulu().strip()
    base = "COALESCE(c.is_active, TRUE) = TRUE"
    return f"{base} AND {durum_sql}"


def _fatura_rapor_musteri_where_with_bizim(musteri_where: str, bizim_only: bool) -> str:
    """İşaretliyse yalnız customers.bizim_hesap = TRUE (Bizim Hesap uygulamasında takip)."""
    if not bizim_only:
        return musteri_where
    mw = (musteri_where or "").strip()
    bsql = "COALESCE(c.bizim_hesap, FALSE) = TRUE"
    if not mw or mw.upper() == "TRUE":
        return bsql
    return f"({mw}) AND {bsql}"


_AYLAR_TR_FATURA_RAPOR = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


def _fatura_rapor_firma_ozet_mi(val) -> bool:
    s = str(val or "").strip().lower()
    return s in ("firma", "firma_ozet", "ozet", "1", "true", "evet")


def _firma_ozet_durum_etiket(row: dict) -> str:
    """Kart durumu: Aktif / Pasif (is_active + customers.durum)."""
    md = (row.get("musteri_durum") or "").strip().lower()
    if md == "pasif":
        return "Pasif"
    try:
        ia = row.get("is_active_kart")
        if ia is False:
            return "Pasif"
        if ia is not None and str(ia).strip().lower() in ("0", "false", "f", "no", "off"):
            return "Pasif"
    except Exception:
        pass
    return "Aktif"


_FIRMA_OZET_DEDUPE_UNVAN_SUFFIX = re.compile(
    r"\s*[.,]?\s*("
    r"a\.?\s*ş\.?|aş\.?|"
    r"ltd\.?|limited|"
    r"şti\.?|"
    r"san\.?\s*ve\s*tic\.?|sanayi\s*ve\s*ticaret|"
    r"holding"
    r")\s*\.?\s*$",
    re.IGNORECASE,
)


def _firma_ozet_normalize_vergi_no(val) -> str | None:
    """10 / 11 haneli VKN veya T.C. — aynı numaralı kartları tek satırda birleştirmek için."""
    if val is None:
        return None
    s = re.sub(r"\D", "", str(val).strip())
    if len(s) in (10, 11) and s.isdigit():
        return s
    return None


def _firma_ozet_unvan_cekirdek_anahtar(firma_adi: str) -> str:
    """Ünvan sonundaki A.Ş. / ŞTİ vb. kırpılmış, boşluk birleştirilmiş TR küçük harf anahtar."""
    raw = (firma_adi or "").strip()
    if not raw or raw == "—":
        return ""
    t = turkish_lower(raw)
    t = re.sub(r"\s+", " ", t).strip()
    for _ in range(4):
        t2 = _FIRMA_OZET_DEDUPE_UNVAN_SUFFIX.sub("", t).strip(" .,-")
        if t2 == t:
            break
        t = t2
    return t


def _firma_ozet_dedupe_grup_anahtari(it: dict) -> str:
    """Vergi no varsa ad çekirdeği ile birlikte kullan; yoksa çekirdek ünvan; yoksa tekil müşteri id."""
    tax = _firma_ozet_normalize_vergi_no(it.get("_dedupe_vergi"))
    core = _firma_ozet_unvan_cekirdek_anahtar(it.get("firma_adi") or "")
    if tax:
        if core:
            return f"vn:{tax}|nm:{core}"
        return f"vn:{tax}"
    if core:
        return f"nm:{core}"
    try:
        return f"\x00id:{int(it.get('musteri_id') or 0)}"
    except (TypeError, ValueError):
        return "\x00id:0"


def _firma_ozet_liste_ara_blob_from_row(row: dict, firma_goster: str) -> str:
    """Tekil liste arama kutusu: kart + son KYC metinleri tek dizgede (istemci süzgeci)."""
    keys = (
        firma_goster,
        row.get("tax_number"),
        row.get("cust_name"),
        row.get("cust_phone"),
        row.get("cust_phone2"),
        row.get("cust_email"),
        row.get("cust_vergi_dairesi"),
        row.get("cust_address"),
        row.get("cust_ev_adres"),
        row.get("cust_yetkili_kisi"),
        row.get("cust_yetkili_tcno"),
        row.get("kyc_musteri_adi"),
        row.get("kyc_vergi_dairesi"),
        row.get("kyc_yeni_adres"),
        row.get("kyc_yetkili_ikametgah"),
        row.get("kyc_yetkili_adsoyad"),
        row.get("kyc_yetkili_tcno"),
        row.get("kyc_yetkili_tel"),
        row.get("kyc_yetkili_tel2"),
        row.get("kyc_yetkili_email"),
        row.get("kyc_mk_email"),
        row.get("kyc_sirket_unvani"),
        row.get("kyc_unvan"),
        row.get("kyc_vergi_no"),
    )
    return " ".join(str(x).strip() for x in keys if x is not None and str(x).strip())


def _firma_ozet_dedupe_satirlar(satirlar: list) -> list:
    """Aynı vergi no veya aynı çekirdek ünvana sahip satırlardan birini bırak: önce Aktif, sonra en küçük musteri_id."""
    if len(satirlar) < 2:
        return satirlar
    buckets: dict[str, list] = {}
    order: list[str] = []
    for it in satirlar:
        k = _firma_ozet_dedupe_grup_anahtari(it)
        if k not in buckets:
            order.append(k)
            buckets[k] = []
        buckets[k].append(it)
    out: list = []
    for k in order:
        group = buckets[k]
        if len(group) == 1:
            out.append(group[0])
            continue

        def _aktif_mi(x: dict) -> bool:
            return (x.get("durum_etiket") or "").strip().lower() != "pasif"

        aktifler = [x for x in group if _aktif_mi(x)]
        pool = aktifler if aktifler else group

        def _mid(x: dict) -> int:
            try:
                return int(x.get("musteri_id") or 0)
            except (TypeError, ValueError):
                return 0

        win = min(pool, key=_mid)
        out.append(win)
    out.sort(key=lambda x: turkish_lower((x.get("firma_adi") or "").strip()))
    return out


def _firma_ozet_grid_ozet_map_for_rows(rows: list, ref: date | None = None) -> dict[int, dict]:
    """Firma ozet raporunda borc/geciken ay hesabini aylik grid ozetiyle esler."""
    mids: list[int] = []
    seen = set()
    for row in rows or []:
        try:
            mid = int((row or {}).get("id") or (row or {}).get("musteri_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0 or mid in seen:
            continue
        seen.add(mid)
        mids.append(mid)
    if not mids:
        return {}
    try:
        from routes.giris_routes import musteri_firma_ozet_grid_ozet_batch
        return musteri_firma_ozet_grid_ozet_batch(mids, ref) or {}
    except Exception as e:
        try:
            current_app.logger.warning("firma_ozet grid ozet baglanti hatasi: %r", e)
        except Exception:
            pass
        return {}


def _firma_ozet_toplam_borc_sum_from_rows(
    rows: list,
    ref: date | None = None,
    cift_olanlar: bool = False,
    pasifleri_dahil: bool = False,
) -> float:
    """Toplam borcu current_balance yerine aylik grid kaynakli hesapla toplar."""
    if not rows:
        return 0.0
    grid_map = _firma_ozet_grid_ozet_map_for_rows(rows, ref)
    satirlar = [_firma_ozet_row_to_satir_item(r, pasifleri_dahil, grid_map) for r in (rows or [])]
    satirlar.sort(key=lambda x: turkish_lower((x.get("firma_adi") or "").strip()))
    if not cift_olanlar:
        satirlar = _firma_ozet_dedupe_satirlar(satirlar)
    toplam_borc = 0.0
    for it in satirlar:
        try:
            b = float(it.get("toplam_borc") or 0)
            if math.isfinite(b):
                toplam_borc += b
        except (TypeError, ValueError):
            pass
    return round(toplam_borc, 2)


def _firma_ozet_aylik_kdv_dahil(aylik_kira, kira_nakit, kdv_oran) -> float:
    """Giriş formu ile aynı: net aylık × (1+KDV) veya nakit ise tutar aynı (KDV dahil)."""
    try:
        a = float(aylik_kira or 0)
    except (TypeError, ValueError):
        a = 0.0
    if not math.isfinite(a) or a <= 0:
        return 0.0
    kn = bool(kira_nakit)
    try:
        kdv = float(kdv_oran) if kdv_oran is not None else 20.0
    except (TypeError, ValueError):
        kdv = 20.0
    if not math.isfinite(kdv):
        kdv = 20.0
    if kn:
        return round(a, 2)
    return round(a * (1.0 + kdv / 100.0), 2)


def _firma_ozet_sql_turkish_lower(col_sql: str) -> str:
    """PostgreSQL ifadesi: utils.turkish_lower ile aynı harita + lower (sıralama için)."""
    return f"""lower(
      replace(replace(replace(replace(replace(replace(replace(replace(replace(
        COALESCE(BTRIM(({col_sql})::text), ''),
        E'İ','i'),'I','i'),'ı','i'),
        'Ş','s'),'ş','s'),
        'Ğ','g'),'ğ','g'),
        'Ü','u'),'ü','u'),
        'Ö','o'),'ö','o'),
        'Ç','c'),'ç','c')
    )"""


# _firma_ozet_unvan_cekirdek_anahtar ile aynı sonek (4 geçiş); PG regexp 'gi'
_FIRMA_OZET_SQL_UNVAN_SUFFIX_RE = (
    r"\s*[.,]?\s*("
    r"a\.?\s*ş\.?|aş\.?|"
    r"ltd\.?|limited|"
    r"şti\.?|"
    r"san\.?\s+ve\s+tic\.?|sanayi\s+ve\s+ticaret|"
    r"holding"
    r")\s*\.?\s*$"
)


def _firma_ozet_sql_nm_core_sql(fa_col_sql: str) -> str:
    """Ünvan çekirdeği — Python _firma_ozet_unvan_cekirdek_anahtar ile uyumlu (4 regexp geçişi)."""
    tl = _firma_ozet_sql_turkish_lower(fa_col_sql)
    x = f"regexp_replace(({tl})::text, E'\\\\s+', ' ', 'g')"
    pat = _FIRMA_OZET_SQL_UNVAN_SUFFIX_RE.replace("'", "''")
    for _ in range(4):
        x = f"trim(both ' .,-' from regexp_replace({x}, '{pat}', '', 'gi'))"
    return x


def _firma_ozet_sql_sort_key(alias: str = "raw") -> str:
    return _firma_ozet_sql_turkish_lower(f"{alias}.firma_adi")


def _firma_ozet_sql_dedupe_key_sql(alias: str = "raw") -> str:
    vn = f"regexp_replace(COALESCE(BTRIM({alias}.tax_number::text), ''), '[^0-9]', '', 'g')"
    nm = _firma_ozet_sql_nm_core_sql(f"{alias}.firma_adi")
    return f"""(CASE
        WHEN length({vn}) BETWEEN 10 AND 11 AND ({vn}) ~ '^[0-9]+$' AND length(trim(COALESCE(({nm})::text, ''))) > 0
            THEN 'vn:' || ({vn}) || '|nm:' || trim(COALESCE(({nm})::text, ''))
        WHEN length({vn}) BETWEEN 10 AND 11 AND ({vn}) ~ '^[0-9]+$' THEN 'vn:' || ({vn})
        WHEN length(trim(COALESCE(({nm})::text, ''))) > 0 THEN 'nm:' || trim(COALESCE(({nm})::text, ''))
        ELSE 'id:' || {alias}.id::text
    END)"""


def _firma_ozet_expect_borc_env():
    """FIRMA_OZET_EXPECT_BORC: örn. 14590966.16 veya 14.590.966,16 (doğrulama logu için)."""
    raw = (os.environ.get("FIRMA_OZET_EXPECT_BORC") or "").strip()
    if not raw:
        return None
    s = raw.replace(" ", "").replace("\xa0", "").replace("₺", "")
    if re.search(r",\d{1,2}\s*$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _firma_ozet_sql_guncel_grid_kdv_dahil_expr(ay_y: int, ay_m: int) -> str:
    """Tekil rapor referans ayı: musteri_aylik_grid_cache.payload.aylar içindeki tutar_kdv_dahil (Aylık Tutarlar grid).

    Önbellek yok/eksikse mk.aylik_kira (KDV dahil) ile yedekler, en son
    customers.guncel_kira_bedeli'ne düşer. Böylece grid henüz hesaplanmamış müşterilerde
    de Güncel sütunu bilinen en iyi değeri gösterir (kullanıcı girişinden sonra güncellenir).
    """
    yy = int(ay_y)
    mm = int(ay_m)
    if mm < 1:
        mm = 1
    if mm > 12:
        mm = 12
    return f"""COALESCE(
    (
        SELECT (elem->>'tutar_kdv_dahil')::numeric
        FROM musteri_aylik_grid_cache _agc
        CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(
                CASE
                    WHEN jsonb_typeof(_agc.payload::jsonb) = 'array'
                        THEN _agc.payload::jsonb
                    WHEN jsonb_typeof((_agc.payload::jsonb)->'aylar') = 'array'
                        THEN (_agc.payload::jsonb)->'aylar'
                    ELSE '[]'::jsonb
                END,
                '[]'::jsonb
            )
        ) AS elem
        WHERE _agc.musteri_id = c.id
          AND COALESCE(NULLIF(elem->>'yil', '')::int, 0) = {yy}
          AND COALESCE(NULLIF(elem->>'ay', '')::int, 0) = {mm}
          AND (elem->>'tutar_kdv_dahil') IS NOT NULL
        ORDER BY (elem->>'tutar_kdv_dahil')::numeric DESC NULLS LAST
        LIMIT 1
    ),
    CASE
        WHEN mk.aylik_kira IS NOT NULL AND mk.aylik_kira > 0 THEN
            CASE
                WHEN COALESCE(mk.kira_nakit, FALSE) THEN round(mk.aylik_kira::numeric, 2)
                ELSE round(mk.aylik_kira::numeric * (1 + COALESCE(mk.kdv_oran::numeric, 20) / 100), 2)
            END
        ELSE NULL
    END,
    NULLIF(c.guncel_kira_bedeli, 0),
    NULLIF(c.ilk_kira_bedeli, 0)
)"""


def _firma_ozet_sql_paging_queries(rows_firma_sql_no_order: str, cift_olanlar: bool):
    """Aynı filtreli müşteri kümesi: (1) toplamlar, (2) LIMIT/OFFSET sayfa satırları."""
    dk = _firma_ozet_sql_dedupe_key_sql("raw")
    pasif_rank = """(CASE WHEN lower(trim(COALESCE(raw.musteri_durum,''))) = 'pasif'
        OR (COALESCE(raw.is_active_kart, TRUE) IS NOT TRUE)
        THEN 1 ELSE 0 END)"""
    tborc = "GREATEST(0, round(COALESCE(raw.current_balance, 0)::numeric, 2))"
    aylik = """(CASE
        WHEN COALESCE(raw.firma_grid_aylik_net, 0) <= 0 THEN 0::numeric
        WHEN COALESCE(raw.kira_nakit, FALSE) THEN round(COALESCE(raw.firma_grid_aylik_net, 0)::numeric, 2)
        ELSE round(
            COALESCE(raw.firma_grid_aylik_net, 0)::numeric
            * (1 + COALESCE(raw.kdv_oran::numeric, 20) / 100),
            2
        )
    END)"""
    sk = _firma_ozet_sql_sort_key("raw")
    raw_cte = f"raw AS (\n{rows_firma_sql_no_order}\n)"
    if cift_olanlar:
        keyed = f"""
        keyed AS (
            SELECT raw.*,
                   {tborc}::numeric AS tborc,
                   {aylik}::numeric AS aylik_dahil,
                   {sk}::text AS sk,
                   1 AS rn
            FROM raw
        )"""
    else:
        keyed = f"""
        keyed0 AS (
            SELECT raw.*,
                   {dk} AS dk,
                   {pasif_rank} AS pasif_rank,
                   {tborc}::numeric AS tborc,
                   {aylik}::numeric AS aylik_dahil,
                   {sk}::text AS sk
            FROM raw
        ),
        keyed AS (
            SELECT keyed0.*,
                   row_number() OVER (PARTITION BY dk ORDER BY pasif_rank ASC, id) AS rn
            FROM keyed0
        )"""
    winners = """
        winners AS (
            SELECT * FROM keyed WHERE rn = 1
        )"""
    with_common = f"WITH {raw_cte.strip()},\n{keyed.strip()},\n{winners.strip()}\n"
    totals_sql = (
        with_common
        + """
SELECT COUNT(*)::int AS cnt,
       COALESCE(SUM(tborc), 0)::double precision AS sum_borc,
       COALESCE(SUM(aylik_dahil), 0)::double precision AS sum_aylik
FROM winners
"""
    )
    page_sql = (
        with_common
        + """
SELECT * FROM winners ORDER BY sk, id LIMIT %s OFFSET %s
"""
    )
    return totals_sql, page_sql


def _firma_ozet_row_to_satir_item(row: dict, pasifleri_dahil: bool, grid_ozet_map: dict | None = None) -> dict:
    """Tek SQL satırından API firma_ozet öğesi (mevcut api_fatura_rapor döngüsü ile aynı mantık)."""
    gid = row.get("id")
    firma = (row.get("firma_adi") or "").strip() or "—"
    mdur = (row.get("musteri_durum") or "").strip() or None
    raw_soz_bas = row.get("kyc_soz_bas")
    raw_soz_bit = row.get("kyc_soz_bit")
    giris_sql = row.get("giris_raw")

    def _coerce_date_light(v):
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        s = str(v or "").strip()
        if len(s) >= 10:
            s = s[:10]
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except (TypeError, ValueError):
            return None

    bas_parsed = _coerce_date_light(raw_soz_bas)
    bit_parsed = _coerce_date_light(raw_soz_bit)
    soz_bas_eff = bas_parsed or giris_sql or raw_soz_bas
    soz_bit_eff = bit_parsed if bit_parsed is not None else raw_soz_bit
    if isinstance(soz_bas_eff, datetime):
        giris_iso = soz_bas_eff.date().isoformat()[:10]
    elif isinstance(soz_bas_eff, date):
        giris_iso = soz_bas_eff.isoformat()[:10]
    else:
        gr = giris_sql
        giris_iso = str(gr)[:10] if gr is not None and str(gr).strip() else ""
    sozlesme_gun = 0
    if giris_iso:
        try:
            _gds = str(giris_iso).strip()[:10]
            if len(_gds) == 10:
                sozlesme_gun = int(date.fromisoformat(_gds).day)
        except (ValueError, TypeError):
            sozlesme_gun = 0
    if not (1 <= sozlesme_gun <= 31):
        sozlesme_gun = 0
    sozlesme_ay = 0
    sozlesme_ay_adi = ""
    if giris_iso:
        try:
            _gds_ay = str(giris_iso).strip()[:10]
            if len(_gds_ay) == 10:
                _d_ay = date.fromisoformat(_gds_ay)
                _m_ay = int(_d_ay.month)
                if 1 <= _m_ay <= 12:
                    sozlesme_ay = _m_ay
                    sozlesme_ay_adi = _AYLAR_TR_FATURA_RAPOR[_m_ay - 1]
        except (ValueError, TypeError):
            sozlesme_ay = 0
            sozlesme_ay_adi = ""
    # Aylık tutar: SQL'deki birleşik net taban (KYC aylık_kira yoksa customers yedekleri).
    # Sadece mk.aylik_kira kullanılırsa özet toplam (firma_grid_aylik_net) ile satır değeri uyuşmaz.
    _net_taban = row.get("firma_grid_aylik_net")
    if _net_taban is None:
        _net_taban = row.get("aylik_kira")
    atut = _firma_ozet_aylik_kdv_dahil(_net_taban, row.get("kira_nakit"), row.get("kdv_oran"))
    def _firma_ozet_kira_json_val(v):
        """Müşteriler 2 ile aynı: JSON'da sayı veya null (0 → null). Metin/virgül toleranslı."""
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        try:
            if isinstance(v, (int, float)):
                x = float(v)
            elif hasattr(v, "__float__") and hasattr(v, "__trunc__"):
                x = float(v)
            else:
                s = str(v).strip().replace(" ", "").replace("\u00a0", "")
                if not s:
                    return None
                if "," in s and "." in s and s.rfind(",") > s.rfind("."):
                    s = s.replace(".", "").replace(",", ".")
                elif "," in s:
                    s = s.replace(",", ".")
                x = float(s)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(x):
            return None
        if abs(x) < 1e-9:
            return None
        return round(x, 2)

    # İlk kira: müşteri kartındaki ilk kutu (musteri_kyc.aylik_kira); KYC yoksa customers.ilk_kira_bedeli.
    ilk_kira_json = _firma_ozet_kira_json_val(row.get("ilk_kira_bedeli"))
    guncel_raw = row.get("guncel_ay_grid_kdv_dahil")
    guncel_json = None
    if guncel_raw is not None:
        try:
            gx = float(guncel_raw)
            if math.isfinite(gx) and abs(gx) > 1e-9:
                guncel_json = round(gx, 2)
        except (TypeError, ValueError):
            guncel_json = None
    grid_ozet = {}
    if isinstance(grid_ozet_map, dict):
        try:
            grid_ozet = dict(grid_ozet_map.get(int(gid or 0)) or {})
        except (TypeError, ValueError):
            grid_ozet = {}
    if grid_ozet:
        try:
            gcur = float(grid_ozet.get("borc_month") or 0)
            if math.isfinite(gcur) and abs(gcur) > 1e-9:
                guncel_json = round(gcur, 2)
        except (TypeError, ValueError):
            pass
        try:
            tborc = max(0.0, round(float(grid_ozet.get("toplam_borc") or 0), 2))
        except (TypeError, ValueError):
            tborc = 0.0
        try:
            geciken_ay = max(0, int(grid_ozet.get("geciken_ay") or 0))
        except (TypeError, ValueError):
            geciken_ay = 0
    else:
        try:
            tborc = max(0.0, round(float(row.get("current_balance") or 0), 2))
        except (TypeError, ValueError):
            tborc = 0.0
        geciken_ay = 0
    g2_raw = row.get("grup2_secimleri")
    g2_list = []
    if isinstance(g2_raw, list):
        g2_list = [str(x).strip() for x in g2_raw if str(x).strip()]
    elif g2_raw is not None:
        try:
            gs = str(g2_raw).strip()
            if gs.startswith("{") and gs.endswith("}"):
                gs = gs[1:-1]
            if gs:
                g2_list = [x.strip().strip('"') for x in gs.split(",") if x.strip().strip('"')]
        except Exception:
            g2_list = []
    g2_list = list(dict.fromkeys(g2_list))
    item = {
        "musteri_id": gid,
        "firma_adi": firma,
        "vergi_no": (str(row.get("rapor_vergi_no") or "").strip() or "—"),
        "kimlik_no": (str(row.get("rapor_kimlik_no") or "").strip() or "—"),
        "giris_tarihi": giris_iso,
        "aylik_tutar": atut,
        "ilk_kira_bedeli": ilk_kira_json,
        "guncel_kira_bedeli": guncel_json,
        "toplam_borc": tborc,
        "grup2": (row.get("grup2_etiketler") or ("Bizim Hesap" if bool(row.get("bizim_hesap")) else "")).strip(),
        "grup2_secimleri": g2_list,
        "sozlesme_gun": sozlesme_gun,
        "sozlesme_ay": sozlesme_ay,
        "sozlesme_ay_adi": sozlesme_ay_adi,
        "geciken_ay": geciken_ay,
        "hizmet_turu": ((row.get("rapor_hizmet_turu") or "").strip() or "—"),
        "durum_etiket": _firma_ozet_durum_etiket(row),
        "kapanis_tarihi": (_coerce_date_light(row.get("kapanis_tarihi")).isoformat() if _coerce_date_light(row.get("kapanis_tarihi")) else ""),
        "_dedupe_vergi": row.get("tax_number"),
        "liste_ara_blob": (
            " ".join(
                part
                for part in (
                    firma,
                    str(row.get("rapor_vergi_no") or row.get("tax_number") or "").strip(),
                    str(row.get("rapor_kimlik_no") or "").strip(),
                    str(row.get("rapor_hizmet_turu") or "").strip(),
                    str(row.get("grup2_etiketler") or "").strip(),
                    str(row.get("kyc_arama_blob_tum") or "").strip(),
                    str(row.get("liste_ara_blob_db") or "").strip(),
                )
                if part
            )
        ),
    }
    _ho = row.get("hazir_ofis_oda_no")
    if _ho is not None and str(_ho).strip() != "":
        try:
            item["hazir_ofis_oda_no"] = int(_ho)
        except (TypeError, ValueError):
            item["hazir_ofis_oda_no"] = _ho
    try:
        ko_raw = row.get("kdv_oran")
        if ko_raw is not None and str(ko_raw).strip() != "":
            item["kdv_oran"] = int(round(float(ko_raw)))
    except (TypeError, ValueError):
        pass
    if mdur and pasifleri_dahil:
        item["musteri_durum"] = mdur
    return item


UI_PREF_KEY_FIRMA_MUSTERI = "firma_musteri_liste"


@bp.route("/api/ui-tercih", methods=["GET"])
@faturalar_gerekli
def api_ui_tercih_get():
    """Kullanıcıya özel UI ayarı (ör. müşteri listesi sütunları)."""
    ensure_user_ui_preferences_table()
    key = (request.args.get("key") or UI_PREF_KEY_FIRMA_MUSTERI).strip() or UI_PREF_KEY_FIRMA_MUSTERI
    if len(key) > 120:
        return jsonify({"ok": False, "mesaj": "Geçersiz anahtar."}), 400
    row = fetch_one(
        "SELECT pref_json FROM user_ui_preferences WHERE user_id = %s AND pref_key = %s",
        (current_user.id, key),
    )
    if not row or row.get("pref_json") is None:
        return jsonify({"ok": True, "prefs": None})
    prefs = row.get("pref_json")
    if isinstance(prefs, str):
        try:
            prefs = json.loads(prefs)
        except Exception:
            prefs = {}
    return jsonify({"ok": True, "prefs": prefs})


@bp.route("/api/ui-tercih", methods=["POST"])
@faturalar_gerekli
def api_ui_tercih_post():
    ensure_user_ui_preferences_table()
    data = request.get_json(silent=True) or {}
    key = str(data.get("key") or UI_PREF_KEY_FIRMA_MUSTERI).strip() or UI_PREF_KEY_FIRMA_MUSTERI
    if len(key) > 120:
        return jsonify({"ok": False, "mesaj": "Geçersiz anahtar."}), 400
    prefs = data.get("prefs")
    if prefs is None or not isinstance(prefs, dict):
        return jsonify({"ok": False, "mesaj": "prefs (nesne) gerekli."}), 400
    raw = json.dumps(prefs, ensure_ascii=False)
    if len(raw) > 32000:
        return jsonify({"ok": False, "mesaj": "Tercih verisi çok büyük."}), 400
    execute(
        """
        INSERT INTO user_ui_preferences (user_id, pref_key, pref_json, updated_at)
        VALUES (%s, %s, %s::jsonb, NOW())
        ON CONFLICT (user_id, pref_key) DO UPDATE SET
            pref_json = EXCLUDED.pref_json,
            updated_at = NOW()
        """,
        (current_user.id, key, raw),
    )
    return jsonify({"ok": True})


@bp.route('/api/fatura-rapor')
@faturalar_gerekli
def api_fatura_rapor():
    """Tarih aralığında kesilen faturaların satır bazlı dökümü (müşteri, tutarlar)."""
    bugun = date.today()
    first_this = bugun.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    bas_default = last_prev.replace(day=1)
    bas_s = (request.args.get("baslangic") or "").strip()
    bit_s = (request.args.get("bitis") or "").strip()
    try:
        bas = datetime.strptime(bas_s[:10], "%Y-%m-%d").date() if len(bas_s) >= 10 else bas_default
    except Exception:
        bas = bas_default
    try:
        bit = datetime.strptime(bit_s[:10], "%Y-%m-%d").date() if len(bit_s) >= 10 else bugun
    except Exception:
        bit = bugun
    if bas > bit:
        bas, bit = bit, bas
    try:
        page_size = int(request.args.get("page_size", 0) or 0)
    except (TypeError, ValueError):
        page_size = 0
    try:
        page = int(request.args.get("page", 1) or 1)
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1
    if page_size < 0:
        page_size = 0
    # Tekil müşteri raporunda (firma_ozet) Grup2 gibi client-side süzgeçlerde
    # "tek seferde tümünü getir" senaryosu için üst sınırı daha geniş tut.
    if page_size > 5000:
        page_size = 5000
    ensure_customers_bizim_hesap()
    sadece_faturali = str(request.args.get("sadece_faturali") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
        "evet",
    )
    pasifleri_dahil = _fatura_rapor_query_truthy(request.args.get("pasifleri_dahil"))
    tum_musteriler = _fatura_rapor_query_truthy(request.args.get("tum_musteriler"))
    bizim_hesap = _fatura_rapor_query_truthy(request.args.get("bizim_hesap"))
    cift_olanlar = _fatura_rapor_query_truthy(request.args.get("cift_olanlar"))
    duzenli_fatura = _fatura_rapor_duzenli_fatura_norm(request.args.get("duzenli_fatura"))
    gorunum_firma = _fatura_rapor_firma_ozet_mi(request.args.get("gorunum"))
    if gorunum_firma:
        # Tekil müşteri listesi: faturalar tablosu gerekmez; şema kontrolleri ve sorgu hafifletildi.
        ensure_customers_is_active()
        ensure_customers_durum()
        ensure_customers_rent_columns()
        ensure_customers_hazir_ofis_oda()
        ensure_musteri_kyc_columns()
        ensure_musteri_kyc_hazir_ofis_oda_no()
        ensure_musteri_kyc_latest_lookup_index()
        ensure_customers_grup2_secimleri()
        ensure_grup2_etiketleri_table()
        ensure_customers_balance_trigger()
        ref = bugun
        ay_y, ay_m = ref.year, ref.month
        ref_first = date(ay_y, ay_m, 1)
        ay_etiket = f"{_AYLAR_TR_FATURA_RAPOR[ay_m - 1]} {ay_y}"
        try:
            from routes.giris_routes import _ensure_aylik_grid_cache_table

            _ensure_aylik_grid_cache_table()
        except Exception as _e_agc:
            current_app.logger.warning("firma_ozet aylik_grid_cache ensure: %r", _e_agc)
        _firma_guncel_grid_sql = _firma_ozet_sql_guncel_grid_kdv_dahil_expr(ay_y, ay_m)
        musteri_where = _fatura_rapor_musteri_where_with_bizim(
            _fatura_rapor_musteri_where_sql(pasifleri_dahil, tum_musteriler),
            bizim_hesap,
        )
        mk_df_sql = ""
        mk_df_params = []
        arama = (request.args.get("q") or "").strip()
        arama_sql = ""
        if arama:
            # Tekil müşteri raporunda arama kutusu: sayfadan bağımsız server-side süz.
            # Önce aramayla eşleşen müşteri id'lerini tek bir ayrı sorguda buluyoruz, sonra
            # ana rapor sorgusuna `c.id = ANY(%s)` olarak enjekte ediyoruz. Bu yaklaşım:
            #   • Çok sayıda placeholder yerine tek parametre kullandığı için parametre
            #     sıralama hatalarına neden olmaz.
            #   • Paging CTE'sine güvenle girer, SQL paging fallback olsa bile bozulmaz.
            #   • Büyük tabloda da ID+GIN/B-tree indeksleri üzerinden hızlıdır.
            try:
                _t_arama = time.perf_counter()
                arama_where_sql, arama_where_params = customers_arama_sql_params_giris_genis_tokens(
                    arama, "c"
                )
                arama_id_rows = fetch_all(
                    f"""
                    SELECT DISTINCT c.id
                    FROM customers c
                    WHERE {arama_where_sql}
                    """,
                    arama_where_params,
                ) or []
                arama_ids = [int(r["id"]) for r in arama_id_rows if r and r.get("id") is not None]
                current_app.logger.info(
                    "firma_ozet_server_arama q=%r matched=%d ms=%.2f",
                    arama,
                    len(arama_ids),
                    (time.perf_counter() - _t_arama) * 1000.0,
                )
            except Exception as e_ar:
                current_app.logger.warning("firma_ozet_server_arama err=%r", e_ar)
                arama_ids = []
            # Eşleşme yoksa bile boş liste döndürecek biçimde filtreyi mutlak yap (sayfada
            # eski 50 satır görünmesin).
            arama_sql = " AND c.id = ANY(%s::bigint[])"
            mk_df_params.append(arama_ids if arama_ids else [0])
        if duzenli_fatura:
            mk_df_sql = (
                "AND COALESCE(NULLIF(LOWER(TRIM(mk.duzenli_fatura)), ''), 'duzenle') = %s"
            )
            mk_df_params.append(duzenli_fatura)
        giris_aylar_filtre = _fatura_rapor_giris_aylari_parse(request.args.get("giri_aylar"))
        giri_ay_sql = ""
        if giris_aylar_filtre:
            # IN (%s,…) — psycopg2’de ANY(%s::int[]) bazı sürümlerde/uzak DB’de güvenilir bağlanmıyor.
            _gph = ", ".join(["%s"] * len(giris_aylar_filtre))
            giri_ay_sql = (
                f" AND (EXTRACT(MONTH FROM ({_FIRMA_OZET_GIRIS_TARIHI_SQL})::date))::int IN ({_gph})"
            )
            mk_df_params.extend(giris_aylar_filtre)
        hazir_oda_raw = (request.args.get("hazir_ofis_oda") or request.args.get("hazir_oda") or "").strip()
        hazir_oda_filtre = None
        if hazir_oda_raw:
            try:
                _hz = int(hazir_oda_raw)
                if 200 <= _hz <= 230:
                    hazir_oda_filtre = _hz
            except (TypeError, ValueError):
                pass
        ho_sql = ""
        if hazir_oda_filtre is not None:
            ho_sql = " AND COALESCE(mk.hazir_ofis_oda_no, c.hazir_ofis_oda_no) = %s"
            mk_df_params.append(hazir_oda_filtre)
        rows_firma_sql = f"""
            SELECT c.id,
                   c.tax_number,
                   COALESCE(c.bizim_hesap, FALSE) AS bizim_hesap,
                   COALESCE(NULLIF(TRIM(c.musteri_adi), ''), NULLIF(TRIM(c.name), ''), '—') AS firma_adi,
                   COALESCE(NULLIF(TRIM(c.tax_number::text), ''), NULLIF(TRIM(mk.vergi_no::text), ''), '') AS rapor_vergi_no,
                   COALESCE(NULLIF(TRIM(mk.yetkili_tcno::text), ''), NULLIF(TRIM(c.yetkili_tcno::text), ''), '') AS rapor_kimlik_no,
                   c.kapanis_tarihi,
                   TRIM(COALESCE(c.durum, '')) AS musteri_durum,
                   COALESCE(c.is_active, TRUE) AS is_active_kart,
                   COALESCE(
                       NULLIF(TRIM(mk.hizmet_turu), ''),
                       NULLIF(TRIM(c.hizmet_turu), ''),
                       ''
                   ) AS rapor_hizmet_turu,
                   COALESCE(mk.hazir_ofis_oda_no, c.hazir_ofis_oda_no) AS hazir_ofis_oda_no,
                   ({_FIRMA_OZET_GIRIS_TARIHI_SQL}) AS giris_raw,
                   mk.sozlesme_tarihi AS kyc_soz_bas,
                   mk.sozlesme_bitis AS kyc_soz_bit,
                   mk.kira_artis_tarihi AS kyc_kira_artis,
                   mk.kira_suresi_ay AS kyc_kira_suresi_ay,
                   mk.aylik_kira, mk.kira_nakit, mk.kdv_oran,
                   CASE
                       WHEN mk.aylik_kira IS NOT NULL AND mk.aylik_kira > 0 THEN mk.aylik_kira
                       ELSE COALESCE(c.guncel_kira_bedeli, c.ilk_kira_bedeli, mk.aylik_kira)
                   END AS firma_grid_aylik_net,
                   COALESCE(mk.aylik_kira, c.ilk_kira_bedeli) AS ilk_kira_bedeli,
                   {_firma_guncel_grid_sql} AS guncel_ay_grid_kdv_dahil,
                   COALESCE(c.current_balance, 0) AS current_balance,
                   (
                       COALESCE(c.grup2_secimleri, ARRAY[]::text[]) ||
                       CASE WHEN COALESCE(c.bizim_hesap, FALSE) THEN ARRAY['bizim_hesap']::text[] ELSE ARRAY[]::text[] END
                   ) AS grup2_secimleri,
                   COALESCE(
                       (
                           SELECT string_agg(
                                      COALESCE(g2.etiket, gs),
                                      ' | '
                                      ORDER BY
                                          CASE gs
                                              WHEN 'bizim_hesap' THEN 0
                                              WHEN 'vergi_dairesi' THEN 1
                                              WHEN 'vergi_dairesi_terk' THEN 2
                                              ELSE 3
                                          END,
                                          COALESCE(g2.etiket, gs)
                                  )
                           FROM (
                               SELECT DISTINCT gs
                               FROM unnest(
                                   COALESCE(c.grup2_secimleri, ARRAY[]::text[]) ||
                                   CASE WHEN COALESCE(c.bizim_hesap, FALSE) THEN ARRAY['bizim_hesap']::text[] ELSE ARRAY[]::text[] END
                               ) gs
                           ) gg
                           LEFT JOIN grup2_etiketleri g2 ON g2.slug = gg.gs
                       ),
                       ''
                   ) AS grup2_etiketler,
                   (
                       SELECT string_agg(
                                  CONCAT_WS(' ',
                                      mkx.sirket_unvani,
                                      mkx.unvan,
                                      mkx.musteri_adi,
                                      mkx.vergi_no::text,
                                      mkx.vergi_dairesi,
                                      mkx.faaliyet_konusu,
                                      mkx.hizmet_turu,
                                      mkx.yetkili_adsoyad,
                                      mkx.yetkili_tcno::text,
                                      mkx.yetkili_tel,
                                      mkx.yetkili_tel2,
                                      mkx.yetkili_email,
                                      mkx.email,
                                      mkx.yeni_adres,
                                      mkx.yetkili_ikametgah,
                                      mkx.notlar
                                  ),
                                  ' '
                              )
                       FROM musteri_kyc mkx
                       WHERE mkx.musteri_id = c.id
                   ) AS kyc_arama_blob_tum,
                   /* Geniş yerel arama için birleşik blob: liste tam yüklendikten sonra
                      arama kutusunun saniyesinden küçük sürede filtreleme yapmasını sağlar. */
                   CONCAT_WS(' ',
                       c.name, c.musteri_adi, c.yetkili_kisi, c.phone, c.phone2, c.email,
                       c.ilk_kira_bedeli::text, c.guncel_kira_bedeli::text, mk.aylik_kira::text,
                       c.tax_number::text, c.office_code::text,
                       mk.sirket_unvani, mk.unvan, mk.musteri_adi,
                       mk.vergi_no::text, mk.vergi_dairesi, mk.faaliyet_konusu, mk.hizmet_turu,
                       mk.yetkili_adsoyad, mk.yetkili_tcno::text,
                       mk.yetkili_tel, mk.yetkili_tel2, mk.yetkili_email, mk.email,
                       mk.yeni_adres, mk.yetkili_ikametgah
                   ) AS liste_ara_blob_db
            FROM customers c
            LEFT JOIN (
                SELECT DISTINCT ON (musteri_id)
                    musteri_id,
                    sozlesme_tarihi,
                    sozlesme_bitis,
                    kira_artis_tarihi,
                    kira_suresi_ay,
                    aylik_kira,
                    kira_nakit,
                    kdv_oran,
                    duzenli_fatura,
                    hizmet_turu,
                    hazir_ofis_oda_no,
                    musteri_adi,
                    faaliyet_konusu,
                    vergi_dairesi,
                    yeni_adres,
                    yetkili_ikametgah,
                    yetkili_adsoyad,
                    yetkili_tcno,
                    yetkili_tel,
                    yetkili_tel2,
                    yetkili_email,
                    email,
                    sirket_unvani,
                    unvan,
                    vergi_no
                FROM musteri_kyc
                ORDER BY musteri_id, id DESC
            ) mk ON mk.musteri_id = c.id
            WHERE {musteri_where}
              {mk_df_sql}{giri_ay_sql}{ho_sql}{arama_sql}
            ORDER BY 2
            """
        rows_firma_params = tuple(mk_df_params)
        rows_firma_sql_no_order = re.sub(r"\s+ORDER\s+BY\s+2\s*$", "", rows_firma_sql.rstrip(), flags=re.I)
        firma_hizli_degrade = False
        rows_firma = []
        satirlar_firma = []
        toplam_aylik = 0.0
        toplam_borc = 0.0
        total_count_firma = 0
        satirlar_resp = []
        has_more = False
        sql_paging_ok = False
        firma_sql_ms = None
        firma_sunucu_ms = None
        if page_size > 0:
            totals_sql, page_sql = _firma_ozet_sql_paging_queries(rows_firma_sql_no_order, cift_olanlar)
            _t_sql = time.perf_counter()
            try:
                tot_row = fetch_one(totals_sql, rows_firma_params)
                lim = page_size
                off = (page - 1) * page_size
                page_rows = fetch_all(page_sql, rows_firma_params + (lim, off)) or []
                firma_sql_ms = (time.perf_counter() - _t_sql) * 1000.0
                if not tot_row:
                    raise RuntimeError("firma_ozet totals row missing")
                total_count_firma = int(tot_row.get("cnt") or 0)
                toplam_borc = round(float(tot_row.get("sum_borc") or 0), 2)
                toplam_aylik = round(float(tot_row.get("sum_aylik") or 0), 2)
                page_grid_map = _firma_ozet_grid_ozet_map_for_rows(page_rows, ref_first)
                satirlar_resp = [_firma_ozet_row_to_satir_item(r, pasifleri_dahil, page_grid_map) for r in page_rows]
                for _it in satirlar_resp:
                    _it.pop("_dedupe_vergi", None)
                has_more = off + len(satirlar_resp) < total_count_firma
                try:
                    all_rows_for_borc = fetch_all(rows_firma_sql, rows_firma_params) or []
                    toplam_borc = _firma_ozet_toplam_borc_sum_from_rows(
                        all_rows_for_borc,
                        ref_first,
                        cift_olanlar=cift_olanlar,
                        pasifleri_dahil=pasifleri_dahil,
                    )
                except Exception as e_borc:
                    current_app.logger.warning("firma_ozet grid toplam_borc fallback err=%r", e_borc)
                sql_paging_ok = True
                firma_sunucu_ms = firma_sql_ms
                current_app.logger.info(
                    "firma_ozet_sql_paging page=%s page_size=%s page_rows=%s total=%s ms=%.2f",
                    page,
                    page_size,
                    len(satirlar_resp),
                    total_count_firma,
                    firma_sql_ms,
                )
                exp_b = _firma_ozet_expect_borc_env()
                if exp_b is not None:
                    if abs(toplam_borc - exp_b) > 0.005:
                        current_app.logger.warning(
                            "firma_ozet toplam_borc=%s FIRMA_OZET_EXPECT_BORC=%s fark=%.4f",
                            toplam_borc,
                            exp_b,
                            toplam_borc - exp_b,
                        )
                    else:
                        current_app.logger.info(
                            "firma_ozet toplam_borc teyit OK (FIRMA_OZET_EXPECT_BORC=%s)", exp_b
                        )
            except Exception as e_pg:
                firma_sql_ms = (time.perf_counter() - _t_sql) * 1000.0
                current_app.logger.warning(
                    "firma_ozet_sql_paging fallback err=%r ms=%.2f", e_pg, firma_sql_ms
                )
                sql_paging_ok = False
        _last_firma_err = None
        if not sql_paging_ok:
            _t_fb = time.perf_counter()
            for _att in range(0, 2):
                try:
                    rows_firma = fetch_all(rows_firma_sql, rows_firma_params) or []
                    _last_firma_err = None
                    break
                except Exception as e_rf:
                    _last_firma_err = e_rf
                    em = str(e_rf or "").lower()
                    # Supabase pooler bağlantı kesmesi (SSL closed) için bir kez hızlı tekrar dene.
                    if _att == 0 and ("ssl connection has been closed" in em or "connection to server" in em):
                        time.sleep(0.35)
                        continue
                    break
            if _last_firma_err is not None:
                # Ağ/DB dalgalanmasında raporu tamamen düşürme:
                # hafif (borç özeti olmayan) sorguya dönüp listeyi yine göster.
                firma_hizli_degrade = True
                rows_firma = fetch_all(
                    f"""
                SELECT c.id,
                       c.tax_number,
                       COALESCE(c.bizim_hesap, FALSE) AS bizim_hesap,
                       COALESCE(NULLIF(TRIM(c.musteri_adi), ''), NULLIF(TRIM(c.name), ''), '—') AS firma_adi,
                       COALESCE(NULLIF(TRIM(c.tax_number::text), ''), NULLIF(TRIM(mk.vergi_no::text), ''), '') AS rapor_vergi_no,
                       COALESCE(NULLIF(TRIM(mk.yetkili_tcno::text), ''), NULLIF(TRIM(c.yetkili_tcno::text), ''), '') AS rapor_kimlik_no,
                       c.kapanis_tarihi,
                       TRIM(COALESCE(c.durum, '')) AS musteri_durum,
                       COALESCE(c.is_active, TRUE) AS is_active_kart,
                       COALESCE(
                           NULLIF(TRIM(mk.hizmet_turu), ''),
                           NULLIF(TRIM(c.hizmet_turu), ''),
                           ''
                       ) AS rapor_hizmet_turu,
                       COALESCE(mk.hazir_ofis_oda_no, c.hazir_ofis_oda_no) AS hazir_ofis_oda_no,
                       ({_FIRMA_OZET_GIRIS_TARIHI_SQL}) AS giris_raw,
                       mk.sozlesme_tarihi AS kyc_soz_bas,
                       mk.sozlesme_bitis AS kyc_soz_bit,
                       mk.kira_artis_tarihi AS kyc_kira_artis,
                       mk.kira_suresi_ay AS kyc_kira_suresi_ay,
                       mk.aylik_kira, mk.kira_nakit, mk.kdv_oran,
                       CASE
                           WHEN mk.aylik_kira IS NOT NULL AND mk.aylik_kira > 0 THEN mk.aylik_kira
                           ELSE COALESCE(c.guncel_kira_bedeli, c.ilk_kira_bedeli, mk.aylik_kira)
                       END AS firma_grid_aylik_net,
                       COALESCE(mk.aylik_kira, c.ilk_kira_bedeli) AS ilk_kira_bedeli,
                       {_firma_guncel_grid_sql} AS guncel_ay_grid_kdv_dahil,
                       COALESCE(c.current_balance, 0) AS current_balance,
                       (
                           COALESCE(c.grup2_secimleri, ARRAY[]::text[]) ||
                           CASE WHEN COALESCE(c.bizim_hesap, FALSE) THEN ARRAY['bizim_hesap']::text[] ELSE ARRAY[]::text[] END
                       ) AS grup2_secimleri,
                       COALESCE(
                           (
                               SELECT string_agg(
                                          COALESCE(g2.etiket, gs),
                                          ' | '
                                          ORDER BY
                                              CASE gs
                                                  WHEN 'bizim_hesap' THEN 0
                                                  WHEN 'vergi_dairesi' THEN 1
                                                  WHEN 'vergi_dairesi_terk' THEN 2
                                                  ELSE 3
                                              END,
                                              COALESCE(g2.etiket, gs)
                                      )
                               FROM (
                                   SELECT DISTINCT gs
                                   FROM unnest(
                                       COALESCE(c.grup2_secimleri, ARRAY[]::text[]) ||
                                       CASE WHEN COALESCE(c.bizim_hesap, FALSE) THEN ARRAY['bizim_hesap']::text[] ELSE ARRAY[]::text[] END
                                   ) gs
                               ) gg
                               LEFT JOIN grup2_etiketleri g2 ON g2.slug = gg.gs
                           ),
                           ''
                       ) AS grup2_etiketler,
                       (
                           SELECT string_agg(
                                      CONCAT_WS(' ',
                                          mkx.sirket_unvani,
                                          mkx.unvan,
                                          mkx.musteri_adi,
                                          mkx.vergi_no::text,
                                          mkx.vergi_dairesi,
                                          mkx.faaliyet_konusu,
                                          mkx.hizmet_turu,
                                          mkx.yetkili_adsoyad,
                                          mkx.yetkili_tcno::text,
                                          mkx.yetkili_tel,
                                          mkx.yetkili_tel2,
                                          mkx.yetkili_email,
                                          mkx.email,
                                          mkx.yeni_adres,
                                          mkx.yetkili_ikametgah,
                                          mkx.notlar
                                      ),
                                      ' '
                                  )
                           FROM musteri_kyc mkx
                           WHERE mkx.musteri_id = c.id
                       ) AS kyc_arama_blob_tum,
                       CONCAT_WS(' ',
                           c.name, c.musteri_adi, c.yetkili_kisi, c.phone, c.phone2, c.email,
                           c.ilk_kira_bedeli::text, c.guncel_kira_bedeli::text, mk.aylik_kira::text,
                           c.tax_number::text, c.office_code::text,
                           mk.sirket_unvani, mk.unvan, mk.musteri_adi,
                           mk.vergi_no::text, mk.vergi_dairesi, mk.faaliyet_konusu, mk.hizmet_turu,
                           mk.yetkili_adsoyad, mk.yetkili_tcno::text,
                           mk.yetkili_tel, mk.yetkili_tel2, mk.yetkili_email, mk.email,
                           mk.yeni_adres, mk.yetkili_ikametgah
                       ) AS liste_ara_blob_db
                FROM customers c
                LEFT JOIN (
                    SELECT DISTINCT ON (musteri_id)
                        musteri_id,
                        sozlesme_tarihi,
                        sozlesme_bitis,
                        kira_artis_tarihi,
                        kira_suresi_ay,
                        aylik_kira,
                        kira_nakit,
                        kdv_oran,
                        duzenli_fatura,
                        hizmet_turu,
                        hazir_ofis_oda_no,
                        sirket_unvani,
                        unvan,
                        musteri_adi,
                        yetkili_tcno,
                        vergi_no,
                        vergi_dairesi,
                        faaliyet_konusu,
                        yetkili_adsoyad,
                        yetkili_tel,
                        yetkili_tel2,
                        yetkili_email,
                        email,
                        yeni_adres,
                        yetkili_ikametgah
                    FROM musteri_kyc
                    ORDER BY musteri_id, id DESC
                ) mk ON mk.musteri_id = c.id
                WHERE {musteri_where}
                  {mk_df_sql}{giri_ay_sql}{ho_sql}{arama_sql}
                ORDER BY 2
                    """,
                    rows_firma_params,
                ) or []
            grid_map_all = _firma_ozet_grid_ozet_map_for_rows(rows_firma, ref_first)
            satirlar_firma = []
            for row in rows_firma:
                satirlar_firma.append(_firma_ozet_row_to_satir_item(row, pasifleri_dahil, grid_map_all))
            satirlar_firma.sort(key=lambda x: turkish_lower((x.get("firma_adi") or "").strip()))
            if not cift_olanlar:
                satirlar_firma = _firma_ozet_dedupe_satirlar(satirlar_firma)
            for _it in satirlar_firma:
                _it.pop("_dedupe_vergi", None)
            toplam_aylik = 0.0
            toplam_borc = 0.0
            for it in satirlar_firma:
                try:
                    v = float(it.get("aylik_tutar") or 0)
                    if math.isfinite(v):
                        toplam_aylik += v
                except (TypeError, ValueError):
                    pass
                try:
                    b = float(it.get("toplam_borc") or 0)
                    if math.isfinite(b):
                        toplam_borc += b
                except (TypeError, ValueError):
                    pass
            toplam_aylik = round(toplam_aylik, 2)
            toplam_borc = round(toplam_borc, 2)
            total_count_firma = len(satirlar_firma)
            satirlar_resp = satirlar_firma
            has_more = False
            if page_size > 0:
                start_idx = (page - 1) * page_size
                end_idx = start_idx + page_size
                satirlar_resp = satirlar_firma[start_idx:end_idx]
                has_more = end_idx < total_count_firma
            if page_size > 0:
                current_app.logger.info(
                    "firma_ozet_python_liste_ms=%.2f (sql_paging devre dışı veya hata)",
                    (time.perf_counter() - _t_fb) * 1000.0,
                )
                exp_b = _firma_ozet_expect_borc_env()
                if exp_b is not None:
                    if abs(toplam_borc - exp_b) > 0.005:
                        current_app.logger.warning(
                            "firma_ozet toplam_borc=%s FIRMA_OZET_EXPECT_BORC=%s fark=%.4f (python yolu)",
                            toplam_borc,
                            exp_b,
                            toplam_borc - exp_b,
                        )
                    else:
                        current_app.logger.info(
                            "firma_ozet toplam_borc teyit OK (FIRMA_OZET_EXPECT_BORC=%s, python yolu)", exp_b
                        )
            firma_sunucu_ms = (time.perf_counter() - _t_fb) * 1000.0
        kapsam_etiket = "tum_kayitlar" if tum_musteriler else ("pasif_dahil" if pasifleri_dahil else "aktif")
        return jsonify({
            "ok": True,
            "gorunum": "firma_ozet",
            "sunucu_islem_ms": (round(float(firma_sunucu_ms), 2) if firma_sunucu_ms is not None else None),
            "baslangic": bas.isoformat(),
            "bitis": bit.isoformat(),
            "duzenli_fatura": duzenli_fatura or "",
            "musteri_kapsam": kapsam_etiket,
            "pasifleri_dahil": pasifleri_dahil,
            "tum_musteriler": tum_musteriler,
            "bizim_hesap": bizim_hesap,
            "cift_olanlar": cift_olanlar,
            "sadece_faturali": False,
            "giri_aylar_filtre": giris_aylar_filtre or [],
            "hazir_ofis_oda_filtre": hazir_oda_filtre,
            "ay_referans": {"y": ay_y, "m": ay_m, "etiket": ay_etiket},
            "satirlar": satirlar_resp,
            "total_count": total_count_firma,
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "ozet": {
                "fatura_adedi": 0,
                "satir_adedi": total_count_firma,
                "kesilen_fatura_satir_sayisi": 0,
                "musteri_kapsam_adedi": total_count_firma,
                "donemde_faturasiz_musteri": 0,
                "toplam_satir_kdv_dahil": toplam_aylik,
                "toplam_borc_kdv_dahil": toplam_borc,
            },
            "degrade_mode": firma_hizli_degrade,
            "mesaj": ("Bağlantı dalgalanması nedeniyle hızlı modda yüklendi (borç özetleri sadeleştirildi)." if firma_hizli_degrade else ""),
        })
    ensure_faturalar_amount_columns()
    ensure_customers_is_active()
    ensure_customers_durum()
    # Tarih filtresi: bazı kayıtlarda fatura_tarihi boş kalabiliyor (GİB sonrası vb.);
    # vade_tarihi veya oluşturulma tarihi ile yedeklenir; aksi halde raporda görünmezler.
    df_join = ""
    df_where = ""
    df_params: list = []
    if duzenli_fatura:
        # Satır başı correlated subquery yerine tek DISTINCT ON birleşimi (N× alt sorgu yok).
        df_join = """
        LEFT JOIN (
            SELECT DISTINCT ON (musteri_id)
                musteri_id,
                duzenli_fatura
            FROM musteri_kyc
            ORDER BY musteri_id, id DESC
        ) mk_df ON mk_df.musteri_id = CAST(f.musteri_id AS INTEGER)
        """
        df_where = """
        AND COALESCE(
            NULLIF(LOWER(TRIM(mk_df.duzenli_fatura)), ''),
            'duzenle'
        ) = %s
        """
        df_params.append(duzenli_fatura)
    rows_raw = fetch_all(
        f"""
        SELECT f.id, f.fatura_no, f.fatura_tarihi, f.vade_tarihi,
               f.musteri_id, f.musteri_adi,
               COALESCE(
                   NULLIF(TRIM(c.musteri_adi), ''),
                   NULLIF(TRIM(c.name), ''),
                   NULLIF(TRIM(f.musteri_adi), ''),
                   '—'
               ) AS musteri_adi_goster,
               f.tutar, f.kdv_tutar, f.toplam, f.satirlar_json, f.durum, f.ettn
        FROM faturalar f
        LEFT JOIN customers c ON CAST(f.musteri_id AS INTEGER) = c.id
        {df_join}
        WHERE (
            (
                COALESCE(
                        f.fatura_tarihi::date,
                        f.vade_tarihi::date
                      ) >= %s
                AND COALESCE(
                        f.fatura_tarihi::date,
                        f.vade_tarihi::date
                      ) <= %s
            )
            OR (
                COALESCE(
                        f.fatura_tarihi::date,
                        f.vade_tarihi::date
                      ) IS NULL
                AND NULLIF(TRIM(COALESCE(f.ettn::text, '')), '') IS NOT NULL
            )
        )
        AND {sql_expr_fatura_not_gib_taslak("f.notlar")}
        {df_where}
        ORDER BY COALESCE(
                f.fatura_tarihi::date,
                f.vade_tarihi::date
              ) ASC NULLS LAST, f.id ASC
        """,
        tuple([bas, bit] + df_params),
    ) or []
    satirlar_out = []
    for f in rows_raw:
        fdict = dict(f)
        line_items = _fatura_satirlar_hesapla(fdict)
        ft = fdict.get("fatura_tarihi")
        ftarih = ""
        if ft is not None and str(ft).strip():
            ftarih = str(ft)[:10]
        elif fdict.get("vade_tarihi") is not None and str(fdict.get("vade_tarihi") or "").strip():
            ftarih = str(fdict.get("vade_tarihi"))[:10]
        else:
            ftarih = ""
        m_ad = (fdict.get("musteri_adi_goster") or fdict.get("musteri_adi") or "").strip() or "—"
        mid = fdict.get("musteri_id")
        fn = (fdict.get("fatura_no") or "").strip()
        gid = fdict.get("id")
        genel = round(float(fdict.get("toplam") or 0), 2)
        ettn = (str(fdict.get("ettn") or "").strip() or None)
        for idx, ln in enumerate(line_items, start=1):
            st = float(ln.get("satir_toplam") or 0)
            satirlar_out.append({
                "fatura_id": gid,
                "fatura_no": fn,
                "fatura_tarihi": ftarih,
                "musteri_id": mid,
                "musteri_adi": m_ad,
                "satir_no": idx,
                "satir_aciklama": ln.get("ad") or "Hizmet",
                "miktar": round(float(ln.get("miktar") or 0), 4),
                "birim": (ln.get("birim") or "Adet").strip(),
                "birim_fiyat": round(float(ln.get("birim_fiyat") or 0), 2),
                "iskonto_tutar": round(float(ln.get("iskonto_tutar") or 0), 2),
                "kdv_orani": round(float(ln.get("kdv_orani") or 0), 2),
                "mal_tutar": round(float(ln.get("mal_tutar") or 0), 2),
                "kdv_tutar": round(float(ln.get("kdv_tutar") or 0), 2),
                "satir_toplam": round(st, 2),
                "fatura_genel_toplam": genel,
                "ettn": ettn,
            })
    kesilen_fatura_satir_sayisi = len(satirlar_out)
    musteri_kapsam_adedi = 0
    donemde_faturasiz_musteri = 0
    if not sadece_faturali:
        musteri_where = _fatura_rapor_musteri_where_with_bizim(
            _fatura_rapor_musteri_where_sql(pasifleri_dahil, tum_musteriler),
            bizim_hesap,
        )
        mk_df_sql = ""
        mk_df_params = []
        if duzenli_fatura:
            mk_df_sql = """
            AND COALESCE(
                NULLIF(LOWER(TRIM((
                    SELECT mk.duzenli_fatura
                    FROM musteri_kyc mk
                    WHERE mk.musteri_id = c.id
                    ORDER BY mk.id DESC
                    LIMIT 1
                ))), ''),
                'duzenle'
            ) = %s
            """
            mk_df_params.append(duzenli_fatura)
        aktif_musteriler = fetch_all(
            f"""
            SELECT c.id,
                   COALESCE(NULLIF(TRIM(c.musteri_adi), ''), NULLIF(TRIM(c.name), ''), '—') AS musteri_adi_goster,
                   TRIM(COALESCE(c.durum, '')) AS musteri_durum
            FROM customers c
            WHERE {musteri_where}
              {mk_df_sql}
            ORDER BY 2
            """,
            tuple(mk_df_params),
        ) or []
        musteri_kapsam_adedi = len(aktif_musteriler)
        musteri_faturali = set()
        for s in satirlar_out:
            mid = s.get("musteri_id")
            if mid is None:
                continue
            try:
                musteri_faturali.add(int(mid))
            except (TypeError, ValueError):
                continue
        for row in aktif_musteriler:
            try:
                cid = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            if cid in musteri_faturali:
                continue
            donemde_faturasiz_musteri += 1
            m_ad = (row.get("musteri_adi_goster") or "—").strip() or "—"
            mdur = (row.get("musteri_durum") or "").strip() or None
            ph = {
                "fatura_id": None,
                "fatura_no": "—",
                "fatura_tarihi": "",
                "musteri_id": cid,
                "musteri_adi": m_ad,
                "satir_no": None,
                "satir_aciklama": "Seçilen dönemde fatura kaydı yok",
                "miktar": 0.0,
                "birim": "—",
                "birim_fiyat": 0.0,
                "iskonto_tutar": 0.0,
                "kdv_orani": 0.0,
                "mal_tutar": 0.0,
                "kdv_tutar": 0.0,
                "satir_toplam": 0.0,
                "fatura_genel_toplam": 0.0,
                "ettn": None,
                "musteri_doldurma": True,
            }
            if mdur:
                ph["musteri_durum"] = mdur
            satirlar_out.append(ph)
        satirlar_out.sort(
            key=lambda s: (
                turkish_lower((s.get("musteri_adi") or "").strip()),
                str(s.get("fatura_tarihi") or "9999-12-31"),
                int(s.get("fatura_id") or 0),
                int(s.get("satir_no") or 0),
            )
        )
    toplam_satir = round(sum(s["satir_toplam"] for s in satirlar_out), 2)
    kapsam_etiket = "tum_kayitlar" if tum_musteriler else ("pasif_dahil" if pasifleri_dahil else "aktif")
    return jsonify({
        "ok": True,
        "baslangic": bas.isoformat(),
        "bitis": bit.isoformat(),
        "sadece_faturali": sadece_faturali,
        "duzenli_fatura": duzenli_fatura or "",
        "musteri_kapsam": kapsam_etiket,
        "pasifleri_dahil": pasifleri_dahil,
        "tum_musteriler": tum_musteriler,
        "bizim_hesap": bizim_hesap,
        "satirlar": satirlar_out,
        "ozet": {
            "fatura_adedi": len(rows_raw),
            "satir_adedi": len(satirlar_out),
            "kesilen_fatura_satir_sayisi": kesilen_fatura_satir_sayisi,
            "musteri_kapsam_adedi": musteri_kapsam_adedi,
            "donemde_faturasiz_musteri": donemde_faturasiz_musteri,
            "toplam_satir_kdv_dahil": toplam_satir,
        },
    })


@bp.route("/api/duzenli-fatura-secenekleri")
@faturalar_gerekli
def api_duzenli_fatura_secenekleri():
    ensure_duzenli_fatura_secenekleri_table()
    rows = fetch_all(
        "SELECT kod, etiket FROM duzenli_fatura_secenekleri ORDER BY sira NULLS LAST, etiket"
    ) or []
    secenekler = []
    for r in rows:
        kod = str(r.get("kod") or "").strip()
        etiket = str(r.get("etiket") or "").strip()
        if not kod:
            continue
        secenekler.append({"kod": kod, "etiket": etiket or kod})
    return jsonify({"ok": True, "secenekler": secenekler})


@bp.route("/api/fatura-sil-toplu", methods=["POST"])
@faturalar_gerekli
def api_fatura_sil_toplu():
    """Fatura raporundan seçilen faturaları siler; önce tahsilatlar, sonra fatura. ETTN’li (GİB kesilmiş) faturalar silinmez."""
    ensure_faturalar_amount_columns()
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    raw = data.get("fatura_ids")
    if not isinstance(raw, list):
        return jsonify({"ok": False, "mesaj": "fatura_ids listesi gerekli."}), 400
    ids = []
    seen = set()
    for x in raw:
        try:
            i = int(x)
            if i > 0 and i not in seen:
                seen.add(i)
                ids.append(i)
        except (TypeError, ValueError):
            continue
    if not ids:
        return jsonify({"ok": False, "mesaj": "Geçerli fatura id yok."}), 400
    if len(ids) > 500:
        return jsonify({"ok": False, "mesaj": "En fazla 500 fatura birden silinebilir."}), 400

    silinen = []
    atlanan = []
    for fid in ids:
        row = fetch_one(
            """SELECT id, fatura_no,
                      NULLIF(TRIM(COALESCE(ettn::text, '')), '') AS ettn_val
               FROM faturalar WHERE id = %s""",
            (fid,),
        )
        if not row:
            atlanan.append({"id": fid, "neden": "bulunamadi"})
            continue
        if row.get("ettn_val"):
            atlanan.append(
                {
                    "id": fid,
                    "fatura_no": row.get("fatura_no"),
                    "neden": "ettn_var",
                }
            )
            continue
        execute("DELETE FROM tahsilatlar WHERE fatura_id = %s", (fid,))
        rc = execute("DELETE FROM faturalar WHERE id = %s", (fid,))
        if rc:
            silinen.append({"id": fid, "fatura_no": row.get("fatura_no")})
        else:
            atlanan.append({"id": fid, "neden": "silinemedi"})
    msg = f"{len(silinen)} fatura silindi."
    if atlanan:
        msg += f" {len(atlanan)} kayıt atlandı (ETTN’li veya bulunamadı)."
    return jsonify({"ok": True, "silinen": silinen, "atlanan": atlanan, "mesaj": msg})


@bp.route("/api/vergi-daireleri", methods=["GET"])
@faturalar_gerekli
def api_faturalar_vergi_daireleri():
    """Finans sayfası /faturalar altında göreli `api/...` çağrıları /giris önekine düşmeden 404 olmasın."""
    from routes.giris_routes import api_vergi_daireleri

    return api_vergi_daireleri()


@bp.route("/api/grup2-etiketleri", methods=["GET", "POST", "PUT", "DELETE"])
@faturalar_gerekli
def api_faturalar_grup2_etiketleri():
    from routes.giris_routes import api_grup2_etiketleri

    return api_grup2_etiketleri()


@bp.route("/api/grup2-etiket-guncelle", methods=["GET", "POST"])
@faturalar_gerekli
def api_faturalar_grup2_etiket_guncelle():
    from routes.giris_routes import api_grup2_etiket_guncelle

    return api_grup2_etiket_guncelle()


@bp.route("/api/grup2-etiket-sil", methods=["GET", "POST"])
@faturalar_gerekli
def api_faturalar_grup2_etiket_sil():
    from routes.giris_routes import api_grup2_etiket_sil

    return api_grup2_etiket_sil()


@bp.route('/')
@bp.route('/finans')
@faturalar_gerekli
def index():
    """Finans ana sayfası - Faturalar ve Tahsilatlar sekmeleri (/faturalar/ ve /faturalar/finans)"""
    import time

    bugun = date.today()
    return render_template(
        "faturalar/finans.html",
        now_ts=int(time.time()),
        tahsilat_iframe_bas=bugun.replace(day=1).isoformat(),
        tahsilat_iframe_bit=bugun.isoformat(),
    )


@bp.route('/yeni')
@faturalar_gerekli
def yeni_fatura():
    """Yeni fatura oluşturma ekranı. ?musteri_id= ile gelirse seçili müşteri bilgileri forma doldurulur.
    Sözleşmeler gridinden: ?birim_fiyat=&kdv=&satir_aciklama= (net birim fiyat; KDV satıra uygulanır).
    Müşteri listesi (KDV dahil aylık): ?birim_fiyat_kdv_dahil=&kdv= → matrah otomatik hesaplanır."""
    now = datetime.now()
    secili_musteri = None
    default_hizmet_urun = ""
    default_birim_fiyat = 0
    default_kdv_orani = 20
    satir_aciklama_url = (request.args.get("satir_aciklama") or "").strip()
    edit_fatura = None
    duzenleme_modu = False

    def _musteri_bul_ada_gore(ad):
        ad = str(ad or "").strip()
        if not ad:
            return None
        row = fetch_one(
            """
            SELECT id, name, musteri_adi, address, tax_number
            FROM customers
            WHERE LOWER(TRIM(COALESCE(name, ''))) = LOWER(TRIM(%s))
               OR LOWER(TRIM(COALESCE(musteri_adi, ''))) = LOWER(TRIM(%s))
            ORDER BY id DESC
            LIMIT 1
            """,
            (ad, ad),
        )
        if row and row.get("id"):
            return row
        # Yumuşak eşleşme: normalize ada göre en yakın sonucu bul.
        ad_norm = re.sub(r"[^a-z0-9]+", "", turkish_lower(ad))
        if not ad_norm:
            return None
        stop_words = {
            "ve", "tic", "ticaret", "anonim", "sirketi", "şirketi", "ltd", "ltd.", "sti", "şti", "san", "sanayi",
            "hizmet", "hizmetleri", "organizasyon", "org", "otomotiv", "as", "aş", "a.ş", "a.ş.", "danismanlik",
        }
        words = [w for w in re.split(r"\s+", ad) if w]
        token_candidates = []
        for w in words:
            wn = turkish_lower(re.sub(r"[^a-z0-9]+", "", w))
            if len(wn) < 3 or wn in stop_words:
                continue
            token_candidates.append((len(wn), w))
        token_candidates.sort(reverse=True)
        tokens = [w for _, w in token_candidates[:3]]
        if not tokens:
            tokens = [words[0] if words else ad]
        like_clauses = []
        like_params = []
        for t in tokens:
            like_clauses.append("COALESCE(name, '') ILIKE %s")
            like_clauses.append("COALESCE(musteri_adi, '') ILIKE %s")
            like_params.extend([f"%{t}%", f"%{t}%"])
        where_like = " OR ".join(like_clauses) if like_clauses else "TRUE"
        adaylar = fetch_all(
            f"""
            SELECT id, name, musteri_adi, address, tax_number
            FROM customers
            WHERE {where_like}
            ORDER BY id DESC
            LIMIT 1200
            """,
            tuple(like_params),
        ) or []
        best = None
        best_score = -1.0
        for a in adaylar:
            nm = str(a.get("name") or "").strip()
            ma = str(a.get("musteri_adi") or "").strip()
            for cand in (nm, ma):
                c_norm = re.sub(r"[^a-z0-9]+", "", turkish_lower(cand))
                if not c_norm:
                    continue
                if c_norm == ad_norm:
                    return a
                if ad_norm in c_norm or c_norm in ad_norm:
                    kisa = min(len(c_norm), len(ad_norm))
                    uzun = max(len(c_norm), len(ad_norm))
                    score = (kisa / float(uzun or 1))
                    if score > best_score:
                        best_score = score
                        best = a
        return best if best_score >= 0.6 else None

    def _gibden_musteri_adi_bul(uuid_val, fatura_no_val="", tarih_iso_hint=""):
        uuid_val = str(uuid_val or "").strip()
        fatura_no_val = str(fatura_no_val or "").strip().upper()
        try:
            from gib_earsiv import BestOfficeGIBManager
            gib = BestOfficeGIBManager()
            if not gib.is_available():
                return ""
            if uuid_val:
                st = gib.fatura_durum_getir(uuid_val, days_back=370) or {}
                for k in ("musteri_adi", "aliciUnvanAdSoyad", "aliciUnvan", "unvan", "customer_name"):
                    v = str(st.get(k) or "").strip()
                    if v:
                        return v
                ad = str(st.get("aliciAdi") or "").strip()
                soy = str(st.get("aliciSoyadi") or "").strip()
                adsoy = f"{ad} {soy}".strip()
                if adsoy:
                    return adsoy
            if not fatura_no_val:
                return ""
            bas = date.today() - timedelta(days=370)
            bit = date.today()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", str(tarih_iso_hint or "")):
                try:
                    t = datetime.strptime(str(tarih_iso_hint), "%Y-%m-%d").date()
                    bas = t - timedelta(days=45)
                    bit = t + timedelta(days=45)
                except Exception:
                    pass
            items = gib.portal_kesilen_fatura_listesi_normalized(bas, bit) or []
            for it in items:
                if str(it.get("fatura_no") or "").strip().upper() == fatura_no_val:
                    v = str(it.get("musteri_adi") or "").strip()
                    if v:
                        return v
        except Exception:
            pass
        return ""

    def _digits(v):
        return "".join(ch for ch in str(v or "") if ch.isdigit())

    def _kimlikten_musteri_bul(kimlik_no):
        kimlik_no = _digits(kimlik_no)
        if len(kimlik_no) not in (10, 11):
            return None
        row = fetch_one(
            """
            SELECT id, name, musteri_adi, address, tax_number
            FROM customers
            WHERE regexp_replace(COALESCE(tax_number::text, ''), '[^0-9]', '', 'g') = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (kimlik_no,),
        )
        if row:
            return row
        row = fetch_one(
            """
            SELECT c.id, c.name, c.musteri_adi, c.address, c.tax_number
            FROM customers c
            JOIN LATERAL (
                SELECT k.vergi_no, k.yetkili_tcno
                FROM musteri_kyc k
                WHERE k.musteri_id = c.id
                ORDER BY k.id DESC
                LIMIT 1
            ) kx ON TRUE
            WHERE regexp_replace(COALESCE(kx.vergi_no::text, ''), '[^0-9]', '', 'g') = %s
               OR regexp_replace(COALESCE(kx.yetkili_tcno::text, ''), '[^0-9]', '', 'g') = %s
            ORDER BY c.id DESC
            LIMIT 1
            """,
            (kimlik_no, kimlik_no),
        )
        return row

    def _gibden_musteri_adi_ve_kimlik_bul(uuid_val, fatura_no_val="", tarih_iso_hint=""):
        uuid_val = str(uuid_val or "").strip()
        fatura_no_val = str(fatura_no_val or "").strip().upper()
        ad = ""
        kimlik = ""
        try:
            from gib_earsiv import BestOfficeGIBManager
            gib = BestOfficeGIBManager()
            if not gib.is_available():
                return "", ""
            st = {}
            if uuid_val:
                st = gib.fatura_durum_getir(uuid_val, days_back=370) or {}
            if isinstance(st, dict) and st:
                for k in ("musteri_adi", "aliciUnvanAdSoyad", "aliciUnvan", "unvan", "customer_name"):
                    v = str(st.get(k) or "").strip()
                    if v:
                        ad = v
                        break
                if not ad:
                    ad = f"{str(st.get('aliciAdi') or '').strip()} {str(st.get('aliciSoyadi') or '').strip()}".strip()
                for k in ("vknTckn", "vkn_veya_tckn", "vkn", "tckn", "aliciVknTckn", "aliciVkn", "aliciTckn", "vergiNo", "kimlikNo"):
                    dv = _digits(st.get(k))
                    if len(dv) in (10, 11):
                        kimlik = dv
                        break
                if not kimlik:
                    for kk, vv in st.items():
                        kn = str(kk or "").lower()
                        if any(t in kn for t in ("vkn", "tckn", "kimlik", "vergi")):
                            dv = _digits(vv)
                            if len(dv) in (10, 11):
                                kimlik = dv
                                break
            if (not ad) and fatura_no_val:
                bas = date.today() - timedelta(days=370)
                bit = date.today()
                if re.match(r"^\d{4}-\d{2}-\d{2}$", str(tarih_iso_hint or "")):
                    try:
                        t = datetime.strptime(str(tarih_iso_hint), "%Y-%m-%d").date()
                        bas = t - timedelta(days=45)
                        bit = t + timedelta(days=45)
                    except Exception:
                        pass
                items = gib.portal_kesilen_fatura_listesi_normalized(bas, bit) or []
                for it in items:
                    if str(it.get("fatura_no") or "").strip().upper() == fatura_no_val:
                        ad = str(it.get("musteri_adi") or "").strip()
                        break
            if (not kimlik) and uuid_val:
                try:
                    html = gib.fatura_html_getir(uuid_val, days_back=370) or ""
                    m = re.search(r"(?:VKN\s*/?\s*TCKN|VKN/TCKN|VKN|TCKN)\s*[:\-]?\s*([0-9]{10,11})", str(html), flags=re.IGNORECASE)
                    if m:
                        kimlik = _digits(m.group(1))
                except Exception:
                    pass
        except Exception:
            pass
        return ad, kimlik

    bf_q = request.args.get("birim_fiyat")
    url_birim_set = False
    if bf_q is not None and str(bf_q).strip() != "":
        try:
            default_birim_fiyat = float(str(bf_q).replace(",", "."))
            url_birim_set = True
        except (TypeError, ValueError):
            default_birim_fiyat = 0

    kdv_q = request.args.get("kdv")
    if kdv_q is not None and str(kdv_q).strip() != "":
        try:
            k = int(float(str(kdv_q).replace(",", ".")))
            if k in (0, 1, 10, 20):
                default_kdv_orani = k
        except (TypeError, ValueError):
            pass

    # Müşteri listesi (firma_ozet) aylık sütunu KDV dahil; form birim fiyatı KDV hariç matrah.
    bf_kdv_dahil_q = request.args.get("birim_fiyat_kdv_dahil")
    if (not url_birim_set) and bf_kdv_dahil_q is not None and str(bf_kdv_dahil_q).strip() != "":
        try:
            gross = float(str(bf_kdv_dahil_q).replace(",", "."))
            if gross > 0:
                kpct = float(default_kdv_orani)
                default_birim_fiyat = round(gross / (1.0 + kpct / 100.0), 4)
                url_birim_set = True
        except (TypeError, ValueError):
            pass

    musteri_id = request.args.get('musteri_id', type=int)
    yeni_gib_bos = str(request.args.get("yeni_gib_bos") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
        "evet",
    )
    edit_fatura_id = request.args.get('edit_fatura_id', type=int)
    portal_uuid = (request.args.get("portal_uuid") or "").strip()
    portal_fatura_no = (request.args.get("portal_fatura_no") or "").strip()
    portal_tarih = (request.args.get("portal_tarih") or "").strip()
    portal_musteri_adi = (request.args.get("portal_musteri_adi") or "").strip()
    portal_gib_durum = (request.args.get("portal_gib_durum") or "").strip().lower()
    portal_musteri_id = request.args.get("portal_musteri_id", type=int)
    try:
        portal_tutar = float(request.args.get("portal_tutar", 0) or 0)
    except (TypeError, ValueError):
        portal_tutar = 0.0
    if edit_fatura_id:
        row = fetch_one(
            """
            SELECT id, fatura_no, fatura_tarihi, musteri_id, musteri_adi, tutar, kdv_tutar, toplam,
                   notlar, satirlar_json, sevk_adresi, ettn
            FROM faturalar
            WHERE id = %s
            """,
            (edit_fatura_id,),
        )
        if row:
            duzenleme_modu = True
            musteri_id = row.get("musteri_id") or musteri_id
            aday_musteri_adi = str(row.get("musteri_adi") or "").strip() or portal_musteri_adi
            if not aday_musteri_adi:
                aday_musteri_adi = _gibden_musteri_adi_bul(
                    row.get("ettn"),
                    row.get("fatura_no"),
                    str(row.get("fatura_tarihi") or "")[:10],
                )
            if not musteri_id:
                gib_ad, gib_kimlik = _gibden_musteri_adi_ve_kimlik_bul(
                    row.get("ettn"),
                    row.get("fatura_no"),
                    str(row.get("fatura_tarihi") or "")[:10],
                )
                if gib_kimlik:
                    m_hit = _kimlikten_musteri_bul(gib_kimlik)
                    if m_hit and m_hit.get("id"):
                        musteri_id = int(m_hit.get("id"))
                if (not aday_musteri_adi) and gib_ad:
                    aday_musteri_adi = gib_ad
            if (not musteri_id) and aday_musteri_adi:
                m_hit = _musteri_bul_ada_gore(aday_musteri_adi)
                if m_hit and m_hit.get("id"):
                    musteri_id = int(m_hit.get("id"))
            ft = row.get("fatura_tarihi")
            ft_iso = ft.strftime("%Y-%m-%d") if hasattr(ft, "strftime") else str(ft or "")[:10]
            satirlar = []
            try:
                raw = json.loads(row.get("satirlar_json") or "[]")
                for s in (raw if isinstance(raw, list) else []):
                    satirlar.append({
                        "ad": (s.get("ad") or s.get("mal_hizmet") or "Hizmet").strip() or "Hizmet",
                        "miktar": float(s.get("miktar") or 0) or 1,
                        "birim": (s.get("birim") or "Ay").strip() or "Ay",
                        "birim_fiyat": float(s.get("birim_fiyat") or 0),
                        "iskonto_tipi": (s.get("iskonto_tipi") or "iskonto"),
                        "iskonto_orani": float(s.get("iskonto_orani") or 0),
                        "iskonto_tutar": s.get("iskonto_tutar"),
                        "kdv_orani": int(float(s.get("kdv_orani") or 20) or 20),
                    })
            except Exception:
                satirlar = []
            if not satirlar:
                tut = float(row.get("tutar") or 0)
                kdv_t = float(row.get("kdv_tutar") or 0)
                satirlar = [{
                    "ad": "Hizmet",
                    "miktar": 1,
                    "birim": "Ay",
                    "birim_fiyat": tut,
                    "iskonto_tipi": "iskonto",
                    "iskonto_orani": 0,
                    "iskonto_tutar": 0,
                    "kdv_orani": int(round((kdv_t / tut) * 100)) if tut else 20,
                }]
            edit_fatura = {
                "id": int(row.get("id")),
                "fatura_no": str(row.get("fatura_no") or "").strip(),
                "musteri_id": int(musteri_id) if musteri_id else None,
                "musteri_adi": aday_musteri_adi,
                "fatura_tarihi": ft_iso,
                "fatura_saati": now.strftime("%H:%M"),
                "fatura_tipi": "satis",
                "notlar": str(row.get("notlar") or "").strip(),
                "sevk_adresi": str(row.get("sevk_adresi") or "").strip(),
                "ettn": str(row.get("ettn") or "").strip(),
                "gib_durum": (
                    "imzali" if _fatura_gib_imzalanmis_sayilir(row)
                    else "taslak" if _fatura_gib_taslak_sayilir(row)
                    else ""
                ),
                "satirlar": satirlar,
            }
    elif portal_uuid:
        duzenleme_modu = True
        if portal_musteri_id and not musteri_id:
            musteri_id = portal_musteri_id
        if (not musteri_id) and portal_musteri_adi:
            try:
                mr = _musteri_bul_ada_gore(portal_musteri_adi)
                if mr and mr.get("id"):
                    musteri_id = int(mr.get("id"))
            except Exception:
                pass
        edit_fatura = {
            "id": None,
            "fatura_no": portal_fatura_no,
            "musteri_id": int(musteri_id) if musteri_id else None,
            "musteri_adi": portal_musteri_adi,
            "fatura_tarihi": portal_tarih or now.strftime("%Y-%m-%d"),
            "fatura_saati": now.strftime("%H:%M"),
            "fatura_tipi": "satis",
            "notlar": "",
            "sevk_adresi": "",
            "ettn": portal_uuid,
            "gib_durum": portal_gib_durum or "taslak",
            "satirlar": ([{
                "ad": "Hizmet",
                "miktar": 1,
                "birim": "Ay",
                "birim_fiyat": 0,
                "iskonto_tipi": "iskonto",
                "iskonto_orani": 0,
                "iskonto_tutar": None,
                "kdv_orani": 20,
            }] if portal_tutar <= 0 else [{
                "ad": "Hizmet",
                "miktar": 1,
                "birim": "Ay",
                "birim_fiyat": round(portal_tutar / 1.2, 2),
                "iskonto_tipi": "iskonto",
                "iskonto_orani": 0,
                "iskonto_tutar": None,
                "kdv_orani": 20,
            }]),
            "portal_only": True,
        }
    if musteri_id:
        cust = fetch_one(
            "SELECT id, name, musteri_adi, address, tax_number FROM customers WHERE id = %s",
            (musteri_id,),
        )
        if cust:
            kyc = fetch_one(
                "SELECT yeni_adres, vergi_dairesi, vergi_no, hizmet_turu, aylik_kira FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
                (musteri_id,),
            )
            address = (kyc and (kyc.get("yeni_adres") or kyc.get("address"))) or cust.get("address") or ""
            vergi_dairesi = (kyc and kyc.get("vergi_dairesi")) or ""
            if not vergi_dairesi:
                cust_vd = fetch_one("SELECT vergi_dairesi FROM customers WHERE id = %s", (musteri_id,))
                if cust_vd and cust_vd.get("vergi_dairesi"):
                    vergi_dairesi = (cust_vd.get("vergi_dairesi") or "").strip()
            vergi_no = (kyc and kyc.get("vergi_no")) or cust.get("tax_number") or ""
            if isinstance(vergi_no, float):
                vergi_no = str(int(vergi_no)) if vergi_no == int(vergi_no) else str(vergi_no)
            ma = (cust.get("musteri_adi") or "").strip()
            nm = (cust.get("name") or "").strip()
            secili_musteri = {
                "id": cust["id"],
                "name": cust.get("name") or "",
                "musteri_adi": ma or None,
                # Fatura PDF / müşteri satırı: yalnızca şirket ünvanı (customers.name); kişi adı ayrı alanda kalır.
                "display_label": nm or ma or "",
                "address": address,
                "vergi_dairesi": vergi_dairesi,
                "vergi_no": str(vergi_no) if vergi_no else "",
            }
            if kyc:
                default_hizmet_urun = (kyc.get("hizmet_turu") or "Sanal Ofis").strip() or "Sanal Ofis"
                if not url_birim_set:
                    try:
                        default_birim_fiyat = float(kyc.get("aylik_kira") or 0)
                    except (TypeError, ValueError):
                        default_birim_fiyat = 0
    _pk = (request.args.get("prefill_key") or "").strip()
    fatura_prefill_key = _pk if re.match(r"^[a-zA-Z0-9_-]{12,140}$", _pk) else ""
    default_gib_fatura_id = request.args.get("gib_fatura_id", type=int)
    if default_gib_fatura_id is not None and default_gib_fatura_id < 1:
        default_gib_fatura_id = None
    # GİB paneli: yalnızca bu müşterinin son faturası (global son ID başka müşteriye GİB gönderilmesine yol açıyordu).
    _son_gib = {}
    if musteri_id and not yeni_gib_bos:
        _son_gib = fetch_one(
            "SELECT id FROM faturalar WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
            (musteri_id,),
        ) or {}
    try:
        _sid = _son_gib.get("id")
        son_fatura_id_for_gib = int(_sid) if _sid is not None else None
    except (TypeError, ValueError):
        son_fatura_id_for_gib = None
    if son_fatura_id_for_gib is not None and son_fatura_id_for_gib < 1:
        son_fatura_id_for_gib = None
    if edit_fatura and edit_fatura.get("id"):
        default_gib_fatura_id = int(edit_fatura.get("id"))

    # Taslak listesinden "yeni fatura" açılışında GİB ID alanı boş kalsın (yanlışlıkla eski taslağa GİB gitmesin).
    if yeni_gib_bos and not (edit_fatura and edit_fatura.get("id")):
        default_gib_fatura_id = None
        son_fatura_id_for_gib = None

    return render_template('faturalar/yeni_fatura.html',
                           bugun=now.strftime("%Y-%m-%d"),
                           saat=now.strftime("%H:%M"),
                           secili_musteri=_row_serializable(secili_musteri) if secili_musteri else None,
                           default_hizmet_urun=default_hizmet_urun,
                           default_birim_fiyat=default_birim_fiyat,
                           default_kdv_orani=default_kdv_orani,
                           satir_aciklama_url=satir_aciklama_url,
                           fatura_prefill_key=fatura_prefill_key,
                           edit_fatura=_row_serializable(edit_fatura) if edit_fatura else None,
                           duzenleme_modu=duzenleme_modu,
                           default_gib_fatura_id=default_gib_fatura_id,
                           son_fatura_id_for_gib=son_fatura_id_for_gib,
                           yeni_gib_bos=yeni_gib_bos)


@bp.route('/api/fatura-musteri-bul')
@faturalar_gerekli
def api_fatura_musteri_bul():
    """Düzenleme ekranında müşteri boşsa fatura no/ETTN üzerinden müşteri kartını çözer."""
    fatura_id = request.args.get("fatura_id", type=int)
    fatura_no = str(request.args.get("fatura_no") or "").strip()
    ettn = str(request.args.get("ettn") or "").strip()

    def _digits(v):
        return "".join(ch for ch in str(v or "") if ch.isdigit())

    def _kimlikten_musteri_bul(kimlik_no):
        kimlik_no = _digits(kimlik_no)
        if len(kimlik_no) not in (10, 11):
            return None
        # 10 hane VKN, 11 hane TCKN: customers ve son KYC kaydında ara.
        row = fetch_one(
            """
            SELECT c.id, c.name, c.musteri_adi, c.address, c.tax_number, c.vergi_dairesi
            FROM customers c
            WHERE regexp_replace(COALESCE(c.tax_number::text, ''), '[^0-9]', '', 'g') = %s
            ORDER BY c.id DESC
            LIMIT 1
            """,
            (kimlik_no,),
        )
        if row:
            return row
        row = fetch_one(
            """
            SELECT c.id, c.name, c.musteri_adi, c.address, c.tax_number, c.vergi_dairesi
            FROM customers c
            JOIN LATERAL (
                SELECT k.vergi_no, k.yetkili_tcno
                FROM musteri_kyc k
                WHERE k.musteri_id = c.id
                ORDER BY k.id DESC
                LIMIT 1
            ) kx ON TRUE
            WHERE regexp_replace(COALESCE(kx.vergi_no::text, ''), '[^0-9]', '', 'g') = %s
               OR regexp_replace(COALESCE(kx.yetkili_tcno::text, ''), '[^0-9]', '', 'g') = %s
            ORDER BY c.id DESC
            LIMIT 1
            """,
            (kimlik_no, kimlik_no),
        )
        return row

    def _musteri_row_to_payload(cust):
        if not cust:
            return None
        musteri_id = cust.get("id")
        kyc = fetch_one(
            "SELECT yeni_adres, vergi_dairesi, vergi_no FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
            (musteri_id,),
        ) if musteri_id else None
        address = (kyc and kyc.get("yeni_adres")) or cust.get("address") or ""
        vergi_dairesi = (kyc and kyc.get("vergi_dairesi")) or cust.get("vergi_dairesi") or ""
        vergi_no = (kyc and kyc.get("vergi_no")) or cust.get("tax_number") or ""
        ma = str(cust.get("musteri_adi") or "").strip()
        nm = str(cust.get("name") or "").strip()
        return {
            "id": cust.get("id"),
            "name": cust.get("name") or "",
            "musteri_adi": ma or None,
            "display_label": nm or ma or "",
            "address": address,
            "vergi_dairesi": vergi_dairesi,
            "vergi_no": str(vergi_no) if vergi_no else "",
        }

    def _musteri_ara(ad):
        ad = str(ad or "").strip()
        if not ad:
            return None
        exact = fetch_one(
            """
            SELECT id, name, musteri_adi, address, tax_number, vergi_dairesi
            FROM customers
            WHERE LOWER(TRIM(COALESCE(name, ''))) = LOWER(TRIM(%s))
               OR LOWER(TRIM(COALESCE(musteri_adi, ''))) = LOWER(TRIM(%s))
            ORDER BY id DESC
            LIMIT 1
            """,
            (ad, ad),
        )
        if exact:
            return exact
        words = [w for w in re.split(r"\s+", ad) if len(w.strip()) >= 3]
        stop = {"ve", "ticaret", "anonim", "şirketi", "sirketi", "ltd", "şti", "sti", "sanayi", "hizmetleri"}
        toks = []
        for w in words:
            wn = turkish_lower(re.sub(r"[^a-z0-9]+", "", w))
            if wn and wn not in stop:
                toks.append((len(wn), w))
        toks.sort(reverse=True)
        toks = [w for _, w in toks[:4]] or ([words[0]] if words else [ad])
        clauses = []
        params = []
        for t in toks:
            clauses.extend(["COALESCE(name, '') ILIKE %s", "COALESCE(musteri_adi, '') ILIKE %s"])
            params.extend([f"%{t}%", f"%{t}%"])
        rows = fetch_all(
            f"""
            SELECT id, name, musteri_adi, address, tax_number, vergi_dairesi
            FROM customers
            WHERE {' OR '.join(clauses)}
            ORDER BY id DESC
            LIMIT 1200
            """,
            tuple(params),
        ) or []
        hedef = re.sub(r"[^a-z0-9]+", "", turkish_lower(ad))
        best = None
        best_score = -1.0
        for r in rows:
            for cand in (r.get("name"), r.get("musteri_adi")):
                cn = re.sub(r"[^a-z0-9]+", "", turkish_lower(cand or ""))
                if not cn:
                    continue
                if cn == hedef:
                    return r
                if hedef in cn or cn in hedef:
                    score = min(len(hedef), len(cn)) / float(max(len(hedef), len(cn)) or 1)
                    if score > best_score:
                        best_score = score
                        best = r
        return best if best_score >= 0.55 else None

    row = None
    if fatura_id:
        row = fetch_one(
            "SELECT id, fatura_no, fatura_tarihi, musteri_id, musteri_adi, ettn FROM faturalar WHERE id = %s",
            (fatura_id,),
        )
    if row is None and ettn:
        row = fetch_one(
            "SELECT id, fatura_no, fatura_tarihi, musteri_id, musteri_adi, ettn FROM faturalar WHERE BTRIM(COALESCE(ettn::text, '')) = BTRIM(%s) ORDER BY id DESC LIMIT 1",
            (ettn,),
        )
    if row is None and fatura_no:
        row = fetch_one(
            "SELECT id, fatura_no, fatura_tarihi, musteri_id, musteri_adi, ettn FROM faturalar WHERE fatura_no = %s ORDER BY id DESC LIMIT 1",
            (fatura_no,),
        )

    musteri_adi = ""
    kimlik_no = ""
    if row:
        if row.get("musteri_id"):
            cust = fetch_one(
                "SELECT id, name, musteri_adi, address, tax_number, vergi_dairesi FROM customers WHERE id = %s",
                (row.get("musteri_id"),),
            )
            payload = _musteri_row_to_payload(cust)
            if payload:
                return jsonify({"ok": True, "musteri": payload, "kaynak": "fatura_musteri_id"})
        musteri_adi = str(row.get("musteri_adi") or "").strip()
        ettn = ettn or str(row.get("ettn") or "").strip()
        fatura_no = fatura_no or str(row.get("fatura_no") or "").strip()

    if not musteri_adi:
        try:
            from gib_earsiv import BestOfficeGIBManager
            gib = BestOfficeGIBManager()
            if gib.is_available():
                if ettn:
                    st = gib.fatura_durum_getir(ettn, days_back=370) or {}
                    # Öncelik: VKN/TCKN varsa doğrudan kimlikten eşleştir.
                    aday_kimlikler = []
                    for k in (
                        "vknTckn", "vkn_veya_tckn", "vkn", "tckn",
                        "aliciVknTckn", "aliciVkn", "aliciTckn",
                        "vergiNo", "vergi_no", "kimlikNo", "kimlik_no",
                    ):
                        dv = _digits(st.get(k))
                        if dv:
                            aday_kimlikler.append(dv)
                    if not aday_kimlikler:
                        for kk, vv in (st.items() if isinstance(st, dict) else []):
                            kn = str(kk or "").lower()
                            if any(t in kn for t in ("vkn", "tckn", "kimlik", "vergi")):
                                dv = _digits(vv)
                                if dv:
                                    aday_kimlikler.append(dv)
                    for dv in aday_kimlikler:
                        if len(dv) in (10, 11):
                            kimlik_no = dv
                            cust_by_kimlik = _kimlikten_musteri_bul(kimlik_no)
                            payload = _musteri_row_to_payload(cust_by_kimlik)
                            if payload:
                                return jsonify({
                                    "ok": True,
                                    "musteri": payload,
                                    "musteri_adi": musteri_adi,
                                    "kimlik_no": kimlik_no,
                                    "kaynak": "gib_kimlik_eslesme",
                                })
                    for k in ("musteri_adi", "aliciUnvanAdSoyad", "aliciUnvan", "unvan", "customer_name"):
                        musteri_adi = str(st.get(k) or "").strip()
                        if musteri_adi:
                            break
                    if not musteri_adi:
                        musteri_adi = f"{str(st.get('aliciAdi') or '').strip()} {str(st.get('aliciSoyadi') or '').strip()}".strip()
                if not musteri_adi and fatura_no:
                    items = gib.portal_kesilen_fatura_listesi_normalized(date.today() - timedelta(days=370), date.today()) or []
                    fn = fatura_no.strip().upper()
                    for it in items:
                        if str(it.get("fatura_no") or "").strip().upper() == fn:
                            musteri_adi = str(it.get("musteri_adi") or "").strip()
                            if musteri_adi:
                                break
        except Exception as ex:
            logging.getLogger(__name__).warning("fatura müşteri çözümleme GİB fallback hata: %s", ex)

    cust = _musteri_ara(musteri_adi)
    if not cust and kimlik_no:
        cust = _kimlikten_musteri_bul(kimlik_no)
    payload = _musteri_row_to_payload(cust)
    return jsonify({
        "ok": bool(payload),
        "musteri": payload,
        "musteri_adi": musteri_adi,
        "kimlik_no": kimlik_no,
        "kaynak": "ad_eslesme" if payload else "bulunamadi",
    })


@bp.route('/irsaliye/yeni')
@faturalar_gerekli
def yeni_irsaliye():
    """Yeni irsaliye oluşturma ekranı. Fatura ekranını irsaliye modu ile kullanır."""
    now = datetime.now()
    musteri_id = request.args.get('musteri_id', type=int)
    secili_musteri = None
    default_hizmet_urun = ""
    default_birim_fiyat = 0
    if musteri_id:
        cust = fetch_one(
            "SELECT id, name, musteri_adi, address, tax_number FROM customers WHERE id = %s",
            (musteri_id,),
        )
        if cust:
            kyc = fetch_one(
                "SELECT yeni_adres, vergi_dairesi, vergi_no, hizmet_turu, aylik_kira FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
                (musteri_id,),
            )
            address = (kyc and (kyc.get("yeni_adres") or kyc.get("address"))) or cust.get("address") or ""
            vergi_dairesi = (kyc and kyc.get("vergi_dairesi")) or ""
            if not vergi_dairesi:
                cust_vd = fetch_one("SELECT vergi_dairesi FROM customers WHERE id = %s", (musteri_id,))
                if cust_vd and cust_vd.get("vergi_dairesi"):
                    vergi_dairesi = (cust_vd.get("vergi_dairesi") or "").strip()
            vergi_no = (kyc and kyc.get("vergi_no")) or cust.get("tax_number") or ""
            if isinstance(vergi_no, float):
                vergi_no = str(int(vergi_no)) if vergi_no == int(vergi_no) else str(vergi_no)
            ma = (cust.get("musteri_adi") or "").strip()
            nm = (cust.get("name") or "").strip()
            secili_musteri = {
                "id": cust["id"],
                "name": cust.get("name") or "",
                "musteri_adi": ma or None,
                "display_label": nm or ma or "",
                "address": address,
                "vergi_dairesi": vergi_dairesi,
                "vergi_no": str(vergi_no) if vergi_no else "",
            }
            if kyc:
                default_hizmet_urun = (kyc.get("hizmet_turu") or "Sanal Ofis").strip() or "Sanal Ofis"
                try:
                    default_birim_fiyat = float(kyc.get("aylik_kira") or 0)
                except (TypeError, ValueError):
                    default_birim_fiyat = 0
    # Aynı fatura ekranını, irsaliye modu bilgisiyle kullanıyoruz
    return render_template(
        'faturalar/yeni_fatura.html',
        bugun=now.strftime("%Y-%m-%d"),
        saat=now.strftime("%H:%M"),
        secili_musteri=_row_serializable(secili_musteri) if secili_musteri else None,
        default_hizmet_urun=default_hizmet_urun,
        default_birim_fiyat=default_birim_fiyat,
        default_kdv_orani=20,
        satir_aciklama_url="",
        fatura_prefill_key="",
        irsaliye_modu=True,
        default_gib_fatura_id=None,
        son_fatura_id_for_gib=None,
    )


@bp.route('/faturalar')
@faturalar_gerekli
def faturalar():
    """Faturalar sekmesi. Tarih aralığı varsayılan: bu ayın 1'i - bugün."""
    today = date.today()
    first_of_month = today.replace(day=1)
    baslangic_str = request.args.get('baslangic', first_of_month.isoformat())
    bitis_str = request.args.get('bitis', today.isoformat())
    try:
        baslangic = datetime.strptime(baslangic_str[:10], '%Y-%m-%d').date()
        bitis = datetime.strptime(bitis_str[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        baslangic, bitis = first_of_month, today
    if baslangic > bitis:
        baslangic, bitis = bitis, baslangic
    yil = request.args.get('yil', today.year, type=int)
    ay_str = request.args.get('ay', '')
    ofis_kodu = request.args.get('ofis', '')
    gib_durum = (request.args.get('gib_durum') or '').strip().lower()
    gib_canli_kontrol = (request.args.get('gib_canli') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    erp_taslak_modu = (request.args.get('erp_taslak') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    ay_no = None
    if ay_str:
        try:
            ay_no = AYLAR.index(ay_str) + 1
        except ValueError:
            ay_no = None
    if ay_no:
        # Ay filtresinde tarih aralığını seçilen yılın tamamına çekiyoruz;
        # aksi halde varsayılan "bu ay" aralığı Mart gibi geçmiş ayları gizliyor.
        baslangic = date(yil, 1, 1)
        bitis = date(yil, 12, 31)
    
    ofisler = fetch_all("SELECT DISTINCT code FROM offices WHERE COALESCE(is_active::int, 1) = 1 ORDER BY code")
    
    sql = f"""
        SELECT f.*,
               c.name AS customer_name,
               COALESCE(NULLIF(TRIM(c.name), ''), NULLIF(TRIM(f.musteri_adi), ''), '—') AS musteri_adi_goster,
               mk.odeme_duzeni AS odeme_duzeni,
               mk.odeme_duzeni_manuel AS odeme_duzeni_manuel
        FROM faturalar f
        LEFT JOIN customers c ON CAST(f.musteri_id AS INTEGER) = c.id
        LEFT JOIN LATERAL (
            SELECT k.odeme_duzeni, k.odeme_duzeni_manuel
            FROM musteri_kyc k
            WHERE k.musteri_id = CAST(f.musteri_id AS INTEGER)
            ORDER BY k.id DESC
            LIMIT 1
        ) mk ON TRUE
        WHERE (f.fatura_tarihi::date) >= %s AND (f.fatura_tarihi::date) <= %s
    """
    params = [baslangic, bitis]

    if erp_taslak_modu:
        sql += f" AND {sql_expr_fatura_erp_taslak('f.notlar')}"
    else:
        sql += (
            " AND ("
            + sql_expr_fatura_not_gib_taslak('f.notlar')
            + " OR UPPER(BTRIM(COALESCE(f.fatura_no::text, ''))) LIKE 'GIB%%'"
            + ")"
        )
    
    if ofis_kodu:
        sql += " AND f.ofis_kodu = %s"
        params.append(ofis_kodu)

    # Ay seçilmişse (örn. Mart), seçilen yıl + ay kayıtlarını getir.
    if ay_no:
        sql += " AND EXTRACT(YEAR FROM (f.fatura_tarihi::date)) = %s"
        sql += " AND EXTRACT(MONTH FROM (f.fatura_tarihi::date)) = %s"
        params.extend([yil, ay_no])

    # Ay filtresinde ödenenler (kira ödemesi gelenler) üstte görünsün.
    if ay_no:
        sql += " ORDER BY CASE WHEN COALESCE(f.durum, '') = 'odendi' THEN 0 ELSE 1 END, (f.fatura_tarihi::date) DESC"
    else:
        sql += " ORDER BY (f.fatura_tarihi::date) DESC"
    
    faturalar_raw = fetch_all(sql, tuple(params))
    if erp_taslak_modu:
        faturalar = []
        for f in (faturalar_raw or []):
            row = _row_serializable(f)
            row["kaynak"] = "erp_taslak"
            row["gib_durum_rapor"] = "Taslak"
            row["toplam"] = _fatura_satir_tutar(row)
            row["kdv_tutar"] = _fatura_kdv_liste_gosterim(row)
            row["duzenle_url"] = _fatura_editor_url(row)
            row["odeme_duzeni"] = _odeme_duzeni_norm(row.get("odeme_duzeni"))
            row["odeme_duzeni_manuel"] = (row.get("odeme_duzeni_manuel") or "").strip()
            row["odeme_duzeni_display"] = _odeme_duzeni_label(row.get("odeme_duzeni"), row.get("odeme_duzeni_manuel"))
            faturalar.append(row)
    else:
        # Varsayılan: yalnız ERP — durum _fatura_resmi_gib_durumu (GIB no + ETTN + notlar).
        # «GİB ile kontrol»: portal ile birleştir + aşağıda gib_portal satırlarını ERP'ye yaz.
        # Eksik GİB faturaları: «GİB'den çek + kaydet» ile tarih aralığında toplu ERP'ye alınır (API).
        if gib_canli_kontrol:
            try:
                from gib_earsiv import portal_kesilen_fatura_listesi_cache_clear

                portal_kesilen_fatura_listesi_cache_clear()
            except Exception:
                pass
            try:
                faturalar = _faturalar_ekran_erp_gib_birlestir(
                    [_row_serializable(f) for f in (faturalar_raw or [])],
                    baslangic,
                    bitis,
                    ofis_kodu=ofis_kodu,
                )
            except Exception as ex:
                logging.getLogger(__name__).warning("faturalar GİB birleştirme (ERP yedeği): %s", ex)
                faturalar = []
                for f in (faturalar_raw or []):
                    row = _row_serializable(f)
                    row["kaynak"] = "erp"
                    row["gib_durum_rapor"] = _fatura_resmi_gib_durumu(row)
                    row["toplam"] = _fatura_satir_tutar(row)
                    row["duzenle_url"] = _fatura_editor_url(row)
                    row["odeme_duzeni"] = _odeme_duzeni_norm(row.get("odeme_duzeni"))
                    row["odeme_duzeni_manuel"] = (row.get("odeme_duzeni_manuel") or "").strip()
                    row["odeme_duzeni_display"] = _odeme_duzeni_label(row.get("odeme_duzeni"), row.get("odeme_duzeni_manuel"))
                    faturalar.append(row)
        else:
            faturalar = []
            for f in (faturalar_raw or []):
                row = _row_serializable(f)
                row["kaynak"] = "erp"
                row["gib_durum_rapor"] = _fatura_resmi_gib_durumu(row)
                row["toplam"] = _fatura_satir_tutar(row)
                row["duzenle_url"] = _fatura_editor_url(row)
                row["odeme_duzeni"] = _odeme_duzeni_norm(row.get("odeme_duzeni"))
                row["odeme_duzeni_manuel"] = (row.get("odeme_duzeni_manuel") or "").strip()
                row["odeme_duzeni_display"] = _odeme_duzeni_label(row.get("odeme_duzeni"), row.get("odeme_duzeni_manuel"))
                faturalar.append(row)
    if (not erp_taslak_modu) and gib_durum == 'imzali':
        faturalar = [f for f in faturalar if (f.get("gib_durum_rapor") or "") == "İmzalı"]
    elif (not erp_taslak_modu) and gib_durum == 'taslak':
        faturalar = [
            f for f in faturalar
            if (f.get("gib_durum_rapor") or "") in ("Taslak", "İmzasız")
        ]
    elif (not erp_taslak_modu) and gib_durum == 'iptal':
        faturalar = [f for f in faturalar if (f.get("gib_durum_rapor") or "") == "İptal"]

    if ay_no:
        # Ay bazında "kesilecek" görünümünde:
        # - 0 TL satırları ele
        # - Aynı müşteriyi tek satıra indir (resmi GİB izi güçlü olanı tercih et)
        faturalar = sorted(
            faturalar,
            key=lambda x: (
                -_fatura_gib_resmi_iz_puani(x),
                -int(x.get('id') or 0),
            ),
        )
        tekil = []
        gorulen = set()
        for f in faturalar:
            try:
                if float(f.get('toplam') or 0) <= 0:
                    continue
            except Exception:
                continue
            key = f.get('musteri_id') or (f.get('musteri_adi_goster') or f.get('customer_name') or f.get('musteri_adi') or '').strip().lower()
            if not key or key in gorulen:
                continue
            gorulen.add(key)
            tekil.append(f)
        faturalar = sorted(
            tekil,
            key=lambda x: (
                (x.get('musteri_adi_goster') or x.get('customer_name') or x.get('musteri_adi') or '').strip().lower()
            )
        )

    for f in (faturalar or []):
        mid = _opt_customer_id(f.get("musteri_id"))
        if mid:
            f["odeme_duzeni"] = _odeme_duzeni_norm(f.get("odeme_duzeni"))
            f["odeme_duzeni_manuel"] = (f.get("odeme_duzeni_manuel") or "").strip()
            f["odeme_duzeni_display"] = _odeme_duzeni_label(f.get("odeme_duzeni"), f.get("odeme_duzeni_manuel"))
        else:
            f["odeme_duzeni"] = ""
            f["odeme_duzeni_manuel"] = ""
            f["odeme_duzeni_display"] = "—"

    if (not erp_taslak_modu) and gib_canli_kontrol:
        # Canlı GİB kontrolünde görülen portal kayıtlarını ERP'ye kalıcı yaz.
        # Böylece sonraki normal (hızlı) açılışta da kaybolmazlar.
        # Yalnızca GİB'de imzalı görünen satırlar yeni kayıt olarak alınır;
        # iptal/imzasız satırlar (mevcut değilse) ERP'ye yeni eklenmez.
        for f in (faturalar or []):
            if str(f.get("kaynak") or "") != "gib_portal":
                continue
            gd = str(f.get("gib_durum_rapor") or "").strip()
            if gd in ("İptal", "İmzasız", "Taslak"):
                continue
            try:
                _gibden_erp_upsert({
                    "fatura_no": f.get("fatura_no"),
                    "ettn": f.get("ettn"),
                    "musteri_adi": f.get("musteri_adi_goster") or f.get("musteri_adi"),
                    "fatura_tarihi": f.get("fatura_tarihi"),
                    "tutar": _fatura_satir_tutar(f),
                    "gib_durum": gd or "İmzalı",
                })
            except Exception as ex:
                logging.getLogger(__name__).warning("GİB kontrol kalıcı yazım hatası: %s", ex)
    
    toplam_tutar = sum(f.get('toplam') or 0 for f in faturalar)
    toplam_odenen = sum(f.get('toplam') or 0 for f in faturalar if f.get('durum') == 'odendi')
    toplam_kalan = toplam_tutar - toplam_odenen
    
    ofisler_list = list(ofisler or [])
    ofisler = [_row_serializable(o) for o in ofisler_list]
    yillar = list(range(today.year, today.year - 6, -1))
    musteriler = fetch_all("SELECT id, name, musteri_adi FROM customers ORDER BY name LIMIT 500")
    musteriler = [_row_serializable(m) for m in (musteriler or [])]
    
    rendered = render_template('faturalar/faturalar_tab.html',
                         yil=yil,
                         ay=ay_str,
                         baslangic=baslangic.isoformat(),
                         bitis=bitis.isoformat(),
                         ofis_kodu=ofis_kodu,
                         gib_durum=gib_durum,
                         gib_canli_kontrol=gib_canli_kontrol,
                         erp_taslak_modu=erp_taslak_modu,
                         aylar=AYLAR,
                         yillar=yillar,
                         ofisler=ofisler,
                         musteriler=musteriler,
                         faturalar=faturalar,
                         toplam_tutar=toplam_tutar,
                         toplam_odenen=toplam_odenen,
                         toplam_kalan=toplam_kalan)
    resp = current_app.make_response(rendered)
    # GİB portalından gelen canlı veri olduğu için tarayıcı cache'lemesin.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@bp.route('/api/erp-taslaklar')
def api_erp_taslaklar():
    """ERP taslak faturaları JSON — GİB çağrısı yok, anlık döner."""
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "mesaj": "Giriş gerekli"}), 401
    today = date.today()
    first_of_month = today.replace(day=1)
    baslangic_str = request.args.get('baslangic', first_of_month.isoformat())
    bitis_str = request.args.get('bitis', today.isoformat())
    try:
        baslangic = datetime.strptime(baslangic_str[:10], '%Y-%m-%d').date()
        bitis = datetime.strptime(bitis_str[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        baslangic, bitis = first_of_month, today
    if baslangic > bitis:
        baslangic, bitis = bitis, baslangic

    sql = f"""
        SELECT f.id, f.fatura_no, f.musteri_id, f.musteri_adi, f.fatura_tarihi,
               f.toplam, f.tutar, f.kdv_tutar, f.durum, f.notlar, f.ettn, f.satirlar_json,
               mk.odeme_duzeni AS odeme_duzeni, mk.odeme_duzeni_manuel AS odeme_duzeni_manuel,
               COALESCE(NULLIF(TRIM(c.name), ''), NULLIF(TRIM(f.musteri_adi), ''), '—') AS musteri_adi_goster
        FROM faturalar f
        LEFT JOIN customers c ON CAST(f.musteri_id AS INTEGER) = c.id
        LEFT JOIN LATERAL (
            SELECT k.odeme_duzeni, k.odeme_duzeni_manuel
            FROM musteri_kyc k
            WHERE k.musteri_id = CAST(f.musteri_id AS INTEGER)
            ORDER BY k.id DESC
            LIMIT 1
        ) mk ON TRUE
        WHERE (f.fatura_tarihi::date) >= %s AND (f.fatura_tarihi::date) <= %s
          AND {sql_expr_fatura_erp_taslak('f.notlar')}
        ORDER BY (f.fatura_tarihi::date) DESC
    """
    rows = fetch_all(sql, (baslangic, bitis)) or []
    items = []
    for r in rows:
        rd = dict(r)
        kdv_goster = _fatura_kdv_liste_gosterim(rd)
        items.append({
            "id": r.get("id"),
            "fatura_no": r.get("fatura_no") or "",
            "fatura_tarihi": str(r.get("fatura_tarihi") or "")[:10],
            "musteri_adi": r.get("musteri_adi_goster") or r.get("musteri_adi") or "—",
            "musteri_id": r.get("musteri_id"),
            "toplam": float(r.get("toplam") or 0),
            "tutar": float(r.get("tutar") or 0),
            "kdv_tutar": kdv_goster,
            "durum": r.get("durum") or "",
            "notlar": r.get("notlar") or "",
            "ettn": r.get("ettn") or "",
            "ofis_kodu": "",
            "odeme_duzeni": _odeme_duzeni_norm(r.get("odeme_duzeni")),
            "odeme_duzeni_manuel": (r.get("odeme_duzeni_manuel") or "").strip(),
            "odeme_duzeni_display": _odeme_duzeni_label(r.get("odeme_duzeni"), r.get("odeme_duzeni_manuel")),
            "duzenle_url": "/faturalar/yeni?edit_fatura_id=" + str(r.get("id")) if r.get("id") else "",
        })
    toplam = sum(x["toplam"] for x in items)
    kdv_toplam = round(sum(x["kdv_tutar"] for x in items), 2)
    return jsonify({
        "ok": True,
        "items": items,
        "toplam": round(toplam, 2),
        "kdv_toplam": kdv_toplam,
        "adet": len(items),
    })


@bp.route('/api/musteri-odeme-duzeni', methods=['POST'])
@faturalar_gerekli
def api_musteri_odeme_duzeni():
    """Faturalar raporundan müşteri ödeme düzenini güncelle (Cari Kart ile senkron)."""
    try:
        ensure_musteri_kyc_odeme_duzeni()
    except Exception:
        pass
    data = request.get_json(silent=True) or {}
    musteri_id = _opt_customer_id(data.get("musteri_id"))
    if not musteri_id:
        return jsonify({"ok": False, "mesaj": "Geçerli müşteri gerekli."}), 400
    odeme_duzeni = _odeme_duzeni_norm(data.get("odeme_duzeni"))
    odeme_duzeni_manuel = (data.get("odeme_duzeni_manuel") or "").strip()[:200] if odeme_duzeni == "manuel" else ""
    row = fetch_one("SELECT id FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1", (musteri_id,))
    if row and row.get("id"):
        execute(
            """
            UPDATE musteri_kyc
            SET odeme_duzeni = %s, odeme_duzeni_manuel = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (odeme_duzeni, odeme_duzeni_manuel or None, int(row["id"])),
        )
    else:
        execute(
            """
            INSERT INTO musteri_kyc (musteri_id, odeme_duzeni, odeme_duzeni_manuel)
            VALUES (%s, %s, %s)
            """,
            (musteri_id, odeme_duzeni, odeme_duzeni_manuel or None),
        )
    return jsonify({
        "ok": True,
        "musteri_id": musteri_id,
        "odeme_duzeni": odeme_duzeni,
        "odeme_duzeni_manuel": odeme_duzeni_manuel,
        "odeme_duzeni_display": _odeme_duzeni_label(odeme_duzeni, odeme_duzeni_manuel),
    })


@bp.route('/tahsilatlar')
@faturalar_gerekli
def tahsilatlar():
    """Tahsilatlar sekmesi — tarih aralığı veya (?yil=) tam yıl.

    Tarih filtresi tahsil edildiği güne göre değil, raporun «hangi aya» yazıldığına göre:
    - |AYLIK_TAH|YYYY-MM-DD| varsa en az bir işaretçi tarihi aralıkta olmalı;
    - yoksa COALESCE(fatura_tarihi, tahsilat_tarihi) aralıkta olmalı.
    """
    today = date.today()
    bas_s = (request.args.get("baslangic") or "").strip()
    bit_s = (request.args.get("bitis") or "").strip()
    d0 = d1 = None
    if bas_s and bit_s:
        try:
            d0 = date.fromisoformat(bas_s[:10])
            d1 = date.fromisoformat(bit_s[:10])
        except ValueError:
            d0 = d1 = None
    if d0 is not None and d1 is not None:
        if d0 > d1:
            d0, d1 = d1, d0
        yil = d0.year
    elif "yil" in request.args and not bas_s and not bit_s:
        yil = request.args.get("yil", type=int) or today.year
        d0 = date(int(yil), 1, 1)
        d1 = date(int(yil), 12, 31)
    else:
        d0 = today.replace(day=1)
        d1 = today
        yil = d0.year

    raw_hizmet = (request.args.get("hizmet_turleri") or "").strip()
    secili_hizmet_turleri = []
    if raw_hizmet:
        seen_ht = set()
        for part in raw_hizmet.split(","):
            v = part.strip().lower()
            if not v or v in seen_ht:
                continue
            seen_ht.add(v)
            secili_hizmet_turleri.append(v)

    sql = """
        SELECT t.*,
               c.name as musteri_adi,
               COALESCE(NULLIF(TRIM(mk.hizmet_turu), ''), NULLIF(TRIM(c.hizmet_turu), ''), '') AS rapor_hizmet_turu,
               f.fatura_no,
               f.fatura_tarihi AS fatura_tarihi
        FROM tahsilatlar t
        LEFT JOIN customers c ON COALESCE(t.customer_id, t.musteri_id) = c.id
        LEFT JOIN LATERAL (
            SELECT mkx.hizmet_turu
            FROM musteri_kyc mkx
            WHERE mkx.musteri_id = c.id
            ORDER BY mkx.id DESC
            LIMIT 1
        ) mk ON TRUE
        LEFT JOIN faturalar f ON t.fatura_id = f.id
        WHERE (
            (
                COALESCE(t.aciklama, '') ~ E'\\|AYLIK_TAH\\|[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}\\|'
                AND EXISTS (
                    SELECT 1
                    FROM generate_series(%s::date, %s::date, '1 day'::interval) AS gs(d)
                    WHERE strpos(
                        COALESCE(t.aciklama, ''),
                        concat('|AYLIK_TAH|', to_char((gs.d)::date, 'YYYY-MM-DD'), '|')
                    ) > 0
                )
            )
            OR (
                NOT (COALESCE(t.aciklama, '') ~ E'\\|AYLIK_TAH\\|[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}\\|')
                AND COALESCE(f.fatura_tarihi::date, t.tahsilat_tarihi::date) >= %s::date
                AND COALESCE(f.fatura_tarihi::date, t.tahsilat_tarihi::date) <= %s::date
            )
        )
    """
    params = [d0, d1, d0, d1]
    if secili_hizmet_turleri:
        placeholders = ", ".join(["%s"] * len(secili_hizmet_turleri))
        sql += f" AND LOWER(TRIM(COALESCE(NULLIF(TRIM(mk.hizmet_turu), ''), NULLIF(TRIM(c.hizmet_turu), ''), ''))) IN ({placeholders})"
        params.extend(secili_hizmet_turleri)
    sql += " ORDER BY t.tahsilat_tarihi DESC NULLS LAST, t.id DESC"
    tahsilatlar_raw = fetch_all(sql, tuple(params))
    tahsilatlar_list = [_row_serializable(t) for t in (tahsilatlar_raw or [])]
    # Ana Tahsilatlar raporu: seçili aralıkta gerçekten tahsil edilmiş/işaretlenmiş ayları göster.
    # - Marker'lı kayıtlar: marker tarihi aralıkta olmalı VE (cache varsa) aylık gridde görünür aylar içinde olmalı.
    # - Marker'sız kayıtlar: elle girilmiş kabul edilir, referans tarih (fatura/tahsilat) aralıkta olmalı.
    def _date_from_val(v):
        if v is None:
            return None
        if hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day"):
            try:
                return date(int(v.year), int(v.month), int(v.day))
            except Exception:
                return None
        s = str(v).strip()[:10]
        try:
            return date.fromisoformat(s)
        except Exception:
            return None

    mid_set = set()
    for _t in tahsilatlar_list:
        try:
            _mid = int(_t.get("customer_id") or _t.get("musteri_id") or 0)
        except (TypeError, ValueError):
            _mid = 0
        if _mid > 0:
            mid_set.add(_mid)
    visible_ym_by_mid = {}
    if mid_set:
        try:
            cache_rows = fetch_all(
                "SELECT musteri_id, payload FROM musteri_aylik_grid_cache WHERE musteri_id = ANY(%s::bigint[])",
                (list(mid_set),),
            ) or []
            for cr in cache_rows:
                try:
                    cmid = int((cr or {}).get("musteri_id") or 0)
                except (TypeError, ValueError):
                    continue
                payload_raw = (cr or {}).get("payload")
                if not payload_raw:
                    continue
                try:
                    pobj = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                except Exception:
                    continue
                aylar = pobj if isinstance(pobj, list) else ((pobj or {}).get("aylar") or [])
                if not isinstance(aylar, list):
                    continue
                s = set()
                for a in aylar:
                    if not isinstance(a, dict):
                        continue
                    try:
                        yy = int(a.get("yil"))
                        mm = int(a.get("ay"))
                    except (TypeError, ValueError):
                        continue
                    if 1 <= mm <= 12 and 1900 <= yy <= 2200:
                        s.add(f"{yy:04d}-{mm:02d}")
                if s:
                    visible_ym_by_mid[cmid] = s
        except Exception:
            visible_ym_by_mid = {}

    filtered = []
    for _t in tahsilatlar_list:
        ac = str(_t.get("aciklama") or "")
        marker_isos = re.findall(r"\|AYLIK_TAH\|([0-9]{4}-[0-9]{2}-[0-9]{2})\|", ac)
        try:
            _mid = int(_t.get("customer_id") or _t.get("musteri_id") or 0)
        except (TypeError, ValueError):
            _mid = 0
        if marker_isos:
            in_range = False
            for iso in marker_isos:
                try:
                    dd = date.fromisoformat(iso[:10])
                except Exception:
                    continue
                if d0 <= dd <= d1:
                    in_range = True
                    break
            if not in_range:
                continue
            vis = visible_ym_by_mid.get(_mid)
            if isinstance(vis, set) and vis:
                if not any(iso[:7] in vis for iso in marker_isos):
                    continue
        else:
            ref_d = _date_from_val(_t.get("fatura_tarihi")) or _date_from_val(_t.get("tahsilat_tarihi"))
            if not ref_d or not (d0 <= ref_d <= d1):
                continue
        filtered.append(_t)
    tahsilatlar_list = filtered

    for _t in tahsilatlar_list:
        _t["rapor_aciklama_ay"] = _tahsilat_rapor_aciklama_ay_metni(
            _t.get("aciklama"),
            fatura_tarihi=_t.get("fatura_tarihi"),
            tahsilat_tarihi=_t.get("tahsilat_tarihi"),
        )
    # Güvenli doldurma: bazı ortamlarda join/alias farklarından dolayı hizmet türü boş gelebiliyor.
    # Burada müşteri id -> hizmet türü haritasını ayrıca kurup eksik satırları tamamlıyoruz.
    musteri_idler = []
    seen_mid = set()
    for t in tahsilatlar_list:
        mid = t.get("customer_id") or t.get("musteri_id")
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            mid = None
        if not mid or mid in seen_mid:
            continue
        seen_mid.add(mid)
        musteri_idler.append(mid)
    if musteri_idler:
        ht_rows = fetch_all(
            """
            SELECT c.id AS musteri_id,
                   COALESCE(NULLIF(TRIM(mk.hizmet_turu), ''), NULLIF(TRIM(c.hizmet_turu), ''), '') AS rapor_hizmet_turu
            FROM customers c
            LEFT JOIN LATERAL (
                SELECT mkx.hizmet_turu
                FROM musteri_kyc mkx
                WHERE mkx.musteri_id = c.id
                ORDER BY mkx.id DESC
                LIMIT 1
            ) mk ON TRUE
            WHERE c.id = ANY(%s::bigint[])
            """,
            (musteri_idler,),
        ) or []
        ht_map = {}
        for r in ht_rows:
            try:
                rid = int((r or {}).get("musteri_id"))
            except (TypeError, ValueError):
                continue
            ht_map[rid] = str((r or {}).get("rapor_hizmet_turu") or "").strip()
        for t in tahsilatlar_list:
            mid = t.get("customer_id") or t.get("musteri_id")
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                mid = None
            if not mid:
                continue
            if str(t.get("rapor_hizmet_turu") or "").strip():
                continue
            t["rapor_hizmet_turu"] = ht_map.get(mid, "")

    toplam = sum(t.get("tutar") or 0 for t in tahsilatlar_list)
    hizmet_rows = fetch_all(
        """
        SELECT DISTINCT hizmet_turu
        FROM (
            SELECT TRIM(COALESCE(c.hizmet_turu, '')) AS hizmet_turu
            FROM customers c
            WHERE TRIM(COALESCE(c.hizmet_turu, '')) <> ''
            UNION
            SELECT TRIM(COALESCE(mk.hizmet_turu, '')) AS hizmet_turu
            FROM musteri_kyc mk
            WHERE TRIM(COALESCE(mk.hizmet_turu, '')) <> ''
        ) x
        WHERE hizmet_turu <> ''
        ORDER BY hizmet_turu
        """
    ) or []
    hizmet_turu_options = [str((r or {}).get("hizmet_turu") or "").strip() for r in hizmet_rows]
    hizmet_turu_options = [x for x in hizmet_turu_options if x]

    return render_template(
        "faturalar/tahsilatlar_tab.html",
        yil=yil,
        baslangic_iso=d0.isoformat(),
        bitis_iso=d1.isoformat(),
        hizmet_turu_options=hizmet_turu_options,
        secili_hizmet_turleri=secili_hizmet_turleri,
        tahsilatlar=tahsilatlar_list,
        toplam=toplam,
    )


@bp.route('/api/hizmet-turleri')
@faturalar_gerekli
def api_faturalar_hizmet_turleri():
    rows = fetch_all(
        """
        SELECT DISTINCT TRIM(COALESCE(hizmet_turu, '')) AS ad
        FROM customers
        WHERE TRIM(COALESCE(hizmet_turu, '')) <> ''
        ORDER BY TRIM(COALESCE(hizmet_turu, ''))
        """
    ) or []
    turler = []
    seen = set()
    for r in rows:
        ad = str((r or {}).get("ad") or "").strip()
        if not ad:
            continue
        key = turkish_lower(ad)
        if key in seen:
            continue
        seen.add(key)
        turler.append({"ad": ad})
    return jsonify({"ok": True, "turler": turler})


def _next_fatura_no(prefix="INV"):
    """Yıla göre artan fatura numarası üret (örn: INV2026000001)."""
    yil = datetime.now().year
    like = f"{prefix}{yil}%"
    row = fetch_one("SELECT fatura_no FROM faturalar WHERE fatura_no LIKE %s ORDER BY id DESC LIMIT 1", (like,))
    if not row or not row.get("fatura_no"):
        return f"{prefix}{yil}000001"
    no = str(row["fatura_no"])
    try:
        tail = int(no[-6:])
        return f"{prefix}{yil}{tail+1:06d}"
    except Exception:
        return f"{prefix}{yil}000001"


_AY_ADLARI_TR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
                 "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _auto_inv_settings():
    ensure_auto_invoice_tables()
    s = fetch_one("SELECT * FROM auto_invoice_settings ORDER BY id LIMIT 1") or {}
    return {
        "enabled": bool(s.get("enabled")),
        "run_day": int(s.get("run_day") or 1),
        "run_hour": int(s.get("run_hour") or 9),
        "send_gib": bool(s.get("send_gib")),
        "auto_sms_code": (s.get("auto_sms_code") or "").strip(),
    }


def _auto_month_amount_from_cache(musteri_id, run_month_date):
    try:
        from routes.giris_routes import _build_aylik_grid_cache_payload
    except Exception:
        _build_aylik_grid_cache_payload = None
    if _build_aylik_grid_cache_payload:
        payload = _build_aylik_grid_cache_payload(int(musteri_id))
        if payload and isinstance(payload.get("aylar"), list):
            key = f"{run_month_date.year}-{run_month_date.month}"
            for a in payload["aylar"]:
                if str(a.get("ay_key")) == key:
                    try:
                        v = float(a.get("tutar_kdv_dahil") or 0)
                        if v > 0:
                            return round(v, 2)
                    except Exception:
                        pass
    kyc = fetch_one(
        "SELECT aylik_kira FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
        (musteri_id,),
    ) or {}
    net = float(kyc.get("aylik_kira") or 0)
    return round(net * 1.2, 2) if net > 0 else 0.0


def _auto_month_amount_resolved(musteri_id, run_month_date):
    """Aylık tutarı cache -> kyc -> son fatura toplam fallback zinciriyle bul."""
    try:
        v = float(_auto_month_amount_from_cache(musteri_id, run_month_date) or 0)
    except Exception:
        v = 0.0
    if v > 0:
        return round(v, 2)

    # KYC/cache boş ise müşterinin son pozitif toplamlı faturasını baz al.
    last_inv = fetch_one(
        f"""
        SELECT toplam
        FROM faturalar
        WHERE musteri_id = %s
          AND COALESCE(toplam, 0) > 0
          AND {sql_expr_fatura_not_gib_taslak("notlar")}
        ORDER BY (fatura_tarihi::date) DESC, id DESC
        LIMIT 1
        """,
        (musteri_id,),
    ) or {}
    try:
        v2 = float(last_inv.get("toplam") or 0)
    except Exception:
        v2 = 0.0
    if v2 > 0:
        return round(v2, 2)

    # Son fallback: customers.ilk_kira_bedeli (net) üzerinden KDV dahil.
    c = fetch_one(
        "SELECT ilk_kira_bedeli FROM customers WHERE id = %s",
        (musteri_id,),
    ) or {}
    try:
        net = float(c.get("ilk_kira_bedeli") or 0)
    except Exception:
        net = 0.0
    return round(net * 1.2, 2) if net > 0 else 0.0


def _auto_invoice_create_for_customer(musteri_id, run_month_date):
    marker = f"|AUTO_INV|{run_month_date.strftime('%Y-%m')}|"
    mevcut = fetch_one(
        """SELECT id, fatura_no FROM faturalar
           WHERE musteri_id = %s
             AND COALESCE(notlar, '') LIKE %s
           ORDER BY id DESC LIMIT 1""",
        (musteri_id, f"%{marker}%"),
    )
    if mevcut:
        return {"status": "exists", "fatura_id": mevcut.get("id"), "fatura_no": mevcut.get("fatura_no")}
    cust = fetch_one("SELECT id, name FROM customers WHERE id = %s", (musteri_id,))
    if not cust:
        return {"status": "error", "error": "Müşteri bulunamadı."}
    toplam = _auto_month_amount_resolved(musteri_id, run_month_date)
    if toplam <= 0:
        return {"status": "skip", "error": "Aylık tutar bulunamadı veya 0."}
    fatura_no = _next_fatura_no()
    ay_ad = _AY_ADLARI_TR[run_month_date.month - 1]
    notlar = f"{ay_ad} {run_month_date.year} otomatik kira faturası {marker}"
    row = execute_returning(
        """INSERT INTO faturalar (
               fatura_no, musteri_id, musteri_adi, tutar, kdv_tutar, toplam,
               durum, fatura_tarihi, vade_tarihi, notlar
           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           RETURNING id""",
        (
            fatura_no,
            musteri_id,
            cust.get("name") or "Müşteri",
            round(toplam / 1.2, 2),
            round(toplam - (toplam / 1.2), 2),
            toplam,
            "odenmedi",
            run_month_date,
            run_month_date,
            notlar,
        ),
    )
    return {"status": "created", "fatura_id": (row or {}).get("id"), "fatura_no": fatura_no, "toplam": toplam}


def run_auto_invoice_cycle(force=False, run_date=None):
    ensure_auto_invoice_tables()
    now = run_date or date.today()
    settings = _auto_inv_settings()
    if (not force) and (not settings["enabled"]):
        return {"ok": True, "skipped": True, "mesaj": "Otomatik fatura kapalı."}
    if (not force) and int(now.day) != int(settings["run_day"]):
        return {"ok": True, "skipped": True, "mesaj": "Bugün planlanan gün değil."}
    period_key = now.strftime("%Y-%m")
    var = fetch_one("SELECT id, status FROM auto_invoice_runs WHERE period_key = %s", (period_key,))
    if var and str(var.get("status") or "").lower() == "success" and not force:
        return {"ok": True, "skipped": True, "mesaj": "Bu dönem zaten çalıştırılmış."}

    run_row = execute_returning(
        """INSERT INTO auto_invoice_runs (period_key, run_date, status, started_at)
           VALUES (%s, %s, 'running', NOW())
           ON CONFLICT (period_key)
           DO UPDATE SET status='running', started_at=NOW(), finished_at=NULL, message=NULL
           RETURNING id""",
        (period_key, now),
    )
    run_id = (run_row or {}).get("id")
    success_count = 0
    fail_count = 0
    send_gib = settings["send_gib"]
    auto_sms = settings["auto_sms_code"]
    gib = None
    if send_gib:
        try:
            from gib_earsiv import BestOfficeGIBManager, build_fatura_data_from_db
            gib = BestOfficeGIBManager()
        except Exception:
            gib = None
            send_gib = False

    musteri_rows = fetch_all(
        """
        SELECT c.id
        FROM customers c
        WHERE LOWER(COALESCE(c.durum, 'aktif')) != 'pasif'
          AND EXISTS (SELECT 1 FROM musteri_kyc k WHERE k.musteri_id = c.id)
        ORDER BY c.id
        """
    ) or []
    for mr in musteri_rows:
        mid = int(mr.get("id"))
        try:
            created = _auto_invoice_create_for_customer(mid, now.replace(day=1))
            if created.get("status") in ("skip", "exists"):
                execute(
                    """INSERT INTO auto_invoice_items (run_id, musteri_id, fatura_id, period_key, status, error_message)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (run_id, mid, created.get("fatura_id"), period_key, created.get("status"), created.get("error")),
                )
                continue
            if created.get("status") != "created":
                fail_count += 1
                execute(
                    """INSERT INTO auto_invoice_items (run_id, musteri_id, period_key, status, error_message)
                       VALUES (%s,%s,%s,'error',%s)""",
                    (run_id, mid, period_key, created.get("error") or "Fatura oluşturulamadı."),
                )
                continue
            fatura_id = int(created.get("fatura_id"))
            gib_uuid = None
            item_status = "created"
            err = None
            if send_gib and gib and gib.is_available():
                try:
                    from gib_earsiv import build_fatura_data_from_db
                    f_data = build_fatura_data_from_db(fatura_id, fetch_one)
                    gib_uuid = gib.fatura_taslak_olustur(f_data)
                    item_status = "gib_draft" if gib_uuid else "gib_fail"
                    if gib_uuid and auto_sms:
                        ok_sms = gib.sms_onay_ve_imzala(gib_uuid, auto_sms)
                        item_status = "gib_signed" if ok_sms else "gib_sms_fail"
                except Exception as ge:
                    item_status = "gib_fail"
                    err = str(ge)
            execute(
                """INSERT INTO auto_invoice_items (run_id, musteri_id, fatura_id, period_key, status, gib_uuid, error_message)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (run_id, mid, fatura_id, period_key, item_status, gib_uuid, err),
            )
            success_count += 1
        except Exception as e:
            fail_count += 1
            execute(
                """INSERT INTO auto_invoice_items (run_id, musteri_id, period_key, status, error_message)
                   VALUES (%s,%s,%s,'error',%s)""",
                (run_id, mid, period_key, str(e)),
            )

    execute(
        """UPDATE auto_invoice_runs
           SET status=%s, finished_at=NOW(), success_count=%s, fail_count=%s, message=%s
           WHERE id=%s""",
        ("success" if fail_count == 0 else "partial", success_count, fail_count, f"{success_count} başarılı, {fail_count} hatalı", run_id),
    )
    return {"ok": True, "run_id": run_id, "success_count": success_count, "fail_count": fail_count}


@bp.route('/api/tutar-yazi')
@faturalar_gerekli
def tutar_yazi_api():
    """Genel toplamı Türkçe yazıya çevir (frontend için)."""
    tutar = request.args.get("tutar", "0").replace(",", ".")
    try:
        val = float(tutar)
    except ValueError:
        val = 0.0
    return jsonify({"yazi": tutar_yaziya(val)})


@bp.route('/fatura-onizleme', methods=['POST'])
@faturalar_gerekli
def fatura_onizleme():
    """Form verileriyle A4 fatura PDF önizlemesi (kaydetmeden)."""
    try:
        data = request.get_json() or {}
        satirlar = data.get("satirlar") or []
        irsaliye_modu = str(data.get("irsaliye_modu") or "").lower() in ("1", "true", "yes")
        musteri_id = _opt_customer_id(data.get("musteri_id"))
        musteri = {}
        if musteri_id:
            m = fetch_one("SELECT id, name, address, tax_number FROM customers WHERE id = %s", (musteri_id,))
            if m:
                musteri = dict(m)
        ara_toplam = float(data.get("ara_toplam") or 0)
        toplam_iskonto = float(data.get("toplam_iskonto") or 0)
        kdv_toplam = float(data.get("kdv_toplam") or 0)
        genel_toplam = float(data.get("toplam") or 0)
        fatura_tarihi = data.get("fatura_tarihi") or datetime.now().strftime("%Y-%m-%d")
        try:
            dt = datetime.strptime(fatura_tarihi[:10], "%Y-%m-%d")
            fatura_tarihi_str = dt.strftime("%d.%m.%Y")
        except Exception:
            fatura_tarihi_str = fatura_tarihi
        # Önizlemede boş numara için DB sorgusu yapma (her tıklamada gecikme)
        _fn = (data.get("fatura_no") or "").strip()
        if not _fn:
            _fn = f"ÖN-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        fatura = {
            "fatura_no": _fn,
            "fatura_tarihi": fatura_tarihi,
            "fatura_tarihi_str": fatura_tarihi_str,
            "fatura_tipi": data.get("fatura_tipi") or "satis",
            "fatura_saati": (data.get("fatura_saati") or "").strip() or None,
            "musteri_adi": data.get("musteri_adi"),
            "notlar": data.get("notlar"),
            "ara_toplam": ara_toplam,
            "toplam_iskonto": toplam_iskonto,
            "kdv_toplam": kdv_toplam,
            "genel_toplam": genel_toplam,
            "toplam": genel_toplam,
            "irsaliye_modu": irsaliye_modu,
            "sevk_adresi": (data.get("sevk_adresi") or "").strip() or None,
        }
        pdf_bytes = build_fatura_pdf(fatura, musteri, satirlar, preview=True)
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": "inline; filename=Fatura_Onizleme.pdf",
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


# --- Aylık kira / hizmet: aynı ay mükerrer engeli yalnızca GİB imzalı fatura varsa ---
_AY_PARSE_VARIANTS = [
    (1, ("ocak",)),
    (2, ("şubat", "subat")),
    (3, ("mart",)),
    (4, ("nisan",)),
    (5, ("mayıs", "mayis")),
    (6, ("haziran",)),
    (7, ("temmuz",)),
    (8, ("ağustos", "agustos")),
    (9, ("eylül", "eylul")),
    (10, ("ekim",)),
    (11, ("kasım", "kasim")),
    (12, ("aralık", "aralik")),
]


def _satir_adindan_ay_yil_cikar(ad):
    """Satır açıklamasından (örn. 'Hizmet bedeli — NİSAN 2026') (yıl, ay) çiftleri."""
    tl = turkish_lower(ad or "")
    out = []
    for mno, variants in _AY_PARSE_VARIANTS:
        for vn in variants:
            if vn not in tl:
                continue
            idx = tl.find(vn)
            chunk = tl[max(0, idx - 16) : idx + len(vn) + 22]
            for my in re.finditer(r"(20\d{2})", chunk):
                try:
                    y = int(my.group(1))
                    if 1990 <= y <= 2100:
                        out.append((y, mno))
                except (TypeError, ValueError):
                    pass
            break
    return out


def _fatura_kira_donemleri_topla(satirlar, fatura_tarihi_iso, notlar):
    """Ay birimi, satır metni ve |AYLIK_TUTAR| işaretinden (yıl, ay) kümesi."""
    donemler = set()
    for m in re.finditer(r"\|AYLIK_TUTAR\|(\d{4}-\d{2}-\d{2})\|", notlar or ""):
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            donemler.add((d.year, d.month))
        except Exception:
            pass
    ay_birimi = False
    if isinstance(satirlar, list):
        for s in satirlar:
            if not isinstance(s, dict):
                continue
            ad = (s.get("ad") or s.get("name") or s.get("hizmet") or "").strip()
            birim = (s.get("birim") or "").strip().lower()
            if birim == "ay":
                ay_birimi = True
            for pair in _satir_adindan_ay_yil_cikar(ad):
                donemler.add(pair)
    if ay_birimi and not donemler and fatura_tarihi_iso:
        try:
            d = datetime.strptime(str(fatura_tarihi_iso)[:10], "%Y-%m-%d").date()
            donemler.add((d.year, d.month))
        except Exception:
            pass
    return donemler


def _musteri_icin_ayda_baska_fatura(musteri_id, y, m, exclude_fatura_id=None):
    """Aynı müşteri için verilen ay/yılda (fatura_tarihi ayı) başka fatura var mı."""
    try:
        mid = int(musteri_id)
    except (TypeError, ValueError):
        return None
    ay1 = date(y, m, 1)
    sql = f"""
        SELECT id, fatura_no, fatura_tarihi, ettn, notlar
        FROM faturalar
        WHERE musteri_id = %s
          AND DATE_TRUNC('month', COALESCE(fatura_tarihi::date, vade_tarihi::date)) = DATE_TRUNC('month', %s::date)
          AND {sql_expr_fatura_not_gib_taslak("notlar")}
    """
    params = [mid, ay1]
    if exclude_fatura_id is not None:
        sql += " AND id <> %s"
        params.append(int(exclude_fatura_id))
    sql += " ORDER BY id DESC LIMIT 1"
    return fetch_one(sql, tuple(params))


def _fatura_gib_imzalanmis_sayilir(dup_row):
    """GİB SMS ile kesinleşmiş (ERP notunda «GİB İMZALANDI» veya durum: imzalı)."""
    if not dup_row:
        return False
    n = str(dup_row.get("notlar") or "")
    if "GİB İMZALANDI" in n:
        return True
    norm = n.replace("İ", "I").replace("ı", "i").replace("�", "I")
    # "GİB/GIB/G�B" gibi encoding varyasyonlarını toleranslı yakala.
    if re.search(r"G.?B\s+IMZALANDI", norm, flags=re.IGNORECASE):
        return True
    # «imzalanmadı» / «imzalanmamış» «imzal» ile başlar; sadece tam «imzalı» / «imzali» kabul et.
    if re.search(r"G.?B\s+durum\s*:\s*imzal[ıi]?\b", norm, flags=re.IGNORECASE):
        return True
    # GİB belge no + ETTN taslak aşamasında da verilir; imzalı saymak için yalnızca yukarıdaki açık izler kullanılır.
    return False


def _fatura_gib_taslak_sayilir(row):
    if not row:
        return False
    n = str(row.get("notlar") or "")
    norm = n.replace("İ", "I").replace("ı", "i").replace("�", "I")
    return bool(re.search(r"G.?B\s+durum\s*:\s*taslak", norm, flags=re.IGNORECASE))


def _fatura_gib_resmi_no(row):
    if not row:
        return ""
    no = str(row.get("fatura_no") or "").strip()
    if re.match(r"^GIB\d{6,}$", no, flags=re.IGNORECASE):
        return no
    notlar = str(row.get("notlar") or "")
    m = re.search(r"G[İI]B\s*FATURA\s*NO\s*:\s*([A-Z0-9-]+)", notlar, flags=re.IGNORECASE)
    return (m.group(1).strip() if m else "")


def _fatura_gib_html_onbellek_var(row):
    try:
        fid = int((row or {}).get("id") or 0)
    except (TypeError, ValueError):
        fid = 0
    if fid <= 0:
        return False
    return bool(_gib_portal_html_cache_oku(fid))


def _fatura_gib_resmi_iz_puani(row):
    """Resmi GİB izi taşıyan satırları yerel INV satırlarının önüne al."""
    if not row:
        return 0
    if _fatura_gib_imzalanmis_sayilir(row):
        return 4
    if _fatura_gib_taslak_sayilir(row):
        return 3
    if _fatura_gib_resmi_no(row):
        return 2
    if str((row or {}).get("ettn") or "").strip():
        return 1
    return 0


def _fatura_resmi_gib_durumu(row):
    """Resmi takip mantığı: yalnız açık imza izi varsa imzalı; açık taslak izi taslaktır."""
    if not row:
        return "Taslak"
    n = str(row.get("notlar") or "")
    nn = n.replace("İ", "I").replace("ı", "i")
    if (
        re.search(r"GIB\s*durum\s*:\s*iptal", nn, flags=re.IGNORECASE)
        or re.search(r"IPTAL\s*KABUL", nn, flags=re.IGNORECASE)
        or re.search(r"IPTAL\s*/\s*ITIRAZ", nn, flags=re.IGNORECASE)
    ):
        return "İptal"
    try:
        fid = int((row or {}).get("id") or 0)
    except (TypeError, ValueError):
        fid = 0
    if fid > 0:
        try:
            from gib_earsiv import gib_fatura_html_watermark_etiket

            h = (_gib_portal_html_cache_oku(fid) or "") or ""
            wm = gib_fatura_html_watermark_etiket(h)
            if wm in ("İptal", "İmzasız", "İmzalı"):
                return wm
        except Exception:
            pass
    if _fatura_gib_taslak_sayilir(row):
        return "Taslak"
    if _fatura_gib_imzalanmis_sayilir(row):
        return "İmzalı"
    return "Taslak"


def _fatura_musteri_ad_norm(v):
    s = turkish_lower(str(v or "").strip())
    return re.sub(r"[^a-z0-9]+", "", s)


def _fatura_satir_tarih_iso(row):
    s = str((row or {}).get("fatura_tarihi") or (row or {}).get("tarih") or "").strip()
    return s[:10] if len(s) >= 10 else s


def _fatura_satir_tutar(row):
    val = (row or {}).get("toplam")
    if val in (None, "", 0, 0.0):
        val = (row or {}).get("tutar")
    try:
        return round(float(val or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _fatura_satir_kdv_tutar_hesapla(ln):
    """Tek satır için KDV tutarı (satır JSON’da kdv_tutar yoksa kdv_orani + netten hesaplanır)."""
    if not isinstance(ln, dict):
        return 0.0
    try:
        kt = ln.get("kdv_tutar")
        if kt is not None and str(kt).strip() != "":
            return round(max(0.0, float(kt)), 2)
    except (TypeError, ValueError):
        pass
    try:
        st = float(ln.get("satir_toplam") or 0)
        mt = float(ln.get("mal_tutar") or ln.get("net") or 0)
        if st > 0 and mt >= 0 and st >= mt:
            return round(max(0.0, st - mt), 2)
    except (TypeError, ValueError):
        pass
    try:
        miktar = float(ln.get("miktar") or 0)
        birim_fiyat = float(ln.get("birim_fiyat") or 0)
        isk_oran = float(ln.get("iskonto_orani") or 0)
        brut = miktar * birim_fiyat
        isk_tutar_giris = ln.get("iskonto_tutar")
        if isk_tutar_giris is not None and float(isk_tutar_giris) > 0:
            isk_tutar = min(float(isk_tutar_giris), brut)
        else:
            isk_tutar = brut * isk_oran / 100.0
        net = brut - isk_tutar
        kdv_oran = float(ln.get("kdv_orani") or 0)
        return round(max(0.0, net * kdv_oran / 100.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _fatura_kdv_toplam_satirlardan(row):
    """satirlar_json varsa satır bazlı KDV toplamı; yok veya boşsa None (başlık kdv_tutar kullanılır)."""
    raw = (row or {}).get("satirlar_json")
    if not raw:
        return None
    try:
        arr = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(arr, list) or len(arr) == 0:
        return None
    s = sum(_fatura_satir_kdv_tutar_hesapla(ln) for ln in arr)
    return round(s, 2)


def _fatura_kdv_cikarma_toplam_tutar(row):
    """Yeni fatura kaydı: tutar = KDV hariç ara, toplam = KDV dahil genel → KDV = toplam - tutar.
    Bozuk satır/DB kdv_tutar alanlarından bağımsız; taslağın içindeki özet ile aynı mantık."""
    try:
        toplam = float((row or {}).get("toplam") or 0)
        tutar = float((row or {}).get("tutar") or 0)
    except (TypeError, ValueError):
        return None
    if toplam <= 0:
        return None
    if tutar < -0.01 or tutar > toplam + 0.02:
        return None
    implied = round(max(0.0, toplam - tutar), 2)
    # Makul üst sınır: KDV, genel toplamın ~yarısından fazla olamaz (veri hatası).
    if implied > toplam * 0.46:
        return None
    return implied


def _fatura_kdv_liste_gosterim(row):
    """ERP taslak listesi / API: önce toplam-tutar (yeni fatura özetiyle aynı), sonra satırlar, sonra DB."""
    ext = _fatura_kdv_cikarma_toplam_tutar(row)
    if ext is not None:
        return ext
    v = _fatura_kdv_toplam_satirlardan(row)
    if v is not None:
        return round(max(0.0, v), 2)
    try:
        return round(float((row or {}).get("kdv_tutar") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _fatura_editor_url(row):
    if not row:
        return ""
    edit_id = row.get("erp_duzenle_id") or row.get("id")
    portal_musteri_adi = str(
        row.get("musteri_adi_goster") or row.get("customer_name") or row.get("musteri_adi") or ""
    ).strip()
    q_edit = {}
    if portal_musteri_adi:
        q_edit["portal_musteri_adi"] = portal_musteri_adi
    try:
        mid_edit = int(row.get("musteri_id") or 0)
        if mid_edit > 0:
            q_edit["portal_musteri_id"] = mid_edit
    except (TypeError, ValueError):
        pass
    try:
        if edit_id is not None and int(edit_id) > 0:
            if q_edit:
                q_edit["edit_fatura_id"] = int(edit_id)
                return f"/faturalar/yeni?{urlencode(q_edit)}"
            return f"/faturalar/yeni?edit_fatura_id={int(edit_id)}"
    except (TypeError, ValueError):
        pass
    uuid_val = str(row.get("ettn") or "").strip()
    if not uuid_val:
        return ""
    q = {
        "portal_uuid": uuid_val,
        "portal_fatura_no": str(row.get("fatura_no") or "").strip(),
        "portal_tarih": _fatura_satir_tarih_iso(row),
        "portal_musteri_adi": portal_musteri_adi,
        "portal_tutar": _fatura_satir_tutar(row),
        "portal_gib_durum": str(row.get("gib_durum_rapor") or row.get("gib_durum") or "").strip().lower(),
    }
    try:
        mid = int(row.get("musteri_id") or 0)
        if mid > 0:
            q["portal_musteri_id"] = mid
    except (TypeError, ValueError):
        pass
    return f"/faturalar/yeni?{urlencode(q)}"


def _gib_any_tutar_from_row(d):
    if not isinstance(d, dict):
        return 0.0
    for k in (
        "odenecekTutar",
        "toplamTutar",
        "genelToplam",
        "vergilerDahilToplamTutar",
        "vergilerDahilToplam",
        "vergilerDahilTutar",
        "faturaTutari",
        "tutar",
    ):
        v = d.get(k)
        if v is None:
            continue
        s = str(v).strip().replace("₺", "").replace("TL", "").replace(" ", "")
        if not s:
            continue
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(".", "").replace(",", ".")
        try:
            fv = float(s)
            if fv >= 0:
                return round(fv, 2)
        except (TypeError, ValueError):
            continue
    return 0.0


def _gib_html_toplam_parse(html):
    s = str(html or "")
    if not s:
        return 0.0
    patterns = [
        r"Vergiler\s+Dahil\s+Toplam\s+Tutar.*?>\s*([0-9\.\,]+)\s*TL",
        r"Ödenecek\s+Tutar.*?>\s*([0-9\.\,]+)\s*TL",
        r"Odenecek\s+Tutar.*?>\s*([0-9\.\,]+)\s*TL",
    ]
    for p in patterns:
        m = re.search(p, s, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        raw = (m.group(1) or "").strip().replace(".", "").replace(",", ".")
        try:
            v = float(raw)
            if v >= 0:
                return round(v, 2)
        except (TypeError, ValueError):
            continue
    return 0.0


def _gib_satir_from_status_dict(d):
    if not isinstance(d, dict):
        return {}
    fno = str(
        d.get("belgeNo")
        or d.get("belgeNumarasi")
        or d.get("faturaNo")
        or d.get("fatura_no")
        or ""
    ).strip()
    ettn = str(d.get("ettn") or d.get("uuid") or "").strip()
    ad = str(
        d.get("aliciUnvanAdSoyad")
        or d.get("aliciUnvan")
        or d.get("unvan")
        or d.get("musteri_adi")
        or ""
    ).strip()
    if not ad:
        ad = f"{str(d.get('aliciAdi') or '').strip()} {str(d.get('aliciSoyadi') or '').strip()}".strip()
    tarih = ""
    try:
        from gib_earsiv import BestOfficeGIBManager
        tarih = BestOfficeGIBManager._portal_fatura_tarihi_iso(d) or ""
    except Exception:
        tarih = str(d.get("faturaTarihi") or d.get("tarih") or "")[:10]
    onay = str(d.get("onayDurumu") or d.get("durum") or "").strip().lower()
    gib_durum = "Taslak"
    if "iptal" in onay:
        gib_durum = "İptal"
    elif "onay" in onay or "imza" in onay:
        gib_durum = "İmzalı"
    return {
        "fatura_no": fno,
        "ettn": ettn,
        "musteri_adi": ad or "—",
        "fatura_tarihi": tarih,
        "tutar": _gib_any_tutar_from_row(d),
        "gib_durum": gib_durum,
    }


def _gib_durum_portal_metni_upsert_asama(gib_durum_raw) -> str:
    """Portal/rapor metninden _fatura_gib_bilgilerini_yaz asaması: imzali | taslak | iptal."""
    s = turkish_lower(str(gib_durum_raw or "").strip())
    if not s:
        return "taslak"
    if "iptal" in s:
        return "iptal"
    if any(x in s for x in ("taslak", "imzalanmad", "onaylanmad", "beklemede", "henuz imza")):
        return "taslak"
    # «imzalanmadı» da «imza» içerir; yalnız kesin imzalı / imzalandı ifadeleri.
    if "imzaland" in s or ("imzali" in s and "imzalanmad" not in s):
        return "imzali"
    return "taslak"


def _gibden_erp_upsert(gib_row):
    if not gib_row:
        return {"ok": False, "mesaj": "GİB satırı bulunamadı."}
    fatura_no = str(gib_row.get("fatura_no") or "").strip()
    ettn = str(gib_row.get("ettn") or "").strip()
    musteri_adi = str(gib_row.get("musteri_adi") or "").strip() or "—"
    fatura_tarihi = str(gib_row.get("fatura_tarihi") or "").strip()[:10] or date.today().isoformat()
    toplam = float(gib_row.get("tutar") or 0)
    gib_asama = _gib_durum_portal_metni_upsert_asama(gib_row.get("gib_durum"))
    # Portal liste durum alanı bazı hesaplarda güvenilir olmayabiliyor.
    # ETTN varsa gerçek GİB HTML filigranından kesin durumu okuyup önceliklendir.
    if ettn:
        try:
            from gib_earsiv import BestOfficeGIBManager, gib_fatura_html_watermark_etiket

            g = BestOfficeGIBManager()
            if g.is_available():
                h = g.fatura_html_getir(ettn, days_back=370) or ""
                wm = gib_fatura_html_watermark_etiket(h)
                wm_map = {"İptal": "iptal", "İmzasız": "taslak", "İmzalı": "imzali"}
                if wm in wm_map:
                    gib_asama = wm_map[wm]
        except Exception:
            pass
    if toplam <= 0 and ettn:
        try:
            ref = fetch_one(
                """
                SELECT toplam
                FROM faturalar
                WHERE (BTRIM(COALESCE(ettn::text,'')) = BTRIM(%s)
                       OR COALESCE(notlar,'') ILIKE %s)
                  AND COALESCE(toplam, 0) > 0
                ORDER BY id DESC
                LIMIT 1
                """,
                (ettn, f"%{ettn}%"),
            )
            if ref and ref.get("toplam") is not None:
                toplam = float(ref.get("toplam") or 0)
        except Exception:
            pass
    tutar = round((toplam / 1.2), 2) if toplam > 0 else 0.0
    kdv_tutar = round(max(0.0, toplam - tutar), 2) if toplam > 0 else 0.0

    musteri_id = None
    try:
        m = fetch_one(
            """
            SELECT id
            FROM customers
            WHERE LOWER(TRIM(COALESCE(name, ''))) = LOWER(TRIM(%s))
               OR LOWER(TRIM(COALESCE(musteri_adi, ''))) = LOWER(TRIM(%s))
            ORDER BY id DESC
            LIMIT 1
            """,
            (musteri_adi, musteri_adi),
        )
        if m and m.get("id"):
            musteri_id = int(m.get("id"))
    except Exception:
        pass

    mevcut = None
    if ettn:
        mevcut = fetch_one(
            "SELECT id, fatura_no FROM faturalar WHERE BTRIM(COALESCE(ettn::text,'')) = BTRIM(%s) ORDER BY id DESC LIMIT 1",
            (ettn,),
        )
    if (not mevcut) and fatura_no:
        mevcut = fetch_one("SELECT id, fatura_no FROM faturalar WHERE fatura_no = %s ORDER BY id DESC LIMIT 1", (fatura_no,))

    if mevcut and mevcut.get("id"):
        fid = int(mevcut.get("id"))
        # GİB tarafında iptal/imzasız ise mevcut tutarı/notları zorla değiştirme; sadece durum etiketini güncelle.
        if gib_asama in ("iptal", "taslak"):
            _fatura_gib_bilgilerini_yaz(fid, ettn or None, fatura_no or None, gib_asama=gib_asama)
            return {"ok": True, "fatura_id": fid, "islem": "guncellendi"}
        execute(
            """
            UPDATE faturalar
               SET fatura_no = COALESCE(NULLIF(%s,''), fatura_no),
                   musteri_id = COALESCE(%s, musteri_id),
                   musteri_adi = COALESCE(NULLIF(%s,''), musteri_adi),
                   tutar = CASE WHEN %s > 0 THEN %s ELSE tutar END,
                   kdv_tutar = CASE WHEN %s > 0 THEN %s ELSE kdv_tutar END,
                   toplam = CASE WHEN %s > 0 THEN %s ELSE toplam END,
                   fatura_tarihi = CASE WHEN NULLIF(%s, '') IS NOT NULL THEN %s ELSE fatura_tarihi END
             WHERE id = %s
            """,
            (
                fatura_no,
                musteri_id,
                musteri_adi,
                toplam,
                tutar,
                toplam,
                kdv_tutar,
                toplam,
                toplam,
                (fatura_tarihi or ""),
                (fatura_tarihi or ""),
                fid,
            ),
        )
        if toplam > 0:
            satir = [{
                "ad": "Hizmet",
                "miktar": 1,
                "birim": "Ay",
                "birim_fiyat": tutar,
                "iskonto_tipi": "iskonto",
                "iskonto_orani": 0,
                "iskonto_tutar": None,
                "kdv_orani": 20,
            }]
            execute("UPDATE faturalar SET satirlar_json = %s WHERE id = %s", (json.dumps(satir), fid))
        _fatura_gib_bilgilerini_yaz(fid, ettn or None, fatura_no or None, gib_asama=gib_asama)
        return {"ok": True, "fatura_id": fid, "islem": "guncellendi"}

    # ERP'de eşleşen kayıt yok: yalnızca GİB'de imzalı satırlar yeni fatura olarak ERP'ye eklenir.
    if gib_asama != "imzali":
        return {
            "ok": False,
            "atlandi": True,
            "sebep": gib_asama or "imzasiz",
            "mesaj": (
                "GİB tarafında imzalı olmayan satır (durum: " + (gib_asama or "taslak/imzasız")
                + "). ERP'ye yeni kayıt oluşturulmadı."
            ),
        }

    execute(
        """
        INSERT INTO faturalar (
            fatura_no, musteri_id, musteri_adi, tutar, kdv_tutar, toplam, durum, fatura_tarihi, notlar, ettn, satirlar_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            fatura_no or _next_fatura_no("INV"),
            musteri_id,
            musteri_adi,
            tutar,
            kdv_tutar,
            toplam,
            "odenmedi",
            fatura_tarihi,
            None,
            ettn or None,
            json.dumps([{
                "ad": "Hizmet",
                "miktar": 1,
                "birim": "Ay",
                "birim_fiyat": tutar,
                "iskonto_tipi": "iskonto",
                "iskonto_orani": 0,
                "iskonto_tutar": None,
                "kdv_orani": 20,
            }]) if toplam > 0 else json.dumps([]),
        ),
    )
    row = fetch_one("SELECT id FROM faturalar WHERE BTRIM(COALESCE(ettn::text,'')) = BTRIM(%s) ORDER BY id DESC LIMIT 1", (ettn,)) if ettn else None
    if (not row) and fatura_no:
        row = fetch_one("SELECT id FROM faturalar WHERE fatura_no = %s ORDER BY id DESC LIMIT 1", (fatura_no,))
    fid = int((row or {}).get("id") or 0)
    if fid > 0:
        _fatura_gib_bilgilerini_yaz(fid, ettn or None, fatura_no or None, gib_asama=gib_asama)
    return {"ok": True, "fatura_id": (fid or None), "islem": "eklendi"}


def _fatura_ekran_yerel_inv_satiri_gizlenmeli_mi(row, tum_satirlar):
    """Aynı müşteri+tarih+tutarda resmi GİB izi olan satır varken çıplak INV satırını gizle."""
    if not row:
        return False
    no = str(row.get("fatura_no") or "").strip().upper()
    if not no or no.startswith("GIB"):
        return False
    if str(row.get("ettn") or "").strip():
        return False
    if _fatura_gib_resmi_iz_puani(row) > 0:
        return False
    ad = _fatura_musteri_ad_norm(
        row.get("musteri_adi_goster") or row.get("customer_name") or row.get("musteri_adi") or ""
    )
    tarih = _fatura_satir_tarih_iso(row)
    tutar = _fatura_satir_tutar(row)
    if not ad or not tarih or tutar <= 0:
        return False
    aday_resmi = []
    aday_inv = []
    for other in (tum_satirlar or []):
        if other is row:
            continue
        if _fatura_musteri_ad_norm(
            other.get("musteri_adi_goster") or other.get("customer_name") or other.get("musteri_adi") or ""
        ) != ad:
            continue
        if _fatura_satir_tarih_iso(other) != tarih:
            continue
        if abs(_fatura_satir_tutar(other) - tutar) > 0.01:
            continue
        if _fatura_gib_resmi_iz_puani(other) > 0:
            aday_resmi.append(other)
        else:
            ono = str(other.get("fatura_no") or "").strip().upper()
            if ono.startswith("INV"):
                aday_inv.append(other)

    if not aday_resmi:
        return False

    # Birden fazla INV veya birden fazla resmi aday varsa yanlış gizleme riski artar.
    # Bu durumda satırı görünür bırak.
    if len(aday_inv) > 0 or len(aday_resmi) > 1:
        return False

    other = aday_resmi[0]
    notlar = str(row.get("notlar") or "")
    o_ettn = str(other.get("ettn") or "").strip()
    o_fno = str(other.get("fatura_no") or "").strip().upper()

    # Güvenli eşleşme: yerel INV notlarında resmi no/ettn izini görüyorsak gizle.
    if o_ettn and o_ettn.lower() in notlar.lower():
        return True
    if o_fno and o_fno.startswith("GIB") and o_fno in notlar.upper():
        return True

    # Not bağlantısı yoksa yalnızca "tekil birebir çift" koşulunda gizle.
    # (Aynı gün/tutarda birden fazla fatura olan müşteriler yanlışlıkla kaybolmasın.)
    bucket_inv_count = 0
    bucket_resmi_count = 0
    for s in (tum_satirlar or []):
        if _fatura_musteri_ad_norm(
            s.get("musteri_adi_goster") or s.get("customer_name") or s.get("musteri_adi") or ""
        ) != ad:
            continue
        if _fatura_satir_tarih_iso(s) != tarih:
            continue
        if abs(_fatura_satir_tutar(s) - tutar) > 0.01:
            continue
        sno = str(s.get("fatura_no") or "").strip().upper()
        if sno.startswith("INV") and not str(s.get("ettn") or "").strip():
            bucket_inv_count += 1
        if _fatura_gib_resmi_iz_puani(s) > 0:
            bucket_resmi_count += 1
    return bucket_inv_count == 1 and bucket_resmi_count == 1


def _odeme_duzeni_norm(v):
    s = (str(v or "").strip().lower() or "aylik")
    if s not in ("aylik", "yillik", "3_aylik", "6_aylik", "manuel"):
        s = "aylik"
    return s


def _odeme_duzeni_label(v, manuel_txt=""):
    k = _odeme_duzeni_norm(v)
    m = (manuel_txt or "").strip()
    if k == "yillik":
        return "Yıllık"
    if k == "3_aylik":
        return "3 Aylık"
    if k == "6_aylik":
        return "6 Aylık"
    if k == "manuel":
        return ("Manuel: " + m) if m else "Manuel"
    return "Aylık"


def _fatura_canli_gib_html_ile_durum_duzenle(row, gib, fetch_live=True, html_oncelikli=None, kalici=True):
    """GİB fatura HTML’indeki filigranlara göre gib_durum_rapor günceller.

    Filigran «İPTAL EDİLMİŞTİR» → İptal, «İMZASIZ» → İmzasız, hiçbiri yoksa İmzalı.
    `kalici=True` ise ERP notları da `_fatura_gib_bilgilerini_yaz` ile güncellenir.
    """
    if not gib or not row:
        return
    if str(os.getenv("GIB_HTML_FILIGRAN_ISTEK", "1") or "").strip().lower() in ("0", "false", "no", "off"):
        return
    from gib_earsiv import gib_fatura_html_watermark_etiket

    pet = str((row or {}).get("ettn") or "").strip()
    if not pet:
        return
    html = html_oncelikli if isinstance(html_oncelikli, str) else ""
    try:
        fid = int((row or {}).get("id") or 0)
    except (TypeError, ValueError):
        fid = 0
    if fid > 0 and (not html or len(html) < 200):
        try:
            html = (_gib_portal_html_cache_oku(fid) or "") or ""
        except Exception:
            pass
    if fetch_live and (not html or len(html) < 200):
        try:
            html = gib.fatura_html_getir(pet, days_back=370) or ""
        except Exception:
            pass
    wm = gib_fatura_html_watermark_etiket(html)
    if not wm:
        return
    onceki = str(row.get("gib_durum_rapor") or "").strip()
    row["gib_durum_rapor"] = wm
    if not kalici or fid <= 0:
        return
    asama_map = {"İptal": "iptal", "İmzasız": "taslak", "İmzalı": "imzali"}
    yeni_asama = asama_map.get(wm)
    if not yeni_asama:
        return
    onceki_asama = asama_map.get(onceki)
    if onceki_asama == yeni_asama:
        return
    try:
        gib_no = (row.get("fatura_no") or "")
        _fatura_gib_bilgilerini_yaz(fid, pet or None, gib_no or None, gib_asama=yeni_asama)
    except Exception as ex:
        logging.getLogger(__name__).warning(
            "GİB durum kalıcı yazımı başarısız fid=%s: %s", fid, ex
        )


def _faturalar_ekran_erp_gib_birlestir(erp_rows, baslangic, bitis, ofis_kodu=""):
    erp_rows = [dict(r or {}) for r in (erp_rows or [])]
    for row in erp_rows:
        row["kaynak"] = "erp"
        row["gib_durum_rapor"] = _fatura_resmi_gib_durumu(row)
        row["toplam"] = _fatura_satir_tutar(row)
        row["duzenle_url"] = _fatura_editor_url(row)
    try:
        from gib_earsiv import BestOfficeGIBManager
        gib = BestOfficeGIBManager()
        if not gib.is_available() or getattr(gib, "client_type", "") != "earsivportal":
            return [r for r in erp_rows if not _fatura_ekran_yerel_inv_satiri_gizlenmeli_mi(r, erp_rows)]
        portal_rows = gib.portal_kesilen_fatura_listesi_normalized(baslangic, bitis) or []
    except Exception as ex:
        logging.getLogger(__name__).warning("faturalar ekranı GİB portal listeleme: %s", ex)
        return [r for r in erp_rows if not _fatura_ekran_yerel_inv_satiri_gizlenmeli_mi(r, erp_rows)]

    key_to_erp = {}
    party_day_to_erp = {}
    for row in erp_rows:
        fn = str(row.get("fatura_no") or "").strip().upper()
        et = str(row.get("ettn") or "").strip().lower()
        if fn:
            key_to_erp[fn] = row
        if et:
            key_to_erp[et] = row
        party_key = (
            _fatura_musteri_ad_norm(
                row.get("musteri_adi_goster") or row.get("customer_name") or row.get("musteri_adi") or ""
            ),
            _fatura_satir_tarih_iso(row),
        )
        if party_key[0] and party_key[1]:
            party_day_to_erp.setdefault(party_key, []).append(row)

    merged = []
    used_erp_ids = set()
    for p in portal_rows:
        p = dict(p or {})
        pfn = str(p.get("fatura_no") or "").strip().upper()
        pet = str(p.get("ettn") or "").strip().lower()
        erp_hit = None
        if pfn and pfn in key_to_erp:
            erp_hit = key_to_erp[pfn]
        if erp_hit is None and pet and pet in key_to_erp:
            erp_hit = key_to_erp[pet]
        if erp_hit is not None:
            row = dict(erp_hit)
            row["gib_durum_rapor"] = p.get("gib_durum") or row.get("gib_durum_rapor") or "Taslak"
            row["kaynak"] = "erp"
            if pfn:
                row["fatura_no"] = pfn
            pet_raw = str(p.get("ettn") or row.get("ettn") or "").strip()
            if pet_raw and not str(row.get("ettn") or "").strip():
                row["ettn"] = pet_raw
            # GİB satırıyla eşleştiyse raporda müşteri adı için GİB'i otorite al.
            p_ad = str(p.get("musteri_adi") or "").strip()
            if p_ad:
                row["musteri_adi"] = p_ad
                row["musteri_adi_goster"] = p_ad
                row["customer_name"] = p_ad

            html_snip = ""
            try:
                fid = int(row.get("id") or 0)
            except (TypeError, ValueError):
                fid = 0
            if fid > 0:
                try:
                    html_snip = _gib_portal_html_cache_oku(fid) or ""
                except Exception:
                    html_snip = ""

            # Tutar için öncelik: portal listesi > GİB HTML parse > ERP toplam.
            erp_toplam = _fatura_satir_tutar(row)
            portal_toplam = _fatura_satir_tutar(p)
            final_toplam = erp_toplam
            if portal_toplam > 0:
                final_toplam = portal_toplam
            else:
                html_toplam = _gib_html_toplam_parse(html_snip) if html_snip else 0.0
                if html_toplam <= 0 and pet_raw:
                    try:
                        html_snip = gib.fatura_html_getir(pet_raw, days_back=370) or html_snip
                        html_toplam = _gib_html_toplam_parse(html_snip)
                    except Exception:
                        html_toplam = 0.0
                if html_toplam > 0:
                    final_toplam = html_toplam
            row["toplam"] = final_toplam
            _fatura_canli_gib_html_ile_durum_duzenle(row, gib, fetch_live=True, html_oncelikli=html_snip)
            try:
                _fid = int(row.get("id") or 0)
            except (TypeError, ValueError):
                _fid = 0
            if _fid > 0 and pet_raw and (not html_snip or len(html_snip) < 200):
                try:
                    _gib_portal_html_indir_ve_kaydet(_fid, pet_raw, gib)
                except Exception:
                    pass
            row["duzenle_url"] = _fatura_editor_url(row)
            merged.append(row)
            if row.get("id") is not None:
                used_erp_ids.add(row.get("id"))
            continue

        portal_row = {
            "id": None,
            "fatura_no": p.get("fatura_no") or "",
            "ettn": p.get("ettn") or "",
            "fatura_tarihi": p.get("fatura_tarihi") or "",
            "musteri_adi": p.get("musteri_adi") or "—",
            "musteri_adi_goster": p.get("musteri_adi") or "—",
            "customer_name": p.get("musteri_adi") or "—",
            "ofis_kodu": "",
            "toplam": _fatura_satir_tutar(p),
            "durum": "",
            "gib_durum_rapor": p.get("gib_durum") or "Taslak",
            "kaynak": "gib_portal",
        }
        party_key = (_fatura_musteri_ad_norm(portal_row["musteri_adi_goster"]), _fatura_satir_tarih_iso(portal_row))
        adaylar = party_day_to_erp.get(party_key) or []
        duzenlenebilir_adaylar = [
            a for a in adaylar
            if not str(a.get("ettn") or "").strip()
            and not str(a.get("fatura_no") or "").strip().upper().startswith("GIB")
        ]
        if len(duzenlenebilir_adaylar) == 1:
            aday = duzenlenebilir_adaylar[0]
            portal_row["erp_duzenle_id"] = aday.get("id")
            if not portal_row.get("toplam"):
                portal_row["toplam"] = _fatura_satir_tutar(aday)
            if not portal_row.get("ofis_kodu"):
                portal_row["ofis_kodu"] = aday.get("ofis_kodu") or ""
            if not portal_row.get("musteri_id"):
                portal_row["musteri_id"] = aday.get("musteri_id")
        if ofis_kodu and (portal_row.get("ofis_kodu") or "") != ofis_kodu:
            continue
        _fatura_canli_gib_html_ile_durum_duzenle(portal_row, gib, fetch_live=True, html_oncelikli=None)
        portal_row["duzenle_url"] = _fatura_editor_url(portal_row)
        merged.append(portal_row)

    for row in erp_rows:
        if row.get("id") in used_erp_ids:
            continue
        if _fatura_ekran_yerel_inv_satiri_gizlenmeli_mi(row, erp_rows):
            continue
        _fatura_canli_gib_html_ile_durum_duzenle(row, gib, fetch_live=True, html_oncelikli=None)
        row["duzenle_url"] = _fatura_editor_url(row)
        merged.append(row)
    return merged


def _fatura_ay_icin_mukerrer_engel_var_mi(dup_row):
    """Aynı ay için ikinci kayıt: yalnızca GİB'de kesinleşmiş (SMS / «GİB İMZALANDI») fatura engeller.

    ERP içi INV… veya GİB taslağı (henüz imzalanmamış) aynı ayda tekrar fatura açılmasına izin verilir;
    kullanıcı GİB'e gönderene kadar «tam» mükerrer sayılmaz."""
    if not dup_row:
        return False
    return _fatura_gib_imzalanmis_sayilir(dup_row)


@bp.route("/api/secilen-aylar-fatura-kontrol", methods=["POST"])
@faturalar_gerekli
def api_secilen_aylar_fatura_kontrol():
    """Sözleşme gridinden «Seçilenlerden fatura oluştur» öncesi kontrol.

    Kural: Aynı ayda yalnızca GİB'de kesinleşmiş fatura (notlarda «GİB İMZALANDI» vb.) varsa uyarı.
    Sadece ERP'de kayıtlı (INV…) veya henüz imzalanmamış GİB taslağı mükerrer sayılmaz.
    """
    try:
        data = request.get_json() or {}
    except Exception:
        data = {}
    musteri_id = _opt_customer_id(data.get("musteri_id"))
    if not musteri_id:
        return jsonify({"ok": False, "mesaj": "musteri_id gerekli."}), 400
    aylar = data.get("aylar")
    if not isinstance(aylar, list) or not aylar:
        return jsonify({"ok": False, "mesaj": "aylar listesi gerekli."}), 400
    cakismalar = []
    seen = set()
    for raw in aylar:
        if not isinstance(raw, dict):
            continue
        try:
            yv = int(raw.get("yil"))
            mv = int(raw.get("ay"))
        except (TypeError, ValueError):
            continue
        if mv < 1 or mv > 12 or yv < 1990 or yv > 2100:
            continue
        k = (yv, mv)
        if k in seen:
            continue
        seen.add(k)
        dup = _musteri_icin_ayda_baska_fatura(int(musteri_id), yv, mv)
        if dup and _fatura_ay_icin_mukerrer_engel_var_mi(dup):
            cakismalar.append({
                "yil": yv,
                "ay": mv,
                "fatura_no": dup.get("fatura_no"),
                "fatura_id": dup.get("id"),
            })
    return jsonify({"ok": True, "cakismalar": cakismalar})


def _fatura_notlara_erp_taslak_etiketi_uygula(notlar, aktif=False):
    """Not alanına ERP taslak etiketini ekler/çıkarır."""
    text = str(notlar or "")
    text = re.sub(r"\s*\|\s*ERP\s+durum\s*:\s*taslak\s*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\s*\|\s*\|\s*", " | ", text).strip(" |")
    if not aktif:
        return text or None
    if not text:
        return "ERP durum: taslak"
    return (text + " | ERP durum: taslak").strip()


@bp.route('/fatura-ekle', methods=['POST'])
@faturalar_gerekli
def fatura_ekle():
    """Yeni fatura ekle (satır detaylarıyla)."""
    try:
        ensure_faturalar_amount_columns()
        data = request.get_json() or {}
        erp_taslak_kayit = str(data.get("erp_taslak") or "").strip().lower() in ("1", "true", "yes", "on")
        satirlar = data.get("satirlar") or []
        irsaliye_modu = str(data.get("irsaliye_modu") or "").lower() in ("1", "true", "yes")
        ara_toplam = float(data.get("ara_toplam") or 0)
        toplam_iskonto = float(data.get("toplam_iskonto") or 0)
        kdv_toplam = float(data.get("kdv_toplam") or 0)
        toplam = float(data.get("toplam") or 0)

        # Eğer frontend toplamları göndermediyse satırlardan hesapla
        if satirlar and toplam == 0:
            ara_toplam = 0.0
            toplam_iskonto = 0.0
            kdv_toplam = 0.0
            for s in satirlar:
                miktar = float(s.get("miktar") or 0)
                birim_fiyat = float(s.get("birim_fiyat") or 0)
                isk_oran = float(s.get("iskonto_orani") or 0)
                isk_tutar_giris = s.get("iskonto_tutar")
                kdv = float(s.get("kdv_orani") or 0)
                brut = miktar * birim_fiyat
                if isk_tutar_giris is not None and float(isk_tutar_giris) > 0:
                    isk_tutar = min(float(isk_tutar_giris), brut)
                else:
                    isk_tutar = brut * isk_oran / 100.0
                net = brut - isk_tutar
                kdv_tutar = net * kdv / 100.0
                ara_toplam += brut
                toplam_iskonto += isk_tutar
                kdv_toplam += kdv_tutar
            toplam = ara_toplam - toplam_iskonto + kdv_toplam

        tutar = ara_toplam - toplam_iskonto
        kdv_tutar = kdv_toplam

        raw_fat_tarih = (data.get("fatura_tarihi") or "").strip()
        if not raw_fat_tarih:
            fatura_tarihi = datetime.now().strftime("%Y-%m-%d")
        elif re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", raw_fat_tarih):
            g, a, y = raw_fat_tarih.split(".")
            fatura_tarihi = f"{y}-{a.zfill(2)}-{g.zfill(2)}"
        else:
            fatura_tarihi = raw_fat_tarih[:10]

        vade_tarihi = (data.get("vade_tarihi") or "").strip() or None
        if vade_tarihi and len(vade_tarihi) == 10 and vade_tarihi[2] == ".":
            g, a, y = vade_tarihi.split(".")
            vade_tarihi = f"{y}-{a.zfill(2)}-{g.zfill(2)}"

        edit_fatura_id = data.get("fatura_id")
        try:
            edit_fatura_id = int(edit_fatura_id) if edit_fatura_id is not None else None
        except (TypeError, ValueError):
            edit_fatura_id = None
        mevcut_fatura = None
        if edit_fatura_id:
            mevcut_fatura = fetch_one(
                "SELECT id, fatura_no, ettn, notlar FROM faturalar WHERE id = %s",
                (edit_fatura_id,),
            )
            if not mevcut_fatura:
                return jsonify({"ok": False, "mesaj": "Düzenlenecek fatura bulunamadı."}), 404
        gelen_ettn_req = str(data.get("ettn") or (mevcut_fatura or {}).get("ettn") or "").strip()
        gelen_gib_durum_req = str(data.get("gib_durum") or "").strip().lower()
        # Varsayılan davranış: GİB'de imzalanmamış kayıtlar ERP taslağıdır.
        # Böylece kullanıcı "Kaydet" ile ön kayıt alır; cariye etkisi olmaz.
        if (not erp_taslak_kayit) and (not gelen_ettn_req) and gelen_gib_durum_req != "imzali":
            erp_taslak_kayit = True

        fatura_no = (data.get("fatura_no") or "").strip() or (
            str((mevcut_fatura or {}).get("fatura_no") or "").strip() or _next_fatura_no()
        )
        musteri_id = _opt_customer_id(data.get("musteri_id"))
        musteri_adi = (data.get("musteri_adi") or "").strip()
        # Kayıtlı müşteri: fatura satırında şirket ünvanı (name); yoksa cari müşteri adı.
        if musteri_id:
            try:
                crow = fetch_one(
                    "SELECT NULLIF(TRIM(COALESCE(name, '')), '') AS nm, NULLIF(TRIM(COALESCE(musteri_adi, '')), '') AS ma FROM customers WHERE id = %s",
                    (int(musteri_id),),
                )
                if crow:
                    nm = (crow.get("nm") or "").strip()
                    ma = (crow.get("ma") or "").strip()
                    if nm:
                        musteri_adi = nm
                    elif ma:
                        musteri_adi = ma
            except (TypeError, ValueError):
                pass
        # Cari ekstre tarafında kayıtlar musteri_id ile toplandığı için,
        # id boş gelirse isimden eşleyip otomatik doldur.
        if not musteri_id and musteri_adi:
            try:
                m = fetch_one(
                    """
                    SELECT id, name
                    FROM customers
                    WHERE LOWER(TRIM(COALESCE(name, ''))) = LOWER(TRIM(%s))
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (musteri_adi,),
                )
                if m and m.get("id"):
                    musteri_id = int(m.get("id"))
                    musteri_adi = (m.get("name") or musteri_adi).strip()
            except Exception:
                pass

        if musteri_id:
            try:
                mid = int(musteri_id)
                donemler = _fatura_kira_donemleri_topla(satirlar, fatura_tarihi, data.get("notlar"))
                if donemler:
                    for yy, mm in sorted(donemler):
                        dup = _musteri_icin_ayda_baska_fatura(mid, yy, mm, exclude_fatura_id=edit_fatura_id)
                        if dup and _fatura_ay_icin_mukerrer_engel_var_mi(dup):
                            fn = dup.get("fatura_no") or ""
                            fid = dup.get("id")
                            return jsonify({
                                "ok": False,
                                "mesaj": (
                                    f"Bu müşteri için {mm:02d}.{yy} döneminde GİB'de imzalanmış fatura var "
                                    f"({fn or ('#' + str(fid))}). "
                                    "Aynı ay için ikinci kesin fatura oluşturulamaz; yalnızca ERP kaydı veya "
                                    "GİB taslağı varsa aynı ay tekrar açılabilir."
                                ),
                            }), 409
            except Exception as ex_dup:
                logging.getLogger(__name__).exception("fatura mükerrer ay kontrolü: %s", ex_dup)

        sevk_adresi_kayit = (data.get("sevk_adresi") or "").strip() or None
        notlar_kayit = _fatura_notlara_erp_taslak_etiketi_uygula(data.get("notlar"), aktif=erp_taslak_kayit)
        if edit_fatura_id:
            execute(
                """
                UPDATE faturalar
                   SET fatura_no = %s,
                       musteri_id = %s,
                       musteri_adi = %s,
                       tutar = %s,
                       kdv_tutar = %s,
                       toplam = %s,
                       durum = %s,
                       fatura_tarihi = %s,
                       vade_tarihi = %s,
                       notlar = %s,
                       sevk_adresi = %s
                 WHERE id = %s
                """,
                (
                    fatura_no,
                    musteri_id,
                    musteri_adi,
                    tutar,
                    kdv_tutar,
                    toplam,
                    data.get("durum") or "odenmedi",
                    fatura_tarihi,
                    vade_tarihi,
                    notlar_kayit,
                    sevk_adresi_kayit,
                    edit_fatura_id,
                ),
            )
            fatura_id = edit_fatura_id
        else:
            execute(
                """
                INSERT INTO faturalar (
                    fatura_no, musteri_id, musteri_adi, tutar, kdv_tutar,
                    toplam, durum, fatura_tarihi, vade_tarihi, notlar, sevk_adresi
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    fatura_no,
                    musteri_id,
                    musteri_adi,
                    tutar,
                    kdv_tutar,
                    toplam,
                    data.get("durum") or "odenmedi",
                    fatura_tarihi,
                    vade_tarihi,
                    notlar_kayit,
                    sevk_adresi_kayit,
                ),
            )
            row = fetch_one("SELECT id FROM faturalar WHERE fatura_no = %s ORDER BY id DESC LIMIT 1", (fatura_no,))
            fatura_id = row.get("id") if row else None
        if fatura_id and satirlar:
            try:
                execute("UPDATE faturalar SET satirlar_json = %s WHERE id = %s", (json.dumps(satirlar), fatura_id))
            except Exception:
                pass
        if fatura_id and irsaliye_modu:
            try:
                execute(
                    "UPDATE faturalar SET notlar = COALESCE(notlar,'') || ' | IRSALIYE_MODU' WHERE id = %s",
                    (fatura_id,),
                )
            except Exception:
                pass
        if fatura_id:
            try:
                gelen_ettn = gelen_ettn_req
                gelen_gib_durum = gelen_gib_durum_req
                gib_asama = "imzali" if gelen_gib_durum == "imzali" else "taslak" if gelen_gib_durum == "taslak" else None
                if gelen_ettn or gib_asama:
                    _fatura_gib_bilgilerini_yaz(fatura_id, gelen_ettn or None, fatura_no, gib_asama=gib_asama)
                if gib_asama == "imzali":
                    row_not = fetch_one("SELECT notlar FROM faturalar WHERE id = %s", (fatura_id,)) or {}
                    execute(
                        "UPDATE faturalar SET notlar = %s WHERE id = %s",
                        (_fatura_notlara_erp_taslak_etiketi_uygula(row_not.get("notlar"), aktif=False), fatura_id),
                    )
            except Exception:
                logging.getLogger(__name__).exception("fatura kaydet GİB bağlama id=%s", fatura_id)

        earsiv_payload = {
            "fatura_no": fatura_no,
            "fatura_tarihi": fatura_tarihi,
            "musteri_id": musteri_id,
            "musteri_adi": musteri_adi,
            "fatura_tipi": data.get("fatura_tipi") or "satis",
            "satirlar": satirlar,
            "ara_toplam": ara_toplam,
            "toplam_iskonto": toplam_iskonto,
            "kdv_toplam": kdv_toplam,
            "genel_toplam": toplam,
            "yazi_ile": tutar_yaziya(toplam),
            "irsaliye_modu": irsaliye_modu,
        }

        mesaj = "Fatura güncellendi!" if edit_fatura_id else "Fatura eklendi!"
        return jsonify({"ok": True, "mesaj": mesaj, "fatura_id": fatura_id, "earsiv": earsiv_payload})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/onizleme/<int:fatura_id>')
@faturalar_gerekli
def fatura_onizleme_ekran(fatura_id):
    """Kaydedilmiş faturanın e-Arşiv PDF önizlemesi ("Fatura Oluştur" sonrası)."""
    ensure_faturalar_amount_columns()
    fatura_row = fetch_one(
        "SELECT id, fatura_no, fatura_tarihi, musteri_id, musteri_adi, tutar, kdv_tutar, toplam, notlar, satirlar_json, sevk_adresi, ettn FROM faturalar WHERE id = %s",
        (fatura_id,),
    )
    if not fatura_row:
        abort(404)
    fatura = dict(fatura_row)
    musteri_id = fatura.get("musteri_id")
    musteri = {"name": fatura.get("musteri_adi"), "address": "", "tax_number": "", "vergi_dairesi": ""}
    if musteri_id:
        cust = fetch_one("SELECT id, name, address, tax_number FROM customers WHERE id = %s", (musteri_id,))
        if cust:
            musteri["name"] = cust.get("name") or musteri["name"]
            musteri["address"] = (cust.get("address") or "").strip()
            musteri["tax_number"] = str(cust.get("tax_number") or "").strip()
        vd_row = fetch_one("SELECT vergi_dairesi FROM customers WHERE id = %s", (musteri_id,))
        if vd_row and vd_row.get("vergi_dairesi"):
            musteri["vergi_dairesi"] = (vd_row.get("vergi_dairesi") or "").strip()
        kyc = fetch_one(
            "SELECT vergi_dairesi, vergi_no, yeni_adres FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
            (musteri_id,),
        )
        if kyc:
            if kyc.get("vergi_dairesi"):
                musteri["vergi_dairesi"] = (kyc.get("vergi_dairesi") or "").strip()
            if kyc.get("vergi_no"):
                musteri["tax_number"] = str(kyc.get("vergi_no") or "").strip()
            if kyc.get("yeni_adres"):
                musteri["address"] = (kyc.get("yeni_adres") or "").strip() or musteri["address"]

    ft = fatura.get("fatura_tarihi")
    if hasattr(ft, "strftime"):
        fatura_tarihi_str = ft.strftime("%d.%m.%Y")
    else:
        s = str(ft or "")[:10]
        if s and len(s) == 10 and s[4] == "-":
            fatura_tarihi_str = f"{s[8:10]}.{s[5:7]}.{s[0:4]}"
        else:
            fatura_tarihi_str = s or "--"
    fatura["fatura_tarihi_str"] = fatura_tarihi_str
    toplam = float(fatura.get("toplam") or 0)

    satirlar = []
    try:
        if fatura.get("satirlar_json"):
            raw = json.loads(fatura["satirlar_json"])
            for s in (raw if isinstance(raw, list) else []):
                miktar = float(s.get("miktar") or 0)
                birim_fiyat = float(s.get("birim_fiyat") or 0)
                isk_oran = float(s.get("iskonto_orani") or 0)
                isk_tutar_giris = s.get("iskonto_tutar")
                kdv_oran = float(s.get("kdv_orani") or 0)
                brut = miktar * birim_fiyat
                if isk_tutar_giris is not None and float(isk_tutar_giris or 0) > 0:
                    isk_tutar = min(float(isk_tutar_giris), brut)
                else:
                    isk_tutar = brut * isk_oran / 100.0
                net = brut - isk_tutar
                kdv_tutar = net * kdv_oran / 100.0
                satirlar.append({
                    "ad": (s.get("ad") or s.get("mal_hizmet") or "Hizmet").strip() or "Hizmet",
                    "miktar": miktar,
                    "birim": (s.get("birim") or "Adet").strip() or "Adet",
                    "birim_fiyat": birim_fiyat,
                    "iskonto_orani": isk_oran,
                    "iskonto_tutar": isk_tutar,
                    "kdv_orani": kdv_oran,
                    "kdv_tutar": kdv_tutar,
                    "mal_tutar": net,
                    "satir_toplam": net + kdv_tutar,
                })
    except Exception:
        pass
    if not satirlar:
        tutar = float(fatura.get("tutar") or 0)
        kdv_tutar = float(fatura.get("kdv_tutar") or 0)
        satirlar = [{
            "ad": "Hizmet",
            "miktar": 1,
            "birim": "Ay",
            "birim_fiyat": tutar,
            "iskonto_orani": 0,
            "iskonto_tutar": 0,
            "kdv_orani": (kdv_tutar / tutar * 100) if tutar else 20,
            "kdv_tutar": kdv_tutar,
            "mal_tutar": tutar,
            "satir_toplam": toplam,
        }]

    ara_toplam = sum(s.get("miktar", 0) * s.get("birim_fiyat", 0) for s in satirlar)
    toplam_iskonto = sum(
        (s.get("iskonto_tutar") if s.get("iskonto_tutar") is not None else (s.get("miktar", 0) * s.get("birim_fiyat", 0) * (s.get("iskonto_orani") or 0) / 100.0))
        for s in satirlar
    )
    kdv_toplam = sum(s.get("kdv_tutar", 0) for s in satirlar)
    ettn = (fatura.get("ettn") or "").strip()
    if (not ettn) and fatura.get("notlar"):
        for part in (fatura.get("notlar") or "").split("|"):
            if "ETTN:" in part or "GİB ETTN:" in part:
                ettn = part.split(":")[-1].strip() or ettn
                break

    irsaliye_modu = False
    if fatura.get("notlar") and "IRSALIYE_MODU" in (fatura.get("notlar") or ""):
        irsaliye_modu = True

    fatura_pdf_dict = {
        "id": fatura.get("id"),
        "fatura_no": fatura.get("fatura_no"),
        "fatura_tarihi": fatura.get("fatura_tarihi"),
        "fatura_tarihi_str": fatura_tarihi_str,
        "fatura_tipi": (fatura.get("fatura_tipi") or "satis"),
        "musteri_adi": fatura.get("musteri_adi"),
        "notlar": fatura.get("notlar"),
        "ara_toplam": ara_toplam,
        "toplam_iskonto": toplam_iskonto,
        "kdv_toplam": kdv_toplam,
        "genel_toplam": toplam,
        "toplam": toplam,
        "ettn": ettn,
        "irsaliye_modu": irsaliye_modu,
        "sevk_adresi": (fatura.get("sevk_adresi") or "").strip() or None,
    }

    pdf_bytes = build_fatura_pdf(fatura_pdf_dict, musteri, satirlar)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=Fatura_{fatura.get('fatura_no') or fatura_id}.pdf",
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@bp.route('/tahsilat-ekle', methods=['POST'])
@faturalar_gerekli
def tahsilat_ekle():
    """Yeni tahsilat ekle; makbuz no üretir, A5 PDF oluşturup müşteri dosyasına kaydeder."""
    try:
        _ensure_tahsil_eden_column()
        data = request.get_json()
        musteri_id = data.get('musteri_id')
        musteri_adi = (data.get('musteri_adi') or '').strip()
        musteri_phone = (data.get('musteri_phone') or '').strip()
        if not musteri_id:
            if not musteri_adi:
                return jsonify({'ok': False, 'mesaj': 'Müşteri seçiniz veya ad giriniz.'}), 400
            # Yeni, hızlı müşteri kaydı oluştur (sadece ad + telefon)
            yeni = execute_returning(
                """
                INSERT INTO customers (name, phone, created_at)
                VALUES (%s, %s, NOW())
                RETURNING id, name
                """,
                (musteri_adi, musteri_phone or None),
            )
            musteri_id = (yeni or {}).get('id')
            if not musteri_id:
                return jsonify({'ok': False, 'mesaj': 'Yeni müşteri oluşturulamadı.'}), 500
        tutar = _parse_amount_flexible(data.get('tutar'))
        cek_detay_raw = data.get('cek_detay')
        cek_list = cek_detay_raw if isinstance(cek_detay_raw, list) else []
        odeme_turu = (data.get('odeme_turu') or 'nakit').strip().lower().replace(" ", "_")
        if odeme_turu == 'cek' and cek_list:
            tutar = sum(_parse_amount_flexible(c.get("tutar")) for c in cek_list)
        if tutar <= 0:
            return jsonify({'ok': False, 'mesaj': 'Tutar 0\'dan büyük olmalıdır veya çek satırları giriniz.'}), 400

        raw_tarih = (data.get('tahsilat_tarihi') or "").strip() or datetime.now().strftime("%Y-%m-%d")
        # DD.MM.YYYY veya YYYY-MM-DD -> YYYY-MM-DD
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", raw_tarih):
            parts = raw_tarih.split(".")
            tahsilat_tarihi = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
        else:
            tahsilat_tarihi = raw_tarih[:10] if len(raw_tarih) >= 10 else datetime.now().strftime("%Y-%m-%d")
        ay_ref_isos = _ay_ref_iso_list_from_tahsilat_payload(data)
        raw_aciklama = (data.get('aciklama') or '').strip()
        auto_pay_items = []
        # Elle tahsilatta kullanıcı ay seçtiyse o seçim korunur.
        # Ay seçilmemişse en eski açık aylardan otomatik dağıtım yapılır.
        if not data.get("fatura_id"):
            try:
                from .giris_routes import _upsert_aylik_grid_cache as _refresh_aylik_cache_before_alloc
                _refresh_aylik_cache_before_alloc(int(musteri_id))
            except Exception:
                pass
        if not ay_ref_isos:
            auto_isos, auto_pay_items = _auto_allocate_oldest_unpaid_months(
                musteri_id,
                tutar,
                data.get("aylik_borc_listesi"),
                data.get("ay_ref_start_iso"),
            )
            if auto_isos:
                ay_ref_isos = auto_isos
        aciklama_text = _aciklama_with_aylik_markers(
            raw_aciklama,
            ay_ref_isos,
        )
        if auto_pay_items:
            aciklama_text = _aciklama_with_aylik_pay_tokens(aciklama_text, auto_pay_items)

        cek_detay_str = json.dumps(cek_list) if cek_list else ""
        havale_banka = (data.get('havale_banka') or "").strip()[:200]

        tahsil_eden = (data.get('tahsil_eden') or '').strip()[:120]
        # Makbuz no + INSERT aynı transaction ve global kilitte (çift 1100 / form yenileme yarışı).
        with db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('tahsilat_makbuz_no_alloc')::bigint)")
            makbuz_no = _tahsilat_icin_makbuz_no_sec_cursor(cur, data.get("makbuz_no"))
            cur.execute(
                """
                INSERT INTO tahsilatlar (
                    musteri_id, customer_id, fatura_id, tutar, odeme_turu,
                    tahsilat_tarihi, aciklama, makbuz_no, cek_detay, havale_banka, tahsil_eden
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, fatura_id, makbuz_no, tutar, odeme_turu, tahsilat_tarihi, aciklama, created_at, cek_detay, havale_banka, tahsil_eden
                """,
                (
                    musteri_id,
                    musteri_id,
                    data.get("fatura_id") or None,
                    tutar,
                    odeme_turu,
                    tahsilat_tarihi,
                    aciklama_text,
                    makbuz_no,
                    cek_detay_str or None,
                    havale_banka or None,
                    tahsil_eden or None,
                ),
            )
            row = cur.fetchone()
        row = dict(row) if row else None
        if not row:
            return jsonify({'ok': False, 'mesaj': 'Kayıt oluşturulamadı.'}), 500

        musteri = fetch_one("SELECT name FROM customers WHERE id = %s", (musteri_id,))
        musteri_adi = (musteri or {}).get("name") or musteri_adi or "Müşteri"
        fatura_no = None
        if row.get("fatura_id"):
            f = fetch_one("SELECT fatura_no FROM faturalar WHERE id = %s", (row["fatura_id"],))
            fatura_no = (f or {}).get("fatura_no")

        banka_hesaplar = fetch_all(
            "SELECT banka_adi, hesap_adi, iban FROM banka_hesaplar WHERE COALESCE(is_active::int, 1) = 1 AND (iban IS NOT NULL AND iban != '') ORDER BY banka_adi"
        )
        pdf_bytes = build_makbuz_pdf(row, musteri_adi, fatura_no, banka_hesaplar=banka_hesaplar)
        os.makedirs(UPLOAD_MUSTERI_DOSYALARI, exist_ok=True)
        safe_name = re.sub(r'[^\w\s-]', '', musteri_adi)[:40].strip() or "musteri"
        safe_name = re.sub(r'[-\s]+', '_', safe_name)
        pdf_filename = f"Tahsilat_{makbuz_no}_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        pdf_path = os.path.join(UPLOAD_MUSTERI_DOSYALARI, pdf_filename)
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        try:
            from .giris_routes import _upsert_aylik_grid_cache as _refresh_aylik_cache
            _refresh_aylik_cache(int(musteri_id))
        except Exception:
            pass

        dagitim_items = []
        if auto_pay_items:
            dagitim_items = [
                (str(iso), round(float(pay or 0), 2))
                for iso, pay in auto_pay_items
                if re.match(r"^\d{4}-\d{2}-\d{2}$", str(iso or ""))
            ]
        elif ay_ref_isos:
            uniq_isos = []
            seen_isos = set()
            for iso in ay_ref_isos:
                iso_s = str(iso or "").strip()
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", iso_s):
                    continue
                if iso_s in seen_isos:
                    continue
                seen_isos.add(iso_s)
                uniq_isos.append(iso_s)
            if len(uniq_isos) == 1:
                dagitim_items = [(uniq_isos[0], round(float(tutar or 0), 2))]
            elif len(uniq_isos) > 1:
                cents_total = int(round(float(tutar or 0) * 100))
                n = len(uniq_isos)
                base = cents_total // n if n else 0
                rem = cents_total % n if n else 0
                dagitim_items = []
                for i, iso_s in enumerate(uniq_isos):
                    pay_cents = base + (1 if i < rem else 0)
                    if pay_cents <= 0:
                        continue
                    dagitim_items.append((iso_s, pay_cents / 100.0))

        return jsonify({
            'ok': True,
            'mesaj': 'Tahsilat eklendi. Makbuz PDF müşteri dosyalarına kaydedildi.',
            'tahsilat_id': row['id'],
            'makbuz_no': makbuz_no,
            'pdf_dosya': pdf_filename,
            'aylik_dagitim': [
                {'iso': iso, 'tutar': round(float(pay or 0), 2)}
                for iso, pay in dagitim_items
            ],
        })
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


@bp.route('/tahsilat-guncelle', methods=['POST'])
@faturalar_gerekli
def tahsilat_guncelle():
    """Mevcut tahsilat: tutar, tarih, ödeme türü, açıklama, tahsil eden güncellenir (rapor / düzeltme)."""
    try:
        _ensure_tahsil_eden_column()
        data = request.get_json(silent=True) or request.form
        tahsilat_id = int(data.get('tahsilat_id') or 0)
        if tahsilat_id <= 0:
            return jsonify({'ok': False, 'mesaj': 'Geçersiz tahsilat kaydı.'}), 400

        odeme_turu = (data.get('odeme_turu') or 'nakit').strip().lower().replace(" ", "_")
        if odeme_turu not in ('nakit', 'havale', 'eft', 'banka', 'kredi_karti', 'cek'):
            return jsonify({'ok': False, 'mesaj': 'Geçersiz ödeme türü.'}), 400

        raw_tarih = (data.get('tahsilat_tarihi') or '').strip()
        if not raw_tarih:
            return jsonify({'ok': False, 'mesaj': 'Tahsilat tarihi zorunludur.'}), 400
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", raw_tarih):
            parts = raw_tarih.split(".")
            tahsilat_tarihi = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
        else:
            tahsilat_tarihi = raw_tarih[:10]
        try:
            datetime.strptime(tahsilat_tarihi, "%Y-%m-%d")
        except ValueError:
            return jsonify({'ok': False, 'mesaj': 'Tarih formatı geçersiz.'}), 400

        existing = fetch_one(
            "SELECT tutar, aciklama, tahsil_eden FROM tahsilatlar WHERE id = %s",
            (tahsilat_id,),
        )
        if not existing:
            return jsonify({'ok': False, 'mesaj': 'Tahsilat kaydı bulunamadı.'}), 404

        if "tutar" in data and str(data.get("tutar") or "").strip() != "":
            tutar = _parse_amount_flexible(data.get("tutar"))
            if tutar <= 0:
                return jsonify({'ok': False, 'mesaj': 'Tutar 0\'dan büyük olmalıdır.'}), 400
        else:
            try:
                tutar = float(existing.get("tutar") or 0)
            except (TypeError, ValueError):
                tutar = 0.0
            if tutar <= 0:
                return jsonify({'ok': False, 'mesaj': 'Geçersiz mevcut tutar; tutar alanını gönderin.'}), 400

        if "aciklama" in data:
            aciklama_text = (data.get("aciklama") or "").strip()
            if len(aciklama_text) > 4000:
                aciklama_text = aciklama_text[:4000]
            aciklama_text = aciklama_text or None
        else:
            aciklama_text = existing.get("aciklama")

        if "tahsil_eden" in data:
            tahsil_eden = (data.get("tahsil_eden") or "").strip()[:120]
            tahsil_eden = tahsil_eden or None
        else:
            tahsil_eden = existing.get("tahsil_eden")

        row = execute_returning(
            """
            UPDATE tahsilatlar
               SET odeme_turu = %s,
                   tahsilat_tarihi = %s,
                   tutar = %s,
                   aciklama = %s,
                   tahsil_eden = %s
             WHERE id = %s
            RETURNING id, COALESCE(customer_id, musteri_id) AS musteri_id
            """,
            (
                odeme_turu,
                tahsilat_tarihi,
                tutar,
                aciklama_text,
                tahsil_eden,
                tahsilat_id,
            ),
        )
        if not row:
            return jsonify({'ok': False, 'mesaj': 'Tahsilat kaydı bulunamadı.'}), 404
        try:
            mid = int((row or {}).get("musteri_id") or 0)
            if mid > 0:
                from .giris_routes import _upsert_aylik_grid_cache as _refresh_aylik_cache
                _refresh_aylik_cache(mid)
        except Exception:
            pass
        return jsonify({'ok': True, 'mesaj': 'Tahsilat güncellendi.'})
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


@bp.route('/tahsilat-hizmet-turu-guncelle', methods=['POST'])
@faturalar_gerekli
def tahsilat_hizmet_turu_guncelle():
    """Tahsilat satırından müşteri hizmet türünü günceller (Cari Kart'a işler)."""
    try:
        data = request.get_json(silent=True) or request.form
        tahsilat_id = int(data.get('tahsilat_id') or 0)
        musteri_id = int(data.get('musteri_id') or 0)
        hizmet_turu = str(data.get('hizmet_turu') or '').strip()

        if tahsilat_id <= 0:
            return jsonify({'ok': False, 'mesaj': 'Geçersiz tahsilat kaydı.'}), 400
        if not hizmet_turu:
            return jsonify({'ok': False, 'mesaj': 'Hizmet türü zorunludur.'}), 400
        if len(hizmet_turu) > 120:
            return jsonify({'ok': False, 'mesaj': 'Hizmet türü çok uzun.'}), 400

        if musteri_id <= 0:
            t_row = fetch_one(
                "SELECT COALESCE(customer_id, musteri_id) AS musteri_id FROM tahsilatlar WHERE id = %s",
                (tahsilat_id,),
            ) or {}
            try:
                musteri_id = int(t_row.get('musteri_id') or 0)
            except (TypeError, ValueError):
                musteri_id = 0
        if musteri_id <= 0:
            return jsonify({'ok': False, 'mesaj': 'Müşteri kaydı bulunamadı.'}), 404

        c_row = execute_returning(
            """
            UPDATE customers
               SET hizmet_turu = %s
             WHERE id = %s
            RETURNING id
            """,
            (hizmet_turu, musteri_id),
        )
        if not c_row:
            return jsonify({'ok': False, 'mesaj': 'Müşteri bulunamadı.'}), 404

        # Cari Kart sözleşme ekranı son KYC kaydını referans alıyor.
        # Varsa son kaydı da güncelleyelim.
        execute(
            """
            UPDATE musteri_kyc
               SET hizmet_turu = %s
             WHERE id = (
                 SELECT id
                 FROM musteri_kyc
                 WHERE musteri_id = %s
                 ORDER BY id DESC
                 LIMIT 1
             )
            """,
            (hizmet_turu, musteri_id),
        )

        return jsonify({'ok': True, 'mesaj': 'Hizmet türü güncellendi.', 'hizmet_turu': hizmet_turu, 'musteri_id': musteri_id})
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


@bp.route('/tahsilat-sil', methods=['POST'])
@faturalar_gerekli
def tahsilat_sil():
    """Tahsilat kaydını siler."""
    try:
        data = request.get_json(silent=True) or request.form
        tahsilat_id = int(data.get('tahsilat_id') or 0)
        if tahsilat_id <= 0:
            return jsonify({'ok': False, 'mesaj': 'Geçersiz tahsilat kaydı.'}), 400
        pre = fetch_one(
            "SELECT COALESCE(customer_id, musteri_id) AS mid FROM tahsilatlar WHERE id = %s",
            (tahsilat_id,),
        ) or {}
        try:
            mid = int(pre.get("mid") or 0)
        except (TypeError, ValueError):
            mid = 0
        row = execute_returning(
            "DELETE FROM tahsilatlar WHERE id = %s RETURNING id",
            (tahsilat_id,),
        )
        if not row:
            return jsonify({'ok': False, 'mesaj': 'Tahsilat kaydı bulunamadı.'}), 404
        if mid > 0:
            try:
                from .giris_routes import _upsert_aylik_grid_cache as _refresh_aylik_cache
                _refresh_aylik_cache(mid)
            except Exception:
                pass
        return jsonify({'ok': True, 'mesaj': 'Tahsilat silindi.'})
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


@bp.route("/api/tahsilat-raporu")
@faturalar_gerekli
def api_tahsilat_raporu():
    """Tahsilat makbuzları: tarih aralığına göre listesi (giriş Tahsilat / Rapor sekmesi). musteri_id ile sadece o cari."""
    _ensure_tahsil_eden_column()
    bugun = date.today()
    bas_raw = (request.args.get("baslangic") or "").strip()
    bit_raw = (request.args.get("bitis") or "").strip()
    try:
        bas = datetime.strptime(bas_raw[:10], "%Y-%m-%d").date() if len(bas_raw) >= 10 else bugun
    except ValueError:
        bas = bugun
    try:
        bit = datetime.strptime(bit_raw[:10], "%Y-%m-%d").date() if len(bit_raw) >= 10 else bugun
    except ValueError:
        bit = bugun
    if bas > bit:
        bas, bit = bit, bas
    mid = _opt_customer_id(request.args.get("musteri_id")) or 0
    # Rapor kuralı:
    # - Marker'lı kayıtta hem marker ay aralığı hem de fiili tahsilat_tarihi aralığı kabul edilir
    #   (elle kaydettiğim bugünkü makbuzlar marker ayı eski olsa da raporda görünmeli).
    # - Marker'sız kayıtta tahsilat_tarihi aralığı esas alınır.
    wh = ["""(
            (
                COALESCE(t.aciklama, '') LIKE '%%|AYLIK_TAH|%%'
                AND (
                    (
                        t.tahsilat_tarihi::date >= %s::date
                        AND t.tahsilat_tarihi::date <= %s::date
                    )
                    OR EXISTS (
                    SELECT 1
                    FROM regexp_matches(
                        COALESCE(t.aciklama, ''),
                        E'\\|AYLIK_TAH\\|([0-9]{4}-[0-9]{2}-[0-9]{2})\\|',
                        'g'
                    ) AS rm(mm)
                    WHERE (mm[1])::date >= %s::date
                      AND (mm[1])::date <= %s::date
                )
                )
            )
            OR
            (
                COALESCE(t.aciklama, '') NOT LIKE '%%|AYLIK_TAH|%%'
                AND t.tahsilat_tarihi::date >= %s::date
                AND t.tahsilat_tarihi::date <= %s::date
            )
        )""",
    ]
    params = [bas, bit, bas, bit, bas, bit]
    if mid > 0:
        wh.insert(0, "(t.musteri_id = %s OR t.customer_id = %s)")
        params = [mid, mid] + params
    rows = fetch_all(
        f"""
        SELECT t.id, t.fatura_id, t.makbuz_no, t.tutar, t.odeme_turu, t.tahsilat_tarihi,
               t.aciklama, t.tahsil_eden,
               COALESCE(t.customer_id, t.musteri_id) AS cari_id,
               COALESCE(NULLIF(TRIM(c.name), ''), '—') AS musteri_adi,
               COALESCE(
                   NULLIF(substring(COALESCE(t.aciklama, '') from '\\|AYLIK_TAH\\|([0-9]{4}-[0-9]{2}-[0-9]{2})\\|'), '')::date,
                   t.tahsilat_tarihi::date
               ) AS rapor_tarihi
        FROM tahsilatlar t
        LEFT JOIN customers c ON COALESCE(t.customer_id, t.musteri_id) = c.id
        WHERE {" AND ".join(wh)}
        ORDER BY rapor_tarihi DESC NULLS LAST, t.id DESC
        """,
        tuple(params),
    )
    # Sözleşme/Aylık Tutarlar gridinde görünmeyen aylar rapora düşmesin:
    # marker'lı kayıtlar yalnızca cache.payload.aylar içindeki yıl-ay anahtarlarıyla eşleşirse gösterilir.
    visible_ym = None  # None => cache yok/okunamadı, düşürme yapma.
    visible_tutar_by_ym = {}
    if mid > 0:
        try:
            cache_row = fetch_one("SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id = %s", (mid,))
            payload_raw = (cache_row or {}).get("payload")
            if payload_raw:
                payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                aylar = payload if isinstance(payload, list) else ((payload or {}).get("aylar") or [])
                if isinstance(aylar, list):
                    s = set()
                    for a in aylar:
                        if not isinstance(a, dict):
                            continue
                        try:
                            yy = int(a.get("yil"))
                            mm = int(a.get("ay"))
                        except (TypeError, ValueError):
                            continue
                        if 1 <= mm <= 12 and 1900 <= yy <= 2200:
                            key = f"{yy:04d}-{mm:02d}"
                            s.add(key)
                            try:
                                tv = float(a.get("tutar_kdv_dahil"))
                                if tv > 0:
                                    visible_tutar_by_ym[key] = round(tv, 2)
                            except (TypeError, ValueError):
                                pass
                    visible_ym = s
        except Exception:
            visible_ym = None

    filtered_rows = []
    for r in (rows or []):
        ac = str((r or {}).get("aciklama") or "")
        marker_isos = re.findall(r"\|AYLIK_TAH\|([0-9]{4}-[0-9]{2})-[0-9]{2}\|", ac)
        pay_tokens = re.findall(r"\|AYLIK_PAY\|([0-9]{4}-[0-9]{2}-[0-9]{2})=([0-9]+(?:\.[0-9]+)?)\|", ac)
        if marker_isos and isinstance(visible_ym, set):
            # Marker'lı kayıt: görünür ayda değilse normalde düşer;
            # ancak fiili tahsilat tarihi seçili aralıktaysa (elle bugünden kayıt) raporda kalsın.
            if not any(ym in visible_ym for ym in marker_isos):
                td = (r or {}).get("tahsilat_tarihi")
                td_ok = False
                if hasattr(td, "year") and hasattr(td, "month") and hasattr(td, "day"):
                    try:
                        td_ok = bas <= date(int(td.year), int(td.month), int(td.day)) <= bit
                    except Exception:
                        td_ok = False
                if not td_ok:
                    continue
            # Rapor tutarı:
            # - AYLİK_PAY varsa pay toplamı (manuel oldest dağıtımda gerçek tutar korunur),
            # - tek marker varsa görünür hücreyle hizalama (net/kdv uyumu),
            # - çoklu marker'da satır toplamı (tutar) korunur.
            if pay_tokens:
                try:
                    pay_toplam = round(
                        sum(float((_t or ("", 0))[1] or 0) for _t in (pay_tokens or [])),
                        2,
                    )
                except Exception:
                    pay_toplam = 0.0
                if pay_toplam > 0:
                    r["tutar"] = pay_toplam
            elif len(marker_isos) == 1:
                try:
                    fatura_id = int((r or {}).get("fatura_id") or 0)
                except (TypeError, ValueError):
                    fatura_id = 0
                if fatura_id <= 0:
                    filtered_rows.append(r)
                    continue
                ym0 = marker_isos[0]
                if ym0 in visible_tutar_by_ym:
                    try:
                        r["tutar"] = visible_tutar_by_ym[ym0]
                    except Exception:
                        pass
        filtered_rows.append(r)

    items = [_row_serializable(r) for r in filtered_rows]
    toplam = sum(float(it.get("tutar") or 0) for it in items)
    ok = jsonify(
        {
            "ok": True,
            "musteri_id": mid or None,
            "baslangic": bas.strftime("%Y-%m-%d"),
            "bitis": bit.strftime("%Y-%m-%d"),
            "items": items,
            "toplam": round(toplam, 2),
            "adet": len(items),
        }
    )
    ok.headers["Cache-Control"] = "no-store, max-age=0"
    return ok


@bp.route("/api/gib-kesilmis-fatura-raporu")
@faturalar_gerekli
def api_gib_kesilmis_fatura_raporu():
    """GİB portalı + ERP: kesinleşmiş e-arşiv satış faturaları; taslaklar hariç. Finans / Rapor sekmesi."""
    try:
        from gib_earsiv import BestOfficeGIBManager

        ensure_faturalar_amount_columns()
        bugun = date.today()
        bas_raw = (request.args.get("baslangic") or "").strip()
        bit_raw = (request.args.get("bitis") or "").strip()
        _truthy = ("1", "true", "evet", "yes", "on")
        _falsy = ("0", "false", "hayir", "hayır", "no", "off")
        raw_sadece_erp = (request.args.get("sadece_erp") or "").strip().lower()
        portal_istegi = str(
            request.args.get("gib_portal") or request.args.get("gib_canli") or ""
        ).strip().lower() in _truthy
        if raw_sadece_erp in _truthy:
            sadece_erp = True
        elif raw_sadece_erp in _falsy or portal_istegi:
            sadece_erp = False
        else:
            # Varsayılan: portal yok — yalnız ERP (GİB’e sürekli istek atmamak için).
            sadece_erp = True
        try:
            bas = datetime.strptime(bas_raw[:10], "%Y-%m-%d").date() if len(bas_raw) >= 10 else bugun
        except ValueError:
            bas = bugun
        try:
            bit = datetime.strptime(bit_raw[:10], "%Y-%m-%d").date() if len(bit_raw) >= 10 else bugun
        except ValueError:
            bit = bugun
        if bas > bit:
            bas, bit = bit, bas
        _nt = sql_expr_fatura_not_gib_taslak("f.notlar")
        _im = sql_expr_fatura_gib_imzalanmis("f.notlar")
        rows = fetch_all(
            f"""
            SELECT f.id,
                   f.fatura_tarihi,
                   f.fatura_no,
                   f.ettn,
                   COALESCE(
                       NULLIF(TRIM(f.musteri_adi), ''),
                       NULLIF(TRIM(c.name), ''),
                       '—'
                   ) AS musteri_adi,
                   COALESCE(f.toplam, f.tutar, 0)::double precision AS tutar
            FROM faturalar f
            LEFT JOIN customers c ON CAST(f.musteri_id AS INTEGER) = c.id
            WHERE (f.fatura_tarihi::date) >= %s
              AND (f.fatura_tarihi::date) <= %s
              AND f.ettn IS NOT NULL
              AND BTRIM(COALESCE(f.ettn::text, '')) <> ''
              AND {_nt}
              AND (
                    {_im}
                    OR UPPER(BTRIM(COALESCE(f.fatura_no::text, ''))) LIKE 'GIB%%'
                  )
            ORDER BY f.fatura_tarihi DESC NULLS LAST, f.id DESC
            """,
            (bas, bit),
        )
        erp_items = [_row_serializable(r) for r in (rows or [])]
        gib_hata = None
        gib_kullanildi = False
        portal_norm = []
        if not sadece_erp:
            try:
                gib = BestOfficeGIBManager()
                if gib.is_available() and getattr(gib, "client_type", "") == "earsivportal":
                    portal_norm = gib.portal_kesilen_fatura_listesi_normalized(bas, bit) or []
                    gib_kullanildi = True
            except Exception as ex_gib:
                gib_hata = str(ex_gib)
                logging.getLogger(__name__).warning("GİB portal kesilmiş fatura listesi: %s", ex_gib)

        if gib_kullanildi:
            items = _merge_gib_kesilmis_portal_ve_erp(portal_norm or [], erp_items)
        else:
            items = _gib_kesilmis_erp_satirlari_yerel_gib_durumu(erp_items)

        gib_erp_disi = sum(1 for it in items if (it or {}).get("kaynak") == "gib_portal")

        def _tut(it):
            try:
                return float((it or {}).get("tutar") or 0)
            except (TypeError, ValueError):
                return 0.0

        def _taslak_satir(it):
            return (it or {}).get("gib_durum") == "Taslak"

        def _iptal_satir(it):
            return (it or {}).get("gib_durum") == "İptal"

        toplam_imzali = sum(_tut(it) for it in items if not _taslak_satir(it) and not _iptal_satir(it))
        toplam_taslak = sum(_tut(it) for it in items if _taslak_satir(it))
        adet_taslak = sum(1 for it in items if _taslak_satir(it))
        payload = {
            "ok": True,
            "baslangic": bas.strftime("%Y-%m-%d"),
            "bitis": bit.strftime("%Y-%m-%d"),
            "items": items,
            "toplam": round(toplam_imzali, 2),
            "toplam_taslak": round(toplam_taslak, 2),
            "adet": len(items),
            "adet_taslak": adet_taslak,
            "gib_portal_kullanildi": gib_kullanildi,
            "gib_portal_esik_adet": gib_erp_disi,
        }
        if gib_hata:
            payload["gib_portal_uyari"] = gib_hata
        return jsonify(payload)
    except Exception as e:
        logging.getLogger(__name__).exception("api_gib_kesilmis_fatura_raporu")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/tahsilat-pdf/<int:tahsilat_id>')
@faturalar_gerekli
def tahsilat_pdf(tahsilat_id):
    """Tahsilat makbuzu A5 PDF'ini oluşturup döndürür (yazdır / indir)."""
    _ensure_tahsil_eden_column()
    row = fetch_one("""
        SELECT t.id, t.makbuz_no, t.tutar, t.odeme_turu, t.tahsilat_tarihi, t.aciklama, t.created_at, t.fatura_id,
               t.tahsil_eden,
               t.cek_detay, t.havale_banka, c.name as musteri_adi
        FROM tahsilatlar t
        LEFT JOIN customers c ON COALESCE(t.customer_id, t.musteri_id) = c.id
        WHERE t.id = %s
    """, (tahsilat_id,))
    if not row:
        return jsonify({'ok': False, 'mesaj': 'Tahsilat bulunamadı.'}), 404
    fatura_no = None
    if row.get("fatura_id"):
        f = fetch_one("SELECT fatura_no FROM faturalar WHERE id = %s", (row["fatura_id"],))
        fatura_no = (f or {}).get("fatura_no")
    banka_hesaplar = fetch_all(
        "SELECT banka_adi, hesap_adi, iban FROM banka_hesaplar WHERE COALESCE(is_active::int, 1) = 1 AND (iban IS NOT NULL AND iban != '') ORDER BY banka_adi"
    )
    pdf_bytes = build_makbuz_pdf(row, row.get("musteri_adi"), fatura_no, banka_hesaplar=banka_hesaplar)
    indir = request.args.get("indir", "").lower() in ("1", "true", "yes")
    disposition = "attachment" if indir else "inline"
    return Response(pdf_bytes, mimetype="application/pdf", headers={
        "Content-Disposition": f"{disposition}; filename=Tahsilat_{row.get('makbuz_no', tahsilat_id)}.pdf",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@bp.route('/tahsilat-makbuz-onizle', methods=['POST'])
@faturalar_gerekli
def tahsilat_makbuz_onizle():
    """Form verileriyle tahsilat makbuzu PDF önizlemesi (kaydetmeden)."""
    try:
        data = request.get_json()
        musteri_id = data.get('musteri_id')
        musteri_adi = (data.get('musteri_adi') or '').strip()
        if not musteri_adi and musteri_id:
            m = fetch_one("SELECT name FROM customers WHERE id = %s", (musteri_id,))
            musteri_adi = (m or {}).get("name") or "Müşteri"
        if not musteri_adi:
            musteri_adi = "Müşteri"
        tutar = _parse_amount_flexible(data.get('tutar'))
        odeme_turu = (data.get('odeme_turu') or 'nakit').strip().lower().replace(" ", "_")
        raw_tarih = (data.get('tahsilat_tarihi') or "").strip() or datetime.now().strftime("%Y-%m-%d")
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", raw_tarih):
            parts = raw_tarih.split(".")
            tahsilat_tarihi = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
        else:
            tahsilat_tarihi = raw_tarih[:10] if len(raw_tarih) >= 10 else datetime.now().strftime("%Y-%m-%d")
        aciklama = _aciklama_with_aylik_markers(
            (data.get('aciklama') or '').strip(),
            _ay_ref_iso_list_from_tahsilat_payload(data),
        )
        tahsil_eden = (data.get('tahsil_eden') or '').strip()[:120]
        cek_detay_raw = data.get('cek_detay')
        cek_list = cek_detay_raw if isinstance(cek_detay_raw, list) else []
        havale_banka = (data.get('havale_banka') or '').strip()[:200]
        fake_row = {
            "makbuz_no": _tahsilat_icin_makbuz_no_sec(data.get("makbuz_no")),
            "tutar": tutar,
            "odeme_turu": odeme_turu,
            "tahsilat_tarihi": tahsilat_tarihi,
            "aciklama": aciklama,
            "tahsil_eden": tahsil_eden,
            "created_at": datetime.now(),
            "cek_detay": cek_list,
            "havale_banka": havale_banka
        }
        banka_hesaplar = fetch_all(
            "SELECT banka_adi, hesap_adi, iban FROM banka_hesaplar WHERE COALESCE(is_active::int, 1) = 1 AND (iban IS NOT NULL AND iban != '') ORDER BY banka_adi"
        )
        pdf_bytes = build_makbuz_pdf(fake_row, musteri_adi, None, banka_hesaplar=banka_hesaplar)
        return Response(pdf_bytes, mimetype="application/pdf", headers={
            "Content-Disposition": "inline; filename=Tahsilat_Onizleme.pdf",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        })
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


@bp.route('/api/next-makbuz-no', methods=['GET'])
@faturalar_gerekli
def api_next_makbuz_no():
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('tahsilat_makbuz_no_alloc')::bigint)")
            no = _next_makbuz_no_with_cursor(cur)
        return jsonify({'ok': True, 'makbuz_no': no})
    except Exception as e:
        return jsonify({'ok': False, 'mesaj': str(e)}), 500


@bp.route("/api/tahsilat-makbuz-sorgula", methods=["GET"])
@faturalar_gerekli
def api_tahsilat_makbuz_sorgula():
    """Müşteriden alınan makbuz no ile kayıt doğrulama (aynı no birden fazlaysa hepsi listelenir)."""
    raw = (request.args.get("makbuz_no") or request.args.get("no") or "").strip()
    no = _normalize_makbuz_no(raw)
    if not no:
        return jsonify({"ok": False, "mesaj": "makbuz_no veya no parametresi gerekli."}), 400
    rows = fetch_all(
        """
        SELECT id, musteri_id, tutar, tahsilat_tarihi, odeme_turu,
               LEFT(COALESCE(aciklama, ''), 200) AS aciklama_ozet
        FROM tahsilatlar
        WHERE TRIM(COALESCE(makbuz_no, '')) = %s
        ORDER BY id DESC
        LIMIT 50
        """,
        (no,),
    )
    return jsonify({"ok": True, "makbuz_no": no, "adet": len(rows), "kayitlar": rows})


# --- GİB e-Arşiv Fatura (taslak + SMS onay) ---
def _extract_gib_fatura_no_from_obj(obj):
    """GİB yanıt/durum sözlüğünden belge/fatura no alanını dayanıklı şekilde çıkar."""
    if not isinstance(obj, dict):
        return ""
    direct_keys = (
        "belgeNumarasi",
        "faturaNo",
        "fatura_no",
        "belge_no",
        "belgeno",
        "belgeNo",
        "invoiceNumber",
    )
    for k in direct_keys:
        v = obj.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    # Anahtar adını normalize edip yeniden dene (büyük/küçük, Türkçe farkları vb.)
    normalized = {}
    for k, v in obj.items():
        nk = re.sub(r"[^a-z0-9]", "", str(k or "").lower())
        normalized[nk] = v
    for nk in ("belgenumarasi", "faturano", "belgeno", "invoicenumber"):
        v = normalized.get(nk)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _extract_gib_ettn_from_obj(obj):
    """GİB yanıt/durum sözlüğünden ETTN/UUID alanını dayanıklı şekilde çıkar."""
    if not isinstance(obj, dict):
        return ""
    for k in ("ettn", "uuid", "faturaUuid", "fatura_uuid"):
        v = obj.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    normalized = {}
    for k, v in obj.items():
        nk = re.sub(r"[^a-z0-9]", "", str(k or "").lower())
        normalized[nk] = v
    for nk in ("ettn", "uuid", "faturauuid"):
        v = normalized.get(nk)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _gib_kesilmis_erp_satirlari_yerel_gib_durumu(erp_items):
    """GİB portal çağrısı olmadan Finans API için ERP satırlarına gib_durum yazar."""
    out = []
    for r in erp_items or []:
        row = dict(r)
        row["gib_durum"] = _fatura_resmi_gib_durumu(row)
        row["kaynak"] = "erp"
        out.append(row)
    return out


def _merge_gib_kesilmis_portal_ve_erp(portal_items, erp_items):
    """
    GİB portal listesi ile ERP faturalar satırlarını tekilleştirir.
    Aynı belge no veya ETTN varsa ERP satırı (id) korunur; yalnızca GİB'de olanlar eklenir.
    """
    portal_items = portal_items or []
    erp_items = erp_items or []
    erp_rows = []
    for e in erp_items:
        row = dict(e)
        row["kaynak"] = "erp"
        row["gib_durum"] = _fatura_resmi_gib_durumu(row)
        erp_rows.append(row)

    key_to_erp = {}
    for e in erp_rows:
        fn = (e.get("fatura_no") or "").strip().upper()
        et = (e.get("ettn") or "").strip().lower()
        keys = []
        if fn:
            keys.append(fn)
        if et:
            keys.append(et)
        if not keys:
            keys.append(f"erp_id_{e.get('id')}")
        for k in keys:
            key_to_erp[k] = e

    result = []
    added_erp_ids = set()
    for p in portal_items:
        p = dict(p)
        pfn = (p.get("fatura_no") or "").strip().upper()
        pet = str(p.get("ettn") or "").strip().lower()
        erp_hit = None
        if pfn and pfn in key_to_erp:
            erp_hit = key_to_erp[pfn]
        if erp_hit is None and pet and pet in key_to_erp:
            erp_hit = key_to_erp[pet]
        if erp_hit is not None:
            eid = erp_hit.get("id")
            if eid not in added_erp_ids:
                erp_hit["gib_durum"] = (
                    (p.get("gib_durum") or "").strip()
                    or _fatura_resmi_gib_durumu(erp_hit)
                )
                p_tarih = (p.get("fatura_tarihi") or "").strip()
                if p_tarih and not (str(erp_hit.get("fatura_tarihi") or "").strip()):
                    erp_hit["fatura_tarihi"] = p_tarih
                try:
                    er_t = float(erp_hit.get("tutar") or 0)
                except (TypeError, ValueError):
                    er_t = 0.0
                try:
                    pt = float(p.get("tutar") or 0)
                except (TypeError, ValueError):
                    pt = 0.0
                if er_t == 0 and pt:
                    erp_hit["tutar"] = pt
                result.append(erp_hit)
                added_erp_ids.add(eid)
        else:
            p["kaynak"] = "gib_portal"
            result.append(p)

    for e in erp_rows:
        eid = e.get("id")
        if eid is not None and eid not in added_erp_ids:
            e["gib_durum"] = _fatura_resmi_gib_durumu(e)
            result.append(e)

    def _sort_tuple(x):
        fd = str(x.get("fatura_tarihi") or "")
        fn = str(x.get("fatura_no") or "")
        return (fd, fn)

    result.sort(key=_sort_tuple, reverse=True)
    return result


def _gib_portal_html_cache_dir_abs() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", GIB_PORTAL_HTML_CACHE_DIR))


def _gib_portal_html_cache_path(fatura_id: int) -> str:
    return os.path.join(_gib_portal_html_cache_dir_abs(), f"{int(fatura_id)}.html")


def _gib_portal_html_cache_oku(fatura_id: int) -> str | None:
    try:
        fid = int(fatura_id)
    except (TypeError, ValueError):
        return None
    p = _gib_portal_html_cache_path(fid)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as rf:
            s = rf.read()
        return s if (s or "").strip() else None
    except OSError:
        return None


def _gib_portal_html_indir_ve_kaydet(fatura_id: int, uuid_ettn: str, gib=None) -> None:
    """İmzadan hemen sonra GİB portal HTML'ini bir kez indirip diske yazar (önizleme GİB'siz açılır)."""
    try:
        fid = int(fatura_id)
    except (TypeError, ValueError):
        return
    u = (uuid_ettn or "").strip()
    if not u:
        return
    try:
        from gib_earsiv import BestOfficeGIBManager
        g = gib if gib is not None else BestOfficeGIBManager()
        if not g.is_available():
            return
        html = g.fatura_html_getir(u, days_back=370)
    except Exception:
        logging.getLogger(__name__).exception("GİB portal HTML indirilemedi (fatura_id=%s)", fid)
        return
    if not (html or "").strip():
        return
    path = _gib_portal_html_cache_path(fid)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as wf:
            wf.write(html)
        os.replace(tmp, path)
    except OSError:
        logging.getLogger(__name__).exception("GİB portal HTML önbelleği yazılamadı (fatura_id=%s)", fid)


def _gib_portal_html_compact_inject(html: str) -> str:
    """GİB HTML'ine ERP kompakt stil (önbellekten okurken de uygulanır)."""
    if not (html or "").strip():
        return html or ""
    compact_patch = """
<style id="boerp-gib-compact">
  body { font-family: Arial, Helvetica, sans-serif !important; }
  td, th, div, span, p { font-size: 12px !important; line-height: 1.18 !important; }
  b, strong { font-size: 13px !important; }
  table { border-collapse: collapse !important; }
</style>
<script id="boerp-gib-compact-script">
(function(){
  function norm(s){
    return (s||'').toLowerCase()
      .replace(/ı/g,'i').replace(/İ/g,'i').replace(/ş/g,'s').replace(/ğ/g,'g')
      .replace(/ü/g,'u').replace(/ö/g,'o').replace(/ç/g,'c')
      .replace(/\\s+/g,' ').trim();
  }
  function hideNoiseRows(){
    var bad = ['no', 'kapi no', '/ turkiye', 'turkiye', 'web sitesi'];
    document.querySelectorAll('tr, div, p, span, td').forEach(function(el){
      try{
        var t = norm(el.textContent || '');
        if (!t) return;
        if (bad.indexOf(t) >= 0 || t.startsWith('kapi no') || t.startsWith('web sitesi') || t === 'no') {
          el.style.display = 'none';
          if (el.parentElement && (el.parentElement.tagName === 'TR' || el.parentElement.tagName === 'TABLE')) {
            el.parentElement.style.display = 'none';
          }
        }
      }catch(e){}
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', hideNoiseRows);
  } else {
    hideNoiseRows();
  }
})();
</script>
"""
    if "</head>" in html:
        return html.replace("</head>", compact_patch + "</head>", 1)
    if "</body>" in html:
        return html.replace("</body>", compact_patch + "</body>", 1)
    return compact_patch + html


def _gib_kayit_bekle(gib, uuid, deneme=10, bekleme_s=1.2):
    """UUID için GİB durum kaydını birkaç kez dener; bulunduğunda döndürür."""
    if not gib or not uuid:
        return {}
    for _ in range(max(1, int(deneme))):
        try:
            st = gib.fatura_durum_getir(uuid, days_back=370) or {}
            if st:
                return st
        except Exception:
            pass
        try:
            import time
            time.sleep(float(bekleme_s))
        except Exception:
            pass
    return {}


def _fatura_gib_bilgilerini_yaz(fatura_id, ettn=None, gib_fatura_no=None, gib_asama=None):
    """GİB ETTN + GİB Fatura No bilgisini faturaya ve notlara tekil şekilde yazar/günceller.

    gib_asama: 'taslak' → «GİB durum: taslak»; 'imzali' → «GİB İMZALANDI»; 'iptal' → «GİB durum: iptal».
    """
    try:
        fid = int(fatura_id)
    except Exception:
        return
    row = fetch_one("SELECT notlar, ettn, fatura_no FROM faturalar WHERE id = %s", (fid,)) or {}
    notlar = str(row.get("notlar") or "")
    ettn_val = (ettn or "").strip() or str(row.get("ettn") or "").strip()
    gib_no_val = (gib_fatura_no or "").strip() or str(row.get("fatura_no") or "").strip()

    # Eski ETTN etiketlerini temizleyip tek bir güncel satır bırak.
    cleaned = re.sub(r"\s*\|\s*G[İI]B\s*ETTN:\s*[^|]*", "", notlar, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*\|\s*G[İI]B\s*FATURA\s*NO:\s*[^|]*", "", cleaned, flags=re.IGNORECASE).strip()
    if gib_asama in ("taslak", "imzali", "iptal"):
        cleaned = re.sub(r"\s*\|\s*G[İI]B\s*durum\s*:\s*[^|]*", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s*\|\s*G[İI]B\s*İMZALANDI\b[^|]*", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^\s*G[İI]B\s*durum\s*:\s*[^\|]+\s*\|?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^\s*G[İI]B\s*İMZALANDI\s*\|?", "", cleaned, flags=re.IGNORECASE).strip()
    if gib_asama == "imzali":
        cleaned = re.sub(r"\s*\|\s*ERP\s*durum\s*:\s*taslak\b[^|]*", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^\s*ERP\s*durum\s*:\s*taslak\s*\|?", "", cleaned, flags=re.IGNORECASE).strip()
    tags = []
    if ettn_val:
        tags.append(f"GİB ETTN: {ettn_val}")
    if gib_no_val:
        tags.append(f"GİB FATURA NO: {gib_no_val}")
    if gib_asama == "taslak":
        tags.append("GİB durum: taslak")
    elif gib_asama == "imzali":
        tags.append("GİB İMZALANDI")
    elif gib_asama == "iptal":
        tags.append("GİB durum: iptal")
    yeni = cleaned
    if tags:
        yeni = f"{cleaned} | {' | '.join(tags)}".strip(" |") if cleaned else " | ".join(tags)

    execute(
        "UPDATE faturalar SET notlar = %s, ettn = %s, fatura_no = %s WHERE id = %s",
        (yeni, ettn_val or None, gib_no_val or row.get("fatura_no"), fid),
    )


@bp.route('/api/gib-taslak', methods=['GET', 'POST'])
@faturalar_gerekli
def api_gib_taslak():
    """Fatura ID ile GİB'de taslak oluşturur. Döner: ok, uuid (ETTN), mesaj."""
    try:
        from gib_earsiv import BestOfficeGIBManager, build_fatura_data_from_db
        def _mask_vkn(v):
            s = str(v or "").strip()
            if len(s) <= 4:
                return s
            return ("*" * (len(s) - 4)) + s[-4:]

        def _payload_debug_text(fd):
            items = fd.get("items") or []
            first = (items[0] if items else {}) or {}
            dbg = {
                "tarih": fd.get("tarih"),
                "saat": fd.get("saat"),
                "vkn_veya_tckn": _mask_vkn(fd.get("vkn")),
                "unvan": fd.get("unvan"),
                "vd": fd.get("vd"),
                "satir_sayisi": len(items),
                "ilk_satir": {
                    "name": first.get("name"),
                    "quantity": first.get("quantity"),
                    "unit_price": first.get("unit_price"),
                    "tax_rate": first.get("tax_rate"),
                    "discount_rate": first.get("discount_rate"),
                    "discount_amount": first.get("discount_amount"),
                },
                "toplam": fd.get("toplam"),
                "kdv_orani_genel": fd.get("kdv_orani"),
            }
            try:
                return json.dumps(dbg, ensure_ascii=False)[:900]
            except Exception:
                return str(dbg)[:900]

        data = request.get_json(silent=True) or {}
        if request.method == "GET":
            # Bazı istemcilerde POST/XHR status=0 (transport) görüldüğü için GET fallback desteği.
            data = {
                "fatura_id": request.args.get("fatura_id"),
                "beklenen_musteri_id": request.args.get("beklenen_musteri_id"),
            }
        fatura_id = data.get("fatura_id") or request.values.get("fatura_id")
        if not fatura_id:
            return jsonify({"ok": False, "mesaj": "fatura_id gerekli."}), 400
        fatura_id = int(fatura_id)
        f_row = fetch_one(
            "SELECT musteri_id, fatura_tarihi, satirlar_json, notlar FROM faturalar WHERE id = %s",
            (fatura_id,),
        )
        if not f_row:
            return jsonify({"ok": False, "mesaj": "Fatura bulunamadı."}), 404
        beklenen_mid = data.get("beklenen_musteri_id") or request.values.get("beklenen_musteri_id")
        if beklenen_mid is not None and str(beklenen_mid).strip() != "":
            try:
                em = int(beklenen_mid)
                fm = f_row.get("musteri_id")
                if fm is not None and int(fm) != em:
                    return jsonify({
                        "ok": False,
                        "mesaj": (
                            f"GİB «Fatura ID» (#{fatura_id}) bu formdaki müşteriye ait değil "
                            f"(kayıt müşteri #{int(fm)}). Önce «Kaydet» ile bu formu kaydedin; "
                            "oluşan fatura numarası/ID ile GİB adımlarına devam edin."
                        ),
                    }), 400
            except (TypeError, ValueError):
                pass
        try:
            sj = f_row.get("satirlar_json")
            satirlar_db = json.loads(sj) if sj else []
        except Exception:
            satirlar_db = []
        ft = f_row.get("fatura_tarihi")
        ft_iso = ft.strftime("%Y-%m-%d") if hasattr(ft, "strftime") else str(ft)[:10]
        donemler_gib = _fatura_kira_donemleri_topla(satirlar_db, ft_iso, f_row.get("notlar") or "")
        mid_gib = f_row.get("musteri_id")
        if donemler_gib and mid_gib:
            try:
                mid_i = int(mid_gib)
                for yy, mm in sorted(donemler_gib):
                    dup = _musteri_icin_ayda_baska_fatura(mid_i, yy, mm, exclude_fatura_id=fatura_id)
                    if dup and _fatura_gib_imzalanmis_sayilir(dup):
                        fn = dup.get("fatura_no") or ""
                        fid = dup.get("id")
                        return jsonify({
                            "ok": False,
                            "mesaj": (
                                f"Bu müşteri için {mm:02d}.{yy} döneminde GİB'de imzalanmış başka fatura var "
                                f"({fn or ('#' + str(fid))}). Aynı ay için ikinci e-Arşiv açılamaz. "
                                "Gerekirse mali müşavir ile iptal sürecini görün."
                            ),
                        }), 409
            except Exception as ex_gib_dup:
                logging.getLogger(__name__).exception("gib-taslak mükerrer kontrol: %s", ex_gib_dup)

        fatura_data = build_fatura_data_from_db(fatura_id, fetch_one)
        payload_debug = _payload_debug_text(fatura_data)
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({
                "ok": False,
                "mesaj": "GİB modülü kullanılamıyor. .env içinde GIB_USER ve GIB_PASS tanımlayın ve eArsivPortal kurulumunu kontrol edin."
            }), 503
        # Bazı GİB çağrıları istemci kütüphanesinde sonsuza yakın bekleyebiliyor.
        # Endpoint'in "Gönderiliyor..."da takılı kalmaması için üst sınır koyuyoruz.
        # Varsayılan 120; alt sınır 90 — GİB yoğun + taslak sonrası işlemler için 65 sn sık yetmiyor (.env 65 ise yükseltilir).
        try:
            taslak_timeout_s = int(str(os.getenv("GIB_TASLAK_TIMEOUT_S") or "120").strip() or "120")
        except ValueError:
            taslak_timeout_s = 120
        taslak_timeout_s = max(90, min(600, taslak_timeout_s))
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(gib.fatura_taslak_olustur, fatura_data)
        try:
            uuid = fut.result(timeout=taslak_timeout_s)
        except concurrent.futures.TimeoutError:
            # Önemli: with ThreadPoolExecutor kullanıldığında __exit__ wait=True ile
            # takılabiliyor ve endpoint fiilen yine dönmüyor. Bu yüzden beklemeden kapat.
            try:
                fut.cancel()
            except Exception:
                pass
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            taslak_raw = getattr(gib, "last_taslak_raw", None)
            gonderilen_payload = getattr(gib, "last_gonderilen_payload", None)
            try:
                if isinstance(taslak_raw, (dict, list)):
                    taslak_raw_text = json.dumps(taslak_raw, ensure_ascii=False)[:700]
                else:
                    taslak_raw_text = str(taslak_raw or "")[:700]
            except Exception:
                taslak_raw_text = str(taslak_raw or "")[:700]
            try:
                if isinstance(gonderilen_payload, dict):
                    gp = gonderilen_payload
                    gmh = (gp.get("malHizmetTable") or [{}])[0]
                    gonderilen_dbg = json.dumps({
                        "malHizmetTable0": {
                            "birimFiyat": gmh.get("birimFiyat"),
                            "fiyat": gmh.get("fiyat"),
                            "malHizmetTutari": gmh.get("malHizmetTutari"),
                            "kdvOrani": gmh.get("kdvOrani"),
                        },
                        "matrah": gp.get("matrah"),
                        "odenecekTutar": gp.get("odenecekTutar"),
                    }, ensure_ascii=False)[:1000]
                else:
                    gonderilen_dbg = ""
            except Exception:
                gonderilen_dbg = ""
            return jsonify({
                "ok": False,
                "mesaj": (
                    f"GİB taslak işlemi sunucuda {taslak_timeout_s} sn içinde tamamlanamadı (iş parçacığı zaman aşımı). "
                    "Bu sürekli «portal yoğun» demek zorunda değil: yanıt gelmeyen HTTP, ağ/VPN/firewall veya "
                    "arka planda takılı istek olabilir. Hâlâ 65 sn yazıyorsa `app.py` bu klasörden yeniden "
                    "başlatılmamış veya önde 65 sn kesen bir proxy olabilir. "
                    "Tekrar deneyin; gerekirse GIB_TASLAK_TIMEOUT_S artırın."
                ),
                "taslak_timeout_sn": taslak_timeout_s,
                "taslak_raw": taslak_raw_text,
                "payload_debug": payload_debug,
                "gonderilen_debug": gonderilen_dbg,
            }), 200
        except Exception as e:
            # Windows'ta bozuk stdout'a traceback.print_exc() bazen OSError(22, 'Invalid argument')
            # fırlatır; gerçek GİB hatası yerine yanıltıcı "GİB hatası" dışarı kaçmasın diye yalnızca logger.
            logging.getLogger(__name__).exception("GİB taslak iş parçacığı hatası (fut.result): %s", e)
            return jsonify({"ok": False, "mesaj": f"GİB bağlantı hatası: {str(e)}"}), 500
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=False)
            except Exception:
                pass
        taslak_raw = getattr(gib, "last_taslak_raw", None)
        gonderilen_payload = getattr(gib, "last_gonderilen_payload", None)
        raw_type = type(taslak_raw).__name__
        try:
            if isinstance(taslak_raw, (dict, list)):
                taslak_raw_text = json.dumps(taslak_raw, ensure_ascii=False)[:700]
            else:
                taslak_raw_text = str(taslak_raw or "")[:700]
        except Exception:
            taslak_raw_text = str(taslak_raw or "")[:700]
        try:
            if isinstance(gonderilen_payload, dict):
                gp = gonderilen_payload
                gmh = (gp.get("malHizmetTable") or [{}])[0]
                gonderilen_dbg = json.dumps({
                    "malHizmetTable0": {
                        "birimFiyat": gmh.get("birimFiyat"),
                        "fiyat": gmh.get("fiyat"),
                        "malHizmetTutari": gmh.get("malHizmetTutari"),
                        "kdvOrani": gmh.get("kdvOrani"),
                        "iskontoOrani": gmh.get("iskontoOrani"),
                        "iskontoTutari": gmh.get("iskontoTutari"),
                        "iskontoNedeni": gmh.get("iskontoNedeni"),
                        "iskontoArttirimOrani": gmh.get("iskontoArttirimOrani"),
                        "iskontoArttirimTutari": gmh.get("iskontoArttirimTutari"),
                        "iskontoArttirimNedeni": gmh.get("iskontoArttirimNedeni"),
                    },
                    "toplamIskonto": gp.get("toplamIskonto"),
                    "matrah": gp.get("matrah"),
                    "odenecekTutar": gp.get("odenecekTutar"),
                }, ensure_ascii=False)[:1000]
            else:
                gonderilen_dbg = ""
        except Exception:
            gonderilen_dbg = ""
        if not uuid:
            return jsonify({
                "ok": False,
                "mesaj": "GİB taslak oluşturulamadı (ETTN dönmedi). Ham yanıtı kontrol edin.",
                "taslak_raw": taslak_raw_text,
                "payload_debug": payload_debug,
                "gonderilen_debug": gonderilen_dbg,
            }), 200
        # Önce taslak gerçekten portal listesinde bulunmalı; aksi halde SMS adımına
        # geçmek kullanıcıyı gereksiz hataya sokuyor.
        taslak_dogrulandi = None
        taslak_onay_durumu = None
        taslak_kayit = None
        if getattr(gib, "client_type", "") == "earsivportal":
            taslak_dogrulandi = False
            st = _gib_kayit_bekle(gib, uuid, deneme=8, bekleme_s=1.5)
            if st:
                taslak_dogrulandi = True
                taslak_kayit = st
                taslak_onay_durumu = (st.get("onayDurumu") or st.get("durum") or "").strip() or None
            if taslak_dogrulandi is False:
                # Pragmatik mod: doğrulama olmasa da akış dursun istemiyoruz.
                # UUID/ETTN kaydı ile SMS adımına izin ver.
                pass
        oid = None
        sms_error = None
        gib_ettn = _extract_gib_ettn_from_obj(taslak_kayit) or uuid
        gib_fatura_no = (
            _extract_gib_fatura_no_from_obj(taslak_kayit)
            or _extract_gib_fatura_no_from_obj(taslak_raw if isinstance(taslak_raw, dict) else {})
        )
        try:
            _fatura_gib_bilgilerini_yaz(fatura_id, gib_ettn, gib_fatura_no, gib_asama="taslak")
        except Exception:
            pass
        # Portal araması çoğu kullanıcıda yalnızca «bugün» seçildiği için taslak «yok» sanılıyor;
        # GİB listesi fatura düzenleme tarihine göre filtrelenir (payload’daki GG/AA/YYYY).
        fatura_tarih_gg_aa_yyyy = str((fatura_data.get("tarih") or "").strip())
        portal_tarih_uyarisi = ""
        if fatura_tarih_gg_aa_yyyy:
            portal_tarih_uyarisi = (
                f" GİB İnteraktif listede ararken başlangıç/bitişe mutlaka {fatura_tarih_gg_aa_yyyy} "
                "(fatura tarihi) dahil edin; yalnızca bugünü seçerseniz satır görünmez. "
                "Onay kolonu boş / Onaylanmadı taslak için normaldir, SMS onayından sonra dolacaktır."
            )
        msg = "Taslak oluşturuldu."
        if getattr(gib, "client_type", "") == "earsivportal":
            if taslak_dogrulandi is False:
                msg = (
                    "GİB taslak isteği gönderildi fakat portal listesinde henüz görünmedi. "
                    "Yine de UUID kaydedildi; SMS adımına devam edebilirsiniz."
                ) + portal_tarih_uyarisi
            else:
                msg = (
                    "GİB taslak isteği gönderildi; portal listesinde bu ETTN ile kayıt bulundu (doğrulandı)."
                ) + portal_tarih_uyarisi
        elif taslak_onay_durumu:
            msg += f" Durum: {taslak_onay_durumu}."
        # Taslak gerçekten portalda görünüyorsa SMS akışına geç.
        try:
            oid = gib.sms_kodu_gonder(uuid)
            sms_error = getattr(gib, "last_sms_error", None)
        except Exception:
            oid = None
            sms_error = getattr(gib, "last_sms_error", None) or "SMS gönderimi tetiklenemedi."
        if oid:
            msg += " SMS gönderildi, gelen kodu girin."
        else:
            msg += " SMS otomatik tetiklenemedi; 'SMS Gönder' ile tekrar deneyin."
            if sms_error:
                msg += f" Detay: {sms_error}"
        debug_meta = (
            f"[debug client={getattr(gib, 'client_type', '-')}; "
            f"raw_type={raw_type}; raw_len={len(taslak_raw_text or '')}; "
            f"verify={taslak_dogrulandi}; onay={taslak_onay_durumu or '-'}]"
        )
        msg = f"{msg} {debug_meta} | payload={payload_debug}"
        if gonderilen_dbg:
            msg += f" | gonderilen={gonderilen_dbg}"
        return jsonify({
            "ok": True,
            "uuid": gib_ettn,
            "gib_fatura_no": gib_fatura_no,
            "oid": oid,
            "sms_sent": bool(oid),
            "sms_error": sms_error,
            "taslak_dogrulandi": taslak_dogrulandi,
            "taslak_onay_durumu": taslak_onay_durumu,
            "taslak_raw": taslak_raw_text,
            "payload_debug": payload_debug,
            "gonderilen_debug": gonderilen_dbg,
            "mesaj": msg,
        })
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 401
    except Exception as e:
        try:
            logging.getLogger(__name__).exception("api_gib_taslak failed")
        except Exception:
            pass
        msg = "GİB hatası: " + str(e)
        if isinstance(e, OSError) and getattr(e, "errno", None) == 22:
            msg += (
                " Bu genelde Windows ortam hatasıdır (geçersiz argüman), GİB’in metin hata kodu değildir. "
                "`python app.py` sürecini güncel kodla yeniden başlatın; devam ederse sunucu logundaki tam stack’e bakın."
            )
        return jsonify({
            "ok": False,
            "mesaj": msg,
            "detay": "Backend exception yakalandı; GİB entegrasyon katmanı yanıtı kontrol edilmeli.",
        }), 200


@bp.route('/api/gib-sms-onay', methods=['POST'])
@faturalar_gerekli
def api_gib_sms_onay():
    """SMS kodu ile taslak faturayı onaylar. İsteğe bağlı fatura_id ile veritabanında güncelleme yapılabilir."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        data = request.get_json() or {}
        uuid = (data.get("uuid") or request.form.get("uuid") or "").strip()
        sms_kodu = (data.get("sms_kodu") or request.form.get("sms_kodu") or "").strip()
        oid = (data.get("oid") or request.form.get("oid") or "").strip()
        fatura_id = data.get("fatura_id") or request.form.get("fatura_id")
        if not uuid or not sms_kodu:
            return jsonify({"ok": False, "mesaj": "uuid ve sms_kodu gerekli."}), 400
        sms_norm = re.sub(r"\s+", "", sms_kodu).upper()
        # GİB kodu: sadece harf, sadece rakam veya harf+rakam karışık olabilir.
        # Uzunluk sağlayıcıya göre değişebildiği için 4-8 aralığı kabul edilir.
        if not re.fullmatch(r"[A-Z0-9]{4,8}", sms_norm):
            return jsonify({"ok": False, "mesaj": "SMS kodu harf/rakam içermeli (4-8 karakter, örn: B2Z7V5)."}), 400
        sms_kodu = sms_norm
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503
        if oid and getattr(gib, "client_type", "") == "earsivportal":
            success = gib.sms_onay_earsivportal(uuid, sms_kodu, oid)
        else:
            success = gib.sms_onay_ve_imzala(uuid, sms_kodu)
        if not success:
            det = getattr(gib, "last_sms_error", None)
            msg = "SMS onayı başarısız veya kod hatalı."
            if det:
                msg += f" Detay: {det}"
            return jsonify({"ok": False, "mesaj": msg}), 400
        if fatura_id:
            fid_i = int(fatura_id)
            ettn_for_cache = uuid
            try:
                st = _gib_kayit_bekle(gib, uuid, deneme=10, bekleme_s=1.2)
                gib_ettn = _extract_gib_ettn_from_obj(st) or uuid
                ettn_for_cache = gib_ettn or uuid
                gib_fatura_no = _extract_gib_fatura_no_from_obj(st)
                _fatura_gib_bilgilerini_yaz(fatura_id, gib_ettn, gib_fatura_no, gib_asama="imzali")
            except Exception:
                pass
            try:
                _gib_portal_html_indir_ve_kaydet(fid_i, ettn_for_cache, gib)
            except Exception:
                logging.getLogger(__name__).exception("GİB portal HTML önbelleği (SMS onay) fid=%s", fid_i)
        return jsonify({"ok": True, "mesaj": "Fatura GİB üzerinde imzalandı."})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/api/gib-sms-gonder', methods=['POST'])
@faturalar_gerekli
def api_gib_sms_gonder():
    """UUID için GİB SMS kodu gönderimini tetikler (eArsivPortal akışı)."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        data = request.get_json() or {}
        uuid = (data.get("uuid") or request.form.get("uuid") or "").strip()
        if not uuid:
            return jsonify({"ok": False, "mesaj": "uuid gerekli."}), 400
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503
        if getattr(gib, "client_type", "") == "earsivportal":
            # Pragmatik mod: taslak doğrulaması başarısız olsa da SMS denenebilir.
            # Burada akışı kesmiyoruz.
            pass
        oid = gib.sms_kodu_gonder(uuid)
        det = (getattr(gib, "last_sms_error", None) or "").strip()
        sms_dbg = getattr(gib, "last_sms_debug", None)
        if not oid:
            msg = "SMS gönderimi başlatılamadı."
            if det:
                msg = msg + " " + det
            payload = {"ok": False, "mesaj": msg, "detay": det or None}
            if sms_dbg:
                payload["dispatch_ham"] = sms_dbg
            return jsonify(payload), 500
        return jsonify({
            "ok": True,
            "oid": oid,
            "mesaj": "SMS gönderildi. Telefonunuza gelen kodu girin.",
            "detay": None,
        })
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/api/gib-jp-onizle', methods=['GET'])
@bp.route('/api/gib-dispatch-onizle', methods=['GET'])
@faturalar_gerekli
def api_gib_dispatch_onizle():
    """
    GİB earsiv-services/dispatch için gönderilecek `jp` gövdesini üretir (FATURA_OLUSTUR çağrılmaz).
    Taslak oluşturmaz; MERNIS + çok satırlı malHizmetTable ile gerçek gönderime eşdeğer sözlük.
    """
    from html import escape

    try:
        from gib_earsiv import BestOfficeGIBManager, build_fatura_data_from_db
    except ImportError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 503
    fatura_id = request.args.get("fatura_id", type=int)
    fmt = (request.args.get("format") or "json").strip().lower()
    if not fatura_id or fatura_id < 1:
        return jsonify({"ok": False, "mesaj": "fatura_id gerekli."}), 400
    try:
        f_data = build_fatura_data_from_db(fatura_id, fetch_one)
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    gib = BestOfficeGIBManager()
    if not gib.is_available():
        return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503
    try:
        jp = gib.gib_dispatch_jp_onizle(f_data)
    except Exception as e:
        logging.getLogger(__name__).exception("api_gib_dispatch_onizle")
        return jsonify({"ok": False, "mesaj": str(e)}), 500
    aciklama = (
        "Bu nesne portalın `jp` alanına `json.dumps` ile gider (cmd=EARSIV_PORTAL_FATURA_OLUSTUR). "
        "`faturaUuid` önizlemede sabit örnek değerdir; gerçek taslakta her denemede `uuid4()` üretilir."
    )
    out = {
        "ok": True,
        "mesaj": aciklama,
        "fatura_id": fatura_id,
        "fatura_olustur_kwarg_ozet": {
            "tarih": f_data.get("tarih"),
            "saat": f_data.get("saat"),
            "vkn": f_data.get("vkn"),
            "ad": f_data.get("ad"),
            "soyad": f_data.get("soyad"),
            "unvan": (str(f_data.get("unvan") or "")[:120] + ("…" if len(str(f_data.get("unvan") or "")) > 120 else "")),
            "satir_sayisi": len(f_data.get("items") or []),
        },
        "jp": jp,
    }
    if fmt == "html":
        body = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>GİB dispatch jp önizleme</title>"
            "<style>body{font-family:Consolas,monospace;background:#0d1117;color:#c9d1d9;padding:16px;}"
            "pre{white-space:pre-wrap;word-break:break-all;background:#161b22;padding:12px;border-radius:8px;border:1px solid #30363d}"
            ".note{color:#8b949e;margin-bottom:12px;max-width:900px}</style></head><body>"
            f"<p class='note'>{escape(aciklama)}</p><pre>{escape(json.dumps(jp, ensure_ascii=False, indent=2))}</pre></body></html>"
        )
        return Response(body, mimetype="text/html; charset=utf-8")
    return jsonify(out)


@bp.route('/api/gib-fatura-onizleme')
@faturalar_gerekli
def api_gib_fatura_onizleme():
    """GİB portal fatura HTML önizlemesi: önce ERP önbelleği (imza sonrası kaydedilir), yoksa bir kez GİB."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        uuid = (request.args.get("uuid") or "").strip()
        fatura_id = request.args.get("fatura_id", type=int)
        if not uuid and fatura_id:
            row = fetch_one("SELECT ettn FROM faturalar WHERE id = %s", (fatura_id,)) or {}
            uuid = str(row.get("ettn") or "").strip()
        if not uuid:
            return jsonify({"ok": False, "mesaj": "uuid veya fatura_id gerekli."}), 400
        fid = fatura_id
        if fid is None or fid < 1:
            r_id = fetch_one(
                "SELECT id FROM faturalar WHERE BTRIM(COALESCE(ettn::text, '')) = BTRIM(%s) ORDER BY id DESC LIMIT 1",
                (uuid,),
            )
            if r_id and r_id.get("id") is not None:
                try:
                    fid = int(r_id.get("id"))
                except (TypeError, ValueError):
                    fid = None
        cached = _gib_portal_html_cache_oku(fid) if fid else None
        if cached:
            return Response(_gib_portal_html_compact_inject(cached), mimetype="text/html; charset=utf-8")
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            if fid:
                return redirect(url_for("faturalar.fatura_onizleme_ekran", fatura_id=fid))
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor; fatura_id ile ERP PDF önizlemesi deneyin."}), 503
        html = gib.fatura_html_getir(uuid, days_back=370)
        if not html.strip():
            return jsonify({"ok": False, "mesaj": "GİB önizleme içeriği boş döndü."}), 404
        if fid:
            try:
                path = _gib_portal_html_cache_path(fid)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as wf:
                    wf.write(html)
                os.replace(tmp, path)
            except OSError:
                logging.getLogger(__name__).exception("GİB HTML önbelleği (canlı çekim) yazılamadı fid=%s", fid)
        return Response(_gib_portal_html_compact_inject(html), mimetype="text/html; charset=utf-8")
    except Exception as e:
        err = str(e or "")
        # Bazı eArsivPortal sürümlerinde fatura_html içinde private login method adı değiştiği için
        # '_eArsivPortal__giris_yap' hatası alınabiliyor. Kullanıcı akışını kırmamak için ERP önizlemeye düş.
        if "_eArsivPortal__giris_yap" in err:
            fatura_id_fb = request.args.get("fatura_id", type=int)
            if fatura_id_fb:
                return redirect(url_for("faturalar.fatura_onizleme_ekran", fatura_id=fatura_id_fb))
        return jsonify({"ok": False, "mesaj": "GİB önizleme hatası: " + err}), 500


@bp.route('/api/gib-fatura-olustur', methods=['POST'])
@faturalar_gerekli
def api_gib_fatura_olustur():
    """SMS onayı sonrası faturayı gerçek ETTN ile kesinleştirir."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        data = request.get_json() or {}
        fatura_id = data.get("fatura_id")
        uuid = (data.get("uuid") or "").strip()
        if not fatura_id:
            return jsonify({"ok": False, "mesaj": "fatura_id gerekli."}), 400
        if not uuid:
            return jsonify({"ok": False, "mesaj": "uuid gerekli."}), 400
        fid = int(fatura_id)
        gib = BestOfficeGIBManager()
        if gib.is_available() and getattr(gib, "client_type", "") == "earsivportal":
            st = gib.fatura_durum_getir(uuid, days_back=370) or {}
            onay = str(st.get("onayDurumu") or "")
            if onay and ("onaylan" not in onay.lower()):
                return jsonify({"ok": False, "mesaj": f"GİB onayı bekleniyor ({onay}). Önce SMS onayını tamamlayın."}), 400
            gib_ettn = _extract_gib_ettn_from_obj(st) or uuid
            gib_fatura_no = _extract_gib_fatura_no_from_obj(st)
            _fatura_gib_bilgilerini_yaz(fid, gib_ettn, gib_fatura_no, gib_asama="imzali")
            _gib_portal_html_indir_ve_kaydet(fid, gib_ettn or uuid, gib)
        else:
            _fatura_gib_bilgilerini_yaz(fid, uuid, None, gib_asama="imzali")
            _gib_portal_html_indir_ve_kaydet(fid, uuid, gib)
        return jsonify({"ok": True, "mesaj": "Fatura gerçek ETTN ile oluşturuldu/kesinleşti.", "fatura_id": fid, "onizleme_url": f"/faturalar/onizleme/{fid}"})
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/api/gib-cek-kaydet', methods=['POST'])
@faturalar_gerekli
def api_gib_cek_kaydet():
    """GİB Fatura No/ETTN ile satırı bul, ERP'ye tek adımda kaydet/güncelle."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        data = request.get_json() or {}
        uuid = str(data.get("uuid") or "").strip()
        fatura_no = str(data.get("fatura_no") or "").strip().upper()
        if not uuid and not fatura_no:
            return jsonify({"ok": False, "mesaj": "ETTN veya Fatura No gerekli."}), 400
        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503
        satir = {}
        if uuid:
            st = gib.fatura_durum_getir(uuid, days_back=370) or {}
            satir = _gib_satir_from_status_dict(st)
        if (not satir) or (fatura_no and (str(satir.get("fatura_no") or "").strip().upper() != fatura_no)):
            items = gib.portal_kesilen_fatura_listesi_normalized(date.today() - timedelta(days=370), date.today()) or []
            for it in items:
                if uuid and str(it.get("ettn") or "").strip().lower() == uuid.lower():
                    satir = {
                        "fatura_no": str(it.get("fatura_no") or "").strip(),
                        "ettn": str(it.get("ettn") or "").strip(),
                        "musteri_adi": str(it.get("musteri_adi") or "").strip(),
                        "fatura_tarihi": str(it.get("fatura_tarihi") or "").strip(),
                        "tutar": float(it.get("tutar") or 0),
                        "gib_durum": str(it.get("gib_durum") or "Taslak"),
                    }
                    break
                if fatura_no and str(it.get("fatura_no") or "").strip().upper() == fatura_no:
                    satir = {
                        "fatura_no": str(it.get("fatura_no") or "").strip(),
                        "ettn": str(it.get("ettn") or "").strip(),
                        "musteri_adi": str(it.get("musteri_adi") or "").strip(),
                        "fatura_tarihi": str(it.get("fatura_tarihi") or "").strip(),
                        "tutar": float(it.get("tutar") or 0),
                        "gib_durum": str(it.get("gib_durum") or "Taslak"),
                    }
                    break
        if not satir:
            return jsonify({"ok": False, "mesaj": "GİB'de ilgili fatura bulunamadı."}), 404
        if fatura_no and not satir.get("fatura_no"):
            satir["fatura_no"] = fatura_no
        if uuid and not satir.get("ettn"):
            satir["ettn"] = uuid
        if float(satir.get("tutar") or 0) <= 0 and str(satir.get("ettn") or "").strip():
            try:
                html = gib.fatura_html_getir(str(satir.get("ettn")).strip(), days_back=370)
                t_html = _gib_html_toplam_parse(html)
                if t_html > 0:
                    satir["tutar"] = t_html
            except Exception:
                pass
        out = _gibden_erp_upsert(satir)
        try:
            fid = int(out.get("fatura_id") or 0)
        except (TypeError, ValueError):
            fid = 0
        if float(satir.get("tutar") or 0) <= 0 and fid > 0 and str(satir.get("ettn") or "").strip():
            try:
                _gib_portal_html_indir_ve_kaydet(fid, str(satir.get("ettn")).strip(), gib)
                cached = _gib_portal_html_cache_oku(fid) or ""
                t_cached = _gib_html_toplam_parse(cached)
                if t_cached > 0:
                    satir["tutar"] = t_cached
                    out = _gibden_erp_upsert(satir)
            except Exception:
                pass
        if out.get("atlandi"):
            return jsonify({
                "ok": False,
                "atlandi": True,
                "mesaj": (
                    "GİB tarafında imzalı olmayan fatura (durum: "
                    + str(satir.get("gib_durum") or "Taslak")
                    + ") ERP'ye yeni kayıt olarak alınmadı."
                ),
                "gib": satir,
            }), 200
        return jsonify({
            "ok": bool(out.get("ok")),
            "mesaj": "GİB faturası ERP'ye aktarıldı." if out.get("ok") else (out.get("mesaj") or "İşlem başarısız."),
            "fatura_id": out.get("fatura_id"),
            "islem": out.get("islem"),
            "gib": satir,
        })
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/gib-durum-tarama", methods=["POST"])
@faturalar_gerekli
def api_gib_durum_tarama():
    """Tarih aralığındaki ETTN'li faturaların GİB durumunu HTML filigranıyla yeniden tespit eder.

    Filigran «İPTAL EDİLMİŞTİR» → İptal, «İMZASIZ» → Taslak, hiçbiri yok ve geçerli HTML → İmzalı.
    Bulunan duruma göre ERP notları (`_fatura_gib_bilgilerini_yaz`) güncellenir; HTML önbelleği yazılır.
    """
    try:
        from gib_earsiv import BestOfficeGIBManager, gib_fatura_html_watermark_etiket

        data = request.get_json(silent=True) or {}
        bas_s = str(data.get("baslangic") or "").strip()
        bit_s = str(data.get("bitis") or "").strip()
        bugun = date.today()
        try:
            bas = datetime.strptime(bas_s[:10], "%Y-%m-%d").date() if len(bas_s) >= 10 else bugun.replace(day=1)
        except ValueError:
            bas = bugun.replace(day=1)
        try:
            bit = datetime.strptime(bit_s[:10], "%Y-%m-%d").date() if len(bit_s) >= 10 else bugun
        except ValueError:
            bit = bugun
        if bas > bit:
            bas, bit = bit, bas
        if (bit - bas).days > 450:
            return jsonify({"ok": False, "mesaj": "Tarih aralığı en fazla 450 gün olabilir."}), 400

        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503

        rows = fetch_all(
            """
            SELECT id, fatura_no, ettn, fatura_tarihi, notlar
            FROM faturalar
            WHERE (fatura_tarihi::date) >= %s
              AND (fatura_tarihi::date) <= %s
              AND BTRIM(COALESCE(ettn::text, '')) <> ''
            ORDER BY fatura_tarihi DESC, id DESC
            """,
            (bas, bit),
        )

        asama_map = {"İptal": "iptal", "İmzasız": "taslak", "İmzalı": "imzali"}
        sonuc = {
            "imzali": 0,
            "taslak": 0,
            "iptal": 0,
            "bilinmiyor": 0,
            "guncellenen": 0,
            "tarama_sayisi": 0,
        }
        ornekler = []
        for r in rows or []:
            sonuc["tarama_sayisi"] += 1
            ettn = (r.get("ettn") or "").strip()
            if not ettn:
                continue
            fid = int(r.get("id") or 0)
            html = ""
            if fid > 0:
                try:
                    html = _gib_portal_html_cache_oku(fid) or ""
                except Exception:
                    html = ""
            if not html or len(html) < 200:
                try:
                    html = gib.fatura_html_getir(ettn, days_back=370) or ""
                except Exception as ex:
                    logging.getLogger(__name__).warning("GİB HTML alınamadı id=%s: %s", fid, ex)
                    sonuc["bilinmiyor"] += 1
                    continue
            wm = gib_fatura_html_watermark_etiket(html)
            if not wm:
                sonuc["bilinmiyor"] += 1
                continue
            yeni_asama = asama_map.get(wm, "")
            if yeni_asama:
                sonuc[yeni_asama] = sonuc.get(yeni_asama, 0) + 1
            # Mevcut not aşaması
            n = r.get("notlar") or ""
            nn = n.replace("İ", "I").replace("ı", "i")
            if "GİB İMZALANDI" in n or re.search(r"G.?B\s+IMZALANDI", nn, flags=re.IGNORECASE):
                eski = "imzali"
            elif re.search(r"G.?B\s+durum\s*:\s*iptal", nn, flags=re.IGNORECASE):
                eski = "iptal"
            elif re.search(r"G.?B\s+durum\s*:\s*taslak", nn, flags=re.IGNORECASE):
                eski = "taslak"
            else:
                eski = ""
            try:
                if fid > 0 and html:
                    _gib_portal_html_indir_ve_kaydet(fid, ettn, gib)
            except Exception:
                pass
            if not yeni_asama or eski == yeni_asama:
                continue
            try:
                _fatura_gib_bilgilerini_yaz(fid, ettn or None, r.get("fatura_no") or None, gib_asama=yeni_asama)
                sonuc["guncellenen"] += 1
                if len(ornekler) < 25:
                    ornekler.append({
                        "fatura_id": fid,
                        "fatura_no": r.get("fatura_no"),
                        "eski": eski or "—",
                        "yeni": yeni_asama,
                    })
            except Exception as ex:
                logging.getLogger(__name__).warning("GİB durum yazılamadı id=%s: %s", fid, ex)

        return jsonify({
            "ok": True,
            "baslangic": bas.isoformat(),
            "bitis": bit.isoformat(),
            "sonuc": sonuc,
            "ornekler": ornekler,
            "mesaj": (
                f"GİB durum taraması: {sonuc['tarama_sayisi']} satır taranıp "
                f"{sonuc['guncellenen']} kayıt güncellendi "
                f"(imzalı: {sonuc['imzali']}, taslak: {sonuc['taslak']}, iptal: {sonuc['iptal']}, "
                f"bilinmeyen: {sonuc['bilinmiyor']})."
            ),
        })
    except Exception as e:
        logging.getLogger(__name__).exception("api_gib_durum_tarama")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/gib-cek-kaydet-aralik", methods=["POST"])
@faturalar_gerekli
def api_gib_cek_kaydet_aralik():
    """Üst filtredeki tarih aralığı için GİB portal listesini bir kez çekip tüm satırları ERP'ye yazar."""
    try:
        from gib_earsiv import BestOfficeGIBManager, portal_kesilen_fatura_listesi_cache_clear

        data = request.get_json(silent=True) or {}
        bas_s = str(data.get("baslangic") or "").strip()
        bit_s = str(data.get("bitis") or "").strip()
        bugun = date.today()
        try:
            bas = datetime.strptime(bas_s[:10], "%Y-%m-%d").date() if len(bas_s) >= 10 else bugun.replace(day=1)
        except ValueError:
            bas = bugun.replace(day=1)
        try:
            bit = datetime.strptime(bit_s[:10], "%Y-%m-%d").date() if len(bit_s) >= 10 else bugun
        except ValueError:
            bit = bugun
        if bas > bit:
            bas, bit = bit, bas
        if (bit - bas).days > 450:
            return jsonify({"ok": False, "mesaj": "Tarih aralığı en fazla 450 gün olabilir."}), 400

        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503

        portal_kesilen_fatura_listesi_cache_clear()
        items = gib.portal_kesilen_fatura_listesi_normalized(bas, bit) or []

        sonuc = {"eklenen": 0, "guncellenen": 0, "atlanan": 0, "hata_sayisi": 0}
        hatalar = []
        for it in items:
            it = dict(it or {})
            satir = {
                "fatura_no": str(it.get("fatura_no") or "").strip(),
                "ettn": str(it.get("ettn") or "").strip(),
                "musteri_adi": str(it.get("musteri_adi") or "").strip(),
                "fatura_tarihi": str(it.get("fatura_tarihi") or "").strip(),
                "tutar": float(it.get("tutar") or 0),
                "gib_durum": str(it.get("gib_durum") or "Taslak"),
            }
            if not satir["fatura_no"] and not satir["ettn"]:
                sonuc["atlanan"] += 1
                continue
            try:
                if satir["tutar"] <= 0 and satir.get("ettn"):
                    html = gib.fatura_html_getir(str(satir["ettn"]).strip(), days_back=370)
                    t_html = _gib_html_toplam_parse(html or "")
                    if t_html > 0:
                        satir["tutar"] = t_html
            except Exception:
                pass
            try:
                out = _gibden_erp_upsert(satir)
                if not out.get("ok"):
                    if out.get("atlandi"):
                        sonuc["atlanan"] += 1
                    else:
                        sonuc["hata_sayisi"] += 1
                        hatalar.append(
                            {"ref": satir.get("fatura_no") or satir.get("ettn"), "mesaj": out.get("mesaj") or "bilinmeyen"}
                        )
                    continue
                fid = int(out.get("fatura_id") or 0)
                if float(satir.get("tutar") or 0) <= 0 and fid > 0 and satir.get("ettn"):
                    try:
                        _gib_portal_html_indir_ve_kaydet(fid, str(satir["ettn"]).strip(), gib)
                        cached = _gib_portal_html_cache_oku(fid) or ""
                        t_cached = _gib_html_toplam_parse(cached)
                        if t_cached > 0:
                            satir["tutar"] = t_cached
                            out = _gibden_erp_upsert(satir)
                    except Exception:
                        pass
                if out.get("islem") == "guncellendi":
                    sonuc["guncellenen"] += 1
                else:
                    sonuc["eklenen"] += 1
            except Exception as ex_row:
                sonuc["hata_sayisi"] += 1
                hatalar.append({"ref": satir.get("fatura_no") or satir.get("ettn"), "mesaj": str(ex_row)})

        mesaj = (
            f"GİB: {len(items)} satır tarandı. ERP eklenen: {sonuc['eklenen']}, güncellenen: {sonuc['guncellenen']}, "
            f"atlanan: {sonuc['atlanan']}, hata: {sonuc['hata_sayisi']}."
        )
        return jsonify(
            {
                "ok": True,
                "mesaj": mesaj,
                "portal_satir": len(items),
                "sonuc": sonuc,
                "hatalar": hatalar[:30],
                "baslangic": bas.isoformat(),
                "bitis": bit.isoformat(),
            }
        )
    except Exception as e:
        logging.getLogger(__name__).exception("api_gib_cek_kaydet_aralik")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/api/gib-sifir-doldur', methods=['POST'])
@faturalar_gerekli
def api_gib_sifir_doldur():
    """Tarih aralığındaki GİB satırlarından toplamı 0 görünenleri ERP'ye doldurur."""
    try:
        from gib_earsiv import BestOfficeGIBManager
        data = request.get_json() or {}
        bas_s = str(data.get("baslangic") or "").strip()
        bit_s = str(data.get("bitis") or "").strip()
        try:
            bas = datetime.strptime(bas_s[:10], "%Y-%m-%d").date() if len(bas_s) >= 10 else (date.today() - timedelta(days=30))
            bit = datetime.strptime(bit_s[:10], "%Y-%m-%d").date() if len(bit_s) >= 10 else date.today()
        except Exception:
            bas, bit = date.today() - timedelta(days=30), date.today()
        if bas > bit:
            bas, bit = bit, bas

        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify({"ok": False, "mesaj": "GİB modülü kullanılamıyor."}), 503
        items = gib.portal_kesilen_fatura_listesi_normalized(bas, bit) or []
        hedef = list(items or [])
        sonuc = {"eklenen": 0, "guncellenen": 0, "atlanan": 0, "detay": []}
        for it in hedef:
            satir = {
                "fatura_no": str(it.get("fatura_no") or "").strip(),
                "ettn": str(it.get("ettn") or "").strip(),
                "musteri_adi": str(it.get("musteri_adi") or "").strip(),
                "fatura_tarihi": str(it.get("fatura_tarihi") or "").strip(),
                "tutar": float(it.get("tutar") or 0),
                "gib_durum": str(it.get("gib_durum") or "Taslak"),
            }
            if satir["tutar"] <= 0 and satir.get("ettn"):
                try:
                    html = gib.fatura_html_getir(str(satir.get("ettn")).strip(), days_back=370)
                    t_html = _gib_html_toplam_parse(html)
                    if t_html > 0:
                        satir["tutar"] = t_html
                except Exception:
                    pass
            if not satir["fatura_no"] and not satir["ettn"]:
                sonuc["atlanan"] += 1
                continue
            out = _gibden_erp_upsert(satir)
            try:
                fid = int(out.get("fatura_id") or 0)
            except (TypeError, ValueError):
                fid = 0
            if satir.get("tutar", 0) <= 0 and fid > 0 and satir.get("ettn"):
                try:
                    _gib_portal_html_indir_ve_kaydet(fid, str(satir.get("ettn")).strip(), gib)
                    cached = _gib_portal_html_cache_oku(fid) or ""
                    t_cached = _gib_html_toplam_parse(cached)
                    if t_cached > 0:
                        satir["tutar"] = t_cached
                        out = _gibden_erp_upsert(satir)
                except Exception:
                    pass
            if out.get("ok"):
                if out.get("islem") == "guncellendi":
                    sonuc["guncellenen"] += 1
                else:
                    sonuc["eklenen"] += 1
            else:
                sonuc["atlanan"] += 1
                sonuc["detay"].append({
                    "fatura_no": satir.get("fatura_no"),
                    "ettn": satir.get("ettn"),
                    "hata": out.get("mesaj") or "bilinmeyen hata",
                })
        return jsonify({
            "ok": True,
            "mesaj": f"İşlem tamamlandı. Eklenen: {sonuc['eklenen']}, Güncellenen: {sonuc['guncellenen']}, Atlanan: {sonuc['atlanan']}",
            "sonuc": sonuc,
            "kayit_sayisi": len(hedef),
        })
    except Exception as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route('/api/gib-son-fatura')
@faturalar_gerekli
def api_gib_son_fatura():
    """GİB manuel gönderim: son fatura. ?musteri_id= verilirse yalnızca o müşterinin son kaydı."""
    mid = request.args.get("musteri_id", type=int)
    _nt = sql_expr_fatura_not_gib_taslak("notlar")
    if mid is not None and mid >= 1:
        row = fetch_one(
            f"""
            SELECT id, fatura_no, musteri_id, musteri_adi, fatura_tarihi, toplam
            FROM faturalar
            WHERE musteri_id = %s
              AND {_nt}
            ORDER BY id DESC
            LIMIT 1
            """,
            (mid,),
        ) or {}
    else:
        row = fetch_one(
            f"""
            SELECT id, fatura_no, musteri_id, musteri_adi, fatura_tarihi, toplam
            FROM faturalar
            WHERE {_nt}
            ORDER BY id DESC
            LIMIT 1
            """
        ) or {}
    if not row or row.get("id") is None:
        # UI akışında "son fatura" sorgusu bazen yalnızca bilgilendirme amaçlı çağrılıyor.
        # 404 dönmek tarayıcı konsolunda gereksiz "Failed to load resource" hatasına yol açıyor.
        mesaj = "Fatura bulunamadı."
        if mid is not None and mid >= 1:
            mesaj = f"Bu müşteri için kayıtlı son fatura bulunamadı (musteri_id={mid})."
        return jsonify({"ok": False, "mesaj": mesaj}), 200
    return jsonify({"ok": True, "fatura": _row_serializable(row)})


@bp.route('/api/auto-fatura/status')
@faturalar_gerekli
def api_auto_fatura_status():
    ensure_auto_invoice_tables()
    settings = _auto_inv_settings()
    last_run = fetch_one("SELECT * FROM auto_invoice_runs ORDER BY id DESC LIMIT 1") or {}
    recent_errors = fetch_all(
        """
        SELECT i.id, i.created_at, i.period_key, i.status, i.error_message,
               i.musteri_id, COALESCE(c.name, '—') AS musteri_adi
        FROM auto_invoice_items i
        LEFT JOIN customers c ON c.id = i.musteri_id
        WHERE i.status IN ('error', 'gib_fail', 'gib_sms_fail')
           OR COALESCE(i.error_message, '') <> ''
        ORDER BY i.id DESC
        LIMIT 20
        """
    ) or []
    return jsonify({
        "ok": True,
        "settings": settings,
        "last_run": _row_serializable(last_run) if last_run else None,
        "recent_errors": [_row_serializable(r) for r in recent_errors],
    })


@bp.route('/api/auto-fatura/settings', methods=['POST'])
@faturalar_gerekli
def api_auto_fatura_settings():
    ensure_auto_invoice_tables()
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    run_day = int(data.get("run_day") or 1)
    run_hour = int(data.get("run_hour") or 9)
    send_gib = bool(data.get("send_gib"))
    auto_sms_code = (data.get("auto_sms_code") or "").strip()
    run_day = min(28, max(1, run_day))
    run_hour = min(23, max(0, run_hour))
    row = fetch_one("SELECT id FROM auto_invoice_settings ORDER BY id LIMIT 1")
    if row:
        execute(
            """UPDATE auto_invoice_settings
               SET enabled=%s, run_day=%s, run_hour=%s, send_gib=%s, auto_sms_code=%s, updated_at=NOW()
               WHERE id=%s""",
            (enabled, run_day, run_hour, send_gib, auto_sms_code or None, row["id"]),
        )
    else:
        execute(
            """INSERT INTO auto_invoice_settings (enabled, run_day, run_hour, send_gib, auto_sms_code)
               VALUES (%s,%s,%s,%s,%s)""",
            (enabled, run_day, run_hour, send_gib, auto_sms_code or None),
        )
    return jsonify({"ok": True, "mesaj": "Otomatik fatura ayarları güncellendi.", "settings": _auto_inv_settings()})


@bp.route('/api/auto-fatura/run', methods=['POST'])
@faturalar_gerekli
def api_auto_fatura_run():
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", True))
    out = run_auto_invoice_cycle(force=force)
    return jsonify(out)


def _parse_auto_year_month(yil_raw, ay_raw):
    try:
        yil = int(yil_raw)
    except Exception:
        yil = date.today().year
    yil = max(2000, min(2100, yil))
    ay_ad = (ay_raw or "").strip()
    ay_no = None
    if ay_ad:
        try:
            ay_no = AYLAR.index(ay_ad) + 1
        except ValueError:
            ay_no = None
    if not ay_no:
        ay_no = date.today().month
    return yil, ay_no


@bp.route('/api/auto-fatura/adaylar')
@faturalar_gerekli
def api_auto_fatura_adaylar():
    yil, ay_no = _parse_auto_year_month(request.args.get("yil"), request.args.get("ay"))
    run_month_date = date(yil, ay_no, 1)
    period_key = run_month_date.strftime("%Y-%m")
    marker_like = f"%|AUTO_INV|{period_key}|%"
    rows = fetch_all(
        f"""
        WITH last_kyc AS (
            SELECT DISTINCT ON (k.musteri_id) k.musteri_id, k.aylik_kira
            FROM musteri_kyc k
            ORDER BY k.musteri_id, k.id DESC
        ),
        last_inv AS (
            SELECT DISTINCT ON (f.musteri_id) f.musteri_id, f.toplam
            FROM faturalar f
            WHERE COALESCE(f.toplam, 0) > 0
              AND {sql_expr_fatura_not_gib_taslak("f.notlar")}
            ORDER BY f.musteri_id, (f.fatura_tarihi::date) DESC, f.id DESC
        )
        SELECT c.id AS musteri_id,
               COALESCE(NULLIF(TRIM(c.name), ''), 'Müşteri') AS musteri_adi,
               ROUND(
                   COALESCE(
                       NULLIF(COALESCE(lk.aylik_kira, 0), 0) * 1.2,
                       NULLIF(COALESCE(li.toplam, 0), 0),
                       NULLIF(COALESCE(c.ilk_kira_bedeli, 0), 0) * 1.2,
                       0
                   )::numeric,
                   2
               ) AS toplam
        FROM customers c
        LEFT JOIN last_kyc lk ON lk.musteri_id = c.id
        LEFT JOIN last_inv li ON (li.musteri_id::text = c.id::text)
        WHERE LOWER(COALESCE(c.durum, 'aktif')) != 'pasif'
          AND NOT EXISTS (
              SELECT 1
              FROM faturalar fx
              WHERE fx.musteri_id::text = c.id::text
                AND COALESCE(fx.notlar, '') LIKE %s
          )
        ORDER BY c.name
        """,
        (marker_like,),
    ) or []
    adaylar = [_row_serializable(r) for r in rows]

    return jsonify({
        "ok": True,
        "period_key": period_key,
        "adaylar": adaylar,
        "count": len(adaylar),
    })


@bp.route('/api/auto-fatura/secili-olustur', methods=['POST'])
@faturalar_gerekli
def api_auto_fatura_secili_olustur():
    data = request.get_json(silent=True) or {}
    yil, ay_no = _parse_auto_year_month(data.get("yil"), data.get("ay"))
    run_month_date = date(yil, ay_no, 1)
    send_gib_raw = str(data.get("send_gib") or "").strip().lower()
    send_gib = send_gib_raw in ("1", "true", "yes", "on", "evet")
    raw_ids = data.get("musteri_ids") or []
    musteri_ids = []
    for v in raw_ids:
        try:
            iv = int(v)
            if iv > 0:
                musteri_ids.append(iv)
        except Exception:
            continue
    musteri_ids = list(dict.fromkeys(musteri_ids))
    if not musteri_ids:
        return jsonify({"ok": False, "mesaj": "En az 1 müşteri seçiniz."}), 400

    created_count = 0
    exists_count = 0
    skip_count = 0
    fail_count = 0
    gib_sent_count = 0
    gib_fail_count = 0
    gib_already_count = 0
    detaylar = []
    gib = None
    build_fatura_data_from_db = None
    if send_gib:
        try:
            from gib_earsiv import BestOfficeGIBManager, build_fatura_data_from_db as _build_fatura_data_from_db
            gib = BestOfficeGIBManager()
            build_fatura_data_from_db = _build_fatura_data_from_db
            if not gib.is_available():
                gib = None
        except Exception:
            gib = None
    for mid in musteri_ids:
        try:
            out = _auto_invoice_create_for_customer(mid, run_month_date)
            st = (out.get("status") or "").lower()
            if st == "created":
                created_count += 1
            elif st == "exists":
                exists_count += 1
            elif st == "skip":
                skip_count += 1
            else:
                fail_count += 1
            det = {
                "musteri_id": mid,
                "status": st or "error",
                "fatura_id": out.get("fatura_id"),
                "fatura_no": out.get("fatura_no"),
                "mesaj": out.get("error") or "",
            }
            fid = int(out.get("fatura_id") or 0)
            if send_gib and fid > 0:
                if not gib or not build_fatura_data_from_db:
                    gib_fail_count += 1
                    det["gib_status"] = "error"
                    det["gib_mesaj"] = "GİB servisi kullanılamıyor."
                else:
                    try:
                        row_f = fetch_one("SELECT ettn FROM faturalar WHERE id = %s", (fid,)) or {}
                        ettn_mevcut = str(row_f.get("ettn") or "").strip()
                        if ettn_mevcut:
                            gib_already_count += 1
                            det["gib_status"] = "already"
                            det["gib_uuid"] = ettn_mevcut
                        else:
                            f_data = build_fatura_data_from_db(fid, fetch_one)
                            gib_uuid = gib.fatura_taslak_olustur(f_data)
                            if gib_uuid:
                                gib_sent_count += 1
                                det["gib_status"] = "sent"
                                det["gib_uuid"] = gib_uuid
                            else:
                                gib_fail_count += 1
                                det["gib_status"] = "error"
                                det["gib_mesaj"] = "GİB taslak gönderilemedi."
                    except Exception as ge:
                        gib_fail_count += 1
                        det["gib_status"] = "error"
                        det["gib_mesaj"] = str(ge)
            detaylar.append(det)
        except Exception as e:
            fail_count += 1
            det = {
                "musteri_id": mid,
                "status": "error",
                "mesaj": str(e),
            }
            if send_gib:
                det["gib_status"] = "skip"
            detaylar.append(det)

    return jsonify({
        "ok": True,
        "period_key": run_month_date.strftime("%Y-%m"),
        "created_count": created_count,
        "exists_count": exists_count,
        "skip_count": skip_count,
        "fail_count": fail_count,
        "send_gib": bool(send_gib),
        "gib_sent_count": gib_sent_count,
        "gib_fail_count": gib_fail_count,
        "gib_already_count": gib_already_count,
        "detaylar": detaylar,
    })


@bp.route('/cron/auto-fatura')
def cron_auto_fatura():
    """Dış cron için açık endpoint (opsiyonel). ?token=CRON_TOKEN doğrulaması yapar."""
    token_cfg = (os.getenv("CRON_TOKEN") or "").strip()
    token_q = (request.args.get("token") or "").strip()
    if token_cfg and token_q != token_cfg:
        return jsonify({"ok": False, "mesaj": "Yetkisiz."}), 401
    out = run_auto_invoice_cycle(force=False)
    return jsonify(out)


@bp.route('/api/musteriler')
@faturalar_gerekli
def api_musteriler_list():
    """Tahsilat formu için müşteri listesi (WhatsApp için phone döner)."""
    arama = (request.args.get('q') or '').strip()
    base = "SELECT id, name, musteri_adi, phone, tax_number FROM customers "
    if not arama:
        rows = fetch_all(base + "ORDER BY name LIMIT 300")
    else:
        w = customers_arama_sql_giris_genis("")
        rows = fetch_all(
            base + f"WHERE {w} ORDER BY name LIMIT 300",
            customers_arama_params_giris_genis(arama),
        )
    return jsonify(
        [
            {
                "id": r["id"],
                "name": r["name"],
                "musteri_adi": r.get("musteri_adi") or "",
                "phone": (r.get("phone") or ""),
            }
            for r in rows
        ]
    )


@bp.route('/api/banka-hesaplar')
@faturalar_gerekli
def api_banka_hesaplar():
    """Tahsilat formu için havale banka seçimi - TL IBAN hesaplar."""
    rows = fetch_all(
        "SELECT id, banka_adi, hesap_adi, iban FROM banka_hesaplar WHERE COALESCE(is_active::int, 1) = 1 ORDER BY banka_adi"
    )
    return jsonify([{
        "id": r["id"],
        "banka_adi": r.get("banka_adi") or "",
        "hesap_adi": r.get("hesap_adi") or "",
        "iban": r.get("iban") or "",
        "label": (r.get("banka_adi") or "") + " - " + (r.get("hesap_adi") or r.get("iban") or "")[:20]
    } for r in rows])