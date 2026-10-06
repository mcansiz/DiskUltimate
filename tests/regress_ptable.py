"""Bolum tablosu uyumluluk regresyon testleri (P1-P8, denetim 2026-10-06).

    python3 -m tests.regress_ptable

Girdiler **bizim kodumuzla degil** util-linux `sfdisk` ile uretilir ve
sonuc `sfdisk --json` / `sfdisk -V` ile karsilastirilir: testler yalnizca
kendi urettigimiz tablolari kullandigi icin mkdosfs FAT32'si gibi yabanci
girdiler yanlis okunmustu. `sgdisk` varsa GPT ayrica `sgdisk -v` ile
dogrulanir. Arac yoksa test ATLANDI yazar, sessizce gecmez.

Kapsam:
  P1  4Kn diskte GPT->MBR (MBR sektor boyunda yazilir; once dogrulama,
      sonra MBR, en son GPT silme; bolum icine silme yok)
  P2  bayat GPT + gecerli DOS MBR: MBR gorunur, usedmap tamamini alir
  P3  bozuk birincil GPT: yedek kullanilir, yazim birincili onarir
  P4  yuva numaralari korunur (GPT 1/3/5, MBR 2/4, mantiksal 5/6)
  P5  hibrit MBR korunur ya da yazma reddedilir
  P6  FirstUsableLBA=34 korunur
  P7  yedek GPT son bolume degiyorsa yazma/kucultme reddedilir
  P8  4Kn GPT goruntusu 4096 bayt sektorle acilir
  EBR girisleri herhangi bir yuvada olabilir (cekirdek gibi)

Yalnizca goruntu dosyalari kullanilir (CLAUDE.md test ortami kurali).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from diskultimate.core import usedmap  # noqa: E402
from diskultimate.core.gpt import GPTTable  # noqa: E402
from diskultimate.core.image import DiskImage  # noqa: E402
from diskultimate.core.mbr import MBRTable  # noqa: E402
from diskultimate.core.ptable import Partition, PartitionTableError  # noqa: E402
from diskultimate.core.session import (DiskSession, SessionError,  # noqa: E402
                                       read_partition_table)
from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024
LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"


class Skip(Exception):
    pass


def _work(name: str) -> str:
    return os.path.join(scratch("regress_ptable"), name)


def _tool(name: str) -> str:
    exe = shutil.which(name) or shutil.which(name, path="/sbin:/usr/sbin")
    if not exe:
        raise Skip(f"{name} yok")
    return exe


def _fresh(name: str, size: int) -> str:
    path = _work(name)
    if os.path.exists(path):
        os.unlink(path)
    with open(path, "wb") as fh:
        fh.truncate(size)
    return path


def _sfdisk(args, path, script=None, ss=None, check=True):
    cmd = [_tool("sfdisk"), "--no-reread", "--no-tell-kernel"]
    if ss:
        cmd += ["--sector-size", str(ss)]
    cmd += list(args) + [path]
    env = dict(os.environ, LC_ALL="C")
    res = subprocess.run(cmd, input=script, capture_output=True, text=True,
                         env=env)
    if ss and "--sector-size" in res.stderr and res.returncode != 0:
        # util-linux < 2.40 (Ubuntu 24.04: 2.39) bu secenegi tanimaz;
        # 4Kn karsilastirmasi o surumde yapilamaz -> gecmis sayilmaz.
        raise Skip(f"sfdisk --sector-size desteklemiyor: {res.stderr.strip()[-120:]}")
    if check and res.returncode != 0:
        raise AssertionError(f"sfdisk {args} basarisiz: {res.stderr}{res.stdout}")
    return res


def _make(path, script, ss=None):
    _sfdisk(["-q", "--wipe", "never"], path, script=script, ss=ss)


def _sf_json(path, ss=None) -> dict:
    """sfdisk'in gordugu tablo: {etiket, ilk_lba, bolumler{yuva: (bas, boy, tur, ad)}}."""
    res = _sfdisk(["-J"], path, ss=ss, check=False)
    if res.returncode != 0:
        return {"label": None, "parts": {}}
    table = json.loads(res.stdout)["partitiontable"]
    parts = {}
    for p in table.get("partitions", []):
        slot = int(re.search(r"(\d+)$", p["node"][len(table["device"]):]).group(1))
        parts[slot] = (p["start"], p["size"], p["type"].upper(), p.get("name", ""))
    return {"label": table["label"], "first": table.get("firstlba"),
            "parts": parts}


