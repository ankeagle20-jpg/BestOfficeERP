# -*- coding: utf-8 -*-
"""Ortak PWA yardımcıları — manifest + navigate-offline service worker.

Payafin Cari L4 deseni: davranış değişmeden yeniden kullanım için çıkarıldı.
Modül başına ayrı scope/start_url/cache_name kullanılır.
"""
from __future__ import annotations

import json
from typing import Any

from flask import Response, render_template


def build_web_manifest(
    *,
    name: str,
    short_name: str | None = None,
    description: str = "",
    start_url: str,
    scope: str,
    theme_color: str,
    background_color: str = "#f4f7f6",
    display: str = "standalone",
    orientation: str = "portrait-primary",
    lang: str = "tr",
    icons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Web App Manifest sözlüğü (parametreli)."""
    return {
        "name": name,
        "short_name": short_name if short_name is not None else name,
        "description": description,
        "start_url": start_url,
        "scope": scope,
        "display": display,
        "orientation": orientation,
        "background_color": background_color,
        "theme_color": theme_color,
        "lang": lang,
        "icons": list(icons or []),
    }


def standard_png_icons(base_path: str) -> list[dict[str, Any]]:
    """/{base}/icon-192.png + icon-512.png — Cari static deseni."""
    root = "/" + str(base_path or "").strip().strip("/")
    return [
        {
            "src": f"{root}/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": f"{root}/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any maskable",
        },
    ]


def web_manifest_response(payload: dict[str, Any]) -> Response:
    """manifest.webmanifest HTTP yanıtı (Cari ile aynı Content-Type / Cache-Control)."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return Response(
        body,
        mimetype="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def build_navigate_offline_sw(
    *,
    cache_name: str,
    offline_url: str,
    product_label: str,
    comment: str = "basit offline fallback (tam offline çalışma değil)",
) -> str:
    """Minimal SW: yalnızca navigate offline fallback; API cache yok.

    Payafin Cari L4 çıktısı ile birebir aynı gövde üretir (aynı parametrelerle).
    """
    # Tek tırnak: Cari L4 ham gövdesiyle byte-düzeyi uyum (JSON çift tırnak değil)
    if "'" in cache_name or "'" in offline_url or "'" in product_label:
        raise ValueError("cache_name/offline_url/product_label tek tırnak içeremez")
    return (
        f"/* {product_label} L4 — {comment} */\n"
        f"const CACHE = '{cache_name}';\n"
        f"const OFFLINE_URL = '{offline_url}';\n"
        "\n"
        "self.addEventListener('install', function (event) {\n"
        "  event.waitUntil(\n"
        "    caches.open(CACHE).then(function (cache) {\n"
        "      return cache.addAll([OFFLINE_URL]);\n"
        "    }).then(function () {\n"
        "      return self.skipWaiting();\n"
        "    })\n"
        "  );\n"
        "});\n"
        "\n"
        "self.addEventListener('activate', function (event) {\n"
        "  event.waitUntil(\n"
        "    caches.keys().then(function (keys) {\n"
        "      return Promise.all(keys.map(function (k) {\n"
        "        if (k !== CACHE) return caches.delete(k);\n"
        "      }));\n"
        "    }).then(function () {\n"
        "      return self.clients.claim();\n"
        "    })\n"
        "  );\n"
        "});\n"
        "\n"
        "self.addEventListener('fetch', function (event) {\n"
        "  var req = event.request;\n"
        "  if (req.method !== 'GET') return;\n"
        "  // Yalnızca sayfa gezintilerinde offline mesajı; API/cache yok\n"
        "  if (req.mode === 'navigate') {\n"
        "    event.respondWith(\n"
        "      fetch(req).catch(function () {\n"
        "        return caches.match(OFFLINE_URL).then(function (cached) {\n"
        "          return cached || new Response(\n"
        "            '<!doctype html><meta charset=utf-8><title>Çevrimdışı</title>' +\n"
        "            '<body style=\"font-family:system-ui;padding:2rem;text-align:center\">' +\n"
        "            '<h1>Bağlantı yok</h1><p>"
        f"{product_label} için internet bağlantısı gerekli.</p></body>',\n"
        "            { headers: { 'Content-Type': 'text/html; charset=utf-8' } }\n"
        "          );\n"
        "        });\n"
        "      })\n"
        "    );\n"
        "  }\n"
        "});\n"
    )


def service_worker_response(js_body: str, *, allowed_scope: str) -> Response:
    """sw.js HTTP yanıtı (Service-Worker-Allowed + no-cache)."""
    return Response(
        js_body,
        mimetype="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": allowed_scope,
        },
    )


def render_offline_page(template_name: str, **context: Any) -> str:
    """Offline HTML şablon deseni — modül templates/{mod}/offline.html."""
    return render_template(template_name, **context)
