from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, g
from functools import wraps
from auth import (admin_gerekli, kullanici_olustur, tum_kullanicilar,
                  kullanici_sil, sifre_degistir, ROLLER)
from db import execute, fetch_one

bp = Blueprint("admin", __name__)

MSG_PLATFORM_ONLY = (
    "Platform yönetimi yalnızca ana (public) host'ta kullanılabilir."
)


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_admin_guard(f):
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


@bp.route("/yonetim")
@platform_admin_guard
def yonetim_hub():
    """Platform yönetim hub sayfası — tüm admin panellerine merkezi erişim."""
    return render_template("admin/yonetim.html")


@bp.route("/")
@admin_gerekli
def index():
    kullanicilar = tum_kullanicilar()
    return render_template("admin/kullanicilar.html",
                           kullanicilar=kullanicilar, roller=ROLLER)


@bp.route("/kullanici/ekle", methods=["POST"])
@admin_gerekli
def kullanici_ekle():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    email    = request.form.get("email", "").strip()
    rol      = request.form.get("rol", "goruntuleme")

    if not username or not password:
        flash("Kullanıcı adı ve şifre zorunludur.", "danger")
        return redirect(url_for("admin.index"))

    if len(password) < 6:
        flash("Şifre en az 6 karakter olmalıdır.", "danger")
        return redirect(url_for("admin.index"))

    sonuc = kullanici_olustur(username, password, email, rol)
    if sonuc["ok"]:
        flash(f"✓ '{username}' kullanıcısı oluşturuldu.", "success")
    else:
        flash(f"Hata: {sonuc['hata']}", "danger")
    return redirect(url_for("admin.index"))


@bp.route("/kullanici/<int:uid>/sil", methods=["POST"])
@admin_gerekli
def kullanici_sil_route(uid):
    from flask_login import current_user
    if uid == current_user.id:
        flash("Kendinizi silemezsiniz.", "danger")
        return redirect(url_for("admin.index"))
    kullanici_sil(uid)
    flash("Kullanıcı deaktive edildi.", "info")
    return redirect(url_for("admin.index"))


@bp.route("/kullanici/<int:uid>/rol", methods=["POST"])
@admin_gerekli
def rol_degistir(uid):
    yeni_rol = request.form.get("rol")
    if yeni_rol not in ROLLER:
        flash("Geçersiz rol.", "danger")
        return redirect(url_for("admin.index"))
    execute("UPDATE users SET role=%s WHERE id=%s", (yeni_rol, uid))
    flash("Rol güncellendi.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/kullanici/<int:uid>/sifre", methods=["POST"])
@admin_gerekli
def sifre_sifirla(uid):
    yeni = request.form.get("yeni_sifre", "")
    if len(yeni) < 6:
        flash("Şifre en az 6 karakter.", "danger")
        return redirect(url_for("admin.index"))
    sifre_degistir(uid, yeni)
    flash("Şifre güncellendi.", "success")
    return redirect(url_for("admin.index"))
