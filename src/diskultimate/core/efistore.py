"""UEFI onyukleme duzeninin okunmasi, yedeklenmesi ve yazilmasi.

`core/efiboot.py` yapiyi cozer, `core/platform.py` degiskeni okur/yazar; bu
modul ikisini birlestirir ve **butun duzeni** tek bir nesne olarak sunar.

## Yazma neden bu kadar dikkatli

Bellenim degiskenleri diskte degil, **anakart uzerindeki kalici bellektedir**.
Yanlis yazilmis bir `BootOrder` makineyi acilmaz hale getirebilir ve bunu geri
almak icin calisan bir isletim sistemi gerekir — yani elde kalan tek arac
kaybolur. Bu yuzden:

1. Yazmadan once **her zaman** tam bir yedek dosyasi uretilir (`save_backup`).
2. Degisiklikler `changes()` ile **onceden listelenir**; arayuz hepsini
   gostermeden hicbir sey yazmaz.
3. Yazma sirasi sabittir: once girisler yazilir/olusturulur, sonra sira
   (`BootOrder`), en son silinenler kaldirilir. Boylece `BootOrder` hicbir an
   var olmayan bir girisi gostermez.
4. Bir adim basarisiz olursa **durulur**; yarim uygulanmis bir duzen
   raporlanir, sessizce gecilmez.

Gerekce: `.claude/decisions/0029-uefi-onyukleme-duzenleyici.md`
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import diagnostics, platform
from .efiboot import (GLOBAL_GUID, KeyOption, LoadOption, build_order,
                      build_uint16, parse_order, parse_uint16)
from ..i18n import mark, tr

# Yedek dosyasinin bicim surumu — ileride alan eklenirse eski dosyalar taninir.
BACKUP_FORMAT = 1

# Okunan giris turleri: degisken onekleri ve sira degiskenlerinin adlari.
OPTION_KINDS = {
    "Boot": "BootOrder",
    "Driver": "DriverOrder",
    "SysPrep": "SysPrepOrder",
}

KIND_LABELS = {
    "Boot": mark("Onyukleme girisi"),
    "Driver": mark("Surucu girisi"),
    "SysPrep": mark("Sistem hazirlik girisi"),
}


class EfiStoreError(Exception):
    """Bellenim duzeni okunamadi ya da yazilamadi."""


# ==========================================================================
# Durum
# ==========================================================================
@dataclass
class BootState:
    """Makinenin butun UEFI onyukleme duzeni.

    `source` verinin nereden geldigini soyler: `firmware` calisan makineden,
    `file` daha once alinmis bir yedekten. Ikisi ayni nesnedir ama **yalnizca
    birincisi yazilabilir** — bir yedegi duzenleyip diske geri yazmak baska
    bir makinenin duzenini bozmak olurdu.
    """

    firmware: str = ""                     # uefi | bios | ''
    readable: bool = False
    writable: bool = False
    reason: str = ""
    source: str = "firmware"

    entries: Dict[str, Dict[int, LoadOption]] = field(default_factory=dict)
    orders: Dict[str, List[int]] = field(default_factory=dict)
    hotkeys: List[KeyOption] = field(default_factory=list)

    boot_next: Optional[int] = None
    boot_current: Optional[int] = None
    timeout: Optional[int] = None
    secure_boot: Optional[bool] = None
    setup_mode: Optional[bool] = None

    # -- kolay erisim ------------------------------------------------------
    @property
    def boot_entries(self) -> Dict[int, LoadOption]:
        return self.entries.setdefault("Boot", {})

    @property
    def boot_order(self) -> List[int]:
        return self.orders.setdefault("Boot", [])

    @boot_order.setter
    def boot_order(self, value: List[int]) -> None:
        self.orders["Boot"] = list(value)

    def ordered(self, kind: str = "Boot") -> List[LoadOption]:
        """Girisleri **sira degiskenine gore** dondurur.

        Sirada olmayan girisler sona eklenir: bellenimde var olan ama
        `BootOrder` disinda kalan bir giris gercekten vardir ve gizlenmemelidir
        — bu, disa dusmus bir isletim sistemini bulmanin tek yoludur.
        """
        table = self.entries.get(kind, {})
        order = self.orders.get(kind, [])
        result = [table[n] for n in order if n in table]
        listed = set(order)
        result.extend(table[n] for n in sorted(table) if n not in listed)
        return result

    def orphans(self, kind: str = "Boot") -> List[int]:
        """Sirada gecen ama karsiligi olmayan giris numaralari.

        Bu, bozuk bir duzenin en sik belirtisidir: bir isletim sistemi
        kaldirilmis ama numarasi `BootOrder` icinde kalmistir.
        """
        table = self.entries.get(kind, {})
        return [n for n in self.orders.get(kind, []) if n not in table]

    def free_number(self, kind: str = "Boot") -> int:
        """Kullanilmayan en kucuk giris numarasi (yeni giris icin)."""
        table = self.entries.get(kind, {})
        for number in range(0, 0x10000):
            if number not in table:
                return number
        raise EfiStoreError(tr("Bos giris numarasi kalmadi"))

    def summary(self) -> Dict[str, str]:
        """Arayuzde gosterilecek ozet."""
        names = {"uefi": tr("UEFI"), "bios": tr("BIOS (eski kip)")}
        secure = (tr("Acik") if self.secure_boot else tr("Kapali")) \
            if self.secure_boot is not None else tr("Bilinmiyor")
        return {
            tr("Bellenim"): names.get(self.firmware, tr("Bilinmiyor")),
            tr("Kaynak"): (tr("Calisan makine") if self.source == "firmware"
                           else tr("Yedek dosyasi")),
            tr("Giris sayisi"): str(len(self.boot_entries)),
            tr("Su an acilan"): (f"Boot{self.boot_current:04X}"
                                 if self.boot_current is not None else "-"),
            tr("Sonraki acilis"): (f"Boot{self.boot_next:04X}"
                                   if self.boot_next is not None else "-"),
            tr("Menu bekleme"): (tr("{} saniye", self.timeout)
                                 if self.timeout is not None else "-"),
            tr("Guvenli onyukleme"): secure,
            tr("Yazma"): (tr("Acik") if self.writable
                          else (self.reason or tr("Kapali"))),
        }


# ==========================================================================
# Okuma
# ==========================================================================
def _read_var(name: str) -> Tuple[int, bytes]:
    return platform.efivar_read(name, GLOBAL_GUID)


def _numbered(prefix: str, names: List[Tuple[str, str]]) -> List[int]:
    """`Boot0001` gibi adlardan numaralari cikarir."""
    found = []
    size = len(prefix)
    for name, guid in names:
        if guid.lower() != GLOBAL_GUID or len(name) != size + 4:
            continue
        if not name.startswith(prefix):
            continue
        try:
            found.append(int(name[size:], 16))
        except ValueError:
            continue
    return sorted(found)


def load(progress=None) -> BootState:
    """Calisan makinenin onyukleme duzenini okur.

    Okunamadiginda **bos degil, aciklamali** bir durum doner: kullanici
    "giris yok" ile "okuyamadim" arasindaki farki gormelidir.
    """
    with diagnostics.span("efi.load"):
        readable, writable, reason = platform.efivars_state()
        state = BootState(firmware=platform.firmware_type(), readable=readable,
                          writable=writable, reason=reason, source="firmware")
        if not readable:
            return state

        names = platform.efivar_names()

        for kind, order_name in OPTION_KINDS.items():
            numbers = _numbered(kind, names)
            table: Dict[int, LoadOption] = {}
            total = max(1, len(numbers))
            for index, number in enumerate(numbers):
                if progress:
                    progress(tr("{} okunuyor...", f"{kind}{number:04X}"),
                             int(index * 100 / total))
                attributes, data = _read_var(f"{kind}{number:04X}")
                if not data:
                    continue
                try:
                    table[number] = LoadOption.parse(data, number=number,
                                                     kind=kind,
                                                     var_attributes=attributes)
                except Exception as exc:            # noqa: BLE001
                    diagnostics.info(f"efi: {kind}{number:04X} cozulemedi: {exc}")
            state.entries[kind] = table
            _, raw = _read_var(order_name)
            state.orders[kind] = parse_order(raw)

        for number in _numbered("Key", names):
            attributes, data = _read_var(f"Key{number:04X}")
            if not data:
                continue
            try:
                state.hotkeys.append(KeyOption.parse(data, number=number,
                                                     var_attributes=attributes))
            except Exception:                       # noqa: BLE001
                continue

        _, raw = _read_var("BootNext")
        state.boot_next = parse_uint16(raw) if raw else None
        _, raw = _read_var("BootCurrent")
        state.boot_current = parse_uint16(raw) if raw else None
        _, raw = _read_var("Timeout")
        state.timeout = parse_uint16(raw) if raw else None
        _, raw = _read_var("SecureBoot")
        state.secure_boot = bool(raw[0]) if raw else None
        _, raw = _read_var("SetupMode")
        state.setup_mode = bool(raw[0]) if raw else None

        if progress:
            progress(tr("Tamamlandi"), 100)
        return state


# ==========================================================================
# Yedekleme
# ==========================================================================
def to_dict(state: BootState) -> Dict:
    """Durumu yedek dosyasinin sozluk gosterimine cevirir."""
    return {
        "format": BACKUP_FORMAT,
        "application": "DiskUltimate",
        "firmware": state.firmware,
        "platform": platform.PLATFORM_NAME,
        "orders": {kind: list(numbers) for kind, numbers in state.orders.items()},
        "entries": {kind: [option.to_dict() for option in table.values()]
                    for kind, table in state.entries.items()},
        "hotkeys": [key.to_dict() for key in state.hotkeys],
        "boot_next": state.boot_next,
        "boot_current": state.boot_current,
        "timeout": state.timeout,
        "secure_boot": state.secure_boot,
        "setup_mode": state.setup_mode,
    }


def from_dict(data: Dict) -> BootState:
    """Yedek sozlugunu duruma cevirir (yazilamaz olarak isaretlenir)."""
    if int(data.get("format", 0)) > BACKUP_FORMAT:
        raise EfiStoreError(tr("Yedek dosyasi daha yeni bir surumle yazilmis "
                               "(bicim {})", data.get("format")))
    state = BootState(firmware=str(data.get("firmware", "")), readable=True,
                      writable=False, source="file",
                      reason=tr("Yedek dosyasi dogrudan yazilamaz."))
    for kind, rows in (data.get("entries") or {}).items():
        table: Dict[int, LoadOption] = {}
        for row in rows:
            try:
                option = LoadOption.from_dict(row)
            except Exception:                       # noqa: BLE001
                continue
            table[option.number] = option
        state.entries[kind] = table
    for kind, numbers in (data.get("orders") or {}).items():
        state.orders[kind] = [int(n) for n in numbers]
    for row in (data.get("hotkeys") or []):
        raw = row.get("raw", "")
        if raw:
            try:
                state.hotkeys.append(
                    KeyOption.parse(bytes.fromhex(raw),
                                    number=int(row.get("number", 0))))
            except Exception:                       # noqa: BLE001
                continue
    state.boot_next = data.get("boot_next")
    state.boot_current = data.get("boot_current")
    state.timeout = data.get("timeout")
    state.secure_boot = data.get("secure_boot")
    state.setup_mode = data.get("setup_mode")
    return state


def save_backup(state: BootState, path: str) -> str:
    """Duzeni JSON olarak kaydeder ve yazilan yolu dondurur.

    Ham baytlar da yazilir: metin gosterimi bilgi kaybeder, ham veriyle
    duzen **bire bir** geri konabilir.
    """
    with diagnostics.span("efi.backup"):
        body = json.dumps(to_dict(state), ensure_ascii=False, indent=2,
                          sort_keys=True)
        temporary = path + ".new"
        with open(temporary, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body + "\n")
        os.replace(temporary, path)
        return path


def load_backup(path: str) -> BootState:
    """Yedek dosyasini okur."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise EfiStoreError(tr("Yedek dosyasi taninmadi"))
    return from_dict(data)


