---
name: git-tek-main
description: Depoda tek dal: main. Yeni dal/PR acilmaz, commit'ler dogrudan main'e gider ve push edilir
metadata:
  type: feedback
---

Kullanici 2026-09-28'de: "tek main olsun branch acma". Commit'ler dogrudan
`main`'e yapilir ve `main` push edilir; ozellik dali ya da PR acilmaz.

**Why:** tek gelistirici, tek dal ile calismak istiyor; o gune kadarki
`tanilama-ve-donma-duzeltmeleri` dali `main`'e ileri sarilip silindi.

**How to apply:** commit isteginde `main` uzerinde calis; baska bir daldaysan
once `main`'e ileri sar (`--ff-only`), sonra commit/push. Zorlamali push
yapma. Oturum dokumleri (`.claude/sessions/`) ayri bir "oturum kaydi"
commit'iyle girer. Ilgili: [[virtualbox-win10-test]].
