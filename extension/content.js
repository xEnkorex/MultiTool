/**
 * Aplica el volumen/mute recibido desde AudioMixer a los elementos de
 * audio/video de la página, y reporta para el otro lado cuando el usuario
 * los cambia desde los controles nativos del propio reproductor (el botón
 * de mute de YouTube, por ejemplo) — así ambos lados quedan sincronizados
 * sin importar desde dónde se los toque.
 *
 * Se reobserva el DOM porque muchos reproductores (YouTube, anuncios,
 * SPAs) insertan el <video>/<audio> recién después de cargar.
 */

let pendingVolume = null;
let pendingMuted = false;

// Evita el eco: cuando NOSOTROS asignamos .volume/.muted, el navegador
// igual dispara "volumechange" (es un evento nativo, no le importa quién
// hizo el cambio) — sin esto, cada comando que aplicamos rebotaría de
// vuelta como si el usuario lo hubiera cambiado a mano en la página.
let applyingProgrammatically = false;

function applyVolume(volume) {
  pendingVolume = volume;
  const level = Math.max(0, Math.min(100, volume)) / 100;
  applyingProgrammatically = true;
  document.querySelectorAll("video, audio").forEach((el) => {
    el.volume = level;
  });
  applyingProgrammatically = false;
}

function applyMuted(muted) {
  pendingMuted = muted;
  applyingProgrammatically = true;
  document.querySelectorAll("video, audio").forEach((el) => {
    el.muted = muted;
  });
  applyingProgrammatically = false;
}

function reportMediaState(el) {
  if (applyingProgrammatically) return;
  chrome.runtime.sendMessage({
    type: "media_state",
    volume: Math.round(el.volume * 100),
    muted: el.muted,
  }).catch(() => {
    // El service worker puede estar despertando; no es crítico, el
    // próximo "volumechange" (o el próximo comando nuestro) reintenta.
  });
}

// Un elemento nuevo (SPA navegando a otro video, anuncio insertado, etc.)
// arranca con el volumen/mute que ya le habíamos pedido a la pestaña, y
// de ahí en más avisa solo cuando cambia — pero si es "nuevo" tiene
// sentido avisar apenas aparece, así el mixer lo descubre aunque nunca
// llegue a estar "audible" para Chrome (ej. arrancó muteado).
function bindElement(el) {
  if (el.dataset.audiomixerBound) return;
  el.dataset.audiomixerBound = "1";

  if (pendingVolume !== null) {
    applyingProgrammatically = true;
    el.volume = Math.max(0, Math.min(100, pendingVolume)) / 100;
    el.muted = pendingMuted;
    applyingProgrammatically = false;
  }

  el.addEventListener("volumechange", () => reportMediaState(el));
  reportMediaState(el);
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "set_volume") {
    applyVolume(msg.volume);
  } else if (msg.type === "set_muted") {
    applyMuted(msg.muted);
  } else if (msg.type === "request_media_state") {
    // El service worker se reinició y perdió su memoria — le volvemos a
    // contar el estado actual de lo que ya tengamos enganchado, sin
    // esperar a que cambie algo para que se entere.
    document.querySelectorAll("video, audio").forEach((el) => {
      if (el.dataset.audiomixerBound) reportMediaState(el);
    });
  }
});

document.querySelectorAll("video, audio").forEach(bindElement);

const observer = new MutationObserver(() => {
  document.querySelectorAll("video, audio").forEach(bindElement);
});
observer.observe(document.documentElement, { childList: true, subtree: true });
