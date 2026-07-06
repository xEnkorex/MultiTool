/**
 * Cliente del Audio Mixer.
 *
 * - Se conecta al WebSocket del servidor en el mismo host/puerto que sirvió
 *   esta página (no hay IPs hardcodeadas: funciona desde localhost o desde
 *   el móvil apuntando a la IP del PC).
 * - Reconecta automáticamente con backoff si se pierde la señal Wi-Fi.
 * - Mientras el usuario está arrastrando un slider, ignora las
 *   actualizaciones de estado entrantes para ESA app en particular, para
 *   que no "salte" bajo el dedo mientras el servidor confirma el cambio.
 */

(() => {
  const container = document.getElementById("panel-container");
  const emptyState = document.getElementById("empty-state");
  const template = document.getElementById("app-card-template");
  const statusDot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");

  /** @type {Map<string, {root: HTMLElement, slider: HTMLInputElement, value: HTMLElement, mute: HTMLElement}>} */
  const cards = new Map();

  /** Nombres de apps cuyo slider el usuario está tocando ahora mismo. */
  const activeDrags = new Set();

  let socket = null;
  let reconnectDelay = 1000;
  const MAX_RECONNECT_DELAY = 10000;
  let sendVolumeTimer = null;
  const PENDING_SENDS = new Map();

  function setStatus(state) {
    statusDot.className = "status-dot " + state;
    statusText.textContent =
      state === "connected"
        ? "Conectado"
        : state === "connecting"
        ? "Conectando…"
        : "Sin conexión — reintentando…";
  }

  function wsUrl() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${location.host}/ws`;
  }

  function connect() {
    setStatus("connecting");
    socket = new WebSocket(wsUrl());

    socket.addEventListener("open", () => {
      setStatus("connected");
      reconnectDelay = 1000; // reset del backoff tras una conexión exitosa
    });

    socket.addEventListener("message", (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      if (msg.type === "state") {
        renderState(msg.apps);
      }
    });

    socket.addEventListener("close", scheduleReconnect);
    socket.addEventListener("error", () => socket.close());
  }

  function scheduleReconnect() {
    setStatus("disconnected");
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.6, MAX_RECONNECT_DELAY);
  }

  function sendMessage(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload));
    }
  }

  // Los eventos "input" del slider disparan muy seguido durante el arrastre;
  // se envía como máximo un mensaje cada ~60ms por app para no saturar el
  // WebSocket ni el hilo de audio en el backend.
  function queueVolumeSend(name, volume) {
    PENDING_SENDS.set(name, volume);
    if (sendVolumeTimer) return;
    sendVolumeTimer = setTimeout(() => {
      for (const [n, v] of PENDING_SENDS) {
        sendMessage({ type: "set_volume", name: n, volume: v });
      }
      PENDING_SENDS.clear();
      sendVolumeTimer = null;
    }, 60);
  }

  function createCard(name) {
    const fragment = template.content.cloneNode(true);
    const root = fragment.querySelector(".app-card");
    const badge = fragment.querySelector(".app-card__badge");
    const nameEl = fragment.querySelector(".app-card__name");
    const value = fragment.querySelector(".app-card__value");
    const slider = fragment.querySelector(".app-card__slider");
    const mute = fragment.querySelector(".app-card__mute");

    root.dataset.name = name;
    badge.textContent = name.replace(/\.exe$/i, "").slice(0, 2).toUpperCase();
    nameEl.textContent = name.replace(/\.exe$/i, "");

    slider.addEventListener("pointerdown", () => activeDrags.add(name));
    slider.addEventListener("pointerup", () => activeDrags.delete(name));
    slider.addEventListener("pointercancel", () => activeDrags.delete(name));
    slider.addEventListener("input", () => {
      const v = Number(slider.value);
      value.textContent = `${v}%`;
      slider.style.setProperty("--fill", `${v}%`);
      queueVolumeSend(name, v);
    });

    mute.addEventListener("click", () => {
      sendMessage({ type: "toggle_mute", name });
    });

    container.appendChild(fragment);
    const entry = { root, slider, value, mute };
    cards.set(name, entry);
    return entry;
  }

  function updateCard(entry, app) {
    const isDragging = activeDrags.has(app.name);
    entry.root.classList.toggle("muted", app.muted);
    entry.mute.classList.toggle("active", app.muted);

    if (!isDragging) {
      entry.slider.value = String(app.volume);
      entry.value.textContent = `${app.volume}%`;
      entry.slider.style.setProperty("--fill", `${app.volume}%`);
    }
  }

  function renderState(apps) {
    emptyState.style.display = apps.length === 0 ? "block" : "none";

    const seen = new Set();
    for (const app of apps) {
      seen.add(app.name);
      const entry = cards.get(app.name) || createCard(app.name);
      updateCard(entry, app);
    }

    // Elimina las tarjetas de apps que ya no tienen audio activo.
    for (const [name, entry] of cards) {
      if (!seen.has(name)) {
        entry.root.remove();
        cards.delete(name);
        activeDrags.delete(name);
      }
    }
  }

  connect();
})();

/**
 * Fábrica genérica para bloques de grilla dinámica estilo SteamDeck
 * (Launcher y Shortcuts comparten exactamente este comportamiento):
 *
 * - Tap corto en un tile = ejecutar su acción (abrir app / disparar shortcut).
 * - Mantener presionado = abrir el modal de edición (o borrar).
 * - Tile "+" al final = agregar uno nuevo (sin límite fijo de cantidad).
 *
 * `config.extraFields` describe los campos del modal más allá de
 * nombre/ícono (ej. la ruta del ejecutable, o la combinación de teclas),
 * cada uno con `{id, key, required}`.
 */
function createTileBlock(config) {
  const grid = document.getElementById(config.gridId);
  const tileTemplate = document.getElementById(config.tileTemplateId);
  const addTileTemplate = document.getElementById("add-tile-template");

  const modal = document.getElementById(config.modal.overlayId);
  const nameInput = document.getElementById(config.modal.nameInputId);
  const iconInput = document.getElementById(config.modal.iconInputId);
  const errorEl = document.getElementById(config.modal.errorId);
  const saveBtn = document.getElementById(config.modal.saveBtnId);
  const cancelBtn = document.getElementById(config.modal.cancelBtnId);
  const deleteBtn = document.getElementById(config.modal.deleteBtnId);
  const extraFields = config.modal.extraFields.map((field) => ({
    ...field,
    input: document.getElementById(field.id),
  }));

  const LONG_PRESS_MS = 450;
  const MOVE_CANCEL_PX = 12;

  let slots = [];
  let editingSlotId = null;

  async function fetchSlots() {
    try {
      const res = await fetch(config.apiBase);
      if (!res.ok) return;
      slots = await res.json();
      renderTiles();
    } catch {
      // Sin conexión momentánea: no es crítico, el resto de la app sigue
      // funcionando; el usuario puede reintentar recargando.
    }
  }

  function renderTiles() {
    grid.innerHTML = "";
    for (const slot of slots) {
      const fragment = tileTemplate.content.cloneNode(true);
      const tile = fragment.querySelector(".tile");
      const icon = fragment.querySelector(".tile__icon");
      const name = fragment.querySelector(".tile__name");

      tile.classList.add("assigned");
      const iconContent = config.renderIcon(slot);
      if (iconContent.img) {
        const img = document.createElement("img");
        img.src = iconContent.img;
        img.alt = "";
        icon.replaceChildren(img);
        icon.classList.add("has-image");
      } else {
        icon.textContent = iconContent.text;
      }
      name.textContent = slot.name;

      attachPressHandlers(tile, slot.id);
      grid.appendChild(fragment);
    }

    const addFragment = addTileTemplate.content.cloneNode(true);
    addFragment.querySelector(".tile--add").addEventListener("click", () => openModal(null));
    grid.appendChild(addFragment);
  }

  function attachPressHandlers(tile, slotId) {
    let timer = null;
    let longPressed = false;
    let startX = 0;
    let startY = 0;

    const clearTimer = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };

    tile.addEventListener("pointerdown", (event) => {
      longPressed = false;
      startX = event.clientX;
      startY = event.clientY;
      timer = setTimeout(() => {
        longPressed = true;
        openModal(slotId);
      }, LONG_PRESS_MS);
    });

    tile.addEventListener("pointermove", (event) => {
      const dx = Math.abs(event.clientX - startX);
      const dy = Math.abs(event.clientY - startY);
      if (dx > MOVE_CANCEL_PX || dy > MOVE_CANCEL_PX) clearTimer();
    });

    tile.addEventListener("pointerup", () => {
      clearTimer();
      if (!longPressed) triggerSlot(slotId);
    });

    tile.addEventListener("pointercancel", clearTimer);
    tile.addEventListener("contextmenu", (event) => event.preventDefault());
  }

  async function triggerSlot(slotId) {
    try {
      const res = await fetch(config.triggerUrl(slotId), { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        alert(body.detail || "No se pudo ejecutar la acción.");
      }
    } catch {
      alert("Sin conexión con el servidor.");
    }
  }

  function openModal(slotId) {
    const slot = slotId !== null ? slots.find((s) => s.id === slotId) : null;
    editingSlotId = slotId;
    nameInput.value = (slot && slot.name) || "";
    iconInput.value = (slot && slot.icon) || "";
    for (const field of extraFields) {
      field.input.value = (slot && slot[field.key]) || "";
    }
    errorEl.hidden = true;
    deleteBtn.style.display = slot ? "block" : "none";
    modal.hidden = false;
    setTimeout(() => nameInput.focus(), 50);
  }

  function closeModal() {
    modal.hidden = true;
    editingSlotId = null;
  }

  async function saveSlot() {
    const name = nameInput.value.trim();
    const icon = iconInput.value.trim();
    const body = { name, icon: icon || null };

    let missing = !name;
    for (const field of extraFields) {
      const value = field.input.value.trim();
      if (field.required && !value) missing = true;
      body[field.key] = value || null;
    }

    if (missing) {
      errorEl.textContent = "Completá los campos obligatorios.";
      errorEl.hidden = false;
      return;
    }

    const isNew = editingSlotId === null;
    const url = isNew ? config.apiBase : `${config.apiBase}/${editingSlotId}`;

    try {
      const res = await fetch(url, {
        method: isNew ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const responseBody = await res.json().catch(() => ({}));
      if (!res.ok) {
        errorEl.textContent = responseBody.detail || "No se pudo guardar.";
        errorEl.hidden = false;
        return;
      }
      if (isNew) {
        slots.push(responseBody);
      } else {
        slots = slots.map((s) => (s.id === responseBody.id ? responseBody : s));
      }
      renderTiles();
      closeModal();
    } catch {
      errorEl.textContent = "Sin conexión con el servidor.";
      errorEl.hidden = false;
    }
  }

  async function deleteSlot() {
    if (editingSlotId === null) return;
    try {
      await fetch(`${config.apiBase}/${editingSlotId}`, { method: "DELETE" });
      slots = slots.filter((s) => s.id !== editingSlotId);
      renderTiles();
    } finally {
      closeModal();
    }
  }

  saveBtn.addEventListener("click", saveSlot);
  cancelBtn.addEventListener("click", closeModal);
  deleteBtn.addEventListener("click", deleteSlot);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });

  fetchSlots();
}

createTileBlock({
  gridId: "launcher-grid",
  tileTemplateId: "launcher-tile-template",
  apiBase: "/api/launcher",
  triggerUrl: (id) => `/api/launcher/${id}/launch`,
  renderIcon: (slot) =>
    // Cache-buster: la URL del ícono es siempre la misma para un slot dado,
    // así que sin esto el navegador se queda pegado a la primera versión
    // que vio (ej. si reemplazás el archivo por otro con el mismo nombre).
    slot.icon_path
      ? { img: `/api/launcher/${slot.id}/icon?t=${Date.now()}` }
      : { text: slot.icon || slot.name.slice(0, 2).toUpperCase() },
  modal: {
    overlayId: "launcher-modal",
    nameInputId: "launcher-name-input",
    iconInputId: "launcher-icon-input",
    errorId: "launcher-modal-error",
    saveBtnId: "launcher-save-btn",
    cancelBtnId: "launcher-cancel-btn",
    deleteBtnId: "launcher-delete-btn",
    extraFields: [
      { id: "launcher-icon-path-input", key: "icon_path", required: false },
      { id: "launcher-path-input", key: "path", required: true },
    ],
  },
});

createTileBlock({
  gridId: "shortcuts-grid",
  tileTemplateId: "shortcut-tile-template",
  apiBase: "/api/shortcuts",
  triggerUrl: (id) => `/api/shortcuts/${id}/trigger`,
  renderIcon: (slot) =>
    slot.icon_path
      ? { img: `/api/shortcuts/${slot.id}/icon?t=${Date.now()}` }
      : { text: slot.icon || slot.name.slice(0, 2).toUpperCase() },
  modal: {
    overlayId: "shortcut-modal",
    nameInputId: "shortcut-name-input",
    iconInputId: "shortcut-icon-input",
    errorId: "shortcut-modal-error",
    saveBtnId: "shortcut-save-btn",
    cancelBtnId: "shortcut-cancel-btn",
    deleteBtnId: "shortcut-delete-btn",
    extraFields: [
      { id: "shortcut-icon-path-input", key: "icon_path", required: false },
      { id: "shortcut-keys-input", key: "keys", required: true },
    ],
  },
});

/**
 * Apps en segundo plano — colapsado por defecto (solo un ícono + contador);
 * tocar el header despliega la grilla completa. Solo lectura + botón de
 * cierre en el badge rojo: el tile en sí no reacciona al tocarlo (a
 * propósito), cerrar requiere apuntarle al badge chico de la esquina,
 * para que no sea un cierre accidental.
 */
(() => {
  const POLL_MS = 20000;

  const toggleBtn = document.getElementById("bg-apps-toggle");
  const grid = document.getElementById("bg-apps-grid");
  const emptyState = document.getElementById("bg-apps-empty");
  const tileTemplate = document.getElementById("bg-app-tile-template");
  const countEl = document.getElementById("bg-apps-count");

  let expanded = false;
  let lastApps = [];

  async function fetchApps() {
    try {
      const res = await fetch("/api/background-apps");
      if (!res.ok) return;
      lastApps = await res.json();
      render();
    } catch {
      // Sin conexión momentánea: se reintenta en el próximo ciclo de polling.
    }
  }

  function render() {
    countEl.textContent = lastApps.length ? String(lastApps.length) : "";

    grid.hidden = !expanded || lastApps.length === 0;
    emptyState.hidden = !expanded || lastApps.length > 0;
    if (!expanded) {
      grid.innerHTML = ""; // no dejar tiles viejos colgados mientras está colapsado
      return;
    }

    grid.innerHTML = "";
    for (const app of lastApps) {
      const fragment = tileTemplate.content.cloneNode(true);
      const icon = fragment.querySelector(".tile__icon");
      const name = fragment.querySelector(".tile__name");
      const closeBadge = fragment.querySelector(".tile__close-badge");

      icon.textContent = app.name.slice(0, 2).toUpperCase();
      name.textContent = app.name;

      closeBadge.addEventListener("click", (event) => {
        event.stopPropagation();
        closeApp(app.pid, closeBadge);
      });

      grid.appendChild(fragment);
    }
  }

  async function closeApp(pid, badgeEl) {
    badgeEl.style.pointerEvents = "none";
    try {
      await fetch(`/api/background-apps/${pid}/close`, { method: "POST" });
    } catch {
      // si falla, el próximo poll la vuelve a mostrar y se puede reintentar
    } finally {
      fetchApps();
    }
  }

  toggleBtn.addEventListener("click", () => {
    expanded = !expanded;
    toggleBtn.classList.toggle("open", expanded);
    if (expanded) {
      fetchApps();
    } else {
      render();
    }
  });

  fetchApps();
  setInterval(fetchApps, POLL_MS);
})();

/**
 * Indicador de batería Logitech (header) — vía HID++, polling periódico.
 * Si no hay ningún dispositivo Logitech compatible conectado, el
 * indicador se mantiene oculto (sin ensuciar el header con un "N/A").
 */
(() => {
  const BATTERY_POLL_MS = 60000;

  const indicator = document.getElementById("battery-indicator");
  const fill = document.getElementById("battery-fill");
  const bolt = document.getElementById("battery-bolt");
  const value = document.getElementById("battery-value");

  const FILL_MAX_WIDTH = 17; // ancho total del rect interior del ícono SVG

  async function pollBattery() {
    try {
      const res = await fetch("/api/logitech/battery");
      if (!res.ok) {
        indicator.hidden = true;
        return;
      }
      const data = await res.json();
      const pct = Math.max(0, Math.min(100, data.percentage));

      indicator.hidden = false;
      indicator.classList.toggle("low", pct <= 15 && !data.charging);
      fill.setAttribute("width", String((FILL_MAX_WIDTH * pct) / 100));
      bolt.hidden = !data.charging;
      value.textContent = `${pct}%`;
    } catch {
      indicator.hidden = true;
    }
  }

  pollBattery();
  setInterval(pollBattery, BATTERY_POLL_MS);
})();

/**
 * Indicador de batería del headset (Redragon Zeus Pro, emparejado por
 * Bluetooth) — vía la propiedad estándar de batería de Windows, sin
 * estado de carga disponible (el perfil Hands-Free no lo reporta).
 */
(() => {
  const BATTERY_POLL_MS = 60000;

  const indicator = document.getElementById("headset-battery-indicator");
  const fill = document.getElementById("headset-battery-fill");
  const value = document.getElementById("headset-battery-value");

  const FILL_MAX_WIDTH = 17;

  async function pollBattery() {
    try {
      const res = await fetch("/api/headset/battery");
      if (!res.ok) {
        indicator.hidden = true;
        return;
      }
      const data = await res.json();
      const pct = Math.max(0, Math.min(100, data.percentage));

      indicator.hidden = false;
      indicator.classList.toggle("low", pct <= 15);
      fill.setAttribute("width", String((FILL_MAX_WIDTH * pct) / 100));
      value.textContent = `${pct}%`;
    } catch {
      indicator.hidden = true;
    }
  }

  pollBattery();
  setInterval(pollBattery, BATTERY_POLL_MS);
})();
