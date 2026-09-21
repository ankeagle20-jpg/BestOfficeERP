# -*- coding: utf-8 -*-
"""Admin: Payafin platform modül entitlement yönetimi (yalnız public host)."""
from __future__ import annotations

import hashlib
import logging
import secrets
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, render_template, request
from flask_login import current_user
from psycopg2 import sql as psql
from werkzeug.security import generate_password_hash

from auth import admin_gerekli, generate_security_stamp
from db import db, execute, fetch_all, fetch_one
from mail_utils import send_mail, send_password_reset_email
from signup_validation import validate_password_strength
from tenant_identity import _tenant_apex_domains
from tenant_module_access import (
    has_module_entitlement,
    invalidate_module_entitlement_cache,
)

logger = logging.getLogger(__name__)

bp = Blueprint("admin_modules", __name__)

# Sprint 1 ilk module_key kataloğu (backfill ile aynı)
MODULE_CATALOG: tuple[tuple[str, str], ...] = (
    ("core_erp", "Core ERP"),
    ("crm", "CRM"),
    ("randevu", "Randevu"),
    ("personnel", "Personel / Devam"),
    ("attendance", "Attendance"),
    ("ledger", "Payafin Cari"),
)
MODULE_KEYS = frozenset(k for k, _ in MODULE_CATALOG)

STATUSES = frozenset({"trial", "active", "suspended", "expired", "revoked"})
BILLING_MODES = frozenset(
    {"included", "addon", "standalone", "promo", "manual"}
)

MSG_PLATFORM_ONLY = (
    "Platform modül yönetimi yalnızca ana (public) host'ta kullanılabilir."
)
MSG_LEDGER_REQUIRED = (
    "Önce Payafin Cari'yi açın (ledger trial|active olmalı)."
)
MSG_PUBLIC_FORBIDDEN = "Platform (public) kiracısı dönüştürülemez."
MSG_CONFIRM_REQUIRED = "Onay gerekli (confirm=true)."
MSG_UNDERSTOOD_REQUIRED = "Anladım onay kutusu gerekli (understood=true)."


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _resolve_active_tenant(tenant_id: int) -> dict | None:
    return fetch_one(
        """
        SELECT id, slug, company_name, schema_name, status
        FROM public.tenants
        WHERE id = %s AND status = 'active'
        """,
        (tenant_id,),
    )


def _schema_ident(schema_name: str) -> str | None:
    """Güvenli şema adı (yalnız tenant_* veya public)."""
    s = str(schema_name or "").strip()
    if s == "public":
        return s
    from db import _TENANT_SCHEMA_RE

    if _TENANT_SCHEMA_RE.fullmatch(s):
        return s
    return None


