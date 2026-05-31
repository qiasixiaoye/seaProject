// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────
const state = {
  selection: null,
  selectMode: false,
  grid: null,
  cesiumRect: null,
  cesiumGrid: null,
  renderMode: "heatmap",
  renderAnim: null,
  renderCanvas: null,
  renderParticles: [],
  mapRenderTick: 0,
  rasterLayer: null,
  datasets: [],
  variableMeta: new Map(),
  ragStatus: null,
  domain: "auto",        // current domain tab selection
  geoApiReady: false,    // whether geo-api (/geo-api/api/health) responded OK
  streamEs: null,        // active EventSource for SSE
  reportText: "",
};

// ─────────────────────────────────────────────────────────────────────────────
// DOM refs
// ─────────────────────────────────────────────────────────────────────────────
const els = {
  health:         document.getElementById("health"),
  chatToggle:     document.getElementById("chatToggle"),
  chatFab:        document.getElementById("chatFab"),
  reportFab:      document.getElementById("reportFab"),
  chatClose:      document.getElementById("chatClose"),
  chatClear:      document.getElementById("chatClear"),
  chatDrawer:     document.getElementById("chatDrawer"),
  chatMessages:   document.getElementById("chatMessages"),
  variable:       document.getElementById("variable"),
  selectBtn:      document.getElementById("selectBtn"),
  queryBtn:       document.getElementById("queryBtn"),
  currentDemoBtn: document.getElementById("currentDemoBtn"),
  clearBtn:       document.getElementById("clearBtn"),
  stepInput:      document.getElementById("stepInput"),
  maxPointsInput: document.getElementById("maxPointsInput"),
  syncBtn:        document.getElementById("syncBtn"),
  oceanStatus:    document.getElementById("oceanStatus"),
  apiStatus:      document.getElementById("apiStatus"),
  stats:          document.getElementById("stats"),
  vmin:           document.getElementById("vmin"),
  vmax:           document.getElementById("vmax"),
  question:       document.getElementById("question"),
  backend:        document.getElementById("backend"),
  topk:           document.getElementById("topk"),
  askBtn:         document.getElementById("askBtn"),
  reportStatus:   document.getElementById("reportStatus"),
  docs:           document.getElementById("docs"),
  agentSummary:   document.getElementById("agentSummary"),
  agentTrace:     document.getElementById("agentTrace"),
  domainTabs:     document.getElementById("domainTabs"),
  suggRow:        document.getElementById("suggRow"),
  pipelineBar:    document.getElementById("pipelineBar"),
  geoTag:         document.getElementById("geoTag"),
  renderModeGroup: document.getElementById("renderModeGroup"),
  reportDock:    document.getElementById("reportDock"),
  reportBody:    document.getElementById("reportBody"),
  reportMeta:    document.getElementById("reportMeta"),
  reportClose:   document.getElementById("reportClose"),
};

// ─────────────────────────────────────────────────────────────────────────────
// Domain config
// ─────────────────────────────────────────────────────────────────────────────
const DOMAIN_INFO = {
  auto: {
    label: "自动", icon: "🔮",
    suggestions: [
      "请评估当前框选区域的综合海洋环境状况",
      "该海域近期有哪些主要风险？",
    ],
  },
  marine: {
    label: "海洋要素", icon: "🌊",
    suggestions: [
      "台湾海峡海表温度异常情况如何？",
      "该海域叶绿素浓度偏高吗？",
      "目前海表盐度是否正常？",
    ],
  },
  stargazing: {
    label: "观星", icon: "🌟",
    suggestions: [
      "今晚该海域适合观星吗？",
      "这片区域光污染程度如何？",
      "当前月相是否影响观星效果？",
    ],
  },
  biology: {
    label: "海洋生物", icon: "🐠",
    suggestions: [
      "这片海域有珊瑚白化风险吗？",
      "目前是否有赤潮（有害藻华）风险？",
      "该海域适合哪些鱼类栖息？",
    ],
  },
  navigation: {
    label: "航行安全", icon: "⚓",
    suggestions: [
      "台湾海峡适合小型渔船出海吗？",
      "当前海况对集装箱船只安全吗？",
      "近期浪高和风级评估如何？",
    ],
  },
};

// 关键词 → 领域，用于"自动"模式下本地推断
const DOMAIN_KEYWORDS = {
  stargazing: ["观星", "看星", "星星", "星空", "天文", "星座", "银河", "月相", "月亮",
               "光污染", "暗天空", "夜空", "天象", "dark sky", "milky way", "astronomy"],
  biology:    ["珊瑚", "白化", "鱼群", "藻华", "赤潮", "渔业", "生物多样性",
               "藻类", "浮游", "生态", "coral", "bloom", "biodiversity", "marine life"],
  navigation: ["航行", "船只", "渔船", "浪高", "风级", "海况", "航运", "港口",
               "出海", "行船", "波高", "涌浪", "风浪", "安全出海",
               "vessel", "wave", "maritime", "sailing"],
  marine:     ["海温", "sst", "盐度", "叶绿素", "海浪", "海洋热浪", "海平面", "水温",
               "海洋要素", "海表", "温度异常", "海流", "洋流",
               "temperature", "salinity", "chlorophyll", "sea level"],
};

function guessDomain(question) {
  const q = question.toLowerCase();
  const scores = {};
  for (const [d, kws] of Object.entries(DOMAIN_KEYWORDS)) {
    scores[d] = kws.filter((k) => q.includes(k.toLowerCase())).length;
  }
  const best = Object.entries(scores).sort((a, b) => b[1] - a[1])[0];
  return best[1] > 0 ? best[0] : "marine";
}

// ─────────────────────────────────────────────────────────────────────────────
// Globe / map globals
// ─────────────────────────────────────────────────────────────────────────────
let viewer;
let map;
let selectionSource;
let mapDragStart = null;

// ─────────────────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────────────────
boot();

async function boot() {
  if (!window.Cesium || !window.ol) {
    els.health.textContent = "Cesium/OpenLayers 未加载，请检查前端网络依赖。";
    return;
  }
  initCesium();
  initOpenLayers();
  bindEvents();
  renderSuggestions();
  await Promise.all([refreshHealth(), checkGeoApi(), refreshRagStatus(), loadDatasets()]);
}

