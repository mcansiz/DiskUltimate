"""Capraz platform uyumluluk denetimi (statik).

Windows ve macOS uzerinde dogrudan test calistirilamadigi icin, kodda platforma
bagli kalan noktalari tarar. `python3 -m tests.platform_check`
"""
from __future__ import annotations

import ast
import os
import re
import sys

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KAYNAK = os.path.join(KOK, "src")

# Platform katmani: isletim sistemi farkliliklarini BARINDIRMASI beklenen dosyalar.
# Muafiyet yalnizca ilgili kurallar icin verilir; tempfile / subprocess / endian
# kurallari bu dosyalarda da gecerlidir.
PLATFORM_KATMANI = {"platform.py", "physical.py"}

# (desen, aciklama, izinli dosyalar)
KURALLAR = [
    (r"/tmp/|/var/tmp", "Sabit POSIX gecici dizin yolu", set()),
    (r"/usr/|/etc/|/dev/sd|/dev/nvme",
     "Sabit POSIX sistem yolu", PLATFORM_KATMANI),
    (r"\bos\.fork\b|\bos\.getuid\b|\bos\.geteuid\b|\bpwd\b|\bgrp\b",
     "POSIX'e ozgu islev", PLATFORM_KATMANI),
    (r"st_blocks", "Windows'ta bulunmayan stat alani", {"platform.py"}),
    (r"subprocess\.(run|Popen|call)\(", "Dogrudan surec cagrisi "
     "(platform.run_tool kullanilmali)", {"platform.py"}),
    (r"shutil\.which\(", "Dogrudan arac arama (platform.find_tool kullanilmali)",
     {"platform.py"}),
    (r"\btempfile\b", "Sistem gecici dizini (paths.scratch kullanilmali)", set()),
    (r"[\"']([A-Za-z]:\\\\|\\\\\\\\)", "Sabit Windows yolu", PLATFORM_KATMANI),
    (r"\.decode\(\)|\.encode\(\)",
     "Kodlamasiz encode/decode (platforma gore degisir)", set()),
    (r"open\([^)]*[\"']r[\"']\s*\)", "Metin modunda kodlamasiz acma", set()),
]

# Mutlaka acikca belirtilmesi gereken: struct bicimlerinde endian isareti
STRUCT_DESENI = re.compile(r"struct\.(pack|unpack)(_into|_from)?\(\s*[\"']([^\"'])")

# --------------------------------------------------------------------------
# Tanimlayici dili denetimi
#
# CLAUDE.md kurali: "Turkce arayuz metni, Ingilizce kod/degisken adi."
# Arayuzde gorunen METIN Turkcedir; fonksiyon ve parametre ADLARI Ingilizce
# olmalidir. Bu denetim kuralin zamanla gevsemesini engeller — bir olcumde
# kuralin en cok **en yeni** dosyalarda ihlal edildigi gorulmustu.
#
# `ISIM_MUAFIYETI` henuz cevrilmemis dosyalari tutar. Yeni ihlal eklenemez;
# listedeki dosyalar cevrildikce buradan **silinir** ve liste bosalir.
# --------------------------------------------------------------------------
TR_SOZCUKLER = {
    "ac", "acilis", "ad", "adi", "adim", "alan", "alt", "anahtar", "aralik",
    "ayirici", "ayrilmis", "bagli", "baslangic", "baslik", "basari", "bayt",
    "bayti", "bicim", "bildir", "bilgi", "birim", "bitis", "blok", "bolum",
    "bolumler", "bos", "boyut", "boyutu", "ciz", "cikis", "deger", "dizin",
    "dolu", "dosya", "dosyalar", "doldur", "durum", "eski", "gecici", "giris",
    "gorev", "guncelle", "hata", "hedef", "hizala", "icerik", "ilerle", "ilk",
    "isaretle", "isaretsiz", "kaydet", "kayit", "kayitlar", "kod", "konum",
    "kok", "kume", "kumeler", "kumesi", "mesaj", "neden", "nedeni", "okunur",
    "olustur", "onay", "onayla", "oturum", "oku", "ozellik", "oznitelik",
    "ozet", "parca", "sayi", "sayisi", "sektor", "sektorler", "secili",
    "serit", "sil", "sistem", "son", "sonuc", "surukle", "tablo", "tamam",
    "tur", "turu", "tutamac", "tutamak", "toplam", "uygula", "uyari", "ust",
    "uzunluk", "varsayilan", "yaz", "yeni", "yol", "yukle", "yuzde",
    # ikinci tur: ilk taramada gozden kacan arayuz sozcukleri
    "ayarla", "bosluk", "bul", "cevir", "cikar", "ekle", "gunluk",
    "goruntu", "goruntusu", "ikon", "kapasite", "kapat", "kesim",
    "pencere", "secenegi", "secim", "seritten", "tema", "temasi",
    "temasini", "yenile",
}

