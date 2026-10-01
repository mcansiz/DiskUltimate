# 0051 — Archify becerisi proje icine kuruldu

Tarih: 2026-09-28
Durum: **uygulandi** ve calistirildi (2026-09-28: Node 22.22.1, `doctor` tamam,
ilk diyagram uretildi). Chrome/Chromium yok -> `browser-check` kapisi atlaniyor
Ilgili: CLAUDE.md "Kayit / Dokumantasyon Kurali"

## Ne

[tt-a1i/archify](https://github.com/tt-a1i/archify) v3.0.1 (MIT, commit
`0e4949f`) — mimari, is akisi, sira (sequence), veri akisi ve yasam dongusu
diyagramlarini JSON'dan tek dosyalik etkilesimli HTML'e ceviren Claude Code
becerisi. Mermaid girdisini de okur.

Konum: `.claude/skills/archify/` (depodaki `archify.zip` surum paketi,
106 dosya, 7.3 MB; depodaki `test/` ve gelistirme betikleri paketlenmiyor).

## Neden bu yoldan kuruldu

Resmi kurulum `npx skills add tt-a1i/archify -g` — becerinin **global**
dizine (`~/.claude/skills`) yazar. CLAUDE.md kaydi projenin lokaline yazmayi
yasaklar; ayrica global kurulum klonla tasinmaz. Surum zip'i elle proje
icine acildi, boylece beceri depoyla birlikte her makineye gider.

## Ev dizinine yazmayi onlemek

Beceri `finalize`/`deliver` sirasinda guncelleme manifestini okur
(`https://tt-a1i.github.io/archify/skill-updates/archify/stable.json`,
yalnizca GET) ve sonucu varsayilan olarak `~/.cache` (XDG_CACHE_HOME /
APPDATA) altina yazar. `.claude/settings.json` -> `env` icinde
`ARCHIFY_UPDATE_CACHE_DIRECTORY=.claude/cache/archify` verildi (calisma
dizinine gore; Claude Code proje kokunden calisir). Denetim tamamen
kapatilmak istenirse: `ARCHIFY_UPDATE_CHECK_DISABLED=1`.

`.gitignore`: `.archify/` (uretilen diyagram klasorleri) ve `.claude/cache/`.

## Gereksinimler

- **Node.js >= 18** — calisma zamani bagimliligi yok (`node_modules`
  gerekmez; `package.json`daki ajv/simple-icons yalnizca uretec betikleri
  icin).
- `browser-check` kapisi icin Chrome/Chromium (`ARCHIFY_CHROME` ile yol
  verilebilir). Gelistirme makinesinde **yok** (yalnizca snap Firefox);
  `finalize` bu kapida `viewer/chrome-unavailable` ile durur, ilk uc kapi
  (validate, deliver, check) yine de kosar. Gorsel denetim yedegi:
  `firefox --headless --no-remote --profile <proje-ici-klasor> --screenshot
  out.png file://...html` — snap Firefox `/tmp` ve gizli ev klasorlerini
  goremez, profil proje icinde olmali; kullanicinin acik Firefox'una
  dokunulmaz (`--no-remote`).

Bu, DiskUltimate'in "calisma zamani bagimliligi yok" kuralini etkilemez:
beceri bir gelistirme/belgeleme aracidir, kullanici ona ihtiyac duymaz.

## Guncelleme

Beceri kendini guncellemez. Yeni surum icin depo klonlanir, `archify.zip`
`.claude/skills/` altina yeniden acilir ve bu ADR'deki surum/commit guncellenir.
