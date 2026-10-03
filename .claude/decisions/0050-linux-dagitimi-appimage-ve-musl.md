# 0050 — Linux dagitimi: AppImage; musl sistemlerde kaynaktan calistirma

Tarih: 2026-09-28
Durum: **uygulandi (2026-10-03)** — yontem plandan farkli; asagidaki
"Uygulama" bolumu gecerlidir. Ilk plan (PyInstaller onedir + Mint VM'de
derleme) tarihce olarak duruyor.
Ilgili: ADR 0042 (acilista kosulsuz yetki), ADR 0044 (uygulama ikonu),
ADR 0021 (ayni aygita ikinci tutamac yok), `DiskUltimate.spec`,
`build_linux.sh`

## Bugunku durum

`build_linux.sh` PyInstaller ile tek dosyalik `dist/DiskUltimate` uretir.
Tasinabilir tek dosyadir ama:

- **Masaustu girdisi ve dosya yoneticisi ikonu yok** (worklog 2026-09-21:
  Linux'ta pencere ikonu calisma aninda gelir, dosyanin kendi ikonu olmaz).
- **Derleme makinesine bagli**: PyInstaller ikilisi yalnizca derlendigi
  makinenin glibc surumu **ve daha yenisi** olan sistemlerde calisir.
  Gelistirme makinesi glibc 2.43, Mint 22.3 test VM'i 2.39 (2026-09-28'de
  olculdu) — gelistirme makinesinde derlenen ikili Mint'te bile acilmaz.

## Karar

### 1. Linux icin birincil dagitim bicimi AppImage

Tek dosya, dagitimdan bagimsiz, kurulum gerektirmez; ikon ve `.desktop`
girdisi icinde gelir (AppImageLauncher gibi araclar menuye ekler).

Degerlendirilip elenenler:

| Bicim | Neden degil |
|---|---|
| Flatpak / Snap (strict) | Korumali ortam **ham disk erisimini** engeller; uygulamanin varlik sebebi o. |
| Yalnizca .deb | Dogal ama yalnizca Debian ailesi. AppImage'dan sonra **ek** secenek olabilir: ayni PyInstaller ciktisini ve bir polkit tanimini kullanir; parola penceresinde "pkexec env ..." yerine uygulamanin adi gorunur. |

### 2. musl sistemlerde (Alpine, Void-musl, Chimera) kaynaktan calistirma

glibc'ye gore derlenmis ikili musl'da calismaz: PyInstaller baslaticisi,
gomulu libpython ve PyPI'daki PyQt5/Qt (manylinux) glibc'ye baglidir;
program glibc yukleyicisini (`/lib64/ld-linux-x86-64.so.2`) arar ve tek
satirlik "not found" ile duser. Alpine'in `gcompat` katmani Qt + Python
eklenti modulleri gibi agir bir yukte guvenilir degildir.

- **Resmi musl ikilisi uretilmez.** PyPI'da musl icin PyQt5 tekerlegi yok;
  Alpine'in sistem PyQt5'iyle ayri derleme hatti ve ayri test gerekirdi —
  musl masaustu kullanicisi az, yuk karsiligini vermez.
- **Desteklenen yol kaynaktan calistirma**: cekirdek saf Python, tek
  bagimlilik PyQt5 (CLAUDE.md). Alpine: `apk add python3 py3-qt5` ve
  `python3 main.py`. README'ye yazilir.
- musl dagitimlarinda polkit/`pkexec` cogu zaman kurulu degildir (Alpine
  `doas` kullanir). ADR 0042'deki otomatik yetki `pkexec` bekler; yoksa kod
  acik mesaj verip yetkisiz acilir. Fiziksel disk icin: `doas python3 main.py`
  — README'ye yazilir.
- Ileride talep olursa Alpine `APKBUILD` (saf Python paket; ikili derlemekten
  cok daha basit).

## Yapilacaklar (siradaki oturum)

1. **`$APPIMAGE` duzeltmesi — AppImage'dan ONCE, zorunlu.**
   `core/platform.py:240-241` paketlenmis kopyayi `sys.executable` ile
   yeniden baslatir. AppImage icinde bu yol kullanicinin FUSE ile bagladigi
   `/tmp/.mount_...` klasorunu gosterir; FUSE baglantisina varsayilan olarak
   **root bile erisemez** -> `pkexec` ile yetkili kopya baslamaz, fiziksel
   diskler hic acilamaz. AppImage icindeyken (`APPIMAGE` ortam degiskeni
   var) komut `[os.environ["APPIMAGE"]] + argv` olmali; root dosyayi kendisi
   baglar. Test: `run_all` (komut uretimi, t22 benzeri) + Mint VM'de gercek
   yetki alma.
2. **AppDir**: PyInstaller'i `onedir` ciktisiyla calistir (onefile'in
   kendi /tmp acilimi AppImage'in icinde gereksiz ikinci katman), ciktiyi
   `AppDir/usr/bin/` altina koy; `AppDir/diskultimate.desktop`,
   `AppDir/diskultimate.png` (`assets/branding` kaynaktan, 256 px),
   `AppDir/AppRun`.
3. **`AppRun` bir kabuk betigi**: glibc yukleyicisi yoksa (musl) anlasilir
   mesajla dur — "Bu paket glibc ister; musl sistemlerde kaynaktan
   calistirin: apk add python3 py3-qt5 && python3 main.py" — sonra
   `usr/bin/DiskUltimate "$@"`.
4. **`appimagetool`** gelistirme araci olarak (kullanici kurmaz; CLAUDE.md
   gelistirme araci istisnasi). `build_linux.sh`e `--appimage` secenegi ya da
   ayri `build_appimage.sh`.
