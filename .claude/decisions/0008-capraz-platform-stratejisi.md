# ADR 0008 — Capraz platform stratejisi

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Baglam
Proje Windows, Linux ve macOS'ta calismali. Python + PyQt5 zaten tasinabilir, ancak
kod birkac yerde Linux'a bagliydi: `mkfs.*` araclari, `st_blocks`, `/tmp`, `dd`
cagrisi, Wayland'a ozgu platform secimi.

## Karar

**1. Platforma bagli her sey tek modulde.** `core/platform.py`:
seyrek dosya isaretleme, gercek dosya boyutu, arac arama, surec calistirma,
Qt platform eklentisi secimi, varsayilan klasor.

**2. Cekirdek yetenekler harici araca bagli olmayacak.**
FAT12/16/32 ve exFAT saf Python (ADR 0001, ADR 0007). Bu sayede Windows ve macOS'ta
bolumleme + bicimlendirme + dosya erisimi eksiksiz calisir.
NTFS ve ext2/3/4 bicimlendirmesi yalnizca araci olan sistemlerde listelenir.

**3. Otomatik denetim.** `tests/platform_check.py` kaynak agacini tarar:
- sabit POSIX yollari, `st_blocks`, `tempfile`, dogrudan `subprocess`/`shutil.which`
- endian isareti tasimayan `struct` cagrilari
- `core/` icine PyQt sizintisi

Denetleyici kasitli ihlal dosyasiyla dogrulandi (9 ihlalin 9'unu yakaladi).

## Kabul edilen sinir
Windows ve macOS'ta **calistirilarak** test edilmedi; yalnizca kod yollari
soyutlandi ve statik denetimden gecti. Bu durum `.claude/docs/cross-platform.md`
icinde acikca belirtilmistir — "destekleniyor" ile "dogrulandi" ayrimi korunmalidir.
