$ErrorActionPreference = 'Stop'
$base = '\\VBoxSvr\GitHub\DiskUltimate\.tmp\vm\w1006'
$vhd = 'C:\du-test\w1006.vhd'
$log = New-Object System.Collections.Generic.List[string]
try {
  Copy-Item "$base\bos.vhd" $vhd -Force
  $img = Mount-DiskImage -ImagePath $vhd -Access ReadWrite -PassThru
  Start-Sleep 3
  $d = $img | Get-Disk
  Initialize-Disk -Number $d.Number -PartitionStyle GPT
  $plan = @(
    @{L='N64K'; F='NTFS';  A=65536;   S=400MB},
    @{L='N4K';  F='NTFS';  A=4096;    S=300MB},
    @{L='N2M';  F='NTFS';  A=2097152; S=300MB},
    @{L='EXF';  F='exFAT'; A=131072;  S=200MB},
    @{L='F32';  F='FAT32'; A=0;       S=200MB}
  )
  $args2 = @()
  foreach ($p in $plan) {
    $part = New-Partition -DiskNumber $d.Number -Size $p.S -AssignDriveLetter
    Start-Sleep 2
    if ($p.A -gt 0) {
      Format-Volume -Partition $part -FileSystem $p.F -AllocationUnitSize $p.A -NewFileSystemLabel $p.L -Confirm:$false -Force | Out-Null
    } else {
      Format-Volume -Partition $part -FileSystem $p.F -NewFileSystemLabel $p.L -Confirm:$false -Force | Out-Null
    }
    $part = Get-Partition -DiskNumber $d.Number -PartitionNumber $part.PartitionNumber
    $v = $part | Get-Volume
    if ($v.FileSystemLabel -ne $p.L) { throw "etiket uyusmuyor: $($v.FileSystemLabel) / $($p.L)" }
    $log.Add("$($p.L) harf=$($part.DriveLetter) ofset=$($part.Offset) boy=$($part.Size) fs=$($v.FileSystem) kume=$($v.AllocationUnitSize)")
    $args2 += "$($p.L)=$($part.DriveLetter):"
  }
  $env:PYTHONIOENCODING = 'utf-8'
  $ErrorActionPreference = 'Continue'
  $py = & C:\Python312\python.exe "$base\doldur.py" "$base\manifest_win.json" @args2 2>&1 | Out-String
  $pyrc = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  $log.Add($py)
  if ($pyrc -ne 0) { throw "doldur.py cikis $pyrc" }
  Dismount-DiskImage -ImagePath $vhd | Out-Null
  Copy-Item $vhd "$base\win.vhd" -Force
  $log.Add('TAMAM')
} catch {
  $log.Add("HATA: $_")
  try { Dismount-DiskImage -ImagePath $vhd | Out-Null } catch {}
}
[IO.File]::WriteAllLines("$base\uret.txt", $log, (New-Object Text.UTF8Encoding $false))
