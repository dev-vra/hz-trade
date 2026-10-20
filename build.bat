@echo off
rem Gera dist\HZSinais\HZSinais.exe (onedir: QtWebEngine nao funciona bem em onefile)
cd /d %~dp0
if exist dist\HZSinais\dados move dist\HZSinais\dados dados_build_bkp >nul
python -m PyInstaller --noconfirm --windowed --name HZSinais ^
  --paths src ^
  --add-data "data\_news.json;data" ^
  --exclude-module matplotlib --exclude-module tkinter ^
  src\app.py
if exist dados_build_bkp move dados_build_bkp dist\HZSinais\dados >nul
echo.
echo Pronto: dist\HZSinais\HZSinais.exe
