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

start "Nucleares Mock Game" %PYTHON_CMD% mock_game.py
timeout /t 2 /nobreak >nul
%PYTHON_CMD% app.py
if errorlevel 1 pause
