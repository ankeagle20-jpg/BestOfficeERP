# -*- coding: utf-8 -*-
"""Yerel süreç canlı veritabanına yazmasın.

RENDER doluysa (Frankfurt) bu katman devreye girmez.
Host değeri ve sırlar loglanmaz.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from urllib.parse import urlsplit

import psycopg2.extras
from psycopg2.extras import RealDictCursor

# Canlı DB host'unun sha256'sı. Düz metin host burada durmaz.
_PROD_HOST_SHA256 = "eb0d823953fe3850583b80753a529656991e2c14b361b421737545498c2da6b0"

_MSG = (
    "Yerel ortam canlı veritabanına yazamaz. Okuma serbest. "
    "Bilinçli yazma için süreci ALLOW_PROD_WRITE=1 ile başlatın."
)
_ALLOW_WARN = (
    "UYARI: ALLOW_PROD_WRITE açık. Bu yerel süreç canlı veritabanına yazabilir."
)

_allow_warned = False
_block_announced = False

_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|ALTER|DROP|GRANT|REVOKE|"
    r"COPY|VACUUM|REINDEX|CLUSTER|COMMENT|DO|CALL|REFRESH|LOCK|EXECUTE|"
    r"ANALYZE|SECURITY|REASSIGN|IMPORT|LOAD|CHECKPOINT)\b",
    re.IGNORECASE,
)
_FOR_LOCK_RE = re.compile(
    r"\bFOR\s+(?:NO\s+KEY\s+UPDATE|KEY\s+SHARE|UPDATE|SHARE)\b",
    re.IGNORECASE,
)
_READ_START_RE = re.compile(
    r"(?is)^(SELECT|SHOW|VALUES|TABLE|SET|RESET|BEGIN|START|COMMIT|ROLLBACK|"
    r"SAVEPOINT|RELEASE|DECLARE|FETCH|MOVE|CLOSE|LISTEN|UNLISTEN|NOTIFY|DISCARD|"
    r"PREPARE|DEALLOCATE)\b"
)


class ProdWriteBlocked(RuntimeError):
    """Yerel süreç canlı veritabanına yazmaya kalktı."""


def _truthy_render() -> bool:
    return bool((os.environ.get("RENDER") or "").strip())


def _allow_prod_write() -> bool:
    return (os.environ.get("ALLOW_PROD_WRITE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def db_host_label() -> str:
    """Bağlanılacak host. Değer çağıran tarafından loglanmamalı."""
    dsn = (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL") or "").strip()
    host = ""
    if dsn:
        if "://" not in dsn:
            dsn = "postgresql://" + dsn
        try:
            host = urlsplit(dsn).hostname or ""
        except Exception:
            host = ""
    if not host:
        raw = (os.environ.get("DB_HOST") or "").strip()
        if "://" in raw:
            try:
                host = urlsplit(raw).hostname or ""
            except Exception:
                host = ""
        else:
            host = raw.split(":")[0]
    return host.strip().lower().rstrip(".")


def host_is_prod(host: str | None = None) -> bool:
    label = (host if host is not None else db_host_label()).strip().lower().rstrip(".")
    if not label or not _PROD_HOST_SHA256:
        return False
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return digest == _PROD_HOST_SHA256


def guard_mode(host: str | None = None) -> str:
    """off: dokunma. block: yazmayı kes. allow: izinli yazma."""
    if _truthy_render():
        return "off"
    if not host_is_prod(host):
        return "off"
    if _allow_prod_write():
        global _allow_warned
        if not _allow_warned:
            _allow_warned = True
            print(_ALLOW_WARN, file=sys.stderr, flush=True)
        return "allow"
    return "block"


def _strip_literals(sql: str) -> str:
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            nxt = sql.find("\n", i)
            i = n if nxt < 0 else nxt
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if m:
                tag = m.group(0)
                end = sql.find(tag, i + len(tag))
                i = n if end < 0 else end + len(tag)
                out.append(" ")
                continue
        if ch == '"':
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _statement_verb(stmt: str) -> str | None:
    """Yazma ise fiil, okuma ise None."""
    s = _FOR_LOCK_RE.sub(" ", stmt).strip().lstrip("(").strip()
    if not s:
        return None
    if re.match(r"(?is)^EXPLAIN\b", s):
        rest = re.sub(r"(?is)^EXPLAIN\b(\s*\([^)]*\))?", " ", s, count=1)
        rest = re.sub(r"(?is)^(\s*(ANALYZE|VERBOSE)\b)+", " ", rest)
        return _statement_verb(rest)
    if re.match(r"(?is)^WITH\b", s):
        m = _WRITE_RE.search(s)
        return m.group(1).upper() if m else None
    if _READ_START_RE.match(s):
        if re.match(r"(?is)^SELECT\b", s) and re.search(r"(?is)\bINTO\b", s):
            return "SELECT INTO"
        m = _WRITE_RE.search(s)
        return m.group(1).upper() if m else None
    m = _WRITE_RE.match(s)
    if m:
        return m.group(1).upper()
    return None


def write_verb(sql: str) -> str | None:
    text = _strip_literals(sql or "")
    for part in text.split(";"):
        verb = _statement_verb(part)
        if verb:
            return verb
    return None


def _sql_text(query, cursor=None) -> str:
    if query is None:
        return ""
    if isinstance(query, bytes):
        return query.decode("utf-8", "replace")
    if hasattr(query, "as_string"):
        try:
            ctx = cursor.connection if cursor is not None else None
            if ctx is not None:
                return query.as_string(ctx)
        except Exception:
            return "/*unparsed*/"
        return "/*unparsed*/"
    return str(query)


def refuse_write_sql(query, cursor=None) -> None:
    if guard_mode() != "block":
        return
    text = _sql_text(query, cursor)
    if text == "/*unparsed*/":
        _raise_blocked("SQL")
    verb = write_verb(text)
    if verb:
        _raise_blocked(verb)


def ensure_write_allowed() -> None:
    """Ham psycopg2 kullanan betikler bağlanmadan önce çağırsın."""
    if guard_mode() == "block":
        _raise_blocked("YAZMA")


def _raise_blocked(verb: str) -> None:
    global _block_announced
    if not _block_announced:
        _block_announced = True
        print(_MSG, file=sys.stderr, flush=True)
    raise ProdWriteBlocked(_MSG + f" ({verb})")


class ProdWriteCursor(RealDictCursor):
    """Yalnızca guard_mode() == block iken bağlanır."""

    def execute(self, query, vars=None):
        refuse_write_sql(query, self)
        return super().execute(query, vars)

    def executemany(self, query, vars_list):
        refuse_write_sql(query, self)
        return super().executemany(query, vars_list)

    def callproc(self, procname, parameters=None):
        if guard_mode() == "block":
            _raise_blocked("CALL")
        return super().callproc(procname, parameters)

    def copy_expert(self, sql, file, size=8192):
        if guard_mode() == "block":
            _raise_blocked("COPY")
        return super().copy_expert(sql, file, size)

    def copy_from(self, file, table, sep="\t", null="\\N", size=8192, columns=None):
        if guard_mode() == "block":
            _raise_blocked("COPY")
        return super().copy_from(file, table, sep, null, size, columns)


def cursor_factory_for_runtime():
    """Frankfurt ve yerel-yerel host: stok RealDictCursor."""
    mode = guard_mode()
    if mode == "block":
        return ProdWriteCursor
    return psycopg2.extras.RealDictCursor
