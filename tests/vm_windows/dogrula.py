# -*- coding: utf-8 -*-
"""Windows misafirinde: manifest ile birimdeki dosyalari karsilastirir."""
import hashlib, json, os, sys, unicodedata
man = json.load(open(sys.argv[1], encoding="utf-8"))
out = []
for arg in sys.argv[2:]:
    label, root = arg.split("=", 1)
    want = {unicodedata.normalize("NFC", k): v for k, v in man[label]["files"].items()}
    seen, bad, missing = {}, [], []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in ("System Volume Information", "$RECYCLE.BIN")]
        for f in files:
            rel = "/" + os.path.relpath(os.path.join(d, f), root).replace("\\", "/")
            seen[unicodedata.normalize("NFC", rel)] = os.path.join(d, f)
    for p, (size, digest) in want.items():
        full = seen.get(p)
        if full is None:
            missing.append(p); continue
        h = hashlib.sha1()
        with open(full, "rb") as fh:
            for b in iter(lambda: fh.read(1 << 22), b""):
                h.update(b)
        if h.hexdigest() != digest:
            bad.append(p)
    extra = sorted(set(seen) - set(want))
    out.append(f"{label}: {len(want)} dosya; eksik {len(missing)} fazla {len(extra)} hatali {len(bad)}"
               + (" -> TAMAM" if not missing and not bad and not extra else " -> HATA"))
    out += ["  eksik " + x for x in missing[:5]] + ["  fazla " + x for x in extra[:5]] + ["  hatali " + x for x in bad[:5]]
print("\n".join(out))
