"""
Gunicorn giriş noktası — repo kökünden çalıştırıldığında erp_web uygulamasını yükler.
Render rootDir=erp_web kullanıyorsa erp_web/run.py kullanılır; root kullanıyorsa bu dosya.
"""
import os
import sys

# Repo kökünden çalışıyorsak erp_web'i path'e ekle
_here = os.path.dirname(os.path.abspath(__file__))
_erp_web = os.path.join(_here, "erp_web")
if os.path.isdir(_erp_web) and _erp_web not in sys.path:
    sys.path.insert(0, _erp_web)

# Uygulama erp_web/app.py içinde
from app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
