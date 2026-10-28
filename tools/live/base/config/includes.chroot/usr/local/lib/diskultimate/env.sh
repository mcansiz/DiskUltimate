# DiskUltimate live: acilis satirindan dil secimi (GRUB menusu).
#   diskultimate.lang=<kod>  ->  DU_LANG
# Ortak: session ve baslatici bunu okur.
DU_LANG=""
for arg in $(cat /proc/cmdline); do
  case "$arg" in
    diskultimate.lang=*) DU_LANG="${arg#diskultimate.lang=}" ;;
  esac
done
export DU_LANG

# Test isareti: seri port varsa yazilir (tools/live/boot_check.py bekler).
# Gercek makinede port yoksa sessizce gecer; asla beklemez.
du_mark() {
  [ -c /dev/ttyS0 ] && timeout 2 sh -c "echo \"DU-LIVE: $*\" > /dev/ttyS0" 2>/dev/null
  return 0
}

# CI denemesi mi? tools/live/boot_check.py QEMU'ya
#   -fw_cfg name=opt/org.diskultimate/ci,string=1
# verir. Yalnizca o zaman ekran cozunurlugu degistirilir ve sanal kablosuz
# kart (mac80211_hwsim) kurulur; gercek makinede bu dosya yoktur.
DU_CI_FLAG=/sys/firmware/qemu_fw_cfg/by_name/opt/org.diskultimate/ci/raw
du_ci() {
  [ -r "$DU_CI_FLAG" ] || modprobe -q qemu_fw_cfg 2>/dev/null
  [ "$(cat "$DU_CI_FLAG" 2>/dev/null)" = 1 ]
}
