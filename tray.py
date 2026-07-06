"""Punto de entrada para la app empaquetada: el mismo servidor FastAPI de
`server.py`, pero minimizado a un ícono en la bandeja del sistema en vez
de una consola visible.

Uso normal (con Python instalado, sin empaquetar):
    python tray.py

Para el .exe, ver el comando de PyInstaller en el README.
"""

from __future__ import annotations

import os
import sys

# En build --noconsole (PyInstaller "windowed"), sys.stdout/stderr son
# None: cualquier logging.StreamHandler (el nuestro o el que arma Uvicorn
# internamente) revienta al primer log y mata el hilo del servidor sin
# dejar rastro. Hay que taparlo ANTES de importar server/uvicorn, que es
# cuando se configuran esos handlers.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import logging
import socket
import subprocess
import threading
import time
import webbrowser

import pystray
import uvicorn

import icon as icon_module
import paths
import updater
from server import app

logger = logging.getLogger("tray")

PORT = 8000
UPDATE_CHECK_INTERVAL_HOURS = 6


def _lan_ip() -> str:
    """Mejor esfuerzo para mostrarle al usuario su IP en la LAN (no abre
    conexión real: UDP a una IP pública solo para que el SO elija la
    interfaz de salida correcta)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


_tk_root = None  # raíz oculta de Tkinter; vive en su propio hilo (ver _start_tk_thread)
_tk_ready = threading.Event()


def _start_tk_thread() -> None:
    """Arranca un hilo dedicado con una raíz de Tkinter oculta y su propio
    mainloop. Es el patrón correcto para usar Tkinter junto a otro loop de
    eventos (el de pystray, que bloquea el hilo principal): todo lo que se
    quiera mostrar después se agenda con `root.after(0, fn)` desde
    cualquier otro hilo, nunca tocando widgets directamente fuera de este.
    """

    def run() -> None:
        global _tk_root
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()  # la raíz en sí nunca se muestra, solo sus Toplevel
        try:
            root.iconbitmap(str(paths.resource_dir() / "assets" / "icon.ico"))
        except tk.TclError:
            pass  # sin ícono no es crítico, seguimos con el default de Tk
        _tk_root = root
        _tk_ready.set()
        root.mainloop()

    threading.Thread(target=run, daemon=True).start()
    _tk_ready.wait(timeout=5)


def _build_info_window(lan_url: str) -> None:
    import tkinter as tk

    from PIL import ImageTk
    import qrcode

    win = tk.Toplevel(_tk_root)
    win.title("Audio Mixer")
    win.configure(bg="#0a0a0f")
    win.resizable(False, False)
    win.attributes("-topmost", True)

    qr_img = qrcode.make(lan_url, border=2).resize((220, 220))
    qr_photo = ImageTk.PhotoImage(qr_img)
    win._qr_photo_ref = qr_photo  # si no se guarda una referencia, el GC la borra

    tk.Label(
        win,
        text="Escaneá para abrir en tu teléfono",
        fg="#7a7a8c",
        bg="#0a0a0f",
        font=("Segoe UI", 9),
    ).pack(padx=28, pady=(20, 10))
    tk.Label(win, image=qr_photo, bg="#0a0a0f", bd=0).pack(padx=28)
    tk.Label(
        win,
        text=lan_url,
        fg="#00fff2",
        bg="#0a0a0f",
        font=("Segoe UI", 12, "bold"),
    ).pack(padx=28, pady=(14, 22))

    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    win.lift()
    win.focus_force()


def _show_update_available(new_version: str) -> None:
    import tkinter.messagebox as messagebox

    should_open = messagebox.askyesno(
        "Audio Mixer — actualización disponible",
        f"Hay una nueva versión: v{new_version}\n"
        f"(la tuya: v{updater.get_current_version()})\n\n"
        "¿Abrir la página de descargas?",
    )
    if should_open:
        webbrowser.open(updater.RELEASES_URL)


def _show_up_to_date() -> None:
    import tkinter.messagebox as messagebox

    messagebox.showinfo(
        "Audio Mixer", f"Estás al día (v{updater.get_current_version()})."
    )


def main() -> None:
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning")
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    _start_tk_thread()

    lan_ip = _lan_ip()
    local_url = f"http://localhost:{PORT}"
    lan_url = f"http://{lan_ip}:{PORT}"
    logger.info("Servidor disponible en %s (LAN: %s)", local_url, lan_url)

    def open_panel(_icon: pystray.Icon | None = None, _item: object = None) -> None:
        webbrowser.open(local_url)

    def show_info(_icon: pystray.Icon | None = None, _item: object = None) -> None:
        if _tk_root is None:
            logger.warning("No se pudo mostrar la ventana de info: Tkinter no inició")
            return
        _tk_root.after(0, _build_info_window, lan_url)

    def copy_lan_url(_icon: pystray.Icon | None = None, _item: object = None) -> None:
        # creationflags evita que clip.exe abra su propia consola visible
        # (el proceso padre no tiene consola por --noconsole).
        subprocess.run(
            ["clip"],
            input=lan_url.encode("utf-8"),
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def quit_app(tray_icon: pystray.Icon, _item: object = None) -> None:
        server.should_exit = True
        tray_icon.stop()

    def check_updates_now(_icon: pystray.Icon | None = None, _item: object = None) -> None:
        def worker() -> None:
            new_version = updater.check_for_update()
            if _tk_root is None:
                return
            if new_version:
                _tk_root.after(0, _show_update_available, new_version)
            else:
                _tk_root.after(0, _show_up_to_date)

        threading.Thread(target=worker, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Abrir panel", open_panel, default=True),
        pystray.MenuItem("Info / código QR", show_info),
        pystray.MenuItem(f"Copiar URL de red ({lan_ip})", copy_lan_url),
        pystray.MenuItem("Buscar actualizaciones", check_updates_now),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Salir", quit_app),
    )

    tray_icon = pystray.Icon("audio_mixer", icon_module.make_icon(64), "Audio Mixer", menu)

    def update_check_loop() -> None:
        # Un chequeo al ratito de arrancar (no apenas inicia, para no competir
        # con el arranque del server/tray) y después cada N horas.
        time.sleep(30)
        while True:
            new_version = updater.check_for_update()
            if new_version:
                tray_icon.notify(
                    f"Versión {new_version} disponible (tenés {updater.get_current_version()}). "
                    "Buscar actualizaciones > para descargarla.",
                    "Audio Mixer",
                )
            time.sleep(UPDATE_CHECK_INTERVAL_HOURS * 3600)

    threading.Thread(target=update_check_loop, daemon=True).start()

    tray_icon.run()


if __name__ == "__main__":
    main()
