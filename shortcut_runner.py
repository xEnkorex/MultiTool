"""Envío de combinaciones de teclado sintéticas (para el panel "Shortcuts").

Usa la librería `keyboard`, que en Windows manda las teclas via el mismo
mecanismo de bajo nivel que usaría el teclado físico (no requiere permisos
de administrador para ENVIAR teclas — solo hacen falta para interceptar
atajos globales de apps elevadas, que no es nuestro caso).

Acepta strings tipo "win+shift+c", "ctrl+alt+t" (nombres de teclas
separados por "+"); ver `keyboard.send` para la sintaxis completa.
"""

from __future__ import annotations

import keyboard


def trigger(keys: str) -> None:
    if not keys or not keys.strip():
        raise ValueError("Sin combinación de teclas configurada")
    keyboard.send(keys.strip())
