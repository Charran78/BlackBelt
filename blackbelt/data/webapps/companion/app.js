"use strict";

const DATABASE_NAME = "blackbelt-companion";
const DATABASE_VERSION = 1;
const STORE_NAME = "conversations";
const MAX_HISTORY_MESSAGES = 6;
const MAX_HISTORY_MESSAGE_CHARS = 2000;
const MAX_ATTACHMENTS = 5;
const MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024;
const MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024;
const MODEL_LIST_TIMEOUT_MS = 15_000;

const state = {
  database: null,
  conversations: [],
  activeConversation: null,
  attachments: [],
  jinaSource: null,
  jinaPreview: null,
  jinaExpandedBudgetUsed: false,
  jinaSearchResults: [],
  uploadingAttachments: false,
  model: "",
  cloudModel: "",
  cloudModels: [],
  mode: "deep",
  profileId: "",
  profiles: [],
  busy: false,
  controller: null,
  installPrompt: null,
  toastTimer: null,
  editingProfileId: "",
  cloudConsentResolver: null,
};

const elements = {
  newChat: document.querySelector("#new-chat-button"),
  mode: document.querySelector("#mode-select"),
  modeDescription: document.querySelector("#mode-description"),
  modeCardTitle: document.querySelector("#mode-card-title"),
  modeCardNote: document.querySelector("#mode-card-note"),
  model: document.querySelector("#model-select"),
  refreshModels: document.querySelector("#refresh-models-button"),
  modelState: document.querySelector("#model-state"),
  modelNote: document.querySelector("#model-note"),
  providerStatus: document.querySelector("#provider-status"),
  cloudDialog: document.querySelector("#cloud-preview-dialog"),
  cloudPayload: document.querySelector("#cloud-payload-preview"),
  cloudPayloadHash: document.querySelector("#cloud-payload-hash"),
  cloudReferences: document.querySelector("#cloud-reference-preview"),
  confirmCloud: document.querySelector("#confirm-cloud-send"),
  cancelCloud: document.querySelector("#cancel-cloud-send"),
  profile: document.querySelector("#profile-select"),
  editProfile: document.querySelector("#edit-profile-button"),
  profileDialog: document.querySelector("#profile-dialog"),
  profileForm: document.querySelector("#profile-form"),
  profileDialogTitle: document.querySelector("#profile-dialog-title"),
  profileName: document.querySelector("#profile-name-input"),
  profileRole: document.querySelector("#profile-role-input"),
  profilePurpose: document.querySelector("#profile-purpose-input"),
  profileRules: document.querySelector("#profile-rules-input"),
  profileRulesCount: document.querySelector("#profile-rules-count"),
  closeProfile: document.querySelector("#close-profile-button"),
  cancelProfile: document.querySelector("#cancel-profile-button"),
  deleteProfile: document.querySelector("#delete-profile-button"),
  exportProfile: document.querySelector("#export-profile-button"),
  temperature: document.querySelector("#temperature-input"),
  temperatureValue: document.querySelector("#temperature-value"),
  topP: document.querySelector("#top-p-input"),
  topPValue: document.querySelector("#top-p-value"),
  topK: document.querySelector("#top-k-input"),
  repeatPenalty: document.querySelector("#repeat-penalty-input"),
  context: document.querySelector("#context-input"),
  predict: document.querySelector("#predict-input"),
  conversationList: document.querySelector("#conversation-list"),
  clearHistory: document.querySelector("#clear-history-button"),
  title: document.querySelector("#conversation-title"),
  messages: document.querySelector("#message-list"),
  welcome: document.querySelector("#welcome-state"),
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#chat-input"),
  attachmentInput: document.querySelector("#attachment-input"),
  attachmentTray: document.querySelector("#attachment-tray"),
  attachmentSummary: document.querySelector("#attachment-summary"),
  attachmentList: document.querySelector("#attachment-list"),
  webSourceTray: document.querySelector("#web-source-tray"),
  webSourceName: document.querySelector("#web-source-name"),
  reviewWebSource: document.querySelector("#review-web-source"),
  removeWebSource: document.querySelector("#remove-web-source"),
  readJina: document.querySelector("#read-jina-button"),
  searchJina: document.querySelector("#search-jina-button"),
  jinaSearchDialog: document.querySelector("#jina-search-dialog"),
  jinaSearchQuery: document.querySelector("#jina-search-query"),
  submitJinaSearch: document.querySelector("#submit-jina-search"),
  closeJinaSearch: document.querySelector("#close-jina-search"),
  jinaSearchResults: document.querySelector("#jina-search-results"),
  jinaDialog: document.querySelector("#jina-dialog"),
  jinaUrl: document.querySelector("#jina-url-input"),
  jinaReadUrl: document.querySelector("#read-jina-url"),
  jinaReadStatus: document.querySelector("#jina-read-status"),
  jinaReadStatusMessage: document.querySelector("#jina-read-status-message"),
  retryJinaRead: document.querySelector("#retry-jina-read"),
  cancelJinaReading: document.querySelector("#cancel-jina-reading"),
  jinaContentReview: document.querySelector("#jina-content-review"),
  jinaPageTitle: document.querySelector("#jina-page-title"),
  jinaPageUrl: document.querySelector("#jina-page-url"),
  jinaSourceNotice: document.querySelector("#jina-source-notice"),
  jinaContentPreview: document.querySelector("#jina-content-preview"),
  jinaContentCount: document.querySelector("#jina-content-count"),
  discardJinaSource: document.querySelector("#discard-jina-source"),
  useJinaSource: document.querySelector("#use-jina-source"),
  send: document.querySelector("#send-button"),
  stop: document.querySelector("#stop-button"),
  status: document.querySelector("#interaction-status"),
  export: document.querySelector("#export-button"),
  install: document.querySelector("#install-button"),
  toast: document.querySelector("#toast"),
};

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        database.createObjectStore(STORE_NAME, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("La base local está bloqueada por otra pestaña."));
  });
}

function databaseRequest(mode, operation) {
  return new Promise((resolve, reject) => {
    const transaction = state.database.transaction(STORE_NAME, mode);
    const request = operation(transaction.objectStore(STORE_NAME));
    let result;
    let settled = false;
    request.onsuccess = () => {
      result = request.result;
    };
    request.onerror = () => {
      if (!settled) {
        settled = true;
        reject(request.error);
      }
    };
    transaction.oncomplete = () => {
      if (!settled) {
        settled = true;
        resolve(result);
      }
    };
    transaction.onabort = () => {
      if (!settled) {
        settled = true;
        reject(transaction.error);
      }
    };
  });
}

async function loadConversations() {
  state.conversations = (await databaseRequest(
    "readonly",
    (store) => store.getAll(),
  )).sort((left, right) => right.updatedAt - left.updatedAt);
  renderConversationList();
}

async function persistConversation(conversation) {
  conversation.updatedAt = Date.now();
  await databaseRequest("readwrite", (store) => store.put(conversation));
  await loadConversations();
}

function showToast(message, error = false) {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", error);
  elements.toast.classList.add("visible");
  state.toastTimer = window.setTimeout(
    () => elements.toast.classList.remove("visible"),
    3800,
  );
}

function setStatus(message, error = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", error);
}

function setJinaReadStatus(
  message,
  { error = false, pending = false } = {},
) {
  elements.jinaReadStatusMessage.textContent = message;
  elements.jinaReadStatus.classList.toggle("hidden", !message);
  elements.jinaReadStatus.classList.toggle("error", error);
  elements.jinaReadStatus.classList.toggle("pending", pending);
  elements.retryJinaRead.disabled = state.busy || state.jinaExpandedBudgetUsed;
  elements.retryJinaRead.classList.toggle(
    "hidden",
    state.jinaExpandedBudgetUsed || Boolean(state.jinaPreview),
  );
}

