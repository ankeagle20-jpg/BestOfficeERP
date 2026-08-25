# -*- coding: utf-8 -*-
"""Yeni kiracı şeması provizyonu (Checkpoint 5).

Checkpoint 2'deki elle DDL replay'i tekrar çağrılabilir hale getirir.
public.customers / faturalar satırlarına dokunmaz.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from psycopg2 import sql as psql
from psycopg2.errors import UniqueViolation
from werkzeug.security import generate_password_hash

from auth import generate_security_stamp

from db import (
    _TENANT_SCHEMA_RE,
    db,
    ensure_platform_tenants_table,
    ensure_tenant_module_entitlements_table,
    execute,
    fetch_one,
)
from signup_provision_errors import MSG_GENERIC, sanitize_public_error_message
from tenant_reserved_slugs import RESERVED_TENANT_SLUGS

logger = logging.getLogger(__name__)

BASELINE_MODULE_KEY = "core_erp"
SIGNUP_SELECTABLE_MODULE_KEYS: frozenset[str] = frozenset({"personnel", "randevu"})

BACKUP_TABLE = "musteri_tahsilat_panel_detay_backup_20260617"
PLATFORM_STRIP_TABLES = (
    "tenants",
    "pricing_regions",
    "pricing_tiers",
    "pricing_overage_rules",
    "tenant_module_entitlements",
    "module_pricing_tiers",
    "module_pricing_leads",
    "platform_credentials",
)
_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_ADMIN_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DEFAULT_DUMP = (
    Path(__file__).resolve().parent
    / "tenant_ddl"
    / "schema_only_public.sql"
)

class TenantProvisionError(RuntimeError):
    """Provizyon durdu (mevcut şema/kayıt, geçersiz slug, DDL hatası)."""


class TenantSlugReserveError(RuntimeError):
    """Slug rezervasyonu başarısız (çakışma veya geçersiz slug)."""


class TenantSlugConflictError(TenantSlugReserveError):
    """Slug veya şema adı zaten alınmış."""


def schema_name_for_slug(slug: str) -> str:
    return "tenant_" + str(slug).strip().lower()


def _valid_admin_username(user: str) -> bool:
    u = str(user or "").strip()
    if not u:
        return False
    if _ADMIN_EMAIL_RE.fullmatch(u):
        return True
    return bool(_SLUG_RE.fullmatch(u.replace("-", "_")))


def _fetch_tenant_row(slug: str, schema: str) -> dict | None:
    return fetch_one(
        "SELECT id, slug, schema_name, plan, status FROM public.tenants "
        "WHERE slug=%s OR schema_name=%s",
        (slug, schema),
    )


def reserve_tenant_slug(
    slug: str,
    *,
    company_name: str | None = None,
    country_code: str | None = None,
    plan: str = "trial",
) -> dict:
    """Asenkron signup: slug'ı hemen status='provisioning' ile rezerve et.

    UNIQUE(slug)/UNIQUE(schema_name) yarışını DB'ye bırakır.
    """
    slug = _normalize_slug(slug)
    schema = schema_name_for_slug(slug)
    plan_s = str(plan or "trial").strip() or "trial"
    if not re.fullmatch(r"[a-z0-9_]{1,32}", plan_s):
        raise TenantSlugReserveError("geçersiz plan")

    ensure_platform_tenants_table()

    if _schema_exists(schema):
        raise TenantSlugConflictError(f"şema zaten var: {schema}")

    existing = _fetch_tenant_row(slug, schema)
    if existing:
        raise TenantSlugConflictError(f"slug zaten kayıtlı: {slug}")

    company = (company_name or "").strip() or None
    country = (country_code or "").strip().upper() or None
    if country and (len(country) != 2 or not country.isalpha()):
        raise TenantSlugReserveError("geçersiz country_code")

    try:
        execute(
            """
            INSERT INTO public.tenants
                (slug, schema_name, plan, status, company_name, country_code)
            VALUES (%s, %s, %s, 'provisioning', %s, %s)
            """,
            (slug, schema, plan_s, company, country),
        )
    except UniqueViolation as e:
        raise TenantSlugConflictError(f"slug çakışması: {slug}") from e

    row = fetch_one(
        "SELECT id, slug, schema_name, plan, status, company_name, country_code "
        "FROM public.tenants WHERE slug=%s",
        (slug,),
    )
    return {"ok": True, "slug": slug, "schema_name": schema, "status": "provisioning", "tenant": row}


def mark_tenant_provision_failed(
    slug: str,
    *,
    reason: str | None = None,
    error_message: str | None = None,
) -> None:
    """Arka plan provizyon hatasında status='failed' + kullanıcıya güvenli özet."""
    slug_s = str(slug or "").strip().lower()
    if not slug_s:
        return
    public_msg = sanitize_public_error_message(error_message or reason) or MSG_GENERIC
    try:
        execute(
            """
            UPDATE public.tenants
            SET status = 'failed', error_message = %s
            WHERE slug = %s AND status = 'provisioning'
            """,
            (public_msg, slug_s),
        )
    except Exception:
        logger.exception(
            "mark_tenant_provision_failed slug=%s reason=%s", slug_s, reason
        )


def _normalize_slug(slug: str) -> str:
    s = str(slug or "").strip().lower()
    if not s or not _SLUG_RE.fullmatch(s):
        raise TenantProvisionError("geçersiz slug")
    if s in RESERVED_TENANT_SLUGS:
        raise TenantProvisionError("rezerve slug")
    schema = schema_name_for_slug(s)
    if not _TENANT_SCHEMA_RE.fullmatch(schema):
        raise TenantProvisionError("slug _TENANT_SCHEMA_RE formatına uymuyor")
    return s


def _is_executable_statement(stmt: str) -> bool:
    """Yorum/boşluk-only parçaları atla (psql bunları sessizce geçer)."""
    body = stmt.strip()
    if not body:
        return False
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("--"):
            continue
        return True
    return False


def _split_ddl_statements(sql: str) -> list[str]:
    """pg_dump DDL metnini dollar-quote güvenli şekilde statement'lara böl."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_line_comment = False
    dollar_tag: str | None = None
    in_single = False
    in_double = False

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if dollar_tag is not None:
            if ch == "$":
                end = f"${dollar_tag}$"
                if sql.startswith(end, i):
                    buf.append(end)
                    i += len(end)
                    dollar_tag = None
                    continue
            buf.append(ch)
            i += 1
            continue

        if in_single:
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            buf.extend((ch, nxt))
            i += 2
            continue

        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue

        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue

        if ch == "$":
            j = i + 1
            while j < n and sql[j] != "$" and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                dollar_tag = sql[i + 1 : j]
                buf.append(sql[i : j + 1])
                i = j + 1
                continue

        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _execute_ddl_script(sql_text: str) -> None:
    """Dönüştürülmüş kiracı DDL'ini psycopg2 ile uygula (tek transaction, hata → rollback)."""
    statements = [s for s in _split_ddl_statements(sql_text) if _is_executable_statement(s)]
    if not statements:
        raise TenantProvisionError("uygulanacak DDL statement yok")
    idx = 0
    try:
        with db() as conn:
            cur = conn.cursor()
            for idx, stmt in enumerate(statements, start=1):
                cur.execute(stmt)
    except Exception as e:
        raise TenantProvisionError(
            f"DDL uygulama hatası (statement {idx}/{len(statements)}): {e}"
        ) from e


