# 0095 — Uzun işlerde güç seçenekleri: uyku engeli ve "işlem bitince"

Tarih: 2026-10-09
Durum: **uygulandı** (Linux'ta ölçüldü; Windows/macOS yolu yazıldı, VM'de henüz sınanmadı)

## Kullanıcı isteği

"Disk klonlama, bölümleme gibi işlemlerde işlem bittikten sonra ne yapılsın
(gerçek donanımda işlemler uzun sürebilir) ekleyebilir miyiz; ek olarak sistem
uyku moduna girmeyi engelleyen yer olmalı." — DiskGenius'un "When Finished:
Shut Down / Reboot / Stand by / Hibernate" ve "Prevent System From Sleeping
During Execution" seçenekleri örnek gösterildi.

## Karar

Ortak bir arayüz bileşeni (`ui/widgets/power_options.PowerOptions`) üç yerde:

| Pencere | Seçenek |
|---|---|
| Uygula (bütün kuyruk işlemleri) | uyku engeli + işlem bitince |
| Yedekle / geri yükle | uyku engeli + işlem bitince |
| `run_task(power=POWER_FULL)`: klonlama, kurtarma, dışa aktarma, sanal disk | uyku engeli + işlem bitince |
| `run_task(power=POWER_SLEEP)`: silinmiş dosya / kayıp bölüm / imza taraması | **yalnızca** uyku engeli |

İşletim sistemi farkı `core/platform.py` içinde (CLAUDE.md çapraz platform kuralı):

* **Uyku engeli** (`SleepInhibitor`):
  * Windows `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)` —
    çağıran iş parçacığına bağlıdır, bu yüzden arayüz parçacığında alınır/bırakılır.
  * Linux `systemd-inhibit --what=sleep:idle --mode=block cat`
  * macOS `caffeinate -i -w <pid>`
* **Eylem** (`power_action`):
  * Windows `shutdown /s|/r /t 0`, `shutdown /h`, uyku için
    `powrprof.SetSuspendState(False, …)`
  * Linux `systemctl poweroff|reboot|suspend|hibernate`
  * macOS `pmset sleepnow`, kapat/yeniden başlat için `shutdown` (root) ya da
    System Events; hazırda bekletme macOS'ta desteklenmez (seçenek gri, neden ipucunda).

## Kurallar ve gerekçeleri

1. **Uyku engeli varsayılan açık ve hatırlanır**; iş sürerken açılıp kapatılabilir.
2. **"İşlem bitince" her pencerede kapalı başlar, hatırlanmaz.** Dün gecenin
   "kapat" seçimi bugünkü kısa işin sonunda bilgisayarı kapatmamalı.
3. **Eylem yalnızca başarıda.** Hata ya da durdurmada bilgisayar açık kalır;
   kullanıcı hatayı görmeli.
4. **Gözetimsiz iş beklemez:** eylem seçiliyse Uygula penceresi "Kapat"
   tıklamasını beklemeden kapanır. Yoksa gece işi hiç bitmemiş sayılırdı.
5. **60 saniyelik geri sayım** (Şimdi / İptal; varsayılan düğme İptal).
6. **Eylemden önce tamponlar diske yazılır** (`MainWindow._flush_for_power`:
   dosya sistemleri kapanır, görüntü `fsync` edilir). Kapatmada işletim sistemi
   süreci sonlandırır; Python'un yazılmamış tamponu kaybolurdu.
7. **Taramalarda "bitince kapat" yok:** sonuç bellekte durur, kapatma sonucu yok eder.
8. **Çökmeye dayanıklı engel:** Linux'ta `cat` bizim borumuzu, macOS'ta
   `caffeinate -w` bizim PID'imizi izler; uygulama çökerse engel kendiliğinden
   kalkar (Linux'ta `os._exit` ile ölçüldü: engel listeden düştü).

## Doğrulama

* `tests.run_all` t97: destek sorgusu, bilinmeyen eylem reddi, engelin yaşam
  döngüsü (gerçek eylem çalıştırılmaz).
* `tests.ui_smoke` `guc_secenekleri_denetimi`: eylem yalnızca başarıda döner,
  engel bitişte ve iş sırasında kapatılınca kalkar, geri sayım → hazırlık → eylem
  sırası (sahte `power_action`).
* Linux ana makine: engel `systemd-inhibit --list` içinde göründü, bırakılınca
  ve süreç çökünce kalktı.
* **Sınanmadı:** Windows `SetThreadExecutionState` / `powercfg /requests`,
  macOS `caffeinate`; gerçek kapatma/uyku eylemi hiçbir platformda çalıştırılmadı.