const MODES = {
  general: {
    label: "Chat general",
    description: "sin memoria, respuesta inmediata",
    cardNote: "Conocimiento del modelo · no consulta la bóveda",
  },
  quick: {
    label: "Memoria rápida",
    description: "solo pasajes, puede ser parcial",
    cardNote: "Recuperación local · evidencia parcial",
  },
  deep: {
    label: "Lectura profunda",
    description: "lee conversaciones completas · puede tardar y usar más CPU",
    cardNote: "Lee ventanas completas · citas verificables",
  },
};

function updateModeSummary() {
  const mode = MODES[state.mode] ? state.mode : "deep";
  const details = { ...MODES[mode] };
  if (isCloudModel() && mode === "deep") {
    details.description = "Profundo solo funciona en local · cambia a General o Rápido";
    details.cardNote = "Cloud no recibe la lectura profunda";
  }
  elements.mode.value = mode;
  elements.modeDescription.textContent = details.description;
  elements.modeCardTitle.textContent = details.label;
  elements.modeCardNote.textContent = details.cardNote;
}

function isCloudModel(model = state.model) {
  return state.cloudModels.some((item) => item.model === model);
}

function selectedCloudModel(model = state.model) {
  return state.cloudModels.find((item) => item.model === model) || null;
}

function cloudReadinessMessage(cloudModel) {
  return cloudModel.availability_reason
    || `Modelo no preparado; ejecuta \`${cloudModel.prepare_command}\` `
      + "en una terminal y pulsa Actualizar.";
}

function updateProviderStatus() {
  const cloud = isCloudModel();
  const cloudModel = cloud ? selectedCloudModel() : null;
  elements.providerStatus.textContent = cloud
    ? `Ollama Cloud · ${cloudModel.label} · confirmación obligatoria`
      + `${state.jinaSource ? " · incluye fuente Jina" : ""}`
    : "Solo local";
  updateModeSummary();
  updateContextLimit();
  updateComposerState();
  if (cloud && state.mode === "deep") {
    setStatus("Cloud no admite Lectura profunda; selecciona General o Rápido.", true);
  } else if (cloud && state.attachments.length) {
    setStatus("Cloud solo admite texto; retira los adjuntos antes de continuar.", true);
  } else if (cloud && !cloudModel.available) {
    setStatus(cloudReadinessMessage(cloudModel), true);
  } else {
    setStatus(cloud
      ? "Uso sujeto a los límites de tu cuenta Ollama · revisa el payload antes de cada envío."
      : "Proveedor local seleccionado.");
  }
}

function updateContextLimit() {
  const cloudModel = selectedCloudModel();
  const maxContext = cloudModel ? cloudModel.context_tokens : 16_384;
  for (const option of elements.context.options) {
    option.disabled = Number(option.value) > maxContext;
  }
  const selectedContext = Number(elements.context.value);
  if (selectedContext > maxContext) {
    const allowedOptions = [...elements.context.options]
      .filter((option) => !option.disabled);
    elements.context.value = allowedOptions[allowedOptions.length - 1]?.value || "4096";
  }
}

function readGenerationOptions() {
  const options = {
    temperature: Number(elements.temperature.value),
    top_p: Number(elements.topP.value),
    top_k: Number(elements.topK.value),
    repeat_penalty: Number(elements.repeatPenalty.value),
    num_ctx: Number(elements.context.value),
    num_predict: Number(elements.predict.value),
  };
  for (const input of [
    elements.temperature,
    elements.topP,
    elements.topK,
    elements.repeatPenalty,
    elements.context,
    elements.predict,
  ]) {
    if (!input.reportValidity()) {
      throw new Error("Revisa los parámetros de generación antes de continuar.");
    }
  }
  return options;
}

function applyGenerationOptions(options = {}) {
  elements.temperature.value = options.temperature ?? 0.7;
  elements.topP.value = options.top_p ?? 0.9;
  elements.topK.value = options.top_k ?? 40;
  elements.repeatPenalty.value = options.repeat_penalty ?? 1.1;
  elements.context.value = options.num_ctx ?? 4096;
  elements.predict.value = options.num_predict ?? 1024;
  elements.temperatureValue.value = elements.temperature.value;
  elements.topPValue.value = elements.topP.value;
}

function selectedProfile() {
  return state.profiles.find((profile) => profile.id === state.profileId) || null;
}

function renderProfiles() {
  elements.profile.replaceChildren(new Option("Compañero base", ""));
  for (const profile of state.profiles) {
    const option = new Option(profile.name, profile.id);
    if (!state.model || profile.model !== state.model) {
      option.textContent = `${profile.name} · usa ${profile.model}`;
    }
    elements.profile.append(option);
  }
  elements.profile.value = state.profileId;
  elements.editProfile.textContent = state.profileId ? "Editar perfil" : "Crear perfil";
}

function saveConversationSettings() {
  if (!state.activeConversation) return;
  state.activeConversation.mode = state.mode;
  state.activeConversation.model = state.model;
  state.activeConversation.profileId = state.profileId;
  state.activeConversation.options = readGenerationOptions();
  persistConversation(state.activeConversation).catch((error) => {
    showToast(error.message, true);
  });
}

function renderConversationList() {
  elements.conversationList.replaceChildren();
  elements.clearHistory.disabled = state.conversations.length === 0;
  if (!state.conversations.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = "Tus conversaciones guardadas en este navegador aparecerán aquí.";
    elements.conversationList.append(empty);
    return;
  }

  for (const conversation of state.conversations) {
    const item = document.createElement("div");
    item.className = "conversation-item";
    if (conversation.id === state.activeConversation?.id) {
      item.classList.add("active");
    }
    const openButton = document.createElement("button");
    openButton.className = "conversation-open";
    openButton.type = "button";
    openButton.addEventListener("click", () => selectConversation(conversation.id));

    const title = document.createElement("strong");
    title.textContent = conversation.title || "Conversación nueva";
    const metadata = document.createElement("small");
    metadata.textContent = formatDate(conversation.updatedAt);
    openButton.append(title, metadata);

    const deleteButton = document.createElement("button");
    deleteButton.className = "conversation-delete";
    deleteButton.type = "button";
    deleteButton.textContent = "×";
    deleteButton.title = "Borrar conversación";
    deleteButton.setAttribute("aria-label", `Borrar ${conversation.title}`);
    deleteButton.addEventListener("click", async () => {
      await deleteConversation(conversation.id);
    });
    item.append(openButton, deleteButton);
    elements.conversationList.append(item);
  }
}

function formatDate(timestamp) {
  return new Intl.DateTimeFormat("es", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(timestamp));
}

function renderConversation() {
  const conversation = state.activeConversation;
  const messages = conversation?.messages || [];
  elements.title.textContent = conversation?.title || "Una conversación con continuidad";
  if (conversation) {
    state.mode = MODES[conversation.mode] ? conversation.mode : "deep";
    state.profileId = conversation.profileId || "";
    applyGenerationOptions(conversation.options);
    updateModeSummary();
    renderProfiles();
  }
  elements.export.disabled = messages.length === 0;
  elements.welcome.classList.toggle("hidden", messages.length > 0);
  elements.messages.querySelectorAll(".message").forEach((message) => message.remove());
  for (const message of messages) {
    appendMessage(message);
  }
  scrollToBottom();
  renderConversationList();
}

function appendMessage(message) {
  const row = document.createElement("article");
  row.className = `message ${message.role}`;

  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = message.role === "user" ? "T" : "C";

  const content = document.createElement("div");
  content.className = "message-content";
  const metadata = document.createElement("p");
  metadata.className = "message-meta";
  const modeName = MODES[message.mode]?.label;
  const assistantDetails = [modeName, message.model].filter(Boolean).join(" · ");
  metadata.textContent = message.role === "user"
    ? "Tú"
    : `Compañero${assistantDetails ? ` · ${assistantDetails}` : ""}`;
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = message.content || "";
  content.append(metadata, body);
  if (message.attachments?.length) {
    content.append(renderAttachmentMetadata(message.attachments));
  }
  if (message.role === "assistant" && message.references?.length) {
    content.append(renderReferences(message.references));
  }
  if (message.webSources?.length) {
    content.append(renderWebSources(message.webSources));
  }
  row.append(avatar, content);
  elements.messages.append(row);
  return { row, body, content };
}

