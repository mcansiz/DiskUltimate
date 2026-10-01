"""UDF yazma — saf Python (ADR 0065).

Kapsam: tek **fiziksel** bolum + ayrilmamis alan bitmap'i, ustune yazilabilir
erisim. Bu, sabit disk/USB UDF'sinin yerlesimidir: bizim bicimlendiricimiz,
mkudffs `-m hd` ve Windows'un `format /fs:UDF` ciktisi boyledir. Yedekli
(CD-RW), sanal (VAT, CD-R) ve metadata bolumlu birimler salt okunur kalir.

Kurallar (ECMA-167 4. bolum, OSTA UDF 2.01):
  * UDF 2.00+ birimde dosya girisi EFE (266), eskilerde FE (261).
  * Kucuk veri girisin icine gomulur (kapsam turu 3); buyuk veri kisa
    kapsamlarla (short_ad), girise sigmayan kapsamlar AED (258) zinciriyle.
  * Dizin verisi FID dizisidir; FID'ler blok sinirini gecebilir (Windows da
    boyle yazar, olculdu). FID etiket konumu FID'in basladigi mantiksal blok.
    FID'deki ICB'nin uygulama alani benzersiz kimligin alt 32 bitini tasir.
  * Dizin bag sayisi 1 + alt dizin sayisi; ".." FID'i ust dizini gosterir.
  * Degisiklik boyunca LVID "acik" (0), `flush`ta "kapali" (1); bos alan,
    dosya/dizin sayilari ve sonraki benzersiz kimlik LVID'de tutulur.
  * Silmede bag sayisi > 1 ise yalnizca FID kalkar; akis dizini ve genis
    oznitelik girisleri de serbest birakilir.
"""
from __future__ import annotations

import datetime
import re
import struct
from typing import Dict, List, Optional, Tuple

from .udf import (ALLOC_EMBEDDED, ALLOC_LONG, ALLOC_SHORT, EXTENT_NEXT,
                  FID_DELETED, FID_DIRECTORY, FID_PARENT, FT_DIRECTORY, FT_FILE, TAG_EFE,
                  TAG_FE, TAG_FID, TAG_LVID, TAG_TD, UdfEntry, UdfError, UdfFS,
                  cs0)
from .udfformat import cs0_encode, make_tag, regid, timestamp
from ..i18n import tr

TAG_AED = 258
TAG_SBD = 264
IMPL_ID = b"*DiskUltimate"
PERM_FILE = 0x7884          # sahip rw + oznitelik/silme, grup r, diger r
PERM_DIR = 0x7CA5           # sahip rwx + oznitelik/silme, grup r-x, diger r-x
ICB_ARCHIVE = 0x20
MAX_EXTENT = (1 << 30) - 4096
ACCESS_OVERWRITABLE = 4
FIRST_UNIQUE_ID = 16


class UdfWriteError(UdfError):
    pass


