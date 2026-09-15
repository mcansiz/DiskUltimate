# Is Gunlugu

Kural: her anlamli degisiklikten sonra bu dosyaya tarihli bir giris eklenir.
Kayit yalnizca bu depo icindeki `.claude/` altinda tutulur.

---

## 2026-09-15 (2) — Tam tutarlilik denetimi (yalnizca analiz, kod degismedi)

Kullanici istegi: projeyi bastan sona analiz et, tutarsizliklari bul, GUI
tarafinda ayni isimlendirme kullanilip kullanilmadigini arastir, tum `.md`
dosyalarini incele ve yapilacaklar icin plan olustur.

### Yontem
Kod degistirilmedi. Once olcum tabani kuruldu, sonra bulgular ona gore yazildi:

| Kosum | Sonuc |
|---|---|
| `python -m tests.run_all` | 18/18 basarili (t05 harici `mkfs` yok, atlandi) |
| `python -m tests.platform_check` | 0 bulgu |

Tanimlayici dili denetimi icin `ast` tabanli tek seferlik bir tarayici yazildi
(fonksiyon adlari + parametreler), gecici alanda calistirildi.

### Bulgular (ozet)
- **Belge kaymasi:** README.md tek guncel belge. `project-overview.md`,
  `cross-platform.md`, `testing.md`, `diskgenius-parity.md`, `feature-analysis.md`
  ve `architecture.md` v0.2.0 doneminde donmus — surum, test sayisi (15 vs 18),
  saf Python NTFS/ext durumu ve fiziksel disk kapsami yanlis anlatiliyor.
  `project-overview.md` "root gerekmez / blok aygita yazilmaz" ilkesi CLAUDE.md ve
  ADR 0014 ile dogrudan celisiyor. `architecture.md` modul agacinda 7 modul eksik.
- **Kodda yanlis bilgi:** `APP_VERSION = "0.1.0"` (README 0.3.0) ve "Hakkinda"
  penceresi hala "mkfs araclariyla bicimlendirme" + "yonetici yetkisi gerektirmez"
  diyor.
- **GUI etiket ikiligi:** bes eylem menude ve sag tik menusunde farkli adla
  gorunuyor ("Yeni bolum..." / "Yeni bolum olustur..." vb.). Kok neden: sag tik
  menuleri mevcut `QAction` yerine etiketi yeniden yaziyor.
- **Sag tik menusu yetki denetimini atliyor:** `rename_partition`, `change_type`,
  `toggle_bootable` salt okunur kaynakta pasiflesmiyor. **Veri riski yok** —
  `session._require_writable()` reddediyor — ama kullanici dostane diyalog yerine
  ham hata metni goruyor.
- **Isimlendirme kurali:** "Turkce arayuz metni, Ingilizce kod adi" kurali 13
  dosyada 74 fonksiyonda ihlal edilmis. En yeni dosyalar en cok sapanlar
  (`resize_bar.py` bastan asagi Turkce). `_salt_okunur_acilis_uyarisi()` ile
  `_read_only_uyarisi()` yan yana duruyor.
- **Depo hijyeni:** `.claude/logs/app-*.log` calisma zamani gunlukleri git ile
  izleniyor ve gitignore disinda; uygulamayi her calistirmak agaci kirletiyor.
  `sessions/INDEX.md` ayni oturum icin mukerrer satir aliyor.

### Cikti
`.claude/docs/consistency-audit.md` — bulgular (A/B/C) ve alti asamali
iyilestirme plani.

---

## 2026-09-15 (3) — Iyilestirme plani: Asama 1, 2 ve 6 uygulandi

`consistency-audit.md` planinin ilk turu. Sira onerildigi gibi: once yanlis bilgi
veren kod, sonra GUI birligi, sonra depo hijyeni.

### Asama 1 — yanlis bilgi veren kod
- `APP_VERSION` `"0.1.0"` → `"0.3.0"`. Yanina, degistirildiginde README rozetinin
  ve `project-overview.md` durum satirinin da guncellenmesi gerektigi yazildi.
- "Hakkinda" penceresi yeniden yazildi. Eski metin iki yanlis soyluyordu:
  exFAT/NTFS/ext'in `mkfs` araclariyla bicimlendirildigi (artik sekizi de saf
  Python) ve uygulamanin "yalnizca secilen goruntu dosyasi uzerinde calistigi"
  (ADR 0014'ten beri fiziksel disk destegi var). Yeni metin fiziksel disk
  erisiminin varsayilan salt okunur oldugunu da soyluyor.

### Asama 2 — GUI etiket ve yetki birligi
Kok neden, sag tik menulerinin etiketi mevcut `QAction` yerine **yeniden
yazmasiydi**. `_partition_menu`, `_free_menu`, `_map_context` ve `_tree_context`
artik eylem nesnelerini dogrudan ekliyor. Boylece:

- Bes etiket ikiligi kapandi ("Yeni bolum olustur..." → "Yeni bolum...",
  "Guvenli sil..." → "Bolumu guvenli sil...", vb.).
- `_update_actions()` yetki denetimi sag tik menusune de islemeye basladi;
  salt okunur kaynakta "Bolum turunu degistir", "Bolum adini degistir" ve
  onyukleme bayragi artik pasif gorunuyor (onceden etkin gorunup is yapmiyordu).
- Salt okunur aciklama girisi tek metne indi (`_readonly_hint`).
- Onyukleme bayragi tek eylem, metni simetrik cift olarak `_update_actions()`
  icinde degisiyor: `BOOT_SET_TEXT` / `BOOT_CLEAR_TEXT`.
- Sekme indisleri `TAB_FILES` / `TAB_INFO` / `TAB_HEX` / `TAB_LOG` sabitlerine bagli.
- `_read_only_uyarisi` → `_readonly_warning`,
  `_salt_okunur_acilis_uyarisi` → `_readonly_open_warning` (Asama 3'ten one alindi;
  yeni `_readonly_hint` ile ayni adlandirmayi paylasmalari gerekiyordu).

**Tuzak:** onyukleme etiketini guncellemek icin ilk denemede `_current_partition()`
kullanildi — ama o islev kullaniciya diyalog acar ve `_update_actions()` her
yenilemede cagrilir. Sessiz bir `_selected_partition_quiet()` eklendi.

**Plan degisikligi:** 2.4 maddesi (`_require_session()` ekle) **gereksiz cikti**.
Dort islev de `_current_partition()` uzerinden zaten koruniyordu; denetim
belgesindeki aksi yondeki iddia duzeltildi. Kusur yalnizca gorseldi.

### Asama 6 — depo hijyeni
- `.gitignore` icine `.claude/logs/*.log`; izlenen iki gunluk dosyasi
  `git rm --cached` ile cikarildi. Uygulamayi calistirmak artik calisma agacini
  kirletmiyor. Konum degistirilmedi: `.claude/logs/` bilerek tasarlanmis bir
  ozellik (parity: "Islem gunlugu: arayuzde sekme + dosya"); ignore kurali
  `*.md` belge dokumleriyle `*.log` calisma ciktisini ayiriyor.
- `archive-session.py`: `append_index` → `upsert_index`. Bir oturum birden cok
  `SessionEnd` uretebiliyor (once `other`, sonra `exit`); eski kod her seferinde
  satir ekliyordu. Artik anahtar dokum dosyasinin adi ve satir yerinde guncelleniyor.
- `INDEX.md` basligindaki "`Ham dokumler .gitignore disidir`" cumlesi gercegin
  tersini soyluyordu; duzeltildi. Mevcut mukerrer satir temizlendi.

### Dogrulama
- `tests.run_all` → **18/18** · `tests.platform_check` → **0 bulgu**
- `tests.ui_smoke` → 17 ekran goruntusu, ana pencere gozle denetlendi
- Baglam menusu icin tek seferlik bir olcum betigi yazildi: salt okunur ve
  yazilabilir iki durumda menu etiketleri + etkinlik durumu arac cubugununkiyle
  **birebir** ayni cikti.
- Kanca, ayni oturum id'si iki kez bitirilerek denendi: tek satir, guncel `reason`.

> **Not:** PyQt5 bu makinede yalnizca Python **3.12**'de kurulu (varsayilan
> yorumlayici 3.14). Arayuz testleri `py -3.12 -m tests.ui_smoke` ile calistirildi.

### Yan gozlem (plan disi, ileriye not)
`01-ana-pencere.png` durum cubugunda `status_file` etiketi ile
`statusBar().showMessage()` gecici mesaji ust uste biniyor. Bu degisikliklerle
ilgisi yok, onceden de vardi; Asama 4 temizligine aday.

### Kalan
Asama 5 (belgeleri gercege esitle), Asama 3 (isimlendirme + denetim), Asama 4 (temizlik).

---

## 2026-09-15 (4) — Iyilestirme plani tamamlandi: Asama 5, 3 ve 4

### Asama 5 — belgeler gercege esitlendi
Kod degistirilmedi; on belge guncellendi.

