"""Persistencia de los accesos directos del panel "Launcher".

Se guarda como un JSON plano en disco (`launcher_config.json`, en
`%APPDATA%\\AudioMixer`). Es uso personal con a lo sumo un par de clientes
en la LAN, así que un lock en memoria + reescritura completa del archivo
es más que suficiente.

Lista dinámica (no un número fijo de slots): cada acceso directo tiene un
`id` propio que no se reutiliza, así que agregar/quitar no reordena ni
pisa a los demás.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
from typing import Optional

from pydantic import BaseModel

import paths

CONFIG_PATH = paths.data_dir() / "launcher_config.json"

# RLock (no Lock): save_slot/delete_slot llaman a load_slots() internamente
# mientras ya tienen el lock tomado.
_lock = threading.RLock()
_id_counter = itertools.count(1)


class LauncherSlot(BaseModel):
    id: int
    name: str
    path: str
    icon: Optional[str] = None
    icon_path: Optional[str] = None


def _next_id() -> int:
    return next(_id_counter)


def _bump_counter_past(existing_ids: list[int]) -> None:
    global _id_counter
    start = (max(existing_ids) + 1) if existing_ids else 1
    _id_counter = itertools.count(start)


def load_slots() -> list[LauncherSlot]:
    with _lock:
        if not CONFIG_PATH.exists():
            return []
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            slots = [LauncherSlot(**s) for s in raw.get("slots", []) if s.get("path")]
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            return []
        _bump_counter_past([s.id for s in slots])
        return slots


def add_slot(name: str, path: str, icon: str | None, icon_path: str | None) -> LauncherSlot:
    with _lock:
        slots = load_slots()
        slot = LauncherSlot(id=_next_id(), name=name, path=path, icon=icon, icon_path=icon_path)
        slots.append(slot)
        _write(slots)
        return slot


def save_slot(
    slot_id: int, name: str, path: str, icon: str | None, icon_path: str | None
) -> LauncherSlot:
    with _lock:
        slots = load_slots()
        for i, slot in enumerate(slots):
            if slot.id == slot_id:
                slots[i] = LauncherSlot(id=slot_id, name=name, path=path, icon=icon, icon_path=icon_path)
                _write(slots)
                return slots[i]
        raise ValueError(f"No existe el slot {slot_id}")


def delete_slot(slot_id: int) -> None:
    with _lock:
        slots = [s for s in load_slots() if s.id != slot_id]
        _write(slots)


def get_slot(slot_id: int) -> Optional[LauncherSlot]:
    for slot in load_slots():
        if slot.id == slot_id:
            return slot
    return None


def _write(slots: list[LauncherSlot]) -> None:
    payload = {"slots": [s.model_dump() for s in slots]}
    CONFIG_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def launch(path: str) -> None:
    """Lanza un ejecutable, acceso directo (.lnk) o documento asociado.

    Usa `os.startfile`, el mismo mecanismo que un doble clic en el
    Explorador de Windows: respeta asociaciones de archivo y no bloquea
    esperando a que la app termine.
    """
    if not path:
        raise FileNotFoundError("Sin ruta configurada")
    expanded = os.path.expandvars(path)
    if not os.path.exists(expanded):
        raise FileNotFoundError(expanded)
    os.startfile(expanded)  # type: ignore[attr-defined]  # solo existe en Windows
