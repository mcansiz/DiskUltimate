# `.dub` Yedek Bicimi (DiskUltimate Backup)

Uygulama: [`src/diskultimate/core/clone.py`](../../src/diskultimate/core/clone.py)

Blok tabanli, sifir bloklari atlayan, istege bagli zlib sikistirmali yedek bicimi.

## Yerlesim

```
[ Baslik: 512 bayt ]
[ Indeks: blok_sayisi x 16 bayt ]
[ Veri blogu 0 ][ Veri blogu 1 ]...   (yalnizca sifir olmayan bloklar)
```

## Baslik (512 bayt, little-endian)

| Ofset | Boyut | Alan |
|---|---|---|
| 0 | 8 | `DUBACKUP` |
| 8 | 2 | Surum (1) |
| 10 | 2 | Bayraklar (bit 0: zlib sikistirmasi) |
| 12 | 4 | Blok boyutu (bayt, varsayilan 1 MiB) |
| 16 | 8 | Kaynak toplam boyut (bayt) |
| 24 | 4 | Sektor boyutu |
| 28 | 8 | Olusturma zamani (Unix) |
| 36 | 4 | Blok sayisi |
| 40 | 32 | Dosya sistemi adi (UTF-8) |
| 72 | 64 | Birim etiketi (UTF-8) |
| 510 | 2 | `0xAA55` |

## Indeks girisi (16 bayt)

| Ofset | Boyut | Alan |
|---|---|---|
| 0 | 1 | Tur: `0` sifir · `1` ham · `2` zlib |
| 1 | 3 | Ayrilmis |
| 4 | 4 | Dosyadaki uzunluk |
| 8 | 8 | Dosyadaki ofset |

## Tasarim gerekceleri

- **Sifir bloklar dosyada yer kaplamaz.** Bos alani cok olan bolumlerin yedegi cok
  kucuk olur: testte 400 MB'lik FAT32 bolumu 34 KB'a indi.
- **Geri yuklemede seyreklik korunur.** Sifir blok, hedefte zaten sifirsa yazilmaz;
  boylece hedef goruntu sisirilmez.
- **Baslik tek basina anlamlidir.** Geri yuklemeden once boyut, dosya sistemi,
  etiket ve tarih gosterilerek onay alinir.
- **Blok basina sikistirma.** Sikisma kazanc saglamiyorsa blok ham yazilir; boylece
  sikistirilamayan veride buyume olmaz.

## Kullanim

```python
from diskultimate.core.clone import backup, restore, read_backup_info
bilgi = backup(view, "bolum.dub", compress=True, fs_type="FAT32", label="VERI")
print(read_backup_info("bolum.dub").summary())
restore("bolum.dub", view)
```