function renderWebSources(sources) {
  const section = document.createElement("section");
  section.className = "web-sources";
  const heading = document.createElement("div");
  heading.className = "references-heading";
  heading.textContent = `Lectura web explícita · ${sources.length}`;
  section.append(heading);
  for (const source of sources) {
    const url = safeWebUrl(source.url);
    const item = document.createElement("div");
    item.className = "web-source-reference";
    const title = document.createElement("strong");
    title.textContent = source.title || source.url;
    item.append(title);
    if (url) {
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = url;
      item.append(link);
    } else {
      const address = document.createElement("span");
      address.textContent = source.url || "URL no disponible";
      item.append(address);
    }
    if (source.truncated) {
      const note = document.createElement("small");
      note.textContent = "Se utilizó una extracción truncada.";
      item.append(note);
    }
    if (source.edited) {
      const note = document.createElement("small");
      note.textContent = "Extracto editado manualmente antes del envío.";
      item.append(note);
    }
    section.append(item);
  }
  return section;
}

function safeWebUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol)
      && !url.username && !url.password
      ? url.href
      : "";
  } catch {
    return "";
  }
}

function renderReferences(references) {
  const section = document.createElement("section");
  section.className = "references";
  const heading = document.createElement("div");
  heading.className = "references-heading";
  heading.textContent = `Fuentes verificables · ${references.length}`;
  section.append(heading);

  for (const reference of references) {
    const details = document.createElement("details");
    details.className = "reference-card";
    const summary = document.createElement("summary");
    const alias = document.createElement("span");
    alias.textContent = `[${reference.alias}] `;
    summary.append(alias, document.createTextNode(
      `${reference.title || reference.path} · ${reference.section || "sin sección"}`,
    ));
    const detail = document.createElement("div");
    detail.className = "reference-detail";
    const path = document.createElement("p");
    path.textContent = reference.path;
    const provenance = document.createElement("p");
    const range = reference.char_start === null || reference.char_end === null
      ? "rango no disponible"
      : `caracteres ${reference.char_start}-${reference.char_end}`;
    provenance.textContent = [
      reference.speaker ? `turno: ${reference.speaker}` : "",
      reference.conversation_date ? `fecha: ${reference.conversation_date}` : "",
      range,
    ].filter(Boolean).join(" · ");
    const passage = document.createElement("p");
    passage.className = "reference-passage";
    passage.textContent = reference.passage || "El índice no guardó el texto del pasaje.";
    const hash = document.createElement("p");
    hash.textContent = `SHA-256: ${reference.source_hash || "no disponible"}`;
    detail.append(path, provenance, passage, hash);
    details.append(summary, detail);
    section.append(details);
  }
  return section;
}

function renderAttachmentMetadata(attachments) {
  const list = document.createElement("ul");
  list.className = "message-attachments";
  for (const attachment of attachments) {
    const item = document.createElement("li");
    item.textContent = `${attachment.name} · ${formatBytes(attachment.size_bytes)}`;
    list.append(item);
  }
  return list;
}

