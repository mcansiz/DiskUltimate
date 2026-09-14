# Fiziksel disk sondasi — YONETICI PowerShell'de calistirin.
# Yalnizca OKUMA yapar; hicbir diske yazmaz.
#
# EN KOLAY YOL: yanindaki disk-testi-yonetici.bat dosyasina
#   sag tik > "Yonetici olarak calistir"
#
# Ya da yonetici PowerShell'de:
#   powershell -ExecutionPolicy Bypass -File \\VBOXSVR\duproje\windows-setup\disk-testi-yonetici.ps1

$kimlik = [Security.Principal.WindowsIdentity]::GetCurrent()
$yetkili = (New-Object Security.Principal.WindowsPrincipal $kimlik).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $yetkili) {
    Write-Host "HATA: Yonetici PowerShell gerekiyor." -ForegroundColor Red
    Write-Host "Baslat > 'PowerShell' > sag tik > 'Yonetici olarak calistir'"
    Write-Host "Sonra bu betigi yeniden calistirin."
    Read-Host "Kapatmak icin Enter"
    exit 1
}

$hedef = "C:\du-test"
Write-Host "Kaynak guncelleniyor..." -ForegroundColor Yellow
robocopy \\VBOXSVR\duproje\src $hedef\src /E /XD __pycache__ /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
robocopy \\VBOXSVR\duproje\tests $hedef\tests /E /XD __pycache__ /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null

Set-Location $hedef
$env:PYTHONIOENCODING = "utf-8"
Write-Host "`nSonda calistiriliyor (yalnizca okuma)...`n" -ForegroundColor Cyan
# Cikti once VM'in kendi diskine yazilir (yukseltilmis oturumda ag yolu
# yazilamayabilir), sonra paylasima kopyalanmaya calisilir.
python tests\physical_probe.py 2>&1 | Tee-Object -FilePath "$hedef\sonda.txt"
try {
    Copy-Item "$hedef\sonda.txt" "\\VBOXSVR\duproje\.tmp\win-disk-sonda.txt" -Force
    Write-Host "`nCikti ana makineye kopyalandi." -ForegroundColor Green
} catch {
    Write-Host "`nCikti: $hedef\sonda.txt (paylasima kopyalanamadi)" -ForegroundColor Yellow
}
Read-Host "Kapatmak icin Enter"
