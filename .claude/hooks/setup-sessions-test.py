#!/usr/bin/env python3
"""`setup-sessions.py` icin kum havuzu testi.

Gercek dokumlere **dokunmaz**: sahte bir depo ve sahte bir yapilandirma
dizini (`CLAUDE_CONFIG_DIR`) kurup betigi orada calistirir.

    python3 .claude/hooks/setup-sessions-test.py     # beklenen: 6/6 TAMAM
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BETIK = Path(__file__).resolve().parent / "setup-sessions.py"


def kos(repo: Path, cfg: Path, *ek: str) -> tuple[int, str]:
    ortam = dict(os.environ, CLAUDE_CONFIG_DIR=str(cfg))
    sonuc = subprocess.run(
        [sys.executable, str(repo / ".claude" / "hooks" / "setup-sessions.py"), *ek],
        capture_output=True, text=True, env=ortam,
    )
    return sonuc.returncode, sonuc.stdout + sonuc.stderr


def slug(yol: Path) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9]", "-", str(yol.resolve()))


def kur(kok: Path) -> tuple[Path, Path, Path]:
    """Sahte depo + yapilandirma dizini; gercekteki durumu taklit eder."""
    repo = kok / "DiskUltimate"
    live = repo / ".claude" / "sessions" / "live"
    live.mkdir(parents=True)
    (repo / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    shutil.copy2(BETIK, repo / ".claude" / "hooks" / "setup-sessions.py")

    cfg = kok / "cfg"
    proje = cfg / "projects" / slug(repo)
    proje.mkdir(parents=True)

    # (a) depoda duran eski oturum + yan dosya klasoru
    (live / "11111111-1111-1111-1111-111111111111.jsonl").write_text('{"cwd":"/win/DiskUltimate"}\n')
    yan = live / "11111111-1111-1111-1111-111111111111" / "tool-results"
    yan.mkdir(parents=True)
    (yan / "a.txt").write_text("a")
    # ...ve bu makinede ona bakan gecici baglantilar
    os.symlink(live / "11111111-1111-1111-1111-111111111111.jsonl",
               proje / "11111111-1111-1111-1111-111111111111.jsonl")
    os.symlink(live / "11111111-1111-1111-1111-111111111111",
               proje / "11111111-1111-1111-1111-111111111111",
               target_is_directory=True)

    # (b) bu makinede yazilmis, henuz depoya girmemis oturum
    canli = proje / "44444444-4444-4444-4444-444444444444.jsonl"
    canli.write_text('{"cwd":"%s"}\n' % repo)
    eski_zaman = time.time() - 600
    os.utime(canli, (eski_zaman, eski_zaman))
    (proje / "22222222-2222-2222-2222-222222222222").mkdir()   # bos yan klasor

    # (c) projenin eski yolundaki dokum
    legacy = cfg / "projects" / "-eski-yol-DiskUltimate"
    legacy.mkdir()
    (legacy / "33333333-3333-3333-3333-333333333333.jsonl").write_text(
        '{"type":"user","cwd":"/eski/yol/DiskUltimate"}\n')

    # (d) alakasiz baska bir proje
    baska = cfg / "projects" / "-home-biri-baska"
    baska.mkdir()
    (baska / "55555555-5555-5555-5555-555555555555.jsonl").write_text(
        '{"type":"user","cwd":"/home/biri/baska"}\n')

    return repo, cfg, live


def main() -> int:
    gecen = toplam = 0

    def dogrula(baslik: str, kosul: bool) -> None:
        nonlocal gecen, toplam
        toplam += 1
        gecen += bool(kosul)
        print(f"[{'TAMAM' if kosul else 'HATA '}] {baslik}")

    with tempfile.TemporaryDirectory() as tmp:
        repo, cfg, live = kur(Path(tmp))
        proje = cfg / "projects" / slug(repo)

        kod, cikti = kos(repo, cfg, "--check")
        dogrula("--check kurulmamis durumu bildirir", kod == 1 and "gercek klasor" in cikti)

        kod, cikti = kos(repo, cfg, "--dry-run")
        dogrula("--dry-run hicbir sey degistirmez",
                kod == 0 and proje.is_dir() and not proje.is_symlink())

        # acik oturum korumasi: dokum simdi yazilmis gibi gorunsun
        (proje / "44444444-4444-4444-4444-444444444444.jsonl").touch()
        kod, cikti = kos(repo, cfg)
        dogrula("acik oturum gorulunce durur", kod == 2 and "DUR" in cikti)
        eski = time.time() - 600
        os.utime(proje / "44444444-4444-4444-4444-444444444444.jsonl", (eski, eski))

        kod, cikti = kos(repo, cfg, "--import-legacy")
        dokumler = sorted(p.name for p in live.glob("*.jsonl"))
        dogrula("tasima + baglanti + eski yol ithali",
                kod == 0 and proje.is_symlink()
                and proje.resolve() == live.resolve()
                and dokumler == [
                    "11111111-1111-1111-1111-111111111111.jsonl",
                    "33333333-3333-3333-3333-333333333333.jsonl",
                    "44444444-4444-4444-4444-444444444444.jsonl",
                ]
                and (live / "11111111-1111-1111-1111-111111111111"
                     / "tool-results" / "a.txt").is_file())

        dogrula("alakasiz proje klasorune dokunulmaz",
                (cfg / "projects" / "-home-biri-baska"
                 / "55555555-5555-5555-5555-555555555555.jsonl").is_file())

        kod, cikti = kos(repo, cfg, "--check")
        dogrula("ikinci calistirmada TAMAM", kod == 0 and "TAMAM" in cikti)

    print(f"\n{gecen}/{toplam} TAMAM")
    return 0 if gecen == toplam else 1


if __name__ == "__main__":
    sys.exit(main())
