# 0050 — Linux dagitimi: AppImage; musl sistemlerde kaynaktan calistirma

Tarih: 2026-09-28
Durum: **planlandi** — uygulanmadi (kullanici: "AppImage islemini baska bir
oturumda yapacagim")
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
