"""NTFS **yazma** destegi (1. asama).

Okuma `ntfsread.py`, bicimlendirme `ntfs.py` icindedir. Bu modul var olan bir
NTFS birimine dosya/klasor ekler, siler ve yeniden adlandirir.

## NTFS'e yazmak neden ext'ten zor

ext'te dizin duz bir kayit zinciridir; NTFS'te **sirali bir B+ agaci**dir.
Yeni giris dogru yere sokulmali, dizin dolunca dugum bolunmeli ve
`$INDEX_ROOT`'tan `$INDEX_ALLOCATION`'a tasinmalidir. Yanlis yapilirsa `chkdsk`
indeksi bozuk sayar ve dosya "yok" gorunur.

Bu surumun kapsami bilincli olarak dar tutuldu:

  * Dizin indeksi tam bir B+ agacidir (`ntfsindex.py`): kok tasinca
    `$INDEX_ALLOCATION`a tasinir, dugumler bolunur, ara dugumdeki giris
    silinebilir (ADR 0058).
  * Sikistirilmis/sifrelenmis akislar desteklenmez.

Reddetme sessiz degildir: nedeni metinle dondurulur. **Yanlis yazip bozmaktansa
yazmamak yeglenir.**

Yazilan her birim `ntfsfix` ve mumkunse `ntfs-3g` ile baglanarak dogrulanir
(`tests/ntfs_write_check.py`).
"""
from __future__ import annotations

import datetime
import io
import struct
from typing import List, Optional, Tuple

from .ntfsread import (AT_BITMAP, AT_DATA, AT_END, AT_FILE_NAME,
                       AT_INDEX_ALLOCATION, AT_INDEX_ROOT,
                       AT_STANDARD_INFORMATION, FILE_ATTR_DIRECTORY,
                       FILE_MAGIC, INDEX_ENTRY_END, INDEX_ENTRY_NODE,
                       MFT_FLAG_DIRECTORY, MFT_FLAG_IN_USE, MFT_RECORD_ROOT,
                       Attribute, MftRecord, NtfsError, NtfsFS)
from .ntfsindex import DirIndex, Entry
from .streamio import read_exact, write_runs
from ..i18n import tr

# $Bitmap pencere boyutu: bir kerede okunup taranan bayt. 64 KB, 4 KB
# kumede 512 bin kumeyi (~2 GB alan) kapsar; tahsis genellikle ilk
# pencerede biter.
BITMAP_WINDOW = 64 * 1024

MFT_BITMAP_RECORD = 0          # $MFT kaydi; kendi $BITMAP'i kayit tahsisini tutar
BITMAP_RECORD = 6              # $Bitmap: kume tahsisi
MFTMIRR_RECORD = 1

# 0-15 sistem dosyalarinin kayitlaridir. 16-23 `$MFT`in kendi ek kayitlari
# (oznitelik listesi gerekince) icin ayrilmistir; bitmap'te bos gorunseler de
# Windows ve ntfs-3g kullanici dosyasini oraya koymaz. Eskiden 16'dan
# tahsis ediliyordu; Windows birimi "MFT bozuk kayit iceriyor" diye
# isaretledi (olay 55, $MFT'yi gosteren basvuru; olculdu 2026-09-29).
FIRST_USER_RECORD = 24
MFT_GROW_RECORDS = 32          # MFT dolunca bir seferde eklenecek kayit sayisi

STD_INFO_SIZE = 0x48           # NTFS 3.1 $STANDARD_INFORMATION
INDEX_ENTRY_HEADER = 0x10


def _now_filetime() -> int:
    """Simdiki zamani Windows FILETIME (UTC) olarak dondurur.

    NTFS zamanlari UTC'dir; eskiden yerel saat yaziliyordu ve Windows
    dosyalari saat dilimi kadar kaymis gosteriyordu (olculdu: +3 saat).
    """
    delta = datetime.datetime.utcnow() - datetime.datetime(1601, 1, 1)
    return int(delta.total_seconds() * 10_000_000)


def _align8(n: int) -> int:
    return (n + 7) & ~7


