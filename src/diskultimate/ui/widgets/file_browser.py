"""Bolum icerigi gezgini: klasor agaci + dosya listesi."""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QFileDialog,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                             QLineEdit, QMenu, QMessageBox, QSplitter,
                             QToolBar, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from ...core.fsregistry import fs_display
from ...core.filesystem import FileNode, FileSystemAccess
from ...core.ptable import human_size
from ..dialogs.task import run_task
from ..icons import icon as app_icon
from ..theme import palette_color
from ...i18n import tr, trn


class FileBrowser(QWidget):
    """Secili bolumun dosya sistemi icerigini gosterir ve duzenler."""

    statusMessage = pyqtSignal(str)
    contentChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fs: Optional[FileSystemAccess] = None
        self.current_path = "/"
        # Ana pencere doldurur: kaynagi yazma moduna alip TAZE dosya sistemi
        # dondurur (bkz. `_require_writable`).
        self.ensure_writable = None
        self._build()
        self._update_actions()

    # -- arayuz --------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QToolBar()
        self.toolbar.setIconSize(QSize(20, 20))
        self.act_up = self.toolbar.addAction(
            app_icon("up"), tr("Ust klasor"), self.go_up)
        self.act_refresh = self.toolbar.addAction(
            app_icon("refresh"), tr("Yenile"), self.refresh)
        self.toolbar.addSeparator()
        self.act_export = self.toolbar.addAction(
            app_icon("export"), tr("Disa aktar"), self.export_selected)
        self.act_import = self.toolbar.addAction(
            app_icon("import"), tr("Dosya ekle"), self.import_files)
        self.act_import_dir = self.toolbar.addAction(
            app_icon("folder-add"), tr("Klasor ekle"), self.import_folder)
        self.toolbar.addSeparator()
        self.act_mkdir = self.toolbar.addAction(
            app_icon("folder-new"), tr("Yeni klasor"), self.make_dir)
        self.act_rename = self.toolbar.addAction(
            app_icon("rename"), tr("Yeniden adlandir"),
            self.rename_selected)
        self.act_delete = self.toolbar.addAction(
            app_icon("trash"), tr("Sil"), self.delete_selected)
        layout.addWidget(self.toolbar)

        path = QWidget()
        path_layout = QHBoxLayout(path)
        path_layout.setContentsMargins(6, 4, 6, 4)
        self.path_label = QLabel(tr("Yol:"))
        path_layout.addWidget(self.path_label)
        self.path_edit = QLineEdit("/")
        self.path_edit.returnPressed.connect(self._path_entered)
        path_layout.addWidget(self.path_edit, 1)
        self.info_label = QLabel("")
        self.info_label.setEnabled(False)   # paletten soluk ton
        path_layout.addWidget(self.info_label)
        layout.addWidget(path)

        splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setIconSize(QSize(20, 20))
        self.tree.setHeaderLabel(tr("Klasorler"))
        self.tree.setMinimumWidth(170)
        self.tree.itemExpanded.connect(self._expand_node)
        self.tree.itemClicked.connect(self._tree_clicked)
        splitter.addWidget(self.tree)

        self.list = QTreeWidget()
        self.list.setIconSize(QSize(20, 20))
        self.list.setHeaderLabels([tr("Ad"), tr("Boyut"), tr("Tur"), tr("Degistirme"), tr("Oznitelik")])
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

    def retranslate(self) -> None:
        """Dil degisince gorunen metinleri yeniden yazar (ADR 0027)."""
        self.act_up.setText(tr("Ust klasor"))
        self.act_refresh.setText(tr("Yenile"))
        self.act_export.setText(tr("Disa aktar"))
        self.act_import.setText(tr("Dosya ekle"))
        self.act_import_dir.setText(tr("Klasor ekle"))
        self.act_mkdir.setText(tr("Yeni klasor"))
        self.act_rename.setText(tr("Yeniden adlandir"))
        self.act_delete.setText(tr("Sil"))
        self.path_label.setText(tr("Yol:"))
        self.tree.setHeaderLabel(tr("Klasorler"))
        self.list.setHeaderLabels([tr("Ad"), tr("Boyut"), tr("Tur"),
                                   tr("Degistirme"), tr("Oznitelik")])
        if self.fs is None:
            self.info_label.setText(tr("Bicimlendirilmemis bolum"))
        elif not self.fs.readable:
            self.info_label.setText(
                tr("{}: icerik goruntuleme desteklenmiyor", fs_display(self.fs.fs_type)))
        else:
            # Sessiz: dil degisimi kozmetiktir, hata penceresi dogurmamalidir.
            self.navigate(self.current_path, quiet=True)
        self._update_actions()

    # -- baglama -------------------------------------------------------------
    def set_filesystem(self, fs: Optional[FileSystemAccess], title: str = "") -> None:
        self.fs = fs
        self.current_path = "/"
        self._title = title
        self.tree.clear()
        self.list.clear()
        self.path_edit.setText("/")
        if fs is None:
            self.info_label.setText(tr("Bicimlendirilmemis bolum"))
            self._update_actions()
            return
        if not fs.readable:
            self.info_label.setText(tr("{}: icerik goruntuleme desteklenmiyor",
                                       fs_display(fs.fs_type)))
            self._update_actions()
            return
        root = QTreeWidgetItem(self.tree, [title or "/"])
        root.setIcon(0, app_icon("partition"))
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
            child.setIcon(0, app_icon("folder"))
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
    def navigate(self, path: str, quiet: bool = False) -> None:
        """Klasoru listeler.

        `quiet=True` hatada **pencere acmaz**, yalnizca bilgi satirina yazar.
        Kullanicinin baslatmadigi tazelemeler bunu kullanir: dil degistirmek
        bir dosya islemi degildir ve oradan cikan modal bir pencere arayuzu
        kilitler — otomatik kosumda kapatacak kimse olmadigi icin uygulama
        sonsuza kadar bekler (olculdu: `ui_smoke` Linux'ta burada donuyordu).
        """
        if not self.fs or not self.fs.readable:
            return
        try:
            entries = self.fs.listdir(path)
        except Exception as exc:
            if quiet:
                self.info_label.setText(tr("Klasor acilamadi: {}", exc))
                return
            QMessageBox.warning(self, tr("Klasor acilamadi"), str(exc))
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
                item.setIcon(0, app_icon("folder"))
                item.setText(1, "")
                item.setText(2, tr("Klasor"))
                dir_count += 1
            else:
                item.setIcon(0, app_icon("file"))
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
            tr("{} klasor, {} dosya — {}", dir_count, file_count, human_size(total)))
        self._update_actions()

    @staticmethod
    def _file_kind(name: str) -> str:
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        kinds = {"txt": tr("Metin belgesi"), "log": tr("Gunluk dosyasi"),
                 "png": tr("PNG goruntu"), "jpg": tr("JPEG goruntu"),
                 "jpeg": tr("JPEG goruntu"), "pdf": tr("PDF belgesi"),
                 "zip": tr("Sikistirilmis arsiv"), "exe": tr("Uygulama"),
                 "dll": tr("Kitaplik"), "py": tr("Python betigi"),
                 "img": tr("Disk goruntusu"), "iso": tr("ISO goruntusu"),
                 "bin": tr("Ikili dosya"), "sys": tr("Sistem dosyasi")}
        return kinds.get(ext, tr("{} dosyasi", ext.upper()) if ext
                         else tr("Dosya"))

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
        target = QFileDialog.getExistingDirectory(self, tr("Disa aktarma klasoru"))
        if not target:
            return
        total = sum(max(0, n.size) for n in nodes) or 1

        def work(report):
            done = 0
            copied = 0
            problems = []
            for node in nodes:
                report(tr("{} disa aktariliyor...", node.name),
                       int(100 * done / total))
                try:
                    self.fs.extract(node.path, target)
                    copied += 1
                except Exception as exc:
                    problems.append(f"{node.name}: {exc}")
                done += max(0, node.size)
            return copied, problems

        ok, result = run_task(self, tr("Disa aktariliyor"), work)
        if not ok:
            QMessageBox.warning(self, tr("Disa aktarma hatasi"), str(result))
            return
        copied, problems = result
        if problems:
            QMessageBox.warning(self, tr("Disa aktarma hatasi"),
                                "\n".join(problems[:10]))
        self.statusMessage.emit(tr("{} oge disa aktarildi -> {}", copied, target))

    def import_files(self) -> None:
        """Secilen dosyalari bolume kopyalar (ilerleme penceresi ile).

        Kopyalama arayuz is parcaciginda yapilmaz: fiziksel bir diske buyuk bir
        dosya yazmak dakikalarca surebilir ve kullaniciya donma gibi gorunur
        (bkz. ADR 0021).
        """
        if not self._require_writable():
            return
        files, _ = QFileDialog.getOpenFileNames(self, tr("Eklenecek dosyalar"))
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
            # Ilerleme dosyanin icinde de yurur (yazilan bayt; ADR 0073):
            # tek buyuk dosyada da cubuk ve kalan sure gorunur.
            def on_bytes(done: int, all_bytes: int, name: str) -> None:
                pct = 100.0 * done / all_bytes if all_bytes else 100.0
                if name:
                    report(tr("{} yaziliyor ({} / {})", name, human_size(done),
                              human_size(all_bytes)), pct)
                else:
                    report(tr("Tamamlaniyor..."), 100)
            return self.fs.import_files(files, dest, progress=on_bytes)

        ok, result = run_task(self, tr("Kopyalaniyor — {}", human_size(total)), work)
        if not ok:
            QMessageBox.warning(self, tr("Ekleme hatasi"), str(result))
            result = (0, [])
        copied, problems = result
        if problems:
            QMessageBox.warning(self, tr("Ekleme hatasi"),
                                "\n".join(problems[:10]))
        self.refresh()
        self.contentChanged.emit()
        self.statusMessage.emit(
            trn("{} dosya eklendi ({})", "{} dosya eklendi ({})", copied,
                copied, human_size(total)))

    def import_folder(self) -> None:
        if not self._require_writable():
            return
        folder = QFileDialog.getExistingDirectory(self, tr("Eklenecek klasor"))
        if not folder:
            return
        dest = self.current_path

        def work(report):
            def on_file(done: int, total: int, name: str) -> None:
                pct = 100.0 * done / total if total else 100.0
                report(tr("{} ({} / {})", name or tr("Tamamlaniyor"),
                          human_size(done), human_size(total)), pct)
            return self.fs.import_tree(folder, dest, progress=on_file)

        ok, result = run_task(self, tr("Klasor kopyalaniyor"), work)
        if not ok:
            QMessageBox.warning(self, tr("Ekleme hatasi"), str(result))
            return
        self.refresh()
        self.contentChanged.emit()
        self.statusMessage.emit(tr("Klasor eklendi ({} dosya)", result))

    def make_dir(self) -> None:
        if not self._require_writable():
            return
        name, ok = QInputDialog.getText(self, tr("Yeni klasor"), tr("Klasor adi:"))
        if not ok or not name.strip():
            return
        try:
            self.fs.mkdir(self.current_path.rstrip("/") + "/" + name.strip())
        except Exception as exc:
            QMessageBox.warning(self, tr("Klasor olusturulamadi"), str(exc))
            return
        self.refresh()
        self.contentChanged.emit()

    def rename_selected(self) -> None:
        if not self._require_writable():
            return
        nodes = self.selected_nodes()
        if len(nodes) != 1:
            return
        name, ok = QInputDialog.getText(self, tr("Yeniden adlandir"), tr("Yeni ad:"),
                                         text=nodes[0].name)
        if not ok or not name.strip() or name == nodes[0].name:
            return
        try:
            self.fs.rename(nodes[0].path, name.strip())
        except Exception as exc:
            QMessageBox.warning(self, tr("Yeniden adlandirilamadi"), str(exc))
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
            self, tr("Silme onayi"),
            tr("Su ogeler kalici olarak silinecek:\n\n{}\n\nDevam edilsin mi?", adlar),
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
                QMessageBox.warning(self, tr("Silme hatasi"), f"{node.name}: {exc}")
        self.refresh()
        self.contentChanged.emit()
        if removed == len(nodes):
            self.statusMessage.emit(
                trn("{} oge silindi", "{} oge silindi", removed, removed))
        else:
            self.statusMessage.emit(
                tr("{}/{} oge silindi — {} oge silinemedi",
                   removed, len(nodes), len(nodes) - removed))

    def preview_file(self, node: FileNode) -> None:
        try:
            veri = self.fs.read(node.path, 64 * 1024)
        except Exception as exc:
            QMessageBox.warning(self, tr("Okuma hatasi"), str(exc))
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
        """Yazma islemi yapilabilir mi? Gerekiyorsa kaynagi yazma moduna alir.

        Dosya islemleri bekleyen islem kuyruguna **girmez** (bir dosya
        sisteminin icinde olur, disk yerlesimini degistirmez — ADR 0025). Ama
        kaynak artik her zaman salt okunur aciliyor; bu yuzden ilk yazmada
        yetki burada istenir. Aksi halde fiziksel diske dosya eklemek
        **hicbir zaman** mumkun olmazdi (Linux Mint uzerinde bir USB diske
        dosya eklenemedi — kullanici bildirimi).

        Yazma moduna gecis kaynagi yeniden acar, yani eldeki `fs` nesnesi
        olur; `ensure_writable` tazesini dondurur ve gezgin ona baglanir.
        """
        if not self.fs or not self.fs.readable:
            return False
        if self.fs.writable:
            return True
        if self.ensure_writable is not None:
            path = self.current_path
            fresh = self.ensure_writable()
            if fresh is not None and getattr(fresh, "writable", False):
                self.fs = fresh
                if path and path != self.current_path:
                    self.navigate(path)
                return True
            if fresh is not None:
                # Kaynak yazilabilir oldu ama dosya sistemi surucusu yazmayi
                # desteklemiyor: ayri bir durum, ayri bir mesaj.
                self.fs = fresh
        # Neden surucuden gelir: kaynagin salt okunur acilmasi ile
        # surucunun yazma destegi olmamasi ayni sey degildir.
        QMessageBox.information(self, tr("Yazma yapilamiyor"),
                                self.fs.write_reason)
        return False

    def _update_actions(self) -> None:
        var = self.fs is not None and self.fs.readable
        # Kaynak su an salt okunur olsa bile yazma dugmeleri **etkin kalir**:
        # yetki ilk yazma denemesinde istenir (ADR 0025). Pasif dugme,
        # kullaniciya "bu is hic yapilamaz" der ki dogru degildir.
        yazilabilir = var and (self.fs.writable
                               or self.ensure_writable is not None)
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
            menu.addAction(tr("Onizleme"), lambda: self.preview_file(nodes[0]))
        if nodes:
            menu.addAction(tr("Disa aktar..."), self.export_selected)
            menu.addSeparator()
        if self.fs.writable:
            menu.addAction(tr("Dosya ekle..."), self.import_files)
            menu.addAction(tr("Klasor ekle..."), self.import_folder)
            menu.addAction(tr("Yeni klasor"), self.make_dir)
            if len(nodes) == 1:
                menu.addAction(tr("Yeniden adlandir"), self.rename_selected)
            if nodes:
                menu.addSeparator()
                menu.addAction(tr("Sil"), self.delete_selected)
        menu.addSeparator()
        menu.addAction(tr("Yenile"), self.refresh)
        menu.exec_(self.list.viewport().mapToGlobal(pos))
