"""
OFİSBİR ERP — Web Uygulaması
Flask + Supabase PostgreSQL
"""

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user, logout_user
from config import Config
from auth import login_manager, giris_yap, kullanici_olustur, ROLLER
from flask_login import current_user
import os

app = Flask(__name__)
app.config.from_object(Config)

# Gzip sıkıştırma — mobil ve yavaş bağlantıda cevap boyutunu küçültür
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

# Flask-Login
login_manager.init_app(app)

# ── Blueprint'leri kaydet ────────────────────────────────────────────────────
from routes.auth_routes    import bp as auth_bp
from routes.admin_routes   import bp as admin_bp
from routes.musteri_routes import bp as musteri_bp
from routes.fatura_routes  import bp as fatura_bp
from routes.kargo_routes   import bp as kargo_bp
from routes.kira_routes    import bp as kira_bp
from routes.tufe_routes    import bp as tufe_bp
from routes.tahsilat_routes import bp as tahsilat_bp
from routes.ofis_routes    import bp as ofis_bp
from routes.personel_routes import bp as personel_bp
from routes.banka_routes    import bp as banka_bp
from routes.urun_routes     import bp as urun_bp

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp,   url_prefix="/admin")
app.register_blueprint(musteri_bp, url_prefix="/musteriler")
app.register_blueprint(fatura_bp,  url_prefix="/faturalar")
app.register_blueprint(kargo_bp,   url_prefix="/kargolar")
app.register_blueprint(kira_bp,    url_prefix="/kira")
app.register_blueprint(tufe_bp,    url_prefix="/tufe")
app.register_blueprint(tahsilat_bp, url_prefix="/tahsilat")
app.register_blueprint(ofis_bp,    url_prefix="/ofisler")
app.register_blueprint(personel_bp, url_prefix="/personel")
app.register_blueprint(banka_bp, url_prefix="/bankalar")
app.register_blueprint(urun_bp, url_prefix="/urunler")

# ── Ana sayfa ────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    """Redirect to customers page for quicker access."""
    return redirect(url_for("musteriler.index"))

# ── Hata sayfaları ───────────────────────────────────────────────────────────
@app.errorhandler(403)
def forbidden(e):
    """403 Yasak hatası."""
    return render_template("errors/403.html"), 403

@app.errorhandler(404)
def not_found(e):
    """404 Sayfa bulunamadı hatası."""
    return render_template("errors/404.html"), 404

@app.errorhandler(500)
def server_error(e):
    """500 Sunucu hatası."""
    return render_template("errors/500.html"), 500

# ── Context processor — her template'e gönderilir ────────────────────────────
@app.context_processor
def inject_globals():
    """Template'lere global değişkenler ekle."""
    # Güvenli menü oluştur: current_user.gorunen_menu içindeki endpoint'ler url_for ile çözülemeyebilir.
    menu_items = []
    try:
        if current_user and getattr(current_user, "gorunen_menu", None):
            for item in current_user.gorunen_menu:
                label = item.get("label")
                id_ = item.get("id")
                endpoint = item.get("url")
                try:
                    href = url_for(endpoint)
                except Exception:
                    href = "#"
                menu_items.append({"id": id_, "label": label, "url": href, "endpoint": endpoint})
    except Exception:
        menu_items = []

    # Mevcut sayfa başlığı (üst bar 2. satır için)
    current_section_label = None
    try:
        from flask import request
        if request.endpoint == "index":
            current_section_label = "Dashboard"
        elif getattr(request, "blueprint", None):
            for m in menu_items:
                if m.get("id") == request.blueprint:
                    current_section_label = m.get("label")
                    break
        current_section_label = current_section_label or Config.APP_NAME
    except Exception:
        current_section_label = Config.APP_NAME

    return {
        "app_name": Config.APP_NAME,
        "version":  Config.VERSION,
        "menu_items": menu_items,
        "current_section_label": current_section_label,
    }

# ── İlk kurulum ──────────────────────────────────────────────────────────────
def ilk_kurulum():
    """Uygulama ilk başladığında tabloları oluştur ve admin kullanıcı ekle."""
    try:
        from db import init_schema, fetch_one
        from werkzeug.security import generate_password_hash
        import psycopg2
       
        # Şema oluştur
        init_schema()
       
        # Admin yoksa oluştur
        admin = fetch_one("SELECT id FROM users WHERE role='admin' LIMIT 1")
        if not admin:
            hashed = generate_password_hash('admin123')
           
            # Doğrudan psycopg2 kullan
            import psycopg2
            from config import Config
            conn = psycopg2.connect(Config.SUPABASE_DB_URL)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (username, password_hash, full_name, role, is_active) VALUES (%s, %s, %s, %s, %s)",
                ('admin', hashed, 'Sistem Yöneticisi', 'admin', True)
            )
            conn.commit()
            cur.close()
            conn.close()
           
            print("Admin user created: admin / admin123")
        else:
            print("Admin user already exists")
           
    except Exception as e:
        print(f"Warning - setup error: {e}")

# ── Uygulamayı başlat ────────────────────────────────────────────────────────
if __name__ == "__main__":
    ilk_kurulum()
    # Enable debug for local troubleshooting
    app.debug = True
    app.run(debug=True, host="0.0.0.0", port=5000)