import sys
import os

# erp_web klasörünü sistem yoluna ekliyoruz ki içindekileri bulabilsin
sys.path.append(os.path.join(os.path.dirname(__file__), 'erp_web'))

try:
    # erp_web içindeki asıl web dosyasını çağırıyoruz
    from app import app
except ImportError:
    # Eğer yukarıdaki olmazsa alternatif yolu dene
    from erp_web.app import app

if __name__ == "__main__":
    app.run()
