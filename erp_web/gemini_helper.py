"""
Gemini AI yardımcı modülü.
Müşteri verisi özeti ile analiz/sohbet için tek nokta.
SDK'lar başarısız olursa doğrudan REST API denenir.
"""
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

# .env'den API key: önce dotenv ile tüm .env yükle, sonra açık path'lerden oku
_web_dir = Path(__file__).resolve().parent
_root_dir = _web_dir.parent

try:
    from dotenv import load_dotenv
    # erp_web/.env ve proje kökü .env
    for d in (_web_dir, _root_dir):
        for name in (".env", "env"):
            p = d / name
            if p.exists():
                load_dotenv(p, override=False)
                break
except ImportError:
    pass

def _temiz_anahtar(s):
    """Baş/son boşluk, tırnak, BOM ve gizli karakterleri temizler."""
    if not s or not isinstance(s, str):
        return ""
    s = s.replace("\r", "").replace("\n", " ").strip().strip("\"'").strip()
    if s.startswith("\ufeff"):
        s = s[1:].strip()
    return s

def _read_key_from_file(path):
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("GEMINI_API_KEY="):
                return _temiz_anahtar(line.split("=", 1)[1])
    except Exception:
        pass
    return ""

GEMINI_API_KEY = _temiz_anahtar(os.environ.get("GEMINI_API_KEY") or "")
if not GEMINI_API_KEY:
    for d in (_web_dir, _root_dir):
        for name in (".env", "env"):
            key = _read_key_from_file(d / name)
            if key:
                GEMINI_API_KEY = _temiz_anahtar(key)
                break
        if GEMINI_API_KEY:
            break

GEMINI_AVAILABLE = bool(GEMINI_API_KEY)

# Yeni SDK (google-genai) model listesi
GEMINI_MODELS = ("gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash")
# Eski SDK (google-generativeai) fallback modeli
GEMINI_LEGACY_MODELS = ("gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro")


def _analiz_yeni_sdk(api_key: str, prompt: str):
    """Yeni google.genai SDK ile dene. Başarı: (True, metin), Anahtar hatası: (False, 'key_error'), Diğer: exception fırlat."""
    from google import genai
    client = genai.Client(api_key=api_key)
    for model in GEMINI_MODELS:
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            text = (response.text or "").strip()
            if text:
                return True, text
        except Exception as e:
            err = str(e)
            if "403" in err or "API_KEY" in err or "401" in err or "INVALID_ARGUMENT" in err:
                return False, "key_error"
            if "404" in err or "NOT_FOUND" in err:
                continue
            raise
    return False, "key_error"


def _metin_eski_sdk_result(result):
    """Eski SDK yanıtından metni al (farklı sürümlerde .text veya candidates)."""
    text = getattr(result, "text", None)
    if text and str(text).strip():
        return str(text).strip()
    try:
        for c in getattr(result, "candidates", []) or []:
            content = getattr(c, "content", None) or (c if isinstance(c, dict) else {})
            parts = content.get("parts", []) if isinstance(content, dict) else getattr(content, "parts", [])
            for p in parts or []:
                t = p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "")
                if t and str(t).strip():
                    return str(t).strip()
    except Exception:
        pass
    return ""

def _analiz_eski_sdk(api_key: str, prompt: str):
    """Eski google.generativeai SDK ile dene (AI Studio anahtarları çoğu zaman bununla çalışır)."""
    import google.generativeai as genai_legacy
    genai_legacy.configure(api_key=api_key)
    last_err = ""
    for model in GEMINI_LEGACY_MODELS:
        try:
            m = genai_legacy.GenerativeModel(model)
            result = m.generate_content(prompt)
            text = _metin_eski_sdk_result(result)
            if text:
                return True, text
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err or "quota" in err.lower():
                return False, "quota_exceeded"
            last_err = f"{type(e).__name__}: {err[:220]}"
            continue
    return False, last_err or ""


def _analiz_rest_api(api_key: str, prompt: str):
    """Doğrudan Gemini REST API çağrısı (SDK bağımlılığı yok)."""
    last_err = ""
    key_style_err = False
    # Tek model ile başla (kota tüketimini azalt); 404 alırsan sıradakini dene
    for model in ("gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash", "gemini-2.5-flash"):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            data = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                j = json.loads(resp.read().decode())
            candidates = j.get("candidates") or []
            if candidates:
                parts = (candidates[0].get("content") or {}).get("parts") or []
                if parts and parts[0].get("text"):
                    return True, (parts[0]["text"] or "").strip()
        except urllib.error.HTTPError as e:
            body = (e.read() or b"").decode("utf-8", errors="ignore")
            msg = ""
            try:
                payload = json.loads(body) if body else {}
                msg = (
                    (payload.get("error") or {}).get("message")
                    or payload.get("message")
                    or body
                )
            except Exception:
                msg = body
            msg = (msg or "").strip()
            last_err = f"REST HTTP {e.code}: {msg[:260]}" if msg else f"REST HTTP {e.code}"
            if e.code == 429 or "quota" in body.lower() or "RESOURCE_EXHAUSTED" in body:
                return False, "quota_exceeded"
            if e.code in (401, 403) or "API_KEY" in body or "api key" in body.lower() or "invalid" in body.lower():
                key_style_err = True
            continue
        except Exception as e:
            last_err = f"REST {type(e).__name__}: {str(e)[:260]}"
            continue
    if key_style_err:
        return False, "key_error|" + (last_err or "403/401")
    return False, last_err or ""


