# Features pendientes / en investigación

## Batería Logitech (MX Master 3S) — RESUELTO

Implementado en `logitech_battery.py` + endpoint `GET /api/logitech/battery` +
indicador en el header (logo G, ícono de batería, %). Usa el protocolo HID++
2.0 (el mismo que Solaar), feature `UNIFIED_BATTERY` (0x1004), vía el
receptor Logi Bolt (`VID_046D&PID_C548`). Ver el código para el detalle del
protocolo — quedó documentado en el docstring del módulo.

Nota para el futuro: si el mouse cambia de receptor/slot, el "device index"
cacheado se auto-invalida y se re-descubre solo en la próxima consulta.

## Batería del Redragon Zeus Pro (H510-PRO) — RESUELTO (vía Bluetooth)

**Solución final:** el usuario emparejó el Zeus Pro por Bluetooth directo a la PC (además del dongle 2.4G, que se sigue usando para el audio). Windows recibe el nivel de batería a través del perfil Hands-Free (HFP, UUID `{0000111E-...}`) y lo guarda en la propiedad estándar `DEVPKEY_Device_BatteryLevel` — la misma que usa Configuración > Bluetooth y dispositivos para mostrar el ícono de batería.

Implementado en `bt_battery.py` (consulta vía `Get-PnpDeviceProperty` en PowerShell, matcheando dispositivos PnP por nombre — `"H510-PRO"` — porque un dispositivo Bluetooth aparece como varias entradas PnP, una por perfil, y el dato de batería vive específicamente en la entrada "Hands-Free AG", no en la de audio) + endpoint `GET /api/headset/battery` + indicador en el header (ícono de audífonos, batería, %).

**Importante:** esta consulta es lenta (~10-15s por el arranque de `powershell.exe` + posible escaneo AMSI), a diferencia de la del mouse (HID++ directo, ~instantáneo). No es un problema porque corre en un hilo aparte (`asyncio.to_thread`) y el frontend la pollea en segundo plano cada 60s sin bloquear nada — pero si en el futuro se necesita más velocidad, valdría la pena investigar alternativas (ej. `SetupDiGetDeviceProperty` vía ctypes en vez de spawnear PowerShell).

No se llegó a descifrar el protocolo del dongle 2.4G (ver detalle abajo) — terminó siendo innecesario gracias a la vía Bluetooth.

### Lo que se investigó del dongle 2.4G (ya no es necesario, queda como referencia)
- Dispositivo identificado en Windows como `H510-PRO Wireless headset`, `VID_040B&PID_0897`, fabricante de chipset **XiiSound Technology Corporation** (dongle 2.4GHz).
- Tiene 2 interfaces HID (MI_03):
  - COL02 — `usage_page 0x0C` (Consumer Control), reporta los botones de volumen/mute. No sirve para batería.
  - COL01 — `usage_page 0xF100` (vendor-específico). Descriptor real (extraído con `hidapi`):
    - Input: Report ID `1`, 12 bytes de datos (13 con el ID).
    - Output: Report ID `1`, 64 bytes de datos (65 con el ID).
- No existe soporte en [HeadsetControl](https://github.com/Sapd/HeadsetControl) ni protocolo público documentado para este chipset/modelo.
- El manual solo documenta un LED binario (rojo parpadeante = batería baja, rojo fijo = cargando, apagado = full/apagado) — **no confirma que el firmware trackee un % continuo en el modo dongle 2.4GHz**.
- **Pista clave del usuario:** al conectar el mismo headset por **Bluetooth al teléfono**, sí se ve el % de batería en tiempo real. Esto confirma que el firmware SÍ trackea el nivel de batería internamente — el dongle 2.4G simplemente no lo expone (o lo expone con un comando que no hemos encontrado aún).
- Enviando un output report de prueba (Report ID 1, 64 bytes en cero) al canal vendor (COL01), el headset SÍ responde con datos reales:
  ```
  [1, 42, 190, 225, 89, 0, 0, 0, 0, 8, 82, 63, 201]
  ```
  Sin identificar aún qué byte (si alguno) corresponde a batería — no hay confirmación de que sea estable/reproducible (un segundo intento no obtuvo respuesta, hay que investigar el patrón de timing/reintento).
- Existe software oficial de Redragon para la línea H510 Zeus (instalador Win10 vía Google Drive, enlazado desde `redragonshop.com`, tienda regional no oficial). **Se decidió NO descargarlo/ejecutarlo** por ser un ejecutable sin firma de una fuente no verificada — hubiera permitido esnifar el protocolo real si el usuario lo usara mientras escuchamos el canal HID.

(Quedó confirmado que el headset SÍ permite dongle 2.4G + Bluetooth simultáneo — el usuario usa el dongle para el audio y el BT solo para el reporte de batería.)

---

*(Agregar aquí futuras features pendientes conforme surjan.)*
