# DiskUltimate — Proje Talimatları (CLAUDE.md)

## Proje Özeti
**DiskGenius benzeri** görsel disk yönetim uygulaması. **Windows / Linux / macOS**.
Hem disk görüntüleri (`.img`, VHD/VDI/VMDK/QCOW2) hem de **sistemdeki gerçek diskler**
üzerinde çalışır.

Yetenekler:
- Görüntü oluşturma / açma / yeniden boyutlandırma; sanal disk: VHD, VDI, VMDK, QCOW2
- Bölüm tablosu: **MBR** ve **GPT**, ve aralarında **veri kaybı olmadan dönüşüm**
- Biçimlendirme: FAT12/16/32 ve **exFAT saf Python** (her platformda);
  NTFS / ext2-3-4 harici `mkfs` ile (yalnızca aracı olan sistemlerde)
- Dosya erişimi: listeleme, dışa/içe aktarma, klasör, silme, yeniden adlandırma (FAT + exFAT)
- **Yedekleme / geri yükleme** (`.dub`), disk ve bölüm **klonlama**
- **Güvenli silme** (sıfır / rastgele / DoD), boş alan silme
- **Veri kurtarma**: silinmiş dosya, kayıp bölüm tarama, imza tabanlı kurtarma (carving)
- Hex görüntüleyici, 4K hizalama denetimi
- **Çoklu dil arayüzü**: Türkçe (kaynak), İngilizce, Almanca — çalışırken değişir
- DiskGenius tarzı görsel bölüm haritası, ağaç + tablo + dosya gezgini düzeni

DiskGenius özellik karşılaştırması: `.claude/docs/diskgenius-parity.md`

## Teknoloji
- Python 3.8+ (geliştirme: 3.12)
- GUI: **PyQt5**
- **Çalışma zamanı bağımlılığı yok** (çekirdek saf Python; kullanıcı yalnızca
  PyQt5 kurar). `mkfs.*` araçları varsa opsiyonel kullanılır.
  **Geliştirme/çeviri araçları bu kuralın dışındadır** — kullanıcı onlara
  ihtiyaç duymaz. Bu ayrım yazılı değildi ve bir kez yanlış okundu: çeviri için
  `.po` biçimi "msgfmt kurulu değil" diye elendi, oysa msgfmt bir geliştirici
  aracıdır (ADR 0027, rapor bölüm 12).
- **Görüntü dosyalarında root/sudo gerekmez.** Fiziksel disk erişimi yönetici/root
  yetkisi ister; yetki yoksa diskler listelenir ama açılamaz (anlamlı hata verilir).
  Kaynak **her zaman salt okunur açılır**; yazma yetkisi yalnızca bekleyen
  işlemler uygulanırken, tek seferde alınır (ADR 0025).
  Uygulama yetkiyi **açılışta, pencere açılmadan ve koşulsuz** ister
  (Windows UAC / Linux pkexec) — ADR 0042. Gerekçe: araç fiziksel disklerde
  root olmadan iş görmez, her açılışta soru sormak yalnızca bir tık ekler.
  Yetki verilmezse uygulama **yine açılır**; görüntü dosyalarıyla çalışmak
  için yetki gerekmez ve aynı oturumda ikinci kez sorulmaz. Çıkış kapıları:
  `--no-root`, `DISKULTIMATE_AUTO_ROOT=0`,
  `DISKULTIMATE_NO_ELEVATION_PROMPT=1` (otomatik koşumlar).
  (Önceki kural — yalnızca bilgisi okunamayan disk varken sormak, ADR 0023 —
  kullanıcı kararıyla değiştirildi.)
  **Yetkili kopyada üretilen dosyaların sahipliği geri verilir:** pkexec/sudo
  `PKEXEC_UID`/`SUDO_UID` bırakır, `platform.restore_owner()` görüntü, yedek
  ve dışa aktarılan dosyaları çağıran kullanıcıya çevirir — yoksa kullanıcı
  kendi yedeğini silemez.
- **Çoklu dil:** arayüzde görünen her metin `i18n.tr("...")` ile sarılır; modül
  düzeyinde üretilen metinler (`MBR_TYPES`, `WIPE_METHODS`, `operations.KINDS`)
  `mark("...")` ile işaretlenip gösterim anında çevrilir (ADR 0027).
  Sözlükler: `src/diskultimate/i18n/catalogs/<dil>.po` (gettext biçimi;
  çoğul için `trn()`, bağlam için `trc()`). Poedit/Weblate doğrudan açar.
  Denetim: `python3 -m tests.i18n_check` (beklenen: her dil TAMAM).
- **Yazım kuralı:** konsol/günlük çıktısı ve kod ASCII kalabilir; **arayüz
  metni ve çeviriler dilin doğru yazımıyla** yazılır. Almanca çeviriler bir kez
  ASCII'leştirilmiş ("Grosse", "Datentrager") ve 495 çeviri hatalı çıkmıştı.
