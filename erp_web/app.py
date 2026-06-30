"""
OFİSBİR ERP — Web Uygulaması
Flask + Supabase PostgreSQL
Deploy: 2026-03-14 (Fatura GIB onizleme, Musteri listesi, Kira suresi oto, Cari Kart Musteri Listesi butonu)
"""

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, g, send_file
from flask_login import login_required, current_user, logout_user
from config import Config
from auth import login_manager, giris_yap, kullanici_olustur, ROLLER
from flask_login import current_user
import os
import atexit
import sys
import socket
import re
import threading

from db import (
    fetch_all,
    fetch_one,
    execute,
    ensure_grup2_etiketleri_table,
    ensure_grup2_bizim_hesap_into_array,
)

app = Flask(__name__)
app.config.from_object(Config)


def _format_tr_number(value, decimals=2):
    """1.234,56 biçiminde TR sayı gösterimi."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    try:
        d = int(decimals)
    except (TypeError, ValueError):
        d = 2
    if d < 0:
        d = 0
    s = f"{n:,.{d}f}"
    # en_US: 1,234.56 -> tr_TR: 1.234,56
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("trnum")
def trnum_filter(value, decimals=2):
    return _format_tr_number(value, decimals=decimals)

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
from routes.faturalar_routes import bp as faturalar_bp
from routes.kargo_routes   import bp as kargo_bp
from routes.kira_routes    import bp as kira_bp
from routes.tufe_routes    import bp as tufe_bp
from routes.tahsilat_routes import bp as tahsilat_bp
from routes.ofis_routes    import bp as ofis_bp
from routes.personel_routes import bp as personel_bp
from routes.banka_routes    import bp as banka_bp
from routes.giris_routes    import bp as giris_bp
from routes.urun_routes     import bp as urun_bp
from routes.dashboard_routes import bp as dashboard_bp
from routes.mobile_routes import bp as mobile_bp
from routes.cari_kart_routes import bp as cari_kart_bp
from routes.randevu_routes import bp as randevu_bp
from routes.pdovam_routes import bp as pdovam_bp
from routes.whatsapp_routes import bp as whatsapp_bp
try:
    from routes.ilan_robotu_routes import bp as ilan_robotu_bp
except Exception as e:
    ilan_robotu_bp = None
    print("[WARN] İlan Robotu blueprint yüklenemedi:", e)

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp, url_prefix="/dashboard")
app.register_blueprint(mobile_bp)
app.register_blueprint(admin_bp,   url_prefix="/admin")
app.register_blueprint(musteri_bp, url_prefix="/musteriler")
app.register_blueprint(faturalar_bp,  url_prefix="/faturalar")
app.register_blueprint(kargo_bp,   url_prefix="/kargolar")
app.register_blueprint(kira_bp,    url_prefix="/kira")
app.register_blueprint(tufe_bp,    url_prefix="/tufe")
app.register_blueprint(tahsilat_bp, url_prefix="/tahsilat")
app.register_blueprint(ofis_bp,    url_prefix="/ofisler")
app.register_blueprint(personel_bp, url_prefix="/personel")
app.register_blueprint(banka_bp, url_prefix="/bankalar")
app.register_blueprint(urun_bp, url_prefix="/urunler")
app.register_blueprint(giris_bp, url_prefix="/giris")
app.register_blueprint(cari_kart_bp, url_prefix="/cari-kart")
app.register_blueprint(randevu_bp)
app.register_blueprint(pdovam_bp, url_prefix="/pdovam")
app.register_blueprint(whatsapp_bp)
if ilan_robotu_bp is not None:
    app.register_blueprint(ilan_robotu_bp, url_prefix="/ilan-robotu")

# ── dev_http.log: her istek için [SRV] / [HTTP] (BESTOFFICE_HTTP_FILE_LOG=0 ile kapat) ──
_dev_http_file_lock = threading.Lock()


def _app_dev_http_log_path() -> str:
    raw = (os.environ.get("BESTOFFICE_DEV_HTTP_LOG") or "").strip()
    erp_web = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(raw) if raw else os.path.join(erp_web, "dev_http.log")


def _app_dev_http_file_log_disabled() -> bool:
    return (os.environ.get("BESTOFFICE_HTTP_FILE_LOG") or "").strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    )


def _app_dev_http_append(line: str) -> None:
    if _app_dev_http_file_log_disabled():
        return
    text = line if str(line).endswith("\n") else f"{line}\n"
    path = _app_dev_http_log_path()
    try:
        with _dev_http_file_lock:
            with open(path, "a", encoding="utf-8", errors="replace") as lf:
                lf.write(text)
    except Exception as ex:
        try:
            sys.__stderr__.write(f"[app-dev_http] {ex!r} path={path!r}\n")
            sys.__stderr__.flush()
        except Exception:
            pass


@app.before_request
def _dev_http_log_before():
    if _app_dev_http_file_log_disabled():
        g._dev_http_base = None
        return
    fp = request.full_path
    if fp.endswith("?"):
        fp = fp[:-1]
    g._dev_http_base = f"{request.remote_addr} {request.method} {fp}"
    _app_dev_http_append(f"[SRV] {g._dev_http_base}")


@app.after_request
def _dev_http_log_after(response):
    base = getattr(g, "_dev_http_base", None)
    if not base:
        return response
    try:
        code = response.status_code
    except Exception:
        code = "?"
    _app_dev_http_append(f"[HTTP] {base} -> {code}")
    return response


try:
    if not _app_dev_http_file_log_disabled():
        _app_dev_http_append(
            f"# [BOOT] app.py yüklendi pid={os.getpid()} dev_http={_app_dev_http_log_path()!r}"
        )
except Exception:
    pass


def _register_gib_dispatch_jp_fallback_routes():
    """
    GİB `jp` önizleme (`api_gib_dispatch_onizle`):
    - Render / bazı süreçlerde blueprint route eksik kalınca `url_for('faturalar.api_gib_dispatch_onizle')` BuildError verir.
    - Endpoint yoksa aynı view'ı `faturalar.api_gib_dispatch_onizle` adıyla uygulama seviyesinde ekleriz (url_for çalışır).
    - Endpoint varsa yalnızca kök `/api/gib-dispatch-jp-onizle` alias'ı (farklı endpoint) eklenir.
    """
    ep = "faturalar.api_gib_dispatch_onizle"
    try:
        from routes import faturalar_routes as fr

        vf = fr.api_gib_dispatch_onizle
    except Exception as ex:
        print("[WARN] GİB dispatch jp: api_gib_dispatch_onizle yüklenemedi:", ex)
        return
    if not callable(vf):
        print("[WARN] GİB dispatch jp: faturalar_routes.api_gib_dispatch_onizle tanımsız (dosya sürümü?)")
        return

    has_ep = ep in app.view_functions
    try:
        rule_set = {getattr(r, "rule", None) for r in app.url_map.iter_rules()}
    except Exception:
        rule_set = set()

    paths_bp = ("/faturalar/api/gib-dispatch-onizle", "/faturalar/api/gib-jp-onizle")
    root_path = "/api/gib-dispatch-jp-onizle"

    if not has_ep:
        for p in (*paths_bp, root_path):
            if p in rule_set:
                continue
            try:
                app.add_url_rule(p, ep, vf, methods=["GET"])
                rule_set.add(p)
            except Exception as ex:
                print("[WARN] GİB dispatch jp kural eklenemedi (%s):" % p, ex)
        if ep in app.view_functions:
            print("[OK] GİB dispatch jp: endpoint", ep, "uygulama yedeğiyle kayıtlı")
        return

    if root_path not in rule_set:
        try:
            app.add_url_rule(
                root_path,
                "api_gib_dispatch_jp_onizle__root",
                vf,
                methods=["GET"],
            )
            print("[OK] GİB dispatch jp kök alias:", root_path)
        except Exception as ex:
            print("[WARN] GİB dispatch jp kök:", ex)
    else:
        print("[OK] GİB dispatch jp:", ep, "+", root_path)


_register_gib_dispatch_jp_fallback_routes()


def _g2_slugify(label: str) -> str:
    s = (label or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s or "ozel")[:80]


def _grup2_etiketleri_fallback_api():
    if not getattr(current_user, "is_authenticated", False):
        return jsonify({"ok": False, "mesaj": "Oturum gerekli veya süresi doldu. Sayfayı yenileyip tekrar giriş yapın."}), 401
    ensure_grup2_etiketleri_table()
    if request.method == "GET":
        ensure_grup2_bizim_hesap_into_array()
        rows = fetch_all(
            """
            SELECT id, slug, etiket
            FROM grup2_etiketleri
            WHERE COALESCE(aktif, TRUE)
            ORDER BY
                CASE slug
                    WHEN 'bizim_hesap' THEN 0
                    WHEN 'vergi_dairesi' THEN 1
                    WHEN 'vergi_dairesi_terk' THEN 2
                    ELSE 3
                END,
                sira NULLS LAST,
                etiket
            """
        )
        return jsonify({"ok": True, "etiketler": rows or []})
    data = request.get_json(silent=True) or {}
    if not data:
        data = request.form.to_dict(flat=True) or {}
    if not data:
        data = request.args.to_dict(flat=True) or {}
    act = (data.get("action") or "").strip().lower()
    if act in ("update_etiket", "put") or (str(data.get("slug") or "").strip() and str(data.get("etiket") or "").strip()):
        slug = (data.get("slug") or "").strip()
        etiket_u = (data.get("etiket") or "").strip()
        if not slug:
            return jsonify({"ok": False, "mesaj": "Slug zorunludur."}), 400
        if not etiket_u:
            return jsonify({"ok": False, "mesaj": "Etiket adı boş olamaz."}), 400
        if len(etiket_u) > 200:
            return jsonify({"ok": False, "mesaj": "En fazla 200 karakter girebilirsiniz."}), 400
        row = fetch_one(
            "SELECT id, slug, etiket FROM grup2_etiketleri WHERE slug = %s AND COALESCE(aktif, TRUE) LIMIT 1",
            (slug,),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "Etiket bulunamadı."}), 400
        dup = fetch_one(
            """
            SELECT id FROM grup2_etiketleri
            WHERE COALESCE(aktif, TRUE)
              AND lower(trim(etiket)) = lower(trim(%s))
              AND slug <> %s
            LIMIT 1
            """,
            (etiket_u, slug),
        )
        if dup:
            return jsonify({"ok": False, "mesaj": "Bu etiket adı zaten kullanılıyor."}), 400
        execute("UPDATE grup2_etiketleri SET etiket = %s WHERE slug = %s", (etiket_u, slug))
        return jsonify({"ok": True, "slug": slug, "etiket": etiket_u})
    if act in ("delete_etiket", "delete") or (str(data.get("slug") or "").strip() and not str(data.get("etiket") or "").strip()):
        slug = (data.get("slug") or "").strip()
        if not slug:
            return jsonify({"ok": False, "mesaj": "Silinecek etiket slug bilgisi zorunludur."}), 400
        row = fetch_one(
            "SELECT id, slug, etiket FROM grup2_etiketleri WHERE slug = %s AND COALESCE(aktif, TRUE) LIMIT 1",
            (slug,),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "Etiket bulunamadı."}), 400
        execute("UPDATE grup2_etiketleri SET aktif = FALSE WHERE slug = %s", (slug,))
        execute(
            """
            UPDATE customers
            SET grup2_secimleri = array_remove(COALESCE(grup2_secimleri, ARRAY[]::text[]), %s)
            WHERE %s = ANY(COALESCE(grup2_secimleri, ARRAY[]::text[]))
            """,
            (slug, slug),
        )
        return jsonify({"ok": True, "slug": slug, "etiket": row.get("etiket")})
    etiket = (data.get("etiket") or "").strip()
    if not etiket:
        return jsonify({"ok": False, "mesaj": "Etiket adı boş olamaz."}), 400
    if len(etiket) > 200:
        return jsonify({"ok": False, "mesaj": "En fazla 200 karakter girebilirsiniz."}), 400
    ex = fetch_one(
        "SELECT id, slug, etiket FROM grup2_etiketleri WHERE lower(trim(etiket)) = lower(trim(%s)) LIMIT 1",
        (etiket,),
    )
    if ex:
        return jsonify({"ok": True, "slug": ex["slug"], "etiket": ex["etiket"], "mevcut": True})
    rows_all = fetch_all("SELECT slug FROM grup2_etiketleri")
    slug_set = {r["slug"] for r in (rows_all or [])}
    base = _g2_slugify(etiket)
    slug_out = None
    for n in range(0, 200):
        cand = (base if n == 0 else f"{base}_{n}")[:80]
        if cand in slug_set:
            continue
        mx = fetch_one("SELECT COALESCE(MAX(sira), 0) + 1 AS n FROM grup2_etiketleri")
        next_sira = int(mx["n"] or 1) if mx else 1
        try:
            execute(
                "INSERT INTO grup2_etiketleri (slug, etiket, sira) VALUES (%s, %s, %s)",
                (cand, etiket, next_sira),
            )
            slug_out = cand
            break
        except Exception:
            slug_set.add(cand)
    if not slug_out:
        return jsonify({"ok": False, "mesaj": "Slug üretilemedi."}), 400
    return jsonify({"ok": True, "slug": slug_out, "etiket": etiket})


def _register_grup2_fallback_api(path: str):
    exists = any(r.rule == path for r in app.url_map.iter_rules())
    if not exists:
        app.add_url_rule(path, endpoint=f"grup2_fallback_{path.strip('/').replace('/', '_')}", view_func=_grup2_etiketleri_fallback_api, methods=["GET", "POST"])


_register_grup2_fallback_api("/giris/api/grup2-etiketleri")
_register_grup2_fallback_api("/faturalar/api/grup2-etiketleri")


def _start_background_jobs():
    """Opsiyonel arkaplan işler: otomatik fatura döngüsü + gece izin hesabı."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from routes.faturalar_routes import run_auto_invoice_cycle
        from services.izin_otomatik import run_gece_otomatik_izin_job
    except Exception as e:
        print("[WARN] Background scheduler devre dışı:", e)
        return
    scheduler = BackgroundScheduler(timezone="Europe/Istanbul")
    scheduler.add_job(
        lambda: run_auto_invoice_cycle(force=False),
        "interval",
        minutes=15,
        id="auto_invoice_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_gece_otomatik_izin_job,
        "cron",
        hour=0,
        minute=5,
        id="izin_otomatik_gece",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))
    print("[OK] Background scheduler aktif: auto_invoice_cycle/15dk, izin_otomatik_gece/00:05")