# ==========================================================================
# Degisiklikler
# ==========================================================================
@dataclass
class VariableChange:
    """Yazilacak (ya da silinecek) tek bir bellenim degiskeni."""

    name: str
    action: str                 # write | delete
    data: bytes = b""
    attributes: int = 0
    description: str = ""       # kullaniciya gosterilen aciklama

    @property
    def destructive(self) -> bool:
        """Silme ve sira degisikligi makinenin acilisini etkiler."""
        return self.action == "delete" or self.name.endswith("Order")


def changes(original: BootState, updated: BootState) -> List[VariableChange]:
    """Iki durum arasindaki farki **yazma sirasiyla** listeler.

    Sira bilerek secilmistir: once girisler, sonra sira listeleri, en son
    silmeler. Silmeyi basa almak, `BootOrder` hala o girisi gosterirken
    girisin yok olmasi demektir — bazi bellenimlerde bu, acilista makinenin
    kendi duzenini "onarip" butun sirayi bozmasina yol acar.
    """
    plan: List[VariableChange] = []
    removals: List[VariableChange] = []

    for kind, order_name in OPTION_KINDS.items():
        before = original.entries.get(kind, {})
        after = updated.entries.get(kind, {})

        for number, option in sorted(after.items()):
            raw = option.to_bytes()
            existing = before.get(number)
            if existing is not None and existing.to_bytes() == raw:
                continue
            plan.append(VariableChange(
                name=option.name, action="write", data=raw,
                attributes=option.var_attributes,
                description=(tr("{} guncellendi: {}", option.name,
                                option.description) if existing is not None
                             else tr("{} olusturuldu: {}", option.name,
                                     option.description))))

        for number, option in sorted(before.items()):
            if number not in after:
                removals.append(VariableChange(
                    name=option.name, action="delete",
                    description=tr("{} silindi: {}", option.name,
                                   option.description)))

        if original.orders.get(kind, []) != updated.orders.get(kind, []):
            numbers = updated.orders.get(kind, [])
            plan.append(VariableChange(
                name=order_name, action="write", data=build_order(numbers),
                attributes=_order_attributes(original, order_name),
                description=tr("{} degistirildi: {}", order_name,
                               ", ".join(f"{n:04X}" for n in numbers) or "-")))

    if original.boot_next != updated.boot_next:
        if updated.boot_next is None:
            removals.append(VariableChange(
                name="BootNext", action="delete",
                description=tr("Sonraki acilis secimi kaldirildi")))
        else:
            plan.append(VariableChange(
                name="BootNext", action="write",
                data=build_uint16(updated.boot_next),
                attributes=_order_attributes(original, "BootNext"),
                description=tr("Sonraki acilis: Boot{}",
                               f"{updated.boot_next:04X}")))

    if original.timeout != updated.timeout and updated.timeout is not None:
        plan.append(VariableChange(
            name="Timeout", action="write",
            data=build_uint16(updated.timeout),
            attributes=_order_attributes(original, "Timeout"),
            description=tr("Menu bekleme suresi: {} saniye", updated.timeout)))

    return plan + removals