class NtfsWriter:
    """Bir `NtfsFS` uzerine yazma islemleri ekler."""

    def __init__(self, fs: NtfsFS):
        self.fs = fs
        self.dev = fs.dev
        self.cs = fs.cluster_size
        self.secure = None

    # ------------------------------------------------------------------
    # Destek denetimi
    # ------------------------------------------------------------------
    def write_support(self) -> Tuple[bool, str]:
        if getattr(self.dev, "readonly", False):
            return False, tr("Kaynak salt okunur acildi.")
        return True, ""

    def _require_writable(self) -> None:
        ok, reason = self.write_support()
        if not ok:
            raise NtfsError(reason)

    # ------------------------------------------------------------------
    # Siralama (collation) — NTFS dizin girisleri sirali olmak zorundadir
    # ------------------------------------------------------------------
    def collation_key(self, name: str) -> List[int]:
        """Birimin kendi `$UpCase` tablosuyla buyuk harfe cevrilmis kod birimleri."""
        return self.fs.collation_key(name)

    # ------------------------------------------------------------------
    # Bitmap tahsisi ($Bitmap ve $MFT'nin $BITMAP'i)
    # ------------------------------------------------------------------
    def _bitmap_attr(self) -> Attribute:
        attr = self.fs.record(BITMAP_RECORD).find(AT_DATA)
        if attr is None:
            raise NtfsError(tr("$Bitmap okunamadi"))
        return attr

    def _write_attr_range(self, attr: Attribute, data: bytes,
                          offset: int) -> None:
        """Yerlesik olmayan oznitelugun yalnizca bir araligini yazar.

        Onceki surum oznitelugun **tamamini** yazardi. `$Bitmap` icin bu, tek
        bir kumeyi tahsis etmek ugruna 1.9 MB yazmak demekti (58 GB bolum,
        4 KB kume). SD kart gibi ortamlarda hem yavas hem yipraticidir —
        olculdu ve ADR 0024'te kayit altina alindi.
        """
        if attr.resident:
            raise NtfsError(tr("Yerlesik oznitelik bu yoldan yazilamaz"))
        if not data:
            return
        end = offset + len(data)
        pos = 0                     # oznitelik icinde su anki bayt konumu
        for lcn, count in attr.runs:
            span = count * self.cs
            if pos + span <= offset:
                pos += span
                continue
            if pos >= end:
                break
            inside = max(0, offset - pos)
            take = min(span - inside, end - max(pos, offset))
            if lcn >= 0:
                source = max(pos, offset) - offset
                self.dev.write(lcn * self.cs + inside,
                               data[source:source + take])
            pos += span

    def _align_range(self, low: int, high: int, limit: int) -> Tuple[int, int]:
        """Bayt araligini sektor sinirlarina disa dogru genisletir.

        Aygit yazmalari sektor tanelidir; hizasiz bir yazma alt katmanda
        oku-degistir-yaz gerektirir. Hizalamayi burada yapmak, yazilan
        bolgeyi ongorulebilir kilar.
        """
        ss = self.fs.sector_size
        return max(0, (low // ss) * ss), min(limit, ((high + ss - 1) // ss) * ss)

    @staticmethod
    def _bit(buf: bytearray, index: int) -> bool:
        return bool(buf[index >> 3] & (1 << (index & 7)))

    @staticmethod
    def _set_bit(buf: bytearray, index: int, value: bool) -> None:
        if value:
            buf[index >> 3] |= (1 << (index & 7))
        else:
            buf[index >> 3] &= ~(1 << (index & 7)) & 0xFF

    def _bitmap_limits(self, attr: Attribute) -> Tuple[int, int]:
        """($Bitmap bayt boyutu, gecerli kume sayisi)."""
        size = self.fs.attribute_size(attr)
        total_on_disk = self.fs.total_sectors * self.fs.sector_size // self.cs
        return size, min(size * 8, total_on_disk)

    def alloc_clusters(self, count: int) -> List[Tuple[int, int]]:
        """Ardisik kume arar; bulamazsa parcali tahsis eder.

        Bitmap **pencere pencere** okunur ve yalnizca degisen bolum geri
        yazilir. Onceki surum her cagrida bitmap'in tamamini okuyup tamamini
        yaziyordu: 58 GB'lik bir bolumde tek bir kume icin 1.9 MB okuma +
        1.9 MB yazma (ADR 0024). Tahsis genellikle ilk pencerelerde biter,
        bu yuzden kazanc buyuktur.
        """
        attr = self._bitmap_attr()
        size, limit = self._bitmap_limits(attr)
        byte_limit = (limit + 7) // 8

        # Once yeterince uzun BITISIK bir bosluk aranir. Eskiden ilk bos
        # kumeler aliniyordu; parcali bir birimde 3 MB'lik dosya onlarca
        # parcaya bolunuyor, veri kosullari tek MFT kaydina sigmiyordu
        # ($ATTRIBUTE_LIST bu surumde yok).
        start = self._find_contiguous(attr, limit, byte_limit, count) if count > 1 else -1
        if start >= 0:
            self._mark_clusters(attr, start, count, True)
            return [(start, count)]

        runs: List[Tuple[int, int]] = []
        remaining = count
        base = 0                                    # pencerenin bayt ofseti
        while base < byte_limit and remaining:
            length = min(BITMAP_WINDOW, byte_limit - base)
            window = bytearray(self.fs.read_attribute_range(attr, base, length))
            if len(window) < length:                # kisa okuma: gerisi sifir
                window += bytearray(length - len(window))
            low = high = -1
            first_bit = base * 8
            last_bit = min(limit, (base + length) * 8)
            i = first_bit
            while i < last_bit and remaining:
                local = i - first_bit
                if self._bit(window, local):
                    i += 1
                    continue
                start = i
                while i < last_bit and remaining and \
                        not self._bit(window, i - first_bit):
                    self._set_bit(window, i - first_bit, True)
                    i += 1
                    remaining -= 1
                byte_lo, byte_hi = (start - first_bit) >> 3, (i - 1 - first_bit) >> 3
                low = byte_lo if low < 0 else min(low, byte_lo)
                high = byte_hi if high < 0 else max(high, byte_hi)
                # Pencere sinirini gecen bos alan iki parca gorunur; bitisikse
                # birlestirilir, yoksa veri kosullari gereksiz yere uzardi.
                if runs and runs[-1][0] + runs[-1][1] == start:
                    runs[-1] = (runs[-1][0], runs[-1][1] + (i - start))
                else:
                    runs.append((start, i - start))
            if low >= 0:
                lo, hi = self._align_range(low, high + 1, length)
                self._write_attr_range(attr, bytes(window[lo:hi]), base + lo)
            base += length
        if remaining:
            # Kismi tahsis birakilmaz: alinan kumeler geri verilir.
            if runs:
                self.free_clusters(runs)
            raise NtfsError(tr("Diskte yeterli bos kume yok"))
        return runs

    def alloc_clusters_near(self, goal: int, count: int) -> List[Tuple[int, int]]:
        """Once `goal`dan baslayan `count` kume denenir (onceki parcanin hemen
        ardi — oznitelik yeni parca kazanmaz), olmazsa normal tahsis."""
        attr = self._bitmap_attr()
        size, limit = self._bitmap_limits(attr)
        if goal >= 0 and goal + count <= limit:
            lo, hi = self._align_range(goal >> 3, ((goal + count - 1) >> 3) + 1, size)
            block = self.fs.read_attribute_range(attr, lo, hi - lo)
            if all(not self._bit(block, bit - lo * 8)
                   for bit in range(goal, goal + count)):
                self._mark_clusters(attr, goal, count, True)
                return [(goal, count)]
        return self.alloc_clusters(count)

    def _find_contiguous(self, attr: Attribute, limit: int, byte_limit: int,
                         count: int) -> int:
        """`count` bitisik bos kumenin ilk kume numarasi; yoksa -1."""
        run_start, run_len = -1, 0
        base = 0
        while base < byte_limit:
            length = min(BITMAP_WINDOW, byte_limit - base)
            window = self.fs.read_attribute_range(attr, base, length)
            for j, byte in enumerate(window):
                bit0 = (base + j) * 8
                if byte == 0 and bit0 + 8 <= limit:
                    if run_len == 0:
                        run_start = bit0
                    run_len += 8
                    if run_len >= count:
                        return run_start
                    continue
                if byte == 0xFF:
                    run_len = 0
                    continue
                for k in range(8):
                    bit = bit0 + k
                    if bit >= limit:
                        return -1
                    if byte & (1 << k):
                        run_len = 0
                    else:
                        if run_len == 0:
                            run_start = bit
                        run_len += 1
                        if run_len >= count:
                            return run_start
            base += length
        return -1

    def _mark_clusters(self, attr: Attribute, start: int, count: int,
                       used: bool) -> None:
        size, _limit = self._bitmap_limits(attr)
        lo, hi = self._align_range(start >> 3, ((start + count - 1) >> 3) + 1, size)
        block = bytearray(self.fs.read_attribute_range(attr, lo, hi - lo))
        for bit in range(start, start + count):
            self._set_bit(block, bit - lo * 8, used)
        self._write_attr_range(attr, bytes(block), lo)

    def free_clusters(self, runs: List[Tuple[int, int]]) -> None:
        """Verilen kumeleri serbest birakir.

        Yalnizca **etkilenen bayt araliklari** okunur ve yazilir. Birbirine
        yakin araliklar tek yazmada birlestirilir.
        """
        attr = self._bitmap_attr()
        size, limit = self._bitmap_limits(attr)
        spans: List[Tuple[int, int]] = []
        for lcn, count in runs:
            if lcn < 0 or count <= 0 or lcn >= limit:
                continue
            last = min(lcn + count - 1, limit - 1)
            spans.append(self._align_range(lcn >> 3, (last >> 3) + 1, size))
        if not spans:
            return
        spans.sort()
        merged: List[Tuple[int, int]] = [spans[0]]
        for lo, hi in spans[1:]:
            # Bitisik veya cakisan araliklar tek yazmaya toplanir
            if lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        for lo, hi in merged:
            block = bytearray(self.fs.read_attribute_range(attr, lo, hi - lo))
            if len(block) < hi - lo:
                block += bytearray((hi - lo) - len(block))
            for lcn, count in runs:
                if lcn < 0:
                    continue
                for k in range(count):
                    bit = lcn + k
                    if bit >= limit:
                        break
                    if lo <= (bit >> 3) < hi:
                        self._set_bit(block, bit - lo * 8, False)
            self._write_attr_range(attr, bytes(block), lo)

    def _mft_bitmap(self) -> Attribute:
        attr = self.fs.record(MFT_BITMAP_RECORD).find(AT_BITMAP)
        if attr is None:
            raise NtfsError(tr("$MFT bitmap'i okunamadi"))
        return attr

    def alloc_record(self) -> int:
        """Bos bir MFT kaydi tahsis eder; gerekirse `$MFT`'yi buyutur."""
        for _attempt in range(3):
            attr = self._mft_bitmap()
            bmp = bytearray(self.fs.read_attribute(attr))
            records = self.fs.mft_size // self.fs.record_size
            limit = min(len(bmp) * 8, records)
            for i in range(FIRST_USER_RECORD, limit):
                if not self._bit(bmp, i):
                    self._set_bit(bmp, i, True)
                    self._flush_mft_bitmap(attr, bmp, i)
                    return i
            if len(bmp) * 8 < records:
                self._grow_mft_bitmap(records)   # MFT'de yer var, bitmap kisa
            else:
                self._extend_mft()
        raise NtfsError(tr("Bos MFT kaydi yok ve $MFT buyutulemedi"))

    def _extend_mft(self) -> None:
        """`$MFT`'yi ve bitmap'ini buyutur.

        Iki eski sinir vardi (2026-09-29 olculdu):
          * Buyutme 32 kayitlik yeni bir parca ekliyordu; parca onceki
            parcanin bitisigine dusmeyince veri kosullari `$MFT` kaydindaki
            **sabit uzunluklu** `$DATA` oznitelugune sigmiyor, birim birkac
            buyutmeden sonra "buyutulemedi" diyordu. Artik oznitelik kayit
            icinde yeniden boyutlanir ve pay buyuktur (en az 256 kayit ya da
            MFT'nin 1/8'i).
          * `$MFT:$BITMAP`in veri boyutu hic buyumuyordu; bizim
            bicimlendiricinin birimi 64 kayitta (48 kullanici dosyasi)
            takiliyordu.
        """
        rec0 = self.fs.record(MFT_BITMAP_RECORD)
        data = rec0.find(AT_DATA)
        if data is None or data.resident:
            raise NtfsError(tr("$MFT veri oznitelugu okunamadi"))

        records_now = data.data_size // self.fs.record_size
        grow_records = max(256, records_now // 8)
        grow_bytes = grow_records * self.fs.record_size
        grow_clusters = max(1, (grow_bytes + self.cs - 1) // self.cs)
        last = data.runs[-1] if data.runs else (-1, 0)
        new_runs = self.alloc_clusters_near(last[0] + last[1] if last[0] >= 0 else -1,
                                            grow_clusters)
        # Yeni kayitlar bos FILE kaydi olarak BICIMLENDIRILIR (mkntfs, ntfs-3g
        # ve Windows boyle yapar). Eskiden yalnizca sifirlaniyordu; Windows bu
        # kayitlardan birini ayirinca "MFT bozuk dosya kaydi iceriyor" (olay
        # 55) yaziyordu (olculdu 2026-09-29).
        first_new = data.data_size // self.fs.record_size
        per_cluster = max(1, self.cs // self.fs.record_size)
        number = first_new
        for lcn, count in new_runs:
            buf = bytearray()
            for _k in range(count * per_cluster):
                rec = self._build_record(number, 1, 0, [])
                self._apply_fixup_out(rec, 1)
                buf += rec
                number += 1
            self.dev.write(lcn * self.cs, bytes(buf))

        runs = list(data.runs)
        for lcn, count in new_runs:
            if runs and runs[-1][0] >= 0 and runs[-1][0] + runs[-1][1] == lcn:
                runs[-1] = (runs[-1][0], runs[-1][1] + count)   # bitisikse birlestir
            else:
                runs.append((lcn, count))
        new_size = data.data_size + grow_clusters * self.cs
        attr = self._nonresident_attr(AT_DATA, runs, new_size)
        try:
            self._set_record_attr(MFT_BITMAP_RECORD, AT_DATA, "", attr)
        except NtfsError:
            self.free_clusters(new_runs)
            raise NtfsError(tr("$MFT buyutulemedi: kayit dolu"))
        self.fs._cache.clear()
        self.fs._load_mft()
        self._grow_mft_bitmap(new_size // self.fs.record_size)

    def _grow_mft_bitmap(self, records: int) -> None:
        """`$MFT:$BITMAP` en az `records` biti kapsasin (8 baytin kati)."""
        attr = self._mft_bitmap()
        need = ((records + 63) // 64) * 8
        size = self.fs.attribute_size(attr)
        if size >= need:
            return
        if attr.resident:
            value = bytes(self.fs.read_attribute(attr)).ljust(need, b"\x00")
            self._set_record_attr(MFT_BITMAP_RECORD, AT_BITMAP, "",
                                  self._resident_attr(AT_BITMAP, value))
            self.fs._cache.clear()
            return
        runs = list(attr.runs)
        allocated = sum(c for _l, c in runs) * self.cs
        if need > allocated:
            extra = (need - allocated + self.cs - 1) // self.cs
            more = self.alloc_clusters(extra)
            for lcn, count in more:
                self.dev.write(lcn * self.cs, b"\x00" * (count * self.cs))
                runs.append((lcn, count))
        else:
            # ayrilmis alanda kalan kisim sifirlanir (eski veri bit sanilmasin)
            self._write_attr_range(attr, b"\x00" * (need - size), size)
        new_attr = self._nonresident_attr(AT_BITMAP, runs, need)
        self._set_record_attr(MFT_BITMAP_RECORD, AT_BITMAP, "", new_attr)
        self.fs._cache.clear()

    def free_record(self, number: int) -> None:
        attr = self._mft_bitmap()
        bmp = bytearray(self.fs.read_attribute(attr))
        if number < len(bmp) * 8:
            self._set_bit(bmp, number, False)
            self._flush_mft_bitmap(attr, bmp, number)

    def _flush_mft_bitmap(self, attr: Attribute, bmp: bytearray,
                          changed_bit: int) -> None:
        """`$MFT` bitmap'inin **degisen bolumunu** diske yazar.

        Yerlesik oznitelik kaydin icindedir, tamami yazilir (zaten kucuktur).
        Yerlesik degilse yalnizca degisen bitin sektoru yazilir; onceki surum
        her dosya olusturmada bitmap'in tamamini yaziyordu.
        """
        if attr.resident:
            self._patch_resident(MFT_BITMAP_RECORD, attr, bytes(bmp))
            return
        lo, hi = self._align_range(changed_bit >> 3, (changed_bit >> 3) + 1,
                                   len(bmp))
        self._write_attr_range(attr, bytes(bmp[lo:hi]), lo)

    # ------------------------------------------------------------------
    # FILE kaydi yazimi
    # ------------------------------------------------------------------
    def _record_offset(self, number: int) -> int:
        """Kaydin diskteki bayt ofseti ($MFT kume zinciri uzerinden)."""
        target = number * self.fs.record_size
        pos = 0
        for lcn, count in self.fs._mft_runs:
            span = count * self.cs
            if target < pos + span and lcn >= 0:
                return lcn * self.cs + (target - pos)
            pos += span
        raise NtfsError(tr("MFT kaydi diskte bulunamadi: {}", number))

    def _apply_fixup_out(self, raw: bytearray, usn: int) -> None:
        """Fixup dizisini **kurar**: her sektorun son iki bayti diziye tasinir.

        Okuma tarafinin tersidir. Yapilmazsa NTFS kaydi "bozuk" sayar.
        """
        usa_off, usa_count = struct.unpack_from("<HH", raw, 4)
        struct.pack_into("<H", raw, usa_off, usn)
        for i in range(1, usa_count):
            end = i * self.fs.sector_size - 2
            if end + 2 > len(raw):
                break
            raw[usa_off + i * 2:usa_off + i * 2 + 2] = raw[end:end + 2]
            struct.pack_into("<H", raw, end, usn)

    def write_record(self, number: int, raw: bytearray) -> None:
        """FILE kaydini fixup kurarak diske yazar."""
        if raw[:4] != FILE_MAGIC:
            raise NtfsError(tr("FILE imzasi yok"))
        usn = (struct.unpack_from("<H", raw, struct.unpack_from("<H", raw, 4)[0])[0]
               + 1) & 0xFFFF
        if usn in (0, 0xFFFF):
            usn = 1
        self._apply_fixup_out(raw, usn)
        self.dev.write(self._record_offset(number), bytes(raw))
        self.fs._cache.pop(number, None)
        self._sync_mirror(number, raw)

    def _sync_mirror(self, number: int, raw: bytes) -> None:
        """Ilk dort kayit `$MFTMirr` icinde de tutulur."""
        if number >= 4:
            return
        try:
            mirr = self.fs.record(MFTMIRR_RECORD).find(AT_DATA)
            if mirr is None or mirr.resident or not mirr.runs:
                return
            lcn = mirr.runs[0][0]
            if lcn >= 0:
                self.dev.write(lcn * self.cs + number * self.fs.record_size, raw)
        except NtfsError:
            pass

    def _patch_resident(self, rec_no: int, attr: Attribute,
                        value: bytes) -> None:
        """Yerlesik bir oznitelugun degerini **ayni uzunlukta** gunceller."""
        raw = bytearray(self._raw_record(rec_no))
        pos = self._find_attr_offset(raw, attr)
        value_len, value_off = struct.unpack_from("<IH", raw, pos + 0x10)
        if len(value) != value_len:
            raise NtfsError(tr("Yerlesik oznitelik boyutu degisemez"))
        raw[pos + value_off:pos + value_off + value_len] = value
        self.write_record(rec_no, raw)

    def _raw_record(self, number: int) -> bytearray:
        raw = bytearray(self.fs._run_read(
            self.fs._mft_runs, number * self.fs.record_size,
            self.fs.record_size))
        self.fs._apply_fixup(raw)
        return raw

    @staticmethod
    def _find_attr_offset(raw: bytearray, attr: Attribute) -> int:
        """Bir oznitelugun kayit icindeki ofsetini bulur."""
        pos = struct.unpack_from("<H", raw, 0x14)[0]
        while pos + 8 <= len(raw):
            kind = struct.unpack_from("<I", raw, pos)[0]
            if kind == AT_END:
                break
            length = struct.unpack_from("<I", raw, pos + 4)[0]
            if length < 16:
                break
            name_len = raw[pos + 9]
            name_off = struct.unpack_from("<H", raw, pos + 0x0A)[0]
            name = raw[pos + name_off:pos + name_off + name_len * 2].decode(
                "utf-16-le", "replace") if name_len else ""
            if kind == attr.kind and name == attr.name:
                return pos
            pos += length
        raise NtfsError(tr("Oznitelik kayitta bulunamadi"))

    # ------------------------------------------------------------------
    # Oznitelik kurucular
    # ------------------------------------------------------------------
    @staticmethod
    def _attr_header(kind: int, length: int, resident: bool, name: str = "",
                     attr_id: int = 0) -> bytearray:
        name_bytes = name.encode("utf-16-le")
        head = bytearray(length)
        struct.pack_into("<II", head, 0, kind, length)
        head[8] = 0 if resident else 1
        head[9] = len(name)
        struct.pack_into("<HHH", head, 0x0A,
                         0x40 if not resident else 0x18, 0, attr_id)
        if name:
            off = 0x40 if not resident else 0x18
            head[off:off + len(name_bytes)] = name_bytes
        return head

    def _resident_attr(self, kind: int, value: bytes, name: str = "",
                       attr_id: int = 0, indexed: int = 0) -> bytes:
        name_bytes = name.encode("utf-16-le")
        value_off = _align8(0x18 + len(name_bytes))
        length = _align8(value_off + len(value))
        attr = bytearray(length)
        struct.pack_into("<II", attr, 0, kind, length)
        attr[8] = 0
        attr[9] = len(name)
        struct.pack_into("<HHH", attr, 0x0A, 0x18, 0, attr_id)
        struct.pack_into("<IHBB", attr, 0x10, len(value), value_off, indexed, 0)
        if name:
            attr[0x18:0x18 + len(name_bytes)] = name_bytes
        attr[value_off:value_off + len(value)] = value
        return bytes(attr)

    def _nonresident_attr(self, kind: int, runs: List[Tuple[int, int]],
                          data_size: int, name: str = "",
                          attr_id: int = 0) -> bytes:
        name_bytes = name.encode("utf-16-le")
        mapping_off = _align8(0x40 + len(name_bytes))
        pairs = _encode_runs(runs)
        length = _align8(mapping_off + len(pairs))
        total_clusters = sum(c for _l, c in runs)
        attr = bytearray(length)
        struct.pack_into("<II", attr, 0, kind, length)
        attr[8] = 1
        attr[9] = len(name)
        struct.pack_into("<HHH", attr, 0x0A, 0x40, 0, attr_id)
        struct.pack_into("<QQHHI", attr, 0x10, 0, max(0, total_clusters - 1),
                         mapping_off, 0, 0)
        allocated_bytes = total_clusters * self.cs
        struct.pack_into("<QQQ", attr, 0x28, allocated_bytes, data_size, data_size)
        if name:
            attr[0x40:0x40 + len(name_bytes)] = name_bytes
        attr[mapping_off:mapping_off + len(pairs)] = pairs
        return bytes(attr)

    def _std_info(self, file_attr: int, security_id: int = 0) -> bytes:
        """NTFS 3.x $STANDARD_INFORMATION.

        * Dizin biti (0x10000000) yalnizca $FILE_NAME'de durur; Windows'un
          kendi dizinlerinde SI oznitelik alani 0'dir.
        * Guvenlik kimligi `$Secure`da var olan bir tanimlayiciyi gostermeli;
          0 gecersizdir (Windows'ta erisim/bozukluk hatasi).
        """
        now = _now_filetime()
        value = bytearray(STD_INFO_SIZE)
        struct.pack_into("<QQQQ", value, 0, now, now, now, now)
        struct.pack_into("<I", value, 0x20, file_attr & ~FILE_ATTR_DIRECTORY)
        struct.pack_into("<I", value, 0x34, security_id)
        return bytes(value)

    def _security_id_for(self, dir_no: int, is_dir: bool = False) -> int:
        """Yeni oge icin guvenlik kimligi: ust dizinden devralinan tanimlayici
        `$Secure`da aranir, yoksa eklenir (`ntfssecure`). `$Secure` yoksa
        (NTFS 1.x) 0 doner."""
        if self.secure is None:
            from .ntfssecure import SecureStore
            self.secure = SecureStore(self)
        if not self.secure.available():
            return 0
        return self.secure.id_for_new(dir_no, is_dir)

    def _file_name_value(self, parent_ref: int, name: str,
                         file_attr: int, size: int = 0,
                         allocated: int = 0) -> bytes:
        raw_name = name.encode("utf-16-le")
        value = bytearray(0x42 + len(raw_name))
        now = _now_filetime()
        struct.pack_into("<Q", value, 0, parent_ref)
        struct.pack_into("<QQQQ", value, 8, now, now, now, now)
        struct.pack_into("<QQ", value, 0x28, allocated, size)
        struct.pack_into("<I", value, 0x38, file_attr)
        value[0x40] = len(name)
        # Ad alani POSIX (0): kisa (8.3) adi olmayan uzun ad. Eskiden Win32 (1)
        # yaziliyordu; Windows Win32 adin bir DOS adiyla esli olmasini bekler
        # ve ilk erisimde kaydi "onariyordu" (Ntfs olay 130: "DOS dosya adi
        # ozniteligi yok ... isaretleri 0x1 iken 0x0 yapin"). ntfs-3g de yeni
        # dosyalari POSIX ad alaniyla olusturur.
        value[0x41] = 0
        value[0x42:] = raw_name
        return bytes(value)

    def _build_record(self, number: int, sequence: int, flags: int,
                      attrs: List[bytes]) -> bytearray:
        size = self.fs.record_size
        usa_count = size // self.fs.sector_size + 1
        usa_off = 0x30
        attrs_off = _align8(usa_off + usa_count * 2)
        raw = bytearray(size)
        raw[0:4] = FILE_MAGIC
        struct.pack_into("<HH", raw, 4, usa_off, usa_count)
        struct.pack_into("<HHHH", raw, 0x10, sequence, 1, attrs_off, flags)
        # NTFS 3.1: kayit kendi numarasini tasir; Windows bunu dogrular. Eskiden
        # 0 kaliyordu ve Windows dizini "bozuk ve okunamaz" sayiyordu (olculdu).
        struct.pack_into("<I", raw, 0x2C, number)
        pos = attrs_off
        for a in attrs:
            if pos + len(a) + 8 > size:
                raise NtfsError(
                    tr("Dosya cok parcali: veri kosullari tek MFT kaydina "
                       "sigmiyor ($ATTRIBUTE_LIST bu surumde yok)"))
            raw[pos:pos + len(a)] = a
            pos += len(a)
        struct.pack_into("<I", raw, pos, AT_END)
        struct.pack_into("<II", raw, 0x18, _align8(pos + 8), size)
        struct.pack_into("<Q", raw, 0x20, 0)
        struct.pack_into("<H", raw, 0x28, len(attrs) + 1)
        return raw

    # ------------------------------------------------------------------
    # Dizin indeksi ($INDEX_ROOT icinde)
    # ------------------------------------------------------------------
    def _index_root(self, rec: MftRecord) -> Attribute:
        root = rec.find(AT_INDEX_ROOT, "$I30")
        if root is None:
            raise NtfsError(tr("Dizin indeksi bulunamadi"))
        return root

    # --- $INDEX_ALLOCATION (INDX bloklari) ------------------------------
    def _entry_name(entry: bytes) -> str:
        name_len = entry[INDEX_ENTRY_HEADER + 0x40]
        start = INDEX_ENTRY_HEADER + 0x42
        return entry[start:start + name_len * 2].decode("utf-16-le", "replace")

    def _make_index_entry(self, mft_ref: int, sequence: int, key: bytes) -> bytes:
        length = _align8(INDEX_ENTRY_HEADER + len(key))
        entry = bytearray(length)
        struct.pack_into("<Q", entry, 0, (sequence << 48) | mft_ref)
        struct.pack_into("<HHH", entry, 8, length, len(key), 0)
        entry[INDEX_ENTRY_HEADER:INDEX_ENTRY_HEADER + len(key)] = key
        return bytes(entry)

    def _rebuild_index_root(self, entries: List[bytes]) -> bytes:
        """Girislerden yeni bir `$INDEX_ROOT` degeri kurar (sirali)."""
        entries = sorted(entries, key=lambda e: self.collation_key(
            self._entry_name(e)))
        body = b"".join(entries)
        end = bytearray(INDEX_ENTRY_HEADER)
        struct.pack_into("<HHH", end, 8, INDEX_ENTRY_HEADER, 0, INDEX_ENTRY_END)
        body += bytes(end)

        value = bytearray(0x20 + len(body))
        struct.pack_into("<III", value, 0, AT_FILE_NAME, 1, self.fs.index_size)
        value[0x0C] = max(1, self.fs.index_size // self.cs)
        struct.pack_into("<IIIB", value, 0x10, 0x10, 0x10 + len(body),
                         0x10 + len(body), 0)
        value[0x20:] = body
        return bytes(value)

    def _replace_index_root(self, dir_no: int, new_value: bytes) -> None:
        """Ust dizinin `$INDEX_ROOT` oznitelugunu yeni degerle degistirir."""
        rec = self.fs.record(dir_no)
        root = self._index_root(rec)
        raw = self._raw_record(dir_no)
        pos = self._find_attr_offset(raw, root)
        old_len = struct.unpack_from("<I", raw, pos + 4)[0]

        new_attr = self._resident_attr(AT_INDEX_ROOT, new_value, name="$I30",
                                        attr_id=struct.unpack_from(
                                            "<H", raw, pos + 0x0E)[0],
                                        indexed=0)
        bytes_in_use = struct.unpack_from("<I", raw, 0x18)[0]
        delta = len(new_attr) - old_len
        if bytes_in_use + delta + 8 > self.fs.record_size:
            raise NtfsError(
                tr("Dizin kaydi doldu: bu surumde indeks $INDEX_ALLOCATION'a "
                "tasinamaz. Daha az giris deneyin."))
        tail = bytes(raw[pos + old_len:bytes_in_use])
        new_raw = bytearray(raw)
        new_raw[pos:pos + len(new_attr)] = new_attr
        new_raw[pos + len(new_attr):pos + len(new_attr) + len(tail)] = tail
        end = pos + len(new_attr) + len(tail)
        for i in range(end, self.fs.record_size):
            new_raw[i] = 0
        struct.pack_into("<I", new_raw, 0x18, _align8(end))
        self.write_record(dir_no, new_raw)

    def _record_attrs(self, rec_no: int):
        """Kayittaki oznitelikler: [(tur, ad, ham bayt)] ve ham kayit."""
        raw = self._raw_record(rec_no)
        attrs_off = struct.unpack_from("<H", raw, 0x14)[0]
        items = []
        pos = attrs_off
        while pos + 8 <= len(raw):
            a_kind = struct.unpack_from("<I", raw, pos)[0]
            if a_kind == AT_END:
                break
            length = struct.unpack_from("<I", raw, pos + 4)[0]
            if length < 16:
                break
            n_len, n_off = raw[pos + 9], struct.unpack_from("<H", raw, pos + 0x0A)[0]
            a_name = raw[pos + n_off:pos + n_off + n_len * 2].decode("utf-16-le", "replace")
            items.append([a_kind, a_name, bytearray(raw[pos:pos + length])])
            pos += length
        return items, raw

    def _write_record_attrs(self, rec_no: int, raw: bytearray, items,
                            links: Optional[int] = None) -> None:
        attrs_off = struct.unpack_from("<H", raw, 0x14)[0]
        items.sort(key=lambda it: (it[0], it[1].upper()))
        body = b"".join(bytes(it[2]) for it in items)
        if attrs_off + len(body) + 8 > self.fs.record_size:
            raise NtfsError(tr("Dizin kaydi doldu: oznitelik kayda sigmiyor"))
        new_raw = bytearray(raw[:attrs_off]) + bytearray(self.fs.record_size - attrs_off)
        new_raw[attrs_off:attrs_off + len(body)] = body
        end = attrs_off + len(body)
        struct.pack_into("<I", new_raw, end, AT_END)
        struct.pack_into("<I", new_raw, 0x18, _align8(end + 8))
        if links is not None:
            struct.pack_into("<H", new_raw, 0x12, links)
        self.write_record(rec_no, new_raw)
        self.fs._cache.pop(rec_no, None)

    @staticmethod
    def _fn_info(attr: bytes) -> Tuple[int, str, int]:
        """$FILE_NAME ozniteligi -> (ust dizin no, ad, ad alani)."""
        voff = struct.unpack_from("<H", attr, 0x14)[0]
        v = attr[voff:]
        parent = struct.unpack_from("<Q", v, 0)[0] & 0xFFFFFFFFFFFF
        n = v[0x40]
        return parent, bytes(v[0x42:0x42 + 2 * n]).decode("utf-16-le", "replace"), v[0x41]

    def _set_record_attr(self, rec_no: int, kind: int, name: str,
                         new_attr: Optional[bytes]) -> None:
        """Kayittaki (tur, ad) oznitelugunu degistirir, ekler ya da siler.

        Oznitelikler kayitta tur (sonra ad) sirasiyla durmak zorundadir;
        yeni oznitelik dogru yere konur, kimligi kaydin `next_attr_id`
        sayacindan alinir. Degisen oznitelik kimligini korur.
        """
        items, raw = self._record_attrs(rec_no)
        next_id = struct.unpack_from("<H", raw, 0x28)[0]
        replaced = False
        for item in items:
            if item[0] == kind and item[1] == name:
                if new_attr is None:
                    items.remove(item)
                else:
                    attr = bytearray(new_attr)
                    attr[0x0E:0x10] = item[2][0x0E:0x10]
                    item[2] = attr
                replaced = True
                break
        if not replaced and new_attr is not None:
            attr = bytearray(new_attr)
            struct.pack_into("<H", attr, 0x0E, next_id)
            next_id += 1
            items.append([kind, name, attr])
        struct.pack_into("<H", raw, 0x28, next_id)
        self._write_record_attrs(rec_no, raw, items)

    def index_add(self, dir_no: int, name: str, mft_ref: int, sequence: int,
                  file_attr: int, size: int, allocated: int) -> None:
        """Dizin indeksine yeni giris ekler (B+ agaci; gerekirse dugum bolunur).

        Eskiden kok ya da yaprak dolunca yazma reddediliyordu (`ntfsindex`).
        """
        rec = self.fs.record(dir_no)
        key = self._file_name_value(
            (rec.sequence << 48) | dir_no, name, file_attr, size, allocated)
        DirIndex(self, dir_no).insert(Entry((sequence << 48) | mft_ref, key))
        self.fs._cache.pop(dir_no, None)

    def index_contains(self, dir_no: int, name: str) -> bool:
        return DirIndex(self, dir_no).contains(name)

    def index_remove(self, dir_no: int, name: str) -> int:
        """Girisi siler ve hedefin MFT numarasini dondurur (ara dugumde de)."""
        ref = DirIndex(self, dir_no).remove(name)
        self.fs._cache.pop(dir_no, None)
        return ref

    # ------------------------------------------------------------------
    # Ust duzey islemler
    # ------------------------------------------------------------------
    @staticmethod
    def _split(path: str) -> Tuple[str, str]:
        norm = "/" + "/".join(p for p in path.replace("\\", "/").split("/") if p)
        if norm == "/":
            raise NtfsError(tr("Kok dizin uzerinde islem yapilamaz"))
        i = norm.rfind("/")
        return (norm[:i] or "/"), norm[i + 1:]

    def _dir_number(self, path: str) -> int:
        rec = self.fs.resolve(path)
        if not rec.is_dir:
            raise NtfsError(tr("Dizin degil: {}", path))
        return rec.number

    def write_file(self, path: str, data: bytes) -> None:
        """Dosya olusturur (varsa once siler)."""
        self.write_stream(path, io.BytesIO(data), len(data))

    def write_stream(self, path: str, src, size: int) -> None:
        """Kaynaktan `size` bayti parca parca yazar (ADR 0081)."""
        self._require_writable()
        parent_path, name = self._split(path)
        dir_no = self._dir_number(parent_path)
        try:
            self.remove(path)
        except NtfsError:
            pass

        rec_no = self.alloc_record()
        runs: List[Tuple[int, int]] = []
        try:
            self._write_file_body(rec_no, dir_no, name, src, size, runs)
        except Exception:
            # Yarida kalan yazma birimi bozmasin: kayit ve kumeler geri verilir
            if runs:
                self.free_clusters(runs)
            self.free_record(rec_no)
            self.fs._cache.clear()
            raise

    def _next_sequence(self, rec_no: int) -> int:
        """Yeniden kullanilan kaydin sira numarasi (silmede artirilmisti).

        Sira numarasi eski basvurulari gecersiz kilmak icin ileri gider; hep 1
        yazmak onu geri sariyordu. Bicimsiz/bos kayitta 1.
        """
        try:
            raw = self.fs._run_read(self.fs._mft_runs, rec_no * self.fs.record_size,
                                    self.fs.record_size)
        except Exception:                          # noqa: BLE001
            return 1
        if raw[:4] != FILE_MAGIC:
            return 1
        seq = struct.unpack_from("<H", raw, 0x10)[0]
        return seq if seq else 1

    def _write_file_body(self, rec_no: int, dir_no: int, name: str, src,
                         size: int, runs: List[Tuple[int, int]]) -> None:
        sequence = self._next_sequence(rec_no)
        parent_ref = (self.fs.record(dir_no).sequence << 48) | dir_no

        # Kucuk veri kayda yerlesir; buyuk veri kumelere yazilir.
        resident_limit = self.fs.record_size - 0x200
        if size <= resident_limit:
            data_attr = self._resident_attr(AT_DATA, read_exact(src, size),
                                            attr_id=2)
            allocated = 0
        else:
            clusters = (size + self.cs - 1) // self.cs
            runs.extend(self.alloc_clusters(clusters))
            allocated = clusters * self.cs
            data_attr = self._nonresident_attr(AT_DATA, runs, size, attr_id=2)

        attrs = [
            self._resident_attr(AT_STANDARD_INFORMATION,
                                self._std_info(0x20, self._security_id_for(dir_no)),
                                attr_id=0),
            self._resident_attr(AT_FILE_NAME,
                                self._file_name_value(parent_ref, name, 0x20,
                                                      size, allocated),
                                attr_id=1, indexed=1),
            data_attr,
        ]
        # Kayit veri yazilmadan ONCE kurulur: cok parcali dosyanin kosullari
        # kayda sigmiyorsa hata veri diske yazilmadan cikar (eskiden butun
        # veri yazildiktan sonra cikiyordu).
        raw = self._build_record(rec_no, sequence, MFT_FLAG_IN_USE, attrs)
        if runs:
            write_runs(src, size, runs, self.cs,
                       lambda lcn, off, data: self.dev.write(lcn * self.cs + off,
                                                             data))
        self.write_record(rec_no, raw)
        self.index_add(dir_no, name, rec_no, sequence, 0x20, size, allocated)
        self.fs._cache.clear()

    def mkdir(self, path: str) -> None:
        """Bos bir klasor olusturur."""
        self._require_writable()
        parent_path, name = self._split(path)
        dir_no = self._dir_number(parent_path)
        rec_no = self.alloc_record()
        sequence = self._next_sequence(rec_no)
        parent_ref = (self.fs.record(dir_no).sequence << 48) | dir_no

        attrs = [
            self._resident_attr(AT_STANDARD_INFORMATION,
                                self._std_info(FILE_ATTR_DIRECTORY,
                                               self._security_id_for(dir_no, True)),
                                attr_id=0),
            self._resident_attr(AT_FILE_NAME,
                                self._file_name_value(parent_ref, name,
                                                      FILE_ATTR_DIRECTORY),
                                attr_id=1, indexed=1),
            self._resident_attr(AT_INDEX_ROOT, self._rebuild_index_root([]),
                                name="$I30", attr_id=2),
        ]
        raw = self._build_record(rec_no, sequence,
                                 MFT_FLAG_IN_USE | MFT_FLAG_DIRECTORY, attrs)
        self.write_record(rec_no, raw)
        self.index_add(dir_no, name, rec_no, sequence, FILE_ATTR_DIRECTORY, 0, 0)
        self.fs._cache.clear()

    def remove(self, path: str) -> None:
        """Dosyayi veya **bos** klasoru siler.

        Kaydin bu dizindeki TUM adlari indeksten kalkar: Windows'un
        olusturdugu dosyada uzun ad ile 8.3 kisa ad iki ayri giris olabilir;
        eskiden yalnizca biri siliniyor, digeri serbest kaydi gosteren sahipsiz
        giris olarak kaliyordu. Baska dizinde de adi olan (sabit bag) kayit
        serbest birakilmaz; yalnizca bu dizindeki adlari kalkar.
        """
        self._require_writable()
        parent_path, name = self._split(path)
        dir_no = self._dir_number(parent_path)
        rec = self.fs.resolve(path)
        if rec.is_dir and self.fs.listdir_record(rec):
            raise NtfsError(tr("Klasor bos degil: {}", name))
        target = rec.number
        items, raw = self._record_attrs(target)
        here, elsewhere = [], []
        for it in items:
            if it[0] == AT_FILE_NAME:
                parent, fn, ns = self._fn_info(it[2])
                (here if parent == dir_no else elsewhere).append((it, fn, ns))
        for _it, fn, _ns in here:
            try:
                self.index_remove(dir_no, fn)
            except NtfsError:
                pass                              # zaten yoksa sorun degil
        if any(ns != 2 for _i, _f, ns in elsewhere):
            # sabit bag: kayit yasamaya devam eder
            keep = [it for it in items if not any(it is h[0] for h in here)]
            links = sum(1 for _i, _f, ns in elsewhere if ns != 2)
            self._write_record_attrs(target, raw, keep, links=links)
            self.fs._cache.clear()
            return
        if not rec.is_dir:
            data = rec.find(AT_DATA)
            if data is not None and not data.resident:
                self.free_clusters(data.runs)
        flags = struct.unpack_from("<H", raw, 0x16)[0]
        struct.pack_into("<H", raw, 0x16, flags & ~MFT_FLAG_IN_USE)
        sequence = struct.unpack_from("<H", raw, 0x10)[0]
        struct.pack_into("<H", raw, 0x10, (sequence + 1) & 0xFFFF or 1)
        self.write_record(target, raw)
        self.free_record(target)
        self.fs._cache.clear()

    def rename(self, path: str, new_name: str) -> None:
        """Ayni dizin icinde yeniden adlandirir.

        Hem indeks girisi HEM kayittaki $FILE_NAME degisir. Eskiden yalnizca
        indeks degisiyordu: Windows dosyayi eski adiyla gosteriyordu
        (olculdu 2026-09-29). Bu dizindeki eski adlarin hepsi (8.3 kisa ad
        dahil) kalkar, yerine tek bir Win32 adi gelir.
        """
        self._require_writable()
        parent_path, name = self._split(path)
        if "/" in new_name or "\\" in new_name:
            raise NtfsError(tr("Yeni ad yol icermemeli"))
        dir_no = self._dir_number(parent_path)
        if self.index_contains(dir_no, new_name):
            raise NtfsError(tr("Zaten var: {}", new_name))
        rec = self.fs.resolve(path)
        data = rec.find(AT_DATA)
        size = data.data_size if data is not None else 0
        allocated = data.allocated_size if (data and not data.resident) else 0
        file_attr = FILE_ATTR_DIRECTORY if rec.is_dir else 0x20
        sequence = rec.sequence
        items, raw = self._record_attrs(rec.number)
        here = [it for it in items if it[0] == AT_FILE_NAME
                and self._fn_info(it[2])[0] == dir_no]
        for it in here:
            try:
                self.index_remove(dir_no, self._fn_info(it[2])[1])
            except NtfsError:
                pass
        parent_ref = (self.fs.record(dir_no).sequence << 48) | dir_no
        new_fn = self._resident_attr(
            AT_FILE_NAME, self._file_name_value(parent_ref, new_name, file_attr,
                                                size, allocated),
            attr_id=struct.unpack_from("<H", here[0][2], 0x0E)[0] if here else 0,
            indexed=1)
        items = [it for it in items if not any(it is h for h in here)]
        items.append([AT_FILE_NAME, "", bytearray(new_fn)])
        links = sum(1 for it in items if it[0] == AT_FILE_NAME
                    and self._fn_info(it[2])[2] != 2)
        self._write_record_attrs(rec.number, raw, items, links=links)
        self.index_add(dir_no, new_name, rec.number, sequence, file_attr,
                       size, allocated)
        self.fs._cache.clear()

    def flush(self) -> None:
        f = getattr(self.dev, "flush", None)
        if f:
            f()


def _encode_runs(runs: List[Tuple[int, int]]) -> bytes:
    """(lcn, kume) listesini NTFS veri kosullarina cevirir."""
    out = bytearray()
    prev = 0
    for lcn, count in runs:
        delta = lcn - prev
        prev = lcn
        # Uzunluk da ISARETLI kodlanir: ntfs-3g ve Windows uzunlugu isaretli
        # okur. 138 kume tek bayt 0x8A yazilinca negatif sayiliyor, kosu
        # listesi "bozuk" oluyordu (ntfs-3g: mapping_pairs_decompress failed;
        # 4K kumede 512 KB-1 MB gibi araliktaki her dosya etkileniyordu).
        len_b = _signed_bytes(count)
        off_b = _signed_bytes(delta)
        out.append((len(off_b) << 4) | len(len_b))
        out += len_b
        out += off_b
    out.append(0)
    return bytes(out)


def _signed_bytes(value: int, unsigned: bool = False) -> bytes:
    """Sayiyi en az bayt ile kodlar (NTFS veri kosulu bicimi)."""
    if value == 0:
        return b"\x00"
    length = 1
    while True:
        try:
            return value.to_bytes(length, "little", signed=not unsigned)
        except OverflowError:
            length += 1
            if length > 8:
                raise NtfsError(tr("Veri kosulu degeri cok buyuk"))
