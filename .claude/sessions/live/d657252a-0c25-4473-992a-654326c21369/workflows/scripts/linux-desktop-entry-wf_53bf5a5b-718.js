export const meta = {
  name: 'linux-desktop-entry',
  description: 'DiskUltimate icin .desktop girdisi ve simge kurulumunu kesfet',
  phases: [{ title: 'Kesif', detail: 'freedesktop uyumu, depo entegrasyonu, kurulum betigi' }],
}

const SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string', description: '3-5 cumlelik ozet (Turkce)' },
    facts: {
      type: 'array',
      description: 'Olculmus/dogrulanmis olgular — her biri nasil dogrulandigini soylesin',
      items: { type: 'string' },
    },
    content: {
      type: 'string',
      description: 'Onerilen tam dosya icerigi veya kod blogu (varsa)',
    },
    edits: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          change: { type: 'string' },
        },
        required: ['file', 'line', 'change'],
      },
    },
    risks: { type: 'array', items: { type: 'string' } },
  },
  required: ['summary', 'facts', 'edits', 'risks'],
}

const BASE = `Depo: /home/pc/Belgeler/GitHub/DiskUltimate — PyQt5 disk yonetim araci, GPL-3.0.
Bu oturumda uygulama ikonu baglandi: src/diskultimate/ui/resources/app-icon.ico (+ ayni klasorde
app-icon-16/24/32/48/64/128/256.png ve app-icon.icns). main.py icinde
ui/appicon.apply_app_icon(app) cagriliyor (QApplication.setWindowIcon).

EKSIK OLAN: Linux masaustu girdisi (.desktop). PyInstaller Linux'ta EXE ikonunu uygulamaz, bu
yuzden paketlenmis dist/DiskUltimate ikilisinin dosya yoneticisinde/menude ikonu yok.
Kullanici "ekle" dedi.

Ana makine: Linux Mint, XDG_CURRENT_DESKTOP=KDE, XDG_SESSION_TYPE=wayland.
Kurulu araclar: desktop-file-validate, desktop-file-install, update-desktop-database,
gtk-update-icon-cache, xdg-desktop-menu, xprop. Xvfb YOK.

Gorevin SADECE arastirma: oku, olc, hicbir dosyayi DEGISTIRME ve hicbir sey KURMA
(~/.local/share altina da yazma). Tahmin etme — olcebildigini olc ve nasil olctugunu yaz.`

phase('Kesif')