# Henuz cevrilmemis dosyalar icin gecici muafiyet.
#
# **Liste 2026-09-15'te bosaltildi** — tum kaynak agaci cevrildi. Yeni bir
# dosya gecici olarak eklenirse anahtar **goreli yoldur**, dosya adi degil:
# `core/resize.py` ile `ui/dialogs/resize.py` ayni adi tasir.
ISIM_MUAFIYETI = set()


def modul_anahtari(yol: str) -> str:
    """Muafiyet anahtari: `src/` altindaki goreli yol, egik cizgi ile."""
    rel = os.path.relpath(yol, KAYNAK)
    if rel.startswith(".."):
        rel = os.path.relpath(yol, KOK)
    return rel.replace(os.sep, "/")


def turkce_ad_mi(isim: str) -> bool:
    """Tanimlayici Turkce bir sozcuk parcasi tasiyor mu?"""
    if isim.startswith("__") and isim.endswith("__"):
        return False
    return any(parca.lower() in TR_SOZCUKLER
               for parca in isim.strip("_").split("_") if parca)


def _atanan_adlar(dugum: ast.AST) -> list:
    """Bir dugumun bagladigi degisken adlari (atama, dongu, with, except)."""
    hedefler = []
    if isinstance(dugum, ast.Assign):
        hedefler = dugum.targets
    elif isinstance(dugum, (ast.AugAssign, ast.AnnAssign, ast.For,
                            ast.AsyncFor, ast.comprehension)):
        hedefler = [dugum.target]
    elif isinstance(dugum, (ast.With, ast.AsyncWith)):
        hedefler = [i.optional_vars for i in dugum.items
                    if i.optional_vars is not None]
    out = []
    for h in hedefler:
        for x in ast.walk(h):
            if isinstance(x, ast.Name):
                out.append(x.id)
    if isinstance(dugum, ast.ExceptHandler) and dugum.name:
        out.append(dugum.name)
    return out


def isim_bulgulari(yol: str, agac: ast.AST) -> list:
    """Turkce adli fonksiyon, parametre ve **yerel degiskenleri** dondurur.

    Yerel degiskenler de denetlenir: kural "Ingilizce kod adi" der, yalnizca
    "Ingilizce fonksiyon adi" degil. Fonksiyon govdesindeki adlar disarida
    birakilsaydi kural yeniden gevserdi.
    """
    out = []
    for dugum in ast.walk(agac):
        if not isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        kotu = []
        if turkce_ad_mi(dugum.name):
            kotu.append(dugum.name)
        args = dugum.args
        for a in (args.posonlyargs + args.args + args.kwonlyargs
                  + [args.vararg, args.kwarg]):
            if a is not None and turkce_ad_mi(a.arg):
                kotu.append(a.arg)
        for ic in ast.walk(dugum):
            for ad in _atanan_adlar(ic):
                if turkce_ad_mi(ad):
                    kotu.append(ad)
        if kotu:
            out.append((dugum.lineno, dugum.name, sorted(set(kotu))))
    # modul duzeyi sabitler ve atamalar
    for dugum in (agac.body if isinstance(agac, ast.Module) else []):
        for ad in _atanan_adlar(dugum):
            if turkce_ad_mi(ad):
                out.append((dugum.lineno, "<modul>", [ad]))
    return out


def kaynak_dosyalar():
    for kok, _dizinler, dosyalar in os.walk(KAYNAK):
        if "__pycache__" in kok:
            continue
        for ad in sorted(dosyalar):
            if ad.endswith(".py"):
                yield os.path.join(kok, ad)
    for ad in ("main.py",):
        yield os.path.join(KOK, ad)


