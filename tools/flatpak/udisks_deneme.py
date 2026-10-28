"""UDisks2 uzerinden blok aygiti acma denemesi (Flatpak on calismasi).

Flatpak icinde root olunamaz, pkexec calismaz. Disk erisiminin tek yolu
UDisks2'nin `Block.OpenDevice` cagrisidir: polkit izin verirse servis
aygiti kendisi acar ve dosya tanitici (fd) dondurur. Bu betik o yolu
olcer; uygulama koduna dokunmaz.

    python3 udisks_deneme.py /dev/loopN --isaret DU-UDISKS [--yaz] [--bekle izin-yok]

Cikis kodu: 0 basarili, 2 polkit reddetti (NotAuthorized), 1 diger hata.
`--bekle izin-yok` verilirse beklenen sonuc 2'dir; o zaman 0 doner.

Bagimlilik: jeepney (saf Python D-Bus, fd aktarimi 0.7+). Yalnizca bu
deneme ve ileride Flatpak paketi icin; cekirdek kurali (bagimlilik yok)
etkilenmez.
"""

import argparse
import errno
import fcntl
import os
import struct
import sys

from jeepney import DBusAddress, DBusErrorResponse, new_method_call
from jeepney.wrappers import unwrap_msg
from jeepney.io.blocking import open_dbus_connection
from jeepney.low_level import MessageFlag

BUS = "org.freedesktop.UDisks2"
BLKGETSIZE64 = 0x80081272
BLKSSZGET = 0x1268
SATIR = "-" * 60


def baslik(metin):
    print(f"\n{SATIR}\n{metin}\n{SATIR}", flush=True)


def ay_metin(deger):
    """`ay` (sonu NUL) -> str"""
    return bytes(deger).rstrip(b"\0").decode("utf-8", "replace")


def cagir(conn, adres, yontem, imza=None, govde=(), etkilesimli=False):
    msg = new_method_call(adres, yontem, imza, govde)
    if etkilesimli:
        # Masaustunde polkit ajani parola penceresini bu bayrakla acar.
        msg.header.flags |= MessageFlag.allow_interactive_authorization
    return unwrap_msg(conn.send_and_get_reply(msg, timeout=60))


def ortam():
    baslik("Ortam")
    print("flatpak icinde   :", os.path.exists("/.flatpak-info"))
    print("uid / gruplar    :", os.getuid(), os.getgroups())
    print("python           :", sys.version.split()[0])
    try:
        print("/sys/block       :", sorted(os.listdir("/sys/block")))
    except OSError as exc:
        print("/sys/block       : OKUNAMADI", exc)
    try:
        with open("/proc/mounts") as f:
            kok = [s for s in f.read().splitlines() if s.split()[1:2] == ["/"]]
        print("/proc/mounts '/' :", kok)
    except OSError as exc:
        print("/proc/mounts     : OKUNAMADI", exc)


def dogrudan_ac(aygit):
    baslik(f"Dogrudan os.open({aygit}, O_RDONLY)")
    print("var mi           :", os.path.exists(aygit))
    try:
        fd = os.open(aygit, os.O_RDONLY)
    except OSError as exc:
        print("sonuc            : ACILAMADI", errno.errorcode.get(exc.errno), exc)
        return
    os.close(fd)
    print("sonuc            : ACILDI (kullanici disk grubunda olabilir)")


def cekirdek_listesi():
    """Uygulamanin kendi list_disks()'i: sistem diski korumasi (katman 3)
    korumali ortamda da dogru mu?"""
    baslik("physical.list_disks()")
    kok = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    sys.path.insert(0, kok)
    try:
        from diskultimate.core import physical
        for d in physical.list_disks():
            print(f"{d.path:14} boyut={d.size:>14} is_system={d.is_system} "
                  f"bagli={d.mounted}")
    except Exception as exc:  # deneme raporu: her hata yazilsin
        print("HATA:", type(exc).__name__, exc)


