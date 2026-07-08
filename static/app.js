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
  const rowTemplate = document.getElementById("mixer-row-template");
  const statusDot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");

  /** @type {Map<string, {root: HTMLElement, groupEl: HTMLElement, nestedContainer: HTMLElement, slider: HTMLInputElement, value: HTMLElement, mute: HTMLElement}>} */
  const cards = new Map();

  /** Filas de pestaña anidadas dentro del grupo de su navegador (mismo shape que `cards`, sin groupEl/nestedContainer). */
  const tabRows = new Map();

  /** Claves ("app:name" / "tab:id") cuyo slider el usuario está tocando ahora. */
  const activeDrags = new Set();

  let socket = null;
  let reconnectDelay = 1000;
  const MAX_RECONNECT_DELAY = 10000;

  // Los eventos "input" del slider disparan muy seguido durante el arrastre;
  // se junta como máximo un mensaje cada ~60ms por app/pestaña para no
  // saturar el WebSocket ni el hilo de audio en el backend. Apps y pestañas
  // comparten timer pero van en colas separadas porque el mensaje que
  // espera el servidor es distinto para cada una.
  let sendTimer = null;
  const PENDING_APP_VOLUME = new Map();
  const PENDING_TAB_VOLUME = new Map();

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
        renderState(msg.apps, msg.tabs || []);
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

  function queueAppVolume(name, volume) {
    PENDING_APP_VOLUME.set(name, volume);
    if (sendTimer) return;
    sendTimer = setTimeout(flushPendingVolumes, 60);
  }

  function queueTabVolume(tabId, volume) {
    PENDING_TAB_VOLUME.set(tabId, volume);
    if (sendTimer) return;
    sendTimer = setTimeout(flushPendingVolumes, 60);
  }

  function flushPendingVolumes() {
    for (const [name, v] of PENDING_APP_VOLUME) {
      sendMessage({ type: "set_volume", name, volume: v });
    }
    PENDING_APP_VOLUME.clear();
    for (const [tabId, v] of PENDING_TAB_VOLUME) {
      sendMessage({ type: "set_tab_volume", tabId, volume: v });
    }
    PENDING_TAB_VOLUME.clear();
    sendTimer = null;
  }

  // Fila de mixer compartida por apps y pestañas anidadas (ver
  // `#mixer-row-template`): el propio slider ocupa toda la fila como
  // fondo, así que "crear una fila" es siempre lo mismo sea top-level o
  // anidada — solo cambia dónde se cuelga y si lleva la clase `--nested`.
  function buildRow(key, item, parent, { nested = false } = {}) {
    const fragment = rowTemplate.content.cloneNode(true);
    const root = fragment.querySelector(".mixer-row");
    const badge = fragment.querySelector(".mixer-row__badge");
    const nameEl = fragment.querySelector(".mixer-row__name");
    const value = fragment.querySelector(".mixer-row__value");
    const slider = fragment.querySelector(".mixer-row__slider");
    const focus = fragment.querySelector(".mixer-row__focus");
    const pin = fragment.querySelector(".mixer-row__pin");

    root.dataset.key = key;
    if (nested) root.classList.add("mixer-row--nested");

    slider.addEventListener("pointerdown", () => activeDrags.add(key));
    slider.addEventListener("pointerup", () => activeDrags.delete(key));
    slider.addEventListener("pointercancel", () => activeDrags.delete(key));
    slider.addEventListener("input", () => {
      const v = Number(slider.value);
      value.textContent = `${v}%`;
      slider.style.setProperty("--fill", `${v}%`);
      item.onVolume(v);
    });

    // El ícono/favicon hace doble función de botón de mute (ver el
    // porqué en el template): un solo control en vez de dos apretujados.
    badge.addEventListener("click", () => item.onMute());
    focus.addEventListener("click", () => item.onFocus());
    pin.addEventListener("click", () => item.onTogglePin());

    parent.appendChild(root);
    return { root, badgeEl: badge, labelEl: nameEl, slider, value, focus, pin };
  }

  // Card de nivel superior: representa una app (agrupada por proceso, como
  // siempre) o, si la extensión reportó pestañas sin poder calzarlas con
  // ninguna app conocida, una pestaña "huérfana" mostrada suelta para no
  // perder el control. Vive dentro de un `.mixer-group` que también aloja
  // el contenedor de sus pestañas anidadas (vacío si no tiene ninguna).
  function createCard(key, item) {
    const group = document.createElement("div");
    group.className = "mixer-group";
    group.dataset.key = key;
    container.appendChild(group);

    const row = buildRow(key, item, group);

    const nestedContainer = document.createElement("div");
    nestedContainer.className = "mixer-group__nested";
    group.appendChild(nestedContainer);

    const entry = { ...row, groupEl: group, nestedContainer };
    cards.set(key, entry);
    return entry;
  }

  function createTabRow(key, item, parentContainer) {
    const row = buildRow(key, item, parentContainer, { nested: true });
    tabRows.set(key, row);
    return row;
  }

  // Reemplaza la sigla de 2 letras por el ícono real (favicon de la
  // pestaña, o el .ico extraído del .exe) cuando hay uno disponible. Si la
  // imagen falla (404, sitio sin favicon, etc.) cae de vuelta al texto, y
  // no reintenta la misma URL rota en cada render — solo si `iconUrl`
  // cambia (ej. la pestaña navegó a otro sitio) vuelve a intentarlo.
  function setBadgeContent(badgeEl, item) {
    const iconUrl = item.iconUrl;
    if (!iconUrl) {
      badgeEl.textContent = item.badge;
      badgeEl.classList.remove("has-image");
      delete badgeEl.dataset.iconUrl;
      return;
    }
    if (badgeEl.dataset.iconUrl === iconUrl) return;
    badgeEl.dataset.iconUrl = iconUrl;

    const img = document.createElement("img");
    img.alt = "";
    img.onerror = () => {
      badgeEl.textContent = item.badge;
      badgeEl.classList.remove("has-image");
    };
    img.src = iconUrl;
    badgeEl.replaceChildren(img);
    badgeEl.classList.add("has-image");
  }

  // `item.label`/`item.badge` se reescriben en cada render (no solo al
  // crear la card): el título de una pestaña de YouTube cambia de video en
  // video sin que la pestaña se cierre, y sin esto el fader se quedaba
  // pegado al título del primer video que la hizo aparecer en el mixer.
  function updateCard(entry, key, item) {
    entry.labelEl.textContent = item.label;
    if (entry.badgeEl) setBadgeContent(entry.badgeEl, item);

    const isAvailable = item.available !== false;
    const isDragging = activeDrags.has(key);

    entry.root.classList.toggle("muted", item.muted);
    entry.badgeEl.setAttribute("aria-label", item.muted ? "Activar sonido" : "Silenciar");
    entry.pin.classList.toggle("active", Boolean(item.pinned));
    entry.pin.setAttribute("aria-pressed", String(Boolean(item.pinned)));

    // "Sonando" (glow) solo tiene sentido si de verdad hay señal de audio
    // ahora mismo: una card pineada en silencio, o muteada, no debería
    // brillar como si estuviera sonando.
    entry.root.classList.toggle("sounding", isAvailable && Boolean(item.active) && !item.muted);
    entry.root.classList.toggle("unavailable", !isAvailable);
    entry.slider.disabled = !isAvailable;
    entry.badgeEl.disabled = !isAvailable;
    entry.focus.disabled = !isAvailable;

    if (!isDragging) {
      const displayVolume = isAvailable ? item.volume : 0;
      entry.slider.value = String(displayVolume);
      entry.value.textContent = isAvailable ? `${item.volume}%` : "—";
      entry.slider.style.setProperty("--fill", `${displayVolume}%`);
    }
  }

  // Cuelga cada pestaña de `tabItems` dentro del contenedor anidado de su
  // card de app, creando/reubicando la fila si hace falta (ej. la pestaña
  // cambió de navegador entre un render y otro, caso raro pero posible).
  function renderNestedTabs(nestedContainer, tabItems, seenTabRows) {
    for (const item of tabItems) {
      seenTabRows.add(item.key);
      let row = tabRows.get(item.key);
      if (!row) {
        row = createTabRow(item.key, item, nestedContainer);
      } else if (row.root.parentElement !== nestedContainer) {
        nestedContainer.appendChild(row.root);
      }
      updateCard(row, item.key, item);
    }
  }

  function renderState(apps, tabs) {
    const appItems = apps.map((app) => ({
      key: `app:${app.name}`,
      matchName: app.name.replace(/\.exe$/i, "").toLowerCase(),
      badge: app.name.replace(/\.exe$/i, "").slice(0, 2).toUpperCase(),
      label: app.name.replace(/\.exe$/i, ""),
      // Sin ícono mientras está "phantom" (pineada pero sin proceso real
      // corriendo): no hay nada que extraer, y así reintenta apenas vuelva.
      iconUrl: app.available === false ? null : `/api/app-icon/${encodeURIComponent(app.name)}`,
      volume: app.volume,
      muted: app.muted,
      active: app.active,
      pinned: app.pinned,
      available: app.available,
      tabs: [],
      onVolume: (v) => queueAppVolume(app.name, v),
      onMute: () => sendMessage({ type: "toggle_mute", name: app.name }),
      onTogglePin: () => sendMessage({ type: "toggle_pin_app", name: app.name }),
      onFocus: () => {
        fetch(`/api/focus-app/${encodeURIComponent(app.name)}`, { method: "POST" }).catch(() => {
          // Sin conexión momentánea, o la app no tiene ventana visible — no
          // hay mucho más para hacer que dejar el botón como si nada.
        });
      },
    }));

    // Cada pestaña se anida bajo la app cuyo nombre de proceso coincide
    // (ej. "brave" ← navigator.brave detectado por la extensión). Si
    // ninguna calza —la sesión de audio del navegador todavía no aparece,
    // o la extensión no pudo identificar el navegador—, se muestra suelta
    // en vez de perderla en silencio.
    const orphanTabs = [];
    for (const tab of tabs) {
      const tabItem = {
        key: `tab:${tab.id}`,
        label: tab.title || "Pestaña",
        iconUrl: tab.favIconUrl || null,
        volume: tab.volume,
        muted: tab.muted,
        active: tab.audible,
        pinned: tab.pinned,
        available: true,
        onVolume: (v) => queueTabVolume(tab.id, v),
        onMute: () => sendMessage({ type: "toggle_tab_mute", tabId: tab.id }),
        onTogglePin: () => sendMessage({ type: "toggle_pin_tab", tabId: tab.id }),
        onFocus: () => sendMessage({ type: "focus_tab", tabId: tab.id }),
      };
      const parent = appItems.find((a) => a.matchName === (tab.browser || "").toLowerCase());
      if (parent) {
        parent.tabs.push(tabItem);
      } else {
        orphanTabs.push({
          ...tabItem,
          badge: "TB",
          label: `${tab.browser ? tab.browser + ": " : ""}${tabItem.label}`,
        });
      }
    }

    const topLevelItems = [...appItems, ...orphanTabs];
    emptyState.style.display = topLevelItems.length === 0 ? "block" : "none";

    const seenCards = new Set();
    const seenTabRows = new Set();

    for (const item of topLevelItems) {
      seenCards.add(item.key);
      const entry = cards.get(item.key) || createCard(item.key, item);
      updateCard(entry, item.key, item);
      if (item.tabs) renderNestedTabs(entry.nestedContainer, item.tabs, seenTabRows);
    }

    // Elimina las tarjetas de apps/pestañas que ya no tienen audio activo.
    for (const [key, entry] of cards) {
      if (!seenCards.has(key)) {
        entry.groupEl.remove();
        cards.delete(key);
        activeDrags.delete(key);
      }
    }

    for (const [key, row] of tabRows) {
      if (!seenTabRows.has(key)) {
        row.root.remove();
        tabRows.delete(key);
        activeDrags.delete(key);
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

/**
 * Botón de pantalla completa (Fullscreen API) — alternativa a la PWA
 * standalone real, que necesitaría HTTPS (no tenemos, es HTTP plano en
 * la LAN). Con un toque oculta la barra del navegador mientras se usa
 * la app. No soportado en Safari/iOS: el botón queda oculto ahí
 * (detección de soporte antes de mostrarlo).
 */
(() => {
  const btn = document.getElementById("fullscreen-btn");
  const expandIcon = btn.querySelector(".fullscreen-btn__icon-expand");
  const collapseIcon = btn.querySelector(".fullscreen-btn__icon-collapse");

  const supported = Boolean(
    document.documentElement.requestFullscreen && document.fullscreenEnabled
  );
  if (!supported) return;

  btn.hidden = false;

  function updateIcon() {
    const isFullscreen = Boolean(document.fullscreenElement);
    expandIcon.hidden = isFullscreen;
    collapseIcon.hidden = !isFullscreen;
    btn.setAttribute("aria-label", isFullscreen ? "Salir de pantalla completa" : "Pantalla completa");
  }

  btn.addEventListener("click", () => {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      document.documentElement.requestFullscreen().catch(() => {
        // Algunos navegadores rechazan el pedido si no viene de un gesto
        // directo del usuario; no hay mucho más para hacer acá.
      });
    }
  });

  document.addEventListener("fullscreenchange", updateIcon);
  updateIcon();
})();

/**
 * Grid modular (layout personalizable estilo FancyZones/WindowGrid) —
 * Gridstack.js. Arranca bloqueado (staticGrid); el botón del header
 * activa el modo edición para arrastrar/redimensionar cada bloque. El
 * layout (posición + tamaño por bloque) se guarda en el servidor para
 * que persista entre sesiones y dispositivos.
 */
(() => {
  const gridEl = document.getElementById("app-grid");
  const editBtn = document.getElementById("edit-layout-btn");
  const banner = document.getElementById("edit-layout-banner");
  const doneBtn = document.getElementById("edit-layout-done-btn");

  const grid = GridStack.init(
    {
      column: 4,
      cellHeight: 70,
      margin: 6,
      float: true,
      staticGrid: true,
      disableOneColumnMode: true,
    },
    gridEl
  );

  let saveTimer = null;
  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      const items = grid.save(false).map((item) => ({
        id: String(item.id),
        x: item.x,
        y: item.y,
        w: item.w,
        h: item.h,
      }));
      try {
        await fetch("/api/layout", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(items),
        });
      } catch {
        // Si falla el guardado, el layout sigue andando en esta sesión;
        // se reintenta solo con el próximo cambio.
      }
    }, 400);
  }

  grid.on("change", scheduleSave);

  function setEditMode(enabled) {
    grid.setStatic(!enabled);
    gridEl.classList.toggle("edit-mode", enabled);
    editBtn.classList.toggle("active", enabled);
    banner.hidden = !enabled;
  }

  editBtn.addEventListener("click", () => {
    setEditMode(!gridEl.classList.contains("edit-mode"));
  });
  doneBtn.addEventListener("click", () => setEditMode(false));

  // Carga el layout guardado (si existe) SIN recrear los nodos DOM:
  // grid.update() reposiciona los items existentes en vez de destruirlos,
  // preservando las referencias/listeners que ya armaron los otros
  // módulos de la app sobre el contenido interno de cada bloque.
  (async () => {
    try {
      const res = await fetch("/api/layout");
      if (!res.ok) return;
      const saved = await res.json();
      if (!Array.isArray(saved) || saved.length === 0) return;

      for (const item of saved) {
        const el = gridEl.querySelector(`[gs-id="${item.id}"]`);
        if (el) {
          grid.update(el, { x: item.x, y: item.y, w: item.w, h: item.h });
        }
      }
    } catch {
      // Sin conexión: se queda con el layout default ya presente en el HTML.
    }
  })();
})();
