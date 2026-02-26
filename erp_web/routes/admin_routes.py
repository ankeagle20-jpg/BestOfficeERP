from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from auth import (admin_gerekli, kullanici_olustur, tum_kullanicilar,
                  kullanici_sil, sifre_degistir, ROLLER)
from db import execute, fetch_one

bp = Blueprint("admin", __name__)


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
    execute("UPDATE users SET rol=%s WHERE id=%s", (yeni_rol, uid))
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
