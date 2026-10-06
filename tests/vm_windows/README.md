# Windows'a karsi gidis-donus sinamasi (VirtualBox "win10 ")

Ana makinede DEGIL, VM misafirinde Windows'un kendi araclariyla (CLAUDE.md
test ortami kurali). Betikler `\\VBoxSvr\GitHub\DiskUltimate\.tmp\vm\w1006`
klasorunu kullanir; once buraya kopyalanir:

```bash
mkdir -p .tmp/vm/w1006 && cp tests/vm_windows/* .tmp/vm/w1006/
PYTHONPATH=src python3 -c "from diskultimate.core.vdisk import VhdImage; \
  VhdImage.create_fixed('.tmp/vm/w1006/bos.vhd', 1600*1024*1024)"
```

1. Misafir (Administrator): `uret.ps1` — VHD'yi baglar, Windows `Format-Volume`
   ile NTFS 64K/4K/2M, exFAT 128K, FAT32 acar; `doldur.py` Windows API'leriyle
   doldurur (sabit bag, ADS, seyrek, sikistirilmis, 400 parcali, unicode/emoji)
   ve `manifest_win.json` yazar -> `win.vhd`.
2. Ana makine: `python3 .tmp/vm/w1006/isle.py` — okuma == manifest, iki kipli
   yedek copla dolu hedefe (`geri_kullanilan.vhd`), yazma, boyutlandirma
   -> `work.vhd`, `manifest_son.json`.
3. Misafir: `dogrula.ps1` — iki VHD'yi salt okunur baglar, her birimde
   `chkdsk` (salt okunur) + `dogrula.py` ile ozet karsilastirmasi.

Ilk kosu 2026-10-06: 10/10 birim chkdsk temiz, ozetler birebir (ADR 0094,
.claude/logs/2026-10-06-uyumluluk-denetimi.md).
