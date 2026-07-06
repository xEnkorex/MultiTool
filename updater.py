"""Chequeo de actualizaciones para la app empaquetada.

Compara la versión local (archivo `VERSION` bundleado) contra la del
archivo `VERSION` en la rama `main` del repo público en GitHub, leído
directo via `raw.githubusercontent.com` — sin API ni credenciales, así
que solo funciona mientras el repo sea público. Si el repo pasa a
privado, esto simplemente deja de encontrar actualizaciones (falla la
conexión) sin romper nada más.

No es un webhook: un webhook necesitaría que esta PC exponga un endpoint
público para que GitHub le avise, lo cual no aplica a una app de
escritorio. En cambio, esto es polling (chequeo periódico) desde acá.

Para publicar una actualización: subir el `VERSION` bumpeado a `main`, y
crear un Release en GitHub (con el `.exe` nuevo adjunto) para que el
"Buscar actualizaciones" tenga a dónde mandar al usuario a descargarlo.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request

import paths

logger = logging.getLogger("updater")

REPO_OWNER = "xEnkorex"
REPO_NAME = "MultiTool"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/VERSION"
RELEASES_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases"


def get_current_version() -> str:
    version_file = paths.resource_dir() / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def _parse(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in version.strip().split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_for_update(timeout: float = 6.0) -> str | None:
    """Devuelve la versión remota si es más nueva que la local, o `None`
    si estamos al día o no se pudo chequear (sin internet, repo
    inaccesible, etc. — cualquier falla acá es silenciosa a propósito)."""
    try:
        with urllib.request.urlopen(VERSION_URL, timeout=timeout) as resp:
            remote = resp.read().decode("utf-8").strip()
    except (urllib.error.URLError, OSError, TimeoutError):
        return None

    if not remote:
        return None

    current = get_current_version()
    if _parse(remote) > _parse(current):
        return remote
    return None
