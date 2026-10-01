# 0072 — ReFS: yalnizca Windows'un kendi araciyla, digerlerinde gri + neden

Tarih: 2026-10-01
Durum: **uygulandi** — uygunluk/ret yollari Linux ve Windows'ta sinandi.
**Gercek ReFS bicimlendirme sinanmadi** (asagida).
Ilgili: [0053](0053-sifreli-ve-kapsayici-birim-tanima.md) (ReFS taninmasi),
`.claude/docs/dosya-sistemi-genisletme.md` Asama 4.

## Karar

ReFS'in acik bir belirtimi yok; saf Python bicimlendirici yazilmaz. Windows'un
kendi `Format-Volume -FileSystem ReFS` yolu kullanilir — NTFS/FAT/exFAT icin
zaten var olan, fiziksel diskte disk/bolum numarasiyla calisan
`session._try_native_format` + `platform.windows_format_volume`.

* `formatter.FS_KINDS`: "refs" (`native_only=True`). Biçim listesinde her
  zaman gorunur; kullanilamiyorsa gri + neden:
  * Windows disi: "ReFS yalnizca Windows'un kendi araciyla olusturulabilir;
    <platform> uzerinde arac yok".
  * Windows ama surum uygun degil: Windows 10 1709'dan beri ReFS **olusturma**
    yalnizca Enterprise, Pro for Workstations ve Server'da. Surum kayit
    defterinden (`EditionID`, `InstallationType`) okunur — alt surec yok,
    arayuz is parcaciginda guvenli, onbellekli. Bilinmeyen surum reddedilir
    (yanlis "var" yerine gri). Education bilincli olarak disarida.
* Goruntu dosyasinda ReFS reddedilir (yerel arac yalnizca fiziksel birimde
  calisir; gecici VHD baglayip bicimlendirmek yonetici + disk numarasi
  eslestirmesi gerektirir ve sinanamadigi icin eklenmedi).
* Yerel arac hata verirse saf Python yola **dusulmez**; Windows'un mesaji
  gosterilir. Basarisiz bicimlendirmede bolum tabloya eklenmez (mevcut geri
  alma).
* `platform.format_volume_command` saf islev: disk/bolum tamsayi, etiketten
  `"`, `$`, `` ` `` temizlenir (PowerShell enjeksiyonu yok).

## Dogrulama
* Linux: gri + neden, `format_partition` ve `create_partition` reddi, bolum
  eklenmiyor (t71).
* Windows (VBox win10 = Windows 10 Pro, SKU 48): gercek kayit defterinden
  "Professional" okundu, ReFS gri ve neden surumu gosteriyor; Format-Volume
  komutu PowerShell ayristiricisiyla **calistirilmadan** sozdizimi hatasiz.
* **Sinanmayan:** gercek ReFS bicimlendirme — misafir Pro surumu (ReFS
  olusturamaz) ve guestcontrol yonetici yetkisi vermiyor. Enterprise /
  Server misafirinde yonetici olarak fiziksel (sanal) ikinci diskte
  denenmeli.
