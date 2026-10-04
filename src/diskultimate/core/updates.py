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
import socket
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ..i18n import tr

PROJECT_URL = "https://github.com/mcansiz/DiskUltimate"
RELEASES_URL = PROJECT_URL + "/releases"
API_URL = "https://api.github.com/repos/mcansiz/DiskUltimate/releases?per_page=30"
# Yedek kaynak: surum akisi. API'den farkli sunucudan gelir, saatlik istek
# sinirina takilmaz; taslaklari zaten icermez.
ATOM_URL = RELEASES_URL + ".atom"
# Olculdu (2026-10-04, kullanicinin baglantisi): API bir kez 3.6 sn'de
# yanitladi, bir kez 8 sn sinirinda "read operation timed out" verdi; ayni
# anda surum akisi 1.3 sn'de geldi.
TIMEOUT = 15

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


def _read(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json, application/atom+xml",
        "User-Agent": "DiskUltimate-update-check",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_atom(data: bytes) -> List[dict]:
    """GitHub surum akisini (`releases.atom`) API bicimindeki listeye cevirir.

    Etiket girisin kimliginin sonundadir
    (`tag:github.com,2008:Repository/<no>/v0.6.1-beta`), sayfa `link`tedir.
    """
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(data)
    out = []
    for entry in root.findall("a:entry", ns):
        ident = (entry.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1]
        link = entry.find("a:link", ns)
        url = link.get("href", "") if link is not None else ""
        tag = ident or url.rsplit("/", 1)[-1]
        out.append({"tag_name": tag, "name": entry.findtext("a:title", tag, ns),
                    "html_url": url or RELEASES_URL,
                    "published_at": entry.findtext("a:updated", "", ns)})
    return out


def describe_error(exc: Exception) -> str:
    """Ag hatasini kullaniciya anlasilir anlatir."""
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, (socket.timeout, TimeoutError)) or \
            isinstance(reason, (socket.timeout, TimeoutError)) or \
            "timed out" in str(exc):
        return tr("yanit zaman asimina ugradi")
    if isinstance(exc, urllib.error.HTTPError):
        return tr("sunucu {} dondurdu", exc.code)
    return str(reason)


def fetch(timeout: int = TIMEOUT,
          reader: Callable[[str, int], bytes] = _read) -> List[dict]:
    """Surum listesini okur (ag cagrisi).

    Sira: API, API (ikinci deneme), surum akisi. Ucu de basarisizsa
    `UpdateError`; ilk hatanin nedeni mesajda yazar.
    """
    first_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            data = json.loads(reader(API_URL, timeout).decode("utf-8"))
            if isinstance(data, list):
                return data
            first_error = first_error or ValueError(tr("Beklenmeyen yanit"))
            break                              # bicim hatasi: tekrar denemenin anlami yok
        except Exception as exc:               # noqa: BLE001
            first_error = first_error or exc
    try:
        return parse_atom(reader(ATOM_URL, timeout))
    except Exception:                          # noqa: BLE001
        pass
    raise UpdateError(tr("GitHub'a ulasilamadi: {}", describe_error(first_error)))


def check(current: str, fetcher: Callable[[], List[dict]] = fetch
          ) -> Tuple[Optional[Release], Optional[Release]]:
    """(yeni_surum_ya_da_None, en_son_surum). Ag hatasinda `UpdateError`."""
    latest = newest(fetcher())
    if latest is not None and is_newer(latest.tag, current):
        return latest, latest
    return None, latest
