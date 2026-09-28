"""Geri yuklemede "Bolumleri yonet" — ortak pencerenin geri yukleme kipi.

Pencere `partition_layout.PartitionLayoutDialog`a tasindi (ADR 0049, 5.
asama); ana ekranin "Bolum duzeni" penceresiyle ayni koddur. Bu ad geriye
uyumluluk icin durur.
"""
from __future__ import annotations

from .partition_layout import PartitionLayoutDialog


class RestoreLayoutDialog(PartitionLayoutDialog):
    """Yedekteki bolumlerin hedefteki yerlesimini duzenler."""

    def __init__(self, layout, target_label: str, parent=None):
        super().__init__(layout, target_label, parent, mode="restore")