- **Sözde-yerelleştirme:** `DISKULTIMATE_LANG=qps python3 main.py` metni
  `[!Ɓǿŀŭḿŭ şīŀ···!]` biçimine sokar. Sarılmamış metin dönüşmediği için gözle
  belli olur; `tests.ui_smoke` bunu otomatik denetler. Yeni dil eklemeden önce
  bu kiple bakılır (yerleşim taşması).
- **Çapraz platform kuralı:** işletim sistemi farkları **yalnızca** `core/platform.py`
  içinde durur. `core/` içinde doğrudan `subprocess`, `shutil.which`, `tempfile` veya
  sabit yol kullanılmaz. Tüm `struct` biçimleri açık endian işareti taşır.
  Denetim: `python3 -m tests.platform_check` (beklenen: 0 bulgu).

## Fiziksel Disk Güvenlik Kuralları (ZORUNLU)
Gerçek disklere erişim `core/physical.py` içinde toplanır ve şu katmanlar **asla**
gevşetilmez (gerekçe: `.claude/decisions/0014-fiziksel-disk-destegi.md`):

1. **Listeleme zararsızdır** — `list_disks()` hiçbir sektör okumaz, hiçbir
   yazma yapmaz. (Windows'ta boyut/model yalnızca aygıt tutamacı üzerinden
   sorgulanabildiği için salt okunur bir tutamaç açılıp hemen kapatılır;
   veri okunmaz.) Bu işlev 3 saniyede bir çalışır.
   **Bölüm yoklaması ayrıdır** (`survey_disk`, ADR 0026): bölüm tablosunu ve
   dosya sistemi imzalarını **okur** ama yalnızca salt okunur açar ve hemen
   kapatır; disk listesi değişmedikçe tekrarlanmaz. Aygıta gereksiz dokunmak
   sürücü yığınında sıkışma üretir (ölçüldü: ADR 0021).
2. **Varsayılan salt okunur** — yazma için `readonly=False` *ve* `confirm=True`.
3. **Sistem diski** — yazmak için ayrıca `allow_system=True`; arayüzde kullanıcı
   disk adını yazarak doğrular.
4. **Bilgisi eksik disk** (yetki yok) — yazma **reddedilir**; "bilinmiyor" durumu
   asla "risk yok" gibi sunulmaz.
5. **Bağlı bölüm** varsa yazma öncesi ayrıca uyarılır.
6. Yeni bir yıkıcı işlem eklenirken bu katmanlardan geçtiği **test edilir**.

> Geliştirme sırasında **ana makinenin diskleri üzerinde deneme yapılmaz.**
> Fiziksel disk testleri sanal makine misafirinde, o VM'e eklenen **boş bir sanal
> disk** üzerinde yapılır.

## Test Ortamı Kuralı (ZORUNLU)
**Fiziksel diske, önyükleyiciye veya bellenime yazan testler sanal makinede
çalıştırılır, ana makinede değil.** Linux VM 2026-09-29'dan beri yoktur;
kullanıcı kararıyla Linux tarafı ana makinede **yalnızca disk görüntüsü
dosyaları** üzerinde sınanır.

| | |
|---|---|
| Linux | Ana makine, **yalnızca görüntü dosyası** (`.tmp/tests/`) — bkz. `.claude/memory/linux-test-ortami.md` |
| Windows misafiri (yerel) | VirtualBox `"win10 "` — bkz. `.claude/memory/virtualbox-win10-test.md` |
| Windows misafiri (uzak) | VMware Win10 — bkz. `.claude/memory/windows-test-ortami.md` |

Gerekçe: bu proje **diske yazan** bir araçtır. Fiziksel disk, önyükleyici ve
bellenim (UEFI değişkeni) işlemleri yanlış gittiğinde makineyi açılmaz
bırakabilir; ana makine bu riski taşımamalıdır. Görüntü dosyası bu riski
taşımaz. Çapraz platform iddiası ancak hedef sistemde ölçülerek doğrulanır.

Uygulaması:
- `tests.run_all`, `tests.diag_check` gibi yalnızca görüntü dosyası kullanan
  testler ana makinede koşabilir; Windows sonucu VirtualBox misafirinde alınır.
- `tests.physical_*` ve fiziksel disk/önyükleyici/UEFI denemeleri **ana
  makinede koşmaz**; VirtualBox misafirinin ikinci diskinde (`du-test-disk.vdi`)
  koşar.
- Ana makinede root yoktur: çekirdek bağlaması yapılamaz. Doğrulama kullanıcı
  alanı araçlarıyla (`e2fsck -fn`, `xfs_repair -n`, `btrfs check`,
  `fsck.vfat`) yapılır; "çekirdek bağladı" denmez.