def _apply_tenant_ddl(sql_path: Path) -> None:
    sql_text = sql_path.read_text(encoding="utf-8")
    _execute_ddl_script(sql_text)


def _strip_named_table_block(text: str, table: str) -> str:
    pat = (
        r"\n--\n-- Name: "
        + re.escape(table)
        + r"; Type: TABLE; Schema: public; Owner: -\n--\n\n"
        r"CREATE TABLE public\."
        + re.escape(table)
        + r" \([\s\S]*?\);\n"
    )
    return re.sub(pat, "\n", text, count=1)


def transform_public_dump(src: str, schema: str) -> str:
    """Checkpoint 0 public dump → kiracı şeması DDL (yedek tablo ve platform tablosu hariç)."""
    if not _TENANT_SCHEMA_RE.fullmatch(schema):
        raise TenantProvisionError("geçersiz hedef şema")
    text = src.replace("\r\n", "\n")
    text = re.sub(r"^\\restrict .*\n", "", text, flags=re.M)
    text = re.sub(r"^\\unrestrict .*\n", "", text, flags=re.M)
    text = re.sub(
        r"\n--\n-- Name: public; Type: SCHEMA; Schema: -; Owner: -\n--\n\nCREATE SCHEMA public;\n",
        "\n",
        text,
        count=1,
    )
    text = _strip_named_table_block(text, BACKUP_TABLE)
    for platform_table in PLATFORM_STRIP_TABLES:
        text = _strip_named_table_block(text, platform_table)
    if BACKUP_TABLE in text:
        raise TenantProvisionError("yedek tablo hâlâ dönüştürülmüş DDL içinde")
    for platform_table in PLATFORM_STRIP_TABLES:
        if re.search(rf"CREATE TABLE public\.{re.escape(platform_table)}\b", text):
            raise TenantProvisionError(
                f"public.{platform_table} kiracı dump'ına sızdı"
            )
    if "information_schema" in text.lower():
        raise TenantProvisionError("beklenmeyen information_schema")
    text = text.replace("public.", schema + ".")
    if "CREATE EXTENSION" in text:
        raise TenantProvisionError("dump CREATE EXTENSION içeriyor")
    text = text.replace(
        "LANGUAGE sql\n    AS $_$",
        f"LANGUAGE sql\n    SET search_path TO {schema}, pg_catalog\n    AS $_$",
    )
    text = text.replace(
        "LANGUAGE plpgsql\n    AS $$",
        f"LANGUAGE plpgsql\n    SET search_path TO {schema}, pg_catalog\n    AS $$",
    )

    def qualify_fn_body(m: re.Match) -> str:
        body = m.group(0)
        repls = (
            ("JOIN customers ", f"JOIN {schema}.customers "),
            ("FROM customers\n", f"FROM {schema}.customers\n"),
            ("UPDATE customers\n", f"UPDATE {schema}.customers\n"),
            ("FROM faturalar\n", f"FROM {schema}.faturalar\n"),
            ("FROM faturalar ", f"FROM {schema}.faturalar "),
            ("FROM tahsilatlar\n", f"FROM {schema}.tahsilatlar\n"),
            ("FROM tahsilatlar ", f"FROM {schema}.tahsilatlar "),
            ("FROM musteri_kyc ", f"FROM {schema}.musteri_kyc "),
        )
        for a, b in repls:
            body = body.replace(a, b)
        return body

    text = re.sub(
        rf"CREATE FUNCTION {re.escape(schema)}\.[^;]+?AS \$_\$[\s\S]+?\$_\$;",
        qualify_fn_body,
        text,
    )
    text = re.sub(
        rf"CREATE FUNCTION {re.escape(schema)}\.[^;]+?AS \$\$[\s\S]+?\$\$;",
        qualify_fn_body,
        text,
    )
    return text


