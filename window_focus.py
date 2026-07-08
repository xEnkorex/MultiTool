"""Trae al frente la ventana principal de una app dada su nombre de
proceso — para cuando algo como Discord quedó minimizado o enterrado
detrás de otras ventanas y "se perdió" de vista.

`SetForegroundWindow` por sí solo suele fallar cuando lo llama un proceso
que no tiene el foco (Windows lo bloquea a propósito para que las apps no
se puedan robar el foco entre sí) — el truco estándar para esquivarlo es
"pegar" temporalmente el input de nuestro hilo al del hilo dueño de la
ventana en foco actual, vía `AttachThreadInput`.
"""

from __future__ import annotations

import os

import psutil
import win32api
import win32con
import win32gui
import win32process


def _find_main_window(pids: set[int]) -> int | None:
    candidates: list[int] = []

    def callback(hwnd: int, _extra) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER) != 0:
            return True  # ventana "hija"/popup de otra, no es la principal
        if not win32gui.GetWindowText(hwnd):
            return True
        _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
        if win_pid in pids:
            candidates.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return candidates[0] if candidates else None


def _relaunch(exe_path: str) -> None:
    """Algunas apps (Steam con "minimizar a la bandeja", por ejemplo) no
    iconifican su ventana real al mandarla a la bandeja — la ocultan o
    directamente la destruyen, dejando solo alguna ventanita interna vacía
    ("Sin título", sin contenido) que probamos mostrar y no sirve de nada.
    Volver a "abrir" el ejecutable no crea una segunda instancia: el propio
    mecanismo de instancia única de la app detecta que ya está corriendo y
    trae SU ventana real al frente — más confiable que adivinar qué HWND
    mostrar desde afuera."""
    os.startfile(exe_path)  # noqa: S606 — ruta viene de psutil, no de input externo


def _force_foreground(hwnd: int) -> None:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    current_thread = win32api.GetCurrentThreadId()
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
    target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]

    attached: list[int] = []
    try:
        for thread_id in {fg_thread, target_thread}:
            if thread_id and thread_id != current_thread:
                if win32process.AttachThreadInput(current_thread, thread_id, True):
                    attached.append(thread_id)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    finally:
        for thread_id in attached:
            win32process.AttachThreadInput(current_thread, thread_id, False)


def focus_app(process_name: str) -> bool:
    """Busca la ventana principal de la app corriendo con ese nombre de
    proceso y la trae al frente. Si no encuentra ninguna ventana visible
    (tray-only real, no solo minimizada) reintenta "relanzando" el
    ejecutable. Devuelve False si el proceso ni siquiera está corriendo."""
    target = process_name.lower()
    exe_path = None
    pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        if (proc.info["name"] or "").lower() == target:
            pids.add(proc.info["pid"])
            exe_path = exe_path or proc.info["exe"]

    if not pids:
        return False

    hwnd = _find_main_window(pids)
    if hwnd is not None:
        _force_foreground(hwnd)
        return True

    if exe_path:
        _relaunch(exe_path)
        return True
    return False
