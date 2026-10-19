const state = {
  project: null,
  selectedId: null,
  zoom: 5,
  saveTimer: null,
  pollTimer: null,
  filmstripTimer: null,
  previewInMuteSegment: false,
  replClipDurations: {},
  drag: null,
  suppressSeekClick: false,
  pickMode: false,
  pickPoints: [],
  refImageName: null,
  genJobPoll: null,
  genJobRunning: false,
};

const TOPIC_COLORS = {
  "asian people": "#e67e22",
  "black people": "#9b59b6",
  lgbtq: "#2ecc71",
};

const ACTION_PRECEDENCE = { cut: 5, replace: 4, black: 3, blur: 2, mute: 1, none: 0 };
const ACTION_LABELS = {
  cut: "✂ cut",
  blur: "blur",
  black: "■ black",
  mute: "🔇 mute",
  replace: "⇄ replace",
};
const MIN_SEG_LEN = 0.2;
const SEG_LANE_H = 28;
const SEG_TRACK_PAD = 8;

const $ = (id) => document.getElementById(id);

function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    if (res.status === 409) {
      const e = new Error(typeof detail === "string" ? detail : "conflict");
      e.status = 409;
      throw e;
    }
    if (detail && typeof detail === "object" && detail.message) {
      throw new Error(detail.job_id ? `${detail.message} (${detail.job_id})` : detail.message);
    }
    throw new Error(detail || res.statusText);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

function fmtTime(t) {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function pxPerSec() {
  return 20 * (state.zoom / 5);
}

function timelineContentWidth() {
  const dur = state.project?.duration || 0;
  return Math.max(800, dur * pxPerSec());
}

function filmstripCount() {
  const w = timelineContentWidth();
  return Math.max(1, Math.min(400, Math.ceil(w / 120)));
}

function debouncedSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveProject, 400);
}

