import time

_SIMPLE_CACHE = {}

CACHE_KEY_HIZMET_TURLERI = "giris:hizmet_turleri:get:v1"
CACHE_KEY_DUZENLI_FATURA = "giris:duzenli_fatura_secenekleri:get:v1"
CACHE_KEY_TUFE_VERILERI = "giris:tufe_verileri:get:v1"
CACHE_TTL_SEC = 300.0


def simple_cache_get(key, max_age_sec=CACHE_TTL_SEC):
    entry = _SIMPLE_CACHE.get(key)
    if not entry:
        return None
    ts, val = entry
    if (time.time() - ts) > max_age_sec:
        return None
    return val


def simple_cache_set(key, val):
    _SIMPLE_CACHE[key] = (time.time(), val)


def simple_cache_invalidate(key):
    _SIMPLE_CACHE.pop(key, None)


def simple_cache_invalidate_prefix(prefix: str):
    """Belirtilen önek ile başlayan tüm cache anahtarlarını siler."""
    keys_to_del = [k for k in _SIMPLE_CACHE if str(k).startswith(prefix)]
    for k in keys_to_del:
        _SIMPLE_CACHE.pop(k, None)

