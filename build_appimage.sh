#!/usr/bin/env bash
# DiskUltimate — Linux AppImage paketleme (ADR 0050).
#
#   ./build_appimage.sh          dist/DiskUltimate-<surum>-x86_64.AppImage uretir
#   ./build_appimage.sh --keep   AppDir'i .tmp/appimage altinda birakir
#
# Butun adimlar tools/appimage.py icindedir. Tasinabilir Python (manylinux2014,
# glibc 2.17) ve appimagetool sabit surumlerle indirilir, SHA-256 denetlenir;
# derleme makinesinin glibc surumu sonucu etkilemez. Ag baglantisi gerekir
# (ilk calistirmada araclar, her calistirmada PyQt5 tekerlekleri).
set -eu
cd "$(dirname "$(readlink -f "$0")")"
command -v readelf >/dev/null || { echo "HATA: readelf yok (binutils)"; exit 1; }
exec python3 tools/appimage.py "$@"