def udisks_listesi(conn):
    """UDisks2'nin gordugu bloklar: HintSystem ve baglama noktalari.
    Korumali ortamda /proc/mounts yaniltirsa sistem diski buradan bulunur."""
    baslik("UDisks2 GetManagedObjects")
    nesneler = cagir(conn, DBusAddress("/org/freedesktop/UDisks2", BUS,
                                       "org.freedesktop.DBus.ObjectManager"),
                     "GetManagedObjects")[0]
    for yol, arayuzler in sorted(nesneler.items()):
        blok = arayuzler.get("org.freedesktop.UDisks2.Block")
        if not blok:
            continue
        fs = arayuzler.get("org.freedesktop.UDisks2.Filesystem", {})
        noktalar = [ay_metin(n) for n in fs.get("MountPoints", ("aay", []))[1]]
        print(f"{ay_metin(blok['Device'][1]):16} boyut={blok['Size'][1]:>14} "
              f"HintSystem={blok['HintSystem'][1]} tur={blok['IdType'][1]!r} "
              f"baglama={noktalar}")


def coz(conn, aygit):
    yanit = cagir(conn, DBusAddress("/org/freedesktop/UDisks2/Manager", BUS,
                                    "org.freedesktop.UDisks2.Manager"),
                  "ResolveDevice", "a{sv}a{sv}", ({"path": ("s", aygit)}, {}))
    if not yanit[0]:
        raise SystemExit(f"UDisks2 {aygit} aygitini tanimiyor")
    return yanit[0][0]


def fd_ac(conn, nesne, kip):
    yanit = cagir(conn, DBusAddress(nesne, BUS, "org.freedesktop.UDisks2.Block"),
                  "OpenDevice", "sa{sv}", (kip, {}), etkilesimli=True)
    tanitici = yanit[0]
    return tanitici.to_raw_fd() if hasattr(tanitici, "to_raw_fd") else int(tanitici)


def olc_fd(fd):
    boyut = struct.unpack("<Q", fcntl.ioctl(fd, BLKGETSIZE64, b"\0" * 8))[0]
    sektor = struct.unpack("<i", fcntl.ioctl(fd, BLKSSZGET, b"\0" * 4))[0]
    print("BLKGETSIZE64     :", boyut)
    print("BLKSSZGET        :", sektor)
    print("lseek(SEEK_END)  :", os.lseek(fd, 0, os.SEEK_END))
    return sektor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("aygit")
    ap.add_argument("--isaret", required=True, help="sektor 0'da beklenen metin")
    ap.add_argument("--yaz", action="store_true", help="rw fd ile sektor 1'e yaz")
    ap.add_argument("--bekle", choices=["basari", "izin-yok"], default="basari")
    args = ap.parse_args()

    ortam()
    dogrudan_ac(args.aygit)
    cekirdek_listesi()

    conn = open_dbus_connection(bus="SYSTEM", enable_fds=True)
    udisks_listesi(conn)
    nesne = coz(conn, args.aygit)

    baslik(f"OpenDevice('r')  {nesne}")
    try:
        fd = fd_ac(conn, nesne, "r")
    except DBusErrorResponse as exc:
        print("REDDEDILDI       :", exc.name, exc.data)
        # NotAuthorized / NotAuthorizedCanObtain (ajan olsa sorulabilirdi) /
        # NotAuthorizedDismissed (kullanici pencereyi kapatti)
        if exc.name.startswith("org.freedesktop.UDisks2.Error.NotAuthorized"):
            return 0 if args.bekle == "izin-yok" else 2
        return 1
    if args.bekle == "izin-yok":
        print("BEKLENMEDIK: polkit kurali yokken fd verildi")
        return 1

    olc_fd(fd)
    ilk = os.pread(fd, 512, 0)
    print("sektor 0 basi    :", ilk[:32])
    print("55AA imzasi      :", ilk[510:512] == b"\x55\xaa")
    if not ilk.startswith(args.isaret.encode()):
        print("HATA: isaret bulunamadi")
        return 1
    try:
        os.pwrite(fd, b"X" * 512, 512)
        print("HATA: salt okunur fd'ye yazilabildi")
        return 1
    except OSError as exc:
        print("r fd'ye yazma    : reddedildi", errno.errorcode.get(exc.errno))
    os.close(fd)

    if args.yaz:
        baslik("OpenDevice('rw')")
        fd = fd_ac(conn, nesne, "rw")
        veri = (args.isaret + "-YAZILDI").encode().ljust(512, b"\0")
        os.pwrite(fd, veri, 512)
        os.fsync(fd)
        geri = os.pread(fd, 512, 512)
        os.close(fd)
        print("sektor 1 geri    :", geri[:32])
        if geri != veri:
            print("HATA: yazilan geri okunamadi")
            return 1

    print("\nSONUC: TAMAM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
