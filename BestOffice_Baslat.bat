@echo off
REM ============================================================
REM  BestOffice_Baslat - ILK / YEREL VERSIYON (desktop)
REM  Bu kisa yol SADECE yerel sunucuyu acar - BULUTA GITMEZ.
REM  Masaustu kisayolu bu .bat dosyasina isaret etmeli.
REM ============================================================
cd /d "%~dp0"

REM Sunucuyu yeni pencerede baslat
if exist "erp_web\app.py" (
  start "BestOffice Server (Yerel)" cmd /k "cd /d \"%~dp0erp_web\" && python app.py"
) else (
  start "BestOffice Server (Yerel)" cmd /k "cd /d \"%~dp0\" && python run.py"
)

REM Tarayiciyi SADECE yerel adreste ac (127.0.0.1 = ilk/desktop versiyon)
timeout /t 2 /nobreak > nul
set YEREL_URL=http://127.0.0.1:5000
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
  start "" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" --new-window "%YEREL_URL%"
) else if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --new-window "%YEREL_URL%"
) else if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
  start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --new-window "%YEREL_URL%"
) else if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
  start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --new-window "%YEREL_URL%"
) else if exist "%ProgramFiles%\Mozilla Firefox\firefox.exe" (
  start "" "%ProgramFiles%\Mozilla Firefox\firefox.exe" -new-window "%YEREL_URL%"
) else (
  start "" "%YEREL_URL%"
)
exit