def _order_attributes(state: BootState, name: str) -> int:
    """Degiskenin **var olan** ozniteligini korur, yoksa olagan degeri verir.

    Bellenim bir degiskeni farkli oznitelikle yazmayi reddedebilir; okunani
    geri vermek en guvenli davranistir.
    """
    if state.source == "firmware":
        attributes, _ = _read_var(name)
        if attributes:
            return attributes
    from .efiboot import BOOT_ENTRY_ATTRIBUTES
    return BOOT_ENTRY_ATTRIBUTES


# ==========================================================================
# Yazma
# ==========================================================================
@dataclass
class ApplyReport:
    """Yazma sonucu — kismi basari **gizlenmez**."""

    done: List[VariableChange] = field(default_factory=list)
    failed: Optional[VariableChange] = None
    error: str = ""
    pending: List[VariableChange] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed is None

    def summary(self) -> str:
        if self.ok:
            return tr("{} degisken yazildi", len(self.done))
        return tr("{} degisken yazildi, '{}' yazilamadi: {}",
                  len(self.done), self.failed.name, self.error) + (
            tr(" ({} degisiklik uygulanmadi)", len(self.pending))
            if self.pending else "")


def apply(plan: List[VariableChange], progress=None) -> ApplyReport:
    """Degisiklikleri bellenime yazar.

    Bir adim basarisiz olursa **durur**: sonraki adimlar cogu zaman oncekine
    dayanir (once giris, sonra sira) ve devam etmek tutarsiz bir duzen
    birakirdi.
    """
    report = ApplyReport()
    readable, writable, reason = platform.efivars_state()
    if not writable:
        report.failed = plan[0] if plan else VariableChange("", "write")
        report.error = reason or tr("Bellenim degiskenleri yazilamiyor.")
        report.pending = list(plan)
        return report

    with diagnostics.span("efi.apply", reason=f"{len(plan)} degisiklik"):
        for index, change in enumerate(plan):
            if progress:
                progress(tr("{} yaziliyor...", change.name),
                         int(index * 100 / max(1, len(plan))))
            if change.action == "delete":
                ok, error = platform.efivar_delete(change.name, GLOBAL_GUID)
            else:
                ok, error = platform.efivar_write(change.name, GLOBAL_GUID,
                                                  change.attributes, change.data)
            if not ok:
                report.failed = change
                report.error = error
                report.pending = plan[index + 1:]
                diagnostics.info(f"efi: {change.name} yazilamadi: {error}")
                return report
            report.done.append(change)
            diagnostics.info(f"efi: {change.name} yazildi ({change.action})")
        if progress:
            progress(tr("Tamamlandi"), 100)
    return report
