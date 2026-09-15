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
- DiskGenius tarzı görsel bölüm haritası, ağaç + tablo + dosya gezgini düzeni

DiskGenius özellik karşılaştırması: `.claude/docs/diskgenius-parity.md`

## Teknoloji
- Python 3.8+ (geliştirme: 3.12)
- GUI: **PyQt5**
- Harici bağımlılık yok (çekirdek saf Python). `mkfs.*` araçları varsa opsiyonel kullanılır.
- **Görüntü dosyalarında root/sudo gerekmez.** Fiziksel disk erişimi yönetici/root
  yetkisi ister; yetki yoksa diskler listelenir ama açılamaz (anlamlı hata verilir).
  Uygulama yetkiyi **kendisi isteyebilir** (Windows UAC / Linux pkexec) ama
  **koşulsuz değil**: yalnızca bilgisi okunamayan gerçek bir disk varsa, disk
  açılırken yetki reddedilince veya kullanıcı menüden isteyince
  (ADR 0023). En az yetki ilkesi: görüntü dosyasıyla çalışan kullanıcıdan
  yetki istenmez.
- **Çapraz platform kuralı:** işletim sistemi farkları **yalnızca** `core/platform.py`
  içinde durur. `core/` içinde doğrudan `subprocess`, `shutil.which`, `tempfile` veya
  sabit yol kullanılmaz. Tüm `struct` biçimleri açık endian işareti taşır.
  Denetim: `python3 -m tests.platform_check` (beklenen: 0 bulgu).

## Fiziksel Disk Güvenlik Kuralları (ZORUNLU)
Gerçek disklere erişim `core/physical.py` içinde toplanır ve şu katmanlar **asla**
gevşetilmez (gerekçe: `.claude/decisions/0014-fiziksel-disk-destegi.md`):

1. **Listeleme zararsızdır** — hiçbir sektör okunmaz, hiçbir yazma yapılmaz.
   (Windows'ta boyut/model yalnızca aygıt tutamacı üzerinden sorgulanabildiği
   için salt okunur bir tutamaç açılıp hemen kapatılır; veri okunmaz.)
2. **Varsayılan salt okunur** — yazma için `readonly=False` *ve* `confirm=True`.
3. **Sistem diski** — yazmak için ayrıca `allow_system=True`; arayüzde kullanıcı
   disk adını yazarak doğrular.
4. **Bilgisi eksik disk** (yetki yok) — yazma **reddedilir**; "bilinmiyor" durumu
   asla "risk yok" gibi sunulmaz.
5. **Bağlı bölüm** varsa yazma öncesi ayrıca uyarılır.
6. Yeni bir yıkıcı işlem eklenirken bu katmanlardan geçtiği **test edilir**.

> Geliştirme sırasında **ana makinenin diskleri üzerinde deneme yapılmaz.**
> Fiziksel disk testleri VirtualBox misafirinde, o VM'e eklenen **boş bir sanal
> disk** üzerinde yapılır.

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
| `.claude/sessions/` | Claude oturum dökümleri (`.jsonl`) + `INDEX.md` — otomatik |
| `.claude/memory/` | Claude kalıcı hafızası (`MEMORY.md` + tekil notlar) |
| `.claude/hooks/` | Kayıt otomasyonu betikleri |

**Her anlamlı değişiklikten sonra `worklog.md` güncellenir.**

**Nasıl zorlanır:**
- `.claude/settings.json` → `SessionEnd` kancası her oturum sonunda dökümü
  `.claude/hooks/archive-session.py` ile `.claude/sessions/` altına kopyalar.
- `.claude/settings.local.json` → `autoMemoryDirectory` hafızayı `.claude/memory/`
  içine yönlendirir. (Bu anahtar güvenlik gereği depoya giren `settings.json`
  içinden okunmaz; bu yüzden makineye özel `settings.local.json` içindedir ve
  yolu mutlaktır — depo başka bir yola klonlanırsa bu dosya yeniden yazılmalıdır.)
- `.gitignore` → ham `.jsonl` dökümleri ve `settings.local.json` depoya girmez;
  `.claude/sessions/INDEX.md` girer.

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
- Yıkıcı işlemler (format, silme, tablo yazma) öncesi GUI'de onay diyaloğu zorunludur.
- Türkçe arayüz metni, İngilizce kod/değişken adı.
- **Görünüm:** özel stil sayfası kullanılmaz; sistemin Qt teması geçerlidir
  (ADR 0013). Renk gerektiğinde `theme.palette_color(...)` kullanılır; sabit renk
  yalnızca anlamsal olanlarda (dosya sistemi renkleri) kabul edilir.
- Yeni özellik eklerken önce `core/` tarafında yaz + `tests/` ile doğrula, sonra GUI'ye bağla.

## Çalıştırma
```bash
python3 main.py              # GUI
python3 -m tests.run_all     # çekirdek testleri
python3 -m tests.diag_check  # tanılama / donma yakalayıcı
```