// ─────────────────────────────────────────────────────────────────────────────
// GeoAgent health probe
// ─────────────────────────────────────────────────────────────────────────────
async function checkGeoApi() {
  try {
    const r = await fetch("/geo-api/api/health");
    if (!r.ok) throw new Error("non-ok");
    const d = await r.json();
    state.geoApiReady = true;
    els.geoTag.classList.add("online");
    const llmConfigured = d.llm?.configured ?? d.llm_configured;
    const llmModel = d.llm?.model ?? d.llm_model ?? "LLM未配置";
    els.geoTag.title = `GeoAgent 在线 · ${llmConfigured ? llmModel : "LLM未配置"}`;
    // 展示高级选项只作为备用
    document.getElementById("advOpts").removeAttribute("open");
  } catch {
    state.geoApiReady = false;
    els.geoTag.classList.remove("online");
    els.geoTag.title = "GeoAgent 离线，将使用旧版 RAG API";
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Cesium
// ─────────────────────────────────────────────────────────────────────────────
function initCesium() {
  Cesium.Ion.defaultAccessToken = "";
  viewer = new Cesium.Viewer("earth", {
    animation: false,
    baseLayer: false,
    baseLayerPicker: false,
    fullscreenButton: false,
    geocoder: false,
    homeButton: true,
    infoBox: false,
    sceneModePicker: false,
    selectionIndicator: false,
    timeline: false,
    navigationHelpButton: false,
    terrainProvider: new Cesium.EllipsoidTerrainProvider(),
  });
  viewer.imageryLayers.removeAll();
  addEarthImagery();
  viewer.scene.globe.enableLighting = true;
  viewer.scene.globe.showGroundAtmosphere = true;
  viewer.scene.skyAtmosphere.show = true;
  viewer.scene.screenSpaceCameraController.enableTilt = true;
  viewer.scene.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(145, 5, 16000000),
  });

  let start;
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.canvas);
  handler.setInputAction((event) => {
    if (!state.selectMode) return;
    start = pickLonLat(event.position);
  }, Cesium.ScreenSpaceEventType.LEFT_DOWN);
  handler.setInputAction((event) => {
    if (!state.selectMode || !start) return;
    const end = pickLonLat(event.endPosition);
    if (end) setSelection(start, end);
  }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
  handler.setInputAction(() => {
    start = null;
  }, Cesium.ScreenSpaceEventType.LEFT_UP);
}

function addEarthImagery() {
  viewer.imageryLayers.addImageryProvider(
    new Cesium.SingleTileImageryProvider({
      url: createFallbackEarthTexture(),
      rectangle: Cesium.Rectangle.MAX_VALUE,
    })
  );
  viewer.imageryLayers.addImageryProvider(
    new Cesium.UrlTemplateImageryProvider({
      url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      credit: "OpenStreetMap contributors",
      maximumLevel: 6,
    })
  );
}

function createFallbackEarthTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 2048;
  canvas.height = 1024;
  const ctx = canvas.getContext("2d");
  const ocean = ctx.createLinearGradient(0, 0, 0, canvas.height);
  ocean.addColorStop(0, "#0b2f5e");
  ocean.addColorStop(0.5, "#0f6f9f");
  ocean.addColorStop(1, "#09274e");
  ctx.fillStyle = ocean;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "rgba(255,255,255,.18)";
  ctx.lineWidth = 1;
  for (let lon = -180; lon <= 180; lon += 30) {
    const x = ((lon + 180) / 360) * canvas.width;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const y = ((90 - lat) / 180) * canvas.height;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
  }
  ctx.fillStyle = "#7fb069";
  drawLand(ctx, [[-168,15],[-55,70],[-35,8],[-82,-55],[-120,-42],[-105,8]]);
  drawLand(ctx, [[-18,35],[42,70],[102,58],[150,10],[104,-8],[45,-35],[6,-34]]);
  drawLand(ctx, [[110,-10],[154,-10],[154,-44],[114,-44]]);
  drawLand(ctx, [[-10,37],[35,34],[52,-34],[18,-35]]);
  drawLand(ctx, [[-52,60],[-20,76],[8,60],[-20,52]]);
  return canvas.toDataURL("image/png");
}

