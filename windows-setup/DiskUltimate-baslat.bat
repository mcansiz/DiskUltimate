@echo off
REM DiskUltimate - Windows baslatici
REM Bu dosya proje klasorundeki windows-setup icinde durur; uygulamayi bir ust
REM klasorden calistirir. Python PATH te olmalidir (bkz. OKUBENI.md).

cd /d "%~dp0.."
where python >nul 2>&1
if errorlevel 1 (
    echo HATA: python PATH te bulunamadi.
    echo windows-setup\OKUBENI.md icindeki kurulum adimlarini uygulayin.
    pause
    exit /b 1
)
python main.py %*
if errorlevel 1 pause
