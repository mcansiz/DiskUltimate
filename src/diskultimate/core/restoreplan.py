"""Yedegi hedef diske **yeni bir bolum yerlesimiyle** geri yukleme.

## Neden

Duz geri yukleme (`clone.restore`) yedegi bayt bayt yazar: hedef yedekten
kucukse reddedilir, buyukse fazlasi bos kalir ve GPT'nin yedek basligi eski
disk sonunda kalir. DiskGenius'un "Bolumleri Yonet" dugmesi ise yedekteki
bolumleri hedef diske **yeniden yerlestirir**: bolumler buyutulup
kucultulebilir, kaydirilabilir; dosya sistemi yeni boyuta getirilir.

## Yerlesim

Yerlesim modeli ortak cekirdektedir: `layoutedit.EditableLayout` (ADR 0049;
ana ekranla ayni model). Bu modul onun **yedek -> hedef baglayicisidir**:
`build_layout` modeli yedekten kurar, `restore_with_layout` sonucu yazar.
Sinirlar `resize.fs_resize_info` ile **yedegin icinden** okunur
(`Slot.apply_limits`):

  * FAT / exFAT / NTFS: en az = verinin gerektirdigi, en cok = FS siniri.
  * Dosya sistemi tanimlanamayan bolum ("ham"): **kucultulmez** (icerigini
    bilmiyoruz; BitLocker da ham gorunur), buyutulebilir ama eklenen alan
    kullanilmaz.
  * Boyutlandirilamayan dosya sistemi (ext vb.): boyut sabittir, yer degisir.

Genisletilmis MBR bolumu kullanicinin duzenledigi bir oge degildir; icindeki
mantiksal bolumlere gore her seferinde yeniden hesaplanir.

## Uygulama sirasi (`restore_with_layout`)

  1. Bolumlerden onceki alan (MBR onyukleme kodu, GRUB'un MBR boslugu)
     yedekten kopyalanir.
  2. Her bolum yeni yerine kopyalanir. **Kucultulen** bolumde yedek salt
     okunur oldugu icin dosya sistemi bellekteki bir yazma katmani
     (`_Overlay`) uzerinde kucultulur, sonra yeni boyut kadari yazilir.
  3. Bolum tablosu hedefin boyutuna gore yeniden yazilir (GPT yedek basligi
     yeni disk sonuna gider).
  4. **Buyutulen** bolumde dosya sistemi hedef uzerinde buyutulur; yalnizca
     yeri degisen bolumde onyukleme sektorundeki bolum konumu duzeltilir.
"""
from __future__ import annotations

import copy
from typing import Callable, Dict, Optional

from .clone import CloneError, DubImage, read_backup_info
from .gpt import GPTTable
from .image import BlockDevice, PartitionView
from .layoutedit import EditableLayout, LayoutError, Slot
from .mbr import MBRTable
from .ptable import MBR_EXTENDED_TYPES, PartitionTable, human_size
from .resize import (_fs_resize, _patch_partition_offset, fs_resize_info)
from ..i18n import tr

Progress = Optional[Callable[[str, int], None]]

COPY_CHUNK = 4 * 1024 * 1024
OVERLAY_CHUNK = 64 * 1024


class RestorePlanError(LayoutError):
    pass


# --------------------------------------------------------------------------
#  Yerlesim
# --------------------------------------------------------------------------
# Yerlesim modeli ortak cekirdege tasindi (ADR 0049). Eski adlar geriye
# uyumluluk icin korunur: arayuz ve testler bunlari kullanir.
RestoreLayout = EditableLayout
LayoutPart = Slot


def build_layout(image: BlockDevice, table: PartitionTable) -> EditableLayout:
    """Yedek goruntusunun tablosundan yerlesim cikarir (hedef = kaynak).

    Dosya sistemi sinirlari yedegin icinden okunur; NTFS'te `$Bitmap`
    taranir, yani buyuk yedekte birkac saniye surebilir — arka planda
    cagrilir.
    """
    from .fsdetect import detect

    layout = EditableLayout(scheme=table.scheme,
                           sector_size=image.sector_size,
                           source_sectors=image.sector_count,
                           target_sectors=image.sector_count)
    if isinstance(table, GPTTable):
        layout.gpt_first_usable = table._first_usable
        layout.gpt_entry_sectors = table.entry_sectors
    for part in table.sorted_partitions():
        if not part.logical and part.scheme == "mbr" and \
                part.type_id in MBR_EXTENDED_TYPES:
            layout.extended = copy.copy(part)
            continue
        view = PartitionView(image, part.start_lba, part.sector_count)
        try:
            found = detect(view)
            fs_type, label = found.fs_type, found.label
            part.fs_used = found.used_bytes
        except Exception:                             # noqa: BLE001
            fs_type, label = "", ""
        info = fs_resize_info(view, fs_type)
        item = Slot(part=copy.copy(part), old_start=part.start_lba,
                    old_count=part.sector_count, new_start=part.start_lba,
                    new_count=part.sector_count, fs_type=fs_type, label=label)
        # Geri yuklemede bolum kopyalanir: baslangic her zaman tasinabilir
        item.apply_limits(info.kind, info.min_sectors, info.max_sectors,
                          info.note, movable=True)
        layout.parts.append(item)
    return layout


