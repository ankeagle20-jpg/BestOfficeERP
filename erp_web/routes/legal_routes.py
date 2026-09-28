# -*- coding: utf-8 -*-
"""Payafin / Ofisbir yasal sayfalar. Ödeme ve kiracı mantığına bağlı değildir."""
from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("legal", __name__)


def _page(title: str, body_template: str, kicker: str = ""):
    return render_template(
        "legal/page.html",
        page_title=title,
        page_kicker=kicker,
        body_template=body_template,
    )


@bp.route("/sozlesme")
def sozlesme():
    return _page(
        "Mesafeli Hizmet, Abonelik ve Yazılım Kullanım Sözleşmesi",
        "legal/_sozlesme_body.html",
    )


@bp.route("/iptal-iade")
def iptal_iade():
    return _page("İptal ve İade Politikası", "legal/_iptal_iade_body.html")


@bp.route("/teslimat")
def teslimat():
    return _page("Teslimat ve İfa Koşulları", "legal/_teslimat_body.html")


@bp.route("/gizlilik")
def gizlilik():
    return _page(
        "Gizlilik Politikası",
        "legal/_gizlilik_body.html",
        "6698 sayılı KVKK kapsamında aydınlatma özeti.",
    )
