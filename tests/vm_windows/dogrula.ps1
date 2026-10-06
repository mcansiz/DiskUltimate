$ErrorActionPreference = 'Stop'
$base = '\\VBoxSvr\GitHub\DiskUltimate\.tmp\vm\w1006'
$log = New-Object System.Collections.Generic.List[string]
$env:PYTHONIOENCODING = 'utf-8'
foreach ($pair in @(@('work.vhd','manifest_son.json'), @('geri_kullanilan.vhd','manifest_win.json'))) {
  $name = $pair[0]; $vhd = "C:\du-test\$name"
  $log.Add("===== $name")
  try {
    Copy-Item "$base\$name" $vhd -Force
    $img = Mount-DiskImage -ImagePath $vhd -Access ReadOnly -PassThru
    Start-Sleep 4
    $d = $img | Get-Disk
    $pyargs = @()
    foreach ($part in (Get-Partition -DiskNumber $d.Number | Sort-Object Offset)) {
      $v = $part | Get-Volume -ErrorAction SilentlyContinue
      if (-not $v -or -not $v.FileSystemLabel) { continue }
      $log.Add("-- $($v.FileSystemLabel): fs=$($v.FileSystem) boy=$([math]::Round($v.Size/1MB)) MiB bos=$([math]::Round($v.SizeRemaining/1MB)) MiB saglik=$($v.HealthStatus)")
      $ErrorActionPreference = 'Continue'
      $c = & chkdsk.exe $v.Path.TrimEnd('\') 2>&1 | Out-String
      $rc = $LASTEXITCODE
      $ErrorActionPreference = 'Stop'
      $log.Add("   chkdsk cikis $rc")
      $ilgili = $c -split "`r?`n" | Where-Object { $_ -match 'error|Error|corrupt|found|problem|bad|incorrect|orphan|Correcting|fix|scanned the file system|no problems|No further action' } | Select-Object -First 15
      foreach ($l in $ilgili) { $log.Add("     " + $l.Trim()) }
      $pyargs += "$($v.FileSystemLabel)=$($v.Path)"
    }
    $ErrorActionPreference = 'Continue'
    $py = & C:\Python312\python.exe "$base\dogrula.py" "$base\$($pair[1])" @pyargs 2>&1 | Out-String
    $ErrorActionPreference = 'Stop'
    $log.Add($py)
    Dismount-DiskImage -ImagePath $vhd | Out-Null
    Remove-Item $vhd -Force
  } catch {
    $log.Add("HATA: $_")
    try { Dismount-DiskImage -ImagePath $vhd | Out-Null } catch {}
  }
}
[IO.File]::WriteAllLines("$base\dogrula.txt", $log, (New-Object Text.UTF8Encoding $false))
