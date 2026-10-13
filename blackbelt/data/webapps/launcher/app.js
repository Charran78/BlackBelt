"use strict";

const TERMINAL_TOKEN_KEY = "blackbelt-launcher-terminal-token";
const REFRESH_INTERVAL_MS = 10_000;
const INDEX_REFRESH_INTERVAL_MS = 1_500;

const state = {
  catalog: { applications: [], tools: [] },
  status: null,
  terminal: null,
  terminalAddon: null,
  terminalSocket: null,
  terminalToken: sessionStorage.getItem(TERMINAL_TOKEN_KEY) || "",
  terminalStartup: null,
  pendingTerminalCommands: [],
  installPrompt: null,
  toastTimer: null,
  indexStatus: null,
  workIndexStatus: null,
};

const elements = {
  appGrid: document.querySelector("#app-grid"),
  appSearch: document.querySelector("#app-search"),
  toolGrid: document.querySelector("#tool-grid"),
  environment: document.querySelector("#environment-panel"),
  cpuResource: document.querySelector("#cpu-resource"),
  memoryResource: document.querySelector("#memory-resource"),
  diskResource: document.querySelector("#disk-resource"),
  ollamaIndicator: document.querySelector("#ollama-indicator"),
  ollamaSummary: document.querySelector("#ollama-summary"),
  ollamaStart: document.querySelector("#ollama-start-button"),
  vaultIndicator: document.querySelector("#vault-indicator"),
  vaultSummary: document.querySelector("#vault-summary"),
  refresh: document.querySelector("#refresh-button"),
  lastUpdated: document.querySelector("#last-updated"),
  indexConfiguration: document.querySelector("#index-configuration"),
  indexState: document.querySelector("#index-state"),
  indexDetail: document.querySelector("#index-detail"),
  indexProgress: document.querySelector("#index-progress"),
  indexStart: document.querySelector("#index-start"),
  indexCancel: document.querySelector("#index-cancel"),
  workIndexConfiguration: document.querySelector("#work-index-configuration"),
  workIndexState: document.querySelector("#work-index-state"),
  workIndexDetail: document.querySelector("#work-index-detail"),
  workIndexProgress: document.querySelector("#work-index-progress"),
  workIndexStart: document.querySelector("#work-index-start"),
  workIndexCancel: document.querySelector("#work-index-cancel"),
  clock: document.querySelector("#clock"),
  toast: document.querySelector("#toast"),
  install: document.querySelector("#install-button"),
  terminalPanel: document.querySelector("#terminal-panel"),
  terminalToggle: document.querySelector("#terminal-toggle"),
  terminalHide: document.querySelector("#terminal-hide"),
  terminalClose: document.querySelector("#terminal-close"),
  terminalClear: document.querySelector("#terminal-clear"),
  terminalScreen: document.querySelector("#terminal-screen"),
  terminalStatus: document.querySelector("#terminal-status"),
};

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `La solicitud falló (${response.status}).`);
  }
  return payload;
}

async function loadCatalog() {
  state.catalog = await requestJson("/api/launcher/catalog");
  renderApplications();
  renderTools();
}

