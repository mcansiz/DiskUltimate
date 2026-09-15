"""DiskUltimate cekirdek test paketi (harici test kutuphanesi gerektirmez).

Calistirma:  python3 -m tests.run_all
Sonuclar .claude/logs/test-<tarih>.md dosyasina da yazilabilir (--log).
"""
from __future__ import annotations

import datetime
import os
import shutil
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
