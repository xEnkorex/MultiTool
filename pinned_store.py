"""Persiste qué apps quedan ancladas ("pineadas") en el mixer para que
sigan apareciendo en el panel aunque no estén reproduciendo audio en este
momento. Mismo patrón que `layout_store.py`: JSON plano en
`%APPDATA%\\AudioMixer`.

Las pestañas de navegador NO se persisten acá: su identificador (tabId) no
sobrevive a un reinicio del navegador, así que ese pin vive enteramente en
memoria del lado de la extensión (ver `extension/background.js`).
"""

from __future__ import annotations

import json
import threading

import paths

CONFIG_PATH = paths.data_dir() / "pinned_apps.json"

_lock = threading.RLock()


def load_pinned() -> set[str]:
    with _lock:
        if not CONFIG_PATH.exists():
            return set()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {str(name) for name in raw.get("names", [])}
        except (json.JSONDecodeError, TypeError, AttributeError):
            return set()


def save_pinned(names: set[str]) -> None:
    with _lock:
        payload = {"names": sorted(names)}
        CONFIG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def toggle_pinned(name: str) -> set[str]:
    with _lock:
        names = load_pinned()
        if name in names:
            names.discard(name)
        else:
            names.add(name)
        save_pinned(names)
        return names
