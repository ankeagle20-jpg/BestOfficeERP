# -*- coding: utf-8 -*-
"""Payafin Cari (module_key=ledger) — L1 + L1.5 + L2 + L3 + L4 (PWA hazırlık).

Bakiye kuralı (sabit):
  balance = SUM(give) − SUM(receive)  WHERE is_void = FALSE
  >0 → taraf bize borçlu; <0 → biz tarafa borçluyuz.
Ayrı balance kolonu / cache YOK — her okuma canlı SUM.

JSON API sözleşmesi (L4 — native mobil için stabil):
  Başarı: her zaman {\"ok\": true, ...}
  Hata:   her zaman {\"ok\": false, \"mesaj\": \"...\"} (+ uygun HTTP status)
  İstisna: GET .../statement/pdf başarıda application/pdf (ikili);
           PDF hataları yine JSON {ok:false, mesaj:...} döner.
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, Response, g, jsonify, render_template, request
from flask_login import current_user
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from auth import giris_gerekli
from db import ensure_ledger_tables, execute, execute_returning, fetch_all, fetch_one
from services.exchange_rate_service import get_exchange_rate
from tenant_module_access import module_required, resolve_request_tenant_id

import threading

# Process-ömürlü DDL kapısı: şema başına ensure_ledger_tables SADECE bir kez.
# Finansal veri cache'i DEĞİL — yalnızca "CREATE IF NOT EXISTS zaten koştu mu".
_LEDGER_ENSURED_SCHEMAS: set[str] = set()
_LEDGER_ENSURE_LOCK = threading.Lock()


def _ensure_ledger_tables_once() -> None:
    """İstek yolunda DDL'i şema başına process ömründe tek sefere indirger.

    İlk istek: ensure_ledger_tables() (CREATE IF NOT EXISTS zinciri).
    Sonraki: no-op. Başarısız olursa set'e yazılmaz → sonraki istek yeniden dener.
    """
    schema = getattr(g, "tenant_schema", None) or "__no_tenant__"
    if schema in _LEDGER_ENSURED_SCHEMAS:
        return
    with _LEDGER_ENSURE_LOCK:
        if schema in _LEDGER_ENSURED_SCHEMAS:
            return
        ensure_ledger_tables()
        _LEDGER_ENSURED_SCHEMAS.add(schema)


def _ledger_active_party_count() -> int:
    row = fetch_one(
        "SELECT COUNT(*)::int AS c FROM ledger_parties WHERE is_active = TRUE"
    )
    return int((row or {}).get("c") or 0)


def _ledger_party_quota_block_message() -> str | None:
    """Aktif cari kart sayısı kademe max_personnel (max_parties alias) aşıyorsa mesaj.

    selected_tier yoksa starter varsayılır (fail-closed). max NULL = sınırsız.
    """
    tid = resolve_request_tenant_id()
    if tid is None:
        return "Kiracı doğrulanamadı; cari kart oluşturulamaz."

    ent = fetch_one(
        """
        SELECT metadata, status
        FROM public.tenant_module_entitlements
        WHERE tenant_id = %s AND module_key = 'ledger'
        """,
        (int(tid),),
    )
    if not ent:
        return "Payafin Cari yetkisi yok; cari kart oluşturulamaz."

    meta = ent.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    tier_key = str(meta.get("selected_tier") or "starter").strip().lower() or "starter"

    tenant = fetch_one(
        """
        SELECT COALESCE(NULLIF(TRIM(country_code), ''), 'TR') AS country_code
        FROM public.tenants
        WHERE id = %s
        """,
        (int(tid),),
    )
    cc = str((tenant or {}).get("country_code") or "TR").strip().upper() or "TR"

    tier = fetch_one(
        """
        SELECT max_personnel, display_name, is_contact_sales, tier_key
        FROM public.module_pricing_tiers
        WHERE module_key = 'ledger'
          AND country_code = %s
          AND tier_key = %s
          AND is_active = TRUE
        """,
        (cc, tier_key),
    )
    if not tier and cc != "US":
        tier = fetch_one(
            """
            SELECT max_personnel, display_name, is_contact_sales, tier_key
            FROM public.module_pricing_tiers
            WHERE module_key = 'ledger'
              AND country_code = 'US'
              AND tier_key = %s
              AND is_active = TRUE
            """,
            (tier_key,),
        )
    if not tier:
        # Kademe satırı yoksa oluşturmayı engelleme (seed henüz yüklenmemiş olabilir)
        return None

    mx = tier.get("max_personnel")
    if mx is None:
        return None

    active = _ledger_active_party_count()
    limit_n = int(mx)
    if active >= limit_n:
        disp = str(tier.get("display_name") or tier_key)
        return (
            f"Aktif cari kart limitine ulaşıldı ({active}/{limit_n}, kademe: {disp}). "
            "Yeni cari kart oluşturmak için kademenizi yükseltin "
            "(Pro / Growth / Enterprise) veya mevcut kartları pasifleştirin."
        )
    return None


def _ledger_register_arial():
    """Faturalar PDF ile aynı Arial kayıt deseni (Türkçe glyph)."""
    if getattr(_ledger_register_arial, "_done", False):
        return
    try:
        from routes.faturalar_routes import _register_arial

        _register_arial()
        _ledger_register_arial._done = True
        return
    except Exception:
        pass
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
    _ledger_register_arial._done = True


bp = Blueprint("ledger", __name__)

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_PARTY_TYPES = frozenset({"person", "company"})
_DIRECTIONS = frozenset({"give", "receive"})
_REMINDER_STATUSES = frozenset({"pending", "sent", "dismissed"})
_REMINDER_CHANNELS = frozenset({"email", "in_app"})


def _json_err(message: str, status: int = 400):
    return jsonify({"ok": False, "mesaj": message}), status


def _json_ok(payload: dict | None = None, status: int = 200):
    """Başarılı JSON yanıt — daima ok:true ile birleştirir."""
    body: dict = {"ok": True}
    if payload:
        body.update(payload)
    return jsonify(body), status


_LEDGER_SW_JS = r"""/* Payafin Cari L4 — basit offline fallback (tam offline çalışma değil) */
const CACHE = 'payafin-cari-l4-v1';
const OFFLINE_URL = '/ledger/offline';

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll([OFFLINE_URL]);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== CACHE) return caches.delete(k);
      }));
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', function (event) {
  var req = event.request;
  if (req.method !== 'GET') return;
  // Yalnızca sayfa gezintilerinde offline mesajı; API/cache yok
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(function () {
        return caches.match(OFFLINE_URL).then(function (cached) {
          return cached || new Response(
            '<!doctype html><meta charset=utf-8><title>Çevrimdışı</title>' +
            '<body style="font-family:system-ui;padding:2rem;text-align:center">' +
            '<h1>Bağlantı yok</h1><p>Payafin Cari için internet bağlantısı gerekli.</p></body>',
            { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          );
        });
      })
    );
  }
});
"""


def _dec(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _money(v) -> float:
    """JSON için float; hesaplama Decimal ile yapılır."""
    try:
        return float(_dec(v).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def _format_balance_row(r: dict) -> dict:
    bal = _dec(r["balance"])
    return {
        "currency": str(r["currency"]),
        "given": _money(r["given"]),
        "received": _money(r["received"]),
        "balance": _money(bal),
        "party_owes_us": bal > 0,
        "we_owe_party": bal < 0,
    }


def _balances_by_party_ids(party_ids: list[int]) -> dict[int, list[dict]]:
    """Taraf başına bakiyeler — tek GROUP BY (party_id, currency); liste N+1 önler."""
    ids = sorted({int(p) for p in party_ids if p is not None})
    out: dict[int, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = fetch_all(
        """
        SELECT
            party_id,
            currency,
            COALESCE(SUM(CASE WHEN direction = 'give' THEN amount ELSE 0 END), 0) AS given,
            COALESCE(SUM(CASE WHEN direction = 'receive' THEN amount ELSE 0 END), 0) AS received,
            COALESCE(SUM(
                CASE
                    WHEN direction = 'give' THEN amount
                    WHEN direction = 'receive' THEN -amount
                    ELSE 0
                END
            ), 0) AS balance
        FROM ledger_transactions
        WHERE is_void = FALSE
          AND party_id IN %s
        GROUP BY party_id, currency
        ORDER BY party_id, currency
        """,
        (tuple(ids),),
    ) or []
    for r in rows:
        pid = int(r["party_id"])
        out.setdefault(pid, []).append(_format_balance_row(r))
    return out


def _balances_for_party(party_id: int) -> list[dict]:
    """Para birimi başına canlı SUM — cache yok."""
    return _balances_by_party_ids([int(party_id)]).get(int(party_id), [])


def _party_dict(
    row: dict,
    *,
    with_balances: bool = True,
    balances: list[dict] | None = None,
) -> dict:
    pid = int(row["id"])
    d = {
        "id": pid,
        "name": row.get("name"),
        "type": row.get("type"),
        "phone": row.get("phone"),
        "email": row.get("email"),
        "country": row.get("country"),
        "notes": row.get("notes"),
        "is_active": bool(row.get("is_active")),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }
    if with_balances:
        bals = balances if balances is not None else _balances_for_party(pid)
        d["balances"] = bals
        # UI kısayolu: TRY varsa onu, yoksa ilk para birimi
        primary = next((b for b in bals if b["currency"] == "TRY"), bals[0] if bals else None)
        d["primary_balance"] = primary
    return d


def _tx_dict(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "party_id": int(row["party_id"]),
        "direction": row.get("direction"),
        "amount": _money(row.get("amount")),
        "currency": row.get("currency"),
        "occurred_at": row["occurred_at"].isoformat() if row.get("occurred_at") else None,
        "note": row.get("note"),
        "created_by": row.get("created_by"),
        "is_void": bool(row.get("is_void")),
        "metadata": row.get("metadata") or {},
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def _parse_occurred_at(raw) -> datetime | None:
    if raw is None or raw == "":
        return datetime.now(timezone.utc)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip()
    if not s:
        return datetime.now(timezone.utc)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@bp.route("/")
@giris_gerekli
@module_required("ledger")
def index():
    _ensure_ledger_tables_once()
    return render_template("ledger/index.html")


@bp.route("/manifest.webmanifest")
def pwa_manifest():
    """PWA manifest — giriş gerekmez (Ana ekrana ekle keşfi için)."""
    payload = {
        "name": "Payafin Cari",
        "short_name": "Payafin Cari",
        "description": "Payafin Cari — basit cari takip (ledger)",
        "start_url": "/ledger/",
        "scope": "/ledger/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#f4f7f6",
        "theme_color": "#0d7a5f",
        "lang": "tr",
        "icons": [
            {
                "src": "/static/ledger/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/ledger/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return Response(
        body,
        mimetype="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@bp.route("/sw.js")
def pwa_service_worker():
    """Service worker — scope /ledger/; giriş gerekmez."""
    return Response(
        _LEDGER_SW_JS,
        mimetype="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/ledger/",
        },
    )


@bp.route("/offline")
def pwa_offline():
    """Çevrimdışı fallback sayfası (tam offline çalışma değil)."""
    return render_template("ledger/offline.html")


@bp.route("/api/parties", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_parties_list():
    _ensure_ledger_tables_once()
    active_only = str(request.args.get("active") or "1").strip() not in ("0", "false", "False")
    q = (request.args.get("q") or "").strip()
    sql = """
        SELECT id, name, type, phone, email, country, notes, is_active, created_at, updated_at
        FROM ledger_parties
        WHERE 1=1
    """
    params: list = []
    if active_only:
        sql += " AND is_active = TRUE"
    if q:
        sql += " AND (name ILIKE %s OR COALESCE(phone,'') ILIKE %s OR COALESCE(email,'') ILIKE %s)"
        like = f"%{q}%"
        params.extend([like, like, like])
    sql += " ORDER BY lower(name), id"
    rows = fetch_all(sql, tuple(params)) or []
    ids = [int(r["id"]) for r in rows]
    bals_by = _balances_by_party_ids(ids)
    parties = [
        _party_dict(
            r,
            with_balances=True,
            balances=bals_by.get(int(r["id"]), []),
        )
        for r in rows
    ]
    return jsonify({"ok": True, "parties": parties, "count": len(parties)})


@bp.route("/api/parties", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_parties_create():
    _ensure_ledger_tables_once()
    quota_msg = _ledger_party_quota_block_message()
    if quota_msg:
        return _json_err(quota_msg, 403)
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return _json_err("Ad gerekli.")
    ptype = str(data.get("type") or "person").strip().lower()
    if ptype not in _PARTY_TYPES:
        return _json_err("type person veya company olmalı.")
    phone = (str(data.get("phone") or "").strip() or None)
    email = (str(data.get("email") or "").strip() or None)
    country = (str(data.get("country") or "").strip() or None)
    notes = (str(data.get("notes") or "").strip() or None)

    row = execute_returning(
        """
        INSERT INTO ledger_parties (name, type, phone, email, country, notes)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, name, type, phone, email, country, notes, is_active, created_at, updated_at
        """,
        (name, ptype, phone, email, country, notes),
    )
    if not row:
        return _json_err("Kayıt oluşturulamadı.", 500)
    return jsonify({"ok": True, "party": _party_dict(row, with_balances=True)}), 201


@bp.route("/api/parties/<int:party_id>", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_parties_detail(party_id: int):
    _ensure_ledger_tables_once()
    row = fetch_one(
        """
        SELECT id, name, type, phone, email, country, notes, is_active, created_at, updated_at
        FROM ledger_parties
        WHERE id = %s
        """,
        (int(party_id),),
    )
    if not row:
        return _json_err("Cari bulunamadı.", 404)

    txs = fetch_all(
        """
        SELECT id, party_id, direction, amount, currency, occurred_at, note,
               created_by, is_void, metadata, created_at
        FROM ledger_transactions
        WHERE party_id = %s
        ORDER BY occurred_at DESC, id DESC
        LIMIT 200
        """,
        (int(party_id),),
    ) or []

    party = _party_dict(row, with_balances=True)
    return jsonify(
        {
            "ok": True,
            "party": party,
            "transactions": [_tx_dict(t) for t in txs],
        }
    )


@bp.route("/api/transactions", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_transactions_create():
    _ensure_ledger_tables_once()
    data = request.get_json(silent=True) or {}
    try:
        party_id = int(data.get("party_id"))
    except (TypeError, ValueError):
        return _json_err("party_id gerekli.")

    party = fetch_one("SELECT id FROM ledger_parties WHERE id = %s", (party_id,))
    if not party:
        return _json_err("Cari bulunamadı.", 404)

    direction = str(data.get("direction") or "").strip().lower()
    if direction not in _DIRECTIONS:
        return _json_err("direction give veya receive olmalı.")

    try:
        amount = _dec(data.get("amount"))
    except (InvalidOperation, TypeError, ValueError):
        return _json_err("Geçersiz tutar.")
    if amount <= 0:
        return _json_err("Tutar 0'dan büyük olmalı.")

    currency = str(data.get("currency") or "TRY").strip().upper()
    if not _CURRENCY_RE.fullmatch(currency):
        return _json_err("currency 3 harfli ISO kod olmalı (örn. TRY).")

    occurred_at = _parse_occurred_at(data.get("occurred_at"))
    if occurred_at is None:
        return _json_err("occurred_at geçersiz.")

    note = (str(data.get("note") or "").strip() or None)
    meta = data.get("metadata")
    if meta is None:
        meta_json = "{}"
    elif isinstance(meta, dict):
        meta_json = json.dumps(meta, ensure_ascii=False)
    else:
        return _json_err("metadata nesne olmalı.")

    created_by = None
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            created_by = int(current_user.id)
    except (TypeError, ValueError, AttributeError):
        created_by = None

    row = execute_returning(
        """
        INSERT INTO ledger_transactions (
            party_id, direction, amount, currency, occurred_at, note, created_by, metadata
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s::jsonb
        )
        RETURNING id, party_id, direction, amount, currency, occurred_at, note,
                  created_by, is_void, metadata, created_at
        """,
        (
            party_id,
            direction,
            str(amount.quantize(Decimal("0.01"))),
            currency,
            occurred_at,
            note,
            created_by,
            meta_json,
        ),
    )
    if not row:
        return _json_err("Hareket oluşturulamadı.", 500)

    return jsonify(
        {
            "ok": True,
            "transaction": _tx_dict(row),
            "balances": _balances_for_party(party_id),
        }
    ), 201


@bp.route("/api/transactions/<int:tx_id>/void", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_transactions_void(tx_id: int):
    _ensure_ledger_tables_once()
    row = fetch_one(
        """
        SELECT id, party_id, direction, amount, currency, occurred_at, note,
               created_by, is_void, metadata, created_at
        FROM ledger_transactions
        WHERE id = %s
        """,
        (int(tx_id),),
    )
    if not row:
        return _json_err("Hareket bulunamadı.", 404)
    if row.get("is_void"):
        return jsonify(
            {
                "ok": True,
                "transaction": _tx_dict(row),
                "balances": _balances_for_party(int(row["party_id"])),
                "mesaj": "Zaten iptal.",
            }
        )

    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason") or "").strip()

    updated = execute_returning(
        """
        UPDATE ledger_transactions
        SET is_void = TRUE,
            metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
        WHERE id = %s AND is_void = FALSE
        RETURNING id, party_id, direction, amount, currency, occurred_at, note,
                  created_by, is_void, metadata, created_at
        """,
        (
            json.dumps(
                {
                    "voided_at": datetime.now(timezone.utc).isoformat(),
                    "void_reason": reason or None,
                },
                ensure_ascii=False,
            ),
            int(tx_id),
        ),
    )
    if not updated:
        return _json_err("İptal başarısız.", 500)

    return jsonify(
        {
            "ok": True,
            "transaction": _tx_dict(updated),
            "balances": _balances_for_party(int(updated["party_id"])),
        }
    )


def _parse_date_arg(raw: str | None, *, label: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _balances_for_parties(party_ids: list[int]) -> list[dict]:
    """Birden fazla party için para birimi başına konsolide canlı SUM (grup detay)."""
    if not party_ids:
        return []
    rows = fetch_all(
        """
        SELECT
            currency,
            COALESCE(SUM(CASE WHEN direction = 'give' THEN amount ELSE 0 END), 0) AS given,
            COALESCE(SUM(CASE WHEN direction = 'receive' THEN amount ELSE 0 END), 0) AS received,
            COALESCE(SUM(
                CASE
                    WHEN direction = 'give' THEN amount
                    WHEN direction = 'receive' THEN -amount
                    ELSE 0
                END
            ), 0) AS balance
        FROM ledger_transactions
        WHERE party_id = ANY(%s)
          AND is_void = FALSE
        GROUP BY currency
        ORDER BY currency
        """,
        (list(party_ids),),
    )
    return [_format_balance_row(r) for r in (rows or [])]


def _group_dict(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row.get("name"),
        "notes": row.get("notes"),
        "is_active": bool(row.get("is_active")),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def _build_statement(party_id: int, d_from: date, d_to: date) -> dict | None:
    """Tarih aralığı ekstresi — void hariç; satırlar + para birimi alt toplamları."""
    party = fetch_one(
        """
        SELECT id, name, type, phone, email, country, notes, is_active, created_at, updated_at
        FROM ledger_parties
        WHERE id = %s
        """,
        (int(party_id),),
    )
    if not party:
        return None

    start_ts = datetime(d_from.year, d_from.month, d_from.day, tzinfo=timezone.utc)
    end_excl = datetime(d_to.year, d_to.month, d_to.day, tzinfo=timezone.utc) + timedelta(days=1)

    txs = fetch_all(
        """
        SELECT id, party_id, direction, amount, currency, occurred_at, note,
               created_by, is_void, metadata, created_at
        FROM ledger_transactions
        WHERE party_id = %s
          AND is_void = FALSE
          AND occurred_at >= %s
          AND occurred_at < %s
        ORDER BY occurred_at ASC, id ASC
        """,
        (int(party_id), start_ts, end_excl),
    ) or []

    # Dönem öncesi açılış bakiyesi (canlı SUM)
    opening_rows = fetch_all(
        """
        SELECT
            currency,
            COALESCE(SUM(
                CASE
                    WHEN direction = 'give' THEN amount
                    WHEN direction = 'receive' THEN -amount
                    ELSE 0
                END
            ), 0) AS balance
        FROM ledger_transactions
        WHERE party_id = %s
          AND is_void = FALSE
          AND occurred_at < %s
        GROUP BY currency
        ORDER BY currency
        """,
        (int(party_id), start_ts),
    ) or []
    opening = [
        {"currency": str(r["currency"]), "balance": _money(r["balance"])}
        for r in opening_rows
    ]

    totals_map: dict[str, dict] = {}
    for t in txs:
        cur = str(t["currency"])
        slot = totals_map.setdefault(
            cur, {"currency": cur, "given": Decimal("0"), "received": Decimal("0"), "net": Decimal("0")}
        )
        amt = _dec(t["amount"])
        if t["direction"] == "give":
            slot["given"] += amt
            slot["net"] += amt
        else:
            slot["received"] += amt
            slot["net"] -= amt

    period_totals = [
        {
            "currency": k,
            "given": _money(v["given"]),
            "received": _money(v["received"]),
            "net": _money(v["net"]),
        }
        for k, v in sorted(totals_map.items())
    ]

    # Kapanış = açılış + dönem net (para birimi birleştir)
    closing_map: dict[str, Decimal] = {}
    for o in opening:
        closing_map[o["currency"]] = _dec(o["balance"])
    for p in period_totals:
        closing_map[p["currency"]] = closing_map.get(p["currency"], Decimal("0")) + _dec(p["net"])
    closing = [
        {"currency": k, "balance": _money(v)} for k, v in sorted(closing_map.items())
    ]

    return {
        "party": _party_dict(party, with_balances=True),
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "transactions": [_tx_dict(t) for t in txs],
        "row_count": len(txs),
        "opening_balances": opening,
        "period_totals": period_totals,
        "closing_balances": closing,
    }


def _resolve_ledger_logo_path() -> str | None:
    """Ofisbir / Payafin logo adayları — kira bildirgesi ile aynı desen."""
    here = os.path.dirname(os.path.abspath(__file__))
    names = (
        "Ofisbir Logo.jpg",
        "Ofisbir Logo.jpeg",
        "Ofisbir Logo.png",
        "ofisbir_logo.png",
        "ofisbir_logo.jpg",
        "payafin_logo.png",
        "logo.png",
        "logo.jpg",
    )
    for nm in names:
        for base in (
            os.path.abspath(os.path.join(here, "..", "..", "assets", nm)),
            os.path.abspath(os.path.join(here, "..", "static", nm)),
            os.path.abspath(os.path.join(here, "..", "assets", nm)),
        ):
            if os.path.isfile(base):
                return base
    return None


def _build_statement_pdf(stmt: dict) -> bytes:
    """A4 ekstre PDF — reportlab canvas + Table (faturalar/kira PDF deseni)."""
    _ledger_register_arial()
    font = "Arial" if "Arial" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    font_b = (
        "Arial-Bold"
        if "Arial-Bold" in pdfmetrics.getRegisteredFontNames()
        else ("Helvetica-Bold" if font == "Helvetica" else font)
    )

    buf = io.BytesIO()
    w, h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    c.setPageCompression(0)
    c.setTitle("Payafin Cari Ekstre")

    y = h - 18 * mm
    logo = _resolve_ledger_logo_path()
    if logo:
        try:
            img = ImageReader(logo)
            iw, ih = img.getSize()
            max_w, max_h = 42 * mm, 14 * mm
            scale = min(max_w / float(iw or 1), max_h / float(ih or 1))
            dw, dh = float(iw) * scale, float(ih) * scale
            c.drawImage(logo, 15 * mm, y - dh, width=dw, height=dh, mask="auto")
        except Exception:
            pass

    c.setFont(font_b, 14)
    c.drawString(15 * mm, y - 18 * mm, "Payafin Cari — Ekstre")
    y -= 26 * mm

    party = stmt.get("party") or {}
    c.setFont(font, 10)
    lines = [
        f"Taraf: {party.get('name') or '—'}",
        f"Tür: {'Şirket' if party.get('type') == 'company' else 'Kişi'}",
        f"Dönem: {stmt.get('from')} — {stmt.get('to')}",
        f"Hareket sayısı: {stmt.get('row_count', 0)}",
    ]
    if party.get("phone"):
        lines.append(f"Telefon: {party.get('phone')}")
    if party.get("email"):
        lines.append(f"E-posta: {party.get('email')}")
    for line in lines:
        c.drawString(15 * mm, y, line)
        y -= 5 * mm

    y -= 3 * mm
    c.setFont(font_b, 10)
    c.drawString(15 * mm, y, "Dönem hareketleri")
    y -= 6 * mm

    data = [["Tarih", "Yön", "Tutar", "PB", "Not"]]
    for t in stmt.get("transactions") or []:
        occurred = (t.get("occurred_at") or "")[:10]
        direction = "Verdim" if t.get("direction") == "give" else "Aldım"
        amt = f"{float(t.get('amount') or 0):,.2f}"
        note = (t.get("note") or "")[:40]
        data.append([occurred, direction, amt, t.get("currency") or "", note])

    if len(data) == 1:
        data.append(["—", "—", "—", "—", "Hareket yok"])

    col_w = [28 * mm, 22 * mm, 28 * mm, 14 * mm, 78 * mm]
    table = Table(data, colWidths=col_w, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), font_b, 8),
                ("FONT", (0, 1), (-1, -1), font, 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.92, 0.94, 0.96)),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.Color(0.75, 0.78, 0.82)),
                ("ALIGN", (2, 1), (2, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    tw, th = table.wrapOn(c, w - 30 * mm, y - 40 * mm)
    if y - th < 35 * mm:
        c.showPage()
        y = h - 20 * mm
        tw, th = table.wrapOn(c, w - 30 * mm, y - 40 * mm)
    table.drawOn(c, 15 * mm, y - th)
    y = y - th - 8 * mm

    # Makine-okunur satır izi (Helvetica ASCII) — canlı test satır sayısı doğrulaması
    c.setFont("Helvetica", 7)
    for i, t in enumerate(stmt.get("transactions") or []):
        marker = (
            f"ROW {i + 1} {t.get('direction')} "
            f"{float(t.get('amount') or 0):.2f} {t.get('currency') or ''}"
        )
        c.drawString(15 * mm, y, marker)
        y -= 3.5 * mm
        if y < 28 * mm:
            c.showPage()
            y = h - 20 * mm
            c.setFont("Helvetica", 7)
    c.setFont("Helvetica", 7)
    c.drawString(15 * mm, y, f"LEDGER_STMT_ROWS={int(stmt.get('row_count') or 0)}")
    y -= 6 * mm

    c.setFont(font_b, 10)
    c.drawString(15 * mm, y, "Para birimi alt toplamları (dönem)")
    y -= 5 * mm
    c.setFont(font, 9)
    for p in stmt.get("period_totals") or []:
        line = (
            f"{p['currency']}: Verdim {_money(p['given']):,.2f}  "
            f"Aldım {_money(p['received']):,.2f}  Net {_money(p['net']):,.2f}"
        )
        c.drawString(15 * mm, y, line)
        y -= 4.5 * mm
        if y < 25 * mm:
            c.showPage()
            y = h - 20 * mm
            c.setFont(font, 9)

    y -= 3 * mm
    c.setFont(font_b, 9)
    c.drawString(15 * mm, y, "Kapanış bakiyeleri")
    y -= 4.5 * mm
    c.setFont(font, 9)
    for p in stmt.get("closing_balances") or []:
        c.drawString(15 * mm, y, f"{p['currency']}: {_money(p['balance']):,.2f}")
        y -= 4.5 * mm

    c.setFont(font, 7)
    c.drawString(
        15 * mm,
        12 * mm,
        "Bakiye canlı SUM(give)-SUM(receive), is_void=FALSE — ayrı balance alanı yok.",
    )
    c.save()
    return buf.getvalue()


@bp.route("/api/groups", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_groups_list():
    _ensure_ledger_tables_once()
    active_only = str(request.args.get("active") or "1").strip() not in ("0", "false", "False")
    sql = """
        SELECT g.id, g.name, g.notes, g.is_active, g.created_at,
               COUNT(m.id)::int AS member_count
        FROM ledger_groups g
        LEFT JOIN ledger_group_members m ON m.group_id = g.id
        WHERE 1=1
    """
    params: list = []
    if active_only:
        sql += " AND g.is_active = TRUE"
    sql += " GROUP BY g.id, g.name, g.notes, g.is_active, g.created_at ORDER BY lower(g.name), g.id"
    rows = fetch_all(sql, tuple(params)) or []
    groups = []
    for r in rows:
        d = _group_dict(r)
        d["member_count"] = int(r.get("member_count") or 0)
        groups.append(d)
    return jsonify({"ok": True, "groups": groups, "count": len(groups)})


@bp.route("/api/groups", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_groups_create():
    _ensure_ledger_tables_once()
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return _json_err("Grup adı gerekli.")
    notes = (str(data.get("notes") or "").strip() or None)
    row = execute_returning(
        """
        INSERT INTO ledger_groups (name, notes)
        VALUES (%s, %s)
        RETURNING id, name, notes, is_active, created_at
        """,
        (name, notes),
    )
    if not row:
        return _json_err("Grup oluşturulamadı.", 500)
    g = _group_dict(row)
    g["member_count"] = 0
    return jsonify({"ok": True, "group": g}), 201


@bp.route("/api/groups/<int:group_id>/members", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_groups_members(group_id: int):
    _ensure_ledger_tables_once()
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or data.get("op") or "add").strip().lower()
    try:
        party_id = int(data.get("party_id"))
    except (TypeError, ValueError):
        return _json_err("party_id gerekli.")

    grp = fetch_one("SELECT id FROM ledger_groups WHERE id = %s", (int(group_id),))
    if not grp:
        return _json_err("Grup bulunamadı.", 404)
    party = fetch_one("SELECT id FROM ledger_parties WHERE id = %s", (party_id,))
    if not party:
        return _json_err("Cari bulunamadı.", 404)

    if action in ("add", "ekle"):
        execute_returning(
            """
            INSERT INTO ledger_group_members (group_id, party_id)
            VALUES (%s, %s)
            ON CONFLICT (group_id, party_id) DO NOTHING
            RETURNING id
            """,
            (int(group_id), party_id),
        )
        return jsonify({"ok": True, "action": "add", "group_id": int(group_id), "party_id": party_id})
    if action in ("remove", "çıkar", "cikar", "delete"):
        execute(
            "DELETE FROM ledger_group_members WHERE group_id = %s AND party_id = %s",
            (int(group_id), party_id),
        )
        return jsonify({"ok": True, "action": "remove", "group_id": int(group_id), "party_id": party_id})
    return _json_err("action add veya remove olmalı.")


@bp.route("/api/groups/<int:group_id>", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_groups_detail(group_id: int):
    _ensure_ledger_tables_once()
    row = fetch_one(
        """
        SELECT id, name, notes, is_active, created_at
        FROM ledger_groups
        WHERE id = %s
        """,
        (int(group_id),),
    )
    if not row:
        return _json_err("Grup bulunamadı.", 404)

    members_rows = fetch_all(
        """
        SELECT p.id, p.name, p.type, p.phone, p.email, p.country, p.notes,
               p.is_active, p.created_at, p.updated_at
        FROM ledger_group_members m
        JOIN ledger_parties p ON p.id = m.party_id
        WHERE m.group_id = %s
        ORDER BY lower(p.name), p.id
        """,
        (int(group_id),),
    ) or []

    members = [_party_dict(m, with_balances=True) for m in members_rows]
    party_ids = [int(m["id"]) for m in members]
    consolidated = _balances_for_parties(party_ids)
    primary = next((b for b in consolidated if b["currency"] == "TRY"), consolidated[0] if consolidated else None)

    return jsonify(
        {
            "ok": True,
            "group": _group_dict(row),
            "members": members,
            "member_count": len(members),
            "consolidated_balances": consolidated,
            "primary_balance": primary,
        }
    )


@bp.route("/api/parties/<int:party_id>/statement", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_party_statement(party_id: int):
    _ensure_ledger_tables_once()
    d_from = _parse_date_arg(request.args.get("from"), label="from")
    d_to = _parse_date_arg(request.args.get("to"), label="to")
    if not d_from or not d_to:
        return _json_err("from ve to gerekli (YYYY-MM-DD).")
    if d_to < d_from:
        return _json_err("to, from'dan önce olamaz.")
    stmt = _build_statement(int(party_id), d_from, d_to)
    if not stmt:
        return _json_err("Cari bulunamadı.", 404)
    return jsonify({"ok": True, "statement": stmt})


@bp.route("/api/parties/<int:party_id>/statement/pdf", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_party_statement_pdf(party_id: int):
    _ensure_ledger_tables_once()
    d_from = _parse_date_arg(request.args.get("from"), label="from")
    d_to = _parse_date_arg(request.args.get("to"), label="to")
    if not d_from or not d_to:
        return _json_err("from ve to gerekli (YYYY-MM-DD).")
    if d_to < d_from:
        return _json_err("to, from'dan önce olamaz.")
    stmt = _build_statement(int(party_id), d_from, d_to)
    if not stmt:
        return _json_err("Cari bulunamadı.", 404)
    try:
        pdf_bytes = _build_statement_pdf(stmt)
    except Exception as e:
        return _json_err(f"PDF oluşturulamadı: {e}", 500)

    name = str((stmt.get("party") or {}).get("name") or "cari")
    safe = re.sub(r"[^A-Za-z0-9_\-]+", "_", name)[:40] or "cari"
    fname = f"ekstre_{safe}_{d_from.isoformat()}_{d_to.isoformat()}.pdf"
    indir = str(request.args.get("indir") or "").lower() in ("1", "true", "yes")
    disposition = "attachment" if indir else "inline"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{fname}"'},
    )


def _convert_via_usd(amount: Decimal, from_cur: str, to_cur: str) -> tuple[Decimal | None, str | None]:
    """USD hub ile çevir. Başarısızsa (None, uyarı) — sessiz yanlış çeviri yok."""
    fc = str(from_cur or "").strip().upper()
    tc = str(to_cur or "").strip().upper()
    if not fc or not tc:
        return None, "Para birimi eksik."
    if fc == tc:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), None
    try:
        usd_to_from = get_exchange_rate("USD", fc)
        usd_to_to = get_exchange_rate("USD", tc)
        if usd_to_from <= 0 or usd_to_to <= 0:
            return None, f"Geçersiz kur: USD/{fc} veya USD/{tc}"
        usd_amt = amount / usd_to_from
        converted = (usd_amt * usd_to_to).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return converted, None
    except Exception as e:
        return None, f"Kur alınamadı ({fc}→{tc}): {e}"


def _reminder_dict(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "party_id": int(row["party_id"]),
        "party_name": row.get("party_name"),
        "due_at": row["due_at"].isoformat() if row.get("due_at") else None,
        "note": row.get("note"),
        "status": row.get("status"),
        "channel": row.get("channel"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


@bp.route("/api/reminders", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_reminders_list():
    _ensure_ledger_tables_once()
    # filter: upcoming (default) = pending + due within horizon; today; all
    filt = str(request.args.get("filter") or "upcoming").strip().lower()
    try:
        days = int(request.args.get("days") or 7)
    except (TypeError, ValueError):
        days = 7
    days = max(0, min(days, 90))

    now = datetime.now(timezone.utc)
    sql = """
        SELECT r.id, r.party_id, r.due_at, r.note, r.status, r.channel, r.created_at,
               p.name AS party_name
        FROM ledger_reminders r
        JOIN ledger_parties p ON p.id = r.party_id
        WHERE 1=1
    """
    params: list = []
    if filt == "today":
        day0 = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        day1 = day0 + timedelta(days=1)
        sql += " AND r.status = 'pending' AND r.due_at >= %s AND r.due_at < %s"
        params.extend([day0, day1])
    elif filt == "all":
        pass
    else:
        # upcoming: pending, due_at <= now+days (includes overdue)
        horizon = now + timedelta(days=days)
        sql += " AND r.status = 'pending' AND r.due_at <= %s"
        params.append(horizon)

    sql += " ORDER BY r.due_at ASC, r.id ASC LIMIT 200"
    rows = fetch_all(sql, tuple(params)) or []
    return jsonify(
        {
            "ok": True,
            "filter": filt,
            "days": days,
            "reminders": [_reminder_dict(r) for r in rows],
            "count": len(rows),
        }
    )


@bp.route("/api/reminders", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_reminders_create():
    _ensure_ledger_tables_once()
    data = request.get_json(silent=True) or {}
    try:
        party_id = int(data.get("party_id"))
    except (TypeError, ValueError):
        return _json_err("party_id gerekli.")
    party = fetch_one("SELECT id, name FROM ledger_parties WHERE id = %s", (party_id,))
    if not party:
        return _json_err("Cari bulunamadı.", 404)

    due_at = _parse_occurred_at(data.get("due_at"))
    if due_at is None:
        return _json_err("due_at geçersiz (ISO tarih/saat).")

    channel = str(data.get("channel") or "in_app").strip().lower()
    if channel not in _REMINDER_CHANNELS:
        return _json_err("channel email veya in_app olmalı.")
    note = (str(data.get("note") or "").strip() or None)

    row = execute_returning(
        """
        INSERT INTO ledger_reminders (party_id, due_at, note, status, channel)
        VALUES (%s, %s, %s, 'pending', %s)
        RETURNING id, party_id, due_at, note, status, channel, created_at
        """,
        (party_id, due_at, note, channel),
    )
    if not row:
        return _json_err("Hatırlatma oluşturulamadı.", 500)
    d = _reminder_dict(row)
    d["party_name"] = party.get("name")
    return jsonify({"ok": True, "reminder": d}), 201


@bp.route("/api/reminders/<int:reminder_id>/dismiss", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_reminders_dismiss(reminder_id: int):
    _ensure_ledger_tables_once()
    row = fetch_one(
        """
        SELECT r.id, r.party_id, r.due_at, r.note, r.status, r.channel, r.created_at,
               p.name AS party_name
        FROM ledger_reminders r
        JOIN ledger_parties p ON p.id = r.party_id
        WHERE r.id = %s
        """,
        (int(reminder_id),),
    )
    if not row:
        return _json_err("Hatırlatma bulunamadı.", 404)
    if row.get("status") == "dismissed":
        return jsonify({"ok": True, "reminder": _reminder_dict(row), "mesaj": "Zaten kapatıldı."})

    updated = execute_returning(
        """
        UPDATE ledger_reminders
        SET status = 'dismissed'
        WHERE id = %s AND status <> 'dismissed'
        RETURNING id, party_id, due_at, note, status, channel, created_at
        """,
        (int(reminder_id),),
    )
    if not updated:
        return _json_err("Kapatılamadı.", 500)
    d = _reminder_dict(updated)
    d["party_name"] = row.get("party_name")
    return jsonify({"ok": True, "reminder": d})


@bp.route("/api/summary", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_summary():
    """Tüm taraflar — para birimi başına canlı SUM; isteğe bağlı display_currency çevirisi."""
    _ensure_ledger_tables_once()
    rows = fetch_all(
        """
        SELECT
            currency,
            COALESCE(SUM(CASE WHEN direction = 'give' THEN amount ELSE 0 END), 0) AS given,
            COALESCE(SUM(CASE WHEN direction = 'receive' THEN amount ELSE 0 END), 0) AS received,
            COALESCE(SUM(
                CASE
                    WHEN direction = 'give' THEN amount
                    WHEN direction = 'receive' THEN -amount
                    ELSE 0
                END
            ), 0) AS balance
        FROM ledger_transactions
        WHERE is_void = FALSE
        GROUP BY currency
        ORDER BY currency
        """
    ) or []

    by_currency = []
    for r in rows:
        bal = _dec(r["balance"])
        by_currency.append(
            {
                "currency": str(r["currency"]),
                "given": _money(r["given"]),
                "received": _money(r["received"]),
                "balance": _money(bal),
                "receivable": _money(bal if bal > 0 else Decimal("0")),
                "payable": _money((-bal) if bal < 0 else Decimal("0")),
                "party_owes_us": bal > 0,
                "we_owe_party": bal < 0,
            }
        )

    display = str(request.args.get("display_currency") or "").strip().upper()
    converted = None
    fx_warnings: list[str] = []
    if display:
        if not _CURRENCY_RE.fullmatch(display):
            return _json_err("display_currency 3 harfli ISO kod olmalı.")
        total = Decimal("0")
        receivable = Decimal("0")
        payable = Decimal("0")
        ok_all = True
        parts = []
        for item in by_currency:
            amt = _dec(item["balance"])
            conv, err = _convert_via_usd(amt, item["currency"], display)
            if err or conv is None:
                ok_all = False
                fx_warnings.append(err or f"{item['currency']} çevrilemedi")
                parts.append(
                    {
                        "currency": item["currency"],
                        "balance": item["balance"],
                        "converted": None,
                        "error": err,
                    }
                )
                continue
            total += conv
            if conv > 0:
                receivable += conv
            elif conv < 0:
                payable += -conv
            parts.append(
                {
                    "currency": item["currency"],
                    "balance": item["balance"],
                    "converted": _money(conv),
                    "error": None,
                }
            )
        if ok_all:
            converted = {
                "currency": display,
                "balance": _money(total),
                "receivable": _money(receivable),
                "payable": _money(payable),
                "parts": parts,
                "complete": True,
            }
        else:
            # Kısmi/başarısız: tek toplam üretme (sessiz yanlış yok)
            converted = {
                "currency": display,
                "balance": None,
                "receivable": None,
                "payable": None,
                "parts": parts,
                "complete": False,
            }

    return jsonify(
        {
            "ok": True,
            "by_currency": by_currency,
            "display_currency": display or None,
            "converted": converted,
            "fx_warnings": fx_warnings,
        }
    )
