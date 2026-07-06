# Audio Mixer (control de volumen por app vía LAN)

Nació de una idea simple: darle una segunda vida a un smartphone viejo
que ya no se usaba, convirtiéndolo en un panel de control físico y
táctil para la PC — un mezclador de audio dedicado, launcher de apps,
disparador de shortcuts y monitor de batería, todo accesible desde la
pantalla de ese teléfono en vez de dejarlo juntando polvo en un cajón.

## Estructura

```
App_AudioManager/
├── server.py            # FastAPI: rutas HTTP + WebSocket + APIs, arranca/detiene el hilo de audio
├── audio_manager.py     # Hilo dedicado que habla con pycaw/COM (poll + cola de comandos)
├── layout_store.py      # Persistencia del layout del grid modular (posición/tamaño de cada bloque)
├── launcher_store.py    # Persistencia de los accesos directos del launcher (JSON en disco)
├── shortcut_store.py    # Persistencia de los shortcuts de teclado (JSON en disco)
├── shortcut_runner.py   # Envío de combinaciones de teclas sintéticas (librería `keyboard`)
├── background_apps.py   # Detecta/cierra apps en segundo plano (heurística de ventanas)
├── logitech_battery.py  # Batería de mouse/teclado Logitech vía HID++ (protocolo de Solaar)
├── bt_battery.py        # Batería de dispositivos Bluetooth clásicos (headset) vía Windows
├── paths.py             # Resolución de rutas (dev vs. .exe empaquetado)
├── icon.py              # Genera los íconos de la app (.ico del exe, tray, y set de la PWA)
├── tray.py              # Punto de entrada empaquetado: server + ícono en la bandeja del sistema
├── updater.py           # Chequeo de actualizaciones contra VERSION en GitHub (main)
├── VERSION              # Versión actual, ej. "1.0.0" — bumpear antes de cada release
├── requirements.txt
├── assets/
│   └── icon.ico         # Generado por `python icon.py`, no hace falta tocarlo a mano
├── static/
│   ├── index.html       # Estructura de la UI (mixer + launcher + batería)
│   ├── style.css        # Tema dark/cyberpunk, sliders táctiles grandes
│   ├── app.js           # WebSocket cliente + APIs, reconexión automática, render
│   ├── manifest.json    # Manifest de PWA (Agregar a pantalla de inicio, modo standalone)
│   ├── icons/           # Íconos de la PWA, generados por `python icon.py`
│   └── vendor/gridstack/ # Gridstack.js vendorizado (sin CDN, sin build step)
└── android-app/         # App Android nativa (WebView-wrapper) — ver sección "App Android"
```

La config del launcher (`launcher_config.json`), de shortcuts (`shortcuts_config.json`), del layout del grid (`layout_config.json`) y el log (`audiomixer.log`) NO viven en esta carpeta: se guardan en `%APPDATA%\AudioMixer\`, para que persistan sin importar desde dónde corra el `.exe`.

## Instalación

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Arranque

```powershell
python server.py
```

El servidor queda escuchando en `0.0.0.0:8000` (visible en toda tu red Wi-Fi).

## Conectarse

- **Desde el propio PC (navegador):** `http://localhost:8000`
- **Desde el móvil (misma red Wi-Fi):**
  1. Averigua la IP local del PC: `ipconfig` (Windows), busca la línea `Dirección IPv4` de tu adaptador Wi-Fi/Ethernet (ej. `192.168.1.83`).
  2. En el navegador del móvil, entra a `http://<esa-IP>:8000`.
  3. Si no conecta, revisa que el Firewall de Windows permita conexiones entrantes al puerto 8000 en redes privadas (te lo suele preguntar la primera vez que corres `server.py`; si no, crea una regla entrante para `python.exe` o el puerto TCP 8000).

No hay IPs hardcodeadas en el frontend: `app.js` arma la URL del WebSocket a partir de `location.host`, así que funciona igual desde `localhost` o desde la IP de la LAN.

## Modo pantalla completa en el móvil

