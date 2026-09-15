"""Dosya sistemi erisim arayuzu.

GUI yalnizca bu arayuzu tanir. Yeni bir dosya sistemi destegi eklendiginde
(ornegin ext4 okuyucu) burada yeni bir sinif tanimlanip `open_filesystem`
icine baglanir; arayuz tarafinda degisiklik gerekmez.
"""
from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from .exfat import ExEntry, ExFatError, ExFatFS
from .extread import ExtError, ExtFS
from .extwrite import ExtWriter
from .ntfsread import NtfsEntry, NtfsError, NtfsFS
from .fat import ATTR_DIRECTORY, DirEntry, FatError, FatFS
from .fsdetect import FSInfo, detect
from .image import BlockDevice


@dataclass
class FileNode:
    name: str
    path: str
    is_dir: bool = False
    size: int = 0
    mtime: Optional[datetime.datetime] = None
    attr_text: str = ""
    hidden: bool = False
    readonly: bool = False


class FileSystemAccess:
    """Bolum icerigine erisim icin ortak arayuz."""

    fs_type: str = ""
    label: str = ""
    writable: bool = False
    readable: bool = False

    @property
    def write_reason(self) -> str:
        """Yazma neden kapali? Yazilabiliyorsa bos dize.

        Iki ayri neden vardir ve kullaniciya ayni gorunmemelidir:
        kaynagin salt okunur acilmasi (cozulebilir) ile surucunun yazma
        destegi olmamasi (cozulemez). Her sinif kendi nedenini soyler.
        """
        return "" if self.writable else "Bu bolume yazilamiyor."

    def listdir(self, path: str = "/") -> List[FileNode]:
        raise NotImplementedError

    def read(self, path: str, max_bytes: int = -1) -> bytes:
        raise NotImplementedError

    def extract(self, path: str, dest: str) -> str:
        raise NotImplementedError

    def write_file(self, path: str, data: bytes) -> FileNode:
        raise NotImplementedError

    def import_file(self, local_path: str, dest_dir: str = "/") -> FileNode:
        raise NotImplementedError

    def import_tree(self, local_dir: str, dest_dir: str = "/") -> int:
        raise NotImplementedError

    def mkdir(self, path: str) -> None:
        raise NotImplementedError

    def remove(self, path: str, recursive: bool = True) -> None:
        raise NotImplementedError

    def rename(self, path: str, new_name: str) -> None:
        raise NotImplementedError

    def set_label(self, label: str) -> None:
        raise NotImplementedError

    def stats(self) -> Dict[str, int]:
        return {}

    def flush(self) -> None:
        pass


class FatAccess(FileSystemAccess):
    """Saf Python FAT surucusu uzerinden tam okuma/yazma erisimi."""

    readable = True

    def __init__(self, view: BlockDevice):
        self.fs = FatFS(view)
        self.fs_type = self.fs.fs_type_name
        self.label = self.fs.label
        self.writable = not self.fs.readonly

    @staticmethod
    def _node(entry: DirEntry, parent: str) -> FileNode:
        path = parent.rstrip("/") + "/" + entry.name
        return FileNode(name=entry.name, path=path, is_dir=entry.is_dir,
                        size=entry.size, mtime=entry.mtime,
                        attr_text=entry.attr_text, hidden=entry.is_hidden,
                        readonly=entry.is_readonly)

    def listdir(self, path: str = "/") -> List[FileNode]:
        return [self._node(e, path) for e in self.fs.listdir(path)]

    def read(self, path: str, max_bytes: int = -1) -> bytes:
        return self.fs.read_file(path, max_bytes)

    def extract(self, path: str, dest: str) -> str:
        return self.fs.extract(path, dest)

    def write_file(self, path: str, data: bytes) -> FileNode:
        parent = "/" + "/".join(path.replace("\\", "/").split("/")[:-1]).strip("/")
        entry = self.fs.write_file(path, data)
        return self._node(entry, parent)

    def import_file(self, local_path: str, dest_dir: str = "/") -> FileNode:
        entry = self.fs.import_file(local_path, dest_dir)
        return self._node(entry, dest_dir)

    def import_tree(self, local_dir: str, dest_dir: str = "/") -> int:
        return self.fs.import_tree(local_dir, dest_dir)

    def mkdir(self, path: str) -> None:
        self.fs.mkdir(path)

    def remove(self, path: str, recursive: bool = True) -> None:
        self.fs.remove(path, recursive=recursive)

    def rename(self, path: str, new_name: str) -> None:
        self.fs.rename(path, new_name)

    def set_label(self, label: str) -> None:
        self.fs.set_label(label)
        self.label = label

    @property
    def write_reason(self) -> str:
        if self.writable:
            return ""
        return ("Kaynak salt okunur acildi. Goruntuyu/diski yazma modunda "
                "acarsaniz bu bolume yazabilirsiniz.")

    def stats(self) -> Dict[str, int]:
        return self.fs.stats()

    def flush(self) -> None:
        self.fs.flush()