def main() -> int:
    bulgular = []
    struct_bulgulari = []
    for yol in kaynak_dosyalar():
        ad = os.path.basename(yol)
        with open(yol, encoding="utf-8") as fh:
            satirlar = fh.readlines()
        for no, satir in enumerate(satirlar, 1):
            kod = satir.split("#")[0]
            if not kod.strip():
                continue
            for desen, aciklama, izinli in KURALLAR:
                if ad in izinli:
                    continue
                if re.search(desen, kod):
                    bulgular.append((os.path.relpath(yol, KOK), no, aciklama,
                                     kod.strip()[:80]))
            for eslesme in STRUCT_DESENI.finditer(kod):
                if eslesme.group(3) not in "<>!=@":
                    struct_bulgulari.append(
                        (os.path.relpath(yol, KOK), no, kod.strip()[:80]))

    # AST ile: PyQt ceviriyi core'a sizdirmis mi? + tanimlayici dili
    sizinti = []
    isim_ihlalleri = []
    muaf_kalan = set()
    for yol in kaynak_dosyalar():
        ad = os.path.basename(yol)
        with open(yol, encoding="utf-8") as fh:
            agac = ast.parse(fh.read(), filename=yol)
        bulunan = isim_bulgulari(yol, agac)
        anahtar = modul_anahtari(yol)
        if anahtar in ISIM_MUAFIYETI:
            if bulunan:
                muaf_kalan.add(anahtar)
        elif bulunan:
            for no, fn, kotu in bulunan:
                isim_ihlalleri.append((os.path.relpath(yol, KOK), no, fn, kotu))
        if os.sep + "core" + os.sep not in yol:
            continue
        for dugum in ast.walk(agac):
            if isinstance(dugum, (ast.Import, ast.ImportFrom)):
                adlar = ([a.name for a in dugum.names]
                         if isinstance(dugum, ast.Import) else [dugum.module or ""])
                for a in adlar:
                    if a.startswith("PyQt"):
                        sizinti.append((os.path.relpath(yol, KOK), dugum.lineno))

    print("=== Capraz platform denetimi ===\n")
    print("Platform katmani (isletim sistemi farki barindirmasi beklenen): "
          + ", ".join(sorted(PLATFORM_KATMANI)) + "\n")
    if bulgular:
        print(f"Platforma bagli {len(bulgular)} nokta:")
        for yol, no, aciklama, kod in bulgular:
            print(f"  {yol}:{no}  {aciklama}\n      {kod}")
    else:
        print("Platforma bagli kalip bulunamadi.")

    print()
    if struct_bulgulari:
        print(f"Endian isareti olmayan {len(struct_bulgulari)} struct cagrisi "
              "(makine siralamasina baglidir):")
        for yol, no, kod in struct_bulgulari:
            print(f"  {yol}:{no}\n      {kod}")
    else:
        print("Tum struct cagrilari endian isareti tasiyor.")

    print()
    if sizinti:
        print(f"KATMAN IHLALI: core/ icinde PyQt import edilmis ({len(sizinti)}):")
        for yol, no in sizinti:
            print(f"  {yol}:{no}")
    else:
        print("Katman ayrimi korunuyor: core/ icinde PyQt yok.")

    print()
    if isim_ihlalleri:
        print(f"ISIMLENDIRME: Turkce adli {len(isim_ihlalleri)} fonksiyon "
              "(kural: Turkce arayuz metni, Ingilizce kod adi):")
        for yol, no, fn, kotu in isim_ihlalleri:
            print(f"  {yol}:{no}  {fn}  ->  {', '.join(kotu)}")
    elif ISIM_MUAFIYETI:
        print(f"Tanimlayici dili: cevrilmis dosyalar temiz. "
              f"Bekleyen {len(ISIM_MUAFIYETI)} muaf dosya var.")
    else:
        print("Tanimlayici dili: tum fonksiyon ve parametre adlari Ingilizce.")

    bayat = sorted(ISIM_MUAFIYETI - muaf_kalan)
    if bayat:
        print("\n  Not: su dosyalar artik temiz, ISIM_MUAFIYETI'nden silinebilir:")
        for ad in bayat:
            print(f"    {ad}")

    hata = (len(bulgular) + len(struct_bulgulari) + len(sizinti)
            + len(isim_ihlalleri))
    print(f"\nToplam bulgu: {hata}")
    return 1 if hata else 0


if __name__ == "__main__":
    sys.exit(main())
