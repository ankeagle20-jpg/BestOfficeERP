# -*- coding: utf-8 -*-
"""Kiracı slug / subdomain rezervasyonları — provisioning + host identity ortak liste."""
from __future__ import annotations

# Kayıt, DNS, platform ve Postgres sistem adları; signup + tenant_identity + provisioning.
RESERVED_TENANT_SLUGS = frozenset(
    {
        "www",
        "app",
        "api",
        "admin",
        "login",
        "kayit",
        "signup",
        "public",
        "postgres",
        "pg_catalog",
        "information_schema",
        "pg_toast",
        "test",
        "demo",
        "mail",
        "ftp",
    }
)
