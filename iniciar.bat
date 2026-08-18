@echo off
setlocal
cd /d "%~dp0"
title Extrator PDF Juridico

set "PYEXE=python"
python --version >nul 2>nul
if errorlevel 1 set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

rem Abre o navegador depois que o servidor subir
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8077"

echo Aplicacao rodando em http://127.0.0.1:8077
echo Para encerrar, feche esta janela.
"%PYEXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8077
