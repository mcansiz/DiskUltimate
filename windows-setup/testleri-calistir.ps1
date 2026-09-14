# Testleri VM'in KENDI diskinde calistirir (paylasilan klasorde DEGIL).
# Kullanim:  \\VBOXSVR\duproje\windows-setup\testleri-calistir.ps1

$kaynak = "\\VBOXSVR\duproje"
$hedef  = "C:\du-test"

Write-Host "=== DiskUltimate testleri (Windows) ===" -ForegroundColor Cyan

if (-not (Test-Path $kaynak)) {
    Write-Host "HATA: Paylasilan klasor bulunamadi: $kaynak" -ForegroundColor Red
    Write-Host "VirtualBox Guest Additions kurulu mu? Paylasim adi 'duproje' olmali."
    exit 1
}

Write-Host "`n[1/3] Kaynak kod yerel diske kopyalaniyor: $hedef" -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $hedef | Out-Null
foreach ($klasor in @('src', 'tests')) {
    robocopy "$kaynak\$klasor" "$hedef\$klasor" /E /NFL /NDL /NJH /NJS /NC /NS | Out-Null
}
Copy-Item "$kaynak\main.py" $hedef -Force
Write-Host "      Tamam."

Write-Host "[2/3] Python araniyor..." -ForegroundColor Yellow
$py = $null
foreach ($aday in @('py', 'python')) {
    if (Get-Command $aday -ErrorAction SilentlyContinue) {
        $surum = & $aday --version 2>&1
        if ("$surum" -match 'Python 3') { $py = $aday; Write-Host "      $aday -> $surum"; break }
    }
}
if (-not $py) { Write-Host "HATA: Python 3 bulunamadi." -ForegroundColor Red; exit 1 }

Write-Host "[3/3] Testler calistiriliyor (test alani: $hedef\.tmp)" -ForegroundColor Yellow
Set-Location $hedef
$env:PYTHONIOENCODING = "utf-8"
& $py -m tests.platform_check
& $py -m tests.run_all
Write-Host "`nBitti. Temizlik icin:  Remove-Item -Recurse -Force $hedef" -ForegroundColor Cyan
