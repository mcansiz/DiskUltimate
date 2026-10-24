# DiskUltimate live: tty1'de grafik oturum. Oturum kapanirsa kabuga dusulur
# (dongu yok: X acilmayan makinede ekran surekli yanip sonmesin).
[ -f ~/.bashrc ] && . ~/.bashrc
if [ "$(tty)" = /dev/tty1 ] && [ -z "${DISPLAY:-}" ]; then
  startx /usr/local/lib/diskultimate/session -- -nolisten tcp
  echo
  echo "DiskUltimate: graphical session ended."
  echo "  startx /usr/local/lib/diskultimate/session   restart it"
  echo "  poweroff | reboot"
fi
