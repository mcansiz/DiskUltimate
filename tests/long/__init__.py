"""Uzun (derin) testler — ADR 0080.

    python3 -m tests.long --liste
    python3 -m tests.long --profil quick --fs ntfs --tablo gpt
    python3 -m tests.long --profil full --fs exfat --tablo mbr --tohum 1234

Gercek kod yolunu (DiskSession + kuyruk) her dosya sistemi ve bolum tablosu
icin uctan uca, buyuk verilerle kosar; her adimdan sonra butun dosyalari
ozetle dogrular; sonucu JSON rapor olarak yazar (`tests.long.report`
birlestirir). Yalnizca goruntu dosyasi kullanir; cekirdek baglama ve aygit
yolu yalnizca istenirse ve CI'da (ADR 0080, CLAUDE.md test kurali).
"""