def _read_table(device: BlockDevice) -> Optional[PartitionTable]:
    if GPTTable.is_present(device):
        return GPTTable.read(device)
    # Onyukleme sektoru de 0x55AA ile biter: tek bolum yedeginin (FAT/NTFS)
    # onyukleme kodu bolum girisi diye okunmasin.
    if MBRTable.is_present(device) and MBRTable.entries_plausible(device):
        return MBRTable.read(device)
    return None


def read_layout(src_path: str) -> Optional[EditableLayout]:
    """`.dub` yedeginin yerlesimi; bolum tablosu yoksa None."""
    image = DubImage(src_path)
    try:
        table = _read_table(image)
        if table is None or not table.partitions:
            return None
        return build_layout(image, table)
    finally:
        image.close()


# --------------------------------------------------------------------------
#  Bellekte yazma katmani
# --------------------------------------------------------------------------
class _Overlay(BlockDevice):
    """Salt okunur bir aygitin ustunde bellekte duran yazma katmani.

    Kucultme, dosya sisteminin meta verisini (FAT/bitmap, NTFS'te sondan
    tasinan kumeler) yeniden yazar. Yedek dosyasi salt okunurdur; yazilar
    burada tutulur ve okumada yedegin uzerine bindirilir.
    """

    def __init__(self, base: BlockDevice):
        self._base = base
        self.sector_size = base.sector_size
        self._chunks: Dict[int, bytearray] = {}

    @property
    def sector_count(self) -> int:
        return self._base.sector_count

    @property
    def overlay_bytes(self) -> int:
        return len(self._chunks) * OVERLAY_CHUNK

    def _chunk(self, no: int) -> bytearray:
        data = self._chunks.get(no)
        if data is None:
            start = no * OVERLAY_CHUNK
            length = min(OVERLAY_CHUNK, self.size - start)
            data = bytearray(self._base.read(start, length))
            self._chunks[no] = data
        return data

    def read(self, offset: int, length: int) -> bytes:
        if offset + length > self.size:
            raise RestorePlanError(tr("Okuma bolum sinirini asiyor"))
        out = bytearray()
        pos = offset
        end = offset + length
        while pos < end:
            no, inner = divmod(pos, OVERLAY_CHUNK)
            n = min(OVERLAY_CHUNK - inner, end - pos)
            data = self._chunks.get(no)
            if data is not None:
                out += data[inner:inner + n]
            else:
                # yazilmamis parcalar tek okumada birlestirilir
                run = n
                while pos + run < end and \
                        (pos + run) // OVERLAY_CHUNK not in self._chunks:
                    run += min(OVERLAY_CHUNK, end - pos - run)
                out += self._base.read(pos, run)
                n = run
            pos += n
        return bytes(out)

    def write(self, offset: int, data: bytes) -> None:
        if offset + len(data) > self.size:
            raise RestorePlanError(tr("Yazma bolum sinirini asiyor"))
        pos = 0
        while pos < len(data):
            no, inner = divmod(offset + pos, OVERLAY_CHUNK)
            n = min(OVERLAY_CHUNK - inner, len(data) - pos)
            self._chunk(no)[inner:inner + n] = data[pos:pos + n]
            pos += n