def _ensure_tenant_password_reset_tokens(schema: str) -> None:
    sch = psql.Identifier(schema)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {}.password_reset_tokens (
                    id          BIGSERIAL PRIMARY KEY,
                    user_id     INTEGER NOT NULL
                        REFERENCES {}.users (id) ON DELETE CASCADE,
                    token_hash  TEXT NOT NULL,
                    expires_at  TIMESTAMPTZ NOT NULL,
                    used_at     TIMESTAMPTZ,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    request_ip  TEXT,
                    CONSTRAINT password_reset_tokens_token_hash_key UNIQUE (token_hash)
                )
                """
            ).format(sch, sch)
        )
        cur.execute(
            psql.SQL(
                """
                CREATE INDEX IF NOT EXISTS password_reset_tokens_user_id_idx
                ON {}.password_reset_tokens (user_id)
                """
            ).format(sch)
        )


def _fetch_tenant_admins(schema: str) -> list[dict]:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                """
                SELECT id, username, full_name, role, is_active
                FROM {}.users
                WHERE LOWER(TRIM(COALESCE(role, ''))) = 'admin'
                ORDER BY id
                """
            ).format(psql.Identifier(schema))
        )
        rows = cur.fetchall() or []
        return [dict(r) for r in rows]


def _tenant_reset_url(slug: str, raw_token: str) -> str:
    apex = (_tenant_apex_domains() or ("payafin.com",))[0]
    return f"https://{slug}.{apex}/reset-password?token={raw_token}"


def _generate_temp_password() -> str:
    """signup_validation kurallarına uyan rastgele şifre."""
    for _ in range(40):
        # token_urlsafe + garantili sınıflar
        raw = secrets.token_urlsafe(10)
        pwd = "Aa1" + raw
        if len(pwd) < 10:
            continue
        if validate_password_strength(pwd) is None:
            return pwd
    # son çare
    return "Aa1" + secrets.token_urlsafe(12)




def _upsert_module_entitlement(
    *,
    tenant_id: int,
    slug: str,
    module_key: str,
    status: str,
    billing_mode: str,
    source_reference: str,
    via: str,
) -> None:
    """Admin shell dönüşüm / geri alma için tek satır upsert."""
    execute(
        """
        INSERT INTO public.tenant_module_entitlements (
            tenant_id, tenant_slug, module_key, status, billing_mode,
            source_plan, source_reference, metadata,
            revoked_at, updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s,
            'admin_panel', %s,
            jsonb_build_object('via', %s),
            CASE WHEN %s = 'revoked' THEN NOW() ELSE NULL END,
            NOW()
        )
        ON CONFLICT (tenant_id, module_key) DO UPDATE SET
            status = EXCLUDED.status,
            billing_mode = EXCLUDED.billing_mode,
            tenant_slug = EXCLUDED.tenant_slug,
            source_plan = EXCLUDED.source_plan,
            source_reference = EXCLUDED.source_reference,
            metadata = EXCLUDED.metadata,
            revoked_at = EXCLUDED.revoked_at,
            updated_at = NOW()
        """,
        (
            tenant_id,
            slug,
            module_key,
            status,
            billing_mode,
            source_reference,
            via,
            status,
        ),
    )


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_modules_admin(f):
    """@admin_gerekli + kiracı subdomain'inde 403 (platform-only)."""

    @wraps(f)
    def _tenant_guard(*args, **kwargs):
        if getattr(g, "tenant_schema", None):
            path = request.path or ""
            if "/api/" in path or request.is_json or (
                request.accept_mimetypes.best == "application/json"
            ):
                return _json403(MSG_PLATFORM_ONLY)
            return MSG_PLATFORM_ONLY, 403
        return f(*args, **kwargs)

    return admin_gerekli(_tenant_guard)


@bp.route("/modules")
@platform_modules_admin
def modules_page():
    return render_template(
        "admin/modules.html",
        username=getattr(current_user, "username", "") or "",
        module_catalog=[{"key": k, "label": lab} for k, lab in MODULE_CATALOG],
        statuses=sorted(STATUSES),
        billing_modes=sorted(BILLING_MODES),
    )


@bp.route("/api/modules/tenants")
@platform_modules_admin
def api_modules_tenants():
    rows = fetch_all(
        """
        SELECT id, slug, company_name, schema_name, status
        FROM public.tenants
        WHERE status = 'active'
        ORDER BY
            CASE WHEN slug = 'public' THEN 0 ELSE 1 END,
            slug
        """
    ) or []
    tenants = [
        {
            "id": int(r["id"]),
            "slug": r["slug"],
            "company_name": r.get("company_name") or "",
            "schema_name": r.get("schema_name") or "",
        }
        for r in rows
    ]
    return jsonify({"ok": True, "tenants": tenants, "n": len(tenants)})


