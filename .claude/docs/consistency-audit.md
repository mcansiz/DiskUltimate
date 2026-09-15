# Tutarlilik Denetimi ve Iyilestirme Plani

Tarih: 2026-09-15 · Kapsam: tum kaynak agaci (`src/`, `tests/`, `main.py`) ve
tum `.md` belgeleri (35 belge, ~2.9k satir · ~16.2k satir Python).

## Olcum tabani (dogrulandi, tahmin degil)

Bu denetimdeki "gercek durum" asagidaki kosumlarla saptanmistir:

| Kosum | Sonuc |
|---|---|
| `python -m tests.run_all` | **18/18 basarili** (t05 harici `mkfs` olmadigi icin atlandi) |
| `python -m tests.platform_check` | **0 bulgu** |
| `formatter.FS_KINDS` | 8 dosya sistemi, hepsi `available=True` |
| `filesystem.open_filesystem` | okuma yalnizca FAT + exFAT; digerleri `UnsupportedAccess` |
| `hex_view` | `setReadOnly(True)` — yazma yok |

Yani **README.md tek guncel belgedir**; diger belgeler v0.2.0 doneminde donmustur.

---

# A. Kod ile belge arasindaki tutarsizliklar

| # | Yer | Belgede yazan | Gercek |
|---|---|---|---|
| A1 | `ui/main_window.py:42` | — | `APP_VERSION = "0.1.0"`; README rozeti 0.3.0. "Hakkinda" penceresi yanlis surum gosterir |
| A2 | `ui/main_window.py:1994` "Hakkinda" | "exFAT / NTFS / ext2-3-4 (mkfs araclariyla bicimlendirme)" · "Yonetici yetkisi gerektirmez; yalnizca secilen goruntu dosyasi uzerinde calisir" | Sekizi de saf Python; fiziksel disk destegi var (ADR 0014). Iki cumle de yanlis |
| A3 | `docs/project-overview.md` | "Durum: v0.2.0 — 15/15"; v0.3 listesi isaretsiz | 0.3.0, 18/18; bolum boyutlandirma tamamlandi |
| A3b | `docs/project-overview.md` "Temel ilkeler" 1-2 | "Hicbir kod `/dev/sd*` benzeri blok aygita yazmaz", "root gerekmez" | CLAUDE.md ve ADR 0014 ile **dogrudan celisir** |
| A4 | `docs/cross-platform.md` | "NTFS, ext2/3/4 bicimlendirme → yalnizca Linux"; "o platformlarda listede gorunmez (`formatter.available_kinds`)" | `available=True` sabit; README "gizlemez, nedenini yazar" der. Uc ayri yerde 15/15 |
| A5 | `docs/testing.md` | `cd /home/pc/diskUltimate`; tablo t01–t15; "on dort PNG"; "Windows ve macOS uzerinde testler calistirilmadi" | Yol bu makinede yok; t16/t17/t18 eksik; `ui_smoke` **17** PNG uretir; Windows dogrulandi (cross-platform.md ile celisir) |
| A6 | `docs/diskgenius-parity.md` | "Guncelleme: 2026-09-13 (v0.2.0)"; ozet: "Kapsam disi maddelerin tamami fiziksel diske erisim gerektirdigi icin, proje ilkesi (yalnizca goruntu dosyasi, root yok) geregi disarida birakilmistir" | Icerik v0.3'u anlatiyor; fiziksel erisim **artik var**, dolayisiyla S.M.A.R.T. / bozuk sektor gerekcesi gecersiz |
| A7 | `docs/feature-analysis.md` §4.5 | Ayni eskimis gerekce | Ayni |
| A8 | `docs/architecture.md` | Modul agaci | **7 modul eksik**: `core/ext.py`, `core/ntfs.py`, `core/resize.py`, `core/physical.py`, `core/_ntfs_data.py`, `ui/widgets/resize_bar.py`, `ui/dialogs/resize.py`. `theme.py` hala "stil" iceriyor diye anlatiliyor (ADR 0013 kaldirdi). "Genisletme noktalari" tablosunda boyutlandirma / fiziksel disk yok |
| A9 | `decisions/` | ADR 0002 "Kabul edildi" | 0007 + 0017 tarafindan **gecersiz kilindi**, "Superseded" isareti yok. ADR 0017 "NTFS kismi" der ama NTFS tamam (t17 gecer, ADR 0018). ADR 0019 baslik bicimi digerlerinden farkli |