La app tiene manifest de PWA (`static/manifest.json`) y se puede
"Agregar a pantalla de inicio" (Android: menú ⋮; iPhone: botón
compartir) para tener un ícono propio en el home. **Pero** el modo
`standalone` real (sin barra de navegador, automático al abrir el
ícono) requiere HTTPS — Chrome/Android no lo habilita sobre HTTP plano,
que es como corre esta app en la LAN. Como no vale la pena montar
HTTPS con certificados de verdad para un uso puramente doméstico, el
manifest queda para tener el ícono lindo, y en su lugar:

- Hay un **botón de pantalla completa** en el header (ícono de flechitas,
  al lado del indicador de conexión) que usa la Fullscreen API del
  navegador — un toque oculta la barra de direcciones mientras usás la
  app. No es automático como una PWA instalada de verdad, pero no
  necesita infraestructura nueva. No aparece en Safari/iOS (no soporta
  esa API ahí).

Los íconos de la PWA se generan con `python icon.py` (mismo lugar que
el `.ico` del `.exe` — ver `icon.py`).

## App Android (`android-app/`)

Envoltorio nativo mínimo — un WebView a pantalla completa apuntando al
servidor, en vez de depender del navegador. No es un TWA (eso necesita
HTTPS + Digital Asset Links, que no tenemos en la LAN): el WebView carga
HTTP plano sin problema (`usesCleartextTraffic` en el manifest) y el
modo inmersivo lo controla el propio código nativo, no la Fullscreen API
del navegador.

**Primera vez que abrís la app**, o manteniendo presionada la pantalla
después: un diálogo pide la dirección del servidor, con dos formas de
cargarla —

- **Escanear QR**: el mismo QR del menú "Info / código QR" del ícono de
  la bandeja en Windows (codifica la URL completa).
- **Escribirla a mano**: `IP:puerto` de la PC.

Se guarda en `SharedPreferences`, así que solo hace falta configurarla
una vez (a menos que la IP de la PC cambie).

### Compilar el APK

Requiere JDK 17, Android SDK (platform-tools + `build-tools;34.0.0` +
`platforms;android-34`) y Gradle — no hace falta Android Studio, se
puede armar todo por línea de comandos:

```powershell
# JDK 17
winget install EclipseAdoptium.Temurin.17.JDK

# Android SDK command-line tools (extraer a una ruta SIN espacios —
# sdkmanager.bat tiene un bug conocido con espacios en el path)
# https://developer.android.com/studio#command-line-tools-only
# extraer a, ej., E:\android-tools\sdk\cmdline-tools\latest\

E:\android-tools\sdk\cmdline-tools\latest\bin\sdkmanager.bat --sdk_root=E:\android-tools\sdk platform-tools "platforms;android-34" "build-tools;34.0.0"
```

Con eso, `local.properties` en `android-app/` (no se commitea, es por
máquina) apunta `sdk.dir` a esa ruta, y se compila con:

```powershell
cd android-app
.\gradlew.bat assembleDebug
```

El `.apk` queda en `android-app\app\build\outputs\apk\debug\app-debug.apk`
— firmado con el certificado de debug estándar de Android (se puede
instalar directo, sideloading, sin pasar por Play Store; el teléfono va
a pedir habilitar "Instalar apps de orígenes desconocidos" la primera
vez).

Íconos generados con `python icon.py` (mismo lugar que el `.ico` del
`.exe` y los de la PWA).

## Launcher (accesos directos estilo SteamDeck)

Grilla dinámica (sin límite fijo) para abrir apps con un toque:

- **Tap corto** en un tile → lanza esa app/acceso directo.
- **Mantener presionado** (~0.5s) → abre el modal de configuración (nombre, ícono, ruta). El botón "Quitar" borra el slot.
- **Tile "+"** al final de la grilla → agrega un slot nuevo.

La ruta se escribe a mano (los navegadores no exponen la ruta real de un archivo elegido con un `<input type="file">` por seguridad), así que necesitas copiarla una vez desde las Propiedades del acceso directo o del ejecutable, por ejemplo:

```
C:\Program Files\Spotify\Spotify.exe
C:\Users\TuUsuario\Desktop\MiJuego.lnk
```

El lanzamiento se hace con `os.startfile`, igual que un doble clic en el Explorador: funciona con `.exe`, `.lnk` e incluso documentos con una app asociada.

