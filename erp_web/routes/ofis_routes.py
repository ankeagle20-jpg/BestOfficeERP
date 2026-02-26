"""
Ofis yönetimi routes'ları
"""
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from auth import giris_gerekli, admin_gerekli
from db import fetch_all, fetch_one, execute, execute_returning

bp = Blueprint('ofisler', __name__)


@bp.route('/', methods=['GET'])
@giris_gerekli
def index():
    """Ofis listesi - desktop gibi kartlar ve tablo"""
    try:
        # Filtreleme
        tur = request.args.get('tur', '')
        durum = request.args.get('durum', '')
        aktif = request.args.get('aktif', '')
        
        sql = "SELECT * FROM offices WHERE 1=1"
        params = []
        
        if tur:
            sql += " AND type = %s"
            params.append(tur)
        
        if durum:
            sql += " AND status = %s"
            params.append(durum)
        
        if aktif == 'true':
            sql += " AND is_active = TRUE"
        elif aktif == 'false':
            sql += " AND is_active = FALSE"
        
        sql += " ORDER BY code"
        
        ofisler = fetch_all(sql, params)
        
        # Özet istatistikler
        hazir_toplam = fetch_one("SELECT COUNT(*) as total FROM offices WHERE type='Hazır Ofis'")
        hazir_dolu = fetch_one("SELECT COUNT(*) as total FROM offices WHERE type='Hazır Ofis' AND status='dolu'")
        
        paylasimli_toplam = fetch_one("SELECT COUNT(*) as total FROM offices WHERE type='Paylaşımlı'")
        paylasimli_dolu = fetch_one("SELECT COUNT(*) as total FROM offices WHERE type='Paylaşımlı' AND status='dolu'")
        
        sanal_toplam = fetch_one("SELECT COUNT(*) as total FROM offices WHERE type='Sanal'")
        sanal_dolu = fetch_one("SELECT COUNT(*) as total FROM offices WHERE type='Sanal' AND status='dolu'")
        
        istatistikler = {
            'hazir': {
                'toplam': hazir_toplam['total'] if hazir_toplam else 0,
                'dolu': hazir_dolu['total'] if hazir_dolu else 0
            },
            'paylasimli': {
                'toplam': paylasimli_toplam['total'] if paylasimli_toplam else 0,
                'dolu': paylasimli_dolu['total'] if paylasimli_dolu else 0
            },
            'sanal': {
                'toplam': sanal_toplam['total'] if sanal_toplam else 0,
                'dolu': sanal_dolu['total'] if sanal_dolu else 0
            }
        }
        
        return render_template('ofisler/index.html', 
                               ofisler=ofisler, 
                               istatistikler=istatistikler,
                               tur=tur, 
                               durum=durum, 
                               aktif=aktif)
    
    except Exception as e:
        flash(f"Hata: {e}", "danger")
        return render_template('ofisler/index.html', ofisler=[], istatistikler={})


@bp.route('/yeni', methods=['GET', 'POST'])
@admin_gerekli
def yeni():
    """Yeni ofis oluştur"""
    if request.method == 'POST':
        try:
            code = request.form.get('code')
            tur = request.form.get('type')
            unit_no = request.form.get('unit_no')
            monthly_price = request.form.get('monthly_price', 0)
            
            if not code or not tur:
                flash("Kod ve tür gereklidir!", "warning")
                return redirect(url_for('ofisler.yeni'))
            
            ofis = execute_returning("""
                INSERT INTO offices (code, type, unit_no, monthly_price, status, is_active)
                VALUES (%s, %s, %s, %s, 'bos', TRUE)
                RETURNING id
            """, (code, tur, unit_no, monthly_price))
            
            flash(f"✅ Ofis oluşturuldu (ID: {ofis['id']})", "success")
            return redirect(url_for('ofisler.index'))
        
        except Exception as e:
            flash(f"❌ Hata: {e}", "danger")
            return redirect(url_for('ofisler.yeni'))
    
    return render_template('ofisler/yeni.html')


@bp.route('/<int:ofis_id>/bos', methods=['POST'])
@admin_gerekli
def bosalt(ofis_id):
    """Ofisi boşalt"""
    try:
        execute("""
            UPDATE offices 
            SET status = 'bos', customer_id = NULL 
            WHERE id = %s
        """, (ofis_id,))
        
        flash("✅ Ofis boşaltıldı", "success")
        return redirect(url_for('ofisler.index'))
    
    except Exception as e:
        flash(f"❌ Hata: {e}", "danger")
        return redirect(url_for('ofisler.index'))


@bp.route('/<int:ofis_id>/sil', methods=['POST'])
@admin_gerekli
def sil(ofis_id):
    """Ofis sil"""
    try:
        execute("DELETE FROM offices WHERE id = %s", (ofis_id,))
        flash("✅ Ofis silindi", "success")
        return redirect(url_for('ofisler.index'))
    
    except Exception as e:
        flash(f"❌ Hata: {e}", "danger")
        return redirect(url_for('ofisler.index'))