const $ = (id) => document.getElementById(id);

const els = {
  health: $("health"),
  conf: $("conf"),
  file: $("file"),
  drop: $("drop"),
  status: $("status"),
  detectCanvas: $("detectCanvas"),
  predictJson: $("predictJson"),
  datasetCounts: $("datasetCounts"),
  datasetStatus: $("datasetStatus"),
  datasetList: $("datasetList"),
  datasetCanvas: $("datasetCanvas"),
  datasetPreviewTitle: $("datasetPreviewTitle"),
  datasetPreviewMeta: $("datasetPreviewMeta"),
  datasetStatusFilter: $("datasetStatusFilter"),
  refreshDatasetBtn: $("refreshDatasetBtn"),
  exportDatasetBtn: $("exportDatasetBtn"),
  deleteCandidatesBtn: $("deleteCandidatesBtn"),
  deleteLabeledBtn: $("deleteLabeledBtn"),
  approveDatasetBtn: $("approveDatasetBtn"),
  rejectDatasetBtn: $("rejectDatasetBtn"),
  apiHistoryBody: $("apiHistoryBody"),
  refreshApiHistoryBtn: $("refreshApiHistoryBtn"),
  apiPreviewModal: $("apiPreviewModal"),
  apiPreviewBackdrop: $("apiPreviewBackdrop"),
  apiPreviewClose: $("apiPreviewClose"),
  apiPreviewCanvas: $("apiPreviewCanvas"),
  apiPreviewMeta: $("apiPreviewMeta"),
  apiPreviewHint: $("apiPreviewHint"),
  apiPreviewRecalc: $("apiPreviewRecalc"),
  apiHistoryKind: $("apiHistoryKind"),
  apiHistoryIp: $("apiHistoryIp"),
  apiHistoryPath: $("apiHistoryPath"),
  apiHistoryLimit: $("apiHistoryLimit"),
  refreshPerfBtn: $("refreshPerfBtn"),
  perfKind: $("perfKind"),
  perfMetric: $("perfMetric"),
  perfHours: $("perfHours"),
  perfBucket: $("perfBucket"),
  perfKpi: $("perfKpi"),
  perfCanvas: $("perfCanvas"),
  perfTooltip: $("perfTooltip"),
  perfStatus: $("perfStatus"),
};

const settingsIds = [
  "settingModelPath", "settingDetectScore", "settingOldScore", "settingNmsIou", "settingDiffIou",
  "settingSmbScore", "settingSmbOldScore", "settingImageSize", "settingTimezone", "settingZipTtl",
  "settingRelabelThreshold", "settingRelabelLimit", "settingDatasetScore", "settingExportLimit",
  "settingTrainPct", "settingValPct", "settingTestPct", "settingTrainEpochs", "settingTrainBatch",
  "settingTrainLimit", "settingSmbLimit", "settingSmbCompressMaxSide", "settingSmbCompressQuality",
  "settingSmbFixExifOrientation", "settingSmbCompressEnabled", "settingOldDetectEnabled",
  "settingSmbUseLimit", "settingSmbDryRun", "settingDatasetAutosave", "settingDatasetClassIds", "settingRequestPreviewSave",
  "datasetAutosaveEnabled", "datasetAutosaveScore", "datasetAutosaveClassIds", "saveDatasetAutosaveBtn", "datasetAutosaveStatus",
  "saveSettingsBtn", "cleanupDatasetBtn", "relabelCandidatesBtn", "relabelThreshold", "relabelLimit",
  "relabelStatus", "relabelResult", "startTrainingBtn", "trainEpochs", "trainBatch", "trainImageSize",
  "trainLimit", "trainingStatus", "trainingResult", "exportDatasetLimit", "exportTrainPct",
  "exportValPct", "exportTestPct", "smbIngestDirs", "smbIngestUseLimit", "smbIngestLimit",
  "smbIngestDryRun", "smbIngestBtn", "smbIngestStatus", "smbIngestResult",
];
settingsIds.forEach((id) => { els[id] = $(id); });

const HISTORY_KEY = "garbage-detection-history-v1";
const SMB_INGEST_JOB_KEY = "garbage-detection-smb-ingest-job-v1";
let projectTimeZone = "Europe/Moscow";
let datasetItems = [];
let selectedDatasetItem = null;
let lastApiHistoryItems = [];
let apiPreviewItem = null;
let apiPreviewBlob = null;
let apiPreviewObjectUrl = null;
let smbPoll = null;
let relabelPoll = null;
let trainingPoll = null;
let settingsAutosave = null;
let perfChartData = null;
let perfHoverPoints = [];

