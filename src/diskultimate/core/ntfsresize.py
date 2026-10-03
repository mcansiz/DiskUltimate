"""NTFS birimini **saf Python** ile kucultup buyutur — her platformda.

## Neden bu modul var

Bu surume kadar NTFS boyutlandirmasi yalnizca Windows'ta, isletim sisteminin
kendi `Resize-Partition` komutuyla yapilabiliyordu. Linux veya macOS'ta ayni
islem **reddediliyordu**; goruntu dosyalarinda ise hicbir platformda
calismiyordu. Capraz platform iddiasi tasiyan bir arac icin bu bir eksikti
(bkz. `.claude/decisions/0030-ntfs-boyutlandirma.md`).

Diskteki islerin hepsi zaten saf Python'da yapiliyordu: NTFS bicimlendirme
(`ntfs.py`), okuma (`ntfsread.py`), yazma (`ntfswrite.py`). Boyutlandirma da
ayni yoldan gider; disari bagimlilik yoktur.

## NTFS'i boyutlandirmak ne demek

Bir NTFS biriminin boyutu su dort yerde yazar ve **hepsi** tutmak zorundadir:

1. **Onyukleme sektoru** (ofset 0x28) — birimin sektor sayisi. Son sektor
   yedek onyukleme kopyasi icin ayrilir, bu yuzden alan `bolum_sektoru - 1`
   tutar.
2. **Yedek onyukleme sektoru** — birimin *son* sektorudur; boyut degisince
   yeri de degisir.
3. **`$Bitmap`** — her kume icin bir bit. Son bayttaki fazla bitler **dolu**
   isaretlenir; yoksa olmayan kumeler bos gorunur ve tahsis edilirler.
4. **`$BadClus:$Bad`** — birim boyutunda, tumuyle "delik" olan bir akis.
   Guncellenmezse `chkdsk` birimi bozuk sayar.

## Kucultmede tasima

Kucultme, sinirin otesinde **tahsisli kume kalmamasini** ister. Gercek bir
Windows biriminde meta veri ($MFTMirr cogu kez birimin ortasinda) ve dosya
parcalari her yere dagilmistir; yalnizca "dolu ise reddet" demek kucultmeyi
pratikte imkansiz kilardi. Bu yuzden sinirin otesindeki kume araliklari
**tasinir**: yeni yer tahsis edilir, veri kopyalanir, oznitelugun veri
kosullari yeniden yazilir.

Tasinamayan durumlar **reddedilir**, tahmin yurutulmez:

  * sikistirilmis / sifrelenmis / seyrek akislar,
  * yeni veri kosullari kendi FILE kaydina sigmiyorsa,
  * birim "kirli" isaretliyse (once chkdsk / ntfsfix calismali).

Yanlis yazip bozmaktansa yazmamak yeglenir — `ntfswrite.py` ile ayni ilke.

## Islem gunlugu

Basarili bir boyutlandirmadan sonra `$LogFile` 0xFF ile doldurulur (bos
gunluk). Eski gunluk kayitlari artik var olmayan kume numaralarina gonderme
yapabilir; bir sonraki baglamada yeniden oynatilirsa birimi bozar.
`ntfsresize` de ayni seyi yapar.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .image import BlockDevice
from .ntfsread import (AT_DATA, ATTR_COMPRESSED, ATTR_ENCRYPTED, ATTR_SPARSE,
                       Attribute, NtfsError, NtfsFS)
from .ntfswrite import BITMAP_RECORD, MFTMIRR_RECORD, NtfsWriter
from ..i18n import tr

MFT_RECORD = 0                 # $MFT
LOGFILE_RECORD = 2             # $LogFile
VOLUME_RECORD = 3              # $Volume
BOOT_RECORD = 7                # $Boot
BADCLUS_RECORD = 8             # $BadClus
LOW_CLUSTERS = 4               # eski bicimlendiricinin sabit isaretledigi alan
AT_VOLUME_INFORMATION = 0x70

VOLUME_DIRTY = 0x0001          # $VOLUME_INFORMATION bayraklari
MIN_CLUSTERS = 64              # bicimlendirici de bu sinirin altina inmez
MAX_CLUSTERS = (1 << 32) - 1   # NTFS'in adresleyebildigi en yuksek kume
SCAN_WINDOW = 64 * 1024        # bitmap taramasi penceresi (bayt)
COPY_CLUSTERS = 256            # tasimada tek seferde kopyalanan kume sayisi

Progress = Optional[Callable[[str, int], None]]


class NtfsResizeError(NtfsError):
    """Boyutlandirma reddedildi ya da yarida kaldi."""


# ==========================================================================
# Bilgi
# ==========================================================================
@dataclass
class NtfsSizeInfo:
    """Bir NTFS biriminin boyutlandirma sinirlari."""

    sector_size: int = 512
    cluster_size: int = 4096
    sectors_per_cluster: int = 8
    volume_sectors: int = 0        # onyukleme sektorundeki deger + 1
    cluster_count: int = 0
    used_clusters: int = 0         # bitmap'te dolu isaretli kume sayisi
    highest_used: int = -1         # en yuksek dolu kume (yoksa -1)
    min_sectors: int = 1           # verinin gerektirdigi en dusuk boyut
    max_sectors: int = 0           # 0 = dosya sistemi tarafindan sinirsiz
    dirty: bool = False
    note: str = ""


def ntfs_size_info(view: BlockDevice) -> NtfsSizeInfo:
    """Birimi okuyup boyut sinirlarini cikarir (hicbir sey yazmaz).

    `min_sectors` **tasimayi hesaba katar**: dolu kume sayisi kadar yer
    yeterlidir, verinin nerede durdugu degil. Tasinamayan bir oznitelik
    cikarsa `ntfs_resize` islemi reddeder; sinirin iyimser olmasi
    kullaniciya hangi boyutun hedeflenebilir oldugunu gosterir.
    """
    fs = NtfsFS(view)
    cluster_count = _cluster_count(fs, fs.total_sectors)
    used, highest = _bitmap_usage(fs, cluster_count)
    spc = fs.sectors_per_cluster
    info = NtfsSizeInfo(
        sector_size=fs.sector_size, cluster_size=fs.cluster_size,
        sectors_per_cluster=spc, volume_sectors=fs.total_sectors + 1,
        cluster_count=cluster_count, used_clusters=used, highest_used=highest,
        dirty=_is_dirty(fs))
    # Tasima payi: veriyi sinirin altina sigdirmak icin bos yer de gerekir.
    needed = max(MIN_CLUSTERS, int(used * 1.05) + 16)
    info.min_sectors = needed * spc + 1
    info.max_sectors = MAX_CLUSTERS * spc + 1
    if info.dirty:
        info.note = tr("Birim 'kirli' isaretli; once chkdsk / ntfsfix "
                       "calistirilmali")
    return info


def _cluster_count(fs: NtfsFS, total_sectors: int) -> int:
    return total_sectors * fs.sector_size // fs.cluster_size


def _is_dirty(fs: NtfsFS) -> bool:
    """`$Volume` icindeki "kirli" bayragi.

    Temiz ayrilmamis bir birimde yapilan boyutlandirma, bir sonraki chkdsk
    "duzeltmesinde" veri kaybettirebilir. Alan okunamazsa kirli sayilmaz;
    karar tek basina buna dayanmaz, kullaniciya da gosterilir.
    """
    try:
        attr = fs.record(VOLUME_RECORD).find(AT_VOLUME_INFORMATION)
    except NtfsError:
        return False
    # VOLUME_INFORMATION: 8 bayt ayrilmis, surum (2 bayt), bayraklar ofset 10.
    # 2026-10-03'e kadar ofset 12 okunuyordu; deger 12 bayt oldugu icin
    # uzunluk denetimi hep "temiz" donduruyor, kirli birim boyutlandiriliyordu.
    if attr is None or not attr.resident or len(attr.value) < 12:
        return False
    return bool(struct.unpack_from("<H", attr.value, 10)[0] & VOLUME_DIRTY)


def _bitmap_attr(fs: NtfsFS) -> Attribute:
    attr = fs.record(BITMAP_RECORD).find(AT_DATA)
    if attr is None or attr.resident:
        raise NtfsResizeError(tr("$Bitmap okunamadi"))
    return attr


def _bitmap_usage(fs: NtfsFS, cluster_count: int) -> Tuple[int, int]:
    """(dolu kume sayisi, en yuksek dolu kume) — bitmap pencere pencere okunur."""
    attr = _bitmap_attr(fs)
    byte_limit = min(fs.attribute_size(attr), (cluster_count + 7) // 8)
    used = 0
    highest = -1
    base = 0
    while base < byte_limit:
        length = min(SCAN_WINDOW, byte_limit - base)
        window = fs.read_attribute_range(attr, base, length)
        if len(window) < length:
            window = window.ljust(length, b"\x00")
        count, top = bitmap_window_usage(window, cluster_count - base * 8)
        used += count
        if top >= 0:
            highest = base * 8 + top
        base += length
    return used, highest


def bitmap_window_usage(window: bytes, valid_bits: int) -> Tuple[int, int]:
    """Bir bitmap penceresinde (dolu bit sayisi, en yuksek dolu bit).

    Yalnizca ilk `valid_bits` bit sayilir (birimin son kumesinden sonrasi
    yok sayilir). Sayim bayt duzeyinde yapilir: bit sayimi `int.bit_count`,
    son dolu bayt `rstrip` ile — ikisi de C'de calisir.

    Onceki surum her biti Python dongusunde geziyordu; 217 GB'lik bir NTFS'te
    (~53 milyon kume) arayuzu saniyelerce donduruyordu (olculdu: donma
    raporu `map.handle_limits` 1.5 sn, 2026-09-28).
    """
    if valid_bits <= 0 or not window:
        return 0, -1
    full, rest = divmod(min(valid_bits, len(window) * 8), 8)
    data = bytes(window[:full])
    if rest:
        data += bytes([window[full] & ((1 << rest) - 1)])
    number = int.from_bytes(data, "little")
    if not number:
        return 0, -1
    count = (number.bit_count() if hasattr(number, "bit_count")
             else bin(number).count("1"))
    return count, number.bit_length() - 1


# ==========================================================================
# Dusuk seviye: veri kosullari ve oznitelik yeniden yazimi
# ==========================================================================
def _signed_bytes(value: int, unsigned: bool = False) -> bytes:
    if value == 0:
        return b"\x00"
    length = 1
    while length <= 8:
        try:
            return value.to_bytes(length, "little", signed=not unsigned)
        except OverflowError:
            length += 1
    raise NtfsResizeError(tr("Veri kosulu degeri cok buyuk"))


def encode_runs(runs: List[Tuple[int, int]]) -> bytes:
    """(lcn, kume) listesini NTFS veri kosullarina cevirir.

    `ntfswrite._encode_runs`'tan farki: `lcn < 0` **delik** (seyrek alan)
    olarak kodlanir, ofset alani hic yazilmaz. `$BadClus` boyle bir akistir;
    normal kodlayiciya verilseydi gercek bir kume numarasi yazilirdi.
    """
    out = bytearray()
    prev = 0
    for lcn, count in runs:
        if count <= 0:
            continue
        len_b = _signed_bytes(count, unsigned=True)
        if lcn < 0:
            out.append(len(len_b))
            out += len_b
            continue
        off_b = _signed_bytes(lcn - prev)
        prev = lcn
        out.append((len(off_b) << 4) | len(len_b))
        out += len_b
        out += off_b
    out.append(0)
    return bytes(out)


def merge_runs(runs: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Ardisik kosullari birlestirir (veri kosullari kisalir, kayda siger)."""
    out: List[Tuple[int, int]] = []
    for lcn, count in runs:
        if count <= 0:
            continue
        if out:
            onceki_lcn, onceki_adet = out[-1]
            if lcn < 0 and onceki_lcn < 0:
                out[-1] = (-1, onceki_adet + count)
                continue
            if lcn >= 0 and onceki_lcn >= 0 and onceki_lcn + onceki_adet == lcn:
                out[-1] = (onceki_lcn, onceki_adet + count)
                continue
        out.append((lcn, count))
    return out


