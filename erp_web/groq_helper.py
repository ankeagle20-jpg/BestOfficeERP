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
MODEL = "qwen/qwen3.8-27b"
MAX_TOKENS = 4096

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

USER_PROMPT_RETRY = (
    USER_PROMPT
    + "\n\nEğer görselde birden fazla fiş/belge parçası varsa, SADECE en net/en büyük "
    "olanı işle, diğerlerini tamamen yok say."
)


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


def _log_groq_issue(
    image_path,
    status_code,
    raw_snippet,
    *,
    neden: str,
    deneme: int = 1,
) -> None:
    """Groq hata/yanıt teşhisi — sadece log; davranış değiştirmez."""
    snippet = (raw_snippet or "")[:2000]
    _log.warning(
        "Groq fis_oku sorun: neden=%s status_code=%s image_path=%s deneme=%s raw_body=%s",
        neden,
        status_code,
        image_path,
        deneme,
        snippet,
    )


def _is_json_validate_failed(status_code: int, raw_body: str) -> bool:
    """HTTP 400 gövdesinde Groq json_validate_failed kodu var mı?"""
    if status_code != 400:
        return False
    text = raw_body or ""
    try:
        obj = json.loads(text)
        err = obj.get("error") if isinstance(obj, dict) else None
        if isinstance(err, dict) and err.get("code") == "json_validate_failed":
            return True
    except Exception:
        pass
    return '"code":"json_validate_failed"' in text.replace(" ", "") or (
        "json_validate_failed" in text
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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # deneme 1: normal; deneme 2: yalnızca json_validate_failed sonrası
    attempt_specs = (
        {"deneme": 1, "temperature": 0.1, "user_text": USER_PROMPT},
        {"deneme": 2, "temperature": 0.0, "user_text": USER_PROMPT_RETRY},
    )

    last_raw_body: str | None = None
    last_status: int | None = None

    for attempt_i, spec in enumerate(attempt_specs):
        deneme = int(spec["deneme"])
        payload = {
            "model": use_model,
            "temperature": spec["temperature"],
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": spec["user_text"]},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }

        try:
            resp = requests.post(
                GROQ_URL, headers=headers, json=payload, timeout=timeout
            )
        except requests.Timeout:
            return False, None, "AI servisi zaman aşımına uğradı; tekrar deneyin.", None
        except requests.RequestException as e:
            return False, None, f"AI servisine ulaşılamadı: {e}", None

        raw_body = (resp.text or "")[:8000]
        last_raw_body = raw_body
        last_status = resp.status_code

        if resp.status_code != 200:
            _log_groq_issue(
                str(path),
                resp.status_code,
                raw_body,
                neden=f"http_{resp.status_code}",
                deneme=deneme,
            )
            # 429 / 401 / 404 ve diğer hatalar: retry yok (yalnızca json_validate_failed)
            if resp.status_code == 429:
                return (
                    False,
                    None,
                    "AI servisi yoğun; biraz sonra tekrar deneyin.",
                    raw_body,
                )
            if resp.status_code == 401:
                return (
                    False,
                    None,
                    "Fiş okuma yapılandırması geçersiz (API anahtarı).",
                    raw_body,
                )
            if resp.status_code == 404:
                return (
                    False,
                    None,
                    "Fiş okuma modeli erişilemiyor; yapılandırmayı kontrol edin.",
                    raw_body,
                )
            if (
                attempt_i == 0
                and _is_json_validate_failed(resp.status_code, raw_body)
            ):
                continue  # bir kez daha dene
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
                deneme=deneme,
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
                deneme=deneme,
            )
            return False, None, "AI yanıtında beklenen içerik yok.", raw_body

        raw = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False)
        )
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except (json.JSONDecodeError, TypeError):
            _log_groq_issue(
                str(path),
                resp.status_code,
                raw,
                neden="gecersiz_json",
                deneme=deneme,
            )
            return False, None, "Fiş okunamadı (geçersiz JSON).", raw

        if not isinstance(parsed, dict):
            _log_groq_issue(
                str(path),
                resp.status_code,
                raw,
                neden="beklenmeyen_yanit",
                deneme=deneme,
            )
            return False, None, "Fiş okunamadı (beklenmeyen yanıt).", raw

        return True, parsed, None, raw

    # Teorik: döngü bitti (2. deneme de HTTP hata ile continue etmedi)
    return (
        False,
        None,
        f"AI servisi hata döndü (HTTP {last_status}).",
        last_raw_body,
    )


