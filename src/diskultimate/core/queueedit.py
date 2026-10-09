"""Disk + kuyruk baglayicisi: ortak yerlesim modelini ana ekrana baglar.

ADR 0049, 2. asama. `layoutedit.EditableLayout` kaynaktan bagimsizdir; bu
modul onu **acik bir diskten ve bekleyen islem kuyrugundan** kurar ve
duzenlenen sonucu yine kuyruga **boyutlandirma adimlari** olarak yazar.
Diske hicbir sey yazilmaz (ADR 0025).

Kurallar:

* Model **planlanan** yerlesimden kurulur (ADR 0048): onceki bir adimin
  actigi bos alan komsu bolum icin kullanilabilir.
* Bir bolumun zaten bekleyen boyutlandirmasi varsa yeni adim eklenmez, o
  adim **yerinde** guncellenir; diskteki haline donduyse kaldirilir.
* Kuyrugun **bu sirayla uygulanabilir** oldugu `planview.project` ile
  denetlenir (her adim o anki yerlesime gore). Bozulursa yalnizca bu islemin
  degistirdigi adimlar sona alinir; yine olmuyorsa kuyruk eski haline doner
  ve hata verilir. Uygulama aninda her adim ayrica diskteki tabloya gore
  yeniden dogrulanir.
* Duzenlenmeyen bolumler kilitlidir (engel olarak durur): kuyrukta
  olusturulacak bolum, genisletilmis kapsayici, sinirlari henuz hesaplanmamis
  bolum. Mantiksal bolumler bu modelde yer almaz (EBR zinciri; kapsayici
  engel olarak durur).
"""
from __future__ import annotations

import copy
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import operations as ops
from . import planview
from .gpt import GPTTable
from .layoutedit import EditableLayout, LayoutError, Slot
from .ptable import MBR_EXTENDED_TYPES, Partition
from .resize import FsResizeInfo, ResizeWindow
from ..i18n import tr

LimitsLookup = Callable[[Partition], Optional[FsResizeInfo]]


def build(session, queue, limits: Optional[LimitsLookup] = None
          ) -> Optional[EditableLayout]:
    """Oturum + kuyruktan duzenlenebilir yerlesim (tablo yoksa None).

    `limits(diskteki_bolum)` dosya sistemi sinirlarini dondurur; None
    donerse (henuz hesaplanmadi) bolum kilitli ve `pending` olur.
    """
    table = getattr(session, "table", None)
    if table is None:
        return None
    plan = planview.project(session, queue) if len(queue) else None
    shown = plan.partitions if plan is not None else session.partitions
    scheme = plan.scheme if plan is not None else table.scheme
    if scheme != table.scheme:
        return None               # sema kuyrukta degisiyor: duzenleme yok
    image = session.image
    layout = EditableLayout(scheme=scheme, sector_size=image.sector_size,
                            source_sectors=image.sector_count,
                            target_sectors=image.sector_count)
    if isinstance(table, GPTTable):
        layout.gpt_first_usable = table._first_usable
        layout.gpt_entry_sectors = table.entry_sectors
    on_disk = {p.index: p for p in table.partitions}
    # 1 MiB'dan once baslayan diskteki bolum (SD kart: LBA 1) gecerli
    # sayilir; yoksa yerlesim bastan "cakisiyor" olur ve hicbir bolum
    # boyutlandirilamazdi. Ayni kural: ptable.check_range.
    first = min([table.first_usable_lba()]
                + [p.start_lba for p in table.partitions
                   if not p.logical and p.start_lba > 0])
    layout.usable_override = (first,
                              min(table.last_usable_lba(),
                                  image.sector_count - 1))
    for part in shown:
        if part.logical:
            continue
        disk = (on_disk.get(part.index)
                if part.plan_state != planview.STATE_NEW else None)
        base = disk or part
        slot = Slot(part=copy.copy(part), old_start=base.start_lba,
                    old_count=base.sector_count, new_start=part.start_lba,
                    new_count=part.sector_count, fs_type=part.fs_type,
                    label=part.fs_label)
        extended = part.scheme == "mbr" and part.type_id in MBR_EXTENDED_TYPES
        if disk is None or extended:
            slot.lock()
        else:
            info = limits(disk) if limits is not None else None
            if info is None:
                slot.lock(pending=True)
            else:
                slot.apply_limits(info.kind, info.min_sectors,
                                  info.max_sectors, info.note,
                                  movable=info.movable)
        layout.parts.append(slot)
    return layout