def _set_nonresident(writer: NtfsWriter, rec_no: int, attr: Attribute,
                     runs: List[Tuple[int, int]],
                     data_size: Optional[int] = None,
                     initialized: Optional[int] = None,
                     allocated: Optional[int] = None,
                     keep_sizes: bool = False) -> None:
    """Yerlesik olmayan bir oznitelugun kosullarini ve boyutlarini degistirir.

    Oznitelik kayit icinde **yerinde** buyutulur/kucultulur; gerekirse
    arkasindaki oznitelikler kaydirilir. Kayda sigmiyorsa islem reddedilir:
    bu surumde `$ATTRIBUTE_LIST` uretilmez, yani oznitelik baska bir kayda
    tasinamaz.

    `keep_sizes`: oznitelik bir **uzanti kaydinda** duruyorsa (start_vcn > 0)
    boyut alanlari yalnizca ilk parcada anlamlidir; orada duran degerlere
    dokunulmaz.
    """
    runs = merge_runs(runs)
    raw = writer._raw_record(rec_no)
    pos = writer._find_attr_offset(raw, attr)
    attr_len = struct.unpack_from("<I", raw, pos + 4)[0]
    used = struct.unpack_from("<I", raw, 0x18)[0]
    mapping_off = struct.unpack_from("<H", raw, pos + 0x20)[0]
    pairs = encode_runs(runs)
    new_len = (mapping_off + len(pairs) + 7) & ~7
    delta = new_len - attr_len
    if used + delta > len(raw):
        raise NtfsResizeError(
            tr("Veri kosullari FILE kaydina sigmadi (kayit {})", rec_no))

    head = bytes(raw[pos:pos + mapping_off])
    tail = bytes(raw[pos + attr_len:used])
    body = bytearray(len(raw))
    body[:pos] = raw[:pos]
    body[pos:pos + mapping_off] = head
    body[pos + mapping_off:pos + mapping_off + len(pairs)] = pairs
    body[pos + new_len:pos + new_len + len(tail)] = tail
    struct.pack_into("<I", body, pos + 4, new_len)
    struct.pack_into("<I", body, 0x18, used + delta)

    clusters = sum(c for _lcn, c in runs)
    struct.pack_into("<Q", body, pos + 0x18,
                     attr.start_vcn + max(0, clusters - 1))          # son VCN
    if not keep_sizes:
        alloc = clusters * writer.cs if allocated is None else allocated
        size = attr.data_size if data_size is None else data_size
        init = (min(attr.initialized_size, size) if initialized is None
                else initialized)
        struct.pack_into("<QQQ", body, pos + 0x28, alloc, size, init)
    writer.write_record(rec_no, body)


