"""DiskUltimate cekirdek test paketi (harici test kutuphanesi gerektirmez).

Calistirma:  python3 -m tests.run_all
Sonuclar .claude/logs/test-<tarih>.md dosyasina da yazilabilir (--log).
"""
from __future__ import annotations

import datetime
import hashlib
import os
import shutil
import copy
import struct
import subprocess
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.clone import backup, clone_to_new_image, restore  # noqa: E402
from diskultimate.core.convert import (alignment_report, gpt_to_mbr,  # noqa: E402
                                       mbr_to_gpt)
from diskultimate.core.exfat import (ExFatFS, UPCASE_STANDARD_CHECKSUM,  # noqa: E402
                                     standard_upcase_table, table_checksum)
from diskultimate.core.ext import format_ext  # noqa: E402
from diskultimate.core.extread import ExtFS  # noqa: E402
from diskultimate.core.extwrite import ExtWriter  # noqa: E402
from diskultimate.core.filesystem import (ExtAccess,  # noqa: E402
                                          NtfsAccess, open_filesystem)
from diskultimate.core.ntfsread import NtfsError, NtfsFS  # noqa: E402
from diskultimate.core.fat import FatFS  # noqa: E402
from diskultimate.core.ntfs import (attrdef_table, format_ntfs,  # noqa: E402
                                    upcase_table)
from diskultimate.core.platform import PLATFORM_NAME, actual_size  # noqa: E402
from diskultimate.core.recovery import (carve_files, extract_carved,  # noqa: E402
                                        recover_deleted, scan_deleted,
                                        scan_lost_partitions)
from diskultimate.core.vdisk import VhdImage, detect_format, open_disk  # noqa: E402
from diskultimate.core.wipe import wipe_device, wipe_free_space  # noqa: E402
from diskultimate.core.formatter import available_kinds, format_partition  # noqa: E402
from diskultimate.core.fsdetect import detect  # noqa: E402
from diskultimate.core.gpt import GPTTable  # noqa: E402
from diskultimate.core.image import DiskImage, PartitionView  # noqa: E402
from diskultimate.core.mbr import MBRTable  # noqa: E402
from diskultimate.core.ptable import human_size, parse_size  # noqa: E402
from diskultimate.core.clone import read_backup_info  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402
from diskultimate.core import bootloader  # noqa: E402
from diskultimate.core import efiboot  # noqa: E402
from diskultimate.core import efistore  # noqa: E402
from diskultimate.core import operations as ops  # noqa: E402
from diskultimate.core import platform as platform_mod  # noqa: E402

from diskultimate.paths import scratch  # noqa: E402

MIB = 1024 * 1024
TMP = scratch("tests")   # <proje>/.tmp/tests
KEEP = os.environ.get("DISKULTIMATE_KEEP_TEST_FILES") == "1"

# Testler arasinda uretilen goruntuler silinir; boylece pik disk kullanimi
# toplamin degil, en buyuk tek testin boyutu kadar olur. Inceleme icin:
#   DISKULTIMATE_KEEP_TEST_FILES=1


def _free_space(path: str) -> int:
    try:
        st = shutil.disk_usage(path)
        return st.free
    except OSError:
        return 0


def _sparse_supported(path: str) -> bool:
    """Hedef dosya sisteminin seyrek dosya destekleyip desteklemedigini olcer."""
    deneme = os.path.join(path, "_sparse_deneme.tmp")
    try:
        # Olcum, uretimde kullanilan yolun aynisi olmali: DiskImage.create de
        # make_sparse + truncate_sparse cagirir.
        with open(deneme, "wb") as fh:
            from diskultimate.core.platform import make_sparse, truncate_sparse
            make_sparse(fh.fileno())
            truncate_sparse(fh.fileno(), 64 * MIB)
        gercek = actual_size(deneme)
        return gercek < 8 * MIB
    except OSError:
        return False
    finally:
        try:
            os.unlink(deneme)
        except OSError:
            pass


def check_environment() -> bool:
    """Test ortamini denetler. Guvensizse False doner ve nedenini yazar.

    Seyrek dosya desteklenmeyen bir konumda (orn. VirtualBox paylasilan klasoru)
    testler gercekten birkac GB yazar; disk dolarsa makine kilitlenebilir.
    """
    bos = _free_space(TMP)
    seyrek = _sparse_supported(TMP)
    gereken = (2 * 1024 * MIB) if seyrek else (12 * 1024 * MIB)
    print(f"Test alani : {TMP}")
    print(f"Bos alan   : {bos // MIB} MB   (gereken en az {gereken // MIB} MB)")
    print(f"Seyrek dosya: {'destekleniyor' if seyrek else 'DESTEKLENMIYOR'}")
    if not seyrek:
        print("  ! Bu konumda goruntuler tum boyutlariyla diske yazilir.")
        print("  ! VirtualBox paylasilan klasorunde (vboxsf) veya FAT32'de testleri")
        print("  ! CALISTIRMAYIN. Yerel bir NTFS/ext4 dizin secin:")
        print("  !   DISKULTIMATE_SCRATCH=C:\\du-test python -m tests.run_all")
    if bos < gereken:
        print(f"\nDURDURULDU: yetersiz disk alani. "
              f"{gereken // MIB} MB bos alan gerekiyor.")
        return False
    print()
    return True


def cleanup_test_files(prefix: str) -> None:
    """Bir testin urettigi goruntuleri siler (pik disk kullanimini dusurur)."""
    if KEEP:
        return
    for ad in os.listdir(TMP):
        if ad.startswith(prefix):
            hedef = os.path.join(TMP, ad)
            try:
                if os.path.isdir(hedef):
                    shutil.rmtree(hedef, ignore_errors=True)
                else:
                    os.unlink(hedef)
            except OSError:
                pass
RESULTS = []


def test(fn):
    RESULTS.append(fn)
    return fn


def img_path(name: str) -> str:
    return os.path.join(TMP, name)


# --------------------------------------------------------------------------
@test
def t01_image_temel():
    """Goruntu olusturma, okuma/yazma, yeniden boyutlandirma"""
    p = img_path("t01.img")
    d = DiskImage.create(p, 16 * MIB, overwrite=True)
    assert d.sector_count == 16 * MIB // 512
    d.write_sectors(10, b"X" * 512)
    assert d.read_sectors(10)[:4] == b"XXXX"
    assert d.read_sectors(11)[:4] == b"\x00" * 4
    d.resize(32 * MIB)
    assert d.size == 32 * MIB
    v = PartitionView(d, 2048, 1024)
    v.write_sectors(0, b"Y" * 512)
    assert d.read_sectors(2048)[:1] == b"Y"
    try:
        v.write_sectors(1024, b"Z" * 512)
        raise AssertionError("sinir disi yazma engellenmedi")
    except Exception:
        pass
    d.close()
    assert parse_size("1.5 GB") == 1610612736
    assert human_size(1536 * MIB) == "1.50 GB"


@test
def t02_mbr():
    """MBR olusturma, birincil + genisletilmis + mantiksal bolumler"""
    p = img_path("t02.img")
    d = DiskImage.create(p, 512 * MIB, overwrite=True)
    t = MBRTable.create(d)
    t.add_partition(2048, 100 * MIB // 512, type_id=0x0C, bootable=True)
    t.add_partition(2048 + 100 * MIB // 512, 100 * MIB // 512, type_id=0x83)
    ext = 2048 + 200 * MIB // 512
    t.create_extended(ext, 300 * MIB // 512 - 2048)
    t.add_partition(ext + 2048, 80 * MIB // 512, type_id=0x06)
    assert len(t.partitions) == 4
    again = MBRTable.read(d)
    assert [p.start_lba for p in again.sorted_partitions()][:3] == [2048, 206848, 411648]
    logical = [p for p in again.partitions if p.logical]
    assert len(logical) == 1 and logical[0].index == 5
    assert again.partitions[0].bootable
    again.delete_partition(2)
    assert len(MBRTable.read(d).partitions) == 3
    d.close()


@test
def t03_gpt():
    """GPT olusturma, CRC dogrulama, yedek baslik"""
    p = img_path("t03.img")
    d = DiskImage.create(p, 1024 * MIB, overwrite=True)
    g = GPTTable.create(d)
    g.add_partition(2048, 100 * MIB // 512,
                    type_guid="C12A7328-F81F-11D2-BA4B-00A0C93EC93B", name="EFI")
    g.add_partition(2048 + 100 * MIB // 512, 400 * MIB // 512, name="Veri Bolumu")
    again = GPTTable.read(d)
    assert again.header_crc_ok and again.entries_crc_ok, "GPT CRC hatasi"
    assert [x.name for x in again.sorted_partitions()] == ["EFI", "Veri Bolumu"]
    assert d.read_sectors(0)[450] == 0xEE, "koruyucu MBR yok"
    # yedek baslik disk sonunda olmali
    assert d.read_sectors(d.sector_count - 1)[:8] == b"EFI PART"
    again.delete_partition(1)
    assert len(GPTTable.read(d).partitions) == 1
    d.close()


@test
def t04_fat_bicimlendirme():
    """FAT12/16/32 bicimlendirme + dosya/klasor islemleri + LFN"""
    for ft, size in ((32, 1024), (16, 200), (12, 8)):
        p = img_path(f"t04_fat{ft}.img")
        d = DiskImage.create(p, size * MIB, overwrite=True)
        view = PartitionView(d, 2048, d.sector_count - 2048)
        fs = FatFS.format(view, fat_type=ft, label=f"BIRIM{ft}")
        assert fs.fat_type == ft
        assert fs.label == f"BIRIM{ft}"
        fs.mkdir("/belgeler")
        fs.mkdir("/belgeler/alt klasor")
        icerik = b"merhaba dunya\n" * 100
        fs.write_file("/belgeler/deneme.txt", icerik)
        buyuk = bytes(range(256)) * 2048    # 512 KiB
        uzun_ad = "/belgeler/alt klasor/Cok Uzun Turkce Dosya Adi.bin"
        fs.write_file(uzun_ad, buyuk)
        fs.write_file("/kok.txt", b"kok")
        assert fs.read_file("/belgeler/deneme.txt") == icerik
        assert fs.read_file(uzun_ad) == buyuk
        # yeniden baglama sonrasi veri korunmali
        fs2 = FatFS(PartitionView(d, 2048, d.sector_count - 2048))
        assert fs2.read_file(uzun_ad) == buyuk
        assert {e.name for e in fs2.listdir("/")} == {"belgeler", "kok.txt"}
        assert {e.name for e in fs2.listdir("/belgeler")} == {"alt klasor", "deneme.txt"}
        st = fs2.stats()
        assert st["used_bytes"] > len(buyuk)
        assert st["free_bytes"] > 0
        # disa aktarma
        out = os.path.join(TMP, f"cikti{ft}")
        os.makedirs(out, exist_ok=True)
        dest = fs2.extract(uzun_ad, out)
        assert open(dest, "rb").read() == buyuk
        # ice aktarma
        fs2.import_file(dest, "/")
        assert fs2.exists("/Cok Uzun Turkce Dosya Adi.bin")
        # yeniden adlandirma ve silme
        fs2.rename("/kok.txt", "yeni ad.txt")
        assert fs2.exists("/yeni ad.txt") and not fs2.exists("/kok.txt")
        fs2.remove("/belgeler", recursive=True)
        assert not fs2.exists("/belgeler")
        info = detect(PartitionView(d, 2048, d.sector_count - 2048))
        assert info.fs_type == f"FAT{ft}", info
        assert info.label == f"BIRIM{ft}"
        d.close()
        _fsck(p, 2048)


def _fsck(image_path: str, skip_sectors: int) -> None:
    """fsck.vfat varsa bolumu ayiklayip dogrular (yalnizca bulunan sistemlerde)."""
    arac = shutil.which("fsck.vfat") or shutil.which("fsck.fat")
    if not arac:
        return
    part = image_path + ".part"
    with open(image_path, "rb") as kaynak, open(part, "wb") as hedef:
        kaynak.seek(skip_sectors * 512)
        while True:
            blok = kaynak.read(4 * 1024 * 1024)
            if not blok:
                break
            hedef.write(blok)
    r = subprocess.run([arac, "-n", part], capture_output=True, text=True)
    os.unlink(part)
    assert r.returncode == 0, f"fsck.vfat hata: {r.stdout}{r.stderr}"


def _arac_eski_mi(r, arac: str, secenek: str) -> None:
    """Harici arac bir secenegi tanimiyorsa testi ATLA (basarisiz sayma).

    Ornek: Ubuntu 24.04'teki mkfs.xfs 6.6 `-p <klasor>`u bilmez (klasoru
    eski "protofile" sanip "Is a directory" der), mkfs.btrfs 6.6
    `--subvol`u bilmez. Gelistirme makinesinde araclar yenidir (CI'da
    olculdu, ADR 0079). Gomulu orneklerle yapilan denetimler bundan once
    kosmus olur; yalnizca aracla varyant uretimi atlanir.
    """
    if r.returncode == 0:
        return
    hata = (r.stderr or "") + (r.stdout or "")
    if any(m in hata for m in ("Is a directory", "unrecognized option",
                               "invalid option", "unknown option")):
        raise Atlandi(f"{arac} '{secenek}' secenegini desteklemiyor (eski "
                      f"surum); gomulu ornekler denetlendi, varyant uretimi "
                      f"atlandi")


class Atlandi(Exception):
    """Test bu ortamda calistirilamadi (eksik harici arac vb.).

    Basarisizliktan ayrilir: kosum kirmizi olmaz ama ozette **gorunur**,
    boylece "18/18" cikitisi atlanan testi gizlemez.
    """


@test
def t05_yeniden_bicimlendirme():
    """Yeniden bicimlendirme eski imzayi birakmamali (tespit dogru kalmali)

    Her dosya sistemi kendi alanini yazar ama otekinin imzasini silmez: exFAT
    onyukleme sektoru ofset 0'dadir, ext ilk 1024 bayti rezerve birakip ona
    dokunmaz. Imzalar temizlenmezse exFAT'ten ext4'e cevrilen bir bolum exFAT
    sanilir. Gercekte yasandi: fiziksel diskte ext4 bolum acilirken exFAT
    surucusu cagrildi ve cokti. `formatter.wipe_signatures` bunu onler.
    """
    p = img_path("t05.img")
    d = DiskImage.create(p, 200 * MIB, overwrite=True)
    view = lambda: PartitionView(d, 2048, 180 * MIB // 512)   # noqa: E731
    beklenen = {"fat32": "FAT32", "fat16": "FAT16", "exfat": "exFAT",
                "ntfs": "NTFS", "ext4": "ext4", "ext2": "ext2"}
    onceki = None
    for key in ("fat32", "exfat", "ntfs", "ext4", "fat16", "ext2"):
        format_partition(view(), key, label="DENEME")
        bulunan = detect(view()).fs_type
        assert bulunan == beklenen[key], (
            f"{onceki} -> {key} sonrasi tespit {bulunan!r}, "
            f"{beklenen[key]!r} bekleniyordu (eski imza kalmis olabilir)")
        onceki = key
    d.close()
    if _sparse_supported(TMP):      # dosya sistemi destekliyorsa korunmali
        assert actual_size(p) < os.path.getsize(p), "seyreklik korunmadi"


@test
def t06_oturum_uctan_uca():
    """DiskSession: goruntu -> GPT -> bolum -> bicimlendirme -> dosya"""
    p = img_path("t06.img")
    s = DiskSession.create(p, 1024 * MIB, scheme="gpt", overwrite=True)
    assert s.scheme == "gpt"
    bos = s.free_regions()[0]
    part = s.create_partition(bos.start_lba, 300 * MIB // 512,
                              fs_key="fat32", label="VERI", name="Veri Bolumu")
    assert part.fs_type == "FAT32", part.fs_type
    fs = s.filesystem(part.index)
    assert fs is not None and fs.writable
    fs.mkdir("/proje")
    fs.import_file(__file__, "/proje")
    dosyalar = fs.listdir("/proje")
    assert len(dosyalar) == 1 and dosyalar[0].size > 0
    # ikinci bolum
    bos2 = s.free_regions()[0]
    p2 = s.create_partition(bos2.start_lba, 200 * MIB // 512, fs_key="fat16",
                            label="IKINCI")
    assert p2.fs_type == "FAT16"
    assert len(s.partitions) == 2
    # yeniden acma
    s.close()
    s2 = DiskSession.open(p)
    assert s2.scheme == "gpt" and len(s2.partitions) == 2
    fs2 = s2.filesystem(1)
    assert [f.name for f in fs2.listdir("/")] == ["proje"]
    # bicimlendirme degistirme ve silme
    s2.format_partition(2, "fat32", label="YENI")
    assert s2.table.get(2).fs_type == "FAT32"
    s2.delete_partition(2)
    assert len(s2.partitions) == 1
    s2.close()


@test
def t07_mbr_oturum():
    """DiskSession MBR akisi ve 4 birincil bolum siniri"""
    p = img_path("t07.img")
    s = DiskSession.create(p, 512 * MIB, scheme="mbr", overwrite=True)
    for i in range(4):
        bos = s.free_regions()[0]
        s.create_partition(bos.start_lba, 100 * MIB // 512,
                           fs_key="fat16" if i == 0 else "", label=f"B{i}")
    assert len(s.partitions) == 4
    bos = s.free_regions()
    if bos:
        try:
            s.create_partition(bos[0].start_lba, 10 * MIB // 512)
            raise AssertionError("5. birincil bolum engellenmedi")
        except Exception as exc:
            assert "birincil" in str(exc).lower(), exc
    s.set_bootable(1, True)
    assert s.table.get(1).bootable
    s.close()


@test
def t08_hata_geri_alma():
    """Bicimlendirme basarisiz olursa bolum tabloya eklenmemeli"""
    p = img_path("t08.img")
    s = DiskSession.create(p, 256 * MIB, scheme="gpt", overwrite=True)
    bos = s.free_regions()[0]
    try:
        s.create_partition(bos.start_lba, 4 * MIB // 512, fs_key="fat32")
        raise AssertionError("cok kucuk FAT32 bolumu engellenmedi")
    except Exception as exc:
        from diskultimate.core.formatter import FS_BY_KEY
        assert human_size(FS_BY_KEY["fat32"].min_bytes) in str(exc), exc
    assert len(s.partitions) == 0, "basarisiz islemden hayalet bolum kaldi"
    # ayni alanda gecerli bir bolum olusturulabilmeli
    part = s.create_partition(bos.start_lba, 64 * MIB // 512, fs_key="fat32",
                              label="TAMAM")
    assert part.fs_type == "FAT32" and len(s.partitions) == 1
    s.close()




@test
def t09_exfat_saf_python():
    """exFAT bicimlendirme, dosya islemleri ve standart upcase tablosu"""
    assert table_checksum(standard_upcase_table()) == UPCASE_STANDARD_CHECKSUM, \
        "Gomulu upcase tablosunun saglamasi bozuk"
    p = img_path("t09.img")
    d = DiskImage.create(p, 256 * MIB, overwrite=True)
    view = PartitionView(d, 2048, d.sector_count - 2048)
    fs = ExFatFS.format(view, label="EXTEST")
    assert fs.label == "EXTEST"
    fs.mkdir("/klasor")
    fs.mkdir("/klasor/alt")
    buyuk = bytes(range(256)) * 8192          # 2 MiB
    fs.write_file("/klasor/Uzun Turkce Dosya Adi.bin", buyuk)
    fs.write_file("/klasor/alt/kucuk.txt", b"merhaba")
    assert fs.read_file("/klasor/Uzun Turkce Dosya Adi.bin") == buyuk
    fs2 = ExFatFS(PartitionView(d, 2048, d.sector_count - 2048))
    assert {e.name for e in fs2.listdir("/klasor")} == {"alt", "Uzun Turkce Dosya Adi.bin"}
    assert fs2.read_file("/klasor/alt/kucuk.txt") == b"merhaba"
    st = fs2.stats()
    assert st["used_bytes"] > len(buyuk) and st["free_bytes"] > 0
    out = os.path.join(TMP, "exfat_cikti")
    hedef = fs2.extract("/klasor/Uzun Turkce Dosya Adi.bin", out)
    assert open(hedef, "rb").read() == buyuk
    fs2.rename("/klasor/alt/kucuk.txt", "yeni ad.txt")
    assert fs2.exists("/klasor/alt/yeni ad.txt")
    fs2.remove("/klasor", recursive=True)
    assert not fs2.exists("/klasor")
    info = detect(PartitionView(d, 2048, d.sector_count - 2048))
    assert info.fs_type == "exFAT" and info.label == "EXTEST", info
    d.close()
    _fsck_exfat(p, 2048)


def _fsck_exfat(image_path: str, skip_sectors: int) -> None:
    """fsck.exfat varsa bolumu ayiklayip dogrular."""
    arac = shutil.which("fsck.exfat")
    if not arac:
        return
    part = image_path + ".expart"
    with open(image_path, "rb") as kaynak, open(part, "wb") as hedef:
        kaynak.seek(skip_sectors * 512)
        while True:
            blok = kaynak.read(4 * 1024 * 1024)
            if not blok:
                break
            hedef.write(blok)
    r = subprocess.run([arac, "-n", part], capture_output=True, text=True)
    os.unlink(part)
    assert r.returncode == 0, f"fsck.exfat hata: {r.stdout}{r.stderr}"


@test
def t10_sema_donusumu():
    """MBR <-> GPT donusumu bolum verisini korumali"""
    p = img_path("t10.img")
    s = DiskSession.create(p, 400 * MIB, scheme="mbr", overwrite=True)
    r = s.free_regions()[0]
    s.create_partition(r.start_lba, 120 * MIB // 512, fs_key="fat32", label="VERI")
    r = s.free_regions()[0]
    s.create_partition(r.start_lba, 120 * MIB // 512, fs_key="exfat", label="EX")
    fs = s.filesystem(1)
    icerik = b"donusumden sonra da burada olmali\n" * 200
    fs.write_file("/kanit.txt", icerik)

    uygun, neden = s.can_convert_to("gpt")
    assert uygun, neden
    s.convert_scheme("gpt")
    assert s.scheme == "gpt"
    assert [p.fs_type for p in s.partitions] == ["FAT32", "exFAT"]
    assert s.filesystem(1).read("/kanit.txt") == icerik

    uygun, neden = s.can_convert_to("mbr")
    assert uygun, neden
    s.convert_scheme("mbr")
    assert s.scheme == "mbr"
    assert s.table.get(1).type_id == 0x0C and s.table.get(2).type_id == 0x07
    assert s.filesystem(1).read("/kanit.txt") == icerik
    rapor = s.alignment_report()
    assert all(r["aligned_4k"] and r["aligned_1m"] for r in rapor), rapor
    s.close()


@test
def t11_yedekleme_ve_klonlama():
    """Bolum yedegi, geri yukleme ve disk klonu"""
    p = img_path("t11.img")
    s = DiskSession.create(p, 500 * MIB, scheme="gpt", overwrite=True)
    r = s.free_regions()[0]
    part = s.create_partition(r.start_lba, 300 * MIB // 512, fs_key="fat32",
                              label="YEDEK")
    fs = s.filesystem(part.index)
    fs.mkdir("/veri")
    icerik = bytes(range(256)) * 8000
    fs.write_file("/veri/buyuk.bin", icerik)

    yedek = os.path.join(TMP, "t11.dub")
    bilgi = s.backup_partition(part.index, yedek)
    assert bilgi.total_bytes == 300 * MIB
    assert bilgi.file_size < bilgi.total_bytes // 4, "yedek beklenenden buyuk"
    assert DiskSession.backup_info(yedek).fs_type == "FAT32"

    # bolumu boz, geri yukle
    s.view(s.table.get(part.index)).write(0, b"\xFF" * 8192)
    s.restore_partition(part.index, yedek)
    assert s.filesystem(part.index).read("/veri/buyuk.bin") == icerik

    # disk klonu
    klon = s.clone_to(img_path("t11_klon.img"))
    s2 = DiskSession.open(klon)
    assert s2.scheme == "gpt" and len(s2.partitions) == 1
    assert s2.filesystem(1).read("/veri/buyuk.bin") == icerik
    if _sparse_supported(TMP):
        assert actual_size(klon) < os.path.getsize(klon), "klon seyrekligi korumadi"
    s2.close()

    # --- yedek dosyasi ham goruntu SANILMAMALI ---
    # .dub basliginin 510. baytinda 0xAA55 durur; ham acilirsa bolum tablosu
    # cozumleyicisi bunu "gecerli ama bos MBR" sanip kullaniciya bos disk
    # gosteriyordu. Imza denetimi bu yolu kapatir.
    assert DiskSession.is_backup_file(yedek), ".dub yedegi taninmadi"
    assert not DiskSession.is_backup_file(klon), "ham goruntu yedek sanildi"

    # --- DISK yedegi yeni bir goruntuye acilabilmeli, bolumler geri gelmeli ---
    # Kullanicinin yasadigi durum budur: tum disk yedeklenir, sonra yedek
    # "acilmak" istenir. Geri yukleme olmadan icerik gorunmez.
    disk_yedek = os.path.join(TMP, "t11_disk.dub")
    s.backup_disk(disk_yedek)
    assert DiskSession.is_backup_file(disk_yedek)
    acilan = DiskSession.restore_to_new_image(disk_yedek,
                                              img_path("t11_acilan.img"))
    s3 = DiskSession.open(acilan)
    assert s3.image.size == read_backup_info(disk_yedek).total_bytes
    assert s3.scheme == "gpt", f"bolum tablosu geri gelmedi: {s3.scheme}"
    assert len(s3.partitions) == 1, f"bolumler geri gelmedi: {len(s3.partitions)}"
    assert s3.filesystem(1).read("/veri/buyuk.bin") == icerik
    s3.close()

    # --- yedek GERI YUKLEMEDEN gezilebilmeli (DubImage) ---
    # Kullanici istegi: ".dub acildiginda disk ve bolumler gorulmeli, dosyalara
    # ulasilabilmeli." Yedek blok tablolidir, bu yuzden rastgele erisimle salt
    # okunur bir disk gibi sunulabilir.
    s4 = DiskSession.open(disk_yedek)
    assert s4.is_backup, "yedek oturumu yedek olarak isaretlenmedi"
    assert s4.readonly, "yedek yazilabilir acildi"
    assert s4.image.size == read_backup_info(disk_yedek).total_bytes
    assert s4.scheme == "gpt", f"yedekten bolum tablosu okunamadi: {s4.scheme}"
    assert len(s4.partitions) == 1
    assert s4.filesystem(1).read("/veri/buyuk.bin") == icerik,         "yedekten okunan dosya icerigi bozuk"
    try:
        s4.image.write(0, b"x" * 512)
        raise AssertionError("yedege yazma engellenmedi")
    except Exception:
        pass
    s4.close()
    s.close()


@test
def t12_guvenli_silme():
    """Silme yalnizca hedef alani etkilemeli; bos alan silme veriyi korumali"""
    p = img_path("t12.img")
    d = DiskImage.create(p, 32 * MIB, overwrite=True)
    d.write(0, b"DISARIDA KALMALI" * 64)
    view = PartitionView(d, 2048, 4096)
    view.write(0, b"SILINECEK" * 200)
    sonuc = wipe_device(view, "dod")
    assert sonuc["passes"] == 3
    assert wipe_device(view, "zero", verify=True)["verified"]
    assert d.read(0, 16) == b"DISARIDA KALMALI", "silme bolum disina tasti"
    d.close()

    s = DiskSession.create(img_path("t12b.img"), 200 * MIB, scheme="mbr",
                           overwrite=True)
    r = s.free_regions()[0]
    part = s.create_partition(r.start_lba, 150 * MIB // 512, fs_key="fat32",
                              label="TEMIZ")
    fs = s.filesystem(part.index)
    fs.write_file("/kalici.txt", b"BU KALMALI")
    fs.write_file("/gizli.txt", b"SIR-ICERIK-ANAHTARI " * 500)
    fs.remove("/gizli.txt")
    s.wipe_free_space(part.index)
    ham = s.view(s.table.get(part.index)).read(0, 150 * MIB)
    assert b"SIR-ICERIK-ANAHTARI" not in ham, "silinen dosyanin artigi kaldi"
    assert s.filesystem(part.index).read("/kalici.txt") == b"BU KALMALI"
    s.close()


@test
def t13_kurtarma():
    """Silinmis dosya, kayip bolum ve imza tabanli kurtarma"""
    p = img_path("t13.img")
    s = DiskSession.create(p, 300 * MIB, scheme="gpt", overwrite=True)
    r = s.free_regions()[0]
    part = s.create_partition(r.start_lba, 200 * MIB // 512, fs_key="fat32",
                              label="KURTAR")
    fs = s.filesystem(part.index)
    adlar = {"Onemli Rapor 2026.txt": b"RAPOR-" * 2000,
             "kisa.bin": bytes(range(256)) * 100}
    fs.mkdir("/belgeler")
    for ad, veri in adlar.items():
        fs.write_file("/belgeler/" + ad, veri)
    for ad in adlar:
        fs.fs.remove("/belgeler/" + ad)

    silinmis = s.scan_deleted(part.index)
    bulunan = {d.name for d in silinmis}
    assert bulunan == set(adlar), f"uzun ad kurtarilamadi: {bulunan}"
    hedef = os.path.join(TMP, "t13_kurtarilan")
    for d in silinmis:
        n = s.recover_deleted(part.index, d, os.path.join(hedef, d.name))
        assert n == d.size
        assert open(os.path.join(hedef, d.name), "rb").read() == adlar[d.name]

    # kayip bolum: tabloyu sil, veriyi birak
    s.table.delete_partition(part.index)
    s.reload()
    adaylar = s.scan_lost_partitions()
    assert any(a.start_lba == 2048 and a.fs_type == "FAT32" and a.label == "KURTAR"
               for a in adaylar), adaylar
    s.close()

    # imza tabanli kurtarma
    d = DiskImage.create(img_path("t13b.img"), 24 * MIB, overwrite=True)
    png = b"\x89PNG\r\n\x1a\n" + b"P" * 5000 + b"IEND\xaeB`\x82"
    pdf = b"%PDF-1.7\n" + b"D" * 3000 + b"%%EOF"
    d.write(1 * MIB, png)
    d.write(5 * MIB, pdf)
    bulunanlar = carve_files(d, keys=["png", "pdf"])
    assert len(bulunanlar) == 2, bulunanlar
    cikti = os.path.join(TMP, "t13_carve")
    yollar = [extract_carved(d, c, cikti) for c in bulunanlar]
    assert open(yollar[0], "rb").read() == png
    d.close()


def _vbox_forget(vbox: str, path: str) -> None:
    """VirtualBox medya kaydini birakir (kayitli degilse sessizce gecer)."""
    subprocess.run([vbox, "closemedium", "disk", os.path.abspath(path)],
                   capture_output=True, text=True)


@test
def t14_sanal_diskler():
    """VHD olusturma/yazma ve VHD/VDI/VMDK okuma"""
    vhd = img_path("t14.vhd")
    img = VhdImage.create_fixed(vhd, 128 * MIB, overwrite=True)
    img.close()
    assert detect_format(vhd) == "vhd"

    dev = open_disk(vhd, readonly=False)
    s = DiskSession(dev)
    s.create_table("gpt")
    r = s.free_regions()[0]
    part = s.create_partition(r.start_lba, 100 * MIB // 512, fs_key="fat32",
                              label="VHD")
    icerik = b"sanal disk icerigi\n" * 100
    s.filesystem(part.index).write_file("/kanit.txt", icerik)
    s.close()

    s2 = DiskSession.open(vhd, readonly=True)
    assert s2.image_format == "vhd"
    assert s2.scheme == "gpt" and len(s2.partitions) == 1
    assert s2.filesystem(1).read("/kanit.txt") == icerik
    s2.close()

    # VBoxManage varsa uretilen VHD'yi bagimsiz dogrula
    vbox = shutil.which("VBoxManage")
    if vbox:
        # Onceki kosumdan kalan kayit, ayni yoldaki yeni UUID ile catisir:
        # once medya kaydini birak, sonra dogrula, sonunda yine birak.
        _vbox_forget(vbox, vhd)
        r = subprocess.run([vbox, "showhdinfo", vhd], capture_output=True, text=True)
        _vbox_forget(vbox, vhd)
        assert r.returncode == 0, f"VBoxManage VHD'yi reddetti: {r.stderr[:200]}"

    # ham goruntuden VDI/VMDK uretip okuma (VBoxManage varsa)
    if not vbox:
        return
    ham = img_path("t14_ham.img")
    s3 = DiskSession.create(ham, 64 * MIB, scheme="mbr", overwrite=True)
    r = s3.free_regions()[0]
    pp = s3.create_partition(r.start_lba, 50 * MIB // 512, fs_key="fat32", label="X")
    s3.filesystem(pp.index).write_file("/kanit.txt", icerik)
    s3.close()
    for bicim, uzanti in (("VDI", "vdi"), ("VMDK", "vmdk")):
        hedef = img_path(f"t14_d.{uzanti}")
        _vbox_forget(vbox, hedef)
        if os.path.exists(hedef):
            os.unlink(hedef)
        r = subprocess.run([vbox, "convertfromraw", ham, hedef, "--format", bicim],
                           capture_output=True, text=True)
        if r.returncode != 0:
            continue
        s4 = DiskSession.open(hedef, readonly=True)
        assert s4.image_format == uzanti, s4.image_format
        assert s4.filesystem(1).read("/kanit.txt") == icerik, f"{bicim} okunamadi"
        s4.close()


@test
def t15_yazma_basarimi():
    """Buyuk dosya yazma makul hizda olmali (tahsis dongusu regresyon korumasi)"""
    import time
    p = img_path("t15.img")
    d = DiskImage.create(p, 400 * MIB, overwrite=True)
    view = PartitionView(d, 2048, d.sector_count - 2048)
    fs = FatFS.format(view, fat_type=32, label="HIZ")
    veri = b"x" * (32 * MIB)
    t0 = time.time()
    fs.write_file("/buyuk.bin", veri)
    sure = time.time() - t0
    assert sure < 10, f"FAT32 32 MB yazma cok yavas: {sure:.1f}s"
    t0 = time.time()
    assert fs.read_file("/buyuk.bin") == veri
    assert time.time() - t0 < 10, "okuma cok yavas"

    view2 = PartitionView(d, 2048, d.sector_count - 2048)
    d2 = DiskImage.create(img_path("t15b.img"), 400 * MIB, overwrite=True)
    exview = PartitionView(d2, 2048, d2.sector_count - 2048)
    exfs = ExFatFS.format(exview, label="HIZ")
    t0 = time.time()
    exfs.write_file("/buyuk.bin", veri)
    assert time.time() - t0 < 10, "exFAT yazma cok yavas"
    assert exfs.read_file("/buyuk.bin") == veri
    d.close()
    d2.close()


@test
def t16_ext_ailesi():
    """ext2/ext3/ext4 saf Python bicimlendirme (fsck ile dogrulanir)"""
    for surum, boyut in (("ext2", 64), ("ext3", 256), ("ext4", 256)):
        p = img_path(f"t16_{surum}.img")
        d = DiskImage.create(p, boyut * MIB, overwrite=True)
        view = PartitionView(d, 2048, d.sector_count - 2048)
        bilgi = format_ext(view, surum, label=f"DU{surum.upper()}")
        assert bilgi["version"] == surum
        if surum != "ext2":
            assert bilgi["journal_blocks"] > 0, "gunluk olusturulmadi"
        info = detect(PartitionView(d, 2048, d.sector_count - 2048))
        assert info.fs_type == surum, f"{surum} -> {info.fs_type}"
        assert info.label == f"DU{surum.upper()}", info.label

        # --- salt okunur okuyucu ayni birimi cozebilmeli ---
        # ext2/ext3 dolayli blok, ext4 extent agaci kullanir; ucu de burada
        # ayni kod yolundan gecer.
        view2 = PartitionView(d, 2048, d.sector_count - 2048)
        fs = ExtFS(view2)
        assert fs.label == f"DU{surum.upper()}", fs.label
        assert fs.block_size in (1024, 2048, 4096), fs.block_size
        kok = fs.read_inode(2)
        assert kok.is_dir, "kok inode dizin degil"
        adlar = {e.name for e in fs.read_dir(kok)}
        assert {".", "..", "lost+found"} <= adlar, adlar
        lf = fs.resolve("/lost+found")
        assert lf.is_dir, "lost+found dizin degil"
        erisim = open_filesystem(view2, info)
        assert isinstance(erisim, ExtAccess), type(erisim)
        assert erisim.readable, "ext okunabilir olmali"
        assert any(n.name == "lost+found" for n in erisim.listdir("/"))
        # ext4 mkfs.ext4 varsayilanlariyla (extent, flex_bg, metadata_csum;
        # 64bit/resize_inode haric) olusur — ADR 0073. Yazma desteklenmeli;
        # ayrintili dogrulama (her adimda e2fsck) tests/ext_write_check.py.
        ozellik = (fs.feature_incompat & 0x0240, bool(fs.ro_compat & 0x0400))
        beklenen = ((0x0240, True) if surum == "ext4" else (0, False))
        assert ozellik == beklenen, (surum, ozellik)
        assert erisim.writable, f"ext yazilabilir olmali: {erisim.write_reason}"
        erisim.write_file("/deneme.txt", b"ext yazma\n")
        assert erisim.read("/deneme.txt") == b"ext yazma\n"
        erisim.mkdir("/klasor")
        assert any(n.name == "klasor" and n.is_dir for n in erisim.listdir("/"))
        erisim.remove("/deneme.txt")
        erisim.remove("/klasor")
        erisim.flush()
        assert {n.name for n in erisim.listdir("/")} == {"lost+found"}

        d.close()
        _fsck_ext(p, 2048, surum)

    # eski ozellik seti (3.18 oncesi cekirdek) hala uretilebilmeli
    from diskultimate.core.ext import ExtFormatter
    p = img_path("t16_ext4_eski.img")
    d = DiskImage.create(p, 64 * MIB, overwrite=True)
    ExtFormatter(PartitionView(d, 2048, d.sector_count - 2048), "ext4",
                 modern=False).format()
    fs = ExtFS(PartitionView(d, 2048, d.sector_count - 2048))
    assert not fs.feature_incompat & 0x0240 and not fs.ro_compat & 0x0400
    d.close()
    _fsck_ext(p, 2048, "ext4")


def _fsck_ext(image_path: str, skip_sectors: int, surum: str) -> None:
    """fsck.ext* varsa bolumu ayiklayip dogrular."""
    arac = shutil.which(f"fsck.{surum}") or shutil.which("e2fsck")
    if not arac:
        return
    part = image_path + ".part"
    with open(image_path, "rb") as kaynak, open(part, "wb") as hedef:
        kaynak.seek(skip_sectors * 512)
        while True:
            blok = kaynak.read(4 * 1024 * 1024)
            if not blok:
                break
            hedef.write(blok)
    r = subprocess.run([arac, "-nf", part], capture_output=True, text=True)
    os.unlink(part)
    assert r.returncode == 0, f"fsck.{surum} hata ({r.returncode}): {r.stdout[-400:]}"


@test
def t17_ntfs():
    """NTFS saf Python bicimlendirme (ntfsfix/ntfsinfo ile dogrulanir)"""
    # gomulu tablolar referansla ayni olmali
    assert len(attrdef_table()) == 2560
    assert len(upcase_table()) == 128 * 1024

    p = img_path("t17.img")
    d = DiskImage.create(p, 96 * MIB, overwrite=True)
    view = PartitionView(d, 2048, d.sector_count - 2048)
    bilgi = format_ntfs(view, label="DUNTFS")
    assert bilgi["cluster_size"] in (4096, 2048, 1024)
    info = detect(PartitionView(d, 2048, d.sector_count - 2048))
    assert info.fs_type == "NTFS", info
    # Etiket ve doluluk onyukleme sektorunde degil, ustveri dosyalarindadir
    # ($Volume / $Bitmap). Tespit bunlari okumazsa harita cubugu ve tablodaki
    # "Kullanilan" sutunu NTFS'te bos kalir.
    assert info.label == "DUNTFS", info.label
    assert info.used_bytes > 0, "NTFS doluluk olculememis"
    assert info.used_bytes < info.total_bytes, (info.used_bytes, info.total_bytes)

    # --- okuyucu kendi urettigimiz birimi cozebilmeli ---
    view2 = PartitionView(d, 2048, d.sector_count - 2048)
    nfs = NtfsFS(view2)
    assert nfs.label == "DUNTFS", nfs.label
    assert nfs.cluster_size == bilgi["cluster_size"]
    kok = nfs.listdir("/")
    # Bicimlendirme sonrasi kok bos olmalidir (sistem dosyalari gizlenir)
    assert all(not e.name.startswith("$") for e in kok), [e.name for e in kok]
    erisim = open_filesystem(view2, info)
    assert isinstance(erisim, NtfsAccess), type(erisim)
    assert erisim.readable, "NTFS okunabilir olmali"
    assert erisim.stats()["cluster_size"] == bilgi["cluster_size"]

    # --- yazma: kendi urettigimiz birimde indeks $INDEX_ROOT icinde durur ---
    # Ayrintili dogrulama (her adimda ntfsfix + ntfs-3g baglama)
    # tests/ntfs_write_check.py icindedir.
    # Doluluk hesabi bagimsiz bir uygulamayla (boyutlandirmanin bitmap
    # taramasi) ayni sonucu vermeli; hizli olan tabloyla sayar, bu bit bit.
    from diskultimate.core.ntfsresize import _bitmap_usage
    dolu_kume, _ = _bitmap_usage(nfs, nfs.cluster_count)
    assert nfs.used_bytes() == dolu_kume * nfs.cluster_size, (
        nfs.used_bytes(), dolu_kume * nfs.cluster_size)

    assert erisim.writable, f"NTFS yazilabilir olmali: {erisim.write_reason}"
    # Yazilan veri dolulugu artirmali (bitmap gercekten okunuyor mu?)
    once = NtfsFS(PartitionView(d, 2048, d.sector_count - 2048)).used_bytes()
    erisim.write_file("/buyuk.bin", b"\xA5" * (2 * MIB))
    erisim.flush()
    sonra = NtfsFS(PartitionView(d, 2048, d.sector_count - 2048)).used_bytes()
    assert sonra - once >= 2 * MIB, (once, sonra)
    erisim.remove("/buyuk.bin")
    erisim.flush()

    erisim.write_file("/deneme.txt", b"NTFS yazma\n")
    assert erisim.read("/deneme.txt") == b"NTFS yazma\n"
    erisim.mkdir("/klasor")
    adlar = {n.name for n in erisim.listdir("/")}
    assert {"deneme.txt", "klasor"} <= adlar, adlar
    erisim.rename("/deneme.txt", "okundu.txt")
    assert "okundu.txt" in {n.name for n in erisim.listdir("/")}
    erisim.remove("/okundu.txt")
    erisim.remove("/klasor")
    erisim.flush()
    assert not erisim.listdir("/"), [n.name for n in erisim.listdir("/")]
    d.close()

    part = p + ".part"
    with open(p, "rb") as kaynak, open(part, "wb") as hedef:
        kaynak.seek(2048 * 512)
        while True:
            blok = kaynak.read(4 * 1024 * 1024)
            if not blok:
                break
            hedef.write(blok)
    try:
        for arac, bayraklar in (("ntfsinfo", ["-m"]), ("ntfsfix", ["-n"])):
            yol = shutil.which(arac)
            if not yol:
                continue
            r = subprocess.run([yol] + bayraklar + [part], capture_output=True,
                               text=True)
            assert r.returncode == 0, f"{arac} hata: {r.stdout}{r.stderr}"
    finally:
        os.unlink(part)


@test
def t18_bolum_boyutlandirma():
    """Bolum kucultme / buyutme / tasima (fsck ile dogrulanir)"""
    from diskultimate.core.resize import ResizeError

    def icerik_dogrula(oturum, etiket):
        oturum.close_filesystems()
        fs = oturum.filesystem(1)
        assert fs.read("/veri.bin") == b"A" * (5 * MIB), etiket
        assert fs.read("/klasor/not.txt") == b"merhaba" * 100, etiket
        oturum.close_filesystems()

    def bolumu_cikar(img_yol: str, hedef: str) -> None:
        with open(img_yol, "rb") as kaynak:
            mbr = kaynak.read(512)
            bas, adet = struct.unpack_from("<II", mbr, 446 + 8)
            kaynak.seek(bas * 512)
            with open(hedef, "wb") as cikti:
                kalan = adet * 512
                while kalan > 0:
                    blok = kaynak.read(min(4 * MIB, kalan))
                    if not blok:
                        break
                    cikti.write(blok)
                    kalan -= len(blok)

    # --- FAT32: kucult, buyut (FAT tablosu buyur), tasi -------------------
    yol = img_path("t18.img")
    s = DiskSession.create(yol, 600 * MIB, overwrite=True)
    s.create_table("mbr")
    s.create_partition(2048, 200 * MIB // 512, fs_key="fat32", label="RZ")
    fs = s.filesystem(1)
    fs.write_file("/veri.bin", b"A" * (5 * MIB))
    fs.mkdir("/klasor")
    fs.write_file("/klasor/not.txt", b"merhaba" * 100)
    fs.flush()
    s.close_filesystems()

    bilgi = s.resize_info(1)
    assert bilgi.kind == "fat" and bilgi.max_sectors > 200 * MIB // 512, bilgi

    # dosya sisteminin gerektirdigi asgarinin altina inilemez
    try:
        s.plan_resize(1, 2048, 8 * MIB // 512)
        raise AssertionError("asgari sinir denetlenmedi")
    except ResizeError:
        pass
    # kapsayici alanin disina cikilamaz
    try:
        s.plan_resize(1, 2048, 1000 * MIB // 512)
        raise AssertionError("disk siniri denetlenmedi")
    except ResizeError:
        pass
    # onay olmadan calismaz
    try:
        s.resize_partition(1, 2048, 80 * MIB // 512)
        raise AssertionError("onaysiz boyutlandirma yapildi")
    except Exception as exc:
        assert "onay" in str(exc).lower(), exc

    for hedef in (80, 450):
        s.resize_partition(1, 2048, hedef * MIB // 512, confirm=True)
        assert s.table.get(1).size == hedef * MIB
        icerik_dogrula(s, f"fat32 {hedef}MB")
    s.resize_partition(1, 2048 + 100 * MIB // 512, 450 * MIB // 512, confirm=True)
    assert s.table.get(1).start_lba == 2048 + 100 * MIB // 512
    icerik_dogrula(s, "fat32 tasima")
    s.close()

    arac = shutil.which("fsck.vfat")
    if arac:
        parca = yol + ".part"
        bolumu_cikar(yol, parca)
        try:
            r = subprocess.run([arac, "-n", parca], capture_output=True, text=True)
            assert r.returncode == 0, f"fsck.vfat: {r.stdout}{r.stderr}"
        finally:
            os.unlink(parca)

    # --- exFAT: kucult, buyut (FAT bolgesi + bitmap tasinir), kucult ------
    yol2 = img_path("t18x.img")
    s = DiskSession.create(yol2, 900 * MIB, overwrite=True)
    s.create_table("mbr")
    s.create_partition(2048, 200 * MIB // 512, fs_key="exfat", label="RZX")
    fs = s.filesystem(1)
    fs.write_file("/veri.bin", b"A" * (5 * MIB))
    fs.mkdir("/klasor")
    fs.write_file("/klasor/not.txt", b"merhaba" * 100)
    fs.flush()
    s.close_filesystems()
    for hedef in (100, 800, 300):
        s.resize_partition(1, 2048, hedef * MIB // 512, confirm=True)
        icerik_dogrula(s, f"exfat {hedef}MB")
    s.resize_partition(1, 2048 + 40 * MIB // 512, 300 * MIB // 512, confirm=True)
    icerik_dogrula(s, "exfat tasima")
    s.close()

    arac = shutil.which("fsck.exfat") or shutil.which("exfatfsck")
    if arac:
        parca = yol2 + ".part"
        bolumu_cikar(yol2, parca)
        try:
            r = subprocess.run([arac, "-n", parca], capture_output=True, text=True)
            assert r.returncode == 0, f"fsck.exfat: {r.stdout}{r.stderr}"
        finally:
            os.unlink(parca)

    # --- GPT: yeni yerlesim diske yazilip geri okunabilmeli ----------------
    yol3 = img_path("t18g.img")
    s = DiskSession.create(yol3, 500 * MIB, overwrite=True)
    s.create_table("gpt")
    s.create_partition(2048, 120 * MIB // 512, fs_key="fat32", label="GPTRZ")
    fs = s.filesystem(1)
    fs.write_file("/veri.bin", b"A" * (5 * MIB))
    fs.mkdir("/klasor")
    fs.write_file("/klasor/not.txt", b"merhaba" * 100)
    fs.flush()
    s.close_filesystems()
    kimlik = s.table.get(1).part_guid
    yeni_bas = 2048 + 60 * MIB // 512
    s.resize_partition(1, yeni_bas, 300 * MIB // 512, confirm=True)
    s.close()

    s = DiskSession.open(yol3)
    p1 = s.table.get(1)
    assert p1.start_lba == yeni_bas, p1.start_lba
    assert p1.size == 300 * MIB, p1.size
    assert p1.part_guid == kimlik, "GPT bolum GUID'i korunmadi"
    icerik_dogrula(s, "gpt tasima+buyutme")
    s.close()



# --------------------------------------------------------------------------
@test
def t19_klasor_kopyalama():
    """Klasor kopyalama: tek uygulama, ilerleme bildirimi, ayni yerlesim

    Daha once her dosya sistemi `import_tree`i ayri yazmisti ve ikisi hedefte
    **ust klasoru olusturmuyordu**: ayni dugme FAT'te `/hedef/klasor/a.txt`,
    ext ve NTFS'te `/hedef/a.txt` uretiyordu. Artik tek uygulama var.

    Ilerleme bildirimi de burada dogrulanir: buyuk bir kopyanin sessizce
    surmesi kullaniciya donma gibi gorunuyordu (ADR 0021).
    """
    kaynak = os.path.join(TMP, "t19_kaynak")
    shutil.rmtree(kaynak, ignore_errors=True)
    os.makedirs(os.path.join(kaynak, "alt", "daha_alt"))
    with open(os.path.join(kaynak, "kok.txt"), "wb") as fh:
        fh.write(b"A" * 1000)
    with open(os.path.join(kaynak, "alt", "orta.bin"), "wb") as fh:
        fh.write(b"B" * 5000)
    with open(os.path.join(kaynak, "alt", "daha_alt", "derin.dat"), "wb") as fh:
        fh.write(b"C" * 3000)
    toplam_bayt = 1000 + 5000 + 3000

    mevcut = {k.key for k in available_kinds()}
    denenen = []
    for fs_key in ("fat32", "exfat", "ntfs", "ext4"):
        if fs_key not in mevcut:
            continue
        yol = img_path(f"t19_{fs_key}.img")
        s = DiskSession.create(yol, 256 * MIB, scheme="gpt", overwrite=True)
        r = s.free_regions()[0]
        p = s.create_partition(r.start_lba, 200 * MIB // 512, fs_key=fs_key,
                               label="KOPYA")
        fs = s.filesystem(p.index)
        if fs is None or not fs.writable:
            s.close()
            continue

        adimlar = []
        sayi = fs.import_tree(kaynak, "/",
                              progress=lambda d, t, ad: adimlar.append((d, t, ad)))
        fs.flush()

        assert sayi == 3, f"{fs_key}: {sayi} dosya kopyalandi, 3 bekleniyordu"
        # Ust klasor olusmali: her dosya sisteminde ayni yerlesim
        adlar = {n.name for n in fs.listdir("/")}
        assert "t19_kaynak" in adlar, f"{fs_key}: ust klasor olusmadi — {adlar}"
        kok = {n.name for n in fs.listdir("/t19_kaynak")}
        assert {"kok.txt", "alt"} <= kok, f"{fs_key}: {kok}"
        derin = fs.read("/t19_kaynak/alt/daha_alt/derin.dat")
        assert derin == b"C" * 3000, f"{fs_key}: derin dosya icerigi bozuk"

        # Ilerleme: her dosyadan once bir kez + sonda kapanis
        assert len(adimlar) == 4, f"{fs_key}: {len(adimlar)} ilerleme bildirimi"
        assert adimlar[0][0] == 0, "ilk bildirim 0 bayttan baslamali"
        assert adimlar[-1][0] == toplam_bayt, "son bildirim toplami vermeli"
        assert all(t == toplam_bayt for _d, t, _a in adimlar), "toplam degisti"
        s.close()
        denenen.append(fs_key)

    assert len(denenen) >= 2, f"yalnizca {denenen} uzerinde denendi"
    shutil.rmtree(kaynak, ignore_errors=True)


# --------------------------------------------------------------------------
@test
def t20_acik_aygit_kutugu():
    """Acik tutulan aygit listelemede yeniden yoklanmaz (donma korumasi)

    Uygulama bir diski acikken ayni aygita ikinci bir tutamac acip IOCTL
    sormak, surucu yiginini dakikalarca bloklayabiliyor; o sirada asil is
    parcaciginin okumalari da ayni kuyruga takiliyor ve uygulama doniyor.
    Olculmus ornek: `.claude/logs/freeze/freeze-20260915-134507.md`.

    Gercek diske DOKUNULMAZ: aygit acma islevi sahte bir surumle degistirilir,
    hangi yollarin acilmaya calisildigi kaydedilir.
    """
    from diskultimate.core import physical

    assert not physical.open_device_paths(), "kutuk bos baslamali"
    sahte = physical.DiskInfo(path="/sahte/aygit0", name="SahteAygit0",
                              size=64 * MIB, mounted=["Z:"])
    physical._register_open(sahte)
    try:
        acik = physical.open_device_paths()
        assert sahte.path in acik, "acik aygit kutuge girmedi"
        assert physical.busy_drive_letters() == {"Z"}, \
            physical.busy_drive_letters()
    finally:
        physical._unregister_open(sahte.path)
    assert sahte.path not in physical.open_device_paths(), "kutuk temizlenmedi"

    if not physical.IS_WINDOWS:
        raise Atlandi("aygit yoklamasi yalnizca Windows dalinda")

    # Windows dali: acik aygit icin CreateFileW HIC cagrilmamali.
    acilan = []
    gercek_handle = physical._win_handle
    gercek_letters = physical._win_drive_letters
    gercek_system = physical._win_system_disk_numbers

    def sahte_handle(path, write=False):
        acilan.append(path)
        raise physical.PhysicalDiskError(f"test: {path} acilmiyor")

    hedef = "\\\\.\\PhysicalDrive9"
    kayitli = physical.DiskInfo(path=hedef, name="PhysicalDrive9",
                                size=32 * MIB, model="Sahte Kart")
    physical._win_handle = sahte_handle
    physical._win_drive_letters = lambda: {}
    physical._win_system_disk_numbers = lambda: []
    physical._register_open(kayitli)
    try:
        diskler = physical._list_windows()
    finally:
        physical._unregister_open(hedef)
        physical._win_handle = gercek_handle
        physical._win_drive_letters = gercek_letters
        physical._win_system_disk_numbers = gercek_system

    assert hedef not in acilan, \
        f"acik aygit yine de acilmaya calisildi: {hedef}"
    assert any(p.endswith("PhysicalDrive0") for p in acilan), \
        "diger aygitlar yoklanmali"
    bulunan = [d for d in diskler if d.path == hedef]
    assert bulunan, "acik aygit listeden dustu"
    assert bulunan[0].in_use, "acik aygit in_use isareti tasimali"
    assert bulunan[0].model == "Sahte Kart", "bilinen bilgi korunmali"


# --------------------------------------------------------------------------
@test
def t21_birim_kilidi_kutukten_etkilenmez():
    """Birim kilitleme, acik aygit kutugune takilmamali (Windows)

    Gercek hata (2026-09-15): aygit acilir acilmaz kutuge girdigi icin
    `_win_drive_letters()` o diskin harflerini atliyordu; `_win_lock_volumes`
    de harfleri oradan sordugu icin **hicbir birim kilitlenmiyordu**. Sonuc:
    sonraki her yazma "Windows bagli birimlere yazmayi engeller" ile
    reddedildi — kullanici yonetici oldugu halde.

    Kilit artik harfleri `info.mounted` icinden alir. Gercek diske
    DOKUNULMAZ: aygit acma ve IOCTL islevleri sahte surumlerle degistirilir.
    """
    from diskultimate.core import physical

    if not physical.IS_WINDOWS:
        raise Atlandi("birim kilidi yalnizca Windows dalinda")

    info = physical.DiskInfo(path="\\\\.\\PhysicalDrive9", name="PhysicalDrive9",
                             size=64 * MIB, mounted=["Q:", "R:"])
    acilan = []
    ioctl_kodlari = []

    def sahte_handle(path, write=False):
        acilan.append(path)
        return 1234                      # sahte tutamac

    def sahte_ioctl(handle, code, data=b"", out_size=256):
        ioctl_kodlari.append(code)
        return b""                       # basarili sayilir

    gercek = (physical._win_handle, physical._win_ioctl, physical._win_close,
              physical._win_drive_letters)
    physical._win_handle = sahte_handle
    physical._win_ioctl = sahte_ioctl
    physical._win_close = lambda handle: None
    # Kutuk yuzunden bos donen surum: kilit buna GUVENMEMELI
    physical._win_drive_letters = lambda: {}

    disk = physical.PhysicalDisk.__new__(physical.PhysicalDisk)
    disk.info = info
    disk.path = info.path
    disk.sector_size = 512
    disk.readonly = False
    disk._volume_handles = []
    disk._locked_letters = []
    disk._unlocked_letters = []
    disk._locked_devices = set()      # ADR 0084: kilitli birim aygitlari
    disk._relocking = False
    try:
        physical._register_open(info)        # gercek akista da boyle olur
        try:
            disk._win_lock_volumes()
        finally:
            physical._unregister_open(info.path)
    finally:
        (physical._win_handle, physical._win_ioctl, physical._win_close,
         physical._win_drive_letters) = gercek

    assert disk._locked_letters == ["Q:", "R:"], disk._locked_letters
    assert not disk._unlocked_letters, disk._unlocked_letters
    assert acilan == ["\\\\.\\Q:", "\\\\.\\R:"], acilan
    assert len(disk._volume_handles) == 2, "kilitli tutamaclar tutulmali"
    assert physical.FSCTL_LOCK_VOLUME in ioctl_kodlari, "birim kilitlenmedi"
    assert physical.FSCTL_DISMOUNT_VOLUME in ioctl_kodlari, "birim ayrilmadi"


# --------------------------------------------------------------------------
@test
def t22_yetki_yukseltme():
    """Yonetici/root yukseltmesi: durum, uygunluk ve yeniden baslatma komutu

    Gercek bir UAC/polkit penceresi ACILMAZ: yalnizca karar mantigi ve
    uretilen komut satiri denetlenir.
    """
    from diskultimate.core import platform as pf

    # -- durum sorgusu calisiyor ve mantikli --
    elevated = pf.is_elevated()
    assert isinstance(elevated, bool)
    assert pf.ELEVATION_NAME in ("Yonetici", "root")
    assert pf.summary()["Yetki"], "ozet yetki satiri tasimali"

    # -- yeniden baslatma komutu: yorumlayici + betik --
    komut = pf._relaunch_target()
    assert komut, "betikten calistirilirken komut uretilmeli"
    assert komut[0] == sys.executable, komut
    assert os.path.isfile(komut[1]), f"betik yolu gecerli degil: {komut}"

    # Betik yolu yoksa (python -c) UAC penceresi ACILMAMALI
    eski_argv = sys.argv
    sys.argv = ["-c"]
    try:
        assert pf._relaunch_target() == [], "betiksiz durumda komut uretilmemeli"
        if not elevated:
            uygun, neden = pf.elevation_available()
            assert not uygun and neden, "betiksiz durumda yukseltme sunulmamali"
    finally:
        sys.argv = eski_argv

    # -- AppImage icinde (ADR 0050): yorumlayici FUSE baglantisinda durur,
    # root ona erisemez; komut AppImage dosyasinin KENDISI olmali --
    from diskultimate import paths as _paths
    sahte = img_path("t22_DiskUltimate.AppImage")
    open(sahte, "wb").close()
    eski = (_paths.IS_APPIMAGE, os.environ.get("APPIMAGE"), list(sys.argv))
    try:
        _paths.IS_APPIMAGE = True
        os.environ["APPIMAGE"] = sahte
        sys.argv = [sys.argv[0], "disk.img"]
        assert pf._relaunch_target() == [sahte, "disk.img"], pf._relaunch_target()
        os.environ["APPIMAGE"] = sahte + ".yok"       # dosya yoksa eski yol
        assert pf._relaunch_target()[0] != sahte + ".yok"
    finally:
        _paths.IS_APPIMAGE = eski[0]
        if eski[1] is None:
            os.environ.pop("APPIMAGE", None)
        else:
            os.environ["APPIMAGE"] = eski[1]
        sys.argv = eski[2]
    # Kaynaktan calisirken AppImage sayilmamali (APPDIR proje disinda)
    assert not _paths._inside_appimage() or os.environ.get("APPDIR")

    # -- uygunluk yetkiliyken kapali, nedeni acik --
    uygun, neden = pf.elevation_available()
    if elevated:
        assert not uygun, "zaten yetkiliyken yukseltme sunulmamali"
        assert "zaten" in neden.lower(), neden
        # Zaten yetkiliyken cagrilsa bile hicbir sey baslatilmamali
        tutamac, hata = pf.relaunch_elevated()
        assert tutamac is None and hata == neden, (tutamac, hata)
    else:
        assert uygun or neden, "ya yukseltilebilmeli ya da nedeni olmali"

    # -- yeni kopyanin "acildim" bildirimi --
    #
    # pkexec yetkilendirmeyi kendi EBEVEYNINE bakarak yapar; eski kopya
    # baslatir baslatmaz kapanirsa parola penceresi hic acilmaz (ADR 0039).
    # Bu yuzden eski kopya yenisinin bildirimini bekler. Burada denenen o
    # el sikisma: gercek bir polkit/UAC penceresi ACILMAZ.
    class SahteSurec:
        """Popen yerine gecer: `poll()` sabit bir cikis kodu doner."""

        def __init__(self, code):
            self.code = code

        def poll(self):
            return self.code

    yol = os.path.join(scratch("elevate"), f"test-ready-{os.getpid()}")
    if os.path.exists(yol):
        os.remove(yol)
    eski_ortam = os.environ.get(pf.HANDOFF_ENV)
    os.environ[pf.HANDOFF_ENV] = yol
    try:
        assert pf.signal_elevated_ready() == yol, "bildirim yazilmali"
        assert os.path.isfile(yol), "bildirim dosyasi olusmali"
        # Degisken okunurken silinir: alt surecler bildirimi devralmamali
        assert pf.HANDOFF_ENV not in os.environ, "bildirim ortamdan silinmeli"
        assert pf.signal_elevated_ready() == "", "bildirim yalnizca bir kez"

        # Bildirim varken: yeni kopya acildi -> eski kopya kapanabilir
        durum, mesaj = pf.ElevatedLaunch(SahteSurec(None), yol).poll()
        assert durum == pf.ElevatedLaunch.STARTED, (durum, mesaj)
        os.remove(yol)

        # Bildirim yok, surec suruyor -> parola penceresi bekleniyor
        durum, _ = pf.ElevatedLaunch(SahteSurec(None), yol).poll()
        assert durum == pf.ElevatedLaunch.WAITING, durum

        # Surec bildirimsiz bitti -> hata, ve nedeni kullaniciya soylenir
        for kod in (pf.PKEXEC_DISMISSED, pf.PKEXEC_ERROR, 1):
            durum, mesaj = pf.ElevatedLaunch(SahteSurec(kod), yol).poll()
            assert durum == pf.ElevatedLaunch.FAILED, (kod, durum)
            assert mesaj.strip(), f"cikis kodu {kod} icin aciklama bos"

        # Windows/macOS: pencereyi isletim sistemi yonetir, beklenmez
        assert pf.ElevatedLaunch().poll()[0] == pf.ElevatedLaunch.STARTED
    finally:
        if eski_ortam is None:
            os.environ.pop(pf.HANDOFF_ENV, None)
        else:
            os.environ[pf.HANDOFF_ENV] = eski_ortam
        if os.path.exists(yol):
            os.remove(yol)


# --------------------------------------------------------------------------
@test
def t23_ntfs_bitmap_aralikli_yazma():
    """$Bitmap yalnizca degisen bolumu yazmali, sonuc tam yazmayla ayni olmali

    Onceki surum her tahsiste ve her serbest birakmada bitmap'in **tamamini**
    okuyup yaziyordu: 58 GB'lik bir bolumde tek bir kume icin 1.9 MB okuma +
    1.9 MB yazma (ADR 0024, gercek olcum).

    Buradaki olcut nettir: **aralikli yazmadan sonraki bitmap, tam yazmanin
    uretecegi bitmap ile birebir ayni olmalidir.** Yanlis ofset, atlanan
    pencere veya hizalama hatasi bu karsilastirmada yakalanir.

    Pencere siniri de zorlanir: `BITMAP_WINDOW` gecici olarak kucultulur,
    boylece kucuk bir test biriminde bile tahsis birden fazla pencereye yayilir.
    """
    from diskultimate.core import ntfswrite
    from diskultimate.core.ntfswrite import NtfsWriter

    p = img_path("t23.img")
    d = DiskImage.create(p, 96 * MIB, overwrite=True)
    view = PartitionView(d, 2048, d.sector_count - 2048)
    format_ntfs(view, label="BITMAP")

    fs = NtfsFS(PartitionView(d, 2048, d.sector_count - 2048))
    writer = NtfsWriter(fs)

    def tam_bitmap() -> bytearray:
        """$Bitmap'in tamami — karsilastirma olcutu."""
        return bytearray(fs.read_attribute(writer._bitmap_attr()))

    def bit_oku(buf, index) -> bool:
        return bool(buf[index >> 3] & (1 << (index & 7)))

    eski_pencere = ntfswrite.BITMAP_WINDOW
    ntfswrite.BITMAP_WINDOW = 64        # 64 bayt = 512 kume: sinir zorlanir
    try:
        # --- tahsis: sonuc tam yazmayla ayni mi? ---
        onceki = tam_bitmap()
        kumeler = writer.alloc_clusters(1500)      # birkac pencereye yayilir
        toplam = sum(c for _l, c in kumeler)
        assert toplam == 1500, f"{toplam} kume tahsis edildi"

        beklenen = bytearray(onceki)
        for lcn, count in kumeler:
            for k in range(count):
                beklenen[(lcn + k) >> 3] |= 1 << ((lcn + k) & 7)
        sonra = tam_bitmap()
        assert sonra == beklenen, "tahsis sonrasi bitmap tam yazmadan farkli"

        # 1500 kume, 64 baytlik pencerede ~3 pencereye yayilir. Yeni
        # bicimlendirilmis birimde bu alan bitisiktir: sonuc TEK parca olmali.
        # Bu tek olcut iki hatayi birden yakalar — pencere sinirinda parcalarin
        # birlestirilmemesi ve bir pencerenin atlanmasi (arada bosluk kalir).
        assert len(kumeler) == 1, \
            f"bitisik alan tek parca donmeli, donen: {kumeler}"

        # --- serbest birakma: yalnizca hedef bitler sifirlanmali ---
        onceki = tam_bitmap()
        birak = kumeler[:1] if len(kumeler) == 1 else kumeler
        writer.free_clusters(birak)
        beklenen = bytearray(onceki)
        for lcn, count in birak:
            for k in range(count):
                beklenen[(lcn + k) >> 3] &= ~(1 << ((lcn + k) & 7)) & 0xFF
        sonra = tam_bitmap()
        assert sonra == beklenen, "serbest birakma sonrasi bitmap farkli"

        # --- dagitik serbest birakma: komsu bitler bozulmamali ---
        yeni = writer.alloc_clusters(64)
        hepsi = [(lcn + k) for lcn, count in yeni for k in range(count)]
        assert len(hepsi) == 64
        # Aradan bir bit birak; komsulari 1 kalmali
        hedef = hepsi[len(hepsi) // 2]
        onceki = tam_bitmap()
        writer.free_clusters([(hedef, 1)])
        sonra = tam_bitmap()
        assert not bit_oku(sonra, hedef), "hedef bit serbest birakilmadi"
        assert bit_oku(sonra, hedef - 1) and bit_oku(sonra, hedef + 1), \
            "komsu bitler bozuldu"
        beklenen = bytearray(onceki)
        beklenen[hedef >> 3] &= ~(1 << (hedef & 7)) & 0xFF
        assert sonra == beklenen, "tek bit serbest birakma tam yazmadan farkli"
        writer.free_clusters(yeni)

        # --- yetersiz alan: kismi tahsis birakilmamali ---
        onceki = tam_bitmap()
        try:
            writer.alloc_clusters(10 ** 9)
            assert False, "yetersiz alanda tahsis basarili olmamaliydi"
        except NtfsError:
            pass
        assert tam_bitmap() == onceki, \
            "basarisiz tahsis bitmap'te iz birakti (kismi tahsis)"
    finally:
        ntfswrite.BITMAP_WINDOW = eski_pencere

    # --- gercek dosya islemleri hala dogru (varsayilan pencere ile) ---
    erisim = open_filesystem(PartitionView(d, 2048, d.sector_count - 2048),
                             detect(PartitionView(d, 2048, d.sector_count - 2048)))
    icerik = {f"/dosya{i}.bin": bytes([i % 251]) * (40000 + i * 1000)
              for i in range(6)}
    for yol, veri in icerik.items():
        erisim.write_file(yol, veri)
    erisim.flush()
    for yol, veri in icerik.items():
        assert erisim.read(yol) == veri, f"{yol} icerigi bozuk"
    for yol in list(icerik)[:3]:
        erisim.remove(yol)
    erisim.flush()
    kalan = {n.name for n in erisim.listdir("/")}
    assert kalan == {"dosya3.bin", "dosya4.bin", "dosya5.bin"}, kalan
    for yol in list(icerik)[3:]:
        assert erisim.read(yol) == icerik[yol], f"{yol} silme sonrasi bozuldu"


# --------------------------------------------------------------------------
@test
def t24_yedek_onizleme():
    """Yedegin icerigi GERI YUKLENMEDEN okunabilmeli (bolumler + kok klasor)

    `backup_info` yalnizca basligi okur. Kullanici "bu yedekte ne var?"
    sorusunu ancak yedegi acarak ya da bir diske yazarak yanitlayabiliyordu.
    `backup_preview` yedegi `DubImage` uzerinden acar, bolum tablosunu cozer,
    dosya sistemlerini tespit eder ve kok klasoru listeler — hicbir yere
    yazmadan.
    """
    kaynak = img_path("t24.img")
    s = DiskSession.create(kaynak, 512 * MIB, scheme="gpt", overwrite=True)
    mevcut = {k.key for k in available_kinds()}
    beklenen_fs = []
    for key, mb, etiket in (("fat32", 200, "SISTEM"),
                            ("ntfs", 160, "VERI"),
                            ("exfat", 100, "TASINABILIR")):
        if key not in mevcut:
            continue
        r = s.free_regions()[0]
        p = s.create_partition(r.start_lba, mb * MIB // 512, fs_key=key,
                               label=etiket)
        fs = s.filesystem(p.index)
        fs.mkdir("/Belgeler")
        fs.write_file("/okubeni.txt", b"yedek onizleme\n")
        fs.flush()
        beklenen_fs.append(key)
    assert len(beklenen_fs) >= 2, f"yeterli dosya sistemi yok: {beklenen_fs}"
    bolum_sayisi = len(s.partitions)
    s.close()

    # --- tum diskin yedegi ---
    disk_dub = img_path("t24_disk.dub")
    s = DiskSession.open(kaynak, readonly=True)
    backup(s.image, disk_dub, compress=True)
    ilk_bolum = s.table.get(1)
    bolum_dub = img_path("t24_bolum.dub")
    backup(s.view(ilk_bolum), bolum_dub, compress=True,
           fs_type=ilk_bolum.fs_type, label=ilk_bolum.fs_label)
    s.close()

    kaynak_once = os.path.getsize(kaynak)
    onizleme = DiskSession.backup_preview(disk_dub)
    assert onizleme.is_whole_disk, "disk yedegi tum disk olarak gorulmeli"
    assert len(onizleme.partitions) == bolum_sayisi, \
        f"{len(onizleme.partitions)} bolum gorundu, {bolum_sayisi} bekleniyordu"
    for part in onizleme.partitions:
        assert part.fs_type, f"Bolum {part.index} dosya sistemi tespit edilmedi"
        girisler = onizleme.root_entries.get(part.index)
        assert girisler is not None,             f"Bolum {part.index} ({part.fs_type}) icerigi okunamadi"
        assert "okubeni.txt" in girisler, f"Bolum {part.index}: {girisler}"
        assert "Belgeler/" in girisler, f"Bolum {part.index}: {girisler}"
    assert onizleme.info.total_bytes == 512 * MIB, onizleme.info.total_bytes

    # --- tek bolumun yedegi ---
    # FAT onyukleme sektoru de 0xAA55 ile biter; bu yuzden "bolum tablosu var
    # mi" olcutu yaniltir. Olcut bolum BULUNUP bulunmadigidir.
    tek = DiskSession.backup_preview(bolum_dub)
    assert not tek.is_whole_disk, "tek bolum yedegi disk sanildi"
    assert not tek.partitions, tek.partitions
    assert tek.filesystem is not None and tek.filesystem.fs_type, \
        "tek bolum yedeginde dosya sistemi tespit edilmedi"
    assert "okubeni.txt" in tek.root_entries.get(-1, []), tek.root_entries

    # --- onizleme HICBIR SEY yazmamali ---
    assert os.path.getsize(kaynak) == kaynak_once, "kaynak goruntu degisti"
    for yol in (disk_dub, bolum_dub):
        with open(yol, "rb") as fh:
            assert fh.read(8) == b"DUBACKUP", "yedek dosyasi bozuldu"

    # --- "bos" ile "okunamadi" ayri seylerdir ---
    # Ikisi de bos liste gosterilse, bos bir NTFS bolumu "desteklenmiyor"
    # sanilirdi. Bos bir bolum ekleyip ayrimin korundugu dogrulanir.
    bos_kaynak = img_path("t24_bos.img")
    s = DiskSession.create(bos_kaynak, 128 * MIB, scheme="gpt", overwrite=True)
    r = s.free_regions()[0]
    s.create_partition(r.start_lba, 64 * MIB // 512, fs_key="fat32",
                       label="BOS")
    s.close()
    bos_dub = img_path("t24_bos.dub")
    s = DiskSession.open(bos_kaynak, readonly=True)
    backup(s.image, bos_dub, compress=True)
    s.close()
    bos_onizleme = DiskSession.backup_preview(bos_dub)
    bos_girisler = bos_onizleme.root_entries.get(1)
    assert bos_girisler == [],         f"bos bolum [] dondurmeli, donen: {bos_girisler!r}"

    # --- yedek olmayan dosya reddedilmeli ---
    try:
        DiskSession.backup_preview(kaynak)
        assert False, "ham goruntu yedek sayilmamaliydi"
    except Exception as exc:
        assert "yedegi degil" in str(exc) or "yedek" in str(exc).lower(), exc


# --------------------------------------------------------------------------
@test
def t25_islem_kuyrugu():
    """Bekleyen islem kuyrugu: kuyruk dolarken diske DOKUNULMAZ, Uygula sirayla isler

    Disk araclarinin ortak calisma bicimi (Acronis, EaseUS, AOMEI): islemler
    once kuyruga girer, hicbir sey yazilmaz; kullanici hepsini gorup tek bir
    "Uygula" ile calistirir (ADR 0025).

    Burada dogrulanan sozler:
      1. Kuyruga eklemek diski **degistirmez** (sha256 ayni kalir).
      2. Adimlar **sirayla** calisir; sonraki oncekinin sonucuna dayanabilir.
      3. Basarisiz adim kuyrugu **durdurur**; tamamlananlar raporlanir ve
         duran adim kuyrukta kalir ki kullanici duzeltebilsin.
      4. Salt okunur kaynakta kuyruk dolar ama uygulama reddedilir.
    """
    from diskultimate.core import operations as ops

    yol = img_path("t25.img")
    s = DiskSession.create(yol, 256 * MIB, overwrite=True)
    s.close()
    with open(yol, "rb") as fh:
        once = hashlib.sha256(fh.read()).hexdigest()

    kuyruk = ops.OperationQueue()
    assert kuyruk.is_empty and len(kuyruk) == 0

    s = DiskSession.open(yol)
    sektor = s.image.sector_size
    kuyruk.add(ops.create_table_op("gpt"))
    kuyruk.add(ops.create_op(2048, 64 * MIB // sektor, sektor,
                             fs_key="fat32", label="BIR"))
    kuyruk.add(ops.create_op(2048 + 64 * MIB // sektor, 64 * MIB // sektor,
                             sektor, fs_key="fat32", label="IKI"))
    kuyruk.add(ops.label_op(1, "YENIAD"))
    assert len(kuyruk) == 4, len(kuyruk)
    assert kuyruk.destructive_count == 1, "yalnizca tablo olusturma yikici"

    # 1. Kuyruk dolarken hicbir sey yazilmamis olmali
    s.close()
    with open(yol, "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == once, \
            "kuyruga eklemek diski degistirdi"

    # Kuyruk yonetimi: cikarma, geri alma, tasima
    kopya = ops.OperationQueue()
    for op in kuyruk:
        kopya.add(op)
    kopya.remove(0)
    assert len(kopya) == 3
    assert kopya.undo().kind == "label", "undo son adimi almali"
    assert len(kopya) == 2
    kopya.move(1, 0)
    assert kopya[0].params["label"] == "IKI", "tasima calismadi"
    assert kopya.clear() == 2 and kopya.is_empty

    # 2. Uygula: adimlar sirayla islemeli
    s = DiskSession.open(yol)
    adimlar = []
    sonuc = kuyruk.apply(s, on_step=lambda i, op: adimlar.append(op.kind))
    assert sonuc.ok, sonuc.summary()
    assert adimlar == ["create_table", "create", "create", "label"], adimlar
    assert len(sonuc.done) == 4, sonuc.summary()
    assert kuyruk.is_empty, "uygulanan adimlar kuyrukta kalmamali"

    s.reload()
    assert s.scheme == "gpt", s.scheme
    assert len(s.partitions) == 2, s.partitions
    fs = s.filesystem(1)
    assert fs.label == "YENIAD", f"etiket adimi islemedi: {fs.label}"
    s.close()

    # 3. Basarisiz adim kuyrugu durdurmali
    s = DiskSession.open(yol)
    kuyruk2 = ops.OperationQueue()
    kuyruk2.add(ops.label_op(2, "OLUR"))
    kuyruk2.add(ops.format_op(99, "fat32"))          # boyle bir bolum yok
    kuyruk2.add(ops.label_op(1, "CALISMAMALI"))
    sonuc2 = kuyruk2.apply(s)
    assert not sonuc2.ok, "olmayan bolum bicimlendirilemez"
    assert len(sonuc2.done) == 1, sonuc2.summary()
    assert sonuc2.failed.kind == "format", sonuc2.failed
    assert len(sonuc2.pending) == 1, sonuc2.pending
    assert len(kuyruk2) == 2, "duran adim ve sonrasi kuyrukta kalmali"
    assert s.filesystem(1).label == "YENIAD", "durdurulan adim yine de islemis"
    s.close()

    # 4. Salt okunur kaynak: kuyruk dolar, uygulama reddedilir
    s = DiskSession.open(yol, readonly=True)
    assert s.readonly
    uygun, neden = s.can_become_writable()
    assert uygun and not neden, (uygun, neden)     # goruntu dosyasi gecebilir
    kuyruk3 = ops.OperationQueue()
    kuyruk3.add(ops.label_op(1, "REDDEDILMELI"))
    sonuc3 = kuyruk3.apply(s)
    assert not sonuc3.ok, "salt okunur kaynakta yazma basarili olmamaliydi"
    s.close()

    # Yedek dosyasi yazma moduna GECEMEZ ve bunu soyler
    dub = img_path("t25.dub")
    s = DiskSession.open(yol, readonly=True)
    backup(s.image, dub, compress=True)
    s.close()
    s = DiskSession.open(dub)
    uygun, neden = s.can_become_writable()
    assert not uygun and "arsiv" in neden.lower(), (uygun, neden)
    s.close()


# --------------------------------------------------------------------------
@test
def t26_dogrudan_yazma_yollari():
    """Kuyruga GIRMEYEN yazma islemleri de salt okunur kaynakta calisabilmeli

    ADR 0025 ile kaynak her zaman salt okunur acilir. Geri yukleme, klonlama
    gibi **kuyruklanmayan** islemler bundan etkilendi: Linux'ta bir `.dub`
    yedegi `/dev/sdb` diskine yazilmak istendiginde "Fiziksel diskler ...
    SALT OKUNUR acilir" hatasi aliniyordu (2026-09-15, kullanici bildirimi).

    Cozum: bu yollar once `become_writable()` cagirir. Burada dogrulanan:
      1. Salt okunur acilan bir kaynak yazma moduna **gecebiliyor**.
      2. Gectikten sonra geri yukleme gercekten calisiyor.
      3. Gecilemeyen kaynak (yedek dosyasi) nedenini soyluyor.
      4. `become_writable()` idempotent: zaten yazilabilirken bozmuyor.
    """
    kaynak = img_path("t26_kaynak.img")
    s = DiskSession.create(kaynak, 128 * MIB, scheme="mbr", overwrite=True)
    r = s.free_regions()[0]
    p = s.create_partition(r.start_lba, 64 * MIB // 512, fs_key="fat32",
                           label="KAYNAK")
    fs = s.filesystem(p.index)
    fs.write_file("/imza.txt", b"t26 imzasi\n")
    fs.flush()
    s.close_filesystems()
    dub = img_path("t26.dub")
    backup(s.view(s.table.get(1)), dub, compress=True, fs_type="FAT32")
    s.close()

    # --- hedef: salt okunur acilmis bir goruntu ---
    hedef = img_path("t26_hedef.img")
    s = DiskSession.create(hedef, 128 * MIB, scheme="mbr", overwrite=True)
    r = s.free_regions()[0]
    s.create_partition(r.start_lba, 64 * MIB // 512, fs_key="fat32",
                       label="HEDEF")
    s.close()

    s = DiskSession.open(hedef, readonly=True)
    assert s.readonly, "test salt okunur acmali"

    # 1. Gecis mumkun olmali ve nedeni bos olmali
    uygun, neden = s.can_become_writable()
    assert uygun and not neden, (uygun, neden)

    # Gecmeden once yazma reddedilmeli (eski hatanin ta kendisi)
    try:
        s.restore_partition(1, dub)
        assert False, "salt okunur kaynakta geri yukleme basarili olmamaliydi"
    except Exception as exc:
        assert "salt okunur" in str(exc).lower(), exc

    # 2. Gectikten sonra calismali
    s.become_writable(confirm=True)
    assert not s.readonly, "yazma moduna gecilemedi"
    s.restore_partition(1, dub)
    fs = s.filesystem(1)
    assert fs.read("/imza.txt") == b"t26 imzasi\n", "geri yukleme icerigi bozuk"
    assert fs.label == "KAYNAK", f"etiket geri yuklenmedi: {fs.label}"

    # 4. Ikinci cagri zararsiz olmali
    s.become_writable(confirm=True)
    assert not s.readonly
    s.close()

    # 3. Gecilemeyen kaynak nedenini soylemeli
    s = DiskSession.open(dub)
    uygun, neden = s.can_become_writable()
    assert not uygun and neden, (uygun, neden)
    try:
        s.become_writable(confirm=True)
        assert False, "yedek dosyasi yazma moduna gecmemeliydi"
    except Exception as exc:
        assert "arsiv" in str(exc).lower(), exc
    assert s.readonly, "basarisiz gecis kaynagi bozmamali"
    # Kaynak hala okunabilir olmali
    assert s.image.size > 0
    s.close()


# --------------------------------------------------------------------------
@test
def t27_disk_yoklamasi():
    """Disk bolumleri **acilmadan** okunabilmeli, aygit hemen birakilmali

    Acronis vari gorunum icin diskin bolumlerini diski acmadan bilmek gerekir
    (ADR 0026). `survey_disk` aygiti salt okunur acar, bolum tablosunu ve her
    bolumun dosya sistemini okur, sonra **hemen kapatir**.

    Gercek diske dokunulmaz: `PhysicalDisk` sahte bir surumle degistirilir,
    aygitin acilip kapandigi ve **hicbir yazma yapilmadigi** sayilarak
    dogrulanir.
    """
    from diskultimate.core import session as session_mod
    from diskultimate.core.physical import DiskInfo

    # Icinde iki bolumlu bir MBR bulunan goruntu
    yol = img_path("t27.img")
    s = DiskSession.create(yol, 256 * MIB, scheme="mbr", overwrite=True)
    r = s.free_regions()[0]
    s.create_partition(r.start_lba, 100 * MIB // 512, fs_key="fat32",
                       label="BIRINCI")
    r = s.free_regions()[0]
    s.create_partition(r.start_lba, 80 * MIB // 512, fs_key="fat16",
                       label="IKINCI")
    s.close()

    olaylar = []

    class SahteAygit(DiskImage):
        """Goruntu dosyasini fiziksel disk gibi sunar; yazmayi reddeder."""

        def __init__(self, info, readonly=True, confirm=False,
                     allow_system=False):
            super().__init__(yol, readonly=True)
            self.info = info
            olaylar.append(("acildi", readonly))

        def write(self, offset, data):
            olaylar.append(("YAZMA", offset))
            raise AssertionError("yoklama sirasinda yazma yapilmamali")

        def close(self):
            olaylar.append(("kapandi", 0))
            super().close()

    gercek = session_mod.PhysicalDisk
    session_mod.PhysicalDisk = SahteAygit
    try:
        info = DiskInfo(path="/sahte/t27", name="SahteT27", size=256 * MIB)
        survey = DiskSession.survey_disk(info)
    finally:
        session_mod.PhysicalDisk = gercek

    assert survey.error == "", survey.error
    assert survey.scheme == "mbr", survey.scheme
    assert survey.scheme_name == "MBR", survey.scheme_name
    assert len(survey.partitions) == 2, survey.partitions
    etiketler = [p.fs_label for p in survey.partitions]
    assert etiketler == ["BIRINCI", "IKINCI"], etiketler
    turler = [p.fs_type for p in survey.partitions]
    assert turler == ["FAT32", "FAT16"], turler

    # Aygit salt okunur acilmis, yazilmamis ve KAPATILMIS olmali
    assert ("acildi", True) in olaylar, olaylar
    assert not any(e[0] == "YAZMA" for e in olaylar), olaylar
    assert olaylar[-1][0] == "kapandi", f"aygit birakilmadi: {olaylar}"

    # Uygulamanin kendi acik tuttugu aygit YOKLANMAZ (ADR 0021)
    from diskultimate.core import physical

    kayitli = DiskInfo(path="/sahte/t27b", name="SahteT27b", size=MIB)
    physical._register_open(kayitli)
    try:
        session_mod.PhysicalDisk = SahteAygit
        onceki = len(olaylar)
        survey2 = DiskSession.survey_disk(kayitli)
    finally:
        session_mod.PhysicalDisk = gercek
        physical._unregister_open(kayitli.path)
    assert survey2.error == "uygulamada acik", survey2.error
    assert len(olaylar) == onceki, "acik aygit yine de acilmaya calisildi"

    # Bolum tablosu olmayan disk hatasiz, bos sonuc dondurmeli
    bos_yol = img_path("t27_bos.img")
    DiskImage.create(bos_yol, 16 * MIB, overwrite=True).close()

    class BosAygit(SahteAygit):
        def __init__(self, info, readonly=True, confirm=False,
                     allow_system=False):
            DiskImage.__init__(self, bos_yol, readonly=True)
            self.info = info

    session_mod.PhysicalDisk = BosAygit
    try:
        bos = DiskSession.survey_disk(
            DiskInfo(path="/sahte/t27c", name="Bos", size=16 * MIB))
    finally:
        session_mod.PhysicalDisk = gercek
    assert bos.error == "", bos.error
    assert bos.partitions == [], bos.partitions
    assert bos.scheme_name == "Bolum tablosu yok", bos.scheme_name


# --------------------------------------------------------------------------
@test
def t28_dosya_ekleme_yazma_modu():
    """Salt okunur acilan kaynaga dosya eklenebilmeli (yetki ilk yazmada)

    ADR 0025 ile kaynak her zaman salt okunur aciliyor. Dosya islemleri
    kuyruga **girmez** (bir dosya sisteminin icinde olurlar), bu yuzden
    yazma yetkisini kendileri istemek zorunda. Bu yapilmayinca Linux Mint'te
    bir USB diske dosya eklenemiyordu (kullanici bildirimi).

    Burada dogrulanan:
      1. Salt okunur oturumun dosya sistemi `writable=False` verir.
      2. `become_writable()` sonrasi **taze** dosya sistemi yazilabilirdir.
      3. Eski (bayat) dosya sistemi nesnesi kullanilmaz — yeniden alinir.
      4. Dosya gercekten yazilir ve geri okunur.
    """
    yol = img_path("t28.img")
    s = DiskSession.create(yol, 128 * MIB, scheme="mbr", overwrite=True)
    r = s.free_regions()[0]
    s.create_partition(r.start_lba, 64 * MIB // 512, fs_key="fat32",
                       label="HEDEF")
    s.close()

    # 1. Salt okunur acilan kaynak: yazilamaz
    s = DiskSession.open(yol, readonly=True)
    fs = s.filesystem(1)
    assert fs is not None and fs.readable, "bolum okunabilir olmali"
    assert not fs.writable, "salt okunur kaynakta yazilabilir gorunmemeli"
    assert fs.write_reason, "neden bos olmamali"
    bayat = fs

    try:
        fs.write_file("/olmaz.txt", b"x")
        assert False, "salt okunur kaynakta yazma basarili olmamaliydi"
    except Exception:
        pass

    # 2. Yazma moduna gec — kaynak yeniden acilir
    uygun, neden = s.can_become_writable()
    assert uygun and not neden, (uygun, neden)
    s.become_writable(confirm=True)
    assert not s.readonly

    # 3. Taze dosya sistemi alinmali; bayat nesne artik kullanilmaz
    taze = s.filesystem(1)
    assert taze is not bayat, "yeniden acilan kaynakta ayni fs nesnesi donuyor"
    assert taze.writable, f"taze dosya sistemi yazilabilir olmali: {taze.write_reason}"

    # 4. Dosya gercekten yazilmali
    veri = b"DiskUltimate t28\n" * 64
    taze.write_file("/eklendi.txt", veri)
    taze.mkdir("/klasor")
    taze.flush()
    s.close()

    s = DiskSession.open(yol, readonly=True)
    fs = s.filesystem(1)
    adlar = {n.name for n in fs.listdir("/")}
    assert "eklendi.txt" in adlar, adlar
    assert "klasor" in adlar, adlar
    assert fs.read("/eklendi.txt") == veri, "yazilan icerik bozuk"
    s.close()



# --------------------------------------------------------------------------
@test
def t29_uefi_yapilari():
    """UEFI onyukleme girisi, aygit yolu ve kisayol yapilari gidis-donus olmali

    Bu sinama bir UEFI makinesi gerektirmez ve bu bilerek boyledir: yapi
    cozumlemesi `core/efiboot.py` icinde, bellenim erisiminden **ayri**
    durur (ADR 0029). Ayrilma olmasaydi cozumleyici yalnizca UEFI ile
    acilmis bir makinede test edilebilirdi.

    Olculen sey `efibootmgr`in urettigi metnin birebir aynisidir; boylece
    ciktimiz bagimsiz bir araca karsi dogrulanabilir.
    """
    # -- aygit yolu: gercek bir Ubuntu shim girisinin bileseni --------------
    nodes = [
        efiboot.DevicePathNode(efiboot.TYPE_ACPI, 0x01,
                               struct.pack("<II", 0x0A0341D0, 0)),
        efiboot.DevicePathNode(efiboot.TYPE_HARDWARE, 0x01,
                               struct.pack("<BB", 0x00, 0x1D)),
        efiboot.make_hard_drive_node(2, 264192, 204800, "gpt",
                                     "a967f8c5-ed93-4c8d-927d-61ca376a5e9b"),
        efiboot.make_file_node("/EFI/ubuntu/shimx64.efi"),
    ]
    beklenen = ("PciRoot(0x0)/Pci(0x1d,0x00)/"
                "HD(2,GPT,a967f8c5-ed93-4c8d-927d-61ca376a5e9b,0x40800,0x32000)/"
                "File(\\EFI\\ubuntu\\shimx64.efi)")
    uretilen = efiboot.device_path_text(nodes)
    assert uretilen == beklenen, f"{uretilen!r} != {beklenen!r}"

    ham = efiboot.build_device_path(nodes)
    geri = efiboot.parse_device_path(ham)
    assert efiboot.device_path_text(geri) == beklenen, "yol gidis-donus bozuk"
    assert geri[-1].is_end, "bitis dugumu eklenmedi"

    # -- onyukleme girisi ---------------------------------------------------
    option = efiboot.LoadOption(number=5, description="ubuntu",
                                path_nodes=nodes,
                                optional_data="root=UUID=x".encode("utf-16-le"))
    ham = option.to_bytes()
    geri = efiboot.LoadOption.parse(ham, number=5)
    assert geri.to_bytes() == ham, "giris gidis-donus bozuk"
    assert geri.description == "ubuntu", geri.description
    assert geri.active, "etkin bayragi kayboldu"
    assert geri.name == "Boot0005", geri.name
    assert geri.file_path == "\\EFI\\ubuntu\\shimx64.efi", geri.file_path
    bolum = geri.partition
    assert bolum["number"] == 2 and bolum["start_lba"] == 264192, bolum
    assert bolum["guid"] == "a967f8c5-ed93-4c8d-927d-61ca376a5e9b", bolum
    assert geri.optional_text == "root=UUID=x", geri.optional_text

    # Etkin bayragini kapatmak yalnizca o biti degistirmeli
    geri.active = False
    assert not geri.active and geri.description == "ubuntu"
    assert efiboot.LoadOption.parse(geri.to_bytes()).attributes \
        == option.attributes & ~efiboot.LOAD_OPTION_ACTIVE

    # -- cozulemeyen dugum korunmali ---------------------------------------
    # Tanimadigimiz bir bellenimin dugumunu dusurmek, girisi sessizce bozmak
    # olurdu; ham haliyle saklanir.
    garip = efiboot.DevicePathNode(0x42, 0x13, b"\xde\xad\xbe\xef")
    ham = efiboot.build_device_path([garip])
    geri = efiboot.parse_device_path(ham)
    assert geri[0].data == b"\xde\xad\xbe\xef", "bilinmeyen dugum bozuldu"
    assert "deadbeef" in geri[0].text(), geri[0].text()

    # -- GUID karisik siralamasi -------------------------------------------
    metin = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
    ham = efiboot.guid_to_bytes(metin)
    assert ham[:4] == bytes.fromhex("61dfe48b"), ham[:4].hex()
    assert efiboot.guid_from_bytes(ham) == metin

    # -- sira listesi -------------------------------------------------------
    sira = [7, 5, 0, 1, 2, 3]
    assert efiboot.parse_order(efiboot.build_order(sira)) == sira

    # -- kisayol ------------------------------------------------------------
    key_data = (2 << 30) | (1 << 9) | (1 << 10)      # iki tus, Ctrl+Alt
    ham = (struct.pack("<IIH", key_data, 0, 5)
           + struct.pack("<HH", 0x0B, 0) + struct.pack("<HH", 0, ord("X")))
    kisayol = efiboot.KeyOption.parse(ham, number=0)
    assert kisayol.to_bytes() == ham, "kisayol gidis-donus bozuk"
    assert kisayol.text() == "Ctrl+Alt+F1+X", kisayol.text()
    assert kisayol.boot_option == 5, kisayol.boot_option


# --------------------------------------------------------------------------
def _poke(sector: bytes, offset: int, data: bytes) -> bytes:
    """Sektorun icine **uzunlugunu degistirmeden** bayt yazar.

    Dilim atamasi (`sektor[a:b] = veri`) veri daha uzunsa bytearray'i buyutur
    ve 512 bayt 514'e cikar; `write_sector` bunu haklı olarak hizalama hatasi
    sayar. Bu yardimci o tuzagi kapatir.
    """
    body = bytearray(sector)
    assert offset + len(data) <= len(body), "sektor disina yazma"
    body[offset:offset + len(data)] = data
    return bytes(body)


@test
def t30_onyukleme_kodu_ve_sistem_tespiti():
    """Onyukleme kodu taninmali, kaldirilmasi bolum tablosunu KORUMALI

    `exxos-easy-grub-manager` iki isi de kabuk araclariyla yapiyordu:
    `dd | strings | grep GRUB` ile tanima ve `dd if=/dev/zero bs=440` ile
    kaldirma. Birincisi bolum tablosundaki rastgele baytlara takilabiliyor,
    ikincisi yalnizca Linux'ta calisiyordu. Buradaki karsiliklari her
    platformda ayni kodla, goruntu dosyasi uzerinde sinanir.
    """
    yol = img_path("t30.img")
    s = DiskSession.create(yol, 128 * MIB, scheme="mbr", overwrite=True)
    bolge = s.free_regions()[0]
    s.create_partition(bolge.start_lba, 64 * MIB // 512, fs_key="fat32",
                       label="SISTEM")
    tablo_once = s.read_sector(0)[bootloader.PARTITION_TABLE_OFFSET:512]
    assert any(tablo_once), "bolum tablosu bos cikti"

    # -- bos disk ----------------------------------------------------------
    assert s.boot_code().kind == "empty", s.boot_code().kind

    # -- GRUB imzasi taninmali --------------------------------------------
    sektor = _poke(s.read_sector(0), 0, b"\xeb\x63\x90")
    sektor = _poke(sektor, 0x180,
                   b"GRUB \x00Geom\x00Hard Disk\x00Read\x00 Error\x00")
    s.write_sector(0, sektor)
    kod = s.boot_code()
    assert kod.kind == "grub2", kod.kind
    assert kod.is_grub and not kod.is_empty
    assert kod.label, "onyukleme kodunun gorunen adi bos"

    # -- imza YALNIZCA ilk 440 bayttan okunmali ---------------------------
    # Bolum tablosunun icine "GRUB" yazmak tanimayi etkilememeli; eski
    # `strings | grep` yaklasiminin yaniltildigi nokta tam olarak burasiydi.
    s2 = DiskSession.create(img_path("t30b.img"), 64 * MIB, scheme="mbr",
                            overwrite=True)
    sektor = _poke(s2.read_sector(0),
                   bootloader.PARTITION_TABLE_OFFSET + 4, b"GRUB")
    s2.write_sector(0, sektor)
    assert s2.boot_code().kind != "grub2", \
        "bolum tablosundaki bayt onyukleme kodu sanildi"
    s2.close()

    # -- Windows onyukleyicisi --------------------------------------------
    sektor = _poke(s.read_sector(0), 0, b"\x00" * 440)
    sektor = _poke(sektor, 0x120, b"Invalid partition table\x00")
    s.write_sector(0, sektor)
    assert s.boot_code().kind == "windows", s.boot_code().kind

    # -- kaldirma: kod gitmeli, tablo DURMALI ------------------------------
    # Onceki adimin biraktigi Windows imzasi once temizlenir: iki imza ayni
    # sektorde bulunmaz ve `identify_boot_code` once Windows'a bakar.
    sektor = _poke(s.read_sector(0), 0, b"\x00" * 440)
    sektor = _poke(sektor, 0x180,
                   b"GRUB \x00Geom\x00Hard Disk\x00Read\x00 Error\x00")
    sektor = _poke(sektor, 0, b"\xeb\x63\x90")
    s.write_sector(0, sektor)
    assert s.boot_code().is_grub, s.boot_code().kind

    s.clear_boot_code()
    ilk = s.read_sector(0)
    assert ilk[:bootloader.BOOT_CODE_BYTES] == b"\x00" * bootloader.BOOT_CODE_BYTES, \
        "onyukleme kodu sifirlanmadi"
    assert ilk[bootloader.PARTITION_TABLE_OFFSET:512] == tablo_once, \
        "bolum tablosu bozuldu — veri erisilemez olurdu"
    assert ilk[510:512] == b"\x55\xaa", "MBR imzasi silindi"
    assert s.boot_code().kind == "empty", s.boot_code().kind
    # Bolum hala okunabilmeli
    s.reload()
    assert len(s.partitions) == 1, s.partitions
    assert s.filesystem(1) is not None, "bolum artik acilmiyor"
    s.close()

    # -- islem kuyrugundan calistirilabilmeli ------------------------------
    s = DiskSession.open(yol, readonly=False)
    sektor = _poke(s.read_sector(0), 0x100, b"GRUB \x00Geom\x00Read\x00")
    s.write_sector(0, sektor)
    s.close()

    s = DiskSession.open(yol, readonly=False)
    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.Operation(kind="clear_boot_code",
                             title_text="Onyukleme kodunu kaldir"))
    assert kuyruk.destructive_count == 1, "adim yikici sayilmadi"
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, sonuc.summary()
    assert s.read_sector(0)[:440] == b"\x00" * 440, "kuyruk adimi is gormedi"
    assert s.read_sector(0)[bootloader.PARTITION_TABLE_OFFSET:512] == tablo_once
    s.close()


# --------------------------------------------------------------------------
@test
def t31_isletim_sistemi_tespiti():
    """Kurulu sistem, VERI bolumunden ayirt edilmeli (baglamadan, her platformda)

    Dosya sisteminin turu kanit degildir: ext4 bir bolum, kurulu bir sistem
    kadar kolaylikla bir yedek diski olabilir. Orijinal arac bunu
    `mount -o ro` ile acip bakarak cozuyordu — root ve Linux gerektiriyordu.
    Burada ayni sey projenin kendi surucileriyle yapilir.
    """
    yol = img_path("t31.img")
    s = DiskSession.create(yol, 400 * MIB, scheme="gpt", overwrite=True)
    mevcut = {k.key for k in available_kinds()}

    # 1) ESP: FAT32 + \EFI klasoru
    bolge = s.free_regions()[0]
    esp = s.create_partition(bolge.start_lba, 100 * MIB // 512, fs_key="fat32",
                            label="ESP", type_guid=bootloader.ESP_GUID)
    fs = s.filesystem(esp.index)
    fs.mkdir("/EFI")
    fs.mkdir("/EFI/ubuntu")
    fs.write_file("/EFI/ubuntu/shimx64.efi", b"MZ" + b"\x00" * 256)
    fs.mkdir("/EFI/BOOT")
    fs.write_file("/EFI/BOOT/BOOTX64.EFI", b"MZ" + b"\x00" * 256)
    fs.flush()

    # 2) Kurulu Linux gorunumu (ext varsa) ya da FAT uzerinde veri bolumu
    linux_index = -1
    if "ext4" in mevcut:
        bolge = s.free_regions()[0]
        kok = s.create_partition(bolge.start_lba, 120 * MIB // 512,
                                 fs_key="ext4", label="KOK")
        fs = s.filesystem(kok.index)
        fs.mkdir("/etc")
        fs.write_file("/etc/os-release",
                      b'PRETTY_NAME="Linux Mint 22.3"\nNAME="Linux Mint"\n')
        fs.flush()
        linux_index = kok.index

        # 3) Ayni dosya sisteminde ama sistemsiz: veri bolumu
        bolge = s.free_regions()[0]
        veri = s.create_partition(bolge.start_lba, 100 * MIB // 512,
                                  fs_key="ext4", label="YEDEK")
        fs = s.filesystem(veri.index)
        fs.mkdir("/yedekler")
        fs.flush()
    else:
        veri = None

    def kayit(rapor, numara):
        """Bolum NUMARASINA gore kaydi bulur — liste konumu degil.

        `Partition.index` 1 tabanlidir ve GPT'de bosluk birakabilir; listeyi
        konumla indekslemek yanlis bolume bakmak olur.
        """
        for item in rapor.systems:
            if item.index == numara:
                return item
        raise AssertionError(f"bolum {numara} raporda yok: "
                             f"{[i.index for i in rapor.systems]}")

    rapor = bootloader.survey_session(s)
    assert rapor.readable, rapor.reason
    esp_kaydi = kayit(rapor, esp.index)
    assert esp_kaydi.is_esp, "EFI Sistem Bolumu taninmadi"
    assert rapor.esp_index == esp.index, rapor.esp_index
    assert "\\EFI\\ubuntu\\shimx64.efi" in esp_kaydi.loaders, esp_kaydi.loaders
    assert "\\EFI\\BOOT\\BOOTX64.EFI" in esp_kaydi.loaders, esp_kaydi.loaders

    if linux_index >= 0:
        kurulu = kayit(rapor, linux_index)
        assert kurulu.os_kind == "linux", kurulu.os_kind
        assert "Mint" in kurulu.os_name, kurulu.os_name
        bos = kayit(rapor, veri.index)
        assert not bos.os_name, f"veri bolumu sistem sanildi: {bos.os_name}"
        assert bos.reason, "atlanma nedeni yazilmadi"
        assert kurulu.os_name in rapor.os_names, rapor.os_names
    s.close()

    # Buyuk/kucuk harf duyarsiz arama: NTFS'te Windows, FAT'te WINDOWS
    s = DiskSession.open(yol, readonly=True)
    fs = s.filesystem(esp.index)
    assert bootloader.find_path(fs, "efi", "UBUNTU", "ShimX64.efi"), \
        "harf buyuklugunden bagimsiz arama calismiyor"
    assert not bootloader.find_path(fs, "EFI", "yok"), "olmayan yol bulundu"
    s.close()


# --------------------------------------------------------------------------
@test
def t32_grub_yapilandirmasi():
    """GRUB ayar dosyasi duzenlenirken yorumlar ve sira KORUNMALI

    Bir yapilandirma dosyasini sozluge cevirip geri yazmak kullanicinin kendi
    notlarini siler. Duzenleme satir satir yapilir.
    """
    kaynak = (
        "# GRUB varsayilanlari\n"
        "GRUB_DEFAULT=0\n"
        "GRUB_TIMEOUT=5\n"
        "\n"
        "# Diger sistemleri tara\n"
        "#GRUB_DISABLE_OS_PROBER=true\n"
        "GRUB_CMDLINE_LINUX_DEFAULT=\"quiet splash\"\n"
    )
    ayarlar = bootloader.GrubDefaults(kaynak)
    assert ayarlar.get("GRUB_TIMEOUT") == "5", ayarlar.get("GRUB_TIMEOUT")
    assert ayarlar.get("GRUB_CMDLINE_LINUX_DEFAULT") == "quiet splash"
    assert ayarlar.is_commented("GRUB_DISABLE_OS_PROBER"), \
        "yoruma alinmis anahtar etkin sanildi"

    # Yoruma alinmis anahtar **yerinde** acilmali, sona eklenmemeli
    ayarlar.set("GRUB_DISABLE_OS_PROBER", "false")
    uretilen = ayarlar.render()
    assert "GRUB_DISABLE_OS_PROBER=false" in uretilen
    assert "#GRUB_DISABLE_OS_PROBER" not in uretilen
    assert "# GRUB varsayilanlari" in uretilen, "yorum satiri kayboldu"
    assert "# Diger sistemleri tara" in uretilen, "yorum satiri kayboldu"
    satirlar = uretilen.splitlines()
    assert satirlar.index("GRUB_DEFAULT=0") < satirlar.index("GRUB_TIMEOUT=5"), \
        "sira bozuldu"
    assert satirlar.index("GRUB_DISABLE_OS_PROBER=false") \
        < satirlar.index('GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"'), \
        "anahtar yerinde acilmadi, sona eklendi"

    # Var olan anahtari degistirmek satiri yerinde guncellemeli
    ayarlar.set("GRUB_TIMEOUT", "10")
    assert ayarlar.get("GRUB_TIMEOUT") == "10"
    assert ayarlar.render().count("GRUB_TIMEOUT=") == 1, "anahtar cogaldi"

    # Hic olmayan anahtar sona eklenmeli
    ayarlar.set("GRUB_GFXMODE", "auto")
    assert ayarlar.render().rstrip().endswith("GRUB_GFXMODE=auto")

    # -- menu cozumlemesi ---------------------------------------------------
    cfg = (
        "menuentry 'Linux Mint 22.3' --class linuxmint --class gnu-linux {\n"
        "  linux /boot/vmlinuz root=UUID=x\n"
        "}\n"
        "submenu 'Gelismis secenekler' {\n"
        "  menuentry 'Linux Mint 22.3 (kurtarma kipi)' --class linuxmint {\n"
        "    linux /boot/vmlinuz single\n"
        "  }\n"
        "}\n"
        "menuentry 'Windows Boot Manager (on /dev/sda2)' --class windows {\n"
        "  chainloader /EFI/Microsoft/Boot/bootmgfw.efi\n"
        "}\n"
    )
    girisler = bootloader.parse_grub_cfg(cfg)
    basliklar = [g.title for g in girisler]
    assert len(girisler) == 3, basliklar
    # Alt menude **birden fazla** giris olmali ve `menuentry` blogunun kapanis
    # parantezi alt menuyu kapatmamali. Gercek bir `grub.cfg` uzerinde
    # olculdu: eski surum ikinci girisi (kurtarma kipi) ust duzeyde
    # gosteriyordu.
    ic_ice = (
        "submenu 'Gelismis' {\n"
        "  menuentry 'Cekirdek A' {\n"
        "    linux /boot/a\n"
        "  }\n"
        "  menuentry 'Cekirdek B (kurtarma)' {\n"
        "    linux /boot/b single\n"
        "  }\n"
        "}\n"
        "menuentry 'Disarida' {\n"
        "  chainloader +1\n"
        "}\n"
    )
    ic = bootloader.parse_grub_cfg(ic_ice)
    assert [g.submenu for g in ic] == ["Gelismis", "Gelismis", ""], \
        [(g.submenu, g.title) for g in ic]

    # `${...}` yazimi parantez sayimini bozmamali (grub.cfg bunlarla doludur)
    degiskenli = (
        "if [ \"${next_entry}\" ] ; then\n"
        "  set default=\"${next_entry}\"\n"
        "fi\n"
        "submenu 'Ust' ${menuentry_id_option} 'x' {\n"
        "  menuentry 'Icerideki' {\n"
        "    linux /boot/c\n"
        "  }\n"
        "}\n"
    )
    dg = bootloader.parse_grub_cfg(degiskenli)
    assert [(g.submenu, g.title) for g in dg] == [("Ust", "Icerideki")], \
        [(g.submenu, g.title) for g in dg]
    assert basliklar[0] == "Linux Mint 22.3", basliklar
    assert girisler[0].classes == ["linuxmint", "gnu-linux"], girisler[0].classes
    assert girisler[1].submenu == "Gelismis secenekler", girisler[1].submenu
    assert "Gelismis secenekler >" in girisler[1].display, girisler[1].display
    assert girisler[2].submenu == "", "alt menuden cikilmadi"
    assert "Windows" in basliklar[2], basliklar


# --------------------------------------------------------------------------
@test
def t33_uefi_yedegi_ve_degisiklik_plani():
    """UEFI duzeni yedeklenip geri okunmali; degisiklikler GUVENLI sirada olmali

    Yazma sirasi rastgele degildir: once girisler, sonra sira listesi, en son
    silmeler. Silmeyi basa almak `BootOrder`in var olmayan bir girisi
    gosterdigi bir an yaratir ve bazi bellenimler o anda butun sirayi
    "onarip" bozar.
    """
    def giris(numara, ad, dosya):
        return efiboot.LoadOption(
            number=numara, description=ad,
            path_nodes=[efiboot.make_hard_drive_node(
                1, 2048, 204800, "gpt",
                "11111111-2222-3333-4444-555555555555"),
                efiboot.make_file_node(dosya)])

    once = efistore.BootState(firmware="uefi", readable=True, writable=True,
                              source="firmware")
    once.entries["Boot"] = {0: giris(0, "Windows Boot Manager",
                                     "/EFI/Microsoft/Boot/bootmgfw.efi"),
                            1: giris(1, "ubuntu", "/EFI/ubuntu/shimx64.efi"),
                            2: giris(2, "Eski kurulum", "/EFI/eski/boot.efi")}
    once.orders["Boot"] = [0, 1, 2]
    once.timeout = 5
    once.boot_current = 0

    # -- yedek gidis-donus --------------------------------------------------
    hedef = os.path.join(TMP, "t33-uefi.json")
    efistore.save_backup(once, hedef)
    geri = efistore.load_backup(hedef)
    assert geri.orders["Boot"] == [0, 1, 2], geri.orders
    assert len(geri.boot_entries) == 3, geri.boot_entries
    for numara, option in once.boot_entries.items():
        assert geri.boot_entries[numara].to_bytes() == option.to_bytes(), \
            f"Boot{numara:04X} yedekten bozuk dondu"
    assert not geri.writable, "yedek dosyasi yazilabilir isaretlendi"
    assert geri.reason, "neden yazilamadigi soylenmedi"
    assert not efistore.changes(once, geri), "yedek kendinden farkli gorundu"

    # -- degisiklik plani ---------------------------------------------------
    sonra = copy.deepcopy(once)
    sonra.boot_entries[1].description = "Linux Mint"    # guncelleme
    sonra.boot_entries[1].active = False                # etkin bayragi
    sonra.boot_entries.pop(2)                           # silme
    sonra.orders["Boot"] = [1, 0]                       # sira
    sonra.timeout = 10
    yeni = sonra.free_number("Boot")
    assert yeni == 2, yeni                              # bosalan numara
    sonra.boot_entries[yeni] = giris(2, "Kurtarma", "/EFI/kurtarma/boot.efi")
    sonra.orders["Boot"] = [1, 0, 2]

    plan = efistore.changes(once, sonra)
    adlar = [c.name for c in plan]
    eylemler = {c.name: c.action for c in plan}
    assert "Boot0001" in adlar and eylemler["Boot0001"] == "write", adlar
    assert "BootOrder" in adlar, adlar
    assert "Timeout" in adlar, adlar
    assert "Boot0000" not in adlar, "degismeyen giris plana girdi"

    # Boot0002 hem silinip hem yazildi: sondaki durum yazmadir, silme
    # olmamalidir (numara yeniden kullanildi).
    assert eylemler["Boot0002"] == "write", eylemler
    assert adlar.index("Boot0001") < adlar.index("BootOrder"), \
        "sira listesi girislerden ONCE yaziliyor"
    assert plan[-1].name in ("BootOrder", "Timeout", "Boot0002") or \
        plan[-1].action == "delete", [c.name for c in plan]

    # Silmenin gercekten sona kaldigini ayri bir durumda olc
    silinen = copy.deepcopy(once)
    silinen.boot_entries.pop(2)
    silinen.orders["Boot"] = [0, 1]
    plan2 = efistore.changes(once, silinen)
    adlar2 = [c.name for c in plan2]
    assert adlar2 == ["BootOrder", "Boot0002"], adlar2
    assert plan2[-1].action == "delete", plan2[-1].action
    assert plan2[0].destructive, "sira degisikligi yikici sayilmadi"
    assert plan2[-1].destructive, "silme yikici sayilmadi"

    # -- sirada olmayan giris gizlenmemeli ---------------------------------
    oksuz = copy.deepcopy(once)
    oksuz.orders["Boot"] = [0, 9]           # 9 yok, 1 ve 2 sirada degil
    sirali = [o.number for o in oksuz.ordered("Boot")]
    assert sirali == [0, 1, 2], sirali
    assert oksuz.orphans("Boot") == [9], oksuz.orphans("Boot")


# --------------------------------------------------------------------------
@test
def t34_bellenime_yazma_kapisi():
    """Bellenim yazilamiyorken plan UYGULANMAMALI ve nedeni soylenmeli

    Bu test **gercek bellenime** dokunan tek testtir ve yalnizca yazmanin
    zaten kapali oldugu makinede kosar (BIOS kipi, yetki yok ya da desteksiz
    platform). Yazmanin acik oldugu bir makinede atlanir: bir testin
    makinenin onyukleme duzenini degistirmesi kabul edilemez.
    """
    readable, writable, reason = platform_mod.efivars_state()
    if writable:
        raise Atlandi("bellenim yazilabilir — gercek degiskenlere dokunulmaz")
    assert reason, "erisilemeyen bellenim icin neden yazilmadi"
    rapor = efistore.apply([efistore.VariableChange("Boot0000", "write",
                                                    b"\x00" * 8, 7)])
    assert not rapor.ok, "yazilamayan bellenime yazildi sanildi"
    assert rapor.error, "neden yazilamadigi soylenmedi"
    assert rapor.pending, "uygulanmayan adimlar bildirilmedi"

    # Okunamayan bir duzen bos degil, **aciklamali** donmeli
    durum = efistore.load()
    assert durum.reason or durum.readable, \
        "okunamayan duzen icin aciklama yok"
    if not durum.readable:
        assert not durum.boot_entries, "okunamayan duzende giris uretildi"


# --------------------------------------------------------------------------
@test
def t35_ntfs_boyutlandirma():
    """NTFS saf Python boyutlandirma: kucultme, buyutme, veri korunur

    Bu yetenek Linux/macOS icin **yeniydi**: NTFS yalnizca Windows'un kendi
    `Resize-Partition` komutuyla boyutlandirilabiliyordu, goruntu
    dosyalarinda ise hic calismiyordu (ADR 0030).

    Dogrulananlar:
      1. Sinirlar `$Bitmap`'ten gelir; dolu veriden kucuge inilemez.
      2. Kucultmeden sonra birim **acilir ve dosyalar okunur**.
      3. Buyutmeden sonra bos alan gercekten kullanilabilir (yeni dosya).
      4. Onyukleme sektoru ve yedegi yeni boyutla tutarli.
      5. `ntfsfix`/`ntfsinfo` varsa birim onlarin gozunde de saglam.
    """
    from diskultimate.core.ntfsresize import (NtfsResizeError, ntfs_resize,
                                              ntfs_size_info)

    yol = img_path("t35.img")
    d = DiskImage.create(yol, 256 * MIB, overwrite=True)
    bas, toplam = 2048, d.sector_count - 2048
    format_ntfs(PartitionView(d, bas, toplam), label="BOYUT")

    # --- icerik: kucultmeden sonra da bulunmali ---
    erisim = open_filesystem(PartitionView(d, bas, toplam),
                             detect(PartitionView(d, bas, toplam)))
    veri = b"NTFS boyutlandirma denemesi\n" * 64
    erisim.write_file("/deneme.txt", veri)
    erisim.mkdir("/klasor")
    erisim.flush()

    bilgi = ntfs_size_info(PartitionView(d, bas, toplam))
    assert bilgi.cluster_count > 0 and bilgi.used_clusters > 0, bilgi
    assert bilgi.min_sectors < toplam, bilgi.min_sectors
    assert not bilgi.dirty, "yeni bicimlendirilmis birim kirli olmamali"

    # 1) Dolu veriden kucuge inilemez
    try:
        ntfs_resize(PartitionView(d, bas, toplam), 64)
        raise AssertionError("cok kucuk boyut kabul edildi")
    except NtfsResizeError:
        pass

    # 2) Kucultme: 256 MB -> 96 MB
    kucuk = 96 * MIB // 512
    ntfs_resize(PartitionView(d, bas, toplam), kucuk)
    gorunum = PartitionView(d, bas, kucuk)
    fs = NtfsFS(gorunum)
    assert fs.total_sectors == kucuk - 1, (fs.total_sectors, kucuk)
    yedek = d.read((bas + kucuk - 1) * 512, 512)
    assert yedek[3:11] == b"NTFS    ", "yedek onyukleme sektoru tasinmadi"
    erisim = open_filesystem(gorunum, detect(gorunum))
    assert erisim.read("/deneme.txt") == veri, "kucultmede veri bozuldu"
    assert {"deneme.txt", "klasor"} <= {n.name for n in erisim.listdir("/")}

    # Kume haritasi yeni boyutla tutarli olmali
    bilgi = ntfs_size_info(gorunum)
    assert bilgi.cluster_count == (kucuk - 1) * 512 // bilgi.cluster_size
    assert bilgi.highest_used < bilgi.cluster_count, (
        "sinir otesinde tahsisli kume kaldi")

    # 3) Buyutme: 96 MB -> 200 MB, sonra yeni dosya yazilabilmeli
    buyuk = 200 * MIB // 512
    ntfs_resize(PartitionView(d, bas, buyuk), buyuk)
    gorunum = PartitionView(d, bas, buyuk)
    bilgi = ntfs_size_info(gorunum)
    assert bilgi.cluster_count == (buyuk - 1) * 512 // bilgi.cluster_size
    erisim = open_filesystem(gorunum, detect(gorunum))
    assert erisim.read("/deneme.txt") == veri, "buyutmede veri bozuldu"
    buyuk_veri = os.urandom(3 * MIB)
    erisim.write_file("/buyuk.bin", buyuk_veri)
    erisim.flush()
    erisim = open_filesystem(PartitionView(d, bas, buyuk),
                             detect(PartitionView(d, bas, buyuk)))
    assert erisim.read("/buyuk.bin") == buyuk_veri, "buyuyen alana yazilamadi"
    d.close()

    # 4) Harici araclar (varsa) birimi saglam gormeli
    parca = yol + ".part"
    with open(yol, "rb") as kaynak, open(parca, "wb") as hedef:
        kaynak.seek(bas * 512)
        kalan = buyuk * 512
        while kalan > 0:
            blok = kaynak.read(min(4 * MIB, kalan))
            if not blok:
                break
            hedef.write(blok)
            kalan -= len(blok)
    try:
        for arac, bayraklar in (("ntfsinfo", ["-m"]), ("ntfsfix", ["-n"])):
            konum = shutil.which(arac)
            if not konum:
                continue
            r = subprocess.run([konum] + bayraklar + [parca],
                               capture_output=True, text=True)
            assert r.returncode == 0, f"{arac} hata: {r.stdout}{r.stderr}"
    finally:
        os.unlink(parca)


# --------------------------------------------------------------------------
@test
def t36_ntfs_bolum_boyutlandirma():
    """Oturum uzerinden NTFS bolumu kucultme/buyutme (her platformda)

    `t35` dosya sistemini dogrudan boyutlandirir; burada **bolum tablosuyla
    birlikte** yol denenir: sinirlar `resize_info` ile bildirilir, plan
    dogrulanir ve `resize_partition` tabloyu da gunceller.
    """
    yol = img_path("t36.img")
    s = DiskSession.create(yol, 400 * MIB, overwrite=True)
    s.create_table("gpt")
    sektor = s.image.sector_size
    s.create_partition(2048, 300 * MIB // sektor, fs_key="ntfs", label="NT")
    s.reload()
    assert s.partitions[0].fs_type == "NTFS", s.partitions[0].fs_type

    fs = s.filesystem(1)
    icerik = b"bolum boyutlandirma\n" * 256
    fs.write_file("/veri.txt", icerik)
    fs.flush()
    s.close_filesystems()

    bilgi = s.resize_info(1)
    assert bilgi.kind == "ntfs", f"NTFS saf Python yolu secilmedi: {bilgi.kind}"
    assert bilgi.resizable and bilgi.min_sectors > 1, bilgi

    hedef = 150 * MIB // sektor
    plan = s.plan_resize(1, 2048, hedef)
    assert plan.shrinks and not plan.moves, plan.summary()
    s.resize_partition(1, 2048, hedef, confirm=True)
    s.reload()
    assert s.partitions[0].sector_count == hedef, s.partitions[0].sector_count
    assert s.partitions[0].fs_type == "NTFS", s.partitions[0].fs_type
    assert s.filesystem(1).read("/veri.txt") == icerik, "kucultmede veri gitti"
    s.close_filesystems()

    buyuk = 350 * MIB // sektor
    s.resize_partition(1, 2048, buyuk, confirm=True)
    s.reload()
    assert s.partitions[0].sector_count == buyuk
    assert s.filesystem(1).read("/veri.txt") == icerik, "buyutmede veri gitti"
    s.close()


# --------------------------------------------------------------------------
@test
def t37_plan_onizlemesi():
    """Bekleyen adimlar ana ekranda gosterilecek yerlesimi uretmeli

    Kuyruk diske dokunmaz; ama kullanici "Uygula" demeden sonucu gormelidir
    (ADR 0031). `planview.project` o sonucu **bellekte** uretir ve gercek
    bolum tablosuna dokunmaz.
    """
    from diskultimate.core import planview

    yol = img_path("t37.img")
    s = DiskSession.create(yol, 256 * MIB, overwrite=True)
    s.create_table("gpt")
    sektor = s.image.sector_size
    s.create_partition(2048, 64 * MIB // sektor, fs_key="fat32", label="BIR")
    s.reload()
    once = [(p.index, p.start_lba, p.sector_count) for p in s.partitions]

    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.create_op(2048 + 64 * MIB // sektor, 64 * MIB // sektor,
                             sektor, fs_key="ntfs", label="IKI"))
    kuyruk.add(ops.format_op(1, "exfat", label="DEGISTI", fs_name="exFAT"))

    plan = planview.project(s, kuyruk)
    assert len(plan.partitions) == 2, plan.partitions
    yeni = [p for p in plan.partitions if p.plan_state == planview.STATE_NEW]
    assert len(yeni) == 1 and yeni[0].fs_type == "NTFS", yeni
    eski = [p for p in plan.partitions if p.index == 1][0]
    assert eski.plan_state == planview.STATE_FORMAT, eski.plan_state
    assert eski.fs_type == "exFAT" and eski.fs_label == "DEGISTI", eski
    assert plan.notes.get(1), "bicimlendirme adimi nota yazilmadi"

    # Onizleme gercek tabloya DOKUNMAMALI
    assert [(p.index, p.start_lba, p.sector_count) for p in s.partitions] == once
    assert all(p.plan_state == "" for p in s.partitions), "gercek bolum isaretlendi"

    # Bos alan yeniden hesaplanmali: iki bolumden sonra kalan yer
    assert plan.free, "planlanan yerlesimde bos alan bulunamadi"
    assert plan.free[0].start_lba >= 2048 + 128 * MIB // sektor, plan.free[0]

    # Silme adimi bolumu haritadan kaldirir
    kuyruk2 = ops.OperationQueue()
    kuyruk2.add(ops.delete_op(1, "BIR"))
    plan2 = planview.project(s, kuyruk2)
    assert not plan2.partitions, plan2.partitions
    assert len(s.partitions) == 1, "silme adimi gercek tabloyu degistirdi"

    # Sema donusumu onizlemede de gorunmeli
    kuyruk3 = ops.OperationQueue()
    kuyruk3.add(ops.convert_table_op("mbr"))
    plan3 = planview.project(s, kuyruk3)
    assert plan3.scheme == "mbr", plan3.scheme
    assert s.scheme == "gpt", "donusum adimi gercek semayi degistirdi"
    s.close()


# --------------------------------------------------------------------------
@test
def t38_uygulama_adim_geri_cagrilari():
    """Uygulama penceresi icin adim basina ilerleme bildirilmeli

    Pencere her adimin kendi cubugunu cizer (ADR 0031); bunun icin cekirdek
    adim basina **ayri** ilerleme ve bitis bildirir. Genel cubuk geri
    gitmemelidir: adimin kendi yuzdesi genel olcege sigdirilir.
    """
    yol = img_path("t38.img")
    s = DiskSession.create(yol, 256 * MIB, overwrite=True)
    s.close()
    s = DiskSession.open(yol)
    sektor = s.image.sector_size

    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.create_table_op("gpt"))
    kuyruk.add(ops.create_op(2048, 64 * MIB // sektor, sektor,
                             fs_key="fat32", label="BIR"))
    basladi, bitti, adim_yuzdeleri, genel = [], [], [], []
    sonuc = kuyruk.apply(
        s,
        progress=lambda m, p: genel.append(p),
        on_step=lambda i, op: basladi.append(i),
        on_step_progress=lambda i, op, m, p: adim_yuzdeleri.append((i, p)),
        on_step_done=lambda i, op, err: bitti.append((i, err)))
    assert sonuc.ok, sonuc.summary()
    assert basladi == [0, 1], basladi
    assert [i for i, _err in bitti] == [0, 1], bitti
    assert all(not err for _i, err in bitti), bitti
    assert adim_yuzdeleri, "adim basina ilerleme hic bildirilmedi"
    pozitif = [p for p in genel if p >= 0]
    assert pozitif == sorted(pozitif), f"genel ilerleme geri gitti: {genel}"
    assert max(pozitif) <= 100 and pozitif[-1] == 100, genel

    # Basarisiz adim da bildirilmeli
    kuyruk2 = ops.OperationQueue()
    kuyruk2.add(ops.format_op(99, "fat32"))
    hatalar = []
    kuyruk2.apply(s, on_step_done=lambda i, op, err: hatalar.append(err))
    assert hatalar and hatalar[0], "basarisiz adim bildirilmedi"
    s.close()


# --------------------------------------------------------------------------
@test
def t39_yedek_notu_ve_sikistirma():
    """Yedek dosyasi kullanici notu tasimali; sikistirma duzeyi secilebilmeli

    Not (`remark`) baslikta durur: yedegi acan herkes onu gorur, yanina ayri
    bir metin dosyasi tasimak gerekmez (ADR 0032).

    Dogrulananlar:
      1. Not yazilir ve geri okunur; bos not "-" degil bos dizedir.
      2. Not **sonradan** degistirilebilir ve bu veriye dokunmaz: notu
         degistirilmis yedekten geri yukleme bire bir ayni veriyi verir.
      3. Not bayt siniriyla kirpilir ve **cok baytli karakter ortadan
         bolunmez** (Turkce harfler UTF-8'de iki bayt tutar).
      4. Sikistirma duzeyi dosya boyutunu degistirir; "yok" secildiginde
         baslik sikistirmasiz isaretlenir.
    """
    from diskultimate.core import clone as clone_mod

    yol = img_path("t39.img")
    d = DiskImage.create(yol, 32 * MIB, overwrite=True)
    # Sikistirilabilir ama tumuyle tekduze olmayan icerik
    for i in range(8):
        d.write(i * MIB, (b"DiskUltimate yedek notu denemesi " * 1024)[:MIB])
    ozet = hashlib.sha256(d.read(0, 8 * MIB)).hexdigest()
    d.close()

    # 1) Not yazilir ve okunur
    dub = img_path("t39-normal.dub")
    kaynak = DiskImage(yol, readonly=True)
    not_metni = "Haftalik yedek - 2026 sunucu"
    bilgi = clone_mod.backup(kaynak, dub, remark=not_metni,
                             level=clone_mod.LEVEL_NORMAL)
    kaynak.close()
    assert bilgi.remark == not_metni, bilgi.remark
    assert clone_mod.read_backup_info(dub).remark == not_metni
    assert "Not" in " ".join(bilgi.summary().keys()), bilgi.summary()

    # 2) Not sonradan degistirilir; veri bozulmaz
    clone_mod.write_remark(dub, "Duzeltilmis not")
    assert clone_mod.read_backup_info(dub).remark == "Duzeltilmis not"
    geri = img_path("t39-geri.img")
    clone_mod.restore(dub, DiskImage.create(geri, 32 * MIB, overwrite=True))
    okunan = DiskImage(geri, readonly=True)
    assert hashlib.sha256(okunan.read(0, 8 * MIB)).hexdigest() == ozet, \
        "not degistirilince veri bozuldu"
    okunan.close()

    # 3) Bayt siniri: cok baytli karakter ortadan bolunmez
    uzun = "ç" * 400                      # her biri UTF-8'de 2 bayt
    yazilan = clone_mod.write_remark(dub, uzun)
    ham = len(yazilan.encode("utf-8"))
    assert ham <= clone_mod.REMARK_SIZE, ham
    assert ham >= clone_mod.REMARK_SIZE - 1, ham
    assert yazilan == "ç" * (ham // 2), "cok baytli karakter bolunmus"
    assert clone_mod.read_backup_info(dub).remark == yazilan

    # 4) Sikistirma duzeyleri
    boyutlar = {}
    for ad, duzey in (("none", clone_mod.LEVEL_NONE),
                      ("fast", clone_mod.LEVEL_FAST),
                      ("high", clone_mod.LEVEL_HIGH)):
        hedef = img_path(f"t39-{ad}.dub")
        kaynak = DiskImage(yol, readonly=True)
        sonuc = clone_mod.backup(kaynak, hedef, level=duzey, remark=ad)
        kaynak.close()
        boyutlar[ad] = sonuc.file_size
        assert sonuc.compressed == (duzey > clone_mod.LEVEL_NONE), ad
        assert sonuc.remark == ad
    assert boyutlar["none"] > boyutlar["fast"] > 0, boyutlar
    assert boyutlar["high"] <= boyutlar["fast"], boyutlar

    # Eski (notsuz) yedekler de okunmali: not alani sifir ise bos dize
    with open(dub, "r+b") as fh:
        fh.seek(clone_mod.REMARK_OFFSET)
        fh.write(b"\x00" * clone_mod.REMARK_SIZE)
    assert clone_mod.read_backup_info(dub).remark == "", "bos not bos gelmeli"


# --------------------------------------------------------------------------
@test
def t40_kuyrukta_bolum_numarasi_kaymasi():
    """Adimlar bolum numarasi kaysa bile DOGRU bolumu bulmali

    Gercek bir hata: Linux misafirinde iki bolum silme adimi kuyruga alinip
    uygulandiginda ilk adim gecti, ikincisi *"2 numarali bolum yok"* diye
    durdu. Neden: bolum numarasi kalici bir kimlik degildir — tablo yeniden
    okununca numaralar yerlesime gore bastan verilir, yani 1 numarali bolum
    silinince 2 numarali bolum 1 olur (ADR 0033).

    Cozum: adim hedefini **baslangic LBA'si** ile de tasir; numara uygulama
    aninda yeniden bulunur.

    Dogrulananlar:
      1. "Bolum 1 sil" + "Bolum 2 sil" ikisi de uygulanir (asil hata).
      2. Silme sirasi karisik verilse de dogru bolumler gider.
      3. Bicimlendirme/etiket adimlari, araya bir silme girse bile **kendi**
         bolumlerine uygulanir.
      4. Hedef bolum artik yoksa adim durur ve nedeni acikca soylenir.
      5. Capasiz (eski) adimlar eskisi gibi numaradan calisir.
    """
    from diskultimate.core import operations as ops

    def yeni_disk(ad, adet=3, sema="gpt"):
        yol = img_path(ad)
        s = DiskSession.create(yol, 256 * MIB, overwrite=True)
        s.create_table(sema)
        sektor = s.image.sector_size
        bas = 2048
        for i in range(adet):
            s.create_partition(bas, 48 * MIB // sektor, fs_key="fat32",
                               label=f"BOLUM{i + 1}")
            bas += 48 * MIB // sektor
        s.reload()
        return s

    # 1) Asil hata: iki silme adimi arka arkaya
    s = yeni_disk("t40a.img", adet=2)
    lba = {p.index: p.start_lba for p in s.partitions}
    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.delete_op(1, at_lba=lba[1]))
    kuyruk.add(ops.delete_op(2, at_lba=lba[2]))
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, f"numara kaymasi hala kiriyor: {sonuc.summary()}"
    s.reload()
    assert not s.partitions, [p.index for p in s.partitions]
    s.close()

    # 2) Ters sirada silme de dogru bolumleri gotursun
    s = yeni_disk("t40b.img", adet=3)
    lba = {p.index: p.start_lba for p in s.partitions}
    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.delete_op(2, at_lba=lba[2]))
    kuyruk.add(ops.delete_op(1, at_lba=lba[1]))
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, sonuc.summary()
    s.reload()
    kalan = [p.start_lba for p in s.partitions]
    assert kalan == [lba[3]], f"yanlis bolum silindi: {kalan}"
    s.close()

    # 3) Silme ile etiketleme karisik: etiket DOGRU bolume gitmeli
    s = yeni_disk("t40c.img", adet=3)
    lba = {p.index: p.start_lba for p in s.partitions}
    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.delete_op(1, at_lba=lba[1]))
    kuyruk.add(ops.label_op(3, "UCUNCU", at_lba=lba[3]))
    kuyruk.add(ops.format_op(2, "exfat", label="IKINCI", at_lba=lba[2]))
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, sonuc.summary()
    s.reload()
    yerlesim = {p.start_lba: (p.fs_type, p.fs_label) for p in s.partitions}
    assert yerlesim[lba[2]][0].lower().startswith("exfat"), yerlesim
    assert yerlesim[lba[2]][1] == "IKINCI", yerlesim
    assert yerlesim[lba[3]][1] == "UCUNCU", \
        f"etiket yanlis bolume yazildi: {yerlesim}"
    s.close()

    # 4) Hedef bolum yoksa adim durmali ve nedeni soylenmeli
    s = yeni_disk("t40d.img", adet=2)
    lba = {p.index: p.start_lba for p in s.partitions}
    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.delete_op(1, at_lba=lba[1]))
    kuyruk.add(ops.label_op(1, "OLMAZ", at_lba=lba[1]))   # ayni bolum, artik yok
    sonuc = kuyruk.apply(s)
    assert not sonuc.ok, "silinmis bolume etiket yazildi"
    assert "LBA" in sonuc.error, sonuc.error
    assert len(sonuc.done) == 1, sonuc.summary()
    s.close()

    # 5) Capasiz adim (eski kuyruk) numaradan calismaya devam etmeli
    s = yeni_disk("t40e.img", adet=2)
    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.label_op(2, "ESKIYOL"))
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, sonuc.summary()
    s.close_filesystems()
    s.reload()
    assert s.filesystem(2).label == "ESKIYOL"
    s.close()

    # 6) Onizleme de ayni bolumu secmeli (ekranla uygulama ayni seyi demeli)
    from diskultimate.core import planview

    s = yeni_disk("t40f.img", adet=3)
    lba = {p.index: p.start_lba for p in s.partitions}
    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.delete_op(1, at_lba=lba[1]))
    kuyruk.add(ops.format_op(3, "ntfs", label="UC", at_lba=lba[3]))
    plan = planview.project(s, kuyruk)
    kalanlar = {p.start_lba: p for p in plan.partitions}
    assert lba[1] not in kalanlar, "silinen bolum onizlemede duruyor"
    assert kalanlar[lba[3]].fs_type == "NTFS", \
        f"onizleme yanlis bolumu isaretledi: {kalanlar[lba[3]].fs_type}"
    assert kalanlar[lba[2]].plan_state == "", "dokunulmayan bolum isaretlenmis"
    s.close()


# --------------------------------------------------------------------------
@test
def t41_ust_uste_bolum_planlama():
    """Arka arkaya kuyruga alinan bolumler ayni bos alani PAYLASMALI

    Gercek hata (Linux misafiri, /dev/sdb): kullanici uc "yeni bolum" adimini
    arka arkaya kuyruga aldi; ucunun de hedefi **ayni LBA** oldu (14626816) ve
    Uygula sirasinda ikinci adim *"2 numarali bolum ile cakisiyor"* diye durdu.

    Neden: bos alan diskteki duruma gore soruluyordu. Kuyruktaki bolum diske
    yazilmadigi icin o alan hala "bos" gorunuyordu (ADR 0034).

    Dogrulananlar:
      1. Diske gore sorulan bos alan **ayni** kalir — hatanin kok nedeni.
      2. Plana gore sorulan bos alan her adimdan sonra kuculur.
      3. Plana gore kurulan uc adim tek Uygula ile **sorunsuz** uygulanir.
      4. `overlap_at` planlanan bolumle cakismayi tiklama aninda yakalar.
    """
    from diskultimate.core import operations as ops
    from diskultimate.core import planview

    yol = img_path("t41.img")
    s = DiskSession.create(yol, 512 * MIB, overwrite=True)
    s.create_table("gpt")
    sektor = s.image.sector_size
    s.create_partition(2048, 128 * MIB // sektor, fs_key="fat32", label="VAR")
    s.reload()

    boyutlar = [64 * MIB // sektor, 96 * MIB // sektor, 128 * MIB // sektor]

    # 1) Kok neden: diske gore sorulan bos alan degismiyor
    kuyruk = ops.OperationQueue()
    diske_gore = []
    for adet in boyutlar:
        bolge = max(s.free_regions(), key=lambda r: r.sector_count)
        diske_gore.append(bolge.start_lba)
        kuyruk.add(ops.create_op(bolge.start_lba, adet, sektor, fs_key="fat32"))
    assert len(set(diske_gore)) == 1, \
        f"kok neden degismis olmali: {diske_gore}"

    # Bu kuyruk gercekten kirikti: ikinci adim cakismadan durur
    sonuc = kuyruk.apply(s)
    assert not sonuc.ok, "ust uste planlanan bolumler sessizce uygulanmamali"
    assert "cakis" in sonuc.error.lower(), sonuc.error
    s.reload()
    # Ilk adim uygulandi; temizleyip asil senaryoya gecilir
    for part in list(s.partitions):
        if part.start_lba != 2048:
            s.delete_partition(part.index)
    s.reload()
    assert len(s.partitions) == 1, s.partitions

    # 2 + 3) Plana gore sorulunca alan her adimda kuculur ve hepsi uygulanir
    kuyruk = ops.OperationQueue()
    plana_gore = []
    for adet in boyutlar:
        plan = planview.project(s, kuyruk)
        bolgeler = plan.free if plan is not None else s.free_regions()
        bolge = max(bolgeler, key=lambda r: r.sector_count)
        plana_gore.append(bolge.start_lba)
        kuyruk.add(ops.create_op(bolge.start_lba, adet, sektor, fs_key="fat32"))
    assert len(set(plana_gore)) == 3, f"alan kuculmemis: {plana_gore}"
    assert plana_gore == sorted(plana_gore), plana_gore

    sonuc = kuyruk.apply(s)
    assert sonuc.ok, f"plana gore kurulan adimlar da kirildi: {sonuc.summary()}"
    s.reload()
    assert len(s.partitions) == 4, [p.index for p in s.partitions]
    # Cakisma olmamali: her bolum bir oncekinin bitiminden sonra baslar
    sirali = sorted(s.partitions, key=lambda p: p.start_lba)
    for onceki, sonraki in zip(sirali, sirali[1:]):
        assert sonraki.start_lba > onceki.end_lba, \
            f"bolumler cakisti: {onceki.index} - {sonraki.index}"

    # 4) Cakisma denetimi: planlanan bolumun uzerine yeni adim kurulamaz
    s2 = DiskSession.create(img_path("t41b.img"), 256 * MIB, overwrite=True)
    s2.create_table("gpt")
    kuyruk2 = ops.OperationQueue()
    kuyruk2.add(ops.create_op(2048, 64 * MIB // sektor, sektor, fs_key="fat32"))
    plan = planview.project(s2, kuyruk2)
    carpan = planview.overlap_at(plan, 2048 + 16 * MIB // sektor,
                                 32 * MIB // sektor)
    assert carpan is not None, "planlanan bolumle cakisma yakalanmadi"
    uzak = planview.overlap_at(plan, 2048 + 128 * MIB // sektor,
                               32 * MIB // sektor)
    assert uzak is None, "cakismayan alan cakisti sanildi"
    # Kendi kendini kesiyor sayilmamali (boyutlandirmada bolumun kendisi)
    kendi = planview.overlap_at(plan, 2048, 96 * MIB // sektor, ignore_lba=2048)
    assert kendi is None, "bolum kendisiyle cakisti sanildi"
    s2.close()
    s.close()


# --------------------------------------------------------------------------
@test
def t42_ntfs_isletim_sistemi_yazabilmeli():
    """Bicimlendirdigimiz NTFS'e isletim sisteminin surucusu YAZABILMELI

    Gercek hata (Linux misafiri): uygulamayla NTFS bicimlendirilen bolum
    baglaniyor ve okunuyordu ama `ntfs-3g` uzerine dosya olusturamiyordu.
    Uc eksik vardi (ADR 0037):

      1. `$MFT:$BITMAP` **yerlesikti**; surucu yeni MFT kaydi ayirirken
         bitmap'in son kumesini soruyor, yerlesik oznitelikte boyle bir sey
         olmadigi icin duruyordu ("Failed to determine last allocated
         cluster of mft bitmap attribute" -> EINVAL).
      2. Kok dizinde **"." girisi** yoktu; surucu dosya olusturduktan sonra
         ust dizinin adini kendi indeksinde arayip tazeliyor, bulamayinca
         islem G/C hatasiyla dusuyordu ("Index lookup failed, inode 5").
      3. `$Secure` **bostu**; NTFS 3.x'te her dosyaya guvenlik kimligi
         atanir ve tanimlayicilar bu dosyada durur.

    Burada yapinin **kendisi** sinanir (root gerekmez). Gercek surucuyle
    yazma denemesi `tests/ntfs_write_check.py` icindedir ve root ister.
    """
    import struct as _struct
    from diskultimate.core.ntfs import (SECURITY_ID_FULL, SECURITY_ID_SYSTEM,
                                        security_descriptor, security_hash)

    yol = img_path("t42.img")
    d = DiskImage.create(yol, 200 * MIB, overwrite=True)
    format_ntfs(d, label="SURUCU")
    d.close()

    fs = NtfsFS(DiskImage(yol, readonly=True))

    # 1) $MFT:$BITMAP yerlesik OLMAMALI ve kendi kumesi olmali
    bitmap = fs.record(0).find(0xB0)
    assert bitmap is not None, "$MFT'nin $BITMAP ozniteligi yok"
    assert not bitmap.resident, \
        "$MFT:$BITMAP yerlesik — surucu yeni kayit ayiramaz (ADR 0037)"
    assert bitmap.runs and bitmap.runs[0][0] > 0, bitmap.runs
    kullanim = fs.read_attribute(bitmap)
    assert len(kullanim) >= 8 and len(kullanim) % 8 == 0, len(kullanim)
    dolu = sum(bin(b).count("1") for b in kullanim)
    kapasite = fs.mft_size // fs.record_size
    assert 0 < dolu < kapasite, \
        f"MFT bitmap'inde bos kayit kalmamis: {dolu}/{kapasite}"

    # 2) Kok dizin kendi girisini ("." -> kayit 5) tasimali.
    #    Okuyucumuz "." girisini listelemez; indeks agaci dogrudan gezilir
    #    (kok 1 KiB kayitta INDX'li buyuk dizindir, ADR 0058).
    from diskultimate.core.ntfsindex import DirIndex
    from diskultimate.core.ntfswrite import NtfsWriter as _W
    adlar = [(e.name, e.ref & 0xFFFFFFFFFFFF)
             for e in DirIndex(_W(fs), 5).all_entries()]
    assert (".", 5) in adlar, f"kok dizinde '.' girisi yok: {adlar}"
    assert ("$MFT", 0) in adlar and ("$Secure", 9) in adlar, adlar
    # Kullaniciya gosterilen listede "." gorunmemeli
    assert all(e.name != "." for e in fs.listdir("/")), "'.' listelenmis"

    # 3) $Secure gercek icerik tasimali
    secure = fs.record(9)
    sds = fs.read_attribute(secure.find(0x80, "$SDS"))
    # Iki mkntfs tanimlayicisi + kok tanimlayicisi (0x102, ADR 0058)
    kullanilan = len(sds) - 0x40000
    assert kullanilan > 0xFC, f"$SDS boyutu {len(sds):#x}"
    assert sds[:kullanilan] == sds[0x40000:], "$SDS aynasi tutmuyor"
    kimlikler = {}
    pos = 0
    while pos + 20 <= kullanilan:
        karma, kimlik, ofset, boy = _struct.unpack_from("<IIQI", sds, pos)
        if boy == 0:
            break
        tanim = sds[pos + 20:pos + boy]
        assert security_hash(tanim) == karma, f"karma tutmuyor: {kimlik:#x}"
        assert ofset == pos, (ofset, pos)
        kimlikler[kimlik] = tanim
        pos = (pos + boy + 15) & ~15
    from diskultimate.core.ntfs import SECURITY_ID_ROOT, root_security_descriptor
    assert set(kimlikler) == {SECURITY_ID_SYSTEM, SECURITY_ID_FULL,
                              SECURITY_ID_ROOT}, list(map(hex, kimlikler))
    assert kimlikler[SECURITY_ID_ROOT] == root_security_descriptor()
    assert kimlikler[SECURITY_ID_SYSTEM] == security_descriptor(0x00120089)
    assert kimlikler[SECURITY_ID_FULL] == security_descriptor(0x0012019F)

    # Iki indeks de dolu olmali (bos indeks = surucu kimlik bulamaz)
    for ad, anahtar_boyu in (("$SDH", 8), ("$SII", 4)):
        indeks = secure.find(0x90, ad)
        assert indeks is not None and indeks.resident, ad
        veri = indeks.value
        bas, boy = _struct.unpack_from("<II", veri, 0x10)
        pos, sayi = 0x10 + bas, 0
        while pos + 0x10 <= 0x10 + boy:
            _ofset, _uzunluk, _ayrilmis, giris_boyu, anahtar, bayrak = \
                _struct.unpack_from("<HHIHHH", veri, pos)[:6]
            if giris_boyu < 0x10 or bayrak & 0x02:
                break
            assert anahtar == anahtar_boyu, (ad, anahtar)
            sayi += 1
            pos += giris_boyu
        assert sayi == 3, f"{ad} icinde {sayi} giris var, 3 bekleniyor"

    # Sistem dosyalari gecerli bir kimlik gostermeli
    std = fs.record(0).find(0x10)
    assert len(std.value) >= 0x48, "standart bilgi NTFS 3.x bicimi degil"
    assert _struct.unpack_from("<I", std.value, 0x34)[0] == SECURITY_ID_SYSTEM

    # 4) Kendi okuyucumuz ve yazicimiz bozulmamis olmali
    erisim = open_filesystem(DiskImage(yol, readonly=False), detect(fs.dev))
    erisim.write_file("/kendi.txt", b"kendi yazicimiz\n")
    assert erisim.read("/kendi.txt") == b"kendi yazicimiz\n"
    erisim.flush()
    fs.dev.close()


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
@test
def t43_baglama_guvenlik_katmani():
    """Baglama: aygit yolu turetimi, platform etiketleri, kritik nokta korumasi

    Gercek bir baglama YAPILMAZ (root ve gercek aygit ister). Sinanan sey
    karar mantigidir: hangi aygit yolu uretiliyor, hangi noktalar
    "calisan sistem" sayilip cikarilmasi reddediliyor (ADR 0043).
    """
    from diskultimate.core import physical as ph
    from diskultimate.core import platform as pf

    # -- aygit yolu: nvme/mmcblk 'p' ekler, sd/hd eklemez --
    # Windows'ta bolum aygit yolu kavrami yoktur (bos doner); macOS "s" ekler.
    # Bu test ilk yazildiginda yalnizca Linux'ta kosmustu ve Windows'ta
    # yanlis beklentiyle basarisiz oluyordu.
    if pf.IS_WINDOWS:
        assert pf.partition_device("/dev/sdb", 3) == ""
    elif pf.IS_MACOS:
        assert pf.partition_device("/dev/disk2", 3) == "/dev/disk2s3"
    else:
        assert pf.partition_device("/dev/sdb", 3) == "/dev/sdb3"
        assert pf.partition_device("/dev/nvme0n1", 2) == "/dev/nvme0n1p2"
        assert pf.partition_device("/dev/mmcblk0", 1) == "/dev/mmcblk0p1"
    assert pf.partition_device("/dev/sda", 0) == "", "0 gecerli bolum degil"

    # -- etiketler platformun kavramiyla --
    mount_text, unmount_text = pf.mount_action_labels()
    assert mount_text and unmount_text and mount_text != unmount_text
    if pf.IS_WINDOWS:
        assert "harf" in mount_text.lower() or "letter" in mount_text.lower()

    # -- kritik baglama noktalari --
    if not pf.IS_WINDOWS:
        for point in ("/", "/boot", "/boot/efi", "/usr", "/var"):
            assert ph.is_critical_mount(point), point
        assert not ph.is_critical_mount("/media/kullanici/DENEME")
    assert not ph.is_critical_mount("")

    # -- kok dosya sistemi cikarilmaya CALISILMAMALI --
    #
    # `physical` islevleri platform katmanindan ada gore aldigi icin taklit
    # de orada yapilir; boylece gercek `umount` cagrilmadigi dogrulanir.
    info = ph.DiskInfo(path="/dev/sahte", name="sahte")
    eski_nokta, eski_cikar = ph.pf_mount_point, ph.pf_unmount

    def patlayici(*args, **kwargs):
        raise AssertionError("kritik bolumde cikarma denenmemeli")

    # Kritik nokta platforma gore degisir: Linux/macOS'ta "/", Windows'ta
    # sistem surucusu. Test ilk yazildiginda "/" sabitti ve Windows'ta
    # (orada "/" kritik degildir) yanlis beklentiyle basarisiz oluyordu.
    if pf.IS_WINDOWS:
        kok = (os.environ.get("SystemRoot") or "C:\\")[:2] + "\\"
        siradan = "Q:\\"
    else:
        kok, siradan = "/", "/media/kullanici/VERI"
    try:
        ph.pf_mount_point = lambda path, index, offset=-1: kok
        ph.pf_unmount = patlayici
        ok, message = ph.unmount_partition(info, 1, offset=MIB)
        assert not ok and message, (ok, message)
        assert kok in message

        # Kritik olmayan noktada cikarma platform katmanina INER
        cagrildi = []
        ph.pf_mount_point = lambda path, index, offset=-1: siradan
        ph.pf_unmount = lambda path, index, offset=-1: (
            cagrildi.append((path, index, offset)), (True, ""))[1]
        ok, message = ph.unmount_partition(info, 2, offset=300 * MIB)
        # ofset platform katmanina kadar tasinir (Windows bolumu onunla bulur)
        assert ok and cagrildi == [("/dev/sahte", 2, 300 * MIB)], (ok, cagrildi)
    finally:
        ph.pf_mount_point, ph.pf_unmount = eski_nokta, eski_cikar


@test
def t44_geri_yuklemede_bolum_yerlesimi():
    """Yedek hedef diske yeni bolum boyutlariyla geri yuklenebilmeli

    DiskGenius'un "Bolumleri Yonet" davranisi: yedekten buyuk diskte bolumler
    buyutulur, kucuk diskte (veri sigiyorsa) kucultulur. Dosyalar okunur
    kalmali, GPT yedek basligi yeni disk sonuna gitmeli, onyukleme
    sektorundeki bolum konumu yeni baslangici gostermeli.
    """
    from diskultimate.core.restoreplan import RestorePlanError

    kaynak = img_path("t44.img")
    s = DiskSession.create(kaynak, 512 * MIB, scheme="gpt", overwrite=True)
    mevcut = {k.key for k in available_kinds()}
    icerik = bytes(range(256)) * 4000            # ~1 MB
    bolumler = []
    for key, mb in (("fat32", 200), ("ntfs", 120), ("exfat", 100)):
        if key not in mevcut:
            continue
        r = s.free_regions()[0]
        p = s.create_partition(r.start_lba, mb * MIB // 512, fs_key=key,
                               label=key.upper()[:8])
        fs = s.filesystem(p.index)
        fs.mkdir("/klasor")
        fs.write_file("/klasor/veri.bin", icerik)
        fs.flush()
        bolumler.append((p.index, key))
    assert len(bolumler) >= 2, bolumler
    s.close()

    dub = img_path("t44.dub")
    s = DiskSession.open(kaynak, readonly=True)
    backup(s.image, dub, compress=True)
    s.close()

    onizleme = DiskSession.backup_preview(dub)
    temel = onizleme.layout
    assert temel is not None, "disk yedeginin yerlesimi okunmadi"
    assert len(temel.parts) == len(bolumler)
    assert temel.is_identity
    for lp in temel.parts:
        assert lp.fs_resizable, (lp.index, lp.kind, lp.note)
        assert 0 < lp.min_count < lp.old_count, (lp.index, lp.min_count)

    def dogrula(yol, beklenen_sektor):
        hedef = DiskSession.open(yol, readonly=True)
        try:
            assert hedef.scheme == "gpt", hedef.scheme
            assert len(hedef.partitions) == len(bolumler)
            son = hedef.image.read_sectors(hedef.image.sector_count - 1)
            assert son[:8] == b"EFI PART", "GPT yedek basligi disk sonunda degil"
            for index, key in bolumler:
                part = hedef.table.get(index)
                bek_start, bek_count = beklenen_sektor[index]
                assert (part.start_lba, part.sector_count) == \
                    (bek_start, bek_count), (index, part.start_lba,
                                             part.sector_count, bek_start,
                                             bek_count)
                okunan = hedef.filesystem(index).read("/klasor/veri.bin")
                assert okunan == icerik, f"Bolum {index} ({key}) verisi bozuk"
                bulgu = detect(hedef.view(part))
                assert bulgu.total_bytes > 0.9 * part.size, \
                    (index, key, bulgu.total_bytes, part.size)
                boot = hedef.view(part).read(0, 512)
                if key == "exfat":
                    ofset = struct.unpack_from("<Q", boot, 64)[0]
                else:
                    ofset = struct.unpack_from("<I", boot, 28)[0]
                assert ofset == part.start_lba, (index, key, ofset)
        finally:
            hedef.close()

    # --- buyuk hedef: bolumler diske orantili yayilir ---
    plan = temel.copy()
    plan.retarget(1024 * MIB // 512)
    assert plan.is_identity is False and not plan.changed, \
        "sigan yerlesim kendiliginden degismemeli"
    assert plan.fit(expand=True)
    assert not plan.validate(), plan.validate()
    assert all(lp.grows for lp in plan.parts), [
        (lp.index, lp.old_count, lp.new_count) for lp in plan.parts]
    buyuk = DiskSession.restore_to_new_image(
        dub, img_path("t44_buyuk.img"), size_bytes=1024 * MIB, layout=plan)
    dogrula(buyuk, {lp.index: (lp.new_start, lp.new_count)
                    for lp in plan.parts})

    # --- kucuk hedef: veri sigdigi icin bolumler kucultulur ---
    kucuk_boyut = 256 * MIB
    plan = temel.copy()
    plan.retarget(kucuk_boyut // 512)
    assert not plan.validate(), plan.validate()
    assert any(lp.shrinks for lp in plan.parts)
    kucuk = DiskSession.restore_to_new_image(
        dub, img_path("t44_kucuk.img"), size_bytes=kucuk_boyut, layout=plan)
    assert os.path.getsize(kucuk) == kucuk_boyut
    dogrula(kucuk, {lp.index: (lp.new_start, lp.new_count)
                    for lp in plan.parts})

    # --- elle: ortadaki bolum tasinir, sondaki buyutulur ---
    plan = temel.copy()
    plan.retarget(768 * MIB // 512)
    sirali = plan.sorted_parts()
    sirali[-1].new_start += 32 * MIB // 512       # once sondaki kayar,
    if len(sirali) > 2:                           # sonra ortadaki: cakisma yok
        sirali[-2].new_start += 16 * MIB // 512
    for lp in sirali[1:]:
        alt, ust = plan.window(lp.index)
        assert alt <= lp.new_start and lp.new_end <= ust, (lp.index, alt, ust)
    assert plan.extend_last()
    assert not plan.validate(), plan.validate()
    elle = DiskSession.restore_to_new_image(
        dub, img_path("t44_elle.img"), size_bytes=768 * MIB, layout=plan)
    dogrula(elle, {lp.index: (lp.new_start, lp.new_count)
                   for lp in plan.parts})

    # --- seritteki tutamaklar (move_edge): DiskGenius gibi surukleme ---
    plan = temel.copy()
    plan.retarget(768 * MIB // 512)
    kenarlar = plan.edges()
    sinirlar = [k for k in kenarlar if k[1] == "boundary"]
    assert sinirlar, f"bitisik bolumler ortak tutamak almadi: {kenarlar}"
    assert kenarlar[-1][1] == "end", "son bolumun sag tutamagi yok"
    _lba, _tur, sol, sag = sinirlar[0]
    a, b = plan.get(sol), plan.get(sag)
    b_son = b.new_end
    # sinir saga: sol bolum buyur, sag bolum kuculur, sag bolumun sonu sabit
    assert plan.move_edge("boundary", sol, sag, a.new_end + 1 + 8 * MIB // 512)
    assert a.grows and b.shrinks and b.new_end == b_son, \
        (a.new_count, b.new_start, b.new_count)
    assert a.new_count % plan.align == 0 and b.new_start % plan.align == 0
    # asiri surukleme en az boyutta durur (veri kaybi yok)
    plan.move_edge("boundary", sol, sag, b_son)
    assert b.new_count == b.min_count or \
        b.new_count - b.min_count < plan.align, (b.new_count, b.min_count)
    assert not plan.validate(), plan.validate()
    # son bolumun sag kenari disk sonunu gecemez
    son = plan.sorted_parts()[-1]
    plan.move_edge("end", son.index, -1, plan.target_sectors * 2)
    assert son.new_end <= plan.last_usable()
    assert not plan.validate(), plan.validate()
    surukle = DiskSession.restore_to_new_image(
        dub, img_path("t44_surukle.img"), size_bytes=768 * MIB, layout=plan)
    dogrula(surukle, {lp.index: (lp.new_start, lp.new_count)
                      for lp in plan.parts})

    # --- sigmayan hedef reddedilir, yerlesim bozulmaz ---
    # (24 MiB: FAT32 65525 kumenin altina inemez, tek basina ~33 MB ister)
    plan = temel.copy()
    plan.retarget(24 * MIB // 512)
    hata = plan.validate()
    assert hata, "sigmayan yerlesim gecerli sayildi"
    assert plan.required_sectors() > 24 * MIB // 512
    hedef = DiskImage.create(img_path("t44_dar.img"), 24 * MIB, overwrite=True)
    try:
        from diskultimate.core.restoreplan import restore_with_layout
        try:
            restore_with_layout(dub, hedef, plan)
            raise AssertionError("sigmayan yerlesim yazildi")
        except RestorePlanError:
            pass
    finally:
        hedef.close()

    # --- ayni boyut + degismemis yerlesim: bayt bayt yol ---
    ayni = DiskSession.restore_to_new_image(dub, img_path("t44_ayni.img"),
                                            layout=temel.copy())
    with open(ayni, "rb") as a, open(kaynak, "rb") as b:
        assert hashlib.sha1(a.read()).digest() == \
            hashlib.sha1(b.read()).digest(), "ozdes geri yukleme farkli"

    # --- bolum yedegi daha buyuk bolume: dosya sistemi bolumu doldurur ---
    ilk_index = bolumler[0][0]
    s = DiskSession.open(kaynak, readonly=True)
    bolum_dub = img_path("t44_bolum.dub")
    s.backup_partition(ilk_index, bolum_dub)
    s.close()
    hedef_yol = img_path("t44_bolum_hedef.img")
    h = DiskSession.create(hedef_yol, 512 * MIB, scheme="mbr", overwrite=True)
    r = h.free_regions()[0]
    genis = h.create_partition(r.start_lba + 8 * MIB // 512, 300 * MIB // 512,
                               fs_key="fat32", label="ESKI")
    h.restore_partition(genis.index, bolum_dub)
    part = h.table.get(genis.index)
    assert h.filesystem(genis.index).read("/klasor/veri.bin") == icerik
    bulgu = detect(h.view(part))
    assert bulgu.total_bytes > 280 * MIB, \
        f"dosya sistemi bolumu doldurmadi: {human_size(bulgu.total_bytes)}"
    assert struct.unpack_from("<I", h.view(part).read(0, 512), 28)[0] == \
        part.start_lba, "gizli sektor alani yeni bolume gore duzeltilmedi"
    h.close()


@test
def t45_bitmap_sayimi_ve_aygit_kilidi():
    """Hizli NTFS bitmap sayimi eskisiyle ayni; aygit okumasi is parcacigi guvenli

    Haritadaki tutamaklar her suruklemede donuyordu: NTFS `$Bitmap` bit bit
    Python dongusuyle geziliyordu (217 GB'ta 1.5 sn). Sayim bayt duzeyine
    indi; sonuc eski yontemle **birebir** ayni olmali. Sinirlar artik arka
    planda hesaplandigi icin ayni tutamaktan iki is parcacigi okuyabilir;
    `seek`+`read` yarisinda yanlis sektor okunmamali.
    """
    import random
    import threading
    from diskultimate.core.ntfsresize import bitmap_window_usage

    def yavas(window, valid_bits):
        used, highest = 0, -1
        for i, byte in enumerate(window):
            for bit in range(8):
                index = i * 8 + bit
                if index >= valid_bits:
                    return used, highest
                if byte & (1 << bit):
                    used += 1
                    highest = index
        return used, highest

    rng = random.Random(44)
    durumlar = [(b"", 0), (b"\x00" * 64, 512), (b"\xff" * 3, 24),
                (b"\xff" * 3, 20), (b"\x80", 7), (b"\x80", 8),
                (b"\x01" + b"\x00" * 9, 80), (b"\xff\xff", 0)]
    for _ in range(300):
        n = rng.randint(1, 300)
        veri = bytes(rng.choice((0, 0, 0xFF, rng.randrange(256)))
                     for _ in range(n))
        durumlar.append((veri, rng.randint(0, n * 8 + 5)))
    for veri, gecerli in durumlar:
        assert bitmap_window_usage(veri, gecerli) == yavas(veri, gecerli), \
            (veri[:8], gecerli)

    # --- ayni tutamaktan iki is parcacigi: veri karismamali ---
    yol = img_path("t45.img")
    d = DiskImage.create(yol, 8 * MIB, overwrite=True)
    for lba in range(0, 16384, 64):
        d.write_sectors(lba, struct.pack("<I", lba).ljust(512, b"\xAB") * 64)
    hatalar = []

    def okuyucu(tohum):
        r = random.Random(tohum)
        for _ in range(3000):
            lba = r.randrange(0, 16384, 64)
            veri = d.read_sectors(lba, 1)
            if struct.unpack_from("<I", veri)[0] != lba:
                hatalar.append(lba)
                return

    iplikler = [threading.Thread(target=okuyucu, args=(i,)) for i in range(4)]
    for ip in iplikler:
        ip.start()
    for ip in iplikler:
        ip.join()
    d.close()
    assert not hatalar, f"eszamanli okumada yanlis sektor: {hatalar[:5]}"


@test
def t46_planda_acilan_alana_buyume():
    """Kuyrukta kucultulen bolumun actigi alana komsu bolum buyuyebilmeli

    Kullanici bildirimi (2026-09-28): ana ekranda bolum kucultuluyor, bos
    alan gorunuyor ama hicbir bolum o alana buyutulemiyordu. Artik ana ekran
    ortak modeli kullanir (`queueedit`, ADR 0049): pencere planlanan
    yerlesimden gelir, ayni bolumun adimi yerinde guncellenir, sinir tutamagi
    iki adim uretir ve **yer acan adim once** gelir; kuyruk bu sirayla
    uygulanabilir olmali (`planview` cakisma denetimi).
    """
    from diskultimate.core import planview, queueedit
    from diskultimate.core.fsdetect import detect as fs_detect
    from diskultimate.core.layoutedit import LayoutError
    from diskultimate.core.resize import ResizeError, fs_resize_info_for

    yol = img_path("t46.img")
    s = DiskSession.create(yol, 256 * MIB, scheme="gpt", overwrite=True)
    r = s.free_regions()[0]
    p1 = s.create_partition(r.start_lba, 100 * MIB // 512, fs_key="fat32",
                            label="BIR")
    r = s.free_regions()[0]
    p2 = s.create_partition(r.start_lba, 60 * MIB // 512, fs_key="exfat",
                            label="IKI")
    icerik = bytes(range(256)) * 2000
    for bolum in (p1, p2):
        fs = s.filesystem(bolum.index)
        fs.write_file("/veri.bin", icerik)
        fs.flush()
    s.close_filesystems()
    p1, p2 = s.table.get(p1.index), s.table.get(p2.index)
    sinirlar = {p.index: fs_resize_info_for(s, p) for p in (p1, p2)}
    bak = lambda part: sinirlar.get(part.index)          # noqa: E731

    kuyruk = ops.OperationQueue()
    M = MIB // 512

    # 1) P1'i kucult (tek bolum, harita tutamagi gibi)
    model = queueedit.build(s, kuyruk, limits=bak)
    assert not model.get(p1.index).locked
    model.get(p1.index).new_count = 60 * M
    queueedit.commit(s, kuyruk, model, [p1.index])
    assert len(kuyruk) == 1

    # 2) Planlanan pencere acilan alani gorur; disk penceresi gormez
    model = queueedit.build(s, kuyruk, limits=bak)
    alt, ust = model.window(p2.index)
    assert alt == p1.start_lba + 60 * M, (alt, p1.start_lba + 60 * M)
    yeni_bas = alt
    yeni_boy = p2.end_lba - yeni_bas + 1
    try:
        s.plan_resize(p2.index, yeni_bas, yeni_boy)
        raise AssertionError("disk penceresi planda acilan alani gormemeli")
    except ResizeError:
        pass
    b = model.get(p2.index)
    b.new_start, b.new_count = yeni_bas, yeni_boy
    queueedit.commit(s, kuyruk, model, [p2.index])
    assert len(kuyruk) == 2 and not planview.project(s, kuyruk).conflicts

    # 3) Ayni bolum yeniden duzenlenir: yeni adim eklenmez, yerinde degisir
    model = queueedit.build(s, kuyruk, limits=bak)
    model.get(p1.index).new_count = 50 * M
    queueedit.commit(s, kuyruk, model, [p1.index])
    assert len(kuyruk) == 2 and kuyruk[0].params["sector_count"] == 50 * M, \
        [op.params for op in kuyruk]

    # 4) Gecersiz duzenleme: kuyruk degismeden kalir
    once = [dict(op.params) for op in kuyruk]
    model = queueedit.build(s, kuyruk, limits=bak)
    model.get(p1.index).new_count = 2 * M          # FAT32 en azinin alti
    try:
        queueedit.commit(s, kuyruk, model, [p1.index])
        raise AssertionError("en az boyutun alti kabul edildi")
    except (LayoutError, ResizeError):
        pass
    assert [dict(op.params) for op in kuyruk] == once, "kuyruk geri donmedi"

    # 5) Uygula: iki adim sirayla, veri saglam, exFAT buyudu
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, sonuc.summary()
    s.reload()
    a, b = s.table.get(p1.index), s.table.get(p2.index)
    assert a.sector_count == 50 * M, a.sector_count
    assert (b.start_lba, b.sector_count) == (yeni_bas, yeni_boy), \
        (b.start_lba, b.sector_count, yeni_bas, yeni_boy)
    assert s.filesystem(a.index).read("/veri.bin") == icerik
    assert s.filesystem(b.index).read("/veri.bin") == icerik
    assert fs_detect(s.view(b)).total_bytes > 0.9 * b.size, "exFAT buyumedi"
    s.close_filesystems()

    # 6) Sinir tutamagi. Once P1 buyutulup P2'ye bitistirilir (bir adim);
    #    sonra ortak sinir SAGA: P1'in adimi yerinde buyur (onde kalir), P2
    #    icin yeni adim eklenir (yer acan). Kuyruk ters sirada kalir; bu
    #    duzenleme kendi adimini sona alarak duzeltmeli: [P2, P1].
    a, b = s.table.get(p1.index), s.table.get(p2.index)
    # Sayi olarak sakla: uygulama bolum nesnelerini YERINDE degistirir
    a_bas, b_bas, b_boy = a.start_lba, b.start_lba, b.sector_count
    sinirlar.clear()
    sinirlar.update({p.index: fs_resize_info_for(s, p) for p in (a, b)})
    s.close_filesystems()
    kuyruk = ops.OperationQueue()
    model = queueedit.build(s, kuyruk, limits=bak)
    model.get(a.index).new_count = b.start_lba - a.start_lba
    queueedit.commit(s, kuyruk, model, [a.index])
    assert len(kuyruk) == 1
    model = queueedit.build(s, kuyruk, limits=bak)
    kenar = [k for k in model.edges() if k[1] == "boundary"]
    assert kenar, f"bitisik bolumlerde sinir tutamagi yok: {model.edges()}"
    once = {x.index: (x.new_start, x.new_count) for x in model.parts}
    assert model.move_edge("boundary", a.index, b.index, b.start_lba + 8 * M)
    degisen = [x.index for x in model.parts
               if (x.new_start, x.new_count) != once[x.index]]
    assert sorted(degisen) == sorted([a.index, b.index]), degisen
    queueedit.commit(s, kuyruk, model, degisen, before=once)
    assert [op.params["index"] for op in kuyruk] == [b.index, a.index], \
        f"yer acan adim once gelmeli: {[op.params for op in kuyruk]}"
    assert not planview.project(s, kuyruk).conflicts

    # 7) Ters sira cakisma olarak yakalanir (uygulama aninda duracakti)
    kuyruk.move(1, 0)
    assert planview.project(s, kuyruk).conflicts == [0], \
        planview.project(s, kuyruk).conflicts
    kuyruk.move(1, 0)
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, sonuc.summary()
    s.reload()
    a2, b2 = s.table.get(p1.index), s.table.get(p2.index)
    assert a2.sector_count == b_bas - a_bas + 8 * M, a2.sector_count
    assert (b2.start_lba, b2.sector_count) == (b_bas + 8 * M, b_boy - 8 * M)
    assert s.filesystem(a2.index).read("/veri.bin") == icerik
    assert s.filesystem(b2.index).read("/veri.bin") == icerik
    s.close()


@test
def t47_ortak_yerlesim_modeli():
    """Ortak bolum duzenleme modeli kaynaktan bagimsiz calismali (ADR 0049)

    `layoutedit.EditableLayout` ana ekran ve geri yuklemenin ortak
    cekirdegidir. Diske dokunmadan sinanir: ortak sinir tutamagi, tasinamayan
    ve boyutu sabit bolumler, hizalama, dogrulama, mantiksal MBR bolumlerinin
    EBR boslugu, eski adlarin (RestoreLayout/LayoutPart) ayni sinif olmasi.
    """
    from diskultimate.core import layoutedit as le
    from diskultimate.core import restoreplan
    from diskultimate.core.ptable import Partition

    assert restoreplan.RestoreLayout is le.EditableLayout
    assert restoreplan.LayoutPart is le.Slot
    assert issubclass(restoreplan.RestorePlanError, le.LayoutError)

    M = MIB // 512

    def yuva(index, start, count, kind="fat", en_az=1, en_cok=0,
             tasinir=True, logical=False, scheme="gpt"):
        part = Partition(index=index, start_lba=start, sector_count=count,
                         scheme=scheme, logical=logical)
        slot = le.Slot(part=part, old_start=start, old_count=count,
                       new_start=start, new_count=count, fs_type=kind)
        slot.apply_limits(kind, en_az, en_cok, "", movable=tasinir)
        return slot

    def duzen(slots, total=200 * M, scheme="gpt"):
        return le.EditableLayout(scheme=scheme, sector_size=512,
                                 source_sectors=total, target_sectors=total,
                                 parts=slots)

    # -- apply_limits: tur bazli sinirlar --
    ham = yuva(9, 2048, 10 * M, kind="raw")
    assert (ham.min_count, ham.max_count) == (10 * M, 0), "ham kucultulmemeli"
    ext = yuva(9, 2048, 10 * M, kind="unsupported")
    assert ext.min_count == ext.max_count == 10 * M, "ext boyutu sabit olmali"

    # A (FAT) | B (NTFS) bitisik, C (ext) sabit, sonda bos alan
    a = yuva(1, 2048, 40 * M, en_az=20 * M)
    b = yuva(2, 2048 + 40 * M, 40 * M, kind="ntfs", en_az=10 * M)
    c = yuva(3, 2048 + 80 * M, 20 * M, kind="unsupported")
    d = duzen([a, b, c])
    assert not d.validate(), d.validate()
    kenarlar = {(k[1], k[2], k[3]) for k in d.edges()}
    assert ("boundary", 1, 2) in kenarlar, kenarlar
    assert not any(3 in (k[1], k[2]) for k in kenarlar), \
        f"boyutu sabit bolumun kenari oynamamali: {kenarlar}"
    # Sabit komsunun yanindaki bolum ortak sinir yerine TEK BASINA
    # kuculebilmeli (bitisik ext4'un yanindaki FAT sagdan kuculebilsin)
    assert ("end", 2, -1) in kenarlar, kenarlar
    assert d.move_edge("end", 2, -1, b.new_end + 1 - 8 * M)
    assert b.new_count == 32 * M and c.new_start == 2048 + 80 * M
    d.reset()

    # ortak sinir saga: A buyur, B kuculur, B'nin sonu yerinde, hizali
    b_son = b.new_end
    assert d.move_edge("boundary", 1, 2, a.new_end + 1 + 8 * M)
    assert a.new_count == 48 * M and b.new_end == b_son, (a.new_count, b.new_end)
    assert b.new_start % d.align == 0
    # asiri surukleme en az boyutta durur
    d.move_edge("boundary", 1, 2, b_son + 1)
    assert b.new_count == b.min_count, (b.new_count, b.min_count)
    assert not d.validate(), d.validate()
    d.reset()

    # tasinamayan B: A|B siniri oynamaz (B'nin baslangici kayardi)
    b.movable = False
    assert not any(k[1] == "boundary" and k[3] == 2 for k in d.edges())
    assert not d.move_edge("boundary", 1, 2, a.new_end + 1 + 8 * M) or \
        (a.new_count, b.new_start) == (40 * M, 2048 + 40 * M)
    assert (b.new_start, b.new_count) == (2048 + 40 * M, 40 * M)
    assert not d.fit(expand=True), "tasinamayan bolum varken fit uygulanmamali"
    b.movable = True

    # dogrulama: en az boyut ve cakisma
    a.new_count = 5 * M
    assert d.validate(), "en az boyutun alti gecerli sayildi"
    d.reset()
    b.new_start = a.new_start + 10 * M
    assert d.validate(), "cakisma gecerli sayildi"
    d.reset()

    # -- mantiksal MBR bolumleri: EBR icin bir hiza birimi bosluk --
    genis = Partition(index=4, start_lba=2048, sector_count=100 * M,
                      scheme="mbr", type_id=0x0F)
    l1 = yuva(5, 2048 + 2048, 40 * M, logical=True, scheme="mbr", en_az=M)
    l2 = yuva(6, l1.new_end + 1 + 2048, 40 * M, logical=True, scheme="mbr",
              en_az=M)
    m = duzen([l1, l2], scheme="mbr")
    m.extended = genis
    assert not m.validate(), m.validate()
    assert any(k[1] == "boundary" and (k[2], k[3]) == (5, 6) for k in m.edges())
    m.move_edge("boundary", 5, 6, l1.new_end + 1 + 4 * M)
    assert l2.new_start - l1.new_end - 1 == m.align, \
        "mantiksal bolumler arasinda EBR boslugu korunmadi"
    kapsayici = [p for p in m.partitions() if p.type_id == 0x0F][0]
    assert kapsayici.start_lba == l1.new_start - m.align


# --------------------------------------------------------------------------
@test
def t48_ext_boyutlandirma():
    """ext2/3/4 saf Python buyutme, kucultme ve tasima (ADR 0052)

    Eskiden ext boyutlandirmasi yalnizca Windows'un `Resize-Partition`
    aracina yonlendiriliyordu ve o arac ext'i tanimadigi icin **hicbir
    platformda** calismiyordu. Burada ayni islemler saf Python ile yapilir;
    varsa `e2fsck -fn` her adimdan sonra birimi denetler.
    """
    from diskultimate.core.resize import ResizeError
    e2fsck = shutil.which("e2fsck") or shutil.which("e2fsck", path="/sbin:/usr/sbin")

    def bolum_denetle(img_yol: str, etiket: str) -> None:
        if not e2fsck:
            return
        with open(img_yol, "rb") as kaynak:
            mbr = kaynak.read(512)
            bas, adet = struct.unpack_from("<II", mbr, 446 + 8)
            parca = img_yol + ".part"
            kaynak.seek(bas * 512)
            with open(parca, "wb") as cikti:
                kalan = adet * 512
                while kalan > 0:
                    blok = kaynak.read(min(4 * MIB, kalan))
                    if not blok:
                        break
                    cikti.write(blok)
                    kalan -= len(blok)
        try:
            r = subprocess.run([e2fsck, "-fn", parca], capture_output=True,
                               text=True)
            assert r.returncode == 0, f"{etiket}: e2fsck\n{r.stdout[-1500:]}"
        finally:
            os.unlink(parca)

    veri = {f"/dosya{i}.bin": os.urandom(37_000 * (i + 1)) for i in range(12)}

    def icerik(oturum, etiket):
        oturum.close_filesystems()
        fs = oturum.filesystem(1)
        for ad, beklenen in veri.items():
            assert fs.read(ad) == beklenen, f"{etiket}: {ad}"
        assert fs.read("/klasor/not.txt") == b"ext" * 999, etiket
        oturum.close_filesystems()

    for surum in ("ext4", "ext3", "ext2"):
        yol = img_path(f"t48_{surum}.img")
        s = DiskSession.create(yol, 1200 * MIB, overwrite=True)
        s.create_table("mbr")
        s.create_partition(2048, 160 * MIB // 512, fs_key=surum, label="EXTRZ")
        fs = s.filesystem(1)
        for ad, icerik_ in veri.items():
            fs.write_file(ad, icerik_)
        fs.mkdir("/klasor")
        fs.write_file("/klasor/not.txt", b"ext" * 999)
        fs.flush()
        s.close_filesystems()

        bilgi = s.resize_info(1)
        assert bilgi.kind == "ext" and bilgi.movable, bilgi
        assert bilgi.min_sectors < 160 * MIB // 512, bilgi

        # buyut -> kucult (asgariye yakin) -> tasi
        s.resize_partition(1, 2048, 900 * MIB // 512, confirm=True)
        assert s.table.get(1).size == 900 * MIB
        icerik(s, f"{surum} buyut")
        s.close()
        bolum_denetle(yol, f"{surum} buyut")
        s = DiskSession.open(yol, readonly=False)

        bilgi = s.resize_info(1)
        try:
            s.plan_resize(1, 2048, max(1, bilgi.min_sectors - 4096))
            raise AssertionError("ext asgari siniri denetlenmedi")
        except ResizeError:
            pass
        hedef = bilgi.min_sectors + 2048
        s.resize_partition(1, 2048, hedef, confirm=True)
        icerik(s, f"{surum} kucult")
        s.close()
        bolum_denetle(yol, f"{surum} kucult")
        s = DiskSession.open(yol, readonly=False)

        s.resize_partition(1, 2048 + 300 * MIB // 512, 400 * MIB // 512,
                           confirm=True)
        assert s.table.get(1).start_lba == 2048 + 300 * MIB // 512
        icerik(s, f"{surum} tasi+buyut")
        s.close()
        bolum_denetle(yol, f"{surum} tasi+buyut")
    if not e2fsck:
        raise Atlandi("e2fsck yok: islemler calisti, harici denetim yapilamadi")


@test
def t49_sifreli_ve_kapsayici_taninma():
    """Sifreli / kapsayici bolumler adiyla taninir ve Uygula uyarir (ADR 0053)

    BitLocker, LUKS, LVM, RAID bolumleri eskiden "Bilinmeyen" gorunuyordu;
    kullanici bunlari bos sanip bicimlendirebilirdi. Imzalar belgelenmis
    yerlesime gore kurulur (gercek araclar her platformda yok); tespit
    bayraklari ve `operations.risk_notes` uyarilari sinanir.
    """
    import uuid as _uuid
    from diskultimate.core import operations as ops_mod

    def yaz(view, ofset, veri):
        view.write(ofset, veri)

    yol = img_path("t49.img")
    s = DiskSession.create(yol, 200 * MIB, overwrite=True)
    s.create_table("gpt")
    bolumler = []
    for i in range(6):
        bolumler.append(s.create_partition(2048 + i * 30 * MIB // 512,
                                           28 * MIB // 512))
    s.create_partition(2048 + 6 * 30 * MIB // 512, 10 * MIB // 512,
                       fs_key="fat16", label="SAGLAM")
    p = [s.view(b) for b in s.table.partitions]

    luks = bytearray(512)
    luks[0:6] = b"LUKS\xba\xbe"
    struct.pack_into(">H", luks, 6, 2)
    luks[24:31] = b"GIZLI01"
    yaz(p[0], 0, bytes(luks))

    bl = bytearray(512)
    bl[0:3] = b"\xeb\x58\x90"
    bl[3:11] = b"-FVE-FS-"
    bl[510:512] = b"\x55\xaa"
    yaz(p[1], 0, bytes(bl))

    lvm = bytearray(512)
    lvm[0:8] = b"LABELONE"
    lvm[24:32] = b"LVM2 001"
    yaz(p[2], 512, bytes(lvm))

    md = bytearray(64)
    struct.pack_into("<III", md, 0, 0xA92B4EFC, 1, 0)
    md[32:40] = b"host:md0"
    yaz(p[3], 4096, bytes(md))

    yaz(p[4], 0, os.urandom(8192))                      # imzasiz, rastgele

    # Sihirli sayi tek basina RAID yapmamali (artik veri): surum alani 7
    artik = bytearray(64)
    struct.pack_into("<III", artik, 0, 0xA92B4EFC, 7, 0)
    son = p[6].size
    yaz(p[6], (son & ~(65536 - 1)) - 65536, bytes(artik))
    s.reload()

    beklenen = [("LUKS2", "encrypted", "GIZLI01"), ("BitLocker", "encrypted", ""),
                ("LVM2", "container", ""), ("Linux RAID", "container", "host:md0"),
                ("Bilinmeyen", "maybe_encrypted", "")]
    parts = s.table.partitions
    for part, (tur, bayrak, etiket) in zip(parts, beklenen):
        info = s.detect_fs(part)
        assert info.fs_type == tur, (part.index, info.fs_type, tur)
        assert getattr(info, bayrak), (tur, bayrak)
        if etiket:
            assert info.label == etiket, (tur, info.label)
    saglam = s.detect_fs(parts[6])
    assert saglam.fs_type == "FAT16" and not (saglam.encrypted or saglam.container
                                              or saglam.maybe_encrypted), saglam
    assert s.detect_fs(parts[5]).fs_type == "" and not \
        s.detect_fs(parts[5]).maybe_encrypted, "sifir bolum bicimsiz sayilmali"

    kuyruk = ops_mod.OperationQueue()
    for part in parts[:5]:
        kuyruk.add(ops_mod.format_op(part.index, "fat32", at_lba=part.start_lba))
    kuyruk.add(ops_mod.format_op(parts[6].index, "fat32", at_lba=parts[6].start_lba))
    notlar = ops_mod.risk_notes(s, kuyruk)
    assert len(notlar) == 5, notlar
    kuyruk2 = ops_mod.OperationQueue()
    kuyruk2.add(ops_mod.Operation("clear_table"))
    assert len(ops_mod.risk_notes(s, kuyruk2)) == 5, "tablo silme hepsini soylemeli"
    s.close()


@test
def t50_takas_bicimlendirme():
    """Linux takas alani saf Python ile bicimlendirilir (mkswap ile bayt bayt ayni)

    Takas bicimi eskiden listede yoktu (`planview` adini biliyordu ama
    `FS_KINDS`te yoktu). Baslik sayfasi `mkswap -L -U` ciktisiyla birebir
    ayni olmali; bolum turu GPT'de takas GUID'i, MBR'de 0x82 olmali.
    """
    import uuid as _uuid
    from diskultimate.core.formatter import FS_BY_KEY
    from diskultimate.core.swap import format_swap

    assert "swap" in {k.key for k in available_kinds(64 * MIB)}
    for sema, tur in (("gpt", "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"), ("mbr", 0x82)):
        yol = img_path(f"t50_{sema}.img")
        s = DiskSession.create(yol, 100 * MIB, overwrite=True)
        s.create_table(sema)
        part = s.create_partition(2048, 64 * MIB // 512, fs_key="swap",
                                  label="TAKAS")
        info = s.detect_fs(part)
        assert info.fs_type == FS_BY_KEY["swap"].label, info.fs_type
        assert info.label == "TAKAS" and info.uuid, info
        if sema == "gpt":
            assert part.type_guid.upper() == tur, part.type_guid
        else:
            assert part.type_id == tur, hex(part.type_id)
        s.close()

    # mkswap ile karsilastirma (arac varsa)
    mkswap = shutil.which("mkswap") or shutil.which("mkswap", path="/sbin:/usr/sbin")
    kimlik = _uuid.UUID("12345678-1234-5678-9abc-def012345678")
    bizim = img_path("t50_bizim.swap")
    with open(bizim, "wb") as f:
        f.truncate(16 * MIB)
    d = DiskImage(bizim)
    format_swap(d, "ETIKET", uuid=kimlik)
    d.close()
    if not mkswap:
        raise Atlandi("mkswap yok: bicimlendirme calisti, baytlar karsilastirilamadi")
    ref = img_path("t50_ref.swap")
    with open(ref, "wb") as f:
        f.truncate(16 * MIB)
    subprocess.run([mkswap, "-q", "-L", "ETIKET", "-U", str(kimlik), ref],
                   capture_output=True)
    with open(ref, "rb") as a, open(bizim, "rb") as b:
        assert a.read(4096) == b.read(4096), "mkswap baslik sayfasi farkli"


@test
def t51_kayip_bolum_yeni_imzalar():
    """Kayip bolum taramasi ext/takas/NTFS'i bulur, yedek ustbloklari yinelemez

    Tarama eskiden yalnizca FAT/exFAT/NTFS ariyordu. Artik ext2/3/4, XFS,
    btrfs, HFS+, APFS, F2FS, takas ve ReFS de boyutuyla bulunur. Derin
    taramada ext'in yedek ustbloklari ayri aday uretmemeli; tabloya geri
    eklenen bolumun turu dosya sistemine uymali (eskiden MBR'de hep 0x83).
    """
    yol = img_path("t51.img")
    s = DiskSession.create(yol, 700 * MIB, overwrite=True)
    s.create_table("mbr")
    yerlesim = [(2048, 300, "ext4", "KAYIPEXT", 0x83),
                (2048 + 310 * MIB // 512, 64, "swap", "KAYIPSW", 0x82),
                (2048 + 380 * MIB // 512, 100, "ntfs", "KAYIPNT", 0x07)]
    for bas, boy, fs_key, etiket, _tur in yerlesim:
        s.create_partition(bas, boy * MIB // 512, fs_key=fs_key, label=etiket)
    for part in list(s.table.partitions)[::-1]:
        s.table.delete_partition(part.index)
    s.table.write()
    s.reload()
    assert not s.table.partitions

    for derin in (False, True):
        adaylar = s.scan_lost_partitions(deep=derin)
        ozet = [(a.start_lba, a.sector_count, a.label) for a in adaylar]
        beklenen = [(bas, boy * MIB // 512, etiket)
                    for bas, boy, _f, etiket, _t in yerlesim]   # NTFS etiketi $Volume'dan
        assert ozet == beklenen, (derin, ozet)
    # Iki yol da veriyi korumali: dogrudan `adopt_lost_partition` ve
    # arayuzun kullandigi kuyruk adimi (`create_op(keep_data=True)`).
    # Eskiden ikisi de bolumun ilk/son 2 MB'ini siliyordu.
    from diskultimate.core import operations as ops_mod
    part = s.adopt_lost_partition(adaylar[0])
    assert part.type_id == yerlesim[0][4], hex(part.type_id)
    kuyruk = ops_mod.OperationQueue()
    for aday in adaylar[1:]:
        kuyruk.add(ops_mod.create_op(aday.start_lba, aday.sector_count, 512,
                                     name=aday.label, keep_data=True,
                                     found_fs=aday.fs_type))
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, sonuc.error
    s.reload()
    parts = s.table.partitions
    assert [p.type_id for p in parts] == [t for *_x, t in yerlesim], \
        [hex(p.type_id) for p in parts]
    turler = [s.detect_fs(p).fs_type for p in parts]
    assert turler == ["ext4", "Linux Takas", "NTFS"], turler
    assert [s.detect_fs(p).label for p in parts] == ["KAYIPEXT", "KAYIPSW", "KAYIPNT"]
    s.close()


@test
def t52_iso9660_okuma():
    """ISO 9660 salt okuma: Rock Ridge, Joliet ve duz adlar (ADR 0055)

    Test verisi `tests/fixtures/*.iso.gz` (xorriso/genisoimage ciktisi) —
    ISO ureticisi her platformda olmadigi icin depoda durur. 4 GiB ustu cok
    parcali dosya burada sinanmaz (4 GB veri); gelistirme sirasinda olculdu.
    """
    import gzip
    from diskultimate.core.filesystem import IsoAccess
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    ikili = bytes(range(256)) * 1000

    def gez(fs, yol="/"):
        out = {}
        for n in fs.listdir(yol):
            if n.is_dir:
                out.update(gez(fs, n.path))
            else:
                out[n.path] = n
        return out

    for ad, etiket, rr, joliet in (("fx_rr", "ETIKETRR", True, False),
                                   ("fx_j", "ETIKETJ", False, True),
                                   ("fx_plain", "DUZ", False, False)):
        yol = img_path(f"t52_{ad}.iso")
        with gzip.open(os.path.join(fixtures, ad + ".iso.gz"), "rb") as kaynak, \
                open(yol, "wb") as hedef:
            hedef.write(kaynak.read())
        d = DiskImage(yol, readonly=True)
        info = detect(d)
        assert info.fs_type == "ISO9660", info.fs_type
        fs = open_filesystem(d, info)
        assert isinstance(fs, IsoAccess) and fs.readable and not fs.writable
        assert fs.label == etiket, fs.label
        assert fs.fs.rock_ridge == rr and fs.fs.joliet == joliet, ad
        dosyalar = gez(fs)
        ikili_yol = next(p for p in dosyalar if p.lower().endswith(".bin"))
        assert fs.read(ikili_yol) == ikili, ad
        if rr or joliet:
            assert "/Klasör/türkçe ğüşiöç.txt" in dosyalar, sorted(dosyalar)
            assert fs.read("/Klasör/türkçe ğüşiöç.txt") == "türkçe".encode()
            assert fs.read("/BuyukHarf_UzunAd_dosyasi.txt").startswith(b"merhaba iso")
        else:
            assert all(";" not in p for p in dosyalar), "surum eki atilmali"
        if rr:
            assert dosyalar["/Klasör/bag"].attr_text.endswith(
                "../BuyukHarf_UzunAd_dosyasi.txt"), dosyalar["/Klasör/bag"]
        hedef = img_path(f"t52_{ad}.out")
        fs.extract(ikili_yol, hedef)
        with open(hedef, "rb") as f:
            assert f.read() == ikili
        d.close()


@test
def t53_tablosuz_disk():
    """Tablosuz disk (duz .iso, super disket FAT, ext4.img) tek bolum gorunur

    Bolum tablosu olmayan goruntu eskiden "bolum tablosu yok, GPT
    olusturulsun mu?" diye karsilaniyordu; icindeki dosya sistemi
    gorunmuyordu. 0x55AA'li FAT onyukleme sektoru MBR sanilip onyukleme
    kodu bolum girisi diye okunuyordu. Bos MBR'nin arkasindaki eski ext4
    kalintisi ise tablosuz sayilmamali.
    """
    import gzip
    from diskultimate.core.ext import format_ext
    from diskultimate.core.ptable import PartitionTableError
    from diskultimate.core.session import SessionError

    # 1) duz ISO
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    iso = img_path("t53.iso")
    with gzip.open(os.path.join(fixtures, "fx_rr.iso.gz"), "rb") as a, \
            open(iso, "wb") as b:
        b.write(a.read())
    s = DiskSession.open(iso, readonly=True)
    assert s.scheme == "none" and len(s.partitions) == 1, s.partitions
    assert s.partitions[0].fs_type == "ISO9660" and s.partitions[0].start_lba == 0
    assert s.filesystem(1).read("/Klasör/türkçe ğüşiöç.txt") == "türkçe".encode()
    s.close()

    # 2) super disket FAT: onyukleme sektoru 0x55AA ile biter
    yol = img_path("t53_fat.img")
    d = DiskImage.create(yol, 64 * MIB, overwrite=True)
    FatFS.format(d, fat_type=16, label="USBBELLEK")
    d.close()
    s = DiskSession.open(yol, readonly=False)
    assert s.scheme == "none", s.scheme
    assert [(p.fs_type, p.fs_label) for p in s.partitions] == [("FAT16", "USBBELLEK")]
    fs = s.filesystem(1)
    fs.write_file("/not.txt", b"tablosuz")
    fs.flush()
    s.close_filesystems()
    for islem in (lambda: s.create_partition(2048, 2048),
                  lambda: s.delete_partition(1)):
        try:
            islem()
            raise AssertionError("tablosuz diskte tablo degisti")
        except (PartitionTableError, SessionError):
            pass
    assert s.resize_info(1).kind == "unsupported"
    assert not s.can_convert_to("gpt")[0]
    assert s.filesystem(1).read("/not.txt") == b"tablosuz"
    s.close()

    # 3) ext4.img (tablosuz, ustblok 1024'te, 0x55AA yok)
    yol = img_path("t53_ext.img")
    d = DiskImage.create(yol, 32 * MIB, overwrite=True)
    format_ext(d, "ext4", label="HAMEXT")
    d.close()
    s = DiskSession.open(yol, readonly=True)
    assert s.scheme == "none" and s.partitions[0].fs_type == "ext4", s.partitions
    s.close()

    # 4) ayni goruntuye bos MBR: eski ext4 kalintisi tablosuz sayilmamali
    s = DiskSession.open(yol, readonly=False)
    s.create_table("mbr")
    s.close()
    s = DiskSession.open(yol, readonly=True)
    assert s.scheme == "mbr" and not s.partitions, (s.scheme, s.partitions)
    s.close()


@test
def t54_dosya_sistemi_kutugu():
    """Her dosya sistemi tek kutukte; gorunen ad cevrilir, veri cevrilmez (ADR 0056)

    Yeni bir dosya sistemi 13 yere dokunmayi gerektiriyordu ve biri
    unutulunca yarim gorunuyordu. "Bilinmeyen" ve "Linux Takas" cevrilmeden
    gosteriliyordu. Bu test: `fsdetect`in uretebilecegi her ad ve her
    bicimlendirme turu kutukte; renk/tur tablolari kutukten turuyor;
    gorunen ad dil degisince degisir ama `fs_type` verisi degismez.
    """
    import re
    from diskultimate import i18n
    from diskultimate.core import convert, fsregistry
    from diskultimate.core.formatter import FS_KINDS

    kaynak = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
        __file__))), "src", "diskultimate", "core", "fsdetect.py"),
        encoding="utf-8").read()
    uretilen = set(re.findall(r'fs_type="([^"]+)"', kaynak))
    uretilen |= {"FAT12", "FAT16", "FAT32", "ext2", "ext3", "ext4", "HFS+",
                 "HFSX", "LUKS1", "LUKS2", "Linux Takas"}  # hesaplanan adlar
    eksik = sorted(u for u in uretilen if u not in fsregistry.BY_KEY)
    assert not eksik, f"kutukte olmayan dosya sistemi: {eksik}"
    for kind in FS_KINDS:
        assert kind.label in fsregistry.BY_KEY, kind.label

    assert convert.FS_TO_MBR == fsregistry.mbr_types()
    assert convert.FS_TO_GPT == fsregistry.gpt_types()
    assert convert.FS_TO_MBR["NTFS"] == 0x07 and convert.FS_TO_MBR["Linux Takas"] == 0x82

    onceki = i18n.current_language()
    try:
        i18n.set_language("en", remember=False)
        assert fsregistry.fs_display("Linux Takas") == "Linux Swap"
        assert fsregistry.fs_display("Bilinmeyen") == "Unknown"
        assert fsregistry.fs_display("ext4") == "ext4"
        assert fsregistry.fs_display("") == ""
        assert fsregistry.fs_display("YeniFS") == "YeniFS", "bilinmeyen anahtar aynen"
        # renk veriden gelir; dil degisince degismez
        assert fsregistry.fs_color("Linux Takas") == fsregistry.BY_KEY["Linux Takas"].color
    finally:
        i18n.set_language(onceki, remember=False)


@test
def t55_ext_htree_ve_extent_yazma():
    """ext yazma: htree dizinine ekleme, dizin buyumesi, extent'li dosyalar

    Eskiden yazici indeksli (htree) dizine duz sirayla yaziyor ve dx_root
    indeksini eziyordu (e2fsck: "HTREE directory ... invalid"); extent'li
    dizin dolunca yazma reddediliyor, dolayli dizin 12 blokta duruyordu;
    baslatilmamis gruplar hic kullanilmiyordu. Test verisi
    `tests/fixtures/ext4_htree.img.gz` (mkfs.ext4 + e2fsck -D, 300 girisli
    indeksli /d). Her girisin kendi karmasinin yapraginda durdugu platformdan
    bagimsiz denetlenir; e2fsck varsa birim ayrica denetlenir.
    """
    import gzip
    from diskultimate.core.extwrite import INDEX_FL
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    yol = img_path("t55.img")
    with gzip.open(os.path.join(fixtures, "ext4_htree.img.gz"), "rb") as a, \
            open(yol, "wb") as b:
        b.write(a.read())
    d = DiskImage(yol)
    w = ExtWriter(ExtFS(d))
    yeni = {f"/d/eklenen_{i:04d}_türkçe_ad.txt": bytes([i % 251]) * (i % 900)
            for i in range(800)}
    for ad, veri in yeni.items():
        w.write_file(ad, veri)
    w.mkdir("/buyuk")
    for i in range(2000):
        w.write_file(f"/buyuk/uzun_bir_dosya_adi_ornegi_{i:05d}.dat", b"")
    buyuk_veri = os.urandom(3 * MIB)
    w.write_file("/veri.bin", buyuk_veri)
    w.flush()
    d.close()

    d = DiskImage(yol)
    fs = ExtFS(d)
    w = ExtWriter(fs)
    dizin = fs.resolve("/d")
    assert dizin.flags & INDEX_FL, "dizin indeksli kalmali"
    for ad, veri in yeni.items():
        assert fs.read_data(fs.resolve(ad)) == veri, ad
        # giris, kendi karmasinin indekste gosterdigi yaprakta olmali
        yaprak = w.htree_leaf_for(dizin.number, ad.rsplit("/", 1)[1])
        assert ad.rsplit("/", 1)[1].encode() in d.read(yaprak * fs.block_size,
                                                        fs.block_size), ad
    for i in range(1, 301):
        yaprak = w.htree_leaf_for(dizin.number, f"dosya_{i}.txt")
        assert f"dosya_{i}.txt".encode() in d.read(yaprak * fs.block_size,
                                                   fs.block_size)
    buyuk = fs.resolve("/buyuk")
    assert buyuk.size > 12 * fs.block_size, "dizin 12 blogu asmali"
    assert len([e for e in fs.read_dir(buyuk) if e.name not in (".", "..")]) == 2000
    veri_inode = fs.resolve("/veri.bin")
    assert veri_inode.uses_extents and fs.read_data(veri_inode) == buyuk_veri
    d.close()

    e2fsck = shutil.which("e2fsck") or shutil.which("e2fsck", path="/sbin:/usr/sbin")
    if not e2fsck:
        raise Atlandi("e2fsck yok: yaprak tutarliligi denetlendi, birim denetimi yapilamadi")
    r = subprocess.run([e2fsck, "-fn", yol], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-1500:]


@test
def t56_ntfs_b_agaci_ve_windows_yapilari():
    """NTFS B+ agaci: Windows'un yazdigi birimde silme/ekleme/adlandirma (ADR 0058)

    Test verisi `tests/fixtures/ntfs_windows.img.gz`: Windows 10'un kendi
    surucusuyle olusturdugu dizinler (uzun ad + 8.3 kisa ad ciftleri, iki
    seviyeli INDX agaci). Her adimda `ntfsindex.verify_tree` (sira, yaprak
    derinligi, sahipsiz giris, $BITMAP, INDX basligi) temiz olmali — bu
    kurallarin ihlalini ntfs-3g gormez, Windows "dosya bozuk" der (olculdu).
    """
    import gzip
    from diskultimate.core.ntfsindex import verify_tree
    from diskultimate.core.ntfsread import NtfsFS as _NtfsFS
    from diskultimate.core.ntfswrite import NtfsWriter
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    yol = img_path("t56.img")
    with gzip.open(os.path.join(fixtures, "ntfs_windows.img.gz"), "rb") as a, \
            open(yol, "wb") as b:
        b.write(a.read())

    def denetle(etiket):
        d = DiskImage(yol, readonly=True)
        fs = _NtfsFS(d)
        w = NtfsWriter(fs)
        for dizin in ("/", "/wbig", "/wref"):
            sorun = verify_tree(w, fs.resolve(dizin).number)
            assert not sorun, f"{etiket} {dizin}: {sorun[:3]}"
        d.close()

    # Kosu uzunlugu isaretli okunur (ntfs-3g, Windows): 128-255 kume gibi
    # degerler tek baytta 0x80+ yazilirsa negatif sayilir (eski hata).
    from diskultimate.core.ntfswrite import _encode_runs
    for uzunluk in (127, 128, 138, 255, 256, 32768, 40000, 65535):
        kod = _encode_runs([(1000, uzunluk)])
        n = kod[0] & 0x0F
        assert int.from_bytes(kod[1:1 + n], "little", signed=True) == uzunluk, uzunluk

    denetle("baslangic")
    d = DiskImage(yol)
    w = NtfsWriter(_NtfsFS(d))
    for i in range(1, 401):                       # uzun + kisa ad ciftleri
        w.remove(f"/wbig/dosya_{i:05d}_c.txt")
    w.rename("/wref/kucuk.txt", "yeniden adlandirildi.txt")
    w.mkdir("/wref/bizim")
    yeni = {f"/wbig/eklenen_{i:04d}_ğüş.txt": bytes([i % 251]) * (i % 700)
            for i in range(1500)}
    for ad, veri in yeni.items():
        w.write_file(ad, veri)
    w.flush()
    d.close()
    denetle("islemler sonrasi")
    d = DiskImage(yol, readonly=True)
    fs = _NtfsFS(d)
    adlar = {e.name for e in fs.listdir("/wbig")}
    assert len(adlar) == 200 + 1500, len(adlar)
    for ad, veri in list(yeni.items())[::97]:
        assert fs.read_file(ad) == veri, ad
    assert fs.read_file("/wref/yeniden adlandirildi.txt").startswith(b"merhaba")
    d.close()

    # Kendi bicimlendiricimizin biriminde yogun ekleme/silme
    yol2 = img_path("t56b.img")
    d = DiskImage.create(yol2, 120 * MIB, overwrite=True)
    format_ntfs(d, label="T56")
    d.close()
    d = DiskImage(yol2)
    w = NtfsWriter(_NtfsFS(d))
    w.mkdir("/k")
    for i in range(1200):
        w.write_file(f"/k/f{i:05d}.bin", bytes([i % 256]) * (i % 900))
    for i in range(0, 1200, 3):
        w.remove(f"/k/f{i:05d}.bin")
    for i in range(0, 1200, 7):
        if i % 3:
            w.rename(f"/k/f{i:05d}.bin", f"ad_{i:05d}.bin")
    w.flush()
    fs = w.fs
    sorun = verify_tree(w, fs.resolve("/k").number)
    assert not sorun, sorun[:3]
    assert len(fs.listdir("/k")) == 800, len(fs.listdir("/k"))
    d.close()


@test
def t57_ntfs_bicimlendirici_windows_yapilari():
    """Saf Python NTFS bicimlendirici Windows'un bekledigi yapilari uretir (ADR 0059)

    Windows eksik yapida birimi "Unknown" ya da "bozuk" (olay 55) sayiyordu;
    mkntfs ile karsilastirilip duzeltildi ve VirtualBox win10'da Healthy
    olculdu. Burada o yapilar her platformda denetlenir:
      1. MFT kaydi 1 KiB (512 bayt sektorde Windows boyle bekler).
      2. `$Extend` altinda $Quota(24) $ObjId(25) $Reparse(26); gorunum
         indeksleri ve kota girisleri; MFT bitmap'inde dolu.
      3. Her sistem dosyasinin $STANDARD_INFORMATION'i 72 bayt ve gecerli
         guvenlik kimligi tasir; 12-15'in bag sayisi 0.
      4. Kok dizin INDX'li; yazici ilk kullanici kaydini 27'den sonra alir.
    """
    import struct as _struct
    from diskultimate.core import ntfs as _n
    from diskultimate.core.ntfsread import NtfsFS as _NtfsFS
    from diskultimate.core.ntfswrite import NtfsWriter

    yol = img_path("t57.img")
    d = DiskImage.create(yol, 64 * MIB, overwrite=True)
    format_ntfs(d, label="T57")
    d.close()
    d = DiskImage(yol)
    fs = _NtfsFS(d)
    assert fs.record_size == 1024, fs.record_size
    gecerli = {_n.SECURITY_ID_SYSTEM, _n.SECURITY_ID_FULL, _n.SECURITY_ID_ROOT}
    for no in list(range(16)) + [24, 25, 26]:
        kayit = fs.record(no)
        assert kayit.in_use, no
        si = kayit.find(0x10)
        assert len(si.value) == 72, (no, len(si.value))
        kimlik = _struct.unpack_from("<I", si.value, 0x34)[0]
        assert kimlik in gecerli, (no, hex(kimlik))
        bag = _struct.unpack_from("<H", kayit.raw, 0x12)[0]
        assert bag == (0 if 12 <= no <= 15 else 1), (no, bag)

    beklenen = {24: ("$Quota", {"$O": 0x11, "$Q": 0x10}),
                25: ("$ObjId", {"$O": 0x13}), 26: ("$Reparse", {"$R": 0x13})}
    extend = fs.record(11)
    for no, (ad, indeksler) in beklenen.items():
        giris = fs.lookup(extend, ad)
        assert giris is not None and giris.mft_ref & 0xFFFFFFFFFFFF == no, ad
        kayit = fs.record(no)
        bayrak = _struct.unpack_from("<H", kayit.raw, 0x16)[0]
        assert bayrak == 0x0D, (ad, hex(bayrak))
        for iad, siralama in indeksler.items():
            kok = kayit.find(0x90, iad)
            assert kok is not None, (ad, iad)
            assert _struct.unpack_from("<I", kok.value, 4)[0] == siralama, (ad, iad)
    q = fs.record(24).find(0x90, "$Q").value
    assert _struct.unpack_from("<I", q, 0x20 + 0x10)[0] == 1   # ilk anahtar: sahip 1
    mft = fs.record(0)
    bitmap = fs.read_attribute(mft.find(0xB0))
    for no in list(range(16)) + [24, 25, 26]:
        assert bitmap[no >> 3] & (1 << (no & 7)), no

    w = NtfsWriter(fs)
    w.write_file("/ilk.txt", b"ilk")
    w.flush()
    kayit = w.fs.resolve("/ilk.txt")
    assert kayit.number >= 27, kayit.number
    d.close()

    # ntfs-3g varsa onun gozunde de temiz ve $Extend alt dosyalari listelenir
    ntfsfix, ntfsls = shutil.which("ntfsfix"), shutil.which("ntfsls")
    if ntfsfix and ntfsls:
        r = subprocess.run([ntfsfix, "-n", yol], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        r = subprocess.run([ntfsls, "-a", "-p", "/$Extend", yol],
                           capture_output=True, text=True)
        for ad in ("$Quota", "$ObjId", "$Reparse"):
            assert ad in r.stdout, r.stdout


@test
def t58_fat_exfat_alt_dizin_buyumesi():
    """FAT/exFAT: alt dizin yeni kumeyle buyurken giris kaybolmaz (matris testi)

    Hata: dizin sonunda yetersiz bos yuva kalinca yeni kume ekleniyor ama
    giris yeni kumenin basina yaziliyordu; aradaki 0x00 ("dizin sonu") yuvasi
    okuyucuyu durdurdugu icin yazilan dosya "Bulunamadi" oluyordu. exFAT'ta
    ayrica ust girisin DataLength'i buyumuyordu ve bos dosya NoFatChain
    bayragiyla yaziliyordu (fsck.exfat: "empty, but has no Fat chain").
    """
    for anahtar in ("fat16", "fat32", "exfat"):
        yol = img_path(f"t58_{anahtar}.img")
        s = DiskSession.create(yol, 200 * MIB, overwrite=True)
        s.create_table("mbr")
        s.create_partition(2048, 160 * MIB // 512, fs_key=anahtar, label="T58")
        fs = s.filesystem(1)
        fs.mkdir("/k")
        adlar = [f"/k/dosya_{i:03d}.dat" for i in range(150)]
        for i, ad in enumerate(adlar):
            fs.write_file(ad, bytes([i % 251]) * (i * 7))
        fs.flush()
        s.close_filesystems()
        fs = s.filesystem(1)
        for i, ad in enumerate(adlar):
            assert fs.read(ad) == bytes([i % 251]) * (i * 7), (anahtar, ad)
        s.close()
        arac = shutil.which("fsck.exfat" if anahtar == "exfat" else "fsck.vfat")
        if arac:
            parca = yol + ".part"
            with open(yol, "rb") as kaynak, open(parca, "wb") as hedef:
                kaynak.seek(2048 * 512)
                hedef.write(kaynak.read(160 * MIB))
            try:
                r = subprocess.run([arac, "-n", parca], capture_output=True,
                                   text=True)
                assert r.returncode == 0, f"{anahtar}: {r.stdout}{r.stderr}"
            finally:
                os.unlink(parca)


@test
def t59_hfsplus_okuma():
    """HFS+ / HFSX salt okuma: katalog B-agaci, bag, Turkce ad, gunluk, sarmalayici

    Test verisi baska uygulamalarin urettigi birimlerdir (ana makinede
    HFS+ baglanamaz, macOS yok):
      * `hfs_xorriso.iso.gz` — libisofs (xorriso -hfsplus) HFS+ agaci:
        1504 dosya, 3 duzeyli katalog, sembolik bag, Turkce ad. Icerik
        asagidaki uretecle birebir yeniden uretilir.
      * `hfs_journal.img.gz` / `hfsx_bos.img.gz` — mkfs.hfsplus (hfsprogs)
        gunluklu HFS+ ve buyuk/kucuk harf duyarli HFSX.
    """
    import gzip
    from diskultimate.core.hfsplus import HfsPlusFS
    from diskultimate.core.filesystem import HfsAccess
    from diskultimate.core.fsregistry import APPLE_HFS

    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

    def ac(ad, hedef):
        with gzip.open(os.path.join(fixtures, ad), "rb") as a, open(hedef, "wb") as b:
            b.write(a.read())
        return hedef

    beklenen = {"/a.txt": b"merhaba\n",
                "/klasor/desen.bin": bytes(range(256)) * 800,
                "/klasor/çğüşöı ÇĞÜŞÖİ.txt": "Türkçe içerik\n".encode("utf-8")}
    for d in range(6):
        for i in range(250):
            beklenen[f"/cok/d{d}/dosya_{i:04d}_ğüş.txt"] = \
                (f"{d}-{i}\n" * (i % 7)).encode("utf-8")

    # --- 1. xorriso birimi: APM bolumunden dogrudan ---------------------
    iso = ac("hfs_xorriso.iso.gz", img_path("t59.iso"))
    with open(iso, "rb") as fh:
        apm = fh.read(4 * 512)[3 * 512:]
    assert apm[:2] == b"PM" and apm[48:57] == b"Apple_HFS"
    bas, adet = struct.unpack_from(">II", apm, 8)
    d = DiskImage(iso, readonly=True)
    fs = HfsPlusFS(PartitionView(d, bas, adet))
    assert fs.fs_type == "HFS+" and fs.label == "FXHFS", (fs.fs_type, fs.label)
    assert fs.catalog.depth >= 2, fs.catalog.depth          # ara dugumler de sinanir
    for yol, veri in beklenen.items():
        assert fs.read_file(yol) == veri, yol
    assert sorted(e.name for e in fs.listdir("/")) == ["a.txt", "bos", "cok", "klasor"]
    assert fs.listdir("/bos") == []
    bag = next(e for e in fs.listdir("/klasor") if e.name == "bag")
    assert bag.symlink == "../a.txt", bag.symlink
    # HFS+ buyuk/kucuk harf duyarsiz; ad NFD saklanir, NFC sorguyla bulunur.
    # Noktasiz ı ile I ayri harftir (Apple katlama tablosu da eslemez).
    assert fs.read_file("/KLASOR/ÇĞÜŞÖı çğüşöİ.TXT") == \
        beklenen["/klasor/çğüşöı ÇĞÜŞÖİ.txt"]
    assert fs.resolve("/COK/D3/DOSYA_0100_ĞÜŞ.TXT").size == len(
        beklenen["/cok/d3/dosya_0100_ğüş.txt"])
    d.close()

    # --- 2. uctan uca: GPT bolumunde HFS+ -> DiskSession / HfsAccess ----
    yol = img_path("t59_gpt.img")
    s = DiskSession.create(yol, 16 * MIB, overwrite=True)
    s.create_table("gpt")
    s.create_partition(2048, adet, type_guid=APPLE_HFS)
    with open(iso, "rb") as kaynak:
        kaynak.seek(bas * 512)
        s.image.write(2048 * 512, kaynak.read(adet * 512))
    s.close()
    s = DiskSession.open(yol)
    erisim = s.filesystem(1)
    assert isinstance(erisim, HfsAccess), type(erisim)
    assert erisim.readable and not erisim.writable and erisim.write_reason
    adlar = {n.name for n in erisim.listdir("/klasor")}
    assert adlar == {"alt", "bag", "desen.bin", "çğüşöı ÇĞÜŞÖİ.txt"}, adlar
    assert erisim.read("/klasor/desen.bin") == beklenen["/klasor/desen.bin"]
    hedef = img_path("t59_cikti")
    shutil.rmtree(hedef, ignore_errors=True)
    erisim.extract("/cok/d5", hedef)
    assert len(os.listdir(hedef)) == 250
    s.close()

    # --- 3. gunluklu HFS+ ve HFSX ------------------------------------------
    d = DiskImage(ac("hfs_journal.img.gz", img_path("t59_j.img")), readonly=True)
    fs = HfsPlusFS(d)
    assert (fs.fs_type, fs.label, fs.journal_dirty) == ("HFS+", "GUNLUK", False)
    gizli = {e.name: e.hidden for e in fs.listdir("/")}
    assert gizli == {".journal": True, ".journal_info_block": True}, gizli
    assert detect(d).fs_type == "HFS+"
    d.close()
    d = DiskImage(ac("hfsx_bos.img.gz", img_path("t59_x.img")), readonly=True)
    fs = HfsPlusFS(d)
    assert (fs.fs_type, fs.label, fs.case_sensitive) == ("HFSX", "Buyuk", True)
    d.close()

    # --- 4. eski HFS sarmalayicisi icine gomulu HFS+ (sentetik MDB) ------
    yol = img_path("t59_w.img")
    with open(img_path("t59_j.img"), "rb") as fh:
        ic = fh.read()
    with open(yol, "wb") as fh:
        mdb = bytearray(512)
        mdb[0:2] = b"BD"
        struct.pack_into(">I", mdb, 0x14, 4096)          # ayirma blogu
        struct.pack_into(">H", mdb, 0x1C, 16)            # ilk ayirma sektoru
        mdb[0x7C:0x7E] = b"H+"
        struct.pack_into(">HH", mdb, 0x7E, 2, len(ic) // 4096)
        fh.write(bytes(1024) + bytes(mdb))
        fh.seek(16 * 512 + 2 * 4096)
        fh.write(ic)
    d = DiskImage(yol, readonly=True)
    bilgi = detect(d)
    assert (bilgi.fs_type, bilgi.label) == ("HFS+", "GUNLUK"), bilgi
    assert HfsPlusFS(d).label == "GUNLUK"
    d.close()


def _dev_tool(name: str):
    """(yol, ortam) — PATH'te ya da kullanici alanina acilmis paketlerde
    (`.tmp/tools/root`, bkz. fs-genisletme-ilerleme.md) gelistirme araci."""
    yol = shutil.which(name) or shutil.which(name, path="/sbin:/usr/sbin")
    if yol:
        return yol, None
    kok = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       ".tmp", "tools", "root")
    for alt in ("usr/sbin", "usr/bin", "sbin"):
        aday = os.path.join(kok, alt, name)
        if os.path.isfile(aday):
            ortam = dict(os.environ)
            ortam["LD_LIBRARY_PATH"] = os.path.join(kok, "usr/lib/x86_64-linux-gnu")
            return aday, ortam
    return None, None


@test
def t60_hfsplus_bicimlendirme():
    """HFS+ bicimlendirme (saf Python, gunluksuz): mkfs.hfsplus ile ayni yerlesim (ADR 0061)

    Yerlesim mkfs.hfsplus (hfsprogs) ciktisi olculerek cikarildi; bos blok
    sayilari birebir ayni olmali (8M 1997, 64M 15997, 1G 259062, 40G
    10449854). fsck.hfsplus varsa her birim onun gozunde temiz olmali.
    Bolum GPT'de Apple HFS GUID'i, MBR'de 0xAF almali; birim okuyucumuzla
    acilmali. HFSX (buyuk/kucuk harf duyarli) API ile uretilebilir.
    """
    from diskultimate.core.hfsformat import format_hfsplus, plan_layout
    from diskultimate.core.hfsplus import HfsPlusFS
    from diskultimate.core.filesystem import HfsAccess
    from diskultimate.core.fsregistry import APPLE_HFS

    GIB = 1024 * MIB
    for boyut, bos in ((8 * MIB, 1997), (64 * MIB, 15997), (GIB, 259062),
                       (40 * GIB, 10449854)):
        L = plan_layout(boyut)
        kullanilan = (1 + L["alloc_blocks"] + L["ext_blocks"] + L["attr_blocks"]
                      + L["cat_blocks"] + 1)
        assert L["total"] - kullanilan == bos, (boyut, L["total"] - kullanilan)

    fsck, ortam = _dev_tool("fsck.hfsplus")
    for boyut, etiket, duyarli in ((2 * MIB, "Küçük", False),
                                   (13333333, "Tuhaf boy", False),
                                   (GIB, "Büyük ğüşİı", False),
                                   (64 * MIB, "HFSX", True)):
        yol = img_path("t60.img")
        if os.path.exists(yol):
            os.unlink(yol)
        with open(yol, "wb") as fh:
            fh.truncate(boyut)                         # seyrek
        d = DiskImage(yol)
        format_hfsplus(d, label=etiket, case_sensitive=duyarli)
        d.close()
        d = DiskImage(yol, readonly=True)
        fs = HfsPlusFS(d)
        assert (fs.label, fs.case_sensitive) == (etiket, duyarli), (fs.label, etiket)
        assert fs.fs_type == ("HFSX" if duyarli else "HFS+")
        assert fs.listdir("/") == []
        # yedek baslik aygitin (tam sektorlerin) sonundan 1024 bayt once
        assert d.read(d.size - 1024, 512) == d.read(1024, 512), "yedek baslik farkli"
        d.close()
        if fsck:
            r = subprocess.run([fsck, "-f", "-n", yol], capture_output=True,
                               text=True, env=ortam)
            assert r.returncode == 0 and "appears to be OK" in r.stdout, \
                f"{boyut}: {r.stdout}{r.stderr}"

    # uctan uca: DiskSession ile GPT ve MBR bolumu
    for sema, beklenen_tur in (("gpt", APPLE_HFS), ("mbr", 0xAF)):
        yol = img_path(f"t60_{sema}.img")
        s = DiskSession.create(yol, 48 * MIB, overwrite=True)
        s.create_table(sema)
        s.create_partition(2048, 40 * MIB // 512, fs_key="hfsplus", label="MacDisk")
        bolum = s.table.get(1)
        tur = bolum.type_guid if sema == "gpt" else bolum.type_id
        assert str(tur).upper() == str(beklenen_tur).upper(), (sema, tur)
        erisim = s.filesystem(1)
        assert isinstance(erisim, HfsAccess) and erisim.label == "MacDisk"
        assert erisim.stats()["total_bytes"] == 40 * MIB
        s.close()


@test
def t61_hfsplus_yazma():
    """HFS+ / HFSX yazma: B-agaci bolme/silme, kapsam tasmasi, katalog buyumesi (ADR 0062)

    Apple katlama tablosu kendi kuralimizla uretilir; ozeti fsck_hfs'in
    tablosundan uretilenle ayni olmali (65 536 giris). Yazma senaryosu:
    Turkce/Unicode/':' adlar, 3 duzeyli katalog, katalog dosyasinin
    buyumesi, dolu birimde geri alma, parcali alanda 8+ kapsam, klasorler
    arasi tasima, ozyinelemeli silme. Her asamada okuyucu ile birebir; fsck.hfsplus
    varsa "appears to be OK". Kirli/kilitli birime yazma reddedilir.
    """
    import hashlib
    import random
    from diskultimate.core.hfsformat import format_hfsplus
    from diskultimate.core.hfsplus import HfsPlusFS
    from diskultimate.core.hfsunicode import compare_names, fold_table, hfs_nfd
    from diskultimate.core.hfswrite import HfsWriteError, HfsWriter
    from diskultimate.core.filesystem import HfsAccess

    tablo = struct.pack(">65536H", *fold_table())
    assert hashlib.sha1(tablo).hexdigest() == "084766d190993d68c129bf80ec1db385cddde73c"
    assert compare_names("a", "B") < 0
    assert compare_names(hfs_nfd("İ"), "i̇") == 0     # diskteki ad NFD'dir
    assert compare_names("ı", "I") != 0 and compare_names("a", "B", True) > 0

    fsck, ortam = _dev_tool("fsck.hfsplus")

    def denetle(yol, beklenen, etiket):
        d = DiskImage(yol, readonly=True)
        fs = HfsPlusFS(d)
        for p, veri in beklenen.items():
            assert fs.read_file(p) == veri, (etiket, p)
        d.close()
        if fsck:
            r = subprocess.run([fsck, "-f", "-n", yol], capture_output=True,
                               text=True, env=ortam)
            assert r.returncode == 0 and "appears to be OK" in r.stdout, \
                f"{etiket}: {r.stdout}"

    adlar = ["dosya", "Dosya", "çğüş", "İstanbul", "ısık", "ÉLAN", "émile",
             "a:b", "Ω", "Straße", "日本語"]
    for boyut, duyarli, tohum in ((20_000_000, False, 5), (12_000_000, True, 6)):
        yol = img_path("t61.img")
        if os.path.exists(yol):
            os.unlink(yol)
        with open(yol, "wb") as fh:
            fh.truncate(boyut)
        d = DiskImage(yol)
        format_hfsplus(d, label="Yazma", case_sensitive=duyarli)
        d.close()
        r = random.Random(tohum)
        beklenen, klasorler, dolu = {}, ["/"], 0
        d = DiskImage(yol)
        w = HfsWriter(HfsPlusFS(d))
        for i in range(1500):
            op = r.random()
            if op < 0.1 and len(klasorler) < 40:
                p = r.choice(klasorler).rstrip("/") + f"/k{i}_{r.choice(adlar)}"
                w.mkdir(p)
                klasorler.append(p)
            elif op < 0.78 or not beklenen:
                p = r.choice(klasorler).rstrip("/") + f"/{r.choice(adlar)}_{i:05d}"
                veri = r.randbytes(r.choice([0, 1, 4096, 5000, 70000, 300000]))
                try:
                    w.write_file(p, veri)
                    beklenen[p] = veri
                except HfsWriteError:
                    dolu += 1                           # birim doldu: geri alindi
                    for q in r.sample(sorted(beklenen), min(5, len(beklenen))):
                        w.remove(q)
                        del beklenen[q]
            elif op < 0.9:
                p = r.choice(sorted(beklenen))
                w.remove(p)
                del beklenen[p]
            else:
                p = r.choice(sorted(beklenen))
                yeni = r.choice(klasorler).rstrip("/") + f"/tasindi_{i}"
                w.rename(p, yeni)
                beklenen[yeni] = beklenen.pop(p)
        w.flush()
        fs = w.fs
        parcali = sum(1 for p in beklenen
                      if len(fs.fork_runs(fs.resolve(p).data, fs.resolve(p).cnid)) > 8)
        assert dolu and parcali, (dolu, parcali)                # senaryolar sinandi
        assert fs.catalog.depth >= 2
        d.close()
        denetle(yol, beklenen, f"yazma {boyut}")

        d = DiskImage(yol)
        w = HfsWriter(HfsPlusFS(d))
        for e in list(w.fs.listdir("/")):
            w.remove("/" + e.name, recursive=True)
        w.flush()
        assert w.fs.listdir("/") == [] and (w.fs.file_count, w.fs.folder_count) == (0, 0)
        d.close()
        denetle(yol, {}, f"hepsi silindi {boyut}")

    # arayuz yolu + reddetme
    yol = img_path("t61_gpt.img")
    s = DiskSession.create(yol, 40 * MIB, overwrite=True)
    s.create_table("gpt")
    s.create_partition(2048, 32 * MIB // 512, fs_key="hfsplus", label="Mac")
    erisim = s.filesystem(1)
    assert isinstance(erisim, HfsAccess) and erisim.writable, erisim.write_reason
    erisim.mkdir("/Belgeler")
    erisim.write_file("/Belgeler/not.txt", "Türkçe not\n".encode("utf-8"))
    erisim.rename("/Belgeler/not.txt", "Not (yeni).txt")
    assert erisim.read("/belgeler/NOT (YENİ).TXT".replace("İ", "I")) == \
        "Türkçe not\n".encode("utf-8")
    s.close()
    with open(yol, "r+b") as fh:                       # "temiz kapatildi" bitini sil
        fh.seek(2048 * 512 + 1024 + 4)
        attrs = struct.unpack(">I", fh.read(4))[0]
        fh.seek(2048 * 512 + 1024 + 4)
        fh.write(struct.pack(">I", attrs & ~0x100))
    s = DiskSession.open(yol)
    erisim = s.filesystem(1)
    assert not erisim.writable and erisim.write_reason
    s.close()


@test
def t62_udf_okuma():
    """UDF salt okuma: Windows'un yazdigi 2.01, genisoimage 1.02 koprusu, yedekli CD-RW (ADR 0063)

    * `udf_windows.img.gz` — mkudffs (UDF 2.01, 512 bayt blok, MBR) birimine
      **Windows 10'un kendi UDF surucusu** 402 dosya yazdi (EFE, kisa ve
      gomulu kapsam, 32 KB'lik dizin, Turkce adlar). SHA-1 listesi Windows'ta
      alindi (`udf_windows.sha1.txt`).
    * `udf_genisoimage.iso.gz` — UDF 1.02 + ISO koprusu (FE); icerik asagidaki
      uretecle birebir.
    * `udf_cdrw_bos.img.gz` — mkudffs CD-RW: yedekli (sparable) bolum haritasi.
    """
    import gzip
    import hashlib
    from diskultimate.core.udf import UdfFS
    from diskultimate.core.filesystem import UdfAccess, open_filesystem

    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

    def ac(ad, hedef):
        with gzip.open(os.path.join(fixtures, ad), "rb") as a, open(hedef, "wb") as b:
            b.write(a.read())
        return hedef

    # --- 1. Windows'un yazdigi UDF 2.01 (MBR bolumu) ---------------------
    yol = ac("udf_windows.img.gz", img_path("t62_win.img"))
    s = DiskSession.open(yol, readonly=True)
    erisim = s.filesystem(1)
    assert isinstance(erisim, UdfAccess) and erisim.label == "WinUDF", erisim
    assert not erisim.writable and erisim.write_reason
    with open(os.path.join(fixtures, "udf_windows.sha1.txt"), encoding="utf-8") as fh:
        satirlar = fh.read().splitlines()
    assert len(satirlar) == 402
    for satir in satirlar:
        ozet, p = satir.split(" ", 1)
        assert hashlib.sha1(erisim.read(p)).hexdigest() == ozet, p
    assert {n.name for n in erisim.listdir("/")} >= {"Boş", "Klasör", "kök.txt"}
    assert len(erisim.listdir("/KLASÖR")) == 401               # buyuk/kucuk harf duyarsiz
    hedef = img_path("t62_cikti")
    shutil.rmtree(hedef, ignore_errors=True)
    erisim.extract("/Klasör/Alt Klasör ğüşİı", hedef)
    assert os.path.getsize(os.path.join(hedef, "büyük dosya.bin")) == 400000
    st = erisim.stats()
    assert 0 < st["used_bytes"] < st["total_bytes"], st
    s.close()

    # --- 2. genisoimage UDF 1.02 koprusu: UDF agaci ISO'ya tercih edilir ---
    yol = ac("udf_genisoimage.iso.gz", img_path("t62_g.iso"))
    beklenen = {"/a.txt": b"merhaba\n",
                "/klasor/desen.bin": bytes(range(256)) * 3000,
                "/klasor/Türkçe ğüşİı dosya adı uzun.txt": "içerik\n".encode("utf-8")}
    for i in range(300):
        beklenen[f"/klasor/alt/f_{i:03d}.dat"] = bytes([i % 256]) * (i * 37)
    d = DiskImage(yol, readonly=True)
    erisim = open_filesystem(d)
    assert isinstance(erisim, UdfAccess) and erisim.label == "UDFKOPRU"
    for p, veri in beklenen.items():
        assert erisim.read(p) == veri, p
    assert erisim.fs.revision == 0x102
    assert sorted(n.name for n in erisim.listdir("/")) == ["a.txt", "bos", "klasor"]
    d.close()

    # --- 3. yedekli (sparable) bolum haritasi -----------------------------
    d = DiskImage(ac("udf_cdrw_bos.img.gz", img_path("t62_cd.img")), readonly=True)
    fs = UdfFS(d)
    assert fs.label == "YedekliCD" and fs.partitions[0].kind == "sparable"
    assert fs.partitions[0].packet == 32 and fs.listdir("/") == []
    assert detect(d).fs_type == "UDF"
    d.close()


@test
def t63_udf_bicimlendirme():
    """UDF 2.01 bicimlendirme (saf Python): mkudffs yerlesimi, etiket CRC'leri (ADR 0064)

    Yerlesim mkudffs -m hd -r 2.01 ile olculdu; 16M/512'de kullanilan 11,
    bos 32237 blok birebir. Her tanimlayicinin etiket saglamasi, CRC-16'si
    ve etiket konumu dogrulanir (Windows/udf surucusu yanlis etiketli
    tanimlayiciyi yok sayar). Windows 10 bu birimleri (MBR ve GPT) UDF
    olarak baglayip 301 dosya yazdi, birim Healthy kaldi (olculdu, bkz. ADR).
    udfinfo varsa onun gozunde de ayni sayilar.
    """
    from diskultimate.core.udf import UdfFS
    from diskultimate.core.udfformat import crc16, format_udf
    from diskultimate.core.filesystem import UdfAccess

    def etiket_dogru(blok: bytes, konum: int) -> int:
        kimlik, _surum, saglama, _r, _seri, crc, crc_boy, yer = \
            struct.unpack_from("<HHBBHHHI", blok, 0)
        assert saglama == sum(blok[i] for i in range(16) if i != 4) & 0xFF, kimlik
        assert crc == crc16(blok[16:16 + crc_boy]), kimlik
        assert yer == konum, (kimlik, yer, konum)
        return kimlik

    udfinfo, ortam = _dev_tool("udfinfo")
    for boyut, bs, kullanilan, bos in ((16 * MIB, 512, 11, 32237),
                                       (64 * MIB, 4096, 4, 15860),
                                       (5_000_000, 512, 6, 9239)):
        yol = img_path("t63.img")
        if os.path.exists(yol):
            os.unlink(yol)
        with open(yol, "wb") as fh:
            fh.truncate(boyut)
        d = DiskImage(yol)
        sonuc = format_udf(d, label="Türkçe ğüşİı UDF", block_size=bs)
        d.close()
        assert sonuc["free_blocks"] == bos, (boyut, sonuc)
        n = boyut // bs
        with open(yol, "rb") as fh:
            veri = fh.read()
        blok = lambda b: veri[b * bs:b * bs + 512]           # noqa: E731
        assert [veri[32768 + i * max(2048, bs) + 1:32768 + i * max(2048, bs) + 6]
                for i in range(3)] == [b"BEA01", b"NSR03", b"TEA01"]
        for capa in (256, n - 257, n - 1):
            assert etiket_dogru(blok(capa), capa) == 2
        for taban in (96, n - 160):
            kimlikler = [etiket_dogru(blok(taban + i), taban + i) for i in range(6)]
            assert kimlikler == [1, 6, 5, 7, 4, 8], kimlikler
        assert etiket_dogru(blok(128), 128) == 9
        # bolum: alan bitmap'i (0), ardindan FSD, akis dizini, kok (bolume gore konum)
        assert etiket_dogru(blok(257), 0) == 264
        sbd = (24 + (sonuc["partition_blocks"] + 7) // 8 + bs - 1) // bs
        for goreli, beklenen in ((sbd, 256), (sbd + 1, 266), (sbd + 2, 266)):
            assert etiket_dogru(blok(257 + goreli), goreli) == beklenen
        d = DiskImage(yol, readonly=True)
        fs = UdfFS(d)
        assert fs.label == "Türkçe ğüşİı UDF" and fs.listdir("/") == []
        assert fs.revision == 0x201 and fs.block_size == bs
        d.close()
        if udfinfo:
            r = subprocess.run([udfinfo, yol], capture_output=True, text=True, env=ortam)
            alanlar = dict(s.split("=", 1) for s in r.stdout.splitlines() if "=" in s)
            assert (alanlar.get("usedblocks"), alanlar.get("freeblocks"),
                    alanlar.get("integrity"), alanlar.get("udfrev")) == \
                (str(kullanilan), str(bos), "closed", "2.01"), alanlar

    for sema in ("mbr", "gpt"):
        yol = img_path(f"t63_{sema}.img")
        s = DiskSession.create(yol, 32 * MIB, overwrite=True)
        s.create_table(sema)
        s.create_partition(2048, 28 * MIB // 512, fs_key="udf", label="Ortak")
        erisim = s.filesystem(1)
        assert isinstance(erisim, UdfAccess) and erisim.label == "Ortak"
        if sema == "mbr":
            assert s.table.get(1).type_id == 0x07
        s.close()


def _udf_denetle(dev) -> tuple:
    """UDF tutarlilik denetimi (Linux'ta udf fsck yok): her giris/FID/AED
    etiketinin saglamasi, CRC'si ve konumu; kokten ulasilan bloklar bitmap'teki
    dolu bloklarla **birebir** (sizinti ya da cifte kullanim yok); dizin bag
    sayisi = 1 + alt dizin; LVID kapali, bos alan ve dosya/dizin sayilari
    tutarli. (dosya, dizin) dondurur."""
    from diskultimate.core.udf import UdfFS
    from diskultimate.core.udfformat import crc16
    from diskultimate.core.udfwrite import UdfWriter

    def etiket(b, yer):
        kimlik, _v, sag, _r, _s, crc, boy, konum = struct.unpack_from("<HHBBHHHI", b, 0)
        assert sag == sum(b[i] for i in range(16) if i != 4) & 0xFF, ("saglama", kimlik)
        assert crc == crc16(bytes(b[16:16 + boy])), ("crc", kimlik, yer)
        assert konum == yer, ("konum", kimlik, konum, yer)
        return kimlik

    fs = UdfFS(dev)
    w = UdfWriter(fs)
    bs = fs.block_size
    dolu = set()
    boy, yer = fs.pd_detail[fs.partitions[0].number]["space_bitmap"]
    dolu.update(range(yer, yer + (boy + bs - 1) // bs))
    sayac = [0, 0]

    def giris(blk):
        ham = fs._read_logical(blk, 0)
        etiket(ham, blk)
        assert blk not in dolu, ("cift", blk)
        dolu.add(blk)
        veri, aed = w._split_runs(ham)
        for s0, c in veri + aed:
            for b in range(s0, s0 + c):
                assert b not in dolu, ("cift kullanim", b)
                dolu.add(b)
        for s0, _c in aed:
            assert etiket(fs._read_logical(s0, 0), s0) == 258
        return ham, [s0 + i for s0, c in veri for i in range(c)]

    def gez(blk, ust):
        ham, bloklar = giris(blk)
        veri = fs._read_entry(fs._file_entry(blk, 0))
        pos, alt, ust_var = 0, 0, False
        while pos + 38 <= len(veri):
            l_fi = veri[pos + 19]
            l_iu = struct.unpack_from("<H", veri, pos + 36)[0]
            n = (38 + l_iu + l_fi + 3) & ~3
            fid = veri[pos:pos + n]
            etiket(fid, bloklar[pos // bs] if bloklar else blk)
            ozellik, cblk = fid[18], struct.unpack_from("<I", fid, 24)[0]
            if ozellik & 4:
                pass                                     # silinmis FID
            elif ozellik & 8:
                assert cblk == ust
                ust_var = True
            elif ozellik & 2:
                alt += 1
                sayac[1] += 1
                gez(cblk, blk)
            else:
                sayac[0] += 1
                giris(cblk)
            pos += n
        assert ust_var and struct.unpack_from("<H", ham, 48)[0] == 1 + alt, blk

    for b in range(fs.root_icb[0]):                      # FSD, akis dizini
        ham = fs._read_logical(b, 0)
        if struct.unpack_from("<H", ham, 0)[0] in (256, 266):
            dolu.add(b)
    gez(fs.root_icb[0], fs.root_icb[0])
    w._load_bitmap()
    bitmap_dolu = {b for b in range(w._bits) if not w._is_free(b)}
    assert bitmap_dolu == dolu, (sorted(bitmap_dolu - dolu)[:5], sorted(dolu - bitmap_dolu)[:5])
    lvid = w._lvid()
    n = struct.unpack_from("<I", lvid, 72)[0]
    assert struct.unpack_from("<I", lvid, 28)[0] == 1                   # kapali
    assert struct.unpack_from("<I", lvid, 80)[0] == w._bits - len(dolu)
    assert struct.unpack_from("<II", lvid, 80 + 8 * n + 32) == (sayac[0], sayac[1] + 1)
    return tuple(sayac)


@test
def t64_udf_yazma():
    """UDF yazma: mkdir/yaz/sil/adlandir/tasi, AED zinciri, dolu birim, Windows birimi (ADR 0065)

    Denetim `_udf_denetle` ile (bitmap birebir, etiket/CRC, bag, LVID).
    Olculen: Windows 10 yazicimizin urettigi 2499 dosyayi (AED'liler dahil)
    birebir okudu, ustune yazdi (Healthy); Windows'un degistirdigi birime
    bizim yazmamiz da denetimden gecti ve Windows tekrar birebir okudu.
    """
    import gzip
    import random
    from diskultimate.core.udf import UdfFS
    from diskultimate.core.udfformat import format_udf
    from diskultimate.core.udfwrite import UdfWriteError, UdfWriter

    adlar = ["dosya", "Dosya", "çğüş", "İstanbul", "ısık", "Ω", "日本語", "uzun " * 20]
    for boyut, bs, tohum in ((6_000_000, 512, 5), (20_000_000, 4096, 4)):
        yol = img_path("t64.img")
        if os.path.exists(yol):
            os.unlink(yol)
        with open(yol, "wb") as fh:
            fh.truncate(boyut)
        d = DiskImage(yol)
        format_udf(d, label="Yazma", block_size=bs)
        d.close()
        r = random.Random(tohum)
        beklenen, klasorler, dolu, aedli = {}, ["/"], 0, 0
        d = DiskImage(yol)
        w = UdfWriter(UdfFS(d))
        for i in range(1500):
            op = r.random()
            if op < 0.1 and len(klasorler) < 30:
                p = r.choice(klasorler).rstrip("/") + f"/k{i}_{r.choice(adlar)[:30]}"
                w.mkdir(p)
                klasorler.append(p)
            elif op < 0.76 or not beklenen:
                p = r.choice(klasorler).rstrip("/") + f"/{r.choice(adlar)[:40]}_{i:05d}"
                veri = r.randbytes(r.choice([0, 1, 296, 297, 5000, 70000, 300000]))
                try:
                    w.write_file(p, veri)
                    beklenen[p] = veri
                except UdfWriteError:
                    dolu += 1
                    for q in r.sample(sorted(beklenen), min(5, len(beklenen))):
                        w.remove(q)
                        del beklenen[q]
            elif op < 0.88:
                p = r.choice(sorted(beklenen))
                w.remove(p)
                del beklenen[p]
            else:
                p = r.choice(sorted(beklenen))
                yeni = r.choice(klasorler).rstrip("/") + f"/tasindi_{i}"
                w.rename(p, yeni)
                beklenen[yeni] = beklenen.pop(p)
            if i % 100 == 99:
                w.flush()
        w.flush()
        fs = w.fs
        for p, veri in beklenen.items():
            e = fs.resolve(p)
            assert fs._read_entry(e) == veri, p
            aedli += len(e.extents) > (bs - 216) // 8
        assert dolu, "dolu birim senaryosu sinanmadi"
        if bs == 512:
            assert aedli, "AED zinciri sinanmadi"
        d.close()
        d = DiskImage(yol, readonly=True)
        assert _udf_denetle(d) == (len(beklenen), len(klasorler) - 1)
        d.close()
        d = DiskImage(yol)
        w = UdfWriter(UdfFS(d))
        for e in w.fs.listdir("/"):
            w.remove("/" + e.name, recursive=True)
        w.flush()
        d.close()
        d = DiskImage(yol, readonly=True)
        assert _udf_denetle(d) == (0, 0)
        d.close()

    # Windows'un yazdigi birime (silinmis FID'ler iceriyor) yazma
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    yol = img_path("t64_win.img")
    with gzip.open(os.path.join(fixtures, "udf_windows.img.gz"), "rb") as a, \
            open(yol, "wb") as b:
        b.write(a.read())
    s = DiskSession.open(yol)
    erisim = s.filesystem(1)
    assert erisim.writable, erisim.write_reason
    erisim.mkdir("/Klasör/Bizim")
    erisim.write_file("/Klasör/Bizim/not.txt", "Türkçe\n".encode("utf-8"))
    erisim.remove("/Klasör/dosya_050_ğüşİı.bin")
    erisim.rename("/kök.txt", "/Klasör/Bizim/taşınan kök.txt")
    assert erisim.read("/klasör/bizim/taşınan KÖK.TXT") == \
        "Türkçe kök dosyası\r\n".encode("utf-8")
    bas = s.table.get(1).start_lba
    adet = s.table.get(1).sector_count
    s.close()
    d = DiskImage(yol, readonly=True)
    dosya, dizin = _udf_denetle(PartitionView(d, bas, adet))
    assert dosya >= 400 and dizin >= 4, (dosya, dizin)
    d.close()


def _xfs_kaynak(kok: str) -> dict:
    """t65'in XFS test agaci: kisa bicim / dugum dizini, B-agacli seyrek dosya,
    yerel ve uzak sembolik bag, Turkce ad. {yol: veri} (bag icin "->hedef")."""
    beklenen = {"/a.txt": b"merhaba\n",
                "/Türkçe ğüşİı dosya.txt": "içerik\n".encode("utf-8"),
                "/desen.bin": bytes(range(256)) * 4000}
    for i in range(3):
        beklenen[f"/kisa/k{i}.txt"] = str(i).encode()
    for i in range(2000):
        beklenen[f"/node/uzun_bir_dosya_adi_{i:05d}.txt"] = bytes([i % 256]) * (i % 50)
    seyrek = bytearray((299 * 3 + 1) * 4096)
    for i in range(300):
        seyrek[i * 3 * 4096:(i * 3 + 1) * 4096] = bytes([i % 251]) * 4096
    beklenen["/seyrek.bin"] = bytes(seyrek)
    if kok:
        for yol, veri in beklenen.items():
            hedef = os.path.join(kok, yol.lstrip("/"))
            os.makedirs(os.path.dirname(hedef), exist_ok=True)
            with open(hedef, "wb") as fh:
                if yol == "/seyrek.bin":
                    for i in range(300):
                        fh.seek(i * 3 * 4096)
                        fh.write(bytes([i % 251]) * 4096)
                else:
                    fh.write(veri)
        os.symlink("a.txt", os.path.join(kok, "kisa_bag"))
        os.symlink("/" + "uzun_hedef/" * 40 + "son", os.path.join(kok, "uzun_bag"))
    return beklenen


@test
def t65_xfs_okuma():
    """XFS salt okuma (v4/v5): kisa bicim/blok/dugum dizin, B-agacli catal, bag (ADR 0066)

    `xfs_v5.img.gz`: mkfs.xfs 6.18 `-p dizin` ile uretildi (v5, FTYPE, bigtime,
    NREXT64, seyrek inode). 2000 girdilik dizin ve 300 kapsamli seyrek dosya
    B-agacli; uzun sembolik bag uzak blokta. mkfs.xfs varsa v4, 1 KiB blok ve
    16 KiB dizin blogu varyantlari da uretilip sinanir.
    """
    import gzip
    from diskultimate.core.xfs import XfsFS
    from diskultimate.core.filesystem import XfsAccess, open_filesystem

    beklenen = _xfs_kaynak("")
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

    def denetle(yol, etiket):
        d = DiskImage(yol, readonly=True)
        erisim = open_filesystem(d)
        assert isinstance(erisim, XfsAccess) and erisim.label == etiket, erisim
        assert not erisim.writable and erisim.write_reason
        for p, veri in beklenen.items():
            assert erisim.read(p) == veri, (etiket, p)
        fs = erisim.fs
        assert fs.resolve("/kisa_bag").symlink == "a.txt"
        assert fs.resolve("/uzun_bag").symlink == "/" + "uzun_hedef/" * 40 + "son"
        assert len(fs.listdir("/node")) == 2000
        assert sorted(n.name for n in erisim.listdir("/kisa")) == ["k0.txt", "k1.txt", "k2.txt"]
        assert fs.inode(fs.resolve("/seyrek.bin").ino)[5] == 3        # B-agaci
        st = erisim.stats()
        assert 0 < st["used_bytes"] < st["total_bytes"]
        d.close()
        return fs

    yol = img_path("t65.img")
    with gzip.open(os.path.join(fixtures, "xfs_v5.img.gz"), "rb") as a, open(yol, "wb") as b:
        b.write(a.read())
    fs = denetle(yol, "XFSFIX")
    assert fs.version == 5 and fs.ftype

    mkfs = shutil.which("mkfs.xfs") or shutil.which("mkfs.xfs", path="/sbin:/usr/sbin")
    if not mkfs:
        return
    kok = img_path("t65_kaynak")
    shutil.rmtree(kok, ignore_errors=True)
    _xfs_kaynak(kok)
    for secenek in (["-m", "crc=0"], ["-b", "size=1024"], ["-n", "size=16384"]):
        yol = img_path("t65_v.img")
        if os.path.exists(yol):
            os.unlink(yol)
        with open(yol, "wb") as fh:
            fh.truncate(320 * MIB)
        r = subprocess.run([mkfs, "-q", *secenek, "-L", "VARYANT", "-p", kok, yol],
                           capture_output=True, text=True)
        _arac_eski_mi(r, "mkfs.xfs", "-p <klasor>")
        if r.returncode:
            if "crc=0" in secenek:                # v4 derleme disi olabilir
                continue
            raise AssertionError(r.stderr)
        denetle(yol, "VARYANT")


@test
def t66_xfs_bicimlendirme():
    """XFS v5 bicimlendirme (saf Python): mkfs.xfs ile bayt bayt ayni, xfs_repair temiz (ADR 0067)

    Geometri kurallari mkfs.xfs 6.18 -N ciktisindan (asagidaki tablo, arac
    gerekmeden her platformda). mkfs.xfs varsa ayni UUID/etiketle uretilen
    birim inode obegi (zaman/nesil/CRC) disinda birebir olmali; xfs_repair
    -n temiz. Bicimlendirilen birim okuyucumuzla acilmali.
    """
    import uuid as _uuid
    from diskultimate.core.xfsformat import Geometry, XfsFormatter
    from diskultimate.core.xfs import XfsFS

    # boyut (bayt) -> (agcount, agsize, dblocks, logblocks, imaxpct)  [mkfs -N]
    olculen = {
        300 * MIB: (4, 19200, 76800, 16384, 25),
        333333333: (4, 20345, 81380, 16384, 25),
        7777777777: (4, 474718, 1898871, 16384, 25),
        512 << 30: (4, 33554432, 134217728, 65536, 25),
        1 << 40: (4, 67108864, 268435456, 131072, 5),
        1100000000000: (4, 67138672, 268554687, 131130, 5),
        4400000000000: (5, 268435455, 1074218750, 521728, 5),
        5 << 40: (5, 268435455, 1342177275, 521728, 5),
    }
    for boyut, beklenen in olculen.items():
        g = Geometry(boyut)
        assert (g.agcount, g.agsize, g.dblocks, g.logblocks, g.imax_pct) == beklenen, \
            (boyut, g.agcount, g.agsize, g.dblocks, g.logblocks, g.imax_pct)

    yol = img_path("t66.img")
    kimlik = _uuid.UUID("92e5fb8e-1c8f-44e0-9dc0-0319d5cb83b2")
    for boyut in (320 * MIB, 333333333):
        if os.path.exists(yol):
            os.unlink(yol)
        with open(yol, "wb") as fh:
            fh.truncate(boyut)
        d = DiskImage(yol)
        XfsFormatter(d, label="REF", uuid=kimlik.bytes).format()
        d.close()
        d = DiskImage(yol, readonly=True)
        fs = XfsFS(d)
        assert (fs.label, fs.version, fs.listdir("/")) == ("REF", 5, [])
        assert fs.stats()["free_bytes"] > 0
        d.close()
        mkfs = shutil.which("mkfs.xfs") or shutil.which("mkfs.xfs", path="/sbin:/usr/sbin")
        repair = shutil.which("xfs_repair") or shutil.which("xfs_repair", path="/sbin:/usr/sbin")
        if repair:
            r = subprocess.run([repair, "-n", yol], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr[-500:]
        if mkfs:
            ref = img_path("t66_ref.img")
            if os.path.exists(ref):
                os.unlink(ref)
            with open(ref, "wb") as fh:
                fh.truncate(boyut)
            r = subprocess.run([mkfs, "-q", "-m", "crc=1,finobt=1,rmapbt=0,reflink=0,"
                                "bigtime=1,inobtcount=1,metadir=0", "-i",
                                "sparse=0,nrext64=0,exchange=0", "-n", "ftype=1,parent=0",
                                "-m", f"uuid={kimlik}", "-L", "REF", ref],
                               capture_output=True, text=True)
            if r.returncode == 0:
                with open(ref, "rb") as a, open(yol, "rb") as b:
                    blok = 0
                    while True:
                        x, y = a.read(4096 * 64), b.read(4096 * 64)
                        if not x:
                            break
                        if x != y:
                            for k in range(0, len(x), 4096):
                                if x[k:k + 4096] != y[k:k + 4096]:
                                    assert 12 <= blok + k // 4096 < 20, \
                                        (boyut, "fark blogu", blok + k // 4096)
                        blok += 64

    s = DiskSession.create(img_path("t66_gpt.img"), 340 * MIB, overwrite=True)
    s.create_table("gpt")
    s.create_partition(2048, (340 * MIB - 2 * MIB) // 512, fs_key="xfs", label="LinuxVeri")
    erisim = s.filesystem(1)
    assert erisim.fs_type == "XFS" and erisim.label == "LinuxVeri"
    s.close()


@test
def t67_xfs_buyutme():
    """XFS buyutme (saf Python): son AG uzatma + yeni AG, rmap/reflink, tasima (ADR 0068)

    DiskSession uzerinden: buyut, saga tasi, sola tasi + buyut; kucultme
    reddedilir. Her adimda icerik (okuyucumuz) ve varsa xfs_repair -n.
    mkfs.xfs varsa varsayilan ozellikli (rmapbt+reflink+sparse+nrext64),
    icerikli ve son AG'si kisa bir birim de buyutulur. Kirli gunluklu birim
    reddedilir.
    """
    from diskultimate.core.resize import ResizeError
    from diskultimate.core.xfsgrow import XfsGrowError, xfs_grow

    repair = shutil.which("xfs_repair") or shutil.which("xfs_repair", path="/sbin:/usr/sbin")
    mkfs = shutil.which("mkfs.xfs") or shutil.which("mkfs.xfs", path="/sbin:/usr/sbin")

    def onar(yol, bas, adet):
        if not repair:
            return
        parca = yol + ".part"
        with open(yol, "rb") as a, open(parca, "wb") as b:
            a.seek(bas * 512)
            kalan = adet * 512
            while kalan:
                blok = a.read(min(8 * MIB, kalan))
                b.write(blok)
                kalan -= len(blok)
        try:
            r = subprocess.run([repair, "-n", parca], capture_output=True, text=True)
            assert r.returncode == 0, r.stdout[-600:] + r.stderr[-300:]
        finally:
            os.unlink(parca)

    yol = img_path("t67.img")
    s = DiskSession.create(yol, 1400 * MIB, overwrite=True)
    s.create_table("gpt")
    s.create_partition(2048, 320 * MIB // 512, fs_key="xfs", label="BUYU")
    bilgi = s.resize_info(1)
    assert bilgi.kind == "xfs" and bilgi.min_sectors == 320 * MIB // 512, bilgi
    try:
        s.resize_partition(1, 2048, 300 * MIB // 512, confirm=True)
        raise AssertionError("XFS kucultuldu")
    except (ResizeError, Exception) as exc:
        assert not isinstance(exc, AssertionError), exc
    adimlar = [(2048, 500), (2048 + 400 * MIB // 512, 500), (2048 + 100 * MIB // 512, 1100)]
    for bas, mib in adimlar:
        s.resize_partition(1, bas, mib * MIB // 512, confirm=True)
        s.close_filesystems()
        erisim = s.filesystem(1)
        assert erisim.fs_type == "XFS" and erisim.label == "BUYU"
        st = erisim.stats()
        assert st["total_bytes"] > (mib - 20) * MIB, (mib, st)
        s.close_filesystems()
        onar(yol, bas, mib * MIB // 512)
    s.close()

    if not mkfs:
        return
    kok = img_path("t67_kaynak")
    shutil.rmtree(kok, ignore_errors=True)
    beklenen = _xfs_kaynak(kok)
    from diskultimate.core.xfs import XfsFS
    yol = img_path("t67_mkfs.img")
    if os.path.exists(yol):
        os.unlink(yol)
    with open(yol, "wb") as fh:
        fh.truncate(320 * MIB)
    r = subprocess.run([mkfs, "-q", "-d", "agsize=24000b", "-p", kok, yol],
                       capture_output=True, text=True)
    _arac_eski_mi(r, "mkfs.xfs", "-p <klasor>")
    assert r.returncode == 0, r.stderr
    with open(yol, "r+b") as fh:
        fh.truncate(900 * MIB)
    d = DiskImage(yol)
    plan = xfs_grow(d, 900 * MIB)
    d.close()
    assert plan["new_agcount"] > plan["old_agcount"]
    d = DiskImage(yol, readonly=True)
    fs = XfsFS(d)
    for p, veri in beklenen.items():
        assert fs.read_file(p) == veri, p
    d.close()
    onar(yol, 0, 900 * MIB // 512)

    # kirli gunluk: son kaydin "unmount" bayragi silinir
    if os.path.exists(yol):
        os.unlink(yol)
    with open(yol, "wb") as fh:
        fh.truncate(320 * MIB)
    subprocess.run([mkfs, "-q", yol], check=True)
    with open(yol, "r+b") as fh:
        sb = fh.read(512)
        agb = struct.unpack_from(">I", sb, 84)[0]
        ls = struct.unpack_from(">Q", sb, 48)[0]
        agl = sb[124]
        fh.seek(((ls >> agl) * agb + (ls & ((1 << agl) - 1))) * 4096 + 512 + 9)
        fh.write(b"\x00")
        fh.truncate(400 * MIB)
    d = DiskImage(yol)
    try:
        xfs_grow(d, 400 * MIB)
        raise AssertionError("kirli gunluklu XFS buyutuldu")
    except XfsGrowError:
        pass
    d.close()


def _btrfs_kaynak(kok: str) -> dict:
    """t68 btrfs agaci: {yol: veri}; `kok` verilirse diske de yazar."""
    beklenen = {"/a.txt": b"merhaba\n",
                "/Türkçe ğüşİı dosya.txt": "içerik\n".encode("utf-8"),
                "/metin.txt": "".join(f"Lorem ipsum dolor sit amet, satir {i}\n"
                                      for i in range(8000)).encode(),
                "/desen.bin": bytes(range(256)) * 1500,
                "/alt_hacim/ic.txt": b"alt hacim icerigi\n"}
    for i in range(300):
        beklenen[f"/klasor/alt/f_{i:03d}.dat"] = bytes([i % 256]) * (i * 7)
    seyrek = bytearray(9 * 262144 + 5000)
    for i in range(10):
        seyrek[i * 262144:i * 262144 + 5000] = b"y" * 5000
    beklenen["/seyrek.bin"] = bytes(seyrek)
    if kok:
        for yol, veri in beklenen.items():
            hedef = os.path.join(kok, yol.lstrip("/"))
            os.makedirs(os.path.dirname(hedef), exist_ok=True)
            with open(hedef, "wb") as fh:
                if yol == "/seyrek.bin":
                    for i in range(10):
                        fh.seek(i * 262144)
                        fh.write(b"y" * 5000)
                else:
                    fh.write(veri)
        os.makedirs(os.path.join(kok, "bos"), exist_ok=True)
        os.symlink("a.txt", os.path.join(kok, "bag"))
    return beklenen


@test
def t68_btrfs_okuma():
    """btrfs salt okuma: zlib / LZO / zstd (saf cozuculer), alt hacim, seyrek (ADR 0069)

    Fixture'lar mkfs.btrfs 6.17 `--rootdir --subvol --compress` ile uretildi
    (satir ici ve diskteki kapsamlar sikistirilmis). LZO1X ve zstd cozuculeri
    saf Python; zstd ayrica zstd CLI ciktisiyla (varsa) sinanir. mkfs.btrfs
    varsa 4 KiB dugum (derin agac) + DUP ve karisik blok grubu varyantlari.
    """
    import gzip
    import random
    from diskultimate.core.btrfs import BtrfsFS
    from diskultimate.core.compress import _zstd_pure, lzo1x_decompress
    from diskultimate.core.filesystem import BtrfsAccess, open_filesystem

    beklenen = _btrfs_kaynak("")
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

    def denetle(yol, etiket):
        d = DiskImage(yol, readonly=True)
        erisim = open_filesystem(d)
        assert isinstance(erisim, BtrfsAccess) and erisim.label == etiket, erisim
        assert not erisim.writable and erisim.write_reason
        for p, veri in beklenen.items():
            assert erisim.read(p) == veri, (etiket, p)
        fs = erisim.fs
        assert fs.resolve("/bag").symlink == "a.txt"
        assert fs.resolve("/alt_hacim").subvolume
        assert sorted(n.name for n in erisim.listdir("/")) == sorted(
            ["a.txt", "Türkçe ğüşİı dosya.txt", "metin.txt", "desen.bin", "klasor",
             "alt_hacim", "seyrek.bin", "bos", "bag"])
        sikistirilmis = sum(1 for k, v in fs._walk(fs._roots[5], (0, 0, 0))
                            if k[1] == 108 and v[16])
        d.close()
        return sikistirilmis

    for tur in ("zlib", "lzo", "zstd"):
        yol = img_path(f"t68_{tur}.img")
        with gzip.open(os.path.join(fixtures, f"btrfs_{tur}.img.gz"), "rb") as a, \
                open(yol, "wb") as b:
            b.write(a.read())
        assert denetle(yol, "FX" + tur) > 100, tur

    # LZO1X: elle kurulmus kucuk akislar (literal + eslesme + son isaret)
    assert lzo1x_decompress(bytes([17 + 5]) + b"abcde" + bytes([0x11, 0, 0])) == b"abcde"

    zstd = shutil.which("zstd")
    if zstd:
        r = random.Random(7)
        veri = beklenen["/metin.txt"] + r.randbytes(50000) + bytes(100000)
        for seviye in ("-1", "-19", "--fast=3"):
            sik = subprocess.run([zstd, "-q", "-c", *seviye.split()], input=veri,
                                 capture_output=True).stdout
            assert _zstd_pure(sik) == veri, seviye

    mkfs = shutil.which("mkfs.btrfs") or shutil.which("mkfs.btrfs", path="/sbin:/usr/sbin")
    if not mkfs:
        return
    kok = img_path("t68_kaynak")
    shutil.rmtree(kok, ignore_errors=True)
    _btrfs_kaynak(kok)
    for secenek in (["-n", "4096", "--compress", "zstd", "-m", "dup"],
                    ["--mixed", "--compress", "lzo"]):
        yol = img_path("t68_v.img")
        if os.path.exists(yol):
            os.unlink(yol)
        with open(yol, "wb") as fh:
            fh.truncate(200 * MIB)
        r = subprocess.run([mkfs, "-q", "-f", "-L", "VARYANT", "--rootdir", kok,
                            "--subvol", "rw:alt_hacim", *secenek, yol],
                           capture_output=True, text=True)
        _arac_eski_mi(r, "mkfs.btrfs", "--subvol")
        assert r.returncode == 0, r.stderr
        denetle(yol, "VARYANT")


@test
def t69_f2fs_okuma():
    """F2FS salt okuma: satir ici veri/dizin, dolayli dugumler, NAT gunlugu, LZ4 (ADR 0070)

    `f2fs.img.gz`: mkfs.f2fs + sload.f2fs 1.16 (extra_attr, inode_checksum):
    600 girdilik dizin, 16 MB seyrek dosya (dolayli dugum), satir ici veri,
    satir disi uzun sembolik bag. NAT gunlugu ve ikinci NAT kopyasi yalnizca
    cekirdek yazinca olusur; burada birim elle degistirilerek okuyucunun bu
    yollari izledigi sinanir (oz-tutarlilik, cekirdek ciktisi degil).
    LZ4 blok cozucusu lz4 CLI varsa onun ciktisiyla sinanir. F2FS
    sikistirmasi icin uretici yok (sload LZ4/LZO'suz derlenmis) — sinanmadi.
    """
    import gzip
    import random
    from diskultimate.core.f2fs import (BLOCK, NAT_ENTRY_SIZE, NAT_PER_BLOCK, F2fsFS,
                                        lz4_block_decompress)
    from diskultimate.core.filesystem import F2fsAccess, open_filesystem

    beklenen = {"/a.txt": b"merhaba\n", "/kucuk.txt": b"x" * 3000,
                "/Türkçe ğüşİı dosya.txt": "içerik\n".encode("utf-8"),
                "/desen.bin": bytes(range(256)) * 16000}
    for i in range(600):
        beklenen[f"/buyukdizin/uzun_bir_dosya_adi_{i:04d}.txt"] = bytes([i % 256]) * (i % 300)
    seyrek = bytearray(15 * 1048576 + 5000)
    for i in range(16):
        seyrek[i * 1048576:i * 1048576 + 5000] = b"z" * 5000
    beklenen["/seyrek.bin"] = bytes(seyrek)

    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    yol = img_path("t69.img")
    with gzip.open(os.path.join(fixtures, "f2fs.img.gz"), "rb") as a, open(yol, "wb") as b:
        b.write(a.read())

    def denetle(etiket):
        d = DiskImage(yol, readonly=True)
        erisim = open_filesystem(d)
        assert isinstance(erisim, F2fsAccess) and erisim.label == "F2FSFIX", erisim
        assert not erisim.writable and erisim.write_reason
        for p, veri in beklenen.items():
            assert erisim.read(p) == veri, (etiket, p)
        fs = erisim.fs
        assert fs.resolve("/bag").symlink == "a.txt"
        assert fs.resolve("/uzun_bag").symlink == "/" + "uzun/" * 200 + "son"
        assert len(fs.listdir("/buyukdizin")) == 600
        assert fs.listdir("/klasor") == []
        d.close()
        return fs

    fs = denetle("ozgun")
    # --- NAT gunlugu ve ikinci kopya: elle ---------------------------------
    hedef = fs.resolve("/desen.bin").ino
    blok_ofs = hedef // NAT_PER_BLOCK
    sbs = fs.bps
    birinci = fs.nat_blkaddr + ((blok_ofs >> fs.log_bps) << fs.log_bps << 1) + (blok_ofs & (sbs - 1))
    with open(yol, "r+b") as fh:
        fh.seek(birinci * BLOCK)
        nat = bytearray(fh.read(BLOCK))
        eski = struct.unpack_from("<I", nat, (hedef % NAT_PER_BLOCK) * NAT_ENTRY_SIZE + 5)[0]
        # 1) giris NAT blogunda sifirlanir, gunluge yazilir
        struct.pack_into("<I", nat, (hedef % NAT_PER_BLOCK) * NAT_ENTRY_SIZE + 5, 0)
        # 2) NAT blogu ikinci kopyaya tasinir, birinci bozulur, bitmap biti kurulur
        fh.seek((birinci + sbs) * BLOCK)
        fh.write(bytes(nat))
        fh.seek(birinci * BLOCK)
        fh.write(b"\xEE" * BLOCK)
        fh.seek(fs.cp_blkaddr * BLOCK)                 # gecerli checkpoint paketi
        cp = fs.cp_blkaddr if fh.read(8) == fs.cp[:8] else fs.cp_blkaddr + sbs
        sit_boy, nat_boy = struct.unpack_from("<II", fs.cp, 156)
        fh.seek(cp * BLOCK + 192 + sit_boy + (blok_ofs >> 3))
        bayt = fh.read(1)[0] | (0x80 >> (blok_ofs & 7))
        fh.seek(cp * BLOCK + 192 + sit_boy + (blok_ofs >> 3))
        fh.write(bytes([bayt]))
        ozet_bas = struct.unpack_from("<I", fs.cp, 140)[0]
        fh.seek((cp + ozet_bas) * BLOCK + 3584)
        gunluk = bytearray(fh.read(507))
        n = struct.unpack_from("<H", gunluk, 0)[0]
        struct.pack_into("<IBII", gunluk, 2 + 13 * n, hedef, 0, hedef, eski)
        struct.pack_into("<H", gunluk, 0, n + 1)
        fh.seek((cp + ozet_bas) * BLOCK + 3584)
        fh.write(bytes(gunluk))
    fs = denetle("gunluk + ikinci kopya")
    assert hedef in fs._nat_journal

    lz4, _ortam = _dev_tool("lz4")
    if lz4:
        r = random.Random(5)
        for veri in (beklenen["/desen.bin"][:300000], r.randbytes(80000),
                     "".join(f"satir {i}\n" for i in range(9000)).encode()):
            kare = subprocess.run([lz4, "-q", "-c", "-B4", "-BI", "--no-frame-crc"],
                                  input=veri, capture_output=True).stdout
            pos = 7 + (8 if kare[4] & 0x08 else 0)
            cikti = bytearray()
            while True:
                boy = struct.unpack_from("<I", kare, pos)[0]
                pos += 4
                if not boy:
                    break
                ham, boy = boy & 0x80000000, boy & 0x7FFFFFFF
                parca = kare[pos:pos + boy]
                cikti += parca if ham else lz4_block_decompress(parca, 1 << 20)
                pos += boy
            assert bytes(cikti) == veri


@test
def t70_apfs_okuma():
    """APFS salt okuma: macOS'un urettigi kapsayici, sifreli birim reddi, mkapfs (ADR 0071)

    `apfs_dfvfs.raw.gz` macOS'ta olusturulmus (log2timeline/dfvfs, Apache 2.0;
    kaynaklar tests/fixtures/KAYNAKLAR.md). Beklenen degerler bagimsiz okuyucu
    libfsapfs (pyfsapfs 20240429) ile alindi: kip, boyut, SHA-1, bag hedefi.
    `apfs_dfvfs_sifreli.dmg.gz`: GPT icinde sifreli birim — acik ret.
    `apfs_mkapfs.img.gz`: apfsprogs mkapfs (baska bir uygulama), bos birim.
    """
    import gzip
    import hashlib
    from diskultimate.core.apfs import ApfsError, fletcher64
    from diskultimate.core.filesystem import ApfsAccess, open_filesystem

    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

    def ac(ad, hedef):
        with gzip.open(os.path.join(fixtures, ad), "rb") as a, open(hedef, "wb") as b:
            b.write(a.read())
        return hedef

    # libfsapfs ile alinan beklenen degerler
    beklenen = {
        "/passwords.txt": (0o100644, 116, "677bc4ec72665fabac0bc91cd4e423a535a0cae4"),
        "/a_link": (0o120755, 0, "->a_directory/another_file"),
        "/a_directory": (0o040755, 0, None),
        "/a_directory/a_resourcefork": (0o100644, 0, "da39a3ee5e6b4b0d3255bfef95601890afd80709"),
        "/a_directory/another_file": (0o100644, 22, "4bdb40dfd6ec75cb730e678b5d7786e30170c5fb"),
        "/a_directory/a_file": (0o100644, 53, "2f5fe1248105fbad54b76b361adbb49322386f8c"),
        "/.fseventsd": (0o040700, 0, None),
        "/.fseventsd/fseventsd-uuid": (0o100600, 36, "3067098ecf586c9dd2a87923960f1fa947b07ae2"),
        "/.fseventsd/000000001714941a": (0o100600, 164, "fe2e4aac31ed1cdabd903697389a96797e50dce9"),
        "/.fseventsd/000000001714941b": (0o100600, 72, "019f7ff228039b8f7a30a8996948122533de518d"),
    }
    d = DiskImage(ac("apfs_dfvfs.raw.gz", img_path("t70.raw")), readonly=True)
    blok0 = d.read(0, 4096)
    assert struct.unpack_from("<Q", blok0, 0)[0] == fletcher64(blok0)
    erisim = open_filesystem(d)
    assert isinstance(erisim, ApfsAccess) and erisim.label == "apfs_test"
    assert not erisim.writable and erisim.write_reason
    fs = erisim.fs
    goruldu = {}

    def gez(yol):
        for e in fs.listdir(yol or "/"):
            p = yol + "/" + e.name
            if e.symlink:
                goruldu[p] = (e.mode, e.size, "->" + e.symlink)
            elif e.is_dir:
                goruldu[p] = (e.mode, e.size, None)
                gez(p)
            else:
                goruldu[p] = (e.mode, e.size, hashlib.sha1(fs.read_file(p)).hexdigest())

    gez("")
    assert goruldu == beklenen, set(goruldu.items()) ^ set(beklenen.items())
    birim = fs.volumes[0]
    assert birim.case_insensitive
    assert fs.read_file("/A_DIRECTORY/Another_File") == fs.read_file("/a_directory/another_file")
    assert birim.xattr(fs.resolve("/a_directory/a_file").ino, "myxattr") == b"My extended attribute"
    assert len(birim.xattr(fs.resolve("/a_directory/a_resourcefork").ino,
                           "com.apple.ResourceFork")) == 17
    d.close()

    s = DiskSession.open(ac("apfs_dfvfs_sifreli.dmg.gz", img_path("t70.dmg")), readonly=True)
    assert s.table.scheme == "gpt"
    erisim = s.filesystem(1)
    assert isinstance(erisim, ApfsAccess) and erisim.fs.volumes[0].encrypted
    try:
        erisim.listdir("/")
        raise AssertionError("sifreli APFS birimi okundu")
    except ApfsError:
        pass
    s.close()

    d = DiskImage(ac("apfs_mkapfs.img.gz", img_path("t70_mk.img")), readonly=True)
    erisim = open_filesystem(d)
    assert isinstance(erisim, ApfsAccess) and erisim.label == "Türkçe Birim"
    assert erisim.listdir("/") == []
    d.close()


@test
def t71_refs_yerel_bicimlendirme():
    """ReFS: yalnizca Windows'un kendi araci, uygun surum ve fiziksel disk; digerlerinde gri + neden (ADR 0072)

    * Surum kurali (EditionID / InstallationType): Enterprise, Pro for
      Workstations, Server evet; Pro, Home (Core), Education, bilinmeyen hayir.
    * Format-Volume komutu: ReFS, disk/bolum numarasi tamsayi, etiketten
      tirnak/$/` temizlenir. Windows'ta PowerShell ayristiricisiyla
      **calistirilmadan** sozdizimi denetlenir.
    * Goruntu dosyasinda ve uygun olmayan sistemde ret; bolum eklenmez.
    * Windows'ta: gercek kayit defterinden surum okunur; test misafiri
      Windows 10 Pro oldugu icin ReFS gri olmali.
    """
    from diskultimate.core import platform as plat
    from diskultimate.core.formatter import FS_BY_KEY, FormatError, all_kinds, format_partition
    from diskultimate.core.session import SessionError

    tablo = {("Enterprise", "Client"): True, ("EnterpriseN", "Client"): True,
             ("EnterpriseS", "Client"): True, ("ProfessionalWorkstation", "Client"): True,
             ("ServerStandard", "Server"): True, ("ServerDatacenter", ""): True,
             ("Professional", "Client"): False, ("Core", "Client"): False,
             ("Education", "Client"): False, ("", ""): False}
    for (surum, tur), beklenen in tablo.items():
        assert plat.refs_edition_allows(surum, tur) is beklenen, (surum, tur)

    komut = plat.format_volume_command(2, 3 * MIB, "refs", 'Veri"$x`')
    assert "-FileSystem ReFS" in komut and "-DiskNumber 2" in komut
    assert '"Verix"' in komut
    # bolum numarayla degil ofsetle secilir (Windows mantiksal bolumleri
    # kendi sirasiyla numaralar; ADR 0085)
    assert "-PartitionNumber" not in komut
    assert f"$_.Offset -eq {3 * MIB}" in komut and "throw" in komut

    tur = FS_BY_KEY["refs"]
    neden = dict((k.key, r) for k, r in all_kinds(1 << 30))["refs"]
    destek, destek_nedeni = plat.refs_format_support()
    assert tur.native_only and not tur.internal
    if destek:
        assert neden == "" and tur.available
    else:
        assert neden and neden == destek_nedeni and not tur.available

    yol = img_path("t71.img")
    d = DiskImage.create(yol, 64 * MIB, overwrite=True)
    try:
        format_partition(d, "refs", label="R")
        raise AssertionError("ReFS goruntu dosyasina bicimlendirildi")
    except FormatError:
        pass
    d.close()
    s = DiskSession.create(yol, 64 * MIB, overwrite=True)
    s.create_table("gpt")
    try:
        s.create_partition(2048, 100000, fs_key="refs", label="R")
        raise AssertionError("ReFS goruntu dosyasinda kabul edildi")
    except (SessionError, FormatError):
        pass
    assert len(s.table.partitions) == 0, "basarisiz bicimlendirmede bolum kaldi"
    s.close()

    if plat.IS_WINDOWS:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            surum = str(winreg.QueryValueEx(key, "EditionID")[0])
        assert surum in destek_nedeni or destek, (surum, destek_nedeni)
        ayristir = ("$e=$null; [void][System.Management.Automation.Language.Parser]::"
                    "ParseInput($env:DU_KOMUT, [ref]$null, [ref]$e); $e.Count")
        ortam = dict(os.environ, DU_KOMUT=komut)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            ayristir], capture_output=True, text=True, env=ortam)
        assert r.returncode == 0 and r.stdout.strip() == "0", r.stdout + r.stderr


@test
def t72_goruntu_konumu_guvenligi():
    """Yeni goruntu konumu: aygit klasoru yasak, fiziksel diskte belge klasoru (ADR 0073)

    2026-10-01: arayuz acik fiziksel diskin klasorunu (/dev) varsayilan yapti;
    20 GB goruntu devtmpfs'e yazildi, /dev doldu, ext4 tasimasi ENOSPC ile
    yarida kaldi. Artik: fiziksel disk/aygit yolunda kullanicinin belge
    klasoru onerilir; aygit/sozde dosya sistemi engellenir; bellek tabanli
    ya da yetersiz alanli hedef uyarilir.
    """
    from diskultimate.core import platform as plat
    ev = plat.user_home()
    assert os.path.isdir(ev)
    belge = plat.default_image_dir()
    assert belge.startswith(ev) and not plat.is_device_path(belge + "/")
    if plat.IS_WINDOWS:
        assert plat.is_device_path(r"\\.\PhysicalDrive0")
        assert plat.suggested_image_dir(r"\\.\PhysicalDrive0", True) == belge
    else:
        assert plat.is_device_path("/dev/nvme0n1") and not plat.is_device_path("/home/x.img")
        assert plat.suggested_image_dir("/dev/nvme0n1", True) == belge
        assert plat.suggested_image_dir("/dev/sda", False) == belge   # aygit yolu
        engel, _ = plat.image_location_problem("/dev", 20 << 30)
        assert engel, "/dev engellenmedi"
        if os.path.isdir("/proc"):
            assert plat.image_location_problem("/proc", 1)[0]
    # acik goruntunun klasoru korunur
    klasor = os.path.dirname(img_path("t72.img"))
    assert plat.suggested_image_dir(img_path("t72.img"), False) == os.path.abspath(klasor)
    # yetersiz alan: seyrekte uyari, seyrek degilse engel
    cok = 1 << 60
    engel, uyari = plat.image_location_problem(klasor, cok, sparse=True)
    assert not engel and uyari
    engel, _ = plat.image_location_problem(klasor, cok, sparse=False)
    assert engel
    assert plat.image_location_problem(klasor, 1 << 20) == ("", "")


@test
def t73_tasima_kesintisi_ve_seyreklik():
    """Bolum tasima: yarida kesinti dogru bildirilir, bos alan yazilmaz, yer on denetimi (ADR 0073)

    2026-10-01: ext4 bos alana sola tasinirken /dev (devtmpfs) doldu; tasima
    ENOSPC ile kesildi, kaynak bolumun basi ezilmisti ama arayuz yalnizca
    "0 adim uygulandi" dedi. Artik:
      * kesinti `MoveInterrupted` (kaynak saglam mi, kac sektor) — mesaj
        bolumun bozuk olabilecegini soyler;
      * hedefte ayni icerik varsa yazilmaz (seyrek goruntu sismez);
      * seyrek goruntude gereken yer yoksa hicbir sey yazilmadan ret.
    """
    import errno as _errno
    from diskultimate.core import platform as plat
    from diskultimate.core import resize as rz
    from diskultimate.core.operations import OperationQueue, resize_op

    def hazirla(ad):
        yol = img_path(ad)
        s = DiskSession.create(yol, 400 * MIB, overwrite=True)
        s.create_table("gpt")
        s.create_partition(2048, 100 * MIB // 512, fs_key="fat32", label="A")
        bas = 2048 + 200 * MIB // 512
        s.create_partition(bas, 150 * MIB // 512, fs_key="ext4", label="E")
        fs = s.filesystem(2)
        for i in range(30):
            fs.write_file(f"/d{i}.bin", bytes([i + 1]) * (1 << 20))
        fs.flush()
        s.close_filesystems()
        return yol, s, bas

    hedef = 2048 + 100 * MIB // 512
    # Kesinti KONUMA gore tetiklenir (yazilan bayta gore degil): bayt siniri
    # dosya sisteminin yerlesimine baglidir — flex_bg ile veri asagi kayinca
    # 40 MB'lik sinir kaynak ezilmeden doluyordu. Bolumun 110. MB'inin
    # otesine (kaymanin, yani 100 MB'in ardi) ilk yazim kaynagi ezer; 128.
    # MB'teki yedek ustblok her zaman yazilacagi icin tetikleme kesindir.
    ezen = hedef * 512 + 110 * MIB
    for sinir, saglam_beklenen in ((0, True), (ezen, False)):
        yol, s, bas = hazirla("t73.img")
        img, asil = s.image, s.image.write

        def kisitli(off, data):
            if off + len(data) > sinir:
                raise OSError(_errno.ENOSPC, "no space")
            return asil(off, data)

        img.write = kisitli
        try:
            s.resize_partition(2, hedef, 250 * MIB // 512, confirm=True)
            raise AssertionError("kesinti yok")
        except rz.MoveInterrupted as exc:
            assert exc.source_intact is saglam_beklenen, (sinir, exc.moved_sectors)
        img.write = asil
        s.reload()
        p = s.table.get(2)
        assert p.start_lba == bas                       # tablo degismedi
        tur = detect(s.view(p)).fs_type
        assert (tur == "ext4") is saglam_beklenen, (sinir, tur)
        s.close()

    # kuyruk mesaji: bozulma durumunda "BOZUK" uyarisini tasir (gosterim)
    yol, s, bas = hazirla("t73.img")
    img, asil = s.image, s.image.write

    def kisitli2(off, data):
        if off + len(data) > ezen:
            raise OSError(_errno.ENOSPC, "no space")
        return asil(off, data)

    img.write = kisitli2
    q = OperationQueue()
    q.add(resize_op(2, hedef, 250 * MIB // 512, at_lba=bas))
    sonuc = q.apply(s)
    img.write = asil
    assert sonuc.failed is not None and str(hedef) in sonuc.error
    s.close()

    # seyreklik: tasima yalnizca dolu veriyi yazar
    yol, s, bas = hazirla("t73s.img")
    once = plat.actual_size(yol)
    s.resize_partition(2, hedef, 250 * MIB // 512, confirm=True)
    s.close()
    if plat.supports_sparse(yol) and once < 400 * MIB:
        assert plat.actual_size(yol) < once + 80 * MIB, (once, plat.actual_size(yol))
    s = DiskSession.open(yol)
    fs = s.filesystem(2)
    assert fs.fs_type == "ext4"
    for i in range(30):
        assert fs.read(f"/d{i}.bin") == bytes([i + 1]) * (1 << 20)
    s.close()

    # yer on denetimi (yalnizca seyrek goruntu; olcum yoksa atlanir)
    yol, s, bas = hazirla("t73y.img")
    gereken, bos = rz.move_space_needed(s.image, bas, hedef, 150 * MIB // 512)
    if bos >= 0:
        assert 30 * MIB <= gereken <= 160 * MIB, gereken
        asil_bos = plat.free_space
        plat.free_space = lambda yol_: 1 * MIB
        try:
            s.resize_partition(2, hedef, 250 * MIB // 512, confirm=True)
            raise AssertionError("yetersiz alan reddedilmedi")
        except rz.ResizeError as exc:
            assert not isinstance(exc, rz.MoveInterrupted)
        finally:
            plat.free_space = asil_bos
        s.reload()
        p = s.table.get(2)
        assert p.start_lba == bas and detect(s.view(p)).fs_type == "ext4"
    s.close()


@test
def t74_dosya_ekleme_ilerlemesi():
    """Dosya ekleme ilerlemesi dosyanin icinde de yurur (yazilan bayt; ADR 0073)

    2026-10-01 kullanici: "dosya yuklerken ne kadar kaldigini gormek
    istiyorum". Tek dosyada cubuk belirsizdi, cok dosyada yalnizca dosya
    bitince ilerliyordu; kalan sure hesaplanamiyordu.
    """
    from diskultimate.core import image as im

    kaynak = img_path("t74_kaynak.bin")
    veri = os.urandom(12 * MIB)
    with open(kaynak, "wb") as f:
        f.write(veri)
    kucuk = img_path("t74_kucuk.txt")
    with open(kucuk, "wb") as f:
        f.write(b"kucuk")
    for fs_key in ("fat32", "exfat", "ext4"):
        yol = img_path(f"t74_{fs_key}.img")
        s = DiskSession.create(yol, 128 * MIB, overwrite=True)
        s.create_table("gpt")
        s.create_partition(2048, 100 * MIB // 512, fs_key=fs_key, label="IL")
        fs = s.filesystem(1)
        adimlar = []
        n, sorun = fs.import_files([kaynak, kucuk, img_path("t74_yok.bin")], "/",
                                   progress=lambda d, t, ad: adimlar.append((d, t, ad)))
        assert n == 2 and len(sorun) == 1 and "t74_yok.bin" in sorun[0], (n, sorun)
        toplam = len(veri) + 5
        assert all(t == toplam for _, t, _ in adimlar), fs_key
        ara = [d for d, _, ad in adimlar if ad == "t74_kaynak.bin" and 0 < d < len(veri)]
        assert len(ara) >= 8, (fs_key, len(ara))       # ~1 MB'ta bir
        assert len(adimlar) < 200, (fs_key, len(adimlar))   # sinyal selini onle
        degerler = [d for d, _, _ in adimlar]
        assert degerler == sorted(degerler), fs_key
        assert adimlar[-1] == (toplam, toplam, ""), adimlar[-1]
        assert fs.read("/t74_kaynak.bin") == veri
        # gozlemci islem bitince kaldirilir
        assert "write_observer" not in fs.device.__dict__
        s.close()

    # buyuk tek yazim gozlemciyle WRITE_PROGRESS_CHUNK'lik parcalara bolunur
    # (1 MiB, arayuzun bildirim araligi; ADR 0081), icerik ayni
    adim = im.WRITE_PROGRESS_CHUNK
    d = DiskImage.create(img_path("t74_ham.img"), 32 * MIB, overwrite=True)
    parcalar = []
    d.write_observer = parcalar.append
    blok = os.urandom(10 * MIB + 512)
    d.write(MIB, blok)
    del d.write_observer
    beklenen = [adim] * (len(blok) // adim) + [len(blok) % adim]
    assert parcalar == beklenen, parcalar
    assert d.read(MIB, len(blok)) == blok
    d.write(0, b"x" * 512)                     # gozlemcisiz: davranis ayni
    assert parcalar == beklenen
    # Bolme adimi arayuzun bildirim araligindan buyuk olursa cubuk adim atlar
    from diskultimate.core.filesystem import PROGRESS_STEP
    assert im.WRITE_PROGRESS_CHUNK <= PROGRESS_STEP, im.WRITE_PROGRESS_CHUNK
    d.close()


@test
def t75_ntfs_kume_boyutlari_ve_onarim():
    """NTFS: her kume boyutunda tutarli bicim; eski bicimin sahipsiz kumeleri onarilir (ADR 0074)

    Eski bicimlendirici kume 0-3'u sabit dolu isaretliyordu ($Boot 4 KiB
    kumede 2 kume): ntfsresize "extra cluster in $Bitmap" deyip calismiyordu.
    4 KiB alti kumede $Boot ile $MFT cakisiyor, $AttrDef kisa kaliyor ve
    onyukleme sektorundeki dizin kaydi boyu yanlis yaziliyordu — birim hic
    okunamiyordu (arayuzde 512 bayt / 1 / 2 KB secilebiliyordu).
    """
    import struct as _st
    from diskultimate.core.ntfs import format_ntfs
    from diskultimate.core.ntfsread import NtfsFS
    from diskultimate.core.ntfsresize import ntfs_repair, ntfs_size_info
    ntfsresize = shutil.which("ntfsresize") or shutil.which(
        "ntfsresize", path="/sbin:/usr/sbin")

    def bitmap0(dev):
        fs = NtfsFS(dev)
        attr = fs.record(6).find(0x80)
        return fs.read_attribute_range(attr, 0, 1)[0]

    for cs in (512, 1024, 2048, 4096, 65536):
        yol = img_path(f"t75_{cs}.img")
        d = DiskImage.create(yol, 200 * MIB, overwrite=True)
        format_ntfs(d, label="KUME", cluster_size=cs)
        fs = NtfsFS(d)
        assert fs.cluster_size == cs and fs.index_size == 4096, (cs, fs.index_size)
        boot_kume = max(1, 8192 // cs)
        beklenen = (1 << min(boot_kume, 8)) - 1
        assert bitmap0(d) & 0x0F == beklenen & 0x0F, (cs, bin(bitmap0(d)))
        erisim = open_filesystem(d)
        erisim.mkdir("/k")
        for i in range(120):                     # dizin kaydi (INDX) zorlanir
            erisim.write_file(f"/k/dosya_{i:03d}_uzun_ad.txt", b"z" * i)
        erisim.flush()
        erisim = open_filesystem(d)
        assert len([n for n in erisim.listdir("/k")]) == 120, cs
        assert erisim.read("/k/dosya_077_uzun_ad.txt") == b"z" * 77
        d.close()
        if ntfsresize:
            r = subprocess.run([ntfsresize, "--info", "--force", "--no-action", yol],
                               capture_output=True, text=True)
            assert r.returncode == 0 and "nconsistent" not in r.stdout + r.stderr, \
                (cs, (r.stdout + r.stderr)[-300:])

    # eski bicimin izi: 4 KiB kumede 2-3 sahipsiz dolu -> onarim yalnizca onlari bosaltir
    yol = img_path("t75_eski.img")
    d = DiskImage.create(yol, 200 * MIB, overwrite=True)
    format_ntfs(d, label="ESKI")
    erisim = open_filesystem(d)
    erisim.write_file("/veri.bin", b"V" * 300000)
    erisim.flush()
    fs = NtfsFS(d)
    attr = fs.record(6).find(0x80)
    ilk = bytearray(fs.read_attribute_range(attr, 0, 1))
    assert ilk[0] & 0x0F == 0x03
    ilk[0] |= 0x0C                               # eski bicimlendiricinin yazdigi
    lcn = attr.runs[0][0]
    d.write(lcn * fs.cluster_size, bytes(ilk))
    once = ntfs_size_info(d).used_clusters
    assert ntfs_repair(d) == 2
    assert bitmap0(d) & 0x0F == 0x03
    assert ntfs_size_info(d).used_clusters == once - 2
    assert ntfs_repair(d) == 0                   # ikinci kez: dokunmaz
    assert open_filesystem(d).read("/veri.bin") == b"V" * 300000
    d.close()


@test
def t76_diskten_diske_klon():
    """Diskten diske klon: birebir icerik, GPT yedek basligi hedefin sonunda, ret yollari

    Fiziksel hedef ana makinede sinanmaz (CLAUDE.md); ayni kod yolu acik bir
    goruntu oturumuyla (`clone_to_session`) sinanir. Uygunluk kurali ve
    `clone_to_physical`in on denetimleri sahte disk bilgisiyle sinanir.
    """
    from types import SimpleNamespace
    from diskultimate.core.disksource import clone_target_problem
    from diskultimate.core.gpt import GPTTable
    from diskultimate.core.session import SessionError

    kaynak_yol = img_path("t76_kaynak.img")
    s = DiskSession.create(kaynak_yol, 96 * MIB, scheme="gpt", overwrite=True)
    s.create_partition(2048, 40 * MIB // 512, fs_key="fat32", label="KLON")
    fs = s.filesystem(1)
    fs.write_file("/belge.txt", b"klon icerigi\n" * 1000)
    fs.flush()
    s.close_filesystems()
    s.close()
    kaynak = DiskSession.open(kaynak_yol, readonly=True)

    # hedef daha buyuk ve icinde eski veri var: hepsi ezilmeli
    hedef_yol = img_path("t76_hedef.img")
    h = DiskSession.create(hedef_yol, 160 * MIB, scheme="gpt", overwrite=True)
    h.image.write(150 * MIB, b"ESKI" * 128)
    h.close()
    hedef = DiskSession.open(hedef_yol, readonly=True)
    kopya = kaynak.clone_to_session(hedef)
    assert kopya == 96 * MIB, kopya
    assert not hedef.readonly                      # yazma moduna gecti
    assert hedef.scheme == "gpt" and len(hedef.partitions) == 1
    assert hedef.filesystem(1).read("/belge.txt") == b"klon icerigi\n" * 1000
    tablo = hedef.table
    assert isinstance(tablo, GPTTable)
    assert tablo.backup_header_lba == hedef.image.sector_count - 1, \
        (tablo.backup_header_lba, hedef.image.sector_count)
    # bolum verisi birebir (tablo sektorleri buyuk diske gore yeniden yazilir:
    # koruyucu MBR boyutu, birincil baslikta yedek konumu)
    for ofset in (2048 * 512, 20 * MIB, 40 * MIB):
        assert hedef.image.read(ofset, 4096) == kaynak.image.read(ofset, 4096)
    assert hedef.free_regions()[-1].sector_count * 512 > 60 * MIB, \
        "buyuk hedefin fazlasi bos alan olarak kullanilamiyor"
    # Kaynak boyunun otesi kopyalanmaz (ayrilmamis alan olur; DiskGenius da
    # boyle). Eski veri orada fiziksel olarak kalir — pencere bunu soyler.
    assert hedef.image.read(150 * MIB, 4) == b"ESKI"
    hedef.close()

    # MBR kaynak, onceden GPT olan buyuk hedef: eski yedek GPT silinmeli
    mbr_yol = img_path("t76_mbr.img")
    m = DiskSession.create(mbr_yol, 64 * MIB, scheme="mbr", overwrite=True)
    m.create_partition(2048, 40 * MIB // 512, fs_key="fat32", label="MBRK")
    m.close()
    hedef2 = DiskSession.create(img_path("t76_hedef2.img"), 128 * MIB,
                                scheme="gpt", overwrite=True)
    son = hedef2.image.sector_count - 1
    assert hedef2.image.read(son * 512, 8) == b"EFI PART"
    mbr = DiskSession.open(mbr_yol, readonly=True)
    mbr.clone_to_session(hedef2)
    assert hedef2.scheme == "mbr", hedef2.scheme
    assert hedef2.image.read(son * 512, 8) != b"EFI PART", "eski yedek GPT kaldi"
    hedef2.close()
    mbr.close()

    # ret: kendine, kucuk hedefe
    try:
        kaynak.clone_to_session(kaynak)
        raise AssertionError("kendine klon reddedilmedi")
    except SessionError:
        pass
    kucuk = DiskSession.create(img_path("t76_kucuk.img"), 64 * MIB, overwrite=True)
    try:
        kaynak.clone_to_session(kucuk)
        raise AssertionError("kucuk hedef reddedilmedi")
    except SessionError:
        pass
    kucuk.close()
    try:
        kaynak.clone_to_physical(SimpleNamespace(path="/dev/sahte", size=1 * MIB))
        raise AssertionError("kucuk fiziksel hedef aygit acilmadan reddedilmeli")
    except SessionError:
        pass

    # uygunluk kurali (arayuzdeki gri satirlar)
    def disk(**kw):
        base = dict(path="/dev/sdx", size=200 * MIB, info_complete=True,
                    readonly=False, is_system=False, mounted=[])
        base.update(kw)
        return SimpleNamespace(**base)
    assert clone_target_problem("/dev/sda", 96 * MIB, disk()) == ""
    assert clone_target_problem("/dev/sdx", 96 * MIB, disk())          # kendisi
    assert clone_target_problem("", 96 * MIB, disk(info_complete=False))
    assert clone_target_problem("", 96 * MIB, disk(readonly=True))
    assert clone_target_problem("", 96 * MIB, disk(size=10 * MIB))
    # sistem diski ve bagli bolum ENGEL degil (onay penceresi ele alir)
    assert clone_target_problem("", 96 * MIB, disk(is_system=True, mounted=["/"])) == ""
    kaynak.close()


@test
def t77_ntfs_denetle_ve_onar():
    """NTFS denetle/onar (ntfsfix karsiligi): kirli bayrak, gunluk, aynalar, onyukleme, hiberfil (ADR 0077)

    Gercek ornek `tests/fixtures/ntfs_windows_kirli.img.gz`: Windows 10'un
    bagli tuttugu bir birimin o anki hali — `$LogFile` temiz degil.
    ntfs-3g ayni goruntu icin "Metadata kept in Windows cache, refused to
    mount" der; onarimdan sonra ntfs-3g'nin `ntfsfix -d` ciktisiyla yalnizca
    `$Volume` kaydinin USN'si farklidir (2026-10-03, olculdu).
    """
    import gzip
    import struct as _st
    from diskultimate.core import ntfsfix as nf
    from diskultimate.core.ntfs import format_ntfs
    from diskultimate.core.ntfsread import NtfsFS as _NtfsFS
    from diskultimate.core.ntfsresize import NtfsResizeError, ntfs_resize, ntfs_size_info
    from diskultimate.core.ntfswrite import NtfsWriter

    def bayrak_yaz(dev, deger):
        fs = _NtfsFS(dev)
        attr = fs.record(3).find(0x70)
        v = bytearray(attr.value)
        _st.pack_into("<H", v, 10, deger)
        NtfsWriter(fs)._patch_resident(3, attr, bytes(v))

    def temiz_degil_gunluk(dev):
        """Windows'un bagliyken biraktigi yeniden baslatma sayfasi (etkin istemci)."""
        fs = _NtfsFS(dev)
        lcn = fs.record(2).find(0x80).runs[0][0]
        sayfa = bytearray(4096)
        sayfa[0:4] = b"RSTR"
        _st.pack_into("<HH", sayfa, 4, 0x1E, 9)
        _st.pack_into("<QIIHHH", sayfa, 8, 0, 4096, 4096, 0x30, 1, 1)
        _st.pack_into("<QHHHH", sayfa, 0x30, 0x1234, 1, 0xFFFF, 0, 0)
        for i in range(1, 9):
            sayfa[0x1E + 2 * i:0x20 + 2 * i] = sayfa[i * 512 - 2:i * 512]
            sayfa[i * 512 - 2:i * 512] = b"\x01\x00"
        sayfa[0x1E:0x20] = b"\x01\x00"
        for k in (0, 1):
            dev.write(lcn * fs.cluster_size + k * 4096, bytes(sayfa))

    def ntfs3g_kabul(yol):
        """ntfs-3g yazma kipinde acmayi kabul ediyor mu (kopya uzerinde)."""
        arac = shutil.which("ntfsfix")
        if not arac:
            return None
        kopya = yol + ".kopya"
        shutil.copyfile(yol, kopya)
        try:
            r = subprocess.run([arac, kopya], capture_output=True, text=True)
            return r.stdout.startswith("Mounting volume... OK")
        finally:
            os.unlink(kopya)

    # --- 1. Kirli bayrak + temiz olmayan gunluk ---
    yol = img_path("t77.img")
    d = DiskImage.create(yol, 128 * MIB, overwrite=True)
    format_ntfs(d, label="KIRLI")
    erisim = open_filesystem(d)
    erisim.mkdir("/klasor")
    erisim.write_file("/klasor/veri.bin", b"\x5A" * 300000)
    erisim.flush()
    h = nf.ntfs_check(d)
    assert not h.needs_repair and h.logfile == nf.LOG_CLEAN, h.problems()
    assert h.version == "3.1", h.version
    bayrak_yaz(d, nf.VOLUME_DIRTY)
    temiz_degil_gunluk(d)
    d.flush()
    h = nf.ntfs_check(d)
    assert h.dirty and h.logfile == nf.LOG_UNCLEAN and h.blocks_linux_mount, h
    assert len(h.problems()) == 2, h.problems()
    assert detect(d).unclean, "tespit kirli birimi gormedi"
    # 2026-10-03'e kadar `_is_dirty` yanlis ofsetten okuyup hep "temiz"
    # diyordu; kirli birim boyutlandiriliyordu.
    assert ntfs_size_info(d).dirty, "boyutlandirma kirli bayragi gormedi"
    try:
        ntfs_resize(d, d.sector_count - 2048)
        raise AssertionError("kirli birim boyutlandirildi")
    except NtfsResizeError:
        pass
    d.close()
    assert ntfs3g_kabul(yol) in (False, None), "ntfs-3g kirli birimi kabul etti"

    d = DiskImage(yol)
    try:
        nf.ntfs_fix(d, clear_dirty=True, schedule_chkdsk=True)
        raise AssertionError("celisen secenekler kabul edildi")
    except nf.NtfsFixError:
        pass
    sonuc = nf.ntfs_fix(d)
    assert not sonuc.after.needs_repair, sonuc.after.problems()
    assert len(sonuc.steps) == 2, sonuc.steps
    assert not detect(d).unclean
    assert open_filesystem(d).read("/klasor/veri.bin") == b"\x5A" * 300000
    assert nf.ntfs_fix(d).steps == [], "temiz birimde onarim bir sey yazdi"
    d.close()
    assert ntfs3g_kabul(yol) in (True, None), "ntfs-3g onarilan birimi reddetti"

    # chkdsk istemek: bayrak acilir, gunluk yine bosaltilir
    d = DiskImage(yol)
    temiz_degil_gunluk(d)
    sonuc = nf.ntfs_fix(d, clear_dirty=False, schedule_chkdsk=True)
    assert sonuc.after.dirty and sonuc.after.logfile == nf.LOG_CLEAN, sonuc.after
    nf.ntfs_fix(d)
    d.close()

    # --- 2. Onyukleme sektoru ve $MFTMirr ---
    d = DiskImage(yol)
    fs = _NtfsFS(d)
    cs, rs = fs.cluster_size, fs.record_size
    asil = d.read(0, 512)
    d.write(0, b"\x00" * 512)                       # asil bozuk, yedek saglam
    h = nf.ntfs_check(d)
    assert h.primary_boot == nf.BOOT_BAD and h.backup_boot == nf.BOOT_OK and h.repairable
    nf.ntfs_fix(d)
    assert d.read(0, 512) == asil, "onyukleme sektoru yedekten donmedi"
    yedek_ofs = _st.unpack_from("<Q", asil, 0x28)[0] * 512
    d.write(yedek_ofs, b"\x00" * 512)               # yedek bozuk
    assert nf.ntfs_check(d).backup_boot == nf.BOOT_BAD
    nf.ntfs_fix(d)
    assert d.read(yedek_ofs, 512) == asil, "yedek onyukleme yazilmadi"

    ayna = fs.mftmirr_lcn * cs
    kayit1 = d.read(fs.mft_lcn * cs + rs, rs)
    d.write(ayna + rs, b"\x00" * rs)                # ayna kaydi 1 bozuk
    assert nf.ntfs_check(d).mirror_mismatch == [1]
    nf.ntfs_fix(d)
    assert d.read(ayna + rs, rs) == kayit1, "$MFTMirr duzeltilmedi"
    kayit3 = d.read(fs.mft_lcn * cs + 3 * rs, rs)
    d.write(fs.mft_lcn * cs + 3 * rs, b"BAAD" + kayit3[4:])   # asil kayit 3 bozuk
    nf.ntfs_fix(d)
    assert d.read(fs.mft_lcn * cs + 3 * rs, rs) == kayit3, "$MFT aynadan donmedi"
    assert not nf.ntfs_check(d).needs_repair
    d.close()

    # --- 3. Hazirda bekletme: secilmeden reddedilir ---
    d = DiskImage(yol)
    erisim = open_filesystem(d)
    erisim.write_file("/hiberfil.sys", b"HIBR" + b"\x11" * 20000)
    erisim.flush()
    bayrak_yaz(d, nf.VOLUME_DIRTY)
    h = nf.ntfs_check(d)
    assert h.hibernated and detect(d).hibernated, h
    try:
        nf.ntfs_fix(d)
        raise AssertionError("hazirda bekletmedeki birim onarildi")
    except nf.NtfsFixError:
        pass
    assert nf.ntfs_check(d).dirty, "reddedilen onarim yine de yazdi"
    nf.ntfs_fix(d, remove_hibernation=True)
    h = nf.ntfs_check(d)
    assert not h.hibernated and not h.dirty, h
    veri = open_filesystem(d).read("/hiberfil.sys")
    assert veri[:4096] == b"\x00" * 4096 and veri[4096:] == b"\x11" * (20004 - 4096)
    d.close()

    # --- 4. Gercek Windows ornegi + kuyruk ---
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    bolum = img_path("t77_win_bolum.img")
    with gzip.open(os.path.join(fixtures, "ntfs_windows_kirli.img.gz"), "rb") as a, \
            open(bolum, "wb") as b:
        shutil.copyfileobj(a, b)
    d = DiskImage(bolum, readonly=True)
    h = nf.ntfs_check(d)
    assert h.logfile == nf.LOG_UNCLEAN and not h.dirty and h.blocks_linux_mount, h
    assert len(open_filesystem(d).listdir("/Belgeler")) == 20
    d.close()
    assert ntfs3g_kabul(bolum) in (False, None), "ntfs-3g Windows birimini kabul etti"

    disk = img_path("t77_win_disk.img")
    s = DiskSession.create(disk, 300 * MIB, scheme="gpt", overwrite=True)
    r = s.free_regions()[0]
    boyut = os.path.getsize(bolum) // 512
    s.create_partition(r.start_lba, boyut, fs_key="", name="Windows")
    v = s.view(s.table.get(1))
    with open(bolum, "rb") as f:
        v.write(0, f.read())
    s.reload()
    assert s.partitions[0].fs_type == "NTFS"
    assert s.detect_fs(s.table.get(1)).unclean
    kuyruk = ops.OperationQueue()
    kuyruk.add(ops.ntfs_fix_op(1, at_lba=r.start_lba))
    assert kuyruk[0].destructive
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, sonuc.summary()
    assert not s.detect_fs(s.table.get(1)).unclean, "kuyruk onarimi gunlugu birakti"
    assert not s.ntfs_check(1).needs_repair
    assert len(s.filesystem(1).listdir("/Belgeler")) == 20
    s.close()


@test
def t78_yetkili_kopya_sahiplik_ve_ayarlar():
    """Yetkili kopya: klasor/gunluk/ayar sahipligi kullaniciya, ayarlar kullanicinin evinde

    Root olmadan gercek sahiplik degistirilemez: yetkiyi veren kullanici
    taklit edilir, `os.chown`/`os.lchown` cagrilari kaydedilir. Olculen eski
    hata: `logs/freeze` root'a aitti, normal kopya donma raporu yazamiyordu;
    yetkili kopyanin ayarlari /root/.config'e gidiyordu.
    """
    from diskultimate.core import diagnostics as dg
    from diskultimate.core import platform as pf
    from diskultimate.core import settings as st
    if pf.IS_WINDOWS:
        raise Atlandi("sahiplik kavrami yalnizca Unix'te")

    kok = img_path("t78")
    shutil.rmtree(kok, ignore_errors=True)
    os.makedirs(kok)
    cagrilar = []
    eski = (pf.invoking_user, os.chown, os.lchown, os.environ.get("HOME"),
            os.environ.get("XDG_CONFIG_HOME"), os.environ.get("XDG_STATE_HOME"),
            st.config_dir)
    try:
        pf.invoking_user = lambda: (54321, 54321)
        os.chown = lambda p, u, g: cagrilar.append(("chown", os.path.relpath(p, kok)))
        os.lchown = lambda p, u, g: cagrilar.append(("lchown", os.path.relpath(p, kok)))

        # 1. Yalnizca YENI acilan basamaklar cevrilir
        pf.make_user_dirs(os.path.join(kok, "a", "b", "c"))
        assert cagrilar == [("chown", "a"), ("chown", "a/b"), ("chown", "a/b/c")], cagrilar
        cagrilar.clear()
        pf.make_user_dirs(os.path.join(kok, "a", "b"))       # zaten var
        assert cagrilar == [], cagrilar

        # 2. Onceki surumun root'a biraktiklari (burada: bizim uid'imiz
        # "yabanci" sayilir) bir kez cevrilir; sembolik bag izlenmez
        dis = os.path.join(kok, "disari")
        os.makedirs(dis)
        open(os.path.join(dis, "dokunma.txt"), "w").close()
        gunluk = os.path.join(kok, "logs")
        os.makedirs(os.path.join(gunluk, "freeze"))
        open(os.path.join(gunluk, "freeze", "eski.md"), "w").close()
        os.symlink(dis, os.path.join(gunluk, "bag"))
        n = pf.reclaim_tree(gunluk, foreign_uid=os.getuid())
        yollar = sorted(p for _, p in cagrilar)
        assert yollar == ["logs", "logs/bag", "logs/freeze", "logs/freeze/eski.md"], yollar
        assert n == 4, n
        cagrilar.clear()

        # 3. Tanilama: donma raporu dosyasi ve klasoru
        os.environ["DISKULTIMATE_LOG_DIR"] = os.path.join(kok, "tlog")
        dg.report_dir()
        dg._write(os.path.join(kok, "tlog", "freeze", "r.md"), "rapor")
        dg._write(os.path.join(kok, "tlog", "freeze", "r.md"), "ek", append=True)
        assert ("chown", "tlog") in cagrilar and ("chown", "tlog/freeze") in cagrilar, cagrilar
        assert cagrilar.count(("chown", "tlog/freeze/r.md")) == 1, cagrilar
        cagrilar.clear()

        # 4. Ayar dosyasi
        st.config_dir = lambda: kok
        st.reset_cache()
        st.load()
        assert st.save()
        assert ("chown", os.path.basename(st.path())) in cagrilar, cagrilar

        # 5. Ayar ve veri klasoru yetkili kopyada da kullanicinin evinde
        import pwd
        pf.invoking_user = lambda: (os.getuid(), os.getgid())
        os.chown = eski[1]
        os.environ["HOME"] = os.path.join(kok, "sahte_root_evi")
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ.pop("XDG_STATE_HOME", None)
        gercek_ev = pwd.getpwuid(os.getuid()).pw_dir
        assert eski[6]().startswith(gercek_ev + os.sep), eski[6]()
        assert pf.user_data_dir().startswith(gercek_ev + os.sep), pf.user_data_dir()
        assert not os.path.exists(os.environ["HOME"]), "sahte ev dizinine yazildi"
        # Yetkisiz kopyada eskisi gibi ~ kullanilir
        pf.invoking_user = lambda: None
        assert eski[6]().startswith(os.environ["HOME"]), eski[6]()
    finally:
        pf.invoking_user, os.chown, os.lchown = eski[0], eski[1], eski[2]
        for ad, deger in (("HOME", eski[3]), ("XDG_CONFIG_HOME", eski[4]),
                          ("XDG_STATE_HOME", eski[5])):
            if deger is None:
                os.environ.pop(ad, None)
            else:
                os.environ[ad] = deger
        os.environ.pop("DISKULTIMATE_LOG_DIR", None)
        st.config_dir = eski[6]
        st.reset_cache()
        shutil.rmtree(kok, ignore_errors=True)


@test
def t79_akis_yazma_ve_okuma():
    """Akis yazma/okuma: alti yazicida parcali yerlesim, yarida kalan kaynak, FAT32 4 GiB reddi (ADR 0081)

    Yazicilar eskiden dosyanin tamamini bellege aliyordu; FAT32 4 GiB+
    dosyayi yazip sonra dusuyor, kumeleri sizdiriyordu. Burada her yazilabilir
    dosya sisteminde: bosluklara dagilmak zorunda kalan dosya akisla yazilir,
    akisla geri okunur (`iter_read`, `extract`), yarida kalan kaynak yarim
    dosya ve sizinti birakmaz.
    """
    import hashlib
    import io as _io
    from diskultimate.core.formatter import format_partition
    from diskultimate.core.streamio import StreamError

    def ozet(parcalar):
        h = hashlib.sha1()
        for p in parcalar:
            h.update(p)
        return h.hexdigest()

    for fs_key in ("fat32", "exfat", "ntfs", "ext4", "ext2", "hfsplus", "udf"):
        yol = img_path(f"t79_{fs_key}.img")
        d = DiskImage.create(yol, 160 * MIB, overwrite=True)
        format_partition(d, fs_key, label="AKIS")
        a = open_filesystem(d)
        for i in range(20):
            a.write_file(f"/d{i:02d}.bin", bytes([i]) * (3 * MIB))
        for i in range(0, 20, 2):
            a.remove(f"/d{i:02d}.bin")
        a.flush()
        # kalan bitisik alani doldur: buyuk dosya bosluklara dagilmak zorunda
        bos = a.stats().get("free_bytes", 0) if hasattr(a, "stats") else 0
        if bos > 40 * MIB:
            a.write_stream("/dolgu.bin", _io.BytesIO(bytes(bos - 32 * MIB)),
                           bos - 32 * MIB)
        veri = os.urandom(21 * MIB + 333)
        a.write_stream("/parcali.bin", _io.BytesIO(veri), len(veri))
        kucuk = b"k" * 100
        a.write_stream("/kucuk.txt", _io.BytesIO(kucuk), len(kucuk))
        try:
            a.write_stream("/yarim.bin", _io.BytesIO(b"x" * 1000), 4 * MIB)
            raise AssertionError(f"{fs_key}: kisa kaynak kabul edildi")
        except StreamError:
            pass
        a.flush()
        d.close()

        d = DiskImage(yol, readonly=True)
        a = open_filesystem(d)
        adlar = {n.name for n in a.listdir("/")}
        assert "yarim.bin" not in adlar, (fs_key, "yarim dosya kaldi")
        assert ozet(a.iter_read("/parcali.bin")) == hashlib.sha1(veri).hexdigest(), fs_key
        assert b"".join(a.iter_read("/kucuk.txt")) == kucuk, fs_key
        assert a.read("/d01.bin") == bytes([1]) * (3 * MIB), fs_key
        cikan = img_path(f"t79_{fs_key}.out")
        a.extract("/parcali.bin", cikan)
        with open(cikan, "rb") as fh:
            assert fh.read() == veri, (fs_key, "extract")
        os.unlink(cikan)
        d.close()
        _dis_denetim(fs_key, yol)

    # FAT32: 4 GiB+ yer AYRILMADAN reddedilir, kaynak okunmaz
    from diskultimate.core.fat import FatError, FatFS
    yol = img_path("t79_fat_sinir.img")
    d = DiskImage.create(yol, 64 * MIB, overwrite=True)
    format_partition(d, "fat32", label="SINIR")
    fs = FatFS(d)
    once = fs.free_clusters()

    class Okunmamali:
        def read(self, n):
            raise AssertionError("4 GiB+ dosyada kaynak okundu")
    try:
        fs.write_stream("/buyuk.bin", Okunmamali(), 5 * 1024 ** 3)
        raise AssertionError("FAT32 5 GiB dosyayi kabul etti")
    except FatError:
        pass
    assert fs.free_clusters() == once, "reddedilen dosya kume sizdirdi"
    d.close()


@test
def t80_mantiksal_bolum_boyutlandirma_ve_tasima():
    """MBR mantiksal bolum: buyut/kucult/tasi; ilk EBR genisletilmis bolumun basinda (ADR 0082)

    Uzun testler (2026-10-04) yakaladi: (1) genel cakisma denetimi mantiksal
    bolumu kapsayicisiyla karsilastiriyordu, her boyutlandirma "1 numarali
    bolum ile cakisiyor" diye dusuyordu — tasimada veri ONCEDEN kopyalanmis
    oluyordu; (2) tasinan ilk mantiksal bolumun EBR'si yeni yerin onune
    yaziliyor, zincirin basi eski yeri gostermeye devam ediyordu; (3) pencere
    onceki mantiksal bolume bitisik baslangica izin veriyordu (EBR'ye yer yok).
    """
    from diskultimate.core import operations as _ops

    def uygula(s, *adimlar):
        k = _ops.OperationQueue()
        for a in adimlar:
            k.add(a)
        r = k.apply(s)
        assert r.ok, r.summary()

    yol = img_path("t80.img")
    s = DiskSession.create(yol, 600 * MIB, scheme="mbr", overwrite=True)
    uygula(s, _ops.create_op(2048, 500 * 2048, 512, extended=True))
    uygula(s, _ops.create_op(4096, 100 * 2048, 512, fs_key="fat32", label="BIR",
                             logical=True))
    s.reload()
    ic = [r for r in s.free_regions() if 4096 < r.start_lba < 2048 + 500 * 2048][0]
    uygula(s, _ops.create_op(ic.start_lba, 100 * 2048, 512, fs_key="fat32",
                             label="IKI", logical=True))
    s.reload()
    veriler = []
    for p in [x for x in s.partitions if x.logical]:
        d = os.urandom(30 * MIB)
        fs = s.filesystem(p.index)
        fs.write_file("/v.bin", d)
        fs.flush()
        veriler.append(d)
    s.close_filesystems()

    def mantiksal():
        s.reload()
        return [x for x in s.partitions if x.logical]

    def denetle(etiket):
        ls = mantiksal()
        assert len(ls) == 2, (etiket, ls)
        for p, d in zip(ls, veriler):
            assert s.filesystem(p.index).read("/v.bin") == d, etiket
        s.close_filesystems()
        # diskten yeniden okununca ayni yerlesim (EBR zinciri dogru)
        t = DiskSession.open(yol, readonly=True)
        assert [(x.start_lba, x.sector_count) for x in t.partitions if x.logical] == \
            [(x.start_lba, x.sector_count) for x in ls], (etiket, "zincir")
        t.close()
        if shutil.which("sfdisk"):
            r = subprocess.run(["sfdisk", "--verify", yol], capture_output=True,
                               text=True, env=dict(os.environ, LC_ALL="C"))
            assert "No errors detected" in r.stdout, (etiket, r.stdout[-300:])

    denetle("baslangic")
    p = mantiksal()[0]
    s.resize_partition(p.index, p.start_lba, 60 * 2048, confirm=True)
    denetle("ilk kucult")
    p = mantiksal()[0]
    s.resize_partition(p.index, p.start_lba + 30 * 2048, p.sector_count, confirm=True)
    denetle("ilk saga tasi")
    p = mantiksal()[0]
    s.resize_partition(p.index, 4096, p.sector_count, confirm=True)
    denetle("ilk geri tasi")
    onceki, p2 = mantiksal()
    w = s.resize_window(p2.index)
    assert w.start_lba > onceki.end_lba + 1, "EBR icin bosluk birakilmadi"
    s.resize_partition(p2.index, w.start_lba, p2.sector_count, confirm=True)
    denetle("ikinci sola tasi")
    p2 = mantiksal()[1]
    s.resize_partition(p2.index, p2.start_lba, p2.sector_count + 50 * 2048,
                       confirm=True)
    denetle("ikinci buyut")
    s.close()


@test
def t81_macos_yetki_komutu():
    """macOS yetki komutu: POSIX tirnaklama, ortam aktarimi, iptal mesaji (ADR 0083)

    Eskiden `do shell script` icin Windows'un `list2cmdline`i kullaniliyordu:
    yolda `$`, ters tirnak ya da tirnak varsa /bin/sh onlari yorumluyordu.
    Burada uretilen kabuk komutu /bin/sh ile GERCEKTEN calistirilir; argumanlar
    ve ortam karakteri karakterine korunmali. (Gercek osascript parola
    penceresi otomatik sinanamaz — ADR 0080.)
    """
    import json as _json
    from diskultimate.core import platform as pf
    if pf.IS_WINDOWS or not shutil.which("sh"):
        raise Atlandi("POSIX kabuk yok")
    zor = '/Users/a b/$HOME/`echo SIZDI`/"tirnak"/ağaç;rm -rf x'
    log = img_path("t81.log")
    env = {"DISKULTIMATE_HANDOFF": "/tmp/el $sikisma", "DISKULTIMATE_LANG": "en"}
    komut = [sys.executable, "-c",
             "import sys,os,json;print(json.dumps([sys.argv[1:],"
             "os.environ.get('DISKULTIMATE_HANDOFF'),os.environ.get('DISKULTIMATE_LANG')]))",
             zor, "--no-root"]
    betik = pf.mac_elevation_script(komut, env, log)
    assert betik.startswith('do shell script "') and betik.endswith(
        '" with administrator privileges'), betik[:80]
    # AppleScript dizesini geri coz (\\ ve \" kacislari)
    ic = betik[len('do shell script "'):-len('" with administrator privileges')]
    kabuk = ic.replace('\\"', '"').replace("\\\\", "\\")
    r = subprocess.run(["sh", "-c", kabuk], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with open(log, encoding="utf-8") as fh:
        argv, handoff, lang = _json.loads(fh.read().strip().splitlines()[-1])
    assert argv == [zor, "--no-root"], argv
    # argv karakteri karakterine ayni: $HOME genislemedi, ters tirnak calismadi
    assert handoff == "/tmp/el $sikisma" and lang == "en", (handoff, lang)


@test
def t82_windows_exfat_buyutme():
    """Windows'un bicimlendirdigi exFAT: buyut/kucult veriyi korur (ADR 0084)

    Uzun testler (Windows aygit kipi, 2026-10-04) yakaladi: Windows kume
    yiginini FAT'in ardina degil hizali bir sinira koyar (FAT 128+96, yigin
    256). Buyutme yigini yeniden hesapliyor (224), "kaydirma" negatif
    cikiyor ve sondan basa kopya her parcada sonrakinin kaynagini eziyordu.
    Bizim bicimlendiricimizde bosluk olmadigi icin goruntu testleri bunu
    hic gormedi. Fikstur: Windows 10, diskpart + Format exFAT (48 MB).
    """
    import gzip
    from diskultimate.core import operations as _ops
    from diskultimate.core.resize import _shift_forward, ResizeError
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    with gzip.open(os.path.join(fixtures, "exfat_windows.img.gz"), "rb") as fh:
        birim = fh.read()
    beklenen = {}
    with open(os.path.join(fixtures, "exfat_windows.sha1.txt"), encoding="utf-8") as fh:
        for satir in fh:
            if satir.strip():
                ozet, ad = satir.split(None, 1)
                beklenen[ad.strip()] = ozet
    sektor = len(birim) // 512

    yol = img_path("t82.img")
    s = DiskSession.create(yol, 300 * MIB, scheme="mbr", overwrite=True)
    k = _ops.OperationQueue()
    k.add(_ops.create_op(2048, sektor, 512))
    assert k.apply(s).ok
    s.image.write(2048 * 512, birim)
    s.reload()
    p = s.partitions[0]
    fs = ExFatFS(s.view(p))
    assert fs.cluster_heap_offset > fs.fat_offset + fs.fat_length, \
        "fikstur Windows yerlesimi degil (yigin onunde bosluk yok)"

    def denetle(etiket, yigin=None):
        s.close_filesystems()
        s.reload()
        p = s.partitions[0]
        fs = s.filesystem(p.index)
        for ad, ozet in beklenen.items():
            assert hashlib.sha1(fs.read(ad)).hexdigest() == ozet, (etiket, ad)
        s.close_filesystems()
        ex = ExFatFS(s.view(p))
        if yigin is not None:
            assert ex.cluster_heap_offset == yigin, (etiket, ex.cluster_heap_offset)
        assert ex.cluster_count == (p.sector_count - ex.cluster_heap_offset) // \
            ex.sectors_per_cluster, (etiket, ex.cluster_count)
        if shutil.which("fsck.exfat"):
            parca = img_path("t82_bolum.img")
            with open(parca, "wb") as out:
                for bas in range(0, p.sector_count, 8192):
                    n = min(8192, p.sector_count - bas)
                    out.write(s.image.read((p.start_lba + bas) * 512, n * 512))
            r = subprocess.run(["fsck.exfat", "-n", parca], capture_output=True,
                               text=True)
            assert r.returncode == 0, (etiket, r.stdout[-800:], r.stderr[-300:])
        return p

    p = denetle("baslangic", yigin=256)
    # Ezilme parca (4 MiB) sinirlarinda olur: kullanilan alan birkac parca
    # olmali. Windows'un yazdiklarina ek olarak bizden bir dosya.
    ek = os.urandom(12 * MIB)
    fs = s.filesystem(p.index)
    fs.write_file("/bizim_12MiB.bin", ek)
    fs.flush()
    beklenen["/bizim_12MiB.bin"] = hashlib.sha1(ek).hexdigest()
    p = denetle("dosya eklendi", yigin=256)
    # 56 MB: buyuyen FAT (112 sektor) bosluga sigar, yigin yerinde kalir.
    # Eski hesap yigini 240'a koyuyordu: -16 sektor, aygit kipindeki ariza.
    s.resize_partition(p.index, p.start_lba, 56 * 2048, confirm=True)
    p = denetle("buyut", yigin=256)
    # 200 MB: FAT bosluga sigmaz, yigin bu kez ileri kayar
    s.resize_partition(p.index, p.start_lba, 200 * 2048, confirm=True)
    p = denetle("cok buyut")
    assert ExFatFS(s.view(p)).cluster_heap_offset > 256
    s.resize_partition(p.index, p.start_lba, 60 * 2048, confirm=True)
    denetle("kucult")
    s.close()

    # Geri kaydirma her durumda reddedilir (sondan basa kopya veriyi ezer)
    d = DiskImage.create(img_path("t82_bos.img"), MIB, overwrite=True)
    try:
        _shift_forward(d, 100, -16, 64, 512)
    except ResizeError:
        pass
    else:
        raise AssertionError("geri kaydirma reddedilmedi")
    d.close()


@test
def t83_yerel_boyutlandirma_tablo_yalniz_degil():
    """Fiziksel diskte yerel arac yoksa NTFS saf Python ile boyutlanir (ADR 0084)

    Uzun testler (Windows aygit kipi) yakaladi: oturum `disk_number`
    alanini hic tasimiyordu; "native" secilip `Resize-Partition` hic
    calismiyor, plan `apply_resize`'a dusup **yalnizca tabloyu** yaziyordu.
    Buyutmede NTFS eski boyutta kaliyor, kucultmede (en kucuk 1 sektor
    gorundugu icin) bolum 1 MB'a inip birim bozuluyordu.
    """
    from diskultimate.core import operations as _ops
    from diskultimate.core import resize as _resize
    from diskultimate.core.resize import FsResizeInfo, ResizeError, apply_resize

    yol = img_path("t83.img")
    s = DiskSession.create(yol, 400 * MIB, scheme="gpt", overwrite=True)
    k = _ops.OperationQueue()
    k.add(_ops.create_op(2048, 150 * 2048, 512, fs_key="ntfs", label="YEREL"))
    assert k.apply(s).ok
    s.reload()
    veri = os.urandom(5 * MIB)
    fs = s.filesystem(1)
    fs.write_file("/v.bin", veri)
    fs.flush()
    s.close_filesystems()

    # 1) "native" plan apply_resize'a giderse tablo tek basina degismez
    p = s.table.get(1)
    yerel = FsResizeInfo(kind="native", fs_type="NTFS", min_sectors=1,
                         movable=False)
    plan = s.plan_resize(1, p.start_lba, 2048, fs_info=yerel)
    try:
        apply_resize(s, plan)
    except ResizeError:
        pass
    else:
        raise AssertionError("native plan tablo-yalniz uygulandi")
    s.reload()
    assert s.table.get(1).sector_count == 150 * 2048, "tablo degisti"

    # 2) Fiziksel disk gibi davranan oturum
    class FizikselGibi(type(s)):
        is_physical = True
        disk_info = None
    asil = type(s)
    s.__class__ = FizikselGibi
    eski_yerel = _resize._native_resize_available
    _resize._native_resize_available = lambda session: True
    try:
        # disk numarasi yoksa "native" hic secilmez
        s.windows_disk_number = lambda: None
        assert _resize.fs_resize_info_for(s, s.table.get(1)).kind == "ntfs"
        # numara var ama Windows sinir bildirmiyor: saf Python sinirlari
        s.windows_disk_number = lambda: 7
        s._native_size_limits = lambda index: (False, 0, 0, "sahte")
        bilgi = s.resize_info(1)
        assert bilgi.kind == "ntfs" and bilgi.min_sectors > 2048, bilgi
        # yerel arac calistirilamadi: saf Python yoluna doner
        s._try_native_resize = lambda *a: False
        p = s.table.get(1)
        s.resize_partition(1, p.start_lba, 250 * 2048, confirm=True)
        s.reload()
        assert s.table.get(1).sector_count == 250 * 2048
        ntfs = NtfsFS(s.view(s.table.get(1)))
        assert ntfs.total_sectors >= 249 * 2048, "NTFS buyumedi (tablo-yalniz)"
        # tasima istenirse de saf Python (Windows'un araci tasiyamaz)
        p = s.table.get(1)
        s.resize_partition(1, p.start_lba + 20 * 2048, p.sector_count,
                           confirm=True)
    finally:
        _resize._native_resize_available = eski_yerel
        s.__class__ = asil
        for ad in ("windows_disk_number", "_native_size_limits",
                   "_try_native_resize"):
            s.__dict__.pop(ad, None)
    s.reload()
    assert s.filesystem(1).read("/v.bin") == veri
    s.close()


@test
def t84_ntfs_temiz_gunluk():
    """NTFS $LogFile: temiz yeniden baslatma alani (Windows salt okunur baglar, ADR 0085)

    Uzun testler (Windows, salt okunur VHD) yakaladi: 0xFF dolu "bos" gunlugu
    Windows ilk baglamada yazarak baslatir; salt okunur ortamda birim
    baglanmaz, kok dizin "Yazma korumasi var" der. Bicimlendirici, boyutlandirma
    ve onarim artik Windows'un yazdigiyla ayni RSTR sayfalarini yazar; VM'de
    olculdu: salt okunur baglama + chkdsk temiz.
    """
    import gzip
    from diskultimate.core import ntfsfix as nf
    from diskultimate.core.ntfsread import AT_DATA
    from diskultimate.core.ntfsresize import ntfs_resize

    def sayfa(fs, no):
        a = fs.record(2).find(AT_DATA)
        p = bytearray(fs.read_attribute_range(a, no * 4096, 4096))
        assert p[:4] == b"RSTR", (no, bytes(p[:4]))
        fs._apply_fixup(p)
        bas = struct.unpack_from("<HHQIIHhh", p, 4)
        alan = list(struct.unpack_from("<QHHHHIHHqIHHI", p, 0x30))
        istemci = bytes(p[0x70 + 0x1C:0x70 + 0x28])
        alan[0] = alan[9] = alan[12] = 0      # LSN, son kayit boyu, acilis sayaci
        return bas, tuple(alan), istemci, a.data_size

    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    win = img_path("t84_win.img")
    with gzip.open(os.path.join(fixtures, "ntfs_windows.img.gz"), "rb") as a, \
            open(win, "wb") as b:
        b.write(a.read())
    dw = DiskImage(win, readonly=True)
    beklenen = sayfa(NtfsFS(dw), 0)
    dw.close()

    yol = img_path("t84.img")
    d = DiskImage.create(yol, 64 * MIB, overwrite=True)
    format_ntfs(d, label="GUNLUK")
    fs = NtfsFS(d)
    for no in (0, 1):
        assert sayfa(fs, no) == beklenen, (no, sayfa(fs, no), beklenen)
    a = fs.record(2).find(AT_DATA)
    geri = fs.read_attribute_range(a, 8192, a.data_size - 8192)
    assert geri.count(0xFF) == len(geri), "gunlugun geri kalani 0xFF degil"
    assert nf.ntfs_check(d).logfile == nf.LOG_CLEAN
    d.close()
    if shutil.which("ntfs-3g.probe"):
        r = subprocess.run(["ntfs-3g.probe", "--readonly", yol], capture_output=True,
                           text=True)
        assert r.returncode == 0, r.stderr[-400:]

    # boyutlandirma ve onarim gunlugu yine temiz birakir
    d = DiskImage(yol)
    ntfs_resize(d, d.sector_count - 8 * 2048)
    assert nf.ntfs_check(d).logfile == nf.LOG_CLEAN
    assert sayfa(NtfsFS(d), 0) == beklenen
    d.close()


@test
def t85_ntfs_boyutlandirma_kosu_uzunlugu_isaretli():
    """NTFS boyutlandirma: kosu uzunlugu isaretli kodlanir ($BadClus, ADR 0085)

    VM'de Windows chkdsk: buyutulen birimde "Attribute record (80, $Bad) from
    file record segment 8 is corrupt". `ntfsresize.encode_runs` uzunlugu
    isaretsiz yaziyordu: 46335 kume = 0xB4FF iki bayt, Windows'a gore negatif.
    Okuyucumuz da isaretsiz okudugu icin hata gorunmuyordu; artik negatif
    uzunluk bozukluk sayilir.
    """
    from diskultimate.core import operations as _ops
    from diskultimate.core.ntfsread import AT_DATA, _decode_runs
    from diskultimate.core.ntfsresize import encode_runs

    for uzunluk in (127, 128, 255, 256, 32767, 32768, 46335, 65535, 8388608):
        for lcn in (-1, 5000):
            kod = encode_runs([(lcn, uzunluk)])
            assert _decode_runs(kod) == [(lcn, uzunluk)], (lcn, uzunluk, kod.hex())
    try:
        _decode_runs(bytes([0x02, 0xFF, 0xB4, 0x00]))   # eski hatali kodlama
        raise AssertionError("negatif uzunluk kabul edildi")
    except NtfsError:
        pass

    yol = img_path("t85.img")
    s = DiskSession.create(yol, 264 * MIB, scheme="gpt", overwrite=True)
    k = _ops.OperationQueue()
    k.add(_ops.create_op(2048, 145 * 2048, 512, fs_key="ntfs", label="KOSU"))
    assert k.apply(s).ok
    s.reload()
    veri = os.urandom(3 * MIB)
    fs = s.filesystem(1)
    fs.write_file("/v.bin", veri)
    fs.flush()
    s.close_filesystems()
    for boyut in (181 * 2048, 60 * 2048, 181 * 2048):    # buyut, kucult, buyut
        p = s.table.get(1)
        s.resize_partition(1, p.start_lba, boyut, confirm=True)
        s.reload()
        ntfs = NtfsFS(s.view(s.table.get(1)))
        kume = ntfs.total_sectors * ntfs.sector_size // ntfs.cluster_size
        bad = ntfs.record(8).find(AT_DATA, "$Bad")
        assert bad.runs == [(-1, kume)], (boyut, bad.runs, kume)
        assert s.filesystem(1).read("/v.bin") == veri, boyut
        s.close_filesystems()
    s.close()


@test
def t86_surucu_harfi_ofsetle():
    """Windows surucu harfi: bolum numarayla degil ofsetle secilir (ADR 0086)

    Windows MBR mantiksal bolumlerini kendi sirasiyla numaralar; bizim 5
    numarali bolumumuz Windows'ta baska bir numaradir. `-PartitionNumber`
    ile harf baska bir bolume atanir ya da baska bir bolumunki kaldirilirdi.
    Gercek Windows davranisi VM'de sinanir (tests/physical_drive_letter_test.py).
    """
    from diskultimate.core import platform as pf
    ofset = 301 * MIB
    for eylem in ("query", "assign", "remove"):
        komut = pf.win_letter_command(eylem, 3, ofset, "E:\\")
        assert "-PartitionNumber" not in komut, komut
        assert f"$_.Offset -eq {ofset}" in komut and "-DiskNumber 3" in komut
        assert "throw" in komut, "bolum bulunamazsa sessiz gecilmemeli"
    assert "Add-PartitionAccessPath -AssignDriveLetter" in \
        pf.win_letter_command("assign", 3, ofset)
    kaldir = pf.win_letter_command("remove", 3, ofset, "E:\\'; Format-Volume")
    assert "-AccessPath 'E:\\; Format-Volume'" in kaldir, kaldir   # tirnak kacamaz

    # Windows'ta ofsetsiz harf islemi reddedilir; "bagli degil" basarisi
    # donmez (Uygula bu sonuca guvenip diske yazar).
    if pf.IS_WINDOWS:
        ok, mesaj = pf.unmount_partition("\\\\.\\PhysicalDrive9", 5)
        assert not ok and mesaj
        ok, mesaj = pf.mount_partition("\\\\.\\PhysicalDrive9", 5)
        assert not ok and mesaj


@test
def t87_exfat_kucult_buyut_bitmap_zinciri():
    """exFAT: en aza kucultup geri buyutunce bitmap zinciri boyuna yeter (ADR 0087)

    Uzun testler (full profil, 6.7 GiB): `geri_buyut` sonrasi Apple
    fsck_exfat "Cluster chain for Main Bitmap has too few clusters", Windows
    dosyalari listelemiyor. Kucultme zinciri kisaltir (ADR 0083); buyutmede
    yeni ardisik alan eski ilk kumeden basladiginda zincir yeniden
    yazilmiyordu. Quick profilde bitmap tek kumeye sigdigi icin gorunmedi.
    """
    from diskultimate.core import operations as _ops
    from diskultimate.core.exfat import EOC as EXFAT_EOC

    yol = img_path("t87.img")
    s = DiskSession.create(yol, 700 * MIB, scheme="gpt", overwrite=True)
    k = _ops.OperationQueue()
    k.add(_ops.create_op(2048, 600 * 2048, 512))
    assert k.apply(s).ok, "olusturma"
    s.reload()
    # 4 KiB kume: 600 MB'ta bitmap 5 kumeye yayilir
    s.format_partition(1, "exfat", label="ZINCIR", cluster_bytes=4096)
    s.reload()
    veri = {f"/d{i}.bin": os.urandom(300 * 1024 + i) for i in range(6)}
    fs = s.filesystem(1)
    for ad, icerik in veri.items():
        fs.write_file(ad, icerik)
    fs.flush()
    s.close_filesystems()

    def denetle(etiket):
        ex = ExFatFS(s.view(s.table.get(1)))
        kb = ex.cluster_bytes
        gereken = (ex.bitmap_length + kb - 1) // kb
        zincir = ex.chain(ex.bitmap_cluster)           # FAT'i EOC'ye kadar izler
        assert len(zincir) == gereken, (etiket, len(zincir), gereken)
        assert ex.get_fat(zincir[-1]) == EXFAT_EOC, etiket
        assert ex.bitmap_length == (ex.cluster_count + 7) // 8, etiket
        for c in zincir:
            assert ex.is_used(c), (etiket, "bitmap kumesi bos isaretli", c)
        okunan = s.filesystem(1)
        for ad, icerik in veri.items():
            assert okunan.read(ad) == icerik, (etiket, ad)
        s.close_filesystems()
        return gereken

    assert denetle("baslangic") >= 4, "bitmap birden cok kumeye yayilmali"
    en_az = s.resize_info(1).min_sectors
    s.resize_partition(1, 2048, -(-en_az // 2048) * 2048, confirm=True)
    s.reload()
    assert denetle("kucult") == 1
    s.resize_partition(1, 2048, 600 * 2048, confirm=True)
    s.reload()
    assert denetle("geri buyut") >= 4
    s.close()
    if shutil.which("fsck.exfat"):
        parca = img_path("t87_bolum.img")
        with open(yol, "rb") as src, open(parca, "wb") as out:
            src.seek(2048 * 512)
            for _ in range(600):
                out.write(src.read(MIB))
        r = subprocess.run(["fsck.exfat", "-n", parca], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout[-600:]


@test
def t88_fat32_tasimada_yedek_onyukleme():
    """FAT32 tasima: yedek onyukleme sektorunun gizli sektor alani da guncellenir (ADR 0087)

    Uzun testler (full profil, Linux): `saga_tasi` sonrasi fsck.fat "There
    are differences between boot sector and its backup (29:48/08, 30:3c/00)".
    `_patch_hidden_sectors` yalnizca asil sektoru yaziyordu.
    """
    from diskultimate.core import operations as _ops

    yol = img_path("t88.img")
    s = DiskSession.create(yol, 400 * MIB, scheme="gpt", overwrite=True)
    k = _ops.OperationQueue()
    k.add(_ops.create_op(2048, 100 * 2048, 512, fs_key="fat32", label="YEDEK"))
    assert k.apply(s).ok
    s.reload()
    veri = os.urandom(2 * MIB)
    fs = s.filesystem(1)
    fs.write_file("/v.bin", veri)
    fs.flush()
    s.close_filesystems()

    def denetle(etiket, beklenen_lba):
        p = s.table.get(1)
        asil = s.image.read(p.start_lba * 512, 512)
        yedek_no = struct.unpack_from("<H", asil, 50)[0]
        yedek = s.image.read((p.start_lba + yedek_no) * 512, 512)
        assert 0 < yedek_no < 32, (etiket, yedek_no)
        gizli = struct.unpack_from("<I", asil, 28)[0]
        assert gizli == beklenen_lba, (etiket, gizli, beklenen_lba)
        assert asil[:90] == yedek[:90], (etiket, "asil/yedek BPB farkli")
        assert s.filesystem(1).read("/v.bin") == veri, etiket
        s.close_filesystems()
        if shutil.which("fsck.fat"):
            parca = img_path("t88_bolum.img")
            with open(parca, "wb") as out:
                out.write(s.image.read(p.start_lba * 512, p.sector_count * 512))
            r = subprocess.run(["fsck.fat", "-n", parca], capture_output=True,
                               text=True)
            assert r.returncode == 0, (etiket, r.stdout[-600:])

    denetle("baslangic", 2048)
    s.resize_partition(1, 2048 + 50 * 2048, 100 * 2048, confirm=True)
    s.reload()
    denetle("saga tasi", 2048 + 50 * 2048)
    s.resize_partition(1, 2048, 100 * 2048, confirm=True)
    s.reload()
    denetle("sola tasi", 2048)
    s.resize_partition(1, 2048 + 20 * 2048, 150 * 2048, confirm=True)
    s.reload()
    denetle("tasi + buyut", 2048 + 20 * 2048)
    s.close()


@test
def t89_bolum_basina_isletim_sistemi():
    """Bolum basina kurulu isletim sistemi: Windows surumu, Linux, macOS, ESP (ADR 0089)

    Disk satirindaki amblem bolum turlerinden bir tahmindir; bir diskte
    birden cok sistem olabilir. Her bolumun icine bakilir: Windows surumu
    `ntoskrnl.exe` surum kaydindan (derleme 22631 -> Windows 11), Linux
    `/etc/os-release`, macOS `SystemVersion.plist`, ESP'de yukleyicisi olan
    sistemler (`EFI/Microsoft/Boot` bir alt klasorde). Veri bolumu "sistem
    yok" demeli.
    """
    import plistlib
    from diskultimate.core.bootloader import partition_os

    yol = img_path("t89.img")
    s = DiskSession.create(yol, 400 * MIB, scheme="gpt", overwrite=True)
    s.create_partition(2048, 60 * 2048, fs_key="fat32", label="ESP")
    s.create_partition(62 * 2048, 80 * 2048, fs_key="ntfs", label="WIN")
    s.create_partition(142 * 2048, 80 * 2048, fs_key="ext4", label="ubuntu")
    s.create_partition(222 * 2048, 60 * 2048, fs_key="hfsplus", label="Mac")
    s.create_partition(282 * 2048, 60 * 2048, fs_key="ntfs", label="VERI")
    s.reload()

    def yaz(index, dizinler, dosyalar):
        fs = s.filesystem(index)
        for d in dizinler:
            fs.mkdir(d)
        for ad, veri in dosyalar.items():
            fs.write_file(ad, veri)
        fs.flush()
        s.close_filesystems()

    yaz(1, ["/EFI", "/EFI/Microsoft", "/EFI/Microsoft/Boot", "/EFI/ubuntu",
            "/EFI/Boot"],
        {"/EFI/Microsoft/Boot/bootmgfw.efi": b"MZ" + b"\0" * 64,
         "/EFI/ubuntu/shimx64.efi": b"MZ" + b"\0" * 64,
         "/EFI/Boot/bootx64.efi": b"MZ" + b"\0" * 64})
    # ntoskrnl.exe: VS_FIXEDFILEINFO, dosya surumu 10.0.22631.4317
    surum = struct.pack("<IIII", 0xFEEF04BD, 0x10000, (10 << 16) | 0,
                        (22631 << 16) | 4317)
    yaz(2, ["/Windows", "/Windows/System32", "/Windows/System32/config"],
        {"/Windows/System32/config/SYSTEM": b"regf" + b"\0" * 4092,
         "/Windows/System32/ntoskrnl.exe": b"MZ" + os.urandom(200_000) + surum
         + b"\0" * 1024})
    yaz(3, ["/etc"], {"/etc/os-release":
                      b'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.1 LTS"\n'})
    yaz(4, ["/System", "/System/Library", "/System/Library/CoreServices"],
        {"/System/Library/CoreServices/SystemVersion.plist": plistlib.dumps(
            {"ProductName": "macOS", "ProductVersion": "14.6.1"})})
    yaz(5, ["/Belgeler"], {"/Belgeler/not.txt": b"veri"})

    beklenen = {1: ("esp", "EFI: Windows, ubuntu"),
                2: ("windows", "Windows 11 (22631)"),
                3: ("linux", "Ubuntu 24.04.1 LTS"),
                4: ("macos", "macOS 14.6.1"),
                5: ("", "")}
    for index, (tur, ad) in beklenen.items():
        sonuc = partition_os(s, s.table.get(index))
        assert (sonuc["kind"], sonuc["name"]) == (tur, ad), (index, sonuc)
        if not tur:
            assert sonuc["reason"], "veri bolumunde neden yazilmali"
    s.close()


@test
def t90_guncelleme_denetimi_ve_lisanslar():
    """Guncelleme denetimi (agsiz) ve dagitilan bilesenlerin lisans metinleri (ADR 0090)

    Butun surumler on surum: `/releases/latest` 404 verir, liste okunur.
    En yeni surum numarayla secilir, tarihle degil; taslak sayilmaz. Exe ve
    AppImage Qt (LGPLv3), PyQt5 (GPLv3), sip (BSD-2) ve Python'u (PSF) icinde
    tasir: lisans metinleri pakette olmali.
    """
    from diskultimate import licenses as lic
    from diskultimate.core import platform as pf
    from diskultimate.core import updates as up

    sira = ["0.5.9", "0.6.0-beta", "0.6.1-beta", "v0.6.1-rc1", "0.6.1",
            "0.6.2-beta"]
    for i in range(len(sira) - 1):
        assert up.is_newer(sira[i + 1], sira[i]), (sira[i + 1], sira[i])
        assert not up.is_newer(sira[i], sira[i + 1]), (sira[i], sira[i + 1])
    assert not up.is_newer("v0.6.1-beta", "0.6.1-beta")
    assert not up.is_newer("nightly", "0.6.1-beta"), "taninmayan surum yeni sayilmaz"

    liste = [
        {"tag_name": "v0.7.0-beta", "draft": True, "html_url": "u-taslak"},
        {"tag_name": "v0.6.2-beta", "prerelease": True, "html_url": "u-062",
         "published_at": "2026-10-05"},
        # eski dal icin sonradan cikan duzeltme: tarihce en yeni ama surumce degil
        {"tag_name": "v0.5.3-beta", "prerelease": True, "html_url": "u-053",
         "published_at": "2026-10-09"},
        {"tag_name": "gecersiz", "html_url": "u-x"},
    ]
    en_yeni = up.newest(liste)
    assert en_yeni.tag == "v0.6.2-beta" and en_yeni.url == "u-062", en_yeni
    yeni, son = up.check("0.6.1-beta", fetcher=lambda: liste)
    assert yeni is not None and yeni.version == "0.6.2-beta", yeni
    yeni, son = up.check("0.6.2-beta", fetcher=lambda: liste)
    assert yeni is None and son.tag == "v0.6.2-beta"

    def kopuk():
        raise up.UpdateError("ag yok")
    try:
        up.check("0.6.1-beta", fetcher=kopuk)
        raise AssertionError("ag hatasi yutuldu")
    except up.UpdateError:
        pass

    # Ag katmani (sahte okuyucu): API zaman asimina dusunce bir kez daha
    # denenir, sonra surum akisina (releases.atom) gecilir. Kullanicinin
    # baglantisinda API "read operation timed out" verdi (2026-10-04).
    import json as _json
    import socket as _socket
    atom = (b'<?xml version="1.0" encoding="UTF-8"?><feed xmlns='
            b'"http://www.w3.org/2005/Atom"><entry><id>tag:github.com,2008:'
            b'Repository/1/v0.6.2-beta</id><link rel="alternate" href='
            b'"https://github.com/mcansiz/DiskUltimate/releases/tag/v0.6.2-beta"/>'
            b'<title>DiskUltimate 0.6.2-beta</title></entry></feed>')
    cagrilar = []

    def yavas_api(url, timeout):
        cagrilar.append(url)
        if url == up.API_URL:
            raise _socket.timeout("The read operation timed out")
        return atom
    yeni, _son = up.check("0.6.1-beta", fetcher=lambda: up.fetch(reader=yavas_api))
    assert yeni is not None and yeni.tag == "v0.6.2-beta", yeni
    assert cagrilar == [up.API_URL, up.API_URL, up.ATOM_URL], cagrilar

    cagrilar.clear()

    def saglam_api(url, timeout):
        cagrilar.append(url)
        return _json.dumps(liste).encode()
    assert up.newest(up.fetch(reader=saglam_api)).tag == "v0.6.2-beta"
    assert cagrilar == [up.API_URL], "API calisirken akisa gidilmemeli"

    def kopuk_ag(url, timeout):
        raise _socket.timeout("The read operation timed out")
    try:
        up.fetch(reader=kopuk_ag)
        raise AssertionError("ag yokken hata verilmedi")
    except up.UpdateError as exc:
        assert "zaman" in str(exc), str(exc)

    assert not pf.open_url("file:///etc/passwd"), "yalnizca https acilmali"
    assert not pf.open_url("http://example.com"), "yalnizca https acilmali"

    beklenen = {"qt": "GNU LESSER GENERAL PUBLIC LICENSE",
                "pyqt5": "GNU GENERAL PUBLIC LICENSE",
                "sip": "Redistribution and use in source and binary forms",
                "python": "PYTHON SOFTWARE FOUNDATION LICENSE"}
    for bilesen in lic.COMPONENTS:
        metin = lic.text(bilesen)
        assert beklenen[bilesen["key"]] in metin, bilesen["name"]
    qt = [b for b in lic.COMPONENTS if b["key"] == "qt"][0]
    assert "{}" in qt["note"], "Qt notu kaynak arsivi baglantisini tasimali"


@test
def t91_az_kumeli_fat32():
    """65525'ten az kumeli FAT32 (mkdosfs -F 32) FAT16 sanilmaz; yazma tabloyu bozmaz

    PicoZed/Xilinx BOOT bolumu: 100 MB, kume 4 KB -> 25549 kume, ama BPB
    FAT32 (BPB_FATSz16 = 0). Tip kume sayisindan secilince FAT16 gorunuyor,
    etiket 0x2B'den (FAT32'de BPB_FATSz32'nin ortasi) cop okunuyor ve dosya
    yazimi 32 bitlik tabloya 16 bitlik girdi yaziyordu.
    """
    yol = img_path("t91.img")
    d = DiskImage.create(yol, 101 * MIB, overwrite=True)
    MBRTable.create(d).add_partition(2048, 100 * 2048, type_id=0x0B)
    v = PartitionView(d, 2048, 100 * 2048)
    FatFS.format(v, fat_type=32, cluster_sectors=1, label="BOOT")
    # mkdosfs -F 32 -s 8 yerlesimi: kume 8 sektore cikar, kume sayisi
    # 65525'in altina iner; buyuk kalan FAT tablosu gecerlidir.
    asil = bytearray(v.read(0, 512))
    asil[13] = 8
    yedek_no = struct.unpack_from("<H", asil, 50)[0]
    v.write(0, bytes(asil))
    v.write(yedek_no * 512, bytes(asil))
    fs = FatFS(v)
    assert fs.fat_type == 32 and fs.cluster_count < 65525, (fs.fat_type,
                                                             fs.cluster_count)
    assert fs.label == "BOOT", fs.label
    bilgi = detect(v)
    assert bilgi.fs_type == "FAT32" and bilgi.label == "BOOT", (bilgi.fs_type,
                                                               bilgi.label)
    veri = os.urandom(300 * 1024)
    fs.write_file("/BOOT.BIN", veri)
    fs.flush()
    assert FatFS(v).read_file("/BOOT.BIN") == veri
    d.close()
    _fsck(yol, 2048)


@test
def t92_paralel_yedek_ozdes():
    """Paralel sikistirmali yedek tek is parcacikli yedekle bayt bayt ayni

    Yedekleme bloklari is parcaciklarinda sikistirir ama sirayla yazar;
    sira kayarsa dizin baska bloga isaret eder ve geri yukleme sessizce
    yanlis veri yazar.
    """
    from diskultimate.core.image import is_zero

    assert is_zero(b"") and is_zero(bytes(9 * MIB))
    tek = bytearray(9 * MIB)
    tek[-1] = 1
    assert not is_zero(tek) and not is_zero(b"\x00\x01")

    yol = img_path("t92.img")
    d = DiskImage.create(yol, 24 * MIB, overwrite=True)
    for i in range(0, 24, 3):          # karisik: rastgele / metin / sifir
        d.write(i * MIB, os.urandom(MIB))
        d.write((i + 1) * MIB, (b"DiskUltimate %d " % i) * 30000)
    eski = os.environ.get("DISKULTIMATE_BACKUP_THREADS")
    try:
        dosyalar = []
        for adet in ("1", "4"):
            os.environ["DISKULTIMATE_BACKUP_THREADS"] = adet
            hedef = img_path("t92_%s.dub" % adet)
            backup(d, hedef, block_size=256 * 1024)
            dosyalar.append(open(hedef, "rb").read())
    finally:
        if eski is None:
            os.environ.pop("DISKULTIMATE_BACKUP_THREADS", None)
        else:
            os.environ["DISKULTIMATE_BACKUP_THREADS"] = eski
    a, b = dosyalar
    assert a[:24] == b[:24] and a[32:] == b[32:], "paralel cikti farkli"
    geri = DiskImage.create(img_path("t92_geri.img"), 24 * MIB, overwrite=True)
    restore(img_path("t92_4.dub"), geri)
    assert geri.read(0, 24 * MIB) == d.read(0, 24 * MIB)
    geri.close()
    d.close()


@test
def t93_yalnizca_kullanilan_alan_yedegi():
    """Yalnizca kullanilan alan yedegi: FAT32/exFAT/ext4/NTFS, ham onyukleyici, EBR (ADR 0092)

    64 GB SD kartta yedek her sektoru okuyordu; DiskGenius yalnizca dolu
    kumeleri okur. Bos alandaki cop ve bolumlenmemis buyuk alan okunmamali;
    bolum disi ham onyukleyici (U-Boot gibi), dosyalar ve dosya sistemi
    tutarliligi geri yuklemeden sonra korunmali. Atlanan bloga geri
    yuklemede hedefte dokunulmaz.
    """
    from diskultimate.core import clone as _clone
    from diskultimate.core import usedmap

    yol = img_path("t93.img")
    s = DiskSession.create(yol, 700 * MIB, scheme="mbr", overwrite=True)
    k = ops.OperationQueue()
    bolumler = (("fat32", 2048 * 4), ("exfat", 2048 * 110), ("ext4", 2048 * 220),
                ("ntfs", 2048 * 340))
    for anahtar, bas in bolumler:
        k.add(ops.create_op(bas, 100 * 2048, 512, fs_key=anahtar,
                            label=anahtar.upper()))
    assert k.apply(s).ok
    s.reload()
    dosyalar = {}
    for no in range(1, 5):
        veri = os.urandom(3 * MIB) + b"son"
        fs = s.filesystem(no)
        fs.write_file("/veri.bin", veri)
        fs.flush()
        dosyalar[no] = veri
    s.close_filesystems()
    # Ham onyukleyici: ilk bolumden once (bolum tablosu disi), 64. sektor.
    uboot = b"U-BOOT-SPL" + os.urandom(64 * 1024)
    s.image.write(64 * 512, uboot)
    # Cop: FAT32'nin bos kumelerinin sonuna ve bolumlenmemis alana
    p1 = s.table.get(1)
    cop_fat = (p1.start_lba + p1.sector_count) * 512 - 20 * MIB
    s.image.write(cop_fat, os.urandom(8 * MIB))
    s.image.write(500 * MIB, os.urandom(40 * MIB))      # 440..700 MB bos
    s.image.flush()

    tam = s.backup_disk(img_path("t93_tam.dub"))
    akilli = s.backup_disk(img_path("t93_akilli.dub"), used_only=True)
    assert not tam.used_only and akilli.used_only
    assert _clone.read_backup_info(akilli.path).used_only
    # 48 MB cop sikismaz: tam yedekte var, akillida yok
    assert tam.file_size - akilli.file_size > 40 * MIB, (tam.file_size,
                                                         akilli.file_size)
    araliklar = usedmap.disk_used_ranges(s.image)
    okunan = usedmap.total_bytes(araliklar)
    assert okunan < 120 * MIB, okunan

    # Hedefte eski veri: atlanan alanda aynen kalmali
    hedef = DiskImage.create(img_path("t93_geri.img"), 700 * MIB, overwrite=True)
    eski = os.urandom(MIB)
    hedef.write(600 * MIB, eski)
    restore(akilli.path, hedef)
    assert hedef.read(600 * MIB, MIB) == eski, "atlanan bloga yazildi"
    assert hedef.read(64 * 512, len(uboot)) == uboot, "ham onyukleyici yok"
    hedef.close()

    g = DiskSession.open(img_path("t93_geri.img"), readonly=True)
    for no in range(1, 5):
        assert g.filesystem(no).read("/veri.bin") == dosyalar[no], no
    g.close_filesystems()
    denetimler = {1: ["fsck.vfat", "-n"], 2: ["fsck.exfat", "-n"],
                  3: ["e2fsck", "-fn"], 4: ["ntfsfix", "-n"]}
    for no, komut in denetimler.items():
        arac = shutil.which(komut[0])
        if not arac:
            continue
        p = g.table.get(no)
        parca = img_path("t93_p%d.img" % no)
        with open(parca, "wb") as out:
            out.write(g.image.read(p.start_lba * 512, p.sector_count * 512))
        r = subprocess.run([arac] + komut[1:] + [parca], capture_output=True,
                           text=True)
        assert r.returncode == 0, (komut[0], r.stdout[-600:], r.stderr[-300:])
        os.unlink(parca)
    g.close()

    # Yedek gezilebilir kalir: atlanan alan sifir okunur
    dub = _clone.DubImage(akilli.path)
    assert dub.is_skipped(600 * MIB, MIB) and not dub.is_skipped(0, 512)
    assert dub.read(600 * MIB, 4096) == bytes(4096)
    dub.close()
    s.close()

    # EBR: buyuk bosluktan sonraki mantiksal bolumun EBR'si alinir
    d = DiskImage.create(img_path("t93_ebr.img"), 300 * MIB, overwrite=True)
    t = MBRTable.create(d)
    ext = 2048
    t.create_extended(ext, 290 * 2048)
    t.add_partition(ext + 2048, 20 * 2048, type_id=0x83)
    t.add_partition(ext + 120 * 2048, 20 * 2048, type_id=0x83)
    mantiksal = [p for p in MBRTable.read(d).partitions if p.logical]
    assert len(mantiksal) == 2 and mantiksal[1].ebr_lba
    r = usedmap.disk_used_ranges(d)
    for p in mantiksal:
        on = p.ebr_lba * 512
        assert any(a <= on < a + n for a, n in r), ("EBR alinmadi", p.ebr_lba)
    bosluk = (ext + 2048 + 20 * 2048 + 50 * 2048) * 512
    assert not any(a <= bosluk < a + n for a, n in r), "buyuk bosluk alindi"
    d.close()


@test
def t94_yedek_durdurma():
    """Yedekleme/geri yukleme durdurulur; yarim dosya silinir, aygit kapanir

    Arayuzun "Durdur" dugmesi ilerleme geri cagrisindan OperationCancelled
    firlatir. Cekirdekteki `except Exception` bloklari onu yutmamali
    (BaseException); paralel sikistirma havuzu kapanmali; yarim `.dub` ve
    yarim yeni goruntu diskte kalmamali.
    """
    from diskultimate.core import clone as _clone

    yol = img_path("t94.img")
    s = DiskSession.create(yol, 200 * MIB, scheme="mbr", overwrite=True)
    k = ops.OperationQueue()
    k.add(ops.create_op(2048, 150 * 2048, 512, fs_key="fat32", label="VERI"))
    assert k.apply(s).ok
    s.reload()
    fs = s.filesystem(1)
    fs.write_file("/a.bin", os.urandom(60 * MIB))
    fs.flush()
    s.close_filesystems()

    def durdur_sonra(n):
        sayac = [0]

        def report(mesaj, yuzde):
            sayac[0] += 1
            if sayac[0] > n:
                raise _clone.OperationCancelled()
        return report

    assert not issubclass(_clone.OperationCancelled, Exception)
    for akilli in (False, True):
        hedef = img_path("t94_%d.dub" % akilli)
        try:
            s.backup_disk(hedef, used_only=akilli, progress=durdur_sonra(3))
            raise AssertionError("durdurma islemedi")
        except _clone.OperationCancelled:
            pass
        assert not os.path.exists(hedef), "yarim yedek kaldi"

    tam = s.backup_disk(img_path("t94_tam.dub"))
    yeni = img_path("t94_yeni.img")
    try:
        DiskSession.restore_to_new_image(tam.path, yeni,
                                         progress=durdur_sonra(2))
        raise AssertionError("durdurma islemedi")
    except _clone.OperationCancelled:
        pass
    assert not os.path.exists(yeni), "yarim goruntu kaldi"
    # Durdurmadan sonra ayni oturum calismaya devam eder
    tekrar = s.backup_disk(img_path("t94_tekrar.dub"), used_only=True)
    assert tekrar.used_only
    s.close()


@test
def t95_mib_oncesi_baslayan_bolum():
    """1 MiB'dan once baslayan bolumlu MBR (SD kart: LBA 1) boyutlandirilir

    Kullanici bildirimi (2026-10-09): SD kart goruntusunde bolum 1 LBA 1'de
    basliyordu. Duzenleme modeli MBR'nin ilk kullanilabilir LBA'sini 2048
    sayip yerlesimi bastan "Bolum 1 onceki bolumle cakisiyor" diye
    reddediyordu; hicbir bolum boyutlandirilamiyordu. Cekirdekte de
    `check_range` bolum 1'i reddediyor, ustelik bunu dosya sistemi
    kucultulduktan **sonra** yapiyordu.
    """
    from diskultimate.core import queueedit
    from diskultimate.core.ptable import PartitionTableError
    from diskultimate.core.resize import fs_resize_info_for

    yol = img_path("t95.img")
    M = MIB // 512
    s = DiskSession.create(yol, 96 * MIB, scheme="mbr", overwrite=True)
    k = ops.OperationQueue()
    k.add(ops.create_op(2048, 20 * M, 512, fs_key="fat16", label="BOOT"))
    k.add(ops.create_op(2048 + 20 * M, 20 * M, 512, fs_key="fat16",
                        label="VERI"))
    assert k.apply(s).ok
    s.reload()
    icerik = os.urandom(3 * MIB)
    for i in (1, 2):
        fs = s.filesystem(i)
        fs.write_file("/veri.bin", icerik)
        fs.flush()
    s.close()
    # Bolum 1'i LBA 1'e kaydir (one dogru kopyalama: ileri sirayla guvenli)
    with open(yol, "r+b") as f:
        for n in range(20):
            f.seek((2048 + n * M) * 512)
            parca = f.read(MIB)
            f.seek((1 + n * M) * 512)
            f.write(parca)
        f.seek(446 + 8)
        f.write(struct.pack("<I", 1))
        f.seek(512 + 28)                    # BPB gizli sektor
        f.write(struct.pack("<I", 1))

    s = DiskSession.open(yol)
    assert s.table.get(1).start_lba == 1
    kuyruk = ops.OperationQueue()
    sinirlar = {p.index: fs_resize_info_for(s, p) for p in s.partitions}
    bak = lambda part: sinirlar.get(part.index)          # noqa: E731
    model = queueedit.build(s, kuyruk, limits=bak)
    assert model.validate() == "", model.validate()
    assert model.window(1)[0] == 1, model.window(1)

    once = {x.index: (x.new_start, x.new_count) for x in model.parts}
    model.get(1).new_count = 16 * M
    queueedit.commit(s, kuyruk, model, [1], before=once)
    model = queueedit.build(s, kuyruk, limits=bak)
    once = {x.index: (x.new_start, x.new_count) for x in model.parts}
    _alt, ust = model.window(2)
    model.get(2).new_count = ust - model.get(2).new_start + 1
    queueedit.commit(s, kuyruk, model, [2], before=once)
    assert len(kuyruk) == 2

    # Bolum 1 kendi yerinden geriye gidemez (LBA 0 MBR'dir)
    try:
        s.table.check_range(0, 16 * M, ignore_index=1)
        raise AssertionError("LBA 0 kabul edildi")
    except PartitionTableError:
        pass

    s.become_writable(confirm=True)
    sonuc = kuyruk.apply(s)
    assert sonuc.ok, sonuc.summary()
    s.reload()
    a, b = s.table.get(1), s.table.get(2)
    assert (a.start_lba, a.sector_count) == (1, 16 * M), a
    assert b.end_lba == ust, (b.end_lba, ust)
    for i in (1, 2):
        assert s.filesystem(i).read("/veri.bin") == icerik, i
    s.close()


@test
def t96_disk_kullanilan_alan_toplami():
    """Disk ozeti bolumlerin kullanilan alanini toplar; bilinmeyen sifir sayilmaz

    Kullanici istegi (2026-10-09): disk secilince butun bolumlerin toplam
    kullanilan alani gorunmeli. Genisletilmis kapsayici sayilmaz (mantiksal
    bolumler zaten sayilir); dolulugu okunamayan bolum "0 kullanilan" gibi
    sunulmaz, ayrica belirtilir.
    """
    from diskultimate.core.ptable import Partition, usage_text, usage_total

    parcalar = [
        Partition(index=1, start_lba=2048, sector_count=1000, fs_used=300,
                  fs_total=500),
        Partition(index=2, start_lba=4096, sector_count=9000, type_id=0x05),
        Partition(index=5, start_lba=6144, sector_count=1000, logical=True,
                  fs_used=200, fs_total=500),
        Partition(index=6, start_lba=8192, sector_count=1000, logical=True),
    ]
    t = usage_total(parcalar)
    assert (t.used, t.measured, t.unmeasured) == (500, 2, 1), t
    metin = usage_text(t, 1000)
    assert metin.startswith("500 B (%50.0)") and "1 " in metin, metin
    assert usage_text(usage_total([parcalar[3]]), 1000) == "bilinmiyor"

    # Gercek goruntu: ozet satiri FAT'in kendi olcumuyle ayni
    yol = img_path("t96.img")
    s = DiskSession.create(yol, 64 * MIB, scheme="mbr", overwrite=True)
    k = ops.OperationQueue()
    k.add(ops.create_op(2048, 40 * 2048, 512, fs_key="fat32", label="V"))
    assert k.apply(s).ok
    s.reload()
    fs = s.filesystem(1)
    fs.write_file("/a.bin", b"x" * (3 * MIB))
    fs.flush()
    s.close_filesystems()
    s.reload()
    beklenen = s.detect_fs(s.table.get(1)).used_bytes
    assert usage_total(s.partitions).used == beklenen
    from diskultimate.i18n import tr
    assert s.summary()[tr("Kullanilan")].startswith(human_size(beklenen))
    s.close()



@test
def t97_guc_platform_katmani():
    """Uyku engeli konup kalkar; desteklenmeyen guc eylemi nedenle reddedilir

    Kullanici istegi (2026-10-09, DiskGenius "When Finished / Prevent
    sleeping"): klonlama gibi uzun islerde uyku engellenir, bitince
    kapat/yeniden baslat/uyku/hazirda beklet secilebilir. Bu test gercek
    eylemi CALISTIRMAZ; yalnizca destek sorgusu ve engelin yasam dongusu.
    """
    from diskultimate.core.platform import (POWER_ACTIONS, SleepInhibitor,
                                            power_action,
                                            power_action_supported)
    for kind in POWER_ACTIONS:
        ok, why = power_action_supported(kind)
        assert isinstance(ok, bool) and (ok or why), (kind, ok, why)
    assert power_action_supported("format_c")[0] is False
    assert power_action("format_c")[0] is False     # bilinmeyen eylem reddedilir
    engel = SleepInhibitor("DiskUltimate testi")
    engel.release()                                  # konmadan kaldirmak zararsiz
    ok, why = engel.acquire()
    if not ok:
        # systemd-inhibit olmayan ortam (konteyner): neden bos olmamali
        assert why, "engel konamadi ama neden yok"
        raise Atlandi(f"uyku engeli bu ortamda konamiyor: {why}")
    assert engel.active
    assert engel.acquire() == (True, "")             # ikinci kez: ayni engel
    engel.release()
    assert not engel.active



@test
def t98_tek_form_klon_cekirdegi():
    """Tek klon formunun cekirdegi: dosyaya/acik goruntuye klon, durdurmada yarim dosya silinir

    Kullanici istegi (2026-10-09): klonlama secimi ve takibi tek formda
    (ADR 0096). `DiskSession.clone_between` formun tek cagrisidir. Ayrica
    iki eski acik: durdurulan/basarisiz klon yarim dosyayi birakiyordu;
    hedef olarak kaynagin kendi dosyasi secilirse kaynak sifirlanirdi.
    """
    from diskultimate.core import clone as clone_mod
    from diskultimate.core.disksource import DiskSource
    from diskultimate.core.session import SessionError

    kaynak_yol = img_path("t98_kaynak.img")
    s = DiskSession.create(kaynak_yol, 48 * MIB, scheme="gpt", overwrite=True)
    k = ops.OperationQueue()
    k.add(ops.create_op(2048, 30 * 2048, 512, fs_key="fat16", label="KLON"))
    assert k.apply(s).ok
    s.reload()
    icerik = os.urandom(2 * MIB)
    fs = s.filesystem(1)
    fs.write_file("/veri.bin", icerik)
    fs.flush()
    s.close_filesystems()
    s.image.flush()
    kaynak = DiskSource(kind="image", path=kaynak_yol, label="t98_kaynak.img",
                        size=s.image.size, session=s)

    # 1) yeni dosyaya
    hedef_yol = img_path("t98_klon.img")
    sonuc = DiskSession.clone_between(kaynak, None, dest_path=hedef_yol)
    assert os.path.samefile(sonuc, hedef_yol)
    k1 = DiskSession.open(hedef_yol, readonly=True)
    assert k1.filesystem(1).read("/veri.bin") == icerik
    k1.close()

    # 2) uygulamada acik baska bir goruntuye (o oturumun tutamaci)
    acik_yol = img_path("t98_acik.img")
    t = DiskSession.create(acik_yol, 64 * MIB, scheme="mbr", overwrite=True)
    hedef = DiskSource(kind="image", path=acik_yol, label="t98_acik.img",
                       size=t.image.size, session=t)
    kopyalanan = DiskSession.clone_between(kaynak, hedef)
    assert kopyalanan == s.image.size, kopyalanan
    assert t.scheme == "gpt" and t.filesystem(1).read("/veri.bin") == icerik
    t.close()

    # 3) durdurma: yarim dosya kalmaz
    yarim = img_path("t98_yarim.img")

    def durdur(mesaj, yuzde):
        if yuzde > 0:
            raise clone_mod.OperationCancelled()
    try:
        DiskSession.clone_between(kaynak, None, dest_path=yarim, progress=durdur)
        raise AssertionError("durdurma islemedi")
    except clone_mod.OperationCancelled:
        pass
    assert not os.path.exists(yarim), "yarim klon dosyasi kaldi"

    # 4) kaynagin kendi dosyasi hedef olamaz; kaynak saglam kalir
    try:
        DiskSession.clone_between(kaynak, None, dest_path=kaynak_yol)
        raise AssertionError("kaynagin uzerine klon kabul edildi")
    except SessionError:
        pass
    assert s.filesystem(1).read("/veri.bin") == icerik
    s.close()



@test
def t99_acik_diskin_baglama_bilgisi_tazelenir():
    """Disk acildiktan sonra baglanan bolum gorunur; /proc/mounts kacislari cozulur

    Kullanici bildirimi (2026-10-09): nvme0n1 acikken bolum 3 ve 4 dosya
    yoneticisinden baglandi; uygulama "bagli degil" gostermeye devam etti,
    "Bagla" basarili donup ekranda hicbir sey degismedi. Oturum diski
    actigi andaki DiskInfo'yu tutuyordu. Bagli bolum uyarisi (CLAUDE.md
    kural 5) da bu bayat bilgiye dayaniyordu.
    """
    from diskultimate.core.physical import (DiskInfo, fill_mount_points,
                                            sync_mounts)
    from diskultimate.core.platform import decode_mount_field
    from diskultimate.core.ptable import Partition

    assert decode_mount_field(r"/media/pc/Basic\040data\040partition") == \
        "/media/pc/Basic data partition"
    assert decode_mount_field(r"/a\011b\134c") == "/a\tb\\c"

    acik = DiskInfo(path="/dev/nvme0n1", name="nvme0n1",
                    mounted=["nvme0n1p5 → /"], mount_map={349625647104: "/"})
    bolumler = [Partition(index=4, start_lba=268675072, sector_count=1000),
                Partition(index=5, start_lba=682862592, sector_count=1000)]
    fill_mount_points(acik, bolumler)
    assert [p.mount_point for p in bolumler] == ["", "/"]

    taze = DiskInfo(path="/dev/nvme0n1", name="nvme0n1",
                    mounted=["nvme0n1p4 → /media/pc/Data", "nvme0n1p5 → /"],
                    mount_map={137561636864: "/media/pc/Data",
                               349625647104: "/"})
    assert sync_mounts(acik, taze) is True
    assert acik.mounted == taze.mounted and acik.mounted is not taze.mounted
    fill_mount_points(acik, bolumler)
    assert [p.mount_point for p in bolumler] == ["/media/pc/Data", "/"]
    assert sync_mounts(acik, taze) is False          # degisiklik yok
    assert sync_mounts(acik, acik) is False          # ayni nesne
    assert sync_mounts(acik, None) is False          # listede yok: dokunma
    assert acik.mounted                              # bilinmiyor != bagli degil
    cikti = DiskInfo(path="/dev/nvme0n1", name="nvme0n1",
                     mounted=["nvme0n1p5 → /"], mount_map={349625647104: "/"})
    assert sync_mounts(acik, cikti) is True          # cikarildi
    fill_mount_points(acik, bolumler)
    assert [p.mount_point for p in bolumler] == ["", "/"]


def _dis_denetim(fs_key: str, yol: str) -> None:
    """Varsa harici araclarla birim denetimi (yoksa sessizce gecer)."""
    araclar = {"fat32": ["fsck.vfat", "-n"], "exfat": ["fsck.exfat", "-n"],
               "ntfs": ["ntfsfix", "-n"], "ext4": ["e2fsck", "-fn"],
               "ext2": ["e2fsck", "-fn"], "hfsplus": ["fsck.hfsplus", "-n"]}
    if fs_key not in araclar:
        return
    arac = shutil.which(araclar[fs_key][0]) or shutil.which(
        araclar[fs_key][0], path="/usr/sbin:/sbin")
    if not arac:
        return
    r = subprocess.run([arac] + araclar[fs_key][1:] + [yol], capture_output=True,
                       text=True)
    assert r.returncode == 0, (fs_key, (r.stdout + r.stderr)[-400:])


def main() -> int:
    print(f"Platform   : {PLATFORM_NAME}")
    if not check_environment():
        return 2
    basarili, basarisiz, atlanan = 0, [], []
    for fn in RESULTS:
        ad = fn.__name__
        aciklama = (fn.__doc__ or "").strip()
        print(f"  {ad}: {aciklama} ... ", end="", flush=True)
        try:
            fn()
            print("TAMAM")
            basarili += 1
        except Atlandi as exc:
            print(f"ATLANDI ({exc})")
            atlanan.append((ad, str(exc)))
        except Exception as exc:
            print("BASARISIZ")
            traceback.print_exc()
            basarisiz.append((ad, str(exc)))
        finally:
            cleanup_test_files(ad.split("_")[0])   # t01, t02, ...
    # Atlanan test "basarili" sayilmaz; ozette acikca gorunur ki "18/18"
    # ciktisi o ortamda kosmamis bir testi gizlemesin.
    ozet = f"\nSonuc: {basarili}/{len(RESULTS)} basarili"
    if atlanan:
        ozet += f" · {len(atlanan)} atlandi"
    print(ozet)
    for ad, neden in atlanan:
        print(f"  - {ad} atlandi: {neden}")
    for ad, hata in basarisiz:
        print(f"  ! {ad}: {hata}")
    return 1 if basarisiz else 0


if __name__ == "__main__":
    sys.exit(main())
