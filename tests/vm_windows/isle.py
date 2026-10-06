# -*- coding: utf-8 -*-
"""Ana makine: Windows'un urettigi win.vhd uzerinde DiskUltimate.

1) okuma == Windows manifesti   2) tam + kullanilan alan yedegi -> copla dolu
hedefe geri yukle -> okuma; geri yukleneni Windows'a VHD olarak ver
3) yazma (klasor, dosya, silme, sabit bag, ad degistirme)   4) boyutlandirma
Sonuc: work.vhd + geri_kullanilan.vhd + manifest_son.json (Windows dogrular).
"""
import hashlib
import json
import os
import shutil
import sys
import traceback
import unicodedata

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "..", "..", "..", "src"))

from diskultimate.core import clone  # noqa: E402
from diskultimate.core.image import DiskImage  # noqa: E402
from diskultimate.core.session import DiskSession  # noqa: E402
from diskultimate.core.vdisk import build_vhd_footer  # noqa: E402

MIB = 1024 * 1024
W = lambda n: os.path.join(ROOT, n)          # noqa: E731
rapor = []


def say(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    rapor.append(line)


def nfc(s):
    return unicodedata.normalize("NFC", s)


def by_label(s):
    out = {}
    for p in s.partitions:
        out[p.fs_label] = p
    return out


def walk(acc, path="/"):
    res = {}
    for n in acc.listdir(path):
        if n.name in (".", ".."):
            continue
        if n.is_dir:
            res.update(walk(acc, n.path))
        else:
            res[nfc(n.path)] = n.size
    return res


def verify(acc, files, label, what):
    listed = walk(acc)
    want = {nfc(k): v for k, v in files.items()}
    missing = sorted(set(want) - set(listed))
    extra = sorted(e for e in set(listed) - set(want)
                   if not e.startswith(("/System Volume Information", "/$RECYCLE.BIN")))
    bad = []
    for path, (size, digest) in sorted(want.items()):
        if path not in listed:
            continue
        try:
            h = hashlib.sha1()
            n = 0
            for piece in acc.iter_read(path):
                h.update(piece)
                n += len(piece)
            if n != size or h.hexdigest() != digest:
                bad.append(f"{path} (boy {n}/{size})")
        except Exception as exc:              # noqa: BLE001
            bad.append(f"{path}: {type(exc).__name__}: {str(exc)[:80]}")
    ok = not missing and not bad
    say(f"  [{what}] {label}: {len(want)} dosya, eksik {len(missing)}, fazla {len(extra)}, "
        f"icerik hatali {len(bad)} -> {'TAMAM' if ok else 'HATA'}")
    for x in missing[:5]:
        say("     eksik:", x)
    for x in extra[:5]:
        say("     fazla:", x)
    for x in bad[:8]:
        say("     hatali:", x)
    return ok


def raw_to_vhd(raw, vhd):
    size = os.path.getsize(raw)
    os.replace(raw, vhd)
    with open(vhd, "ab") as fh:
        fh.write(build_vhd_footer(size))


def main():
    man = json.load(open(W("manifest_win.json"), encoding="utf-8"))
    shutil.copyfile(W("win.vhd"), W("work.vhd"))
    s = DiskSession.open(W("work.vhd"), readonly=True)
    parts = by_label(s)
    say("== 1) Tespit ve okuma (Windows'un yazdigi)")
    beklenen = {"N64K": "NTFS", "N4K": "NTFS", "N2M": "NTFS", "EXF": "exFAT", "F32": "FAT32"}
    for label, fs in beklenen.items():
        p = parts.get(label)
        if p is None:
            say(f"  {label}: BOLUM/ETIKET BULUNAMADI; gorulen etiketler {list(parts)}")
            continue
        info = s.detect_fs(p)
        say(f"  {label}: tur {p.fs_type} (beklenen {fs}) kume {info.cluster_size} "
            f"{'HATA' if p.fs_type != fs else ''}")
        acc = s.filesystem(p.index)
        verify(acc, man[label]["files"], label, "okuma")
        for path, kind in man[label]["special"].items():
            say(f"     ozel {kind}: {path}")
    s.close_filesystems()

    say("== 2) Yedek: tam + kullanilan alan -> copla dolu hedef -> okuma")
    size = s.image.size
    for used in (False, True):
        kip = "kullanilan" if used else "tam"
        dub = W(f"y_{kip}.dub")
        info = s.backup_disk(dub, used_only=used)
        raw = W(f"geri_{kip}.img")
        with open(raw, "wb") as fh:
            chunk = os.urandom(4 * MIB)
            left = size
            while left > 0:
                n = min(left, len(chunk))
                fh.write(chunk[:n])
                left -= n
        h = DiskImage(raw)
        clone.restore(dub, h)
        h.close()
        say(f"  {kip}: .dub {os.path.getsize(dub) // MIB} MiB, kapsam "
            f"{'kullanilan' if info.used_only else 'tum'}")
        g = DiskSession.open(raw, readonly=True)
        gp = by_label(g)
        for label in beklenen:
            if label in gp:
                verify(g.filesystem(gp[label].index), man[label]["files"], label, f"geri-{kip}")
        g.close()
        os.unlink(dub)
        if used:
            raw_to_vhd(raw, W("geri_kullanilan.vhd"))
        else:
            os.unlink(raw)
    s.close()

    say("== 3) Yazma (work.vhd)")
    s = DiskSession.open(W("work.vhd"))
    parts = by_label(s)
    son = {}
    for label in beklenen:
        files = dict(man[label]["files"])
        acc = s.filesystem(parts[label].index)
        if not acc.writable:
            say(f"  {label}: yazilamaz: {acc.write_reason}")
            son[label] = files
            continue
        ops = []
        try:
            acc.mkdir("/yeni_klasör")
            for i, sz in enumerate((0, 1, 4096, 300000, 3 * MIB + 17)):
                data = hashlib.sha256(f"{label}{i}".encode()).digest() * (sz // 32 + 1)
                data = data[:sz]
                pth = f"/yeni_klasör/yeni_{i}.bin"
                acc.write_file(pth, data)
                files[pth] = [len(data), hashlib.sha1(data).hexdigest()]
            ops.append("klasor+5 dosya")
            kal = sorted(p for p in files if p.startswith("/kalabalik/"))[:5]
            for pth in kal:
                acc.remove(pth)
                files.pop(pth)
            ops.append(f"{len(kal)} silme")
            data = b"kalabalik dizine yeni giris " * 50
            acc.write_file("/kalabalik/yeni_giris.dat", data)
            files["/kalabalik/yeni_giris.dat"] = [len(data), hashlib.sha1(data).hexdigest()]
            ad = sorted(p for p in files if p.startswith("/adlar/"))[0]
            acc.rename(ad, "yeniden_adlandırıldı.bin")
            files["/adlar/yeniden_adlandırıldı.bin"] = files.pop(ad)
            ops.append("ad degistirme")
            if "/ozel/bag_b.bin" in files:
                acc.remove("/ozel/bag_b.bin")
                files.pop("/ozel/bag_b.bin")
                ops.append("sabit bag sil (bag_a, ozel2/bag_c kalmali)")
            for pth in ("/ozel/akisli.bin", "/ozel/sikistirilmis.txt"):
                if pth in files:
                    try:
                        acc.remove(pth)
                        files.pop(pth)
                        ops.append(f"sil {pth}")
                    except Exception as exc:          # noqa: BLE001
                        ops.append(f"sil {pth} REDDEDILDI: {str(exc)[:70]}")
            acc.flush()
        except Exception as exc:                      # noqa: BLE001
            ops.append(f"HATA {type(exc).__name__}: {exc}")
            say(traceback.format_exc()[-600:])
        say(f"  {label}: " + "; ".join(ops))
        son[label] = files
    s.close_filesystems()
    s.close()

    say("== 4) Boyutlandirma (work.vhd)")
    s = DiskSession.open(W("work.vhd"))
    parts = by_label(s)
    for label, how in (("N64K", "kucult"), ("F32", "buyut")):
        p = parts[label]
        try:
            info = s.resize_info(p.index)
            if how == "kucult":
                count = max(info.min_sectors + 16 * 2048, p.sector_count * 75 // 100)
                count = count // 2048 * 2048
            else:
                end = s.image.size // 512 - 34 - 2048
                count = (end - p.start_lba) // 2048 * 2048
            s.resize_partition(p.index, p.start_lba, count, confirm=True)
            say(f"  {label} {how}: {p.sector_count // 2048} -> {count // 2048} MiB")
        except Exception as exc:                      # noqa: BLE001
            say(f"  {label} {how}: REDDEDILDI/HATA {type(exc).__name__}: {exc}")
        s.reload()
        parts = by_label(s)
    s.close()
    s = DiskSession.open(W("work.vhd"), readonly=True)
    parts = by_label(s)
    for label in beklenen:
        verify(s.filesystem(parts[label].index), son[label], label, "son")
    s.close()
    json.dump({k: {"files": v} for k, v in son.items()},
              open(W("manifest_son.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(W("isle.txt"), "w", encoding="utf-8").write("\n".join(rapor) + "\n")


if __name__ == "__main__":
    main()
