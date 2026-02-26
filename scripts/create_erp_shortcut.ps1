<#
  create_erp_shortcut.ps1
  Kullanım: PowerShell (normal user) ile çalıştırın.
  Bu script masaüstüne "Ofisbir ERP.lnk" oluşturur ve çalıştırılacak komut olarak
  proje içindeki erp_web klasöründe `python app.py` komutunu başlatır.

  Eğer proje farklı bir dizindeyse $ProjectPath'i güncelleyin.
  Eğer virtualenv kullanıyorsanız, .lnk içinde doğrudan venv aktifleştirme yapmak yerine
  run_ofisbir.bat'ı kullanıp kısayolu ona işaret etmek daha güvenlidir.
#>

$desktop = [Environment]::GetFolderPath("Desktop")
$project = "C:\Users\Dell\Desktop\BestOfficeERP"
$webdir = Join-Path $project "erp_web"
$shortcutPath = Join-Path $desktop "Ofisbir ERP.lnk"

$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($shortcutPath)

# Target olarak cmd.exe kullanıyoruz; /k ile komut penceresi açık kalır.
$batPath = Join-Path $project "scripts\run_ofisbir.bat"

if (Test-Path $batPath) {
    # Eğer .bat varsa, kısayolu doğrudan .bat dosyasına işaret edecek şekilde oluştur.
    # Bu Windows'un .lnk davranışıyla daha uyumludur.
    $sc.TargetPath = $batPath
    $sc.Arguments = ""
    $sc.WorkingDirectory = $webdir
} else {
    # Fallback: cmd üzerinden proje dizinine geçip python çalıştır
    $ps = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
    $sc.TargetPath = $ps
    $sc.Arguments = "-NoExit -Command cd `"$webdir`"; python app.py"
    $sc.WorkingDirectory = $webdir
}

# Eğer bir .ico dosyanız varsa burada gösterin (ör: assets\ofisbir.ico)
# tercih edilen ikon (ico) varsa kullan
$ico = Join-Path $project "assets\ofisbir.ico"
if (Test-Path $ico) {
    $sc.IconLocation = $ico
}

$sc.Save()
Write-Output "Kısayol oluşturuldu: $shortcutPath"
Write-Output "Not: Eğer ofisbir.ico yoksa scripts\make_icon.ps1 çalıştırılarak oluşturulabilir."

