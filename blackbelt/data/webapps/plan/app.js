"use strict";

const state = {
  drafts: [],
  archived: [],
  approved: [],
  selectedId: null,
  selectedKind: null,
  detail: null,
  installPrompt: null,
  toastTimer: null,
  contextRequestId: 0,
  panoramaPreview: null,
  cloudWorkflow: null,
};

const elements = {
  client: document.querySelector("#client-select"),
  project: document.querySelector("#project-id"),
  hubSources: document.querySelector("#hub-source-ids"),
  hubPreviewButton: document.querySelector("#preview-panorama-button"),
  hubReview: document.querySelector("#panorama-review"),
  hubDiff: document.querySelector("#panorama-diff"),
  hubConfirmButton: document.querySelector("#confirm-panorama-button"),
  hubCancelButton: document.querySelector("#cancel-panorama-button"),
  proposals: document.querySelector("#proposal-ids"),
  meetings: document.querySelector("#meeting-ids"),
  dossiers: document.querySelector("#dossier-ids"),
  prepareForm: document.querySelector("#prepare-form"),
  prepareButton: document.querySelector("#prepare-button"),
  refreshButton: document.querySelector("#refresh-button"),
  printButton: document.querySelector("#print-button"),
  installButton: document.querySelector("#install-button"),
  draftList: document.querySelector("#draft-list"),
  archivedList: document.querySelector("#archived-list"),
  approvedList: document.querySelector("#approved-list"),
  draftCount: document.querySelector("#draft-count"),
  archivedCount: document.querySelector("#archived-count"),
  approvedCount: document.querySelector("#approved-count"),
  emptyState: document.querySelector("#empty-state"),
  detail: document.querySelector("#detail-content"),
  toast: document.querySelector("#toast"),
};

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `Error HTTP ${response.status}`);
  }
  return payload;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = String(text);
  return element;
}

function button(label, className, onClick, disabled = false) {
  const element = node("button", `button ${className}`, label);
  element.type = "button";
  element.disabled = disabled;
  element.addEventListener("click", onClick);
  return element;
}

function showToast(message, isError = false) {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", isError);
  elements.toast.classList.add("visible");
  state.toastTimer = window.setTimeout(
    () => elements.toast.classList.remove("visible"),
    4200,
  );
}

async function loadClients() {
  try {
    const clients = await requestJson("/api/plan/clients");
    const previousClient = elements.client.value;
    elements.client.replaceChildren();
    const placeholder = node("option", "", "Selecciona un cliente");
    placeholder.value = "";
    elements.client.append(placeholder);
    for (const client of clients) {
      const option = node("option", "", `${client.client_id} · ${client.name}`);
      option.value = client.client_id;
      elements.client.append(option);
    }
    if (!clients.length) {
      elements.client.replaceChildren(
        node("option", "", "No hay clientes disponibles"),
      );
      elements.client.disabled = true;
      elements.prepareButton.disabled = true;
      await loadClientSources();
      return;
    }
    elements.client.disabled = false;
    elements.client.value = clients.some(
      (client) => client.client_id === previousClient,
    )
      ? previousClient
      : "";
    await loadClientSources();
  } catch (error) {
    elements.client.replaceChildren(
      node("option", "", "No se pudieron cargar los clientes"),
    );
    elements.client.disabled = true;
    elements.prepareButton.disabled = true;
    await loadClientSources();
    showToast(error.message, true);
  }
}

