export const meta = {
  name: 'app-icon-wiring',
  description: 'DiskUltimate uygulama ikonu icin baglanti noktalarini kesfet',
  phases: [{ title: 'Kesif', detail: 'spec, Linux paketleme, calisma zamani yolu, testler' }],
}

const SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string', description: 'Bulgularin 2-4 cumlelik ozeti (Turkce)' },
    edits: {
      type: 'array',
      description: 'Yapilmasi gereken somut duzenlemeler',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string', description: 'Depo koku gore yol' },
          line: { type: 'number', description: 'Ilgili satir numarasi (yoksa 0)' },
          current: { type: 'string', description: 'Su anki hali / neden degismeli' },
          change: { type: 'string', description: 'Tam olarak ne yazilmali' },
        },
        required: ['file', 'line', 'current', 'change'],
      },
    },
    constraints: {
      type: 'array',
      description: 'CLAUDE.md / ADR kaynakli, bu degisikligi kisitlayan kurallar (kaynak dosya + kural)',
      items: { type: 'string' },
    },
    risks: { type: 'array', items: { type: 'string' } },
  },
  required: ['summary', 'edits', 'constraints', 'risks'],
}

const BASE = `Depo: /home/pc/Belgeler/GitHub/DiskUltimate (DiskUltimate, PyQt5, saf Python cekirdek).
Kullanici depo kokune bir favicon.ico birakti (6 boyut: 16/32/48/64/128/256, 32bpp, alfa kanalli)
ve "bunu uygulama ikonu olarak kullan" dedi. Su an projede HIC uygulama ikonu yok.

Gorevin SADECE arastirma: dosyalari oku, hicbir dosyayi DEGISTIRME, hicbir sey yazma.
Somut satir numaralari ve yazilacak tam metinle don. Tahmin etme; dosyayi ac ve bak.`

phase('Kesif')

const [spec, linux, runtime, tests] = await parallel([
  () => agent(`${BASE}

ALANIN: Windows/PyInstaller paketleme.
- DiskUltimate.spec dosyasinin TAMAMINI oku. ROOT nasil tanimlanmis, datas/binaries nasil toplaniyor,
  EXE(...) cagrisinda hangi parametreler var, 139-142. satirlardaki "ikon yok" yorumu ne diyor.
- build_exe.bat dosyasini oku: PyInstaller'i nasil cagiriyor, spec mi kullaniyor yoksa bayrak mi.
- windows-setup/ altinda ikon bekleyen bir kurulum betigi (Inno Setup .iss, .bat) var mi.
- Sorular: ikon dosyasi EXE'ye gomulurken hangi yol yazilmali? Calisma zamaninda da gerekiyorsa
  datas'a nasil eklenir? Spec'teki mevcut datas girdilerinin bicimi tam olarak nedir (ornek ver)?`,
    { label: 'kesif:spec', phase: 'Kesif', schema: SCHEMA }),

  () => agent(`${BASE}

ALANIN: Linux/macOS paketleme ve masaustu entegrasyonu.
- build_linux.sh dosyasinin TAMAMINI oku: ne uretiyor (tek dosya mi, klasor mu, AppImage mi),
  .desktop dosyasi uretiyor mu, kurulum adimi var mi.
- Depoda .desktop dosyasi, hicolor ikon dizini veya macOS .icns ile ilgili bir sey var mi (ara).
- README.md'nin kurulum/calistirma bolumunu oku: kullanici uygulamayi nasil baslatiyor.
- Sorular: Linux'ta ikonun gorunmesi icin tam olarak neye ihtiyac var (.desktop Icon= alani,
  PNG'nin nereye kurulacagi)? macOS .icns icin bu depoda bir yol var mi, yoksa kapsam disi mi?
  .ico dosyasindan PNG uretmek icin harici arac (imagemagick) gerekir mi, yoksa PyQt5 yeter mi?`,
    { label: 'kesif:linux', phase: 'Kesif', schema: SCHEMA }),

  () => agent(`${BASE}

ALANIN: Calisma zamani kaynak dosya yolu + proje kurallari.
- main.py ve src/diskultimate/ui/main_window.py dosyalarini oku: QApplication/pencere ikonu
  nerede ayarlanmali (setWindowIcon), APP_NAME nerede tanimli.
- src/diskultimate/core/platform.py icinde dosya yolu / kaynak dosya cozumleyen bir sey var mi
  (sys._MEIPASS, frozen, resource_path benzeri). Varsa TAM adini ve satirini ver.
- src/diskultimate/ui/icons.py bas kismindaki docstring'i oku: "ikonlar cizilir, dosya degil"
  kurali uygulama ikonunu da kapsiyor mu, yoksa ayri mi.
- CLAUDE.md ve .claude/decisions/ altindaki ADR'leri tara: kaynak dosya yerlesimi, core/ui katman
  ayrimi (core PyQt import etmez, core'da sabit yol/tempfile yok), "calisma zamani bagimliligi yok"
  kurallari bu degisikligi nasil kisitliyor.
- Sorular: .ico dosyasi depoda NEREYE konmali (kok mu, src/diskultimate/resources/ mi, assets/ mi)?
  Hem kaynaktan calistirinca hem PyInstaller paketinde bulunacak yol cozumu hangi modulde durmali
  (core/platform.py mi, ui/ mi) ve neden? Kurallara uyan tam fonksiyon imzasini oner.`,
    { label: 'kesif:calisma-zamani', phase: 'Kesif', schema: SCHEMA }),

  () => agent(`${BASE}

ALANIN: Testler ve denetimler.
- tests/ dizinini listele. tests/ui_smoke.py, tests/platform_check.py, tests/diag_check.py,
  tests/run_all.py dosyalarini oku.
- ui_smoke ikonlarla ilgili ne denetliyor (icons.DRAWERS'i tek tek ciziyor mu, digest() kullaniliyor mu)?
- platform_check hangi desenleri hata sayiyor (core/ icinde subprocess/shutil.which/tempfile/sabit yol,
  struct endian isareti)? Yeni bir kaynak-dosya yolu kodu bu denetime takilir mi?
- .gitignore uygulama ikonunu (kokteki favicon.ico veya assets/*.ico) disarida birakir mi? Kontrol et.
- Sorular: uygulama ikonu icin nereye ve nasil bir test eklenmeli (ikonun yuklenebildigini, bos
  olmadigini, tum boyutlarin bulundugunu dogrulayan)? Mevcut test bicimine uygun ornek kod ver.`,
    { label: 'kesif:testler', phase: 'Kesif', schema: SCHEMA }),
])

return { spec, linux, runtime, tests }