5. **Derleme tabani**: Mint 22.3 VM'de (glibc 2.39) derle -> Ubuntu 24.04 /
   Mint 22 ve sonrasi. Daha genis kapsam istenirse Ubuntu 22.04 (2.35) tabani.
   Gelistirme makinesinde (2.43) derlenen AppImage yayimlanmaz.
6. **Mint VM'de dogrula**: acilis, `pkexec` ile yetki alma, bos test diskine
   (sdb) yazma, dosya yoneticisinde ikon, AppImageLauncher ile menu girdisi.
7. README: Linux (AppImage), musl (kaynaktan + `doas`).

## Dogrulanmamis varsayimlar (siradaki oturumda olculecek)

- Yeni `appimagetool` surumlerinin **statik** calistirici (runtime) urettigi,
  yani `libfuse2` gerektirmedigi (Ubuntu 22.04+ `libfuse2`yi varsayilan
  kurmaz). FUSE hic yoksa `--appimage-extract-and-run` yedegi.
- Bu statik calisiricinin musl'da da acildigi (acilirsa `AppRun` mesaji
  gorunur; acilmazsa kullanici yine anlamsiz hata gorur — o durumda musl
  notu yalnizca README'de kalir).
- `pkexec "$APPIMAGE"` ile root'un AppImage'i kendisi baglayip calistirdigi.


## Uygulama (2026-10-03)

Plan PyInstaller ciktisini eski glibc'li Mint VM'de derlemeyi oneriyordu.
Linux VM 2026-09-29'dan beri yok, ana makinede root ve konteyner araci da
yok. Bu yuzden **derleme tabani sorunu baska yoldan cozuldu**:

* `niess/python-appimage` **manylinux2014** (glibc 2.17) Python 3.12.14 —
  tasinabilir yorumlayici, eski bir derleme makinesi gerektirmez.
* PyQt5 PyPI tekerlekleri (PyQt5 5.15.11, PyQt5-Qt5 5.15.19, sip 12.19.0)
  zaten manylinux_2_17.
* PyInstaller yok: kaynak `usr/share/diskultimate` altinda, `AppRun`
  paketteki Python'la `main.py`yi calistirir (`-s -E`, kullanicinin
  `PYTHONPATH`i karismaz). musl'da (glibc yukleyicisi yok) anlasilir mesaj.
* Kirpma: uygulama yalnizca QtCore/QtGui/QtWidgets kullanir; Qt
  kutuphanelerinden Core, Gui, Widgets, DBus, XcbQpa, WaylandClient, Svg
  kalir; QML/Quick/Multimedia/... ve tkinter, test, pip silinir. Arac kalan
  her ELF'in Qt/ICU `NEEDED`larini denetler, eksik varsa durur.
* `appimagetool` 1.9.1 (statik calisma zamani; libfuse2 gerekmez).
  Araclarin SHA-256'si `tools/appimage.py` icinde sabit.
* Giris: `./build_appimage.sh` -> `tools/appimage.py`.

**`$APPIMAGE` duzeltmesi** (planin 1. maddesi) yapildi:
`platform._relaunch_target()` AppImage icindeyse `[$APPIMAGE] + argv`
dondurur. Ayrica `paths.IS_APPIMAGE` / `IS_PACKAGED`: kod `$APPDIR`
altindaysa paketlenmis kopya sayilir; gunluk ve gecici dosyalar kullanicinin
veri dizinine yazilir (aksi halde salt okunur FUSE baglantisina yazmaya
calisirdi). t22 komut uretimini sinar.

### Olculenler

* Paket 39.6 MB; icindeki butun ELF dosyalarinda gereken en yuksek
  surum **GLIBC_2.17**.
* Gelistirme makinesinde (Kubuntu, glibc 2.43, Wayland): offscreen ve gercek
  ekranda (xcb/XWayland) acildi; gunluk `~/.local/state/DiskUltimate/`
  altina yazildi, Qt cevirisi yuklendi, surum 0.5.0-beta.
* `tests.ui_smoke` paketin **kendi** Python'u ve kirpilmis PyQt5'iyle
  tamamen gecti (butun pencereler).
* `libqxcb` / `libqwayland-generic` / GLX eklentisinin sistem bagimliliklari
  bu makinede tam. Bunlar (libxcb-*, libxkbcommon-x11, libGL, fontconfig)
  pakete konmaz; README en kucuk kurulum icin paket listesini verir.

* **`pkexec "$APPIMAGE"` (kullanici, 2026-10-03, ana makine):** parola
  soruldu, yetkili kopya acildi. Gunluk: acilis kopyasi "yetkili kopya
  acildi; acilis kopyasi kapaniyor", root kopyasi fiziksel diski erisim
  hatasi olmadan listeledi. Root, AppImage'i kendisi bagladi (FUSE).
* Yan bulgu (AppImage'a ozgu degil): root kopyanin **tanilama gunlugu**
  root'a ait kaliyor; `restore_owner` gunluklere uygulanmiyor. Kaynaktan
  yetkili calismalarin `.claude/logs` dosyalari da ayni (Eylul'den beri).

### Olculmeyenler

* Eski bir dagitimda calistirma (glibc 2.17 iddiasi yalnizca sembol
  surumlerinden olculdu).
* musl'da `AppRun` mesajinin gorunmesi.
* Stil: pakette Qt'nin kendi "Fusion" stili kullanilir; dagitimin PyQt5'iyle
  gelen KDE/Breeze stil eklentisi pakette yoktur.
