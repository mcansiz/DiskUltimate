#!/usr/bin/env bash
# DiskUltimate live ISO tabani (ADR 0101) — GELISTIRME ARACI.
#
#   docker run --rm --privileged -v "$PWD/tools/live/base:/cfg:ro" \
#     -v "$PWD/.tmp/live/base:/out" debian:trixie bash /cfg/build.sh /out
#
# debian:trixie kapsayicisinda root olarak kosar (live-build/debootstrap root
# ister; ana makinede root yok, CLAUDE.md). Cikti:
#   /out/base.iso        uygulamasiz taban (Debian 13 + X + jwm + pcmanfm)
#   /out/base.packages   ISO'daki paketlerin surum listesi (lisans manifesti)
#
# Uygulama bu ISO'da YOKTUR: AppImage her surumde ayri bir squashfs katmani
# olarak `tools/live/remaster.py` ile eklenir. Taban yalnizca bu dizindeki
# bir dosya degisince ya da haftalik guvenlik tazelemesinde yeniden kurulur
# (.github/workflows/live-iso.yml).
set -euo pipefail

CFG="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:?kullanim: build.sh <cikis-dizini>}"
mkdir -p "$OUT"

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
  live-build debootstrap xorriso squashfs-tools mtools dosfstools cpio \
  grub-pc-bin grub-efi-amd64-bin ca-certificates

WORK="/var/tmp/du-live"
rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK"

# Paket listesi ve ayarlar config/ altinda; lb config yalnizca iskeleti kurar.
lb config \
  --mode debian --system live \
  --distribution trixie \
  --archive-areas "main non-free-firmware" \
  --security true --updates true \
  --apt-recommends false \
  --apt-indices false \
  --cache-packages false \
  --firmware-binary false --firmware-chroot false \
  --debian-installer none \
  --memtest none \
  --binary-images iso-hybrid \
  --bootloaders "grub-efi grub-pc" \
  --uefi-secure-boot enable \
  --chroot-squashfs-compression-type xz \
  --checksums sha256 \
  --iso-application "DiskUltimate Live" \
  --iso-publisher "DiskUltimate" \
  --iso-volume "DISKULTIMATE" \
  --image-name "diskultimate-base" \
  --bootappend-live "boot=live components quiet live-config.noautologin live-config.hostname=diskultimate"

cp -a "$CFG/config/." config/

# Sikistirma: 1 MB blok + x86 suzgeci (live-build MKSQUASHFS_OPTIONS'u
# komut satirina ekler). Bellenim klasorunde olculdu (2026-10-28):
# 128 KB blok 127 MB, 1 MB blok 114 MB. Suzgec yalnizca kucultugu blokta
# kullanilir (programlar: LLVM, gallium).
export MKSQUASHFS_OPTIONS="-b 1M -Xbcj x86"

lb build 2>&1 | tee "$OUT/build.log"

iso="$(ls diskultimate-base-*.iso | head -n1)"
mv "$iso" "$OUT/base.iso"
cp chroot.packages.live "$OUT/base.packages"
chmod a+r "$OUT"/*
ls -l "$OUT"
