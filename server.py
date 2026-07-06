"""Servidor FastAPI: expone control de volumen por aplicación de Windows
a través de WebSockets, para usar con el panel táctil (móvil) o el navegador.

Arranque:
    python server.py

Luego, desde el navegador del PC:  http://localhost:8000
Desde el móvil (misma red Wi-Fi): http://<IP-LOCAL-DEL-PC>:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from audio_manager import AppVolumeState, AudioSessionManager
from layout_store import LayoutItem
import layout_store
from launcher_store import LauncherSlot
import launcher_store
from shortcut_store import ShortcutSlot
import shortcut_store
import shortcut_runner
import background_apps
import logitech_battery
import bt_battery
import paths

ALLOWED_ICON_EXTENSIONS = {".ico", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg"}

_log_handlers: list[logging.Handler] = []
if sys.stderr is not None:
    # En apps --noconsole (PyInstaller "windowed") sys.stderr es None y
    # StreamHandler reventaría al primer log, matando el hilo del server.
    _log_handlers.append(logging.StreamHandler())
if paths.is_frozen():
    # Empaquetado no tiene consola visible (--noconsole): sin esto, un
    # error sería invisible. El log queda junto a la config, en %APPDATA%.
    _log_handlers.append(logging.FileHandler(paths.data_dir() / "audiomixer.log", encoding="utf-8"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("server")

STATIC_DIR = paths.resource_dir() / "static"


class ConnectionManager:
    """Lleva el registro de clientes WebSocket conectados y les reenvía JSON."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        message = json.dumps(payload)
        dead: list[WebSocket] = []
        for client in self._clients:
            try:
                await client.send_text(message)
            except Exception:
                dead.append(client)
        for client in dead:
            self.disconnect(client)


connections = ConnectionManager()
audio_manager: AudioSessionManager | None = None


def _state_to_payload(state: list[AppVolumeState]) -> dict:
    return {"type": "state", "apps": [asdict(a) for a in state]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global audio_manager
    loop = asyncio.get_running_loop()

    def on_state_change(state: list[AppVolumeState]) -> None:
        # Se ejecuta en el hilo de audio: saltamos de vuelta al event loop
        # de asyncio para poder hacer el broadcast (await) de forma segura.
        asyncio.run_coroutine_threadsafe(
            connections.broadcast(_state_to_payload(state)), loop
        )

    audio_manager = AudioSessionManager(on_state_change=on_state_change)
    audio_manager.start()
    logger.info("Audio session manager iniciado")

    yield

    audio_manager.stop()
    logger.info("Audio session manager detenido")


app = FastAPI(title="PC Audio Mixer", lifespan=lifespan)


@app.middleware("http")
async def no_cache_frontend(request: Request, call_next):
    # HTML/CSS/JS/íconos se sirven siempre frescos: ya nos pasó más de una
    # vez que el navegador se queda pegado a una versión vieja (íconos
    # SVG, o el propio app.js) y toca explicarle al usuario que haga un
    # hard-refresh. Más simple: no dejar que el navegador cachee nada de
    # esto — son archivos chicos, no vale la pena el riesgo de staleness.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/layout")
async def get_layout() -> list[LayoutItem]:
    return layout_store.load_layout() or []


@app.put("/api/layout")
async def put_layout(items: list[LayoutItem]) -> dict:
    layout_store.save_layout(items)
    return {"ok": True}


class LauncherUpdateRequest(BaseModel):
    name: str
    path: str
    icon: str | None = None
    icon_path: str | None = None


@app.get("/api/launcher")
async def get_launcher_slots() -> list[LauncherSlot]:
    return launcher_store.load_slots()


@app.post("/api/launcher")
async def create_launcher_slot(body: LauncherUpdateRequest) -> LauncherSlot:
    name = body.name.strip()
    path = body.path.strip()
    icon = (body.icon or "").strip() or None
    icon_path = (body.icon_path or "").strip() or None
    if not name or not path:
        raise HTTPException(400, "Nombre y ruta son obligatorios")
    return launcher_store.add_slot(name, path, icon, icon_path)


@app.put("/api/launcher/{slot_id}")
async def update_launcher_slot(slot_id: int, body: LauncherUpdateRequest) -> LauncherSlot:
    name = body.name.strip()
    path = body.path.strip()
    icon = (body.icon or "").strip() or None
    icon_path = (body.icon_path or "").strip() or None
    if not name or not path:
        raise HTTPException(400, "Nombre y ruta son obligatorios")
    try:
        return launcher_store.save_slot(slot_id, name, path, icon, icon_path)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.delete("/api/launcher/{slot_id}")
async def delete_launcher_slot(slot_id: int) -> dict:
    launcher_store.delete_slot(slot_id)
    return {"ok": True}


@app.post("/api/launcher/{slot_id}/launch")
async def launch_launcher_slot(slot_id: int) -> dict:
    slot = launcher_store.get_slot(slot_id)
    if not slot:
        raise HTTPException(404, "Slot inválido")
    try:
        launcher_store.launch(slot.path)
    except FileNotFoundError:
        raise HTTPException(404, f"No se encontró la ruta: {slot.path}")
    except OSError as exc:
        raise HTTPException(500, f"No se pudo iniciar la app: {exc}")
    return {"ok": True}


def _serve_icon_file(icon_path: str | None) -> FileResponse:
    if not icon_path:
        raise HTTPException(404, "Sin ícono personalizado")

    path = Path(os.path.expandvars(icon_path))
    if path.suffix.lower() not in ALLOWED_ICON_EXTENSIONS or not path.is_file():
        raise HTTPException(404, "Ícono no encontrado")

    media_type = "image/x-icon" if path.suffix.lower() == ".ico" else mimetypes.guess_type(str(path))[0]
    return FileResponse(path, media_type=media_type or "application/octet-stream")


@app.get("/api/launcher/{slot_id}/icon")
async def get_launcher_icon(slot_id: int) -> FileResponse:
    slot = launcher_store.get_slot(slot_id)
    return _serve_icon_file(slot.icon_path if slot else None)


class ShortcutUpdateRequest(BaseModel):
    name: str
    keys: str
    icon: str | None = None
    icon_path: str | None = None


@app.get("/api/shortcuts")
async def get_shortcuts() -> list[ShortcutSlot]:
    return shortcut_store.load_slots()


@app.post("/api/shortcuts")
async def create_shortcut(body: ShortcutUpdateRequest) -> ShortcutSlot:
    name = body.name.strip()
    keys = body.keys.strip()
    icon = (body.icon or "").strip() or None
    icon_path = (body.icon_path or "").strip() or None
    if not name or not keys:
        raise HTTPException(400, "Nombre y combinación son obligatorios")
    return shortcut_store.add_slot(name, keys, icon, icon_path)


@app.put("/api/shortcuts/{slot_id}")
async def update_shortcut(slot_id: int, body: ShortcutUpdateRequest) -> ShortcutSlot:
    name = body.name.strip()
    keys = body.keys.strip()
    icon = (body.icon or "").strip() or None
    icon_path = (body.icon_path or "").strip() or None
    if not name or not keys:
        raise HTTPException(400, "Nombre y combinación son obligatorios")
    try:
        return shortcut_store.save_slot(slot_id, name, keys, icon, icon_path)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.delete("/api/shortcuts/{slot_id}")
async def delete_shortcut(slot_id: int) -> dict:
    shortcut_store.delete_slot(slot_id)
    return {"ok": True}


@app.get("/api/shortcuts/{slot_id}/icon")
async def get_shortcut_icon(slot_id: int) -> FileResponse:
    slot = shortcut_store.get_slot(slot_id)
    return _serve_icon_file(slot.icon_path if slot else None)


@app.post("/api/shortcuts/{slot_id}/trigger")
async def trigger_shortcut(slot_id: int) -> dict:
    slot = shortcut_store.get_slot(slot_id)
    if not slot:
        raise HTTPException(404, "Shortcut inválido")
    try:
        await asyncio.to_thread(shortcut_runner.trigger, slot.keys)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"No se pudo enviar el shortcut: {exc}")
    return {"ok": True}