async function loadClientSources() {
  const requestId = ++state.contextRequestId;
  const clientId = elements.client.value;
  const requestedProjectId = elements.project.value;
  cancelProjectPanorama();
  elements.hubSources.replaceChildren(
    node("option", "", "Selecciona primero un proyecto"),
  );
  elements.hubSources.options[0].value = "";
  elements.hubSources.disabled = true;
  elements.hubPreviewButton.disabled = true;
  const selectors = [
    [elements.project, "Sin proyecto seleccionado"],
    [elements.proposals, "Sin propuesta seleccionada"],
    [elements.meetings, "No hay reuniones vinculadas"],
    [elements.dossiers, "No hay dossieres vinculados"],
  ];
  for (const [selector, placeholder] of selectors) {
    selector.replaceChildren(node("option", "", placeholder));
    selector.options[0].value = "";
    selector.disabled = true;
  }
  elements.prepareButton.disabled = !clientId;
  if (!clientId) {
    await loadProjectHubSources();
    return;
  }

  try {
    const projectQuery = requestedProjectId
      ? `&project_id=${encodeURIComponent(requestedProjectId)}`
      : "";
    const context = await requestJson(
      `/api/plan/context?client_id=${encodeURIComponent(clientId)}` +
        projectQuery,
    );
    if (
      requestId !== state.contextRequestId ||
      clientId !== elements.client.value
    ) {
      return;
    }
    for (const [selector, options, placeholder] of [
      [elements.project, context.projects, "Sin proyecto seleccionado"],
      [elements.proposals, context.proposals, "Sin propuesta seleccionada"],
      [elements.meetings, context.meetings, "No hay reuniones vinculadas"],
      [elements.dossiers, context.dossiers, "No hay dossieres vinculados"],
    ]) {
      selector.replaceChildren();
      const empty = node("option", "", placeholder);
      empty.value = "";
      if (selector !== elements.project) empty.disabled = true;
      empty.selected =
        selector === elements.project
          ? !requestedProjectId
          : options.length === 0;
      selector.append(empty);
      for (const option of options) {
        const item = node("option", "", `${option.id} · ${option.title}`);
        item.value = option.id;
        item.selected = selector === elements.project
          ? option.id === requestedProjectId
          : Boolean(requestedProjectId);
        selector.append(item);
      }
      selector.disabled = options.length === 0;
    }
    await loadProjectHubSources();
    elements.prepareButton.disabled = false;
  } catch (error) {
    if (requestId === state.contextRequestId) {
      elements.prepareButton.disabled = true;
      showToast(error.message, true);
    }
  }
}

async function loadProjectHubSources() {
  state.panoramaPreview = null;
  elements.hubReview.hidden = true;
  elements.hubDiff.textContent = "";
  elements.hubSources.replaceChildren(
    node("option", "", "Selecciona primero un proyecto"),
  );
  elements.hubSources.options[0].value = "";
  elements.hubSources.disabled = true;
  elements.hubPreviewButton.disabled = true;
  const clientId = elements.client.value;
  const projectId = elements.project.value;
  if (!clientId || !projectId) return;

  try {
    const response = await requestJson(
      `/api/plan/projects/${encodeURIComponent(projectId)}/sources` +
        `?client_id=${encodeURIComponent(clientId)}`,
    );
    if (clientId !== elements.client.value || projectId !== elements.project.value) {
      return;
    }
    elements.hubSources.replaceChildren();
    for (const source of response.sources) {
      const option = node(
        "option",
        "",
        `${source.selected ? "Vinculada" : "Disponible para vincular"} · ` +
          `${source.kind} · ${source.id} · ${source.title}`,
      );
      option.value = source.id;
      option.selected = source.selected;
      elements.hubSources.append(option);
    }
    if (!response.sources.length) {
      elements.hubSources.append(
        node("option", "", "No hay fuentes vinculadas disponibles"),
      );
    }
    elements.hubSources.disabled = response.sources.length === 0;
    elements.hubPreviewButton.disabled = response.sources.length === 0;
  } catch (error) {
    showToast(error.message, true);
  }
}

async function previewProjectPanorama() {
  const sourceIds = Array.from(elements.hubSources.selectedOptions)
    .map((option) => option.value)
    .filter(Boolean);
  if (!elements.client.value || !elements.project.value || !sourceIds.length) {
    showToast("Selecciona un proyecto y al menos una fuente.", true);
    return;
  }
  elements.hubPreviewButton.disabled = true;
  elements.hubPreviewButton.textContent = "Generando panorama local...";
  try {
    const preview = await requestJson(
      `/api/plan/projects/${encodeURIComponent(elements.project.value)}` +
        "/panorama/preview",
      {
        method: "POST",
        body: JSON.stringify({
          client_id: elements.client.value,
          source_ids: sourceIds,
        }),
      },
    );
    state.panoramaPreview = preview;
    elements.hubDiff.textContent = preview.diff || "No hay cambios.";
    elements.hubReview.hidden = false;
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.hubPreviewButton.disabled = false;
    elements.hubPreviewButton.textContent = "Generar y revisar diff";
  }
}

