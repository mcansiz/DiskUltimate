"""OS parola sıfırlama denetimi — Linux yolu (ADR 0100).

1. `sha512crypt` saf-Python uygulaması, ana makinedeki stdlib `crypt` ile
   birebir mi (KAT). `crypt` yoksa (Windows ana makine, 3.13+) bu adım atlanır.
2. Uçtan uca: ext4 imajı oluştur, /etc/passwd + /etc/shadow yaz, kullanıcıları
   listele, parola belirle / boşalt / kilitle / aç, geri oku + e2fsck.

Çalıştırma:  python3 -m tests.ospass_check
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from diskultimate.core import ospass                      # noqa: E402
from diskultimate.core.ext import format_ext              # noqa: E402
from diskultimate.core.filesystem import open_filesystem  # noqa: E402
from diskultimate.core.fsdetect import detect             # noqa: E402
from diskultimate.core.image import DiskImage, PartitionView  # noqa: E402
from diskultimate.paths import scratch                    # noqa: E402

MIB = 1024 * 1024
_fail = 0


def check(name, ok):
    global _fail
    print(("  TAMAM  " if ok else "  HATA   ") + name)
    if not ok:
        _fail += 1


def main():
    print("=== OS parola sifirlama denetimi (Linux) ===\n")

    print("sha512crypt KAT:")
    # Sabit bilinen-cevap vektorleri (openssl `passwd -6` ile uretilip
    # dogrulandi). Oracle'a BAGLI DEGIL: her platformda kosar (macOS'un
    # stdlib crypt'i ve LibreSSL openssl'i $6$ desteklemez; oraya guvenilmez).
    KAT = [
        (b"Parola123!", "abcdefgh",
         "$6$abcdefgh$uzhEz.DiIAXjMSVzrtd.y3hMZV5c4yA8ij0g7zD6erejPG6R3RMGQ"
         "36j8jL3uyKgAdeWt4hX0fVVVZebhfQJB."),
        (b"x", "0123456789abcdef",
         "$6$0123456789abcdef$8YztVyzwDEH96nI5J4SMvvvaVH43cm6MjQyfl3gZsAa8rZ"
         "MsHQVTiLQraFpug5a6xlaOJbzt.7jq8UBJs4F7M1"),
    ]
    for pw, salt, expected in KAT:
        check(f"$6${salt}: {pw!r}", ospass.sha512crypt(pw, salt) == expected)
    check("bos parola $6$ uretir",
          ospass.sha512crypt(b"", "saltsalt").startswith("$6$saltsalt$"))
    # Bonus: gercek bir $6$ oracle'i varsa (Linux), rastgele bir durum da
    # ona karsi dogrulanir. Oracle $6$ desteklemiyorsa (macOS) sessizce atlanir.
    oracle = _oracle()
    if oracle and oracle(KAT[0][0], KAT[0][1]) == KAT[0][2]:
        check("oracle: uzun parola",
              ospass.sha512crypt(b"uzun bir parola 12345", "QWERTYui")
              == oracle(b"uzun bir parola 12345", "QWERTYui"))

    print("\nUctan uca (ext4 imaji):")
    p = os.path.join(scratch(), "ospass_t.img")
    d = DiskImage.create(p, 64 * MIB, overwrite=True)
    view = PartitionView(d, 2048, d.sector_count - 2048)
    format_ext(view, "ext4", label="OSPASS")
    view = PartitionView(d, 2048, d.sector_count - 2048)
    fs = open_filesystem(view, detect(view))
    assert fs.writable, fs.write_reason

    passwd = ("root:x:0:0:root:/root:/bin/bash\n"
              "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
              "mikail:x:1000:1000:Mikail:/home/mikail:/bin/bash\n")
    shadow = ("root:$6$abc$oldhashplaceholder:19000:0:99999:7:::\n"
              "daemon:*:19000:0:99999:7:::\n"
              "mikail:!$6$xyz$lockedhash:19000:0:99999:7:::\n")
    fs.mkdir("/etc")
    fs.write_file("/etc/passwd", passwd.encode())
    fs.write_file("/etc/shadow", shadow.encode())
    fs.flush()

    users = {u.name: u for u in ospass.linux_users(fs)}
    check("listele: root/daemon/mikail", {"root", "daemon", "mikail"} <= set(users))
    check("daemon kilitli (*)", users["daemon"].disabled)
    check("mikail kilitli (!)", users["mikail"].disabled)
    check("root parola var", users["root"].note == "parola var"
          and not users["root"].disabled)

    # yeni parola belirle
    ospass.linux_set_password(fs, "mikail", new_password="YeniParola!42")
    line = _shadow_line(fs, "mikail")
    field = line.split(":")[1]
    check("yeni parola $6$ ile yazildi", field.startswith("$6$"))
    # Alanin dogru hash'lendigini KENDI sha512crypt'imizle dogrula (platformdan
    # bagimsiz; macOS stdlib crypt'i $6$ desteklemez).
    salt = field.split("$")[2]
    check("yeni parola dogru hash'leniyor",
          ospass.sha512crypt(b"YeniParola!42", salt) == field)

    # bosalt
    ospass.linux_set_password(fs, "root", blank=True)
    check("root parolasi bosaltildi", _shadow_line(fs, "root").split(":")[1] == "")

    # kilitle / ac
    ospass.linux_set_password(fs, "mikail", lock=True)
    check("mikail kilitlendi", _shadow_line(fs, "mikail").split(":")[1].startswith("!"))
    ospass.linux_set_password(fs, "mikail", unlock=True)
    f2 = _shadow_line(fs, "mikail").split(":")[1]
    check("mikail kilidi acildi", not f2.startswith("!") and f2.startswith("$6$"))

    # olmayan kullanici
    try:
        ospass.linux_set_password(fs, "yok", blank=True)
        check("olmayan kullanici reddi", False)
    except ospass.OsPassError:
        check("olmayan kullanici reddi", True)

    fs.flush()
    d.close()
    _fsck(p)

    print("\nWindows SAM (sentetik regf kutugu):")
    sam = _build_sam(username="Administrator", rid=0x1F4, nt_present=True,
                     disabled=True)
    users = {u.name: u for u in ospass.windows_users(sam)}
    check("SAM listele: Administrator", "Administrator" in users)
    check("RID 500 (0x1F4)", users.get("Administrator", _N()).uid == 0x1F4)
    check("parola var tespit", users.get("Administrator", _N()).note == "parola var"
          or users.get("Administrator", _N()).disabled)
    check("hesap kilitli (ACB)", users.get("Administrator", _N()).disabled)

    sam2 = ospass.windows_blank_password(sam, 0x1F4)
    check("boyut degismedi (yerinde)", len(sam2) == len(sam))
    check("parola bosaltildi (NT uzunlugu 0)", _nt_len(sam2, 0x1F4) == 0)

    sam3 = ospass.windows_enable_account(sam2, 0x1F4)
    u3 = {u.name: u for u in ospass.windows_users(sam3)}["Administrator"]
    check("hesap etkinlestirildi (ACB_DISABLED temiz)", not u3.disabled)
    check("etkin + parolasiz -> note", u3.note == "parola yok")

    try:
        ospass.windows_blank_password(sam, 0x270F)      # olmayan RID
        check("olmayan RID reddi", False)
    except ospass.OsPassError:
        check("olmayan RID reddi", True)

    print()
    if _fail:
        print("SONUC: %d HATA" % _fail)
        return 1
    print("SONUC: TAMAM")
    return 0


class _N:
    name = ""; uid = -1; note = ""; disabled = False


def _nt_len(sam_bytes, rid):
    from diskultimate.core import winreg
    h = winreg.Hive(sam_bytes)
    key = h.subkey(h.find("SAM\\Domains\\Account\\Users"), "%08X" % rid)
    V = h.value(key, "V")[0]
    return struct.unpack_from("<I", V, 0xB0)[0]


def _build_sam(username="Administrator", rid=0x1F4, nt_present=True,
               disabled=True):
    """Yapisal olarak gecerli, kucuk bir SAM regf kutugu kurar
    (SAM\\Domains\\Account\\Users\\<RID> + V/F degerleri)."""
    import struct as _s
    area = bytearray()

    def alloc(payload):
        size = (4 + len(payload) + 7) & ~7
        rel = 32 + len(area)
        cell = bytearray(size)
        _s.pack_into("<i", cell, 0, -size)
        cell[4:4 + len(payload)] = payload
        area.extend(cell)
        return rel

    def nk(name, flags, subkey_off, nsub, values_off, nval):
        nm = name.encode("latin-1")
        p = bytearray(76 + len(nm))
        p[0:2] = b"nk"
        _s.pack_into("<H", p, 2, flags | 0x0020)       # ASCII ad
        _s.pack_into("<I", p, 20, nsub)
        _s.pack_into("<i", p, 28, subkey_off)
        _s.pack_into("<i", p, 32, -1)
        _s.pack_into("<I", p, 36, nval)
        _s.pack_into("<i", p, 40, values_off)
        _s.pack_into("<i", p, 44, -1)
        _s.pack_into("<i", p, 48, -1)
        _s.pack_into("<H", p, 72, len(nm))
        p[76:76 + len(nm)] = nm
        return alloc(bytes(p))

    def li(children):
        p = bytearray(4 + 4 * len(children))
        p[0:2] = b"li"
        _s.pack_into("<H", p, 2, len(children))
        for i, c in enumerate(children):
            _s.pack_into("<i", p, 4 + i * 4, c)
        return alloc(bytes(p))

    def vk(name, data_off, data_size):
        nm = name.encode("latin-1")
        p = bytearray(20 + len(nm))
        p[0:2] = b"vk"
        _s.pack_into("<H", p, 2, len(nm))
        _s.pack_into("<I", p, 4, data_size)
        _s.pack_into("<i", p, 8, data_off)
        _s.pack_into("<I", p, 12, 3)
        _s.pack_into("<H", p, 16, 0x0001)
        p[20:20 + len(nm)] = nm
        return alloc(bytes(p))

    uname = username.encode("utf-16-le")
    V = bytearray(0xCC)
    _s.pack_into("<I", V, 0x0C, 0)
    _s.pack_into("<I", V, 0x10, len(uname))
    V += uname
    nt_rel = len(V) - 0xCC
    _s.pack_into("<I", V, 0x9C, nt_rel)
    _s.pack_into("<I", V, 0xA0, 0)
    _s.pack_into("<I", V, 0xA8, nt_rel)
    _s.pack_into("<I", V, 0xB0, 0x14 if nt_present else 0)
    V += b"\x01\x00\x00\x00" + b"\x11" * 16
    F = bytearray(0x50)
    if disabled:
        _s.pack_into("<H", F, 0x38, 0x0001)

    v_data = alloc(bytes(V))
    f_data = alloc(bytes(F))
    v_vk = vk("V", v_data, len(V))
    f_vk = vk("F", f_data, len(F))
    vlist = alloc(_s.pack("<ii", v_vk, f_vk))
    rid_nk = nk("%08X" % rid, 0, -1, 0, vlist, 2)
    root = nk("ROOT", 0x0004, li([
        nk("SAM", 0, li([
            nk("Domains", 0, li([
                nk("Account", 0, li([
                    nk("Users", 0, li([rid_nk]), 1, -1, 0)]), 1, -1, 0)]),
               1, -1, 0)]), 1, -1, 0)]), 1, -1, 0)

    bin_size = (32 + len(area) + 4095) & ~4095
    hbin = bytearray(bin_size)
    hbin[0:4] = b"hbin"
    _s.pack_into("<I", hbin, 8, bin_size)
    hbin[32:32 + len(area)] = area
    used = 32 + len(area)
    if used < bin_size:
        _s.pack_into("<i", hbin, used, bin_size - used)   # bos hucre (pozitif)
    base = bytearray(4096)
    base[0:4] = b"regf"
    _s.pack_into("<I", base, 4, 1)
    _s.pack_into("<I", base, 8, 1)
    _s.pack_into("<I", base, 20, 1)
    _s.pack_into("<I", base, 24, 5)
    _s.pack_into("<I", base, 36, root)
    _s.pack_into("<I", base, 40, bin_size)
    return bytes(base + hbin)


def _oracle():
    """sha512crypt referansi: stdlib crypt, yoksa `openssl passwd -6`."""
    try:
        import crypt
        return lambda pw, salt: crypt.crypt(pw.decode("latin-1"), "$6$" + salt)
    except ImportError:
        pass
    if not shutil.which("openssl"):
        return None

    def _ossl(pw, salt):
        r = subprocess.run(["openssl", "passwd", "-6", "-salt", salt,
                            pw.decode("latin-1")], capture_output=True, text=True)
        return r.stdout.strip()
    return _ossl


def _shadow_line(fs, user):
    for line in fs.read("/etc/shadow").decode().splitlines():
        if line.split(":")[0] == user:
            return line
    raise AssertionError("satir yok: " + user)


def _fsck(image_path):
    tool = shutil.which("e2fsck")
    if not tool:
        print("  (e2fsck yok, dogrulama atlandi)")
        return
    part = image_path + ".part"
    with open(image_path, "rb") as src:
        src.seek(2048 * 512)
        with open(part, "wb") as dst:
            shutil.copyfileobj(src, dst)
    r = subprocess.run([tool, "-fn", part], capture_output=True, text=True)
    os.unlink(part)
    check("e2fsck -fn temiz", r.returncode == 0)
    if r.returncode != 0:
        print(r.stdout[-500:])


if __name__ == "__main__":
    raise SystemExit(main())
