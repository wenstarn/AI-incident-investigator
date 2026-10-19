"use strict";

// All data is rendered with textContent: tool output (logs) is untrusted text.

const LANES = {
  llm_only: [{ id: "llm-only", title: "Только LLM", subtitle: "без инструментов, только симптом" }],
  single: [{ id: "single", title: "Агент", subtitle: "все 8 инструментов" }],
  mas: [
    { id: "coordinator", title: "Координатор", subtitle: "план и итоговый отчёт" },
    { id: "infrastructure", title: "Инфраструктура", subtitle: "контейнеры, логи" },
    { id: "connectivity", title: "Связность", subtitle: "HTTP, зависимости, конфиг" },
    { id: "datastore", title: "Хранилища", subtitle: "PostgreSQL, Redis, метрики" },
    { id: "critic", title: "Критик", subtitle: "проверка выводов" },
  ],
};
const CATEGORY = {
  component_down: "компонент остановлен или не отвечает",
  dependency_misconfiguration: "неверная конфигурация зависимости",
  dependency_slow: "зависимость отвечает медленнее таймаута",
  no_incident: "инцидент не обнаружен",
  unknown: "причина не установлена",
};
const STATUS_TEXT = { ok: "ok", degraded: "проблема", error: "ошибка" };

const $ = (id) => document.getElementById(id);
const state = { options: null, source: null, model: null, lanes: {}, active: {}, clock: null };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", "X-Agent-Console": "1" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

function selected(name) {
  return document.querySelector(`input[name="${name}"]:checked`).value;
}

function systemLabel(system) {
  return state.options?.systems.find((s) => s.id === system)?.label || system;
}

// ---------------------------------------------------------------- options --
async function loadOptions() {
  const options = await api("/api/options");
  state.options = options;
  $("model-badge").textContent = options.model
    ? `новые расследования: ${options.model}`
    : "модель не задана (LLM_MODEL)";
  $("model-badge").title = "Модель для новых расследований. У сохранённых прогонов модель указана в списке.";
  for (const incident of options.incidents) {
    const option = el("option", null, `${incident.id} — ${incident.name}`);
    option.value = incident.id;
    $("incident-select").append(option);
  }
  const custom = el("option", null, "— свой текст —");
  custom.value = "";
  $("preset-select").append(custom);
  for (const item of options.cases) {
    const option = el("option", null, `${item.id}: ${item.symptom.slice(0, 60)}…`);
    option.value = item.id;
    $("preset-select").append(option);
  }
  if (!options.controller) {
    $("break-button").disabled = true;
    $("repair-button").disabled = true;
    $("incident-status").textContent =
      "INCIDENT_CONTROLLER_TOKEN не задан: кнопки управления сбоем недоступны.";
  }
}

function applyPreset() {
  const item = state.options.cases.find((c) => c.id === $("preset-select").value);
  if (!item) return;
  $("symptom").value = item.symptom;
}

// -------------------------------------------------------------- incidents --
async function refreshIncident() {
  if (!state.options?.controller) return;
  try {
    const { incident } = await api("/api/incidents/active");
    const badge = $("incident-badge");
    badge.textContent = incident ? `инцидент: ${incident} активен` : "инцидент: нет";
    badge.classList.toggle("alert", Boolean(incident));
  } catch (error) {
    $("incident-badge").textContent = "контроллер недоступен";
  }
}

async function incidentAction(kind) {
  const id = $("incident-select").value;
  const buttons = [$("break-button"), $("repair-button")];
  buttons.forEach((b) => (b.disabled = true));
  $("incident-status").textContent =
    kind === "break"
      ? `Включаю ${id} и воспроизвожу запросы пользователей…`
      : "Сбрасываю сбой и жду восстановления сервисов…";
  try {
    if (kind === "break") {
      const body = await api(`/api/incidents/${id}/activate`, { method: "POST" });
      $("symptom").value = body.symptom;
      $("preset-select").value = id;
      $("incident-status").textContent = body.observable
        ? `${id} активен. Симптом подставлен — можно расследовать.`
        : `${id} включён, но проявление не подтверждено.`;
    } else {
      const body = await api("/api/incidents/reset", { method: "POST" });
      $("incident-status").textContent = body.recovered
        ? "Сбой сброшен, сервисы здоровы."
        : "Сбой сброшен, но сервисы ещё восстанавливаются.";
    }
  } catch (error) {
    $("incident-status").textContent = `Ошибка: ${error.message}`;
  } finally {
    buttons.forEach((b) => (b.disabled = false));
    refreshIncident();
  }
}

