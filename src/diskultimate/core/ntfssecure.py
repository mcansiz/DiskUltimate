"""NTFS guvenlik tanimlayicilari: devralma ve `$Secure` kaydi.

Neden (2026-09-29, Windows'ta olculdu): yazicinin olusturdugu her dosya ve
klasorde `$STANDARD_INFORMATION` guvenlik kimligi 0'di. NTFS 3.x'te kimlik
`$Secure`daki bir tanimlayiciyi gostermek zorundadir; Windows bu ogeleri
"erisim engellendi" ile acmiyordu. ntfs-3g izin denetlemedigi icin Linux'ta
fark edilmemisti.

Windows (ve ntfs-3g) yeni bir oge icin ust dizinin tanimlayicisindan
**devralinan** bir tanimlayici uretir, `$Secure`da aynisi varsa kimligini
yeniden kullanir, yoksa ekler:

  * `$SDS` akisi: [karma, kimlik, ofset, uzunluk] + tanimlayici, 16 bayta
    hizali; her 256 KiB'lik blok bir sonraki 256 KiB'de aynalanir, giris
    blok sinirini asmaz.
  * `$SII`: kimlik -> $SDS konumu (ULONG siralamasi)
  * `$SDH`: (karma, kimlik) -> $SDS konumu (SECURITY_HASH siralamasi)

Devralma (Windows kurallari, olculen ornekle birebir):
  * dosya: ust ACL'deki OBJECT_INHERIT girisleri; genel haklar (GENERIC_*)
    dosya haklarina cevrilir, devralma bayraklari kalkar.
  * dizin: CONTAINER_INHERIT girisleri etkin (cevrilmis) haliyle + asagiya
    aktarilmak uzere INHERIT_ONLY kopyasi; yalniz OBJECT_INHERIT olanlar
    INHERIT_ONLY kopya olarak.
  * ust tanimlayicida SE_DACL_AUTO_INHERITED varsa girisler INHERITED_ACE
    bayragi alir.
"""
from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

from .ntfs import SDS_MIRROR, security_hash
from .ntfsindex import DirIndex, Entry
from .ntfsread import AT_DATA, AT_STANDARD_INFORMATION, NtfsError

SECURE_RECORD = 9
AT_SECURITY_DESCRIPTOR = 0x50

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
GENERIC_EXECUTE, GENERIC_ALL = 0x20000000, 0x10000000
FILE_GENERIC_READ, FILE_GENERIC_WRITE = 0x120089, 0x120116
FILE_GENERIC_EXECUTE, FILE_ALL_ACCESS = 0x1200A0, 0x1F01FF

OBJECT_INHERIT, CONTAINER_INHERIT = 0x01, 0x02
NO_PROPAGATE, INHERIT_ONLY, INHERITED = 0x04, 0x08, 0x10
SE_DACL_PRESENT, SE_DACL_AUTO_INHERITED, SE_SELF_RELATIVE = 0x0004, 0x0400, 0x8000

CREATOR_OWNER = b"\x01\x01\x00\x00\x00\x00\x00\x03\x00\x00\x00\x00"
CREATOR_GROUP = b"\x01\x01\x00\x00\x00\x00\x00\x03\x01\x00\x00\x00"


def map_generic(mask: int) -> int:
    out = mask & 0x0FFFFFFF
    if mask & GENERIC_READ:
        out |= FILE_GENERIC_READ
    if mask & GENERIC_WRITE:
        out |= FILE_GENERIC_WRITE
    if mask & GENERIC_EXECUTE:
        out |= FILE_GENERIC_EXECUTE
    if mask & GENERIC_ALL:
        out |= FILE_ALL_ACCESS
    return out


def _sid_at(sd: bytes, off: int) -> bytes:
    n = sd[off + 1]
    return bytes(sd[off:off + 8 + 4 * n])


def parse_sd(sd: bytes):
    """(kontrol, sahip SID, grup SID, [(tur, bayrak, maske, SID)] ya da None)."""
    _rev, _sbz, ctrl, o_off, g_off, _s_off, d_off = struct.unpack_from("<BBHIIII", sd, 0)
    owner = _sid_at(sd, o_off) if o_off else b""
    group = _sid_at(sd, g_off) if g_off else b""
    aces = None
    if d_off and ctrl & SE_DACL_PRESENT:
        aces = []
        _r, _z, _size, count, _z2 = struct.unpack_from("<BBHHH", sd, d_off)
        pos = d_off + 8
        for _ in range(count):
            a_type, a_flags, a_size = struct.unpack_from("<BBH", sd, pos)
            mask = struct.unpack_from("<I", sd, pos + 4)[0]
            aces.append((a_type, a_flags, mask, bytes(sd[pos + 8:pos + a_size])))
            pos += a_size
    return ctrl, owner, group, aces


