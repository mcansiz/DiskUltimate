---
name: agir-testler-github
description: "Agir testler (VM, tam run_all, GUI gezinti) yerelde degil GitHub Actions'ta; uzun sessiz beklemeden kacin"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 39080068-d28d-474a-9f81-3c0c3d3086f5
  modified: 2026-10-14T17:46:04.594Z
---

Kullanici 2026-10-14'te iki kez "uzun surdu", sonra "pc cok isindi" dedi:
VirtualBox win10 misafiri + ana makinede paralel GUI testi bilgisayari
isitti; 10 dakikalik asili kalan bir test de sessizce beklenmisti.

**Why:** ana makine bir dizustu/masaustu; VM + Qt testleri ayni anda
calisinca isiniyor. Sessiz bekleme kullaniciya "takildi mi?" dedirtiyor.

**How to apply:**
- Agir koşulari (tam `tests.run_all`, `ui_smoke`, `tests.long.*`, Windows
  dogrulamasi) once GitHub Actions'ta kos (push sonrasi `gh run` ile izle);
  GitHub makinesi VM sayilir (ADR 0080). Yerelde yalnizca hafif denetimler.
- VirtualBox win10'u yalnizca GitHub'da olculemeyen sey icin ac; bitince
  kapat. Ayni anda ana makinede ikinci agir is kosma.
- Her uzun komuta kisa bir sure siniri koy (`--sinir`, `timeout`), ciktiyi
  dosyaya yaz ve arka planda izle; `| tail` ile tamponlayip korlemesine
  bekleme. Bekleme 1-2 dakikayi asarsa kullaniciya ne beklendigini soyle.
Ilgili: [[virtualbox-win10-test]], [[linux-test-ortami]].
