"""Extrae el ícono real de una app de Windows a partir de su nombre de
proceso (ej. "brave.exe"), para mostrarlo en el mixer en vez de una sigla
de 2 letras. Se cachea en memoria: el ícono de un ejecutable no cambia
mientras el proceso sigue corriendo, y volver a hacer la extracción GDI
en cada request sería un desperdicio.
"""

from __future__ import annotations

import io
import threading

import psutil
import win32gui
import win32ui
from PIL import Image

_lock = threading.Lock()
_cache: dict[str, bytes | None] = {}


def _find_exe_path(process_name: str) -> str | None:
    target = process_name.lower()
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            if (proc.info["name"] or "").lower() == target and proc.info["exe"]:
                return proc.info["exe"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _extract_icon_png(exe_path: str) -> bytes | None:
    large, small = win32gui.ExtractIconEx(exe_path, 0)
    all_icons = list(large) + list(small)
    if not all_icons:
        return None

    hicon = all_icons[0]
    hbm_color = None
    hbm_mask = None
    try:
        _, _, _, hbm_mask, hbm_color = win32gui.GetIconInfo(hicon)
        bitmap = win32ui.CreateBitmapFromHandle(hbm_color)
        info = bitmap.GetInfo()

        # Solo los íconos de 32bpp traen canal alfa real en su bitmap de
        # color; los legacy (16 colores, máscara 1-bit) necesitarían
        # combinar hbm_color + hbm_mask a mano — no vale la pena para un
        # simple ícono de mixer, se cae al fallback de texto en ese caso.
        if info["bmBitsPixel"] != 32:
            return None

        bits = bitmap.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGBA", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRA", 0, 1
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        for h in all_icons:
            win32gui.DestroyIcon(h)
        if hbm_color is not None:
            win32gui.DeleteObject(hbm_color)
        if hbm_mask is not None:
            win32gui.DeleteObject(hbm_mask)


def get_icon_png(process_name: str) -> bytes | None:
    """Devuelve el PNG del ícono (con alfa) del ejecutable corriendo con ese
    nombre de proceso, o None si no se pudo (proceso no encontrado, ícono
    legacy sin alfa, o cualquier falla de la extracción GDI).

    Solo se cachean los éxitos: el ícono de un .exe no cambia mientras
    sigue corriendo, así que vale la pena guardarlo. Un fallo, en cambio,
    puede ser transitorio (el proceso reapareció bajo otro PID, un hiccup
    de GDI) — cachearlo dejaría el ícono "roto" para siempre hasta
    reiniciar el servidor, así que se reintenta en cada request.
    """
    with _lock:
        cached = _cache.get(process_name)
    if cached is not None:
        return cached

    icon_bytes = None
    exe_path = _find_exe_path(process_name)
    if exe_path:
        try:
            icon_bytes = _extract_icon_png(exe_path)
        except Exception:
            icon_bytes = None

    if icon_bytes is not None:
        with _lock:
            _cache[process_name] = icon_bytes
    return icon_bytes
