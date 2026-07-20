# -*- coding: utf-8 -*-
"""
Groq AI yardımcı modülü — fiş/fatura görsel OCR.
gemini_helper.py ile aynı .env deseni; anahtar: GROQ_API_KEY (erp_web/.env).
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
from pathlib import Path

_log = logging.getLogger(__name__)

_web_dir = Path(__file__).resolve().parent
_root_dir = _web_dir.parent

try:
    from dotenv import load_dotenv

    for d in (_web_dir, _root_dir):
        for name in (".env", "env"):
            p = d / name
            if p.exists():
                load_dotenv(p, override=False)
                break
    # erp_web/.env ana depo — override ile güçlendir
    env_web = _web_dir / ".env"
    if env_web.exists():
        load_dotenv(env_web, override=True)
except ImportError:
    pass

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "qwen/qwen3.6-27b"

SYSTEM_PROMPT = (
    "Sen bir fiş/fatura OCR asistanısın. Sadece geçerli JSON döndür. "
    "Tahmin etme; okunamayan alanları null bırak. "
    "Para tutarlarını noktalı ondalık (örn. 1234.56) olarak ver. "
    "tarih alanı YYYY-MM-DD formatında olsun."
)

USER_PROMPT = """Bu fiş görselinden şu alanları çıkar ve SADECE JSON döndür:
{
  "magaza_adi": "string",
  "fis_no": "string",
  "tarih": "YYYY-MM-DD",
  "toplam_tutar": 0.0,
  "kdv_orani": 0.0,
  "kdv_tutari": 0.0,
  "urunler": [
    {"ad": "string", "adet": 0.0, "birim_fiyat": 0.0, "tutar": 0.0}
  ],
  "kategori_tahmini": "string"
}

kategori_tahmini için kaba sınıflar kullan: market, yakıt, kırtasiye, yemek, diğer.
Okunamayan sayısal alanlar için null kullan.
"""


def _temiz_anahtar(s: str) -> str:
    if not s or not isinstance(s, str):
        return ""
    s = s.replace("\r", "").replace("\n", " ").strip().strip("\"'").strip()
    if s.startswith("\ufeff"):
        s = s[1:].strip()
    return s


def _api_key() -> str:
    return _temiz_anahtar(os.getenv("GROQ_API_KEY") or "")


def _mime_for(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("image/"):
        return mime
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")


def _load_image_data_url(path: Path) -> str:
    raw = path.read_bytes()
    if not raw:
        raise ValueError(f"Görsel boş: {path}")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{_mime_for(path)};base64,{b64}"


def _log_groq_issue(image_path, status_code, raw_snippet, *, neden: str) -> None:
    """Groq hata/yanıt teşhisi — sadece log; davranış değiştirmez."""
    snippet = (raw_snippet or "")[:2000]
    _log.warning(
        "Groq fis_oku sorun: neden=%s status_code=%s image_path=%s raw_body=%s",
        neden,
        status_code,
        image_path,
        snippet,
    )


def fis_oku(
    image_path,
    *,
    model: str | None = None,
    timeout: int = 120,
) -> tuple[bool, dict | None, str | None, str | None]:
    """
    Fiş görselini Groq ile okur.

    Returns:
        (ok, result, error, raw)
        - ok=True: result dict (parse edilmiş alanlar), raw=ham model metni
        - ok=False: result=None, error=kullanıcı/mesaj metni, raw=varsa ham yanıt
    """
    api_key = _api_key()
    if not api_key:
        return False, None, "Fiş okuma yapılandırması eksik (GROQ_API_KEY).", None

    path = Path(image_path).expanduser()
    if not path.is_file():
        return False, None, f"Görsel dosyası bulunamadı: {path}", None

    try:
        data_url = _load_image_data_url(path)
    except Exception as e:
        return False, None, f"Görsel okunamadı: {e}", None

    try:
        import requests
    except ImportError:
        return False, None, "requests kütüphanesi yüklü değil.", None

    use_model = (model or MODEL).strip() or MODEL
    payload = {
        "model": use_model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=timeout)
    except requests.Timeout:
        return False, None, "AI servisi zaman aşımına uğradı; tekrar deneyin.", None
    except requests.RequestException as e:
        return False, None, f"AI servisine ulaşılamadı: {e}", None

    raw_body = (resp.text or "")[:8000]
    if resp.status_code != 200:
        _log_groq_issue(
            str(path),
            resp.status_code,
            raw_body,
            neden=f"http_{resp.status_code}",
        )
    if resp.status_code == 429:
        return False, None, "AI servisi yoğun; biraz sonra tekrar deneyin.", raw_body
    if resp.status_code == 401:
        return False, None, "Fiş okuma yapılandırması geçersiz (API anahtarı).", raw_body
    if resp.status_code == 404:
        return (
            False,
            None,
            "Fiş okuma modeli erişilemiyor; yapılandırmayı kontrol edin.",
            raw_body,
        )
    if resp.status_code != 200:
        return (
            False,
            None,
            f"AI servisi hata döndü (HTTP {resp.status_code}).",
            raw_body,
        )

    try:
        body = resp.json()
    except Exception:
        _log_groq_issue(
            str(path),
            resp.status_code,
            raw_body,
            neden="json_parse",
        )
        return False, None, "AI yanıtı okunamadı.", raw_body

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        _log_groq_issue(
            str(path),
            resp.status_code,
            raw_body,
            neden="beklenen_icerik_yok",
        )
        return False, None, "AI yanıtında beklenen içerik yok.", raw_body

    raw = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except (json.JSONDecodeError, TypeError):
        _log_groq_issue(
            str(path),
            resp.status_code,
            raw,
            neden="gecersiz_json",
        )
        return False, None, "Fiş okunamadı (geçersiz JSON).", raw

    if not isinstance(parsed, dict):
        _log_groq_issue(
            str(path),
            resp.status_code,
            raw,
            neden="beklenmeyen_yanit",
        )
        return False, None, "Fiş okunamadı (beklenmeyen yanıt).", raw

    return True, parsed, None, raw
