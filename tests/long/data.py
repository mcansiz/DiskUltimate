"""Tohumlu test verisi ve ozet defteri.

Ayni tohum ayni dosya agacini ve ayni icerigi uretir: CI'da dusen bir
senaryo yerelde ayni tohumla birebir tekrarlanir. Icerik akistir (5 GiB'lik
dosya bellege alinmaz); her 1 MiB'lik parcanin basinda dosya kimligi ve parca
numarasi vardir, boylece yanlis yere yazilmis ya da yer degistirmis bir parca
da ozet farki olarak yakalanir.
"""
from __future__ import annotations

import hashlib
import random
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB
BLOCK = MIB

# Ad havuzu: uzun ad, Turkce/Unicode, bosluk, nokta, buyuk-kucuk harf.
_WORDS = ["Belgeler", "Resimler", "ağaç", "çiçek", "Öğrenci", "İstanbul",
          "şarkı", "ünlü", "dosya", "rapor 2026", "Yedek.eski", "日本語",
          "Ελληνικά", "кириллица", "notlar", "veri_seti", "x" * 40]
_EXTS = [".txt", ".bin", ".log", ".pdf", ".jpg", ".tar", ""]


@dataclass
class FileSpec:
    path: str
    size: int
    key: int                        # icerik tohumu


@dataclass
class Profile:
    name: str
    small_count: int                # kucuk dosya sayisi
    small_max: int                  # kucuk dosya en buyuk boyut
    medium: List[int]               # orta dosya boyutlari
    big: List[int]                  # buyuk dosya boyutlari (FS sinirina gore suzulur)
    max_depth: int = 4


PROFILES: Dict[str, Profile] = {
    "quick": Profile("quick", small_count=120, small_max=64 * KIB,
                     medium=[3 * MIB, 8 * MIB, 8 * MIB + 7], big=[64 * MIB + 3]),
    "full": Profile("full", small_count=3000, small_max=256 * KIB,
                    medium=[64 * MIB, 256 * MIB, 256 * MIB + 511],
                    big=[1 * GIB + 1, 5 * GIB + 3]),
}


class Content:
    """`FileSpec` icin akis icerigi (dosya benzeri: `read(n)`)."""

    def __init__(self, spec: FileSpec):
        self.spec = spec
        self.pos = 0
        rnd = random.Random(spec.key)
        self._base = rnd.randbytes(BLOCK)

    def _block(self, index: int) -> bytes:
        # 1 MiB'lik taban blok parca numarasiyla dondurulur; bastaki 16 bayt
        # dosya anahtari ve parca numarasi — yer degistirme yakalanir.
        rot = (index * 7919) % BLOCK
        body = self._base[rot:] + self._base[:rot]
        return struct.pack("<QQ", self.spec.key, index) + body[16:]

    def read(self, n: int = -1) -> bytes:
        left = self.spec.size - self.pos
        if n < 0 or n > left:
            n = left
        out = []
        while n > 0:
            index, off = divmod(self.pos, BLOCK)
            take = min(n, BLOCK - off)
            out.append(self._block(index)[off:off + take])
            self.pos += take
            n -= take
        return b"".join(out)


def content_sha1(spec: FileSpec) -> str:
    h = hashlib.sha1()
    c = Content(spec)
    while True:
        piece = c.read(4 * MIB)
        if not piece:
            break
        h.update(piece)
    return h.hexdigest()


@dataclass
class Dataset:
    seed: int
    profile: Profile
    files: List[FileSpec] = field(default_factory=list)
    dirs: List[str] = field(default_factory=list)


def footprint(size: int, unit: int) -> int:
    """Dosyanin birimde kapladigi yer (kume artigi + dizin girisi payi).

    Bayt toplami kucuk dosyali profillerde yaniltir: FAT12'de (8 KiB kume)
    1455 dosyanin artigi tek basina ~6 MiB tuttu, 21 MiB'lik birim "yer yok"
    dedi (full profil, 2026-10-04).
    """
    if not unit:
        return size
    return -(-max(size, 1) // unit) * unit + 512


def build(seed: int, profile: Profile, max_file: int = 0,
          budget: int = 0, ascii_only: bool = False,
          name_max: int = 255, unit: int = 0) -> Dataset:
    """Tohumdan dosya agaci uretir.

    `unit`: butce hesabinda dosya boyutu bu birime yuvarlanir (`footprint`).
    `max_file`: dosya sisteminin tek dosya siniri (FAT32: 4 GiB-1); asan
    buyuk dosyalar listeden cikar (sinir ayrica sinanir). `budget`: toplam
    veri ust siniri (bolum boyutu). `ascii_only`/`name_max`: ad kurallari
    (ISO vb. icin; yazilabilir FS'lerde Unicode kullanilir).
    """
    rnd = random.Random(seed)
    ds = Dataset(seed, profile)
    words = [w for w in _WORDS if not ascii_only or w.isascii()]
    dirs = ["/"]
    for i in range(max(3, profile.small_count // 40)):
        parent = rnd.choice(dirs)
        depth = parent.count("/") if parent != "/" else 0
        if depth >= profile.max_depth:
            parent = "/"
        name = f"{rnd.choice(words)} {i:03d}"[:name_max]
        path = (parent.rstrip("/") + "/" + name)
        dirs.append(path)
    ds.dirs = dirs[1:]
    used = 0
    key = seed * 1_000_003

    def add(size: int, folder: str, stem: str) -> None:
        nonlocal used, key
        cost = footprint(size, unit)
        if budget and used + cost > budget:
            return
        if max_file and size > max_file:
            return
        key += 1
        name = f"{stem}{rnd.choice(_EXTS)}"[:name_max]
        path = (folder.rstrip("/") + "/" + name) if folder != "/" else "/" + name
        if any(f.path.lower() == path.lower() for f in ds.files):
            path += f".{key % 1000}"
        ds.files.append(FileSpec(path, size, key))
        used += cost

    for size in profile.big:
        add(size, "/", f"buyuk_{size // MIB}MiB")
    for size in profile.medium:
        add(size, rnd.choice(ds.dirs or ["/"]), f"orta_{size // MIB}MiB")
    for i in range(profile.small_count):
        size = rnd.choice([0, 1, 511, 512, 4096, rnd.randint(1, profile.small_max)])
        add(size, rnd.choice(["/"] + ds.dirs), f"{rnd.choice(words)}_{i:04d}")
    return ds


@dataclass
class Manifest:
    """Beklenen icerik: yol -> (boyut, sha1)."""
    entries: Dict[str, tuple] = field(default_factory=dict)

    def put(self, spec: FileSpec, sha1: Optional[str] = None) -> None:
        self.entries[spec.path] = (spec.size, sha1 or content_sha1(spec))

    def drop(self, path: str) -> None:
        self.entries.pop(path, None)

    @property
    def total_bytes(self) -> int:
        return sum(size for size, _ in self.entries.values())


def iter_specs(ds: Dataset) -> Iterator[FileSpec]:
    return iter(ds.files)