class BackgroundAppResponse(BaseModel):
    pid: int
    name: str


@app.get("/api/background-apps")
async def get_background_apps() -> list[BackgroundAppResponse]:
    apps = await asyncio.to_thread(background_apps.list_background_apps)
    return [BackgroundAppResponse(pid=a.pid, name=a.name) for a in apps]


@app.post("/api/background-apps/{pid}/close")
async def close_background_app(pid: int) -> dict:
    await asyncio.to_thread(background_apps.close_app, pid)
    return {"ok": True}


@app.get("/api/logitech/battery")
async def get_logitech_battery() -> dict:
    # read_battery() hace I/O HID bloqueante (con timeouts de hasta ~150ms
    # por intento); se corre en un hilo aparte para no bloquear el event loop.
    battery = await asyncio.to_thread(logitech_battery.read_battery)
    if battery is None:
        raise HTTPException(404, "No se encontró un dispositivo Logitech con batería soportada")
    return {"percentage": battery.percentage, "charging": battery.charging}


@app.get("/api/headset/battery")
async def get_headset_battery() -> dict:
    # subprocess bloqueante (arranca powershell.exe); se corre en un hilo
    # aparte para no bloquear el event loop / WebSocket del mixer.
    percentage = await asyncio.to_thread(bt_battery.read_battery, "H510-PRO")
    if percentage is None:
        raise HTTPException(404, "No se encontró batería reportada por el H510-PRO (Bluetooth)")
    return {"percentage": percentage}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await connections.connect(websocket)
    logger.info("Cliente conectado: %s", websocket.client)

    assert audio_manager is not None
    # Estado inicial inmediato: no esperamos al próximo tick de polling.
    snapshot = audio_manager.get_state_snapshot()
    await websocket.send_text(json.dumps(_state_to_payload(snapshot)))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = data.get("type")
            name = data.get("name")
            if not name:
                continue

            if action == "set_volume":
                volume = data.get("volume")
                if isinstance(volume, (int, float)):
                    audio_manager.request_set_volume(name, int(volume))
            elif action == "toggle_mute":
                audio_manager.request_toggle_mute(name)
    except WebSocketDisconnect:
        logger.info("Cliente desconectado: %s", websocket.client)
    finally:
        connections.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn

    # 0.0.0.0 para aceptar conexiones desde cualquier dispositivo de la LAN
    # (el móvil), no solo desde localhost.
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
