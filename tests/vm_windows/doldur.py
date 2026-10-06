# -*- coding: utf-8 -*-
"""Windows misafirinde: Windows'un bicimlendirdigi birimleri Windows'un kendi
API'leriyle doldurur ve manifest yazar. Depodan bagimsizdir (embeddable
Python, depo modulu import edilmez).

    python doldur.py MANIFEST.json ETIKET=SURUCU: ...
"""
import hashlib
import json
import os
import random
import subprocess
import sys

MIB = 1024 * 1024


def data_for(key, size):
    rnd = random.Random(key)
    block = bytes(rnd.getrandbits(8) for _ in range(4096))
    reps = size // 4096 + 1
    return (block * reps)[:size]


def sha1(b):
    return hashlib.sha1(b).hexdigest()


def write(root, rel, data, man):
    """Yer kalmazsa (2 MiB kumede 300 kucuk dosya) dosya atlanir, yarim kalan
    silinir; manifestte yalnizca gercekten yazilanlar durur."""
    full = root + rel.replace("/", "\\")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    try:
        with open(full, "wb") as fh:
            fh.write(data)
    except OSError:
        try:
            os.remove(full)
        except OSError:
            pass
        return False
    man[rel] = [len(data), sha1(data)]
    return True


def fill(label, root, budget):
    man, special, notes = {}, {}, []
    seed = sum(ord(c) for c in label) * 7919
    rnd = random.Random(seed)
    used = 0
    write(root, "/KOK.TXT", b"kok dosyasi\r\n" * 10, man)
    for i, size in enumerate([0, 1, 511, 512, 513, 4095, 4096, 4097, 65535, 65536,
                              65537, MIB - 1, MIB, MIB + 1]):
        write(root, f"/sinir/s{i:02d}_{size}.bin", data_for(seed + i, size), man)
    names = ["kısa ad.txt", "ÇĞİÖŞÜ çğıöşü.dat", "Ελληνικά αρχεία.bin", "Русский.txt",
             "日本語のファイル.txt", "😀 emoji dosyası.txt", "UPPER.TXT", "MiXeD.Txt",
             "x" * 150 + ".bin", "nokta.cok.nokta.tar.gz"]
    for i, n in enumerate(names):
        write(root, f"/adlar/{n}", data_for(seed + 100 + i, rnd.randint(0, 120000)), man)
    for i in range(300):
        write(root, f"/kalabalik/d{i:04d}.dat", data_for(seed + 1000 + i, rnd.randint(0, 5000)), man)
    # parcalanma
    chunk = max(4096, min(2 * MIB, budget // 60))
    for i in range(24):
        write(root, f"/parca/p{i:02d}.bin", data_for(seed + 2000 + i, chunk + i * 333), man)
    for i in range(1, 24, 2):
        if man.pop(f"/parca/p{i:02d}.bin", None) is not None:
            os.remove(root + f"\\parca\\p{i:02d}.bin")
    write(root, "/parca/buyuk_parcali.bin", data_for(seed + 3000, chunk * 11 + 777), man)
    used = sum(v[0] for v in man.values())
    big = max(0, min(48 * MIB, (budget - used) // 2))
    if big > MIB:
        write(root, "/buyuk/b1.bin", data_for(seed + 4000, big), man)
    return man, special, notes


def ntfs_extras(root, man, special, notes):
    base = root + "\\ozel"
    os.makedirs(base, exist_ok=True)
    # sabit bag: ayni klasor + baska klasor
    write(root, "/ozel/bag_a.bin", data_for(9001, 70000), man)
    os.link(base + "\\bag_a.bin", base + "\\bag_b.bin")
    man["/ozel/bag_b.bin"] = man["/ozel/bag_a.bin"]
    os.makedirs(root + "\\ozel2", exist_ok=True)
    os.link(base + "\\bag_a.bin", root + "\\ozel2\\bag_c.bin")
    man["/ozel2/bag_c.bin"] = man["/ozel/bag_a.bin"]
    # adli akis (ADS)
    write(root, "/ozel/akisli.bin", data_for(9002, 30000), man)
    with open(base + "\\akisli.bin:gizli", "wb") as fh:
        fh.write(b"gizli akis " * 3000)
    special["/ozel/akisli.bin"] = "ads"
    # seyrek
    p = base + "\\seyrek.bin"
    open(p, "wb").close()
    subprocess.run(["fsutil", "sparse", "setflag", p], capture_output=True)
    with open(p, "r+b") as fh:
        fh.seek(9 * MIB + 123)
        fh.write(b"son-blok" * 512)
        fh.seek(3 * MIB)
        fh.write(b"orta" * 1000)
    with open(p, "rb") as fh:
        b = fh.read()
    man["/ozel/seyrek.bin"] = [len(b), sha1(b)]
    r = subprocess.run(["fsutil", "sparse", "queryflag", p], capture_output=True, text=True)
    notes.append("seyrek: " + r.stdout.strip()[:80])
    # sikistirilmis
    write(root, "/ozel/sikistirilmis.txt", b"sikisan metin satiri\r\n" * 20000, man)
    r = subprocess.run(["compact", "/c", base + "\\sikistirilmis.txt"], capture_output=True, text=True)
    if r.returncode == 0:      # NTFS sikistirmasi en cok 4 KiB kumede
        special["/ozel/sikistirilmis.txt"] = "compressed"
    notes.append("compact rc=%d" % r.returncode)
    # cok parcali buyuk dosya (attribute list'e zorlamak icin): parca parca yaz,
    # arada baska dosyalar yaz
    p = base + "\\cok_parcali.bin"
    allb = b""
    with open(p, "wb") as fh:
        pass
    made = 0
    try:
        for i in range(400):
            piece = data_for(9100 + i, 64 * 1024)
            with open(p, "ab") as fh:
                fh.write(piece)
            allb += piece
            with open(root + f"\\ozel\\ara_{i:03d}.tmp", "wb") as fh:
                fh.write(b"a" * 70000)
            made = i + 1
    except OSError:
        notes.append("cok_parcali: yer bitti, %d parca" % made)
        with open(p, "rb") as fh:
            allb = fh.read()
    for i in range(made + 1):
        try:
            os.remove(root + f"\\ozel\\ara_{i:03d}.tmp")
        except OSError:
            pass
    man["/ozel/cok_parcali.bin"] = [len(allb), sha1(allb)]


def main():
    out = sys.argv[1]
    result = {}
    for arg in sys.argv[2:]:
        label, drive = arg.split("=")
        root = drive.rstrip("\\") + "\\"
        free = __import__("shutil").disk_usage(root).free
        man, special, notes = fill(label, root.rstrip("\\"), int(free * 0.4))
        if label.startswith("N"):
            try:
                ntfs_extras(root.rstrip("\\"), man, special, notes)
            except Exception as exc:          # noqa: BLE001
                notes.append("ntfs ekstra HATA: %r" % (exc,))
        result[label] = {"files": man, "special": special, "notes": notes}
        print(label, drive, len(man), "dosya", notes)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
