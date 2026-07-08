/**
 * Service worker de la extensión: mantiene un WebSocket con AudioMixer y
 * le reporta qué pestañas están reproduciendo audio ahora mismo.
 *
 * Mute usa la API nativa `chrome.tabs.update({muted})` (instantánea y
 * confiable). El volumen no tiene equivalente nativo en la API de
 * pestañas, así que se logra escalando el `.volume` de los elementos
 * <video>/<audio> dentro de la página, vía content.js.
 */

const BACKEND_WS_URL = "ws://localhost:8000/ws/extension";

// Solo un fallback: el backend identifica el navegador real mirando qué
// proceso abrió esta conexión TCP (ver `_detect_browser_process_name` en
// server.py) y pisa este valor en cada `tabs_state`. Probamos primero
// detectarlo acá con `navigator.brave.isBrave()` (la API pública de Brave
// para esto) pero no está disponible en todas las instalaciones — el
// backend es más confiable porque no depende de ninguna API del navegador.
const browserLabel = "Chrome";

let socket = null;
let reconnectDelay = 1000;
const MAX_RECONNECT_DELAY = 10000;

/** tabId -> {title, audible, muted, volume, pinned} */
const tabState = new Map();
let sendStateTimer = null;

// tabIds pineados desde el mixer: sobreviven en tabState aunque la pestaña
// deje de sonar (solo se limpian al cerrarla o al despinearla). No se
// persiste en disco a propósito — el tabId no sobrevive un reinicio del
// navegador, así que no tendría sentido guardarlo.
const pinnedTabs = new Set();

// `tab.audible` de Chrome puede parpadear a `false` por un instante en
// silencios cortos (anuncios, pausas breves) sin que el video haya dejado
// de reproducirse — sin este margen, la card del mixer aparecía y
// desaparecía todo el rato en vez de quedarse quieta. Solo aplica a
// pestañas no pineadas: una pineada se queda igual, silenciosa o no.
const SILENCE_GRACE_MS = 5000;
/** tabId -> setTimeout handle de la remoción diferida por silencio. */
const silenceTimers = new Map();

function clearSilenceTimer(tabId) {
  const timer = silenceTimers.get(tabId);
  if (timer) {
    clearTimeout(timer);
    silenceTimers.delete(tabId);
  }
}

// El service worker de la extensión se apaga solo por inactividad cada
// pocos segundos y pierde TODO su estado en memoria (tabState, pins,
// browserLabel) al despertar de nuevo. Sin esto, una pestaña que ya venía
// sonando desde antes del reinicio no se volvía a reportar nunca — recién
// se detectaba si pasaba algo que disparara onUpdated (pausarla, mutearla).
async function rehydrateFromLiveTabs() {
  let audibleTabs;
  try {
    audibleTabs = await chrome.tabs.query({ audible: true });
  } catch {
    return;
  }
  for (const tab of audibleTabs) {
    upsertTab(tab);
  }
}

// No conectar de nuevo si ya hay un socket abierto o conectándose —
// `ensureConnected` es el único punto de entrada seguro para todos los
// disparadores de reconexión (el propio close, la alarma, y cualquier
// actividad de pestañas), así nunca se abren dos sockets a la vez.
function ensureConnected() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  connect();
}

function connect() {
  socket = new WebSocket(BACKEND_WS_URL);

  socket.addEventListener("open", () => {
    reconnectDelay = 1000;
    rehydrateFromLiveTabs();
    sendState();
  });

  socket.addEventListener("message", (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    handleCommand(msg);
  });

  socket.addEventListener("close", () => {
    // Este setTimeout solo sirve si el service worker sigue vivo cuando
    // se cumple el plazo — Chrome no lo respeta si lo apaga antes por
    // inactividad, que es el caso común. La alarma de más abajo es la
    // red de seguridad real para ese escenario.
    setTimeout(ensureConnected, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.6, MAX_RECONNECT_DELAY);
  });
  socket.addEventListener("error", () => socket.close());
}

// Red de seguridad contra el problema de arriba: `chrome.alarms` sí
// garantiza despertar al service worker (a diferencia de un setTimeout
// colgado en un contexto que Chrome puede matar en cualquier momento),
// así que como mucho pasa ~1 minuto sin conexión aunque no haya ninguna
// actividad de pestañas que la reestablezca antes por su cuenta.
const RECONNECT_ALARM_NAME = "audiomixer-reconnect";
chrome.alarms.create(RECONNECT_ALARM_NAME, { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === RECONNECT_ALARM_NAME) ensureConnected();
});

