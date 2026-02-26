"""
Kargo yönetimi routes'ları
"""
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from flask_login import login_required
from auth import admin_gerekli, giris_gerekli
from db import fetch_all, fetch_one, execute, execute_returning

bp = Blueprint('kargolar', __name__)


@bp.route('/', methods=['GET'])
@giris_gerekli
def index():
    """Kargo kayıtlarını listele"""
    try:
        # Desktop'taki sütunlara göre çek
        kargolar = fetch_all("""
            SELECT k.id, k.tarih, k.musteri_id, k.kargo_firmasi, k.teslim_alan, 
                   k.takip_no, k.odeme_tutari, k.odeme_durumu, k.notlar,
                   c.name as musteri_adi
            FROM kargolar k
            LEFT JOIN customers c ON k.musteri_id = c.id
            ORDER BY k.created_at DESC
        """)
        
        if not kargolar:
            kargolar = []
        
        return render_template('kargolar/index.html', kargolar=kargolar)
    
    except Exception as e:
        flash(f"Hata: {e}", "danger")
        return render_template('kargolar/index.html', kargolar=[])


@bp.route('/yeni', methods=['GET', 'POST'])
@giris_gerekli
def yeni():
    """Yeni kargo oluştur"""
    if request.method == 'POST':
        try:
            musteri_id = request.form.get('musteri_id')
            kargo_firmasi = request.form.get('kargo_firmasi')
            takip_no = request.form.get('takip_no')
            teslim_alan = request.form.get('teslim_alan')
            odeme_tutari = request.form.get('odeme_tutari', 0)
            
            if not musteri_id or not takip_no:
                flash("Müşteri ve takip no gereklidir!", "warning")
                return redirect(url_for('kargolar.yeni'))
            
            kargo = execute_returning("""
                INSERT INTO kargolar (musteri_id, kargo_firmasi, takip_no, teslim_alan, odeme_tutari, odeme_durumu, durum)
                VALUES (%s, %s, %s, %s, %s, 'odenmedi', 'beklemede')
                RETURNING id
            """, (musteri_id, kargo_firmasi, takip_no, teslim_alan, odeme_tutari))
            
            flash(f"✅ Kargo oluşturuldu (ID: {kargo['id']})", "success")
            return redirect(url_for('kargolar.index'))
        
        except Exception as e:
            flash(f"❌ Hata: {e}", "danger")
            return redirect(url_for('kargolar.yeni'))
    
    # GET - form göster
    try:
        musteriler = fetch_all("SELECT id, name FROM customers ORDER BY name")
        return render_template('kargolar/yeni.html', musteriler=musteriler)
    except Exception as e:
        flash(f"Hata: {e}", "danger")
        return redirect(url_for('kargolar.index'))


@bp.route('/<int:kargo_id>', methods=['GET'])
@giris_gerekli
def detay(kargo_id):
    """Kargo detayları"""
    try:
        kargo = fetch_one("""
            SELECT k.*, c.name as musteri_adi
            FROM kargolar k
            LEFT JOIN customers c ON k.musteri_id = c.id
            WHERE k.id = %s
        """, (kargo_id,))
        
        if not kargo:
            flash("Kargo bulunamadı!", "warning")
            return redirect(url_for('kargolar.index'))
        
        return render_template('kargolar/detay.html', kargo=kargo)
    
    except Exception as e:
        flash(f"Hata: {e}", "danger")
        return redirect(url_for('kargolar.index'))


@bp.route('/<int:kargo_id>/sil', methods=['POST'])
@admin_gerekli
def sil(kargo_id):
    """Kargo sil"""
    try:
        execute("DELETE FROM kargolar WHERE id = %s", (kargo_id,))
        flash("✅ Kargo silindi", "success")
        return redirect(url_for('kargolar.index'))
    
    except Exception as e:
        flash(f"❌ Hata: {e}", "danger")
        return redirect(url_for('kargolar.index'))