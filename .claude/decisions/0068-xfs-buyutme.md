# 0068 — XFS buyutme (saf Python, cevrimdisi)

Tarih: 2026-09-29
Durum: **uygulandi** — ana makinede goruntu dosyalariyla, xfs_repair -n ile
dogrulandi. Cekirdek baglamasi ve Windows kosusu **sinanmadi** (Windows
misafiri ana makine diski dolunca kapandi; bkz. worklog).
Ilgili: [0066](0066-xfs-okuma.md), [0067](0067-xfs-bicimlendirme.md)

## Neden kendi buyutucumuz

`xfs_growfs` yalnizca **bagli** birimde calisir (cekirdek islemidir).
Ana makinede root yok, Windows/macOS'ta XFS baglanamaz. Cevrimdisi buyutme
her platformda calisir.

## Karar

`core/xfsgrow.py::xfs_grow` — cekirdegin `xfs_growfs_data` adimlari:

1. **Son AG uzatilir** (AG boyuna kadar): AGF/AGI boyu, bnobt/cntbt'ye
   sondaki bos kapsam eklenir ya da uzatilir; freeblks/longest.
   Bos alan agaci cok duzeyliyse acik hata (tek duzey 505 kapsam tasir).
2. **Yeni AG'ler:** ustblok kopyasi, AGF/AGI/AGFL, bos B-agaci kokleri.
   Yerlesim ozellige gore (mkfs.xfs 6.18 ile olculdu): basliklar, bnobt,
   cntbt, inobt, [finobt], [rmapbt], [refcountbt]; AGFL rmapbt varsa 6,
   yoksa 4 blok; rmapbt kayitlari AG meta verisinin sahipleri (FS -3,
   AG -5, INOBT -6, REFC -8), bitisik ayni sahipler birlesik. mkfs
   varsayilani (rmapbt + reflink + seyrek inode + nrext64) desteklenir.
3. Son AG 64 bloktan kucuk kalacaksa atilir (cekirdek kurali).
4. Yazma sirasi: yeni AG'ler, son AG, ikinciller (birincilin kopyasi —
   cekirdek de oyle yapar), **birincil en son**.

Guvenlik: **gunluk temiz olmali** — dongu numaralarindan gunluk basi
bulunur, son kayit tek islemli "unmount" degilse ret ("Linux'ta baglayip
duzgun ayirin"). v4, gercek zamanli alt birim, harici gunluk, NEEDSREPAIR,
metadir ve bilinmeyen ozellikler reddedilir.

Boyutlandirma katmani: `FsResizeInfo.kind == "xfs"` — en az = mevcut boy
(XFS kuculmez), ust sinir yok, **tasinabilir** (XFS bolum konumu tutmaz).
`layoutedit`, geri yukleme doldurma ve Uygula kuyrugu ayni yolu kullanir.

## Dogrulama (xfs_repair -n, hepsi temiz)
* Bizim birim 320M -> 330M / 900M; mkfs sade -> 1,2 GB (16 AG).
* mkfs varsayilani (rmapbt+reflink) 320M -> 1 GB; icerikli (5067 dosya)
  -> 700 MB ve sade icerikli -> 2 GB: tum icerik birebir.
* Son AG kisa (agsize=24000b): yalnizca uzatma, icerikli uzatma, uzatma +
  yeni AG (varsayilan ve sade) — icerik birebir.
* 300M -> 301M (yeni AG 256 blok).
* Kirli gunluk (unmount bayragi silinmis) reddedildi.
* t67: DiskSession ile buyut / saga tasi / sola tasi + buyut, kucultme
  reddi ("320 MB altina inemez"), mkfs birimi, kirli gunluk.