class UdfWriter:
    def __init__(self, fs: UdfFS):
        self.fs = fs
        self.dev = fs.dev
        self.bs = fs.block_size
        self.ref = next((i for i, p in enumerate(fs.partitions) if p.kind == "physical"), -1)
        self._bitmap: Optional[bytearray] = None
        self._sbd_raw: Optional[bytearray] = None
        self._dirty = False
        self._began = False
        self._lvid_loc = -1
        self._counts = [0, 0]                      # dosya, dizin degisimi
        self.efe = fs.revision >= 0x200

    # ---- izin ---------------------------------------------------------------
    def write_support(self) -> Tuple[bool, str]:
        fs = self.fs
        if getattr(self.dev, "readonly", False):
            return False, tr("Goruntu salt okunur acildi")
        if len(fs.partitions) != 1 or self.ref < 0:
            return False, tr("Bu UDF birimi (yedekli, sanal ya da metadata bolumu) "
                             "bu surumde salt okunur acilir.")
        part = fs.partitions[self.ref]
        detail = fs.pd_detail.get(part.number, {})
        if detail.get("access") != ACCESS_OVERWRITABLE:
            return False, tr("UDF bolumu ustune yazilabilir degil (erisim turu {})",
                             detail.get("access"))
        if not detail.get("space_bitmap", (0, 0))[0]:
            return False, tr("UDF bolumunde alan bitmap'i yok; bu surumde yazilamaz.")
        if fs.domain_flags & 0x03:
            return False, tr("UDF birimi yazmaya karsi korunuyor")
        lvid = self._lvid()
        if lvid is None:
            return False, tr("UDF butunluk tanimlayicisi (LVID) bulunamadi")
        if not self._began and struct.unpack_from("<I", lvid, 28)[0] != 1:
            return False, tr("UDF birimi temiz kapatilmamis (butunluk acik); "
                             "once olusturan sistemde onarilmali.")
        return True, ""

    def _require(self) -> None:
        ok, reason = self.write_support()
        if not ok:
            raise UdfWriteError(reason)
        if not self._began:
            self._began = True
            lvid = bytearray(self._lvid())
            struct.pack_into("<I", lvid, 28, 0)            # acik
            self._write_lvid(lvid)

    # ---- LVID ------------------------------------------------------------------
    def _lvid(self) -> Optional[bytes]:
        length, loc = self.fs._lvid
        if not length:
            return None
        found = None
        for i in range(max(1, length // self.bs)):
            d = self.fs._read_block(loc + i)
            tag = struct.unpack_from("<H", d, 0)[0]
            if tag == TAG_LVID:
                found = d
                self._lvid_loc = loc + i
            elif tag == TAG_TD or tag == 0:
                break
        return found

    def _write_lvid(self, lvid: bytearray) -> None:
        crc_len = struct.unpack_from("<H", lvid, 10)[0]
        size = 16 + crc_len
        body = bytearray(lvid[:size])
        self.dev.write(self._lvid_loc * self.bs,
                       make_tag(body, TAG_LVID, self._lvid_loc).ljust(self.bs, b"\x00"))

    def _next_unique(self) -> int:
        lvid = bytearray(self._lvid())
        uid = max(FIRST_UNIQUE_ID, struct.unpack_from("<Q", lvid, 40)[0])
        nxt = uid + 1
        if (nxt & 0xFFFFFFFF) < FIRST_UNIQUE_ID:          # alt 32 bit 0-15 ayrilmis
            nxt = (nxt & ~0xFFFFFFFF) + FIRST_UNIQUE_ID
        struct.pack_into("<Q", lvid, 40, nxt)
        self._write_lvid(lvid)
        return uid

    # ---- bitmap -------------------------------------------------------------------
    def _load_bitmap(self) -> bytearray:
        if self._bitmap is None:
            part = self.fs.partitions[self.ref]
            length, pos = self.fs.pd_detail[part.number]["space_bitmap"]
            raw = bytearray(self.fs._read_logical(pos, self.ref,
                                                  (length + self.bs - 1) // self.bs))
            if struct.unpack_from("<H", raw, 0)[0] != TAG_SBD:
                raise UdfWriteError(tr("UDF alan bitmap'i bozuk"))
            nbytes = struct.unpack_from("<I", raw, 20)[0]
            self._sbd_raw = raw
            self._sbd_pos = pos
            self._bits = struct.unpack_from("<I", raw, 16)[0]
            self._bitmap = bytearray(raw[24:24 + nbytes])
        return self._bitmap

    def _is_free(self, b: int) -> bool:
        return bool(self._load_bitmap()[b >> 3] & (1 << (b & 7)))

    def _mark(self, start: int, count: int, used: bool) -> None:
        bm = self._load_bitmap()
        for b in range(start, start + count):
            if used:
                bm[b >> 3] &= ~(1 << (b & 7)) & 0xFF
            else:
                bm[b >> 3] |= 1 << (b & 7)
        self._dirty = True

    def free_count(self) -> int:
        bm = self._load_bitmap()
        total = sum(bin(x).count("1") for x in bm)
        # son bayttaki gecersiz bitler zaten 0
        return total

    def _find_contiguous(self, count: int, goal: int) -> int:
        bm = self._load_bitmap()
        total = self._bits
        if goal + count <= total and all(self._is_free(b) for b in range(goal, goal + count)):
            return goal
        need = max(1, (count + 7) // 8)
        pattern = re.compile(b"\xff{%d}" % need)
        for first in (goal >> 3, 0):
            m = pattern.search(bm, max(0, first))
            if not m:
                continue
            start = m.start() * 8
            back = 0
            while back < 7 and start > 0 and self._is_free(start - 1):
                start -= 1
                back += 1
            if start + count <= total:
                return start
        return -1

    def alloc(self, count: int, goal: int = 0) -> List[Tuple[int, int]]:
        if count <= 0:
            return []
        start = self._find_contiguous(count, goal)
        if start >= 0:
            self._mark(start, count, True)
            return [(start, count)]
        runs: List[Tuple[int, int]] = []
        bm = self._load_bitmap()
        for m in re.finditer(rb"[^\x00]+", bytes(bm)):
            run = -1
            for b in range(m.start() * 8, min(m.end() * 8, self._bits)):
                if self._is_free(b):
                    if run < 0:
                        run = b
                elif run >= 0:
                    runs.append((run, b - run))
                    run = -1
            if run >= 0:
                runs.append((run, min(m.end() * 8, self._bits) - run))
        if sum(c for _s, c in runs) < count:
            raise UdfWriteError(tr("UDF biriminde yeterli bos alan yok"))
        out: List[Tuple[int, int]] = []
        left = count
        for s0, c in sorted(runs, key=lambda r: -r[1]):
            take = min(c, left)
            self._mark(s0, take, True)
            out.append((s0, take))
            left -= take
            if not left:
                break
        return sorted(out)

    def free(self, runs: List[Tuple[int, int]]) -> None:
        for s0, c in runs:
            if c:
                self._mark(s0, c, False)

    # ---- blok G/C ---------------------------------------------------------------
    def _write_logical(self, block: int, data: bytes) -> None:
        if len(data) % self.bs:
            data = data + bytes(self.bs - len(data) % self.bs)
        part = self.fs.partitions[self.ref]
        if part.kind == "physical":             # ardisik: tek yazim
            self.dev.write((part.start + block) * self.bs, data)
            return
        for i in range(len(data) // self.bs):
            phys = self.fs._physical(block + i, self.ref)
            self.dev.write(phys * self.bs, data[i * self.bs:(i + 1) * self.bs])

    # ---- giris olusturma ---------------------------------------------------------
    def _entry_header(self) -> int:
        return 216 if self.efe else 176

    def _max_ads(self) -> int:
        return (self.bs - self._entry_header()) // 8

    def _build_entry(self, loc: int, ftype: int, unique: int, size: int,
                     alloc_type: int, ads: bytes, links: int, recorded: int,
                     perms: int, flags_extra: int = 0,
                     times: Optional[bytes] = None,
                     keep: Optional[bytes] = None) -> bytes:
        """EFE/FE; `keep` verilirse (mevcut giris) kimlik/izin/tarih alanlari
        korunur ve giris turu (FE/EFE) mevcut giristen alinir."""
        efe = self.efe if keep is None else \
            struct.unpack_from("<H", keep, 0)[0] == TAG_EFE
        head = 216 if efe else 176
        d = bytearray(head) + ads
        stamp = times or timestamp()
        if keep is not None:
            d[16:head] = keep[16:head]
        else:
            struct.pack_into("<IHHHBB", d, 16, 0, 4, 0, 1, 0, ftype)
            struct.pack_into("<III", d, 36, 0xFFFFFFFF, 0xFFFFFFFF, perms)
        flags = struct.unpack_from("<H", d, 34)[0]
        flags = (flags & ~0x0007) | alloc_type | flags_extra
        struct.pack_into("<H", d, 34, flags)
        struct.pack_into("<H", d, 48, links)
        if efe:
            struct.pack_into("<QQQ", d, 56, size, size, recorded)
            if keep is None:
                for off in (80, 104, 116):
                    d[off:off + 12] = stamp
                struct.pack_into("<I", d, 128, 1)
                struct.pack_into("<Q", d, 200, unique)
            d[92:104] = stamp                                  # degistirme
            d[168:200] = regid(IMPL_ID)
            struct.pack_into("<II", d, 208, 0, len(ads))
        else:
            struct.pack_into("<QQ", d, 56, size, recorded)
            if keep is None:
                for off in (72, 96):
                    d[off:off + 12] = stamp
                struct.pack_into("<I", d, 108, 1)
                struct.pack_into("<Q", d, 160, unique)
            d[84:96] = stamp
            d[128:160] = regid(IMPL_ID)
            struct.pack_into("<II", d, 168, 0, len(ads))
        if len(d) > self.bs:
            raise UdfWriteError(tr("UDF dosya girisi bloga sigmiyor"))
        return make_tag(d, TAG_EFE if efe else TAG_FE, loc)

    def _store_data(self, data: bytes, goal: int, entry_loc: int
                    ) -> Tuple[int, bytes, int, List[Tuple[int, int]]]:
        """Veriyi yazar: (kapsam turu, AD baytlari, kayitli blok, ek bloklar).

        Ek bloklar (veri + AED) silmede serbest birakilacak her sey degildir;
        cagiran gerekirse geri almak icin kullanir.
        """
        bs = self.bs
        if len(data) <= self.bs - self._entry_header():
            return ALLOC_EMBEDDED, data, 0, []
        blocks = (len(data) + bs - 1) // bs
        runs = self.alloc(blocks, goal)
        taken = list(runs)
        try:
            pos = 0
            ads: List[bytes] = []
            for start, count in runs:
                chunk = data[pos:pos + count * bs]
                self._write_logical(start, chunk)
                # kapsam en fazla MAX_EXTENT bayt
                off = 0
                while off < len(chunk):
                    n = min(MAX_EXTENT, len(chunk) - off)
                    ads.append(struct.pack("<II", n, start + off // bs))
                    off += n
                pos += count * bs
            head, aeds = self._chain_ads(ads, entry_loc, self._max_ads())
            taken += aeds
            return ALLOC_SHORT, head, blocks, taken
        except Exception:
            self.free(taken)
            raise

    def _chain_ads(self, ads: List[bytes], entry_loc: int, limit: int
                   ) -> Tuple[bytes, List[Tuple[int, int]]]:
        """Kapsamlar girise sigmazsa AED zinciri kurar: (giristeki AD'ler, AED bloklari)."""
        if len(ads) <= limit:
            return b"".join(ads), []
        bs = self.bs
        first = ads[:limit - 1]
        rest = ads[limit - 1:]
        per_aed = (bs - 24) // 8 - 1
        aeds = self.alloc((len(rest) + per_aed - 1) // per_aed, entry_loc + 1)
        aed_blocks = [s0 + i for s0, c in aeds for i in range(c)]
        prev = entry_loc
        for i, blk in enumerate(aed_blocks):
            body = b"".join(rest[i * per_aed:(i + 1) * per_aed])
            if i + 1 < len(aed_blocks):
                body += struct.pack("<II", (EXTENT_NEXT << 30) | bs, aed_blocks[i + 1])
            d = bytearray(24) + body
            struct.pack_into("<II", d, 16, prev, len(body))
            self._write_logical(blk, make_tag(d, TAG_AED, blk))
            prev = blk
        head = b"".join(first) + struct.pack("<II", (EXTENT_NEXT << 30) | bs,
                                             aed_blocks[0])
        return head, aeds

    # ---- dizin verisi -----------------------------------------------------------
    @staticmethod
    def _fid(characteristics: int, name: str, icb_block: int, icb_ref: int,
             unique: int, bs: int) -> bytearray:
        ident = cs0_encode(name) if name else b""
        if len(ident) > 255:
            raise UdfWriteError(tr("UDF adi cok uzun: {}", name))
        size = (38 + len(ident) + 3) & ~3
        fid = bytearray(size)
        struct.pack_into("<HBB", fid, 16, 1, characteristics, len(ident))
        struct.pack_into("<IIH", fid, 20, bs, icb_block, icb_ref)
        struct.pack_into("<HI", fid, 30, 0, unique & 0xFFFFFFFF)
        fid[38:38 + len(ident)] = ident
        return fid

    def _read_dir(self, entry: UdfEntry) -> List[bytearray]:
        data = self.fs._read_entry(entry)
        out: List[bytearray] = []
        pos = 0
        while pos + 38 <= len(data):
            if struct.unpack_from("<H", data, pos)[0] != TAG_FID:
                break
            l_fi = data[pos + 19]
            l_iu = struct.unpack_from("<H", data, pos + 36)[0]
            size = (38 + l_iu + l_fi + 3) & ~3
            # Silinmis FID'ler (Windows silmede sikistirmaz, bayrakla birakir;
            # ICB'si serbest bir blogu gosterebilir) yeniden yazimda atilir.
            if not data[pos + 18] & FID_DELETED:
                out.append(bytearray(data[pos:pos + size]))
            pos += size
        return out

    @staticmethod
    def _fid_name(fid: bytes) -> str:
        l_fi = fid[19]
        l_iu = struct.unpack_from("<H", fid, 36)[0]
        return cs0(bytes(fid[38 + l_iu:38 + l_iu + l_fi]))

    def _entry_raw(self, entry: UdfEntry) -> bytes:
        return self.fs._read_logical(*entry.icb)

    def _data_runs(self, raw: bytes) -> List[Tuple[int, int]]:
        """Girisin kapladigi tum bloklar (veri + AED), serbest birakmak icin."""
        data, aed = self._split_runs(raw)
        return data + aed

    def _split_runs(self, raw: bytes) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
        """(veri kapsamlari, AED bloklari) — sira korunur."""
        tag = struct.unpack_from("<H", raw, 0)[0]
        flags = struct.unpack_from("<H", raw, 34)[0]
        alloc_type = flags & 7
        if tag == TAG_FE:
            l_ea, alloc_len = struct.unpack_from("<II", raw, 168)
            base = 176
        else:
            l_ea, alloc_len = struct.unpack_from("<II", raw, 208)
            base = 216
        if alloc_type == ALLOC_EMBEDDED:
            return [], []
        ads = raw[base + l_ea:base + l_ea + alloc_len]
        size = {ALLOC_SHORT: 8, ALLOC_LONG: 16}.get(alloc_type)
        if size is None:
            raise UdfWriteError(tr("Bu kapsam turu yazmada desteklenmiyor: {}", alloc_type))
        out: List[Tuple[int, int]] = []
        aed_out: List[Tuple[int, int]] = []
        pos, guard = 0, 0
        while pos + size <= len(ads):
            raw_len = struct.unpack_from("<I", ads, pos)[0]
            length, kind = raw_len & 0x3FFFFFFF, raw_len >> 30
            blk = struct.unpack_from("<I", ads, pos + 4)[0]
            pos += size
            if not length:
                break
            nblocks = (length + self.bs - 1) // self.bs
            if kind == EXTENT_NEXT:
                aed_out.append((blk, 1))
                aed = self.fs._read_logical(blk, self.ref)
                n = struct.unpack_from("<I", aed, 20)[0]
                ads, pos = aed[24:24 + n], 0
                guard += 1
                if guard > 100000:
                    raise UdfWriteError(tr("UDF kapsam zinciri dongude"))
                continue
            if kind in (0, 1):                     # kayitli / ayrilmis
                out.append((blk, nblocks))
        return out, aed_out

    def _write_dir(self, entry: UdfEntry, fids: List[bytearray],
                   links_delta: int = 0) -> None:
        """Dizin verisini (FID'ler) ve dizin girisini yeniden yazar.

        Buyurken once bitisik uzatilir; olmazsa dizin bitisik yeni bir bolgeye
        tasinir; o da olmazsa parcali yazilir ve kapsamlar AED zincirine gider.
        """
        self._load_bitmap()
        raw = self._entry_raw(entry)
        old_data, old_aed = self._split_runs(raw)
        self.free(old_aed)                             # zincir her seferinde yeniden
        size = sum(len(f) for f in fids)
        loc = entry.icb[0]
        links = struct.unpack_from("<H", raw, 48)[0] + links_delta
        raw_efe = struct.unpack_from("<H", raw, 0)[0] == TAG_EFE
        head_size = 216 if raw_efe else 176
        if size <= self.bs - head_size:
            self.free(old_data)
            data = bytearray()
            for fid in fids:
                data += make_tag(fid, TAG_FID, loc, crc_len=len(fid) - 16)
            new = self._build_entry(loc, FT_DIRECTORY, 0, size, ALLOC_EMBEDDED,
                                    bytes(data), links, 0, PERM_DIR, keep=raw)
            self._write_logical(loc, new)
            return
        bs = self.bs
        need = (size + bs - 1) // bs
        blocks = [s0 + i for s0, c in old_data for i in range(c)]
        if len(blocks) > need:
            self.free([(b, 1) for b in blocks[need:]])
            blocks = blocks[:need]
        elif len(blocks) < need:
            add = need - len(blocks)
            goal = blocks[-1] + 1 if blocks else loc + 1
            if blocks and goal + add <= self._bits and all(
                    self._is_free(b) for b in range(goal, goal + add)):
                self._mark(goal, add, True)
                blocks += list(range(goal, goal + add))
            else:
                start = self._find_contiguous(need, goal)
                if start >= 0:
                    self._mark(start, need, True)
                    self.free([(b, 1) for b in blocks])
                    blocks = list(range(start, start + need))
                else:
                    for s0, c in self.alloc(add, goal):
                        blocks += list(range(s0, s0 + c))
        data = bytearray()
        for fid in fids:
            where = blocks[len(data) // bs]
            data += make_tag(fid, TAG_FID, where, crc_len=len(fid) - 16)
        runs: List[Tuple[int, int]] = []
        for b in blocks:
            if runs and runs[-1][0] + runs[-1][1] == b:
                runs[-1] = (runs[-1][0], runs[-1][1] + 1)
            else:
                runs.append((b, 1))
        pos = 0
        ads: List[bytes] = []
        for s0, c in runs:
            chunk = bytes(data[pos:pos + c * bs])
            self._write_logical(s0, chunk)
            ads.append(struct.pack("<II", len(chunk), s0))
            pos += c * bs
        head, _aeds = self._chain_ads(ads, loc, (self.bs - head_size) // 8)
        new = self._build_entry(loc, FT_DIRECTORY, 0, size, ALLOC_SHORT,
                                head, links, need, PERM_DIR, keep=raw)
        self._write_logical(loc, new)

    def _add_fid(self, parent: UdfEntry, fids: List[bytearray], fid: bytearray,
                 links_delta: int = 0) -> None:
        """Dizine FID ekler. Kapsamli dizinde yalnizca son blok(lar) yazilir;
        buyuk dizinde her eklemede tum dizini yeniden yazmak O(n^2) idi
        (5000 dosyalik dizin dakikalar suruyordu)."""
        self._load_bitmap()
        raw = self._entry_raw(parent)
        data_runs, aed_runs = self._split_runs(raw)
        flags = struct.unpack_from("<H", raw, 34)[0]
        if (flags & 7) == ALLOC_EMBEDDED or not data_runs:
            fids.append(fid)
            self._write_dir(parent, fids, links_delta)
            return
        bs = self.bs
        loc = parent.icb[0]
        blocks = [s0 + i for s0, c in data_runs for i in range(c)]
        size = struct.unpack_from("<Q", raw, 56)[0]
        new_size = size + len(fid)
        need = (new_size + bs - 1) // bs
        if need > len(blocks):
            add = need - len(blocks)
            goal = blocks[-1] + 1
            if goal + add <= self._bits and all(
                    self._is_free(b) for b in range(goal, goal + add)):
                self._mark(goal, add, True)
                blocks += list(range(goal, goal + add))
            else:
                fids.append(fid)                    # tasima/parcali yol
                self._write_dir(parent, fids, links_delta)
                return
        first, last = size // bs, (new_size - 1) // bs
        buf = bytearray()
        for i in range(first, last + 1):
            buf += self.fs._read_logical(blocks[i], self.ref) if i < len(blocks) and \
                i * bs < size else bytes(bs)
        tagged = make_tag(bytearray(fid), TAG_FID, blocks[first], crc_len=len(fid) - 16)
        off = size - first * bs
        buf[off:off + len(tagged)] = tagged
        for k, i in enumerate(range(first, last + 1)):
            self._write_logical(blocks[i], bytes(buf[k * bs:(k + 1) * bs]))
        self.free(aed_runs)
        runs: List[Tuple[int, int]] = []
        for b in blocks:
            if runs and runs[-1][0] + runs[-1][1] == b:
                runs[-1] = (runs[-1][0], runs[-1][1] + 1)
            else:
                runs.append((b, 1))
        ads = []
        for idx, (s0, c) in enumerate(runs):
            length = c * bs if idx + 1 < len(runs) else new_size - (len(blocks) - c) * bs
            ads.append(struct.pack("<II", length, s0))
        head_size = 216 if struct.unpack_from("<H", raw, 0)[0] == TAG_EFE else 176
        head, _aeds = self._chain_ads(ads, loc, (bs - head_size) // 8)
        links = struct.unpack_from("<H", raw, 48)[0] + links_delta
        new = self._build_entry(loc, FT_DIRECTORY, 0, new_size, ALLOC_SHORT, head,
                                links, need, PERM_DIR, keep=raw)
        self._write_logical(loc, new)

    # ---- yardimcilar --------------------------------------------------------------
    def _split(self, path: str) -> Tuple[UdfEntry, str]:
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if not parts:
            raise UdfWriteError(tr("Gecersiz dosya yolu"))
        parent = self.fs.resolve("/" + "/".join(parts[:-1]))
        if not parent.is_dir:
            raise UdfWriteError(tr("Dizin degil: {}", path))
        return parent, parts[-1]

    def _find(self, fids: List[bytearray], name: str) -> int:
        low = name.lower()
        for i, fid in enumerate(fids):
            if fid[18] & FID_PARENT:
                continue
            if self._fid_name(fid).lower() == low:
                return i
        return -1

    def _counts_add(self, files: int, dirs: int) -> None:
        self._counts[0] += files
        self._counts[1] += dirs

    def _invalidate(self) -> None:
        self.fs._cache.clear()

    # ---- islemler -------------------------------------------------------------------
    def mkdir(self, path: str) -> None:
        self._require()
        parent, name = self._split(path)
        fids = self._read_dir(parent)
        if self._find(fids, name) >= 0:
            raise UdfWriteError(tr("Zaten var: {}", path))
        loc = self.alloc(1, parent.icb[0] + 1)[0][0]
        try:
            unique = self._next_unique()
            up = self._fid(FID_DIRECTORY | FID_PARENT, "", parent.icb[0], self.ref,
                           self._unique_of(parent), self.bs)
            body = make_tag(up, TAG_FID, loc, crc_len=len(up) - 16)
            entry = self._build_entry(loc, FT_DIRECTORY, unique, len(body),
                                      ALLOC_EMBEDDED, body, 1, 0, PERM_DIR)
            self._write_logical(loc, entry)
            self._add_fid(parent, fids, self._fid(FID_DIRECTORY, name, loc, self.ref,
                                                  unique, self.bs), links_delta=+1)
        except Exception:
            self.free([(loc, 1)])
            raise
        self._counts_add(0, 1)
        self._invalidate()

    def _unique_of(self, entry: UdfEntry) -> int:
        raw = self._entry_raw(entry)
        off = 200 if struct.unpack_from("<H", raw, 0)[0] == TAG_EFE else 160
        return struct.unpack_from("<Q", raw, off)[0]

    def write_file(self, path: str, data: bytes, overwrite: bool = True) -> None:
        self._require()
        parent, name = self._split(path)
        fids = self._read_dir(parent)
        idx = self._find(fids, name)
        if idx >= 0:
            if fids[idx][18] & FID_DIRECTORY or not overwrite:
                raise UdfWriteError(tr("Zaten var: {}", path))
            self.remove(path)
            parent, name = self._split(path)
            fids = self._read_dir(parent)
        loc = self.alloc(1, parent.icb[0] + 1)[0][0]
        taken: List[Tuple[int, int]] = [(loc, 1)]
        try:
            alloc_type, ads, recorded, runs = self._store_data(data, loc + 1, loc)
            taken += runs
            unique = self._next_unique()
            entry = self._build_entry(loc, FT_FILE, unique, len(data), alloc_type,
                                      ads, 1, recorded, PERM_FILE,
                                      flags_extra=ICB_ARCHIVE)
            self._write_logical(loc, entry)
            self._add_fid(parent, fids, self._fid(0, name, loc, self.ref, unique, self.bs))
        except Exception:
            self.free(taken)
            self._invalidate()
            raise
        self._counts_add(1, 0)
        self._invalidate()

    def _release(self, block: int, ref: int) -> None:
        """Girisi ve kapladigi her seyi (veri, AED, akislar, EA) serbest birakir."""
        raw = self.fs._read_logical(block, ref)
        tag = struct.unpack_from("<H", raw, 0)[0]
        if tag not in (TAG_FE, TAG_EFE):
            raise UdfWriteError(tr("UDF dosya girisi bozuk (blok {})", block))
        self.free(self._data_runs(raw))
        ea_off = 136 if tag == TAG_EFE else 112
        ea_len, ea_blk = struct.unpack_from("<II", raw, ea_off)
        if ea_len & 0x3FFFFFFF:
            self._release(ea_blk, ref)
        if tag == TAG_EFE:
            st_len, st_blk, st_ref = struct.unpack_from("<IIH", raw, 152)
            if st_len & 0x3FFFFFFF:
                stream = self.fs._file_entry(st_blk, st_ref)
                for fid in self._read_dir(stream):
                    if not fid[18] & FID_PARENT:
                        blk, pref = struct.unpack_from("<IH", fid, 24)
                        self._release(blk, pref)
                self._release(st_blk, st_ref)
        self.free([(block, 1)])

    def remove(self, path: str, recursive: bool = False) -> None:
        self._require()
        parent, name = self._split(path)
        fids = self._read_dir(parent)
        idx = self._find(fids, name)
        if idx < 0:
            raise UdfWriteError(tr("Bulunamadi: {}", path))
        fid = fids[idx]
        blk, pref = struct.unpack_from("<IH", fid, 24)
        is_dir = bool(fid[18] & FID_DIRECTORY)
        if is_dir:
            child = self.fs._file_entry(blk, pref)
            children = [f for f in self._read_dir(child) if not f[18] & FID_PARENT]
            if children and not recursive:
                raise UdfWriteError(tr("Klasor bos degil: {}", path))
            for f in children:
                self.remove(path.rstrip("/") + "/" + self._fid_name(f), recursive=True)
            parent, name = self._split(path)
            fids = self._read_dir(parent)
            idx = self._find(fids, name)
            fid = fids[idx]
        raw = self.fs._read_logical(blk, pref)
        links = struct.unpack_from("<H", raw, 48)[0]
        del fids[idx]
        self._write_dir(parent, fids, links_delta=-1 if is_dir else 0)
        if not is_dir and links > 1:
            # sabit bag: giris yasar, bag sayisi azalir
            new = bytearray(raw)
            struct.pack_into("<H", new, 48, links - 1)
            crc_len = struct.unpack_from("<H", new, 10)[0]
            self._write_logical(blk, make_tag(new[:16 + crc_len], struct.unpack_from(
                "<H", new, 0)[0], blk))
        else:
            self._release(blk, pref)
        self._counts_add(0 if is_dir else -1, -1 if is_dir else 0)
        self._invalidate()

    def rename(self, path: str, new_name: str) -> None:
        """Yeniden adlandirma (`new_name` ad) ya da tasima (`new_name` yol)."""
        self._require()
        parent, name = self._split(path)
        fids = self._read_dir(parent)
        idx = self._find(fids, name)
        if idx < 0:
            raise UdfWriteError(tr("Bulunamadi: {}", path))
        fid = fids[idx]
        blk, pref = struct.unpack_from("<IH", fid, 24)
        unique = struct.unpack_from("<I", fid, 32)[0]
        is_dir = bool(fid[18] & FID_DIRECTORY)
        if "/" in new_name:
            target_parent, target = self._split(new_name)
        else:
            target_parent, target = parent, new_name
        if target_parent.icb == parent.icb:
            other = self._find(fids, target)
            if other >= 0 and other != idx:
                raise UdfWriteError(tr("Zaten var: {}", target))
            fids[idx] = self._fid(fid[18], target, blk, pref, unique, self.bs)
            self._write_dir(parent, fids)
            self._invalidate()
            return
        tfids = self._read_dir(target_parent)
        if self._find(tfids, target) >= 0:
            raise UdfWriteError(tr("Zaten var: {}", target))
        if is_dir:
            walk = target_parent
            for _ in range(4096):
                if walk.icb == (blk, pref):
                    raise UdfWriteError(tr("Klasor kendi icine tasinamaz"))
                if walk.icb == self.fs.root_icb:
                    break
                up = next((f for f in self._read_dir(walk) if f[18] & FID_PARENT), None)
                if up is None:
                    break
                walk = self.fs._file_entry(*struct.unpack_from("<IH", up, 24))
        self._add_fid(target_parent, tfids, self._fid(fid[18], target, blk, pref,
                                                      unique, self.bs),
                      links_delta=+1 if is_dir else 0)
        parent = self.fs._file_entry(*parent.icb)
        fids = self._read_dir(parent)
        del fids[self._find(fids, name)]
        self._write_dir(parent, fids, links_delta=-1 if is_dir else 0)
        if is_dir:
            moved = self.fs._file_entry(blk, pref)
            mfids = self._read_dir(moved)
            for i, f in enumerate(mfids):
                if f[18] & FID_PARENT:
                    mfids[i] = self._fid(FID_DIRECTORY | FID_PARENT, "",
                                         target_parent.icb[0], self.ref,
                                         self._unique_of(target_parent), self.bs)
            self._write_dir(moved, mfids)
        self._invalidate()

    # ---- kapatma --------------------------------------------------------------------
    def flush(self) -> None:
        if not self._began:
            return
        if self._dirty and self._bitmap is not None:
            raw = self._sbd_raw
            raw[24:24 + len(self._bitmap)] = self._bitmap
            crc_len = struct.unpack_from("<H", raw, 10)[0]
            head = bytearray(raw[:16 + crc_len])
            tagged = make_tag(head, TAG_SBD, self._sbd_pos, crc_len=crc_len)
            raw[:len(tagged)] = tagged
            self._write_logical(self._sbd_pos, bytes(raw))
            self._dirty = False
        lvid = bytearray(self._lvid())
        lvid[16:28] = timestamp()
        struct.pack_into("<I", lvid, 28, 1)                  # kapali
        n = struct.unpack_from("<I", lvid, 72)[0]
        if self._bitmap is not None and n:
            struct.pack_into("<I", lvid, 80 + 4 * self.ref, self.free_count())
        iu = 80 + 8 * n
        impl_len = struct.unpack_from("<I", lvid, 76)[0]
        if impl_len >= 40:
            files, dirs = struct.unpack_from("<II", lvid, iu + 32)
            struct.pack_into("<II", lvid, iu + 32, max(0, files + self._counts[0]),
                             max(0, dirs + self._counts[1]))
            self._counts = [0, 0]
        self._write_lvid(lvid)
        f = getattr(self.dev, "flush", None)
        if f:
            f()
        self._began = False
