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

import psutil
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
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
import pinned_store
import app_icons
import window_focus

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

# Puede haber más de un navegador con la extensión instalada a la vez (ej.
# Chrome y Brave abiertos juntos) — cada uno abre su propia conexión acá,
# identificada por el nombre de proceso que le detectamos (o una key
# sintética si no se pudo detectar). Antes esto era un solo par de
# variables globales, así que el segundo navegador en conectar pisaba por
# completo las pestañas del primero en vez de sumarse.
extension_sessions: dict[str, dict] = {}

# El service worker de la extensión (Manifest V3) se apaga solo por
# inactividad y se reconecta cuando puede — normalmente enseguida, pero en
# el peor caso (nada despierta al service worker antes) depende de una
# alarma que la extensión reprograma cada 1 minuto (ver background.js).
# Un margen más corto que eso dejaría el mixer sin esas pestañas por un
# rato cada vez que le toque ese peor caso.
EXTENSION_RECONNECT_GRACE_SECONDS = 75


def _all_extension_tabs() -> list[dict]:
    # El tabId de Chrome son solo únicos DENTRO de ese navegador — con dos
    # sesiones conectadas (Chrome y Brave a la vez) es perfectamente
    # posible que ambos tengan, por ejemplo, una pestaña con id=5. Por eso
    # acá se le antepone la sesión al id antes de mandarlo al frontend: es
    # un identificador opaco para el cliente, pero le alcanza a
    # `_resolve_tab_target` para saber a qué navegador reenviar un comando
    # sin ambigüedad.
    tabs: list[dict] = []
    for session_key, session in extension_sessions.items():
        for tab in session["tabs"]:
            tabs.append({**tab, "id": f"{session_key}:{tab['id']}"})
    return tabs


async def _clear_session_tabs_after_grace(session_key: str) -> None:
    await asyncio.sleep(EXTENSION_RECONNECT_GRACE_SECONDS)
    session = extension_sessions.get(session_key)
    if session is None or session["ws"] is not None:
        return  # se reconectó (o ya se limpió) antes de que se cumpliera el margen
    del extension_sessions[session_key]
    assert audio_manager is not None
    await connections.broadcast(_state_to_payload(audio_manager.get_state_snapshot(), _all_extension_tabs()))


def _split_composite_tab_id(composite_tab_id: str) -> tuple[str, int] | None:
    """Deshace el `id` compuesto (`session_key:tabId`) que arma
    `_all_extension_tabs`, sin resolver todavía a qué WebSocket corresponde."""
    session_key, sep, raw_id = composite_tab_id.rpartition(":")
    if not sep or not raw_id.lstrip("-").isdigit():
        return None
    return session_key, int(raw_id)


def _resolve_tab_target(composite_tab_id: str) -> tuple[WebSocket, int] | None:
    """A qué conexión de extensión (y con qué tabId real) reenviarle un
    comando para la pestaña identificada por ese id compuesto."""
    split = _split_composite_tab_id(composite_tab_id)
    if split is None:
        return None
    session_key, raw_id = split
    session = extension_sessions.get(session_key)
    if session is None or session["ws"] is None:
        return None
    return session["ws"], raw_id


def _strip_exe(name: str) -> str:
    return name[:-4] if name.lower().endswith(".exe") else name


def _detect_browser_process_name(client_port: int) -> str | None:
    """Identifica qué .exe abrió la conexión WebSocket de la extensión,
    mirando la tabla de sockets TCP del sistema en vez de confiar en que el
    navegador se autoidentifique desde JavaScript.

    Se probó primero pedirle al propio navegador que se identifique (vía
    `navigator.brave.isBrave()` desde la extensión) pero esa API no está
    disponible en todas las instalaciones de Brave — quedaba siempre en el
    fallback "Chrome" y las pestañas no anidaban. Esto es más confiable:
    del lado del proceso que abrió la conexión hacia nuestro puerto, su
    extremo local es exactamente `client_port` (el puerto efímero que
    Starlette reporta como `websocket.client`), así que sea cual sea el
    navegador (Brave, Chrome, Edge...) esto identifica el .exe real sin
    adivinar nada.
    """
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.pid and conn.raddr and conn.laddr and conn.laddr.port == client_port and conn.raddr.port == 8000:
                return psutil.Process(conn.pid).name()
    except (psutil.Error, PermissionError, OSError):
        pass
    return None


# Apps "pineadas": quedan visibles en el mixer aunque Windows no les tenga
# una sesión de audio activa ahora mismo (ver pinned_store.py). Se cargan
# una sola vez al arrancar; toggle_pin_app las actualiza en memoria y en disco.
pinned_apps: set[str] = pinned_store.load_pinned()


