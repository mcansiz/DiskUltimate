"""Sektor duzeyinde onaltilik (hex) goruntuleyici."""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

from ...core.image import BlockDevice

BYTES_PER_ROW = 16


def hexdump(data: bytes, base_offset: int = 0) -> str:
    satirlar = []
    for i in range(0, len(data), BYTES_PER_ROW):
        parca = data[i:i + BYTES_PER_ROW]
        hexler = " ".join(f"{b:02X}" for b in parca).ljust(BYTES_PER_ROW * 3 - 1)
        ortada = hexler[:BYTES_PER_ROW // 2 * 3] + " " + hexler[BYTES_PER_ROW // 2 * 3:]
        metin = "".join(chr(b) if 32 <= b < 127 else "." for b in parca)
        satirlar.append(f"{base_offset + i:010X}  {ortada}  |{metin}|")
    return "\n".join(satirlar)


class HexViewer(QWidget):
    """Secili aygitin (goruntu veya bolum) sektorlerini gosterir."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.device: Optional[BlockDevice] = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        ust = QHBoxLayout()
        ust.addWidget(QLabel("Sektor (LBA):"))
        self.spin = QSpinBox()
        self.spin.setRange(0, 2 ** 31 - 1)
        self.spin.setFixedWidth(130)
        self.spin.valueChanged.connect(self.refresh)
        ust.addWidget(self.spin)
        self.btn_prev = QPushButton("< Onceki")
        self.btn_prev.clicked.connect(lambda: self.spin.setValue(max(0, self.spin.value() - 1)))
        self.btn_next = QPushButton("Sonraki >")
        self.btn_next.clicked.connect(lambda: self.spin.setValue(self.spin.value() + 1))
        ust.addWidget(self.btn_prev)
        ust.addWidget(self.btn_next)
        ust.addSpacing(12)
        self.lbl_kaynak = QLabel("-")
        self.lbl_kaynak.setEnabled(False)   # paletten soluk ton
        ust.addWidget(self.lbl_kaynak, 1)
        layout.addLayout(ust)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(9)
        self.view.setFont(font)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.view, 1)

    def set_device(self, device: Optional[BlockDevice], title: str = "") -> None:
        self.device = device
        self.lbl_kaynak.setText(title or "-")
        if device is not None:
            self.spin.setMaximum(max(0, device.sector_count - 1))
            self.spin.setValue(0)
        self.refresh()

    def refresh(self) -> None:
        if self.device is None:
            self.view.setPlainText("Goruntulenecek aygit secilmedi.")
            return
        lba = self.spin.value()
        try:
            veri = self.device.read_sectors(lba, 1)
        except Exception as exc:
            self.view.setPlainText(f"Okuma hatasi: {exc}")
            return
        self.view.setPlainText(hexdump(veri, lba * self.device.sector_size))