def _schema_exists(schema: str) -> bool:
    row = fetch_one("SELECT 1 AS ok FROM pg_namespace WHERE nspname=%s", (schema,))
    return bool(row)


def _tenants_row_exists(slug: str, schema: str) -> bool:
    return bool(_fetch_tenant_row(slug, schema))


def _insert_admin(schema: str, username: str, password: str, full_name: str) -> int:
    hashed = generate_password_hash(password)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                "INSERT INTO {}.users "
                "(username, password_hash, full_name, role, is_active, security_stamp) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id"
            ).format(psql.Identifier(schema)),
            (username, hashed, full_name, "admin", True, generate_security_stamp()),
        )
        row = cur.fetchone()
    if not row:
        raise TenantProvisionError("admin kullanıcısı yazılamadı")
    return int(row["id"] if isinstance(row, dict) else row[0])


def _register_tenant_user_lookup(slug: str, username: str) -> None:
    """Signup/login yönlendirme indeksine e-posta kaydı (yalnız @ içeren admin)."""
    from db import ensure_tenant_user_lookup_table

    email = str(username or "").strip().lower()
    if "@" not in email or not _ADMIN_EMAIL_RE.fullmatch(email):
        return
    ensure_tenant_user_lookup_table()
    try:
        execute(
            """
            INSERT INTO public.tenant_user_lookup (email, tenant_slug)
            VALUES (%s, %s)
            """,
            (email, slug),
        )
    except UniqueViolation:
        logger.warning(
            "tenant_user_lookup email already registered email=%s slug=%s",
            email,
            slug,
        )
    except Exception:
        logger.exception("tenant_user_lookup insert failed slug=%s", slug)