@bp.route("/api/modules/entitlements")
@platform_modules_admin
def api_modules_entitlements_get():
    raw = request.args.get("tenant_id")
    try:
        tenant_id = int(raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "tenant_id gerekli"}), 400

    tenant = fetch_one(
        """
        SELECT id, slug, company_name, schema_name, status
        FROM public.tenants
        WHERE id = %s AND status = 'active'
        """,
        (tenant_id,),
    )
    if not tenant:
        return jsonify({"ok": False, "mesaj": "Kiracı bulunamadı."}), 404

    existing = fetch_all(
        """
        SELECT module_key, status, billing_mode, starts_at, ends_at,
               granted_at, revoked_at, source_plan, source_reference
        FROM public.tenant_module_entitlements
        WHERE tenant_id = %s
        """,
        (tenant_id,),
    ) or []
    by_key = {str(r["module_key"]): r for r in existing}

    entitlements = []
    for key, label in MODULE_CATALOG:
        row = by_key.get(key)
        if row:
            entitlements.append(
                {
                    "module_key": key,
                    "display_name": label,
                    "status": row["status"],
                    "billing_mode": row["billing_mode"],
                    "granted": True,
                    "starts_at": row["starts_at"].isoformat()
                    if row.get("starts_at")
                    else None,
                    "ends_at": row["ends_at"].isoformat()
                    if row.get("ends_at")
                    else None,
                }
            )
        else:
            entitlements.append(
                {
                    "module_key": key,
                    "display_name": label,
                    "status": "not_granted",
                    "billing_mode": "included",
                    "granted": False,
                    "starts_at": None,
                    "ends_at": None,
                }
            )

    return jsonify(
        {
            "ok": True,
            "tenant": {
                "id": int(tenant["id"]),
                "slug": tenant["slug"],
                "company_name": tenant.get("company_name") or "",
            },
            "entitlements": entitlements,
            "n": len(entitlements),
        }
    )


