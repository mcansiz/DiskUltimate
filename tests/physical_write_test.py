"""Fiziksel diske YAZMA testi — yalnizca ozel olarak ayrilmis bos test diskinde.

Bu betik gercek bir diske yazar. Kaza olmamasi icin hedef disk **alti olcutun
tamamini** saglamadikca calismaz:

  1. Aygit yolu komut satirinda ACIKCA verilmis olmali
  2. `--onayla` bayragi verilmis olmali
  3. Disk sistem diski OLMAMALI
  4. Diskte bagli (mounted) bolum OLMAMALI
  5. Disk bilgileri eksiksiz okunabilmis olmali (yetki var)
  6. Disk boyutu ust sinirin altinda olmali (varsayilan 8 GB;
     `--azami-gb=N` ile yukseltilebilir — bu olcut yalnizca bir sezgidir,
     3, 4 ve 5. olcutler bu bayraktan **etkilenmez**)

Kullanim:
    sudo python3 -m tests.physical_write_test /dev/sdb --onayla
    python  -m tests.physical_write_test \\\\.\\PhysicalDrive1 --onayla   (Yonetici)

`--bagli-birimlere-izin-ver`: diskte bagli birim varsa (orn. onceki testin
olusturdugu bolum) devam eder; birimler kilitlenip baglantisi kesilir.
Sistem diski korumasi bu bayraktan etkilenmez.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from diskultimate.core.physical import (PhysicalDisk,  # noqa: E402
                                        PhysicalDiskError, can_access,
                                        find_disk, list_disks)
from diskultimate.core.ptable import human_size  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402

MIB = 1024 * 1024
AZAMI_BOYUT = 8 * 1024 * MIB       # bundan buyuk diske test yapilmaz


def olcutleri_dogrula(yol: str, azami: int = AZAMI_BOYUT,
                      bagli_izin: bool = False):
    """Hedefin test diski olduguna dair alti olcutu denetler.

    `bagli_izin=True` yalnizca **bagli bolum** olcutunu gevsetir (onceki testin
    olusturdugu birim isletim sistemi tarafindan baglanmis olabilir). Diger bes
    olcut — ozellikle sistem diski korumasi — aynen gecerlidir.
    """
    bilgi = find_disk(yol)
    if bilgi is None:
        print(f"HATA: disk bulunamadi: {yol}")
        print("Mevcut diskler:")
        for d in list_disks():
            print(f"  {d.path}  {human_size(d.size)}  {d.model}")
        return None

    engeller = []
    if bilgi.is_system:
        engeller.append("disk SISTEM DISKI")
    if bilgi.mounted and not bagli_izin:
        engeller.append(f"diskte bagli bolum var: {', '.join(bilgi.mounted)} "
                        "(bilerek devam etmek icin --bagli-birimlere-izin-ver)")
    if not bilgi.info_complete:
        engeller.append("disk bilgileri okunamadi (yetki yok)")
    if bilgi.size > azami:
        engeller.append(f"disk cok buyuk ({human_size(bilgi.size)} > "
                        f"{human_size(azami)}) — test diski olmadigi varsayiliyor")
    if bilgi.readonly:
        engeller.append("disk donanimsal olarak yazma korumali")

    print(f"Hedef: {bilgi.path}")
    for anahtar, deger in bilgi.summary().items():
        print(f"  {anahtar:<16}: {deger}")
    if engeller:
        print("\nTEST YAPILMADI — su olcutler saglanmadi:")
        for e in engeller:
            print(f"  * {e}")
        return None
    if bilgi.mounted and bagli_izin:
        print(f"\nUYARI: bagli birim(ler) var: {', '.join(bilgi.mounted)}")
        print("Bunlar kilitlenip baglantisi kesilecek (FSCTL_LOCK_VOLUME + DISMOUNT).")
    print("\nOlcutler saglandi; bu disk test diski olarak kabul edildi.")
    return bilgi


def testi_calistir(bilgi) -> int:
    print("\n" + "=" * 60)
    print("YAZMA TESTI")
    print("=" * 60)
    oturum = DiskSession.open_physical(bilgi, readonly=False, confirm=True)
    try:
        print(f"\n[1] Disk yazma modunda acildi: {human_size(oturum.image.size)}")

        print("[2] GPT bolum tablosu olusturuluyor")
        oturum.create_table("gpt")
        assert oturum.scheme == "gpt", oturum.scheme

        bos = oturum.free_regions()[0]
        boyut = min(bos.sector_count, 512 * MIB // 512)
        print(f"[3] FAT32 bolum olusturuluyor ({human_size(boyut * 512)})")
        part = oturum.create_partition(bos.start_lba, boyut, fs_key="fat32",
                                       label="DUTEST", name="DiskUltimate Test")
        print(f"    -> bolum {part.index}: {part.fs_type}, {human_size(part.size)}")

        print("[4] Dosya yaziliyor")
        fs = oturum.filesystem(part.index)
        icerik = b"DiskUltimate fiziksel disk yazma testi\n" * 500
        fs.mkdir("/deneme")
        fs.write_file("/deneme/kanit.txt", icerik)
        fs.flush()

        print("[5] Disk kapatilip yeniden aciliyor (gercek kalicilik denetimi)")
        oturum.close()
        oturum = DiskSession.open_physical(find_disk(bilgi.path), readonly=True)
        yeniden = oturum.filesystem(1).read("/deneme/kanit.txt")
        assert yeniden == icerik, "geri okunan veri farkli!"
        print(f"    -> {len(yeniden)} bayt dogrulandi, icerik ayni")
        print(f"    -> sema: {oturum.scheme_name}, bolum: "
              f"{[(p.index, p.fs_type, p.fs_label) for p in oturum.partitions]}")

        print("\n[6] exFAT ile yeniden bicimlendirme")
        oturum.close()
        oturum = DiskSession.open_physical(find_disk(bilgi.path), readonly=False,
                                           confirm=True)
        oturum.format_partition(1, "exfat", label="DUEXFAT")
        fs = oturum.filesystem(1)
        fs.write_file("/exfat.txt", b"exFAT da calisiyor\n")
        fs.flush()
        oturum.close()
        oturum = DiskSession.open_physical(find_disk(bilgi.path), readonly=True)
        print(f"    -> {oturum.partitions[0].fs_type}, "
              f"dosya: {oturum.filesystem(1).read('/exfat.txt')!r}")

        print("\n[7] Isletim sistemine bolum tablosu degisikligi bildiriliyor")
        oturum.close()
        oturum = DiskSession.open_physical(find_disk(bilgi.path), readonly=False,
                                           confirm=True)
        bildirildi = oturum.notify_os()
        print(f"    -> rescan cagrisi: {'basarili' if bildirildi else 'yapilamadi'}")
        oturum.close()
        _os_gorebiliyor_mu(bilgi)

        print("\nSONUC: TUM ADIMLAR BASARILI")
        return 0
    finally:
        try:
            oturum.close()
        except Exception:
            pass


def _os_gorebiliyor_mu(bilgi) -> None:
    """Isletim sisteminin kendi araclariyla birimi gorup gormedigini raporlar."""
    from diskultimate.core.platform import IS_LINUX, IS_MACOS, IS_WINDOWS, run_tool
    print("\n    Isletim sistemi dogrulamasi:")
    try:
        if IS_LINUX:
            ad = os.path.basename(bilgi.path)
            sonuc = run_tool(["lsblk", "-f", bilgi.path], timeout=20)
            for satir in (sonuc.stdout or "").strip().splitlines():
                print(f"      {satir}")
        elif IS_WINDOWS:
            numara = "".join(ch for ch in bilgi.name if ch.isdigit())
            ps = ("Get-Disk -Number {0} | Format-List Number,PartitionStyle; "
                  "Get-Partition -DiskNumber {0} -ErrorAction SilentlyContinue | "
                  "Format-Table PartitionNumber,DriveLetter,Size,Type -AutoSize; "
                  "Get-Volume -ErrorAction SilentlyContinue | "
                  "Where-Object {{ $_.FileSystem -in 'exFAT','FAT32' }} | "
                  "Format-Table DriveLetter,FileSystemLabel,FileSystem -AutoSize"
                  ).format(numara)
            sonuc = run_tool(["powershell", "-NoProfile", "-Command", ps], timeout=60)
            for satir in (sonuc.stdout or "").strip().splitlines():
                if satir.strip():
                    print(f"      {satir}")
        elif IS_MACOS:
            sonuc = run_tool(["diskutil", "list", bilgi.path], timeout=20)
            for satir in (sonuc.stdout or "").strip().splitlines():
                print(f"      {satir}")
    except Exception as exc:
        print(f"      (dogrulama calistirilamadi: {exc})")


def main() -> int:
    argv = [a for a in sys.argv[1:]]
    onay = "--onayla" in argv
    bagli_izin = "--bagli-birimlere-izin-ver" in argv
    # Boyut siniri bir guvenlik sezgisidir: "bu kadar buyuk bir disk muhtemelen
    # test diski degildir". 10-16 GB'lik ayrilmis test diskleri yaygin oldugu
    # icin acikca yukseltilebilir; digerlerini gevsetmez.
    azami = AZAMI_BOYUT
    for a in argv:
        if a.startswith("--azami-gb="):
            try:
                azami = max(1, int(a.split("=", 1)[1])) * 1024 * MIB
            except ValueError:
                print(f"Gecersiz --azami-gb degeri: {a}")
                return 2
    yollar = [a for a in argv if not a.startswith("--")]
    if not yollar:
        print(__doc__)
        print("\nMevcut diskler:")
        for d in list_disks():
            isaret = {"sistem": "[!]", "bagli": "[*]", "bilinmiyor": "[?]",
                      "normal": "   "}[d.risk_level]
            print(f"  {isaret} {d.path:<24} {human_size(d.size):>10}  {d.model}")
        return 1
    # Olcutler once denetlenir: kullanici yetki almadan once hedefin uygun olup
    # olmadigini gorebilsin (bu yol yalnizca listeleme bilgisine bakar; hicbir
    # sektor okunmaz, hicbir yazma yapilmaz).
    bilgi = olcutleri_dogrula(yollar[0], azami=azami, bagli_izin=bagli_izin)
    if bilgi is None:
        return 2
    if not can_access():
        print("\nHATA: yonetici/root yetkisi gerekiyor (hicbir sey yazilmadi).")
        return 1
    if not onay:
        print("\n--onayla bayragi verilmedi; hicbir sey yazilmadi.")
        print(f"Calistirmak icin: {sys.argv[0]} {yollar[0]} --onayla")
        return 3
    try:
        return testi_calistir(bilgi)
    except AssertionError as exc:
        print(f"\nDOGRULAMA HATASI: {exc}")
        return 4
    except PhysicalDiskError as exc:
        print(f"\nDISK HATASI: {exc}")
        return 5


if __name__ == "__main__":
    sys.exit(main())
