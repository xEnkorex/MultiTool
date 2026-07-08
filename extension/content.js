/**
 * Aplica el volumen recibido desde AudioMixer a los elementos de audio/video
 * de la página. Se reobserva el DOM porque muchos reproductores (YouTube,
 * anuncios, SPAs) insertan el <video>/<audio> recién después de cargar, y
 * sin esto el volumen fijado nunca les llegaría.
 */

let pendingVolume = null;

function applyVolume(volume) {
  const level = Math.max(0, Math.min(100, volume)) / 100;
  document.querySelectorAll("video, audio").forEach((el) => {
    el.volume = level;
  });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "set_volume") {
    pendingVolume = msg.volume;
    applyVolume(msg.volume);
  }
});

const observer = new MutationObserver(() => {
  if (pendingVolume !== null) applyVolume(pendingVolume);
});
observer.observe(document.documentElement, { childList: true, subtree: true });
