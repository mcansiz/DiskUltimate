"""Uzun test raporlarini birlestirir: Markdown ozet ve issue metni.

    python -m tests.long.report <klasor> [--md ozet.md] [--issue issue.md]

Klasor altindaki butun `*.json` raporlari (alt klasorler dahil; CI'da her is
kendi artifact klasorune yazar) okunur. Hata varsa cikis kodu 1.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from typing import List


def load(folder: str) -> List[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "**", "*.json"), recursive=True)):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if "adimlar" in data:
                out.append(data)
        except (OSError, ValueError):
            continue
    return out


def _platform_short(r: dict) -> str:
    p = r.get("platform", "")
    return p.split(" py")[0]


def summary_md(results: List[dict], run_url: str = "") -> str:
    ok = [r for r in results if r["tamam"]]
    bad = [r for r in results if not r["tamam"]]
    lines = ["## Uzun testler", ""]
    if results:
        profiles = sorted({r["profil"] for r in results})
        seeds = sorted({str(r["tohum"]) for r in results})
        lines.append(f"Profil: **{', '.join(profiles)}** · tohum: {', '.join(seeds)} · "
                     f"**{len(ok)}/{len(results)}** senaryo tamam"
                     + (f" · [kosu]({run_url})" if run_url else ""))
        lines.append("")
    lines += ["| Senaryo | Platform | Sonuc | Sure | Veri | En yuksek bellek | Atlanan | Hatali adim |",
              "|---|---|---|---:|---:|---:|---:|---|"]
    for r in sorted(results, key=lambda x: (x["tamam"], x["senaryo"], x.get("platform", ""))):
        steps = r["adimlar"]
        skipped = sum(1 for s in steps if s["durum"] == "atlandi")
        failed = [s for s in steps if s["durum"] == "hata"]
        mem = max((s.get("bellek_mb", 0) for s in steps), default=0)
        lines.append(
            f"| {r['senaryo']} | {_platform_short(r)} | {'tamam' if r['tamam'] else '**HATA**'} "
            f"| {r['sure_sn']:.0f} sn | {r['veri_mb']:.0f} MB | {mem:.0f} MB | {skipped} "
            f"| {failed[0]['ad'] if failed else ''} |")
    if bad:
        lines += ["", "### Hatalar", ""]
        for r in bad:
            for s in r["adimlar"]:
                if s["durum"] != "hata":
                    continue
                lines.append(f"**{r['senaryo']}** ({_platform_short(r)}) — adim `{s['ad']}`")
                lines.append("")
                lines.append("```")
                lines.append(s.get("hata", "")[:1500])
                for key in ("fsck", "cekirdek"):
                    if key in s and s[key].get("durum") == "fail":
                        lines.append(f"{key}: {s[key].get('ayrinti', '')[:600]}")
                lines.append("```")
                lines.append(f"Tekrar: `{r['tekrar']}`")
                lines.append("")
    dropped = [r for r in results if r.get("dusen_dosyalar_mb")]
    if dropped:
        lines += ["", "### Disk butcesine sigmayan buyuk dosyalar", ""]
        for r in dropped:
            lines.append(f"- {r['senaryo']} ({_platform_short(r)}): "
                         f"{', '.join(str(x) + ' MiB' for x in r['dusen_dosyalar_mb'])} "
                         f"yazilmadi (butce {r.get('butce_gb')} GiB) — bu boyut bu "
                         f"platformda SINANMADI")
    reasons = Counter(s.get("neden", "") for r in results for s in r["adimlar"]
                      if s["durum"] == "atlandi")
    checks = Counter()
    for r in results:
        for s in r["adimlar"]:
            for key in ("fsck", "cekirdek"):
                if key in s:
                    checks[(key, s[key]["durum"])] += 1
    if checks:
        lines += ["", "### Dogrulama katmanlari", ""]
        for (key, status), n in sorted(checks.items()):
            lines.append(f"- {key}: {status} × {n}")
    if reasons:
        lines += ["", "### Atlanan adimlar (nedenleriyle)", ""]
        for reason, n in reasons.most_common():
            lines.append(f"- {n} × {reason}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.long.report")
    ap.add_argument("klasor")
    ap.add_argument("--md", default="")
    ap.add_argument("--issue", default="", help="hata varsa issue govdesi")
    ap.add_argument("--kosu", default="", help="kosu baglantisi")
    a = ap.parse_args(argv)
    results = load(a.klasor)
    text = summary_md(results, a.kosu)
    if a.md:
        with open(a.md, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text)
    failed = any(not r["tamam"] for r in results)
    if a.issue and failed:
        with open(a.issue, "w", encoding="utf-8") as fh:
            fh.write(text + "\n_Bu issue uzun testler is akisi tarafindan otomatik "
                     "acildi (ADR 0080)._\n")
    if not results:
        print("rapor bulunamadi", file=sys.stderr)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