# ---------------------------------------------------------------------------
# Payafin Cari — kartvizit / vergi levhası / serbest metin OCR (Aşama O0)
# fis_oku'dan bağımsız; masraf akışına dokunulmaz.
# ---------------------------------------------------------------------------

CARI_BELGE_SYSTEM_PROMPT = (
    "Sen bir kartvizit, vergi levhası ve serbest ticari metin OCR asistanısın. "
    "Sadece geçerli JSON döndür. Tahmin etme veya uydurma; okunamayan alanları null bırak. "
    "Fiş/fatura ürün satırı veya tutar çıkarma. "
    "Telefon ve e-postayı belgedeki ham metin olarak ver (aşırı normalizasyon yapma). "
    "VKN genelde 10, TCKN 11 hanedir; uymazsa yine yaz ama guven alanını dusuk yap."
)

CARI_BELGE_USER_PROMPT = """Bu görselden (kartvizit, vergi levhası veya serbest ticari metin)
şu alanları çıkar ve SADECE JSON döndür:
{
  "belge_tipi": "kartvizit | vergi_levhasi | serbest | bilinmiyor",
  "unvan": "string|null",
  "tip_tahmini": "person|company|null",
  "vkn": "string|null",
  "tckn": "string|null",
  "vergi_dairesi": "string|null",
  "adres": "string|null",
  "telefon": "string|null",
  "email": "string|null",
  "ulke": "string|null",
  "not_ham": "string|null",
  "guven": "yuksek|orta|dusuk"
}

Kurallar:
- unvan: vergi levhasında Unvan/Trade name; kartvizitte şirket veya kişi adı.
- tip_tahmini: VKN/şirket ünvani varsa company; şahıs/kişiyse person; belirsizse null.
- Hem VKN hem TCKN görünüyorsa ikisini de doldur; çelişirse guven=dusuk.
- ulke yoksa ve belge TR ise "TR" yazabilirsin; emin değilsen null.
- not_ham: forma uymayan kısa ek not (isteğe bağlı); yoksa null.
"""

CARI_BELGE_USER_PROMPT_RETRY = (
    CARI_BELGE_USER_PROMPT
    + "\n\nGörselde birden fazla belge/parça varsa SADECE en net/en büyük "
    "olanı işle; diğerlerini yok say. Yalnızca geçerli JSON üret."
)

_CARI_BELGE_EXPECTED_KEYS = (
    "belge_tipi",
    "unvan",
    "tip_tahmini",
    "vkn",
    "tckn",
    "vergi_dairesi",
    "adres",
    "telefon",
    "email",
    "ulke",
    "not_ham",
    "guven",
)


def _log_cari_belge_issue(
    image_path,
    status_code,
    raw_snippet,
    *,
    neden: str,
    deneme: int = 1,
) -> None:
    """cari_belge_oku hata teşhisi — fis_oku log yardımcısından ayrı."""
    snippet = (raw_snippet or "")[:2000]
    _log.warning(
        "Groq cari_belge_oku sorun: neden=%s status_code=%s image_path=%s deneme=%s raw_body=%s",
        neden,
        status_code,
        image_path,
        deneme,
        snippet,
    )


def _normalize_cari_belge_result(parsed: dict) -> dict:
    """Beklenen anahtarları garanti et; bilinmeyenleri koru."""
    out: dict = {}
    for k in _CARI_BELGE_EXPECTED_KEYS:
        v = parsed.get(k)
        if isinstance(v, str):
            v = v.strip() or None
        out[k] = v

    tip = out.get("tip_tahmini")
    if tip is not None:
        tip_l = str(tip).strip().lower()
        if tip_l in ("person", "kişi", "kisi", "şahıs", "sahis"):
            out["tip_tahmini"] = "person"
        elif tip_l in ("company", "firma", "şirket", "sirket"):
            out["tip_tahmini"] = "company"
        elif tip_l in ("person", "company"):
            out["tip_tahmini"] = tip_l
        else:
            out["tip_tahmini"] = None

    belge = out.get("belge_tipi")
    if belge is not None:
        b = str(belge).strip().lower().replace(" ", "_")
        allowed = {"kartvizit", "vergi_levhasi", "serbest", "bilinmiyor"}
        if b in ("vergi-levhasi", "vergi_levhası", "vergi levhasi"):
            b = "vergi_levhasi"
        out["belge_tipi"] = b if b in allowed else "bilinmiyor"

    guven = out.get("guven")
    if guven is not None:
        g = str(guven).strip().lower()
        if g not in ("yuksek", "orta", "dusuk"):
            out["guven"] = "orta"
        else:
            out["guven"] = g
    else:
        out["guven"] = "orta"

    return out