---

# B. GUI isimlendirme tutarsizliklari

## B1. Ayni eylem, farkli etiket

| Eylem | Menu / arac cubugu | Sag tik menusu |
|---|---|---|
| Bolum olusturma | "Yeni bolum..." | "Yeni bolum olustur..." |
| Bolum guvenli silme | "Bolumu guvenli sil..." | "Guvenli sil..." |
| Onyukleme bayragi | "Onyukleme bayragini degistir" | "Onyukleme bayragini kaldir" / "Onyuklenebilir yap" |
| Salt okunur bilgisi | — | "(salt okunur — degisiklik yapilamaz)" / "(salt okunur — neden?)" — ayni islevi cagirir |
| Goruntu kapatma | "Goruntuyu kapat" | "Bu goruntuyu kapat" |

Kok neden: `_partition_menu()` ve `_free_menu()` etiketleri **yeniden yazar**,
mevcut `QAction` nesnesini kullanmaz. Her yeni eylemde ikinci bir etiket dogar.

## B2. Sag tik menusu merkezi yetki denetimini atliyor

`_update_actions()` `act_boot`, `act_type_part`, `act_rename_part` eylemlerini
`yazilabilir` kosuluna baglar. `_partition_menu()` ayni ucunu **baglamaz**:

```
eylem.setEnabled(yazilabilir)   # bicimlendir, boyutlandir, etiket, geri yukle, sil, wipe
menu.addAction("Bolum turunu degistir...", self.change_type)        # kosulsuz
menu.addAction("Bolum adini degistir...", self.rename_partition)    # kosulsuz
menu.addAction("Onyukleme bayragini ...", self.toggle_bootable)     # kosulsuz
```

> **Duzeltme (uygulama sirasinda saptandi).** Bu bolumun ilk surumu "bu uc islev
> `_require_session()` cagirmaz" diyordu; **yanlisti**. Dordu de
> (`toggle_bootable`, `rename_partition`, `change_type`, `change_label`)
> `_current_partition()` ile basliyor, o da `_require_session()` cagiriyor.
> Yani salt okunur kaynakta kullanici zaten dostane diyalogu goruyordu.
>
> Gercek kusur yalnizca **gorseldi**: girisler sag tik menusunde etkin
> gorunuyor, tiklaninca is yapmiyordu. Veri riski hicbir asamada yoktu —
> `core/session.py::_require_writable()` zaten reddediyor.

## B3. Tanimlayici dili — CLAUDE.md kuralinin ihlali

Kural: *"Turkce arayuz metni, Ingilizce kod/degisken adi."*
AST taramasi: **13 dosyada 74 fonksiyon** Turkce ad veya parametre tasiyor.

| Dosya | Fonksiyon | Ornek |
|---|---|---|
| `ui/main_window.py` | 23 | `_yol_anahtari`, `mesaj`, `baslik`, `oturum`, `bolumler`, `hedef`, `konum` |
| `core/ntfs.py` | 19 | `_std_info(dosya_ozniteligi)`, `_file_name(ust_kayit, ad_turu)`, `_mref(kayit_no)` |
| `core/resize.py` | 9 | `_exfat_extend_bitmap(eski_kume)`, `_exfat_free_run(icerik, kume_sayisi)` |
| `core/ext.py` | 6 | `_pack_superblock(bos_blok, bos_inode)` |
| `core/physical.py` | 5 | `_oku`, `_ac`, `_win_ioctl(tutamac, kod, giris)` |
| `ui/dialogs/*`, `ui/theme.py`, digerleri | 12 | `_doldur_fs_listesi`, `_sektor`, `_ozet_yaz`, `ikon_temasini_ayarla`, `_ciz_windows` |

Ozellikle dikkat cekenler:

- **Tek kavram, iki dil:** `_salt_okunur_acilis_uyarisi()` ile `_read_only_uyarisi()`
  yan yana duruyor. Ustelik `readonly` (core) ile `read_only` (ui) yazimi da ayrilmis.