async function saveProject() {
  if (!state.project) return;
  const payload = {
    revision: state.project.revision,
    name: state.project.name,
    segments: state.project.segments,
  };
  try {
    const updated = await api(`/api/projects/${state.project.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.project.revision = updated.revision;
  } catch (e) {
    if (e.status === 409) {
      const reloaded = await api(`/api/projects/${state.project.id}`);
      state.project = reloaded;
      renderTimeline();
      refreshInspector();
      refreshSegmentList();
      refreshExportSummary();
      $("detectMsg").textContent = "Project changed on server, reloaded.";
      return;
    }
    throw e;
  }
}

async function refreshProjects() {
  const list = await api("/api/projects");
  const ul = $("projectList");
  ul.innerHTML = "";
  list.forEach((p) => {
    const li = document.createElement("li");
    li.className = "project-item" + (state.project?.id === p.id ? " active" : "");
    li.textContent = p.name;
    li.onclick = () => openProject(p.id);
    ul.appendChild(li);
  });
}

async function refreshMedia() {
  const names = await api("/api/media");
  const ul = $("mediaList");
  ul.innerHTML = "";
  names.forEach((name) => {
    const li = document.createElement("li");
    li.className = "project-item";
    li.textContent = name;
    li.onclick = async () => {
      const p = await api("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ media: name }),
      });
      await openProject(p.id);
      refreshProjects();
    };
    ul.appendChild(li);
  });
}

function syncTimelineLayout() {
  const w = timelineContentWidth();
  const inner = $("timelineInner");
  if (inner) inner.style.width = `${w}px`;
  $("timelineCanvas").style.width = `${w}px`;
  $("filmstrip").style.width = `${w}px`;
  $("timeRuler").style.width = `${w}px`;
}

function assignSegmentLanes(segments) {
  const sorted = [...segments].sort((a, b) => a.start - b.start || a.end - b.end);
  const laneEnds = [];
  const laneOf = new Map();
  sorted.forEach((seg) => {
    let lane = 0;
    for (; lane < laneEnds.length; lane++) {
      if (laneEnds[lane] <= seg.start + 1e-6) break;
    }
    if (lane === laneEnds.length) laneEnds.push(0);
    laneEnds[lane] = seg.end;
    laneOf.set(seg.id, lane);
  });
  return { laneOf, laneCount: Math.max(1, laneEnds.length) };
}

function segmentBlockLabel(seg) {
  const topic = seg.source === "manual" ? "manual" : (seg.topic || "—").slice(0, 24);
  const action = seg.action && seg.action !== "none" ? ACTION_LABELS[seg.action] || seg.action : "";
  return action ? `${topic} · ${action}` : topic;
}

async function openProject(id) {
  state.project = await api(`/api/projects/${id}`);
  state.selectedId = null;
  state.previewInMuteSegment = false;
  $("projectTitle").textContent = state.project.name;
  $("mainVideo").src = `/api/projects/${id}/video`;
  $("detectBtn").disabled = false;
  $("exportBtn").disabled = false;
  $("addSegBtn").disabled = false;
  syncTimelineLayout();
  await loadFilmstrip();
  renderTimeRuler();
  renderTimeline();
  refreshInspector();
  refreshSegmentList();
  refreshProjects();
  refreshReplacementSelect();
  refreshExportSummary();
}

function debouncedLoadFilmstrip() {
  clearTimeout(state.filmstripTimer);
  state.filmstripTimer = setTimeout(() => {
    syncTimelineLayout();
    loadFilmstrip();
    renderTimeRuler();
  }, 200);
}

async function loadFilmstrip() {
  if (!state.project) return;
  const count = filmstripCount();
  const strip = await api(`/api/projects/${state.project.id}/filmstrip?count=${count}`);
  const el = $("filmstrip");
  el.innerHTML = "";
  const pps = pxPerSec();
  strip.forEach((item) => {
    const img = document.createElement("img");
    img.src = item.url;
    img.title = fmtTime(item.t);
    const t0 = item.t_start ?? item.t;
    const t1 = item.t_end ?? item.t;
    img.style.left = `${t0 * pps}px`;
    img.style.width = `${Math.max(1, (t1 - t0) * pps)}px`;
    el.appendChild(img);
  });
}

function renderTimeRuler() {
  const ruler = $("timeRuler");
  ruler.innerHTML = "";
  if (!state.project) return;
  const pps = pxPerSec();
  const dur = state.project.duration;
  let step = 1;
  if (pps < 8) step = 60;
  else if (pps < 20) step = 10;
  else if (pps < 50) step = 5;
  else step = 1;
  for (let t = 0; t <= dur; t += step) {
    const tick = document.createElement("div");
    tick.className = "tick";
    tick.style.left = `${t * pps}px`;
    tick.textContent = fmtTime(t);
    ruler.appendChild(tick);
  }
}

function segmentsAtTime(t) {
  return (state.project?.segments || []).filter((s) => t >= s.start && t < s.end && s.action !== "none");
}

function dominantVisualSegment(t) {
  const active = segmentsAtTime(t);
  if (!active.length) return null;
  return active.sort((a, b) => ACTION_PRECEDENCE[b.action] - ACTION_PRECEDENCE[a.action])[0];
}

function renderTimeline() {
  const canvas = $("timelineCanvas");
  canvas.querySelectorAll(".seg-block").forEach((n) => n.remove());
  const pps = pxPerSec();
  const segs = state.project?.segments || [];
  const { laneOf, laneCount } = assignSegmentLanes(segs);
  canvas.style.height = `${laneCount * SEG_LANE_H + SEG_TRACK_PAD * 2}px`;

  segs.forEach((seg) => {
    const div = document.createElement("div");
    div.className = "seg-block";
    if (seg.source === "manual") div.classList.add("manual");
    if (seg.status === "suggested") div.classList.add("suggested");
    if (seg.id === state.selectedId) div.classList.add("selected");
    if (seg.action === "none") {
      div.classList.add("action-none");
    } else {
      if (seg.action === "cut") div.classList.add("action-cut");
      const color = seg.source === "manual" ? null : TOPIC_COLORS[seg.topic] || "#3d7ea6";
      if (color) div.style.background = color;
    }
    const lane = laneOf.get(seg.id) || 0;
    div.style.left = `${seg.start * pps}px`;
    div.style.width = `${Math.max(4, (seg.end - seg.start) * pps)}px`;
    div.style.top = `${SEG_TRACK_PAD + lane * SEG_LANE_H}px`;
    div.dataset.id = seg.id;

    const label = document.createElement("span");
    label.className = "seg-label";
    label.textContent = segmentBlockLabel(seg);
    div.appendChild(label);

    const leftH = document.createElement("div");
    leftH.className = "resize-handle left";
    const rightH = document.createElement("div");
    rightH.className = "resize-handle right";
    div.appendChild(leftH);
    div.appendChild(rightH);

    div.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      const mode = e.target.classList.contains("resize-handle")
        ? e.target.classList.contains("left")
          ? "resize-left"
          : "resize-right"
        : "move";
      startSegmentDrag(e, seg.id, mode);
    });

    canvas.appendChild(div);
  });
  refreshSegmentList();
}

function startSegmentDrag(e, segId, mode) {
  e.stopPropagation();
  e.preventDefault();
  const seg = state.project.segments.find((s) => s.id === segId);
  if (!seg) return;
  state.selectedId = segId;
  state.drag = {
    mode,
    segId,
    startX: e.clientX,
    origStart: seg.start,
    origEnd: seg.end,
    moved: false,
  };
  // Listen on window: renderTimeline() replaces the block element during the
  // drag, which would drop element-level pointer capture and listeners.
  const onMove = (ev) => {
    if (!state.drag || state.drag.segId !== segId) return;
    const pps = pxPerSec();
    const dx = (ev.clientX - state.drag.startX) / pps;
    if (Math.abs(ev.clientX - state.drag.startX) > 3) state.drag.moved = true;
    const dur = state.project.duration;
    let ns = state.drag.origStart;
    let ne = state.drag.origEnd;
    if (state.drag.mode === "move") {
      const len = ne - ns;
      ns = Math.max(0, Math.min(dur - len, state.drag.origStart + dx));
      ne = ns + len;
    } else if (state.drag.mode === "resize-left") {
      ns = Math.max(0, Math.min(ne - MIN_SEG_LEN, state.drag.origStart + dx));
    } else {
      ne = Math.min(dur, Math.max(ns + MIN_SEG_LEN, state.drag.origEnd + dx));
    }
    seg.start = ns;
    seg.end = ne;
    renderTimeline();
    refreshInspector();
  };
  const onUp = (ev) => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    const wasDrag = state.drag?.moved;
    if (wasDrag) {
      state.suppressSeekClick = true;
      setTimeout(() => {
        state.suppressSeekClick = false;
      }, 0);
      debouncedSave();
    } else {
      state.selectedId = segId;
      renderTimeline();
      refreshInspector();
      refreshSegmentList();
    }
    state.drag = null;
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

function seekFromClientX(clientX, scrollEl) {
  const canvas = $("timelineCanvas");
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left + scrollEl.scrollLeft;
  $("mainVideo").currentTime = Math.max(0, Math.min(state.project.duration, x / pxPerSec()));
}

function bindScrubArea(el) {
  let scrubbing = false;
  el.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    if (e.target.closest?.(".seg-block")) return;
    scrubbing = true;
    state.drag = { mode: "scrub", moved: false, startX: e.clientX };
    el.setPointerCapture(e.pointerId);
    seekFromClientX(e.clientX, $("timelineScroll"));
  });
  el.addEventListener("pointermove", (e) => {
    if (!scrubbing) return;
    if (Math.abs(e.clientX - state.drag.startX) > 3) state.drag.moved = true;
    seekFromClientX(e.clientX, $("timelineScroll"));
  });
  el.addEventListener("pointerup", (e) => {
    if (!scrubbing) return;
    scrubbing = false;
    if (state.drag?.moved) {
      state.suppressSeekClick = true;
      setTimeout(() => {
        state.suppressSeekClick = false;
      }, 0);
    }
    e.currentTarget.releasePointerCapture(e.pointerId);
    state.drag = null;
  });
}

function refreshSegmentList() {
  const ul = $("segmentList");
  if (!ul || !state.project) return;
  ul.innerHTML = "";
  const sorted = [...state.project.segments].sort((a, b) => a.start - b.start);
  sorted.forEach((seg) => {
    const li = document.createElement("li");
    if (seg.id === state.selectedId) li.classList.add("selected");
    li.textContent = `${fmtTime(seg.start)}–${fmtTime(seg.end)} | ${seg.topic || "—"} | ${(seg.score || 0).toFixed(2)} | ${seg.action} | ${seg.status}`;
    li.onclick = () => {
      state.selectedId = seg.id;
      $("mainVideo").currentTime = seg.start;
      renderTimeline();
      refreshInspector();
      refreshSegmentList();
    };
    ul.appendChild(li);
  });
}

function refreshInspector() {
  const seg = state.project?.segments.find((s) => s.id === state.selectedId);
  if (!seg) {
    $("inspector").classList.add("hidden");
    $("noSelection").classList.remove("hidden");
    return;
  }
  $("inspector").classList.remove("hidden");
  $("noSelection").classList.add("hidden");
  $("segTopic").value = seg.topic || "";
  $("segStart").value = seg.start;
  $("segEnd").value = seg.end;
  $("segScore").value = seg.score || 0;
  $("segAction").value = seg.action || "none";
  $("segReplMode").value = seg.replacement_mode || "trim";
  $("segReplacement").value = seg.replacement_clip || "";
  const replaceOn = seg.action === "replace";
  $("replaceControls").classList.toggle("hidden", !replaceOn);
  if (!replaceOn) $("genReplMsg").textContent = "";
  drawPickOverlay();
}

function videoContentRect() {
  const v = $("mainVideo");
  const wrap = $("videoWrap");
  if (!v.videoWidth || !wrap) return null;
  const wr = wrap.getBoundingClientRect();
  const vw = v.videoWidth;
  const vh = v.videoHeight;
  const scale = Math.min(wr.width / vw, wr.height / vh);
  const dw = vw * scale;
  const dh = vh * scale;
  const left = wr.left + (wr.width - dw) / 2;
  const top = wr.top + (wr.height - dh) / 2;
  return { left, top, width: dw, height: dh, vw, vh };
}

function clientToNormalized(clientX, clientY) {
  const r = videoContentRect();
  if (!r) return null;
  const x = (clientX - r.left) / r.width;
  const y = (clientY - r.top) / r.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  return [x, y];
}

function resizePickCanvas() {
  const canvas = $("pickOverlay");
  const wrap = $("videoWrap");
  if (!canvas || !wrap) return;
  const r = wrap.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(r.width));
  canvas.height = Math.max(1, Math.floor(r.height));
  drawPickOverlay();
}

function drawPickOverlay() {
  const canvas = $("pickOverlay");
  if (!canvas || canvas.classList.contains("hidden")) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const wrap = $("videoWrap");
  const wr = wrap.getBoundingClientRect();
  const r = videoContentRect();
  if (!r) return;
  const ox = r.left - wr.left;
  const oy = r.top - wr.top;
  state.pickPoints.forEach((pt) => {
    const cx = ox + pt[0] * r.width;
    const cy = oy + pt[1] * r.height;
    ctx.beginPath();
    ctx.arc(cx, cy, 7, 0, Math.PI * 2);
    ctx.fillStyle = pt[2] === 1 ? "rgba(46, 204, 113, 0.85)" : "rgba(231, 76, 60, 0.85)";
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.stroke();
  });
}

function setPickMode(on) {
  state.pickMode = on;
  const canvas = $("pickOverlay");
  if (!canvas) return;
  canvas.classList.toggle("hidden", !on);
  $("pickHint").classList.toggle("hidden", !on);
  $("pickPersonBtn").textContent = on ? "Done picking" : "Pick person";
  if (on) {
    resizePickCanvas();
  } else {
    drawPickOverlay();
  }
}

function bindPickOverlay() {
  const canvas = $("pickOverlay");
  const v = $("mainVideo");
  if (!canvas || !v) return;
  window.addEventListener("resize", () => {
    if (state.pickMode) resizePickCanvas();
  });
  canvas.addEventListener("click", (e) => {
    if (!state.pickMode || !state.selectedId) return;
    e.preventDefault();
    const neg = e.shiftKey || e.altKey || e.button === 2;
    const norm = clientToNormalized(e.clientX, e.clientY);
    if (!norm) return;
    state.pickPoints.push([norm[0], norm[1], neg ? 0 : 1]);
    $("clearPointsBtn").disabled = state.pickPoints.length === 0;
    drawPickOverlay();
  });
  canvas.addEventListener("contextmenu", (e) => {
    if (!state.pickMode) return;
    e.preventDefault();
  });
}

function refreshReplacementSelect() {
  const sel = $("segReplacement");
  sel.innerHTML = "";
  (state.project?.replacement_clips || []).forEach((name) => {
    const o = document.createElement("option");
    o.value = name;
    o.textContent = name;
    sel.appendChild(o);
  });
}

function bindInspector() {
  const applyField = () => {
    const seg = state.project.segments.find((s) => s.id === state.selectedId);
    if (!seg) return;
    seg.topic = $("segTopic").value;
    seg.start = parseFloat($("segStart").value);
    seg.end = parseFloat($("segEnd").value);
    seg.score = parseFloat($("segScore").value);
    seg.action = $("segAction").value;
    seg.replacement_mode = $("segReplMode").value;
    seg.replacement_clip = $("segReplacement").value || null;
    renderTimeline();
    refreshInspector();
    refreshExportSummary();
    debouncedSave();
  };
  ["segTopic", "segStart", "segEnd", "segScore", "segAction", "segReplMode", "segReplacement"].forEach(
    (id) => $(id).addEventListener("change", applyField)
  );

  $("acceptSegBtn").onclick = () => {
    const seg = state.project.segments.find((s) => s.id === state.selectedId);
    if (!seg) return;
    seg.status = "accepted";
    if (!seg.action || seg.action === "none") {
      seg.action = "blur";
      $("segAction").value = "blur";
    }
    renderTimeline();
    refreshInspector();
    refreshExportSummary();
    debouncedSave();
  };
  $("deleteSegBtn").onclick = async () => {
    if (!state.selectedId) return;
    await api(`/api/projects/${state.project.id}/segments/${state.selectedId}`, { method: "DELETE" });
    state.project = await api(`/api/projects/${state.project.id}`);
    state.selectedId = null;
    renderTimeline();
    refreshInspector();
    refreshSegmentList();
  };

  $("acceptAllBtn").onclick = () => {
    state.project.segments.forEach((seg) => {
      if (seg.status === "suggested") {
        seg.status = "accepted";
        if (!seg.action || seg.action === "none") seg.action = "blur";
      }
    });
    renderTimeline();
    refreshInspector();
    refreshExportSummary();
    debouncedSave();
  };

  $("clearSuggBtn").onclick = () => {
    state.project.segments = state.project.segments.filter((s) => s.status !== "suggested");
    if (state.selectedId && !state.project.segments.find((s) => s.id === state.selectedId)) {
      state.selectedId = null;
    }
    renderTimeline();
    refreshInspector();
    refreshExportSummary();
    debouncedSave();
  };

  $("pickPersonBtn").onclick = () => {
    const seg = state.project?.segments.find((s) => s.id === state.selectedId);
    if (!seg) return;
    const v = $("mainVideo");
    if (!state.pickMode) {
      v.currentTime = seg.start;
      v.pause();
      setPickMode(true);
    } else {
      setPickMode(false);
    }
  };
  $("clearPointsBtn").onclick = () => {
    state.pickPoints = [];
    $("clearPointsBtn").disabled = true;
    drawPickOverlay();
  };
  $("refImageUpload").onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/projects/${state.project.id}/ref-images`, { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      $("refImageName").textContent = data.detail || `HTTP ${res.status}`;
      return;
    }
    state.refImageName = data.name;
    $("refImageName").textContent = data.name;
  };
  $("genReplBtn").onclick = async () => {
    if (!state.selectedId || state.genJobRunning) return;
    $("genReplMsg").textContent = "";
    const prompt = ($("aiPrompt").value || "").trim();
    if (!state.refImageName && !prompt) {
      $("genReplMsg").textContent = "Upload a reference image or enter a prompt.";
      return;
    }
    if (!state.pickPoints.some((p) => p[2] === 1)) {
      $("genReplMsg").textContent = "Add at least one positive point (Pick person).";
      return;
    }
    const seedRaw = $("aiSeed").value;
    const body = {
      points: state.pickPoints,
      prompt: prompt || undefined,
      ref_image: state.refImageName || undefined,
      resolution: "480p",
    };
    if (seedRaw !== "") body.seed = parseInt(seedRaw, 10);
    state.genJobRunning = true;
    $("genReplBtn").disabled = true;
    try {
      const res = await fetch(
        `/api/projects/${state.project.id}/segments/${state.selectedId}/generate-replacement`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        $("genReplMsg").textContent = typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`;
        state.genJobRunning = false;
        $("genReplBtn").disabled = false;
        return;
      }
      const jobId = data.job_id;
      const poll = async () => {
        const job = await api(`/api/jobs/${jobId}`);
        const elapsed = job.elapsed != null ? ` (${Math.round(job.elapsed)}s)` : "";
        const stage = job.stage ? `${job.stage}: ` : "";
        $("genReplMsg").textContent = `${stage}${job.message || job.status}${elapsed}`;
        if (job.status === "done") {
          clearInterval(state.genJobPoll);
          state.genJobPoll = null;
          state.genJobRunning = false;
          $("genReplBtn").disabled = false;
          state.project = await api(`/api/projects/${state.project.id}`);
          refreshReplacementSelect();
          refreshInspector();
          renderTimeline();
          refreshExportSummary();
          return;
        }
        if (job.status === "error") {
          clearInterval(state.genJobPoll);
          state.genJobPoll = null;
          state.genJobRunning = false;
          $("genReplBtn").disabled = false;
          $("genReplMsg").textContent = job.error || job.message || "Generation failed";
        }
      };
      state.genJobPoll = setInterval(poll, 2000);
      poll();
    } catch (e) {
      $("genReplMsg").textContent = String(e);
      state.genJobRunning = false;
      $("genReplBtn").disabled = false;
    }
  };
  $("replUpload").onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/projects/${state.project.id}/replacements`, { method: "POST", body: fd });
    const data = await res.json();
    state.project = await api(`/api/projects/${state.project.id}`);
    if (data.meta?.duration) state.replClipDurations[data.name] = data.meta.duration;
    refreshReplacementSelect();
    $("segReplacement").value = data.name;
    debouncedSave();
  };
}

function updatePlayhead() {
  const v = $("mainVideo");
  const t = v.currentTime || 0;
  const left = `${t * pxPerSec()}px`;
  $("playhead").style.left = left;
  const handle = $("playheadHandle");
  if (handle) handle.style.left = left;
  $("timeDisplay").textContent = `${fmtTime(t)} / ${fmtTime(state.project?.duration || 0)}`;
  applyLivePreview(t);
}

async function ensureReplDuration(name) {
  if (state.replClipDurations[name]) return state.replClipDurations[name];
  const entry = state.project?.replacements?.find((r) => r.name === name);
  if (entry?.duration) {
    state.replClipDurations[name] = entry.duration;
    return entry.duration;
  }
  return 5;
}

async function applyLivePreview(t) {
  const v = $("mainVideo");
  const rv = $("replVideo");
  const segs = state.project?.segments || [];
  const inMute = segs.some((s) => t >= s.start && t < s.end && s.action === "mute");
  if (inMute && !state.previewInMuteSegment) {
    v.muted = true;
    state.previewInMuteSegment = true;
  } else if (!inMute && state.previewInMuteSegment) {
    v.muted = false;
    state.previewInMuteSegment = false;
  }

  const visual = dominantVisualSegment(t);
  v.style.filter = "";
  $("blackOverlay").style.display = "none";
  $("replaceOverlay").style.display = "none";
  rv.classList.add("hidden");
  rv.pause();

  if (!visual) return;

  if (visual.action === "cut") {
    v.currentTime = visual.end;
  } else if (visual.action === "blur") {
    v.style.filter = "blur(30px)";
  } else if (visual.action === "black") {
    $("blackOverlay").style.display = "block";
  } else if (visual.action === "replace") {
    if (visual.replacement_clip) {
      const url = `/api/projects/${state.project.id}/replacements/${encodeURIComponent(visual.replacement_clip)}`;
      if (rv.getAttribute("data-src") !== url) {
        rv.src = url;
        rv.setAttribute("data-src", url);
      }
      const clipDur = await ensureReplDuration(visual.replacement_clip);
      const offset = t - visual.start;
      let ct = offset;
      if (visual.replacement_mode === "loop" && clipDur > 0) {
        ct = offset % clipDur;
      } else {
        ct = Math.min(offset, clipDur);
      }
      if (Math.abs(rv.currentTime - ct) > 0.15) rv.currentTime = ct;
      rv.classList.remove("hidden");
      if (v.paused) rv.pause();
      else rv.play().catch(() => {});
    } else {
      $("replaceOverlay").style.display = "flex";
    }
  }
}

function bindVideo() {
  const v = $("mainVideo");
  const updatePlayBtn = () => {
    const btn = $("playBtn");
    const paused = v.paused;
    btn.textContent = paused ? "▶" : "⏸";
    btn.setAttribute("aria-label", paused ? "Play" : "Pause");
    btn.title = paused ? "Play" : "Pause";
  };
  $("playBtn").onclick = () => (v.paused ? v.play() : v.pause());
  v.addEventListener("timeupdate", updatePlayhead);
  v.addEventListener("play", () => {
    updatePlayBtn();
    const rv = $("replVideo");
    if (!rv.classList.contains("hidden")) rv.play().catch(() => {});
  });
  v.addEventListener("pause", () => {
    updatePlayBtn();
    $("replVideo").pause();
  });
  v.addEventListener("ended", updatePlayBtn);
  updatePlayBtn();
  requestAnimationFrame(function loop() {
    updatePlayhead();
    requestAnimationFrame(loop);
  });

  $("timelineCanvas").onclick = (e) => {
    if (state.suppressSeekClick) return;
    if (e.target.classList.contains("seg-block") || e.target.closest(".seg-block")) return;
    seekFromClientX(e.clientX, $("timelineScroll"));
  };

  bindScrubArea($("filmstrip"));
  bindScrubArea($("timeRuler"));

  const playheadHandle = $("playheadHandle");
  playheadHandle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const scroll = $("timelineScroll");
    playheadHandle.setPointerCapture(e.pointerId);
    seekFromClientX(e.clientX, scroll);
    const onMove = (ev) => {
      state.drag = { moved: true };
      seekFromClientX(ev.clientX, scroll);
    };
    const onUp = (ev) => {
      playheadHandle.releasePointerCapture(ev.pointerId);
      playheadHandle.removeEventListener("pointermove", onMove);
      playheadHandle.removeEventListener("pointerup", onUp);
      state.suppressSeekClick = true;
      setTimeout(() => {
        state.suppressSeekClick = false;
      }, 0);
      state.drag = null;
    };
    playheadHandle.addEventListener("pointermove", onMove);
    playheadHandle.addEventListener("pointerup", onUp);
  });

  $("zoomSlider").oninput = (e) => {
    state.zoom = parseInt(e.target.value, 10);
    syncTimelineLayout();
    renderTimeline();
    debouncedLoadFilmstrip();
  };
  $("timelineScroll").addEventListener(
    "wheel",
    (e) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const z = $("zoomSlider");
        z.value = Math.min(20, Math.max(1, parseInt(z.value, 10) + (e.deltaY < 0 ? 1 : -1)));
        z.dispatchEvent(new Event("input"));
      }
    },
    { passive: false }
  );
}

