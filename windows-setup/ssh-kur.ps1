# DiskUltimate - Windows test ortami hazirlama
# YONETICI PowerShell'de:
#   Set-ExecutionPolicy -Scope Process Bypass -Force
#   \\VBOXSVR\duproje\windows-setup\ssh-kur.ps1

Write-Host "=== DiskUltimate Windows test ortami ===" -ForegroundColor Cyan

$kimlik = [Security.Principal.WindowsIdentity]::GetCurrent()
$yetkili = (New-Object Security.Principal.WindowsPrincipal $kimlik).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $yetkili) {
    Write-Host "HATA: Yonetici PowerShell'de calistirin." -ForegroundColor Red
    Write-Host "Baslat > 'PowerShell' > sag tik > 'Yonetici olarak calistir'"
    exit 1
}

Write-Host "`n[1/4] OpenSSH Server..." -ForegroundColor Yellow
$ssh = Get-WindowsCapability -Online -Name OpenSSH.Server* | Select-Object -First 1
if ($ssh -and $ssh.State -ne 'Installed') {
    Write-Host "      Kuruluyor (birkac dakika surebilir)..."
    Add-WindowsCapability -Online -Name $ssh.Name | Out-Null
} else {
    Write-Host "      Zaten kurulu."
}

Write-Host "[2/4] sshd servisi..." -ForegroundColor Yellow
Start-Service sshd -ErrorAction SilentlyContinue
Set-Service -Name sshd -StartupType Automatic -ErrorAction SilentlyContinue
Write-Host "      Durum: $((Get-Service sshd -ErrorAction SilentlyContinue).Status)"

Write-Host "[3/4] Guvenlik duvari kurali..." -ForegroundColor Yellow
if (-not (Get-NetFirewallRule -Name sshd -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd)' `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    Write-Host "      Eklendi."
} else {
    Write-Host "      Zaten var."
}

Write-Host "[4/4] Python..." -ForegroundColor Yellow
$py = $null
foreach ($aday in @('py', 'python')) {
    $bulundu = Get-Command $aday -ErrorAction SilentlyContinue
    if ($bulundu) {
        $surum = & $aday --version 2>&1
        if ("$surum" -match 'Python 3') { $py = $aday; Write-Host "      $aday -> $surum"; break }
    }
}
if (-not $py) {
    Write-Host "      Python 3 bulunamadi." -ForegroundColor Red
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "      winget ile kuruluyor..."
        winget install --id Python.Python.3.12 -e --scope machine `
            --accept-source-agreements --accept-package-agreements
        Write-Host "      Bitti. PowerShell'i kapatip yeniden acin." -ForegroundColor Yellow
    } else {
        Write-Host "      https://www.python.org/downloads/ -> kurulumda" -ForegroundColor Yellow
        Write-Host "      'Add python.exe to PATH' secenegini ISARETLEYIN."
    }
}

Write-Host "`n=== Baglanti bilgileri ===" -ForegroundColor Cyan
Write-Host "Kullanici adi : $env:USERNAME"
Write-Host "sshd durumu   : $((Get-Service sshd -ErrorAction SilentlyContinue).Status)"
$parolasiz = $false
try {
    $u = Get-LocalUser -Name $env:USERNAME -ErrorAction Stop
    if (-not $u.PasswordLastSet) { $parolasiz = $true }
} catch {}
if ($parolasiz) {
    Write-Host "`nUYARI: Bu hesabin parolasi yok gorunuyor. SSH parolasiz hesapla" -ForegroundColor Red
    Write-Host "calismaz. Parola belirleyin:   net user $env:USERNAME *" -ForegroundColor Red
}
Write-Host "`nAna makineden:  ssh $env:USERNAME@127.0.0.1 -p 2222" -ForegroundColor Green
