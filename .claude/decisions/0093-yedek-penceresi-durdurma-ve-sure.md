# 0093 — Yedek penceresi: işlem sırasında kilit, geçen/kalan süre, Durdur

Tarih: 2026-10-06
Durum: **uygulandı**. İlgili: [0092](0092-yalnizca-kullanilan-alan-yedegi.md),
[0020](0020-tanilama-ve-donma-yakalayici.md).

## Kullanıcı isteği

"Yedek alma işlemi yapılırken GUI'de disk seç gibi alanlar aktif, bunlar
kapalı olmalı; geçen ve kalan süre gösterilmeli; Yedeği al butonu işlemi
durdur olarak yapılandırılabilir."

## Karar

**Kilit.** İş sürerken kip seçimi, dosya yolu ve "Seç...", not, kaynak/hedef
grubu ("Disk seç..." dahil) ve seçenekler grubu devre dışı. Çalışan iş
başlangıçta okunan değerlerle sürer; arada değişen seçim yalnızca ekranı
yanlış gösterirdi. İş bitince/durunca açılır (hedef alanı kipe göre:
`_sync_target_state`).

**Süre.** İlerleme çubuğunun altında `Geçen: 01:23 · Kalan: ~04:10`;
saniyede bir `QTimer` ile tazelenir (ilerleme gelmese de saat işler).
Kalan süre, son belirsiz aşamadan (kullanılan alan hesabı gibi, `-1`)
sonra ilk yüzdenin geldiği andan ölçülen hızla hesaplanır; hazırlık süresi
hızı bozmaz. En az 1 puan ve 1 sn ilerleme yoksa "hesaplanıyor...". İş
bitince `Süre: 01:23`.

**Durdur.** İş sürerken başlat düğmesi "Durdur" (kırmızı kare ikonu)
olur; basılınca "Durduruluyor..." ve devre dışı. Mekanizma:

- Pencerenin iş parçacığı çekirdeğe verdiği ilerleme geri çağrısında,
  durdurma istenmişse `clone.OperationCancelled` fırlatır. Çekirdekteki
  uzun işler ilerlemeyi düzenli bildirir (yedekte her 8 MB), yani iş hangi
  döngüdeyse orada durur; `with`/`finally` aygıtı, dosyayı ve sıkıştırma
  havuzunu kapatır. Çekirdeğe "iptal bayrağı" parametresi eklemek gerekmez.
- `OperationCancelled` **`BaseException`**'dır (KeyboardInterrupt gibi).
  Çekirdekte "her hatayı yut, tamamını yedekle" türünden çok sayıda
  `except Exception` var (usedmap, fsdetect...); iptal onlara takılıp
  sessizce yutulmamalı. t94 bunu denetler.
- Yarım yedek (`.dub`) ve yarım **yeni** görüntü dosyası silinir
  (`clone._partial_file`, `restore_to_new_image`). Hata durumunda da aynı:
  yarım dosya geçerli bir yedek gibi görünmemeli.
- Var olan diske/bölüme geri yükleme durdurulursa hedef tutarsız kalır:
  önce onay sorulur, sonuç satırı "yeniden geri yükleyin ya da
  biçimlendirin" der.

İkon: `icons.py` `stop` + gömülü paketlerde karşılığı (tabler
`player-stop`, phosphor `stop`, lucide `circle-stop`, bootstrap
`stop-circle`, material `stop_circle`; `tools/iconpacks.py`).

## Test

- t94: disk yedeği (tam ve yalnızca kullanılan alan) ve yeni görüntüye
  geri yükleme ilerleme geri çağrısından durdurulur; yarım dosya kalmaz,
  oturum ardından yeniden yedek alabilir.
- ui_smoke: gerçek yedek başlarken alanlar kilitli ve düğme "Durdur";
  bitince açılır ve "Süre:" yazar. Yavaş bir işte "Geçen: 00:0x · Kalan:
  ~..." görünür, Durdur → "Durduruluyor..." → "durduruldu", pencere
  toparlanır.