$("addSegBtn").onclick = async () => {
  const t = $("mainVideo").currentTime || 0;
  const seg = await api(`/api/projects/${state.project.id}/segments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      start: t,
      end: Math.min(state.project.duration, t + 3),
      source: "manual",
      status: "accepted",
      action: "none",
    }),
  });
  state.project = await api(`/api/projects/${state.project.id}`);
  state.selectedId = seg.id;
  renderTimeline();
  refreshInspector();
};

$("uploadInput").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/projects", { method: "POST", body: fd });
  const p = await res.json();
  await openProject(p.id);
  refreshProjects();
};

$("detectBtn").onclick = async () => {
  const topics = [...document.querySelectorAll(".topic:checked")].map((el) => el.value);
  const body = {
    topics,
    threshold: parseFloat($("detThreshold").value),
    interval: parseFloat($("detInterval").value),
  };
  $("detectMsg").textContent = "";
  try {
    const { job_id } = await api(`/api/projects/${state.project.id}/detect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    pollJob(job_id, "detectProgress", "detectMsg", async () => {
      await openProject(state.project.id);
    });
  } catch (e) {
    $("detectMsg").textContent = String(e.message || e);
  }
};

function refreshExportSummary() {
  const el = $("exportSummary");
  if (!el || !state.project) return;
  const segs = state.project.segments || [];
  const applied = segs.filter((s) => s.action && s.action !== "none");
  const pendingSuggested = segs.filter(
    (s) => s.status === "suggested" && (!s.action || s.action === "none")
  );
  el.textContent = `${applied.length} segment(s) will be applied on export.`;
  const warn = $("exportWarn");
  if (pendingSuggested.length) {
    warn.textContent = `Warning: ${pendingSuggested.length} suggested segment(s) still have action "none".`;
    warn.classList.remove("hidden");
  } else {
    warn.textContent = "";
    warn.classList.add("hidden");
  }
}

