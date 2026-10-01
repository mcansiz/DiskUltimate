"""ISO 9660 salt okuma — Joliet ve Rock Ridge adlariyla.

Kaynak: ECMA-119 (ISO 9660), Joliet (Microsoft, UCS-2 adlar), SUSP / RRIP
(IEEE P1281/P1282 — Rock Ridge). Hepsi acik belgedir.

Ad onceligi: **Rock Ridge** (POSIX adlari, buyuk/kucuk harf, uzun ad) >
**Joliet** (Unicode, 64 karakter) > duz ISO 9660 (8.3, ";1" surum eki
atilir). Rock Ridge birincil agacta durur; Joliet ayri bir agactir.

Yerlesim:
  * mantiksal blok 2048 bayt; birim tanimlayicilari 16. bloktan itibaren
    (tur 1 birincil, 2 ek/Joliet, 255 sonlandirici).
  * dizin kaydi: uzunluk(1) ek-oznitelik(1) extent(4LE+4BE) boyut(4LE+4BE)
    tarih(7) bayrak(1) ... ad-uzunlugu(1) ad; ardindan System Use (SUSP).
  * kayitlar blok sinirini asmaz: uzunluk 0 ise sonraki bloga gecilir.
  * 4 GiB'den buyuk dosya birden cok kayitla yazilir (bayrak 0x80 "devami
    var"); ayni adli ardisik kayitlar birlestirilir.

Yazma yoktur: ISO 9660 yazilmak icin degil, bastan uretilmek icin tasarlanmistir.
"""
from __future__ import annotations

import datetime
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .image import BlockDevice
from ..i18n import tr

BLOCK = 2048
JOLIET_ESCAPES = (b"%/@", b"%/C", b"%/E")


class IsoError(Exception):
    pass


@dataclass
class IsoEntry:
    name: str
    is_dir: bool
    size: int
    extents: List[Tuple[int, int]] = field(default_factory=list)  # (blok, bayt)
    mtime: Optional[datetime.datetime] = None
    hidden: bool = False
    symlink: str = ""
    mode: int = 0


def _date7(raw: bytes) -> Optional[datetime.datetime]:
    try:
        year, month, day, hour, minute, second, offset = struct.unpack("<6Bb", raw[:7])
        if not month or not day:
            return None
        tz = datetime.timezone(datetime.timedelta(minutes=15 * offset))
        return datetime.datetime(1900 + year, month, day, hour, minute, second,
                                 tzinfo=tz).astimezone().replace(tzinfo=None)
    except (ValueError, struct.error, OverflowError):
        return None


