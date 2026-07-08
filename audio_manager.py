"""Puente entre el Core Audio API de Windows (via pycaw/comtypes) y el
event loop de asyncio que usa FastAPI.

Los objetos COM que envuelve pycaw solo son seguros de usar desde el hilo
que llamo `CoInitialize()`. En vez de inicializar COM en cada llamada
(lento y propenso a errores si se mezcla con el hilo del event loop), este
módulo dedica UN hilo daemon que:

  1. Inicializa COM una sola vez y vive durante toda la ejecución del
     programa.
  2. Cada `POLL_INTERVAL_SECONDS` enumera las sesiones de audio activas de
     Windows y, si la lista cambió (una app abrió/cerró audio, o alguien
     cambió el volumen desde el Mixer de Windows), notifica al event loop
     principal mediante `on_state_change` (usando
     `asyncio.run_coroutine_threadsafe`).
  3. Atiende comandos (`set_volume`, `toggle_mute`) que llegan desde el
     WebSocket a través de una `queue.Queue` thread-safe, aplicándolos de
     inmediato sin esperar al próximo tick de polling.

Así el event loop de FastAPI nunca bloquea esperando a Windows, y pycaw
nunca es tocado desde dos hilos a la vez.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Callable

import comtypes
from pycaw.constants import AudioSessionState
from pycaw.pycaw import AudioUtilities

logger = logging.getLogger("audio_manager")

POLL_INTERVAL_SECONDS = 1.0


@dataclass(eq=True)
class AppVolumeState:
    """Snapshot serializable del estado de una app para enviar al cliente."""

    name: str
    volume: int
    muted: bool
    # True si Windows reporta ALGUNA sesión de este proceso como "Active"
    # (con un stream de audio abierto ahora mismo) — no es lo mismo que
    # "muted": una app pausada/silenciosa quedaría Inactive aunque no esté
    # muteada. Se usa para el efecto de "sonando" en el mixer.
    active: bool


class AudioSessionManager:
    """Administra el apartamento COM y expone una API thread-safe simple."""

    def __init__(self, on_state_change: Callable[[list[AppVolumeState]], None]):
        self._on_state_change = on_state_change
        self._commands: "queue.Queue[dict]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="AudioSessionThread", daemon=True
        )

        # Sesiones agrupadas por nombre de proceso: si una app abre varias
        # sesiones de audio (p. ej. Chrome con procesos hijos), todas se
        # controlan juntas con un solo fader, igual que el Mixer de Windows.
        self._sessions_by_name: dict[str, list] = {}

        self._lock = threading.Lock()
        self._last_state: list[AppVolumeState] = []

    # ---- API pública (llamable desde el event loop de asyncio) ------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2)

    def request_set_volume(self, name: str, volume: int) -> None:
        self._commands.put({"action": "set_volume", "name": name, "volume": volume})

    def request_toggle_mute(self, name: str) -> None:
        self._commands.put({"action": "toggle_mute", "name": name})

    def get_state_snapshot(self) -> list[AppVolumeState]:
        with self._lock:
            return list(self._last_state)

    # ---- cuerpo del hilo dedicado ------------------------------------------

    def _run(self) -> None:
        comtypes.CoInitialize()
        logger.info("Apartamento COM inicializado en el hilo de audio")
        try:
            while not self._stop_event.is_set():
                self._drain_commands()
                self._poll_sessions()
                self._stop_event.wait(POLL_INTERVAL_SECONDS)
        finally:
            comtypes.CoUninitialize()

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                self._apply_command(cmd)
            except Exception:
                logger.exception("No se pudo aplicar el comando %s", cmd)

    def _apply_command(self, cmd: dict) -> None:
        name = cmd["name"]
        sessions = self._sessions_by_name.get(name, [])
        if not sessions:
            # La app pudo haber cerrado justo antes de recibir el comando.
            return

        for session in list(sessions):
            try:
                if cmd["action"] == "set_volume":
                    level = max(0, min(100, cmd["volume"])) / 100.0
                    session.SimpleAudioVolume.SetMasterVolume(level, None)
                elif cmd["action"] == "toggle_mute":
                    currently_muted = bool(session.SimpleAudioVolume.GetMute())
                    session.SimpleAudioVolume.SetMute(not currently_muted, None)
            except comtypes.COMError:
                # El proceso murió entre el último poll y este comando:
                # descartamos la referencia; el próximo poll reconcilia todo.
                logger.debug("Sesión de %s ya no es válida, se descarta", name)
                sessions.remove(session)

    def _poll_sessions(self) -> None:
        try:
            raw_sessions = AudioUtilities.GetAllSessions()
        except comtypes.COMError:
            logger.warning("No se pudo enumerar sesiones de audio en este tick")
            return

        grouped: dict[str, list] = {}
        for session in raw_sessions:
            process = session.Process
            if process is None:
                continue  # sonidos del sistema / sesión sin proceso dueño
            try:
                name = process.name()
            except Exception:
                continue  # proceso murió justo al leer su nombre
            grouped.setdefault(name, []).append(session)

        self._sessions_by_name = grouped

        new_state: list[AppVolumeState] = []
        for name, sessions in grouped.items():
            try:
                primary = sessions[0]
                volume = round(primary.SimpleAudioVolume.GetMasterVolume() * 100)
                muted = bool(primary.SimpleAudioVolume.GetMute())
                active = any(s.State == AudioSessionState.Active for s in sessions)
            except comtypes.COMError:
                continue
            new_state.append(AppVolumeState(name=name, volume=volume, muted=muted, active=active))

        new_state.sort(key=lambda a: a.name.lower())

        with self._lock:
            changed = new_state != self._last_state
            self._last_state = new_state

        if changed:
            self._on_state_change(new_state)
