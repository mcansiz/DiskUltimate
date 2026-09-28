---
name: siradaki-appimage
description: Bekleyen is — Linux AppImage paketlemesi (ADR 0050); kullanici ayri bir oturumda yapacak
metadata:
  type: project
---

2026-09-28: Linux dagitimi icin AppImage kararlastirildi, **uygulanmadi**;
kullanici "AppImage islemini baska bir oturumda yapacagim" dedi.
Is listesi ve dogrulanmamis varsayimlar: `.claude/decisions/0050-linux-dagitimi-appimage-ve-musl.md`.

**Why:** mevcut tek dosyalik PyInstaller ikilisinin masaustu ikonu yok ve
derlendigi makinenin glibc'sine bagli (gelistirme 2.43 > Mint 2.39).

**How to apply:** AppImage'a baslamadan once `core/platform.py`deki yeniden
baslatma komutu `$APPIMAGE` kullanacak sekilde duzeltilmeli (yoksa pkexec ile
yetki alinamaz). Derleme ve test Mint VM'de ([[linux-test-ortami]]). musl
icin ikili uretilmez, kaynaktan calistirma belgelenir.
