"""Windows NT Registry (regf) hive salt-okur çözümleyici — saf Python.

Yalnızca SAM kütüğünde gezinmek ve küçük, yerinde düzenlemeler (parola
boşaltma, hesap etkinleştirme) için gereken kadarıdır (ADR 0100): base blok →
kök nk → alt anahtarlar (lf/lh/li/ri) → değerler (vk). Transaction log
uygulanmaz; birincil/ikincil sıra numarası farklıysa kütük tutarsızdır.

Byte düzeni libregf REGF belgesine göredir. Çalışma zamanı bağımlılığı yok.
"""
from __future__ import annotations

import struct
from typing import Iterator, List, Optional, Tuple

from ..i18n import tr

HBIN_BASE = 4096


class RegfError(Exception):
    """regf çözümleme hatası."""


class Hive:
    """Bir regf kütüğü (tüm dosya belleğe alınır; düzenleme yerinde yapılır)."""

    def __init__(self, data: bytes):
        self.data = bytearray(data)
        if self.data[0:4] != b"regf":
            raise RegfError(tr("Geçerli bir registry kütüğü değil (regf yok)."))
        seq1 = struct.unpack_from("<I", self.data, 4)[0]
        seq2 = struct.unpack_from("<I", self.data, 8)[0]
        if seq1 != seq2:
            raise RegfError(tr("Kütük tutarsız (işlem günlüğü uygulanmamış)."))
        self.root_off = struct.unpack_from("<I", self.data, 36)[0]

    # -- hücre erişimi -----------------------------------------------------
    def _cell_data(self, rel_off: int) -> int:
        """Referans ofsetinden (hbin verisine göre) hücre verisinin DOSYA
        ofsetini döndürür (4 baytlık boyut alanı atlanır)."""
        pos = HBIN_BASE + rel_off
        if pos + 4 > len(self.data):
            raise RegfError(tr("Kütük sınırı dışında hücre."))
        return pos + 4

    def _nk(self, rel_off: int) -> int:
        d = self._cell_data(rel_off)
        if self.data[d:d + 2] != b"nk":
            raise RegfError(tr("nk kaydı bekleniyordu."))
        return d

    def root(self) -> int:
        return self._nk(self.root_off)

    # -- alt anahtarlar ----------------------------------------------------
    def subkeys(self, nk: int) -> List[Tuple[int, int]]:
        n = struct.unpack_from("<I", self.data, nk + 20)[0]
        lst = struct.unpack_from("<i", self.data, nk + 28)[0]
        if n == 0 or lst == -1 or lst == 0xFFFFFFFF:
            return []
        return list(self._iter_list(lst))

    def _iter_list(self, rel_off: int) -> Iterator[Tuple[int, int]]:
        d = self._cell_data(rel_off)
        sig = bytes(self.data[d:d + 2])
        count = struct.unpack_from("<H", self.data, d + 2)[0]
        if sig in (b"lf", b"lh"):
            for i in range(count):
                child = struct.unpack_from("<i", self.data, d + 4 + i * 8)[0]
                yield self._nk(child), child
        elif sig == b"li":
            for i in range(count):
                child = struct.unpack_from("<i", self.data, d + 4 + i * 4)[0]
                yield self._nk(child), child
        elif sig == b"ri":
            for i in range(count):
                sub = struct.unpack_from("<i", self.data, d + 4 + i * 4)[0]
                yield from self._iter_list(sub)
        else:
            raise RegfError(tr("Bilinmeyen alt anahtar listesi: {}", sig))

    def nk_name(self, nk: int) -> str:
        flags = struct.unpack_from("<H", self.data, nk + 2)[0]
        nlen = struct.unpack_from("<H", self.data, nk + 72)[0]
        raw = bytes(self.data[nk + 76:nk + 76 + nlen])
        if flags & 0x0020:
            return raw.decode("latin-1", "replace")
        return raw.decode("utf-16-le", "replace")

    def subkey(self, nk: int, name: str) -> Optional[int]:
        low = name.lower()
        for child, _ in self.subkeys(nk):
            if self.nk_name(child).lower() == low:
                return child
        return None

    def find(self, path: str) -> Optional[int]:
        nk = self.root()
        for part in path.replace("/", "\\").split("\\"):
            if not part:
                continue
            nk = self.subkey(nk, part)
            if nk is None:
                return None
        return nk

    # -- değerler ----------------------------------------------------------
    def values(self, nk: int) -> Iterator[Tuple[str, bytes, int, int]]:
        n = struct.unpack_from("<I", self.data, nk + 36)[0]
        voff = struct.unpack_from("<i", self.data, nk + 40)[0]
        if n == 0 or voff == -1 or voff == 0xFFFFFFFF:
            return
        ld = self._cell_data(voff)
        for i in range(n):
            vk = struct.unpack_from("<i", self.data, ld + i * 4)[0]
            yield self._vk(vk)

    def _vk(self, rel_off: int) -> Tuple[str, bytes, int, int]:
        d = self._cell_data(rel_off)
        if self.data[d:d + 2] != b"vk":
            raise RegfError(tr("vk kaydı bekleniyordu."))
        nlen = struct.unpack_from("<H", self.data, d + 2)[0]
        dsize = struct.unpack_from("<I", self.data, d + 4)[0]
        doff = struct.unpack_from("<i", self.data, d + 8)[0]
        flags = struct.unpack_from("<H", self.data, d + 16)[0]
        name = bytes(self.data[d + 20:d + 20 + nlen]).decode(
            "latin-1" if flags & 0x0001 else "utf-16-le", "replace")
        size = dsize & 0x7FFFFFFF
        if dsize & 0x80000000:            # satır içi veri (data offset alanında)
            data_off = d + 8
        else:
            data_off = self._cell_data(doff)
        return name, bytes(self.data[data_off:data_off + size]), data_off, size

    def value(self, nk: int, name: str) -> Optional[Tuple[bytes, int, int]]:
        """(veri, dosya_ofseti, boy) ya da None. Dosya ofseti yerinde
        düzenleme için kullanılır."""
        low = name.lower()
        for vn, data, off, size in self.values(nk):
            if vn.lower() == low:
                return data, off, size
        return None

    def patch(self, file_off: int, raw: bytes) -> None:
        """Kütük baytlarını yerinde değiştirir (boyut değişmez)."""
        self.data[file_off:file_off + len(raw)] = raw

    def to_bytes(self) -> bytes:
        return bytes(self.data)