function drawLand(ctx, points) {
  ctx.beginPath();
  points.forEach(([lon, lat], index) => {
    const x = ((lon + 180) / 360) * 2048;
    const y = ((90 - lat) / 180) * 1024;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
  ctx.fill();
}

// ─────────────────────────────────────────────────────────────────────────────
// OpenLayers
// ─────────────────────────────────────────────────────────────────────────────
function initOpenLayers() {
  selectionSource = new ol.source.Vector();
  const selectionLayer = new ol.layer.Vector({
    source: selectionSource,
    style: new ol.style.Style({
      stroke: new ol.style.Stroke({ color: "#facc15", width: 3 }),
      fill: new ol.style.Fill({ color: "rgba(250, 204, 21, 0.12)" }),
    }),
  });
  map = new ol.Map({
    target: "map",
    layers: [new ol.layer.Tile({ source: new ol.source.OSM() }), selectionLayer],
    view: new ol.View({ center: ol.proj.fromLonLat([145, 5]), zoom: 2.8 }),
  });
  map.on("pointerdown", (event) => {
    mapDragStart = ol.proj.toLonLat(event.coordinate);
  });
  map.on("pointerdrag", (event) => {
    if (!mapDragStart) return;
    const end = ol.proj.toLonLat(event.coordinate);
    setSelection({ lon: mapDragStart[0], lat: mapDragStart[1] }, { lon: end[0], lat: end[1] });
  });
  map.on("pointerup", () => { mapDragStart = null; });
}

// ─────────────────────────────────────────────────────────────────────────────
// Events
// ─────────────────────────────────────────────────────────────────────────────
function bindEvents() {
  els.chatToggle.addEventListener("click", openChat);
  els.chatFab.addEventListener("click", openChat);
  els.reportFab.addEventListener("click", toggleReportDock);
  els.chatClose.addEventListener("click", closeChat);
  els.chatClear.addEventListener("click", clearChat);
  els.selectBtn.addEventListener("click", toggleSelectMode);
  els.queryBtn.addEventListener("click", queryOcean);
  els.currentDemoBtn?.addEventListener("click", runCurrentDemo);
  els.clearBtn.addEventListener("click", clearSelection);
  els.syncBtn.addEventListener("click", syncFiles);
  els.askBtn.addEventListener("click", askAgent);
  els.reportClose.addEventListener("click", hideReportDock);
  els.variable.addEventListener("change", handleVariableChange);

  els.renderModeGroup.querySelectorAll(".renderMode").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      els.renderModeGroup.querySelectorAll(".renderMode").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.renderMode = btn.dataset.mode || "heatmap";
      if (state.grid) {
        renderGridOnCesium(state.grid);
        renderGridOnMap(state.grid);
        els.oceanStatus.textContent = `已切换为${renderModeLabel(state.renderMode)}：${state.grid.dataset} / ${state.grid.long_name}`;
      }
    });
  });

  // Ctrl+Enter / Cmd+Enter 快捷发送
  els.question.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      askAgent();
    }
  });

  // 领域 Tab 切换
  els.domainTabs.querySelectorAll(".domTab").forEach((btn) => {
    btn.addEventListener("click", () => {
      els.domainTabs.querySelectorAll(".domTab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.domain = btn.dataset.domain;
      renderSuggestions();
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Domain suggestions
// ─────────────────────────────────────────────────────────────────────────────
function renderSuggestions() {
  const info = DOMAIN_INFO[state.domain];
  els.suggRow.innerHTML = (info.suggestions || []).map((s) =>
    `<button class="suggChip" onclick="setQuestion(this.textContent)">${escapeHtml(s)}</button>`
  ).join("");
}

function setQuestion(text) {
  els.question.value = text;
  els.question.focus();
}

// ─────────────────────────────────────────────────────────────────────────────
// Chat open / close / clear
// ─────────────────────────────────────────────────────────────────────────────
function openChat() {
  els.chatDrawer.classList.add("open");
  document.body.classList.add("chat-open");
}
function closeChat() {
  els.chatDrawer.classList.remove("open");
  document.body.classList.remove("chat-open");
}

function showReportDock(meta = "报告生成中...") {
  els.reportDock.classList.add("open");
  els.reportFab.classList.add("open");
  els.reportMeta.textContent = meta;
}

function hideReportDock() {
  els.reportDock.classList.remove("open");
  els.reportFab.classList.remove("open");
}

function toggleReportDock() {
  if (els.reportDock.classList.contains("open")) {
    hideReportDock();
  } else {
    showReportDock(state.reportText ? els.reportMeta.textContent : "等待生成。");
  }
}

function setReportText(text, streaming = false) {
  state.reportText = text || "";
  els.reportBody.textContent = text || "";
  els.reportBody.classList.toggle("streaming", streaming);
  els.reportFab.classList.toggle("ready", Boolean(state.reportText));
  els.reportBody.scrollTop = els.reportBody.scrollHeight;
}

function clearChat() {
  // Cancel any running stream
  if (state.streamEs) { state.streamEs.close(); state.streamEs = null; }
  els.chatMessages.innerHTML = "";
  appendMessage("ai", "对话已清空。可以继续询问海洋要素、风险评估或规划建议。");
  els.docs.innerHTML = "";
  els.agentTrace.innerHTML = "";
  renderAgentSummary(null);
  els.reportStatus.textContent = "等待提问。";
  els.reportMeta.textContent = "等待生成。";
  setReportText("");
  hideReportDock();
  hidePipeline();
}

// ─────────────────────────────────────────────────────────────────────────────
// Pipeline bar helpers
// ─────────────────────────────────────────────────────────────────────────────
const PIPELINE_STEPS = ["intent", "retrieval", "context", "reasoning", "report"];

function showPipeline() { els.pipelineBar.classList.add("visible"); }
function hidePipeline() {
  els.pipelineBar.classList.remove("visible");
  PIPELINE_STEPS.forEach((s) => {
    const el = document.getElementById("ps-" + s);
    if (el) el.className = "pStep";
  });
}

function setPipelineStep(id, status) {
  // status: "active" | "done" | ""
  const el = document.getElementById("ps-" + id);
  if (el) el.className = "pStep " + status;
}

// ─────────────────────────────────────────────────────────────────────────────
// Main ask dispatcher — routes to GeoAgent (SSE) or legacy RAG API
// ─────────────────────────────────────────────────────────────────────────────
async function askAgent() {
  openChat();
  const question = els.question.value.trim();
  if (!question) return;

  // Cancel previous stream if any
  if (state.streamEs) { state.streamEs.close(); state.streamEs = null; }

  appendMessage("user", question);
  els.askBtn.disabled = true;

  if (state.geoApiReady) {
    await askGeoAgent(question);
  } else {
    await askLegacyAgent(question);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// GeoAgent — SSE streaming
// ─────────────────────────────────────────────────────────────────────────────
async function askGeoAgent(question) {
  const domain = state.domain === "auto" ? guessDomain(question) : state.domain;
  const bbox = state.selection || null;

  els.reportStatus.textContent = `GeoAgent · ${DOMAIN_INFO[domain]?.icon || ""} ${DOMAIN_INFO[domain]?.label || domain} · 分析中...`;
  els.docs.innerHTML = "";
  els.agentTrace.innerHTML = "";
  renderAgentSummary({ status: "running", domain });

  showPipeline();
  setPipelineStep("intent", "active");

  // Create a streaming AI message bubble
  const aiMsg = appendMessage("ai", "");
  aiMsg.classList.add("streaming");
  aiMsg.classList.add("reportPreview");
  let streamBuffer = "";
  showReportDock(`GeoAgent · ${DOMAIN_INFO[domain]?.label || domain} · 生成中`);
  setReportText("", true);

  const params = new URLSearchParams({ question, domain });
  if (bbox) params.set("bbox", JSON.stringify(bbox));

  const es = new EventSource(`/geo-api/api/geo/stream?${params}`);
  state.streamEs = es;

  es.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    const { type } = msg;
    const payload = msg.data ?? msg.content;
    const message = msg.message ?? msg.content;
    const usage = msg.usage ?? msg.token_usage;

    if (type === "domain") {
      // domain confirmed by backend
    }
    else if (type === "intent") {
      setPipelineStep("intent", "done");
      setPipelineStep("retrieval", "active");
      setPipelineStep("context", "active");
      if (payload) {
        renderAgentSummary({
          status: "running",
          domain: payload.domain || domain,
          intent_type: payload.intent_type,
          topics: payload.topics,
        });
      }
    }
    else if (type === "context") {
      setPipelineStep("context", "done");
      setPipelineStep("retrieval", "done");
      setPipelineStep("reasoning", "active");
      if (payload) appendContextChip(payload);
    }
    else if (type === "analysis") {
      setPipelineStep("reasoning", "done");
      setPipelineStep("report", "active");
    }
    else if (type === "revision") {
      // Critic requested a revision — briefly flash the report step
      setPipelineStep("report", "active");
      appendGeoTraceItem("🔄 Critic 修订", "报告未通过审查，正在修订...");
    }
    else if (type === "token") {
      // Stream token into the AI message bubble
      if (payload) {
        streamBuffer += payload;
        aiMsg.textContent = streamBuffer;
        // Re-add blinking cursor
        const cur = document.createElement("span");
        cur.className = "streamCursor";
        aiMsg.appendChild(cur);
        setReportText(streamBuffer, true);
        els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
      }
    }
    else if (type === "done") {
      // Finalize
      aiMsg.classList.remove("streaming");
      aiMsg.textContent = streamBuffer;   // remove cursor
      setReportText(streamBuffer, false);
      els.reportMeta.textContent = `完成 · ${DOMAIN_INFO[domain]?.label || domain}`;
      setPipelineStep("report", "done");
      hidePipeline();
      els.reportStatus.textContent = `完成 · ${DOMAIN_INFO[domain]?.icon || ""} ${DOMAIN_INFO[domain]?.label || domain}`;
      renderAgentSummary({ status: "done", domain, usage });
      els.askBtn.disabled = false;
      state.streamEs = null;
      es.close();
    }
    else if (type === "error") {
      aiMsg.classList.remove("streaming");
      aiMsg.textContent = `分析失败：${message || "未知错误"}`;
      setReportText(`分析失败：${message || "未知错误"}`, false);
      els.reportMeta.textContent = "生成失败";
      aiMsg.classList.add("error");
      hidePipeline();
      els.reportStatus.textContent = "生成失败。";
      els.askBtn.disabled = false;
      state.streamEs = null;
      es.close();
    }
  };

  es.onerror = () => {
    if (!streamBuffer) {
      // Never got any tokens — hard failure
      aiMsg.classList.remove("streaming");
      aiMsg.textContent = "连接 GeoAgent 失败，正在切换到旧版 API…";
      setReportText("连接 GeoAgent 失败，正在切换到旧版 API…", false);
      aiMsg.classList.add("warn");
      state.geoApiReady = false;
      els.geoTag.classList.remove("online");
      hidePipeline();
      es.close();
      state.streamEs = null;
      // Retry with legacy
      askLegacyAgent(els.question.value.trim());
    } else {
      // Got some tokens then disconnected — treat as done
      aiMsg.classList.remove("streaming");
      aiMsg.textContent = streamBuffer;
      setReportText(streamBuffer, false);
      els.reportMeta.textContent = "完成（连接中断）";
      hidePipeline();
      els.reportStatus.textContent = "完成（连接中断）";
      els.askBtn.disabled = false;
      state.streamEs = null;
    }
  };
}

function appendContextChip(contextData) {
  const vars = (contextData.variables_queried || []).join("、") || "无";
  appendGeoTraceItem("📊 海洋数据", `已查询变量：${vars}`);
}

function appendGeoTraceItem(title, detail) {
  const div = document.createElement("div");
  div.className = "traceItem";
  div.innerHTML = `
    <span class="traceDot"></span>
    <div><b>${escapeHtml(title)}</b><p>${escapeHtml(detail)}</p></div>
  `;
  els.agentTrace.appendChild(div);
}

// ─────────────────────────────────────────────────────────────────────────────
// Legacy RAG pipeline (original code, unchanged logic)
// ─────────────────────────────────────────────────────────────────────────────
async function askLegacyAgent(question) {
  els.reportStatus.textContent = "多 Agent 报告生成中...";
  els.docs.innerHTML = "";
  els.agentTrace.innerHTML = "";
  renderAgentSummary({ status: "running" });
  const loading = appendMessage("ai", "正在进行意图凝练、RAG 检索、证据筛选、报告生成和 Critic 审查");
  loading.classList.add("loading");
  loading.classList.add("reportPreview");
  showReportDock("旧版多 Agent · 生成中");
  setReportText("正在进行意图凝练、RAG 检索、证据筛选、报告生成和 Critic 审查", true);
  const started = performance.now();
  try {
    const data = await fetchJson("/api/agents/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: buildAgentQuestion(question),
        backend: els.backend.value,
        top_k: Number(els.topk.value),
        max_revisions: 0,
        trace: true,
        region: state.selection || null,
        variables: state.grid ? [state.grid.variable] : [],
      }),
    });
    loading.remove();
    els.reportStatus.textContent = `完成：${data.backend} RAG + ${data.llm?.configured ? data.llm.model : "local fallback"}；${Math.round(performance.now() - started)} ms`;
    appendMessage("ai", data.report);
    setReportText(data.report, false);
    els.reportMeta.textContent = `完成 · ${data.backend} RAG`;
    renderAgentSummary(data);
    renderTrace(data.trace || []);
    renderEvidence(data);
    await refreshRagStatus();
  } catch (error) {
    loading.remove();
    els.reportStatus.textContent = "生成失败。";
    appendMessage("ai", `生成失败：${error.message}`);
    setReportText(`生成失败：${error.message}`, false);
    els.reportMeta.textContent = "生成失败";
    renderAgentSummary({ status: "failed", error: error.message });
  } finally {
    els.askBtn.disabled = false;
  }
}

function buildAgentQuestion(question) {
  const extra = [];
  if (state.selection) {
    const b = state.selection;
    extra.push(`当前框选区域：经度 ${b.west.toFixed(2)} 至 ${b.east.toFixed(2)}，纬度 ${b.south.toFixed(2)} 至 ${b.north.toFixed(2)}。`);
  }
  if (state.grid) {
    extra.push(`当前已渲染要素：${state.grid.long_name}(${state.grid.variable})，单位 ${state.grid.units || "-"}，均值 ${state.grid.stats.mean}，最小 ${state.grid.stats.min}，最大 ${state.grid.stats.max}，有效格点 ${state.grid.stats.count}。`);
  }
  return extra.length ? `${question}\n\n请同时参考以下空间数据上下文：\n${extra.join("\n")}` : question;
}

// ─────────────────────────────────────────────────────────────────────────────
// Agent summary chips  (handles both GeoAgent and legacy data)
// ─────────────────────────────────────────────────────────────────────────────
function renderAgentSummary(data) {
  const rag = state.ragStatus?.ragflow;
  const local = state.ragStatus?.local;

  const chips = [
    chip(state.geoApiReady ? "GeoAgent" : "RAG API",
         state.geoApiReady ? "在线" : "旧版",
         state.geoApiReady ? "cyan" : "dim"),
    chip("LLM",
         state.ragStatus?.llm?.configured ? state.ragStatus.llm.model : "local fallback",
         "cyan"),
  ];

  if (!state.geoApiReady) {
    chips.push(chip("RAGFlow", rag?.configured ? `${rag.dataset_ids.length} datasets` : "未绑定", rag?.configured ? "cyan" : "warn"));
    chips.push(chip("Local Docs", local ? `${local.document_count}` : "-", "cyan"));
  }

  if (data?.domain && data.domain !== "auto") {
    const info = DOMAIN_INFO[data.domain] || {};
    chips.push(chip("领域", `${info.icon || ""} ${info.label || data.domain}`, "cyan"));
  }
  if (data?.intent_type) {
    chips.push(chip("意图", data.intent_type, "dim"));
  }
  if ((data?.topics || []).length) {
    chips.push(chip("主题", data.topics.slice(0, 3).join("、"), "dim"));
  }
  if (data?.usage) {
    const u = data.usage;
    chips.push(chip("Tokens", `${(u.input_tokens || 0) + (u.output_tokens || 0)}`, "dim"));
    if (u.estimated_cost_usd) {
      chips.push(chip("费用", `$${u.estimated_cost_usd.toFixed(5)}`, "dim"));
    }
  }
  // Legacy fields
  if (data?.task_id) {
    chips.push(chip("Task", data.task_id, "dim"));
    chips.push(chip("Critic", data.critic?.passed === false ? "需修订" : "通过",
                    data.critic?.passed === false ? "warn" : "cyan"));
    chips.push(chip("Revision", String(data.revisions || 0), "dim"));
    if ((data.risk_hypotheses || []).length) chips.push(chip("Risk", String(data.risk_hypotheses.length), "warn"));
  } else if (data?.status === "running") {
    chips.push(chip("Pipeline", "running", "warn"));
  } else if (data?.status === "failed") {
    chips.push(chip("Pipeline", "failed", "warn"));
  }

  els.agentSummary.innerHTML = chips.join("");
}

function chip(label, value, tone) {
  return `<span class="chip ${tone || ""}"><small>${escapeHtml(label)}</small>${escapeHtml(value || "-")}</span>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Legacy trace / evidence (unchanged)
// ─────────────────────────────────────────────────────────────────────────────
function renderTrace(trace) {
  els.agentTrace.innerHTML = trace.map((event) => {
    const title = event.agent || event.node || "Agent";
    const meta = summarizeEvent(event);
    return `<div class="traceItem">
      <span class="traceDot"></span>
      <div><b>${escapeHtml(title)}</b><p>${escapeHtml(meta)}</p></div>
      <time>${escapeHtml(String(event.t_ms ?? ""))} ms</time>
    </div>`;
  }).join("");
}

function summarizeEvent(event) {
  if (event.node) {
    return summarizeGeoNodeEvent(event);
  }
  if (event.agent === "IntentAgent") {
    const topics = event.output?.topics || [];
    return `意图=${event.output?.intent || "-"}；主题=${topics.join("、") || "-"}`;
  }
  if (event.agent === "RetrievalAgent") {
    const tc = event.tool_calls != null ? `；工具调用=${event.tool_calls}` : "";
    return `模式=${event.mode || "-"}；后端=${event.backend || "-"}；候选=${event.candidate_count || 0}${tc}`;
  }
  if (event.agent === "ContextAgent") {
    if (event.mode === "skip") return event.note || "跳过(无区域)";
    const stats = (event.stats || []).map((s) => `${s.variable}=${s.mean}`).join("、");
    return `区域要素：${stats || (event.variables || []).join("、") || "-"}`;
  }
  if (event.agent === "DomainReasoningAgent") {
    return `模式=${event.mode || "-"}；风险假设=${(event.hypotheses || []).length}`;
  }
  if (event.agent === "ScreeningAgent") {
    const decisions = event.decisions || [];
    const kept = decisions.filter((x) => x.decision === "keep").length;
    return `模式=${event.mode || "-"}；保留=${kept}；过滤=${Math.max(0, decisions.length - kept)}`;
  }
  if (event.agent === "ReportAgent") {
    return `${event.revised ? "按 Critic 意见修订" : "生成初稿"}；长度=${event.chars || 0}`;
  }
  if (event.agent === "CriticAgent") {
    return `审查=${event.passed === false ? "未通过" : "通过"}；问题=${(event.issues || []).length}`;
  }
  return event.status || event.mode || "完成";
}

function summarizeGeoNodeEvent(event) {
  if (event.node === "IntentNode") {
    return `模式=${event.mode || "-"}；领域=${event.domain || "-"}`;
  }
  if (event.node === "RetrievalNode") {
    return `模式=${event.mode || "-"}；后端=${event.backend || "-"}；候选=${event.count || 0}`;
  }
  if (event.node === "ContextNode") {
    if (event.mode === "skipped") return event.reason || "跳过";
    return `变量=${(event.vars_queried || []).join("、") || "-"}；成功=${event.vars_ok ?? 0}`;
  }
  if (event.node === "ScreeningNode") {
    return `模式=${event.mode || "-"}；保留=${event.kept ?? 0}；过滤=${event.passed ?? 0}`;
  }
  if (event.node === "ReasoningNode") {
    return `领域=${event.domain || "-"}；风险假设=${event.hypotheses_count ?? 0}`;
  }
  if (event.node === "ReportNode") {
    return `${event.revised ? "按 Critic 意见修订" : "生成初稿"}；长度=${event.chars || 0}`;
  }
  if (event.node === "CriticNode") {
    return `审查=${event.passed === false ? "未通过" : "通过"}；问题=${(event.issues || []).length}`;
  }
  return event.status || event.mode || "完成";
}

function renderEvidence(data) {
  const kept = (data.kept_documents || []).map((doc) => ({ ...doc, decision: "keep" }));
  const passed = (data.passed_documents || []).map((doc) => ({ ...doc, decision: "pass" }));
  const docs = [...kept, ...passed];
  els.docs.innerHTML = docs.map((doc) => `
    <article class="doc ${doc.decision}">
      <div class="docTop">
        <b>${escapeHtml(doc.title)}</b>
        <span>${doc.decision === "keep" ? "KEEP" : "PASS"}</span>
      </div>
      <p>${escapeHtml(evidenceMeta(doc))}</p>
      <p>${escapeHtml(doc.reason || doc.abstract || "无筛选理由").slice(0, 260)}</p>
      ${doc.source ? `<p class="docSource">${escapeHtml(doc.source)}</p>` : ""}
    </article>
  `).join("");
}

function evidenceMeta(doc) {
  const meta = doc.metadata || {};
  const parts = [doc.kind || doc.backend || "document", `score=${doc.decision_score || doc.score || 0}`];
  if (meta.dataset_name) parts.push(meta.dataset_name);
  if (meta.dataset_language) parts.push(meta.dataset_language);
  if (meta.pages?.length) parts.push(`p.${meta.pages.join(",")}`);
  if (meta.vector_similarity) parts.push(`vec=${Number(meta.vector_similarity).toFixed(3)}`);
  if (meta.term_similarity) parts.push(`term=${Number(meta.term_similarity).toFixed(3)}`);
  return parts.join(" · ");
}

// ─────────────────────────────────────────────────────────────────────────────
// Ocean layer / selection / stats (unchanged)
// ─────────────────────────────────────────────────────────────────────────────
function toggleSelectMode() {
  state.selectMode = !state.selectMode;
  els.selectBtn.classList.toggle("active", state.selectMode);
  els.selectBtn.textContent = state.selectMode ? "退出" : "框选";
  document.getElementById("earth").classList.toggle("selecting", state.selectMode);
  viewer.scene.screenSpaceCameraController.enableRotate = !state.selectMode;
  viewer.scene.screenSpaceCameraController.enableTranslate = !state.selectMode;
  els.oceanStatus.textContent = state.selectMode
    ? "框选模式已开启：在地球或右下角二维图上拖拽。"
    : "地球浏览模式：可旋转、缩放和定位。";
}

async function refreshHealth() {
  const data = await fetchJson("/api/health");
  els.health.textContent = `API=${data.status} | LLM=${data.llm.configured ? data.llm.model : "未配置"} | GeoServer=${data.geoserver.public_url}`;
  els.apiStatus.textContent = `接口：GET /api/health 已连接；LLM=${data.llm.configured ? data.llm.model : "local fallback"}。`;
}

async function refreshRagStatus() {
  const data = await fetchJson("/api/rag/status");
  state.ragStatus = data;
  renderAgentSummary(null);
}

async function loadDatasets() {
  const data = await fetchJson("/api/ocean/datasets");
  state.datasets = data.datasets || [];
  state.variableMeta = new Map();
  const options = [];
  for (const dataset of state.datasets) {
    for (const variable of dataset.variables || []) {
      const res = dataset.resolution?.lat && dataset.resolution?.lon
        ? ` | ${dataset.resolution.lat}x${dataset.resolution.lon}°` : "";
      const step = variable.recommended_step || dataset.recommended_step || 1;
      const value = `${dataset.id}::${variable.name}`;
      const meta = { ...variable, dataset: dataset.id, dataset_resolution: dataset.resolution || {} };
      state.variableMeta.set(value, meta);
      const category = renderCategoryLabel(meta);
      options.push(`<option value="${escapeAttr(value)}">${escapeHtml(dataset.id)} / ${escapeHtml(variable.name)} - ${escapeHtml(variable.long_name || variable.name)} | ${category}${res} | step≥${step}</option>`);
    }
  }
  els.variable.innerHTML = options.join("");
  updateRenderModeAvailability();
}

function handleVariableChange() {
  stopRenderAnimation();
  state.grid = null;
  clearGrid();
  drawSelection();
  updateRenderModeAvailability();
  const meta = selectedVariableMeta();
  els.oceanStatus.textContent = `已切换变量：${meta?.dataset || "-"} / ${meta?.long_name || meta?.name || "-"}。点击“渲染”后读取 NetCDF。`;
  els.apiStatus.textContent = "接口：仅切换变量，尚未请求后端。";
}

function selectedVariableMeta() {
  return state.variableMeta.get(els.variable.value) || null;
}

function renderCategoryLabel(meta) {
  if (!meta) return "标量";
  if (meta.category === "relief") return "地形/水深";
  if (meta.category === "vector_component") return meta.particle_ready ? "矢量/粒子" : "矢量分量";
  const text = `${meta.name || ""} ${meta.long_name || ""}`.toLowerCase();
  if (text.includes("chlor")) return "叶绿素";
  if (text.includes("salinity") || text.includes("sss")) return "盐度";
  if (text.includes("sst") || text.includes("temperature")) return "温度";
  if (text.includes("wave") || text.includes("swell")) return "海浪";
  return "标量";
}

function allowedRenderModes(meta) {
  const modes = meta?.render_modes?.length ? meta.render_modes : ["heatmap", "contour", "points"];
  return new Set(modes);
}

function updateRenderModeAvailability() {
  const meta = selectedVariableMeta();
  const allowed = allowedRenderModes(meta);
  if (!allowed.has(state.renderMode)) {
    state.renderMode = "heatmap";
  }
  els.renderModeGroup.querySelectorAll(".renderMode").forEach((btn) => {
    const mode = btn.dataset.mode || "heatmap";
    const enabled = allowed.has(mode);
    btn.disabled = !enabled;
    btn.classList.toggle("active", mode === state.renderMode);
    if (enabled) {
      btn.title = {
        heatmap: "连续填色栅格",
        particles: "真实 u/v 矢量场粒子流",
        contour: "等值线叠加",
        points: "采样点符号图",
      }[mode] || "";
    } else if (mode === "particles") {
      btn.title = meta?.vector_pair
        ? "已识别到矢量分量；接入 u/v 联合查询后开放粒子流"
        : "粒子流只对真实 u/v 风场或海流开放";
    } else if (!enabled) {
      btn.title = "当前变量不适合该渲染方式";
    }
  });
}

async function syncFiles() {
  const started = performance.now();
  const data = await fetchJson("/api/sync", { method: "POST" });
  els.oceanStatus.textContent = `已同步 ${data.files.length} 个本地 NC/PDF 文件。`;
  els.apiStatus.textContent = `接口：POST /api/sync，用时 ${Math.round(performance.now() - started)} ms。`;
  await Promise.all([loadDatasets(), refreshRagStatus()]);
}

function pickLonLat(position) {
  const cartesian = viewer.camera.pickEllipsoid(position, viewer.scene.globe.ellipsoid);
  if (!cartesian) return null;
  const cartographic = Cesium.Cartographic.fromCartesian(cartesian);
  return {
    lon: Cesium.Math.toDegrees(cartographic.longitude),
    lat: Cesium.Math.toDegrees(cartographic.latitude),
  };
}

function setSelection(a, b) {
  const west  = Math.max(-180, Math.min(a.lon, b.lon));
  const east  = Math.min(180,  Math.max(a.lon, b.lon));
  const south = Math.max(-90,  Math.min(a.lat, b.lat));
  const north = Math.min(90,   Math.max(a.lat, b.lat));
  if (Math.abs(east - west) < 0.1 || Math.abs(north - south) < 0.1) return;
  state.selection = { west, east, south, north };
  state.grid = null;
  clearGrid();
  drawSelection();
  els.oceanStatus.textContent = `已框选：经度 ${west.toFixed(2)} 至 ${east.toFixed(2)}，纬度 ${south.toFixed(2)} 至 ${north.toFixed(2)}。`;
  els.apiStatus.textContent = '接口：仅更新前端框选，尚未请求后端。点击“渲染”后才读取 NetCDF。';
}

function drawSelection() {
  if (!state.selection) return;
  const b = state.selection;
  if (state.cesiumRect) viewer.entities.remove(state.cesiumRect);
  state.cesiumRect = viewer.entities.add({
    rectangle: {
      coordinates: Cesium.Rectangle.fromDegrees(b.west, b.south, b.east, b.north),
      material: Cesium.Color.YELLOW.withAlpha(0.16),
      outline: true,
      outlineColor: Cesium.Color.YELLOW,
      outlineWidth: 3,
    },
  });
  selectionSource.clear();
  const polygon = ol.geom.Polygon.fromExtent(
    ol.proj.transformExtent([b.west, b.south, b.east, b.north], "EPSG:4326", "EPSG:3857")
  );
  selectionSource.addFeature(new ol.Feature(polygon));
}

async function queryOcean() {
  if (!state.selection) {
    els.oceanStatus.textContent = "请先开启框选并选择一个区域。";
    return;
  }
  updateRenderModeAvailability();
  const [dataset, variable] = els.variable.value.split("::");
  if (!allowedRenderModes(selectedVariableMeta()).has(state.renderMode)) {
    state.renderMode = "heatmap";
    updateRenderModeAvailability();
  }
  const step = Math.max(0, Number(els.stepInput.value || 0));
  const maxPoints = Math.max(100, Math.min(50000, Number(els.maxPointsInput.value || 9000)));
  els.oceanStatus.textContent = "正在读取 NetCDF 并生成区域栅格...";
  const started = performance.now();
  els.apiStatus.textContent = `接口：POST /api/ocean/query 请求中；step=${step || "auto"}，max_points=${maxPoints}。`;
  try {
    const data = await fetchJson("/api/ocean/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset, variable, bounds: state.selection, step, max_points: maxPoints }),
    });
    state.grid = data;
    renderStats(data);
    renderGridOnCesium(data);
    renderGridOnMap(data);
    els.oceanStatus.textContent = `已渲染 ${data.dataset} / ${data.long_name}`;
    els.apiStatus.textContent = `接口：POST /api/ocean/query；后端切片读取 ${data.source}；step=${data.step}；格点=${data.shape?.lat || data.values.length}x${data.shape?.lon || data.values[0]?.length || 0}；用时 ${Math.round(performance.now() - started)} ms。`;
  } catch (error) {
    clearGrid();
    els.oceanStatus.textContent = `未渲染：${error.message}。请确认框选区域与所选数据集覆盖范围相交，或调大步长/降低格点上限。`;
    els.apiStatus.textContent = `接口：POST /api/ocean/query 失败；用时 ${Math.round(performance.now() - started)} ms。`;
  }
}

async function runCurrentDemo() {
  const value = "hycom_luzon_uv_surface_20240905::water_u";
  const option = Array.from(els.variable.options).find((item) => item.value === value);
  if (!option) {
    els.oceanStatus.textContent = "未找到 HYCOM 海流样例，请先确认 hycom_luzon_uv_surface_20240905.nc 已在 data/nc_uploads 中。";
    return;
  }
  els.variable.value = value;
  state.renderMode = "particles";
  els.stepInput.value = "1";
  els.maxPointsInput.value = "9000";
  setSelection({ lon: 117, lat: 18 }, { lon: 127, lat: 26 });
  updateRenderModeAvailability();
  await queryOcean();
}

function clearSelection() {
  state.selection = null;
  state.grid = null;
  clearGrid();
  if (state.cesiumRect) viewer.entities.remove(state.cesiumRect);
  state.cesiumRect = null;
  selectionSource.clear();
  els.oceanStatus.textContent = "已擦除框选与渲染图层；地球可继续旋转浏览。";
  els.apiStatus.textContent = "接口：擦除为前端操作，未请求后端。";
}

function renderStats(data) {
  els.vmin.textContent = `${data.stats.min} ${data.units || ""}`;
  els.vmax.textContent = `${data.stats.max} ${data.units || ""}`;
  els.stats.innerHTML = [
    ["最小", data.stats.min], ["最大", data.stats.max], ["均值", data.stats.mean],
    ["格点", data.stats.count], ["步长", data.step || 1],
  ].map(([k, v]) => `<div class="stat"><span>${k}</span><b>${escapeHtml(String(v))}</b></div>`).join("");
}

function renderGridOnCesium(data) {
  if (state.cesiumGrid) viewer.entities.remove(state.cesiumGrid);
  stopRenderAnimation();
  const canvas = renderCanvas(data, 900, 540, state.renderMode);
  const b = data.bounds;
  state.renderCanvas = canvas;
  state.cesiumGrid = viewer.entities.add({
    rectangle: {
      coordinates: Cesium.Rectangle.fromDegrees(b.west, b.south, b.east, b.north),
      material: new Cesium.ImageMaterialProperty({ image: canvas, transparent: true }),
    },
  });
  if (state.renderMode === "particles") {
    startParticleAnimation(data, canvas);
  }
}

function renderGridOnMap(data) {
  if (state.rasterLayer) map.removeLayer(state.rasterLayer);
  const b = data.bounds;
  const extent = ol.proj.transformExtent([b.west, b.south, b.east, b.north], "EPSG:4326", "EPSG:3857");
  const canvas = renderCanvas(data, 900, 540, state.renderMode);
  state.rasterLayer = new ol.layer.Image({
    source: new ol.source.ImageStatic({
      url: canvas.toDataURL("image/png"),
      imageExtent: extent,
      projection: "EPSG:3857",
    }),
    opacity: state.renderMode === "particles" ? 0.9 : 0.78,
  });
  map.getLayers().insertAt(1, state.rasterLayer);
  map.getView().fit(extent, { padding: [30, 30, 30, 30], duration: 350 });
}

function clearGrid() {
  stopRenderAnimation();
  if (state.cesiumGrid) viewer.entities.remove(state.cesiumGrid);
  state.cesiumGrid = null;
  state.renderCanvas = null;
  state.renderParticles = [];
  if (state.rasterLayer) map.removeLayer(state.rasterLayer);
  state.rasterLayer = null;
  els.stats.innerHTML = "";
  els.vmin.textContent = "-";
  els.vmax.textContent = "-";
}

function renderModeLabel(mode) {
  return {
    heatmap: "填色图",
    particles: "粒子流",
    contour: "等值线",
    points: "采样点图",
  }[mode] || "填色图";
}

function renderCanvas(data, width, height, mode) {
  const allowed = allowedRenderModes(data);
  if (!allowed.has(mode)) mode = "heatmap";
  if (mode === "particles") return particleCanvas(data, width, height);
  if (mode === "contour") return contourCanvas(data, width, height);
  if (mode === "points") return pointCanvas(data, width, height);
  return gridCanvas(data, width, height);
}

function gridCanvas(data, width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  const min = data.stats.min;
  const max = data.stats.max;
  const image = ctx.createImageData(width, height);
  const pixels = image.data;
  for (let y = 0; y < height; y++) {
    const ny = height <= 1 ? 0 : y / (height - 1);
    for (let x = 0; x < width; x++) {
      const nx = width <= 1 ? 0 : x / (width - 1);
      const value = sampleInterpolatedGrid(data, nx, ny);
      if (shouldSkipValue(data, value)) continue;
      const rgb = colorRgb(normalizeValue(value, min, max));
      const k = (y * width + x) * 4;
      pixels[k] = rgb[0];
      pixels[k + 1] = rgb[1];
      pixels[k + 2] = rgb[2];
      pixels[k + 3] = data.category === "vector" ? 205 : 190;
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}

function pointCanvas(data, width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  const rows = data.values.length;
  const cols = data.values[0]?.length || 0;
  const min = data.stats.min;
  const max = data.stats.max;
  const skip = Math.max(1, Math.ceil(Math.max(rows, cols) / 46));
  ctx.globalCompositeOperation = "source-over";
  for (let i = 0; i < rows; i += skip) {
    for (let j = 0; j < cols; j += skip) {
      const value = data.values[i][j];
      if (shouldSkipValue(data, value)) continue;
      const t = normalizeValue(value, min, max);
      const x = ((j + 0.5) / Math.max(1, cols)) * width;
      const y = ((i + 0.5) / Math.max(1, rows)) * height;
      const radius = 2.8 + t * 7.5;
      ctx.beginPath();
      ctx.fillStyle = colorAlpha(t, 0.72);
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,.38)";
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }
  }
  return canvas;
}

function contourCanvas(data, width, height) {
  const canvas = gridCanvas(data, width, height);
  const ctx = canvas.getContext("2d");
  const rows = data.values.length;
  const cols = data.values[0]?.length || 0;
  const min = data.stats.min;
  const max = data.stats.max;
  const cw = width / Math.max(1, cols - 1);
  const ch = height / Math.max(1, rows - 1);
  ctx.globalCompositeOperation = "source-over";
  ctx.lineWidth = 1.4;
  ctx.shadowColor = "rgba(0,0,0,.55)";
  ctx.shadowBlur = 2;
  for (let k = 1; k <= 8; k++) {
    const level = min + (max - min) * (k / 9);
    ctx.strokeStyle = k % 2 ? "rgba(238,253,251,.72)" : "rgba(69,240,222,.78)";
    for (let i = 0; i < rows - 1; i++) {
      for (let j = 0; j < cols - 1; j++) {
        const v00 = data.values[i][j];
        const v10 = data.values[i][j + 1];
        const v11 = data.values[i + 1]?.[j + 1];
        const v01 = data.values[i + 1]?.[j];
        if ([v00, v10, v11, v01].some((v) => shouldSkipValue(data, v))) continue;
        const pts = marchingCell(v00, v10, v11, v01, level, j * cw, i * ch, cw, ch);
        for (let p = 0; p < pts.length; p += 2) {
          ctx.beginPath();
          ctx.moveTo(pts[p].x, pts[p].y);
          ctx.lineTo(pts[p + 1].x, pts[p + 1].y);
          ctx.stroke();
        }
      }
    }
  }
  ctx.shadowBlur = 0;
  return canvas;
}

function particleCanvas(data, width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  if (!data.u_grid || !data.v_grid) {
    return gridCanvas(data, width, height);
  }
  seedParticles(data, width, height);
  drawParticles(data, canvas, true);
  return canvas;
}

function seedParticles(data, width, height) {
  const rows = data.values.length;
  const cols = data.values[0]?.length || 0;
  const count = Math.max(120, Math.min(520, Math.floor(rows * cols * 0.18)));
  state.renderParticles = Array.from({ length: count }, () => randomParticle(data, width, height));
}

function randomParticle(data, width, height) {
  for (let tries = 0; tries < 20; tries++) {
    const x = Math.random() * width;
    const y = Math.random() * height;
    if (sampleVector(data, x / width, y / height)) {
      return { x, y, life: 35 + Math.random() * 130, speed: 1.1 + Math.random() * 1.9 };
    }
  }
  return {
    x: Math.random() * width,
    y: Math.random() * height,
    life: 35 + Math.random() * 130,
    speed: 1.1 + Math.random() * 1.9,
  };
}

function startParticleAnimation(data, canvas) {
  const tick = () => {
    drawParticles(data, canvas, false);
    if (viewer?.scene) viewer.scene.requestRender();
    state.renderAnim = requestAnimationFrame(tick);
  };
  state.renderAnim = requestAnimationFrame(tick);
}

function stopRenderAnimation() {
  if (state.renderAnim) cancelAnimationFrame(state.renderAnim);
  state.renderAnim = null;
}

function drawParticles(data, canvas, initial) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  if (initial) {
    ctx.clearRect(0, 0, width, height);
    const base = gridCanvas(data, width, height);
    ctx.globalAlpha = 0.34;
    ctx.drawImage(base, 0, 0);
    ctx.globalAlpha = 1;
  } else {
    ctx.fillStyle = "rgba(2, 8, 10, .085)";
    ctx.fillRect(0, 0, width, height);
  }
  ctx.lineCap = "round";
  ctx.lineWidth = 1.35;
  const min = data.stats.min;
  const max = data.stats.max;
  for (const p of state.renderParticles) {
    const beforeX = p.x;
    const beforeY = p.y;
    const flow = sampleVector(data, p.x / width, p.y / height);
    if (!flow) {
      Object.assign(p, randomParticle(data, width, height));
      continue;
    }
    const scale = 9.5 / Math.max(0.08, max);
    p.x += flow.u * scale * p.speed;
    p.y -= flow.v * scale * p.speed;
    p.life -= 1;
    if (p.x < 0 || p.x > width || p.y < 0 || p.y > height || p.life <= 0) {
      Object.assign(p, randomParticle(data, width, height));
      continue;
    }
    const t = normalizeValue(flow.speed ?? min, min, max);
    ctx.strokeStyle = colorAlpha(t, 0.55 + t * 0.35);
    ctx.beginPath();
    ctx.moveTo(beforeX, beforeY);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  }
}

function sampleVector(data, nx, ny) {
  if (!data.u_grid || !data.v_grid) return null;
  const u = sampleGridValue(data.u_grid, nx, ny);
  const v = sampleGridValue(data.v_grid, nx, ny);
  const speed = sampleGrid(data, nx, ny);
  if (u === null || v === null || speed === null) return null;
  return { u, v, speed };
}

function sampleGrid(data, nx, ny) {
  return sampleGridValue(data.values, nx, ny);
}

function sampleGridValue(values, nx, ny) {
  const rows = values.length;
  const cols = values[0]?.length || 0;
  if (!rows || !cols) return null;
  const x = Math.max(0, Math.min(cols - 1, nx * (cols - 1)));
  const y = Math.max(0, Math.min(rows - 1, ny * (rows - 1)));
  const i = Math.floor(y);
  const j = Math.floor(x);
  return values[i]?.[j] ?? null;
}

function sampleInterpolatedGrid(data, nx, ny) {
  const values = data.values;
  const rows = values.length;
  const cols = values[0]?.length || 0;
  if (!rows || !cols) return null;
  const x = Math.max(0, Math.min(cols - 1, nx * (cols - 1)));
  const y = Math.max(0, Math.min(rows - 1, ny * (rows - 1)));
  const j0 = Math.floor(x);
  const i0 = Math.floor(y);
  const j1 = Math.min(cols - 1, j0 + 1);
  const i1 = Math.min(rows - 1, i0 + 1);
  const v00 = values[i0]?.[j0];
  const v10 = values[i0]?.[j1];
  const v01 = values[i1]?.[j0];
  const v11 = values[i1]?.[j1];
  if ([v00, v10, v01, v11].some((v) => shouldSkipValue(data, v))) return null;
  const fx = x - j0;
  const fy = y - i0;
  const top = v00 + (v10 - v00) * fx;
  const bottom = v01 + (v11 - v01) * fx;
  return top + (bottom - top) * fy;
}

function marchingCell(v00, v10, v11, v01, level, x, y, w, h) {
  if ([v00, v10, v11, v01].some((v) => v === null || Number.isNaN(v))) return [];
  const pts = [];
  function interp(a, b, ax, ay, bx, by) {
    const t = (level - a) / Math.max(1e-9, b - a);
    return { x: ax + (bx - ax) * t, y: ay + (by - ay) * t };
  }
  if ((v00 < level) !== (v10 < level)) pts.push(interp(v00, v10, x, y, x + w, y));
  if ((v10 < level) !== (v11 < level)) pts.push(interp(v10, v11, x + w, y, x + w, y + h));
  if ((v11 < level) !== (v01 < level)) pts.push(interp(v11, v01, x + w, y + h, x, y + h));
  if ((v01 < level) !== (v00 < level)) pts.push(interp(v01, v00, x, y + h, x, y));
  return pts.length === 2 ? pts : pts.length === 4 ? [pts[0], pts[1], pts[2], pts[3]] : [];
}

function normalizeValue(value, min, max) {
  return Math.max(0, Math.min(1, (value - min) / Math.max(1e-9, max - min)));
}

function shouldSkipValue(data, value) {
  if (value === null || Number.isNaN(value)) return true;
  return data?.land_mask === "positive" && value > 0;
}

function colorAlpha(t, alpha) {
  const rgb = colorRgb(t);
  return `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`;
}

function color(t) {
  const rgb = colorRgb(t);
  return `rgb(${rgb.join(",")})`;
}

function colorRgb(t) {
  const stops = [[29,78,216],[8,145,178],[34,197,94],[253,224,71],[220,38,38]];
  return interpolateStops(t, stops);
}

function interpolateStops(t, stops) {
  t = Math.max(0, Math.min(1, t));
  const p = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(p));
  const f = p - i;
  const a = stops[i];
  const b = stops[i + 1];
  return a.map((v, k) => Math.round(v + (b[k] - v) * f));
}

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────
function appendMessage(role, text) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = text;
  els.chatMessages.appendChild(div);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
  return div;
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const contentType = res.headers.get("content-type") || "";
  const raw = await res.text();
  let data;
  if (contentType.includes("application/json")) {
    try { data = raw ? JSON.parse(raw) : {}; }
    catch (error) { throw new Error(`接口返回 JSON 解析失败：${error.message}`); }
  } else {
    const preview = raw.replace(/\s+/g, " ").replace(/<[^>]+>/g, " ").trim().slice(0, 220);
    throw new Error(`接口返回非 JSON（HTTP ${res.status}）：${preview || res.statusText}`);
  }
  if (!res.ok) throw new Error(data.message || data.error || res.statusText);
  return data;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function escapeAttr(value) { return escapeHtml(value); }
