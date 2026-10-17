# -*- coding: utf-8 -*-
"""Lint: giris/index.html typeof-korumalı isimler tanımsız kalmasın (allowlist hariç).

Davranış testi değil: yeni typeof X === 'function' / !== 'undefined' eklendiğinde
X ya dosyada/static scriptte tanımlı olmalı ya da allowlist'e gerekçeyle yazılmalı.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IDX = ROOT / "templates" / "giris" / "index.html"
STATIC = ROOT / "static"

# BİLİNÇLİ tanımsız (veya bu şablonda tanımsız kalan) typeof hedefleri.
# Anahtar = isim; değer = tek satır gerekçe (test mesajında görünür).
ALLOWLIST: dict[str, str] = {
    "girisTahsilatYilAyPanelKalanRenkStil": (
        "Bilinçli tanımsız: gövde yalnız _t.js; 2c0a01f sonrası index'te yok; "
        "eklemek kozmetik, hayalet/bayat ile hizalı değilse risk."
    ),
    "girisTahsilatYilAyPanelSatirAcikBorcMu": (
        "Bilinçli tanımsız: gövde yalnız _t.js; typeof no-op; ileride bilinçli port ayrı commit."
    ),
    "girisTahsilatYilAyPanelBrutGoster": (
        "Bilinçli tanımsız: 2c0a01f ile index'ten kaldırıldı; reel/aylik_tutar fallback yeterli."
    ),
    "girisTahsilatYilAyPanelHucreSatirBul": (
        "Bilinçli tanımsız: Set→ödendi zorlaması hayalet yeşille çelişebilir (6fbd8ab); "
        "fb5aac5 typeof no-op bırakıldı."
    ),
    "sozlesmeTahsilSetFromGridCache": (
        "Bilinçli tanımsız: 2c0a01f kaldırdı; poll+Zorla yeşil ile çelişir (f2c02e5/6fbd8ab)."
    ),
    "doneCb": (
        "Yanlış alarm değil: yerel callback parametresi; global tanım beklenmez."
    ),
    "toast": (
        "İsteğe bağlı UI helper; giris sayfasında tanımlı değil, typeof ile sessiz no-op."
    ),
    "formatNumberTR": (
        "faturalar_tab içinde tanımlı; giris inline yolunda typeof + sayı fallback."
    ),
}

# Tarayıcı / Web API globals (tanım şablonda aranmaz).
BROWSER_GLOBALS: frozenset[str] = frozenset(
    {
        "AbortController",
        "Array",
        "BigInt",
        "Blob",
        "Boolean",
        "CSS",
        "ClipboardItem",
        "CustomEvent",
        "DOMException",
        "DataTransfer",
        "Date",
        "Element",
        "Error",
        "Event",
        "File",
        "FileList",
        "FileReader",
        "FormData",
        "HTMLElement",
        "HTMLInputElement",
        "HTMLSelectElement",
        "Image",
        "Intl",
        "JSON",
        "KeyboardEvent",
        "Map",
        "Math",
        "MouseEvent",
        "MutationObserver",
        "Node",
        "Number",
        "Object",
        "Option",
        "Promise",
        "RegExp",
        "ResizeObserver",
        "Set",
        "String",
        "Symbol",
        "TextDecoder",
        "TextEncoder",
        "TypeError",
        "URL",
        "URLSearchParams",
        "WebSocket",
        "Worker",
        "XMLHttpRequest",
        "alert",
        "atob",
        "btoa",
        "clearInterval",
        "clearTimeout",
        "confirm",
        "console",
        "crypto",
        "decodeURIComponent",
        "document",
        "encodeURIComponent",
        "fetch",
        "history",
        "indexedDB",
        "isFinite",
        "isNaN",
        "localStorage",
        "location",
        "navigator",
        "parseFloat",
        "parseInt",
        "performance",
        "prompt",
        "queueMicrotask",
        "requestAnimationFrame",
        "sessionStorage",
        "setInterval",
        "setTimeout",
        "structuredClone",
        "window",
    }
)

TYPEOF_RE = re.compile(
    r"typeof\s+([A-Za-z_$][\w$]*)\s*(===|!==)\s*['\"](?:function|undefined)['\"]"
)

DEF_PATTERNS = [
    re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("),
    re.compile(r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"\bwindow\[['\"]([A-Za-z_$][\w$]*)['\"]\]\s*="),
    re.compile(r"\b([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b"),
    re.compile(r"\b([A-Za-z_$][\w$]*)\s*=\s*\([^)]*\)\s*=>"),
    re.compile(r"\b([A-Za-z_$][\w$]*)\s*=\s*[A-Za-z_$][\w$]*\s*=>"),
]


def _defined_names(text: str) -> set[str]:
    out: set[str] = set()
    for pat in DEF_PATTERNS:
        out.update(pat.findall(text))
    return out


def _typeof_names(text: str) -> set[str]:
    return {m.group(1) for m in TYPEOF_RE.finditer(text)}


def _static_script_texts(html: str) -> list[str]:
    rels = re.findall(
        r"""url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]""",
        html,
    )
    blobs: list[str] = []
    for rel in sorted(set(rels)):
        if not str(rel).startswith("js/") or not str(rel).endswith(".js"):
            continue
        path = STATIC / Path(rel)
        if path.is_file():
            blobs.append(path.read_text(encoding="utf-8", errors="replace"))
    return blobs


def collect_undefined(html: str) -> list[str]:
    names = _typeof_names(html)
    defined = _defined_names(html)
    for blob in _static_script_texts(html):
        defined |= _defined_names(blob)
    out = []
    for n in sorted(names):
        if n in BROWSER_GLOBALS:
            continue
        if n in defined:
            continue
        out.append(n)
    return out


def main() -> int:
    if not IDX.is_file():
        print("FAIL index yok:", IDX)
        return 1
    html = IDX.read_text(encoding="utf-8", errors="replace")
    undefined = collect_undefined(html)
    allow = set(ALLOWLIST)
    undef_set = set(undefined)

    extra = sorted(undef_set - allow)
    stale = sorted(allow - undef_set)

    print("typeof_guarded_n=%s undefined_n=%s allowlist_n=%s" % (
        len(_typeof_names(html)), len(undefined), len(ALLOWLIST)))
    print("undefined:", undefined)
    for name in undefined:
        print("  - %s :: %s" % (name, ALLOWLIST.get(name, "ALLOWLIST YOK")))

    ok = True
    if extra:
        ok = False
        print(
            "FAIL yeni tanımsız typeof-korumalı isim(ler): %s — "
            "tanımla veya allowlist'e gerekçeyle ekle (test_index_typeof_tanimsiz)."
            % (", ".join(extra),)
        )
    if stale:
        ok = False
        print(
            "FAIL allowlist'te olup artık tanımsız olmayan: %s — "
            "allowlist'ten çıkar (tanım bulundu)."
            % (", ".join(stale),)
        )
    if ok and undef_set != allow:
        ok = False
        print("FAIL allowlist eşleşmesi bozuldu")

    if ok:
        print("ok typeof_tanimsiz_allowlist")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
