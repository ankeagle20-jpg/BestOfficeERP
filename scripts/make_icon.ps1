<#
 make_icon.ps1
 SVG (assets/ofisbir.svg) -> ICO (assets/ofisbir.ico) dönüştürücü.
 Gereksinim: ImageMagick yüklü ise `magick` komutu kullanılacaktır.
 Kullanım: PowerShell'de normal user olarak çalıştırın.
#>

$project = "C:\Users\Dell\Desktop\BestOfficeERP"
$svg = Join-Path $project "assets\ofisbir.svg"
$ico = Join-Path $project "assets\ofisbir.ico"

if (-not (Test-Path $svg)) {
    Write-Error "SVG bulunamadı: $svg"
    exit 1
}

# ImageMagick var mı kontrol et
$magick = Get-Command magick -ErrorAction SilentlyContinue
if ($magick) {
    Write-Output "ImageMagick bulundu: $($magick.Source). ICO oluşturuluyor..."
    try {
        magick convert $svg -define icon:auto-resize=256,128,64,48,32,16 $ico
        Write-Output "ICO oluşturuldu: $ico"
    } catch {
        Write-Error "ImageMagick ile dönüştürme başarısız: $_"
        exit 1
    }
} else {
    Write-Warning "ImageMagick (magick) bulunamadı. Lütfen ImageMagick yükleyip tekrar çalıştırın veya manuel olarak ofisbir.svg -> ofisbir.ico dönüştürün."
    Write-Output "ImageMagick indirme: https://imagemagick.org"
    exit 2
}

