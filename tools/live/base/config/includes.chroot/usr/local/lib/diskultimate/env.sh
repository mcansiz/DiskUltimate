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