def cari_belge_oku(
    image_path,
    *,
    model: str | None = None,
    timeout: int = 120,
    belge_ipucu: str | None = None,
) -> tuple[bool, dict | None, str | None, str | None]:
    """Kartvizit / vergi levhası / serbest metin görselini Groq ile okur.

    fis_oku'dan bağımsız (ayrı prompt + retry). Masraf akışına yazmaz.

    Returns:
        (ok, result, error, raw) — result normalize edilmiş cari belge alanları.
    """
    api_key = _api_key()
    if not api_key:
        return False, None, "Cari belge okuma yapılandırması eksik (GROQ_API_KEY).", None

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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    tip_hint = (belge_ipucu or "").strip().lower()
    hint_line = ""
    if tip_hint in ("kartvizit", "vergi_levhasi", "serbest"):
        hint_line = f"\n\nBelge tipi ipucu (kullanıcı): {tip_hint}."

    user_text_1 = CARI_BELGE_USER_PROMPT + hint_line
    user_text_2 = CARI_BELGE_USER_PROMPT_RETRY + hint_line

    attempt_specs = (
        {"deneme": 1, "temperature": 0.1, "user_text": user_text_1},
        {"deneme": 2, "temperature": 0.0, "user_text": user_text_2},
    )

    last_raw_body: str | None = None
    last_status: int | None = None

    for attempt_i, spec in enumerate(attempt_specs):
        deneme = int(spec["deneme"])
        payload = {
            "model": use_model,
            "temperature": spec["temperature"],
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": CARI_BELGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": spec["user_text"]},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }

        try:
            resp = requests.post(
                GROQ_URL, headers=headers, json=payload, timeout=timeout
            )
        except requests.Timeout:
            return False, None, "AI servisi zaman aşımına uğradı; tekrar deneyin.", None
        except requests.RequestException as e:
            return False, None, f"AI servisine ulaşılamadı: {e}", None

        raw_body = (resp.text or "")[:8000]
        last_raw_body = raw_body
        last_status = resp.status_code

        if resp.status_code != 200:
            _log_cari_belge_issue(
                str(path),
                resp.status_code,
                raw_body,
                neden=f"http_{resp.status_code}",
                deneme=deneme,
            )
            if resp.status_code == 429:
                return (
                    False,
                    None,
                    "AI servisi yoğun; biraz sonra tekrar deneyin.",
                    raw_body,
                )
            if resp.status_code == 401:
                return (
                    False,
                    None,
                    "Cari belge okuma yapılandırması geçersiz (API anahtarı).",
                    raw_body,
                )
            if resp.status_code == 404:
                return (
                    False,
                    None,
                    "Cari belge okuma modeli erişilemiyor; yapılandırmayı kontrol edin.",
                    raw_body,
                )
            if attempt_i == 0 and _is_json_validate_failed(
                resp.status_code, raw_body
            ):
                continue
            return (
                False,
                None,
                f"AI servisi hata döndü (HTTP {resp.status_code}).",
                raw_body,
            )

        try:
            body = resp.json()
        except Exception:
            _log_cari_belge_issue(
                str(path),
                resp.status_code,
                raw_body,
                neden="json_parse",
                deneme=deneme,
            )
            return False, None, "AI yanıtı okunamadı.", raw_body

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            _log_cari_belge_issue(
                str(path),
                resp.status_code,
                raw_body,
                neden="beklenen_icerik_yok",
                deneme=deneme,
            )
            return False, None, "AI yanıtında beklenen içerik yok.", raw_body

        raw = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False)
        )
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except (json.JSONDecodeError, TypeError):
            _log_cari_belge_issue(
                str(path),
                resp.status_code,
                raw,
                neden="gecersiz_json",
                deneme=deneme,
            )
            return False, None, "Cari belge okunamadı (geçersiz JSON).", raw

        if not isinstance(parsed, dict):
            _log_cari_belge_issue(
                str(path),
                resp.status_code,
                raw,
                neden="beklenmeyen_yanit",
                deneme=deneme,
            )
            return False, None, "Cari belge okunamadı (beklenmeyen yanıt).", raw

        return True, _normalize_cari_belge_result(parsed), None, raw

    return (
        False,
        None,
        f"AI servisi hata döndü (HTTP {last_status}).",
        last_raw_body,
    )