async function refreshStatus() {
  try {
    state.status = await requestJson("/api/launcher/status");
    renderEnvironment(state.status.system);
    renderResources(state.status.system);
    renderOllama(state.status.ollama);
    renderVault(state.status.vault);
    renderApplications();
    elements.lastUpdated.textContent = `Actualizado ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    showToast(error.message || "No se pudo leer el estado local.");
    elements.lastUpdated.textContent = "No se pudo actualizar el estado";
  }
}

function renderEnvironment(system) {
  const rows = [
    ["Sistema", system.os],
    ["Procesador", system.processor],
    ["Arquitectura", system.architecture],
    [
      "Núcleos",
      `${system.cpu_physical_cores ?? "?"} físicos · ` +
        `${system.cpu_logical_cores ?? "?"} lógicos`,
    ],
    ["Shell", system.shell],
    ["Python", `${system.python}${system.venv_active ? " · venv activo" : ""}`],
    ["Directorio", system.project_root],
  ];
  elements.environment.replaceChildren(
    ...rows.map(([label, value]) => {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = label;
      description.textContent = value || "No disponible";
      description.title = value || "";
      row.append(term, description);
      return row;
    }),
  );
}

function renderResources(system) {
  setResource(
    elements.cpuResource,
    system.cpu_percent,
    typeof system.cpu_percent === "number"
      ? `${Math.round(system.cpu_percent)}%`
      : "No disponible",
  );
  setResource(
    elements.memoryResource,
    system.memory_percent,
    formatMemory(system.memory_available_bytes, system.memory_total_bytes),
  );
  const usedDisk = Number.isFinite(system.disk_percent)
    ? system.disk_percent
    : null;
  setResource(
    elements.diskResource,
    usedDisk,
    formatDisk(system.disk_free_bytes, usedDisk),
  );
}

function setResource(element, percent, label) {
  const value = Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
  element.querySelector("strong").textContent = label;
  element.querySelector("progress").value = value;
}

function formatMemory(available, total) {
  if (!Number.isFinite(available) || !Number.isFinite(total) || total <= 0) {
    return "No disponible";
  }
  return `${formatBytes(available)} libres · ${Math.round((1 - available / total) * 100)}%`;
}

function formatDisk(free, percent) {
  if (!Number.isFinite(free) || !Number.isFinite(percent)) return "No disponible";
  return `${formatBytes(free)} libres · ${Math.round(percent)}% usado`;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "No disponible";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function indexPanel(kind) {
  if (kind === "work") {
    return {
      statusPath: "/api/launcher/index/work",
      cancelPath: "/api/launcher/index/work/cancel",
      statusKey: "workIndexStatus",
      configuration: elements.workIndexConfiguration,
      state: elements.workIndexState,
      detail: elements.workIndexDetail,
      progress: elements.workIndexProgress,
      start: elements.workIndexStart,
      cancel: elements.workIndexCancel,
      label: "el índice de trabajo",
    };
  }
  return {
    statusPath: "/api/launcher/index",
    cancelPath: "/api/launcher/index/cancel",
    statusKey: "indexStatus",
    configuration: elements.indexConfiguration,
    state: elements.indexState,
    detail: elements.indexDetail,
    progress: elements.indexProgress,
    start: elements.indexStart,
    cancel: elements.indexCancel,
    label: "la memoria de Companion",
  };
}

async function refreshIndexStatus(kind = "companion") {
  const panel = indexPanel(kind);
  try {
    const previousState = state[panel.statusKey]?.state;
    state[panel.statusKey] = await requestJson(panel.statusPath);
    renderIndexStatus(state[panel.statusKey], panel);
    if (
      ["completed", "cancelled", "failed"].includes(
        state[panel.statusKey].state,
      ) &&
      ["running", "cancelling"].includes(previousState)
    ) {
      showToast(state[panel.statusKey].detail);
    }
  } catch (error) {
    panel.detail.textContent =
      error.message || "No se pudo consultar el estado del índice.";
  }
}

function renderIndexStatus(indexStatus, panel) {
  const config = indexStatus.configuration || {};
  const sourceDirs = (config.source_dirs || []).join(" y ");
  panel.configuration.textContent =
    `Alcance: ${sourceDirs || "configuración local"} · ` +
    `modelo ${config.embedding_model || "local configurado"} · ` +
    `${config.work_interval_seconds ?? "?"} s de trabajo / ` +
    `${config.cooldown_seconds ?? "?"} s de pausa · ` +
    `${config.embedding_threads ?? "?"} hilos para embeddings.`;
  panel.state.textContent = {
    idle: "Lista para iniciar",
    running: "Indexación en curso",
    cancelling: "Detención solicitada",
    completed: "Índice sincronizado",
    cancelled: "Indexación detenida",
    failed: "La indexación encontró un error",
  }[indexStatus.state] || "Preparando indexación";
  panel.detail.textContent = indexStatus.error
    ? `${indexStatus.detail} ${indexStatus.error}`
    : indexStatus.detail;
  const otherStatusKey =
    panel.statusKey === "indexStatus" ? "workIndexStatus" : "indexStatus";
  const otherState = state[otherStatusKey]?.state;
  const otherIndexActive = ["running", "cancelling"].includes(otherState);
  panel.start.disabled =
    ["running", "cancelling"].includes(indexStatus.state) || otherIndexActive;
  if (otherIndexActive) {
    panel.detail.textContent +=
      " El otro índice está activo; solo se ejecuta una indexación a la vez.";
  }
  panel.cancel.disabled = indexStatus.state !== "running";

  const total = Number(indexStatus.total);
  const current = Number(indexStatus.current);
  if (total > 0 && Number.isFinite(current)) {
    panel.progress.value = Math.max(
      0,
      Math.min(100, (current / total) * 100),
    );
  } else if (indexStatus.state === "completed") {
    panel.progress.value = 100;
  } else {
    panel.progress.value = 0;
  }
}

async function startIndexing(kind = "companion") {
  const panel = indexPanel(kind);
  const config = state[panel.statusKey]?.configuration || {};
  const scope = (config.source_dirs || []).join(" y ") || "fuentes configuradas";
  if (!state.status?.ollama?.available) {
    showToast(`Inicia Ollama local antes de indexar ${panel.label}.`);
    return;
  }
  const confirmed = window.confirm(
    `La indexación incremental leerá ${scope} y generará embeddings con ` +
    `${config.embedding_model || "el modelo configurado"} en Ollama local. ` +
    `Puede consumir CPU y memoria durante mucho tiempo. Ritmo configurado: ` +
    `${config.work_interval_seconds ?? "?"} s de trabajo y ` +
    `${config.cooldown_seconds ?? "?"} s de pausa. ` +
    "No hay ejecución diaria automática. ¿Autorizas iniciar ahora?",
  );
  if (!confirmed) return;
  panel.start.disabled = true;
  try {
    await requestJson(panel.statusPath, {
      method: "POST",
      body: JSON.stringify({ confirmed: true }),
    });
    await refreshIndexStatus(kind);
  } catch (error) {
    showToast(error.message || "No se pudo iniciar la indexación.");
    panel.start.disabled = false;
  }
}

async function cancelIndexing(kind = "companion") {
  const panel = indexPanel(kind);
  panel.cancel.disabled = true;
  try {
    await requestJson(panel.cancelPath, {
      method: "POST",
      body: "{}",
    });
    await refreshIndexStatus(kind);
  } catch (error) {
    showToast(error.message || "No se pudo detener la indexación.");
    panel.cancel.disabled = false;
  }
}

function renderOllama(ollama) {
  elements.ollamaIndicator.classList.toggle("ok", ollama.available);
  elements.ollamaIndicator.classList.toggle("bad", !ollama.available);
  elements.ollamaIndicator.setAttribute(
    "aria-label",
    ollama.available ? "Ollama disponible" : "Ollama no disponible",
  );
  elements.ollamaSummary.textContent = ollama.available
    ? `${ollama.model_count} modelos · API local disponible`
    : ollama.detail;
  elements.ollamaStart.disabled = ollama.available || !ollama.local;
  elements.ollamaStart.textContent = ollama.available
    ? "Ollama disponible"
    : "Iniciar Ollama";
}

function renderVault(vault) {
  elements.vaultIndicator.classList.toggle("ok", vault.available);
  elements.vaultIndicator.classList.toggle("bad", !vault.available);
  elements.vaultIndicator.setAttribute(
    "aria-label",
    vault.available ? "Bóveda disponible" : "Bóveda no disponible",
  );
  elements.vaultSummary.textContent = vault.available
    ? vault.path
    : `No encontrada: ${vault.path}`;
  elements.vaultSummary.title = vault.path;
}

function renderApplications() {
  const query = elements.appSearch.value.trim().toLocaleLowerCase();
  const appStates = new Map(
    (state.status?.applications || []).map((app) => [app.id, app]),
  );
  const applications = state.catalog.applications.filter((app) =>
    searchableText(app).includes(query),
  );
  if (!applications.length) {
    elements.appGrid.replaceChildren(emptyState("No hay aplicaciones que coincidan."));
    return;
  }
  elements.appGrid.replaceChildren(
    ...applications.map((app) => renderApplicationCard(app, appStates.get(app.id))),
  );
}

function renderApplicationCard(app, appStatus) {
  const card = document.createElement("article");
  card.className = "app-card";
  const header = document.createElement("div");
  header.className = "card-header";
  const title = document.createElement("h3");
  title.textContent = app.name;
  const category = document.createElement("span");
  category.className = "category-label";
  category.textContent = app.category;
  header.append(title, category);
  const description = document.createElement("p");
  description.textContent = app.description;
  const status = document.createElement("p");
  status.className = `app-status ${appStatus?.available ? "available" : "stopped"}`;
  status.textContent = appStatus?.available
    ? `Disponible · puerto ${appStatus.port}`
    : `Detenida · puerto ${appStatus?.port ?? "configurado"}`;
  const action = document.createElement("button");
  action.className = "button button-primary";
  action.type = "button";
  action.textContent = appStatus?.available ? "Abrir aplicación" : "Iniciar aplicación";
  action.addEventListener("click", () => openApplication(app.id, action));
  card.append(header, description, status, action);
  return card;
}

async function openApplication(applicationId, button) {
  const newWindow = window.open("about:blank", "_blank");
  button.disabled = true;
  button.textContent = "Comprobando servicio…";
  try {
    const result = await requestJson(
      `/api/launcher/apps/${encodeURIComponent(applicationId)}/launch`,
      { method: "POST", body: "{}" },
    );
    if (newWindow) {
      newWindow.opener = null;
      newWindow.location.replace(result.url);
    } else {
      window.location.assign(result.url);
    }
    await refreshStatus();
  } catch (error) {
    newWindow?.close();
    showToast(error.message || "No se pudo iniciar la aplicación.");
  } finally {
    button.disabled = false;
    button.textContent = "Iniciar aplicación";
  }
}

function renderTools() {
  elements.toolGrid.replaceChildren(
    ...state.catalog.tools.map((tool) => {
      const card = document.createElement("article");
      card.className = "app-card tool-card";
      const heading = document.createElement("h3");
      heading.textContent = tool.name;
      const description = document.createElement("p");
      description.textContent = tool.description;
      const command = document.createElement("code");
      command.textContent = tool.command;
      const action = document.createElement("button");
      action.className = "button button-secondary";
      action.type = "button";
      action.textContent = "Preparar en PowerShell";
      action.addEventListener("click", () => prepareCommand(tool.command));
      card.append(heading, description, command, action);
      return card;
    }),
  );
}

function searchableText(item) {
  return [
    item.name,
    item.category,
    item.description,
    ...(item.tags || []),
  ].join(" ").toLocaleLowerCase();
}

function emptyState(message) {
  const empty = document.createElement("p");
  empty.className = "empty-state";
  empty.textContent = message;
  return empty;
}

async function startOllama() {
  elements.ollamaStart.disabled = true;
  elements.ollamaStart.textContent = "Iniciando…";
  try {
    const result = await requestJson("/api/launcher/ollama/start", {
      method: "POST",
      body: "{}",
    });
    showToast(result.detail);
    await refreshStatus();
  } catch (error) {
    showToast(error.message || "No se pudo iniciar Ollama.");
  } finally {
    elements.ollamaStart.disabled = false;
  }
}

function showToast(message) {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  state.toastTimer = window.setTimeout(
    () => elements.toast.classList.remove("visible"),
    4500,
  );
}

function setTerminalStatus(message, connected = false) {
  elements.terminalStatus.textContent = message;
  elements.terminalStatus.classList.toggle("connected", connected);
  elements.terminalClose.disabled = !connected && !state.terminalToken;
}

function openTerminalPanel(prefill = "") {
  elements.terminalPanel.hidden = false;
  elements.terminalToggle.setAttribute("aria-expanded", "true");
  return ensureTerminal(prefill);
}

function hideTerminalPanel() {
  elements.terminalPanel.hidden = true;
  elements.terminalToggle.setAttribute("aria-expanded", "false");
}

async function ensureTerminal(prefill = "") {
  if (state.terminalSocket?.readyState === WebSocket.OPEN) {
    if (prefill) state.terminal?.paste(prefill);
    return;
  }
  if (state.terminalStartup) {
    if (prefill) state.pendingTerminalCommands.push(prefill);
    return state.terminalStartup;
  }
  if (state.terminalSocket?.readyState === WebSocket.CONNECTING) {
    if (prefill) state.pendingTerminalCommands.push(prefill);
    return;
  }
  if (!state.terminalToken && !sessionStorage.getItem("terminal-acknowledged")) {
    const confirmed = window.confirm(
      "Esta es una PowerShell real con los permisos de tu usuario de Windows. " +
      "No es un sandbox y Gatekeeper no inspecciona cada comando. ¿Abrir la sesión?",
    );
    if (!confirmed) return;
    sessionStorage.setItem("terminal-acknowledged", "true");
  }

  setTerminalStatus("Abriendo PowerShell…");
  if (prefill) state.pendingTerminalCommands.push(prefill);
  state.terminalStartup = (async () => {
    await initializeTerminal();
    const dimensions = state.terminalAddon.proposeDimensions() || {
      cols: 100,
      rows: 28,
    };
    const session = await requestJson("/api/launcher/terminal/start", {
      method: "POST",
      body: JSON.stringify(dimensions),
    });
    state.terminalToken = session.token;
    sessionStorage.setItem(TERMINAL_TOKEN_KEY, session.token);
    connectTerminal();
  })();
  try {
    await state.terminalStartup;
  } catch (error) {
    state.pendingTerminalCommands = [];
    setTerminalStatus("No disponible");
    showToast(error.message || "No se pudo abrir PowerShell.");
  } finally {
    state.terminalStartup = null;
  }
}

async function initializeTerminal() {
  if (state.terminal) return;
  state.terminal = new window.Terminal({
    allowProposedApi: false,
    convertEol: true,
    cursorBlink: true,
    fontFamily: "Cascadia Code, Consolas, monospace",
    fontSize: 13,
    scrollback: 5000,
    theme: {
      background: "#090c11",
      foreground: "#e8ecf1",
      cursor: "#f5b301",
      selectionBackground: "#f5b30155",
    },
  });
  state.terminalAddon = new window.FitAddon.FitAddon();
  state.terminal.loadAddon(state.terminalAddon);
  state.terminal.open(elements.terminalScreen);
  state.terminalAddon.fit();
  state.terminal.onData((data) => {
    if (state.terminalSocket?.readyState === WebSocket.OPEN) {
      state.terminalSocket.send(JSON.stringify({ type: "input", data }));
    }
  });
  state.terminal.onResize(({ cols, rows }) => {
    if (state.terminalSocket?.readyState === WebSocket.OPEN) {
      state.terminalSocket.send(JSON.stringify({ type: "resize", cols, rows }));
    }
  });
  window.addEventListener("resize", fitTerminal);
  new ResizeObserver(fitTerminal).observe(elements.terminalScreen);
}

function connectTerminal() {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socketUrl = `${scheme}//${window.location.host}` +
    `/api/launcher/terminal?token=${encodeURIComponent(state.terminalToken)}`;
  const socket = new WebSocket(socketUrl);
  state.terminalSocket = socket;
  socket.addEventListener("open", () => {
    setTerminalStatus("Conectada", true);
    elements.terminalClose.disabled = false;
    fitTerminal();
    window.setTimeout(flushPendingTerminalCommands, 150);
    state.terminal?.focus();
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data === "string") state.terminal?.write(event.data);
  });
  socket.addEventListener("close", (event) => {
    setTerminalStatus(event.code === 1000 ? "PowerShell terminó" : "Desconectada");
    state.pendingTerminalCommands = [];
    if (event.code === 4401 || event.code === 1000) {
      state.terminalToken = "";
      sessionStorage.removeItem(TERMINAL_TOKEN_KEY);
    }
  });
  socket.addEventListener("error", () => {
    setTerminalStatus("Error de conexión");
  });
}