function toNumber(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function escapeHtml(v) {
  return String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function setStatus(el, text, kind = "") {
  if (!el) return;
  el.textContent = text || "";
  el.className = "status" + (kind ? " " + kind : "");
}

function formatMs(v) {
  const n = toNumber(v, NaN);
  return Number.isFinite(n) ? `${n.toFixed(1)} мс` : "—";
}

function formatConf(v) {
  const n = toNumber(v, NaN);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
}

function formatDateTime(value) {
  if (!value) return "—";
  const raw = String(value);
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(raw) ? raw : `${raw}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return raw;
  try {
    return d.toLocaleString("ru-RU", { timeZone: projectTimeZone });
  } catch {
    return d.toLocaleString("ru-RU", { timeZone: "UTC" });
  }
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, { cache: "no-store", ...options });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data;
}

function drawBoxes(canvas, img, detections = []) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = img?.naturalWidth || img?.width || 960;
  const h = img?.naturalHeight || img?.height || 540;
  const maxW = Math.min(1000, window.innerWidth - 48);
  const maxH = 720;
  const scale = Math.min(1, maxW / w, maxH / h);
  const dpr = window.devicePixelRatio || 1;
  canvas.style.width = `${w * scale}px`;
  canvas.style.height = `${h * scale}px`;
  canvas.width = Math.round(w * scale * dpr);
  canvas.height = Math.round(h * scale * dpr);
  ctx.setTransform(dpr * scale, 0, 0, dpr * scale, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#0d1117";
  ctx.fillRect(0, 0, w, h);
  if (img) ctx.drawImage(img, 0, 0, w, h);
  detections.forEach((det) => {
    const xy = det.xyxy || [det.x0, det.y0, det.x1, det.y1];
    if (!Array.isArray(xy) || xy.length < 4) return;
    ctx.strokeStyle = det.source === "current" ? "#f97316" : det.source === "old" ? "#58a6ff" : "#e63946";
    ctx.lineWidth = Math.max(2, 2 / scale);
    ctx.strokeRect(xy[0], xy[1], xy[2] - xy[0], xy[3] - xy[1]);
    const label = `${det.source ? det.source + " " : ""}${det.name || det.class_name || det.class_id || 0} ${formatConf(det.conf)}`;
    ctx.font = `${Math.max(14, 14 / scale)}px system-ui`;
    ctx.lineWidth = 4 / scale;
    ctx.strokeStyle = "#000";
    ctx.strokeText(label, xy[0] + 4 / scale, Math.max(16 / scale, xy[1] - 4 / scale));
    ctx.fillStyle = "#fff";
    ctx.fillText(label, xy[0] + 4 / scale, Math.max(16 / scale, xy[1] - 4 / scale));
  });
}

function switchTab(tab) {
  document.querySelectorAll(".tab-btn, .mobile-tab-btn").forEach((b) => {
    const on = b.dataset.tab === tab;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
    if (b.classList.contains("mobile-tab-btn")) {
      if (on) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    }
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    const on = p.dataset.tabPanel === tab;
    p.classList.toggle("active", on);
    p.setAttribute("aria-hidden", on ? "false" : "true");
  });
  if (tab === "dataset") void fetchDataset();
  if (tab === "performance") void fetchPerformance();
  if (tab === "logs") void fetchApiHistory();
  if (tab === "settings") void fetchSettings();
}

document.querySelectorAll(".tab-btn, .mobile-tab-btn").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

/** Клик по зоне загрузки открывает диалог выбора файла */
els.drop?.addEventListener("click", (e) => {
  if (e.target === els.file) return;
  els.file?.click();
});
els.drop?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    els.file?.click();
  }
});

async function runPredict(file) {
  if (!file) return;
  setStatus(els.status, "Распознаём…");
  const img = new Image();
  const objectUrl = URL.createObjectURL(file);
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
    img.src = objectUrl;
  });
  const fd = new FormData();
  fd.append("file", file, file.name);
  const conf = parseFloat(els.conf?.value || "0.7") || 0.7;
  try {
    const data = await fetchJson(`/api/predict?conf=${encodeURIComponent(conf)}`, { method: "POST", body: fd });
    drawBoxes(els.detectCanvas, img, data.detections || []);
    els.predictJson.textContent = JSON.stringify(data, null, 2);
    setStatus(els.status, `Готово: объектов ${data.detections?.length || 0}, модель ${data.process?.model_name || "—"}`, "ok");
    await fetchDataset();
    await fetchApiHistory();
  } catch (e) {
    setStatus(els.status, `Ошибка: ${e.message}`, "err");
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

els.file?.addEventListener("change", () => runPredict(els.file.files?.[0]));
els.drop?.addEventListener("dragover", (e) => {
  e.preventDefault();
  els.drop.classList.add("drop--active");
});
els.drop?.addEventListener("dragleave", () => els.drop.classList.remove("drop--active"));
els.drop?.addEventListener("drop", (e) => {
  e.preventDefault();
  els.drop.classList.remove("drop--active");
  runPredict(e.dataTransfer.files?.[0]);
});

function renderCounts(counts = {}) {
  if (!els.datasetCounts) return;
  els.datasetCounts.textContent = `Кандидаты: ${counts.candidate || 0} · Подтверждено: ${counts.approved || 0} · Отклонено: ${counts.rejected || 0}`;
}

function renderDatasetClassFilters(container, classes, selectedIds) {
  if (!container) return;
  const list = classes || [];
  const unset = selectedIds == null || selectedIds === undefined;
  const selected = unset
    ? new Set(list.map((c) => c.id))
    : new Set((selectedIds || []).map((n) => Number(n)).filter((n) => Number.isFinite(n)));
  container.innerHTML = "";
  if (!list.length) {
    container.textContent = "Список классов недоступен — откройте «Настройки» или перезагрузите страницу.";
    return;
  }
  list.forEach((c) => {
    const label = document.createElement("label");
    label.className = "dataset-class-filter";
    const inp = document.createElement("input");
    inp.type = "checkbox";
    inp.value = String(c.id);
    inp.checked = selected.has(c.id);
    if (container.id === "settingDatasetClassIds") {
      inp.addEventListener("change", scheduleSettingsSave);
    }
    label.appendChild(inp);
    const span = document.createElement("span");
    span.textContent = `[${c.id}] ${c.name || "class"}`;
    label.appendChild(span);
    container.appendChild(label);
  });
}

function readDatasetClassIdsFrom(container) {
  return [...(container?.querySelectorAll('input[type="checkbox"]:checked') || [])]
    .map((el) => parseInt(el.value, 10))
    .filter((n) => Number.isFinite(n));
}

function applyDatasetAutosaveSettings(s = {}) {
  if (els.datasetAutosaveEnabled) els.datasetAutosaveEnabled.checked = Boolean(s.DATASET_AUTOSAVE_ENABLED);
  if (els.datasetAutosaveScore && s.DATASET_SCORE_THRESHOLD !== undefined) {
    els.datasetAutosaveScore.value = s.DATASET_SCORE_THRESHOLD;
  }
  const classes = s.MODEL_CLASSES || [];
  renderDatasetClassFilters(els.datasetAutosaveClassIds, classes, s.DATASET_AUTOSAVE_CLASS_IDS);
  renderDatasetClassFilters(els.settingDatasetClassIds, classes, s.DATASET_AUTOSAVE_CLASS_IDS);
}

async function saveDatasetAutosaveRules() {
  const payload = {
    DATASET_AUTOSAVE_ENABLED: Boolean(els.datasetAutosaveEnabled?.checked),
    DATASET_SCORE_THRESHOLD: parseFloat(els.datasetAutosaveScore?.value || "0.5"),
    DATASET_AUTOSAVE_CLASS_IDS: readDatasetClassIdsFrom(els.datasetAutosaveClassIds),
  };
  try {
    const data = await fetchJson("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    applyDatasetAutosaveSettings(data.settings || {});
    if (els.settingDatasetAutosave) els.settingDatasetAutosave.checked = Boolean(payload.DATASET_AUTOSAVE_ENABLED);
    if (els.settingDatasetScore) els.settingDatasetScore.value = payload.DATASET_SCORE_THRESHOLD;
    setStatus(els.datasetAutosaveStatus, "Правила автосохранения сохранены", "ok");
    setStatus(els.settingsStatus, "Правила датасета сохранены", "ok");
  } catch (e) {
    setStatus(els.datasetAutosaveStatus, `Ошибка: ${e.message}`, "err");
  }
}

async function fetchDataset() {
  const status = els.datasetStatusFilter?.value || "candidate";
  try {
    const [data, settingsRes] = await Promise.all([
      fetchJson(`/api/dataset/candidates?status=${encodeURIComponent(status)}&limit=50`),
      fetchJson("/api/settings"),
    ]);
    datasetItems = data.items || [];
    renderCounts(data.counts || {});
    renderDatasetList();
    applyDatasetAutosaveSettings(settingsRes.settings || {});
  } catch (e) {
    setStatus(els.datasetStatus, `Ошибка: ${e.message}`, "err");
  }
}

function renderDatasetList() {
  if (!els.datasetList) return;
  els.datasetList.innerHTML = "";
  if (!datasetItems.length) {
    els.datasetList.innerHTML = '<li class="history-item">Нет записей</li>';
    return;
  }
  datasetItems.forEach((item) => {
    const li = document.createElement("li");
    li.className = "dataset-item";
    li.dataset.id = item.id;
    const anns = item.annotations?.length || 0;
    li.innerHTML = `<div class="history-main"><strong>${escapeHtml(item.image_name || item.id)}</strong><span>${escapeHtml(item.status)}</span></div>
      <div class="history-sub"><span>${item.width}×${item.height}</span><span>боксов: ${anns}</span><span>${formatDateTime(item.created_at)}</span></div>`;
    li.addEventListener("click", () => selectDatasetItem(item));
    els.datasetList.appendChild(li);
  });
  selectDatasetItem(datasetItems[0]);
}

async function selectDatasetItem(item) {
  selectedDatasetItem = item;
  document.querySelectorAll(".dataset-item").forEach((li) => li.classList.toggle("selected", li.dataset.id === item.id));
  els.datasetPreviewTitle.textContent = item.image_name || item.id;
  els.datasetPreviewMeta.textContent = `${item.status} · ${item.source_type || "—"} · ${item.conf ?? "—"}`;
  els.approveDatasetBtn.disabled = item.status === "approved";
  els.rejectDatasetBtn.disabled = item.status === "rejected";
  try {
    const blob = await (await fetch(`/api/dataset/sample/${encodeURIComponent(item.id)}/image`)).blob();
    const img = new Image();
    const url = URL.createObjectURL(blob);
    await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = url; });
    const old = item.extra?.old_detections || [];
    const cur = item.extra?.current_detections || [];
    const dets = old.length || cur.length ? [...old.map((d) => ({ ...d, source: "old" })), ...cur.map((d) => ({ ...d, source: "current" }))] : (item.annotations || []);
    drawBoxes(els.datasetCanvas, img, dets);
    URL.revokeObjectURL(url);
  } catch {}
}

async function updateDatasetStatus(status) {
  if (!selectedDatasetItem) return;
  const action = status === "approved" ? "approve" : "reject";
  await fetchJson(`/api/dataset/sample/${selectedDatasetItem.id}/${action}`, { method: "POST" });
  await fetchDataset();
}
els.approveDatasetBtn?.addEventListener("click", () => updateDatasetStatus("approved"));
els.rejectDatasetBtn?.addEventListener("click", () => updateDatasetStatus("rejected"));
els.refreshDatasetBtn?.addEventListener("click", fetchDataset);
els.datasetStatusFilter?.addEventListener("change", fetchDataset);
els.saveDatasetAutosaveBtn?.addEventListener("click", saveDatasetAutosaveRules);

async function deleteDataset(url, text) {
  if (!confirm(text)) return;
  const data = await fetchJson(url, { method: "POST" });
  setStatus(els.datasetStatus, `Удалено: ${data.deleted_samples || 0}, файлов: ${data.deleted_files || 0}`, "ok");
  renderCounts(data.counts || {});
  await fetchDataset();
}
els.deleteCandidatesBtn?.addEventListener("click", () => deleteDataset("/api/dataset/delete-candidates", "Удалить все кандидаты?"));
els.deleteLabeledBtn?.addEventListener("click", () => deleteDataset("/api/dataset/delete-labeled", "Удалить подтверждённые и отклонённые?"));

async function exportDataset() {
  setStatus(els.datasetStatus, "Собираем ZIP…");
  const payload = {
    limit: (parseInt(els.exportDatasetLimit?.value || "0", 10) || 0) || null,
    train_pct: parseFloat(els.exportTrainPct?.value || "70") || 70,
    val_pct: parseFloat(els.exportValPct?.value || "20") || 20,
    test_pct: parseFloat(els.exportTestPct?.value || "10") || 10,
  };
  try {
    const data = await fetchJson("/api/dataset/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    setStatus(els.datasetStatus, `Экспорт: ${data.samples}, train=${data.split?.train}, val=${data.split?.val}, test=${data.split?.test}`, "ok");
    if (data.download_url) window.location.href = data.download_url;
  } catch (e) {
    setStatus(els.datasetStatus, `Ошибка экспорта: ${e.message}`, "err");
  }
}
els.exportDatasetBtn?.addEventListener("click", exportDataset);

function metricLabel(metric) {
  return metric === "inference_ms" ? "инференс" : "запрос";
}

function compactMs(v) {
  const n = toNumber(v, NaN);
  if (!Number.isFinite(n)) return "—";
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 1 : 2)} c`;
  return `${n.toFixed(0)} мс`;
}

function renderPerfKpi(data) {
  if (!els.perfKpi) return;
  const s = data.summary || {};
  const slow = s.slowest || {};
  els.perfKpi.innerHTML = [
    ["Запросов", s.count ?? 0, "в выбранном периоде"],
    ["Минимум", compactMs(s.min_ms), "самый быстрый"],
    ["Среднее", compactMs(s.avg_ms), metricLabel(data.metric)],
    ["Максимум", compactMs(s.max_ms), slow.image_name || "самый медленный"],
  ].map(([title, value, sub]) => `<div class="perf-card"><span>${escapeHtml(title)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(sub)}</small></div>`).join("");
}

function drawPerformanceChart(data) {
  const canvas = els.perfCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const points = data.points || [];
  const cssW = canvas.clientWidth || canvas.width;
  const cssH = 460;
  const dpr = window.devicePixelRatio || 1;
  canvas.style.height = `${cssH}px`;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  perfHoverPoints = [];

  const bg = ctx.createLinearGradient(0, 0, cssW, cssH);
  bg.addColorStop(0, "#111f31");
  bg.addColorStop(0.55, "#0d141d");
  bg.addColorStop(1, "#091018");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, cssW, cssH);

  const pad = { left: 74, right: 28, top: 36, bottom: 62 };
  const chartW = cssW - pad.left - pad.right;
  const chartH = cssH - pad.top - pad.bottom;
  const allVals = points.flatMap((p) => [p.min_ms, p.avg_ms, p.max_ms]).filter((v) => Number.isFinite(Number(v)));
  const maxVal = Math.max(100, ...(allVals.length ? allVals : [100]));
  const yMax = Math.ceil(maxVal * 1.16 / 100) * 100;
  const x = (i) => pad.left + (points.length <= 1 ? chartW / 2 : (i / (points.length - 1)) * chartW);
  const y = (v) => pad.top + chartH - (Number(v) / yMax) * chartH;

  ctx.strokeStyle = "rgba(148, 163, 184, .13)";
  ctx.lineWidth = 1;
  ctx.font = "12px ui-sans-serif, system-ui";
  ctx.fillStyle = "#8b949e";
  for (let i = 0; i <= 5; i++) {
    const yy = pad.top + (chartH / 5) * i;
    const value = yMax - (yMax / 5) * i;
    ctx.beginPath();
    ctx.moveTo(pad.left, yy);
    ctx.lineTo(cssW - pad.right, yy);
    ctx.stroke();
    ctx.fillText(compactMs(value), 12, yy + 4);
  }

  ctx.strokeStyle = "rgba(88, 166, 255, .35)";
  ctx.strokeRect(pad.left, pad.top, chartW, chartH);

  if (!points.length) {
    ctx.fillStyle = "#8b949e";
    ctx.font = "600 18px ui-sans-serif, system-ui";
    ctx.fillText("Недостаточно данных для графика", pad.left + 24, pad.top + 58);
    return;
  }

  const area = new Path2D();
  points.forEach((p, i) => {
    const xx = x(i);
    const yy = y(p.max_ms);
    if (i === 0) area.moveTo(xx, yy);
    else area.lineTo(xx, yy);
  });
  [...points].reverse().forEach((p, ri) => {
    const i = points.length - 1 - ri;
    area.lineTo(x(i), y(p.min_ms));
  });
  area.closePath();
  const fill = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH);
  fill.addColorStop(0, "rgba(248, 81, 73, .24)");
  fill.addColorStop(.55, "rgba(88, 166, 255, .13)");
  fill.addColorStop(1, "rgba(63, 185, 80, .18)");
  ctx.fillStyle = fill;
  ctx.fill(area);

  function drawLine(key, color, width) {
    ctx.beginPath();
    points.forEach((p, i) => {
      const xx = x(i);
      const yy = y(p[key]);
      if (i === 0) ctx.moveTo(xx, yy);
      else ctx.lineTo(xx, yy);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.stroke();
  }
  drawLine("max_ms", "rgba(248, 81, 73, .78)", 1.8);
  drawLine("avg_ms", "#58a6ff", 3);
  drawLine("min_ms", "rgba(63, 185, 80, .82)", 1.8);

  points.forEach((p, i) => {
    const xx = x(i);
    const yy = y(p.avg_ms);
    ctx.beginPath();
    ctx.arc(xx, yy, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = "#dbeafe";
    ctx.fill();
    ctx.strokeStyle = "#0b1220";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    perfHoverPoints.push({ x: xx, y: yy, point: p });
  });

  const first = points[0];
  const last = points[points.length - 1];
  ctx.fillStyle = "#8b949e";
  ctx.font = "12px ui-sans-serif, system-ui";
  ctx.fillText(formatDateTime(first.bucket_start), pad.left, cssH - 24);
  const lastLabel = formatDateTime(last.bucket_start);
  ctx.fillText(lastLabel, Math.max(pad.left, cssW - pad.right - ctx.measureText(lastLabel).width), cssH - 24);

  const legend = [
    ["min", "rgba(63, 185, 80, .9)"],
    ["avg", "#58a6ff"],
    ["max", "rgba(248, 81, 73, .9)"],
  ];
  let lx = pad.left;
  legend.forEach(([label, color]) => {
    ctx.fillStyle = color;
    ctx.fillRect(lx, 16, 24, 3);
    ctx.fillStyle = "#c9d1d9";
    ctx.fillText(label, lx + 32, 20);
    lx += 74;
  });
}

async function fetchPerformance() {
  if (!els.perfCanvas) return;
  setStatus(els.perfStatus, "Загружаем метрики…");
  const params = new URLSearchParams();
  params.set("kind", els.perfKind?.value || "1c");
  params.set("metric", els.perfMetric?.value || "duration_ms");
  params.set("hours", String(parseInt(els.perfHours?.value || "24", 10) || 24));
  params.set("bucket_minutes", String(parseInt(els.perfBucket?.value || "15", 10) || 15));
  try {
    const data = await fetchJson(`/api/metrics/recognition-speed?${params.toString()}`);
    perfChartData = data;
    renderPerfKpi(data);
    drawPerformanceChart(data);
    setStatus(els.perfStatus, `Точек: ${data.points?.length || 0}, запросов: ${data.summary?.count || 0}`, "ok");
  } catch (e) {
    setStatus(els.perfStatus, `Ошибка метрик: ${e.message}`, "err");
  }
}

function handlePerfHover(e) {
  if (!els.perfTooltip || !els.perfCanvas || !perfHoverPoints.length) return;
  const rect = els.perfCanvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  let nearest = null;
  let dist = Infinity;
  perfHoverPoints.forEach((p) => {
    const d = Math.hypot(p.x - x, p.y - y);
    if (d < dist) { dist = d; nearest = p; }
  });
  if (!nearest || dist > 28) {
    els.perfTooltip.classList.add("hidden");
    return;
  }
  const p = nearest.point;
  els.perfTooltip.innerHTML = `<strong>${formatDateTime(p.bucket_start)}</strong><br>min: ${compactMs(p.min_ms)} · avg: ${compactMs(p.avg_ms)} · max: ${compactMs(p.max_ms)}<br>запросов: ${p.count}`;
  els.perfTooltip.style.left = `${Math.min(rect.width - 230, Math.max(12, x + 14))}px`;
  els.perfTooltip.style.top = `${Math.max(12, y - 54)}px`;
  els.perfTooltip.classList.remove("hidden");
}

els.refreshPerfBtn?.addEventListener("click", fetchPerformance);
["perfKind", "perfMetric", "perfHours", "perfBucket"].forEach((id) => {
  els[id]?.addEventListener("change", fetchPerformance);
});
els.perfCanvas?.addEventListener("mousemove", handlePerfHover);
els.perfCanvas?.addEventListener("mouseleave", () => els.perfTooltip?.classList.add("hidden"));
window.addEventListener("resize", () => { if (perfChartData) drawPerformanceChart(perfChartData); });

async function fetchSettings() {
  try {
    const data = await fetchJson("/api/settings");
    fillSettings(data.settings || {});
    els.health.textContent = "Сервис доступен";
    els.health.classList.remove("status-pill--bad");
    els.health.classList.add("status-pill--ok");
  } catch (e) {
    els.health.textContent = "Нет связи с API";
    els.health.classList.remove("status-pill--ok");
    els.health.classList.add("status-pill--bad");
  }
}

function fillSettings(s) {
  projectTimeZone = s.PROJECT_TIMEZONE || projectTimeZone;
  const set = (id, v) => { if (els[id] && v !== undefined) els[id].value = v; };
  const chk = (id, v) => { if (els[id]) els[id].checked = Boolean(v); };
  if (els.settingModelPath) {
    els.settingModelPath.innerHTML = "";
    (s.AVAILABLE_MODELS || []).forEach((m) => {
      const o = document.createElement("option");
      o.value = m.path; o.textContent = `${m.label} (${m.type})`;
      els.settingModelPath.appendChild(o);
    });
    els.settingModelPath.value = s.MODEL_PATH || "";
  }
  set("settingDetectScore", s.DETECT_SCORE_THRESHOLD);
  set("settingDatasetScore", s.DATASET_SCORE_THRESHOLD);
  set("settingOldScore", s.OLD_DETECT_SCORE_THRESHOLD);
  set("settingNmsIou", s.DETECT_NMS_IOU);
  set("settingDiffIou", s.DETECT_DIFF_IOU_THRESHOLD);
  set("settingImageSize", s.DETECT_IMAGE_SIZE);
  set("settingTimezone", s.PROJECT_TIMEZONE);
  set("settingZipTtl", s.EXPORT_ZIP_TTL_HOURS);
  set("settingRelabelThreshold", s.DEFAULT_RELABEL_THRESHOLD);
  set("settingRelabelLimit", s.DEFAULT_RELABEL_LIMIT);
  set("settingExportLimit", s.DEFAULT_EXPORT_LIMIT);
  set("settingTrainPct", s.DEFAULT_TRAIN_PCT);
  set("settingValPct", s.DEFAULT_VAL_PCT);
  set("settingTestPct", s.DEFAULT_TEST_PCT);
  set("settingTrainEpochs", s.DEFAULT_TRAIN_EPOCHS);
  set("settingTrainBatch", s.DEFAULT_TRAIN_BATCH);
  set("settingTrainLimit", s.DEFAULT_TRAIN_LIMIT);
  set("settingSmbScore", s.SMB_INGEST_SCORE_THRESHOLD);
  set("settingSmbOldScore", s.SMB_INGEST_OLD_SCORE_THRESHOLD);
  set("settingSmbLimit", s.DEFAULT_SMB_INGEST_LIMIT);
  set("settingSmbCompressMaxSide", s.SMB_COMPRESS_MAX_SIDE);
  set("settingSmbCompressQuality", s.SMB_COMPRESS_JPEG_QUALITY);
  chk("settingOldDetectEnabled", s.OLD_DETECT_ENABLED);
  chk("settingDatasetAutosave", s.DATASET_AUTOSAVE_ENABLED);
  renderDatasetClassFilters(els.settingDatasetClassIds, s.MODEL_CLASSES || [], s.DATASET_AUTOSAVE_CLASS_IDS);
  applyDatasetAutosaveSettings(s);
  chk("settingRequestPreviewSave", s.REQUEST_PREVIEW_SAVE_ENABLED !== false);
  chk("settingSmbUseLimit", s.DEFAULT_SMB_INGEST_USE_LIMIT);
  chk("settingSmbDryRun", s.DEFAULT_SMB_INGEST_DRY_RUN);
  chk("settingSmbCompressEnabled", s.SMB_COMPRESS_ENABLED);
  chk("settingSmbFixExifOrientation", s.SMB_FIX_EXIF_ORIENTATION);
}

let settingsTimer = null;
function scheduleSettingsSave() {
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(saveSettings, 800);
}
async function saveSettings() {
  const payload = {
    MODEL_PATH: els.settingModelPath?.value || "",
    DETECT_SCORE_THRESHOLD: parseFloat(els.settingDetectScore?.value || "0.7"),
    DATASET_SCORE_THRESHOLD: parseFloat(els.settingDatasetScore?.value || "0.05"),
    OLD_DETECT_SCORE_THRESHOLD: parseFloat(els.settingOldScore?.value || "0.7"),
    DETECT_NMS_IOU: parseFloat(els.settingNmsIou?.value || "0.4"),
    DETECT_DIFF_IOU_THRESHOLD: parseFloat(els.settingDiffIou?.value || "0.5"),
    DETECT_IMAGE_SIZE: parseInt(els.settingImageSize?.value || "1024", 10),
    PROJECT_TIMEZONE: els.settingTimezone?.value || "Europe/Moscow",
    EXPORT_ZIP_TTL_HOURS: parseInt(els.settingZipTtl?.value || "24", 10),
    DEFAULT_RELABEL_THRESHOLD: parseFloat(els.settingRelabelThreshold?.value || "0.99"),
    DEFAULT_RELABEL_LIMIT: parseInt(els.settingRelabelLimit?.value || "0", 10) || 0,
    DEFAULT_EXPORT_LIMIT: parseInt(els.settingExportLimit?.value || "0", 10) || 0,
    DEFAULT_TRAIN_PCT: parseFloat(els.settingTrainPct?.value || "70"),
    DEFAULT_VAL_PCT: parseFloat(els.settingValPct?.value || "20"),
    DEFAULT_TEST_PCT: parseFloat(els.settingTestPct?.value || "10"),
    DEFAULT_TRAIN_EPOCHS: parseInt(els.settingTrainEpochs?.value || "30", 10),
    DEFAULT_TRAIN_BATCH: parseInt(els.settingTrainBatch?.value || "4", 10),
    DEFAULT_TRAIN_LIMIT: parseInt(els.settingTrainLimit?.value || "0", 10) || 0,
    SMB_INGEST_SCORE_THRESHOLD: parseFloat(els.settingSmbScore?.value || "0.9"),
    SMB_INGEST_OLD_SCORE_THRESHOLD: parseFloat(els.settingSmbOldScore?.value || "0.9"),
    DEFAULT_SMB_INGEST_LIMIT: parseInt(els.settingSmbLimit?.value || "20", 10),
    SMB_COMPRESS_MAX_SIDE: parseInt(els.settingSmbCompressMaxSide?.value || "1024", 10),
    SMB_COMPRESS_JPEG_QUALITY: parseInt(els.settingSmbCompressQuality?.value || "50", 10),
    OLD_DETECT_ENABLED: Boolean(els.settingOldDetectEnabled?.checked),
    DATASET_AUTOSAVE_ENABLED: Boolean(els.settingDatasetAutosave?.checked),
    DATASET_AUTOSAVE_CLASS_IDS: readDatasetClassIdsFrom(els.settingDatasetClassIds),
    REQUEST_PREVIEW_SAVE_ENABLED: Boolean(els.settingRequestPreviewSave?.checked),
    DEFAULT_SMB_INGEST_USE_LIMIT: Boolean(els.settingSmbUseLimit?.checked),
    DEFAULT_SMB_INGEST_DRY_RUN: Boolean(els.settingSmbDryRun?.checked),
    SMB_COMPRESS_ENABLED: Boolean(els.settingSmbCompressEnabled?.checked),
    SMB_FIX_EXIF_ORIENTATION: Boolean(els.settingSmbFixExifOrientation?.checked),
  };
  try {
    const data = await fetchJson("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    fillSettings(data.settings || {});
    setStatus(els.settingsStatus, "Настройки сохранены", "ok");
  } catch (e) {
    setStatus(els.settingsStatus, `Ошибка сохранения: ${e.message}`, "err");
  }
}
els.saveSettingsBtn?.addEventListener("click", saveSettings);
document.querySelectorAll("#panel-settings input,#panel-settings select").forEach((el) => el.addEventListener("change", scheduleSettingsSave));

async function fetchApiHistory() {
  try {
    const params = new URLSearchParams();
    params.set("kind", els.apiHistoryKind?.value || "1c");
    params.set("limit", String(parseInt(els.apiHistoryLimit?.value || "50", 10) || 50));
    const ip = (els.apiHistoryIp?.value || "").trim();
    const path = (els.apiHistoryPath?.value || "").trim();
    if (ip) params.set("client_ip", ip);
    if (path) params.set("path_contains", path);
    const data = await fetchJson(`/api/request-history?${params.toString()}`);
    lastApiHistoryItems = data.items || [];
    els.apiHistoryBody.innerHTML = "";
    if (!lastApiHistoryItems.length) {
      els.apiHistoryBody.innerHTML = '<tr><td colspan="8">Нет запросов по выбранному фильтру</td></tr>';
      return;
    }
    lastApiHistoryItems.forEach((item) => {
      const ex = item.extra || {};
      const tr = document.createElement("tr");
      const details = [ex.image_name && `img: ${ex.image_name}`, ex.result_count != null && `result: ${ex.result_count}`, ex.current_count != null && `current: ${ex.current_count}`].filter(Boolean).join(" | ") || "—";
      tr.innerHTML = `<td>${formatDateTime(item.date)}</td><td>${escapeHtml(item.method)}</td><td>${escapeHtml(item.path)}</td><td>${item.status_code}</td><td>${formatMs(item.duration_ms)}</td><td>${escapeHtml(item.client_ip || "")}</td><td>${escapeHtml(details)}</td><td><button class="secondary-btn" type="button">Просмотр</button></td>`;
      tr.querySelector("button").addEventListener("click", () => openApiPreview(item));
      els.apiHistoryBody.appendChild(tr);
    });
  } catch (e) {
    els.apiHistoryBody.innerHTML = `<tr><td colspan="8">Ошибка: ${escapeHtml(e.message)}</td></tr>`;
  }
}
els.refreshApiHistoryBtn?.addEventListener("click", fetchApiHistory);
["apiHistoryKind", "apiHistoryIp", "apiHistoryPath", "apiHistoryLimit"].forEach((id) => {
  els[id]?.addEventListener("change", fetchApiHistory);
});
["apiHistoryIp", "apiHistoryPath"].forEach((id) => {
  els[id]?.addEventListener("input", () => {
    clearTimeout(els.apiHistoryFilterTimer);
    els.apiHistoryFilterTimer = setTimeout(fetchApiHistory, 500);
  });
});

async function openApiPreview(item) {
  apiPreviewItem = item;
  const ex = item.extra || {};
  els.apiPreviewMeta.textContent = `${formatDateTime(item.date)} · ${item.path} · ${item.client_ip || ""}`;
  els.apiPreviewHint.textContent = "";
  els.apiPreviewRecalc.disabled = true;
  els.apiPreviewModal.classList.remove("hidden");
  try {
    const imageResp = await fetch(`/api/request-log/${encodeURIComponent(item.id)}/image`, { cache: "no-store" });
    if (!imageResp.ok) {
      const errorData = await imageResp.json().catch(() => ({}));
      throw new Error(errorData.detail || `HTTP ${imageResp.status}`);
    }
    const blob = await imageResp.blob();
    apiPreviewBlob = blob;
    const img = new Image();
    const url = URL.createObjectURL(blob);
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error("браузер не смог открыть изображение из ответа API"));
      img.src = url;
    });
    const old = ex.old_detections || [];
    const cur = ex.current_detections || [];
    let dets = [];
    if (old.length || cur.length) dets = [...old.map((d) => ({ ...d, source: "old" })), ...cur.map((d) => ({ ...d, source: "current" }))];
    else if (ex.legacy_result?.length > 1) dets = ex.legacy_result.slice(1).map((b) => ({ class_id: b.key, conf: b.ratio, xyxy: [b.x0, b.y0, b.x1, b.y1] }));
    drawBoxes(els.apiPreviewCanvas, img, dets);
    els.apiPreviewRecalc.disabled = false;
    if (!dets.length && ex.legacy_status === false) {
      els.apiPreviewHint.textContent = `Изображение восстановлено по SMB, но в этой записи 1С разметка не сохранилась: ${ex.legacy_error || "запрос завершился ошибкой"}. Нажмите «Пересчитать», чтобы получить разметку текущей моделью.`;
    }
    URL.revokeObjectURL(url);
  } catch {
    apiPreviewBlob = null;
    els.apiPreviewHint.textContent = "";
    els.apiPreviewRecalc.disabled = true;
    const c = els.apiPreviewCanvas?.getContext("2d");
    if (c && els.apiPreviewCanvas) c.clearRect(0, 0, els.apiPreviewCanvas.width, els.apiPreviewCanvas.height);
  }
}
els.apiPreviewClose?.addEventListener("click", () => els.apiPreviewModal.classList.add("hidden"));
els.apiPreviewBackdrop?.addEventListener("click", () => els.apiPreviewModal.classList.add("hidden"));
els.apiPreviewRecalc?.addEventListener("click", async () => {
  if (!apiPreviewBlob) return;
  const fd = new FormData();
  fd.append("file", apiPreviewBlob, "from-log.jpg");
  const conf = parseFloat(els.conf?.value || "0.7") || 0.7;
  const data = await fetchJson(`/api/predict?conf=${encodeURIComponent(conf)}`, { method: "POST", body: fd });
  const img = new Image();
  const url = URL.createObjectURL(apiPreviewBlob);
  await new Promise((resolve) => { img.onload = resolve; img.src = url; });
  drawBoxes(els.apiPreviewCanvas, img, data.detections || []);
  els.apiPreviewHint.textContent = `Пересчёт: conf=${conf}, объектов=${data.detections?.length || 0}`;
  URL.revokeObjectURL(url);
});

async function simpleJob(url, payload, statusEl, resultEl) {
  try {
    const data = await fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload || {}) });
    resultEl.textContent = JSON.stringify(data, null, 2);
    setStatus(statusEl, `Задача: ${data.id || "запущена"}`, "ok");
  } catch (e) {
    setStatus(statusEl, `Ошибка: ${e.message}`, "err");
  }
}
els.relabelCandidatesBtn?.addEventListener("click", () => simpleJob("/api/dataset/relabel-candidates", { threshold: parseFloat(els.relabelThreshold?.value || "0.99"), limit: (parseInt(els.relabelLimit?.value || "0", 10) || null), auto_approve: true }, els.relabelStatus, els.relabelResult));
els.startTrainingBtn?.addEventListener("click", () => simpleJob("/api/dataset/train", { epochs: parseInt(els.trainEpochs?.value || "30", 10), batch: parseInt(els.trainBatch?.value || "4", 10), imgsz: parseInt(els.trainImageSize?.value || "1024", 10), limit: (parseInt(els.trainLimit?.value || "0", 10) || null), train_pct: parseFloat(els.exportTrainPct?.value || "70"), val_pct: parseFloat(els.exportValPct?.value || "20"), test_pct: parseFloat(els.exportTestPct?.value || "10") }, els.trainingStatus, els.trainingResult));
els.smbIngestBtn?.addEventListener("click", () => simpleJob("/api/dataset/ingest-smb", { directories: (els.smbIngestDirs?.value || "").split(/\r?\n/).filter(Boolean), limit: parseInt(els.smbIngestLimit?.value || "20", 10), no_limit: !els.smbIngestUseLimit?.checked, dry_run: Boolean(els.smbIngestDryRun?.checked) }, els.smbIngestStatus, els.smbIngestResult));
els.cleanupDatasetBtn?.addEventListener("click", async () => setStatus(els.settingsStatus, JSON.stringify(await fetchJson("/api/dataset/cleanup", { method: "POST" })), "ok"));

const ONBOARDING_DETECT_KEY = "onboarding_detect_dismissed_v1";

async function init() {
  const onboarding = $("detectOnboarding");
  const dismissOnboarding = $("dismissDetectOnboarding");
  if (localStorage.getItem(ONBOARDING_DETECT_KEY)) {
    onboarding?.classList.add("hidden");
  }
  dismissOnboarding?.addEventListener("click", () => {
    localStorage.setItem(ONBOARDING_DETECT_KEY, "1");
    onboarding?.classList.add("hidden");
  });

  if ("serviceWorker" in navigator) {
    try {
      await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    } catch {
      /* ignore */
    }
  }

  await fetchSettings();
  await fetchDataset();
  await fetchApiHistory();
}
init();