function queueSendState() {
  if (sendStateTimer) return;
  sendStateTimer = setTimeout(() => {
    sendState();
    sendStateTimer = null;
  }, 250);
}

function sendState() {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const tabs = [];
  for (const [id, info] of tabState) {
    tabs.push({
      id,
      title: info.title,
      favIconUrl: info.favIconUrl,
      audible: info.audible,
      muted: info.muted,
      volume: info.volume,
      pinned: info.pinned,
      browser: browserLabel,
    });
  }
  socket.send(JSON.stringify({ type: "tabs_state", tabs }));
}

async function handleCommand(msg) {
  if (msg.type === "set_tab_volume") {
    const info = tabState.get(msg.tabId);
    if (!info) return;
    info.volume = msg.volume;
    try {
      await chrome.tabs.sendMessage(msg.tabId, { type: "set_volume", volume: msg.volume });
    } catch {
      // El content script puede no estar inyectado (páginas internas del
      // navegador, chrome://, la Web Store, etc.) — no hay nada que hacer ahí.
    }
    queueSendState();
  } else if (msg.type === "toggle_tab_mute") {
    const info = tabState.get(msg.tabId);
    if (!info) return;
    try {
      await chrome.tabs.update(msg.tabId, { muted: !info.muted });
    } catch {
      // La pestaña pudo haberse cerrado justo antes de recibir el comando.
    }
  } else if (msg.type === "toggle_pin_tab") {
    if (pinnedTabs.has(msg.tabId)) {
      pinnedTabs.delete(msg.tabId);
    } else {
      pinnedTabs.add(msg.tabId);
    }
    try {
      // No hay evento nativo para "se pineó/despineó": hay que releer la
      // pestaña a mano y dejar que upsertTab decida si la mantiene (pineada)
      // o la descarta (despineada y ya en silencio).
      const tab = await chrome.tabs.get(msg.tabId);
      upsertTab(tab);
    } catch {
      tabState.delete(msg.tabId);
      queueSendState();
    }
  } else if (msg.type === "focus_tab") {
    try {
      const tab = await chrome.tabs.update(msg.tabId, { active: true });
      if (tab && tab.windowId !== undefined) {
        await chrome.windows.update(tab.windowId, { focused: true });
      }
    } catch {
      // La pestaña pudo haberse cerrado justo antes de recibir el comando.
    }
  }
}

function upsertTab(tab) {
  const pinned = pinnedTabs.has(tab.id);

  if (!tab.audible && !pinned && !tabState.has(tab.id)) return; // nunca estuvo, nada que hacer

  if (tab.audible) clearSilenceTimer(tab.id);

  const existing = tabState.get(tab.id);
  tabState.set(tab.id, {
    title: tab.title || "Pestaña",
    favIconUrl: tab.favIconUrl || null,
    audible: Boolean(tab.audible),
    muted: Boolean(tab.mutedInfo && tab.mutedInfo.muted),
    volume: existing ? existing.volume : 100,
    pinned,
  });
  queueSendState();

  if (!tab.audible && !pinned) {
    // Le damos un margen antes de sacarla del mixer en vez de borrarla ya:
    // si vuelve a sonar antes de que expire, `clearSilenceTimer` de arriba
    // cancela esta remoción.
    if (!silenceTimers.has(tab.id)) {
      const timer = setTimeout(() => {
        silenceTimers.delete(tab.id);
        if (tabState.delete(tab.id)) queueSendState();
      }, SILENCE_GRACE_MS);
      silenceTimers.set(tab.id, timer);
    }
  }
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // Cualquier actividad de pestañas despierta el service worker de todos
  // modos — de paso, aprovechamos para reconectar si hacía falta, en vez
  // de esperar a la alarma (que puede tardar hasta 1 minuto).
  ensureConnected();
  if ("audible" in changeInfo || "mutedInfo" in changeInfo || "title" in changeInfo || "favIconUrl" in changeInfo) {
    upsertTab(tab);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  pinnedTabs.delete(tabId);
  clearSilenceTimer(tabId);
  if (tabState.delete(tabId)) queueSendState();
});

connect();