# ── Sağlık (Render health check / yük dengeleyici) — DB veya giriş gerekmez ───
@app.route("/favicon.ico")
def favicon():
    """Tarayıcı varsayılan isteği; 404 konsol gürültüsünü önler."""
    static_ico = os.path.join(app.root_path, "static", "favicon.ico")
    if os.path.isfile(static_ico):
        return send_file(static_ico, mimetype="image/x-icon", max_age=86400)
    logo = os.path.join(os.path.dirname(app.root_path), "assets", "Ofisbir Logo.jpg")
    if os.path.isfile(logo):
        return send_file(logo, mimetype="image/jpeg", max_age=86400)
    return "", 204


@app.route("/healthz")
def healthz():
    """Render `healthCheckPath` ve manuel ping için; uygulama ayakta mı kontrol eder."""
    return "ok\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


# ── Ana sayfa ────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    """Sekreterya Dashboard — tek ekranda tüm operasyonlar."""
    return redirect(url_for("dashboard.index"))

# ── Hata sayfaları ───────────────────────────────────────────────────────────
@app.errorhandler(403)
def forbidden(e):
    """403 Yasak hatası."""
    return render_template("errors/403.html"), 403

@app.errorhandler(404)
def not_found(e):
    """404 Sayfa bulunamadı hatası."""
    return render_template("errors/404.html"), 404