function formatBytes(size) {
  if (size < 1024 * 1024) return `${Math.max(1, Math.ceil(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
}

function renderAttachments() {
  elements.attachmentList.replaceChildren();
  const count = state.attachments.length;
  elements.attachmentTray.classList.toggle("hidden", count === 0);
  elements.attachmentSummary.textContent = `${count}/${MAX_ATTACHMENTS} archivos · `
    + `${formatBytes(state.attachments.reduce((total, item) => total + item.size_bytes, 0))}`
    + " / 20 MiB";
  for (const attachment of state.attachments) {
    const item = document.createElement("div");
    item.className = "attachment-item";
    const description = document.createElement("span");
    description.className = "attachment-name";
    description.textContent = `${attachment.name} · ${formatBytes(attachment.size_bytes)}`;
    const remove = document.createElement("button");
    remove.className = "attachment-remove";
    remove.type = "button";
    remove.textContent = "Quitar";
    remove.setAttribute("aria-label", `Quitar ${attachment.name}`);
    remove.disabled = state.busy || state.uploadingAttachments;
    remove.addEventListener("click", () => {
      removeAttachment(attachment.id).catch((error) => showToast(error.message, true));
    });
    item.append(description, remove);
    elements.attachmentList.append(item);
  }
}

function jinaSourcePayload(source) {
  if (!source) return null;
  return {
    url: source.url,
    title: source.title,
    content: source.content,
    truncated: Boolean(source.truncated),
    edited: Boolean(source.edited),
  };
}

function renderJinaSource() {
  const source = state.jinaSource;
  elements.webSourceTray.classList.toggle("hidden", !source);
  if (!source) return;
  const editedLabel = source.edited ? " · extracto editado" : "";
  elements.webSourceName.textContent = `Jina · ${source.title} · `
    + `${source.content.length.toLocaleString("es")} caracteres${editedLabel}`;
  elements.removeWebSource.disabled = state.busy;
  elements.reviewWebSource.disabled = state.busy;
  updateComposerState();
}

function displayJinaDocument(source, staged = false) {
  state.jinaPreview = source;
  const characterCount = source.content.length;
  elements.jinaPageTitle.textContent = source.title || source.url;
  elements.jinaPageUrl.textContent = source.url;
  elements.jinaPageUrl.href = safeWebUrl(source.url);
  elements.jinaSourceNotice.textContent = source.truncated
    ? `Jina truncó la extracción a ${characterCount.toLocaleString("es")} caracteres. `
      + "Se alcanzó el límite de lectura de Jina."
    : `${characterCount.toLocaleString("es")} caracteres extraídos. `
      + (source.edited
        ? "Este extracto ya fue editado manualmente."
        : "Puedes recortarlo aquí antes de añadirlo.");
  elements.jinaContentPreview.value = source.content;
  updateJinaContentCount();
  elements.jinaContentReview.classList.remove("hidden");
  elements.jinaUrl.disabled = staged;
  elements.jinaReadUrl.classList.toggle("hidden", staged);
  elements.useJinaSource.classList.remove("hidden");
  elements.useJinaSource.textContent = staged ? "Actualizar fuente" : "Usar como contexto";
  elements.discardJinaSource.classList.toggle("hidden", !staged);
  elements.cancelJinaReading.textContent = staged ? "Cerrar vista previa" : "Cancelar";
}

function updateJinaContentCount() {
  const characters = elements.jinaContentPreview.value.length;
  elements.jinaContentCount.textContent = `${characters.toLocaleString("es")} / 24.000 caracteres. `
    + "Cloud limita el payload completo a 32.000 caracteres; pregunta e historial "
    + "también cuentan.";
}

function resetJinaDialog() {
  state.jinaPreview = null;
  state.jinaExpandedBudgetUsed = false;
  elements.jinaUrl.disabled = false;
  elements.jinaUrl.value = "";
  elements.jinaReadUrl.textContent = "Enviar URL a Jina y previsualizar";
  elements.jinaContentReview.classList.add("hidden");
  elements.jinaContentPreview.value = "";
  elements.jinaContentCount.textContent = "";
  setJinaReadStatus("");
  elements.jinaReadUrl.classList.remove("hidden");
  elements.jinaReadUrl.disabled = false;
  elements.retryJinaRead.classList.remove("hidden");
  elements.retryJinaRead.disabled = false;
  elements.useJinaSource.classList.add("hidden");
  elements.discardJinaSource.classList.add("hidden");
  elements.cancelJinaReading.textContent = "Cancelar";
}

function openJinaDialog() {
  if (state.busy || state.uploadingAttachments) return;
  if (state.jinaSource && !window.confirm(
    "Ya hay una lectura Jina preparada. ¿Quieres sustituirla por otra URL?",
  )) {
    return;
  }
  resetJinaDialog();
  elements.jinaDialog.showModal();
  elements.jinaUrl.focus();
}

function openJinaSearchDialog() {
  if (state.busy || state.uploadingAttachments) return;
  if (state.jinaSource && !window.confirm(
    "Ya hay una lectura Jina preparada. ¿Quieres buscar una fuente para sustituirla?",
  )) {
    return;
  }
  state.jinaSearchResults = [];
  elements.jinaSearchQuery.value = "";
  elements.jinaSearchResults.replaceChildren();
  elements.jinaSearchResults.classList.add("hidden");
  elements.jinaSearchDialog.showModal();
  elements.jinaSearchQuery.focus();
}

function renderJinaSearchResults(results) {
  state.jinaSearchResults = results;
  elements.jinaSearchResults.replaceChildren();
  for (const result of results) {
    const url = safeWebUrl(result.url);
    if (!url) continue;
    const card = document.createElement("article");
    card.className = "jina-search-result";
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = result.title || result.url;
    const address = document.createElement("span");
    address.className = "jina-search-result-url";
    address.textContent = result.url;
    const snippet = document.createElement("p");
    snippet.textContent = result.snippet || "Jina no devolvió un extracto.";
    const read = document.createElement("button");
    read.className = "button button-quiet";
    read.type = "button";
    read.textContent = "Leer y revisar con Jina";
    read.addEventListener("click", () => {
      readSelectedSearchResult(result).catch((error) => {
        setStatus(error.message, true);
        showToast(error.message, true);
      });
    });
    card.append(link, address, snippet, read);
    elements.jinaSearchResults.append(card);
  }
  elements.jinaSearchResults.classList.remove("hidden");
}

async function searchWebWithJina() {
  if (state.busy || !elements.jinaSearchQuery.reportValidity()) return;
  state.busy = true;
  state.controller = new AbortController();
  elements.stop.classList.remove("hidden");
  elements.send.classList.add("hidden");
  elements.submitJinaSearch.disabled = true;
  setStatus("Enviando solo la consulta escrita a Jina Search...");
  updateComposerState();
  try {
    const response = await fetch("/api/companion/jina-search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: elements.jinaSearchQuery.value.trim() }),
      signal: state.controller.signal,
    });
    const result = await readJsonResponse(response);
    renderJinaSearchResults(result.results || []);
    setStatus(
      `${state.jinaSearchResults.length} resultados recibidos. `
      + "Elige uno para revisar su contenido con Jina Reader.",
    );
  } catch (error) {
    if (error.name === "AbortError") {
      setStatus("Búsqueda Jina cancelada antes de recibir resultados.");
    } else {
      setStatus(error.message, true);
      showToast(error.message, true);
    }
  } finally {
    state.busy = false;
    state.controller = null;
    elements.stop.classList.add("hidden");
    elements.send.classList.remove("hidden");
    updateComposerState();
  }
}

async function readSelectedSearchResult(result) {
  if (state.busy) return;
  elements.jinaSearchDialog.close();
  resetJinaDialog();
  elements.jinaUrl.value = result.url;
  elements.jinaDialog.showModal();
  await readJinaUrl();
}

async function readJinaUrl({ tokenBudget = 4_000 } = {}) {
  if (state.busy || !elements.jinaUrl.reportValidity()) return;
  state.busy = true;
  state.controller = new AbortController();
  elements.stop.classList.remove("hidden");
  elements.send.classList.add("hidden");
  elements.jinaReadUrl.disabled = true;
  elements.jinaReadUrl.textContent = "Leyendo página...";
  elements.retryJinaRead.classList.add("hidden");
  setJinaReadStatus(
    `Jina Reader está procesando la URL con un límite de ${tokenBudget.toLocaleString("es")} tokens. `
      + "La lectura es anónima, sin API key. Puede tardar hasta 45 segundos; "
      + "puedes cancelar sin añadir contenido.",
    { pending: true },
  );
  setStatus("Enviando solo la URL a Jina Reader...");
  updateComposerState();
  try {
    const response = await fetch("/api/companion/jina-read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: elements.jinaUrl.value.trim(),
        token_budget: tokenBudget,
      }),
      signal: state.controller.signal,
    });
    const result = await readJsonResponse(response);
    displayJinaDocument(result);
    setJinaReadStatus(
      "Lectura recibida. Revisa el contenido antes de añadirlo a la consulta.",
    );
    setStatus("Revisa la extracción; todavía no se ha añadido a tu consulta.");
  } catch (error) {
    if (error.name === "AbortError") {
      if (elements.jinaDialog.open) {
        setJinaReadStatus("Lectura cancelada; no se añadió contenido.");
      }
      setStatus("Lectura Jina cancelada antes de añadir contenido.");
    } else {
      const budgetGuidance = /BudgetExceededError|Token budget .* exceeded/i.test(
        error.message,
      )
        ? tokenBudget === 4_000 && !state.jinaExpandedBudgetUsed
          ? "La página supera el límite normal de 4.000 tokens. Puedes elegir "
            + "la lectura ampliada de 15.000 tokens; requiere confirmación."
          : `Jina también rechazó la lectura con el límite de ${tokenBudget.toLocaleString("es")} tokens.`
        : "";
      const message = `${error.message} ${budgetGuidance}`.trim();
      if (elements.jinaDialog.open) {
        setJinaReadStatus(message, { error: true });
      }
      setStatus(message, true);
    }
  } finally {
    state.busy = false;
    state.controller = null;
    elements.jinaReadUrl.textContent = "Enviar URL a Jina y previsualizar";
    elements.retryJinaRead.disabled = state.jinaExpandedBudgetUsed;
    elements.stop.classList.add("hidden");
    elements.send.classList.remove("hidden");
    updateComposerState();
  }
}

function readJinaWithExpandedBudget() {
  if (state.busy || state.jinaExpandedBudgetUsed) return;
  const confirmed = window.confirm(
    "¿Reintentar esta URL una sola vez con un máximo de 15.000 tokens? "
      + "Jina Reader no recibirá una API key. No se guardará ningún cambio "
      + "de presupuesto; se aplican los límites gratuitos de solicitudes de Jina.",
  );
  if (!confirmed) return;
  state.jinaExpandedBudgetUsed = true;
  readJinaUrl({ tokenBudget: 15_000 }).catch((error) => {
    setJinaReadStatus(error.message, { error: true });
  });
}

function useJinaSource() {
  if (!state.jinaPreview) return;
  if (!elements.jinaContentPreview.reportValidity()) return;
  const content = elements.jinaContentPreview.value;
  if (!content.trim()) {
    elements.jinaContentPreview.setCustomValidity("El extracto no puede estar vacío.");
    elements.jinaContentPreview.reportValidity();
    elements.jinaContentPreview.setCustomValidity("");
    return;
  }
  const edited = Boolean(state.jinaPreview.edited)
    || content !== state.jinaPreview.content;
  state.jinaSource = {
    ...jinaSourcePayload(state.jinaPreview),
    content,
    edited,
  };
  renderJinaSource();
  updateProviderStatus();
  elements.jinaDialog.close();
  setStatus(edited
    ? `Fuente Jina actualizada con un extracto editado de ${content.length.toLocaleString("es")} caracteres.`
    : "La lectura Jina queda preparada para esta conversación hasta que la retires.");
}

function removeJinaSource() {
  state.jinaSource = null;
  state.jinaPreview = null;
  renderJinaSource();
  updateProviderStatus();
  setStatus("Lectura Jina retirada; no se añadirá a la consulta.");
}

function clearTransientJinaSource() {
  state.jinaSource = null;
  state.jinaPreview = null;
  renderJinaSource();
}

function reviewJinaSource() {
  if (!state.jinaSource || state.busy) return;
  displayJinaDocument(state.jinaSource, true);
  elements.jinaDialog.showModal();
}

async function uploadAttachments(fileList) {
  const files = [...fileList];
  elements.attachmentInput.value = "";
  if (!files.length || state.busy) return;
  if (state.attachments.length + files.length > MAX_ATTACHMENTS) {
    showToast(`Puedes adjuntar hasta ${MAX_ATTACHMENTS} archivos por turno.`, true);
    return;
  }
  const selectedBytes = files.reduce((total, file) => total + file.size, 0);
  const currentBytes = state.attachments.reduce(
    (total, item) => total + item.size_bytes,
    0,
  );
  if (files.some((file) => file.size > MAX_ATTACHMENT_BYTES)) {
    showToast("Cada archivo debe ocupar como máximo 8 MiB.", true);
    return;
  }
  if (selectedBytes + currentBytes > MAX_TOTAL_ATTACHMENT_BYTES) {
    showToast("El total de adjuntos no puede superar 20 MiB.", true);
    return;
  }

  state.uploadingAttachments = true;
  updateComposerState();
  try {
    for (const file of files) {
      setStatus(`Leyendo ${file.name} localmente...`);
      const form = new FormData();
      form.append("file", file, file.name);
      const response = await fetch("/api/companion/attachments", {
        method: "POST",
        body: form,
      });
      const result = await readJsonResponse(response);
      state.attachments.push(result.attachment);
      renderAttachments();
    }
    setStatus("Adjuntos listos; su contenido solo se enviará a Ollama local.");
  } finally {
    state.uploadingAttachments = false;
    updateComposerState();
  }
}

async function removeAttachment(id) {
  const attachment = state.attachments.find((item) => item.id === id);
  if (!attachment) return;
  state.attachments = state.attachments.filter((item) => item.id !== id);
  renderAttachments();
  const response = await fetch(
    `/api/companion/attachments/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
  if (!response.ok) await readJsonResponse(response);
  updateProviderStatus();
}