# --------------------------------------------------------------------------
#  Uygulama
# --------------------------------------------------------------------------
def _copy_region(src: BlockDevice, src_lba: int, dst: BlockDevice,
                 dst_lba: int, count: int, tick: Callable[[int], None]) -> None:
    """Sektorleri kopyalar; bos (sifir) parcada hedef zaten sifirsa yazmaz."""
    ss = src.sector_size
    step = max(1, COPY_CHUNK // ss)
    pos = 0
    while pos < count:
        n = min(step, count - pos)
        data = src.read_sectors(src_lba + pos, n)
        if data.strip(b"\x00") or \
                dst.read_sectors(dst_lba + pos, n).strip(b"\x00"):
            dst.write_sectors(dst_lba + pos, data)
        pos += n
        tick(n)


def restore_with_layout(src_path: str, device: BlockDevice,
                        layout: EditableLayout,
                        progress: Progress = None):
    """`.dub` disk yedegini `layout` yerlesimiyle `device` uzerine yazar."""
    def report(message: str, percent: int) -> None:
        if progress:
            progress(message, max(0, min(100, percent)))

    info = read_backup_info(src_path)
    if device.sector_count != layout.target_sectors:
        raise RestorePlanError(
            tr("Yerlesim baska bir hedef boyutu icin hazirlanmis; yenileyin"))
    if getattr(device, "readonly", False):
        raise CloneError(tr("Hedef salt okunur"))
    problem = layout.validate()
    if problem:
        raise RestorePlanError(problem)

    src = DubImage(src_path)
    try:
        if src.sector_size != device.sector_size:
            raise RestorePlanError(
                tr("Sektor boyutu farkli: yedek {}, hedef {}",
                   src.sector_size, device.sector_size))
        table = _read_table(src)
        if table is None or table.scheme != layout.scheme:
            raise RestorePlanError(tr("Yedegin bolum tablosu okunamadi"))

        ss = device.sector_size
        total = sum(min(p.old_count, p.new_count) for p in layout.parts) or 1
        done = [0]

        def tick(n: int) -> None:
            done[0] += n
            report(tr("Geri yukleniyor... {} / {}",
                      human_size(done[0] * ss), human_size(total * ss)),
                   5 + int(85 * done[0] / total))

        # 1) bolumlerden onceki alan: onyukleme kodu ve MBR boslugu
        report(tr("Geri yukleme baslatiliyor..."), 1)
        head = min([p.old_start - layout._reserved_before(p)
                    for p in layout.parts]
                   + [p.new_start - layout._reserved_before(p)
                      for p in layout.parts])
        _copy_region(src, 0, device, 0, max(1, head), lambda n: None)
        if layout.scheme == "mbr":
            # Hedefte eskiden GPT varsa yedek basligi diskin sonunda kalir;
            # bazi sistemler onu bulup MBR'yi yok sayar.
            last = device.sector_count - 1
            if device.read_sectors(last)[:8] == b"EFI PART":
                device.write_sectors(last, b"\x00" * ss)

        # 2) bolumler yeni yerlerine
        for part in layout.sorted_parts():
            source: BlockDevice = PartitionView(src, part.old_start,
                                                part.old_count)
            if part.shrinks and part.fs_resizable:
                report(tr("Bolum {}: dosya sistemi kucultuluyor...",
                          part.index), 5 + int(85 * done[0] / total))
                overlay = _Overlay(source)
                _fs_resize(overlay, 0, part.new_count, part.kind,
                           part.new_start, span=part.old_count)
                source = overlay
            _copy_region(source, 0, device, part.new_start,
                         min(part.old_count, part.new_count), tick)

        # 3) bolum tablosu hedefin boyutuna gore
        report(tr("Bolum tablosu yaziliyor..."), 91)
        if layout.scheme == "gpt":
            new_table = GPTTable(device)
            new_table.disk_guid = table.disk_guid
            new_table.entry_count = table.entry_count
            new_table.entry_size = table.entry_size
            new_table._first_usable = layout.first_usable()
        else:
            new_table = MBRTable(device)
            new_table.bootcode = table.bootcode
            new_table.disk_signature = table.disk_signature
        new_table.partitions = layout.partitions()
        new_table.write()

        # 4) buyutme ve konum duzeltme hedefin uzerinde
        for part in layout.sorted_parts():
            if part.grows and part.fs_resizable:
                report(tr("Bolum {}: dosya sistemi buyutuluyor...",
                          part.index), 93)
                _fs_resize(device, part.new_start, part.new_count, part.kind,
                           part.new_start)
            elif part.moves and not part.shrinks:
                _patch_partition_offset(device, part.new_start,
                                        part.new_count)
        flush = getattr(device, "flush", None)
        if flush:
            flush()
    finally:
        src.close()
    report(tr("Tamamlandi"), 100)
    return info
