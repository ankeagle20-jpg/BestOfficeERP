# -*- coding: utf-8 -*-
"""Payafin Cari (module_key=ledger) — Aşama L0 iskelet (boş sayfa, entitlement guard)."""
from __future__ import annotations

from flask import Blueprint, render_template

from auth import giris_gerekli
from tenant_module_access import module_required

bp = Blueprint("ledger", __name__)


@bp.route("/")
@giris_gerekli
@module_required("ledger")
def index():
    """L0: finansal tablo yok — yalnızca placeholder."""
    return render_template("ledger/index.html")