def _refresh(fs: NtfsFS, rec_no: int, kind: int, name: str = "") -> Attribute:
    """Kaydi yeniden okuyup oznitelugu tazeler (yazmadan sonra sart)."""
    fs._cache.pop(rec_no, None)
    attr = fs.record(rec_no).find(kind, name)
    if attr is None:
        raise NtfsResizeError(tr("Oznitelik kayboldu (kayit {})", rec_no))
    return attr


# ==========================================================================
# Bitmap yazimi
# ==========================================================================
def _mark_range(writer: NtfsWriter, first: int, last: int, value: bool) -> None:
    """[first, last) kume araligini bitmap'te dolu/bos isaretler."""
    if last <= first:
        return
    fs = writer.fs
    attr = _bitmap_attr(fs)
    size = fs.attribute_size(attr)
    lo = first >> 3
    hi = min(size, (last + 7) >> 3)
    base = lo
    while base < hi:
        length = min(SCAN_WINDOW, hi - base)
        block = bytearray(fs.read_attribute_range(attr, base, length))
        if len(block) < length:
            block += bytearray(length - len(block))
        first_bit = base * 8
        for index in range(max(first, first_bit),
                           min(last, (base + length) * 8)):
            local = index - first_bit
            if value:
                block[local >> 3] |= 1 << (local & 7)
            else:
                block[local >> 3] &= ~(1 << (local & 7)) & 0xFF
        writer._write_attr_range(attr, bytes(block), base)
        base += length


