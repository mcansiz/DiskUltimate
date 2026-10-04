# Test verisi kaynaklari

Projenin kendi uretmedigi ya da baska bir aracin ciktisi olan test goruntuleri.

| Dosya | Kaynak | Lisans / not |
|---|---|---|
| `apfs_dfvfs.raw.gz` | log2timeline/dfvfs `test_data/apfs.raw` (macOS'ta olusturulmus APFS kapsayicisi). SHA-256 (ham): `e3e3adcbbf189403d892b013d6cba155f2e58e42ff5eb541ec681c37a91a3f29` | Apache License 2.0 — https://github.com/log2timeline/dfvfs |
| `apfs_dfvfs_sifreli.dmg.gz` | log2timeline/dfvfs `test_data/apfs_encrypted.dmg` (GPT + sifreli APFS birimi). SHA-256 (ham): `33fe6f183aeb1a95fec68efdab17d59aedbad3d8ef1a117d411117376d9d8485` | Apache License 2.0 |
| `apfs_mkapfs.img.gz` | apfsprogs 0.2.1 `mkapfs` (bos birim, etiket "Türkçe Birim") | uretilen veri |
| `ntfs_windows.img.gz`, `udf_windows.img.gz` | Windows 10 surucusunun VirtualBox misafirinde yazdigi birimler (ADR 0058, 0063) | uretilen veri |
| `exfat_windows.img.gz` | Windows 10 diskpart `format fs=exfat` (48 MB, Windows yerlesimi: yigin onunde bosluk) + Windows surucusuyle yazilan 3 dosya; ozetler `exfat_windows.sha1.txt` (ADR 0084) | uretilen veri |
| digerleri | mkfs/xorriso/genisoimage/sload gibi araclarla bu projede uretildi (ilgili ADR'ler) | uretilen veri |