# ---------------------------------------------------------------------------
# Payafin Cari — sipariş / teklif kalem OCR (Aşama S1)
# cari_belge_oku ve fis_oku'dan bağımsız; masraf / Ana ERP'ye dokunulmaz.
# ---------------------------------------------------------------------------

SIPARIS_BELGE_SYSTEM_PROMPT = (
    "Sen bir sipariş formu, teklif veya fatura görseli OCR asistanısın. "
    "Sadece geçerli JSON döndür. Uydurma satır yazma; okunamayan alanları null bırak. "
    "birim_fiyat her zaman KDV hariç birim fiyattır. "
    "Kartvizit / vergi kimliği çıkarma; odak ürün/hizmet satırları ve tutarlardır."
)

SIPARIS_BELGE_USER_PROMPT = """Bu görselden (sipariş, teklif veya fatura) kalemleri çıkar
ve SADECE JSON döndür:
{
  "belge_tipi": "siparis | teklif | fatura | diger",
  "belge_no": "string|null",
  "tarih": "YYYY-MM-DD|null",
  "para_birimi": "TRY",
  "satirlar": [
    {
      "aciklama": "string",
      "miktar": 1.0,
      "birim_fiyat": 100.0,
      "kdv_orani": 20
    }
  ],
  "toplam_kdv_haric_tahmini": 100.0,
  "toplam_kdv_dahil_tahmini": 120.0,
  "guven": "yuksek|orta|dusuk",
  "not_ham": "string|null"
}

Kurallar:
- satirlar: belgede görünen her ürün/hizmet satırı; yoksa [].
- birim_fiyat: KDV hariç; miktar > 0; kdv_orani 0–100 tamsayı (belirsizse 20).
- toplam_*: belgedeki genel toplamlar; yoksa satırlardan hesaplayabildiğin tahmin; yoksa null.
- para_birimi yoksa veya TR ise "TRY".
- Emin değilsen guven=dusuk; uydurma satır ekleme.
"""

SIPARIS_BELGE_USER_PROMPT_RETRY = (
    SIPARIS_BELGE_USER_PROMPT
    + "\n\nGörselde birden fazla tablo/parça varsa SADECE en net kalem "
    "tablosunu işle. Yalnızca geçerli JSON üret."
)

_SIPARIS_BELGE_EXPECTED_KEYS = (
    "belge_tipi",
    "belge_no",
    "tarih",
    "para_birimi",
    "satirlar",
    "toplam_kdv_haric_tahmini",
    "toplam_kdv_dahil_tahmini",
    "guven",
    "not_ham",
)


def _log_siparis_belge_issue(
    image_path,
    status_code,
    raw_snippet,
    *,
    neden: str,
    deneme: int = 1,
) -> None:
    """siparis_belge_oku hata teşhisi — fis_oku / cari_belge_oku loglarından ayrı."""
    snippet = (raw_snippet or "")[:2000]
    _log.warning(
        "Groq siparis_belge_oku sorun: neden=%s status_code=%s image_path=%s deneme=%s raw_body=%s",
        neden,
        status_code,
        image_path,
        deneme,
        snippet,
    )


