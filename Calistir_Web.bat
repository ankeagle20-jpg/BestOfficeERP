@echo off
chcp 65001 >nul
title OFİSBİR ERP - Web
cd /d "%~dp0"

echo.
echo ========================================
echo   OFİSBİR ERP - Web Uygulaması
echo ========================================
echo.

:: Python var mı?
python --version >nul 2>&1
if errorlevel 1 (
    echo [HATA] Python bulunamadı.
    echo.
    echo Python 3.8 veya üzeri kurulu olmalı.
    echo İndir: https://www.python.org/downloads/
    echo Kurulumda "Add Python to PATH" kutusunu işaretle.
    echo.
    pause
    exit /b 1
)

cd erp_web
if not exist "app.py" (
    echo [HATA] erp_web\app.py bulunamadı. Proje klasörü eksik olabilir.
    pause
    exit /b 1
)

:: .env uyarısı
if not exist ".env" (
    echo [UYARI] erp_web\.env dosyası yok.
    echo Veritabanı bağlantısı için .env dosyası gerekli.
    echo .env.example dosyasını .env olarak kopyalayıp doldurun.
    echo.
    set /p devam="Yine de devam etmek istiyor musunuz? (e/h): "
    if /i not "%devam%"=="e" exit /b 1
)

echo Bağımlılıklar kontrol ediliyor...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [UYARI] pip kurulumunda uyarı olabilir. Uygulama yine de başlatılıyor.
)

echo.
echo Sunucu başlatılıyor...
echo.

:: Yerel IP'yi göster (aynı ağdakiler bu linkten girebilir)
for /f "delims=" %%a in ('python -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2^>nul') do set IP=%%a
set IP=%IP: =%
if defined IP (
    echo ----------------------------------------
    echo  ERISIM LINKI (WhatsApp'tan bunu paylas):
    echo  http://%IP%:5000
    echo ----------------------------------------
    echo  Ayni agdakiler bu link + kullanici adi/sifre ile giris yapabilir.
    echo.
) else (
    echo  Sen: http://127.0.0.1:5000
    echo  Digerleri: Bilgisayarinda cmd ac, "ipconfig" yaz, IPv4 adresini paylas.
    echo.
)

echo Tarayici birkac saniye icinde acilacak...
echo Durdurmak icin bu pencerede Ctrl+C veya penceresi kapat.
echo.

:: Arka planda tarayıcıyı 3 saniye sonra aç
start /b cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:5000"

python app.py

pause