def normalize_signup_selected_modules(selected_module_keys) -> list[str]:
    """Signup body selected_modules → izinli, benzersiz module_key listesi."""
    if not selected_module_keys:
        return []
    out: list[str] = []
    for raw in selected_module_keys:
        mk = str(raw or "").strip().lower()
        if mk in SIGNUP_SELECTABLE_MODULE_KEYS and mk not in out:
            out.append(mk)
    return out


def normalize_module_tier_preferences(prefs) -> dict[str, str]:
    """Signup body module_tier_preferences → {module_key: tier_key} (geçersizler elenir)."""
    if not isinstance(prefs, dict):
        return {}
    out: dict[str, str] = {}
    for raw_mk, raw_tier in prefs.items():
        mk = str(raw_mk or "").strip().lower()
        tier = str(raw_tier or "").strip().lower()
        if mk not in SIGNUP_SELECTABLE_MODULE_KEYS:
            continue
        if not re.fullmatch(r"[a-z0-9_]{1,32}", tier):
            continue
        if tier == "enterprise":
            continue
        out[mk] = tier
    return out


def grant_default_module_entitlements(
    tenant_id: int,
    tenant_slug: str,
    selected_module_keys: list | tuple | None = None,
    module_tier_preferences: dict | None = None,
) -> int:
    """Yeni kiracı: core_erp (baseline) + seçilen modüller için trial entitlement."""
    import json

    ensure_tenant_module_entitlements_table()
    tid = int(tenant_id)
    slug = str(tenant_slug).strip()
    selected = normalize_signup_selected_modules(selected_module_keys)
    tier_prefs = normalize_module_tier_preferences(module_tier_preferences)

    modules_to_grant: list[tuple[str, str]] = [
        (BASELINE_MODULE_KEY, "included"),
    ]
    for mk in selected:
        modules_to_grant.append((mk, "standalone"))

    inserted = 0
    for module_key, billing_mode in modules_to_grant:
        before = fetch_one(
            """
            SELECT 1 AS ok
            FROM public.tenant_module_entitlements
            WHERE tenant_id = %s AND module_key = %s
            """,
            (tid, module_key),
        )
        meta: dict = {
            "note": "Signup provisioning — baseline + selected modules",
        }
        if module_key in tier_prefs:
            meta["selected_tier"] = tier_prefs[module_key]
        execute(
            """
            INSERT INTO public.tenant_module_entitlements (
                tenant_id, tenant_slug, module_key, status, billing_mode,
                source_plan, source_reference, metadata
            )
            VALUES (
                %s, %s, %s, 'trial', %s,
                'trial', 'signup_provision',
                %s::jsonb
            )
            ON CONFLICT (tenant_id, module_key) DO NOTHING
            """,
            (tid, slug, module_key, billing_mode, json.dumps(meta, ensure_ascii=False)),
        )
        after = fetch_one(
            """
            SELECT 1 AS ok
            FROM public.tenant_module_entitlements
            WHERE tenant_id = %s AND module_key = %s
            """,
            (tid, module_key),
        )
        if after and not before:
            inserted += 1

    logger.info(
        "grant_default_module_entitlements tenant_id=%s slug=%s selected=%s tiers=%s inserted=%s",
        tid,
        slug,
        selected,
        tier_prefs,
        inserted,
    )
    return inserted


