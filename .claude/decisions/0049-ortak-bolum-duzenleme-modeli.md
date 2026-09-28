# 0049 — Bolum duzenleme tek modelden beslenir (asamali birlestirme)

Tarih: 2026-09-28
Durum: kabul edildi — bes asamanin tamami uygulandi (Win10 VM'de sinandi)
Ilgili: ADR 0045 (geri yuklemede yerlesim), 0047 (sinirlar arka planda),
0048 (planlanan yerlesime gore boyutlandirma)

## Sorun

Ana ekran ve yedek geri yukleme ayni isi — bolumleri buyut/kucult/tasi —
**iki ayri kodla** yapiyordu:

| Is | Ana ekran | Geri yukleme |
|---|---|---|
| Yerlesim modeli | `resize.py` + `planview.py` | `restoreplan.RestoreLayout` |
| Kenar surukleme mantigi | `disk_map._continue_drag` (arayuzde) | `RestoreLayout.move_edge` (cekirdekte) |
| Suruklenebilir serit | `DiskMapWidget` tutamaklari | `LayoutBar` |
| FS sinirlari | `_LimitsWorker` + onbellek | `build_layout` |
| Dogrulama | `plan_resize` | `RestoreLayout.validate` |
| Disk listesi | ana agac | `BackupDialog._fill_targets` |

Ayrismanin bedeli olculdu: tutamak donmasi (yalnizca ana ekran, ADR 0047),
buyutulemeyen bolum (yalnizca ana ekran, ADR 0048), cift disk satiri
(yalnizca yedek penceresi, ADR 0047). Her hata iki yerde ayri bulundu.

## Karar

Dort ortak parca + iki baglayici:

1. **`core/layoutedit.py`** — kaynaktan bagimsiz duzenlenebilir yerlesim
   (`EditableLayout`, `Slot`): pencere, kenarlar, surukleme, dogrulama,
   hazir duzenler. Arayuz import etmez; saf Python testlenir.
2. **Baglayicilar** — modeli bir kaynaktan kurar, sonucu bir hedefe yazar:
   *disk + kuyruk* (`resize_op` uretir / gunceller) ve *yedek -> hedef*
   (`restore_with_layout`). Yeni kaynak = yeni baglayici.
3. **Tek serit** (`PartitionEditBar`): oransal ve blok kipi; ana harita ve
   geri yukleme ayni tutamak kodunu kullanir.
4. **Ortak servisler**: FS sinir servisi (arka plan + onbellek) ve disk
   kaynak modeli ("her fiziksel disk bir kez; aciksa oturuma bagli" —
   ADR 0021 garantisi tek yerde).

Yazma yollari **ayri kalir**: geri yukleme yeni diske kopyalar, ana ekran
yerinde tasir. Ortak olan model, arayuz ve dogrulamadir; ikisi zaten ayni
FS boyutlandiricisini (`resize._fs_resize`) kullanir.

## Asamalar (hepsi uygulandi)

1. **Cekirdek model** — `core/layoutedit.py` (`EditableLayout`, `Slot`).
   `restoreplan` yalnizca yedek -> hedef baglayicisi; `RestoreLayout` /
   `LayoutPart` geriye uyumlu adlar. Test: t47.
2. **Disk + kuyruk baglayicisi** — `core/queueedit.py`: `build` modeli
   oturum + kuyruktan (planlanan yerlesim) kurar; `commit` degisen bolumleri
   `resize_op` olarak yazar, ayni bolumun adimini yerinde gunceller,
   diskteki haline donduyse kaldirir, **yer acan adimi once** koyar ve
   kuyrugun uygulanabilirligini `planview.project(...).conflicts` ile
   denetler; bozulursa yalnizca kendi adimlarini sona alir, olmuyorsa
   kuyrugu geri alir. `planview` tasinan bolumun capasini izler (uygulamadaki
   `_follow_move` karsiligi). `planview.planned_window` ve `_queue_resize`in
   kendi dogrulamasi kalkti. Test: t46 (yeniden yazildi).
3. **Ortak surukleme** — `ui/widgets/edgedrag.py` (`EdgeDragController`):
   kenarlar modelden, hareket `move_edge`e, tutamak cizimi tek yerde.
   `PartitionEditBar` (oransal; eski `LayoutBar`) ve `DiskMapWidget` (blok;
   eski `_continue_drag` + sinir hesabi silindi) ayni denetleyiciyi kullanir.
   Ana ekrana **sinir tutamagi** geldi.
4. **Ortak servisler** — `ui/fslimits.py` (`FsLimitsService`: arka plan +
   onbellek; harita, boyutlandirma ve bolum duzeni penceresi ortak) ve
   `core/disksource.py` (`collect`: her fiziksel disk bir kez, acik disk
   oturuma bagli; ana agac ve "Disk sec" listesi ortak).
5. **Ortak pencere** — `ui/dialogs/partition_layout.py`
   (`PartitionLayoutDialog`, `mode="restore" | "disk"`). Geri yuklemede
   "Bolumleri yonet", ana ekranda **Bolum > Bolum duzenini degistir...**.
   `RestoreLayoutDialog` geriye uyumlu alt sinif.

## Uygulama sirasinda bulunanlar

- **Sabit komsunun yanindaki bolum kuculemiyordu.** Model iki bitisik bolumun
  sinirini yalnizca ortak tutamak olarak sunuyordu; komsu sabitse (ext4)
  tutamak oynamiyor, FAT bolumu sagdan hic kucultulemiyordu (eski harita
  yapabiliyordu). Ortak sinir kayamiyorsa iki ayri kenar sunulur. t47 sinar.
- **Kilitli bolum** kavrami: kuyrukta yeni bolum, genisletilmis kapsayici,
  sinirlari henuz hesaplanmamis bolum engeldir ama duzenlenmez.
- Ana ekranda **ham** (tanimlanamayan) bolum artik kucultulmez — eskiden
  `plan_resize` en az 1 sektor kabul ediyordu; icerigi bilinmeyen (BitLocker
  gibi) bolumde bu veri kaybi olurdu. Buyutme serbest.

## Model notlari

- `Slot.movable`: dosya sisteminin baslangici tasinabilir mi. Geri yuklemede
  her bolum kopyalandigi icin hep `True`; yerinde tasimada FS sinirindan
  gelir. Tasinamayan bolumun sol kenari ve sagindaki sinir tutamagi oynamaz.
- Mantiksal MBR bolumleri modelde acikca tasinir (`_reserved_before`: EBR
  icin bir hiza birimi); genisletilmis bolum duzenlenmez, mantiksallardan
  hesaplanir.