// ------------------------------------------------------------------ board --
function laneFor(agent) {
  if (!agent || agent === "console") return null;
  if (agent === "single-investigator") return "single";
  if (agent.startsWith("coordinator")) return "coordinator";
  if (agent.startsWith("specialist-")) return agent.slice("specialist-".length);
  return agent;
}

function buildLanes(system) {
  const container = $("lanes");
  container.replaceChildren();
  state.lanes = {};
  state.active = {};
  for (const lane of LANES[system] || LANES.single) {
    const node = el("div", "lane");
    const header = el("div", "lane-header");
    const title = el("h3");
    const dot = el("span", "dot");
    title.append(dot, el("span", null, lane.title));
    header.append(title, el("small", null, lane.subtitle));
    const body = el("div", "lane-body");
    node.append(header, body);
    container.append(node);
    state.lanes[lane.id] = { body, dot };
    state.active[lane.id] = 0;
  }
}

function addCard(agent, className, title, time) {
  const lane = state.lanes[laneFor(agent)] || Object.values(state.lanes)[0];
  if (!lane) return null;
  const card = el("div", `card ${className}`);
  const head = el("div", "card-title");
  head.append(el("span", null, title));
  if (time !== undefined) head.append(el("span", "time", `${Number(time).toFixed(1)} с`));
  card.append(head);
  lane.body.append(card);
  lane.body.scrollTop = lane.body.scrollHeight;
  return card;
}

function setLaneState(agent, delta, failed) {
  const id = laneFor(agent);
  const lane = state.lanes[id];
  if (!lane) return;
  state.active[id] = Math.max(0, (state.active[id] || 0) + delta);
  lane.dot.className = "dot " + (state.active[id] > 0 ? "working" : failed ? "failed" : "done");
}

function formatArgs(args) {
  let value = args;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return value;
    }
  }
  return Object.entries(value || {})
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(", ");
}

function summarizeResult(result) {
  const data = result?.data || {};
  const skip = new Set(["service", "source_service", "dependency", "path", "started_at", "logs"]);
  const parts = Object.entries(data)
    .filter(([k, v]) => !skip.has(k) && (typeof v !== "object" || v === null))
    .map(([k, v]) => `${k}=${v}`);
  if (data.metrics) {
    const m = data.metrics;
    parts.push(
      Object.entries(m)
        .filter(([, v]) => typeof v === "number" && v > 0)
        .map(([k, v]) => `${k}=${v}`)
        .join(", ") || "счётчики нулевые",
    );
  }
  if (typeof data.logs === "string") {
    const lines = data.logs.split("\n").filter(Boolean);
    const errors = lines.filter((l) => l.includes('"level":"ERROR"')).length;
    parts.push(`логи: ${lines.length} строк, ERROR: ${errors}`);
  }
  if (result?.error) parts.push(`ошибка: ${result.error.type}`);
  return parts.join(" · ");
}

function taskText(agent, task) {
  if (agent === "coordinator-synthesis") return "Собирает выводы специалистов в отчёт";
  if (agent === "critic") return "Проверяет черновик отчёта";
  const match = /Your task:\n([\s\S]*)$/.exec(task || "");
  if (match) return match[1].trim();
  const symptom = /Symptom report:\n([\s\S]*?)\n\n/.exec(task || "");
  return symptom ? `Симптом: ${symptom[1]}` : task;
}

function renderFinal(event) {
  const p = event.payload || {};
  if (event.tool === "dispatch") {
    const card = addCard(event.agent, "final", "📋 План расследования", event.t);
    if (p.hypotheses?.length) card.append(el("p", null, `Гипотезы: ${p.hypotheses.join("; ")}`));
    const list = el("ul");
    for (const task of p.tasks || []) list.append(el("li", null, `${task.specialist}: ${task.task}`));
    card.append(list);
  } else if (event.tool === "submit_findings") {
    const card = addCard(event.agent, "final", "📝 Выводы для координатора", event.t);
    card.append(el("p", null, p.interpretation));
    if (p.suspected_cause) card.append(el("p", null, `Подозрение: ${p.suspected_cause}`));
  } else if (event.tool === "submit_verdict") {
    const card = addCard(
      event.agent,
      p.approved ? "final" : "rejected",
      p.approved ? "✅ Отчёт одобрен" : "❌ Отчёт отклонён",
      event.t,
    );
    if (p.issues?.length) {
      const list = el("ul");
      p.issues.forEach((issue) => list.append(el("li", null, issue)));
      card.append(list);
    }
    for (const check of p.requested_checks || []) {
      card.append(el("p", null, `Запрошено у ${check.specialist}: ${check.task}`));
    }
  } else if (event.tool === "submit_report") {
    const title = event.agent === "coordinator-synthesis" ? "📄 Черновик отчёта" : "📄 Отчёт";
    const card = addCard(event.agent, "final", title, event.t);
    card.append(el("p", null, `${CATEGORY[p.category] || p.category} — ${p.component}`));
    card.append(el("p", null, p.summary));
  }
}

