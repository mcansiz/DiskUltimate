"""Bekleyen islemler uygulaninca diskin **nasil gorunecegini** hesaplar.

## Neden

Kuyruk (ADR 0025) diske hicbir sey yazmaz; bu dogru ve guvenlidir ama bir yan
etkisi vardi: kullanici bolum olusturup silince ana ekran **hic degismiyordu**.
Degisiklik yalnizca kenardaki "Bekleyen islemler" listesinde ve tablodaki kum
saati isaretinde goruluyordu. Benzer araclarda (DiskGenius, EaseUS, AOMEI,
MiniTool) ana harita her zaman **planlanan** yerlesimi gosterir: yeni bolum
haritada hemen belirir, silinen kaybolur, boyutlandirilan yeni boyutuyla
cizilir. Kullanici "Uygula"ya basmadan once sonucu gorur.

Bu modul o "sonuc"u uretir: bolum tablosuna **dokunmadan**, kuyruktaki
adimlari bellekteki kopyalar uzerinde sirayla isleyerek.

## Sinirlar (bilerek)

Bu bir **onizlemedir**, benzetim degil. Dosya sistemi ici ayrintilar
(bicimlendirme sonrasi gercek doluluk, NTFS meta veri boyutu) hesaplanmaz;
bilinmeyen degerler "bilinmiyor" kalir (`fs_used = -1`). Bir adim
uygulanamayacaksa bunu onizleme degil, uygulama soyler — yanlis bir "olur"
gostermemek icin onizleme adimlari **dogrulamaz**, yalnizca gosterir.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .ptable import (GPT_UNUSED, FreeRegion, Partition, PartitionTable,
                     human_size)
from . import fsregistry
from ..i18n import tr

# Bolumun bekleyen adimlara gore durumu (`Partition.plan_state`)
STATE_NEW = "new"            # kuyrukta olusturulacak
STATE_CHANGED = "changed"    # boyutu/turu/etiketi degisecek
STATE_FORMAT = "format"      # bicimlendirilecek (veri gider)
STATE_DELETED = "deleted"    # silinecek
STATE_WIPE = "wipe"          # guvenli silinecek

GPT_TAIL_SECTORS = 33        # yedek GPT basligi + giris dizisi


@dataclass
class PlannedLayout:
    """Kuyruk uygulandiginda ortaya cikacak yerlesim."""

    partitions: List[Partition] = field(default_factory=list)
    free: List[FreeRegion] = field(default_factory=list)
    scheme: str = "mbr"
    notes: Dict[int, List[str]] = field(default_factory=dict)
    disk_notes: List[str] = field(default_factory=list)
    changed: bool = False
    # Uygulama sirasinda diger bolumlerle cakisacak adimlarin sirasi (0
    # tabanli). Her adim **o anki** yerlesime gore denetlenir: kuyruk bu
    # sirayla uygulanamazsa burada gorunur (ADR 0049).
    conflicts: List[int] = field(default_factory=list)

    def note_for(self, index: int) -> List[str]:
        return self.notes.get(index, [])


def project(session, queue) -> PlannedLayout:
    """Kuyruktaki adimlari bellekte isleyip beklenen yerlesimi dondurur.

    Kaynak nesnelere **dokunulmaz**: bolumlerin derin kopyasi uzerinde
    calisilir. Bilinmeyen bir adim turu yerlesimi degistirmez, yalnizca
    notlara girer — onizleme hicbir zaman "biliyormus gibi" yapmaz.
    """
    table: Optional[PartitionTable] = getattr(session, "table", None)
    sector_size = session.image.sector_size
    total_sectors = session.image.sector_count
    scheme = session.scheme if table is not None else ""
    parts: List[Partition] = [copy.deepcopy(p) for p in session.partitions]
    for p in parts:
        p.plan_state = ""
    layout = PlannedLayout(scheme=scheme or "mbr")

    def note(index: int, text: str) -> None:
        layout.notes.setdefault(index, []).append(text)

    # Tasinan bolumun eski capasi -> yeni baslangici (uygulamadaki
    # `OperationQueue._follow_move` karsiligi)
    moved: Dict[int, int] = {}

    def clashes(target: Partition, start: int, count: int) -> bool:
        last = start + count - 1
        for other in parts:
            if other is target:
                continue
            if other.scheme == "mbr" and other.type_id in (0x05, 0x0F, 0x85):
                continue                       # kapsayici
            if other.logical != getattr(target, "logical", False):
                continue                       # farkli katman
            if start <= other.end_lba and last >= other.start_lba:
                return True
        return False

    def find(index: int, at_lba: int = -1) -> Optional[Partition]:
        """Adimin hedefledigi bolum.

        Once **capa** (baslangic LBA) aranir: bolum numarasi kalici bir kimlik
        degildir ve uygulama sirasinda kayabilir (ADR 0033). Onizleme ile
        uygulamanin ayni bolumu secmesi icin ikisi de ayni olcutu kullanir.
        """
        if at_lba is not None and at_lba >= 0:
            seen = set()
            while at_lba in moved and at_lba not in seen:
                seen.add(at_lba)             # dongu korumasi
                at_lba = moved[at_lba]
            for p in parts:
                if p.start_lba == at_lba:
                    return p
            return None
        for p in parts:
            if p.index == index:
                return p
        return None

    def next_index(logical: bool = False) -> int:
        """Yeni bolumun alacagi numara — tablo kodunun kuraliyla ayni (P4):
        birincil/GPT bolumu ilk bos yuvayi, MBR mantiksal bolumu zincirin
        sonundaki numarayi (5'ten) alir. Eskiden max+1 tahmin ediliyordu;
        silmeden sonra onizlemedeki numara gercek numaradan sapiyordu."""
        if logical:
            return max([p.index for p in parts if p.index >= 5], default=4) + 1
        used = {p.index for p in parts}
        index = 1
        while index in used:
            index += 1
        return index

    for position, op in enumerate(queue):
        params = op.params
        kind = op.kind
        layout.changed = True
        index = params.get("index")
        at_lba = params.get("at_lba", -1)

        if kind in ("create_table", "clear_table"):
            parts = []
            scheme = params.get("scheme", scheme)
            layout.disk_notes.append(str(op))
        elif kind == "convert_table":
            scheme = params.get("scheme", scheme)
            for p in parts:
                p.scheme = scheme
                p.plan_state = p.plan_state or STATE_CHANGED
            layout.disk_notes.append(str(op))
        elif kind == "create":
            created = _new_partition(params, scheme, sector_size,
                                     next_index(bool(params.get("logical", False))))
            if clashes(created, created.start_lba, created.sector_count):
                layout.conflicts.append(position)
            parts.append(created)
            note(created.index, str(op))
        elif kind == "delete":
            p = find(index, at_lba)
            if p is not None:
                parts.remove(p)
            note(index, str(op))
        elif kind == "resize":
            p = find(index, at_lba)
            if p is not None:
                new_start = params.get("start_lba", p.start_lba)
                new_count = params.get("sector_count", p.sector_count)
                if clashes(p, new_start, new_count):
                    layout.conflicts.append(position)
                if new_start != p.start_lba:
                    moved[p.start_lba] = new_start
                p.start_lba = new_start
                p.sector_count = new_count
                p.fs_used = -1          # doluluk orani artik bilinmiyor
                p.fs_total = -1
                p.plan_state = STATE_CHANGED
            note(index, str(op))
        elif kind == "format":
            p = find(index, at_lba)
            if p is not None:
                p.fs_type = _fs_name(params.get("fs_key", ""))
                p.fs_label = params.get("label", "")
                p.fs_used, p.fs_total = -1, -1
                p.plan_state = STATE_FORMAT
            note(index, str(op))
        elif kind in ("label", "rename"):
            p = find(index, at_lba)
            if p is not None:
                if kind == "label":
                    p.fs_label = params.get("label", p.fs_label)
                else:
                    p.name = params.get("name", p.name)
                p.plan_state = p.plan_state or STATE_CHANGED
            note(index, str(op))
        elif kind == "type":
            p = find(index, at_lba)
            if p is not None:
                if params.get("type_id"):
                    p.type_id = params["type_id"]
                if params.get("type_guid"):
                    p.type_guid = params["type_guid"]
                p.plan_state = p.plan_state or STATE_CHANGED
            note(index, str(op))
        elif kind == "boot":
            p = find(index, at_lba)
            if p is not None:
                p.bootable = bool(params.get("value"))
                p.plan_state = p.plan_state or STATE_CHANGED
            note(index, str(op))
        elif kind == "wipe_partition":
            if index is None or index < 0:
                parts = []
                layout.disk_notes.append(str(op))
            else:
                p = find(index, at_lba)
                if p is not None:
                    p.fs_type = ""
                    p.fs_label = ""
                    p.fs_used, p.fs_total = -1, -1
                    p.plan_state = STATE_WIPE
                note(index, str(op))
        elif kind == "resize_image":
            new_size = params.get("size", 0)
            if new_size > 0:
                total_sectors = new_size // sector_size
            layout.disk_notes.append(str(op))
        else:
            # Yerlesimi degistirmeyen adimlar (onyukleme kodu vb.)
            if isinstance(index, int) and index >= 0:
                note(index, str(op))
            else:
                layout.disk_notes.append(str(op))

    layout.scheme = scheme or "mbr"
    layout.partitions = sorted(parts, key=lambda p: (p.logical, p.start_lba))
    layout.free = _free_regions(layout.partitions, total_sectors, sector_size,
                                layout.scheme, table)
    return layout


def _fs_name(fs_key: str) -> str:
    """Kuyruktaki dosya sistemi anahtarini gosterilecek ada cevirir."""
    names = {"fat12": "FAT12", "fat16": "FAT16", "fat32": "FAT32",
             "exfat": "exFAT", "ntfs": "NTFS", "ext2": "ext2",
             "ext3": "ext3", "ext4": "ext4", "swap": fsregistry.SWAP,
             "hfsplus": "HFS+", "udf": "UDF", "xfs": "XFS", "refs": "ReFS"}
    return names.get((fs_key or "").lower(), fs_key.upper() if fs_key else "")


def _new_partition(params: dict, scheme: str, sector_size: int,
                   index: int) -> Partition:
    """Kuyruktaki "yeni bolum" adimindan gorunur bir bolum uretir."""
    part = Partition(
        index=index,
        start_lba=params.get("start_lba", 0),
        sector_count=params.get("sector_count", 0),
        scheme=scheme or "mbr",
        type_id=params.get("type_id", 0x83) or 0x83,
        type_guid=params.get("type_guid", "") or GPT_UNUSED,
        name=params.get("name", ""),
        bootable=bool(params.get("bootable", False)),
        logical=bool(params.get("logical", False)),
        fs_type=(params.get("found_fs", "") if params.get("keep_data")
                 else _fs_name(params.get("fs_key", ""))),
        fs_label=params.get("label", ""),
        sector_size=sector_size)
    part.plan_state = STATE_NEW
    return part


def _usable_bounds(total_sectors: int, sector_size: int, scheme: str,
                   table: Optional[PartitionTable]) -> tuple:
    """(hiza, ilk_kullanilabilir, son_kullanilabilir) LBA.

    Sinirlar mumkunse gercek tablodan alinir; sema kuyrukta degistiyse
    (MBR <-> GPT) tablodan alinamaz, cunku tablo hala eski semadadir. O
    durumda semanin bilinen sabitleri kullanilir.
    """
    align = getattr(table, "align_sectors", 0) or (1024 * 1024 // sector_size)
    if table is not None and table.scheme == scheme:
        return (align, table.first_usable_lba(),
                min(table.last_usable_lba(), total_sectors - 1))
    if scheme == "gpt":
        return align, align, total_sectors - GPT_TAIL_SECTORS - 1
    return align, align, total_sectors - 1


def _free_regions(partitions: List[Partition], total_sectors: int,
                  sector_size: int, scheme: str,
                  table: Optional[PartitionTable]) -> List[FreeRegion]:
    """Planlanan yerlesimdeki bos alanlar."""
    align, first, last = _usable_bounds(total_sectors, sector_size, scheme,
                                        table)

    regions: List[FreeRegion] = []
    cursor = first
    for p in sorted([p for p in partitions if not p.logical],
                    key=lambda x: x.start_lba):
        if p.start_lba > cursor:
            begin = ((cursor + align - 1) // align) * align
            stop = p.start_lba - 1
            if stop >= begin:
                regions.append(FreeRegion(begin, stop - begin + 1, sector_size))
        cursor = max(cursor, p.end_lba + 1)
    if cursor <= last:
        begin = ((cursor + align - 1) // align) * align
        if last >= begin:
            regions.append(FreeRegion(begin, last - begin + 1, sector_size))
    return regions


def overlap_at(layout: PlannedLayout, start_lba: int, sector_count: int,
               ignore_lba: int = -1) -> Optional[Partition]:
    """Verilen araligi kesen **planlanan** bolum (yoksa `None`).

    Kuyruk diske dokunmadigi icin "diskte bos mu?" sorusu yeterli degildir:
    az once kuyruga eklenen bir bolum diskte hala yoktur ama o alani
    tutmustur. Yeni bir adim eklenirken bu islevle bakilir, boylece cakisma
    uygulama aninda degil **tiklama aninda** yakalanir (ADR 0034).

    `ignore_lba` kendini kesiyor sayilmamasi gereken bolumun baslangicidir
    (boyutlandirmada bolumun kendisi).
    """
    if sector_count <= 0:
        return None
    last = start_lba + sector_count - 1
    for part in layout.partitions:
        if part.start_lba == ignore_lba:
            continue
        # Genisletilmis bolum bir kapsayicidir: mantiksal bolumler onun
        # icinde durur, bu bir cakisma degildir.
        if part.scheme == "mbr" and part.type_id in (0x05, 0x0F, 0x85):
            continue
        if start_lba <= part.end_lba and last >= part.start_lba:
            return part
    return None


def summary(layout: PlannedLayout) -> str:
    """Onizleme seridinde gosterilecek tek satirlik ozet."""
    created = sum(1 for p in layout.partitions if p.plan_state == STATE_NEW)
    changed_count = sum(
        1 for p in layout.partitions
        if p.plan_state in (STATE_CHANGED, STATE_FORMAT, STATE_WIPE))
    parts_text = []
    if created:
        parts_text.append(tr("{} yeni bolum", created))
    if changed_count:
        parts_text.append(tr("{} degisen bolum", changed_count))
    total_bytes = sum(p.sector_count * p.sector_size for p in layout.partitions)
    parts_text.append(tr("toplam {}", human_size(total_bytes)))
    return ", ".join(parts_text)
