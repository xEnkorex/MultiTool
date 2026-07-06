"""Resolución de rutas que funciona tanto corriendo desde código fuente
como empaquetado en un .exe con PyInstaller.

- Los assets de solo lectura (`static/`) se empaquetan DENTRO del
  ejecutable y PyInstaller los extrae a una carpeta temporal en cada
  arranque (`sys._MEIPASS` en modo --onefile) — por eso `resource_dir()`
  apunta ahí cuando la app está "congelada".
- Los datos que la app ESCRIBE (config del launcher, logs) no pueden vivir
  en esa carpeta: se borra al cerrar el programa. Van a
  `%APPDATA%\\AudioMixer`, que persiste sin importar desde dónde se corra
  el .exe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Carpeta de assets empaquetados de solo lectura (ej. `static/`)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """Carpeta persistente para archivos que la app escribe (config, logs)."""
    base = Path(os.environ.get("APPDATA", Path.home())) / "AudioMixer"
    base.mkdir(parents=True, exist_ok=True)
    return base
