@echo off
REM run_ofisbir.bat - Masaüstünden çalıştırılabilir başlatıcı
REM Bu .bat, sunucuyu yeni bir cmd penceresinde başlatır ve varsayılan tarayıcıyı açar.
cd /d "C:\Users\Dell\Desktop\BestOfficeERP\erp_web"
REM Eğer sanal ortam (venv) kullanıyorsanız aşağıdaki satırı düzenleyin ve aktif edin:
REM call "..\venv\Scripts\activate.bat"

REM Yeni bir komut penceresinde sunucuyu başlat
start "Ofisbir Server" cmd /k "cd /d \"C:\Users\Dell\Desktop\BestOfficeERP\erp_web\" && python app.py"

REM Kısa bekleme, sonra tarayıcıyı aç
timeout /t 1 /nobreak > nul
REM Prefer specific browser to avoid restoring previous session tabs.
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
  start "" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" --new-window "http://127.0.0.1:5000"
) else if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --new-window "http://127.0.0.1:5000"
) else if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
  start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --new-window "http://127.0.0.1:5000"
) else if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
  start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --new-window "http://127.0.0.1:5000"
) else if exist "%ProgramFiles%\Mozilla Firefox\firefox.exe" (
  start "" "%ProgramFiles%\Mozilla Firefox\firefox.exe" -new-window "http://127.0.0.1:5000"
) else (
  REM Fallback to default handler
  start "" "http://127.0.0.1:5000"
)
exit