def analiz_yap(context_metni: str, kullanici_sorusu: str = ""):
    """
    Verilen bağlam ve isteğe bağlı kullanıcı sorusu ile Gemini'den yanıt alır.
    Önce yeni SDK, 403/anahtar hatası alırsa eski SDK (google-generativeai) denenir.
    """
    if not GEMINI_API_KEY:
        return False, "Gemini API anahtarı tanımlı değil. .env dosyasına GEMINI_API_KEY=... ekleyin."

    api_key = _temiz_anahtar(GEMINI_API_KEY)
    if not api_key:
        return False, "Gemini API anahtarı tanımlı değil. .env dosyasına GEMINI_API_KEY=... ekleyin."

    prompt = (
        "Aşağıda bir ofis kiralama / müşteri takip sisteminden alınmış veri özeti var.\n\n"
        "--- VERİ ÖZETİ ---\n"
        f"{context_metni}\n\n"
        "--- GÖREV ---\n"
    )
    if kullanici_sorusu and kullanici_sorusu.strip():
        prompt += f"Kullanıcının sorusu: {kullanici_sorusu.strip()}\n"
    else:
        prompt += "Bu verilere göre kısa bir özet ve 2-3 maddelik analiz/öneri yaz. Türkçe, net ve kısa olsun.\n"
    prompt += "\nYanıtını yalnızca Türkçe ver, kısa ve net tut."

    # 1) Önce eski SDK (google-generativeai) — AI Studio anahtarları çoğunlukla bununla çalışır
    try:
        import google.generativeai
        ok, out = _analiz_eski_sdk(api_key, prompt)
        if ok and out:
            return True, out
        if out == "quota_exceeded":
            return False, "Kota aşıldı. 1–2 dakika bekleyip tekrar deneyin."
    except ImportError:
        pass

    # 2) Yeni SDK (google-genai)
    try:
        from google import genai  # noqa: F401
        try:
            ok, out = _analiz_yeni_sdk(api_key, prompt)
            if ok:
                return True, out
            if out == "key_error":
                pass  # 3. adımda REST dene
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                return False, "Kota aşıldı. Birkaç dakika sonra tekrar deneyin."
    except ImportError:
        pass

    # 3) Doğrudan REST API (ek paket gerekmez)
    ok, out = _analiz_rest_api(api_key, prompt)
    if ok:
        return True, out
    if out == "quota_exceeded":
        return False, "Kota aşıldı. Ücretsiz planda dakikada sınırlı istek var; 1–2 dakika bekleyip tekrar deneyin."
    if out == "key_error" or (isinstance(out, str) and out.startswith("key_error|")):
        detay = ""
        if isinstance(out, str) and "|" in out:
            detay = out.split("|", 1)[1].strip()
        if detay and "leaked" in detay.lower():
            msg = (
                "Bu API anahtarı Google tarafından «sızdırılmış» olarak işaretlenmiş ve kapatılmış. "
                "Yeni anahtar oluşturmanız gerekiyor: https://aistudio.google.com/apikey — "
                "«Create API key» ile yeni key alın, .env dosyasında GEMINI_API_KEY=... satırını bu yeni key ile değiştirip uygulamayı yeniden başlatın."
            )
        else:
            msg = (
                "API anahtarı reddedildi veya bu modeller için yetkiniz yok. "
                "Yeni anahtar: https://aistudio.google.com/apikey — "
                ".env içinde GEMINI_API_KEY=... yazıp uygulamayı yeniden başlatın."
            )
        if detay and "leaked" not in detay.lower():
            msg += f"\n\nDetay: {detay}"
        return False, msg

    return False, (
        "Gemini yanıt veremedi. Detay: "
        f"{out or 'bilinmeyen hata'}\n\n"
        "Not: Bu hata bölge/plan (billing), API kapalı veya ağ engeli kaynaklı olabilir. "
        "AI Studio plan ekranını ve API key'i kontrol edin."
    )