function handleEvent(event) {
  switch (event.type) {
    case "console_started":
      state.source = event.source;
      state.model = event.model || null;
      break;
    case "investigation_started": {
      buildLanes(event.system);
      $("result").hidden = true;
      const label = systemLabel(event.system);
      const source = { live: "живой стенд", playback: "повтор" }[state.source] || "";
      $("run-title").textContent = `${label}: идёт расследование`;
      const model = state.model ? ` · модель: ${state.model}` : "";
      $("run-meta").textContent = `${source}${model} · симптом: ${event.symptom}`;
      startClock();
      break;
    }
    case "agent_started": {
      setLaneState(event.agent, +1);
      addCard(event.agent, "task", "🎯 " + taskText(event.agent, event.task), event.t);
      break;
    }
    case "llm_response": {
      const calls = (event.tool_calls || []).filter(
        (c) => !["submit_report", "dispatch", "submit_findings", "submit_verdict"].includes(c.name),
      );
      if (!event.content && !calls.length) break;
      const card = addCard(event.agent, "thought", "💭 Решение модели", event.t);
      const text = (event.content || "").trim();
      if (/^[[{]/.test(text)) {
        // Some models answer with raw JSON text; keep the card compact.
        const details = el("details");
        details.append(el("summary", null, "ответ модели в виде JSON"), el("pre", null, text));
        card.append(details);
      } else if (text) {
        card.append(el("p", null, text.length > 600 ? text.slice(0, 600) + "…" : text));
      }
      for (const call of calls) card.append(el("span", "pill", `${call.name}(${formatArgs(call.arguments)})`));
      break;
    }
    case "tool_result": {
      const status = event.status || "error";
      const card = addCard(
        event.agent,
        `tool ${status}${event.duplicate ? " duplicate" : ""}`,
        `🔧 ${event.tool}`,
        event.t,
      );
      const line = el("p");
      line.append(
        el("span", `status-tag ${status}`, event.duplicate ? "повтор" : STATUS_TEXT[status] || status),
        document.createTextNode(" " + formatArgs(event.arguments)),
      );
      card.append(line, el("p", null, summarizeResult(event.result)));
      const details = el("details");
      details.append(el("summary", null, "полный ответ"), el("pre", null, JSON.stringify(event.result, null, 2)));
      card.append(details);
      break;
    }
    case "final":
      renderFinal(event);
      break;
    case "round":
      if (event.round > 1) addCard("coordinator-synthesis", "round", `Раунд ${event.round}`);
      break;
    case "agent_finished":
      setLaneState(event.agent, -1, event.status === "llm_error");
      if (event.status === "step_limit") addCard(event.agent, "failure", "⏱ Лимит шагов исчерпан", event.t);
      break;
    case "error":
      if (event.agent === "console" || !laneFor(event.agent)) showError(event.message);
      else addCard(event.agent, "failure", `⚠ ${event.message}`, event.t);
      break;
    case "investigation_finished":
      stopClock();
      renderResult(event);
      break;
    case "evaluation":
      renderEvaluation(event);
      break;
    case "saved":
      appendResultNote(`Трейс сохранён: ${event.file}`);
      loadTraces();
      break;
  }
}

// ----------------------------------------------------------------- result --
function renderResult(summary) {
  const report = summary.report || {};
  const box = $("result");
  box.replaceChildren();
  box.hidden = false;
  $("run-title").textContent = `${systemLabel(summary.system)}: готово`;
  box.append(el("p", "eyebrow", "Итоговый отчёт"));
  box.append(el("h3", null, CATEGORY[report.category] || report.category));
  const target = report.dependency && report.dependency !== "none" ? ` → ${report.dependency}` : "";
  box.append(el("p", null, `Компонент: ${report.component}${target} · уверенность ${Math.round((report.confidence || 0) * 100)}%`));
  box.append(el("p", null, report.summary));
  if (report.evidence?.length) {
    box.append(el("strong", null, "Доказательства"));
    const list = el("ul");
    report.evidence.forEach((e) => list.append(el("li", null, `[${e.tool}] ${e.observation}`)));
    box.append(list);
  }
  if (report.ruled_out?.length) {
    box.append(el("strong", null, "Исключено"));
    const list = el("ul");
    report.ruled_out.forEach((item) => list.append(el("li", null, item)));
    box.append(list);
  }
  if (report.recommendation) box.append(el("p", null, `Рекомендация: ${report.recommendation}`));
  const metrics = el("div", "metrics");
  [
    `статус: ${summary.status}`,
    `инструментов: ${summary.tool_calls}`,
    `LLM-вызовов: ${summary.llm_calls}`,
    `токенов: ${summary.tokens}`,
    `время: ${summary.duration_s} с`,
  ].forEach((text) => metrics.append(el("span", "badge", text)));
  box.append(metrics);
  if (summary.error) box.append(el("p", "status", `Ошибка: ${summary.error}`));
}

function renderEvaluation(event) {
  const box = $("result");
  box.hidden = false;
  const verdict = el(
    "span",
    `verdict ${event.correct ? "good" : "bad"}`,
    event.correct
      ? `✓ Совпало с правильным ответом (${event.expected})`
      : `✗ Не совпало: ожидалось ${event.expected}, получено ${event.predicted}`,
  );
  box.prepend(verdict);
}

function appendResultNote(text) {
  const box = $("result");
  box.hidden = false;
  box.append(el("p", "status", text));
}

function showError(message) {
  stopClock();
  const box = $("result");
  box.hidden = false;
  box.replaceChildren(el("p", "verdict bad", `Ошибка: ${message}`));
}

function startClock() {
  stopClock();
  const started = Date.now();
  state.clock = setInterval(() => {
    const seconds = ((Date.now() - started) / 1000).toFixed(0);
    $("run-title").dataset.elapsed = seconds;
    const title = $("run-title").textContent.replace(/ \(\d+ с\)$/, "");
    $("run-title").textContent = `${title} (${seconds} с)`;
  }, 1000);
}

function stopClock() {
  if (state.clock) clearInterval(state.clock);
  state.clock = null;
}

// ---------------------------------------------------------------- streams --
let currentStream = null;

function follow(url) {
  if (currentStream) currentStream.close();
  $("lanes").replaceChildren();
  $("result").hidden = true;
  const stream = new EventSource(url);
  currentStream = stream;
  stream.onmessage = (message) => handleEvent(JSON.parse(message.data));
  stream.addEventListener("end", () => {
    stream.close();
    stopClock();
    $("start-button").disabled = false;
  });
  stream.onerror = () => {
    stream.close();
    stopClock();
    $("start-button").disabled = false;
  };
}

async function startInvestigation() {
  const symptom = $("symptom").value.trim();
  if (symptom.length < 3) {
    showError("Опишите симптом.");
    return;
  }
  $("start-button").disabled = true;
  try {
    const { id } = await api("/api/investigations", {
      method: "POST",
      body: JSON.stringify({ symptom, system: selected("system") }),
    });
    follow(`/api/investigations/${id}/stream`);
  } catch (error) {
    $("start-button").disabled = false;
    showError(error.message);
  }
}

async function loadTraces() {
  const list = $("trace-list");
  try {
    const traces = await api("/api/traces");
    list.replaceChildren();
    if (!traces.length) list.append(el("li", "trace-meta", "Сохранённых расследований пока нет."));
    for (const trace of traces) {
      const item = el("li");
      const play = el("button", null, "▶");
      play.type = "button";
      play.title = "Проиграть";
      play.addEventListener("click", () =>
        follow(`/api/traces/${trace.id}/stream?speed=${$("speed-select").value}`),
      );
      const text = el("div");
      const mark = trace.correct === true ? " ✓" : trace.correct === false ? " ✗" : "";
      text.append(
        el("div", null, `[${trace.system.toUpperCase()}] ${trace.symptom.slice(0, 70)}`),
        el("div", "trace-model", trace.model || "модель неизвестна"),
        el("div", "trace-meta", `${trace.case_id ? trace.case_id + " · " : ""}${trace.predicted || "—"}${mark} · ${trace.created.slice(0, 16).replace("T", " ")}`),
      );
      item.append(play, text);
      list.append(item);
    }
  } catch (error) {
    list.replaceChildren(el("li", "trace-meta", `Не удалось загрузить: ${error.message}`));
  }
}

// ------------------------------------------------------------------- init --
$("preset-select").addEventListener("change", applyPreset);
$("start-button").addEventListener("click", startInvestigation);
$("break-button").addEventListener("click", () => incidentAction("break"));
$("repair-button").addEventListener("click", () => incidentAction("repair"));
$("refresh-traces").addEventListener("click", loadTraces);

loadOptions()
  .then(() => {
    refreshIncident();
    setInterval(refreshIncident, 5000);
    loadTraces();
  })
  .catch((error) => showError(error.message));
