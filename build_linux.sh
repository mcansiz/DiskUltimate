#!/usr/bin/env bash
# DiskUltimate — Linux paketleme (PyInstaller).
#
#   ./build_linux.sh            tek dosyalik dist/DiskUltimate uretir
#   ./build_linux.sh --clean    once PyInstaller onbellegini temizler
#
# Windows karsiligi: build_exe.bat. Butun paketleme ayarlari
# DiskUltimate.spec icindedir; bu betik yalnizca ortami dogrular.
set -u

# Betik nerede duruyorsa proje koku orasidir — sabit yol yazilmaz, boylece
# depo baska bir dizine klonlanirsa da calisir.
cd "$(dirname "$(readlink -f "$0")")" || exit 1

kirmizi=""; yesil=""; sari=""; normal=""
if [ -t 1 ]; then
  kirmizi=$'\033[31m'; yesil=$'\033[32m'; sari=$'\033[33m'; normal=$'\033[0m'
fi

hata() { printf '%s\n' "${kirmizi}HATA! $*${normal}" >&2; exit 1; }
uyari() { printf '%s\n' "${sari}UYARI: $*${normal}"; }

echo "============================================================"
echo "  DiskUltimate — Linux paketi uretiliyor (PyInstaller)"
echo "============================================================"
echo

# ---------------------------------------------------------------------
# 1) Python yorumlayicisi. PyQt5 ve PyInstaller HANGI yorumlayicida
#    kuruluysa paketleme de orada yapilmalidir; bu yuzden PyQt5'i bulan
#    ilk yorumlayici secilir.
# ---------------------------------------------------------------------
PY=""
for aday in python3.12 python3 python; do
  komut="$(command -v "$aday" 2>/dev/null)" || continue
  if "$komut" -c "import PyQt5.QtWidgets" >/dev/null 2>&1; then PY="$komut"; break; fi
  [ -n "$PY" ] || PY_YEDEK="${PY_YEDEK:-$komut}"
done
if [ -z "$PY" ]; then
  [ -n "${PY_YEDEK:-}" ] || hata $'Python bulunamadi. Kurun:\n  sudo apt install python3 python3-pip'
  # Python var ama PyQt5 yok: paketleme yine de yapilabilirdi, ama uretilen
  # dosya acilista ModuleNotFoundError verirdi (PyInstaller eksik paketi
  # yalnizca uyariyla gecer). Erken durulur.
  hata "PyQt5 kurulu degil — paketleme durduruldu.
  Kur:  $PY_YEDEK -m pip install -r requirements.txt
  veya: sudo apt install python3-pyqt5"
fi
echo "Python : $("$PY" -c 'import sys; print(sys.version.split()[0], sys.executable)')"

# Kaynak agaci yerinde mi? (Yanlis dizinden calistirma erken yakalanir.)
for gerekli in main.py DiskUltimate.spec src/diskultimate/i18n/catalogs/en.ts \
               src/diskultimate/ui/resources/app-icon.ico; do
  [ -e "$gerekli" ] || hata "Kaynak agaci eksik ($gerekli yok).
  Bu betik depo kokunde durmali. Su an: $PWD"
done

# ---------------------------------------------------------------------
# 2) PyInstaller kurulu mu? Degilse ayni yorumlayiciya kurulur.
# ---------------------------------------------------------------------
if ! "$PY" -m PyInstaller --version >/dev/null 2>&1; then
  echo "PyInstaller bulunamadi — kuruluyor..."
  "$PY" -m pip install pyinstaller || hata $'PyInstaller kurulamadi.\n  Deneyin: '"$PY"' -m pip install --user pyinstaller'
fi
echo "Paketci: PyInstaller $("$PY" -m PyInstaller --version 2>/dev/null)"

# spec dosyasi Linux'ta strip=True kullanir (libpython 31 MB -> ~8 MB).
command -v strip >/dev/null 2>&1 || uyari "\`strip\` yok, paket daha buyuk olacak (sudo apt install binutils)"
# Yetki yukseltmesi pkexec ile yapilir; yoksa uygulama calisir ama fiziksel
# disk icin kullaniciya 'sudo ile baslatin' demek zorunda kalir (ADR 0039).
command -v pkexec >/dev/null 2>&1 || uyari "\`pkexec\` yok; fiziksel disk icin yetki penceresi acilamaz (sudo apt install policykit-1)"
echo

# ---------------------------------------------------------------------
# 3) Paketleme. Butun ayarlar DiskUltimate.spec icinde: giris noktasi
#    main.py, pathex=src, ceviri sozlukleri (.ts) datas olarak,
#    kullanilmayan Qt modullerinin haric tutulmasi ve fazla kitapliklari
#    kirpan _DROP filtresi.
# ---------------------------------------------------------------------
ek=()
for arg in "$@"; do
  case "$arg" in
    --clean) ek+=(--clean) ;;
    *) hata "bilinmeyen secenek: $arg (yalnizca --clean)" ;;
  esac
done

"$PY" -m PyInstaller --noconfirm "${ek[@]+"${ek[@]}"}" DiskUltimate.spec || hata "Paketleme basarisiz oldu. Yukaridaki ciktiya bakin."
[ -f dist/DiskUltimate ] || hata "dist/DiskUltimate uretilemedi."
chmod +x dist/DiskUltimate

boyut="$(du -h dist/DiskUltimate | cut -f1)"
echo
echo "${yesil}============================================================${normal}"
echo "${yesil}  BASARILI!  Cikti: dist/DiskUltimate ($boyut)${normal}"
echo "${yesil}============================================================${normal}"
cat <<'NOT'

  Calistirma:  ./dist/DiskUltimate

  NOT: Goruntu dosyalari (.img/.vhd/...) icin yetki GEREKMEZ. Gercek
  disklere erismek icin uygulama root yetkisini kendisi ister (pkexec
  parola penceresi); paketi "sudo" ile baslatmaniz gerekmez.
  Tanilama gunlugu: ~/.local/state/DiskUltimate/logs/
NOT
