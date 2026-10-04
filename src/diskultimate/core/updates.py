"""GitHub'da yeni surum var mi? (ADR 0090)

Surumler GitHub Releases'ta yayinlanir. Hepsi su an **on surumdur** (beta):
`/releases/latest` on surumleri saymaz ve 404 dondurur; bu yuzden liste
okunur ve taslak olmayanlarin en yenisi secilir.

Karsilastirma surum numarasiyla yapilir, yayin tarihiyle degil: eski bir
dal icin sonradan cikan bir duzeltme surumu "en yeni" sayilmamali.
`0.6.1-beta < 0.6.1-rc1 < 0.6.1 < 0.6.2-beta`.

Ag okumasi `fetch` icinde, karar `newest`/`is_newer` icinde: karar agsiz
sinanir (t90). Arayuz is parcaciginda cagrilmaz (suresi ongorulemez).
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ..i18n import tr

PROJECT_URL = "https://github.com/mcansiz/DiskUltimate"
RELEASES_URL = PROJECT_URL + "/releases"
API_URL = "https://api.github.com/repos/mcansiz/DiskUltimate/releases?per_page=30"
TIMEOUT = 8

# Ortam degiskeni "0" ise acilistaki otomatik denetim yapilmaz (testler, CI).
ENV_NAME = "DISKULTIMATE_UPDATE_CHECK"

_STAGES = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2}
_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-.]?([a-z]+)\.?(\d+)?)?$",
                      re.IGNORECASE)


class UpdateError(Exception):
    """Surum listesi alinamadi."""


@dataclass
class Release:
    tag: str
    name: str
    url: str
    prerelease: bool = False
    published: str = ""

    @property
    def version(self) -> str:
        return self.tag[1:] if self.tag[:1] in "vV" else self.tag


def parse_version(text: str) -> Optional[Tuple[int, int, int, int, int]]:
    """"v0.6.1-beta" -> (0, 6, 1, 1, 0); tanınmazsa None.

    Asama sirasi: alpha < beta < rc < kararli (3).
    """
    match = _VERSION.match((text or "").strip())
    if not match:
        return None
    major, minor, patch, stage, number = match.groups()
    if stage is None:
        rank = 3
    elif stage.lower() in _STAGES:
        rank = _STAGES[stage.lower()]
    else:
        return None
    return (int(major), int(minor), int(patch or 0), rank, int(number or 0))


def is_newer(candidate: str, current: str) -> bool:
    """`candidate` surumu `current`tan yeni mi? Tanınmayan surum yeni sayilmaz."""
    new, old = parse_version(candidate), parse_version(current)
    return bool(new and old and new > old)


def newest(entries: List[dict]) -> Optional[Release]:
    """GitHub API listesinden taslak olmayan en yeni surum."""
    best: Optional[Release] = None
    best_key = None
    for entry in entries or []:
        if entry.get("draft"):
            continue
        tag = str(entry.get("tag_name") or "")
        key = parse_version(tag)
        if key is None:
            continue
        if best_key is None or key > best_key:
            best_key = key
            best = Release(tag=tag, name=str(entry.get("name") or tag),
                           url=str(entry.get("html_url") or RELEASES_URL),
                           prerelease=bool(entry.get("prerelease")),
                           published=str(entry.get("published_at") or ""))
    return best


def fetch(timeout: int = TIMEOUT) -> List[dict]:
    """GitHub'dan surum listesini okur (ag cagrisi)."""
    request = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "DiskUltimate-update-check",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:                       # noqa: BLE001
        raise UpdateError(tr("Surum listesi alinamadi: {}", exc)) from exc
    if not isinstance(data, list):
        raise UpdateError(tr("Beklenmeyen yanit"))
    return data


def check(current: str, fetcher: Callable[[], List[dict]] = fetch
          ) -> Tuple[Optional[Release], Optional[Release]]:
    """(yeni_surum_ya_da_None, en_son_surum). Ag hatasinda `UpdateError`."""
    latest = newest(fetcher())
    if latest is not None and is_newer(latest.tag, current):
        return latest, latest
    return None, latest
