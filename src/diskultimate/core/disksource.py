"""Disk kaynak modeli: goruntuler ve fiziksel diskler tek listede (ADR 0049).

4. asama. "Hangi diskler var, hangisi uygulamada acik?" sorusu iki yerde ayri
ayri yanitlaniyordu — ana agac ve yedek penceresinin "Disk sec" listesi — ve
birbirinden ayrismisti: acik fiziksel disk yedek penceresinde **iki kez**
gorunuyor, ikinci satir secilince ayni aygita ikinci bir tutamac aciliyordu
(ADR 0021 ihlali, ADR 0047). Kural artik tek yerde:

* Goruntu dosyalari (acik oturumlar, fiziksel olmayan) ayri listededir.
* **Her fiziksel disk bir kez** gorunur. Uygulamada aciksa kaynak o
  **oturuma baglanir**: okuma ve yazma oturumun tutamacindan yapilir,
  bolumler oturumdan (tazedir) gelir.
* Disk listesinde (arka plan taramasi) henuz olmayan ama uygulamada acik
  fiziksel disk de listelenir.

Arayuz import etmez; hicbir aygita dokunmaz (listeler zaten toplanmistir).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class DiskSource:
    """Listede tek satir: bir goruntu ya da bir fiziksel disk."""

    kind: str                       # 'image' | 'physical'
    path: str
    label: str
    size: int
    session: object = None          # uygulamada aciksa oturum
    disk: object = None             # fiziksel disk bilgisi (DiskInfo)
    partitions: List = field(default_factory=list)
    scheme: str = ""

    @property
    def is_open(self) -> bool:
        return self.session is not None

    @property
    def is_system(self) -> bool:
        return bool(self.disk is not None and getattr(self.disk, "is_system",
                                                      False))


def _session_disk(session):
    if not getattr(session, "is_physical", False):
        return None
    return getattr(session, "disk_info", None)


def collect(sessions, disks, surveys: Optional[Dict] = None
            ) -> Tuple[List[DiskSource], List[DiskSource]]:
    """(goruntuler, fiziksel_diskler) — her fiziksel disk bir kez."""
    surveys = surveys or {}
    images: List[DiskSource] = []
    open_disks: Dict[str, object] = {}
    for session in sessions:
        info = _session_disk(session)
        if info is not None:
            open_disks[info.path] = session
            continue
        images.append(DiskSource(
            kind="image", path=getattr(session, "path", "") or session.name,
            label=session.name, size=session.image.size, session=session,
            partitions=list(session.partitions), scheme=session.scheme))

    listed = list(disks)
    known = {d.path for d in listed}
    for path, session in open_disks.items():
        if path not in known:
            listed.append(session.disk_info)

    physical: List[DiskSource] = []
    for disk in listed:
        session = open_disks.get(disk.path)
        if session is not None:
            parts, scheme = list(session.partitions), session.scheme
            size = session.image.size
        else:
            survey = surveys.get(disk.path)
            parts = list(getattr(survey, "partitions", []) or [])
            scheme = getattr(survey, "scheme", "")
            size = disk.size
        physical.append(DiskSource(
            kind="physical", path=disk.path,
            label=getattr(disk, "display_name", disk.path), size=size,
            session=session, disk=disk, partitions=parts, scheme=scheme))
    return images, physical