const [spec, repo, installer] = await parallel([
  () => agent(`${BASE}

ALANIN: freedesktop uyumu ve ikonun GERCEKTEN gorunmesi.
- Bir .desktop dosyasinda hangi anahtarlar zorunlu, hangileri bu uygulama icin anlamli?
  (Type, Name, Comment, Exec, Icon, Terminal, Categories, Keywords, StartupNotify,
  StartupWMClass, MimeType, X-GNOME-*/X-KDE-*)
- KRITIK SORU: X11'de ve Wayland'de pencerenin ikonu .desktop girdisiyle NASIL eslesir?
  StartupWMClass ne zaman gerekir? Wayland app_id neye gore belirlenir?
- PyQt5'te QGuiApplication.setDesktopFileName VAR MI? Python'da dogrudan olc:
  python3 -c "from PyQt5.QtGui import QGuiApplication; print(hasattr(QGuiApplication,'setDesktopFileName'))"
  ve Qt surumunu yazdir. Varsa cagri sekli ve dosya adiyla iliskisi nedir (uzanti dahil mi)?
- Qt5 WM_CLASS'i neye gore belirliyor? Kaynaktan calistirma (python3 main.py) ile paketlenmis
  ikili (dist/DiskUltimate) arasinda fark olur mu? Olculebiliyorsa olc (xprop kurulu, oturum
  Wayland — X11 penceresi acmadan olcemiyorsan bunu acikca yaz, uydurma).
- Simge temasina kurulum: PNG'ler tam olarak nereye gider (kullanici duzeyi ve sistem duzeyi),
  dosya adi ne olmali, Icon= alanina uzantili mi uzantisiz mi yazilir, hangi cache komutlari
  gerekir ve bunlar yoksa ne olur?
- Uygulama disk goruntusu dosyalari aciyor (main.py: sys.argv[1] varsa open_path). MimeType
  eklemek mantikli mi? .img/.vhd/.vhdx/.vdi/.vmdk/.qcow2/.dub icin shared-mime-info'da
  KAYITLI tipler neler? Sistemde olc: 'grep -rl' ile /usr/share/mime/ altina bak.
  Kayitli olmayan tipler icin ayri bir mime XML gerekir mi — yoksa bu kapsam disi mi?
- Onerdigin tam .desktop icerigini "content" alanina yaz ve desktop-file-validate ile
  GECICI bir dosyada dogrula (/tmp altinda, depoya yazma), ciktisini facts'e koy.`,
    { label: 'kesif:freedesktop', phase: 'Kesif', schema: SCHEMA }),

  () => agent(`${BASE}

ALANIN: depo entegrasyonu — testler ve belgeler.
- tests/run_all.py: test yazma bicimini cikar. @test dekoratoru nasil calisiyor (satir 1-120),
  testler nasil isimlendiriliyor, assert uslubu nedir, gecici dosya icin ne kullaniliyor,
  Windows'a ozgu testler nasil atlaniyor (skip mekanizmasi var mi)? En son test t43
  (satir 3025-3082) — ornek olarak TAMAMINI oku ve bicimi tarif et.
- Bir '.desktop sablonu gecerli mi' testi run_all'a mi yoksa tests/ui_smoke.py icine mi
  yakisir? Gerekce ver (run_all PyQt gerektirmiyor, ui_smoke gerektiriyor — sablon metin
  dosyasi, hangisi dogru yer?).
- README.md icinde test sayisi ("cekirdek: 43 test" gibi) ve Linux kurulum/calistirma
  bolumleri nerede geciyor? Tam satir numaralarini ver.
- .claude/docs/testing.md ve .claude/docs/architecture.md icinde guncellenmesi gereken yerler
  neresi? (Yeni klasor: assets/linux/, yeni test.)
- build_linux.sh (depo kokunde) sonunda kullaniciya ne yaziyor? Basarili paketlemeden sonra
  "masaustune kur" ipucu eklenecek olsa tam olarak hangi satira ve hangi uslupla?
- .gitignore ve .gitattributes: assets/linux/ altindaki .desktop ve .sh dosyalari icin bir
  sorun var mi (dosya izinleri, satir sonu)?`,
    { label: 'kesif:depo', phase: 'Kesif', schema: SCHEMA }),

  () => agent(`${BASE}

ALANIN: kurulum betigi tasarimi.
- build_linux.sh dosyasinin TAMAMINI oku ve uslubunu cikar: Turkce mesajlar, renk degiskenleri,
  hata()/uyari() islevleri, 'set -u', dizin degistirme kalibi, argüman isleme. Yeni bir betik
  ayni uslupla yazilacak — bu kalibi ornek kod olarak ozetle.
- Kurulum nereye yapilmali? Kullanici duzeyi (~/.local/share/applications,
  ~/.local/share/icons/hicolor/<n>x<n>/apps) ile sistem duzeyi (/usr/share/...) arasindaki
  farki, hangisinin ROOT gerektirdigini ve varsayilanin hangisi olmasi gerektigini yaz.
  XDG_DATA_HOME degiskeni ayarlanmissa ne olmali?
- Exec= alanina ne yazilmali? Iki durum var: (a) paketlenmis dist/DiskUltimate ikilisi,
  (b) kaynaktan calistirma (python3 /yol/main.py). Betik hangisini secmeli, kullaniciya
  secenek mi sunmali? Yol bosluk iceriyorsa Exec alaninda ne yapilir (freedesktop kacis
  kurallari)?
- Kaldirma (uninstall) nasil olmali, hangi dosyalar silinmeli?
- Betik iki kez calistirilirsa ne olmali (idempotent mi)?
- CLAUDE.md'deki kurallardan hangileri bu betigi baglar? Ozellikle:
  "Fiziksel Disk Guvenlik Kurallari", "Test Ortami Kurali" (ana makineye YAZAN deneme
  yapilmaz — betigin kendisi ana makinede DENENEMEZ, bunu nasil ele almali?),
  ve "Kayit/Dokumantasyon Kurali".
- Onerdigin betigin tam icerigini "content" alanina yaz.`,
    { label: 'kesif:kurulum', phase: 'Kesif', schema: SCHEMA }),
])

return { spec, repo, installer }
