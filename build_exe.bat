@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title DiskUltimate - EXE Paketleme

REM Betik nerede duruyorsa proje koku orasidir - sabit yol yazilmaz, boylece
REM depo baska bir dizine klonlanirsa da calisir.
cd /d "%~dp0"

echo ============================================================
echo   DiskUltimate - EXE paketleniyor (PyInstaller)
echo ============================================================
echo.

REM ---------------------------------------------------------------------
REM 1) Python yorumlayicisi. Gelistirme surumu 3.12; yoksa baska bir 3.x'e
REM    duseriz. PyQt5 ve PyInstaller HANGI yorumlayicida kuruluysa paketleme
REM    de orada yapilmalidir.
REM ---------------------------------------------------------------------
set "PY="
for %%V in ("py -3.12" "py -3" "python") do (
  %%~V -c "import sys" >nul 2>&1
  if not errorlevel 1 if not defined PY set "PY=%%~V"
)
if not defined PY (
  echo ############################################################
  echo   HATA! Python bulunamadi. https://python.org adresinden
  echo   Python 3.12 kurun ^(kurulumda "py launcher" isaretli olsun^).
  echo ############################################################
  pause >nul
  exit /b 1
)
for /f "delims=" %%O in ('%PY% -c "import sys;print(sys.version.split()[0], sys.executable)"') do set "PYINFO=%%O"
echo Python: %PYINFO%
echo.

REM ---------------------------------------------------------------------
REM 2) ON KONTROL: uygulamanin bagimliligi BU python'da kurulu mu?
REM    (PyInstaller eksik paketi yalniz UYARIyla gecer; exe "basarili"
REM    uretilir ama acilista ModuleNotFoundError verir.)
REM    DiskUltimate'in tek calisma zamani bagimliligi PyQt5'tir; cekirdek
REM    saf Python'dur (bkz. CLAUDE.md / requirements.txt).
REM ---------------------------------------------------------------------
%PY% -c "import PyQt5.QtWidgets, PyQt5.QtCore, PyQt5.QtGui" >nul 2>&1
if errorlevel 1 (
  echo ############################################################
  echo   HATA! PyQt5 kurulu degil - paketleme durduruldu.
  echo   Kur:  %PY% -m pip install -r requirements.txt
  echo ############################################################
  echo.
  pause >nul
  exit /b 1
)

REM Kaynak agaci yerinde mi? ^(Yanlis dizinden calistirma erken yakalanir.^)
if not exist "main.py" goto :no_source
if not exist "src\diskultimate\i18n\catalogs\en.po" goto :no_source
if not exist "DiskUltimate.spec" goto :no_source

REM ---------------------------------------------------------------------
REM 3) PyInstaller kurulu mu? Degilse ayni yorumlayiciya kur.
REM ---------------------------------------------------------------------
%PY% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo PyInstaller bulunamadi - kuruluyor...
  %PY% -m pip install pyinstaller
  if errorlevel 1 (
    echo ############################################################
    echo   HATA! PyInstaller kurulamadi.
    echo ############################################################
    pause >nul
    exit /b 1
  )
  echo.
)

REM ---------------------------------------------------------------------
REM 4) Paketleme. Butun ayarlar DiskUltimate.spec icinde:
REM    giris noktasi main.py, pathex=src, ceviri sozlukleri (.po) datas
REM    olarak, kullanilmayan Qt modullerinin haric tutulmasi ve
REM    fazla DLL'leri kirpan _DROP filtresi.
REM ---------------------------------------------------------------------
%PY% -m PyInstaller --noconfirm "DiskUltimate.spec"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" goto :failed
if not exist "dist\DiskUltimate.exe" goto :failed

echo ============================================================
echo   BASARILI!  Cikti:  dist\DiskUltimate.exe
echo ============================================================
echo.
echo   NOT: Goruntu dosyalari ^(.img/.vhd/...^) icin yetki gerekmez.
echo   Gercek disklere erismek icin uygulama yonetici yetkisini
echo   kendisi ister ^(UAC^); exe'yi elle "Yonetici olarak calistir"
echo   ile de baslatabilirsiniz.
goto :done

:no_source
echo ############################################################
echo   HATA! Kaynak agaci eksik. Bu betik depo kokunde durmali
echo   ^(main.py, src\diskultimate\, DiskUltimate.spec yaninda^).
echo   Su an: %CD%
echo ############################################################
echo.
pause >nul
exit /b 1

:failed
echo ############################################################
echo   HATA! Paketleme basarisiz oldu. Yukaridaki ciktiya bak.
echo ############################################################
echo.
echo Pencereyi kapatmak icin bir tusa basin...
pause >nul
exit /b 1

:done
echo.
echo Pencereyi kapatmak icin bir tusa basin...
pause >nul
exit /b 0