- **`ui/widgets/resize_bar.py` bastan asagi Turkce** (`_serit`, `_hizala`,
  `_tutamak_ciz`, `_tutamak_bul`, `_surukle`, `_uygula`) — oteki dort widget
  tamamen Ingilizce (`_layout_blocks`, `_draw_block`, `_block_at`, `_populate_tree`).
  En yeni dosyalar en cok sapan dosyalar: kural zamanla gevsemis.
- Kural `core/` icin daha kritik: `ntfs.py` + `resize.py` + `physical.py` = 33 fonksiyon.

## B4. Ayni ad, farkli sozlesme

`_require_writable()` iki yerde, **farkli donus semantigiyle**:

| Yer | Davranis |
|---|---|
| `core/session.py:692` | `SessionError` **firlatir** |
| `ui/widgets/file_browser.py:349` | `bool` **dondurur** |

## B5. Widget API asimetrisi

- Ayarlayicilar: `set_disk` · `set_filesystem` · `set_device` · `set_data` · `set_values`
  — ilk ucu alan adiyla, son ikisi genel adla.
- `clear()` yalnizca `disk_map`'te var; oteki widget'larda yasam dongusu karsiligi yok.
- `freeActivated` yalnizca `disk_map`'te; `partition_table`'da yok.
- Sinyal adlari `partitionSelected`, `freeSelected`, `contentChanged` seklinde alan
  adliyken `resize_bar` yalnizca `changed` diyor.

## B6. Kopya kod

`_icon(self, standard)` govdesi `main_window.py:67` ve `file_browser.py:34`
icinde **birebir ayni**.

## B7. Sihirli sabitler

Sekme indisleri dort yerde ciplak sayi: `setCurrentIndex(0)` (Dosya Gezgini, 3 yer),
`setCurrentIndex(1)` (Bolum Bilgisi). Sekme sirasi degisirse sessizce yanlis sekme acilir.

---

# C. Depo hijyeni ve diger bulgular

| # | Bulgu |
|---|---|
| C1 | `.claude/logs/app-2026-09-1*.log` **git ile izleniyor** ve `.gitignore` disinda. Uygulamayi her calistirmak calisma agacini kirletir (`main_window.log()` her satiri diske yazar). CLAUDE.md bu dizini `*.md` hata ayiklama dokumu icin tanimlar; calisma zamani gunlugu ayni yere karisiyor |
| C2 | `.claude/sessions/INDEX.md`: kanca her `SessionEnd` olayinda **yeni satir ekliyor**, mevcut satiri guncellemiyor — tek oturum icin zaten iki satir var. Ayrica "Ham dokumler `.gitignore` disidir" cumlesi gercegin tersini soyluyor (dosyalar gitignore **icinde**); INDEX baglantilari depoda olmayan dosyalara gidiyor |
| C3 | ~~`ui/theme.py` icindeki `STYLESHEET` belgesiz olarak duruyor~~ — **iddia yanlisti.** ADR 0013 bu kodu "ileriye donuk" basligi altinda **bilerek** birakmis ve `DISKULTIMATE_THEME=diskultimate` kacisini acikca belgelemis. Gercek eksik tek seydi: **hicbir test bu dali kapsamiyordu**, yani ADR 0012'deki sekme kirpilmasi tuzagi fark edilmeden geri gelebilirdi. Kod silinmedi; `ui_smoke` icine tema dalini acip sekme genisliklerini olcen bir adim eklendi |
| C4 | `tests/ui_smoke.py`: `15-coklu-goruntu.png`, `14-sistem-bilgisi.png` dosyasindan **once** kaydediliyor — numara sirasi bozuk |
| C5 | `t05_harici_bicimlendirme` harici `mkfs` yoksa sessizce atlanip "TAMAM" sayiliyor; ozet yine "18/18" diyor. Atlanan test sayisi ozette gorunmuyor |

---

# Iyilestirme plani

Sira: once yanlis bilgi veren seyler, sonra kural ihlalleri, sonra kozmetik.
Her asama bagimsiz dogrulanabilir ve kendi basina tamamlanmis birakilabilir.