def _siparis_dec(v):
    """Sayısal alan → float|None."""
    if v is None or v == "":
        return None
    try:
        from decimal import Decimal, InvalidOperation

        d = Decimal(str(v).replace(",", ".").strip())
        return float(d)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _normalize_siparis_satir(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    aciklama = raw.get("aciklama")
    if isinstance(aciklama, str):
        aciklama = aciklama.strip() or None
    else:
        aciklama = str(aciklama).strip() if aciklama is not None else None
    if not aciklama:
        return None
    miktar = _siparis_dec(raw.get("miktar"))
    if miktar is None or miktar <= 0:
        miktar = 1.0
    birim = _siparis_dec(raw.get("birim_fiyat"))
    if birim is None or birim < 0:
        return None
    try:
        kdv = int(float(str(raw.get("kdv_orani") if raw.get("kdv_orani") is not None else 20)))
    except (TypeError, ValueError):
        kdv = 20
    if kdv < 0 or kdv > 100:
        kdv = 20
    return {
        "aciklama": aciklama[:500],
        "miktar": miktar,
        "birim_fiyat": birim,
        "kdv_orani": kdv,
    }


def _normalize_siparis_belge_result(parsed: dict) -> dict:
    """Beklenen anahtarları garanti et; satirlar listesini temizle."""
    out: dict = {}
    for k in _SIPARIS_BELGE_EXPECTED_KEYS:
        if k == "satirlar":
            continue
        v = parsed.get(k)
        if isinstance(v, str):
            v = v.strip() or None
        out[k] = v

    belge = out.get("belge_tipi")
    if belge is not None:
        b = str(belge).strip().lower().replace(" ", "_")
        allowed = {"siparis", "teklif", "fatura", "diger"}
        aliases = {
            "sipariş": "siparis",
            "order": "siparis",
            "quote": "teklif",
            "invoice": "fatura",
            "other": "diger",
            "bilinmiyor": "diger",
        }
        b = aliases.get(b, b)
        out["belge_tipi"] = b if b in allowed else "diger"
    else:
        out["belge_tipi"] = "diger"

    tarih = out.get("tarih")
    if tarih:
        t = str(tarih).strip()[:10]
        # GG.AA.YYYY → YYYY-MM-DD kaba dönüşüm
        if len(t) == 10 and t[2] == "." and t[5] == ".":
            t = f"{t[6:10]}-{t[3:5]}-{t[0:2]}"
        out["tarih"] = t if len(t) == 10 and t[4] == "-" else None
    else:
        out["tarih"] = None

    para = (out.get("para_birimi") or "TRY")
    para = str(para).strip().upper()[:3] or "TRY"
    out["para_birimi"] = para if para.isalpha() else "TRY"

    raw_lines = parsed.get("satirlar")
    satirlar: list = []
    if isinstance(raw_lines, list):
        for item in raw_lines:
            norm = _normalize_siparis_satir(item)
            if norm:
                satirlar.append(norm)
    out["satirlar"] = satirlar

    out["toplam_kdv_haric_tahmini"] = _siparis_dec(out.get("toplam_kdv_haric_tahmini"))
    out["toplam_kdv_dahil_tahmini"] = _siparis_dec(out.get("toplam_kdv_dahil_tahmini"))

    # Toplam yoksa satırlardan KDV hariç/dahil tahmin
    if out["toplam_kdv_haric_tahmini"] is None and satirlar:
        haric = 0.0
        dahil = 0.0
        for s in satirlar:
            net = float(s["miktar"]) * float(s["birim_fiyat"])
            haric += net
            dahil += net * (1.0 + float(s["kdv_orani"]) / 100.0)
        out["toplam_kdv_haric_tahmini"] = round(haric, 2)
        if out["toplam_kdv_dahil_tahmini"] is None:
            out["toplam_kdv_dahil_tahmini"] = round(dahil, 2)

    guven = out.get("guven")
    if guven is not None:
        g = str(guven).strip().lower()
        out["guven"] = g if g in ("yuksek", "orta", "dusuk") else "orta"
    else:
        out["guven"] = "dusuk" if not satirlar else "orta"

    if out.get("not_ham") is not None:
        out["not_ham"] = str(out["not_ham"]).strip()[:2000] or None

    if out.get("belge_no") is not None:
        out["belge_no"] = str(out["belge_no"]).strip()[:128] or None

    return out


def siparis_belge_oku(
    image_path,
    *,
    model: str | None = None,
    timeout: int = 120,
    belge_ipucu: str | None = None,
) -> tuple[bool, dict | None, str | None, str | None]:
    """Sipariş / teklif / fatura görselinden kalem satırlarını Groq ile okur.

    fis_oku ve cari_belge_oku'dan bağımsız (ayrı prompt + retry).
    Masraf / Ana ERP akışına yazmaz.

    Returns:
        (ok, result, error, raw) — result normalize edilmiş sipariş kalem alanları.
    """
    api_key = _api_key()
    if not api_key:
        return (
            False,
            None,
            "Sipariş belge okuma yapılandırması eksik (GROQ_API_KEY).",
            None,
        )

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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    tip_hint = (belge_ipucu or "").strip().lower()
    hint_line = ""
    if tip_hint in ("siparis", "teklif", "fatura", "serbest"):
        hint_line = f"\n\nBelge tipi ipucu (kullanıcı): {tip_hint}."

    user_text_1 = SIPARIS_BELGE_USER_PROMPT + hint_line
    user_text_2 = SIPARIS_BELGE_USER_PROMPT_RETRY + hint_line

    attempt_specs = (
        {"deneme": 1, "temperature": 0.1, "user_text": user_text_1},
        {"deneme": 2, "temperature": 0.0, "user_text": user_text_2},
    )

    last_raw_body: str | None = None
    last_status: int | None = None

    for attempt_i, spec in enumerate(attempt_specs):
        deneme = int(spec["deneme"])
        payload = {
            "model": use_model,
            "temperature": spec["temperature"],
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SIPARIS_BELGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": spec["user_text"]},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }

        try:
            resp = requests.post(
                GROQ_URL, headers=headers, json=payload, timeout=timeout
            )
        except requests.Timeout:
            return False, None, "AI servisi zaman aşımına uğradı; tekrar deneyin.", None
        except requests.RequestException as e:
            return False, None, f"AI servisine ulaşılamadı: {e}", None

        raw_body = (resp.text or "")[:8000]
        last_raw_body = raw_body
        last_status = resp.status_code

        if resp.status_code != 200:
            _log_siparis_belge_issue(
                str(path),
                resp.status_code,
                raw_body,
                neden=f"http_{resp.status_code}",
                deneme=deneme,
            )
            if resp.status_code == 429:
                return (
                    False,
                    None,
                    "AI servisi yoğun; biraz sonra tekrar deneyin.",
                    raw_body,
                )
            if resp.status_code == 401:
                return (
                    False,
                    None,
                    "Sipariş belge okuma yapılandırması geçersiz (API anahtarı).",
                    raw_body,
                )
            if resp.status_code == 404:
                return (
                    False,
                    None,
                    "Sipariş belge okuma modeli erişilemiyor; yapılandırmayı kontrol edin.",
                    raw_body,
                )
            if attempt_i == 0 and _is_json_validate_failed(
                resp.status_code, raw_body
            ):
                continue
            return (
                False,
                None,
                f"AI servisi hata döndü (HTTP {resp.status_code}).",
                raw_body,
            )

        try:
            body = resp.json()
        except Exception:
            _log_siparis_belge_issue(
                str(path),
                resp.status_code,
                raw_body,
                neden="json_parse",
                deneme=deneme,
            )
            return False, None, "AI yanıtı okunamadı.", raw_body

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            _log_siparis_belge_issue(
                str(path),
                resp.status_code,
                raw_body,
                neden="beklenen_icerik_yok",
                deneme=deneme,
            )
            return False, None, "AI yanıtında beklenen içerik yok.", raw_body

        raw = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False)
        )
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except (json.JSONDecodeError, TypeError):
            _log_siparis_belge_issue(
                str(path),
                resp.status_code,
                raw,
                neden="gecersiz_json",
                deneme=deneme,
            )
            return False, None, "Sipariş belgesi okunamadı (geçersiz JSON).", raw

        if not isinstance(parsed, dict):
            _log_siparis_belge_issue(
                str(path),
                resp.status_code,
                raw,
                neden="beklenmeyen_yanit",
                deneme=deneme,
            )
            return False, None, "Sipariş belgesi okunamadı (beklenmeyen yanıt).", raw

        return True, _normalize_siparis_belge_result(parsed), None, raw

    return (
        False,
        None,
        f"AI servisi hata döndü (HTTP {last_status}).",
        last_raw_body,
    )
