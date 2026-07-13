#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 -m pip install --user --upgrade pyinstaller
python3 -m PyInstaller --noconfirm --clean --onedir --name Nucleares-Control-Center --add-data "static:static" app.py
echo "Application créée dans dist/Nucleares-Control-Center"