def neighbours(layout: EditableLayout, index: int) -> List[int]:
    """Bolum ve planlanan komsularinin numaralari (sinir istemek icin)."""
    ordered = layout.sorted_parts()
    for pos, slot in enumerate(ordered):
        if slot.index == index:
            around = ordered[max(0, pos - 1):pos + 2]
            return [s.index for s in around]
    return [index]


def commit(session, queue, layout: EditableLayout, indices: Iterable[int],
           before: Optional[Dict[int, Tuple[int, int]]] = None,
           fs_infos: Optional[Dict[int, FsResizeInfo]] = None
           ) -> List[ops.Operation]:
    """Duzenlenen bolumleri kuyruga yazar; eklenen/guncellenen adimlari dondurur.

    `before`: duzenlemeden onceki planlanan (baslangic, sektor) — sirayi
    belirler: **yer acan adim once** (sinir tutamaginda kuculen komsu,
    buyuyenden once calismali). Hata olursa kuyruk degismeden kalir.
    """
    problem = layout.validate()
    if problem:
        raise LayoutError(problem)
    fs_infos = fs_infos or {}
    ss = session.image.sector_size
    saved = queue.items
    changes = []
    for index in dict.fromkeys(indices):
        slot = layout.get(index)
        if slot.locked:
            continue
        changes.append((slot, session.table.get(index)))

    def frees_space(slot: Slot) -> bool:
        start, count = (before or {}).get(slot.index,
                                          (slot.old_start, slot.old_count))
        return slot.new_start >= start and slot.new_end <= start + count - 1

    changes.sort(key=lambda c: 0 if frees_space(c[0]) else 1)
    touched: List[ops.Operation] = []
    try:
        for slot, disk in changes:
            position = queue.find_resize(slot.index, disk.start_lba)
            if (slot.new_start, slot.new_count) == (disk.start_lba,
                                                     disk.sector_count):
                if position >= 0:
                    queue.remove(position)      # diskteki haline dondu
                continue
            lower, upper = layout.window(slot.index)
            plan = session.plan_resize(
                slot.index, slot.new_start, slot.new_count,
                fs_info=fs_infos.get(slot.index),
                window=ResizeWindow(lower, upper, ss))
            if not plan.changed:
                continue
            operation = ops.resize_op(slot.index, slot.new_start,
                                      slot.new_count, ss,
                                      at_lba=disk.start_lba)
            operation.detail = plan.summary()
            if position >= 0:
                queue.replace(position, operation)
            else:
                queue.add(operation)
            touched.append(operation)
        _ensure_order(session, queue, touched)
    except Exception:
        queue.restore(saved)
        raise
    return touched


def _ensure_order(session, queue, touched: List[ops.Operation]) -> None:
    """Kuyruk bu sirayla uygulanabilir mi? Degilse yalnizca kendi adimlarini
    sona tasiyarak duzeltir; olmuyorsa LayoutError."""
    for _ in range(len(queue) + 1):
        conflicts = planview.project(session, queue).conflicts
        if not conflicts:
            return
        position = conflicts[0]
        operation = queue[position]
        mine = any(operation is t for t in touched)
        if not mine or position == len(queue) - 1:
            raise LayoutError(
                tr("Adimlar bu sirayla uygulanamaz: '{}' baska bir bolumle "
                   "cakisiyor", operation))
        queue.move(position, len(queue) - 1)
    raise LayoutError(tr("Adimlar icin uygulanabilir bir sira bulunamadi"))