- Bir yetenek VM'de sınanamıyorsa (örneğin misafir BIOS kipinde açıldığı için
  UEFI değişkeni yazılamıyor) bu **açıkça yazılır**, "test edildi" denmez.

## Kayıt / Dokümantasyon Kuralı (ZORUNLU)
Yapılan **tüm işlemler, kararlar, ilerleme ve notlar** proje içindeki `.claude/` klasörüne
yazılır. Bu PC'nin lokaline (`~/.claude/...`, kullanıcı ev dizini, global hafıza) **hiçbir kayıt
yapılmaz.**

| Dosya | İçerik |
|---|---|
| `.claude/docs/project-overview.md` | Projenin amacı, kapsamı, yol haritası |
| `.claude/docs/architecture.md` | Mimari, modül sorumlulukları, veri akışı |
| `.claude/docs/worklog.md` | Tarihli iş günlüğü — her oturumda ne yapıldı |
| `.claude/docs/testing.md` | Test yöntemi, çalıştırma komutları, sonuçlar |
| `.claude/decisions/*.md` | Teknik kararlar (ADR): neden bu yol seçildi |
| `.claude/specs/*.md` | Format/yapı spesifikasyonları (MBR, GPT, FAT vb.) |
| `.claude/logs/*.md` | Uzun çıktı, hata ayıklama dökümleri |
| `.claude/sessions/` | Claude oturum dökümleri: canlı transcript'ler `live/` altında (symlink/junction), arşiv kopyaları + `INDEX.md` — otomatik |
| `.claude/memory/` | Claude kalıcı hafızası (`MEMORY.md` + tekil notlar) |
| `.claude/hooks/` | Kayıt otomasyonu betikleri |

**Her anlamlı değişiklikten sonra `worklog.md` güncellenir.**

**Nasıl zorlanır:**
- `.claude/settings.json` → `SessionEnd` kancası her oturum sonunda dökümü
  `.claude/hooks/archive-session.py` ile `.claude/sessions/` altına kopyalar.
- **Canlı transcript'ler de projede durur:** `~/.claude/projects/<slug>` klasörü
  **tek** bir klasöre — `.claude/sessions/live/` — bağlanır (Linux/macOS symlink,
  Windows junction). İki sorunu birden çözer: `cleanupPeriodDays` varsayılanı
  30 gündür ve dökümler makineye bağlıdır. Ayar `.claude/settings.json` içindeki
  `cleanupPeriodDays` ile birlikte klonla taşınır.
  Kurulum/denetim: `python3 .claude/hooks/setup-sessions.py [--check|--dry-run]
  [--import-legacy]` — **Claude Code kapalıyken** çalıştırılır, yoksa açık oturumun
  transcript'i ikiye bölünür (betik açık oturum görürse durur).
  **Klasör adı slug olamaz** (ADR 0038): slug çalışma dizininin yolundan üretilir,
  yani her makinede başkadır; `live/` ortak olduğu için Windows'ta yazılan geçmiş
  Linux'ta da listelenir. Yeni bir makinede (veya proje başka bir yola taşınınca)
  betik bir kez çalıştırılır; eski yolda kalmış dökümler `--import-legacy` ile
  alınır.
- `.claude/settings.local.json` → `autoMemoryDirectory` hafızayı `.claude/memory/`
  içine yönlendirir. (Bu anahtar güvenlik gereği depoya giren `settings.json`
  içinden okunmaz; bu yüzden makineye özel `settings.local.json` içindedir ve
  yolu mutlaktır — depo başka bir yola klonlanırsa bu dosya yeniden yazılmalıdır.)
