#!/usr/bin/env python3
"""SessionEnd kancasi: Claude oturum dokumunu proje icine arsivler.

CLAUDE.md kurali geregi tum oturum kayitlari proje icindeki .claude/ altinda
durur; kullanici ev dizinine (~/.claude) kalici kayit birakilmaz.

stdin : SessionEnd kanca JSON'u (session_id, transcript_path, cwd, reason)
cikti : <proje>/.claude/sessions/<YYYY-MM-DD>-<session_id>.jsonl
        <proje>/.claude/sessions/INDEX.md  (tarihli dizin)

Kanca hicbir kosulda oturumu bosa dusurmez: hata durumunda sessizce 0 doner.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

INDEX_HEADER = (
    "# Claude Oturum Arsivi\n\n"
    "SessionEnd kancasi (`.claude/hooks/archive-session.py`) tarafindan\n"
    "otomatik yazilir. Ham `.jsonl` dokumleri `.gitignore` icindedir, yani\n"
    "depoya girmez; bu dizin ve bu dosya depoda kalir. Baglantilar bu yuzden\n"
    "yalnizca dokumun bulundugu makinede acilir.\n\n"
    "| Tarih | Oturum | Bitis | Boyut |\n"
    "|---|---|---|---|\n"
)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def upsert_index(index_path: Path, key: str, row: str) -> None:
    """Dokum icin INDEX satirini yazar; ayni dokumun satiri varsa **gunceller**.

    Bir oturum birden cok kez `SessionEnd` uretebilir (ornegin once `other`,
    sonra `exit`). Her seferinde satir eklemek dizini mukerrer kayitla sisirir;
    bu yuzden anahtar olarak dokum dosyasinin adi kullanilir ve o satir yerinde
    degistirilir.
    """
    if not index_path.exists():
        index_path.write_text(INDEX_HEADER, encoding="utf-8")
    satirlar = index_path.read_text(encoding="utf-8").splitlines(keepends=True)
    imza = f"]({key})"
    for i, satir in enumerate(satirlar):
        if satir.startswith("|") and imza in satir:
            if satir == row:
                return
            satirlar[i] = row
            index_path.write_text("".join(satirlar), encoding="utf-8")
            return
    if satirlar and not satirlar[-1].endswith("\n"):
        satirlar[-1] += "\n"
    satirlar.append(row)
    index_path.write_text("".join(satirlar), encoding="utf-8")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    source = payload.get("transcript_path")
    if not source or not os.path.isfile(source):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or "."
    dest_dir = Path(project_dir) / ".claude" / "sessions"
    dest_dir.mkdir(parents=True, exist_ok=True)

    session_id = str(payload.get("session_id") or Path(source).stem)
    stamp = datetime.now().strftime("%Y-%m-%d")
    dest = dest_dir / f"{stamp}-{session_id}.jsonl"
    shutil.copy2(source, dest)

    reason = str(payload.get("reason") or "-")
    row = f"| {stamp} | [{session_id}]({dest.name}) | {reason} | {human_size(dest.stat().st_size)} |\n"
    upsert_index(dest_dir / "INDEX.md", dest.name, row)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
