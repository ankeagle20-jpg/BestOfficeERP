# BestOfficeERP -> GitHub push (Render otomatik deploy icin)
# Calistirma: sag tik -> PowerShell ile calistir, veya: powershell -ExecutionPolicy Bypass -File deploy-push.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Git {
    $c = Get-Command git -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @(
        "${env:ProgramFiles}\Git\bin\git.exe",
        "${env:ProgramFiles(x86)}\Git\bin\git.exe",
        "${env:LOCALAPPDATA}\Programs\Git\bin\git.exe",
        "${env:ProgramFiles}\Git\cmd\git.exe"
    )) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$git = Find-Git
if (-not $git) {
    Write-Host ""
    Write-Host "GIT BULUNAMADI. Render deploy icin once Git kur:" -ForegroundColor Yellow
    Write-Host "  https://git-scm.com/download/win" -ForegroundColor Cyan
    Write-Host "  veya (winget varsa): winget install Git.Git -e --source winget" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Alternatif: GitHub Desktop kur, repoyu ac, 'Push origin' yap." -ForegroundColor Gray
    exit 1
}

Write-Host "Git: $git" -ForegroundColor Green
& $git status --short
$st = & $git status --porcelain
if (-not $st) {
    Write-Host "Commitlenecek degisiklik yok (yerel ile remote zaten ayni olabilir)." -ForegroundColor Yellow
    Write-Host "Yine de push deneniyor..." -ForegroundColor Gray
} else {
    & $git add -A
    & $git commit -m "deploy: ERP guncellemeleri ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Commit basarisiz (bos mesaj veya hata)." -ForegroundColor Red
        exit 1
    }
}

& $git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "PUSH BASARISIZ. Kontrol et:" -ForegroundColor Red
    Write-Host "  - GitHub giris / Personal Access Token" -ForegroundColor Gray
    Write-Host "  - Internet, remote: git remote -v" -ForegroundColor Gray
    exit 1
}

Write-Host ""
Write-Host "Tamam. GitHub'a gitti; Render bir kac dakika icinde deploy almali." -ForegroundColor Green
Write-Host "Render: https://dashboard.render.com -> servis -> Events / Logs" -ForegroundColor Cyan