async function clearAttachments() {
  const attachments = state.attachments;
  state.attachments = [];
  renderAttachments();
  updateProviderStatus();
  await Promise.allSettled(attachments.map(async (attachment) => {
    await fetch(`/api/companion/attachments/${encodeURIComponent(attachment.id)}`, {
      method: "DELETE",
      keepalive: true,
    });
  }));
}

async function selectConversation(id) {
  if (state.busy || state.uploadingAttachments) return;
  const conversation = state.conversations.find((item) => item.id === id);
  if (!conversation) return;
  await clearAttachments();
  clearTransientJinaSource();
  state.activeConversation = conversation;
  if ([...elements.model.options].some((option) => option.value === conversation.model)) {
    state.model = conversation.model;
    elements.model.value = state.model;
  }
  state.mode = MODES[conversation.mode] ? conversation.mode : "deep";
  state.profileId = conversation.profileId || "";
  applyGenerationOptions(conversation.options);
  updateProviderStatus();
  renderProfiles();
  localStorage.setItem("blackbelt-companion-active", id);
  renderConversation();
}

function startNewConversation() {
  if (state.busy || state.uploadingAttachments) return;
  clearAttachments().catch((error) => showToast(error.message, true));
  clearTransientJinaSource();
  state.activeConversation = null;
  state.mode = "deep";
  state.profileId = "";
  applyGenerationOptions();
  updateProviderStatus();
  renderProfiles();
  localStorage.removeItem("blackbelt-companion-active");
  renderConversation();
  elements.input.focus();
}

async function deleteConversation(id) {
  await databaseRequest("readwrite", (store) => store.delete(id));
  if (state.activeConversation?.id === id) {
    state.activeConversation = null;
    localStorage.removeItem("blackbelt-companion-active");
  }
  await loadConversations();
  renderConversation();
}

async function clearHistory() {
  if (!state.conversations.length) return;
  if (!window.confirm("¿Borrar todas las conversaciones guardadas en este navegador?")) {
    return;
  }
  await databaseRequest("readwrite", (store) => store.clear());
  state.activeConversation = null;
  localStorage.removeItem("blackbelt-companion-active");
  await loadConversations();
  renderConversation();
  showToast("Historial local borrado.");
}

async function loadModels() {
  try {
    const previousModel = state.model;
    const response = await fetch("/api/companion/models", {
      cache: "no-store",
      signal: AbortSignal.timeout(MODEL_LIST_TIMEOUT_MS),
    });
    const result = await readJsonResponse(response);
    elements.model.replaceChildren();
    state.cloudModels = result.cloud_models || [];
    state.cloudModel = state.cloudModels.some(
      (item) => item.model === result.cloud_model,
    )
      ? result.cloud_model
      : state.cloudModels[0]?.model || "";
    for (const model of result.models) {
      elements.model.append(new Option(model, model));
    }
    if (!result.models.length) {
      const unavailable = new Option("Modelos locales no registrados", "");
      unavailable.disabled = true;
      elements.model.append(unavailable);
    }
    for (const cloudModel of state.cloudModels) {
      const suffix = cloudModel.available
        ? ` · contexto ${cloudModel.context_description}`
        : " · preparación requerida";
      const cloudOption = new Option(
        `Ollama Cloud · ${cloudModel.label}${suffix}`,
        cloudModel.model,
      );
      cloudOption.title = cloudModel.block_reason
        || `Contexto máximo publicado: ${cloudModel.context_description}`;
      elements.model.append(cloudOption);
    }
    const previousModelStillExists = result.models.includes(previousModel)
      || state.cloudModels.some((item) => item.model === previousModel);
    state.model = previousModelStillExists
      ? previousModel
      : result.default_model || result.models[0] || state.cloudModel;
    elements.model.value = state.model;
    elements.model.disabled = !state.model;
    elements.modelState.classList.remove("offline");
    elements.modelState.setAttribute(
      "aria-label",
      result.models.length
        ? "Ollama local disponible"
        : "Selecciona un modelo Cloud",
    );
    updateProviderStatus();
  } catch (error) {
    elements.model.replaceChildren(new Option("Ollama no disponible", ""));
    elements.model.disabled = true;
    elements.modelState.classList.add("offline");
    elements.modelState.setAttribute("aria-label", "Ollama local no disponible");
    elements.modelNote.textContent = error.message;
    setStatus(error.message, true);
    updateComposerState();
  }
}

async function loadProfiles() {
  try {
    const response = await fetch("/api/companion/profiles", { cache: "no-store" });
    const result = await readJsonResponse(response);
    state.profiles = result.profiles || [];
    renderProfiles();
  } catch (error) {
    state.profiles = [];
    renderProfiles();
    showToast(`No se pudieron cargar los perfiles: ${error.message}`, true);
  }
}

function openProfileEditor() {
  const profile = selectedProfile();
  state.editingProfileId = profile?.id || "";
  elements.profileDialogTitle.textContent = profile ? "Editar agente" : "Crear agente";
  elements.profileName.value = profile?.name || "";
  elements.profileRole.value = profile?.role || "";
  elements.profilePurpose.value = profile?.purpose || "";
  elements.profileRules.value = (profile?.rules || []).join("\n");
  updateProfileRuleCount();
  elements.deleteProfile.classList.toggle("hidden", !profile);
  elements.exportProfile.classList.toggle("hidden", !profile);
  elements.profileDialog.showModal();
  elements.profileName.focus();
}

function profileRulesFromInput() {
  return elements.profileRules.value
    .split(/\r?\n/)
    .map((rule) => rule.trim())
    .filter(Boolean);
}

function updateProfileRuleCount() {
  const rules = profileRulesFromInput();
  const oversizedRule = rules.findIndex((rule) => rule.length > 500);
  const invalid = rules.length > 20 || oversizedRule >= 0;
  elements.profileRulesCount.textContent = oversizedRule >= 0
    ? `Regla ${oversizedRule + 1}: ${rules[oversizedRule].length} / 500 caracteres.`
    : `${rules.length} / 20 reglas · máximo 500 caracteres por regla.`;
  elements.profileRulesCount.classList.toggle("error", invalid);
}