_API_JSON_500_PREFIXES = (
    "/giris/api/",
    "/giris/resim-yukle/",
    "/cari-kart/api/",
    "/musteriler/api/",
    "/bankalar/api/",
    "/faturalar/api/",
    "/randevu/api/",
)


@app.errorhandler(500)
def server_error(e):
    """500 Sunucu hatası — tam sayfa için HTML; fetch+JSON API uçları için JSON (HTML 500 SPA'yı kırıyordu)."""
    try:
        p = getattr(request, "path", None) or ""
    except RuntimeError:
        p = ""
    if p.startswith(_API_JSON_500_PREFIXES):
        return (
            jsonify(
                {
                    "ok": False,
                    "mesaj": "Sunucu hatası oluştu. Lütfen tekrar deneyin.",
                }
            ),
            500,
        )
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
        if request.endpoint == "index" or (getattr(request, "blueprint", None) == "dashboard"):
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
        from db import init_schema, fetch_one, execute
        from werkzeug.security import generate_password_hash

        # Şema oluştur
        init_schema()

        # Admin yoksa oluştur (ilk giriş: admin / admin123)
        admin = fetch_one("SELECT id FROM users WHERE role='admin' LIMIT 1")
        if not admin:
            hashed = generate_password_hash("admin123")
            execute(
                "INSERT INTO users (username, password_hash, full_name, role, is_active) VALUES (%s, %s, %s, %s, %s)",
                ("admin", hashed, "Sistem Yöneticisi", "admin", True),
            )
            print("Admin user created: admin / admin123")
        else:
            print("Admin user already exists")
    except Exception as e:
        print(f"Warning - ilk_kurulum error: {e}")

