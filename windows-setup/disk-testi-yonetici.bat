@echo off
REM Fiziksel disk sondasi — YALNIZCA OKUMA
REM Bu dosyaya SAG TIK > "Yonetici olarak calistir"
REM .bat sarmalayicisi PowerShell'in ExecutionPolicy kisitini atlar.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0disk-testi-yonetici.ps1"
