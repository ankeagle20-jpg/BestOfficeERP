@echo off
chcp 65001 >nul
title ERP Buluta Ac

REM ===== BURAYA GITHUB REPO ADRESINI YAPIŞTIR (tek sefer) =====
set REPO=https://github.com/KULLANICI_ADI/REPO_ADI
REM Ornek: set REPO=https://github.com/ahmet/bestoffice-erp
REM =============================================================

start "" "https://render.com/deploy?repo=%REPO%"

echo.
echo Tarayici acildi.
echo Render'da: Repo secili olacak ^(veya sec^), Root Directory'ye gerekirse erp_web yaz,
echo   Environment'a DB_HOST ve DB_PASSWORD yapistir, Deploy'a bas.
echo.
pause