function profileSlug(name) {
  return name.normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48);
}

async function saveProfile(event) {
  event.preventDefault();
  const name = elements.profileName.value.trim();
  const id = state.editingProfileId || profileSlug(name);
  const rules = profileRulesFromInput();
  if (!id || !rules.length) {
    showToast("Añade un nombre válido y al menos una regla.", true);
    return;
  }
  if (rules.length > 20) {
    showToast("Un perfil admite como máximo 20 reglas.", true);
    return;
  }
  if (rules.some((rule) => rule.length > 500)) {
    showToast("Cada regla puede tener como máximo 500 caracteres.", true);
    return;
  }
  if (!state.editingProfileId && state.profiles.some((profile) => profile.id === id)) {
    showToast("Ese identificador ya existe; cambia el nombre del perfil.", true);
    return;
  }
  const options = readGenerationOptions();
  if (options.num_ctx > 16_384) {
    showToast(
      "Los perfiles locales admiten hasta 16.384 tokens de contexto. "
        + "Reduce la ventana de contexto antes de guardar.",
      true,
    );
    return;
  }
  const profile = {
    id,
    name,
    role: elements.profileRole.value.trim(),
    purpose: elements.profilePurpose.value.trim(),
    rules,
    model: state.model,
    ...options,
  };
  const submit = elements.profileForm.querySelector('button[type="submit"]');
  const originalLabel = submit.textContent;
  submit.disabled = true;
  submit.textContent = "Guardando...";
  try {
    const response = await fetch(`/api/companion/profiles/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    });
    await readJsonResponse(response);
    await loadProfiles();
    state.profileId = id;
    renderProfiles();
    saveConversationSettings();
    elements.profileDialog.close();
    showToast("Perfil local guardado.");
  } finally {
    submit.disabled = false;
    submit.textContent = originalLabel;
  }
}

async function deleteSelectedProfile() {
  const profile = selectedProfile();
  if (!profile) return;
  if (!window.confirm(`¿Borrar el perfil «${profile.name}» de este equipo?`)) return;
  const response = await fetch(
    `/api/companion/profiles/${encodeURIComponent(profile.id)}`,
    { method: "DELETE" },
  );
  if (!response.ok) await readJsonResponse(response);
  state.profiles = state.profiles.filter((item) => item.id !== profile.id);
  state.profileId = "";
  renderProfiles();
  saveConversationSettings();
  elements.profileDialog.close();
  showToast("Perfil local borrado.");
}

async function exportSelectedModelfile() {
  const profile = selectedProfile();
  if (!profile) return;
  const response = await fetch(
    `/api/companion/profiles/${encodeURIComponent(profile.id)}/modelfile`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    await readJsonResponse(response);
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${profile.id}.Modelfile`;
  link.click();
  URL.revokeObjectURL(url);
}

function closeProfileEditor() {
  elements.profileDialog.close();
}

async function readJsonResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(formatApiError(payload.detail) || `Error HTTP ${response.status}`);
  }
  return payload;
}

function formatApiError(detail) {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return "";

  const issues = detail
    .map((issue) => {
      if (!issue || typeof issue !== "object") return "";
      const location = Array.isArray(issue.loc)
        ? issue.loc.filter((part) => !["body", "query", "path"].includes(part))
        : [];
      const field = location
        .map((part) => (
          typeof part === "number" ? `[${part + 1}]` : String(part)
        ))
        .join(".")
        .replace(/\.\[/g, "[");
      const message = typeof issue.msg === "string" ? issue.msg : "";
      if (!message) return "";
      return field ? `${field}: ${translateValidationMessage(message)}` : message;
    })
    .filter(Boolean);
  return issues.join(" · ");
}

function translateValidationMessage(message) {
  const translations = [
    [/^Field required$/i, "Campo obligatorio."],
    [
      /^String should have at least (\d+) characters$/i,
      (length) => `Debe tener al menos ${length} caracteres.`,
    ],
    [
      /^String should have at most (\d+) characters$/i,
      (length) => `No puede superar ${length} caracteres.`,
    ],
    [
      /^Input should be greater than or equal to (.+)$/i,
      (minimum) => `Debe ser como mínimo ${minimum}.`,
    ],
    [
      /^Input should be less than or equal to (.+)$/i,
      (maximum) => `Debe ser como máximo ${maximum}.`,
    ],
  ];
  for (const [pattern, replacement] of translations) {
    if (typeof replacement === "string" && pattern.test(message)) return replacement;
    const match = message.match(pattern);
    if (match && typeof replacement === "function") {
      return replacement(match[1]);
    }
  }
  return message;
}

function updateComposerState() {
  const available = Boolean(state.model);
  const cloud = isCloudModel();
  const cloudModel = cloud ? selectedCloudModel() : null;
  const cloudModeUnsupported = cloud && state.mode === "deep";
  const cloudNotReady = cloud && (
    !cloudModel.available
  );
  elements.mode.querySelector('option[value="deep"]').disabled = cloud;
  elements.input.disabled = !available || state.busy;
  elements.send.disabled = !available || state.busy || state.uploadingAttachments
    || !elements.input.value.trim() || cloudModeUnsupported || cloudNotReady
    || (cloud && state.attachments.length > 0);
  elements.attachmentInput.disabled = !available || state.busy
    || state.uploadingAttachments || cloud;
  elements.readJina.disabled = state.busy || state.uploadingAttachments;
  elements.searchJina.disabled = state.busy || state.uploadingAttachments;
  elements.submitJinaSearch.disabled = state.busy;
  elements.reviewWebSource.disabled = state.busy || !state.jinaSource;
  elements.removeWebSource.disabled = state.busy || !state.jinaSource;
  elements.model.disabled = !available || state.busy;
  elements.refreshModels.disabled = state.busy;
  elements.modelNote.textContent = cloud
    ? cloudReadinessMessage(cloudModel) || (
      `Ollama Cloud · contexto hasta ${cloudModel.context_description}. `
      + "El payload exacto exige confirmación."
      + " El uso depende de los límites de tu cuenta Ollama."
    )
    : state.model
      ? "Modelo local · conversación y consulta permanecen en este equipo."
      : "No hay modelos locales registrados en Ollama.";
  elements.mode.disabled = state.busy;
  elements.profile.disabled = state.busy || cloud;
  elements.editProfile.disabled = state.busy || cloud;
  elements.newChat.disabled = state.busy || state.uploadingAttachments;
  elements.clearHistory.disabled = state.conversations.length === 0
    || state.busy || state.uploadingAttachments;
  elements.conversationList.querySelectorAll("button").forEach((button) => {
    button.disabled = state.busy || state.uploadingAttachments;
  });
}

function showCloudPreview(preview) {
  elements.cloudPayload.textContent = JSON.stringify(preview.payload, null, 2);
  elements.cloudPayloadHash.textContent = preview.payload_sha256;
  elements.cloudReferences.replaceChildren();
  if (preview.references?.length) {
    elements.cloudReferences.append(renderReferences(preview.references));
  } else {
    const note = document.createElement("p");
    note.textContent = "Este envío no incluye referencias de la memoria.";
    elements.cloudReferences.append(note);
  }
  if (preview.web_sources?.length) {
    elements.cloudReferences.append(renderWebSources(preview.web_sources));
  }
  elements.cloudDialog.showModal();
  return new Promise((resolve) => {
    state.cloudConsentResolver = resolve;
  });
}

function finishCloudConsent(confirmed) {
  const resolve = state.cloudConsentResolver;
  state.cloudConsentResolver = null;
  if (elements.cloudDialog.open) elements.cloudDialog.close();
  resolve?.(confirmed);
}

async function consumeCloudPreview(token, confirm) {
  const response = await fetch("/api/companion/cloud-send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, confirm }),
    signal: state.controller.signal,
  });
  return readJsonResponse(response);
}

