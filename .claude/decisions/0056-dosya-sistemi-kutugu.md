# 0056 — Dosya sistemi kutugu (`core/fsregistry.py`)

Tarih: 2026-09-29
Durum: **uygulandi** — Linux ve Windows dogrulandi.
Ilgili: analiz `.claude/docs/dosya-sistemi-genisletme.md` bolum 6, ADR 0027.

## Baglam

Bir dosya sistemi eklemek 13 ayri yere dokunmayi gerektiriyordu; biri
unutulunca yarim gorunuyordu (takas `planview`da vardi, bicim listesinde
yoktu). Ayrica arayuz `Partition.fs_type`i **cevirmeden** gosteriyordu:
"Bilinmeyen" ve "Linux Takas" Ingilizce/Almanca arayuzde Turkce kaliyordu.

## Karar

Her dosya sistemi tek bir `FsSpec` kaydidir: `key` (tespit adi, veri),
`display` (gorunen ad, `mark` ile cevrilebilir), `color`, `mbr_type`,
`gpt_type`, `family` (isletim sistemi ipucu). Tureyenler:

| Eski yer | Simdi |
|---|---|
| `ui/theme.FS_COLORS` sozlugu | `fsregistry.colors()` |
| `convert.FS_TO_MBR` / `FS_TO_GPT` | `fsregistry.mbr_types()` / `gpt_types()` |
| `physical.guess_os_from_partitions` sabit kumeleri | `fsregistry.family_keys()` |
| arayuzde `part.fs_type` gosterimi (~30 yer) | `fs_display(part.fs_type)` |

**Veri ile gosterim ayrimi** (ADR 0027 "gorunen metin karar girdisi
degildir"): `fs_type` hic cevrilmez — renk aramasi, bolum turu secimi ve
baglama secenekleri ona gore yapilir. Yol boyunca bu ayrimin iki kez
bozulmak uzere oldugu goruldu ve duzeltildi:
* bir onceki turda plan onizlemesi takas adini `tr()` ile uretiyordu →
  Ingilizce arayuzde renk aramasi bos donerdi;
* otomatik degistirmede baglama islevine giden `fs_type` ve bazi
  `fs_color(...)` cagrilari cevrilmis ada donusmustu — geri alindi.

Gosterim satirlarindaki cevrilmemis Turkce yedek metinler ('ham', 'yok',
'Bicimlendirilmemis') de `tr()` ile sarildi.

`formatter.FS_KINDS` ayri kalir: bicimlendirilebilen **alt kume**dir ve
bicime ozgu sinirlar (en az/en cok boyut) tasir. Her `FsKind.label` kutukte
bir anahtara karsilik gelmek zorundadir (t54 denetler).

## Yeni dosya sistemi eklemek (artik)

1. `fsdetect` — imza (ve gerekiyorsa `encrypted/container` bayragi)
2. `fsregistry.REGISTRY` — tek satir
3. okuyucu/yazici ve `filesystem.open_filesystem` dali (varsa)
4. bicimlendirilebiliyorsa `formatter.FS_KINDS`

t54 (`fsdetect`in urettigi her ad kutukte mi) unutulan kaydi yakalar —
ilk calistirmada bilinmeyen LUKS surumu icin uretilen "LUKS" adinin
eksikligini buldu.

## Dogrulama

t54 yeni; Linux 52/54 (+2 Windows'a ozgu), Windows 52/54 (+2 Linux aracli
karsilastirma), ui_smoke normal ve sozde dil (qps) kipinde, i18n, platform temiz.