def _sf_verify(path, ss=None) -> None:
    res = _sfdisk(["-V"], path, ss=ss, check=False)
    text = res.stdout + res.stderr
    assert res.returncode == 0, f"sfdisk -V hata: {text}"
    if "gpt" in _sf_json(path, ss)["label"]:
        assert "No errors detected" in text, f"sfdisk -V: {text}"


def _sgdisk_verify(path) -> None:
    exe = shutil.which("sgdisk") or shutil.which("sgdisk", path="/sbin:/usr/sbin")
    if not exe:
        return                       # ayrica test_sgdisk_dogrulama ATLANDI yazar
    res = subprocess.run([exe, "-v", path], capture_output=True, text=True,
                         env=dict(os.environ, LC_ALL="C"))
    text = res.stdout + res.stderr
    assert "No problems found" in text, f"sgdisk -v: {text}"


def _ours(table) -> dict:
    return {p.index: (p.start_lba, p.sector_count)
            for p in table.partitions
            if not (p.scheme == "mbr" and p.type_id in (0x05, 0x0F, 0x85)
                    and not p.logical)}


def _theirs(info) -> dict:
    return {k: (v[0], v[1]) for k, v in info["parts"].items()
            if not (info["label"] == "dos" and v[2] in ("5", "F", "85"))}


