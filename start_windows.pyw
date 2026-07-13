"""Lance NUCLEARES Control Center sans passer par cmd.exe."""

from pathlib import Path
import os
import sys


root = Path(__file__).resolve().parent
os.chdir(root)
sys.path.insert(0, str(root))

try:
    import app
    raise SystemExit(app.run())
except SystemExit:
    raise
except Exception as exc:
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        0,
        f"Impossible de démarrer l’application :\n\n{exc}",
        "NUCLEARES Control Center",
        0x10,
    )