# ==========================================================================
# Onarim: eski bicimlendiricinin sahipsiz kumeleri
# ==========================================================================
def _owned_in(fs: NtfsFS, rec_no: int, lo: int, hi: int) -> set:
    """Kaydin kendi yerlesik olmayan ozniteliklerinin [lo, hi) icindeki kumeleri."""
    out = set()
    try:
        rec = fs.record(rec_no)
    except NtfsError:
        return out
    if not rec.in_use:
        return out
    for attr in rec.attributes:
        if attr.resident or not attr.runs:
            continue
        for lcn, count in attr.runs:
            if lcn is None or lcn < 0:
                continue
            out.update(range(max(lo, lcn), min(hi, lcn + count)))
    return out


def repair_orphan_low_clusters(writer: NtfsWriter, progress: Progress = None) -> int:
    """Kume 0-3'te dolu isaretli ama hicbir dosyanin olmayan kumeleri bosaltir.

    Bu surumden onceki bicimlendirici (ADR 0074) `$Boot` 2 kume iken 0-3'u
    dolu isaretliyordu. Veri kaybi yoktur ama `ntfsresize` birimi "tutarsiz"
    sayip calismaz, chkdsk duzeltmek ister. Onarim dar tutulur:

    * yalnizca kume 0-3'e bakilir; `$Boot`'un kumeleri asla bosaltilmaz,
    * aday varsa **butun MFT** taranir; herhangi bir kaydin sahiplendigi kume
      bosaltilmaz (sahiplik bilinmeden bit silmek veri bozar),
    * aday yoksa (Windows'un ya da yeni bicimlendiricinin birimi) MFT
      taranmaz, hicbir sey yazilmaz.

    Bosaltilan kume sayisini dondurur.
    """
    fs = writer.fs
    attr = _bitmap_attr(fs)
    first = fs.read_attribute_range(attr, 0, 1)
    if not first:
        return 0
    marked = {c for c in range(LOW_CLUSTERS) if first[0] >> c & 1}
    candidates = marked - _owned_in(fs, BOOT_RECORD, 0, LOW_CLUSTERS)
    if not candidates:
        return 0
    record_count = max(1, fs.mft_size // fs.record_size)
    for rec_no in range(record_count):
        if rec_no % 4096 == 0:
            fs._cache.clear()
            if progress:
                progress(tr("MFT taraniyor... {}/{} kayit", rec_no, record_count), -1)
        candidates -= _owned_in(fs, rec_no, 0, LOW_CLUSTERS)
        if not candidates:
            return 0
    for cluster in sorted(candidates):
        _mark_range(writer, cluster, cluster + 1, False)
    return len(candidates)


def ntfs_repair(view: BlockDevice, progress: Progress = None) -> int:
    """Birimi acip `repair_orphan_low_clusters` uygular ve yazar."""
    fs = NtfsFS(view)
    writer = NtfsWriter(fs)
    writer._require_writable()
    if _is_dirty(fs):
        raise NtfsResizeError(
            tr("Birim 'kirli' isaretli. Once Bolum > NTFS'i denetle ve onar "
               "(ya da Windows'ta chkdsk) calistirin; kirli bir birimi "
               "boyutlandirmak veri kaybettirebilir."))
    count = repair_orphan_low_clusters(writer, progress)
    if count:
        writer.flush()
    return count


# ==========================================================================
# Tasima (kucultme icin)
# ==========================================================================
def _copy_clusters(fs: NtfsFS, src: int, dst: int, count: int) -> None:
    """Kume araligini kopyalar (parca parca, bellek sismeden)."""
    cs = fs.cluster_size
    moved = 0
    while moved < count:
        chunk = min(COPY_CLUSTERS, count - moved)
        data = fs.dev.read((src + moved) * cs, chunk * cs)
        fs.dev.write((dst + moved) * cs, data)
        moved += chunk


def _relocate_attr(writer: NtfsWriter, rec_no: int, attr: Attribute,
                   boundary: int, report) -> int:
    """Oznitelugun sinir otesindeki kume araliklarini sinirin altina tasir.

    Yalnizca **etkilenen parcalar** tasinir: 100 GB'lik bir dosyanin son 200
    MB'i sinirin otesindeyse 200 MB kopyalanir, tamami degil. VCN sirasi
    korundugu icin dosya icerigi degismez.
    """
    fs = writer.fs
    if attr.resident:
        return 0
    if attr.flags & (ATTR_COMPRESSED | ATTR_ENCRYPTED | ATTR_SPARSE):
        raise NtfsResizeError(
            tr("Sikistirilmis/sifrelenmis akis tasinamaz (kayit {})", rec_no))

    # Iki asama: once **butun** yeni yerler tahsis edilir, sonra kopyalanir.
    # Tahsis `$Bitmap`'e yazar; bitmap'in kendisi tasiniyorsa araya giren bir
    # tahsis, az once kopyalanmis bir bolgeyi bayatlatirdi.
    plan: List[Tuple[int, List[Tuple[int, int]]]] = []   # (kaynak lcn, hedef)
    new_runs: List[Tuple[int, int]] = []
    moved = 0
    for lcn, count in attr.runs:
        if lcn < 0 or lcn + count <= boundary:
            new_runs.append((lcn, count))
            continue
        if lcn < boundary:            # kosul siniri kesiyor: ikiye ayrilir
            left = boundary - lcn
            new_runs.append((lcn, left))
            lcn += left
            count -= left
        target = writer.alloc_clusters(count)
        plan.append((lcn, target))
        new_runs.extend(target)
        moved += count

    for source_lcn, target in plan:
        source = source_lcn
        for target_lcn, target_count in target:
            _copy_clusters(fs, source, target_lcn, target_count)
            source += target_count
            report(target_count)

    if not moved:
        return 0
    new_runs = merge_runs(new_runs)

    # $MFT kendini tasiyorsa kayit **yeni yerine** yazilmalidir: `write_record`
    # kaydin diskteki yerini `fs._mft_runs` uzerinden bulur. Zincir once
    # guncellenir; yeni yerdeki kopya veriyle esittir, cunku tasinan kumeler
    # birebir kopyalandi.
    if rec_no == MFT_RECORD and attr.kind == AT_DATA:
        fs._mft_runs = list(new_runs)
        fs.mft_lcn = new_runs[0][0]

    _set_nonresident(writer, rec_no, attr, new_runs,
                     keep_sizes=attr.start_vcn > 0)

    if rec_no == MFT_RECORD and attr.kind == AT_DATA:
        _reload_mft(writer)
    elif rec_no == MFTMIRR_RECORD and attr.kind == AT_DATA:
        fs.mftmirr_lcn = new_runs[0][0]
    return moved


def _relocate_beyond(writer: NtfsWriter, boundary: int, old_clusters: int,
                     progress: Progress) -> int:
    """Sinirin otesindeki butun tahsisli kumeleri sinirin altina tasir.

    Once sinir otesi bitmap'te **dolu** isaretlenir: boylece tasima sirasinda
    yapilan tahsisler oraya dusemez. Bu isaretleme kalici bir zarar degildir;
    bitmap zaten kucultulecektir.
    """
    fs = writer.fs
    _mark_range(writer, boundary, old_clusters, True)

    total = max(1, old_clusters - boundary)
    moved = [0]

    def report(count: int) -> None:
        moved[0] += count
        if progress:
            progress(tr("NTFS verisi tasiniyor... {}/{} kume",
                        moved[0], total),
                     min(60, 5 + int(55 * moved[0] / total)))

    # $MFT kendi kaydini tasiyabilmek icin once ele alinir: kayitlar onun
    # kume zinciri uzerinden okunur, once o yerine oturmalidir.
    order = [MFT_RECORD]
    record_count = max(1, fs.mft_size // fs.record_size)
    order += [n for n in range(record_count) if n != MFT_RECORD]

    for position, rec_no in enumerate(order):
        # Kayit onbellegi sinirsiz buyumemeli: milyonlarca kayitli bir MFT
        # tarandiginda bellek tukenirdi. Onbellek yalnizca hiz icindir.
        if position % 4096 == 0:
            fs._cache.clear()
            if progress:
                progress(tr("MFT taraniyor... {}/{} kayit",
                            position, record_count), -1)
        try:
            rec = fs.record(rec_no)
        except NtfsError:
            continue
        if not rec.in_use:
            continue
        for attr in rec.attributes:          # yalnizca bu kaydin kendi ozniteligi
            if attr.resident or not attr.runs:
                continue
            if not any(lcn >= 0 and lcn + count > boundary
                       for lcn, count in attr.runs):
                continue
            _relocate_attr(writer, rec_no, attr, boundary, report)
    return moved[0]


def _reload_mft(writer: NtfsWriter) -> None:
    """$MFT tasindiktan sonra kume zincirini tazeler."""
    fs = writer.fs
    fs._cache.clear()
    fs._load_mft()


# ==========================================================================
# Bitmap / $BadClus / onyukleme guncellemeleri
# ==========================================================================
def _resize_bitmap(writer: NtfsWriter, old_clusters: int,
                   new_clusters: int) -> None:
    """`$Bitmap` akisini yeni kume sayisina gore kucultur/buyutur.

    Bitmap'in **kendi** kumeleri de bu akistadir; buyurken yeni kume gerekirse
    once tahsis edilir (tahsis eski bitmap icinde yapilir, guvenlidir).
    """
    fs = writer.fs
    cs = fs.cluster_size
    attr = _bitmap_attr(fs)
    new_bytes = (new_clusters + 7) // 8
    need_clusters = max(1, (new_bytes + cs - 1) // cs)
    have_clusters = sum(c for _l, c in attr.runs)
    released: List[Tuple[int, int]] = []

    if need_clusters > have_clusters:
        extra = writer.alloc_clusters(need_clusters - have_clusters)
        for lcn, count in extra:                       # yeni alan sifirlanir
            writer.dev.write(lcn * cs, b"\x00" * (count * cs))
        attr = _refresh(fs, BITMAP_RECORD, AT_DATA)
        runs_out = merge_runs(list(attr.runs) + extra)
    elif need_clusters < have_clusters:
        runs_out = []
        left = need_clusters
        for lcn, count in attr.runs:
            if left <= 0:
                released.append((lcn, count))
                continue
            if count <= left:
                runs_out.append((lcn, count))
                left -= count
            else:
                runs_out.append((lcn, left))
                released.append((lcn + left, count - left))
                left = 0
    else:
        runs_out = list(attr.runs)

    # Yeni kapsama alani icindeki bitler yazilmadan boyut alanlari
    # buyutulmemeli: arada okunan bitmap eksik kalirdi.
    if new_clusters > old_clusters:
        _set_nonresident(writer, BITMAP_RECORD, attr, runs_out,
                         data_size=new_bytes, initialized=new_bytes)
        attr = _refresh(fs, BITMAP_RECORD, AT_DATA)
        fs.total_sectors = new_clusters * (cs // fs.sector_size)
        _mark_range(writer, old_clusters, new_clusters, False)
        _mark_range(writer, new_clusters, new_bytes * 8, True)
    else:
        fs.total_sectors = new_clusters * (cs // fs.sector_size)
        _mark_range(writer, new_clusters, new_bytes * 8, True)
        attr = _refresh(fs, BITMAP_RECORD, AT_DATA)
        _set_nonresident(writer, BITMAP_RECORD, attr, runs_out,
                         data_size=new_bytes, initialized=new_bytes)
        if released:
            attr = _refresh(fs, BITMAP_RECORD, AT_DATA)
            writer.free_clusters([(lcn, count) for lcn, count in released
                                  if lcn + count <= new_clusters])


def _resize_badclus(writer: NtfsWriter, new_clusters: int) -> None:
    """`$BadClus:$Bad` akisini birim boyutuna esitler."""
    fs = writer.fs
    try:
        attr = fs.record(BADCLUS_RECORD).find(AT_DATA, "$Bad")
    except NtfsError:
        return
    if attr is None or attr.resident:
        return
    size_bytes = new_clusters * fs.cluster_size
    _set_nonresident(writer, BADCLUS_RECORD, attr, [(-1, new_clusters)],
                     data_size=size_bytes, initialized=0, allocated=size_bytes)


def _reset_logfile(writer: NtfsWriter) -> None:
    """`$LogFile` alanini 0xFF ile doldurur (bos gunluk)."""
    fs = writer.fs
    try:
        attr = fs.record(LOGFILE_RECORD).find(AT_DATA)
    except NtfsError:
        return
    if attr is None or attr.resident:
        return
    cs = fs.cluster_size
    block = b"\xFF" * cs
    for lcn, count in attr.runs:
        if lcn < 0:
            continue
        for i in range(count):
            fs.dev.write((lcn + i) * cs, block)


def _write_boot(view: BlockDevice, fs: NtfsFS, volume_sectors: int,
                partition_offset: Optional[int] = None) -> None:
    """Onyukleme sektorunu yeni boyutla yazar ve yedegini son sektore koyar."""
    ss = fs.sector_size
    boot = bytearray(view.read(0, ss))
    if boot[3:11] != b"NTFS    ":
        raise NtfsResizeError(tr("NTFS onyukleme sektoru taninmadi"))
    struct.pack_into("<Q", boot, 0x28, volume_sectors - 1)
    struct.pack_into("<Q", boot, 0x30, fs.mft_lcn)
    struct.pack_into("<Q", boot, 0x38, fs.mftmirr_lcn)
    if partition_offset is not None:
        struct.pack_into("<I", boot, 28, partition_offset & 0xFFFFFFFF)
    view.write(0, bytes(boot))
    view.write((volume_sectors - 1) * ss, bytes(boot))
    flush = getattr(view, "flush", None)
    if flush:
        flush()


# ==========================================================================
# Ana islev
# ==========================================================================
def ntfs_resize(view: BlockDevice, new_sector_count: int,
                partition_offset: Optional[int] = None,
                progress: Progress = None) -> None:
    """NTFS birimini `new_sector_count` sektore getirir.

    Kucultmede cagiran taraf bolum tablosunu **sonra** gunceller, buyutmede
    **once** (bkz. `resize.py`); bu islev her iki durumda da yalnizca birimin
    kendi yapilarini duzenler.
    """
    def report(message: str, percent: int) -> None:
        if progress:
            progress(message, max(0, min(100, percent)))

    fs = NtfsFS(view)
    writer = NtfsWriter(fs)
    writer._require_writable()

    ss, cs = fs.sector_size, fs.cluster_size
    spc = cs // ss
    old_clusters = _cluster_count(fs, fs.total_sectors)
    new_total = new_sector_count - 1                 # son sektor yedek onyukleme
    new_clusters = new_total * ss // cs
    if new_clusters < MIN_CLUSTERS:
        raise NtfsResizeError(tr("Yeni boyut NTFS icin cok kucuk"))
    if new_clusters > MAX_CLUSTERS:
        raise NtfsResizeError(tr("NTFS en fazla {} kume adresler", MAX_CLUSTERS))
    if _is_dirty(fs):
        raise NtfsResizeError(
            tr("Birim 'kirli' isaretli. Once Bolum > NTFS'i denetle ve onar "
               "(ya da Windows'ta chkdsk) calistirin; kirli bir birimi "
               "boyutlandirmak veri kaybettirebilir."))

    # Eski bicimlendiricinin sahipsiz kumeleri (ADR 0074): boyutlandirma
    # zaten yaziyor, birim tutarli birakilir.
    repair_orphan_low_clusters(writer, progress)

    if new_clusters == old_clusters:
        report(tr("Onyukleme sektoru guncelleniyor..."), 90)
        _write_boot(view, fs, new_sector_count, partition_offset)
        writer.flush()
        report(tr("Tamamlandi"), 100)
        return

    if new_clusters < old_clusters:
        report(tr("Kume haritasi inceleniyor..."), 2)
        kullanilan, _en_yuksek = _bitmap_usage(fs, old_clusters)
        if kullanilan > new_clusters:
            raise NtfsResizeError(
                tr("Veri yeni boyuta sigmiyor: {} kume dolu, yeni boyut {} "
                   "kume", kullanilan, new_clusters))
        _relocate_beyond(writer, new_clusters, old_clusters, progress)

    report(tr("Kume haritasi guncelleniyor..."), 70)
    _resize_bitmap(writer, old_clusters, new_clusters)
    report(tr("$BadClus guncelleniyor..."), 80)
    _resize_badclus(writer, new_clusters)
    report(tr("Islem gunlugu sifirlaniyor..."), 85)
    _reset_logfile(writer)
    report(tr("Onyukleme sektoru guncelleniyor..."), 92)
    fs.total_sectors = new_total
    _write_boot(view, fs, new_sector_count, partition_offset)
    writer.flush()
    report(tr("Tamamlandi"), 100)
