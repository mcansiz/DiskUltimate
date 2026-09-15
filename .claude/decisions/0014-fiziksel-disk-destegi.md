# ADR 0014 — Fiziksel disk destegi ve guvenlik tasarimi

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Kapsam degisikligi
Proje baslangicta **yalnizca goruntu dosyalari** uzerinde calisiyordu; CLAUDE.md
acikca "hicbir zaman gercek blok cihazina yazma yapilmaz" diyordu. Kullanici bu
kisiti kaldirdi: *"ana ekranda sistemdeki var olan diskleri de ekle, takilan her
diski gorebilelim ve her seyi yapabilelim — aynen DiskGenius gibi."*

Bu, projenin risk sinifini degistirir: artik yanlis bir secim **gercek veriyi**
yok edebilir. Ozellik eksiksiz uygulandi, ancak asagidaki katmanli korumalarla.

## Guvenlik katmanlari (`core/physical.py`)

1. **Listeleme zararsizdir.** `list_disks()` yalnizca isletim sisteminin bilgi
   arayuzlerini okur (Linux `/sys/block` + `/proc/mounts`, Windows IOCTL,
   macOS `diskutil`). **Hicbir sektor okunmaz, hicbir yazma yapilmaz.**

   > **Duzeltme (2026-09-15).** Bu madde onceden "hicbir diski acmaz" diyordu;
   > Windows icin **dogru degildi**. Orada boyut, model ve veriyolu yalnizca bir
   > aygit tutamaci uzerinden (`IOCTL_DISK_GET_LENGTH_INFO`,
   > `IOCTL_STORAGE_QUERY_PROPERTY`) sorgulanabilir, yani her aygit icin salt
   > okunur (`GENERIC_READ`, `FILE_SHARE_READ|WRITE`) bir tutamac acilip hemen
   > kapatilir. Linux ve macOS'ta tutamac acilmaz. Katmanin verdigi guvence
   > "aygit acilmaz" degil, **"veri okunmaz ve yazilmaz"** olarak
   > kesinlestirildi; davranis degismedi.
2. **Varsayilan salt okunur.** `PhysicalDisk(...)` yazma iznini ancak
   `readonly=False` **ve** `confirm=True` birlikte verildiginde acar.
3. **Sistem diski korumasi.** Isletim sisteminin bulundugu disk isaretlenir;
   yazmak icin ayrica `allow_system=True` gerekir, aksi halde `SystemDiskError`.
4. **Bilinmeyen disk korumasi.** Yetki yetersizligi nedeniyle bilgiler
   okunamadiysa (`info_complete=False`) disk "bilinmiyor" sayilir ve yazma
   **reddedilir** — eksik bilgiyi "risk yok" gibi sunmak kabul edilemez.
5. **Bagli bolum uyarisi.** Diskte bagli (mounted) bolum varsa listelenir ve
   arayuz yazma oncesi uyarir.
6. **Arayuz onaylari.** Yazma modu acilirken: (a) genel uyari onayi, (b) sistem
   diskinde **disk adini yazarak** dogrulama, (c) bagli bolum varsa ek onay.

## Neden "sil" degil de "katmanli onay"
Ozelligi reddetmek kullanicinin acik istegini gormezden gelmek olurdu; onaysiz
sunmak ise tek yanlis tiklamayla veri kaybi demekti. Secilen yol, DiskGenius'un
da yaptigi gibi ozelligi vermek ama **yikici olani sira disi kilmak**: okuma
akiciyken, yazma bilincli bir dizi adim gerektirir.

## Platform notlari
| | Linux | Windows | macOS |
|---|---|---|---|
| Listeleme | `/sys/block`, `/proc/mounts` | `IOCTL_DISK_*`, `IOCTL_STORAGE_QUERY_PROPERTY` | `diskutil -plist` |
| Yetki | root veya `disk` grubu | Yonetici (UAC yukseltmesi) | root |
| Sistem diski | `/` baglantisinin aygiti | `IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS` | `diskutil info` |

Windows'ta aygit G/C **sektor hizali** olmak zorundadir; `PhysicalDisk._win_read/
_win_write` hizalama disindaki kenarlari korumak icin oku-degistir-yaz uygular.

## Dogrulama durumu
- **Linux listeleme:** dogrulandi — sistem diski isaretlendi, bagli bolumler
  (`/`, `/boot/efi`) tespit edildi. Ana makinede **hicbir disk acilmadi**.
- **Guvenlik denetimleri:** sahte disk tanimlariyla dogrulandi (gercek aygita
  dokunmadan): onaysiz yazma, sistem diski, bilinmeyen disk — ucu de engellendi.
- **Windows yetkisiz davranis:** dogrulandi — disk listede gorunuyor, "(yetki yok)"
  isaretli, acma denemesi anlamli hata veriyor.
- **Windows yonetici ile okuma/yazma:** HENUZ DOGRULANMADI (bkz. worklog).
