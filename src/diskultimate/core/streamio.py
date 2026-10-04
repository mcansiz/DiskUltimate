"""Akis (parca parca) dosya yazma yardimcilari — ADR 0081.

Dosya sistemi yazicilari eskiden dosyanin tamamini tek `bytes` olarak
aliyordu: 5 GB'lik bir dosya 5 GB bellek isterdi (ADR 0073'teki sinir).
Yazicilarin hepsi yeri **once boyuta gore** ayirir, veriyi sonra yazar; bu
modul o ikinci adimi kaynaktan parca parca okuyarak yapar.

Kaynak, `read(n)` yontemi olan herhangi bir nesnedir (acik dosya,
`io.BytesIO`). Boyut onceden bilinir; kaynak erken biterse (dosya yazilirken
kuculduyse) yazma **hata verir** — eksik dosyayi sessizce sifirla doldurmak
veri bozulmasini gizlerdi.
"""
from __future__ import annotations

import io
from typing import BinaryIO, Callable, Iterable, List, Sequence, Tuple, Union

from ..i18n import tr

CHUNK = 4 * 1024 * 1024          # tek seferde okunan/yazilan en fazla bayt

Source = Union[BinaryIO, io.BytesIO]


class StreamError(Exception):
    """Kaynak beklenen boyutta veri vermedi."""


def read_exact(src: Source, n: int) -> bytes:
    """Kaynaktan tam `n` bayt okur; eksik kalirsa `StreamError`."""
    parts: List[bytes] = []
    left = n
    while left > 0:
        block = src.read(left)
        if not block:
            raise StreamError(tr("Kaynak dosya beklenenden kisa ({} bayt eksik); "
                                 "yazilirken degismis olabilir", left))
        parts.append(block)
        left -= len(block)
    return parts[0] if len(parts) == 1 else b"".join(parts)


def group_runs(units: Iterable[int]) -> List[Tuple[int, int]]:
    """Sirali birim numaralarini (kume/blok) ardisik (baslangic, sayi)
    parcalarina toplar; sira korunur."""
    runs: List[Tuple[int, int]] = []
    for u in units:
        if runs and runs[-1][0] + runs[-1][1] == u:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((u, 1))
    return runs


def write_runs(src: Source, size: int, runs: Sequence[Tuple[int, int]],
               unit_bytes: int, write: Callable[[int, int, bytes], None]) -> int:
    """`size` bayti kaynaktan okuyup parcalara yazar.

    Her parca icin `write(baslangic_birimi, ofset_bayt, veri)` cagrilir;
    `ofset_bayt` parcanin basindan itibarendir. Son birim sifirla doldurulur
    (onceki davranisla ayni). Yazilan veri bayti doner.
    """
    per_chunk = max(1, CHUNK // unit_bytes) * unit_bytes
    left = size
    for start, count in runs:
        run_bytes = count * unit_bytes
        offset = 0
        while offset < run_bytes and left > 0:
            take = min(per_chunk, run_bytes - offset, left)
            data = read_exact(src, take)
            left -= take
            if take % unit_bytes:
                data += b"\x00" * (unit_bytes - take % unit_bytes)
            write(start, offset, data)
            offset += len(data)
        if left <= 0:
            break
    if left > 0:
        raise StreamError(tr("Ayrilan alan veriye yetmedi ({} bayt kaldi)", left))
    return size