def build_sd(ctrl: int, owner: bytes, group: bytes,
             aces: List[Tuple[int, int, int, bytes]]) -> bytes:
    """Kendine goreli (self-relative) tanimlayici: baslik, DACL, sahip, grup."""
    acl = bytearray(8)
    for a_type, a_flags, mask, sid in aces:
        size = 8 + len(sid)
        acl += struct.pack("<BBHI", a_type, a_flags, size, mask) + sid
    struct.pack_into("<BBHHH", acl, 0, 2, 0, len(acl), len(aces), 0)
    d_off = 20
    o_off = d_off + len(acl)
    g_off = o_off + len(owner)
    head = struct.pack("<BBHIIII", 1, 0, ctrl | SE_SELF_RELATIVE | SE_DACL_PRESENT,
                       o_off if owner else 0, g_off if group else 0, 0, d_off)
    return head + bytes(acl) + owner + group


def inherit(parent_sd: bytes, is_dir: bool) -> bytes:
    ctrl, owner, group, aces = parse_sd(parent_sd)
    auto = ctrl & SE_DACL_AUTO_INHERITED
    inh = INHERITED if auto else 0
    out: List[Tuple[int, int, int, bytes]] = []
    for a_type, flags, mask, sid in (aces or []):
        if a_type not in (0, 1):            # yalnizca izin/ret girisleri
            continue
        eff_sid = owner if sid == CREATOR_OWNER and owner else \
            (group if sid == CREATOR_GROUP and group else sid)
        if not is_dir:
            if flags & OBJECT_INHERIT:
                out.append((a_type, inh, map_generic(mask), eff_sid))
            continue
        if flags & CONTAINER_INHERIT:
            eff = map_generic(mask)
            if eff == mask and eff_sid == sid and not flags & NO_PROPAGATE:
                out.append((a_type, (flags & 0x03) | inh, mask, sid))
                continue
            out.append((a_type, inh, eff, eff_sid))
            if not flags & NO_PROPAGATE:
                out.append((a_type, (flags & 0x03) | INHERIT_ONLY | inh, mask, sid))
        elif flags & OBJECT_INHERIT and not flags & NO_PROPAGATE:
            out.append((a_type, OBJECT_INHERIT | INHERIT_ONLY | inh, mask, sid))
    if not out and aces:
        # devralinabilir giris yok: ust ACL oldugu gibi (erisim kaybolmasin)
        out = [(t, f & ~(OBJECT_INHERIT | CONTAINER_INHERIT | INHERIT_ONLY
                         | NO_PROPAGATE), m, s) for t, f, m, s in aces
               if not f & INHERIT_ONLY]
    return build_sd(SE_DACL_AUTO_INHERITED if auto else 0, owner, group, out)


def _sid(*parts: int) -> bytes:
    """S-1-5-x-y... SID baytlari (yetki 5 = NT AUTHORITY)."""
    authority, subs = parts[0], parts[1:]
    return struct.pack("<BB", 1, len(subs)) + authority.to_bytes(6, "big") + \
        b"".join(struct.pack("<I", x) for x in subs)


SID_SYSTEM = _sid(5, 18)
SID_ADMINS = _sid(5, 32, 544)
SID_USERS = _sid(5, 32, 545)
SID_AUTH_USERS = _sid(5, 11)

# mkntfs'in kok dizine koydugu tanimlayicinin aynisi (olculdu). Ust dizinde
# hic tanimlayici yoksa (bizim bicimlendiricinin eski birimleri) yeni ogeler
# bunu "ust" kabul ederek devralir: Windows'ta erisilebilir kalirlar.
DEFAULT_PARENT_SD = build_sd(0, SID_SYSTEM, SID_SYSTEM, [
    (0, 0x00, 0x1F01FF, SID_ADMINS), (0, 0x0B, GENERIC_ALL, SID_ADMINS),
    (0, 0x00, 0x1F01FF, SID_SYSTEM), (0, 0x0B, GENERIC_ALL, SID_SYSTEM),
    (0, 0x00, 0x1301BF, SID_AUTH_USERS),
    (0, 0x0B, GENERIC_READ | GENERIC_WRITE | GENERIC_EXECUTE | 0x10000, SID_AUTH_USERS),
    (0, 0x00, 0x1200A9, SID_USERS),
    (0, 0x0B, GENERIC_READ | GENERIC_EXECUTE, SID_USERS),
])


