"""Lectura de batería de dispositivos Logitech via HID++ 2.0.

Mismo protocolo que usa Solaar (https://github.com/pwr-Solaar/Solaar) para
mostrar la batería de mouses/teclados Logitech conectados por receptor
(Unifying/Bolt) o USB directo. Probado con un MX Master 3S vía receptor
Logi Bolt.

Protocolo (simplificado a lo que necesitamos, sin todo el framework de
Solaar): el receptor expone una interfaz HID "long report" (Report ID
0x11, 20 bytes) por la que se hacen "feature calls":

  1. ROOT.GetFeature(0x1004 "UNIFIED_BATTERY") -> índice del feature en
     ESE dispositivo (el índice varía por dispositivo/firmware).
  2. UNIFIED_BATTERY.GetStatus() -> [porcentaje, nivel aproximado, estado
     de carga].

No sabemos de antemano el "device index" (el mouse puede aparecer como 1,
2, 3... según qué más esté parejado al receptor), así que se prueba un
rango pequeño y se cachea el que responda para no repetir el barrido en
cada consulta.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

import hid

LOGITECH_VID = 0x046D
ROOT_FEATURE_INDEX = 0x00
UNIFIED_BATTERY_FEATURE_ID = 0x1004
FUNC_ROOT_GET_FEATURE = 0x01  # function=0 (GetFeature), swid=1
FUNC_BATTERY_GET_STATUS = 0x11  # function=1 (GetStatus), swid=1
MAX_DEVICE_INDEX = 6
REPORT_LENGTH = 20

# BatteryStatus (HID++ 2.0): 0=discharging, 1=recharging, 2=almost full,
# 3=full (cargando), 4=slow recharge. 5/6 son estados de error.
CHARGING_STATUS_BYTES = {0x01, 0x02, 0x03, 0x04}

_cache_lock = threading.Lock()
_cached_device: Optional[tuple[int, int]] = None  # (devnumber, feature_index)


@dataclass
class LogitechBattery:
    percentage: int
    charging: bool


def _find_long_report_path() -> Optional[bytes]:
    for info in hid.enumerate(LOGITECH_VID):
        if info["interface_number"] == 2 and info["usage_page"] == 0xFF00 and info["usage"] == 2:
            return info["path"]
    return None


def _send(device: "hid.device", devnumber: int, feature_idx: int, func_swid: int, params: bytes = b"") -> None:
    payload = bytes([0x11, devnumber, feature_idx, func_swid]) + params
    payload += bytes(REPORT_LENGTH - len(payload))
    device.write(payload)


def _read_matching(
    device: "hid.device", devnumber: int, feature_idx: int, func_swid: int, attempts: int = 6
) -> Optional[list]:
    for _ in range(attempts):
        frame = device.read(REPORT_LENGTH, timeout_ms=150)
        if frame:
            if frame[1] == devnumber and frame[2] == feature_idx and frame[3] == func_swid:
                return frame
            if frame[1] == devnumber and frame[2] == 0xFF:
                return None  # el dispositivo respondió con un error HID++
        # En modo no bloqueante, timeout_ms no siempre hace esperar de
        # verdad al dispositivo: se necesita este respiro real entre
        # intentos para darle tiempo a contestar sobre USB.
        time.sleep(0.08)
    return None


def _query_status(device: "hid.device", devnumber: int, feature_index: int) -> Optional[LogitechBattery]:
    _send(device, devnumber, feature_index, FUNC_BATTERY_GET_STATUS)
    reply = _read_matching(device, devnumber, feature_index, FUNC_BATTERY_GET_STATUS)
    if not reply:
        return None
    percentage, _level, status_byte = reply[4], reply[5], reply[6]
    return LogitechBattery(percentage=percentage, charging=status_byte in CHARGING_STATUS_BYTES)


def _discover_feature_index(device: "hid.device", devnumber: int) -> Optional[int]:
    _send(device, devnumber, ROOT_FEATURE_INDEX, FUNC_ROOT_GET_FEATURE, bytes([0x10, 0x04]))
    reply = _read_matching(device, devnumber, ROOT_FEATURE_INDEX, FUNC_ROOT_GET_FEATURE)
    if not reply:
        return None
    feature_index = reply[4]
    return feature_index or None


def read_battery() -> Optional[LogitechBattery]:
    """Devuelve el estado de batería del primer dispositivo Logitech HID++
    encontrado que soporte el feature UNIFIED_BATTERY, o None si no hay
    receptor conectado o ningún dispositivo parejado lo soporta.
    """
    path = _find_long_report_path()
    if not path:
        return None

    device = hid.device()
    try:
        device.open_path(path)
    except OSError:
        return None

    try:
        device.set_nonblocking(1)

        global _cached_device
        with _cache_lock:
            cached = _cached_device

        if cached:
            devnumber, feature_index = cached
            result = _query_status(device, devnumber, feature_index)
            if result:
                return result
            with _cache_lock:
                _cached_device = None  # el dispositivo cacheado ya no responde

        for devnumber in range(1, MAX_DEVICE_INDEX + 1):
            feature_index = _discover_feature_index(device, devnumber)
            if not feature_index:
                continue
            result = _query_status(device, devnumber, feature_index)
            if result:
                with _cache_lock:
                    _cached_device = (devnumber, feature_index)
                return result
    finally:
        device.close()

    return None