def provision_new_tenant(
    slug: str,
    *,
    plan: str = "trial",
    admin_username: str | None = None,
    admin_password: str | None = None,
    admin_full_name: str | None = None,
    dump_path: Path | None = None,
    allow_existing_provisioning_row: bool = False,
    selected_module_keys: list | tuple | None = None,
    module_tier_preferences: dict | None = None,
) -> dict:
    """Yeni kiracı: şema + DDL replay + admin + public.tenants kaydı.

    slug veya şema zaten varsa hata verir (ikinci kez provision yok).
    allow_existing_provisioning_row=True: status='provisioning' satırı varsa
    devam eder ve sonunda INSERT yerine UPDATE status='active' yapar.
    """
    slug = _normalize_slug(slug)
    schema = schema_name_for_slug(slug)
    plan_s = str(plan or "trial").strip() or "trial"
    if not re.fullmatch(r"[a-z0-9_]{1,32}", plan_s):
        raise TenantProvisionError("geçersiz plan")
    user = (admin_username or (slug + "_admin")).strip()
    if not _valid_admin_username(user):
        raise TenantProvisionError("geçersiz admin kullanıcı adı")
    password = admin_password
    if not password or len(str(password)) < 10:
        raise TenantProvisionError("admin_password en az 10 karakter olmalı")
    full_name = (admin_full_name or (slug + " Admin")).strip()

    src_path = Path(dump_path) if dump_path else _DEFAULT_DUMP
    if not src_path.is_file():
        raise TenantProvisionError(f"DDL şablonu yok: {src_path}")

    ensure_platform_tenants_table()

    existing_row = _fetch_tenant_row(slug, schema)
    resume_provisioning = False
    if allow_existing_provisioning_row and existing_row:
        if existing_row.get("status") == "provisioning":
            resume_provisioning = True
        else:
            raise TenantProvisionError(
                f"kiracı zaten var (slug={slug} status={existing_row.get('status')})"
            )
    elif _schema_exists(schema) or existing_row:
        raise TenantProvisionError(
            f"kiracı zaten var (slug={slug} schema={schema}); ikinci provision engellendi"
        )

    src = src_path.read_text(encoding="utf-8")
    body = transform_public_dump(src, schema)
    header = (
        "-- Checkpoint 5 generated tenant DDL (no COPY / no public DML)\n"
        f"CREATE SCHEMA {schema} AUTHORIZATION CURRENT_USER;\n"
    )
    out_dir = Path(__file__).resolve().parent / "_tmp_multitenancy_checkpoint5"
    out_dir.mkdir(parents=True, exist_ok=True)
    sql_path = out_dir / f"{schema}.sql"
    sql_path.write_text(header + body, encoding="utf-8")

    try:
        if not resume_provisioning or not _schema_exists(schema):
            _apply_tenant_ddl(sql_path)
        admin_id = _insert_admin(schema, user, str(password), full_name)
        if resume_provisioning:
            execute(
                """
                UPDATE public.tenants
                SET plan = %s, status = 'active', error_message = NULL
                WHERE slug = %s AND status = 'provisioning'
                """,
                (plan_s, slug),
            )
        else:
            execute(
                """
                INSERT INTO public.tenants (slug, schema_name, plan, status)
                VALUES (%s, %s, %s, 'active')
                """,
                (slug, schema, plan_s),
            )
        _register_tenant_user_lookup(slug, user)
    except Exception:
        # Kısmi şema bırakılabilir; tekrar çağrı mevcut şema yüzünden durur (fail-closed).
        raise

    row = fetch_one("SELECT id, slug, schema_name, plan, status FROM public.tenants WHERE slug=%s", (slug,))
    if not row:
        raise TenantProvisionError("kiracı kaydı okunamadı")
    ent_inserted = grant_default_module_entitlements(
        int(row["id"]),
        slug,
        selected_module_keys,
        module_tier_preferences,
    )
    return {
        "ok": True,
        "slug": slug,
        "schema_name": schema,
        "plan": plan_s,
        "admin_username": user,
        "admin_id": admin_id,
        "tenant": row,
        "ddl_path": str(sql_path),
        "module_entitlements_inserted": ent_inserted,
        "selected_module_keys": normalize_signup_selected_modules(selected_module_keys),
        "module_tier_preferences": normalize_module_tier_preferences(module_tier_preferences),
    }
