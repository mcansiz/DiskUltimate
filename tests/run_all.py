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
        assert "33 MB" in str(exc), exc
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
        # Kendi bicimlendiricimizin urettigi birimlerde metadata_csum/64bit
        # yoktur, bu yuzden yazma desteklenmelidir. Ayrintili dogrulama
        # (her adimda e2fsck) tests/ext_write_check.py icindedir.
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
    assert erisim.writable, f"NTFS yazilabilir olmali: {erisim.write_reason}"
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

    # -- uygunluk yetkiliyken kapali, nedeni acik --
    uygun, neden = pf.elevation_available()
    if elevated:
        assert not uygun, "zaten yetkiliyken yukseltme sunulmamali"
        assert "zaten" in neden.lower(), neden
        # Zaten yetkiliyken cagrilsa bile hicbir sey baslatilmamali
        baslatildi, hata = pf.relaunch_elevated()
        assert not baslatildi and hata == neden, (baslatildi, hata)
    else:
        assert uygun or neden, "ya yukseltilebilmeli ya da nedeni olmali"


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
    #    Okuyucumuz "." girisini listelemez, bu yuzden ham cozumlenir.
    kok = fs.record(5).find(0x90, "$I30")
    deger = kok.value
    baslangic, uzunluk = _struct.unpack_from("<II", deger, 0x10)
    pos, adlar = 0x10 + baslangic, []
    while pos + 0x10 <= 0x10 + uzunluk:
        ref, boy, anahtar, bayrak = _struct.unpack_from("<QHHH", deger, pos)
        if boy < 0x10 or bayrak & 0x02:
            break
        n = deger[pos + 0x50]
        adlar.append((deger[pos + 0x52:pos + 0x52 + n * 2].decode("utf-16-le"),
                      ref & 0xFFFFFFFFFFFF))
        pos += boy
    assert (".", 5) in adlar, f"kok dizinde '.' girisi yok: {adlar}"
    assert ("$MFT", 0) in adlar and ("$Secure", 9) in adlar, adlar
    # Kullaniciya gosterilen listede "." gorunmemeli
    assert all(e.name != "." for e in fs.listdir("/")), "'.' listelenmis"

    # 3) $Secure gercek icerik tasimali
    secure = fs.record(9)
    sds = fs.read_attribute(secure.find(0x80, "$SDS"))
    assert len(sds) == 0x400FC, f"$SDS boyutu {len(sds):#x}, 0x400fc bekleniyor"
    assert sds[:0xFC] == sds[0x40000:0x40000 + 0xFC], "$SDS aynasi tutmuyor"
    kimlikler = {}
    pos = 0
    while pos + 20 <= 0xFC:
        karma, kimlik, ofset, boy = _struct.unpack_from("<IIQI", sds, pos)
        if boy == 0:
            break
        tanim = sds[pos + 20:pos + boy]
        assert security_hash(tanim) == karma, f"karma tutmuyor: {kimlik:#x}"
        assert ofset == pos, (ofset, pos)
        kimlikler[kimlik] = tanim
        pos = (pos + boy + 15) & ~15
    assert set(kimlikler) == {SECURITY_ID_SYSTEM, SECURITY_ID_FULL}, \
        list(map(hex, kimlikler))
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
        assert sayi == 2, f"{ad} icinde {sayi} giris var, 2 bekleniyor"

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
