@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
python --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
  py -3 --version >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
  echo Python 3 est requis. Installez-le depuis https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

%PYTHON_CMD% -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo Echec de l'installation de PyInstaller.
  pause
  exit /b 1
)

%PYTHON_CMD% -m PyInstaller --noconfirm --clean --onedir --name Nucleares-Control-Center --add-data "static;static" app.py
if errorlevel 1 (
  echo Echec de la creation de l'application.
  pause
  exit /b 1
)

echo.
echo Application creee dans dist\Nucleares-Control-Center
pause
