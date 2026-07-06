"""Persistencia del layout del grid modular (posición/tamaño de cada
bloque: mixer, launcher, shortcuts, apps en segundo plano).

Mismo patrón que `launcher_store.py`: JSON plano en `%APPDATA%\\AudioMixer`.
Si no hay nada guardado, `load_layout()` devuelve `None` y el frontend
cae al layout por defecto (hardcodeado en `app.js`) — no hace falta
duplicar ese default acá.
"""

from __future__ import annotations

import json
import threading
from typing import Optional

from pydantic import BaseModel

import paths

CONFIG_PATH = paths.data_dir() / "layout_config.json"

_lock = threading.RLock()


class LayoutItem(BaseModel):
    id: str  # "mixer" | "launcher" | "shortcuts" | "bg-apps"
    x: int
    y: int
    w: int
    h: int


def load_layout() -> Optional[list[LayoutItem]]:
    with _lock:
        if not CONFIG_PATH.exists():
            return None
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            items = [LayoutItem(**item) for item in raw.get("items", [])]
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            return None
        return items or None


def save_layout(items: list[LayoutItem]) -> None:
    with _lock:
        payload = {"items": [item.model_dump() for item in items]}
        CONFIG_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
