@echo off
setlocal
cd /d "%~dp0"
title Instalador - Extrator PDF Juridico

rem Python ja disponivel no PATH?
set "PYEXE=python"
python --version >nul 2>nul
if not errorlevel 1 goto deps

rem Instalado por este script anteriormente?
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%PYEXE%" goto deps

echo Baixando Python 3.12 (cerca de 25 MB)...
curl -L -o "%TEMP%\python-setup.exe" https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
if errorlevel 1 (
    echo ERRO: falha no download. Verifique a conexao com a internet.
    pause
    exit /b 1
)

echo Instalando Python (1 a 2 minutos, aguarde)...
"%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del "%TEMP%\python-setup.exe"

if not exist "%PYEXE%" (
    echo ERRO: instalacao do Python falhou.
    pause
    exit /b 1
)

:deps
echo Instalando dependencias da aplicacao...
"%PYEXE%" -m pip install --no-warn-script-location -q -r requirements.txt
if errorlevel 1 (
    echo ERRO: falha ao instalar dependencias.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Instalacao concluida!
echo  Use o arquivo iniciar.bat para abrir.
echo ============================================
pause