async function confirmProjectPanorama() {
  if (!state.panoramaPreview) return;
  elements.hubConfirmButton.disabled = true;
  try {
    const result = await requestJson(
      `/api/plan/projects/${encodeURIComponent(state.panoramaPreview.project_id)}` +
        "/panorama/confirm",
      {
        method: "POST",
        body: JSON.stringify({
          token: state.panoramaPreview.token,
          confirm: true,
        }),
      },
    );
    state.panoramaPreview = null;
    elements.hubReview.hidden = true;
    showToast(result.message);
    await loadClientSources();
    openObsidian(result.obsidian_uri);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.hubConfirmButton.disabled = false;
  }
}

function cancelProjectPanorama() {
  state.panoramaPreview = null;
  elements.hubReview.hidden = true;
  elements.hubDiff.textContent = "";
}

async function loadWorkspace({ keepSelection = true } = {}) {
  const previousId = keepSelection ? state.selectedId : null;
  try {
    const [drafts, archived, approved] = await Promise.all([
      requestJson("/api/plan/drafts"),
      requestJson("/api/plan/archived"),
      requestJson("/api/plan/approved"),
    ]);
    state.drafts = drafts;
    state.archived = archived;
    state.approved = approved;
    renderQueues();
    elements.draftCount.textContent = String(drafts.length);
    elements.archivedCount.textContent = String(archived.length);
    elements.approvedCount.textContent = String(approved.length);

    if (previousId && drafts.some((draft) => draft.plan_id === previousId)) {
      await selectPlan(previousId, "draft");
    } else if (
      previousId &&
      approved.some((plan) => plan.plan_id === previousId)
    ) {
      await selectPlan(previousId, "approved");
    } else if (drafts.length) {
      await selectPlan(drafts[0].plan_id, "draft");
    } else if (approved.length) {
      await selectPlan(approved[0].plan_id, "approved");
    } else {
      clearDetail();
    }
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderQueues() {
  renderPlanList(elements.draftList, state.drafts, "draft");
  renderArchivedList();
  renderPlanList(elements.approvedList, state.approved, "approved");
}

function renderArchivedList() {
  elements.archivedList.replaceChildren();
  if (!state.archived.length) {
    elements.archivedList.append(
      node("p", "plan-list-empty", "No hay borradores cancelados o pospuestos."),
    );
    return;
  }
  for (const record of state.archived) {
    const row = node("div", "archived-row");
    const summary = node("div", "plan-row-copy");
    summary.append(
      node("strong", "", record.plan_id),
      node(
        "small",
        "",
        `${record.proyecto || record.cliente || "Sin proyecto"} · pospuesto`,
      ),
    );
    row.append(
      summary,
      button("Restaurar", "button-quiet", () =>
        restoreArchivedDraft(record.plan_id),
      ),
    );
    elements.archivedList.append(row);
  }
}

function renderPlanList(container, records, kind) {
  container.replaceChildren();
  if (!records.length) {
    container.append(
      node(
        "p",
        "plan-list-empty",
        kind === "draft"
          ? "Todavía no hay borradores en la carpeta de revisión."
          : "Aún no hay planes publicados.",
      ),
    );
    return;
  }

  for (const record of records) {
    const row = node("button", "plan-row");
    row.type = "button";
    row.classList.toggle(
      "selected",
      state.selectedId === record.plan_id && state.selectedKind === kind,
    );
    row.setAttribute(
      "aria-label",
      `${record.plan_id}, ${record.proyecto || record.cliente}`,
    );
    const mark = node(
      "span",
      "plan-row-mark",
      kind === "approved" ? "OK" : "PL",
    );
    const copy = node("span", "plan-row-copy");
    copy.append(
      node("strong", "", record.plan_id),
      node(
        "small",
        "",
        `${record.proyecto || record.cliente || "Sin proyecto"} · ${
          kind === "approved"
            ? record.actualizado || "aprobado"
            : `${record.tareas} tareas · ${record.errores.length} errores`
        }`,
      ),
    );
    const status = node("span", "plan-row-status");
    status.classList.toggle("ready", kind === "approved");
    status.classList.toggle(
      "invalid",
      kind === "draft" && record.errores.length > 0,
    );
    row.append(mark, copy, status);
    row.addEventListener("click", () => selectPlan(record.plan_id, kind));
    container.append(row);
  }
}

async function selectPlan(planId, kind) {
  state.selectedId = planId;
  state.selectedKind = kind;
  renderQueues();
  state.cloudWorkflow = null;
  try {
    const endpoint =
      kind === "draft"
        ? `/api/plan/drafts/${encodeURIComponent(planId)}`
        : `/api/plan/approved/${encodeURIComponent(planId)}`;
    state.detail = await requestJson(endpoint);
    renderDetail();
  } catch (error) {
    showToast(error.message, true);
  }
}

function clearDetail() {
  state.selectedId = null;
  state.selectedKind = null;
  state.detail = null;
  elements.detail.replaceChildren();
  elements.detail.classList.add("hidden");
  elements.emptyState.classList.remove("hidden");
  elements.printButton.disabled = true;
}

function renderDetail() {
  const detail = state.detail;
  if (!detail) return;
  const plan = detail.plan;
  const isApproved = state.selectedKind === "approved";
  const errors = detail.issues.filter((issue) => issue.level === "error");
  const warnings = detail.issues.filter((issue) => issue.level !== "error");

  elements.emptyState.classList.add("hidden");
  elements.detail.replaceChildren();
  elements.detail.classList.remove("hidden");
  elements.printButton.disabled = false;

  const header = node("div", "detail-header");
  const heading = node("div");
  heading.append(
    node("p", "eyebrow", isApproved ? "PLAN PUBLICADO" : "BORRADOR EN REVISIÓN"),
    node("h2", "", plan.proyecto_nombre || plan.proyecto || plan.id),
    node(
      "p",
      "detail-subtitle",
      `${plan.id} · Cliente ${plan.cliente} · Actualizado ${
        plan.actualizado || "sin fecha"
      }`,
    ),
  );
  const status = node(
    "span",
    `status-pill${isApproved ? " approved" : errors.length ? " invalid" : ""}`,
    isApproved ? "Aprobado · 021" : errors.length ? "Requiere atención" : "En revisión · 022",
  );
  header.append(heading, status);
  elements.detail.append(header);

  const actions = node("div", "detail-actions");
  actions.append(
    button("Abrir en Obsidian", "button-quiet", () =>
      openObsidian(detail.obsidian_uri),
    ),
  );
  if (isApproved) {
    actions.append(
      button("Crear revisión editable", "button-primary", () =>
        revisePlan(plan.id),
      ),
    );
  } else {
    if (!errors.length) {
      actions.append(
        button("Mejorar con Cloud", "button-cloud", () =>
          previewCloudImprovement(plan.id),
        ),
      );
    }
    actions.append(
      button("Volver a validar", "button-quiet", () => validateSelected()),
      button(
        detail.approved_exists ? "Actualizar aprobado" : "Aprobar plan",
        "button-approve",
        () => approvePlan(plan.id, detail.approved_exists, errors.length > 0),
        errors.length > 0,
      ),
      button(
        "Cancelar / posponer",
        "button-danger",
        () => archiveDraft(plan.id),
      ),
    );
  }
  elements.detail.append(actions);
  renderCloudWorkflow();

  renderValidation(errors, warnings);
  renderStats(plan);
  renderSection("Objetivo", plan.objetivo);
  renderStringGroups("Alcance", [
    { label: "Incluye", items: plan.alcance?.incluye || [] },
    { label: "No incluye", items: plan.alcance?.excluye || [] },
  ]);
  renderEntityList(
    "Entregables",
    plan.entregables,
    (item) => `${item.id} · ${item.titulo}`,
    (item) =>
      [item.criterio_aceptacion, item.fecha_estimada && `Fecha propuesta: ${item.fecha_estimada}`]
        .filter(Boolean)
        .join(" · "),
  );
  renderEntityList(
    "Tareas propuestas",
    plan.tareas,
    (item) => `${item.id} · ${item.titulo}`,
    (item) =>
      [
        item.entregable && `Entregable: ${item.entregable}`,
        item.estimacion?.prob_h
          ? `Estimación probable: ${item.estimacion.prob_h} h`
          : "Estimación pendiente de revisión humana",
        item.criterio_aceptacion,
      ]
        .filter(Boolean)
        .join(" · "),
  );
  renderEntityList(
    "Fases propuestas",
    plan.fases_propuestas,
    (item) => item.fase,
    (item) => [item.actividad, item.fecha && `Fecha externa: ${item.fecha}`]
      .filter(Boolean)
      .join(" · "),
  );
  renderEntityList(
    "Riesgos",
    plan.riesgos,
    (item) => item.descripcion,
    (item) => item.mitigacion,
  );
  renderEntityList(
    "Restricciones candidatas",
    plan.restricciones,
    (item) => item.descripcion,
    (item) =>
      [
        item.tipo,
        `Categoría: ${item.categoria}`,
        `No negociable: ${item.no_negociable ? "sí" : "no"}`,
        item.motivo,
        item.fuentes?.length && `Fuentes: ${item.fuentes.join(", ")}`,
      ]
        .filter(Boolean)
        .join(" · "),
  );
  renderEntityList(
    "Supuestos",
    plan.supuestos,
    (item) => item.descripcion,
    (item) => item.impacto_si_falla,
  );
  renderEntityList(
    "Preguntas abiertas",
    plan.preguntas,
    (item) => item.texto,
    (item) => item.estado,
  );
  renderSection("Notas de revisión", detail.narrative);
  renderSources(plan.fuentes || []);
}

function renderCloudWorkflow() {
  const workflow = state.cloudWorkflow;
  if (!workflow || workflow.planId !== state.selectedId) return;
  const panel = node("section", "cloud-review detail-section");
  panel.append(
    node("h3", "", "Mejora Cloud · revisión humana obligatoria"),
  );

  if (workflow.phase === "preview") {
    panel.append(
      node("p", "", workflow.preview.privacy_notice),
      node(
        "p",
        "cloud-summary",
        `Modelo: ${workflow.preview.model} · Entregables sin tareas: ${
          workflow.preview.uncovered_deliverables.length
        } · SHA-256 de la solicitud: ${workflow.preview.request_sha256}`,
      ),
    );
    const counts = workflow.preview.redaction_counts;
    const redactions = Object.entries(counts)
      .filter(([, count]) => count > 0)
      .map(([kind, count]) => `${kind}: ${count}`)
      .join(" · ");
    panel.append(
      node(
        "p",
        "cloud-summary",
        redactions
          ? `Redacciones y límites aplicados: ${redactions}`
          : "No se detectaron datos para redactar automáticamente; revisa el JSON igualmente.",
      ),
    );
    const request = node("pre", "cloud-payload");
    request.textContent = JSON.stringify(workflow.preview.request, null, 2);
    panel.append(request);
    const actions = node("div", "cloud-review-actions");
    actions.append(
      button("Enviar este payload a Cloud", "button-cloud", () =>
        confirmCloudGeneration(),
      ),
      button("Cancelar sin enviar", "button-quiet", () =>
        cancelCloudImprovement(),
      ),
    );
    panel.append(actions);
  } else if (workflow.phase === "result") {
    panel.append(
      node(
        "p",
        "",
        "La respuesta está validada, pero todavía no ha modificado el plan. " +
          "Marca individualmente lo que quieras incorporar.",
      ),
    );
    if (workflow.result.duplicate_count) {
      panel.append(
        node(
          "p",
          "cloud-warning",
          `Se omitieron ${workflow.result.duplicate_count} sugerencias duplicadas.`,
        ),
      );
    }
    const candidates = [
      ...workflow.result.tasks.map((item) => ({
        ...item,
        kind: "task",
        label: "Tarea propuesta",
        summary: `${item.entregable} · ${item.fase} · estimación pendiente`,
      })),
      ...workflow.result.constraints.map((item) => ({
        ...item,
        kind: "constraint",
        label: "Restricción candidata",
        summary: `${item.tipo} · no negociable: no`,
      })),
    ];
    if (!candidates.length) {
      panel.append(
        node("p", "cloud-warning", "Cloud no propuso tareas ni restricciones. El plan sigue intacto."),
      );
    }
    for (const item of candidates) {
      const label = node("label", "cloud-candidate");
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.value = item.suggestion_id;
      checkbox.dataset.suggestionKind = item.kind;
      const content = node("span");
      content.append(
        node("strong", "", `${item.label}: ${item.titulo || item.descripcion}`),
        node(
          "small",
          "",
          [
            item.descripcion,
            item.criterio_aceptacion,
            item.motivo,
            item.summary,
            item.fuentes?.length && `Fuentes: ${item.fuentes.join(", ")}`,
          ]
            .filter(Boolean)
            .join(" · "),
        ),
      );
      label.append(checkbox, content);
      panel.append(label);
    }
    const actions = node("div", "cloud-review-actions");
    actions.append(
      button("Aplicar seleccionadas", "button-cloud", () =>
        applyCloudSuggestions(panel),
        !candidates.length,
      ),
      button("Descartar respuesta", "button-quiet", () =>
        discardCloudSuggestions(),
      ),
    );
    panel.append(actions);
  }
  elements.detail.append(panel);
}

async function previewCloudImprovement(planId) {
  try {
    const preview = await requestJson(
      `/api/plan/drafts/${encodeURIComponent(planId)}/cloud-improve/preview`,
      { method: "POST", body: "{}" },
    );
    state.cloudWorkflow = { phase: "preview", planId, preview };
    renderDetail();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function confirmCloudGeneration() {
  const workflow = state.cloudWorkflow;
  if (!workflow || workflow.phase !== "preview") return;
  const approved = window.confirm(
    `Se enviará a ${workflow.preview.model} exactamente el JSON mostrado. ` +
      "La redacción no garantiza anonimización completa. ¿Confirmas este envío?",
  );
  if (!approved) return;
  const buttons = elements.detail.querySelectorAll(".cloud-review-actions button");
  buttons.forEach((item) => {
    item.disabled = true;
  });
  try {
    const result = await requestJson(
      `/api/plan/drafts/${encodeURIComponent(workflow.planId)}` +
        "/cloud-improve/generate",
      {
        method: "POST",
        body: JSON.stringify({ token: workflow.preview.token, confirm: true }),
      },
    );
    state.cloudWorkflow = { phase: "result", planId: workflow.planId, result };
    renderDetail();
  } catch (error) {
    state.cloudWorkflow = null;
    renderDetail();
    showToast(error.message, true);
  }
}

async function cancelCloudImprovement() {
  const workflow = state.cloudWorkflow;
  if (!workflow || workflow.phase !== "preview") return;
  try {
    await requestJson(
      `/api/plan/drafts/${encodeURIComponent(workflow.planId)}` +
        "/cloud-improve/generate",
      {
        method: "POST",
        body: JSON.stringify({ token: workflow.preview.token, confirm: false }),
      },
    );
  } catch (error) {
    showToast(error.message, true);
  }
  state.cloudWorkflow = null;
  renderDetail();
  showToast("Vista previa descartada; no se envió nada.");
}

async function applyCloudSuggestions(panel) {
  const workflow = state.cloudWorkflow;
  if (!workflow || workflow.phase !== "result") return;
  const taskIds = Array.from(
    panel.querySelectorAll(
      'input[data-suggestion-kind="task"]:checked',
    ),
  ).map((input) => input.value);
  const constraintIds = Array.from(
    panel.querySelectorAll(
      'input[data-suggestion-kind="constraint"]:checked',
    ),
  ).map((input) => input.value);
  if (!taskIds.length && !constraintIds.length) {
    showToast("Marca al menos una sugerencia para aplicarla.", true);
    return;
  }
  if (
    !window.confirm(
      `Se incorporarán ${taskIds.length} tareas y ${constraintIds.length} ` +
        "restricciones candidatas. Las estimaciones quedarán vacías. ¿Aplicar?",
    )
  ) {
    return;
  }
  try {
    const applied = await requestJson(
      `/api/plan/drafts/${encodeURIComponent(workflow.planId)}` +
        "/cloud-improve/apply",
      {
        method: "POST",
        body: JSON.stringify({
          token: workflow.result.token,
          task_ids: taskIds,
          constraint_ids: constraintIds,
        }),
      },
    );
    state.cloudWorkflow = null;
    showToast(applied.message);
    await loadWorkspace({ keepSelection: false });
    await selectPlan(applied.plan_id, "draft");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function discardCloudSuggestions() {
  const workflow = state.cloudWorkflow;
  if (!workflow || workflow.phase !== "result") return;
  if (!window.confirm("¿Descartar todas las sugerencias Cloud? El plan no cambiará.")) {
    return;
  }
  try {
    await requestJson(
      `/api/plan/drafts/${encodeURIComponent(workflow.planId)}` +
        "/cloud-improve/discard",
      {
        method: "POST",
        body: JSON.stringify({ token: workflow.result.token }),
      },
    );
  } catch (error) {
    showToast(error.message, true);
  }
  state.cloudWorkflow = null;
  renderDetail();
  showToast("Sugerencias descartadas; el borrador no cambió.");
}

function renderValidation(errors, warnings) {
  const box = node(
    "div",
    `validation-box${errors.length ? " error" : warnings.length ? " warning" : ""}`,
  );
  if (!errors.length && !warnings.length) {
    box.append(
      node("div", "validation-line", "✓ Validación correcta; fuentes sin cambios."),
    );
  }
  for (const issue of errors) {
    box.append(
      node("div", "validation-line", `× ${issue.path}: ${issue.message}`),
    );
  }
  for (const issue of warnings) {
    box.append(
      node("div", "validation-line", `! ${issue.path}: ${issue.message}`),
    );
  }
  elements.detail.append(box);
}

function renderStats(plan) {
  const stats = node("div", "stats-grid");
  for (const [label, value] of [
    ["Entregables", plan.entregables?.length || 0],
    ["Tareas", plan.tareas?.length || 0],
    ["Preguntas", plan.preguntas?.length || 0],
    ["Fuentes", plan.fuentes?.length || 0],
  ]) {
    const card = node("div", "stat-card");
    card.append(node("span", "", label), node("strong", "", value));
    stats.append(card);
  }
  elements.detail.append(stats);
}

function renderSection(title, content) {
  if (!content) return;
  const section = node("section", "detail-section");
  section.append(node("h3", "", title), node("p", "", content));
  elements.detail.append(section);
}

function renderStringGroups(title, groups) {
  const hasItems = groups.some((group) => group.items?.length);
  if (!hasItems) return;
  const section = node("section", "detail-section");
  section.append(node("h3", "", title));
  for (const group of groups) {
    if (!group.items?.length) continue;
    const label = node("p", "", group.label);
    label.style.margin = "8px 0";
    section.append(label);
    const list = node("ul", "content-list");
    for (const item of group.items) list.append(node("li", "", item));
    section.append(list);
  }
  elements.detail.append(section);
}

function renderEntityList(title, items, primary, secondary) {
  if (!Array.isArray(items) || !items.length) return;
  const section = node("section", "detail-section");
  section.append(node("h3", "", `${title} · ${items.length}`));
  const list = node("ul", "content-list");
  for (const item of items) {
    const row = node("li");
    row.append(node("strong", "", primary(item)));
    const detail = secondary(item);
    if (detail) row.append(node("small", "", detail));
    list.append(row);
  }
  section.append(list);
  elements.detail.append(section);
}

function renderSources(sources) {
  if (!sources.length) return;
  const section = node("section", "detail-section");
  section.append(node("h3", "", `Fuentes trazables · ${sources.length}`));
  const list = node("div", "source-list");
  for (const source of sources) {
    list.append(
      node(
        "span",
        "source-chip",
        `${source.id} · ${source.nota}${source.seccion ? ` · ${source.seccion}` : ""}`,
      ),
    );
  }
  section.append(list);
  elements.detail.append(section);
}

async function validateSelected() {
  if (!state.selectedId || state.selectedKind !== "draft") return;
  await selectPlan(state.selectedId, "draft");
  showToast(
    state.detail?.issues?.some((issue) => issue.level === "error")
      ? "La validación encontró errores; corrígelos en Obsidian."
      : "Validación completada.",
    state.detail?.issues?.some((issue) => issue.level === "error"),
  );
}

async function approvePlan(planId, update, hasErrors) {
  if (hasErrors) return;
  try {
    const preview = await requestJson(
      `/api/plan/drafts/${encodeURIComponent(planId)}/preview-approval`,
      {
        method: "POST",
        body: JSON.stringify({ update }),
      },
    );
    const action = update ? "actualizar" : "aprobar";
    const confirmation = window.confirm(
      `Vista previa de ${planId}\n\n${preview.preview}\n\n` +
        `¿Confirmas ${action}? Se comprobarán de nuevo las fuentes antes de guardar.`,
    );
    if (!confirmation) return;

    const result = await requestJson(
      `/api/plan/drafts/${encodeURIComponent(planId)}/approve`,
      {
        method: "POST",
        body: JSON.stringify({ confirm: true, update }),
      },
    );
    showToast(result.message);
    await loadWorkspace({ keepSelection: false });
    if (result.obsidian_uri) openObsidian(result.obsidian_uri);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function revisePlan(planId) {
  if (
    !window.confirm(
      `¿Crear una copia editable de ${planId} en 022 - PLANES_BORRADOR?`,
    )
  ) {
    return;
  }
  try {
    const result = await requestJson(
      `/api/plan/approved/${encodeURIComponent(planId)}/revise`,
      { method: "POST", body: "{}" },
    );
    showToast("Revisión creada; abre la nota en Obsidian para editarla.");
    await loadWorkspace();
    openObsidian(result.obsidian_uri);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function archiveDraft(planId) {
  if (
    !window.confirm(
      `¿Mover ${planId} a 023 - PLANES_CANCELADOS? ` +
        "No se borrará y podrás restaurarlo más tarde.",
    )
  ) {
    return;
  }
  try {
    const result = await requestJson(
      `/api/plan/drafts/${encodeURIComponent(planId)}/archive`,
      {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      },
    );
    showToast(result.message);
    await loadWorkspace({ keepSelection: false });
  } catch (error) {
    showToast(error.message, true);
  }
}

async function restoreArchivedDraft(planId) {
  if (!window.confirm(`¿Restaurar ${planId} a la cola activa de 022?`)) return;
  try {
    const result = await requestJson(
      `/api/plan/archived/${encodeURIComponent(planId)}/restore`,
      {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      },
    );
    showToast(result.message);
    await loadWorkspace({ keepSelection: false });
    await selectPlan(planId, "draft");
  } catch (error) {
    showToast(error.message, true);
  }
}

function openObsidian(uri) {
  if (!uri) return;
  window.location.href = uri;
}

async function preparePlan(event) {
  event.preventDefault();
  if (!elements.prepareForm.reportValidity()) return;
  const form = new FormData(elements.prepareForm);
  const meetingIds = form
    .getAll("meeting_ids")
    .map((value) => String(value).trim())
    .filter(Boolean);
  const proposalIds = form
    .getAll("proposal_ids")
    .map((value) => String(value).trim())
    .filter(Boolean);
  const dossierIds = form
    .getAll("dossier_ids")
    .map((value) => String(value).trim())
    .filter(Boolean);
  if (meetingIds.length > 10 || proposalIds.length > 10 || dossierIds.length > 10) {
    showToast("Selecciona como máximo 10 fuentes de cada tipo.", true);
    return;
  }
  const payload = {
    client_id: String(form.get("client_id") || ""),
    project_id: String(form.get("project_id") || "").trim() || null,
    proposal_ids: proposalIds,
    meeting_ids: meetingIds,
    dossier_ids: dossierIds,
  };

  elements.prepareButton.disabled = true;
  elements.prepareButton.textContent = "Generando con modelo local...";
  try {
    const result = await requestJson("/api/plan/prepare", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showToast(
      result.warnings.length
        ? `Borrador ${result.plan_id} creado. ${result.warnings.join(" ")}`
        : `Borrador ${result.plan_id} creado en 022.`,
      result.warnings.length > 0,
    );
    await loadWorkspace({ keepSelection: false });
    await selectPlan(result.plan_id, "draft");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.prepareButton.disabled = !elements.client.value;
    elements.prepareButton.replaceChildren(
      node("span", "", "+"),
      document.createTextNode(" Generar borrador local"),
    );
  }
}

elements.prepareForm.addEventListener("submit", preparePlan);
elements.client.addEventListener("change", () => {
  elements.project.value = "";
  loadClientSources();
});
elements.project.addEventListener("change", loadClientSources);
elements.hubPreviewButton.addEventListener("click", previewProjectPanorama);
elements.hubConfirmButton.addEventListener("click", confirmProjectPanorama);
elements.hubCancelButton.addEventListener("click", cancelProjectPanorama);
elements.refreshButton.addEventListener("click", () => {
  loadClients();
  loadWorkspace();
});
elements.printButton.addEventListener("click", () => {
  if (state.detail) window.print();
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  state.installPrompt = event;
  elements.installButton.classList.remove("hidden");
});

elements.installButton.addEventListener("click", async () => {
  if (!state.installPrompt) return;
  state.installPrompt.prompt();
  await state.installPrompt.userChoice;
  state.installPrompt = null;
  elements.installButton.classList.add("hidden");
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/plan/plan-sw.js").catch(() => {
      showToast("La PWA está disponible, pero no se pudo activar el modo instalable.", true);
    });
  });
}

loadClients();
loadWorkspace({ keepSelection: false });