> **Ilerleme:** alti asamanin **tamami tamamlandi** (2026-09-15).
> Dogrulama: `run_all` 17/18 · 1 atlandi (harici `mkfs` yok), `platform_check` 0 bulgu, `ui_smoke` 18 ekran goruntusu.

## Asama 1 — Yanlis bilgi veren kod — TAMAMLANDI

| Is | Dosya | Dogrulama |
|---|---|---|
| 1.1 | `APP_VERSION` → `"0.3.0"` | `ui/main_window.py:42` |
| 1.2 | "Hakkinda" metnini duzelt: sekiz FS saf Python, fiziksel disk destegi, yonetici yetkisi notu | `ui/main_window.py:1994` |
| 1.3 | Surum bilgisini tek kaynaktan besle (README rozeti ile `APP_VERSION` ayrismasin) | README + kod |

## Asama 2 — GUI etiket ve yetki birligi — TAMAMLANDI

| Is | Ayrinti |
|---|---|
| 2.1 | Sag tik menuleri **mevcut `QAction` nesnelerini kullansin** (`menu.addAction(self.act_format)` vb.). Etiket ve `enabled` durumu tek yerden gelir; B1 ve B2 birlikte kapanir |
| 2.2 | Dinamik kalmasi gereken tek etiket onyukleme bayragi: `act_boot` metnini `_update_actions()` icinde simetrik olarak guncelle ("Onyuklenebilir yap" / "Onyukleme bayragini kaldir") |
| 2.3 | Salt okunur bilgi girisi icin **tek** metin belirle |
| 2.4 | ~~`_require_session()` ekle~~ — **gereksiz cikti**: dordu de `_current_partition()` uzerinden zaten koruniyor (yukaridaki duzeltme notu) |
| 2.5 | Sekme indisleri icin adlandirilmis sabit (`TAB_FILES = 0` …), dort cagri yerini degistir |

**Dogrulama:** `ui_smoke`; salt okunur bir goruntude her sag tik girisinin pasif
oldugu gozle denetlenir.

## Asama 3 — Isimlendirme kuralini geri getir — TAMAMLANDI

Kural netlestirilir ve **denetime baglanir** — aksi halde tekrar gevser.

| Is | Ayrinti |
|---|---|
| 3.1 | `tests/platform_check.py` icine **tanimlayici dili denetimi** ekle (AST ile fonksiyon adi + parametre). Once yalnizca *yeni* ihlalleri engelleyen bir taban listesiyle basla |
| 3.2 | `ui/` katmanini cevir (~35 fonksiyon): `_yol_anahtari`→`_path_key`, `_salt_okunur_acilis_uyarisi`→`_readonly_open_warning`, `_read_only_uyarisi`→`_readonly_warning`, `mesaj`→`message`, `oturum`→`session`, `hedef`/`konum`→`target`/`pos` |
| 3.3 | `resize_bar.py` tumuyle: `_serit`→`_track`, `_hizala`→`_align`, `_tutamak_ciz`→`_draw_handle`, `_tutamak_bul`→`_handle_at`, `_surukle`→`_drag`, `_uygula`→`_apply` |
| 3.4 | `theme.py`: `ikon_temasini_ayarla`→`apply_icon_theme`, `_ciz_*`→`_draw_*` |
| 3.5 | `core/` katmanini cevir (~39 fonksiyon: `ntfs.py`, `resize.py`, `physical.py`, `ext.py`) — katman kurali geregi en kritik olan burasi |
| 3.6 | `readonly` / `read_only` yazimini **tek** bicime indir (`readonly`, core ile ayni) |
| 3.7 | `file_browser._require_writable()` → `_check_writable()` (B4) |

**Dogrulama:** her adimdan sonra `run_all` 18/18 + `platform_check` 0 bulgu +
`ui_smoke`. Salt ad degisikligi oldugu icin davranis degismemelidir.

## Asama 4 — Kucuk kod temizligi — TAMAMLANDI

