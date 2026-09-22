#!/usr/bin/env python3
"""Oturum dokumlerini proje icine tasir ve VS Code eklentisine gosterir.

Claude Code her oturumun dokumunu `<config>/projects/<slug>/<oturum>.jsonl`
altina yazar; `<slug>` calisma dizininin yolundan uretilir. VS Code eklentisi
"Past conversations" listesini **yalnizca bu klasoru okuyarak** kurar (dosya
adi gecerli bir oturum kimligi olmali, `.jsonl` uzantili olmali). Dokumun
icindeki `cwd` alanina bakmaz.

Bundan iki sonuc cikar:

1. Dokumler proje icinde durursa ve `<config>/projects/<slug>` oraya bir
   baglanti (symlink / junction) olursa, gecmis hem depoyla tasinir hem de
   eklentide gorunur.
2. Makineler farkli slug uretir (Windows'ta `d--pythonProjeler-...`,
   Linux'ta `-home-pc-Belgeler-...`). Her makinenin baglantisi **ayni**
   klasore -- `.claude/sessions/live/` -- bakarsa butun makinelerin gecmisi
   her makinede listelenir.

Kullanim (Claude Code KAPALIYKEN):

    python3 .claude/hooks/setup-sessions.py            # kurar / onarir
    python3 .claude/hooks/setup-sessions.py --check    # yalnizca durum
    python3 .claude/hooks/setup-sessions.py --dry-run  # ne yapacagini yazar
    python3 .claude/hooks/setup-sessions.py --import-legacy
        # proje eski bir yolda acilmisken birikmis dokumleri de iceri alir

Acik oturum varken calistirilirsa dokum ikiye bolunebilir; betik son bir
dakika icinde yazilmis dokum gorurse durur (`--force` ile gecilir).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIVE = REPO / ".claude" / "sessions" / "live"
ACTIVE_WINDOW_SN = 60          # son N saniyede yazilmis dokum = acik oturum
SLUG_MAX = 200                 # Claude Code uzun slug'i kisaltip hash ekler


def config_dir() -> Path:
    ozel = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(ozel) if ozel else Path.home() / ".claude"


def slug_of(path: Path) -> str:
    """Claude Code'un klasor adi uretimi: alfanumerik disi her karakter '-'."""
    try:
        cozulmus = path.resolve()
    except OSError:
        cozulmus = path
    ad = re.sub(r"[^a-zA-Z0-9]", "-", str(cozulmus))
    return ad[:SLUG_MAX] if len(ad) > SLUG_MAX else ad


def is_link(path: Path) -> bool:
    """Symlink veya Windows junction mu? (junction icin is_symlink yetmez)"""
    if path.is_symlink():
        return True
    if os.name == "nt" and path.exists():
        try:
            return bool(os.readlink(path))
        except OSError:
            return False
    return False


def link_target(path: Path) -> Path | None:
    try:
        return Path(os.readlink(path))
    except OSError:
        return None


def make_link(link: Path, hedef: Path) -> str:
    """Baglantiyi kurar; nasil kurdugunu soyler."""
    try:
        os.symlink(str(hedef), str(link), target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
    # Windows: symlink yetki ister, junction istemez.
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(hedef)],
        check=True, capture_output=True,
    )
    return "junction"


def baglanti_sil(yol: Path) -> None:
    """Symlink'i siler; Windows'ta klasor baglantisi `rmdir` ister."""
    try:
        yol.unlink()
    except (IsADirectoryError, PermissionError, OSError):
        os.rmdir(yol)


def son_yazma(klasor: Path) -> float:
    en_yeni = 0.0
    for dosya in klasor.glob("*.jsonl"):
        if dosya.is_symlink():
            continue          # depodaki dokuma bakan baglanti; acik oturum degil
        try:
            en_yeni = max(en_yeni, dosya.stat().st_mtime)
        except OSError:
            pass
    return en_yeni


def ilk_cwd(dokum: Path) -> str | None:
    """Dokumun ilk satirlarindaki `cwd` degeri (hangi dizinde calisilmis)."""
    try:
        with dokum.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(40):
                satir = fh.readline()
                if not satir:
                    break
                if '"cwd"' not in satir:
                    continue
                try:
                    kayit = json.loads(satir)
                except ValueError:
                    continue
                if isinstance(kayit, dict) and kayit.get("cwd"):
                    return str(kayit["cwd"])
    except OSError:
        pass
    return None


def tasi(kaynak: Path, hedef_klasor: Path, dry: bool) -> list[str]:
    """Bir klasordeki dokumleri hedefe tasir; catismada buyuk olani tutar.

    Alt klasorler (oturumun `tool-results` yan dosyalari, `memory`) hedefte de
    varsa **ic ice birlestirilir**; yoksa kaynak klasor bosalmaz ve baglanti
    kurulamaz.
    """
    notlar: list[str] = []
    for oge in sorted(kaynak.iterdir()):
        hedef = hedef_klasor / oge.name
        if oge.is_symlink() and hedef.exists():
            # Dokum zaten hedefte; buradaki yalnizca ona bakan bir baglanti.
            notlar.append(f"  atlandi: {oge.name} (baglanti, hedefte var)")
            if not dry:
                baglanti_sil(oge)
            continue
        if oge.is_dir() and not oge.is_symlink() and hedef.is_dir():
            notlar.extend(tasi(oge, hedef, dry))
            if not dry and oge.is_dir() and not any(oge.iterdir()):
                oge.rmdir()
            continue
        if hedef.exists():
            if oge.is_file() and hedef.is_file():
                if oge.stat().st_size > hedef.stat().st_size:
                    notlar.append(f"  guncel: {oge.name} (buyuk olan alindi)")
                    if not dry:
                        shutil.move(str(oge), str(hedef))
                else:
                    notlar.append(f"  atlandi: {oge.name} (hedefte var)")
                    if not dry:
                        oge.unlink()
            else:
                notlar.append(f"  atlandi: {oge.name} (hedefte var)")
            continue
        notlar.append(f"  tasindi: {oge.name}")
        if not dry:
            shutil.move(str(oge), str(hedef))
    return notlar


