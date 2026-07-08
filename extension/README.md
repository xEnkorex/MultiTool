# AudioMixer — Control por pestaña (prototipo)

Extensión mínima para validar que se puede controlar el volumen/mute de
pestañas individuales del navegador desde AudioMixer, algo que la API de
audio de Windows no permite (Chromium mezcla el audio de todas las
pestañas antes de que llegue al sistema operativo).

## Cómo probarla

1. Abrí `brave://extensions` (o `chrome://extensions`).
2. Activá "Modo de desarrollador" (esquina superior derecha).
3. "Cargar descomprimida" → seleccioná esta carpeta (`extension/`).
4. Con AudioMixer corriendo (`python server.py`), abrí una pestaña que
   reproduzca audio (YouTube, por ejemplo). Debería aparecer en el panel
   del mixer como una tarjeta separada ("Brave: <título de la pestaña>").

## Limitaciones conocidas (prototipo)

- El nombre del navegador queda fijo en "Brave" — no hay una API estándar
  para que una extensión sepa si corre en Brave, Chrome o Edge.
- El volumen se logra escalando `.volume` en los elementos `<video>`/`<audio>`
  de la página, no es un control a nivel de sistema operativo. Algunos
  reproductores con Web Audio API custom (raros) podrían no respetarlo.
- Si el backend (`ws://localhost:8000`) no está corriendo, la extensión
  reintenta la conexión con backoff, pero no muestra ningún error visible.