class ExFatAccess(FileSystemAccess):
    """Saf Python exFAT surucusu uzerinden tam okuma/yazma erisimi."""

    readable = True

    def __init__(self, view: BlockDevice):
        self.fs = ExFatFS(view)
        self.fs_type = "exFAT"
        self.label = self.fs.label
        self.writable = not self.fs.readonly

    @staticmethod
    def _node(entry: ExEntry, parent: str) -> FileNode:
        return FileNode(name=entry.name,
                        path=parent.rstrip("/") + "/" + entry.name,
                        is_dir=entry.is_dir, size=entry.size, mtime=entry.mtime,
                        attr_text=entry.attr_text, hidden=entry.is_hidden,
                        readonly=entry.is_readonly)

    def listdir(self, path: str = "/") -> List[FileNode]:
        return [self._node(e, path) for e in self.fs.listdir(path)]

    def read(self, path: str, max_bytes: int = -1) -> bytes:
        return self.fs.read_file(path, max_bytes)

    def extract(self, path: str, dest: str) -> str:
        return self.fs.extract(path, dest)

    def write_file(self, path: str, data: bytes) -> FileNode:
        parent = "/" + "/".join(path.replace("\\", "/").split("/")[:-1]).strip("/")
        return self._node(self.fs.write_file(path, data), parent)

    def import_file(self, local_path: str, dest_dir: str = "/") -> FileNode:
        return self._node(self.fs.import_file(local_path, dest_dir), dest_dir)

    def import_tree(self, local_dir: str, dest_dir: str = "/") -> int:
        return self.fs.import_tree(local_dir, dest_dir)

    def mkdir(self, path: str) -> None:
        self.fs.mkdir(path)

    def remove(self, path: str, recursive: bool = True) -> None:
        self.fs.remove(path, recursive=recursive)

    def rename(self, path: str, new_name: str) -> None:
        self.fs.rename(path, new_name)

    def set_label(self, label: str) -> None:
        self.fs.set_label(label)
        self.label = label

    @property
    def write_reason(self) -> str:
        if self.writable:
            return ""
        return ("Kaynak salt okunur acildi. Goruntuyu/diski yazma modunda "
                "acarsaniz bu bolume yazabilirsiniz.")

    def stats(self) -> Dict[str, int]:
        return self.fs.stats()

    def flush(self) -> None:
        self.fs.flush()


class ExtAccess(FileSystemAccess):
    """ext2/3/4 icerigine **salt okunur** erisim.

    Yazma yoktur: bir ext birimini degistirmek gunluk (journal) tutarliligi
    gerektirir. Arayuz `writable=False` gordugu icin yazma eylemlerini
    kendiliginden pasifler.
    """

    def __init__(self, view: BlockDevice):
        self.fs = ExtFS(view)
        self.fs_type = "ext"
        self.label = self.fs.label
        self.readable = True
        self.writer = ExtWriter(self.fs)
        self.writable, self._write_reason = self.writer.write_support()

    def listdir(self, path: str = "/") -> List[FileNode]:
        node = self.fs.resolve(path or "/")
        out: List[FileNode] = []
        for entry in self.fs.read_dir(node):
            if entry.name in (".", ".."):
                continue
            try:
                child = self.fs.read_inode(entry.inode)
            except ExtError:
                continue
            child_path = (path.rstrip("/") + "/" + entry.name) if path not in ("", "/") \
                else "/" + entry.name
            attrs = []
            if child.is_symlink:
                try:
                    attrs.append("-> " + self.fs.symlink_target(child))
                except ExtError:
                    attrs.append("bag")
            if entry.name.startswith("."):
                attrs.append("gizli")
            out.append(FileNode(
                name=entry.name, path=child_path, is_dir=child.is_dir,
                size=0 if child.is_dir else child.size,
                mtime=ExtFS.timestamp(child.mtime),
                attr_text=" ".join(attrs),
                hidden=entry.name.startswith("."), readonly=True))
        out.sort(key=lambda n: (not n.is_dir, n.name.lower()))
        return out

    def read(self, path: str, max_bytes: int = -1) -> bytes:
        return self.fs.read_data(self.fs.resolve(path), max_bytes)

    def extract(self, path: str, dest: str) -> str:
        data = self.read(path)
        with open(dest, "wb") as fh:
            fh.write(data)
        return dest

    @property
    def write_reason(self) -> str:
        return "" if self.writable else self._write_reason

    # -- yazma (ExtWriter uzerinden; her islem e2fsck ile dogrulanmistir) ----
    def write_file(self, path: str, data: bytes) -> FileNode:
        self.writer.write_file(path, data)
        parent = "/" + "/".join(path.replace("\\", "/").split("/")[:-1]).strip("/")
        name = path.replace("\\", "/").rstrip("/").split("/")[-1]
        return FileNode(name=name, path=path, is_dir=False, size=len(data),
                        mtime=datetime.datetime.now())

    def import_file(self, local_path: str, dest_dir: str = "/") -> FileNode:
        with open(local_path, "rb") as fh:
            data = fh.read()
        name = os.path.basename(local_path)
        target = (dest_dir.rstrip("/") + "/" + name) if dest_dir != "/" else "/" + name
        return self.write_file(target, data)

    def import_tree(self, local_dir: str, dest_dir: str = "/") -> int:
        count = 0
        for root, dirs, files in os.walk(local_dir):
            rel = os.path.relpath(root, local_dir).replace(os.sep, "/")
            base = dest_dir if rel == "." else f"{dest_dir.rstrip('/')}/{rel}"
            if rel != ".":
                try:
                    self.mkdir(base)
                except ExtError:
                    pass
            for entry_name in files:
                self.import_file(os.path.join(root, entry_name), base)
                count += 1
        return count

    def mkdir(self, path: str) -> None:
        self.writer.mkdir(path)

    def remove(self, path: str, recursive: bool = True) -> None:
        node = self.fs.resolve(path, follow=False)
        if node.is_dir and recursive:
            for child in self.fs.read_dir(node):
                if child.name in (".", ".."):
                    continue
                self.remove(path.rstrip("/") + "/" + child.name, recursive=True)
        self.writer.remove(path)

    def rename(self, path: str, new_name: str) -> None:
        self.writer.rename(path, new_name)

    def flush(self) -> None:
        self.writer.flush()

    def stats(self) -> Dict[str, int]:
        return self.fs.stats()