@bp.route("/api/modules/entitlements", methods=["PUT"])
@platform_modules_admin
def api_modules_entitlements_put():
    body = request.get_json(silent=True) or {}
    try:
        tenant_id = int(body.get("tenant_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "tenant_id gerekli"}), 400

    module_key = str(body.get("module_key") or "").strip()
    status = str(body.get("status") or "").strip()
    billing_mode = str(body.get("billing_mode") or "").strip()

    if module_key not in MODULE_KEYS:
        return jsonify({"ok": False, "mesaj": "geçersiz module_key"}), 400
    if status not in STATUSES:
        return jsonify(
            {
                "ok": False,
                "mesaj": "geçersiz status (trial/active/suspended/expired/revoked)",
            }
        ), 400
    if billing_mode not in BILLING_MODES:
        return jsonify({"ok": False, "mesaj": "geçersiz billing_mode"}), 400

    tenant = fetch_one(
        """
        SELECT id, slug, status
        FROM public.tenants
        WHERE id = %s AND status = 'active'
        """,
        (tenant_id,),
    )
    if not tenant:
        return jsonify({"ok": False, "mesaj": "Kiracı bulunamadı."}), 404

    slug = str(tenant["slug"])

    execute(
        """
        INSERT INTO public.tenant_module_entitlements (
            tenant_id, tenant_slug, module_key, status, billing_mode,
            source_plan, source_reference, metadata,
            revoked_at, updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s,
            'admin_panel', 'admin_modules_upsert',
            '{"via": "admin/modules"}'::jsonb,
            CASE WHEN %s = 'revoked' THEN NOW() ELSE NULL END,
            NOW()
        )
        ON CONFLICT (tenant_id, module_key) DO UPDATE SET
            status = EXCLUDED.status,
            billing_mode = EXCLUDED.billing_mode,
            tenant_slug = EXCLUDED.tenant_slug,
            source_plan = EXCLUDED.source_plan,
            source_reference = EXCLUDED.source_reference,
            revoked_at = EXCLUDED.revoked_at,
            updated_at = NOW()
        """,
        (tenant_id, slug, module_key, status, billing_mode, status),
    )

    invalidate_module_entitlement_cache(tenant_id, module_key)

    row = fetch_one(
        """
        SELECT module_key, status, billing_mode, tenant_slug
        FROM public.tenant_module_entitlements
        WHERE tenant_id = %s AND module_key = %s
        """,
        (tenant_id, module_key),
    )
    logger.info(
        "admin modules upsert tenant_id=%s module=%s status=%s billing=%s by=%s",
        tenant_id,
        module_key,
        status,
        billing_mode,
        getattr(current_user, "username", None),
    )
    return jsonify({"ok": True, "entitlement": row})


@bp.route("/api/modules/overview")
@platform_modules_admin
def api_modules_overview():
    """Tüm aktif kiracılar x modül kataloğu matris görünümü."""
    tenants_rows = fetch_all(
        """
        SELECT id, slug, company_name, schema_name, status
        FROM public.tenants
        WHERE status = 'active'
        ORDER BY
            CASE WHEN slug = 'public' THEN 0 ELSE 1 END,
            slug
        """
    ) or []

    all_entitlements = fetch_all(
        """
        SELECT tenant_id, module_key, status, billing_mode, starts_at, ends_at,
               granted_at, revoked_at, source_plan, source_reference
        FROM public.tenant_module_entitlements
        """
    ) or []

    # Harita oluştur: (tenant_id, module_key) -> entitlement
    ent_map: dict[tuple[int, str], dict] = {
        (int(r["tenant_id"]), str(r["module_key"])): r for r in all_entitlements
    }

    catalog = [{"key": k, "label": lab} for k, lab in MODULE_CATALOG]

    tenants_matrix = []
    for t in tenants_rows:
        tid = int(t["id"])
        mod_dict = {}
        for k, lab in MODULE_CATALOG:
            row = ent_map.get((tid, k))
            if row:
                mod_dict[k] = {
                    "module_key": k,
                    "display_name": lab,
                    "status": row["status"],
                    "billing_mode": row["billing_mode"],
                    "granted": True,
                    "starts_at": row["starts_at"].isoformat() if row.get("starts_at") else None,
                    "ends_at": row["ends_at"].isoformat() if row.get("ends_at") else None,
                }
            else:
                mod_dict[k] = {
                    "module_key": k,
                    "display_name": lab,
                    "status": "not_granted",
                    "billing_mode": "included",
                    "granted": False,
                    "starts_at": None,
                    "ends_at": None,
                }

        tenants_matrix.append(
            {
                "id": tid,
                "slug": t["slug"],
                "company_name": t.get("company_name") or "",
                "schema_name": t.get("schema_name") or "",
                "modules": mod_dict,
            }
        )

    return jsonify(
        {
            "ok": True,
            "catalog": catalog,
            "tenants": tenants_matrix,
            "total_tenants": len(tenants_matrix),
        }
    )


@bp.route("/api/modules/bulk-grant", methods=["POST"])
@platform_modules_admin
def api_modules_bulk_grant():
    """Belirtilen modülü belirtilen kiracılara veya tüm aktif kiracılara toplu upsert et."""
    body = request.get_json(silent=True) or {}

    module_key = str(body.get("module_key") or "").strip()
    status = str(body.get("status") or "").strip()
    billing_mode = str(body.get("billing_mode") or "").strip()
    target = body.get("tenant_ids")

    if module_key not in MODULE_KEYS:
        return jsonify({"ok": False, "mesaj": "geçersiz module_key"}), 400
    if status not in STATUSES:
        return jsonify(
            {
                "ok": False,
                "mesaj": "geçersiz status (trial/active/suspended/expired/revoked)",
            }
        ), 400
    if billing_mode not in BILLING_MODES:
        return jsonify({"ok": False, "mesaj": "geçersiz billing_mode"}), 400

    # Hedef kiracıları belirle
    all_active_tenants = fetch_all(
        """
        SELECT id, slug, schema_name
        FROM public.tenants
        WHERE status = 'active'
        """
    ) or []

    if not all_active_tenants:
        return jsonify({"ok": False, "mesaj": "Aktif kiracı bulunamadı."}), 404

    target_tenants = []
    if target == "all" or target == ["all"] or (isinstance(target, str) and target.lower() == "all"):
        target_tenants = all_active_tenants
    elif isinstance(target, list):
        try:
            target_ids = {int(x) for x in target}
        except (TypeError, ValueError):
            return jsonify({"ok": False, "mesaj": "Geçersiz tenant_ids formatı."}), 400
        target_tenants = [t for t in all_active_tenants if int(t["id"]) in target_ids]
    else:
        return jsonify(
            {
                "ok": False,
                "mesaj": "tenant_ids alanı 'all' veya kiracı id listesi olmalıdır.",
            }
        ), 400

    if not target_tenants:
        return jsonify({"ok": False, "mesaj": "Eşleşen aktif kiracı bulunamadı."}), 404

    applied_slugs = []
    applied_ids = []

    for t in target_tenants:
        tid = int(t["id"])
        slug = str(t["slug"])

        execute(
            """
            INSERT INTO public.tenant_module_entitlements (
                tenant_id, tenant_slug, module_key, status, billing_mode,
                source_plan, source_reference, metadata,
                revoked_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s,
                'admin_panel', 'admin_modules_bulk_grant',
                '{"via": "admin/modules/bulk-grant"}'::jsonb,
                CASE WHEN %s = 'revoked' THEN NOW() ELSE NULL END,
                NOW()
            )
            ON CONFLICT (tenant_id, module_key) DO UPDATE SET
                status = EXCLUDED.status,
                billing_mode = EXCLUDED.billing_mode,
                tenant_slug = EXCLUDED.tenant_slug,
                source_plan = EXCLUDED.source_plan,
                source_reference = EXCLUDED.source_reference,
                revoked_at = EXCLUDED.revoked_at,
                updated_at = NOW()
            """,
            (tid, slug, module_key, status, billing_mode, status),
        )

        invalidate_module_entitlement_cache(tid, module_key)
        applied_slugs.append(slug)
        applied_ids.append(tid)

    logger.info(
        "admin modules bulk grant module=%s status=%s billing=%s target_count=%d by=%s",
        module_key,
        status,
        billing_mode,
        len(applied_ids),
        getattr(current_user, "username", None),
    )

    return jsonify(
        {
            "ok": True,
            "mesaj": f"{len(applied_ids)} kiracıya '{module_key}' modülü '{status}' olarak uygulandı.",
            "module_key": module_key,
            "status": status,
            "billing_mode": billing_mode,
            "count": len(applied_ids),
            "applied_tenant_ids": applied_ids,
            "applied_slugs": applied_slugs,
        }
    )


@bp.route(
    "/api/modules/convert-to-ledger-only/<int:tenant_id>",
    methods=["POST"],
)
@platform_modules_admin
def api_convert_to_ledger_only(tenant_id: int):
    """Kiracıyı 'Sadece Payafin Cari' kabuğuna dönüştür (core_erp → revoked).

    Ön koşul: ledger entitlement geçerli (trial|active + tarih penceresi).
    """
    body = request.get_json(silent=True) or {}
    if not body.get("confirm"):
        return jsonify({"ok": False, "mesaj": MSG_CONFIRM_REQUIRED}), 400
    if not body.get("understood"):
        return jsonify({"ok": False, "mesaj": MSG_UNDERSTOOD_REQUIRED}), 400

    tenant = fetch_one(
        """
        SELECT id, slug, status
        FROM public.tenants
        WHERE id = %s AND status = 'active'
        """,
        (tenant_id,),
    )
    if not tenant:
        return jsonify({"ok": False, "mesaj": "Kiracı bulunamadı."}), 404

    slug = str(tenant["slug"] or "")
    if slug.lower() == "public":
        return jsonify({"ok": False, "mesaj": MSG_PUBLIC_FORBIDDEN}), 400

    if not has_module_entitlement(int(tenant_id), "ledger"):
        return jsonify({"ok": False, "mesaj": MSG_LEDGER_REQUIRED}), 400

    _upsert_module_entitlement(
        tenant_id=int(tenant_id),
        slug=slug,
        module_key="core_erp",
        status="revoked",
        billing_mode="included",
        source_reference="admin_convert_to_ledger_only",
        via="admin/modules/convert-to-ledger-only",
    )
    invalidate_module_entitlement_cache(int(tenant_id))

    logger.info(
        "admin convert-to-ledger-only tenant_id=%s slug=%s by=%s",
        tenant_id,
        slug,
        getattr(current_user, "username", None),
    )
    return jsonify(
        {
            "ok": True,
            "mesaj": (
                f"'{slug}' kiracısı Sadece Payafin Cari kabuğuna dönüştürüldü "
                "(core_erp revoked)."
            ),
            "tenant_id": int(tenant_id),
            "slug": slug,
            "shell": "ledger_only",
            "core_erp": "revoked",
        }
    )


@bp.route(
    "/api/modules/restore-full-erp/<int:tenant_id>",
    methods=["POST"],
)
@platform_modules_admin
def api_restore_full_erp(tenant_id: int):
    """Kiracıyı tam ERP kabuğuna geri al (core_erp → active)."""
    body = request.get_json(silent=True) or {}
    if not body.get("confirm"):
        return jsonify({"ok": False, "mesaj": MSG_CONFIRM_REQUIRED}), 400

    tenant = fetch_one(
        """
        SELECT id, slug, status
        FROM public.tenants
        WHERE id = %s AND status = 'active'
        """,
        (tenant_id,),
    )
    if not tenant:
        return jsonify({"ok": False, "mesaj": "Kiracı bulunamadı."}), 404

    slug = str(tenant["slug"] or "")
    if slug.lower() == "public":
        return jsonify({"ok": False, "mesaj": MSG_PUBLIC_FORBIDDEN}), 400

    _upsert_module_entitlement(
        tenant_id=int(tenant_id),
        slug=slug,
        module_key="core_erp",
        status="active",
        billing_mode="included",
        source_reference="admin_restore_full_erp",
        via="admin/modules/restore-full-erp",
    )
    invalidate_module_entitlement_cache(int(tenant_id))

    logger.info(
        "admin restore-full-erp tenant_id=%s slug=%s by=%s",
        tenant_id,
        slug,
        getattr(current_user, "username", None),
    )
    return jsonify(
        {
            "ok": True,
            "mesaj": (
                f"'{slug}' kiracısı Tam ERP kabuğuna geri alındı "
                "(core_erp active)."
            ),
            "tenant_id": int(tenant_id),
            "slug": slug,
            "shell": "full_erp",
            "core_erp": "active",
        }
    )


# ── Erişim Bilgileri (tek kiracı admin username / şifre işlemleri) ─────────────


@bp.route("/api/modules/tenants/<int:tenant_id>/access")
@platform_modules_admin
def api_modules_tenant_access(tenant_id: int):
    """Kiracı admin kullanıcı(lar)ı — şifre gösterilmez."""
    tenant = _resolve_active_tenant(int(tenant_id))
    if not tenant:
        return jsonify({"ok": False, "mesaj": "Kiracı bulunamadı."}), 404

    schema = _schema_ident(tenant.get("schema_name") or "")
    if not schema:
        return jsonify({"ok": False, "mesaj": "Geçersiz kiracı şeması."}), 400

    try:
        admins = _fetch_tenant_admins(schema)
    except Exception as e:
        logger.exception("tenant access list failed schema=%s", schema)
        return jsonify({"ok": False, "mesaj": f"Kullanıcılar okunamadı: {e}"}), 500

    primary = admins[0] if admins else None
    return jsonify(
        {
            "ok": True,
            "tenant": {
                "id": int(tenant["id"]),
                "slug": tenant.get("slug") or "",
                "company_name": tenant.get("company_name") or "",
                "schema_name": schema,
            },
            "admin": (
                {
                    "id": int(primary["id"]),
                    "username": primary.get("username") or "",
                    "full_name": primary.get("full_name") or "",
                    "role": primary.get("role") or "admin",
                    "is_active": bool(primary.get("is_active", True)),
                }
                if primary
                else None
            ),
            "admins": [
                {
                    "id": int(a["id"]),
                    "username": a.get("username") or "",
                    "full_name": a.get("full_name") or "",
                    "role": a.get("role") or "admin",
                    "is_active": bool(a.get("is_active", True)),
                }
                for a in admins
            ],
        }
    )


@bp.route(
    "/api/modules/tenants/<int:tenant_id>/access/send-reset-link",
    methods=["POST"],
)
@platform_modules_admin
def api_modules_tenant_access_send_reset(tenant_id: int):
    """Mevcut forgot-password deseni: token + e-posta (şifre gövdede yok)."""
    body = request.get_json(silent=True) or {}
    if not body.get("confirm"):
        return jsonify({"ok": False, "mesaj": MSG_CONFIRM_REQUIRED}), 400

    tenant = _resolve_active_tenant(int(tenant_id))
    if not tenant:
        return jsonify({"ok": False, "mesaj": "Kiracı bulunamadı."}), 404

    slug = str(tenant.get("slug") or "")
    if slug.lower() == "public":
        return jsonify(
            {"ok": False, "mesaj": "Public platform kullanıcısı için bu akış kullanılamaz."}
        ), 400

    schema = _schema_ident(tenant.get("schema_name") or "")
    if not schema:
        return jsonify({"ok": False, "mesaj": "Geçersiz kiracı şeması."}), 400

    try:
        admins = _fetch_tenant_admins(schema)
    except Exception as e:
        logger.exception("send-reset list failed schema=%s", schema)
        return jsonify({"ok": False, "mesaj": f"Kullanıcılar okunamadı: {e}"}), 500

    if not admins:
        return jsonify({"ok": False, "mesaj": "Bu kiracıda admin kullanıcı yok."}), 404

    admin = admins[0]
    username = str(admin.get("username") or "").strip()
    if "@" not in username:
        return jsonify(
            {
                "ok": False,
                "mesaj": (
                    "Admin kullanıcı adı e-posta değil; "
                    "sıfırlama bağlantısı gönderilemez."
                ),
            }
        ), 400
    if not admin.get("is_active", True):
        return jsonify({"ok": False, "mesaj": "Admin kullanıcı pasif."}), 400

    user_id = int(admin["id"])
    try:
        _ensure_tenant_password_reset_tokens(schema)
        ttl = int(current_app.config.get("PASSWORD_RESET_TTL_SEC", 3600))
        raw_token = secrets.token_urlsafe(32)
        req_ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "")[
            :64
        ]
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                psql.SQL(
                    """
                    UPDATE {}.password_reset_tokens
                    SET used_at = NOW()
                    WHERE user_id = %s AND used_at IS NULL
                    """
                ).format(psql.Identifier(schema)),
                (user_id,),
            )
            cur.execute(
                psql.SQL(
                    """
                    INSERT INTO {}.password_reset_tokens
                        (user_id, token_hash, expires_at, request_ip)
                    VALUES (%s, %s, NOW() + (%s * interval '1 second'), %s)
                    """
                ).format(psql.Identifier(schema)),
                (user_id, _token_hash(raw_token), ttl, req_ip),
            )
        reset_url = _tenant_reset_url(slug, raw_token)
        sent = bool(send_password_reset_email(username, reset_url))
    except Exception as e:
        logger.exception(
            "send-reset failed tenant_id=%s schema=%s", tenant_id, schema
        )
        return jsonify({"ok": False, "mesaj": f"Sıfırlama gönderilemedi: {e}"}), 500

    logger.info(
        "admin access send-reset tenant_id=%s slug=%s user_id=%s by=%s sent=%s",
        tenant_id,
        slug,
        user_id,
        getattr(current_user, "username", None),
        sent,
    )
    if not sent:
        return jsonify(
            {
                "ok": False,
                "mesaj": (
                    "E-posta gönderilemedi (SMTP ayarlarını / mail vault kontrol edin)."
                ),
            }
        ), 502

    return jsonify(
        {
            "ok": True,
            "mesaj": f"Şifre sıfırlama bağlantısı gönderildi: {username}",
            "username": username,
        }
    )


