@echo off
setlocal
cd /d "%~dp0"
title Genially a PDF

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Creando entorno virtual de Python ^(solo la primera vez^)...
    where py >nul 2>&1
    if %errorlevel%==0 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo No se pudo crear el entorno virtual con Python 3.
        echo Descarga Python 3.10 o superior desde https://www.python.org/downloads/
        echo IMPORTANTE: marca la casilla "Add Python to PATH" al instalar.
        echo.
        pause
        exit /b 1
    )

    echo [2/2] Instalando librerias necesarias ^(puede tardar 1-2 minutos la primera vez^)...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Hubo un error instalando las dependencias. Verifica tu conexion a internet.
        pause
        exit /b 1
    )
)

echo Iniciando Genially a PDF...
echo ^(No cierres esta ventana mientras uses la pagina en tu navegador^)
echo.
".venv\Scripts\python.exe" app.py
if errorlevel 1 (
    echo.
    echo La aplicacion se cerro con un error. Revisa el mensaje de arriba.
    pause
)