class NtfsAccess(FileSystemAccess):
    """NTFS icerigine **salt okunur** erisim.

    Yazma henuz yoktur: NTFS'e yazmak `$Bitmap` ve `$MFT` tahsisi, B+ agaci
    indeks ekleme ve `$MFTMirr` esitlemesi gerektirir (yol haritasinda).
    """

    def __init__(self, view: BlockDevice):
        self.fs = NtfsFS(view)
        self.fs_type = "NTFS"
        self.label = self.fs.label
        self.readable = True
        self.writable = False

    @property
    def write_reason(self) -> str:
        return ("NTFS surucusu su an SALT OKUNURDUR: listeleme, okuma ve disa "
                "aktarma calisir. Yazma icin $MFT/$Bitmap tahsisi ve dizin "
                "B+ agacina ekleme gerekir; yol haritasindadir.")

    @staticmethod
    def _node(entry: NtfsEntry, parent: str) -> FileNode:
        path = (parent.rstrip("/") + "/" + entry.name) if parent not in ("", "/")             else "/" + entry.name
        attrs = []
        if entry.is_hidden:
            attrs.append("G")
        if entry.is_readonly:
            attrs.append("S")
        if entry.is_system:
            attrs.append("Sis")
        return FileNode(name=entry.name, path=path, is_dir=entry.is_dir,
                        size=0 if entry.is_dir else entry.size,
                        mtime=entry.mtime, attr_text=" ".join(attrs),
                        hidden=entry.is_hidden, readonly=True)

    def listdir(self, path: str = "/") -> List[FileNode]:
        out = [self._node(e, path) for e in self.fs.listdir(path)]
        out.sort(key=lambda n: (not n.is_dir, n.name.lower()))
        return out

    def read(self, path: str, max_bytes: int = -1) -> bytes:
        return self.fs.read_file(path, max_bytes)

    def extract(self, path: str, dest: str) -> str:
        with open(dest, "wb") as fh:
            fh.write(self.read(path))
        return dest

    def stats(self) -> Dict[str, int]:
        return self.fs.stats()


class UnsupportedAccess(FileSystemAccess):
    """Tanindi ama icerik okuma destegi henuz yok."""

    def __init__(self, info: FSInfo):
        self.fs_type = info.fs_type
        self.label = info.label
        self.info = info
        self.readable = False
        self.writable = False

    def listdir(self, path: str = "/") -> List[FileNode]:
        raise FatError(
            f"{self.fs_type} icerigi bu surumde goruntulenemiyor. "
            "Okunabilen dosya sistemleri: FAT12/16/32, exFAT, ext2/3/4. "
            "NTFS okuyucusu yol haritasindadir.")

    def stats(self) -> Dict[str, int]:
        return {"total_bytes": self.info.total_bytes,
                "used_bytes": self.info.used_bytes,
                "free_bytes": self.info.free_bytes,
                "cluster_size": self.info.cluster_size}


def open_filesystem(view: BlockDevice,
                    info: Optional[FSInfo] = None) -> Optional[FileSystemAccess]:
    """Bolum uzerindeki dosya sistemi icin erisim nesnesi dondurur.

    Bicimlendirilmemis bolumlerde None doner.
    """
    info = info or detect(view)
    if not info.fs_type:
        return None
    if info.fs_type.startswith("FAT"):
        try:
            return FatAccess(view)
        except FatError:
            return UnsupportedAccess(info)
    if info.fs_type == "exFAT":
        try:
            return ExFatAccess(view)
        except ExFatError:
            return UnsupportedAccess(info)
    if info.fs_type == "NTFS":
        try:
            return NtfsAccess(view)
        except NtfsError:
            return UnsupportedAccess(info)
    if info.fs_type.startswith("ext"):
        try:
            access = ExtAccess(view)
            access.fs_type = info.fs_type      # ext2/ext3/ext4 ayrimi korunsun
            return access
        except ExtError:
            return UnsupportedAccess(info)
    return UnsupportedAccess(info)
