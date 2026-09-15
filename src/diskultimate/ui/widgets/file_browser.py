"""Bolum icerigi gezgini: klasor agaci + dosya listesi."""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QFileDialog,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                             QLineEdit, QMenu, QMessageBox, QSplitter, QStyle,
                             QToolBar, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from ...core.filesystem import FileNode, FileSystemAccess
from ...core.ptable import human_size
from ..dialogs.task import run_task
from ..theme import palette_color, standard_icon


class FileBrowser(QWidget):
    """Secili bolumun dosya sistemi icerigini gosterir ve duzenler."""

    statusMessage = pyqtSignal(str)
    contentChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fs: Optional[FileSystemAccess] = None
        self.current_path = "/"
        self._build()
        self._update_actions()

    # -- arayuz --------------------------------------------------------------
    def _icon(self, standard) -> QIcon:
        return standard_icon(self, standard)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QToolBar()
        self.toolbar.setIconSize(QSize(16, 16))
        self.act_up = self.toolbar.addAction(
            self._icon(QStyle.SP_FileDialogToParent), "Ust klasor", self.go_up)
        self.act_refresh = self.toolbar.addAction(
            self._icon(QStyle.SP_BrowserReload), "Yenile", self.refresh)
        self.toolbar.addSeparator()
        self.act_export = self.toolbar.addAction(
            self._icon(QStyle.SP_DialogSaveButton), "Disa aktar", self.export_selected)
        self.act_import = self.toolbar.addAction(
            self._icon(QStyle.SP_DialogOpenButton), "Dosya ekle", self.import_files)
        self.act_import_dir = self.toolbar.addAction(
            self._icon(QStyle.SP_DirOpenIcon), "Klasor ekle", self.import_folder)
        self.toolbar.addSeparator()
        self.act_mkdir = self.toolbar.addAction(
            self._icon(QStyle.SP_FileDialogNewFolder), "Yeni klasor", self.make_dir)
        self.act_rename = self.toolbar.addAction(
            self._icon(QStyle.SP_FileDialogDetailedView), "Yeniden adlandir",
            self.rename_selected)
        self.act_delete = self.toolbar.addAction(
            self._icon(QStyle.SP_TrashIcon), "Sil", self.delete_selected)
        layout.addWidget(self.toolbar)

        path = QWidget()
        path_layout = QHBoxLayout(path)
        path_layout.setContentsMargins(6, 4, 6, 4)
        path_layout.addWidget(QLabel("Yol:"))
        self.path_edit = QLineEdit("/")
        self.path_edit.returnPressed.connect(self._path_entered)
        path_layout.addWidget(self.path_edit, 1)
        self.info_label = QLabel("")
        self.info_label.setEnabled(False)   # paletten soluk ton
        path_layout.addWidget(self.info_label)
        layout.addWidget(path)

        splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Klasorler")
        self.tree.setMinimumWidth(170)
        self.tree.itemExpanded.connect(self._expand_node)
        self.tree.itemClicked.connect(self._tree_clicked)
        splitter.addWidget(self.tree)

        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Ad", "Boyut", "Tur", "Degistirme", "Oznitelik"])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setSortingEnabled(True)
        self.list.sortByColumn(0, Qt.AscendingOrder)
        self.list.itemDoubleClicked.connect(self._list_double)
        self.list.itemSelectionChanged.connect(self._update_actions)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 90), (2, 110), (3, 140), (4, 80)):
            self.list.setColumnWidth(col, width)
        splitter.addWidget(self.list)
        splitter.setSizes([200, 640])
        layout.addWidget(splitter, 1)

    # -- baglama -------------------------------------------------------------
    def set_filesystem(self, fs: Optional[FileSystemAccess], title: str = "") -> None:
        self.fs = fs
        self.current_path = "/"
        self._title = title
        self.tree.clear()
        self.list.clear()
        self.path_edit.setText("/")
        if fs is None:
            self.info_label.setText("Bicimlendirilmemis bolum")
            self._update_actions()
            return
        if not fs.readable:
            self.info_label.setText(f"{fs.fs_type}: icerik goruntuleme desteklenmiyor")
            self._update_actions()
            return
        root = QTreeWidgetItem(self.tree, [title or "/"])
        root.setIcon(0, self._icon(QStyle.SP_DriveHDIcon))
        root.setData(0, Qt.UserRole, "/")
        root.setExpanded(True)
        self.tree.setCurrentItem(root)
        self._populate_tree(root, "/")
        self.navigate("/")

    def _populate_tree(self, item: QTreeWidgetItem, path: str) -> None:
        item.takeChildren()
        try:
            entries = [n for n in self.fs.listdir(path) if n.is_dir]
        except Exception:
            return
        for node in entries:
            child = QTreeWidgetItem(item, [node.name])
            child.setIcon(0, self._icon(QStyle.SP_DirIcon))
            child.setData(0, Qt.UserRole, node.path)
            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)

    def _expand_node(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, Qt.UserRole)
        if path and self.fs:
            self._populate_tree(item, path)

    def _tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        path = item.data(0, Qt.UserRole)
        if path:
            self.navigate(path)

    # -- gezinme -------------------------------------------------------------
    def navigate(self, path: str) -> None:
        if not self.fs or not self.fs.readable:
            return
        try:
            entries = self.fs.listdir(path)
        except Exception as exc:
            QMessageBox.warning(self, "Klasor acilamadi", str(exc))
            return
        self.current_path = path or "/"
        self.path_edit.setText(self.current_path)
        self.list.setSortingEnabled(False)
        self.list.clear()
        dir_count = file_count = 0
        total = 0
        for node in entries:
            item = QTreeWidgetItem(self.list)
            item.setText(0, node.name)
            if node.is_dir:
                item.setIcon(0, self._icon(QStyle.SP_DirIcon))
                item.setText(1, "")
                item.setText(2, "Klasor")
                dir_count += 1
            else:
                item.setIcon(0, self._icon(QStyle.SP_FileIcon))
                item.setText(1, human_size(node.size))
                item.setText(2, self._file_kind(node.name))
                file_count += 1
                total += node.size
            item.setText(3, node.mtime.strftime("%Y-%m-%d %H:%M") if node.mtime else "")
            item.setText(4, node.attr_text)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setData(0, Qt.UserRole, node)
            if node.hidden:
                item.setForeground(0, palette_color(self.list, "dim"))
        self.list.setSortingEnabled(True)
        self.info_label.setText(
            f"{dir_count} klasor, {file_count} dosya — {human_size(total)}")
        self._update_actions()

    @staticmethod
    def _file_kind(name: str) -> str:
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        kinds = {"txt": "Metin belgesi", "log": "Gunluk dosyasi",
                 "png": "PNG goruntu", "jpg": "JPEG goruntu", "jpeg": "JPEG goruntu",
                 "pdf": "PDF belgesi", "zip": "Sikistirilmis arsiv",
                 "exe": "Uygulama", "dll": "Kitaplik", "py": "Python betigi",
                 "img": "Disk goruntusu", "iso": "ISO goruntusu",
                 "bin": "Ikili dosya", "sys": "Sistem dosyasi"}
        return kinds.get(ext, f"{ext.upper()} dosyasi" if ext else "Dosya")

    def go_up(self) -> None:
        if self.current_path in ("/", ""):
            return
        parent = "/" + "/".join([p for p in self.current_path.split("/") if p][:-1])
        self.navigate(parent or "/")

    def refresh(self) -> None:
        if self.fs and self.fs.readable:
            root = self.tree.topLevelItem(0)
            if root:
                self._populate_tree(root, "/")
            self.navigate(self.current_path)

    def _path_entered(self) -> None:
        self.navigate(self.path_edit.text().strip() or "/")

    def _list_double(self, item: QTreeWidgetItem, _col: int) -> None:
        node: FileNode = item.data(0, Qt.UserRole)
        if node and node.is_dir:
            self.navigate(node.path)
        elif node:
            self.preview_file(node)

    # -- islemler ------------------------------------------------------------
    def selected_nodes(self) -> List[FileNode]:
        return [i.data(0, Qt.UserRole) for i in self.list.selectedItems()
                if i.data(0, Qt.UserRole)]

    def export_selected(self) -> None:
        nodes = self.selected_nodes()
        if not self.fs or not nodes:
            return
        target = QFileDialog.getExistingDirectory(self, "Disa aktarma klasoru")
        if not target:
            return
        total = sum(max(0, n.size) for n in nodes) or 1

        def work(report):
            done = 0
            copied = 0
            problems = []
            for node in nodes:
                report(f"{node.name} disa aktariliyor...",
                       int(100 * done / total))
                try:
                    self.fs.extract(node.path, target)
                    copied += 1
                except Exception as exc:
                    problems.append(f"{node.name}: {exc}")
                done += max(0, node.size)
            return copied, problems

        ok, result = run_task(self, "Disa aktariliyor", work)
        if not ok:
            QMessageBox.warning(self, "Disa aktarma hatasi", str(result))
            return
        copied, problems = result
        if problems:
            QMessageBox.warning(self, "Disa aktarma hatasi",
                                "\n".join(problems[:10]))
        self.statusMessage.emit(f"{copied} oge disa aktarildi -> {target}")

    def import_files(self) -> None:
        """Secilen dosyalari bolume kopyalar (ilerleme penceresi ile).

        Kopyalama arayuz is parcaciginda yapilmaz: fiziksel bir diske buyuk bir
        dosya yazmak dakikalarca surebilir ve kullaniciya donma gibi gorunur
        (bkz. ADR 0021).
        """
        if not self._require_writable():
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Eklenecek dosyalar")
        if not files:
            return
        sizes = []
        for path in files:
            try:
                sizes.append(os.path.getsize(path))
            except OSError:
                sizes.append(0)
        total = sum(sizes) or 1
        dest = self.current_path

        def work(report):
            done = 0
            copied = 0
            problems = []
            for path, size in zip(files, sizes):
                # Tek dosya, tek adimdir: yuzde hesaplanamaz, belirsiz cubuk
                # gosterilir. Cok dosyada bayta gore ilerleme anlamlidir.
                pct = -1 if len(files) == 1 else int(100 * done / total)
                report(f"{os.path.basename(path)} yaziliyor "
                       f"({human_size(size)})...", pct)
                try:
                    self.fs.import_file(path, dest)
                    copied += 1
                except Exception as exc:
                    problems.append(f"{os.path.basename(path)}: {exc}")
                done += size
            report("Tamamlaniyor...", 100)
            return copied, problems

        ok, result = run_task(self, f"Kopyalaniyor — {human_size(total)}", work)
        if not ok:
            QMessageBox.warning(self, "Ekleme hatasi", str(result))
            result = (0, [])
        copied, problems = result
        if problems:
            QMessageBox.warning(self, "Ekleme hatasi",
                                "\n".join(problems[:10]))
        self.refresh()
        self.contentChanged.emit()
        self.statusMessage.emit(f"{copied} dosya eklendi ({human_size(total)})")

    def import_folder(self) -> None:
        if not self._require_writable():
            return
        folder = QFileDialog.getExistingDirectory(self, "Eklenecek klasor")
        if not folder:
            return
        dest = self.current_path

        def work(report):
            def on_file(done: int, total: int, name: str) -> None:
                pct = int(100 * done / total) if total else 100
                report(f"{name or 'Tamamlaniyor'} ({human_size(done)} / "
                       f"{human_size(total)})", pct)
            return self.fs.import_tree(folder, dest, progress=on_file)

        ok, result = run_task(self, "Klasor kopyalaniyor", work)
        if not ok:
            QMessageBox.warning(self, "Ekleme hatasi", str(result))
            return
        self.refresh()
        self.contentChanged.emit()
        self.statusMessage.emit(f"Klasor eklendi ({result} dosya)")

    def make_dir(self) -> None:
        if not self._require_writable():
            return
        name, ok = QInputDialog.getText(self, "Yeni klasor", "Klasor adi:")
        if not ok or not name.strip():
            return
        try:
            self.fs.mkdir(self.current_path.rstrip("/") + "/" + name.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Klasor olusturulamadi", str(exc))
            return
        self.refresh()
        self.contentChanged.emit()

    def rename_selected(self) -> None:
        if not self._require_writable():
            return
        nodes = self.selected_nodes()
        if len(nodes) != 1:
            return
        name, ok = QInputDialog.getText(self, "Yeniden adlandir", "Yeni ad:",
                                         text=nodes[0].name)
        if not ok or not name.strip() or name == nodes[0].name:
            return
        try:
            self.fs.rename(nodes[0].path, name.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Yeniden adlandirilamadi", str(exc))
            return
        self.refresh()
        self.contentChanged.emit()

    def delete_selected(self) -> None:
        if not self._require_writable():
            return
        nodes = self.selected_nodes()
        if not nodes:
            return
        adlar = ", ".join(n.name for n in nodes[:5])
        if len(nodes) > 5:
            adlar += f" ve {len(nodes)-5} oge daha"
        cevap = QMessageBox.question(
            self, "Silme onayi",
            f"Su ogeler kalici olarak silinecek:\n\n{adlar}\n\nDevam edilsin mi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if cevap != QMessageBox.Yes:
            return
        # Basarisiz silmeler sayilir: eskiden hata kutusu gosterilse bile islem
        # gunlugune "N oge silindi" yaziliyordu ve kullanici hangi ogenin
        # gercekten silindigini gunlukten anlayamiyordu (2026-09-15).
        removed = 0
        for node in nodes:
            try:
                self.fs.remove(node.path, recursive=True)
                removed += 1
            except Exception as exc:
                QMessageBox.warning(self, "Silme hatasi", f"{node.name}: {exc}")
        self.refresh()
        self.contentChanged.emit()
        if removed == len(nodes):
            self.statusMessage.emit(f"{removed} oge silindi")
        else:
            self.statusMessage.emit(
                f"{removed}/{len(nodes)} oge silindi — {len(nodes) - removed} "
                "oge silinemedi")

    def preview_file(self, node: FileNode) -> None:
        try:
            veri = self.fs.read(node.path, 64 * 1024)
        except Exception as exc:
            QMessageBox.warning(self, "Okuma hatasi", str(exc))
            return
        try:
            metin = veri.decode("utf-8")
        except UnicodeDecodeError:
            try:
                metin = veri.decode("cp1254")
            except UnicodeDecodeError:
                metin = None
        from ..dialogs.base import exec_dialog
        from ..dialogs.preview import PreviewDialog
        exec_dialog(PreviewDialog(node, veri, metin, self))

    def _require_writable(self) -> bool:
        if not self.fs or not self.fs.readable:
            return False
        if not self.fs.writable:
            # Neden surucuden gelir: kaynagin salt okunur acilmasi ile
            # surucunun yazma destegi olmamasi ayni sey degildir.
            QMessageBox.information(self, "Yazma yapilamiyor",
                                    self.fs.write_reason)
            return False
        return True

    def _update_actions(self) -> None:
        var = self.fs is not None and self.fs.readable
        yazilabilir = var and self.fs.writable
        choice = bool(self.list.selectedItems())
        self.act_up.setEnabled(var and self.current_path != "/")
        self.act_refresh.setEnabled(var)
        self.act_export.setEnabled(var and choice)
        self.act_import.setEnabled(yazilabilir)
        self.act_import_dir.setEnabled(yazilabilir)
        self.act_mkdir.setEnabled(yazilabilir)
        self.act_rename.setEnabled(yazilabilir and len(self.list.selectedItems()) == 1)
        self.act_delete.setEnabled(yazilabilir and choice)
        # Pasif bir dugme sessizce pasif kalmamali: nedeni ipucunda dursun,
        # kullanici fareyi uzerine getirince ogrensin.
        reason = "" if (yazilabilir or not var) else self.fs.write_reason
        for act in (self.act_import, self.act_import_dir, self.act_mkdir,
                    self.act_rename, self.act_delete):
            act.setToolTip(reason or act.text())

    def _context_menu(self, pos) -> None:
        if not self.fs or not self.fs.readable:
            return
        menu = QMenu(self)
        nodes = self.selected_nodes()
        if len(nodes) == 1 and not nodes[0].is_dir:
            menu.addAction("Onizleme", lambda: self.preview_file(nodes[0]))
        if nodes:
            menu.addAction("Disa aktar...", self.export_selected)
            menu.addSeparator()
        if self.fs.writable:
            menu.addAction("Dosya ekle...", self.import_files)
            menu.addAction("Klasor ekle...", self.import_folder)
            menu.addAction("Yeni klasor", self.make_dir)
            if len(nodes) == 1:
                menu.addAction("Yeniden adlandir", self.rename_selected)
            if nodes:
                menu.addSeparator()
                menu.addAction("Sil", self.delete_selected)
        menu.addSeparator()
        menu.addAction("Yenile", self.refresh)
        menu.exec_(self.list.viewport().mapToGlobal(pos))
