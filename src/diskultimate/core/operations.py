"""Bekleyen islem kuyrugu — "once planla, sonra uygula".

## Neden

Simdiye kadar her islem tiklandigi anda diske yaziliyordu. Bu iki sorun
uretiyordu:

1. **Her islem icin ayri onay.** Kullanici uc bolum olusturup ikisini
   bicimlendirmek istediginde bes ayri uyari goruyordu; besinciyi okumuyordu.
2. **Fiziksel diskte "yazma modu" secimi.** Disk ya salt okunur ya da yazma
   modunda aciliyordu. Kullanici daha ne yapacagini bilmeden bu secimi yapmak
   zorundaydi ve yazma modunda acilan disk, hicbir sey yapilmasa bile acik
   kaldigi surece risk tasiyordu (birimler kilitleniyordu — ADR 0022).

Disk araclarinin ortak cozumu (Acronis, EaseUS, AOMEI, MiniTool, Macrorit):
islemler bir **kuyruga** girer, diske hicbir sey yazilmaz; kullanici hepsini
gorup tek bir **Uygula** ile calistirir.

## Guvenlik acisindan ne degisir

Daha guvenli olur, daha az degil:

- Kuyruk dolarken kaynak **salt okunur** acilir; hicbir sektor yazilmaz.
- Yazma yetkisi yalnizca uygulama aninda, tek seferde ve **tam listeyi
  gostererek** istenir. Onay hala zorunludur (CLAUDE.md), yalnizca yeri degisir:
  islem basina degil, parti basina.
- Fiziksel diskin butun koruma katmanlari (`physical.py`) aynen gecerlidir;
  kuyruk onlari atlamaz, yalnizca **daha gec** cagirir.

## Geri alma

Kuyruktaki bir adim serbestce cikarilabilir — henuz hicbir sey olmamistir.
**Uygulandiktan sonra geri alma yoktur**: bicimlendirilmis bir bolum geri
gelmez. Bu yuzden uygulama onayi yikici adimlari ayrica sayar ve
`apply()` bir adim basarisiz olursa **durur**; tamamlananlar geri alinmaz,
sonuc raporunda hangi adimin nerede kaldigi acikca yazar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import diagnostics
from .ptable import human_size
from ..i18n import mark, tr

Progress = Optional[Callable[[str, int], None]]


class OperationError(Exception):
    """Bir adim calistirilirken olusan hata."""


# ==========================================================================
# Islem turleri
# ==========================================================================
@dataclass(frozen=True)
class OperationKind:
    """Bir islem turunun degismeyen tanimi.

    `icon` adi `ui/icons.py` icindeki cizim islevine karsilik gelir; cekirdek
    ikonu cizmez, yalnizca **adini** soyler (katman ayrimi).
    """

    key: str
    label: str                 # arayuzde gorunen ad
    icon: str                  # ikon adi
    destructive: bool          # veri kaybettirebilir mi
    needs_write: bool = True   # diske yazar mi


KINDS: Dict[str, OperationKind] = {
    k.key: k for k in (
        OperationKind("create_table", mark("Bolum tablosu olustur"), "table", True),
        OperationKind("clear_table", mark("Bolum tablosunu sil"), "table-clear", True),
        OperationKind("convert_table", mark("Bolum tablosunu donustur"), "convert", True),
        OperationKind("create", mark("Yeni bolum"), "partition-new", False),
        OperationKind("format", mark("Bicimlendir"), "format", True),
        OperationKind("delete", mark("Bolumu sil"), "partition-delete", True),
        OperationKind("resize", mark("Bolumu boyutlandir"), "resize", True),
        OperationKind("label", mark("Birim etiketi"), "label", False),
        OperationKind("rename", mark("Bolum adi"), "rename", False),
        OperationKind("type", mark("Bolum turu"), "type", False),
        OperationKind("boot", mark("Onyukleme bayragi"), "boot", False),
        OperationKind("wipe_partition", mark("Bolumu guvenli sil"), "wipe", True),
        OperationKind("wipe_free", mark("Bos alani sil"), "wipe-free", False),
        OperationKind("resize_image", mark("Goruntu boyutu"), "image-resize", True),
        # Onyukleme kodunun silinmesi yikicidir: diskteki sistem acilmaz hale
        # gelebilir. Bolum tablosu korunur, veri durur — ama makine acilmaz.
        OperationKind("clear_boot_code", mark("Onyukleme kodunu kaldir"),
                      "bootloader", True),
    )
}


@dataclass
class Operation:
    """Kuyruktaki tek bir adim.

    `params` adima ozgu degerleri tasir; calistirma `RUNNERS` icindeki isleve
    aittir. Adim **kendi basina veri tutmaz**, yalnizca ne yapilacagini tarif
    eder — bu yuzden kuyruk her an gosterilebilir ve yeniden siralanabilir.

    Gorunen metinler (`title`, `detail`, `target`) **cevrilmemis kalip** ve
    argumanlari olarak saklanir; okunduklari anda cevrilirler. Boylece kuyruk
    dolduktan sonra dil degistirilirse bekleyen adimlarin basliklari da yeni
    dile doner — hazir uretilmis metin saklansaydi eski dilde kalirlardi.
    """

    kind: str
    title_text: str = ""
    detail_text: str = ""
    target_text: str = ""                      # "Bolum 2" / "Disk" gibi
    title_args: tuple = ()
    detail_args: tuple = ()
    target_args: tuple = ()
    params: Dict = field(default_factory=dict)

    # -- gorunen metinler --------------------------------------------------
    @property
    def title(self) -> str:
        return tr(self.title_text, *self.title_args)

    @title.setter
    def title(self, value: str) -> None:
        self.title_text, self.title_args = value, ()

    @property
    def detail(self) -> str:
        return tr(self.detail_text, *self.detail_args) if self.detail_text else ""

    @detail.setter
    def detail(self, value: str) -> None:
        self.detail_text, self.detail_args = value, ()

    @property
    def target(self) -> str:
        return tr(self.target_text, *self.target_args) if self.target_text else ""

    @target.setter
    def target(self, value: str) -> None:
        self.target_text, self.target_args = value, ()

    @property
    def info(self) -> OperationKind:
        return KINDS[self.kind]

    @property
    def destructive(self) -> bool:
        return self.info.destructive

    @property
    def icon(self) -> str:
        return self.info.icon

    def __str__(self) -> str:
        return f"{self.title}" + (f" — {self.detail}" if self.detail else "")


# ==========================================================================
# Calistiricilar
# ==========================================================================
def _run_create_table(session, p, progress):
    session.create_table(p["scheme"])


def _run_clear_table(session, p, progress):
    session.clear_table()


def _run_convert_table(session, p, progress):
    session.convert_scheme(p["scheme"], progress=progress)


def _run_create(session, p, progress):
    if p.get("extended"):
        # Genisletilmis bolum bir kapsayicidir; icinde dosya sistemi olmaz.
        session.table.create_extended(p["start_lba"], p["sector_count"])
        session.reload()
        return
    session.create_partition(
        p["start_lba"], p["sector_count"], fs_key=p.get("fs_key", ""),
        label=p.get("label", ""), name=p.get("name", ""),
        bootable=p.get("bootable", False),
        type_id=p.get("type_id", 0), type_guid=p.get("type_guid", ""),
        logical=p.get("logical"), progress=progress)


def _run_format(session, p, progress):
    session.format_partition(p["index"], p["fs_key"], label=p.get("label", ""),
                             cluster_bytes=p.get("cluster_bytes", 0),
                             quick=p.get("quick", True), progress=progress)


def _run_delete(session, p, progress):
    session.delete_partition(p["index"], wipe=p.get("wipe", True))


def _run_resize(session, p, progress):
    session.resize_partition(p["index"], p["start_lba"], p["sector_count"],
                             confirm=True, progress=progress)


def _run_label(session, p, progress):
    fs = session.filesystem(p["index"])
    if fs is None:
        raise OperationError(tr("Bolumde etiket yazilabilir bir dosya sistemi yok"))
    fs.set_label(p["label"])
    fs.flush()


def _run_rename(session, p, progress):
    session.set_partition_name(p["index"], p["name"])


def _run_type(session, p, progress):
    session.set_partition_type(p["index"], type_id=p.get("type_id", 0),
                               type_guid=p.get("type_guid", ""))


def _run_boot(session, p, progress):
    session.set_bootable(p["index"], p["value"])


def _run_wipe_partition(session, p, progress):
    if p.get("index", -1) < 0:
        session.wipe_disk(p.get("method", "zero"), progress=progress)
        return
    session.wipe_partition_data(p["index"], p.get("method", "zero"),
                                progress=progress)


def _run_wipe_free(session, p, progress):
    session.wipe_free_space(p["index"], progress=progress)


def _run_resize_image(session, p, progress):
    session.resize_image(p["size"])


def _run_clear_boot_code(session, p, progress):
    session.clear_boot_code()


RUNNERS: Dict[str, Callable] = {
    "create_table": _run_create_table,
    "clear_table": _run_clear_table,
    "convert_table": _run_convert_table,
    "create": _run_create,
    "format": _run_format,
    "delete": _run_delete,
    "resize": _run_resize,
    "label": _run_label,
    "rename": _run_rename,
    "type": _run_type,
    "boot": _run_boot,
    "wipe_partition": _run_wipe_partition,
    "wipe_free": _run_wipe_free,
    "resize_image": _run_resize_image,
    "clear_boot_code": _run_clear_boot_code,
}


# ==========================================================================
# Sonuc
# ==========================================================================
@dataclass
class ApplyResult:
    """Uygulamanin sonucu.

    `done` tamamlanan adimlar, `failed` duran adim (varsa), `pending`
    calistirilamayan kalan adimlardir. Kismi basari **gizlenmez**: bir
    bicimlendirme yapilip sonraki adim patlarsa kullanici bunu bilmelidir.
    """

    done: List[Operation] = field(default_factory=list)
    failed: Optional[Operation] = None
    error: str = ""
    pending: List[Operation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed is None

    def summary(self) -> str:
        if self.ok:
            return tr("{} adim uygulandi", len(self.done))
        return (tr("{} adim uygulandi, '{}' adiminda durdu: {}",
                   len(self.done), self.failed.title, self.error)
                + (tr(" ({} adim calistirilmadi)", len(self.pending))
                   if self.pending else ""))


# ==========================================================================
# Kuyruk
# ==========================================================================
class OperationQueue:
    """Sirali bekleyen islem listesi.

    Liste bos kaldiginda kaynak hicbir zaman yazma modunda acilmaz; yani
    "hicbir sey yapmadim ama disk yazilabilir acik" durumu ortadan kalkar.
    """

    def __init__(self):
        self._items: List[Operation] = []

    # -- liste ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, index: int) -> Operation:
        return self._items[index]

    @property
    def items(self) -> List[Operation]:
        return list(self._items)

    @property
    def is_empty(self) -> bool:
        return not self._items

    @property
    def destructive_count(self) -> int:
        return sum(1 for op in self._items if op.destructive)

    def add(self, operation: Operation) -> Operation:
        if operation.kind not in KINDS:
            raise OperationError(tr("Bilinmeyen islem turu: {}", operation.kind))
        self._items.append(operation)
        diagnostics.info(f"kuyruga eklendi ({len(self._items)}): {operation}")
        return operation

    def remove(self, index: int) -> Operation:
        operation = self._items.pop(index)
        diagnostics.info(f"kuyruktan cikarildi: {operation}")
        return operation

    def undo(self) -> Optional[Operation]:
        """Son eklenen adimi geri alir (henuz uygulanmadigi icin bedelsiz)."""
        if not self._items:
            return None
        return self.remove(len(self._items) - 1)

    def move(self, index: int, target: int) -> None:
        """Adimi listede tasir; sira uygulama sirasidir."""
        if not (0 <= index < len(self._items)):
            raise OperationError(tr("Gecersiz adim numarasi"))
        target = max(0, min(len(self._items) - 1, target))
        self._items.insert(target, self._items.pop(index))

    def clear(self) -> int:
        count = len(self._items)
        self._items.clear()
        if count:
            diagnostics.info(f"kuyruk bosaltildi ({count} adim)")
        return count

    # -- ozet -------------------------------------------------------------
    def describe(self) -> List[str]:
        """Numarali, okunur adim listesi (gunluk ve testler icin)."""
        return [f"{i}. {op}" + (tr(" [YIKICI]") if op.destructive else "")
                for i, op in enumerate(self._items, 1)]

    def describe_rows(self) -> List[tuple]:
        """(metin, yikici mi) ciftleri — onay penceresi icin.

        Yikicilik **metinden okunmaz**: eskiden onay penceresi satirda
        "[YIKICI]" arayip kalin yaziyordu; metin cevrilince bu arama bos
        donerdi. Bayrak veriyle birlikte tasinir.
        """
        return [(f"{i}. {op}", op.destructive)
                for i, op in enumerate(self._items, 1)]

    # -- uygulama ---------------------------------------------------------
    def apply(self, session, progress: Progress = None,
              on_step=None) -> ApplyResult:
        """Adimlari **sirayla** calistirir.

        Bir adim basarisiz olursa durur: sonraki adimlar cogu zaman oncekinin
        sonucuna dayanir (once bolum olustur, sonra bicimlendir), devam etmek
        tahmin edilemez bir duruma goturur.

        Tamamlanan adimlar **geri alinmaz** — bicimlendirilmis bir bolum geri
        gelmez. Sonuc nerede durulduğunu acikca soyler.
        """
        result = ApplyResult()
        total = len(self._items)
        if not total:
            return result
        with diagnostics.span("operations.apply", count=total):
            for i, operation in enumerate(self._items):
                percent = int(100 * i / total)
                if progress:
                    progress(f"{i + 1}/{total} — {operation.title}", percent)
                if on_step:
                    on_step(i, operation)
                runner = RUNNERS.get(operation.kind)
                if runner is None:
                    result.failed = operation
                    result.error = f"Calistirici yok: {operation.kind}"
                    result.pending = self._items[i:]
                    break
                try:
                    with diagnostics.span("operations.step", kind=operation.kind):
                        runner(session, operation.params, progress)
                except Exception as exc:
                    diagnostics.error(f"adim basarisiz: {operation}", exc)
                    result.failed = operation
                    result.error = str(exc)
                    result.pending = self._items[i + 1:]
                    break
                result.done.append(operation)
        if progress:
            progress(tr("Tamamlandi"), 100)
        # Uygulanan adimlar kuyruktan dusulur; basarisiz olan ve sonrasi kalir
        # ki kullanici duzeltip yeniden deneyebilsin.
        self._items = ([result.failed] if result.failed else []) + result.pending
        diagnostics.info(f"uygulama sonucu: {result.summary()}")
        return result


# ==========================================================================
# Adim uretici yardimcilar — baslik metni tek yerde uretilir
# ==========================================================================
# Basliklar `mark()` ile isaretlenir: metin burada **cevrilmez**, yalnizca
# ceviri sozluguna girer. Ceviri, adim gosterilirken `Operation.title` icinde
# yapilir (bkz. Operation).
def create_table_op(scheme: str) -> Operation:
    name = {"mbr": "MBR", "gpt": "GPT"}.get(scheme, scheme.upper())
    return Operation("create_table",
                     title_text=mark("{} bolum tablosu olustur"),
                     title_args=(name,),
                     detail_text=mark("Mevcut bolumler kaybolur"),
                     target_text=mark("Disk"),
                     params={"scheme": scheme})


def clear_table_op() -> Operation:
    return Operation("clear_table",
                     title_text=mark("Bolum tablosunu sil"),
                     detail_text=mark("Tum bolumler kaybolur"),
                     target_text=mark("Disk"))


def convert_table_op(scheme: str) -> Operation:
    name = {"mbr": "MBR", "gpt": "GPT"}.get(scheme, scheme.upper())
    return Operation("convert_table",
                     title_text=mark("Bolum tablosunu {} yap"),
                     title_args=(name,),
                     detail_text=mark("Bolum verisi korunur"),
                     target_text=mark("Disk"),
                     params={"scheme": scheme})


def create_op(start_lba: int, sector_count: int, sector_size: int = 512,
              fs_key: str = "", label: str = "", name: str = "",
              **extra) -> Operation:
    size = human_size(sector_count * sector_size)
    params = {"start_lba": start_lba, "sector_count": sector_count,
              "fs_key": fs_key, "label": label, "name": name}
    params.update(extra)
    operation = Operation("create", title_text=mark("Yeni bolum olustur"),
                          target_text=mark("LBA {}"),
                          target_args=(start_lba,), params=params)
    if fs_key:
        operation.detail_text = mark("{}, {}")
        operation.detail_args = (size, fs_key)
    else:
        operation.detail_text = mark("{}, bicimlendirilmemis")
        operation.detail_args = (size,)
    return operation


def format_op(index: int, fs_key: str, label: str = "", fs_name: str = "",
              **extra) -> Operation:
    params = {"index": index, "fs_key": fs_key, "label": label}
    params.update(extra)
    operation = Operation("format", title_text=mark("Bolum {} bicimlendir"),
                          title_args=(index,), target_text=mark("Bolum {}"),
                          target_args=(index,), params=params)
    if label:
        operation.detail_text = mark("{}, etiket '{}'")
        operation.detail_args = (fs_name or fs_key, label)
    else:
        operation.detail_text = mark("{}")
        operation.detail_args = (fs_name or fs_key,)
    return operation


def delete_op(index: int, name: str = "", wipe: bool = True) -> Operation:
    return Operation("delete", title_text=mark("Bolum {} sil"),
                     title_args=(index,), detail_text=mark("{}"),
                     detail_args=(name,), target_text=mark("Bolum {}"),
                     target_args=(index,),
                     params={"index": index, "wipe": wipe})


def resize_op(index: int, start_lba: int, sector_count: int,
              sector_size: int = 512) -> Operation:
    return Operation("resize", title_text=mark("Bolum {} boyutlandir"),
                     title_args=(index,),
                     detail_text=mark("yeni boyut {}"),
                     detail_args=(human_size(sector_count * sector_size),),
                     target_text=mark("Bolum {}"), target_args=(index,),
                     params={"index": index, "start_lba": start_lba,
                             "sector_count": sector_count})


def label_op(index: int, label: str) -> Operation:
    return Operation("label", title_text=mark("Bolum {} etiketi"),
                     title_args=(index,), detail_text=mark("'{}'"),
                     detail_args=(label,), target_text=mark("Bolum {}"),
                     target_args=(index,),
                     params={"index": index, "label": label})


def rename_op(index: int, name: str) -> Operation:
    return Operation("rename", title_text=mark("Bolum {} adi"),
                     title_args=(index,), detail_text=mark("'{}'"),
                     detail_args=(name,), target_text=mark("Bolum {}"),
                     target_args=(index,),
                     params={"index": index, "name": name})


def type_op(index: int, type_id: int = 0, type_guid: str = "",
            name: str = "") -> Operation:
    return Operation("type", title_text=mark("Bolum {} turu"),
                     title_args=(index,), detail_text=mark("{}"),
                     detail_args=(name,), target_text=mark("Bolum {}"),
                     target_args=(index,),
                     params={"index": index, "type_id": type_id,
                             "type_guid": type_guid})


def boot_op(index: int, value: bool) -> Operation:
    return Operation("boot",
                     title_text=mark("Bolum {} onyukleme bayragi"),
                     title_args=(index,),
                     detail_text=mark("isaretle") if value else mark("kaldir"),
                     target_text=mark("Bolum {}"), target_args=(index,),
                     params={"index": index, "value": value})


def wipe_partition_op(index: int, method: str,
                      method_name: str = "") -> Operation:
    """Guvenli silme adimi. `index < 0` ise hedef **butun disktir**."""
    operation = Operation("wipe_partition", detail_text=mark("{}"),
                          detail_args=(method_name or method,),
                          params={"index": index, "method": method})
    if index < 0:
        operation.title_text = mark("Disk guvenli sil")
        operation.target_text = mark("Disk")
    else:
        operation.title_text = mark("Bolum {} guvenli sil")
        operation.title_args = (index,)
        operation.target_text = mark("Bolum {}")
        operation.target_args = (index,)
    return operation


def wipe_free_op(index: int) -> Operation:
    return Operation("wipe_free", title_text=mark("Bolum {} bos alanini sil"),
                     title_args=(index,),
                     detail_text=mark("dosyalar korunur"),
                     target_text=mark("Bolum {}"), target_args=(index,),
                     params={"index": index})


def resize_image_op(size: int) -> Operation:
    return Operation("resize_image",
                     title_text=mark("Goruntu boyutunu degistir"),
                     detail_text=mark("{}"), detail_args=(human_size(size),),
                     target_text=mark("Goruntu"), params={"size": size})