**Ícono personalizado:** además del ícono de texto/emoji (1-2 caracteres), cada slot admite una ruta a un `.ico`/`.png`/`.jpg`/`.gif`/`.bmp`/`.svg` en el campo "Ícono personalizado" — útil para apps sin un emoji descriptivo (ej. ShareX). Si se configura, reemplaza al ícono de texto. El servidor sirve ese archivo vía `GET /api/launcher/{id}/icon` (lee directo del disco del PC, no del teléfono).

## Shortcuts (combinaciones de teclado)

Mismo diseño y comportamiento que el Launcher (grilla dinámica, tap para disparar, mantener presionado para editar, "+" para agregar), pero en vez de abrir una app, envía una combinación de teclas sintética — pensado para atajos de PowerToys u otras herramientas que uses seguido (Color Picker, regla de pantalla, OCR, etc.).

La combinación se escribe como texto, con las teclas separadas por `+`, por ejemplo:

```
win+shift+c
ctrl+alt+t
```

Se envía con la librería [`keyboard`](https://github.com/boppreh/keyboard) (`keyboard.send(...)`), que simula el teclado a nivel de sistema — no requiere permisos de administrador para enviar teclas (solo haría falta para interceptar atajos globales de apps elevadas, que no es este caso).

## Apps en segundo plano

Lista las apps que probablemente tengan un ícono en la bandeja del sistema, con un botón "–" en rojo para cerrarlas. A propósito el botón es chico y está en la esquina del ícono (no todo el tile), para que cerrar una app requiera precisión y no sea un accidente al tocar la pantalla.

Windows no expone una API pública para leer los íconos reales de la bandeja (es un detalle interno del Shell), así que esto es una **aproximación**: lista procesos del usuario actual que no tienen ninguna ventana visible en primer plano, excluyendo procesos del sistema/plomería de Windows conocidos (ver `background_apps.py` para el detalle de la heurística y sus limitaciones). Puede no incluir el 100% de lo que ves en la bandeja, o incluir alguna app sin ícono de tray real.

Cerrar intenta primero `WM_CLOSE` (lo mismo que Alt+F4, le da a la app la chance de guardar/preguntar) y si no reacciona en 2 segundos, la termina a la fuerza.

## Layout modular (grid personalizable)

Los 4 bloques (mixer, launcher, shortcuts, apps en segundo plano) viven
en un grid tipo FancyZones/WindowGrid — cada uno se puede mover y
redimensionar, y la posición queda guardada.

- Botón de grilla (2x2 cuadrados) en el header → activa el **modo edición**.
- Con el modo activo: arrastrá un bloque para moverlo, tirá del handle
  cyan en la esquina inferior-derecha para cambiar su tamaño.
- "Listo" (o volver a tocar el botón) sale del modo edición y bloquea
  el grid para el uso normal — así un swipe/tap dentro de un bloque
  (mover un slider, tocar un tile) no dispara el drag del bloque entero.

El layout se guarda en el servidor (`GET`/`PUT /api/layout`), así que es
el mismo para cualquier dispositivo que abra la app — no es por
navegador ni por teléfono.

Implementado con [Gridstack.js](https://gridstack.github.io/gridstack.js/)
vendorizado en `static/vendor/gridstack/` (sin CDN, sin build step,
consistente con el resto del frontend). El mixer y las apps en segundo
plano usan `gs-size-to-content` (se autoajustan de alto a su contenido,
que cambia todo el tiempo); launcher y shortcuts tienen alto fijo con
scroll interno si el contenido no entra en el espacio asignado.

## Correr minimizado a la bandeja del sistema (sin consola)

En vez de `server.py`, corré `tray.py`: levanta el mismo servidor pero sin
ventana de consola, con un ícono en el system tray. Menú:

- **Abrir panel** — abre `http://localhost:8000` en el navegador.
- **Info / código QR** — ventana nativa (Tkinter) chica con la URL de red y un QR generado localmente (sin internet) para escanear con el teléfono y abrir la app directo.
- **Copiar URL de red** — copia `http://<IP-LAN>:8000` al portapapeles.
- **Salir** — cierra el servidor y la app.

```powershell
python tray.py
```

## Compilar el ejecutable (.exe)

Requiere `pyinstaller` (no es una dependencia de la app, solo para compilarla):

```powershell
pip install pyinstaller
```

**Primera vez** (genera `AudioMixer.spec` automáticamente):

```powershell
python icon.py   # regenera assets/icon.ico si no existe o si lo tocaste

python -m PyInstaller --noconfirm --onefile --noconsole `
  --name AudioMixer `
  --icon assets/icon.ico `
  --add-data "static;static" `
  --add-data "assets;assets" `
  --add-data "VERSION;." `
  --collect-all uvicorn `
  --hidden-import uvicorn.lifespan.on `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.protocols.websockets.auto `
  --hidden-import uvicorn.loops.auto `
  tray.py
```

**Siguientes veces** (reusa el `.spec` ya generado, más simple):

```powershell
python -m PyInstaller --noconfirm AudioMixer.spec
```

El ejecutable queda en `dist\AudioMixer.exe` — es standalone, se puede
copiar a cualquier lado (no necesita Python instalado en la máquina que lo
corre).

**Para que arranque solo con Windows:** creá un acceso directo a
`dist\AudioMixer.exe` y pegalo en la carpeta de inicio
(`Win+R` → `shell:startup`).

## Publicar una actualización

El tray chequea la versión (ver sección siguiente) leyendo el archivo
`VERSION` de la rama `main` en GitHub — así que publicar una actualización
es:

1. Bumpear el archivo `VERSION` (ej. `1.0.0` → `1.1.0`).
2. Commit + push a `main`.
3. Compilar el `.exe` (pasos de arriba) y crear un **Release** en GitHub
   (tag `v1.1.0`, con `dist\AudioMixer.exe` adjunto) — es donde el botón
   "Buscar actualizaciones" manda a la gente a descargarlo. `dist/` está
   en `.gitignore` a propósito (no se commitea el binario al repo, se
   adjunta al Release).

## Actualizaciones automáticas (tray)

El repo es público, así que el chequeo lee `VERSION` directo de
`raw.githubusercontent.com` sin credenciales — si el repo pasara a
privado, esto deja de encontrar actualizaciones (falla la conexión) sin
romper nada más.

No es un webhook: un webhook necesitaría que la PC del usuario exponga un
endpoint público para que GitHub le avise, lo cual no tiene sentido para
una app de escritorio. En cambio es polling — un hilo en segundo plano
que pregunta cada `UPDATE_CHECK_INTERVAL_HOURS` (6 por defecto, en
`tray.py`), más un ítem de menú **"Buscar actualizaciones"** para
chequear al toque. Si hay una versión más nueva:

- El chequeo periódico muestra una notificación nativa de Windows (globo/toast).
- El chequeo manual abre un diálogo preguntando si querés ir a la página de descargas.

### Nota sobre `--noconsole`

Con `--noconsole`, Windows no le da a la app una consola real: `sys.stdout`
y `sys.stderr` quedan en `None`. Si algo (FastAPI, Uvicorn, o nuestro
propio `logging`) intenta escribir ahí sin chequear primero, revienta en
silencio — así fue como se depuró originalmente: compilando una vez SIN
`--noconsole` para ver el traceback real. `tray.py` ya redirige esos
streams a un destino nulo antes de importar nada más, pero si en el futuro
el .exe empaquetado "no hace nada" sin errores visibles, ese es el primer
sospechoso: recompilá sin `--noconsole` (o usando `console=True` en el
`.spec`) para ver qué está fallando.

## Notas de diseño

- `audio_manager.py` inicializa COM (`CoInitialize`) una sola vez en un hilo daemon dedicado, y jamás se toca pycaw desde el hilo del event loop — evita bloquear el WebSocket y evita compartir el apartamento COM entre hilos.
- Cada ~1s se revisa la lista de sesiones de audio activas; si cambió (una app abrió o cerró audio, o el volumen cambió desde el Mixer de Windows), se notifica a todos los clientes conectados automáticamente.
- Los cambios de volumen desde el móvil se aplican de inmediato a través de una cola de comandos, sin esperar al siguiente ciclo de polling.
- Si un proceso muere justo cuando se le manda un comando, se captura el `COMError` y se descarta silenciosamente esa sesión (el siguiente poll reconcilia el estado).
