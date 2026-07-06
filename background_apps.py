"""Detecta y cierra apps "en segundo plano" (candidatas a estar en el
system tray), aproximando lo que muestra la sección "Procesos en segundo
plano" del Administrador de Tareas.

Windows no expone una API pública para enumerar los íconos reales de la
bandeja del sistema (es un detalle interno del Shell/Explorer). En cambio,
esta heurística combina varias señales para acercarse:

  1. Enumerar ventanas de nivel superior visibles y con título → esos PIDs
     (y sus NOMBRES de proceso) son "apps en primer plano".
  2. Cualquier proceso cuyo NOMBRE coincida con uno en primer plano se
     excluye también, aunque ese PID puntual no tenga ventana — así no
     aparecen los 30 procesos renderer/GPU/helper de Chrome, Brave, Edge,
     VS Code, Steam, etc. por separado.
  3. Se excluye lo que corre desde `C:\\Windows\\...` (la plomería del
     sistema operativo vive ahí casi toda) y procesos de otros usuarios
     (servicios como SYSTEM).
  4. Se excluye por nombre: sufijos típicos de servicios/helpers/updaters
     (no son la "app" en sí, son su soporte) y una lista puntual de ruido
     conocido que se cuela igual.

No es 100% preciso (puede faltar alguna app con tray real, o colarse algo
que no lo tiene) — es una aproximación práctica, no una lectura real de la
bandeja del sistema (que Windows no expone).

Cerrar una app intenta primero `WM_CLOSE` a sus ventanas (aunque estén
ocultas) — el mismo mensaje que dispara Alt+F4 o el botón "X", dándole a
la app la chance de guardar/preguntar antes de salir — y si no reacciona
en unos segundos, la termina a la fuerza.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import psutil
import win32con
import win32gui
import win32process

WINDOWS_DIR = os.environ.get("WINDIR", r"C:\Windows").lower()

NOISE_SUFFIXES = (
    "service",
    "svc",
    "server",
    "broker",
    "updater",
    "helper",
    "agent",
    "host",
    "crashhandler",
    "crashhandler64",
    "redistservice",
)

# Ruido puntual que sobrevive a los filtros de arriba (nombres propios que
# no calzan con ningún sufijo genérico).
NOISE_PROCESS_NAMES = {
    "audiodg.exe",
    "memcompression",
    "registry",
    "system",
    "system idle process",
    "msmpeng.exe",
    "nissrv.exe",
    "nvcontainer.exe",
    "gamingservices.exe",
    "gamingservicesnet.exe",
    "edgegameassist.exe",
    "xboxpcappft.exe",
    "crashpad_handler.exe",
    "lsaiso.exe",
    "ngciso.exe",
    "ctfmon.exe",
    "sihost.exe",
    "spoolsv.exe",
    "taskhostw.exe",
    "unsecapp.exe",
    "wmiprvse.exe",
    "dllhost.exe",
    "backgroundtaskhost.exe",
    "conhost.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "bash.exe",
    "vmms.exe",
    "vmcompute.exe",
    "wslservice.exe",
    "openconsole.exe",
    "audiomixer.exe",  # esta misma app empaquetada
    "python.exe",  # esta misma app corriendo desde código fuente
    "pythonw.exe",
    "claude.exe",  # CLI de este mismo asistente, si queda corriendo
}


@dataclass
class BackgroundApp:
    pid: int
    name: str


def _foreground_pids_and_names() -> tuple[set[int], set[str]]:
    pids: set[int] = set()
    names: set[str] = set()

    def callback(hwnd: int, _extra) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER) != 0:
            return True  # ventana "hija"/popup de otra, no es una app propia
        if not win32gui.GetWindowText(hwnd):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        pids.add(pid)
        try:
            names.add(psutil.Process(pid).name().lower())
        except psutil.Error:
            pass
        return True

    win32gui.EnumWindows(callback, None)
    return pids, names


def _current_username() -> str | None:
    try:
        return psutil.Process(os.getpid()).username()
    except psutil.Error:
        return None


def _is_noise(name_lower: str, exe_path: str | None) -> bool:
    if name_lower in NOISE_PROCESS_NAMES:
        return True
    base = name_lower[:-4] if name_lower.endswith(".exe") else name_lower
    if base.endswith(NOISE_SUFFIXES):
        return True
    if exe_path and exe_path.lower().startswith(WINDOWS_DIR):
        return True
    return False


def list_background_apps() -> list[BackgroundApp]:
    foreground_pids, foreground_names = _foreground_pids_and_names()
    my_user = _current_username()

    apps: list[BackgroundApp] = []
    for proc in psutil.process_iter(["pid", "name", "username"]):
        info = proc.info
        pid = info["pid"]
        name = (info["name"] or "").strip()
        if not name or pid in foreground_pids:
            continue
        name_lower = name.lower()
        if name_lower in foreground_names:
            continue  # proceso auxiliar de una app que ya tiene ventana visible
        if my_user is not None and info["username"] not in (my_user, None):
            continue  # procesos de SYSTEM/otros usuarios: no son "mis" apps

        try:
            exe_path = proc.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            exe_path = None

        if _is_noise(name_lower, exe_path):
            continue

        display_name = name[:-4] if name_lower.endswith(".exe") else name
        apps.append(BackgroundApp(pid=pid, name=display_name))

    apps.sort(key=lambda a: a.name.lower())
    return apps


def close_app(pid: int, wait_seconds: float = 2.0) -> None:
    if not psutil.pid_exists(pid):
        return

    def callback(hwnd: int, _extra) -> bool:
        _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
        if win_pid == pid:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return True

    win32gui.EnumWindows(callback, None)

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return
        time.sleep(0.15)

    try:
        proc = psutil.Process(pid)
        proc.terminate()
        proc.wait(timeout=2)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