- `project-overview.md` bastan yazildi: durum v0.3.0 / 18 test, "Temel ilkeler"
  maddesi 1 artik fiziksel disk gercegini anlatiyor (eski hali *"hicbir kod blok
  aygita yazmaz"* diyordu ve ADR 0014 ile dogrudan celisiyordu). v0.3 bolumu
  "planlanan"dan "tamamlandi"ya alindi, v0.4/v0.5 ayrildi, "Kapsam disi" bolumu
  eklendi.
- `architecture.md`: eksik **7 modul** agaca eklendi (`ext`, `ntfs`,
  `_ntfs_data`, `resize`, `physical`, `resize_bar`, `dialogs/resize`), veri
  akisina fiziksel disk ve boyutlandirma kollari cizildi, "Genisletme
  noktalari"na iki satir eklendi.
- `cross-platform.md`: NTFS/ext artik "yalnizca Linux" degil, uc platformda saf
  Python. Test durumu tablosu **surum ayrimi** yapacak sekilde yeniden yazildi.
- `testing.md`: `cd /home/pc/diskUltimate` kaldirildi, t16/t17/t18 satirlari
  eklendi, ekran goruntusu sayisi duzeltildi.
- `diskgenius-parity.md`: ozet tablosu belgeden **otomatik sayilarak** yeniden
  uretildi (eski sayimlar boyutlandirma tamamlanmadan onceydi). "Kapsam disi"
  olcutu yeniden tanimlandi.
- `feature-analysis.md` §4.5 ve ADR 0002 / 0017 / 0019 basliklari duzeltildi.
- Belge ici baglantilarin tamami betikle dogrulandi: **0 kirik baglanti**.

> **Dikkat edilen nokta:** ilk denemede Windows/Pop!_OS test satirlari toplu
> arama-degistirme ile "15/15 → 18/18" yapilmisti. Bu **yanlis bir iddia**
> olurdu: o kosumlar v0.2.0 doneminde, 15 testle yapildi. Geri alinip tablo
> hangi surumun nerede kosuldugunu ayiracak sekilde yazildi.

### Asama 3 — isimlendirme kurali geri getirildi
Once **denetim**, sonra ceviri (plandaki 3.1 sarti).

- `tests/platform_check.py` icine AST tabanli **tanimlayici dili denetimi**
  eklendi: fonksiyon adlari + parametreler Turkce sozcuk parcasi tasiyamaz.
  Gecici `ISIM_MUAFIYETI` listesiyle baslandi (17 dosya), dosyalar cevrildikce
  bosaltildi. Liste **bos**; denetim artik tum agaci koruyor.
  Muafiyet anahtari dosya adi degil **goreli yol** — `core/resize.py` ile
  `ui/dialogs/resize.py` ayni adi tasiyor ve ilk surumde ikincisi yanlislikla
  muaf kalmisti.
- Cevrilen: 17 dosya, ~92 fonksiyon. `ui/` (main_window, theme, resize_bar,
  dialogs) ve `core/` (ntfs, resize, physical, ext, exfat, clone, convert, gpt,
  image, recovery, vdisk).
- `_read_only_uyarisi`/`_salt_okunur_acilis_uyarisi` ikiligi zaten (3) oturumunda
  kapanmisti; `readonly` yazimi tek bicime indi.

> **Tuzak ve duzeltmesi.** Ilk ceviri duz duzenli ifadeyle yapildi ve
> **Turkce arayuz metinlerini bozdu**: `"Tum bolumler 4K..."` → `"Tum partitions
> 4K..."`, `"Kayip bolumler taraniyor"` → `"Kayip partitions taraniyor"`.
> Dosya yedekten geri alinip `tokenize` tabanli bir arac yazildi: yalnizca
> `NAME` belirtecleri degisir, dize ve yorum belirtecleri ellenmez.
> Sonrasinda tum degisen dosyalarin dize sabitleri HEAD ile karsilastirildi —
> kaybolan 22 dizenin tamami Asama 2'de bilerek birlestirilen menu etiketleri
> cikti, kaza yok. Tek gercek bozulma (`resize_bar` docstring'inde
> "tek adim" → "tek step") elle geri alindi.

### Asama 4 — kucuk temizlik
- `_icon()` kopyasi kaldirildi → `theme.standard_icon(widget, std)`.
- Widget API alan adiyla hizalandi: `set_data`→`set_partitions`,
  `set_values`→`set_range`, `ResizeBar.changed`→`rangeChanged`.
  `plan.changed` (farkli bir alan) **kasten** degistirilmedi.
- `ui_smoke` ekran goruntusu numaralari siralandi (14/15 yer degistirmisti);
  sekme indisleri `TAB_*` sabitlerine baglandi.
- `run_all` artik atlanan testi gizlemiyor: `Atlandi` istisnasi eklendi, ozet
  `17/18 basarili · 1 atlandi` diyor ve nedenini yaziyor. Cikis kodu 0 kalir.
  Eskiden atlanan test "TAMAM" sayilip ozet "18/18" diyordu.
- **`theme.STYLESHEET` silinmedi.** Denetim belgesindeki "belgesiz duruyor"
  iddiasi yanlisti: ADR 0013 bu kodu "ileriye donuk" basligi altinda bilerek
  birakmis ve `DISKULTIMATE_THEME=diskultimate` kacisini belgelemis. Gercek
  eksik, dalin **hic test edilmemesiydi**; `ui_smoke` icine temayi acip
  ADR 0012'deki sekme kirpilmasini olcen bir adim eklendi (16. goruntu).

### Dogrulama
- `tests.run_all` → **17/18 · 1 atlandi** (t05, harici `mkfs` yok)
- `tests.platform_check` → **0 bulgu**, muafiyet listesi bos
- `tests.ui_smoke` → **18 ekran goruntusu**, tema dali dahil
- Denetimin kendisi **olumsuz testten** gecti: gecici olarak eklenen
  `_yeni_bolum_ekle(sektor)` yakalandi, silinince tekrar 0 bulgu.
- Baglam menusu olcumu yinelendi: etiketler arac cubuguyla ayni, salt okunurda
  yazma eylemlerinin tamami pasif.

### Kalan (plan disi, ileriye)
- Yerel degisken adlari (fonksiyon govdesi ici) hala yer yer Turkce; denetim
  fonksiyon adi + parametre duzeyinde. Genisletilebilir.
- Durum cubugunda `status_file` ile gecici mesajin ust uste binmesi (bkz. 3.
  oturum notu) duzeltilmedi.

---

## 2026-09-15 (5) — Kalan iki madde: durum cubugu ve yerel degisken adlari

### 1. Durum cubugu cakismasi (gercek arayuz hatasi)
`01-ana-pencere.png` icinde sol altta iki metin ust uste biniyordu. Olculdu:
`showMessage()` gecici mesaji durum cubugunun **sol** bolgesine cizer — yani
`status_file` etiketiyle ayni yere. Qt'nin bu durumda normal widget'lari
gizlemesi beklenir; **PyQt5 5.15'te gizlemiyor**. Cikplak Qt ile kurulan en kucuk
ornekte de ayni davranis gorulduğu icin sorunun bizim kodumuzda olmadigi
dogrulandi, cozum kendi tarafimizda uygulandi:

```python
self.statusBar().messageChanged.connect(
    lambda metin: self.status_file.setVisible(not metin))
```

Dogrulama: mesaj etkinken `visible=False`, mesaj suresi dolunca `visible=True`
ve etiket metni korunuyor.

### 2. Yerel degisken adlari
Olcum: **656 Turkce yerel atama, 168 farkli ad, 20 dosya.**

Bu is icin kapsam farkindali bir cevirici yazildi (`local_rename.py`): yalnizca
`ast.Name` dugumlerini konumlarina gore degistirir. Boylece dizeler, yorumlar,
oznitelikler (`x.ad`), anahtar argumanlar (`f(ad=...)`) ve iceri aktarma adlari
**dokunulmadan** kalir. Her fonksiyon kapsami ayri degerlendirilir; ic ice
fonksiyon ayni adi yeniden baglarsa o ad atlanir, hedef ad kapsamda zaten
varsa cakisma bildirilip atlanir (3 yerde oldu).

Sonuc: 1871 degisiklik, ardindan kalan 12 ad (modul sabitleri + cakisma nedeniyle
atlananlar) elle cevrildi. **Kalan Turkce yerel: 0.**

Baglama gore anlam ayrimi yapildi: `baslik` cekirdekte **`header`** (ikili
baslik tamponu), arayuzde **`title`** (pencere basligi). Tek karsilik ikisinde de
yanlis olurdu.

> **Iki tuzak yasandi.**
> 1. Ilk kosumda `ast`'in `col_offset` degerinin **UTF-8 bayt ofseti** oldugu
>    (karakter ofseti degil) atlanmisti. Turkce karakter iceren satirlarda
>    hizalama kayiyordu. Betikteki hizalama denetimi bunu yakalayip yazmayi
>    **durdurdu**; degisen dosyalar yedekten geri alindi, duzenleme satirin
>    bayt gosterimi uzerine tasindi.
> 2. `rm -rf src` ile geri alma reddedildi (hakliydi); yalnizca degisen dosyalar
>    yedekten kopyalandi.

### 3. Denetim yerellere genisletildi
`platform_check` artik fonksiyon adi + parametre **ve** yerel degiskenler ile
modul duzeyi atamalari denetliyor. Kural "Ingilizce kod adi" diyor, yalnizca
"Ingilizce fonksiyon adi" degil; govde disarida kalsaydi kural yeniden gevserdi.

Genisletilmis denetim `main.py`'yi de yakaladi (`pencere`, `tema`) — ilk turda
gozden kacmisti, cevrildi.

### Dogrulama
- `tests.run_all` → **17/18 · 1 atlandi** (t05, harici `mkfs` yok)
- `tests.platform_check` → **0 bulgu**
- `tests.ui_smoke` → 18 ekran goruntusu, tema dali dahil
- **Dize butunlugu:** 41 dosyanin tum dize sabitleri ceviri oncesiyle
  karsilastirildi → **0 dosyada degisiklik**. Turkce arayuz metinleri saglam.
- Olumsuz test: gecici eklenen `_deneme()` icindeki `toplam_boyut` yerel
  degiskeni yakalandi, silininde tekrar 0 bulgu.

> **Not:** Bu oturumda ana makinenin fiziksel diskleri uzerinde **hicbir islem
> yapilmadi.** `physical_probe.py` / `physical_write_test.py` calistirilmadi;
> tum testler `.tmp/` altindaki imaj **dosyalari** uzerinde kosuldu. Arayuz
> agacinda gorunen fiziksel diskler yalnizca listelemedir (`list_disks()`
> hicbir aygiti acmaz). Fiziksel disk testleri CLAUDE.md geregi sanal makinede
> yapilacak.

### Durum
Iyilestirme planinin alti asamasi ve plan disi kalan iki madde tamamlandi.

## 2026-09-15 — Oturum kayitlarinin proje icine tasinmasi

### Yapilanlar
- `.claude/hooks/archive-session.py` — `SessionEnd` kancasi. stdin'den gelen
  kanca JSON'undan `transcript_path` okunur, dokum
  `.claude/sessions/<YYYY-MM-DD>-<session_id>.jsonl` olarak kopyalanir ve
  `.claude/sessions/INDEX.md` tablosuna bir satir eklenir. Saf Python, harici
  bagimlilik yok (makinede `jq` bulunmuyor). Hata durumunda sessizce 0 doner,
  oturumu bosa dusurmez.
- `.claude/settings.json` (depoya girer) — `SessionEnd` kancasi tanimlandi.
  `python3` yoksa `python`'a duser.
- `.claude/settings.local.json` (depoya girmez) — `autoMemoryDirectory` ile
  Claude hafizasi `.claude/memory/` icine yonlendirildi. Bu anahtar guvenlik
  geregi depoya giren `settings.json` icinden okunmaz, bu yuzden local dosyada
  ve mutlak yolla durur.
- `.claude/memory/MEMORY.md` — hafiza dizini tohumlandi.
- `.gitignore` — ham `.jsonl` dokumleri ve `settings.local.json` haric tutuldu;
  `.claude/sessions/INDEX.md` depoda kalir.
- `CLAUDE.md` — kayit kurali tablosuna `sessions/`, `memory/`, `hooks/` satirlari
  ve kuralin nasil zorlandigini anlatan bolum eklendi.

### Dogrulama
- Kanca betigi gercek kanca JSON'u ile stdin'den beslendi: cikis 0, dokum
  `.claude/sessions/` altina kopyalandi, `INDEX.md` olustu.
- Iki ayar dosyasi da `json.load` ile ayristirildi; kanca komutu JSON icinden
  geri okundu.
- `git check-ignore` ile `.jsonl` ve `settings.local.json` haric tutmalari
  dogrulandi.

### Not
`autoMemoryDirectory` mutlak yol tasir; depo baska bir dizine klonlanirsa
`.claude/settings.local.json` yeniden yazilmalidir. Kancanin devreye girmesi
icin Claude Code'un ayarlari yeniden okumasi gerekir (`/hooks` menusu veya
yeniden baslatma).

---

## 2026-09-13 — Proje kurulumu ve v0.1.0

### Yapilanlar

**Altyapi**
- `.claude/` yapisi kuruldu: `docs/`, `decisions/`, `specs/`, `logs/`.
- Kok dizine `CLAUDE.md` yazildi (proje kurallari, kayit kurali, kod standartlari).
- Paket iskeleti: `src/diskultimate/{core,ui,utils}`, `tests/`, `main.py`.

