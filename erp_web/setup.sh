#!/bin/bash
# OFİSBİR ERP Web - Kurulum Scripti
# Kullanım: bash setup.sh
set -e

echo "========================================"
echo "  OFİSBİR ERP Web Kurulum Başlıyor..."
echo "========================================"

# 1. Python & pip kontrolü
python3 --version || { echo "Python3 gerekli!"; exit 1; }

# 2. Sanal ortam
python3 -m venv venv
source venv/bin/activate

# 3. Bağımlılıklar
pip install -r requirements.txt

# 4. .env dosyası
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "⚠  .env dosyası oluşturuldu!"
  echo "   Lütfen .env dosyasını açıp Supabase bilgilerini girin:"
  echo "   nano .env"
  echo ""
  read -p "   .env'i düzenledikten sonra Enter'a bas..."
fi

# 5. Şema oluştur
echo "📦 Supabase tabloları oluşturuluyor..."
python3 -c "from db import init_schema; init_schema()"

# 6. Admin kullanıcı
echo "👤 Admin kullanıcı oluşturuluyor..."
python3 -c "
from auth import kullanici_olustur
from db import fetch_one
admin = fetch_one(\"SELECT id FROM users WHERE rol='admin' LIMIT 1\")
if not admin:
    r = kullanici_olustur('admin','Admin1234!','admin@ofisbir.com','admin')
    print('✓ admin / Admin1234! oluşturuldu — ilk girişten sonra şifre değiştirin!')
else:
    print('✓ Admin zaten var.')
"

echo ""
echo "========================================"
echo "  ✅ Kurulum tamamlandı!"
echo "========================================"
echo ""
echo "  Başlatmak için:"
echo "  source venv/bin/activate"
echo "  python app.py"
echo ""
echo "  Üretimde (gunicorn ile):"
echo "  gunicorn -w 4 -b 0.0.0.0:5000 app:app"
echo ""
