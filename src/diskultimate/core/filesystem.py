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

    def stats(self) -> Dict[str, int]:
        return self.fs.stats()

    def flush(self) -> None:
        self.fs.flush()


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
            f"{self.fs_type} icerigi bu surumde goruntulenemiyor "
            f"(yol haritasinda: ext4/NTFS/exFAT okuyucu)")

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
    return UnsupportedAccess(info)