**Cekirdek (saf Python, GUI'den bagimsiz)**
- `core/image.py` — `BlockDevice` arayuzu, `DiskImage` (seyrek olusturma, okuma,
  yazma, yeniden boyutlandirma), `PartitionView` (bolum penceresi, sinir denetimi).
- `core/ptable.py` — `Partition` / `FreeRegion` modeli, MBR tip ve GPT GUID tablolari,
  hizalama ve bos alan hesaplari, `human_size` / `parse_size`.
- `core/mbr.py` — MBR okuma/yazma, CHS donusumu, 4 birincil bolum, genisletilmis
  bolum ve EBR zinciri ile mantiksal bolumler.
- `core/gpt.py` — GPT okuma/yazma, CRC32 dogrulama, koruyucu MBR, birincil + yedek
  baslik ve giris dizisi, 128 bolum destegi.
- `core/fat.py` — sifirdan FAT12/16/32 surucusu: bicimlendirme, FAT tablosu
  yonetimi, kume zinciri, LFN cozumleme ve uretme, 8.3 kisa ad uretimi, dizin
  ekleme/silme, dosya okuma/yazma, disa/ice aktarma, istatistik.
- `core/fsdetect.py` — imza tabanli tespit: FAT12/16/32, exFAT, NTFS, ext2/3/4,
  btrfs, XFS, F2FS, ISO9660, Linux takas; etiket ve kullanim hesabi.
- `core/formatter.py` — bicimlendirme dagiticisi; FAT dahili, exFAT/NTFS/ext*
  icin gecici seyrek birim uzerinde `mkfs.*` calistirip sonucu geri yazma.
- `core/filesystem.py` — `FileSystemAccess` arayuzu, `FatAccess`, `UnsupportedAccess`.
- `core/session.py` — `DiskSession`: GUI'nin gordugu tek cephe.

**Arayuz (PyQt5)**
- `ui/theme.py` — DiskGenius'a yakin acik tema, dosya sistemi renk paleti, stil sayfasi.
- `ui/widgets/disk_map.py` — gorsel bolum haritasi: oransal bloklar, dosya sistemi
  rengi, doluluk cubugu, secim/vurgu, ipucu, sag tik menusu.
- `ui/widgets/partition_table.py` — bolum ve bos alan listesi (10 sutun).
- `ui/widgets/file_browser.py` — klasor agaci + dosya listesi, disa/ice aktarma,
  klasor olusturma, silme, yeniden adlandirma, onizleme.
- `ui/widgets/hex_view.py` — sektor bazli onaltilik goruntuleyici.
- `ui/dialogs/` — yeni goruntu sihirbazi, bolum olusturma, bicimlendirme,
  ilerleme penceresi (QThread), dosya onizleme.
- `ui/main_window.py` — menu/arac cubugu, disk agaci, dort sekmeli alt panel,
  durum cubugu, tum is akislari, onay diyaloglari, `.claude/logs/app-*.log` gunlugu.

### Duzeltilen hatalar
1. **MBR yazma tampon boyutu** — onyukleme kodu 446 bayt olunca sektor 510 baytta
   kaliyor, imza yazilamiyordu. Tampon sabit 512 bayta cekildi.
2. **FAT32 `BPB_BkBootSec` ofseti** — 52 yerine dogru deger olan 50 kullanildi.
   `fsck.vfat` "yedek onyukleme sektoru yok" uyarisi verdigi icin yakalandi.
3. **Basarisiz bicimlendirmede hayalet bolum** — `create_partition` icinde
   bicimlendirme hata verirse bolum tablodan geri alinacak sekilde duzeltildi;
   `t08` testi bunu koruma altina aldi.
4. **Arayuz cakismasi** — disk haritasinda boyut metni ile doluluk cubugu ust uste
   biniyordu; blok yuksekligi ve metin konumlari yeniden ayarlandi.
5. **Gezgin yol cubugu** — desteklenmeyen bir bolume gecildiginde onceki yol
   ekranda kaliyordu; `set_filesystem` artik yolu sifirliyor.
6. **exFAT birim etiketi** — kok dizindeki `0x83` girisinden okunacak sekilde eklendi.

### Dogrulama
- `python3 -m tests.run_all` → **8/8 basarili**.
- Uretilen FAT12/FAT16/FAT32 birimleri `fsck.vfat -n` ile hatasiz dogrulandi.
- Uretilen MBR (mantiksal bolumler dahil) ve GPT tablolari `fdisk -l` ile dogrulandi.
- Arayuz `QT_QPA_PLATFORM=offscreen` altinda 4 bolumlu 4 GB ornek goruntu ile
  calistirilip ekran goruntuleri alinarak gozle dogrulandi.

### Sonraki adim
`.claude/docs/project-overview.md` icindeki v0.2 listesi — oncelik ext4 okuyucu.

### Kapanis notlari (ayni gun)
- `src/diskultimate/utils/` bos kaldigi icin kaldirildi; ihtiyac dogunca yeniden acilir.
- `.gitignore` icinde `.claude/logs/*.log` satiri kaldirildi: proje kurali geregi
  islem kayitlari depo icinde kalmali, disarida tutulmamalidir.
- Uygulama `main.py disk.img` ile calistirilip temiz acilis dogrulandi
  (offscreen, hata ciktisi yok).
- Boyut: 28 Python dosyasi, ~6000 satir kod; 13 markdown belge.

---

## 2026-09-13 (ikinci oturum) — Wayland cizim sorunu ve yol duzeni

### Bildirilen sorun
Kullanici `Yeni Disk Goruntusu` penceresinin **bos** acildigini bildirdi: pencere
cerceve ve baslik ile geliyor ama icerigi hic cizilmiyordu.

### Teshis
Ayni diyalog `xcb` (XWayland) ve `offscreen` altinda eksiksiz cizildi → tema veya
yerlesim hatasi degil, Qt 5.15 yerel Wayland eklentisinin modal pencerede ilk kareyi
boyamamasi. Ek olarak `QIcon.themeName()` = `breeze-dark` bulundu: ikonlar eksik
degil, acik renkli olduklari icin acik zeminde gorunmuyorlardi.

Ayrinti ve kararlar: [ADR 0005](../decisions/0005-wayland-ikon-temasi-ve-platform.md)

### Yapilan degisiklikler
- `main.py` — `secilen_platform()`: Wayland oturumunda XWayland (`xcb`) secilir;
  `DISKULTIMATE_QPA` / `QT_QPA_PLATFORM` ile gecersiz kilinabilir. Taban stil Fusion.
  Secilen platform, stil ve ikon temasi islem gunlugune yazilir.
- `ui/theme.py` — `ikon_temasini_ayarla()`: koyu ikon temasi acik varyantina cekilir
  (`breeze-dark` → `breeze`), bulunamazsa Qt gomulu ikon setine dusulur.
- `ui/dialogs/base.py` (yeni) — `exec_dialog()`: gosterim sonrasi yeniden cizim
  tetikleyen guvenlik agi; tum diyaloglar bunu kullaniyor.
- `ui/theme.py` — `QComboBox::drop-down` kurali kaldirildi (acilir liste oku geri
  geldi); `QSpinBox`/`QDoubleSpinBox` kutu kurali kaldirildi (artir/azalt oklari
  geri geldi).
- Diyalog dugmeleri Turkcelestirildi: `Cancel` → "Iptal", `Close` → "Kapat".

### Yol duzeni (kullanici istegi)
Gecici dosyalar artik `/tmp` yerine proje icinde: [ADR 0006](../decisions/0006-gecici-dosyalar-proje-icinde.md)
- `src/diskultimate/paths.py` (yeni) — tek yetkili yol kaynagi.
- `tests/run_all.py` → `<proje>/.tmp/tests`
- `core/formatter.py` → `<proje>/.tmp/format` (artik `tempfile` kullanmiyor)
- `tests/ui_smoke.py` (yeni) — ornek goruntu uretip 8 ekran goruntusu kaydeder
  (`<proje>/.tmp/screenshots`). Arayuz degisikliklerinden sonra calistirilir.
- `.gitignore` icine `.tmp/` eklendi.

### Dogrulama
- `python3 -m tests.run_all` → **8/8 basarili** (yeni yollarla).
- `DISKULTIMATE_QPA=xcb python3 -m tests.ui_smoke` → 8 ekran goruntusu; ana pencere,
  dort sekme ve uc diyalog gozle denetlendi: ikonlar gorunur, acilir liste ve sayi
  kutusu oklari yerinde, dugme metinleri Turkce.

---

## 2026-09-13 (ucuncu oturum) — v0.2.0: capraz platform + DiskGenius ozellikleri

Istek: (1) Windows/Linux/macOS destegi, (2) GitHub ve populer disk araclarinin
analizi, (3) DiskGenius ozelliklerinin mumkun oldugunca karsilanmasi.

### Arastirma
DiskGenius, GParted, KDE Partition Manager, TestDisk/PhotoRec ve Python
kutuphaneleri (FATtools, dissect.ntfs, ext4, pytsk3, gpt-image, pygpt, hdisk,
gptfdisk) incelendi. Bulgular ve cikan kararlar:
[feature-analysis.md](feature-analysis.md) · Ozellik matrisi: [diskgenius-parity.md](diskgenius-parity.md)

### Capraz platform (ADR 0008)
- `core/platform.py` (yeni) — seyrek dosya isaretleme (Windows `FSCTL_SET_SPARSE`),
  gercek dosya boyutu (`GetCompressedFileSizeW` / `st_blocks`), arac arama
  (Linux `mkfs.*`, macOS `newfs_*`), surec calistirma (`CREATE_NO_WINDOW`),
  Qt platform eklentisi secimi, varsayilan klasor.
- `image.py`, `formatter.py`, `main.py`, `tests/` bu katmana baglandi;
  `dd` cagrisi ve `st_blocks` kullanimi kaldirildi.
- `tests/platform_check.py` (yeni) — statik uyumluluk denetimi: sabit POSIX yollari,
  `tempfile`, dogrudan `subprocess`/`shutil.which`, endian isaretsiz `struct`,
  `core/` icine PyQt sizintisi. **Denetleyici kasitli ihlal dosyasiyla dogrulandi**
  (9 ihlalin 9'u yakalandi). Gercek kod: 0 bulgu.

### exFAT saf Python (ADR 0007)
`core/exfat.py` (yeni, ~1000 satir): bicimlendirme, okuma, yazma, klasor, silme,
yeniden adlandirma, birim etiketi, bitmap tabanli tahsis.
**Kritik bulgu:** upcase tablosu serbest degil — kendi uretilen tablo `fsck.exfat`
tarafindan reddedildi. Standart sikistirilmis tablo (5836 bayt, saglama
`0xE619D30D`) kaynaga gomuldu. Artik exFAT Windows/macOS'ta da calisiyor.

### Yeni ozellikler
| Modul | Yetenek |
|---|---|
| `convert.py` | MBR ↔ GPT donusumu (veri yerinde), uygunluk on denetimi, 4K hizalama raporu |
| `clone.py` | `.dub` yedek bicimi (ADR 0009), yedekleme/geri yukleme, disk ve bolum klonlama |
| `wipe.py` | Guvenli silme: sifir / rastgele / DoD 3 / DoD 7, dogrulama, bos alan silme |
| `recovery.py` | Silinmis dosya tarama ve kurtarma (FAT + exFAT), kayip bolum tarama, 13 imzali dosya carving |
| `vdisk.py` | VHD (sabit+dinamik), VDI, VMDK (duz+seyrek), QCOW2 okuma; VHD olusturma ve yazma |
| `session.py` | Tum bu yetenekler tek cephede toplandi (22 yeni yontem) |

### Arayuz
- Yeni **Araclar** menusu: silinmis dosya tarama, kayip bolum tarama, imza tabanli
  kurtarma, yedek bilgisi, sistem bilgisi.
- Disk menusu: GPT/MBR donusumu, hizalama denetimi, disk yedekle/geri yukle/klonla/sil.
- Bolum menusu ve baglam menusu: bolum yedekle/geri yukle/guvenli sil/silinmis tara.
- Dosya menusu: **Yeni sanal disk (VHD)**; acma filtresi sanal diskleri kapsiyor.
- Yeni diyaloglar (`ui/dialogs/tools.py`): guvenli silme, silinmis dosyalar,
  kayip bolumler, imza secimi, bulunan dosyalar, bilgi penceresi.

### Duzeltilen hatalar
7. **FAT tahsis basarimi (ADR 0010).** `alloc_cluster` her cagrida on binlerce
   elemanlik liste uretiyordu; 8 MB yazma >100 saniye suruyordu. Liste kaldirildi,
   bos kume sayaci onbellege alindi → **0.04 s** (~250 MB/s). `t15` regresyonu korur.
8. **Basarisiz bicimlendirmede tip esleme hatasi.** GPT→MBR donusumunde "Microsoft
   Temel Veri" GUID'i hem FAT32 hem NTFS'i kapsadigindan tip bayti yanlis seciliyordu;
   artik once dosya sistemi turune bakiliyor.
9. **Silinmis LFN adlari eksik kurtariliyordu.** Silme sirasinda LFN sira bayti da
   ezildigi icin sira numarasi guvenilmez; parcalar artik fiziksel siraya gore
   toplanip ters cevriliyor. "Onemli Rapor" → "Onemli Rapor 2026.txt".

10. **Kararsiz test (test altyapisi).** `t14` ikinci kosumda duserdi: VirtualBox,
   onceki kosumda kaydettigi VHD'yi medya kayit defterinde tutuyor ve ayni yolda
   yeni UUID gorunce `NS_ERROR_ABORT` veriyor. Dogrulama oncesi ve sonrasi
   `VBoxManage closemedium disk` cagrilarak cozuldu. Uretilen VHD'nin kendisi
   bastan beri gecerliydi.

### Dogrulama
- `python3 -m tests.run_all` → **15/15 basarili** (7 yeni test), **art arda iki kosumda kararli**
- `python3 -m tests.platform_check` → **0 bulgu**
- Bagimsiz araclarla capraz dogrulama: `fsck.vfat`, **`fsck.exfat` (clean)**,
  `fdisk -l`, **`VBoxManage showhdinfo`** (uretilen VHD gecerli), VBoxManage ile
  uretilen VDI/VMDK dosyalari okundu
- `python3 -m tests.ui_smoke` → 14 ekran goruntusu, gozle denetlendi

---

## 2026-09-13 (dorduncu oturum) — Windows dogrulama hazirligi ve donma nedeni

### Bildirilen olay
Kullanici Windows 10 sanal makinesinde test paketini calistirinca **ana makine
dondu ve resetlemek zorunda kaldi.**

### Kok neden
- VirtualBox paylasilan klasoru (`vboxsf`) **seyrek dosya desteklemez.**
- Testlerin mantiksal toplami ~8 GB. Linux'ta seyreklik sayesinde 1.2 GB yer
  kapliyordu; paylasilan klasorde **8 GB'in tamami gercekten yazilir.**
- Paylasim hedefi `/run/media/pc/Data` diskindeydi: **%93 dolu, 13 GB bos.**
- Ayrica testler urettikleri goruntuleri **silmiyordu**; hepsi birikiyordu.
- Dolan disk + `vboxsf` uzerinden yogun sektor yazimi host'u I/O kilidine soktu.

### Duzeltmeler (tekrarini onlemek icin)
1. **Ortam on denetimi** — `tests/run_all.py::check_environment()`:
   - hedef dizinde seyrek dosya desteginin **olculmesi** (64 MB deneme dosyasi)
   - bos alan denetimi: seyrek destekleniyorsa 2 GB, desteklenmiyorsa **12 GB**
   - yetersizse testler **baslamadan durur** (cikis kodu 2) ve nedeni yazar
2. **Test sonrasi temizlik** — her test kendi goruntulerini siler
   (`cleanup_test_files`). Pik disk kullanimi artik toplamin degil, en buyuk tek
   testin boyutu kadar. Inceleme icin `DISKULTIMATE_KEEP_TEST_FILES=1`.
3. **Belgelendirme** — `windows-setup/OKUBENI.md` icinde acik uyari: testler
   paylasilan klasorde degil, VM'in kendi diskinde (`C:\du-test`) calistirilir.

### Windows dogrulama altyapisi
Kullanicinin istegi uzerine SSH tabanli dogrulama hazirlandi:
- `VBoxManage modifyvm --natpf1 "ssh,tcp,127.0.0.1,2222,,22"` — NAT port
  yonlendirmesi (yalnizca localhost'a acik).
- Proje, VM'e `duproje` adiyla paylasilan klasor olarak baglandi
  (`VBoxManage sharedfolder add --automount`).
- `windows-setup/ssh-kur.ps1` — VM icinde OpenSSH Server kurulumu, sshd servisi,
  guvenlik duvari kurali, Python denetimi/kurulumu, parolasiz hesap uyarisi.
- `windows-setup/testleri-calistir.ps1` — kaynagi `C:\du-test` altina kopyalayip
  testleri **yerel diskte** calistirir (SSH olmadan da kullanilabilir).

### Windows uzerinde GERCEK KOSUM (ayni gun, tamamlandi)

**Engeller:** VM'de OpenSSH kurulu degildi, `pc` hesabi yonetici degildi ve
(baslangicta) internet yoktu — yani SSH veya paket kurulumu yapilamiyordu.

**Uygulanan cozum:** Guest Additions zaten kuruluydu; `VBoxManage guestcontrol`
ile VM icinde dogrudan komut calistirildi. Python'un **tasinabilir (embeddable)**
paketi (3.12.8) ve PyQt5 5.15.11 tekerlekleri ana makinede indirilip `duproje`
paylasimi uzerinden VM'e aktarildi, `C:\du-test` altina acildi. Yonetici yetkisi,
internet ve kurulum gerekmedi.

**Sonuclar (Windows 10 x64):**
- `tests.platform_check` → **0 bulgu**
- `tests.run_all` → **15/15 basarili**, 13.2 saniye
- `tests.ui_smoke` → **14 ekran goruntusu**, gozle denetlendi

**Yakalanan uc gercek hata:**
11. **Seyrek dosya uzatma (ADR 0011).** `FSCTL_SET_SPARSE` basariliydi ama Python'un
    `truncate()` cagrisi Windows'ta dosyayi **sifirlarla dolduruyordu**: 256 MB'lik
    goruntu gercekten 256 MB tahsis ediyordu. `SetFilePointerEx` + `SetEndOfFile`
    ile degistirildi → 0.00 s, 0 bayt. Tam kosum 18.2 s → **13.2 s**, gereken bos
    alan 12 GB → **2 GB**.
12. **Olcum uretim yolunu izlemiyordu.** Duzeltmeden sonra rapor hala
    "DESTEKLENMIYOR" diyordu: `_sparse_supported()` kendi `fh.truncate()` cagrisini
    kullaniyordu. Olcum uretim yoluna (`make_sparse` + `truncate_sparse`) hizalandi;
    `t11`'deki seyreklik dogrulamasi da `IS_LINUX` kosulundan kurtarilip gercek
    dosya sistemi destegine baglandi.
13. **Qt bulgulari (ADR 0012).** (a) Qt'nin Windows'taki `offscreen` eklentisi hic
    font yuklemiyor (font ailesi 0) — uretilen goruntulerde metin gorunmuyordu;
    `ui_smoke` artik Windows'ta `windows` eklentisini secip font sayisini raporluyor.
    (b) `QTabBar::tab:selected { font-weight: 600 }` sekme basliklarini kirpiyordu
    ("Dosya Gezgini" → "osya Gezgin"); sekmelerde font-weight kaldirildi.

**Ek duzeltme:** `tests/ui_smoke.py` artik platformda bulunmayan dosya sistemlerini
(Windows'ta ext4/NTFS) ham bolum olarak olusturuyor; boylece duman testi her
platformda calisiyor ve ekran duzeni karsilastirilabilir kaliyor.

SSH altyapisi (port yonlendirme + `windows-setup/` betikleri) yerinde duruyor;
ileride etkilesimli oturum gerekirse kullanilabilir.

### Not
Eski `myiso` paylasimi (`/run/media/pc/Data/myiso`) artik mevcut degil; VM'de
baglanamaz. Gerekirse kaldirilabilir:
`VBoxManage sharedfolder remove <vm> --name myiso --global`

---

## 2026-09-13 (besinci oturum) — Windows VM'de kalici Python ortami

Istek: "win10'da python nereye kurulu, ortam PATH'e kaydet".

### Yapilanlar
- Python, test klasorunden (`C:\du-test\python`) kalici konuma tasindi:
  **`C:\Python312`**. Boylece test klasoru silinse de yorumlayici kalir.
- `python312._pth` icinde `import site` etkinlestirildi (pip icin gerekli) ve
  `Lib\site-packages` eklendi.
- **pip 26.2.1** kuruldu (`get-pip.py`, ana makineden aktarildi).
- Kullanici duzeyi PATH'e eklendi (yonetici gerekmez):
  `C:\Python312;C:\Python312\Scripts;C:\Users\pc\AppData\Local\Microsoft\WindowsApps`
- `windows-setup/DiskUltimate-baslat.bat` eklendi; `windows-setup/OKUBENI.md`
  basina "kurulu durum" ozeti yazildi.
- Test klasorundeki 164 MB'lik Python kopyasi ve gecici tani betikleri silindi.

### Iki tuzak (ikisi de yasandi ve duzeltildi)
14. **PowerShell dizi tuzagi.** `$parcalar = $mevcut -split ";" | Where-Object {...}`
    tek eleman dondurdugunde **dizi degil string** olur; sonraki `+=` dizi ekleme
    degil **metin birlestirme** yapar. Sonuc bozuk PATH:
    `...WindowsAppsC:\Python312C:\Python312\Scripts` (noktali virguller kayip).
    Duzeltme: `@(...)` ile diziye zorlamak. Bozuk deger hemen duzeltildi.
15. **Windows yurutme takma adi.** PATH duzeldikten sonra `python` hala calismiyor,
    "Microsoft Store'dan kurun" diyordu: `WindowsApps\python.exe` stub'i PATH'te
    once geliyordu. Cozum: `C:\Python312` PATH'in **basina** alindi — resmi Python
    kurulumunun da yaptigi budur.

### Dogrulama (yeni guestcontrol oturumunda, kayit defterinden miras PATH ile)
- `python` → `C:\Python312\python.exe`, Python 3.12.8
- `pip --version` → 26.2.1
- `import PyQt5.QtCore` → Qt 5.15.2
- `python tests\platform_check.py` → **0 bulgu**
- `python tests\run_all.py` → **15/15 basarili**, seyrek dosya destekleniyor

---

## 2026-09-13 (altinci oturum) — Tema kaldirildi, fiziksel disk destegi eklendi

Uc istek: (1) temayi kaldir, sistem gorunumune don; (2) yazilim yalnizca disk
imaji uzerine calismasin; (3) ana ekranda sistemdeki diskler gorunsun, "aynen
DiskGenius gibi".

### 1. Tema kaldirildi (ADR 0013)
- `app.setStyleSheet("")`, `Fusion` zorlamasi ve ikon temasi degistirme kaldirildi.
- Tum sabit renkler (`TEXT_DIM`, `ACCENT`, `BORDER`, `FREE_COLOR`...) widget'lardan
  temizlendi; renkler artik `theme.palette_color()` ile **sistemin paletinden**
  geliyor. Soluk etiketler icin `setEnabled(False)` kullaniliyor.
- Disk haritasinda blok uzerindeki metin rengi, blogun zeminine gore acik/koyu
  seciliyor; dosya sistemi renkleri anlamsal oldugu icin sabit kaldi.
- `theme.apply_theme()` + `THEMES` altyapisi ileride eklenecek "Tema" bolumu icin
  hazir birakildi (`DISKULTIMATE_THEME=diskultimate` ile eski gorunum denenebilir).
- Koyu sistem temasinda dogrulandi: tum paneller okunabilir.

### 2-3. Fiziksel disk destegi (ADR 0014) — KAPSAM DEGISIKLIGI
Proje artik gercek disklere de erisiyor. `core/physical.py` (yeni):
- `list_disks()` — Linux `/sys/block`+`/proc/mounts`, Windows IOCTL
  (`DISK_GET_LENGTH_INFO`, `STORAGE_QUERY_PROPERTY`, `VOLUME_GET_VOLUME_DISK_EXTENTS`),
  macOS `diskutil -plist`. Model, seri, boyut, sektor, veriyolu, cikarilabilirlik,
  bolumler, bagli noktalar, sistem diski tespiti.
- `PhysicalDisk(BlockDevice)` — blok duzeyinde okuma/yazma. Windows'ta aygit G/C
  sektor hizali olmak zorunda oldugu icin oku-degistir-yaz uygulanir.
- `DiskSession.open_physical(...)`, `list_physical_disks()`, `has_disk_privileges()`.
- Arayuz: sol agacta **"Fiziksel Diskler"** dali; sistem diski kirmizi uyari
  ikonuyla `[SISTEM DISKI]` etiketli, bagli bolumu olanlar `[bagli bolum var]`,
  yetki yoksa `[?]`. Cift tiklama salt okunur acar. Disk menusunde yenileme,
  salt okunur acma, **yazma modunda acma** ve disk bilgisi.

**Guvenlik katmanlari** (CLAUDE.md'ye kural olarak islendi): listeleme zararsiz,
varsayilan salt okunur, sistem diskinde ad yazarak dogrulama, **bilgisi eksik
diske yazma reddi**, bagli bolum uyarisi.

### Yakalanan kusur
16. **Eksik bilgi "risk yok" gibi gorunuyordu.** Windows'ta yetki olmadan disk
    bilgileri okunamiyor; ilk surumde bu disk `is_system=False` olarak listeleniyor,
    yani en riskli disk en guvenli gibi gorunuyordu. `DiskInfo.info_complete`
    eklendi: bilgi eksikse risk seviyesi "bilinmiyor" olur ve **yazma reddedilir**.

### Dogrulama
- Linux listeleme: sistem diski (`nvme0n1`) dogru isaretlendi, bagli bolumler
  (`/`, `/boot/efi`, veri bolumu) tespit edildi. **Ana makinede hicbir disk acilmadi.**
- Guvenlik denetimleri sahte disk tanimlariyla dogrulandi (gercek aygita dokunmadan):
  onaysiz yazma, sistem diski, bilinmeyen disk — ucu de engellendi.
- Windows yetkisiz davranis: disk listede gorunuyor, "(yetki yok)" isaretli, acma
  denemesi anlamli hata veriyor.
- `tests.run_all` 15/15, `tests.platform_check` 0 bulgu (denetleyiciye
  "platform katmani" kavrami eklendi: `platform.py` ve `physical.py` yalnizca
  ilgili kurallardan muaf; tempfile/subprocess/endian kurallari onlarda da gecerli
  ve denetleyici kasitli ihlalle yeniden dogrulandi).

### Test araclari (yeni)
- `tests/physical_probe.py` — **yalnizca okuma** sondasi: diskleri listeler, salt
  okunur acar, bolum tablosunu cozumler. Yetkisiz calistirilabilir; neyin
  okunamadigini raporlar.
- `tests/physical_write_test.py` — yazma testi, **alti olcut** saglanmadikca
  calismaz: (1) aygit acikca verilmis, (2) `--onayla` bayragi, (3) sistem diski
  degil, (4) bagli bolum yok, (5) bilgiler eksiksiz, (6) boyut < 8 GB.
  Dogrulandi: ana makinenin sistem diski hedef gosterildiginde uc ayri olcutle
  reddedildi ve hicbir sey yazilmadi.
- `windows-setup/disk-testi-yonetici.bat` — PowerShell `ExecutionPolicy Restricted`
  kisitini asan sarmalayici (kullanici betigi dogrudan calistiramadi).

### FIZIKSEL DISK DOGRULAMASI — Pop!_OS 24.04 misafiri (tamamlandi)

Kullanici ikinci bir misafire Pop!_OS 24.04 kurdu (Guest Additions + SSH hazir).
Her iki misafire de **2 GB'lik bos sanal disk** eklendi (ayri VDI dosyalari; ayni
medya iki VM'e baglanamaz) ve SSH port yonlendirmesi yapildi
(win10 → 2222, Pop!_OS → 2223). Sistem disklerine dokunulmadi.

**Pop!_OS'ta yapilanlar (SSH + sudo ile tam otomatik):**

| Adim | Sonuc |
|---|---|
| `tests.platform_check` | **0 bulgu** |
| `tests.run_all` | **15/15 basarili** |
| `tests.physical_probe` (root) | 2/2 disk okundu |
| Guvenlik kilidi — `/dev/sda --onayla` | **REDDEDILDI** (3 olcut), cikis kodu 2 |
| `tests.physical_write_test /dev/sdb --onayla` | **TUM ADIMLAR BASARILI** |

**Sonda ciktisi:** `/dev/sda` sistem diski olarak isaretlendi, bagli bolum
`sda1 → /` tespit edildi, MBR tablosu cozumlendi (ext4 21 GB + Linux Takas 4 GB).
`/dev/sdb` bos, sistem degil, bagli bolum yok.

**Yazma testi adimlari (gercek diske):** GPT tablosu → 512 MB FAT32 bolum →
dosya yazma → **disk kapatilip yeniden acildi** → 19.500 bayt bayt-bayt dogrulandi
→ exFAT ile yeniden bicimlendirme → dosya yazma ve geri okuma.

**Bagimsiz dogrulama — en guclu kanit:** Isletim sisteminin kendi araclari
yazdigimiz yapiyi kabul etti:
```
lsblk -f : sdb1  exfat  1.0  DUEXFAT  6ADF-1B3D
blkid    : TYPE="exfat" LABEL="DUEXFAT" PARTLABEL="DiskUltimate Test"
           PARTUUID="d6d70183-3665-4235-9078-a71f7696c97f"
mount /dev/sdb1 /mnt/dutest -> BAGLANDI, exfat.txt okundu
```
Yani saf Python exFAT surucumuzun yazdigi birim **Linux cekirdegi tarafindan
baglanip okundu**; GPT bolum adi ve GUID'i de dogru yazilmis.

**Arayuz:** Pop!_OS'ta PyQt5 5.15.10 kurulup GUI calistirildi. Sol agacta
"Fiziksel Diskler (2)": `sda` kirmizi uyari ikonuyla `[SISTEM DISKI]`, `sdb`
normal. `sdb` acildiginda bolum haritasi, tablo ve dosya gezgini gercek diskten
okunan `exfat.txt` dosyasini gosterdi; salt okunur acildigi icin yazma eylemleri
devre disi kaldi. Ekran goruntuleri: `.tmp/linux-screenshots/`

### FIZIKSEL DISK DOGRULAMASI — Windows 10 misafiri (tamamlandi)

Kullanici yonetici komut isteminde calistirdi:

| Adim | Sonuc |
|---|---|
| `tests\physical_probe.py` | **2/2 disk okundu** |
| `tests\physical_write_test.py \\.\PhysicalDrive1 --onayla` | **TUM ADIMLAR BASARILI** |

- `PhysicalDrive0` sistem diski olarak isaretlendi, `C:` bagli bolumu tespit
  edildi, MBR tablosu cozumlendi (NTFS 50 MB + NTFS 49.45 GB + Windows Kurtarma
  510 MB). Model ve seri numarasi `IOCTL_STORAGE_QUERY_PROPERTY` ile okundu.
- `PhysicalDrive1` (2 GB test diski): GPT + FAT32 yazildi, dosya yazilip disk
  kapatilip yeniden acildiktan sonra bayt-bayt dogrulandi, ardindan exFAT ile
  yeniden bicimlendirildi.

### Yakalanan kusur — yalnizca gercek donanimda gorunur
17. **Isletim sistemine bildirim eksikti (ADR 0015).** Yazma testi basariliydi ve
    kendi kodumuz diski yeniden acip okuyabiliyordu, ama Windows diski hala
    **RAW** gosteriyor, `Get-Partition` hicbir bolum dondurmuyordu. Ham blok
    yazma, isletim sisteminin bolum tablosu onbellegini guncellemez. Gercek
    kullanimda kullanici bicimlendirir, "basarili" mesajini gorur, ama birim
    Gezgin'de cikmaz.
    `PhysicalDisk.rescan_partitions()` eklendi (Windows
    `IOCTL_DISK_UPDATE_PROPERTIES`, Linux `BLKRRPART`, macOS `diskutil rescan`);
    yazma modunda `close()` sirasinda kendiliginden cagriliyor.
    **Pop!_OS'ta dogrulandi:** bildirimden once `sdb1` aygiti yokken, sonrasinda
    cekirdek bolumu olusturdu, `blkid` exFAT'i tanidi ve `mount` ile dosya okundu.
    Bu kusur goruntu dosyasi testleriyle **asla** yakalanamazdi.

### Windows fiziksel disk — TAMAMLANDI (SSH ile otomatik)

Kullanici Win10'a OpenSSH Server kurdu. Onemli bulgu: **Windows'ta SSH oturumu
yukseltilmis yetkiyle geliyor** (`IsInRole(Administrator)` = True), yani UAC
engeli olmadan yonetici testleri host'tan otomatik calistirilabiliyor.

Iki gercek kusur daha yakalandi ve duzeltildi:

18. **Rescan islem ortasinda cagriliyordu (ADR 0015 duzeltmesi).**
    `DiskSession` bolum tablosu yazildiktan hemen sonra da `notify_os()`
    cagiriyordu. Windows'ta `IOCTL_DISK_UPDATE_PROPERTIES` aygiti yeniden taratir
    ve **o anda acik olan tutamaci gecersiz kilar**; sonraki yazma
    `ERROR_NO_SUCH_DEVICE` (433) verdi. Kural: rescan yalnizca tum islemler
    bittikten sonra, `close()` sirasinda. Linux'ta `BLKRRPART` bu yan etkiyi
    yaratmadigi icin sorun **yalnizca Windows'ta** gorundu.

19. **Bagli birime ham yazma reddi (ADR 0016).** Rescan basarili olunca Windows
    yeni FAT32 bolumunu tanidi ve `E:` olarak bagladi; ardindan exFAT
    bicimlendirmesi `ERROR_ACCESS_DENIED` aldi — Windows bagli birimin
    sektorlerine dogrudan yazmayi engeller. Cozum: yazma modunda disk acilirken
    o diskteki birimler `FSCTL_LOCK_VOLUME` + `FSCTL_DISMOUNT_VOLUME` ile
    kilitlenip baglantisi kesiliyor, tutamac acik tutuluyor; `close()` sirasinda
    once kilit birakiliyor sonra rescan yapiliyor.

**Nihai Windows dogrulamasi:**
```
[7] rescan cagrisi: basarili
    Number: 1   PartitionStyle: GPT          (onceden RAW idi)
    PartitionNumber DriveLetter Size  -> 1  E  536870912
    DriveLetter FileSystemLabel FileSystem -> E  DUEXFAT  exFAT
```
Windows diski GPT olarak tanidi, bolume surucu harfi atadi ve **saf Python exFAT
surucumuzun yazdigi birimi bagladi**.

### Onceki durum — bekleyen
- Win10'da her iki disk de goruluyor (`Get-Disk`: Disk 0 sistem MBR, Disk 1 2 GB
  RAW) ve sonda ikisini de `[?] BILINMIYOR / EKSIK (yetki yok)` olarak dogru
  raporluyor — yani yetki eksikligi guvenli tarafa dusuyor.
- ADR 0015 duzeltmesinin **Windows tarafinda** dogrulanmasi bekliyor (Linux'ta
  dogrulandi): yonetici komut isteminde yazma testinin yeniden calistirilmasi ve
  `Get-Disk` ciktisinin RAW yerine GPT gostermesi gerekiyor.
- **Tasinabilir Python'da `python -m tests.x` calismaz** (calisma dizini modul
  yoluna eklenmiyor); betikler dogrudan dosya olarak calistirilir
  (`python tests\physical_probe.py`). Belgeler ve `.ps1` buna gore duzeltildi.

---

## 2026-09-13 (yedinci oturum) — Arayuz duzeni, coklu goruntu, takas etiketi

Kullanici gercek bir Debian ARM goruntusu (`am335x-debian-13.6-...-4gb.img`)
acarak Windows'ta denedi ve uc geri bildirim verdi.

### 1. Takas bolumu etiketi bozuk karakter gosteriyordu (GERCEK HATA)
Ekran goruntusunde Bolum 2'nin etiketi `z¹D¶f↓®&4U` gibi cop karakterlerdi.

**Kok neden:** Linux takas basliginda etiket **1052.** bayttadir
(`sws_volume`), ben **1040**'tan okuyordum — orasi `sws_uuid` alaninin ortasi.
Yani UUID'nin ham baytlarini metin sanip gosteriyorduk.

```
1024 version | 1028 last_page | 1032 nr_badpages
1036 sws_uuid (16)            | 1052 sws_volume (16)  <- ETIKET
```

`fsdetect._swap()` yazildi: etiket dogru ofsetten okunuyor, ASCII disi bayt
iceriyorsa etiket **bos** sayiliyor, ayrica UUID cozumleniyor ve boyut
`last_page * 4096` ile hesaplaniyor.
**Dogrulama:** `mkswap -L TAKASETIKET` ile uretilen birimde etiket ve UUID
`mkswap` ciktisiyla birebir eslesti.

### 2. Arac cubugu baglamsal hale getirildi
"Yeni goruntu" ve "Goruntu ac" arac cubugundan kaldirilip **Disk menusune**
alindi (Dosya menusunde de duruyor). Arac cubugu artik yalnizca **secili disk
veya bolum** uzerinde yapilabilecek islemleri tasiyor:

`Yeni bolum · Bicimlendir · Bolumu sil | Bolumu yedekle · Bolume geri yukle |
Silinmis dosyalari tara · Kayip bolumleri tara | Bolumu guvenli sil |
Disk bilgisi · Yenile`

### 3. Coklu goruntu destegi
Onceden yeni bir goruntu acildiginda onceki kapaniyordu. Artik birden fazla
goruntu/disk ayni anda acik kalir ve sol agacta **alt alta** listelenir.

- `MainWindow.sessions: List[DiskSession]` — tum acik oturumlar,
  `session` bunlardan **etkin** olani.
- Her oturum agacta ayri kok: `ad — boyut — sema [salt okunur]`; etkin olan
  **kalin** ve genisletilmis.
- Agac ogeleri artik oturum kimligi tasiyor: `("part", (oturum_id, index))`.
  Baska bir goruntunun bolumune tiklamak once o oturumu etkinlestirir.
- Ayni dosya ikinci kez acilirsa yeni oturum acilmaz, mevcut olan one getirilir.
- Sag tik > "Bu goruntuyu kapat" yalnizca o oturumu kapatir; digerleri kalir.
- Pencere kapanisinda `close_all()` hepsini kapatir.

**Dogrulama:** `tests/ui_smoke.py` icine senaryo eklendi — ikinci goruntu
acildiktan sonra `len(sessions) == 2` ve agacta uc kok (fiziksel diskler +
iki goruntu) bulunuyor; ekran goruntusu `15-coklu-goruntu.png`.

### Durum
`tests.run_all` 15/15 · `tests.platform_check` 0 bulgu · duman testi 15 goruntu.

---

## 2026-09-13 (sekizinci oturum) — Sekiz dosya sistemi saf Python

Kullanici DiskGenius'un bicimlendirme ekranini gosterdi (8 bicim) ve bizdeki
listenin Windows'ta 4'e dustugunu belirtti: *"3 platformda da calisacak"*.

### Arastirma (kullanicinin onerisiyle)
KDE Partition Manager ve GParted incelendi: **ikisi de kendi bicimlendiricisini
yazmiyor**, harici `mkfs.*` cagiriyorlar. Yani saf Python yaklasimimiz bu
araclarin otesinde. NTFS'in tek acik referansi `ntfsprogs/mkntfs.c`.

### Yapilanlar
1. **Arayuz:** bicimlendirme listesi artik tum bicimleri gosteriyor;
   kullanilamayanlar gri ve yaninda nedeni yaziyor (`all_kinds`).
2. **`core/ext.py`** (yeni): ext2/ext3/ext4 saf Python — uc hata bulunup
   duzeltildi (resize_inode bayragi, dis gunluk isareti, gunluk yazim sirasi).
   `fsck` temiz, Pop!_OS cekirdegi uctü de **bagladi**.
3. **`core/ntfs.py`** (yeni): NTFS saf Python — bes hata bulunup duzeltildi
   (dizin siralamasi, sira numaralari, oznitelik kimlikleri, rezerve kayitlarda
   $FILE_NAME, $Bad kosusu). `ntfsinfo`/`ntfsfix` temiz; Windows `chkdsk`
   Asama 1 temiz, Asama 2'de `$Extend` alt yapilari eksik.
4. `_ntfs_data.py`: $AttrDef gomulu; $UpCase Python'dan uretilip 244 istisnayla
   duzeltiliyor — sonuc `mkfs.ntfs` ile **birebir ayni**, kaynak 3,5 KB.

Ayrinti: [ADR 0017](../decisions/0017-saf-python-ext-ve-ntfs.md)

### NTFS tamamlandi — uc katmanli strateji (ADR 0018)
Kullanicinin sorusu (*"ntfs-3g kullanmak yeterli olmuyor mu?"*) dogru yeri
isaret etti. Olculdu:
- Linux'ta `mkfs.ntfs` + `libntfs-3g.so` **zaten vardi** ve kullaniliyordu.
- Windows'ta ntfs-3g **yok** (resmi derleme yok), ama `Format-Volume` **var**.

Cozum: NTFS icin oncelik sirasi — (1) isletim sisteminin kendi araci,
(2) harici `mkfs.*`, (3) saf Python. `session._try_native_format()` fiziksel
disklerde Windows'un bicimlendiricisini cagirir; basarisizsa alt katmana duser.

**Win10 dogrulamasi:**
```
Get-Volume E:  ->  FileSystem: NTFS, Label: DUNTFS, Healthy
chkdsk E:      ->  "Windows has scanned the file system and found no problems."
```

### Durum
`tests.run_all` **17/17** · `platform_check` 0 bulgu.
Sekiz dosya sistemi de uc platformda olusturulabiliyor.

## 2026-09-14 — Tema kaldirma, fiziksel diskler, salt-okunur teshisi

### Tema kaldirildi
Kullanici geri bildirimi: *"temayi simdilik kaldir bazi seyler belli olmuyor."*
`ui/theme.py` icindeki `STYLESHEET` **uygulanmiyor**; sistem paleti kullaniliyor.
Renk gereken yerlerde `theme.palette_color(widget, role)` cagriliyor, boylece
koyu/acik temada da okunur kaliyor. Tema secimi ileride ayri bir bolum olacak.

### Kapsam genislemesi — fiziksel diskler
Uygulama artik yalnizca goruntu dosyasi degil, **sistemdeki tum diskleri** listeler
(`core/physical.py`). Alti katmanli yazma guvenligi: listeleme serbest → varsayilan
salt okunur → `confirm=True` → `allow_system=True` → bilgi eksikse **reddet**.
Sistem diskinde sari unlem yerine isletim sistemi amblemi gosteriliyor
(`theme.os_icon()`).

### Cok oturumlu agac
Birden fazla `.img` acildiginda sonuncusu digerlerini eziyordu. `MainWindow.sessions`
listesi eklendi; her oturum agacta kendi kokunu alir. Ayni dosyanin iki kez acilmasini
onlemek icin `_yol_anahtari()` → `realpath` + `normpath` + `normcase`.

### Duzeltme: swap etiketi bozuk karakter
Gercek bir Debian ARM goruntusunde "Bolum 2" etiketi bozuk cikiyordu. Neden:
`fsdetect._swap()` etiketi **1040** ofsetinden okuyordu; orasi UUID'nin ortasi.
Dogru ofset **1052** (`sws_volume`). Ayrica ASCII disi etiketler atiliyor.

### Salt okunur acilma teshisi (kullanici bildirimi)
*"Goruntu salt okunur acildi"* uyarisinin nedeni belirsizdi. Kullanici kaynagi buldu:
**ayni sanal diski once DiskGenius ile acmis**, DiskGenius dosyayi kilitli tutuyor.

Iki kusur duzeltildi:
1. `image.py` icindeki kilit dali yalnizca `OSError.winerror in (32, 33)` bakiyordu,
   ama Windows bu hatayi **`PermissionError` olarak** firlatir — dal hic calismiyordu.
   Artik `except PermissionError` once yakalaniyor, neden `_readonly_nedeni()` ile
   uc duruma ayriliyor: kilitli / salt okunur isaretli / erisim reddedildi.
2. `session.readonly_reason` fiziksel disk, bicim kisiti (VDI, QCOW2, seyrek VMDK) ve
   dosya duzeyi nedenleri ayirir. GUI tarafinda acilista neden gosterilir; kilit
   durumunda **"Yeniden dene"** dugmesi sunulur (diger program kapatilip yeniden
   denenebilsin diye). Durum cubugunda `🔒 SALT OKUNUR`, yazma islemleri menude pasif.

Dogrulama (Linux): oznitelik → "salt okunur isaretli", normal → yazilabilir,
`readonly=True` → "salt okunur acilmasi istendi".

### Durum
`tests.run_all` **17/17** · `platform_check` 0 bulgu · `ui_smoke` gecti.

## 2026-09-14 (2) — Bolum boyutlandirma / tasima

Kullanici DiskGenius'un "Bolumu Boyutlandir" penceresini ornek gostererek
**fareyle surukleyerek** boyutlandirma istedi.

### Cekirdek: `core/resize.py` (yeni)
- `window_for()` — bolumun komsu bos alanlarla birlikte kapsayici alani.
- `fs_resize_info()` — dosya sisteminin **asgari** (veri) ve **azami** (bicim)
  sektor sinirlari. Sinir disi istek plan asamasinda **reddedilir**.
- `fat_resize()` — FAT12/16/32. Buyutmede FAT tablosu buyudugu icin kok dizin +
  veri bolgesi `num_fats × fark` sektor ileri kaydirilir; kume numaralari
  degismedigi icin FAT icerigi oldugu gibi tasinir. Kucultmede yerlesim korunur,
  veri kopyalanmaz.
- `exfat_resize()` — ayni kaydirma FAT bolgesi icin; ayirma bitmap'i gerekirse
  **en dusuk** bos ardisik alana tasinir (bkz. asagidaki hata).
- `apply_resize()` — sira: kucultmede once FS, buyutmede once tablo; tasimada
  cakisma yonune gore ileri/geri kopyalama.
- `_patch_partition_offset()` — tasima sonrasi FAT/NTFS `BPB_HiddSec` (ofset 28),
  exFAT `PartitionOffset` (ofset 64) + saglama, NTFS'in son sektordeki yedek
  onyukleme kopyasi.

`session.resize_window/resize_info/plan_resize/resize_partition` eklendi.
`resize_partition` **`confirm=True` olmadan calismaz**. Fiziksel disklerde once
`platform.windows_resize_partition` (`Resize-Partition`) denenir — NTFS/ext saf
Python'da boyutlandirilamadigi icin (ADR 0018'deki uc katmanli strateji).

### Arayuz
- `ui/widgets/resize_bar.py` — suruklenebilir serit. Sol tutamak bolumu tasir,
  sag tutamak boyutlandirir, govde suruklemesi kaydirir. Ok tuslari ince ayar
  (Shift ile 16 kat). Her deger hizalama birimine yuvarlanir; surukleyerek
  hizasiz bolum uretilemez.
- `ui/dialogs/resize.py` — serit + "Yeni Kapasite / Baslangic-Bitis Kesimi /
  Onundeki-Arkasindaki Bosluk" kutulari, cift yonlu bagli. Pencere hicbir sey
  yazmaz, yalnizca istenen yerlesimi dondurur.
- Bolum menusu, arac cubugu ve sag tik menusune "Bolumu boyutlandir..." eklendi.

### Hatalar ve duzeltmeleri
- **FAT32 FSInfo bos kume sayaci bayat kaldi** (`fsck.vfat`: "Free cluster
  summary wrong"). Neden: `flush()` yalnizca FAT onbellegi kirliyse
  `_update_fsinfo()` cagiriyor; buyutme yolunda onbellek hic yuklenmiyordu.
  Duzeltme: her iki yolda da `_update_fsinfo()` acikca cagriliyor.
- **exFAT bitmap'i birimi kilitledi.** Bitmap buyudugunde **en yuksek** bos
  alana tasiniyordu; bu, "en yuksek kullanilan kume" degerini tavana cakti ve
  birim bir daha kucultulemez oldu. Duzeltme: en dusuk bos ardisik alan.
- **Diyalogda kutular ust uste bindi.** `QFormLayout` satirlarinin "en kucuk"
  yuksekligi tercih edilenden dusuk; pencere kucultulunce kutular eziliyordu.
  Duzeltme: `QLayout.SetMinimumSize` + dikey kucultmenin kapatilmasi.

### Dogrulama
- FAT32 200 MB → 80 MB → 450 MB → +100 MB tasima: `fsck.vfat` rc=0.
- exFAT 200 MB → 100 MB → 800 MB → 300 MB → tasima: `fsck.exfat` rc=0.
- GPT'de tasima+buyutme sonrasi bolum GUID'i korunuyor.
- Guvenlik kapilari test ediliyor: asgari sinir, disk siniri, onaysiz cagri.

### Durum
`tests.run_all` **18/18** · `platform_check` 0 bulgu · `ui_smoke` gecti.
Karar kaydi: `.claude/decisions/0019-bolum-boyutlandirma.md`.

---

## 2026-09-15 (6) — Takilan aygit gorunmuyordu (kullanici bildirimi)

### Bildirilen sorun
*"Ana bilgisayar uzerinde takili olan SD kart gorunmuyor; USB, SD vb. tum
diskleri gorebiliyor olmali."*

### Teshis
Once cekirdek olculdu (salt okunur, sektor okunmadan). `physical.list_disks()`
SD karti **dogru sekilde donduruyordu**:

```
PhysicalDrive1   63,864,569,856  'Generic- SD/MMC/MS PRO'   bus=USB  cikarilabilir=True
PhysicalDrive2   15,791,554,560  'SanDisk Cruzer Force'     bus=USB  cikarilabilir=True
```

Yani listeleme saglamdi. Kusur **arayuzdeydi**: agac yalnizca dort yerde
kuruluyordu — acilis, `close_image`, `refresh` ve elle "Fiziksel diskleri
yenile". Uygulama acikken takilan bir aygit, kullanici elle yenilemedikce
listede hic gorunmuyordu.

### Cozum
`MainWindow._poll_disks()` + 3 saniyelik `QTimer` (`DISK_POLL_MS`).

- Tarama ucuz: ana makinede olculen sure **~17 ms**.
- Agac yalnizca liste **gercekten** degistiginde yeniden kurulur. Karsilastirma
  `_disk_signature()` ile yapilir; imzaya boyut da girer, cunku kart
  okuyucuda kart degistirildiginde aygit yolu ayni kalir ama boyut degisir.
  Boylece yoklama kullanicinin secimini ve acik dallarini bosuna bozmaz.
- Takma/cikarma gunluge yazilir ("Aygit takildi: ... (59.48 GB)").
- Cikarilan aygitin **adi** yeniden kurulumdan once saklanir; yoksa gunlukte
  ham aygit yolu goruluyordu.
- `_add_physical_disks(disks=...)` hazir listeyi alir, ikinci tarama yapilmaz.
- `closeEvent` zamanlayiciyi once durdurur (kapanista yikilmakta olan agaca
  dokunmasin diye).

Dogrulama: ana makinede agac artik uc diski de gosteriyor —
dahili NVMe, SD kart (59.48 GB) ve SanDisk USB (14.71 GB).

### Yan bulgu — yanlis guvenlik ifadesi duzeltildi
`list_disks()` docstring'i *"Hicbir diski acmaz"* diyordu. Windows'ta bu
**dogru degil**: boyut/model/veriyolu yalnizca aygit tutamaci uzerinden
sorgulanabildigi icin her aygita salt okunur (`GENERIC_READ`, paylasimli) bir
tutamac acilip hemen kapatiliyor. Veri okunmuyor, yazma yapilmiyor. Ifade
kesinlestirildi: "hicbir sektor okunmaz, hicbir yazma yapilmaz".

> CLAUDE.md, README ve ADR 0014'teki "Listeleme zararsizdir — hicbir diski
> acmaz" cumlesi de ayni nedenle teknik olarak eksik. Davranis degismedi
> (Windows'ta baska yolu yok); ifadenin guncellenmesi kullanicinin karari.

### Regresyon korumasi
`ui_smoke` icine takma/cikarma denetimi eklendi. Gercek diske dokunulmaz:
`DiskSession.list_physical_disks` sahte bir listeyle degistirilip aygit takma,
cikarma ve "degisiklik yokken agac yenilenmemeli" durumu olculur.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Ana makinenin diskleri uzerinde **hicbir yazma veya sektor okuma yapilmadi.**

---

## 2026-09-15 (7) — Guvenlik katmani 1'in ifadesi kesinlestirildi

Onceki oturumda saptanan yanlis ifade, kullanicinin onayiyla tum belgelerde
duzeltildi. **Davranis degismedi** — yalnizca verilen guvence dogru anlatiliyor.

### Neden yanlisti
Katman 1 "Listeleme zararsizdir — **hicbir diski acmaz**" diyordu. Linux ve
macOS icin dogru (`/sys/block`, `/proc/mounts`, `diskutil` okunur), ama
Windows'ta **degil**: boyut, model ve veriyolu yalnizca bir aygit tutamaci
uzerinden sorgulanabilir (`IOCTL_DISK_GET_LENGTH_INFO`,
`IOCTL_STORAGE_QUERY_PROPERTY`). Bu yuzden `_list_windows()` her aygit icin
salt okunur (`GENERIC_READ`, `FILE_SHARE_READ|WRITE`, `OPEN_EXISTING`) bir
tutamac acar ve hemen kapatir.

Verilen gercek guvence "aygit acilmaz" degil, **"veri okunmaz ve yazilmaz"**.

### Guncellenen yerler
| Dosya | Ne degisti |
|---|---|
| `CLAUDE.md` | Katman 1 metni + Windows parantezi |
| `README.md` | Guvenlik bolumu, ayni metin |
| `decisions/0014-...md` | Katman 1 + tarihli **duzeltme notu** (neden yanlisti, davranisin degismedigi) |
| `core/physical.py` | Modul basligindaki katman listesi ve `list_disks()` docstring'i |
| `tests/physical_write_test.py` | Olcut denetimi yolundaki yorum |

Karar kaydina duzeltme notu birakildi: ADR'nin ilk hali yanlis bir guvence
veriyordu, bunun izi silinmedi.

### Dogrulama
`platform_check` 0 bulgu · `run_all` 17/18 · 1 atlandi · belge ici baglantilar
0 kirik. Fiziksel disklere dokunulmadi.

---

## 2026-09-15 (8) — `.dub` yedegi "bos disk" gibi aciliyordu (kullanici bildirimi)

### Bildirilen sorun
Kullanici 1.03 GB'lik `sdcard.img` dosyasini yedekledi (`sdcard.img.dub`, 34 MB),
sonra yedegi **Goruntu ac** ile acti: 34 MB'lik **bos** bir disk gorundu, oysa
iki bolum ve dosyalar olmaliydi.

### Once: veri guvende mi?
Panige yer olup olmadigi once olculdu. Yedek `.tmp/` icine geri yuklendi ve
kaynakla karsilastirildi:

```
kaynak : 1,107,296,768 bayt   sha256 9999fa8677b8947e...
geri   : 1,107,296,768 bayt   sha256 9999fa8677b8947e...
SONUC: BIREBIR AYNI
```

Yedek **kusursuzdu**; geri yuklenen goruntu acildiginda iki bolum de yerindeydi
(FAT16 onyukleme: `MLO`, `u-boot.img`, `zImage`, `extlinux` + ext4 `rootfs`).

### Kok neden
`.dub` basliginin **510. baytinda `0xAA55`** durur (specs/dub.md). Bu, basligi
512 bayta tamamlamak icin konmus ama yan etkisi agir: yedek ham goruntu olarak
acildiginda MBR cozumleyicisi imzayi **gecerli** bulur, 446-510 arasi sifir
oldugu icin de "bolum yok" der. Sonuc: 34 MB'lik (dosyanin kendi boyutu) bos bir
disk. Veri kaybi yok, ama kullanici "bolumlerim gitti" diye dusunuyor.

Acma yolunda hicbir yerde yedek imzasi denetlenmiyordu.

### Cozum
- `clone.is_backup_file(path)` — yalnizca `DUBACKUP` imzasina bakar, ucuzdur.
- `session.is_backup_file` / `session.restore_to_new_image` cepheye eklendi.
  Ikincisi hedefi yedegin kaydettigi boyutta **yeni** dosya olarak yaratir;
  mevcut hicbir disk veya goruntu uzerine yazmaz.
- `main_window.open_path` artik once imzaya bakar. Yedek secilirse ham acilmaz;
  boyut/dosya sistemi/etiket/tarih gosterilip uc secenek sunulur:
  **Yeni goruntuye geri yukle...** · Yalnizca bilgi · Kapat.
  Geri yukleme bitince olusan goruntu kendiliginden acilir — kullanicinin
  "yedegi acmak" derken kastettigi sonuc budur.
- `specs/dub.md` icine `0xAA55`'in bu yanilgiyi dogurdugu uyarisi yazildi.

`0xAA55` **kaldirilmadi**: bicim degisikligi olurdu ve mevcut yedekler bu alani
tasiyor. Uygulama icinde imza denetimi sorunu tamamen kapatiyor; baska araclarda
ayni yanilgi olusabilecegi spec'te belirtildi.

### Regresyon korumasi
`t11_yedekleme_ve_klonlama` genisletildi: `.dub` yedek olarak taninmali, ham
goruntu taninmamali, disk yedegi yeni bir goruntuye acilinca **bolum tablosu ve
dosyalar geri gelmeli**. Ilk yazimda iddia bolum yedegi uzerine kuruldugu icin
test hakli olarak patladi (bolum yedeginde tablo yoktur); disk yedegiyle
degistirildi — kullanicinin yasadigi senaryo da zaten budur.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Dogrulama sirasinda uretilen 2.06 GB gecici dosya silindi. Fiziksel disklere
dokunulmadi; tum islemler dosyalar uzerinde yapildi.

---

## 2026-09-15 (9) — `.dub` gezilebilir oldu, hedef olarak fiziksel disk eklendi

### Kullanici istegi
*"DUB kayit dosyasi acildiginda icerigi, disk ve bolumler de gorulmeli,
dosyalara ulasilabilmeli. Bu kayit fiziksel veya sanal diske yazilabilmeli;
su an sadece sanal diske yazabiliyoruz."*

Onceki oturumda yedek acilinca yonlendirme diyalogu gosteriliyordu — yani once
geri yukle, sonra gez. Istenen bu degil: yedek **dogrudan** gezilebilmeli.

### 1. Yedek artik salt okunur bir disk gibi aciliyor
`.dub` bicimi blok tablolidir: indeks her blok icin (tur, uzunluk, ofset)
tasir, yani **rastgele erisime uygundur**. Geri yukleme sirf bu yuzden
gereksizdi.

`clone.DubImage(BlockDevice)` eklendi:
- Istenen bayt araligini kapsayan bloklari bulur, `zlib` olanlari acar,
  sifir bloklar icin sifir uretir (dosyada yer kaplamazlar).
- 4 bloklu kucuk bir LRU onbellegi: ardisik okumalarda ayni blok tekrar
  acilmaz. Dosya sistemi surucusu cok sayida kucuk okuma yaptigi icin onemli.
- Son blok kisa olabilir; eksik kisim sifirla tamamlanir.
- `write()` reddeder — yedek bir arsivdir.

Baglanti: `vdisk.detect_format` `DUBACKUP` imzasini taniyip `"dub"` doner,
`open_disk` `DubImage` acar (her zaman salt okunur; `readonly` yok sayilir),
`format_label` "DiskUltimate yedegi (.dub)" der.

Gercek dosyayla dogrulama (kullanicinin `sdcard.img.dub` yedegi):

```
oturum      : DiskUltimate yedegi (.dub)
sema        : MBR   salt okunur: True
  Bolum 1: NO NAME  FAT16  33,554,432
  Bolum 2: rootfs   ext4   1,073,741,824
dosya gezgini (Bolum 1):
   am335x-mmcu.dtb   70.14 KB  DTB dosyasi
   extlinux                    Klasor
   MLO              107.93 KB  Dosya
   u-boot.img         1.49 MB  Disk goruntusu
   uEnv.txt           1.15 KB  Metin belgesi
   zImage             6.25 MB  Dosya
```

ext4 bolumu listelenemiyor — bu yedege ozgu degil, ext4 okuyucusu henuz yok
(v0.4 hedefi). FAT/exFAT icerigi tam gezilebiliyor.

### 2. Yedegi hedefe yazma
`Disk > Yedegi diske yaz...` (yalnizca acik oturum bir yedekken etkin) iki
hedef sunar:
- **Yeni goruntu dosyasi** — `session.restore_to_new_image`, hedefi yedegin
  kaydettigi boyutta yaratir, mevcut hicbir sey uzerine yazmaz.
- **Fiziksel disk** — `session.restore_to_physical`. ADR 0014 kapilarinin
  tamami gecerli: yazma onayi, **bilgisi eksik diskte ret**, sistem diskinde
  disk adini yazarak dogrulama, bagli bolum uyarisi, boyut denetimi.
  `PhysicalDisk(..., readonly=False, confirm=True)` ile acilir.

Fiziksel hedef **bu makinede denenmedi** (CLAUDE.md: ana makinenin diskleri
uzerinde deneme yapilmaz). Kod yolu ve guvenlik kapilari statik olarak
dogrulandi; gercek yazma testi sanal makinede yapilacak.

### 3. Acilista bilgilendirme
Yedek acilinca bir kereye mahsus not gosterilir: salt okunur oldugu, icerigin
gezilebildigi, yazmak icin nereye bakilacagi. Onceki yonlendirme diyalogu
(`_backup_opened_warning`) kaldirildi — artik gezmeyi engellemiyor.

### Regresyon korumasi
`t11` genisletildi: yedek `is_backup` ve `readonly` olmali, bolum tablosu ve
dosya icerigi yedekten **geri yuklemeden** okunabilmeli, yazma denemesi
reddedilmeli.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Fiziksel disklere yazilmadi.

---

## 2026-09-15 (10) — ext2/3/4 salt okunur okuyucu

### Soru
*"sdcard.img neden ext4 bolum dosyalarini goremiyorum?"*

Cevap: `.dub` ile ilgisi yoktu. DiskUltimate ext ailesini **bicimlendirebiliyor**
ama **okuyamiyordu**; gercek `.img` dosyasinda da ayni durum vardi. Okuyucu v0.4
yol haritasindaydi — bu oturumda yazildi.

### `core/extread.py` (yeni)
- Ustblok: blok boyutu, grup/inode sayilari, inode boyutu, etiket, `incompat`
  bayraklari. rev0 (ext2) icin sabit degerlere duser.
- Grup tanimlayicilari: `INCOMPAT_64BIT` acikken 64 baytlik tanimlayici ve
  yuksek 32 bitlik blok numaralari desteklenir.
- Blok haritalama iki yol: **extent agaci** (ext4; yaprak + ic dugum, uninit
  extent'lerde uzunluk maskesi) ve **dolayli blok** zinciri (ext2/ext3;
  tek/cift/uc kat).
- Dizin girisleri, sembolik bag hedefi (hizli bag `i_block` icinde), yol
  cozumleme ve **sembolik bag izleme** (goreli hedefler, dongu icin derinlik
  siniri).

`filesystem.ExtAccess` bunu `FileSystemAccess` arayuzune baglar; `writable=False`
oldugu icin arayuz yazma eylemlerini kendiliginden pasifler. `open_filesystem`
artik `ext*` icin bu sinifi dondurur.

### Iki hata yakalandi ve duzeltildi
1. **Sembolik bag icerik sanildi.** `/etc/os-release` bir hizli bagdir: hedef
   metni (`../usr/lib/...`) `i_block` icinde durur. `read_data` bunu blok
   numarasi dizisi gibi yorumlayip cop adresler uretti ve "okuma bolum sinirini
   asiyor" hatasi verdi. Artik `read_data` sembolik bagi acikca reddediyor,
   `resolve(follow=True)` bagi izliyor.
2. **Sinir disi blok numarasi coktururdu.** Bozuk ya da yanlis yorumlanmis bir
   numara `BlockDevice` sinirini asiyordu. Tum blok okumalari `_read_block()`
   uzerinden gecirildi: sinir disi numara sifir doner, okuyucu bozuk birimde de
   cokmez.

### Yanlis mesaj duzeltildi
`UnsupportedAccess` "yol haritasinda: ext4/NTFS/**exFAT** okuyucu" diyordu —
exFAT okuma zaten calisiyordu. Mesaj artik okunabilen dosya sistemlerini
sayiyor ve yalnizca NTFS'i yol haritasinda gosteriyor.

### Gercek veriyle dogrulama
Kullanicinin `sdcard.img` dosyasindaki `rootfs` (ext4, 1 GB, extent'li):

```
kok dizin : bin dev etc lib lib32-> libexec linuxrc-> lost+found media mnt
            opt proc root run sbin sys tmp usr var   (19 giris)
/etc      : 23 giris
/etc/fstab, /etc/hostname : metin dogru okundu
/etc/os-release -> ../usr/lib/os-release izlendi, 101 bayt, "NAME..." ile basliyor
/bin/busybox : 870,036 / 870,036 bayt, ELF imzasi dogru (extent agaci)
/bin/sh, /sbin/init, /linuxrc -> hepsi busybox'a cozuldu
```

Ayni icerik **`.dub` yedeginden de** okunuyor (DubImage + ExtFS birlikte).

### Regresyon korumasi
`t16` genisletildi: ext2/ext3/ext4 bicimlendirildikten sonra ayni birim
`ExtFS` ile acilir, etiket ve blok boyutu dogrulanir, kok dizinde
`lost+found` aranir, `open_filesystem` `ExtAccess` dondurmeli ve
`readable/not writable` olmali. Uc surum de ayni kod yolundan gecer
(ext2/3 dolayli blok, ext4 extent).

### Sinir
Salt okunurdur. Yazma, silme ve yeniden adlandirma yoktur — bunlar gunluk
(journal) tutarliligi gerektirir ve ayri bir istir.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Arayuzde hem `.img` hem `.dub` icin ext4 bolumu geziliyor.

---

## 2026-09-15 (11) — "ext4 bolume yazamiyorum" — yaniltici mesaj duzeltildi

### Bildirilen durum
Kullanici SD karti (`PhysicalDrive1`) **yazma modunda** acti, ext4 bolume
yazamadi. Arayuz "Bu bolum salt okunur acildi." diyordu.

### Sorun mesajdaydi
Iki ayri neden ayni metni uretiyordu:
1. **Kaynak** salt okunur acildi (cozulebilir: yazma modunda ac)
2. **Surucu** yazma desteklemiyor (cozulemez: ext yazici yok)

Kullanici (1)'i zaten yapmisti; mesaj onu yanlis yere yonlendiriyordu.

### Cozum
`FileSystemAccess.write_reason` eklendi; her surucu kendi nedenini soyler:
- `FatAccess` / `ExFatAccess`: "Kaynak salt okunur acildi. Goruntuyu/diski
  yazma modunda acarsaniz bu bolume yazabilirsiniz."
- `ExtAccess`: "ext2/3/4 surucusu SALT OKUNURDUR... **Diski yazma modunda
  acmis olmaniz bunu degistirmez.**"

Arayuz bu metni diyalogda gosterir; ayrica pasif dugmelerin **ipucunda** durur,
cunku pasif bir dugme tiklanamadigi icin diyalog hic acilmayabilir.

### ext4 yazma neden buyuk bir is (olculdu)
Kullanicinin `rootfs` birimi:

```
compat    has_journal, ext_attr, resize_inode, dir_index
incompat  filetype, extents, flex_bg, csum_seed
ro_compat sparse_super, large_file, huge_file, dir_nlink, extra_isize,
          metadata_csum        <-- saglama ZORUNLU
```

`metadata_csum` acik: ustblok, grup tanimlayicilari, inode'lar, extent
bloklari, dizin bloklari ve bitmap'lerin **her biri** CRC32c saglamasi tasir.
Yazan taraf bunlarin tamamini dogru guncellemek zorundadir; biri yanlis olursa
`e2fsck` birimi bozuk sayar. Ustune `has_journal` var: gunluk ya dogru
islenmeli ya da birim tutarli birakilmali.

Yani ext4 yazma "bir islev daha" degil, ayri bir calisma: blok/inode tahsisi,
bitmap ve sayac guncellemesi, extent agaci degisikligi, dizin girisi ekleme
(dir_index/htree dahil), CRC32c saglamalar ve gunluk. Ustelik ilk hedef
**gercek, onyuklenebilir bir SD kart** oldugu icin hata maliyeti yuksek.

Bu oturumda uygulanmadi; karar kullaniciya birakildi.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti.
Kullanicinin fiziksel diskine **yazilmadi**; ozellik bayraklari yalnizca
okunarak olculdu.

---

## 2026-09-15 (12) — Dosya sistemi matrisi; ext yerlesim hatasi bulundu

### Istek
*"Sanal makinede uretilmis test birimlerinde gelistirip `e2fsck` ile
dogrularız — bunu yapalim, tum dosya bicimlerinde deneyebilir miyiz?"*

### `tests/fs_matrix.py` (yeni)
Sekiz bicim, ayni adimlar: **bicimlendir → tespit → oku → yaz → harici fsck**.
Sonuc tek tabloda toplanir.

Tasarim kurali: harici arac yoksa sonuc **ATLANDI** yazilir, asla TAMAM
sayilmaz. Aracsiz bir makinede tablo yaniltmaz — `e2fsck` yoksa ext yazmasinin
dogrulanmadigi acikca gorunur.

Ayrica ext yazma gelistirmesinin kosum alanidir: yazici uygulandikca `yaz`
sutunu kendiliginden dolar, `e2fsck` sutunu onu dogrular.

Ana makinedeki (Windows) durum — **hicbir fsck araci yok**, WSL kurulu degil:

```
Bicim | Bicimlendir | Tespit | Oku     | Yaz                   | Harici dogrulama
fat32 | TAMAM       | TAMAM  | TAMAM   | TAMAM                 | ATLANDI (fsck.vfat yok)
exfat | TAMAM       | TAMAM  | TAMAM   | TAMAM                 | ATLANDI (fsck.exfat yok)
ntfs  | TAMAM       | TAMAM  | ATLANDI | ATLANDI (okuyucu yok) | ATLANDI (ntfsfix yok)
ext4  | TAMAM       | TAMAM  | TAMAM   | ATLANDI (salt okunur) | ATLANDI (e2fsck yok)
```

### Matrisin ilk bulgusu: gercek bir hata
`ext3`/`ext4` **tam 129 MB** bolumde coktu (128 ve 130 MB sorunsuz):
`Yazma bolum sinirini asiyor`.

Kok neden: grup sayisi yukari yuvarlanir, bu yuzden kucuk bir artik tam bir
grup dogurur. 129 MB / 4 KB = 33024 blok; grup basi 32768 → 2 grup, sonuncuda
yalnizca 256 blok. Ama o grup da kendi metaverisini (yedek superblok + GDT +
iki bitmap + ~258 bloklu inode tablosu) istiyor → 260 > 256, yerlesim bolum
sinirini asiyor.

`mke2fs` bu durumda dosya sistemini grup sinirina **kirpar**. `_compute_layout`
artik ayni seyi yapiyor: son grup metaverisini alamiyorsa tumuyle dusurulur.
Kirpma inode tablosu boyutunu degistirdigi icin hesap donguyle tekrarlanir.

Dogrulama: ext2/ext3/ext4 × 60–140 MB (birer MB) + 150/200/256/300/512 MB →
**255/255 kombinasyon** bicimlendi ve her biri `ExtFS` ile acilip kok dizininde
`lost+found` dogrulandi.

### Denetimde iki kusur (ikisi de kendi eklediklerimde)
1. `last_blocks` degiskeni `st_blocks` kuralina takildi — kural alt dize
   ariyordu. `\bst_blocks\b` yapildi; iki yonlu test edildi (gercek
   `os.stat().st_blocks` yakalaniyor, `last_blocks` yakalanmiyor).
2. O duzeltmeyi kabuk uzerinden yazarken `\b` **backspace karakterine** donustu
   ve kural bir sure hicbir seyi yakalamadi. Olumsuz test bunu ortaya cikardi;
   bayt duzeyinde onarildi. Ders: kacis dizisi iceren duzenli ifadeler kabuk
   uzerinden yazilmamali.

### ext yazma icin ortam
Ana makinede dogrulama **mumkun degil**. Kullanici VMware'de **Linux Mint**
oldugunu bildirdi — gerekli araclarin tamami orada:

```bash
sudo apt install e2fsprogs dosfstools exfatprogs ntfs-3g
python3 -m tests.fs_matrix          # dort dogrulayici da calisir
```

ext yazma gelistirmesi bu ortamda, `e2fsck` her adimda kosularak yapilacak.

### Dogrulama
`run_all` 17/18 · 1 atlandi · `platform_check` 0 bulgu · `ui_smoke` gecti ·
`fs_matrix` 8/8 hatasiz (dogrulama adimlari atlandi).

---

## 2026-09-15 (13) — Linux Mint dogrulama ortami kuruldu; sekiz bicim fsck ile dogrulandi

### Ortam
Kullanici VMware Linux Mint misafirine SSH erisimi verdi (`192.168.42.131`),
proje `/mnt/hgfs/...` ile paylasildi ve misafire **bos 10 GB `/dev/sdb`** eklendi.

Kurulum: `python3-pyqt5` kuruldu. Dogrulayicilarin tamami zaten vardi —
`fsck.vfat`, `fsck.exfat`, `e2fsck`, `ntfsfix`, `ntfsinfo`, `mkfs.*`.

**Testler paylasilan klasorde kosulmadi.** `vmhgfs` seyrek dosya desteklemez;
goruntuler tum boyutlariyla yazilip ana makinenin diskini doldurabilirdi (bu
daha once yasanmis, bkz. 4. oturum). Kaynak `~/du-test` altina kopyalandi.

### Ilk gercek capraz dogrulama
`tests.run_all` → **17/18 · 1 atlandi**. Bu kez `t16`/`t17`/`t18` gercek
`e2fsck` / `ntfsfix` / `fsck.vfat` ile kosuldu (Windows'ta bu adimlar
atlaniyordu).

`tests.fs_matrix` → **8/8 hatasiz, harici dogrulamalarin tamami TAMAM**:

| Bicim | Bicimlendir | Oku | Yaz | fsck |
|---|---|---|---|---|
| fat12/16/32 | ✅ | ✅ | ✅ | ✅ `fsck.vfat` |
| exfat | ✅ | ✅ | ✅ | ✅ `fsck.exfat` |
| ntfs | ✅ | okuyucu yok | okuyucu yok | ✅ `ntfsfix` |
| ext2/3/4 | ✅ | ✅ | salt okunur | ✅ `e2fsck` |

### Matriste bir yanlis alarm — kusur bendeydi
Ilk kosumda NTFS "alternate boot sector BAD" verdi. Cozumleme, birimin
**saglam** oldugunu gosterdi: `total_sectors` 262143 = bolum sektoru − 1 ve
yedek onyukleme sektoru tam o konumda.

Kusur `fs_matrix._extract` icindeydi: bolumu ayiklarken dosyanin **sonuna
kadar** kopyaliyordu, yani 128 MB'lik birim 129 MB'lik dosyaya donusuyordu.
NTFS yedek onyukleme sektorunu aygitin **son** sektorunde arar; fazladan kuyruk
yuzunden orada sifir buldu. FAT/exFAT/ext boyutu ustbloktan okudugu icin
etkilenmedi. `_extract` artik tam bolum uzunlugu kadar kopyaliyor ve NTFS
`ntfsfix` ile temiz cikiyor.

> Ders: bir dogrulama koşumu hata bildirdiginde once **koşumun kendisi**
> sorgulanmali. "NTFS bicimlendiricimiz bozuk" diye rapor etseydim yanlis olurdu.

### ext4 okuyucu, Linux cekirdek surucusuyle karsilastirildi
Kullanicinin gercek `sdcard.img` dosyasi `mount -o ro,loop,offset=...` ile
baglandi ve **ayni birim** hem cekirdek hem bizim okuyucumuzla okundu:

```
kok dizin ayni mi : True   (19 giris)
/etc giris sayisi : 23 = 23
busybox sha256    : 50e486029e849c99...  (870,036 bayt, extent agaci)
referansla AYNI mi: EVET
```

Okuyucu cikti duzeyinde cekirdekle **birebir** ayni. Referans baglanti
kaldirildi; `/dev/sdb` bu oturumda **hic kullanilmadi** (durumu dogrulandi:
bos, bagli degil).

### Dogrulama
Linux: `run_all` 17/18 · 1 atlandi · `fs_matrix` 8/8.
Windows (ana makine): `run_all` 17/18 · `platform_check` 0 bulgu · `ui_smoke` gecti.

---

## 2026-09-15 (14) — ext2/3/4 yazma destegi (1. asama), e2fsck ile dogrulandi

### Kapsami belirleyen olcum
Kendi bicimlendiricimizin urettigi ext birimleri **metadata_csum, 64bit ve
extent kullanmiyor** (yalnizca `filetype`). Bu, ilk asamayi klasik dolayli blok
yazimina indirdi — saglama ve extent agaci isin disinda kaldi.

### `core/extwrite.py` (yeni)
- Blok ve inode **bitmap tahsisi**; grup tanimlayici sayaclari
  (`free_blocks`, `free_inodes`, `used_dirs`) ve ustblok sayaclari birlikte
  guncellenir.
- Inode yazimi (`i_blocks` 512'lik sektor cinsinden, dolayli bloklar dahil).
- Veri yerlesimi: 12 dogrudan blok + tek kat dolayli (4 KB blokta ~4 MB).
  Ustu acikca reddedilir.
- Dizin girisi ekleme/silme: `rec_len` zinciri dogru bolunur ve birlestirilir;
  yer kalmazsa dizine yeni blok eklenir.
- `mkdir` ( `.` / `..` girisleri, ust dizinin `links_count` artisi), `remove`
  (bos klasor denetimi, `dtime`, blok/inode serbest birakma), `rename`.

### Guvenlik kapisi
`write_support()` su durumlarda yazmayi **reddeder**: `metadata_csum`,
`bigalloc`, `64bit`, `inline_data` ve extent kullanan inode. Neden metinle
dondurulur, arayuz bunu gosterir. **Yanlis yazip bozmaktansa yazmamak yeglenir.**

### Dogrulama — `tests/ext_write_check.py` (yeni)
Her adimdan sonra `e2fsck -nf`: bicimlendirme → kucuk dosya → mkdir → dolayli
bloklu 256 KB dosya → uzun ad → 60 giris → rename → dosya silme → klasor silme.
`e2fsck` yoksa kosum **basarisiz sayilir**, atlanmaz.

Linux Mint misafirinde: **ext2, ext3, ext4 → 3/3 dogrulandi.**

`fs_matrix` artik ext satirlarinda da dolu:

| Bicim | Bicimlendir | Oku | Yaz | fsck |
|---|---|---|---|---|
| fat12/16/32 | ✅ | ✅ | ✅ | ✅ `fsck.vfat` |
| exfat | ✅ | ✅ | ✅ | ✅ `fsck.exfat` |
| ntfs | ✅ | okuyucu yok | okuyucu yok | ✅ `ntfsfix` |
| **ext2/3/4** | ✅ | ✅ | **✅** | **✅ `e2fsck`** |

### Gercek kart uzerinde kapi denendi
Kullanicinin `sdcard.img` kopyasi **yazilabilir** acildi ve ext4 bolume yazma
denendi:

```
Bolum 1 (FAT16): yazilabilir=True
Bolum 2 (ext4) : yazilabilir=False
   RET NEDENI: ... metadata_csum (CRC32c saglamalar) ...
Yazma denemesi REDDEDILDI (beklenen)
Kopya degisti mi? DEGISMEDI (birebir ayni)
```

Kapi calisiyor: gercek kart bozulmadi, dosya bayt bayt ayni kaldi.

### Kalan
- `metadata_csum` destegi (kullanicinin kartinin ihtiyaci) — CRC32c saglamalar.
- Extent agaci yazimi.
- Cok katli dolayli blok (4 MB ustu dosya).
- NTFS okuma/yazma.

### Dogrulama
Linux: `ext_write_check` 3/3 · `run_all` 17/18 · `fs_matrix` 8/8.
Windows: `run_all` 17/18 · `platform_check` 0 bulgu · `ui_smoke` gecti.

---

## 2026-09-15 (15) — Fiziksel diskte ext4 uctan uca; bicimlendirme imza hatasi

### Gercek donanimda okuma
SD kart VM'e aktarildi (`/dev/sdc`, 59.48 GB, iki bolumu de masaustu tarafindan
**bagli**). `tests.physical_probe` dogru siniflandirdi: `sda` sistem diski
(`[!]`), `sdb` temiz, `sdc` cikarilabilir + bagli bolumler listelendi.

Salt okunur acilip okundu: MBR, FAT16 (7 giris) ve ext4 (20 giris) sorunsuz.
Yazma dogru gerekcyle reddedildi ("Kaynak salt okunur acildi").

### Bulunan hata: bicimlendirme eski imzayi birakiyordu
Fiziksel diskte ext4 bolum olusturuldu ama acilirken **exFAT surucusu cagrildi
ve cokti**. Neden:

```
sdb1 ofset 0    : eb76 9045 5846 4154  -> "EXFAT" (onceki testten kalma)
sdb1 ofset 1080 : 53ef                 -> ext ustblok imzasi (dogru yazilmis)
```

Her dosya sistemi kendi alanini yazar ama otekinin imzasini **silmez**: exFAT
onyukleme sektoru ofset 0'dadir, ext ilk 1024 bayti rezerve birakip ona
dokunmaz. Sonuc: ext4 birim exFAT sanildi. (`blkid` dogru taniyordu, cunku o
imza oncelik kurallarini biliyor; bizim tespitimiz ilk eslesmeyi aliyor.)

`formatter.wipe_signatures()` eklendi — `mkfs` araclarinin `wipefs` davranisi:
bicimlendirmeden once ilk 128 KB ve **son sektor** (NTFS yedek onyukleme)
sifirlanir.

**`t05` yeniden amaclandirildi.** Eski hali ("harici mkfs ile bicimlendirme")
oluydu: tum bicimler saf Python oldugundan `available_kinds(internal=False)`
hep bos donuyor, test her zaman atlaniyordu. Yerine yeniden bicimlendirme
zinciri kondu: `fat32 → exfat → ntfs → ext4 → fat16 → ext2`, her adimda tespit
dogrulanir. Bu sayede **`run_all` artik 18/18** (atlanan yok).

### ext4 uctan uca fiziksel disk (`/dev/sdb`, bos test diski)
```
[1] yazma modunda acildi        [5] disk kapatildi
[2] ext4 bolum olusturuldu      [6] yeniden acildi: 21 giris, buyuk.bin AYNI
[3] yazilabilir=True            [7] e2fsck /dev/sdb1 -> rc=0
[4] dosyalar yazildi
```

Ardindan **Linux cekirdegi** ile dogrulandi:
```
kok  : lost+found okubeni.txt veri
veri : 21 giris
okubeni.txt: DiskUltimate ext4 fiziksel disk denemesi
buyuk.bin  : 131072 bayt (dolayli blok), cekirdek okudu
```

`physical_write_test` de gecti (GPT + FAT32 + exFAT). Boyut siniri icin
`--azami-gb=N` eklendi: 8 GB varsayilani bir **sezgidir**, 10 GB'lik ayrilmis
test diskleri yaygin. Sistem diski / bagli bolum / bilgi eksikligi olcutleri bu
bayraktan **etkilenmez**.

### Guvenlik
- Hedef her adimda `/dev/sdb` ile sinirlandi; betikte sistem diski ve bagli
  bolum icin sert `assert` var. Bir kosumda bu koruma **gercekten devreye
  girdi**: onceki testin biraktigi bolum masaustunca baglanmisti, islem iptal
  edildi; elle `umount` sonrasi devam edildi.
- Test sonrasi `/dev/sdb` temizlendi.
- **SD kart (`/dev/sdc`) yalnizca okundu**, hicbir yazma yapilmadi; durumu
  bastaki gibi.

### Dogrulama
Linux: `run_all` **18/18** · `ext_write_check` 3/3 · `fs_matrix` 8/8 ·
fiziksel ext4 e2fsck rc=0 + cekirdek baglama.
Windows: `run_all` 18/18 · `platform_check` 0 bulgu.

---

## 2026-09-15 (16) — ext4 `metadata_csum` destegi

### Once dogrulama, sonra yazma
Yazmadan once hesaplarin dogrulugu kanitlandi. `core/crc32c.py` (saf Python
CRC-32C, standart olcutlerle) ve `core/extcsum.py` yazildi; `verify_volume()`
**hicbir sey yazmadan** var olan birimlerin saglamalarini bizim hesabimizla
karsilastiriyor.

Ilk kosumda ustblok, grup tanimlayici ve dizin bloklari tuttu; **inode ve
bitmap tutmadi**. Ikisi de gercek hataydi:

1. **Inode:** saglama zincirinde `i_extra_isize` alani (128..0x82) atlanmisti.
   Cekirdek bu parcayi da karistiriyor. Eklendi.
2. **Bitmap:** `BLOCK_UNINIT`/`INODE_UNINIT` gruplarda bitmap **diskte
   tutulmaz**, cekirdek onu uretir; diskteki baytlarla karsilastirmak
   anlamsizdi. Dogrulama bu gruplari atliyor, yazici da tahsis icin onlari
   kullanmiyor.

Duzeltmelerden sonra **iki gercek birimde de her saglama tuttu**:

| Birim | ustblok | grup td. | bitmap | inode | dizin blogu |
|---|---|---|---|---|---|
| `sdcard.img` (csum_seed ozellikli) | 1/1 | 8/8 | 7/7 | 393/393 | 60/60 |
| Mint kok `/dev/sda3` (UUID tohumlu) | 1/1 | 476/476 | 437/437 | 10/10 | 60/60 |

Her iki tohum turevi de (ustblokta saklanan `s_checksum_seed` ve UUID'den
hesaplanan) dogru calisiyor.

### Yazma tarafi
`ExtWriter` artik her degisiklikte ilgili saglamayi tazeliyor: ustblok, grup
tanimlayici, iki bitmap, inode (`_patch_inode` tek alan degisse bile tum kaydi
yeniden damgalar) ve **dizin blogu kuyrugu** (`ext4_dir_entry_tail`).

Ek olarak:
- `64bit` artik tek basina engel degil — yalnizca blok numarasi 32 biti asarsa
  reddedilir. Bitmap blok numaralari yuksek yariyla okunuyor.
- **Extent kullanan dizinler** okunabiliyor: var olan bloklara giris eklemek
  agaci degistirmez. Dizine yeni blok gerekirse acikca reddediliyor.
- **Extent kullanan dosyalar** silinebiliyor (bloklari birakiliyor).
- `bg_itable_unused` tahsiste sifirlaniyor.

### Yakalanan kusur
Ilk denemede `e2fsck` "directory passes checks but fails checksum" dedi. Neden:
`dir_add`/`dir_remove`/`mkdir` icindeki uc yazma yeri hala `_write_block`
kullaniyordu (degisken adi `blk` oldugu icin toplu degistirme kacirmisti), yani
kuyruk saglamasi hic guncellenmiyordu. Uc yer de `_write_dir_block`'a baglandi.
Hata, blogu yazip saglamayi karsilastiran kucuk bir tani betigiyle bulundu.

### Dogrulama
`tests/ext_write_check.py` genisletildi — **4/4**:
```
ext2 ... TAMAM        ext4 ... TAMAM
ext3 ... TAMAM        metadata_csum (mkfs.ext4) ... TAMAM
```
`mkfs.ext4` ciktisi `metadata_csum + 64bit + extent + flex_bg` tasir; gercek
dunyada karsilasilan yerlesim budur.

**Kullanicinin kartinin kopyasi uzerinde uctan uca:**
```
Bolum 2 (ext4) yazilabilir=True
once kok: 19 giris -> sonra kok: 21 giris
e2fsck: temiz (cikis 0)
cekirdek ile baglandi: DISKULTIMATE.txt ve du_deneme/veri.bin okundu
```

Orijinal kart dosyasina **dokunulmadi**; islem kopya uzerinde yapildi.

### Kalan
- Extent agaci **buyutme** (dolu bir extent dizinine yeni blok eklemek).
- Cok katli dolayli blok (4 MB ustu dosya).
- `bigalloc`, `inline_data`.
- NTFS okuma/yazma.

### Dogrulama ozeti
Linux: `ext_write_check` 4/4 · `run_all` 18/18 · `fs_matrix` 8/8.
Windows: `run_all` 18/18 · `platform_check` 0 bulgu.

---

## 2026-09-15 (17) — NTFS okuyucu

### Hedef
Kullanici istegi: **NTFS uc platformda B O Y**. Yazma okumayi gerektirdigi icin
once okuyucu yazildi.

### `core/ntfsread.py` (yeni)
NTFS'te her sey `$MFT` icindeki **FILE kayitlari**dir; dosya adi da, veri de,
dizin indeksi de birer **oznitelik**tir. Uygulanan zincir:

```
onyukleme sektoru -> $MFT yeri -> FILE kaydi -> fixup -> oznitelikler
  -> $DATA (yerlesik veya veri kosullari) -> kume zinciri -> bayt
```

- **Fixup (Update Sequence Array):** her `FILE`/`INDX` blogunda her sektorun son
  iki bayti kayit basindaki diziye tasinmistir. Geri konmazsa veri **sessizce
  bozuk** okunur; imza tutmazsa hata verilir.
- **Veri kosullari (runlist):** isaretli ve bir oncekine goreli ofsetler; 0
  ofset seyrek alandir (`lcn = -1`, okunusta sifir doner).
- **Yerlesik / yerlesik olmayan** oznitelikler ayri ele alinir.
- **`$ATTRIBUTE_LIST`:** buyuk dosya/dizinlerde oznitelikler tek kayda sigmaz ve
  baska kayitlara dagilir. Bu baglanti izlenmezse buyuk dizinler **bos gorunur**.
- **Dizinler B+ agacidir:** kucuk dizin `$INDEX_ROOT` icinde yerlesiktir,
  buyuyunce `$INDEX_ALLOCATION` icindeki INDX bloklarina tasar ve agac dolasilir.
- 8.3 kisa adlar (`name_type == 2`), sistem dosyalari ve kokun `.` girisi
  listelemede atlanir.

### Dogrulama — `ntfs-3g` ile karsilastirma
Mint'te `mkntfs` ile birim uretildi, `ntfs-3g` ile dolduruldu, sonra ayni birim
bizim okuyucuyla okundu:

| Olcum | ntfs-3g | DiskUltimate |
|---|---|---|
| kok girisleri | 3 | 3 (ayni adlar) |
| `klasor` giris sayisi | 122 | **122** |
| `buyuk.bin` boyut | 300000 | 300000 |
| `buyuk.bin` sha256 | `6b85f636b5a92cd7…` | **`6b85f636b5a92cd7…`** |

122 girisli dizin `$INDEX_ALLOCATION` yolunu, 300 KB'lik dosya veri kosullarini
zorluyor; ikisi de dogru cikti. Ic ice dizin ve uzun ad da okundu.

### Arayuz baglantisi
`filesystem.NtfsAccess` eklendi (`readable=True`, `writable=False`).
`write_reason` neden yazilamadigini soyluyor: `$MFT`/`$Bitmap` tahsisi ve
dizin B+ agacina ekleme gerekiyor.

`fs_matrix` artik NTFS satirinda **Oku: TAMAM** gosteriyor — ustelik bu,
**kendi bicimlendiricimizin** urettigi birimi okuyor ve ayni birim `ntfsfix`
ile de temiz cikiyor.

### Regresyon korumasi
`t17_ntfs` genisletildi: kendi urettigimiz NTFS birimi `NtfsFS` ile acilir,
etiket ve kume boyutu dogrulanir, `open_filesystem` `NtfsAccess` dondurmeli,
`readable and not writable` olmali.

### Kalan (NTFS yazma)
- `$Bitmap` kume tahsisi ve `$MFT`'nin `$BITMAP`'i ile kayit tahsisi
- Dizin **B+ agacina giris ekleme/silme** (en zor kisim: dugum bolme)
- `$MFTMirr` esitlemesi, fixup dizisi yazimi
- Sikistirilmis akislar (okumada da desteklenmiyor)

### Dogrulama
Windows: `run_all` 18/18 · `platform_check` 0 bulgu.
Linux: `fs_matrix` 8/8 (NTFS Oku TAMAM) · `run_all` 18/18.
