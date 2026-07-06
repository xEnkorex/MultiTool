"""Persistencia de los shortcuts de teclado del panel "Shortcuts".

Mismo patrón que `launcher_store.py`: JSON plano en `%APPDATA%\\AudioMixer`,
lista dinámica con ids propios (no un número fijo de slots).
"""

from __future__ import annotations

import itertools
import json
import threading
from typing import Optional

from pydantic import BaseModel

import paths

CONFIG_PATH = paths.data_dir() / "shortcuts_config.json"

_lock = threading.RLock()
_id_counter = itertools.count(1)


class ShortcutSlot(BaseModel):
    id: int
    name: str
    keys: str  # ej. "windows+shift+c"
    icon: Optional[str] = None
    icon_path: Optional[str] = None


def _next_id() -> int:
    return next(_id_counter)


def _bump_counter_past(existing_ids: list[int]) -> None:
    global _id_counter
    start = (max(existing_ids) + 1) if existing_ids else 1
    _id_counter = itertools.count(start)


def load_slots() -> list[ShortcutSlot]:
    with _lock:
        if not CONFIG_PATH.exists():
            return []
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            slots = [ShortcutSlot(**s) for s in raw.get("slots", []) if s.get("keys")]
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            return []
        _bump_counter_past([s.id for s in slots])
        return slots


def add_slot(name: str, keys: str, icon: str | None, icon_path: str | None) -> ShortcutSlot:
    with _lock:
        slots = load_slots()
        slot = ShortcutSlot(id=_next_id(), name=name, keys=keys, icon=icon, icon_path=icon_path)
        slots.append(slot)
        _write(slots)
        return slot


def save_slot(
    slot_id: int, name: str, keys: str, icon: str | None, icon_path: str | None
) -> ShortcutSlot:
    with _lock:
        slots = load_slots()
        for i, slot in enumerate(slots):
            if slot.id == slot_id:
                slots[i] = ShortcutSlot(
                    id=slot_id, name=name, keys=keys, icon=icon, icon_path=icon_path
                )
                _write(slots)
                return slots[i]
        raise ValueError(f"No existe el shortcut {slot_id}")


def delete_slot(slot_id: int) -> None:
    with _lock:
        slots = [s for s in load_slots() if s.id != slot_id]
        _write(slots)


def get_slot(slot_id: int) -> Optional[ShortcutSlot]:
    for slot in load_slots():
        if slot.id == slot_id:
            return slot
    return None


def _write(slots: list[ShortcutSlot]) -> None:
    payload = {"slots": [s.model_dump() for s in slots]}
    CONFIG_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
