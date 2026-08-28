# -*- coding: utf-8 -*-
"""Admin: Payafin platform modül entitlement yönetimi (yalnız public host)."""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, g, jsonify, render_template, request
from flask_login import current_user

from auth import admin_gerekli
from db import execute, fetch_all, fetch_one
from tenant_module_access import invalidate_module_entitlement_cache

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