$("exportBtn").onclick = async () => {
  const { job_id } = await api(`/api/projects/${state.project.id}/export`, { method: "POST" });
  pollJob(job_id, "exportProgress", null, (job) => {
    if (job.result?.url) {
      const a = $("exportLink");
      a.href = job.result.url;
      a.textContent = "Download export";
      a.classList.remove("hidden");
    }
  });
};

function pollJob(jobId, barId, msgId, onDone) {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    const job = await api(`/api/jobs/${jobId}`);
    $(barId).style.width = `${Math.round((job.progress || 0) * 100)}%`;
    if (msgId && job.message) $(msgId).textContent = job.message;
    if (job.status === "done") {
      clearInterval(state.pollTimer);
      onDone(job);
    }
    if (job.status === "error") {
      clearInterval(state.pollTimer);
      if (msgId) $(msgId).textContent = job.error || "error";
    }
  }, 500);
}

document.addEventListener("keydown", (e) => {
  if (isTypingTarget(e.target)) return;
  if (e.code === "Space") {
    e.preventDefault();
    $("playBtn").click();
  }
  if ((e.code === "Delete" || e.code === "Backspace") && state.selectedId) {
    e.preventDefault();
    $("deleteSegBtn").click();
  }
  if (e.key === "a" || e.key === "A") $("acceptSegBtn").click();
  if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
    e.preventDefault();
    const v = $("mainVideo");
    const delta = e.shiftKey ? 5 : 1;
    v.currentTime = Math.max(
      0,
      Math.min(state.project?.duration || 0, v.currentTime + (e.code === "ArrowRight" ? delta : -delta))
    );
  }
});

bindInspector();
bindPickOverlay();
bindVideo();
refreshProjects();
refreshMedia();