| Is | Ayrinti |
|---|---|
| 4.1 | `_icon()` kopyasini ortak yardimciya tasi (`ui/theme.py` veya `ui/widgets/_util.py`) |
| 4.2 | Widget API hizalamasi: `set_data`→`set_partitions`, `set_values`→`set_range`, `resize_bar.changed`→`rangeChanged`, eksik `clear()` metotlari |
| 4.3 | `theme.py` icindeki `STYLESHEET` + `diskultimate` temasi icin **karar ver**: ya ADR 0013 geregi kaldir, ya da ADR'yi guncelleyip `ui_smoke` icine bu dali kapsayan bir kosum ekle. Simdiki ara durum (kodda duruyor, belgede yok, test edilmiyor) en kotusu |
| 4.4 | `ui_smoke` ekran goruntusu numaralarini sirala |
| 4.5 | `run_all` ozetine atlanan test sayisini yaz ("18/18 · 1 atlandi") |

## Asama 5 — Belgeleri gercege esitle — TAMAMLANDI

| Is | Dosya |
|---|---|
| 5.1 | Durum / surum / test sayisi: 0.3.0 · 18/18 · 17 ekran goruntusu → `project-overview.md`, `cross-platform.md`, `testing.md`, `diskgenius-parity.md` |
| 5.2 | "Yalnizca goruntu dosyasi / root gerekmez" ilkesini fiziksel disk gercegiyle yeniden yaz; S.M.A.R.T. / bozuk sektor gerekcesini duzelt → `project-overview.md`, `diskgenius-parity.md`, `feature-analysis.md` |
| 5.3 | NTFS/ext'in uc platformda saf Python oldugunu yaz → `cross-platform.md` |
| 5.4 | Modul agacina 7 eksik modulu ekle; "Genisletme noktalari" tablosuna boyutlandirma + fiziksel disk satiri → `architecture.md` |
| 5.5 | `cd /home/pc/diskUltimate` satirini kaldir; t16/t17/t18 satirlarini ekle; Windows test durumunu tek dogruya indir → `testing.md` |
| 5.6 | v0.3 tamamlananlari isaretle, v0.4 kapsamini yaz → `project-overview.md` |
| 5.7 | ADR 0002'ye "Superseded by 0007, 0017"; ADR 0017 durumunu "tamamlandi"; ADR 0019 baslik bicimini esitle → `decisions/` |

## Asama 6 — Depo hijyeni — TAMAMLANDI

| Is | Ayrinti |
|---|---|
| 6.1 | `.gitignore` icine `.claude/logs/*.log`; izlenen iki gunluk dosyasini `git rm --cached` ile cikar |
| 6.2 | Konum **degistirilmedi**: `.claude/logs/` `diskgenius-parity.md` icinde bilerek tasarlanmis bir ozellik ("Islem gunlugu: arayuzde sekme + dosya"). Bunun yerine `.gitignore` kurali iki isi ayirir: `*.md` depoda kalir, `*.log` kalmaz |
| 6.3 | `archive-session.py`: ayni oturum id'si icin satir **guncellensin**, yenisi eklenmesin |
| 6.4 | `INDEX.md` icindeki ters anlamli `.gitignore` cumlesini duzelt |

---

## Onerilen sira ve maliyet

| Asama | Buyukluk | Risk | Not |
|---|---|---|---|
| 1 — yanlis bilgi | ~20 satir | yok | Hemen yapilabilir |
| 2 — GUI birligi | ~80 satir | dusuk | Gercek kullanici etkisi en yuksek asama |
| 6 — hijyen | ~15 satir | yok | 2 ile birlikte yapilabilir |
| 5 — belgeler | ~10 dosya | yok | Kod degismez |
| 3 — isimlendirme | ~74 fonksiyon | orta | Mekanik ama genis; 3.1 once gelmeli |
| 4 — temizlik | ~60 satir | dusuk | 3'ten sonra |

**Oneri:** 1 + 2 + 6 tek oturumda, 5 ikinci oturumda (kod dokunulmadigi icin
guvenli), 3 + 4 ucuncu oturumda adim adim — her adimda `run_all` 18/18 sarti.

Asama 3'te en onemli tek is **3.1**'dir: denetim otomatiklesmeden yapilan cevirinin
zamanla yeniden bozulacagi bu denetimle zaten olculmustur (en yeni dosyalar en cok
sapan dosyalar).
