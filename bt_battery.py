"""Lectura de batería de dispositivos Bluetooth clásicos (no HID++, no BLE)
a través de la propiedad estándar de Windows `DEVPKEY_Device_BatteryLevel`.

Usado para el Redragon Zeus Pro (H510-PRO) emparejado por Bluetooth: el
nivel de batería viaja como un indicador del perfil Hands-Free (HFP,
`{0000111E-...}`), no del perfil de audio A2DP. Windows lo recibe y lo
guarda en esa propiedad PnP estándar — es la misma que usa la página de
Configuración > Bluetooth y dispositivos para mostrar el ícono de batería
junto a un dispositivo emparejado, así que no hace falta hablar HID++/GATT
a mano como con el mouse.

Se consulta vía PowerShell (`Get-PnpDeviceProperty`) porque es la forma
soportada y documentada de leer `DEVPKEY_*` desde fuera de C++/SetupAPI, y
la frecuencia de consulta es baja (la UI hace polling cada ~60s), así que
el costo de arrancar `powershell.exe` por consulta es aceptable.
"""

from __future__ import annotations

import json
import subprocess
from typing import Optional

DEVPKEY_BATTERY_LEVEL = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2"

_SCRIPT_TEMPLATE = """
Get-PnpDevice | Where-Object {{ $_.FriendlyName -match "{name_pattern}" }} | ForEach-Object {{
  $bat = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName "{devpkey}" -ErrorAction SilentlyContinue
  if ($bat -and $null -ne $bat.Data) {{
    [PSCustomObject]@{{ FriendlyName = $_.FriendlyName; Battery = $bat.Data }}
  }}
}} | ConvertTo-Json -Compress
"""


def read_battery(name_pattern: str, timeout: float = 20.0) -> Optional[int]:
    """Busca dispositivos PnP cuyo nombre matchee `name_pattern` (regex de
    PowerShell) y devuelve el primer nivel de batería (0-100) encontrado
    entre sus distintas entradas (un dispositivo Bluetooth aparece como
    varias entradas PnP, una por perfil: audio, hands-free, AVRCP, etc.).
    Devuelve None si no se encontró ninguna con batería reportada.
    """
    script = _SCRIPT_TEMPLATE.format(name_pattern=name_pattern, devpkey=DEVPKEY_BATTERY_LEVEL)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            # Sin esto, cada llamada abre una consola visible propia:
            # el proceso padre (la app empaquetada) no tiene consola
            # (--noconsole), así que Windows le crea una nueva a
            # powershell.exe si no se lo prohibimos explícitamente.
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    output = result.stdout.strip()
    if not output:
        return None

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None

    entries = data if isinstance(data, list) else [data]
    for entry in entries:
        battery = entry.get("Battery")
        if isinstance(battery, (int, float)):
            return int(battery)
    return None
