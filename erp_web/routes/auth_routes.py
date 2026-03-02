import os
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, logout_user, current_user
from auth import giris_yap, sifre_degistir
from db import fetch_one, execute
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("auth", __name__)


@bp.route("/setup-admin")
def setup_admin():
    """
    Tek seferlik: Render/Production'da admin yoksa oluşturur.
    Kullanım: https://bestofficeerp.onrender.com/setup-admin?secret=BURAYA_ENV_DEKI_DEGER
    Render'da Environment'a SETUP_SECRET=rastgele_bir_anahtar ekle, sonra bu URL'yi aç.
    """
    secret = request.args.get("secret", "").strip()
    if not secret or secret != os.environ.get("SETUP_SECRET", ""):
        return "<h1>403 Forbidden</h1><p>Geçersiz veya eksik secret.</p>", 403
    try:
        admin = fetch_one("SELECT id FROM users WHERE username=%s", ("admin",))
        reset = request.args.get("reset") == "1"
        if admin and reset:
            hashed = generate_password_hash("admin123")
            execute("UPDATE users SET password_hash=%s, is_active=TRUE WHERE username=%s", (hashed, "admin"))
            return (
                "<h1>Admin şifresi sıfırlandı</h1><p><b>Giriş:</b> admin / admin123</p>"
                "<p><a href='/login'>Giriş sayfasına git</a></p>"
            )
        if admin:
            return (
                "<h1>Admin zaten var</h1><p>Giriş: <b>admin</b> / (mevcut şifren). "
                "Şifreni unuttuysan aynı adrese <code>?secret=...&reset=1</code> ekleyerek şifreyi admin123 yap.</p>"
            )
        hashed = generate_password_hash("admin123")
        execute(
            "INSERT INTO users (username, password_hash, full_name, role, is_active) VALUES (%s, %s, %s, %s, %s)",
            ("admin", hashed, "Sistem Yöneticisi", "admin", True),
        )
        return (
            "<h1>Admin oluşturuldu</h1><p><b>Giriş:</b> admin / admin123</p>"
            "<p><a href='/login'>Giriş sayfasına git</a></p>"
        )
    except Exception as e:
        return f"<h1>Hata</h1><pre>{e}</pre>", 500


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        user = giris_yap(username, password)
        
        if user:
            flash(f"Hoş geldiniz, {user.full_name}!", "success")
            # next: GET'ten veya form POST'tan (mobilde giriş sonrası /m/dashboard'a dönmek için)
            next_url = (request.args.get("next") or request.form.get("next") or "").strip()
            # Sadece güvenli (relative) next kabul et; açık yönlendirme engelle
            if next_url and not next_url.startswith("//") and next_url.startswith("/"):
                from urllib.parse import urlparse
                p = urlparse(next_url)
                if not p.netloc:
                    return redirect(next_url)
            return redirect(url_for("index"))
        else:
            flash("Kullanıcı adı veya şifre hatalı!", "danger")
    
    return render_template("login.html")

@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Güvenli çıkış yapıldı.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Kullanıcı profil sayfası — şifre değiştirme."""
    if request.method == "POST":
        current_pwd = request.form.get("current_password", "")
        new_pwd = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not current_pwd or not new_pwd or not confirm:
            flash("Tüm alanları doldurun.", "danger")
            return redirect(url_for("auth.profile"))

        if new_pwd != confirm:
            flash("Yeni şifre ve onayı eşleşmiyor.", "danger")
            return redirect(url_for("auth.profile"))

        if len(new_pwd) < 6:
            flash("Yeni şifre en az 6 karakter olmalıdır.", "danger")
            return redirect(url_for("auth.profile"))

        # doğrula
        row = fetch_one("SELECT password_hash FROM users WHERE id = %s", (current_user.id,))
        if not row or not check_password_hash(row.get("password_hash",""), current_pwd):
            flash("Mevcut şifre hatalı.", "danger")
            return redirect(url_for("auth.profile"))

        sifre_degistir(current_user.id, new_pwd)
        flash("Şifreniz başarılı şekilde değiştirildi.", "success")
        return redirect(url_for("index"))

    return render_template("auth/profile.html")