function flushPendingTerminalCommands() {
  if (state.terminalSocket?.readyState !== WebSocket.OPEN) return;
  const commands = state.pendingTerminalCommands.splice(0);
  for (const command of commands) {
    state.terminal?.paste(command);
  }
}

function fitTerminal() {
  if (!state.terminalAddon || elements.terminalPanel.hidden) return;
  state.terminalAddon.fit();
}

async function closeTerminal() {
  const token = state.terminalToken;
  state.terminalSocket?.close(1000, "Sesión cerrada por el usuario");
  state.terminalSocket = null;
  if (!token) {
    setTerminalStatus("Cerrada");
    return;
  }
  elements.terminalClose.disabled = true;
  try {
    await requestJson("/api/launcher/terminal/close", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
    state.terminalToken = "";
    sessionStorage.removeItem(TERMINAL_TOKEN_KEY);
    setTerminalStatus("Sesión cerrada");
  } catch (error) {
    showToast(error.message || "No se pudo cerrar la sesión.");
    elements.terminalClose.disabled = false;
  }
}

async function prepareCommand(command) {
  await openTerminalPanel(command);
}

function updateClock() {
  elements.clock.textContent = new Date().toLocaleString();
}

elements.appSearch.addEventListener("input", renderApplications);
elements.refresh.addEventListener("click", refreshStatus);
elements.indexStart.addEventListener("click", () => startIndexing("companion"));
elements.indexCancel.addEventListener("click", () => cancelIndexing("companion"));
elements.workIndexStart.addEventListener("click", () => startIndexing("work"));
elements.workIndexCancel.addEventListener("click", () => cancelIndexing("work"));
elements.ollamaStart.addEventListener("click", startOllama);
elements.terminalToggle.addEventListener("click", () => openTerminalPanel());
elements.terminalHide.addEventListener("click", hideTerminalPanel);
elements.terminalClose.addEventListener("click", closeTerminal);
elements.terminalClear.addEventListener("click", () => state.terminal?.clear());
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key === "`") {
    event.preventDefault();
    if (elements.terminalPanel.hidden) openTerminalPanel();
    else hideTerminalPanel();
  }
});
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  state.installPrompt = event;
  elements.install.classList.remove("hidden");
});
elements.install.addEventListener("click", async () => {
  if (!state.installPrompt) return;
  state.installPrompt.prompt();
  await state.installPrompt.userChoice;
  state.installPrompt = null;
  elements.install.classList.add("hidden");
});
window.addEventListener("load", () => {
  navigator.serviceWorker?.register("/launcher/launcher-sw.js");
});

loadCatalog()
  .then(async () => {
    await Promise.all([
      refreshStatus(),
      refreshIndexStatus("companion"),
      refreshIndexStatus("work"),
    ]);
  })
  .catch((error) => showToast(error.message || "No se pudo cargar el launcher."));
updateClock();
window.setInterval(updateClock, 30_000);
window.setInterval(refreshStatus, REFRESH_INTERVAL_MS);
window.setInterval(() => {
  if (["running", "cancelling"].includes(state.indexStatus?.state)) {
    refreshIndexStatus("companion");
  }
  if (["running", "cancelling"].includes(state.workIndexStatus?.state)) {
    refreshIndexStatus("work");
  }
}, INDEX_REFRESH_INTERVAL_MS);

if (state.terminalToken) {
  setTerminalStatus("Sesión guardada · pulsa Abrir PowerShell para reconectar");
}
