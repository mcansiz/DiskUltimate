"""Duzenlenebilir bolum yerlesimi — kaynaktan bagimsiz ortak model (ADR 0049).

Ana ekran (diskteki bolumleri yerinde boyutlandirma, bekleyen islem kuyrugu)
ve yedek geri yukleme (yedekteki bolumleri hedef diske yerlestirme) ayni isi
yapar: bolumleri buyut / kucult / tasi. Eskiden bunun iki ayri kodu vardi ve
hatalar birinde duzeltilip otekinde kaliyordu. Bu modul ortak cekirdektir:

* **Slot** — tek bolum: eski (kaynaktaki) ve yeni (planlanan) yeri, dosya
  sistemi sinirlari (en az / en cok sektor), tasinabilirlik.
* **EditableLayout** — bolumlerin tamami + hedef disk boyutu: hareket
  penceresi (`window`), suruklenebilir kenarlar (`edges`, `move_edge`),
  dogrulama (`validate`), hazir duzenler (`reset`, `fit`, `extend_last`).

Model hicbir sey okumaz ve yazmaz. Doldurmak ve sonucu uygulamak
**baglayicilarin** isidir (`restoreplan.build_layout` / `restore_with_layout`;
2. asamada disk + kuyruk baglayicisi). Arayuz import etmez.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import List, Optional

from .ptable import ALIGN_BYTES, FreeRegion, Partition, human_size
from ..i18n import tr

FS_KINDS = ("fat", "exfat", "ntfs")      # saf Python'da boyutlandirilabilenler
# Boyutu degisebilen turler: yukaridakiler + isletim sisteminin kendi
# boyutlandiricisi (Windows fiziksel disk, `FsResizeInfo.kind == "native"`).
RESIZABLE_KINDS = FS_KINDS + ("native",)


class LayoutError(Exception):
    pass


@dataclass
class Slot:
    """Duzenlenen bir bolum: diskteki (eski) ve planlanan (yeni) yeri + sinirlari."""
    part: Partition                  # yedekteki tanim (tip, GUID, ad)
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    kind: str = "raw"                # FsResizeInfo.kind
    fs_type: str = ""
    label: str = ""
    min_count: int = 1
    max_count: int = 0               # 0 = sinirsiz
    note: str = ""
    # Baslangic tasinabilir mi? Geri yuklemede hep True (bolum kopyalanir);
    # yerinde tasimada dosya sisteminin sinirindan gelir (ADR 0049).
    movable: bool = True
    # Kilitli bolum duzenlenmez, yalnizca komsulari icin engeldir: kuyrukta
    # henuz olusturulacak bolum, genisletilmis kapsayici, sinirlari henuz
    # hesaplanmamis bolum (`pending`). Kilitli bolumun kenari suruklenmez.
    locked: bool = False
    pending: bool = False            # sinirlar arka planda hesaplaniyor

    @property
    def index(self) -> int:
        return self.part.index

    @property
    def logical(self) -> bool:
        return self.part.logical

    @property
    def resizable(self) -> bool:
        """Boyutu degistirilebilir mi (ham bolum yalnizca buyuyebilir)."""
        return not self.locked and (self.kind in RESIZABLE_KINDS
                                    or self.kind == "raw")

    @property
    def fs_resizable(self) -> bool:
        return self.kind in FS_KINDS

    @property
    def moves(self) -> bool:
        return self.new_start != self.old_start

    @property
    def grows(self) -> bool:
        return self.new_count > self.old_count

    @property
    def shrinks(self) -> bool:
        return self.new_count < self.old_count

    @property
    def new_end(self) -> int:
        return self.new_start + self.new_count - 1

    def clamp(self, count: int) -> int:
        count = max(self.min_count, count)
        if self.max_count:
            count = min(self.max_count, count)
        return count

    def apply_limits(self, kind: str, min_sectors: int = 1,
                     max_sectors: int = 0, note: str = "",
                     movable: bool = True) -> None:
        """Dosya sistemi sinirlarini bolume uygular (`FsResizeInfo` karsiligi).

        * FAT / exFAT / NTFS: verinin gerektirdigi en az, FS'in izin verdigi
          en cok.
        * Ham (tanimlanamayan): **kucultulmez** — icerigi bilinmiyor
          (BitLocker da ham gorunur); buyutulebilir ama eklenen alan bos kalir.
        * Boyutlandirilamayan FS (ext vb.): boyut sabit.
        """
        self.kind, self.note, self.movable = kind, note, movable
        if kind in RESIZABLE_KINDS:
            self.min_count = max(1, min_sectors)
            self.max_count = max_sectors
        elif kind == "raw":
            self.min_count, self.max_count = self.old_count, 0
        else:
            self.min_count = self.max_count = self.old_count

    def lock(self, pending: bool = False) -> None:
        """Bolumu duzenlemeye kapatir (bkz. `locked`)."""
        self.locked, self.pending = True, pending
        self.movable = False
        self.min_count = self.max_count = self.new_count

    def as_partition(self) -> Partition:
        """Yeni yerindeki bolum (harita ve tablo icin kopya)."""
        part = copy.copy(self.part)
        part.start_lba = self.new_start
        part.sector_count = self.new_count
        part.fs_type = self.fs_type
        part.fs_label = self.label
        if part.logical:
            part.ebr_lba = 0          # EBR yeni baslangica gore hesaplanir
        return part


@dataclass
class EditableLayout:
    """Duzenlenebilir bolum yerlesimi (kaynaktan bagimsiz)."""
    scheme: str                       # 'gpt' | 'mbr'
    sector_size: int
    source_sectors: int
    target_sectors: int
    parts: List[Slot] = field(default_factory=list)
    extended: Optional[Partition] = None      # MBR genisletilmis kapsayici
    gpt_first_usable: int = 0
    gpt_entry_sectors: int = 32
    # (ilk, son) kullanilabilir LBA — verilirse tablodan hesaplanan degerler
    # yerine bu kullanilir. Diskteki tablo baglayicisi gercek sinirlari verir
    # (MBR'de ilk bolum 1 MiB'dan once baslamasin).
    usable_override: Optional[tuple] = None

    # -- sinirlar ------------------------------------------------------------
    @property
    def align(self) -> int:
        return max(1, ALIGN_BYTES // self.sector_size)

    def first_usable(self) -> int:
        if self.usable_override:
            return self.usable_override[0]
        # GPTTable.first_usable_lba ile ayni: baslik bu degeri yazar
        if self.scheme == "gpt":
            return max(self.gpt_first_usable or 2 + self.gpt_entry_sectors,
                       self.align)
        return 1

    def last_usable(self) -> int:
        if self.usable_override:
            return self.usable_override[1]
        if self.scheme == "gpt":
            return self.target_sectors - 2 - self.gpt_entry_sectors
        return self.target_sectors - 1

    def align_up(self, lba: int) -> int:
        return -(-lba // self.align) * self.align

    def align_down(self, lba: int) -> int:
        return (lba // self.align) * self.align

    def sorted_parts(self) -> List[Slot]:
        return sorted(self.parts, key=lambda p: (p.new_start, p.index))

    def get(self, index: int) -> Slot:
        for part in self.parts:
            if part.index == index:
                return part
        raise LayoutError(tr("{} numarali bolum yok", index))

    def _reserved_before(self, part: Slot) -> int:
        """Bolumun onunde ayrilmasi gereken sektor (mantiksalda EBR)."""
        return self.align if part.logical else 0

    # -- durum ---------------------------------------------------------------
    @property
    def is_identity(self) -> bool:
        """Yerlesim yedektekiyle ayni ve hedef ayni boyutta mi?"""
        return (self.target_sectors == self.source_sectors
                and all(not p.moves and p.new_count == p.old_count
                        for p in self.parts))

    @property
    def changed(self) -> bool:
        return any(p.moves or p.new_count != p.old_count for p in self.parts)

    def required_sectors(self) -> int:
        """En siki yerlesimde hedefin en az kac sektor olmasi gerektigi."""
        trial = self.copy()
        trial.target_sectors = 1 << 62
        trial._pack(0.0)
        end = max((p.new_end for p in trial.parts), default=0)
        tail = (2 + self.gpt_entry_sectors) if self.scheme == "gpt" else 1
        return end + tail

    def validate(self) -> str:
        """Yerlesimdeki ilk hata ("" = gecerli)."""
        if self.target_sectors <= 0:
            return tr("Hedef boyutu bilinmiyor")
        previous_end = self.first_usable() - 1
        for part in self.sorted_parts():
            if part.new_count <= 0:
                return tr("Bolum {}: boyut sifir olamaz", part.index)
            if part.new_count < part.min_count:
                return tr("Bolum {}: {} altina inemez (veri kaybi olurdu)",
                          part.index,
                          human_size(part.min_count * self.sector_size))
            if part.max_count and part.new_count > part.max_count:
                return tr("Bolum {}: dosya sistemi en fazla {} olabilir",
                          part.index,
                          human_size(part.max_count * self.sector_size))
            if not part.locked and not part.resizable and \
                    part.new_count != part.old_count:
                return tr("Bolum {}: {} boyutlandirilamaz",
                          part.index, part.fs_type or tr("bu dosya sistemi"))
            if part.new_start - self._reserved_before(part) <= previous_end:
                return tr("Bolum {} onceki bolumle cakisiyor", part.index)
            if part.new_end > self.last_usable():
                return tr("Bolum {} hedef diskin sonunu asiyor "
                          "(hedef {}, gereken en az {})", part.index,
                          human_size(self.target_sectors * self.sector_size),
                          human_size(self.required_sectors()
                                     * self.sector_size))
            previous_end = part.new_end
        if self.scheme == "mbr":
            primaries = [p for p in self.parts if not p.logical]
            if len(primaries) + (1 if self.extended is not None else 0) > 4:
                return tr("MBR en fazla 4 birincil bolum destekler")
            if self.extended is not None:
                logicals = [p for p in self.sorted_parts() if p.logical]
                ext_start = logicals[0].new_start - self.align
                ext_end = logicals[-1].new_end
                for p in primaries:
                    if not (p.new_end < ext_start or p.new_start > ext_end):
                        return tr("Bolum {} mantiksal bolumlerin arasina "
                                  "giremez", p.index)
        return ""

    # -- stratejiler ---------------------------------------------------------
    def copy(self) -> "EditableLayout":
        clone = copy.copy(self)
        clone.parts = [copy.copy(p) for p in self.parts]
        return clone

    def reset(self) -> None:
        """Yedekteki yerlesime dondurur."""
        for part in self.parts:
            part.new_start, part.new_count = part.old_start, part.old_count

    def retarget(self, target_sectors: int) -> None:
        """Hedef boyutunu degistirir ve yerlesimi ona uydurur.

        Yedekteki yerlesim sigiyorsa oldugu gibi kalir (fazla alan bos kalir);
        sigmiyorsa bolumler **gerektigi kadar** kucultulur.
        """
        self.target_sectors = target_sectors
        self.reset()
        if self.validate():
            self.fit(expand=False)

    def fit(self, expand: bool = True) -> bool:
        """Bolumleri orantili olarak hedefe yayar.

        `expand=False` yalnizca sigdirmak icin kucultur; `True` bos alani da
        bolumlere dagitir. Sigmiyorsa False doner ve yerlesim degismez.
        """
        if any(not p.movable or p.locked for p in self.parts):
            # Bolumler bastan dizilir, yani baslangiclar kayar; tasinamayan
            # bolum varsa bu duzen uygulanamaz.
            return False
        low, high = 0.0, 1.0
        if expand:
            high = max(1.0, 2.0 * self.target_sectors
                       / max(1, self.source_sectors))
        saved = [(p.new_start, p.new_count) for p in self.parts]
        if not self._pack(0.0):
            for part, (start, count) in zip(self.parts, saved):
                part.new_start, part.new_count = start, count
            return False
        if self._pack(high):
            return True
        for _ in range(48):
            middle = (low + high) / 2
            if self._pack(middle):
                low = middle
            else:
                high = middle
        self._pack(low)
        return True

    def extend_last(self) -> bool:
        """Diskte en sondaki boyutlandirilabilir bolumu bos alana yayar."""
        ordered = self.sorted_parts()
        for part in reversed(ordered):
            if not part.resizable or part.locked:
                continue
            after = [p for p in ordered if p.new_start > part.new_start]
            upper = (min(p.new_start - self._reserved_before(p)
                         for p in after) - 1 if after
                     else self.last_usable())
            count = upper - part.new_start + 1
            if part.max_count:
                count = min(count, part.max_count)
            if count < part.min_count:
                return False
            part.new_count = count
            return True
        return False

    def _pack(self, factor: float) -> bool:
        """Bolumleri eski sirasiyla arka arkaya dizer; boyut = eski * factor."""
        # Ilk bolum yedekteki yerinden baslar (genelde 1 MiB); onundeki
        # bosluk GRUB gibi onyukleyicilerin alanidir, daraltilmaz.
        cursor = self.first_usable()
        if self.parts:
            cursor = max(cursor, min(p.old_start - self._reserved_before(p)
                                     for p in self.parts))
        for part in sorted(self.parts, key=lambda p: p.old_start):
            cursor += self._reserved_before(part)
            start = self.align_up(cursor)
            if part.resizable:
                count = part.clamp(self.align_down(int(part.old_count * factor)))
                if not part.fs_resizable:
                    count = max(count, part.old_count)
            else:
                count = part.old_count
            part.new_start, part.new_count = start, count
            cursor = start + count
        return cursor - 1 <= self.last_usable()

    def window(self, index: int) -> tuple:
        """Bolumun komsulari arasinda hareket edebilecegi (ilk, son) LBA."""
        part = self.get(index)
        ordered = self.sorted_parts()
        pos = ordered.index(part)
        lower = self.first_usable()
        if pos > 0:
            lower = ordered[pos - 1].new_end + 1
        lower += self._reserved_before(part)
        upper = self.last_usable()
        if pos + 1 < len(ordered):
            nxt = ordered[pos + 1]
            upper = nxt.new_start - self._reserved_before(nxt) - 1
        return lower, upper

    # -- surukleme (DiskGenius haritasindaki tutamaklar) -------------------
    def edges(self) -> List[tuple]:
        """Suruklenebilir kenarlar: (lba, tur, sol_bolum, sag_bolum).

        tur: 'start' (bolumun sol kenari, onunde bosluk var), 'end' (sag
        kenar, arkasinda bosluk var) ya da 'boundary' (iki bitisik bolumun
        ortak siniri — surukleyince biri buyur, oteki kuculur). Bolum yoksa
        -1. **Oynayamayan kenar listelenmez** (orn. boyutu sabit ext4'un
        siniri): tutamak gorunup de tepki vermemesi yaniltici olurdu.
        """
        ordered = self.sorted_parts()
        out = []
        for i, part in enumerate(ordered):
            prev = ordered[i - 1] if i else None
            gap = self._reserved_before(part)
            if prev is not None and prev.new_end + 1 + gap == part.new_start:
                if self._movable("boundary", prev.index, part.index):
                    out.append((prev.new_end + 1, "boundary", prev.index,
                                part.index))
                else:
                    # Ortak sinir birlikte kayamiyor (biri sabit/tasinamaz):
                    # oynayabilen taraf TEK BASINA kuculebilsin, arada bos
                    # alan acilsin. Bitisik ext4'un yanindaki FAT bolumu
                    # yoksa sagdan hic kucultulemezdi.
                    out.append((prev.new_end + 1, "end", prev.index, -1))
                    out.append((part.new_start, "start", -1, part.index))
            else:
                out.append((part.new_start, "start", -1, part.index))
            nxt = ordered[i + 1] if i + 1 < len(ordered) else None
            if nxt is None or part.new_end + 1 + self._reserved_before(nxt) \
                    != nxt.new_start:
                out.append((part.new_end + 1, "end", part.index, -1))
        return [e for e in out if self._movable(*e[1:])]

    def _edge_range(self, kind: str, left: int, right: int) -> tuple:
        """Kenarin gidebilecegi [en_az, en_cok] LBA araligi."""
        big = 1 << 62
        if kind == "end":
            part = self.get(left)
            if part.locked:
                return part.new_end + 1, part.new_end + 1
            _lower, upper = self.window(left)
            return (part.new_start + part.min_count,
                    min(upper + 1, part.new_start + (part.max_count or big)))
        if kind == "start":
            part = self.get(right)
            if not part.movable or part.locked:
                return part.new_start, part.new_start      # oynamaz
            lower, _upper = self.window(right)
            end = part.new_end + 1
            return (max(lower, end - (part.max_count or big)),
                    end - part.min_count)
        a, b = self.get(left), self.get(right)
        if not b.movable or a.locked or b.locked:
            x = b.new_start - self._reserved_before(b)
            return x, x                                  # sag bolum tasinamaz
        gap = self._reserved_before(b)
        b_end = b.new_end + 1
        return (max(a.new_start + a.min_count,
                    b_end - gap - (b.max_count or big)),
                min(a.new_start + (a.max_count or big),
                    b_end - gap - b.min_count))

    def _movable(self, kind: str, left: int, right: int) -> bool:
        lo, hi = self._edge_range(kind, left, right)
        return lo < hi

    def move_edge(self, kind: str, left: int, right: int, lba: int) -> bool:
        """Bir kenari `lba`ya surukler; sinirlara ve hizalamaya kirpar.

        Degisiklik olduysa True. Sinirlar: komsu bolumler, dosya sisteminin
        en az/en cok boyutu, disk sonu. Boyutu sabit bolumun kenari oynamaz.
        """
        lba = self.align_down(lba + self.align // 2)       # en yakin hiza
        lo, hi = self._edge_range(kind, left, right)
        x = self._clamp_aligned(lba, lo, hi)
        if x is None:
            return False
        if kind == "end":
            part = self.get(left)
            return self._set(part, part.new_start, x - part.new_start)
        if kind == "start":
            part = self.get(right)
            return self._set(part, x, part.new_end + 1 - x)
        if kind == "boundary":
            a, b = self.get(left), self.get(right)
            gap = self._reserved_before(b)
            b_end = b.new_end + 1
            changed = self._set(a, a.new_start, x - a.new_start)
            return self._set(b, x + gap, b_end - x - gap) or changed
        return False

    def _clamp_aligned(self, lba: int, lo: int, hi: int) -> Optional[int]:
        """`lba`yi [lo, hi] araligina kirpar; hizali bir deger tercih edilir."""
        if lo > hi:
            return None
        value = max(lo, min(hi, lba))
        if value % self.align:
            down, up = self.align_down(value), self.align_up(value)
            if lo <= down:
                value = down
            elif up <= hi:
                value = up
        return value

    @staticmethod
    def _set(part: Slot, start: int, count: int) -> bool:
        if (part.new_start, part.new_count) == (start, count):
            return False
        part.new_start, part.new_count = start, count
        return True

    def partitions(self) -> List[Partition]:
        """Haritada gosterilecek bolumler (yeni yerlerinde)."""
        out = [p.as_partition() for p in self.sorted_parts()]
        extended = self._extended_partition()
        if extended is not None:
            out.append(extended)
        return out

    def free_regions(self) -> List[FreeRegion]:
        """Yeni yerlesimde bolumler arasinda ve sonda kalan bos alanlar."""
        regions = []
        cursor = self.first_usable()
        for part in self.sorted_parts():
            if part.logical:
                gap_end = part.new_start - self.align - 1
            else:
                gap_end = part.new_start - 1
            if gap_end - cursor + 1 >= self.align:
                regions.append(FreeRegion(cursor, gap_end - cursor + 1,
                                          self.sector_size))
            cursor = max(cursor, part.new_end + 1)
        if self.last_usable() - cursor + 1 >= self.align:
            regions.append(FreeRegion(cursor, self.last_usable() - cursor + 1,
                                      self.sector_size))
        return regions

    def _extended_partition(self) -> Optional[Partition]:
        if self.extended is None:
            return None
        logicals = [p for p in self.sorted_parts() if p.logical]
        if not logicals:
            return None
        ext = copy.copy(self.extended)
        ext.start_lba = logicals[0].new_start - self.align
        ext.sector_count = logicals[-1].new_end - ext.start_lba + 1
        return ext

    def summary(self) -> List[str]:
        """Degisen bolumlerin tek satirlik dokumu."""
        lines = []
        ss = self.sector_size
        for part in self.sorted_parts():
            if part.new_count != part.old_count:
                lines.append(tr("Bolum {}: {} -> {}", part.index,
                                human_size(part.old_count * ss),
                                human_size(part.new_count * ss)))
            elif part.moves:
                lines.append(tr("Bolum {}: yeri degisiyor", part.index))
        return lines