class IsoFS:
    def __init__(self, dev: BlockDevice):
        self.dev = dev
        self.label = ""
        self.joliet = False
        self.rock_ridge = False
        self.volume_blocks = 0
        self._susp_skip = 0
        pvd = None
        svd = None
        for n in range(16, 16 + 64):
            vd = self._block(n)
            if vd[1:6] != b"CD001":
                break
            kind = vd[0]
            if kind == 255:
                break
            if kind == 1 and pvd is None:
                pvd = vd
            elif kind == 2 and vd[88:91] in JOLIET_ESCAPES and svd is None:
                svd = vd
        if pvd is None:
            raise IsoError(tr("ISO 9660 birincil birim tanimlayicisi yok"))
        self.volume_blocks = struct.unpack_from("<I", pvd, 80)[0]
        self.label = pvd[40:72].decode("ascii", "ignore").strip()
        self._pvd_root = pvd[156:156 + 34]
        # Rock Ridge: kok dizinin "." kaydinda SUSP "SP" girisi
        self._detect_rock_ridge()
        if not self.rock_ridge and svd is not None:
            self.joliet = True
            self._root = svd[156:156 + 34]
            label = svd[40:72].decode("utf-16-be", "ignore").strip()
            self.label = label or self.label
        else:
            self._root = self._pvd_root
        self._cache: Dict[str, List[IsoEntry]] = {}

    # ------------------------------------------------------------------
    def _block(self, n: int, count: int = 1) -> bytes:
        return self.dev.read(n * BLOCK, count * BLOCK)

    def _detect_rock_ridge(self) -> None:
        extent = struct.unpack_from("<I", self._pvd_root, 2)[0]
        data = self._block(extent)
        length = data[0]
        if length < 34:
            return
        name_len = data[32]
        su = 33 + name_len + (1 - name_len % 2)
        system_use = data[su:length]
        if system_use[:2] == b"SP" and system_use[4:6] == b"\xbe\xef":
            self.rock_ridge = True
            self._susp_skip = system_use[6]

    def _records(self, extent: int, size: int):
        """Dizin kayitlarini (ham bayt) uretir."""
        blocks = (size + BLOCK - 1) // BLOCK
        data = self._block(extent, blocks) if blocks else b""
        for b in range(blocks):
            base = b * BLOCK
            pos = 0
            while pos < BLOCK:
                length = data[base + pos] if base + pos < len(data) else 0
                if length == 0:
                    break
                yield data[base + pos:base + pos + length]
                pos += length

    def _parse_rr(self, system_use: bytes, entry: IsoEntry) -> None:
        """SUSP girislerinden NM (ad), SL (sembolik bag), PX (kip); CE devami."""
        name_parts: List[str] = []
        link_parts: List[str] = []
        pending = [system_use]
        hops = 0
        while pending and hops < 16:
            area = pending.pop(0)
            hops += 1
            pos = 0
            while pos + 4 <= len(area):
                sig = area[pos:pos + 2]
                length = area[pos + 2]
                if length < 4:
                    break
                body = area[pos + 4:pos + length]
                if sig == b"NM" and body:
                    flags = body[0]
                    if not flags & 0x06:              # . / .. degil
                        name_parts.append(body[1:].decode("utf-8", "replace"))
                elif sig == b"PX" and len(body) >= 8:
                    entry.mode = struct.unpack_from("<I", body, 1)[0]
                elif sig == b"SL" and body:
                    link_parts.append(_sl_components(body[1:]))
                elif sig == b"CE" and len(body) >= 25:
                    blk = struct.unpack_from("<I", body, 1)[0]
                    off = struct.unpack_from("<I", body, 9)[0]
                    ln = struct.unpack_from("<I", body, 17)[0]
                    raw = self.dev.read(blk * BLOCK + off, ln)
                    pending.append(raw)
                elif sig == b"ST":
                    break
                pos += length
        if name_parts:
            entry.name = "".join(name_parts)
        if link_parts:
            entry.symlink = "/".join(p for p in link_parts if p is not None)

    def _entries(self, extent: int, size: int) -> List[IsoEntry]:
        out: List[IsoEntry] = []
        for rec in self._records(extent, size):
            name_len = rec[32]
            ident = rec[33:33 + name_len]
            if ident in (b"\x00", b"\x01"):
                continue                            # . ve ..
            flags = rec[25]
            ext = struct.unpack_from("<I", rec, 2)[0]
            length = struct.unpack_from("<I", rec, 10)[0]
            if self.joliet:
                name = ident.decode("utf-16-be", "replace")
            else:
                name = ident.decode("latin-1", "replace")
            if not flags & 0x02 and ";" in name:
                name = name.rsplit(";", 1)[0]
            if not flags & 0x02 and name.endswith("."):
                name = name[:-1]
            entry = IsoEntry(name=name, is_dir=bool(flags & 0x02), size=length,
                             extents=[(ext, length)], mtime=_date7(rec[18:25]),
                             hidden=bool(flags & 0x01))
            if self.rock_ridge:
                su = 33 + name_len + (1 - name_len % 2) + self._susp_skip
                self._parse_rr(rec[su:], entry)
            # Cok parcali dosya: onceki kayit "devami var" dediyse birlestir
            if out and out[-1].name == entry.name and getattr(out[-1], "_more", False):
                prev = out[-1]
                prev.extents.append((ext, length))
                prev.size += length
                prev._more = bool(flags & 0x80)       # type: ignore[attr-defined]
                continue
            entry._more = bool(flags & 0x80)          # type: ignore[attr-defined]
            out.append(entry)
        return out

    # ------------------------------------------------------------------
    def root(self) -> IsoEntry:
        ext = struct.unpack_from("<I", self._root, 2)[0]
        size = struct.unpack_from("<I", self._root, 10)[0]
        return IsoEntry(name="", is_dir=True, size=size, extents=[(ext, size)])

    def listdir(self, path: str = "/") -> List[IsoEntry]:
        key = "/" + path.strip("/")
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        node = self.resolve(path)
        if not node.is_dir:
            raise IsoError(tr("Dizin degil: {}", path))
        entries: List[IsoEntry] = []
        for ext, size in node.extents:
            entries.extend(self._entries(ext, size))
        self._cache[key] = entries
        return entries

    def resolve(self, path: str) -> IsoEntry:
        node = self.root()
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        walked = ""
        for part in parts:
            children = self.listdir(walked or "/")
            match = next((c for c in children if c.name == part), None)
            if match is None:
                lower = part.lower()
                match = next((c for c in children if c.name.lower() == lower), None)
            if match is None:
                raise IsoError(tr("Bulunamadi: {}", path))
            node = match
            walked += "/" + match.name
        return node

    def read_chunks(self, entry: IsoEntry, chunk: int = 4 * 1024 * 1024):
        for ext, size in entry.extents:
            pos = 0
            while pos < size:
                n = min(chunk, size - pos)
                yield self.dev.read(ext * BLOCK + pos, n)
                pos += n

    def read_file(self, path: str, max_bytes: int = -1) -> bytes:
        entry = self.resolve(path)
        if entry.is_dir:
            raise IsoError(tr("Dizin okunamaz: {}", path))
        out = bytearray()
        for piece in self.read_chunks(entry):
            out += piece
            if 0 <= max_bytes <= len(out):
                return bytes(out[:max_bytes])
        return bytes(out)

    def extract(self, path: str, dest: str) -> str:
        entry = self.resolve(path)
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        with open(dest, "wb") as fh:
            for piece in self.read_chunks(entry):
                fh.write(piece)
        return dest

    def stats(self) -> Dict[str, int]:
        total = self.volume_blocks * BLOCK
        return {"total_bytes": total, "used_bytes": total, "free_bytes": 0,
                "cluster_size": BLOCK}


def _sl_components(body: bytes) -> Optional[str]:
    """SL bilesenleri: bayrak 0x02 '.', 0x04 '..', 0x08 kok."""
    parts: List[str] = []
    pos = 0
    while pos + 2 <= len(body):
        flags, length = body[pos], body[pos + 1]
        text = body[pos + 2:pos + 2 + length].decode("utf-8", "replace")
        if flags & 0x08:
            parts.append("")
        elif flags & 0x02:
            parts.append(".")
        elif flags & 0x04:
            parts.append("..")
        else:
            parts.append(text)
        pos += 2 + length
    return "/".join(parts)
