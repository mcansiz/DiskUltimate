# ADR 0038 — Oturum dokumleri makine slug'ina gore degil tek klasorde durur

**Tarih:** 2026-09-21
**Durum:** Kabul edildi
**Ilgili:** CLAUDE.md "Kayit / Dokumantasyon Kurali", ADR 0027 (cok dillilik
oncesi kayit duzeni degil — burada yalnizca kayit altyapisi)

## Baglam

Oturum dokumleri (`transcript`) projeye alinmisti: `~/.claude/projects/<slug>`
klasoru `.claude/sessions/<slug>/` icine tasinip yerine bir baglanti
(Windows'ta junction) birakiliyordu. `<slug>`, **calisma dizininin yolundan**
uretilir:

| Makine | Yol | Slug |
|---|---|---|
| Windows | `d:\pythonProjeler\DiskUltimate\DiskUltimate` | `d--pythonProjeler-DiskUltimate-DiskUltimate` |
| Linux | `/home/pc/Belgeler/GitHub/DiskUltimate` | `-home-pc-Belgeler-GitHub-DiskUltimate` |

Sonuc: depo Linux'a klonlanınca gecmis **dosya olarak geldi ama gorunmedi.**
Linux'taki Claude Code kendi slug'ina bakiyordu; depodaki 9 oturum baska
adli bir klasordeydi. Ayni sekilde projenin daha eski Linux yolu
(`/home/pc/diskUltimate`) altinda kalan ilk iki oturum da listelenmiyordu.

## Olcum: eklenti gecmisi neye bakiyor?

Karar tahminle degil, eklentinin kendi kodu okunarak verildi
(`~/.vscode/extensions/anthropic.claude-code-*/extension.js`):

- Liste `readdir(<config>/projects/<slug>)` ile kurulur; `.jsonl` uzantili ve
  **adi gecerli bir oturum kimligi** olan dosyalar alinir.
- Ozet, dosyanin bas ve son parcasindan cikarilir; `cwd` alani yalnizca
  **gosterilir**, suzme olcutu degildir. `isSidechain` olanlar elenir.

Iki pratik sonuc:

1. Bir dokum, icindeki `cwd` baska bir makinenin yolunu gosterse bile, dogru
   klasorde durdugu surece listelenir. Yani makineler tek klasoru
   paylasabilir.
2. Kancanin urettigi arsiv kopyalari (`2026-09-19-<kimlik>.jsonl`) listede
   **cikmaz** — ad, kimlik olarak ayrıştırılamaz. Bu iyi: ayni oturum iki kez
   gorunmez.

## Karar

Dokumler slug adli klasorlerde degil, tek bir klasorde durur:

    .claude/sessions/live/

Her makine kendi `<config>/projects/<slug>` yolunu **bu** klasore baglar.
Boylece her makine butun gecmisi gorur ve yeni oturumlar dogrudan depo
icine yazilir.

Kurulum/onarim projeye ait bir betikle yapilir:

    python3 .claude/hooks/setup-sessions.py [--check|--dry-run] [--import-legacy]

Betik `.claude/hooks/setup-sessions-test.py` ile sinanir (kum havuzu; gercek
dokumlere dokunmaz). Betik slug'i kendi hesaplar, mevcut dokumleri `live/`
icine **birlestirerek** tasir (alt klasorler dahil), baglantiyi kurar
(Linux/macOS symlink, Windows
symlink olmazsa junction) ve `--import-legacy` ile projenin **eski
yollarindaki** dokumleri de iceri alir. Eski yol adayi, dokumun ilk `cwd`
degerinin son parcasi proje klasoru adiyla eslesince bulunur.

## Gerekce

- Tek klasor, "gecmis makineler arasinda tasinir" iddiasini gercekten
  karsilar; slug'li duzen yalnizca **ayni yolda** calisan makinede ise yarar.
- Dokumun `cwd` alanini yeniden yazmak gerekmez; kayit oldugu gibi kalir.
- Kopya yoktur: ayni dosya hem depo hem eklenti tarafindan tek yerden okunur.

## Sonuclar

- Windows tarafindaki junction, eski slug adini gosterdigi icin bu degisiklikle
  **bosa duser**. O makinede bir kez `setup-sessions.py` calistirilmali.
- Betik **Claude Code kapaliyken** calistirilir. Acik oturumun dokumu son bir
  dakika icinde yazilmis gorunuyorsa betik durur (`--force` ile gecilir).
  Tasima ayni dosya sisteminde rename oldugu icin acik tutamac kopmaz, ama
  bu zamanlamaya baglidir; garanti sayilmaz.
- `.gitignore` deseni degismedi: `.claude/sessions/*.jsonl` (tek yildiz) hala
  yalnizca **arsiv kopyalarini** disarida birakir; `live/` altindaki canli
  dokumler depoya girer. Depo bu yuzden **private** kalmalidir.