function createConversation() {
  return {
    id: crypto.randomUUID(),
    title: "Conversación nueva",
    model: state.model,
    mode: state.mode,
    profileId: state.profileId,
    options: readGenerationOptions(),
    createdAt: Date.now(),
    updatedAt: Date.now(),
    messages: [],
  };
}

async function sendQuestion(question) {
  const query = question.trim();
  if (!query || state.busy || state.uploadingAttachments || !state.model) return;
  const options = readGenerationOptions();
  const mode = state.mode;
  const profileId = state.profileId || null;
  const attachments = state.attachments.map((item) => ({ ...item }));
  const jinaSource = jinaSourcePayload(state.jinaSource);
  state.busy = true;
  state.controller = new AbortController();
  elements.stop.classList.remove("hidden");
  elements.send.classList.add("hidden");
  updateComposerState();

  let conversation = null;
  let assistantMessage = null;
  let messageStatus = null;
  let view = null;
  let deepPreview = null;
  let cloudPreview = null;
  let userMessageSaved = false;
  let completed = false;
  let streamError = "";
  const startedAt = performance.now();
  try {
    const cloud = isCloudModel();
    if (cloud && (mode === "deep" || profileId || attachments.length)) {
      throw new Error(
        "Ollama Cloud solo admite los modos General o Rápido, sin perfiles ni adjuntos.",
      );
    }
    if (mode === "deep") {
      setStatus("Calculando el coste de lectura completa...");
      const previewResponse = await fetch("/api/companion/deep-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, conversations: 3 }),
        signal: state.controller.signal,
      });
      deepPreview = await readJsonResponse(previewResponse);
      if (deepPreview.requires_confirmation) {
        const approved = window.confirm(
          `La lectura profunda abarcará ${deepPreview.window_count} ventanas `
          + `de ${deepPreview.conversation_count} conversaciones. `
          + "Puede tardar y mantener la CPU ocupada. ¿Continuar?",
        );
        if (!approved) {
          setStatus("Lectura profunda cancelada; no se envió al modelo.");
          return;
        }
      }
    }

    const existingMessages = state.activeConversation?.messages || [];
    const history = existingMessages
      .slice(-MAX_HISTORY_MESSAGES)
      .map(({ role, content }) => ({
        role,
        content: content.slice(0, MAX_HISTORY_MESSAGE_CHARS),
      }));
    if (cloud) {
      setStatus("Preparando el payload Cloud para tu revisión; aún no se envía.");
      const previewResponse = await fetch("/api/companion/cloud-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          model: state.model,
          mode,
          history,
          options,
          limit: 5,
          jina_source: jinaSource,
        }),
        signal: state.controller.signal,
      });
      cloudPreview = await readJsonResponse(previewResponse);
      const approved = await showCloudPreview(cloudPreview);
      if (!approved) {
        setStatus("Envío Cloud cancelado; no se envió al modelo.");
        await consumeCloudPreview(cloudPreview.token, false);
        cloudPreview = null;
        return;
      }
    }

    conversation = state.activeConversation || createConversation();
    conversation.messages.push({
      role: "user",
      content: query,
      mode,
      model: state.model,
      attachments: attachments.map(({ id: _id, ...metadata }) => metadata),
      webSources: jinaSource ? [webSourceMetadata(jinaSource)] : [],
      createdAt: Date.now(),
    });
    if (conversation.messages.length === 1) {
      conversation.title = query.length > 64 ? `${query.slice(0, 61)}...` : query;
    }
    conversation.model = state.model;
    conversation.mode = mode;
    conversation.profileId = profileId;
    conversation.options = options;
    state.activeConversation = conversation;
    elements.input.value = "";
    resizeInput();
    renderConversation();
    await persistConversation(conversation);
    userMessageSaved = true;

    assistantMessage = {
      role: "assistant",
      content: "",
      mode,
      model: state.model,
      provider: cloud ? "Ollama Cloud" : "local",
      references: [],
      webSources: [],
      createdAt: Date.now(),
    };
    view = appendMessage(assistantMessage);
    messageStatus = document.createElement("div");
    messageStatus.className = "message-status running";
    messageStatus.textContent = "Preparando respuesta...";
    view.content.append(messageStatus);

    const response = cloud
      ? await fetch("/api/companion/cloud-send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: cloudPreview.token, confirm: true }),
        signal: state.controller.signal,
      })
      : await fetch("/api/companion/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          model: state.model,
          mode,
          profile_id: profileId,
          options,
          history,
          limit: 5,
          deep_preview_token: deepPreview?.token || null,
          confirm_deep_over_20: Boolean(deepPreview?.requires_confirmation),
          attachment_ids: attachments.map((attachment) => attachment.id),
          jina_source: jinaSource,
        }),
        signal: state.controller.signal,
      });
    if (!response.ok) {
      const error = await readJsonResponse(response).catch(
        (cause) => new Error(cause.message),
      );
      throw error;
    }
    await consumeEventStream(response, (event, data) => {
      if (event === "status") {
        messageStatus.textContent = data.message;
        setStatus(data.message);
      } else if (event === "token") {
        assistantMessage.content += data.text;
        view.body.textContent = assistantMessage.content;
        scrollToBottom();
      } else if (event === "complete") {
        completed = true;
        assistantMessage.references = data.references || [];
        assistantMessage.webSources = data.web_sources || [];
        if (assistantMessage.references.length) {
          view.content.append(renderReferences(assistantMessage.references));
        }
        if (assistantMessage.webSources.length) {
          view.content.append(renderWebSources(assistantMessage.webSources));
        }
        messageStatus.classList.remove("running");
        messageStatus.textContent = `${MODES[mode].label} · ${cloud ? "Cloud" : "local"} · `
          + `${assistantMessage.references.length} fuentes · `
          + `${((performance.now() - startedAt) / 1000).toFixed(1)} s`;
      } else if (event === "error") {
        streamError = data.message || "No se pudo completar la consulta.";
      }
    });

    if (streamError) throw new Error(streamError);
    if (!completed) throw new Error("La conexión terminó antes de completar la respuesta.");
    conversation.messages.push(assistantMessage);
    await persistConversation(conversation);
    setStatus("Respuesta completada.");
  } catch (error) {
    const wasCancelled = error.name === "AbortError";
    const message = wasCancelled
      ? assistantMessage?.content
        ? "Interacción detenida. La respuesta parcial se conservó en el historial local."
        : "Interacción detenida antes de recibir texto."
      : error.message || "No se pudo completar la consulta.";
    if (messageStatus) {
      messageStatus.classList.remove("running");
      messageStatus.classList.add("error");
      messageStatus.textContent = message;
    }
    setStatus(message, true);
    if (assistantMessage?.content && conversation) {
      assistantMessage.interrupted = true;
      conversation.messages.push(assistantMessage);
      await persistConversation(conversation);
    }
    if (wasCancelled && !userMessageSaved) {
      setStatus("Lectura detenida antes de enviar la pregunta al modelo.");
    }
    if (!wasCancelled) showToast(message, true);
  } finally {
    state.busy = false;
    state.controller = null;
    elements.stop.classList.add("hidden");
    elements.send.classList.remove("hidden");
    updateComposerState();
    renderConversationList();
    elements.input.focus();
  }
}

async function consumeEventStream(response, onEvent) {
  if (!response.body) throw new Error("El navegador no admite respuestas en streaming.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let separator = buffer.indexOf("\n\n");
    while (separator !== -1) {
      dispatchEventFrame(buffer.slice(0, separator), onEvent);
      buffer = buffer.slice(separator + 2);
      separator = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) dispatchEventFrame(buffer, onEvent);
}

function dispatchEventFrame(frame, onEvent) {
  let eventName = "message";
  const dataLines = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return;
  try {
    onEvent(eventName, JSON.parse(dataLines.join("\n")));
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new Error("El servidor envió un evento de chat no válido.");
    }
    throw error;
  }
}

