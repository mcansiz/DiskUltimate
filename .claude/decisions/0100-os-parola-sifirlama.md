# 0100 — İşletim sistemi (giriş) parolasını sıfırlama

Tarih: 2026-10-21
Durum: **uygulanıyor** — Linux (`/etc/shadow`) çekirdeği + testi bu oturumda;
Windows (SAM hive) çekirdeği sonraki dilimde, gerçek SAM ile Windows
misafirinde doğrulanarak (CLAUDE.md: diske yazan işler VM'de test edilir).
İlgili: [0099](0099-bitlocker-kilit-acma.md) (şifreli birim önce açılır),
[0025](0025-islem-kuyrugu.md), [0014](0014-fiziksel-disk-destegi.md).

## Kullanıcı isteği

"OS şifresi unutulursa bunu çözmeye yönelik ne yapabiliriz?" Kapsam seçimi:
**Windows + Linux**.

Netleştirme: unutulmuş parola **geri getirilemez** (hash'li saklanır; düz metni
çıkarmak kırmaktır — yapılmaz). Yapılan iş parolayı **sıfırlamak / değiştirmek /
kaldırmak**: diske fiziksel erişimi olan kullanıcının kendi makinesinde kurtarma
senaryosu (chntpw'nin yaptığı gibi). Şifreli birimde önce BitLocker/LUKS açılır
(ADR 0099).

## Karar

Yeni modül `core/ospass.py` (saf Python; `crypt` gibi platforma bağlı modül
**import etmez** — çapraz platform, CLAUDE.md). Bir `FileSystemAccess`
(bölümün açık dosya sistemi) üzerinde çalışır.

**Linux** (`/etc/shadow` + `/etc/passwd`):
- `linux_users(fs)` — kullanıcılar: ad, UID, parola durumu (var / yok / kilitli
  `!`/`*` / sistem hesabı).
- `linux_set_password(fs, user, new=None, lock=/unlock=/blank=)` — shadow
  satırının 2. alanını düzenler: yeni parola verilirse **saf-Python
  `sha512crypt` ($6$)** ile hash'lenip yazılır; boşalt (parolasız) / kilitle
  (`!` ön eki) / kilidi aç. Dosya `ExtWriter` ile yeniden yazılır (var olanın
  üzerine, her işlem e2fsck ile doğrulanmış).
- `sha512crypt` saf Python; ana makinedeki stdlib `crypt` ile KAT doğrulanır
  (test oracle'ı — çekirdekte değil).

**Windows** (SAM hive — sonraki dilim):
- `core/winreg.py` — regf (Windows NT Registry) hive çözümleyici (saf Python):
  nk/vk/alt-anahtar listesi gezinme; `SAM\Domains\Account\Users\<RID>`.
- Parola **boşaltma**: `V` değerindeki NT/LM hash uzunluk alanlarını 0 yapmak
  (chntpw yöntemi; bootkey gerekmez — hash kurulmuyor, kaldırılıyor). Hesap
  **etkinleştirme**: `F` değerindeki ACB_DISABLED bitini temizlemek.
- Hive dosyasının boyutu değişmediği için **yerinde bayt yaması** (dosyanın
  kümeleri bulunup ilgili sektörler yazılır) — NTFS tahsisine dokunmaz, sistem
  hive'ı için en güvenli yol.
- Microsoft (online) hesabı yerelden sıfırlanamaz → tespit edilip söylenir.

## Güvenlik / dürüstlük

- İşlemden önce hedef dosyanın (`shadow`, `SAM`/`SYSTEM`) **yedeği** alınır
  (geri dönülebilir).
- Onay zorunlu; fiziksel disk güvenlik katmanları (ADR 0014) aynen geçerli;
  kaynak yazma kipinde açık olmalı.
- "Geri getirme" değil **sıfırlama** olarak sunulur; kendi diskinde kullanım.
- Boş parola (Linux): PAM `nullok` kapalıysa giriş reddedebilir → arayüz
  yeni parola belirlemeyi önerir; boşaltma ikincil seçenektir.

## Test planı

- Linux: `tests/ospass_check.py` — `sha512crypt` KAT (stdlib `crypt`'e karşı,
  ana makine), ve uçtan uca ext4 imajında (/etc/passwd + /etc/shadow yaz,
  parola sıfırla/belirle, geri oku + `e2fsck -fn`). Ana makinede tam
  doğrulanabilir.
- Windows: SAM enumerasyon ve parola boşaltma **gerçek bir SAM hive ile**
  (Windows misafirinde üretilmiş) doğrulanmadan "çalışıyor" denmez; kötü SAM
  yazması girişi açılmaz bırakır. Önce regf çözümleyici gerçek hive'da sınanır,
  sonra yazma izole bir test hesabında denenir (misafir/CI).