class SecureStore:
    """`$Secure` uzerinde okuma ve tanimlayici ekleme."""

    def __init__(self, writer):
        self.w = writer
        self.fs = writer.fs
        self._cache: Dict[Tuple[int, bool], int] = {}

    # -- indeksler ------------------------------------------------------
    def _sii(self) -> DirIndex:
        return DirIndex(self.w, SECURE_RECORD, "$SII",
                        collate=lambda k: struct.unpack_from("<I", k)[0], view=True)

    def _sdh(self) -> DirIndex:
        return DirIndex(self.w, SECURE_RECORD, "$SDH",
                        collate=lambda k: struct.unpack_from("<II", k), view=True)

    def _sds(self):
        attr = self.fs.record(SECURE_RECORD).find(AT_DATA, "$SDS")
        if attr is None:
            raise NtfsError("$SDS yok")
        return attr

    def available(self) -> bool:
        try:
            rec = self.fs.record(SECURE_RECORD)
            return rec.find(AT_DATA, "$SDS") is not None and \
                rec.find(0x90, "$SII") is not None
        except NtfsError:
            return False

    # -- okuma ----------------------------------------------------------
    def read_id(self, sec_id: int) -> Optional[bytes]:
        e = self._sii().find(struct.pack("<I", sec_id))
        if e is None:
            return None
        _h, _i, offset, length = struct.unpack_from("<IIQI", e.data)
        raw = self.fs.read_attribute_range(self._sds(), offset, length)
        return bytes(raw[20:length])

    def descriptor_of(self, rec_no: int) -> Optional[bytes]:
        rec = self.fs.record(rec_no)
        si = rec.find(AT_STANDARD_INFORMATION)
        if si is not None and len(si.value) >= 0x38:
            sid = struct.unpack_from("<I", si.value, 0x34)[0]
            if sid:
                sd = self.read_id(sid)
                if sd:
                    return sd
        attr = rec.find(AT_SECURITY_DESCRIPTOR)
        if attr is not None:
            return bytes(self.fs.read_attribute(attr))
        return None

    # -- ekleme ---------------------------------------------------------
    def ensure_id(self, sd: bytes) -> int:
        h = security_hash(sd)
        for e in self._sdh().scan((h, 0), (h, 0xFFFFFFFF)):
            _hh, sec_id, offset, length = struct.unpack_from("<IIQI", e.data)
            raw = self.fs.read_attribute_range(self._sds(), offset + 20, length - 20)
            if bytes(raw) == sd:
                return sec_id
        entries = self._sii().scan(0, 0xFFFFFFFF)
        new_id = max([struct.unpack_from("<I", e.key)[0] for e in entries] + [0x100]) + 1
        used_end = 0
        for e in entries:
            _hh, _i, offset, length = struct.unpack_from("<IIQI", e.data)
            used_end = max(used_end, offset + length)
        total = 20 + len(sd)
        offset = (used_end + 15) & ~15
        block = offset // SDS_MIRROR
        if block % 2 == 1 or (offset + total - 1) // SDS_MIRROR != block:
            # tek (ayna) bloga ya da sinir asimina dusmesin: sonraki cift blok
            block = block + 1 if block % 2 == 1 else block + 2
            offset = block * SDS_MIRROR
        header = struct.pack("<IIQI", h, new_id, offset, total)
        payload = header + sd
        self._write_sds(offset, payload)
        self._sii().insert(Entry(0, struct.pack("<I", new_id), None, header))
        self._sdh().insert(Entry(0, struct.pack("<II", h, new_id), None, header))
        return new_id

    def _write_sds(self, offset: int, payload: bytes) -> None:
        """Birincil ve ayna kopyayi yazar; gerekirse akisi buyutur."""
        attr = self._sds()
        need = offset + SDS_MIRROR + len(payload)
        runs = list(attr.runs)
        allocated = sum(c for _l, c in runs) * self.fs.cluster_size
        if need > allocated:
            extra = (need - allocated + self.fs.cluster_size - 1) // self.fs.cluster_size
            for lcn, count in self.w.alloc_clusters(extra):
                self.fs.dev.write(lcn * self.fs.cluster_size,
                                  b"\x00" * (count * self.fs.cluster_size))
                if runs and runs[-1][0] + runs[-1][1] == lcn:
                    runs[-1] = (runs[-1][0], runs[-1][1] + count)
                else:
                    runs.append((lcn, count))
        if need > attr.data_size or runs != list(attr.runs):
            old_size = attr.data_size
            size = max(need, attr.data_size)
            self.w._set_record_attr(SECURE_RECORD, AT_DATA, "$SDS",
                                    self.w._nonresident_attr(AT_DATA, runs, size,
                                                             name="$SDS"))
            self.fs._cache.pop(SECURE_RECORD, None)
            attr = self._sds()
            if size > old_size:
                # baslatilmis alan buyudu: aradaki eski veri gecerli sayilmasin
                self.w._write_attr_range(attr, b"\x00" * (size - old_size), old_size)
        self.w._write_attr_range(attr, payload, offset)
        self.w._write_attr_range(attr, payload, offset + SDS_MIRROR)

    # -- yuksek duzey ---------------------------------------------------
    def id_for_new(self, parent_no: int, is_dir: bool) -> int:
        key = (parent_no, is_dir)
        if key in self._cache:
            return self._cache[key]
        parent_sd = self.descriptor_of(parent_no)
        if parent_sd is None and parent_no != 5:
            parent_sd = self.descriptor_of(5)
        if parent_sd is None:
            parent_sd = DEFAULT_PARENT_SD
        sec_id = self.ensure_id(inherit(parent_sd, is_dir))
        self._cache[key] = sec_id
        return sec_id