def _state_to_payload(state: list[AppVolumeState], tabs: list[dict]) -> dict:
    apps_payload = []
    seen_names = set()
    for a in state:
        seen_names.add(a.name)
        apps_payload.append({**asdict(a), "pinned": a.name in pinned_apps, "available": True})

    # Una app pineada que ya no tiene sesión de audio (cerrada, o Windows
    # todavía no le abrió una) igual se manda, sin volumen real: el
    # frontend la muestra atenuada y sin controles hasta que reaparezca.
    for name in sorted(pinned_apps - seen_names):
        apps_payload.append(
            {"name": name, "volume": None, "muted": False, "active": False, "pinned": True, "available": False}
        )

    return {"type": "state", "apps": apps_payload, "tabs": tabs}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global audio_manager
    loop = asyncio.get_running_loop()

    def on_state_change(state: list[AppVolumeState]) -> None:
        # Se ejecuta en el hilo de audio: saltamos de vuelta al event loop
        # de asyncio para poder hacer el broadcast (await) de forma segura.
        asyncio.run_coroutine_threadsafe(
            connections.broadcast(_state_to_payload(state, _all_extension_tabs())), loop
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


@app.get("/api/app-icon/{process_name}")
async def get_app_icon(process_name: str) -> Response:
    # La extracción usa GDI (win32gui/win32ui), bloqueante — se corre en un
    # hilo aparte para no trabar el event loop mientras el mixer sondea íconos.
    icon_png = await asyncio.to_thread(app_icons.get_icon_png, process_name)
    if icon_png is None:
        raise HTTPException(404, "No se pudo extraer el ícono de esa app")
    return Response(
        content=icon_png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/api/focus-app/{process_name}")
async def focus_app(process_name: str) -> dict:
    # EnumWindows + AttachThreadInput son llamadas Win32 bloqueantes; se
    # corren en un hilo aparte para no trabar el event loop.
    found = await asyncio.to_thread(window_focus.focus_app, process_name)
    if not found:
        raise HTTPException(404, "No se encontró una ventana visible para esa app")
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
    global pinned_apps
    await connections.connect(websocket)
    logger.info("Cliente conectado: %s", websocket.client)

    assert audio_manager is not None
    # Estado inicial inmediato: no esperamos al próximo tick de polling.
    snapshot = audio_manager.get_state_snapshot()
    await websocket.send_text(json.dumps(_state_to_payload(snapshot, _all_extension_tabs())))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = data.get("type")

            if action == "set_volume":
                name = data.get("name")
                volume = data.get("volume")
                if name and isinstance(volume, (int, float)):
                    audio_manager.request_set_volume(name, int(volume))
            elif action == "toggle_mute":
                name = data.get("name")
                if name:
                    audio_manager.request_toggle_mute(name)
            elif action == "set_tab_volume":
                tab_id = data.get("tabId")
                volume = data.get("volume")
                target = _resolve_tab_target(tab_id) if isinstance(tab_id, str) else None
                if target is not None and isinstance(volume, (int, float)):
                    target_ws, raw_tab_id = target
                    volume_int = int(volume)
                    await target_ws.send_text(
                        json.dumps({"type": "set_tab_volume", "tabId": raw_tab_id, "volume": volume_int})
                    )
                    # El volumen de la pestaña y el del navegador se
                    # multiplican (no hay forma de que uno "gane" del todo
                    # sobre el otro) — así que si pedís más de lo que el
                    # navegador tiene puesto ahora, lo acompañamos subiendo
                    # el navegador a ese mismo nivel, si no una pestaña al
                    # 100% seguiría sonando bajito con el navegador al 20%.
                    split = _split_composite_tab_id(tab_id)
                    if split is not None:
                        session_key, _ = split
                        browser_volume = next(
                            (a.volume for a in audio_manager.get_state_snapshot() if a.name == session_key),
                            None,
                        )
                        if browser_volume is not None and volume_int > browser_volume:
                            audio_manager.request_set_volume(session_key, volume_int)
            elif action == "toggle_tab_mute":
                tab_id = data.get("tabId")
                target = _resolve_tab_target(tab_id) if isinstance(tab_id, str) else None
                if target is not None:
                    target_ws, raw_tab_id = target
                    await target_ws.send_text(json.dumps({"type": "toggle_tab_mute", "tabId": raw_tab_id}))
            elif action == "toggle_pin_app":
                name = data.get("name")
                if name:
                    pinned_apps = pinned_store.toggle_pinned(name)
                    await connections.broadcast(
                        _state_to_payload(audio_manager.get_state_snapshot(), _all_extension_tabs())
                    )
            elif action == "toggle_pin_tab":
                tab_id = data.get("tabId")
                target = _resolve_tab_target(tab_id) if isinstance(tab_id, str) else None
                if target is not None:
                    target_ws, raw_tab_id = target
                    await target_ws.send_text(json.dumps({"type": "toggle_pin_tab", "tabId": raw_tab_id}))
            elif action == "focus_tab":
                tab_id = data.get("tabId")
                target = _resolve_tab_target(tab_id) if isinstance(tab_id, str) else None
                if target is not None:
                    target_ws, raw_tab_id = target
                    await target_ws.send_text(json.dumps({"type": "focus_tab", "tabId": raw_tab_id}))
    except WebSocketDisconnect:
        logger.info("Cliente desconectado: %s", websocket.client)
    finally:
        connections.disconnect(websocket)


@app.websocket("/ws/extension")
async def extension_websocket(websocket: WebSocket) -> None:
    """Canal separado para la extensión de navegador (puede haber varias
    conexiones activas a la vez, una por cada navegador con la extensión
    instalada): le reporta a AudioMixer qué pestañas suenan ahora mismo,
    y recibe de vuelta comandos de volumen/mute para reenviarle al navegador."""
    await websocket.accept()

    browser_process_name = None
    if websocket.client is not None:
        browser_process_name = _detect_browser_process_name(websocket.client.port)
        if browser_process_name:
            logger.info("Navegador detectado para esta extensión: %s", browser_process_name)

    # Sin detección (rarísimo: fallaría solo si psutil no puede leer la
    # tabla TCP) cada conexión es su propia sesión efímera en vez de
    # compartir identidad con otras — sigue funcionando, solo pierde la
    # gracia de reconexión si el service worker se reinicia.
    session_key = browser_process_name or f"conn:{id(websocket)}"

    session = extension_sessions.get(session_key)
    if session is not None:
        # Reconexión de un navegador ya conocido (típicamente el service
        # worker reiniciándose): se reusa la sesión y sus últimas pestañas
        # conocidas en vez de resetear a [] — si no, cualquier broadcast
        # que cayera justo en el huequito entre "conectó" y "mandó su
        # primer tabs_state" mostraría esas pestañas vacías por un instante.
        if session.get("clear_task") is not None:
            session["clear_task"].cancel()
        session["ws"] = websocket
        session["clear_task"] = None
    else:
        session = {"ws": websocket, "tabs": [], "clear_task": None}
        extension_sessions[session_key] = session
    logger.info("Extensión de navegador conectada (%s): %s", session_key, websocket.client)

    assert audio_manager is not None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "tabs_state":
                tabs = data.get("tabs", [])
                if browser_process_name:
                    # El proceso real (detectado por socket) le gana a lo
                    # que la extensión haya adivinado desde JavaScript.
                    label = _strip_exe(browser_process_name)
                    for tab in tabs:
                        tab["browser"] = label

                # Una pestaña recién descubierta arranca al volumen que la
                # extensión le puso por default (100%) — pero tiene más
                # sentido que arranque calzada con el volumen actual del
                # navegador, así el primer sonido no pega un salto. De ahí
                # en más el fader de la pestaña manda solo (son capas que
                # se multiplican, no hay forma de que "ignore" al del
                # navegador — lo más parecido es arrancar igualados).
                previous_ids = {t.get("id") for t in session["tabs"]}
                new_ids = {t.get("id") for t in tabs} - previous_ids
                if new_ids and browser_process_name:
                    app_volume = next(
                        (a.volume for a in audio_manager.get_state_snapshot() if a.name == browser_process_name),
                        None,
                    )
                    if app_volume is not None:
                        for tab in tabs:
                            if tab.get("id") in new_ids:
                                tab["volume"] = app_volume
                        for new_id in new_ids:
                            await websocket.send_text(
                                json.dumps({"type": "set_tab_volume", "tabId": new_id, "volume": app_volume})
                            )

                session["tabs"] = tabs
                await connections.broadcast(
                    _state_to_payload(audio_manager.get_state_snapshot(), _all_extension_tabs())
                )
    except WebSocketDisconnect:
        logger.info("Extensión de navegador desconectada (%s): %s", session_key, websocket.client)
    finally:
        if extension_sessions.get(session_key) is session:
            session["ws"] = None
            # El service worker de la extensión (Manifest V3) se apaga solo
            # por inactividad cada 10-30s y se reconecta enseguida — sin
            # este margen, cada uno de esos cortes vaciaba de golpe las
            # pestañas de ESTE navegador para volver a llenarse un instante
            # después. Se espera un rato antes de asumir que cerró de verdad.
            session["clear_task"] = asyncio.create_task(_clear_session_tabs_after_grace(session_key))


if __name__ == "__main__":
    import uvicorn

    # 0.0.0.0 para aceptar conexiones desde cualquier dispositivo de la LAN
    # (el móvil), no solo desde localhost.
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
