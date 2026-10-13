@echo off
setlocal
cd /d "%~dp0"
title Build - Extrator.exe

rem Ambiente limpo: sem ele o PyInstaller empacota tudo o que estiver
rem instalado no Python global (torch, pandas...) e o .exe fica enorme.
if not exist ".build-venv\Scripts\python.exe" python -m venv .build-venv
if errorlevel 1 goto erro
set "PY=.build-venv\Scripts\python.exe"

"%PY%" -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 goto erro

"%PY%" -m PyInstaller --noconfirm --clean --onefile --name Extrator ^
    --add-data "static;static" ^
    --add-data "app\pipeline\tessdata;app\pipeline\tessdata" launcher.py
if errorlevel 1 goto erro

echo.
echo Pronto: dist\Extrator.exe
if "%1"=="" pause
exit /b 0

:erro
echo ERRO: falha no build.
if "%1"=="" pause
exit /b 1