def _sha(path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _same(session, path, ss=None):
    info = _sf_json(path, ss)
    assert _ours(session.table) == _theirs(info), \
        f"bizim {_ours(session.table)} != sfdisk {_theirs(info)}"
    return info


# ---------------------------------------------------------------------------
# P4: yuva numaralari
# ---------------------------------------------------------------------------
def test_gpt_yuvalari_korunur():
    """sfdisk GPT 1/3/5: ad, ekleme, silme, buyutme numaralari kaydirmaz."""
    path = _fresh("gpt_slots.img", 64 * MIB)
    _make(path, "label: gpt\n"
                "1: start=2048,size=20480\n"
                "3: start=22528,size=20480,name=veri\n"
                "5: start=43008,size=20480\n")
    with DiskSession.open(path) as s:
        assert s.scheme == "gpt"
        _same(s, path)
        assert sorted(p.index for p in s.partitions) == [1, 3, 5]
        s.set_partition_name(3, "yeni ad")
        info = _same(s, path)
        assert info["parts"][3][3] == "yeni ad"
        _sf_verify(path)
        # yeni bolum ilk bos yuvaya (2) girer
        part = s.create_partition(63488, 8192, wipe=False)
        assert part.index == 2, part.index
        _same(s, path)
        assert sorted(_sf_json(path)["parts"]) == [1, 2, 3, 5]
        s.delete_partition(1, wipe=False)
        _same(s, path)
        assert sorted(_sf_json(path)["parts"]) == [2, 3, 5]
        # ham bolumu buyut (2 -> sona dogru); digerleri yuvasinda kalir
        s.resize_partition(2, 63488, 8192 + 2048, confirm=True)
        info = _same(s, path)
        assert info["parts"][2][:2] == (63488, 10240), info["parts"][2]
        assert sorted(info["parts"]) == [2, 3, 5]
        assert info["parts"][3][3] == "yeni ad"
        _sf_verify(path)
    _sgdisk_verify(path)


def test_mbr_yuvalari_korunur():
    """sfdisk DOS 2/4: tur/onyukleme/ekleme/silme numaralari kaydirmaz."""
    path = _fresh("mbr_slots.img", 64 * MIB)
    _make(path, "label: dos\n"
                "2: start=2048,size=20480,type=83\n"
                "4: start=43008,size=20480,type=7\n")
    with DiskSession.open(path) as s:
        assert s.scheme == "mbr"
        _same(s, path)
        assert sorted(p.index for p in s.partitions) == [2, 4]
        s.set_bootable(4, True)
        s.set_partition_type(2, type_id=0x0C)
        info = _same(s, path)
        assert info["parts"][2][2] == "C" and info["parts"][4][2] == "7"
        part = s.create_partition(22528, 20480, type_id=0x83, wipe=False)
        assert part.index == 1, part.index
        _same(s, path)
        s.delete_partition(2, wipe=False)
        _same(s, path)
        assert sorted(_sf_json(path)["parts"]) == [1, 4]
        _sf_verify(path)


def test_mbr_mantiksal_numaralar():
    """Genisletilmis + mantiksal 5/6: okuma ve silme sonrasi sfdisk ile ayni."""
    path = _fresh("mbr_logical.img", 64 * MIB)
    _make(path, "label: dos\n"
                "1: start=2048,size=20480,type=83\n"
                "2: start=22528,size=80000,type=5\n"
                "5: start=24576,size=10000,type=83\n"
                "6: start=36864,size=10000,type=7\n")
    with DiskSession.open(path) as s:
        _same(s, path)
        s.set_partition_type(6, type_id=0x0C)
        _same(s, path)
        s.delete_partition(5, wipe=False)
        info = _same(s, path)
        assert info["parts"][5][:2] == (36864, 10000), info
        _sf_verify(path)


def test_ebr_girisi_her_yuvada():
    """EBR'de veri girisi 1. yuvada, baglanti 0. yuvada olsa da okunur."""
    path = _fresh("ebr_swap.img", 64 * MIB)
    _make(path, "label: dos\n"
                "1: start=2048,size=80000,type=5\n"
                "5: start=4096,size=10000,type=83\n"
                "6: start=16384,size=10000,type=83\n")
    before = _sf_json(path)
    ebr = 2048
    with open(path, "r+b") as fh:
        fh.seek(ebr * 512 + 446)
        e0, e1 = fh.read(16), fh.read(16)
        fh.seek(ebr * 512 + 446)
        fh.write(e1 + e0)          # yuvalari takas et
    after = _sf_json(path)
    with DiskSession.open(path, readonly=True) as s:
        ours = _ours(s.table)
    assert ours == _theirs(before), (ours, before)
    if _theirs(after) != _theirs(before):
        # sfdisk (libfdisk) yalnizca 0/1 yuvasina bakiyorsa cekirdek
        # davranisi esas alinir: cekirdek dort yuvayi da tarar.
        print("    not: sfdisk takasli EBR'yi farkli okudu:", _theirs(after))


# ---------------------------------------------------------------------------
# P2 / P3: hangi tablo gecerli
# ---------------------------------------------------------------------------
def test_bayat_gpt_dos_mbr():
    """GPT uzerine DOS MBR yazilmis: MBR gorunur, GPT yazilmaz, usedmap=None."""
    path = _fresh("stale_gpt.img", 128 * MIB)
    _make(path, "label: gpt\nstart=2048,size=20480\n")
    dos = _fresh("stale_gpt_dos.img", 128 * MIB)
    _make(dos, "label: dos\nstart=2048,size=100000,type=83\n")
    with open(dos, "rb") as fh:
        sector0 = fh.read(512)
    with open(path, "r+b") as fh:
        fh.write(sector0)
    info = _sf_json(path)
    assert info["label"] == "dos", info
    dev = DiskImage(path, readonly=True)
    try:
        assert not GPTTable.is_present(dev)
        table = read_partition_table(dev)
        assert table.scheme == "mbr", table.scheme
        assert _ours(table) == _theirs(info), (_ours(table), info)
        assert table.ambiguity, "bayat GPT kuskulu sayilmadi"
        assert usedmap.disk_used_ranges(dev) is None, \
            "kuskulu tabloda yalnizca kullanilan alan hesaplandi"
    finally:
        dev.close()
    # MBR'ye yazmak GPT'ye dokunmaz, MBR'yi korur
    with DiskSession.open(path) as s:
        s.set_bootable(1, True)
    info2 = _sf_json(path)
    assert info2["label"] == "dos" and _theirs(info2) == _theirs(info)


def test_temiz_tabloda_usedmap_calisir():
    """Kuskusuz sfdisk GPT'de usedmap None DONMEZ (her seyi tam almaz)."""
    path = _fresh("clean_used.img", 64 * MIB)
    _make(path, "label: gpt\nstart=2048,size=20480\n")
    dev = DiskImage(path, readonly=True)
    try:
        table = read_partition_table(dev)
        assert table.scheme == "gpt" and not table.ambiguity, table.ambiguity
        assert usedmap.disk_used_ranges(dev) is not None
    finally:
        dev.close()


def _corrupt(path, offset):
    with open(path, "r+b") as fh:
        fh.seek(offset)
        b = fh.read(1)
        fh.seek(offset)
        fh.write(bytes([b[0] ^ 0xFF]))


def test_bozuk_birincil_gpt():
    """Birincil giris dizisi/baslik bozuk: yedek okunur, yazim birincili onarir."""
    for what, offset in (("giris", 2 * 512 + 40), ("baslik", 512 + 60)):
        path = _fresh(f"bad_primary_{what}.img", 64 * MIB)
        _make(path, "label: gpt\n"
                    "1: start=2048,size=20480,name=bir\n"
                    "2: start=22528,size=20480,name=iki\n")
        good = _sf_json(path)
        _corrupt(path, offset)
        dev = DiskImage(path, readonly=True)
        try:
            table = read_partition_table(dev)
            assert table is not None and table.scheme == "gpt", what
            assert not table.primary_ok and table.header_backup_ok, what
            assert _ours(table) == _theirs(good), (what, _ours(table))
            assert table.ambiguity
            assert usedmap.disk_used_ranges(dev) is None
        finally:
            dev.close()
        with DiskSession.open(path) as s:
            s.set_partition_name(2, "onarildi")
        fixed = _sf_json(path)
        assert _theirs(fixed) == _theirs(good), (what, fixed)
        assert fixed["parts"][2][3] == "onarildi"
        assert fixed["parts"][1][3] == "bir"
        _sf_verify(path)
        _sgdisk_verify(path)


# ---------------------------------------------------------------------------
# P6 / P7
# ---------------------------------------------------------------------------
def test_ilk_lba_34_korunur():
    """first-lba 34 ve LBA 34'te bolum: yazimdan sonra ikisi de yerinde."""
    path = _fresh("first34.img", 64 * MIB)
    _make(path, "label: gpt\nfirst-lba: 34\n"
                "1: start=34,size=2014\n"
                "2: start=2048,size=20480\n")
    info = _sf_json(path)
    assert info["first"] == 34 and info["parts"][1][0] == 34, info
    with DiskSession.open(path) as s:
        assert s.table.first_usable_lba() == 34
        s.set_partition_name(1, "bios")
        _same(s, path)
    info = _sf_json(path)
    assert info["first"] == 34, info
    assert info["parts"][1][:2] == (34, 2014)
    _sf_verify(path)
    _sgdisk_verify(path)


def test_yedek_gpt_son_bolume_degmez():
    """Son bolum yedek GPT alanina tasacaksa yazma ve kucultme reddedilir."""
    path = _fresh("backup_overlap.img", 64 * MIB)
    # son kullanilabilir LBA = N-34 (512 B); bolum tam oraya kadar
    _make(path, "label: gpt\nstart=2048,size=%d\n" % (64 * MIB // 512 - 34 - 2048 + 1))
    before = _sha(path)
    dev = DiskImage(path)
    try:
        table = GPTTable.read(dev)
        part = table.partitions[0]
        assert part.end_lba == table.backup_entries_lba - 1
        part.sector_count += 1                     # yedek giris dizisine tasir
        try:
            table.write()
            raise AssertionError("yedek GPT ile cakisan bolum yazildi")
        except PartitionTableError:
            pass
    finally:
        dev.close()
    assert _sha(path) == before, "reddedilen yazim diske dokundu"
    with DiskSession.open(path) as s:
        try:
            s.resize_image(63 * MIB)
            raise AssertionError("bolumu kesen kucultme yapildi")
        except SessionError:
            pass
    assert os.path.getsize(path) == 64 * MIB
    assert _sha(path) == before


# ---------------------------------------------------------------------------
# P5: hibrit MBR
# ---------------------------------------------------------------------------
def test_hibrit_mbr():
    """Hibrit MBR: ilgisiz yazimda korunur, hibrit bolumu bozan yazim reddedilir."""
    path = _fresh("hybrid.img", 64 * MIB)
    _make(path, "label: gpt\n"
                "1: start=2048,size=20480\n"
                "2: start=22528,size=20480\n")
    with open(path, "r+b") as fh:
        sector = bytearray(fh.read(512))
        ee = bytes([0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF]) + struct.pack("<II", 1, 2047)
        fat = bytes([0x80, 0, 0, 0, 0x0C, 0xFF, 0xFF, 0xFF]) + struct.pack("<II", 2048, 20480)
        sector[446:462] = ee
        sector[462:478] = fat
        fh.seek(0)
        fh.write(sector)
    info = _sf_json(path)
    with DiskSession.open(path) as s:
        assert s.scheme == "gpt"
        assert s.table.pmbr == "hybrid"
        assert not s.table.ambiguity, s.table.ambiguity
        assert _ours(s.table) == _theirs(info)
        s.set_partition_name(2, "ilgisiz")
    with open(path, "rb") as fh:
        assert fh.read(512) == bytes(sector), "hibrit MBR silindi"
    before = _sha(path)
    with DiskSession.open(path) as s:
        try:
            s.delete_partition(1, wipe=False)
            raise AssertionError("hibrit bolum sessizce silindi")
        except PartitionTableError:
            pass
    assert _sha(path) == before, "reddedilen yazim diske dokundu"


# ---------------------------------------------------------------------------
# P1 / P8: 4Kn
# ---------------------------------------------------------------------------
def test_4kn_gpt_goruntusu_acilir():
    """sfdisk 4Kn GPT: 4096 sektorle acilir, yazim sonrasi sfdisk ile ayni."""
    path = _fresh("gpt4k.img", 64 * MIB)
    _make(path, "label: gpt\n"
                "1: start=256,size=2560\n"
                "3: start=2816,size=2560\n", ss=4096)
    with DiskSession.open(path) as s:
        assert s.image.sector_size == 4096, s.image.sector_size
        assert s.scheme == "gpt"
        _same(s, path, ss=4096)
        s.set_partition_name(3, "dort k")
        info = _same(s, path, ss=4096)
        assert info["parts"][3][3] == "dort k"
    _sf_verify(path, ss=4096)


def test_4kn_gpt_den_mbr():
    """4Kn GPT->MBR: MBR yazilir, son bolum (N-6'da biter) ve LBA 3 bolumu korunur."""
    path = _fresh("gpt4k_conv.img", 64 * MIB)
    n = 64 * MIB // 4096
    # sfdisk 4Kn varsayilani: son kullanilabilir LBA = N-6
    _make(path, "label: gpt\n"
                "1: start=256,size=2560\n"
                "2: start=4096,size=%d\n" % (n - 6 - 4096 + 1), ss=4096)
    info = _sf_json(path, ss=4096)
    last_end = info["parts"][2][0] + info["parts"][2][1] - 1
    assert last_end == n - 6, (last_end, n)
    with open(path, "r+b") as fh:
        fh.seek(last_end * 4096)
        fh.write(b"SONSEKTOR" * 10)
    with DiskSession.open(path) as s:
        ok, why = s.can_convert_to("mbr")
        assert ok, why
        s.convert_scheme("mbr")
        assert s.scheme == "mbr"
        _same(s, path, ss=4096)
    info = _sf_json(path, ss=4096)
    assert info["label"] == "dos", info
    assert _theirs(info) == {1: (256, 2560), 2: (4096, last_end - 4096 + 1)}, info
    with open(path, "rb") as fh:
        fh.seek(last_end * 4096)
        assert fh.read(9) == b"SONSEKTOR", "son bolumun kuyrugu silindi"
    _sf_verify(path, ss=4096)


def test_kisa_giris_dizisi_gpt_den_mbr():
    """table-length 4 GPT, bolum LBA 3'te: donusum bolume degmez."""
    path = _fresh("gpt_short.img", 32 * MIB)
    _make(path, "label: gpt\ntable-length: 4\nfirst-lba: 3\n"
                "1: start=3,size=2045\n"
                "2: start=2048,size=20480\n")
    info = _sf_json(path)
    if info["parts"].get(1, (0,))[0] != 3:
        raise Skip(f"sfdisk LBA 3 bolumu kurmadi: {info}")
    with open(path, "r+b") as fh:
        fh.seek(3 * 512)
        fh.write(b"LBA3VERI" * 8)
    with DiskSession.open(path) as s:
        _same(s, path)
        s.convert_scheme("mbr")
    with open(path, "rb") as fh:
        fh.seek(3 * 512)
        assert fh.read(8) == b"LBA3VERI", "LBA 3'teki bolum silindi"
    info = _sf_json(path)
    assert info["label"] == "dos"
    assert _theirs(info) == {1: (3, 2045), 2: (2048, 20480)}, info


def test_4kn_kendi_tablomuz():
    """DiskImage(sector_size=4096) uzerinde kendi GPT/MBR'miz: sfdisk okur."""
    path = _work("own4k.img")
    dev = DiskImage.create(path, 64 * MIB, overwrite=True, sector_size=4096)
    # bos goruntude sektor boyu tahmin edilemez: aygit 4096 ile verilir
    with DiskSession(dev) as s:
        s.create_table("gpt")
        s.create_partition(256, 2560, wipe=False)
        s.create_partition(4096, 2560, wipe=False)
        _same(s, path, ss=4096)
        _sf_verify(path, ss=4096)
        s.convert_scheme("mbr")
        _same(s, path, ss=4096)
        assert _sf_json(path, ss=4096)["label"] == "dos"
        _sf_verify(path, ss=4096)
        s.convert_scheme("gpt")
        _same(s, path, ss=4096)
        _sf_verify(path, ss=4096)


def test_mbr_den_gpt_numaralar():
    """MBR->GPT: birincil ve mantiksal numaralari (1, 5, 6) korunur."""
    path = _fresh("mbr2gpt.img", 64 * MIB)
    _make(path, "label: dos\n"
                "1: start=2048,size=20480,type=83\n"
                "2: start=22528,size=80000,type=5\n"
                "5: start=24576,size=10000,type=83\n"
                "6: start=36864,size=10000,type=7\n")
    expect = _theirs(_sf_json(path))
    with DiskSession.open(path) as s:
        s.convert_scheme("gpt")
        _same(s, path)
    info = _sf_json(path)
    assert info["label"] == "gpt"
    assert _theirs(info) == expect, (info, expect)
    _sf_verify(path)
    _sgdisk_verify(path)


def test_kendi_tablolarimiz_512():
    """Kendi olusturdugumuz MBR/GPT (512 B) sfdisk -V'den gecer, numaralar ayni."""
    path = _work("own512.img")
    DiskImage.create(path, 64 * MIB, overwrite=True).close()
    with DiskSession.open(path) as s:
        s.create_table("gpt")
        s.create_partition(2048, 20480, wipe=False)
        s.create_partition(22528, 20480, wipe=False)
        s.create_partition(43008, 20480, wipe=False)
        s.delete_partition(2, wipe=False)
        _same(s, path)
        _sf_verify(path)
        s.create_table("mbr")
        assert not s.table.ambiguity, s.table.ambiguity
        s.create_partition(2048, 20480, wipe=False)
        _same(s, path)
        _sf_verify(path)
        assert _sf_json(path)["label"] == "dos"


def test_sgdisk_dogrulama():
    """sgdisk -v ile GPT yapisi (arac yoksa ATLANDI)."""
    _tool("sgdisk")
    path = _fresh("sgdisk.img", 64 * MIB)
    _make(path, "label: gpt\n1: start=2048,size=20480\n3: start=22528,size=20480\n")
    with DiskSession.open(path) as s:
        s.set_partition_name(3, "x")
    _sgdisk_verify(path)


TESTS = [
    test_gpt_yuvalari_korunur,
    test_mbr_yuvalari_korunur,
    test_mbr_mantiksal_numaralar,
    test_ebr_girisi_her_yuvada,
    test_bayat_gpt_dos_mbr,
    test_temiz_tabloda_usedmap_calisir,
    test_bozuk_birincil_gpt,
    test_ilk_lba_34_korunur,
    test_yedek_gpt_son_bolume_degmez,
    test_hibrit_mbr,
    test_4kn_gpt_goruntusu_acilir,
    test_4kn_gpt_den_mbr,
    test_kisa_giris_dizisi_gpt_den_mbr,
    test_4kn_kendi_tablomuz,
    test_mbr_den_gpt_numaralar,
    test_kendi_tablolarimiz_512,
    test_sgdisk_dogrulama,
]


def main() -> int:
    failed = skipped = 0
    for fn in TESTS:
        try:
            fn()
            print(f"TAMAM      {fn.__name__}")
        except Skip as exc:
            skipped += 1
            print(f"ATLANDI    {fn.__name__}: {exc}")
        except Exception:                               # noqa: BLE001
            failed += 1
            print(f"BASARISIZ  {fn.__name__}")
            traceback.print_exc()
    if os.environ.get("DISKULTIMATE_KEEP_TEST_FILES") != "1":
        shutil.rmtree(scratch("regress_ptable"), ignore_errors=True)
    print(f"\n{len(TESTS) - failed - skipped} tamam, {skipped} atlandi, "
          f"{failed} basarisiz")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