def _bootstrap_on_startup_enabled() -> bool:
    """
    Başlangıçta şema/admin bootstrap çalışsın mı?

    Varsayılan:
    - Geliştirmede kapalı (hızlı açılış, lock/connection dalgalanmasında bloklanmasın)
    - Prod benzeri ortamlarda açık

    Override:
    - BESTOFFICE_RUN_BOOTSTRAP=1  -> zorla aç
    - BESTOFFICE_RUN_BOOTSTRAP=0  -> zorla kapat
    """
    raw = (os.environ.get("BESTOFFICE_RUN_BOOTSTRAP") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    # Local debug varsayılanı: kapalı
    return bool(os.environ.get("GUNICORN_CMD_ARGS") or os.environ.get("RENDER"))


# Uygulama yüklendiğinde (özellikle prod) şema ve admin kontrolü
if _bootstrap_on_startup_enabled():
    ilk_kurulum()
else:
    print("[BOOT] ilk_kurulum atlandı (BESTOFFICE_RUN_BOOTSTRAP ile açabilirsiniz).")
try:
    from utils.compute_device import log_startup_accelerators

    log_startup_accelerators()
except Exception:
    pass
# Debug reloader'da parent process'te çift scheduler açmamak için sadece child'da başlat.
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or os.environ.get("GUNICORN_CMD_ARGS"):
    _start_background_jobs()

def _dev_yerel_ag_ipv4():
    """Bu makinenin LAN IP'si (başka PC'ler 127.0.0.1 ile erişemez)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.25)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


# ── Uygulamayı başlat ────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Enable debug for local troubleshooting
    app.debug = True
    port = int(os.environ.get("PORT", 5000))
    # Windows console encoding (cp1254 vb.) yüzünden uygulama çökmesin:
    # stdout/stderr UTF-8 olamıyorsa bile yazdırmayı güvenli hale getir.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("\n" + "=" * 50)
    print("  OFİSBİR ERP — Sunucu çalışıyor")
    print("  Bu bilgisayarda aç: http://127.0.0.1:{}".format(port))
    _lan = _dev_yerel_ag_ipv4()
    if _lan and _lan != "127.0.0.1":
        print("  Başka bilgisayar / telefon (aynı ağ): http://{}:{}".format(_lan, port))
        print("  Not: 127.0.0.1 yalnızca bu PC içindir; diğerine LAN adresini verin.")
    else:
        print("  Başka PC için: ipconfig ile IPv4 adresini kullanın (örn. 192.168.x.x).")
    print("  Giriş: admin / admin123")
    if not _app_dev_http_file_log_disabled():
        print("  İstek günlüğü (dosya): {}".format(_app_dev_http_log_path()))
    print("=" * 50 + "\n")
    # threaded=True: Paralel fetch istekleri birbirini bloklamasın.
    # use_reloader: IDE dosya kaydında sunucu 1–2 sn kapanır → UI'da "Failed to fetch" görülebilir.
    # Varsayılanı "kapalı" tutuyoruz; isteyen BESTOFFICE_DEV_RELOAD=1 ile açar.
    _reload_on = os.environ.get("BESTOFFICE_DEV_RELOAD", "").strip().lower() in ("1", "true", "yes", "on")
    if not _reload_on:
        print("  [DEV] Otomatik yeniden başlatma kapalı (BESTOFFICE_DEV_RELOAD=1 ile açılır).\n")
    use_waitress = (os.environ.get("BESTOFFICE_USE_WAITRESS", "0") or "").strip().lower() in ("1", "true", "yes")
    if use_waitress:
        from waitress import serve
        print(f"[BOOT] waitress ile baslatiliyor (threads=16) - http://127.0.0.1:{port}")
        serve(app, host="0.0.0.0", port=port, threads=16)
    else:
        app.run(
            debug=True,
            host="0.0.0.0",
            port=port,
            threaded=True,
            use_reloader=_reload_on,
        )