- `.gitignore` → `settings.local.json` ve kancanın ürettiği **arşiv kopyaları**
  (`.claude/sessions/*.jsonl`) depoya girmez. **Canlı transcript'ler**
  (`.claude/sessions/live/*.jsonl`) ve `INDEX.md` **girer** — geçmişin
  makineler arasında taşınmasının tek yolu budur. Desen bilerek tek yıldızlıdır;
  `**` yapılırsa kalıcılık ortadan kalkar. Transcript konuşmanın tamamıdır;
  depo 2026-10-03'ten beri **kullanıcı kararıyla public**'tir ve dökümlerin
  (ve `.claude/` notlarının) herkese açık olması bu proje için **sorun
  sayılmaz** — bu konuda uyarı yapılmaz. Yine de dökümlere ve notlara gizli
  bilgi (gerçek parola, anahtar, kişisel veri) yazılmaz. `.gitattributes` → `.claude/sessions/** -text -diff`
  (CRLF çevrimi canlı yazılan JSONL'i bozar).

## Arayuz Donmasi ve Tanilama (ZORUNLU)
Arayuz is parcaciginda **suresi ongorulemeyen is yapilmaz**: isletim sistemi
aygit cagrilari (disk sayimi, birim acma, IOCTL), gercek disk G/C ve tum diski
tarayan isler `dialogs/task.run_task` veya bir `QThread` icinde calisir
(gerekce: `.claude/decisions/0020-tanilama-ve-donma-yakalayici.md`).

Tanilama her calistirmada aciktir:
- `.claude/logs/runtime/session-<zaman>.log` — olculen her islem; 200 ms ustu `YAVAS`
- `.claude/logs/freeze/freeze-<zaman>.md` — arayuz 1.5 sn yanit vermezse **kendiliginden**
  yazilan yigin raporu
- Arayuzden: **Araclar > Tanilama**
- Kapatma/ayar: `DISKULTIMATE_DIAG=0`, `DISKULTIMATE_DIAG_VERBOSE=1`,
  `DISKULTIMATE_DIAG_STALL_MS=<ms>`

Iki kural daha (gerekce: `.claude/decisions/0021-acik-aygiti-yoklamama-ve-kopyalama-ilerlemesi.md`):
- **Ayni aygita iki yerden dokunulmaz.** Uygulamanin acik tuttugu diske ikinci
  bir tutamac acip IOCTL sormak surucu yiginini dakikalarca asili birakabilir.
  Acik aygitlar `core/physical.py` kutugunde durur; `list_disks()` onlara
  dokunmaz.
- **Uzun is sessiz kalmaz.** Kullanicinin baslattigi her uzun islem ilerleme
  penceresi gosterir (`dialogs/task.run_task`). Yuzde hesaplanamiyorsa
  `report(mesaj, -1)` ile belirsiz cubuk kullanilir.

Yeni bir uzun islem eklenirken `diagnostics.span(...)` ile olculur.
Denetim: `python3 -m tests.diag_check` (beklenen: 13/13).

## Kod Kuralları
- Kaynak kod `src/diskultimate/` altında paket olarak durur.
- Katmanlar birbirine sızmaz:
  - `core/` → saf Python, **PyQt import etmez**, GUI'den bağımsız test edilebilir.
  - `ui/` → yalnızca sunum; disk mantığı içermez, `core/`'u çağırır.
- Sektör boyutu her yerde `512` sabiti değil, `DiskImage.sector_size` üzerinden okunur.
- Tüm ofsetler **LBA (sektör)** cinsinden taşınır; bayta çevirme sınırda yapılır.
- **Yıkıcı işlemler kuyruğa girer, tek "Uygula" ile çalışır** (ADR 0025).
  Tıklama anında diske yazılmaz; onay diyaloğu hâlâ zorunludur ama işlem
  başına değil **parti başına**: Uygula penceresi bütün adımları listeler ve
  yıkıcı olanları sayar. Yeni bir yıkıcı işlem eklenirken
  `core/operations.py` içine bir adım türü tanımlanır, doğrudan çalıştırılmaz.
- **Kaynak dil Türkçe, kod adları İngilizce.** Arayüz metni Türkçe yazılır ve
  `tr()` ile sarılır; çeviriler sözlük dosyalarındadır (ADR 0027). Yeni bir
  `QAction` eklenince metni `_retranslate_actions()` içine yazılır — yoksa dil
  değişince eski dilde kalır ve `tests.i18n_check` bunu hata sayar.
- **Görünen metin karar girdisi değildir:** kod, ürettiği metnin içinde arama
  yaparak karar vermez (metin çevrilince arama boşa düşer). Durum bayrakla
  taşınır (`DiskImage.readonly_locked` gibi).
- **Görünüm:** özel stil sayfası kullanılmaz; sistemin Qt teması geçerlidir
  (ADR 0013). Renk gerektiğinde `theme.palette_color(...)` kullanılır; sabit renk
  yalnızca anlamsal olanlarda (dosya sistemi renkleri, ikon renkleri) kabul edilir.
- **İkonlar `ui/icons.py` içinde QPainter ile çizilir** (ADR 0025).
  `QStyle` standart ikonları platforma göre değişir ve çoğu işlemin karşılığı
  yoktur; SVG ise `PyQt5.QtSvg` her dağıtımda kurulu olmadığı için kullanılamaz.
  Yeni ikon `DRAWERS` sözlüğüne eklenir; duman testi hepsini tek tek çizer.
- Yeni özellik eklerken önce `core/` tarafında yaz + `tests/` ile doğrula, sonra GUI'ye bağla.

## Çalıştırma
```bash
python3 main.py              # GUI
python3 -m tests.run_all     # çekirdek testleri
python3 -m tests.diag_check  # tanılama / donma yakalayıcı
python3 -m tests.i18n_check  # ceviri sozlukleri
DISKULTIMATE_LANG=en python3 main.py   # arayuzu baska dilde ac
```