function stopInteraction() {
  state.controller?.abort();
}

function resizeInput() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 180)}px`;
  updateComposerState();
}

function scrollToBottom() {
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function exportConversation() {
  const conversation = state.activeConversation;
  if (!conversation?.messages.length) return;
  const lines = [
    `# ${conversation.title}`,
    "",
    `Modelo seleccionado: ${conversation.model}`,
    `Fecha: ${new Date(conversation.createdAt).toLocaleString("es")}`,
    "",
  ];
  for (const message of conversation.messages) {
    lines.push(
      `## ${message.role === "user" ? "Tú" : "Compañero"}`,
      "",
      message.content,
      "",
    );
    if (message.attachments?.length) {
      lines.push(
        `Adjuntos locales: ${message.attachments.map((item) => item.name).join(", ")}`,
        "",
      );
    }
    for (const source of message.webSources || []) {
      lines.push(`Fuente web: ${source.title || source.url}`, "", source.url, "");
    }
    for (const reference of message.references || []) {
      lines.push(
        `> [${reference.alias}] ${reference.path} · ${reference.section || "sin sección"} · ${reference.source_hash}`,
      );
    }
    if (message.references?.length) lines.push("");
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `companion_${new Date().toISOString().slice(0, 10)}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

function webSourceMetadata(source) {
  return {
    url: source.url,
    title: source.title,
    truncated: Boolean(source.truncated),
    edited: Boolean(source.edited),
  };
}

function handleInstallPrompt(event) {
  event.preventDefault();
  state.installPrompt = event;
  elements.install.classList.remove("hidden");
}

async function installApplication() {
  if (!state.installPrompt) return;
  state.installPrompt.prompt();
  await state.installPrompt.userChoice;
  state.installPrompt = null;
  elements.install.classList.add("hidden");
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("/companion/companion-sw.js").catch(() => {
    showToast("No se pudo activar el modo instalable.", true);
  });
}

elements.newChat.addEventListener("click", startNewConversation);
elements.clearHistory.addEventListener("click", () => {
  clearHistory().catch((error) => showToast(error.message, true));
});
elements.export.addEventListener("click", exportConversation);
elements.stop.addEventListener("click", stopInteraction);
elements.attachmentInput.addEventListener("change", () => {
  uploadAttachments(elements.attachmentInput.files).catch((error) => {
    setStatus(error.message, true);
    showToast(error.message, true);
  });
});
elements.readJina.addEventListener("click", openJinaDialog);
elements.searchJina.addEventListener("click", openJinaSearchDialog);
elements.submitJinaSearch.addEventListener("click", () => {
  searchWebWithJina().catch((error) => showToast(error.message, true));
});
elements.closeJinaSearch.addEventListener("click", () => {
  if (state.busy) state.controller?.abort();
  elements.jinaSearchDialog.close();
});
elements.jinaSearchDialog.addEventListener("cancel", () => {
  if (state.busy) state.controller?.abort();
});
elements.jinaSearchDialog.querySelector("form").addEventListener("submit", (event) => {
  event.preventDefault();
});
elements.jinaReadUrl.addEventListener("click", () => {
  readJinaUrl().catch((error) => {
    setStatus(error.message, true);
    showToast(error.message, true);
  });
});
elements.retryJinaRead.addEventListener("click", readJinaWithExpandedBudget);
elements.useJinaSource.addEventListener("click", useJinaSource);
elements.jinaContentPreview.addEventListener("input", updateJinaContentCount);
elements.discardJinaSource.addEventListener("click", () => {
  removeJinaSource();
  elements.jinaDialog.close();
});
elements.cancelJinaReading.addEventListener("click", () => {
  if (state.busy) state.controller?.abort();
  elements.jinaDialog.close();
});
elements.jinaDialog.addEventListener("cancel", () => {
  if (state.busy) state.controller?.abort();
});
elements.jinaDialog.addEventListener("close", resetJinaDialog);
elements.jinaDialog.querySelector("form").addEventListener("submit", (event) => {
  event.preventDefault();
});
elements.reviewWebSource.addEventListener("click", reviewJinaSource);
elements.removeWebSource.addEventListener("click", removeJinaSource);
elements.install.addEventListener("click", installApplication);
elements.mode.addEventListener("change", () => {
  state.mode = elements.mode.value;
  updateProviderStatus();
  saveConversationSettings();
});
elements.confirmCloud.addEventListener("click", () => finishCloudConsent(true));
elements.cancelCloud.addEventListener("click", () => finishCloudConsent(false));
elements.cloudDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  finishCloudConsent(false);
});
elements.cloudDialog.addEventListener("close", () => {
  if (state.cloudConsentResolver) finishCloudConsent(false);
});
elements.profile.addEventListener("change", () => {
  const profileId = elements.profile.value;
  const profile = state.profiles.find((item) => item.id === profileId);
  if (profile && ![...elements.model.options].some((item) => item.value === profile.model)) {
    showToast(`Instala ${profile.model} para usar este perfil.`, true);
    elements.profile.value = state.profileId;
    return;
  }
  state.profileId = profileId;
  if (profile) {
    state.model = profile.model;
    elements.model.value = profile.model;
    applyGenerationOptions(profile);
  }
  renderProfiles();
  saveConversationSettings();
});
elements.editProfile.addEventListener("click", openProfileEditor);
elements.closeProfile.addEventListener("click", closeProfileEditor);
elements.cancelProfile.addEventListener("click", closeProfileEditor);
elements.profileForm.addEventListener("submit", (event) => {
  saveProfile(event).catch((error) => showToast(error.message, true));
});
elements.profileRules.addEventListener("input", updateProfileRuleCount);
elements.deleteProfile.addEventListener("click", () => {
  deleteSelectedProfile().catch((error) => showToast(error.message, true));
});
elements.exportProfile.addEventListener("click", () => {
  exportSelectedModelfile().catch((error) => showToast(error.message, true));
});
elements.model.addEventListener("change", () => {
  state.model = elements.model.value;
  if (selectedProfile()?.model !== state.model) {
    state.profileId = "";
    renderProfiles();
  }
  if (state.activeConversation) {
    state.activeConversation.model = state.model;
  }
  updateProviderStatus();
  saveConversationSettings();
});
elements.refreshModels.addEventListener("click", () => {
  loadModels().catch((error) => showToast(error.message, true));
});
elements.temperature.addEventListener("input", () => {
  elements.temperatureValue.value = elements.temperature.value;
});
elements.topP.addEventListener("input", () => {
  elements.topPValue.value = elements.topP.value;
});
for (const parameter of [
  elements.temperature,
  elements.topP,
  elements.topK,
  elements.repeatPenalty,
  elements.context,
  elements.predict,
]) {
  parameter.addEventListener("change", saveConversationSettings);
}
elements.input.addEventListener("input", resizeInput);
elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.form.requestSubmit();
  }
});
elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendQuestion(elements.input.value).catch((error) => {
    setStatus(error.message, true);
    showToast(error.message, true);
  });
});
elements.messages.addEventListener("click", (event) => {
  const suggestion = event.target.closest(".suggestion");
  if (!suggestion) return;
  elements.input.value = suggestion.textContent;
  resizeInput();
  elements.form.requestSubmit();
});
window.addEventListener("beforeinstallprompt", handleInstallPrompt);

async function initialize() {
  registerServiceWorker();
  const modelsLoading = loadModels();
  try {
    state.database = await openDatabase();
    await loadConversations();
    await modelsLoading;
    const activeId = localStorage.getItem("blackbelt-companion-active");
    if (activeId) await selectConversation(activeId);
  } catch (error) {
    setStatus(`No se pudo abrir el historial local: ${error.message}`, true);
    showToast("El almacenamiento local no está disponible en este navegador.", true);
  }
  await loadProfiles();
}

initialize();
