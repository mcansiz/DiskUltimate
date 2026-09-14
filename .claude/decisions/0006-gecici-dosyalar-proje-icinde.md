# ADR 0006 — Gecici dosyalar proje dizininde tutulur

**Tarih:** 2026-09-13 · **Durum:** Kabul edildi

## Baglam
Testler ve bicimlendirme sirasinda uretilen gecici disk goruntuleri baslangicta
sistem gecici dizinine (`/tmp`) yaziliyordu. Proje kurali, uretilen her seyin proje
dizininde kalmasini gerektiriyor; ayrica `/tmp` cogu sistemde `tmpfs`, yani **RAM**
uzerindedir ve 1 GB'lik test goruntuleri belleği doldurabilir.

## Karar
`src/diskultimate/paths.py` tek yetkili yol kaynagi oldu:

| Sabit / islev | Deger |
|---|---|
| `PROJECT_ROOT` | depo koku |
| `LOG_DIR` | `<proje>/.claude/logs` |
| `scratch_root()` | `<proje>/.tmp` |
| `scratch("tests")` | `<proje>/.tmp/tests` |
| `scratch("format")` | `<proje>/.tmp/format` (harici `mkfs` gecici birimi) |
| `scratch("ui")`, `scratch("screenshots")` | arayuz duman testi ciktilari |

`DISKULTIMATE_SCRATCH` verilirse o yol kullanilir. `.tmp/` `.gitignore` icindedir.

## Sonuc
- `tests/run_all.py` ve `tests/ui_smoke.py` yalnizca `<proje>/.tmp` altina yazar.
- Temizlik tek komut: `rm -rf .tmp`
- `core/formatter.py` artik `tempfile` kullanmaz.