def legacy_adaylari(projeler: Path, guncel_slug: str) -> list[Path]:
    """Projenin eski yollarina ait dokum klasorleri.

    Proje tasindiginda (ornegin /home/pc/diskUltimate -> .../GitHub/DiskUltimate)
    eski slug altinda kalan dokumler eklentide gorunmez. Aday olmak icin
    klasordeki bir dokumun `cwd` degerinin son parcasi proje klasoru adiyla
    ayni olmali.
    """
    hedef_ad = REPO.name.lower()
    adaylar = []
    if not projeler.is_dir():
        return adaylar
    for klasor in sorted(projeler.iterdir()):
        if klasor.name == guncel_slug or not klasor.is_dir() or is_link(klasor):
            continue
        for dokum in sorted(klasor.glob("*.jsonl")):
            cwd = ilk_cwd(dokum)
            if not cwd:
                continue
            parca = re.split(r"[\\/]", cwd.rstrip("\\/"))
            if parca and parca[-1].lower() == hedef_ad:
                adaylar.append(klasor)
            break
    return adaylar


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="yalnizca durum bildir")
    ap.add_argument("--dry-run", action="store_true", help="degistirmeden anlat")
    ap.add_argument("--import-legacy", action="store_true",
                    help="projenin eski yollarindaki dokumleri de iceri al")
    ap.add_argument("--force", action="store_true",
                    help="acik oturum uyarisini yoksay")
    args = ap.parse_args()
    dry = args.dry_run or args.check

    projeler = config_dir() / "projects"
    slug = slug_of(REPO)
    baglanti = projeler / slug

    print(f"proje      : {REPO}")
    print(f"dokum yeri : {LIVE}")
    print(f"slug       : {slug}")
    print(f"baglanti   : {baglanti}")

    if not LIVE.exists():
        print("durum      : dokum klasoru yok -> olusturulacak")
        if not dry:
            LIVE.mkdir(parents=True, exist_ok=True)

    # 1. Durum
    if is_link(baglanti):
        hedef = link_target(baglanti)
        cozulmus = baglanti.resolve() if baglanti.exists() else None
        if cozulmus == LIVE.resolve():
            print("durum      : TAMAM (baglanti proje icini gosteriyor)")
            adaylar = legacy_adaylari(projeler, slug) if args.import_legacy or args.check else []
            if adaylar and not args.import_legacy:
                for a in adaylar:
                    print(f"not        : eski yol dokumleri var -> {a.name}"
                          "  (--import-legacy ile alinir)")
            elif adaylar:
                for a in adaylar:
                    print(f"iceri al   : {a.name}")
                    for satir in tasi(a, LIVE, dry):
                        print(satir)
                    if not dry and not any(a.iterdir()):
                        a.rmdir()
            return 0
        print(f"durum      : baglanti BASKA yeri gosteriyor -> {hedef}")
        if args.check:
            return 1
        if not dry:
            baglanti.unlink()

    elif baglanti.is_dir():
        yazma = son_yazma(baglanti)
        acik = yazma and (time.time() - yazma) < ACTIVE_WINDOW_SN
        print("durum      : gercek klasor (dokumler ev dizininde, depoya girmiyor)")
        if acik and not args.force and not dry:
            print("DUR        : son bir dakikada yazilmis dokum var -- Claude Code"
                  " acik gorunuyor.\n"
                  "             Kapatip yeniden calistirin (ya da --force).")
            return 2
        if args.check:
            return 1
        print("tasiniyor  :")
        for satir in tasi(baglanti, LIVE, dry):
            print(satir)
        if not dry:
            for alt in sorted(baglanti.glob("**/*"), reverse=True):
                if alt.is_dir() and not any(alt.iterdir()):
                    alt.rmdir()
            if any(baglanti.iterdir()):
                print(f"DUR        : {baglanti} bosalmadi, baglanti kurulmadi")
                return 3
            baglanti.rmdir()

    else:
        print("durum      : baglanti yok -> kurulacak")
        if args.check:
            return 1

    # 2. Baglantiyi kur
    if not dry:
        projeler.mkdir(parents=True, exist_ok=True)
        tur = make_link(baglanti, LIVE)
        print(f"kuruldu    : {tur} -> {LIVE}")
    else:
        print(f"kurulacak  : baglanti -> {LIVE}")

    # 3. Eski yollardaki dokumler
    adaylar = legacy_adaylari(projeler, slug)
    for a in adaylar:
        if args.import_legacy:
            print(f"iceri al   : {a.name}")
            for satir in tasi(a, LIVE, dry):
                print(satir)
            if not dry and not any(a.iterdir()):
                a.rmdir()
        else:
            print(f"not        : eski yol dokumleri var -> {a.name}"
                  "  (--import-legacy ile alinir)")

    sayi = len(list(LIVE.glob("*.jsonl"))) if LIVE.is_dir() else 0
    print(f"gecmis     : {sayi} oturum dokumu")
    print("sonraki    : VS Code'da Claude eklentisini acip gecmis listesine bakin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