@bp.route(
    "/api/modules/tenants/<int:tenant_id>/access/generate-temp-password",
    methods=["POST"],
)
@platform_modules_admin
def api_modules_tenant_access_generate_temp(tenant_id: int):
    """Geçici şifre üret → hash + security_stamp; plaintext yalnız yanıtta bir kez."""
    body = request.get_json(silent=True) or {}
    if not body.get("confirm"):
        return jsonify({"ok": False, "mesaj": MSG_CONFIRM_REQUIRED}), 400

    email_also = bool(body.get("email_also"))
    tenant = _resolve_active_tenant(int(tenant_id))
    if not tenant:
        return jsonify({"ok": False, "mesaj": "Kiracı bulunamadı."}), 404

    slug = str(tenant.get("slug") or "")
    if slug.lower() == "public":
        return jsonify(
            {"ok": False, "mesaj": "Public platform kullanıcısı için bu akış kullanılamaz."}
        ), 400

    schema = _schema_ident(tenant.get("schema_name") or "")
    if not schema:
        return jsonify({"ok": False, "mesaj": "Geçersiz kiracı şeması."}), 400

    try:
        admins = _fetch_tenant_admins(schema)
    except Exception as e:
        return jsonify({"ok": False, "mesaj": f"Kullanıcılar okunamadı: {e}"}), 500

    if not admins:
        return jsonify({"ok": False, "mesaj": "Bu kiracıda admin kullanıcı yok."}), 404

    admin = admins[0]
    user_id = int(admin["id"])
    username = str(admin.get("username") or "").strip()
    temp = _generate_temp_password()
    hashed = generate_password_hash(temp)
    stamp = generate_security_stamp()

    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                psql.SQL(
                    """
                    UPDATE {}.users
                    SET password_hash = %s, security_stamp = %s
                    WHERE id = %s
                    """
                ).format(psql.Identifier(schema)),
                (hashed, stamp, user_id),
            )
            if cur.rowcount != 1:
                return jsonify({"ok": False, "mesaj": "Şifre güncellenemedi."}), 500
    except Exception as e:
        logger.exception("temp-password update failed tenant_id=%s", tenant_id)
        return jsonify({"ok": False, "mesaj": f"Şifre güncellenemedi: {e}"}), 500

    mail_sent = False
    mail_error = None
    if email_also:
        if "@" not in username:
            mail_error = "Kullanıcı adı e-posta değil; e-posta atlandı."
        else:
            apex = (_tenant_apex_domains() or ("payafin.com",))[0]
            login_url = f"https://{slug}.{apex}/login"
            body_txt = (
                f"Merhaba,\n\n"
                f"Payafin hesabınız ({slug}) için geçici bir şifre oluşturuldu.\n\n"
                f"Kullanıcı adı: {username}\n"
                f"Geçici şifre: {temp}\n"
                f"Giriş: {login_url}\n\n"
                f"Giriş yaptıktan sonra şifrenizi değiştirmenizi öneririz.\n"
            )
            try:
                mail_sent = bool(
                    send_mail(
                        username,
                        "Payafin — Geçici erişim şifresi",
                        body_txt,
                    )
                )
                if not mail_sent:
                    mail_error = "E-posta gönderilemedi (SMTP)."
            except Exception as e:
                mail_error = str(e)

    logger.info(
        "admin access temp-password tenant_id=%s slug=%s user_id=%s by=%s email_also=%s mail_sent=%s",
        tenant_id,
        slug,
        user_id,
        getattr(current_user, "username", None),
        email_also,
        mail_sent,
    )

    return jsonify(
        {
            "ok": True,
            "mesaj": "Geçici şifre oluşturuldu (yalnız bu yanıtta bir kez gösterilir).",
            "username": username,
            "temp_password": temp,
            "email_also": email_also,
            "mail_sent": mail_sent,
            "mail_error": mail_error,
        }
    )

