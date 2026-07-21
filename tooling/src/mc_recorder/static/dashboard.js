const state = {
  csrf: "",
  status: null,
  datasets: [],
  dataset: null,
  samples: [],
  sampleIndex: 0,
  currentSample: null,
  sampleRequest: 0,
  playing: false,
  playbackTimer: null,
  currentCursor: null,
  nextCursor: null,
  pageHistory: [],
  sampleTotal: 0,
  datasetRefreshTimer: null,
  trajectory: null,
  trajectoryRequest: 0,
  trajectoryResizeObserver: null,
};

const trajectoryColors = ["#78e08f", "#74b9ff", "#f1c75b", "#ff8f70", "#c7a6ff", "#61d6d0", "#f58bc8", "#b4d273"];

const $ = (selector) => document.querySelector(selector);

const escapeHtml = (value) => String(value ?? "").replace(
  /[&<>"']/g,
  (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character],
);

function fmtBytes(value) {
  if (!Number.isFinite(value)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

async function api(path, options = {}) {
  const mutationHeaders = options.body
    ? { "Content-Type": "application/json", "X-MC-Recorder-CSRF": state.csrf }
    : {};
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: { ...mutationHeaders, ...(options.headers || {}) },
  });
  const type = response.headers.get("content-type") || "";
  const data = type.includes("json") ? await response.json() : await response.blob();
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(node.timer);
  node.timer = setTimeout(() => node.classList.remove("show"), 3500);
}

function badgeClass(value) {
  if (["running", "recording", "complete", "ok", "online", "disconnected", "stopped"].includes(value)) return "ok";
  if (["starting", "stopping", "waiting_for_seal", "sealing", "generating", "queued", "downloading", "rendering", "uploading", "verifying", "attaching", "busy", "partial", "warning", "stale"].includes(value)) return "warning";
  if (["unhealthy", "failed", "interrupted", "offline", "full", "docker_unavailable"].includes(value)) return "error";
  return "neutral";
}

function activateView(view) {
  document.querySelectorAll(".tab").forEach((node) => node.classList.toggle("active", node.dataset.view === view));
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === view));
}

async function refreshStatus() {
  try {
    const data = await api("/api/v1/status");
    state.status = data;
    state.csrf = data.csrf_token;
    const server = data.server.state;
    $("#server-pill").textContent = server;
    $("#server-pill").className = `pill ${badgeClass(server)}`;
    $("#server-state").textContent = server;
    $("#server-detail").textContent = data.server.message
      || (data.server.services || []).map((service) => `${service.service}: ${service.state}${service.health ? `/${service.health}` : ""}`).join(" · ")
      || "No Compose services found";
    $("#start-server").disabled = ["running", "starting", "stopping"].includes(server) || Boolean(data.active_job);
    $("#stop-server").disabled = !["running", "unhealthy"].includes(server) || Boolean(data.active_job);

    const capture = data.capture;
    $("#capture-state").textContent = capture.state || "offline";
    $("#capture-metrics").innerHTML = [
      ["Session", capture.session_id],
      ["Tick", capture.server_tick],
      ["Epoch", capture.epoch_index],
      ["Players", capture.connected_player_count ?? capture.active_connection_count],
      ["Queue", capture.writer_queue_depth],
      ["Heartbeat", capture.heartbeat_age_seconds == null ? "—" : `${capture.heartbeat_age_seconds}s`],
    ].map(([key, value]) => `<dt>${key}</dt><dd>${escapeHtml(value ?? "—")}</dd>`).join("");

    const storage = data.storage;
    const percent = storage.quota_bytes > 0
      ? Math.min(100, storage.used_bytes / storage.quota_bytes * 100)
      : 0;
    $("#storage-state").textContent = storage.state;
    $("#storage-meter").style.width = `${percent}%`;
    $("#storage-meter").style.background = storage.state === "ok" ? "var(--green)" : storage.state === "warning" ? "var(--yellow)" : "var(--red)";
    $("#storage-detail").textContent = `${fmtBytes(storage.used_bytes)} of ${fmtBytes(storage.quota_bytes)} (${percent.toFixed(1)}%)`;
    renderJobs(data.recent_jobs || []);
  } catch (error) {
    toast(error.message);
  }
}

async function mutate(path, confirmText) {
  if (confirmText && !confirm(confirmText)) return;
  try {
    const job = await api(path, { method: "POST", body: "{}" });
    toast(`${job.kind} queued`);
    await refreshStatus();
  } catch (error) {
    toast(error.message);
  }
}

function renderJobs(jobs) {
  $("#jobs").innerHTML = jobs.length
    ? jobs.map((job) => `<div class="job"><div><strong>${escapeHtml(job.kind.replaceAll("_", " "))}</strong><small>${escapeHtml(job.error || job.result?.output || job.created_at)}</small></div><span class="status ${badgeClass(job.state)}">${escapeHtml(job.state)}</span></div>`).join("")
    : '<p class="empty">No operations yet.</p>';
}

async function refreshRecordings() {
  try {
    const { recordings } = await api("/api/v1/recordings");
    const groups = recordings.reduce((map, recording) => {
      map.set(recording.player_uuid, [...(map.get(recording.player_uuid) || []), recording]);
      return map;
    }, new Map());
    $("#recordings").innerHTML = recordings.length
      ? [...groups.entries()].map(([player, rows]) => `<div class="player-group">
          <div class="player-heading"><strong>${escapeHtml(rows[0].player_name || "Unknown player")}</strong><code>${escapeHtml(player)}</code></div>
          ${rows.map((recording) => {
            const generationAction = recording.state === "complete"
              ? `<button class="primary" data-view-dataset="${recording.dataset_id}">View dataset</button>`
              : `<button class="${recording.can_generate ? "primary" : "quiet"}" data-generate="${recording.id}" ${recording.can_generate ? "" : "disabled"}>${recording.state === "failed" ? (recording.can_generate ? "Retry generate" : "Resolve conflict") : "Seal & generate"}</button>`;
            const renderJob = recording.render_job;
            let renderAction = "";
            if (renderJob && ["queued", "downloading", "rendering", "uploading"].includes(renderJob.state)) {
              renderAction = `<span class="status ${badgeClass(renderJob.state)}">RGB ${escapeHtml(renderJob.state)}</span><button class="quiet" data-render-cancel="${renderJob.id}">Cancel</button>`;
            } else if (renderJob && ["verifying", "attaching"].includes(renderJob.state)) {
              renderAction = `<span class="status ${badgeClass(renderJob.state)}">RGB ${escapeHtml(renderJob.state)} · finalizing on server</span>`;
            } else if (recording.can_replace_legacy_rgb) {
              renderAction = `<span class="status warning">Warning: legacy RGB HUD is unsynchronized</span><select class="render-resolution" data-render-resolution="${recording.id}" aria-label="RGB re-render resolution"><option value="640x360">640×360</option><option value="1280x720">1280×720</option></select><button class="quiet" data-render-recording="${recording.id}" data-replace-legacy-rgb="true">Re-render RGB</button>`;
            } else if (renderJob && ["failed", "partial", "canceled"].includes(renderJob.state)) {
              renderAction = `<button class="quiet" data-render-retry="${renderJob.id}">Retry RGB</button>`;
            } else if (recording.rgb_complete) {
              const presentation = {
                full_client: "full client + hand",
                hud_free: "HUD-free (legacy)",
                legacy_gui_unsynchronized: "legacy GUI (unsynchronized)",
                mixed_legacy_gui_unsynchronized: "mixed legacy GUI (unsynchronized)",
                mixed: "mixed presentation",
              }[recording.rgb_presentation];
              renderAction = `<span class="status ok">RGB complete${presentation ? ` · ${presentation}` : ""}</span>`;
            } else if (recording.can_render) {
              renderAction = `<select class="render-resolution" data-render-resolution="${recording.id}" aria-label="RGB render resolution"><option value="640x360">640×360</option><option value="1280x720">1280×720</option></select><button class="quiet" data-render-recording="${recording.id}">Render RGB</button>`;
            }
            const coverage = recording.sample_count == null
              ? ""
              : `<small>RGB ${escapeHtml(recording.rgb_samples ?? 0)} / ${escapeHtml(recording.sample_count)} samples</small>`;
            return `<div class="recording-row">
              <div><strong>Connection</strong><code>${escapeHtml(recording.connection_id)}</code></div>
              <div><small>Ticks</small>${escapeHtml(recording.start_tick ?? "—")} → ${escapeHtml(recording.end_tick ?? "live")}${coverage}</div>
              <span class="status ${badgeClass(recording.state)}">${escapeHtml(recording.state)}</span>
              <div class="recording-actions">${generationAction}${renderAction}</div>
              ${recording.error ? `<p class="recording-error">${escapeHtml(recording.error)}</p>` : ""}
            </div>`;
          }).join("")}
        </div>`).join("")
      : '<p class="empty">No connection ledger yet. Start the server and join once to create a recording.</p>';
    document.querySelectorAll("[data-generate]").forEach((button) => {
      button.addEventListener("click", () => mutate(`/api/v1/recordings/${button.dataset.generate}/generate`));
    });
    document.querySelectorAll("[data-view-dataset]").forEach((button) => {
      button.addEventListener("click", () => openDataset(button.dataset.viewDataset));
    });
    document.querySelectorAll("[data-render-recording]").forEach((button) => {
      button.addEventListener("click", () => queueRender(
        button.dataset.renderRecording,
        button.dataset.replaceLegacyRgb === "true",
      ));
    });
    document.querySelectorAll("[data-render-cancel]").forEach((button) => {
      button.addEventListener("click", () => renderJobAction(button.dataset.renderCancel, "cancel"));
    });
    document.querySelectorAll("[data-render-retry]").forEach((button) => {
      button.addEventListener("click", () => renderJobAction(button.dataset.renderRetry, "retry"));
    });
  } catch (error) {
    toast(error.message);
  }
}

async function queueRender(recordingId, replaceLegacyRgb = false) {
  const resolution = document.querySelector(`[data-render-resolution="${recordingId}"]`)?.value || "640x360";
  const [width, height] = resolution.split("x").map(Number);
  try {
    const job = await api(`/api/v1/recordings/${recordingId}/render`, {
      method: "POST",
      body: JSON.stringify({
        width,
        height,
        fps: 20,
        ...(replaceLegacyRgb ? { replace_legacy_rgb: true } : {}),
      }),
    });
    toast(`RGB render ${job.state}; the foreground GUI worker will claim it when online.`);
    await Promise.all([refreshRecordings(), refreshRenders()]);
  } catch (error) {
    toast(error.message);
  }
}

async function renderJobAction(jobId, action) {
  if (action === "cancel" && !confirm("Cancel this RGB render job?")) return;
  try {
    const job = await api(`/api/v1/render-jobs/${jobId}/${action}`, {
      method: "POST",
      body: "{}",
    });
    toast(`RGB render ${job.state}`);
    await Promise.all([refreshRecordings(), refreshRenders()]);
  } catch (error) {
    toast(error.message);
  }
}

function renderProgress(job) {
  const progress = job.progress || {};
  if (Number.isInteger(progress.current) && Number.isInteger(progress.total)) {
    return `${progress.current} / ${progress.total}${progress.message ? ` · ${progress.message}` : ""}`;
  }
  return progress.message || job.error || job.updated_at;
}

async function refreshRenders() {
  try {
    const [jobData, workerData] = await Promise.all([
      api("/api/v1/render-jobs"),
      api("/api/v1/render-workers"),
    ]);
    const workers = workerData.workers || [];
    $("#render-workers").innerHTML = workers.length
      ? workers.map((worker) => `<div class="worker"><span class="status ${badgeClass(worker.state)}">${escapeHtml(worker.state)}</span><strong>${escapeHtml(worker.name)}</strong><small>${worker.state === "offline" ? `last seen ${escapeHtml(worker.heartbeat_at)}` : escapeHtml(worker.current_job_id || "ready")}</small></div>`).join("")
      : '<p class="empty">No GUI renderer online. Start mc-recorder render-worker in a logged-in graphical session; queued jobs remain safe.</p>';
    const jobs = jobData.jobs || [];
    $("#render-jobs").innerHTML = jobs.length
      ? jobs.map((job) => `<div class="job render-job"><div><strong>${escapeHtml(job.payload?.session_id || job.recording_id)}</strong><small>${escapeHtml(job.payload?.render?.width)}×${escapeHtml(job.payload?.render?.height)} @ ${escapeHtml(job.payload?.render?.fps)} fps · ${escapeHtml(renderProgress(job) || "waiting for worker")}</small></div><span class="status ${badgeClass(job.state)}">${escapeHtml(job.state)}</span></div>`).join("")
      : '<p class="empty">No RGB jobs queued.</p>';
  } catch (error) {
    toast(error.message);
  }
}

async function openDataset(datasetId) {
  activateView("datasets");
  await refreshDatasets();
  if (state.datasets.some((dataset) => dataset.id === datasetId)) {
    await selectDataset(datasetId);
  } else {
    toast("The dataset is still being indexed; refresh the catalog shortly.");
  }
}

async function refreshDatasets() {
  try {
    const data = await api("/api/v1/datasets");
    state.datasets = data.datasets || [];
    if (state.datasetRefreshTimer) clearTimeout(state.datasetRefreshTimer);
    $("#dataset-list").innerHTML = state.datasets.length
      ? state.datasets.map((dataset) => `<button class="dataset-item" data-dataset="${dataset.id}"><strong>${escapeHtml(dataset.session_id)}</strong><small>${escapeHtml(dataset.status || "indexed")} · ${escapeHtml(dataset.sample_count ?? "—")} samples · ${fmtBytes(dataset.size_bytes)}</small></button>`).join("")
      : `<p class="empty">${data.indexing ? "Indexing verified exports…" : "No verified *.dataset exports found."}</p>`;
    document.querySelectorAll("[data-dataset]").forEach((button) => {
      button.addEventListener("click", () => selectDataset(button.dataset.dataset));
    });
    if (data.indexing) state.datasetRefreshTimer = setTimeout(refreshDatasets, 1000);
    if ((data.rejected || []).length) {
      $("#dataset-issues").textContent = `${data.rejected.length} export(s) rejected by integrity checks`;
    } else {
      $("#dataset-issues").textContent = "";
    }
  } catch (error) {
    toast(error.message);
  }
}

function populateDatasetFilters(metadata) {
  const connections = metadata.connections || [];
  const players = new Map();
  connections.forEach((connection) => {
    if (!players.has(connection.player_uuid)) players.set(connection.player_uuid, connection.player_name);
  });
  $("#filter-player").innerHTML = '<option value="">All players</option>'
    + [...players.entries()].map(([uuid, name]) => `<option value="${escapeHtml(uuid)}">${escapeHtml(name || uuid)}</option>`).join("");
  $("#filter-connection").innerHTML = '<option value="">All connections</option>'
    + connections.map((connection) => `<option value="${escapeHtml(connection.connection_id)}">${escapeHtml(connection.player_name || connection.player_uuid)} · ${escapeHtml(connection.connection_id)}</option>`).join("");
}

async function selectDataset(id) {
  stopPlayback();
  try {
    state.dataset = await api(`/api/v1/datasets/${id}`);
    state.currentSample = null;
    state.trajectory = null;
    state.sampleRequest += 1;
    document.querySelectorAll("[data-dataset]").forEach((node) => node.classList.toggle("active", node.dataset.dataset === id));
    populateDatasetFilters(state.dataset);
    state.currentCursor = null;
    state.nextCursor = null;
    state.pageHistory = [];
    $("#dataset-overview").hidden = true;
    $("#sample-viewer").hidden = false;
    renderInputHud(null);
    drawTrajectory();
    await Promise.all([loadTrajectory(), loadSamplePage()]);
  } catch (error) {
    toast(error.message);
  }
}

function activeSampleFilters() {
  return {
    player_uuid: $("#filter-player").value,
    connection_id: $("#filter-connection").value,
    validity: $("#filter-validity").value,
    modality: $("#filter-modality").value,
    from_tick: $("#filter-from-tick").value,
    to_tick: $("#filter-to-tick").value,
  };
}

function appendActiveFilters(query) {
  Object.entries(activeSampleFilters()).forEach(([key, value]) => { if (value) query.set(key, value); });
  return query;
}

function sampleQuery() {
  const query = appendActiveFilters(new URLSearchParams({ limit: "100" }));
  if (state.currentCursor) query.set("cursor", state.currentCursor);
  return query;
}

function trajectoryQuery() {
  return appendActiveFilters(new URLSearchParams({ max_points: "2400" }));
}

async function loadTrajectory() {
  if (!state.dataset) return;
  const request = ++state.trajectoryRequest;
  $("#trajectory-empty").hidden = false;
  $("#trajectory-empty").textContent = "Loading indexed positions…";
  try {
    const trajectory = await api(`/api/v1/datasets/${state.dataset.id}/trajectory?${trajectoryQuery()}`);
    if (request !== state.trajectoryRequest) return;
    state.trajectory = trajectory;
    drawTrajectory();
  } catch (error) {
    if (request !== state.trajectoryRequest) return;
    state.trajectory = null;
    drawTrajectory();
    toast(error.message);
  }
}

async function loadSamplePage() {
  if (!state.dataset) return;
  try {
    const page = await api(`/api/v1/datasets/${state.dataset.id}/samples?${sampleQuery()}`);
    state.samples = page.samples || [];
    state.sampleIndex = 0;
    state.sampleTotal = page.total || 0;
    state.nextCursor = page.next_cursor;
    $("#sample-slider").max = Math.max(0, state.samples.length - 1);
    $("#sample-slider").value = 0;
    $("#page-prev").disabled = state.pageHistory.length === 0;
    $("#page-next").disabled = !state.nextCursor;
    $("#page-position").textContent = state.samples.length
      ? `${state.pageHistory.length * 100 + 1}–${state.pageHistory.length * 100 + state.samples.length} of ${state.sampleTotal}`
      : `0 of ${state.sampleTotal}`;
    if (state.samples.length) {
      await showSample(0);
    } else {
      state.sampleRequest += 1;
      state.currentSample = null;
      $("#sample-position").textContent = "0 / 0";
      $("#sample-summary").innerHTML = '<p class="empty">No samples match these filters.</p>';
      $("#sample-frame").removeAttribute("src");
      $("#sample-frame").hidden = true;
      $("#frame-missing").hidden = false;
      $("#state-diff").textContent = JSON.stringify({ unavailable: "no matching sample" }, null, 2);
      $("#sample-actions").textContent = JSON.stringify({ unavailable: "no matching sample" }, null, 2);
      $("#sample-provenance").textContent = JSON.stringify({ unavailable: "no matching sample" }, null, 2);
      $("#load-voxel").disabled = true;
      $("#voxel-note").textContent = "";
      renderInputHud(null);
      drawTrajectory();
    }
  } catch (error) {
    toast(error.message);
  }
}

async function nextSamplePage() {
  if (!state.nextCursor) return;
  stopPlayback();
  state.pageHistory.push(state.currentCursor);
  state.currentCursor = state.nextCursor;
  await loadSamplePage();
}

async function previousSamplePage() {
  if (!state.pageHistory.length) return;
  stopPlayback();
  state.currentCursor = state.pageHistory.pop() || null;
  await loadSamplePage();
}

function stateDifference(before, after, prefix = "", result = {}) {
  const beforeObject = before && typeof before === "object" && !Array.isArray(before);
  const afterObject = after && typeof after === "object" && !Array.isArray(after);
  if (beforeObject && afterObject) {
    new Set([...Object.keys(before), ...Object.keys(after)]).forEach((key) => {
      stateDifference(before[key], after[key], prefix ? `${prefix}.${key}` : key, result);
    });
  } else if (JSON.stringify(before) !== JSON.stringify(after)) {
    result[prefix || "value"] = { from: before, to: after };
  }
  return result;
}

function finiteCoordinate(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function shortDimension(value) {
  return String(value || "unknown").replace(/^minecraft:/, "");
}

function drawTrajectory() {
  const canvas = $("#trajectory-canvas");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  const width = Math.max(320, Math.floor(canvas.getBoundingClientRect().width || 640));
  const height = 352;
  const scaleFactor = Math.max(1, window.devicePixelRatio || 1);
  canvas.width = Math.floor(width * scaleFactor);
  canvas.height = Math.floor(height * scaleFactor);
  context.setTransform(scaleFactor, 0, 0, scaleFactor, 0, 0);
  context.clearRect(0, 0, width, height);

  const trajectory = state.trajectory;
  const tracks = trajectory?.tracks || [];
  const bounds = trajectory?.bounds;
  const empty = $("#trajectory-empty");
  if (!bounds || !tracks.length) {
    empty.hidden = false;
    empty.textContent = trajectory ? "No indexed positions match these filters." : "Loading indexed positions…";
    $("#trajectory-legend").innerHTML = "";
    $("#trajectory-note").textContent = "";
    canvas.setAttribute("aria-label", "No player trajectory available");
    return;
  }
  empty.hidden = true;

  const padding = { top: 24, right: 24, bottom: 32, left: 40 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const rangeX = Math.max(.001, bounds.max_x - bounds.min_x);
  const rangeZ = Math.max(.001, bounds.max_z - bounds.min_z);
  const scale = Math.min(plotWidth / rangeX, plotHeight / rangeZ);
  const contentWidth = rangeX * scale;
  const contentHeight = rangeZ * scale;
  const originX = padding.left + (plotWidth - contentWidth) / 2;
  const originY = padding.top + (plotHeight - contentHeight) / 2;
  const project = (point) => ({
    x: originX + (point.x - bounds.min_x) * scale,
    y: originY + (point.z - bounds.min_z) * scale,
  });

  context.fillStyle = "#080d0a";
  context.fillRect(0, 0, width, height);
  context.strokeStyle = "#213027";
  context.fillStyle = "#74877a";
  context.lineWidth = 1;
  context.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
  for (let index = 0; index <= 4; index += 1) {
    const x = padding.left + plotWidth * index / 4;
    const y = padding.top + plotHeight * index / 4;
    context.beginPath(); context.moveTo(x, padding.top); context.lineTo(x, padding.top + plotHeight); context.stroke();
    context.beginPath(); context.moveTo(padding.left, y); context.lineTo(padding.left + plotWidth, y); context.stroke();
  }
  context.fillText(`X ${bounds.min_x.toFixed(1)}`, padding.left, height - 10);
  const maxXLabel = `X ${bounds.max_x.toFixed(1)}`;
  context.fillText(maxXLabel, width - padding.right - context.measureText(maxXLabel).width, height - 10);
  context.save();
  context.translate(12, padding.top + plotHeight / 2);
  context.rotate(-Math.PI / 2);
  context.fillText(`Z ${bounds.min_z.toFixed(1)} → ${bounds.max_z.toFixed(1)}`, -54, 0);
  context.restore();

  tracks.forEach((track, trackIndex) => {
    const color = trajectoryColors[trackIndex % trajectoryColors.length];
    context.strokeStyle = color;
    context.lineWidth = 2;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.beginPath();
    (track.points || []).forEach((point, pointIndex) => {
      const projected = project(point);
      if (pointIndex === 0 || !point.continuous_from_previous) context.moveTo(projected.x, projected.y);
      else context.lineTo(projected.x, projected.y);
    });
    context.stroke();
    (track.points || []).forEach((point, pointIndex) => {
      if (pointIndex > 0 && point.continuous_from_previous) return;
      const projected = project(point);
      context.beginPath();
      context.fillStyle = "#080d0a";
      context.strokeStyle = color;
      context.lineWidth = 1.5;
      context.arc(projected.x, projected.y, 3, 0, Math.PI * 2);
      context.fill();
      context.stroke();
    });
  });

  const sample = state.currentSample?.record;
  const position = sample?.state?.position;
  if (position && finiteCoordinate(position.x) && finiteCoordinate(position.z)) {
    const current = project(position);
    context.beginPath();
    context.fillStyle = "#edf5ef";
    context.strokeStyle = "#071109";
    context.lineWidth = 2;
    context.arc(current.x, current.y, 5, 0, Math.PI * 2);
    context.fill();
    context.stroke();
    context.beginPath();
    context.strokeStyle = "#edf5ef";
    context.lineWidth = 1;
    context.arc(current.x, current.y, 9, 0, Math.PI * 2);
    context.stroke();
  }

  $("#trajectory-legend").innerHTML = tracks.map((track, index) => {
    const label = `${track.player_name || track.player_uuid} · ${shortDimension(track.dimension)} · ${track.horizontal_distance_blocks.toFixed(1)} blocks`;
    return `<span class="trajectory-legend-item"><i class="trajectory-swatch" style="background:${trajectoryColors[index % trajectoryColors.length]}"></i>${escapeHtml(label)}</span>`;
  }).join("");
  const notes = [
    `${trajectory.total_points} indexed positions`,
    trajectory.downsampled ? `${trajectory.returned_points} display points` : "full resolution",
  ];
  if (trajectory.omitted_tracks) notes.push(`${trajectory.omitted_tracks} tracks omitted by display bound`);
  if (position && finiteCoordinate(position.x) && finiteCoordinate(position.y) && finiteCoordinate(position.z)) {
    notes.push(`current ${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)}`);
  }
  $("#trajectory-note").textContent = notes.join(" · ");
  canvas.setAttribute("aria-label", `Top-down trajectory with ${tracks.length} track${tracks.length === 1 ? "" : "s"} and ${trajectory.total_points} indexed positions`);
}

function formatControlNumber(value, suffix = "") {
  return finiteCoordinate(value) ? `${value.toFixed(1)}${suffix}` : "—";
}

function renderInputHud(sample) {
  const control = sample?.action?.reconstructed_control;
  const payload = control?.payload;
  const available = Boolean(payload && typeof payload === "object");
  document.querySelectorAll("[data-control]").forEach((button) => {
    const pressed = available && payload[button.dataset.control] === true;
    button.classList.toggle("active", pressed);
    button.classList.toggle("unavailable", !available);
    button.setAttribute("aria-pressed", pressed ? "true" : "false");
  });
  const tick = control?.server_tick;
  $("#input-tick").textContent = Number.isInteger(tick) ? `control tick ${tick}` : "unavailable";
  const selectedSlot = Number.isInteger(payload?.selected_slot) ? payload.selected_slot + 1 : null;
  const metrics = [
    ["Yaw", formatControlNumber(payload?.camera_yaw, "°")],
    ["Pitch", formatControlNumber(payload?.camera_pitch, "°")],
    ["Hotbar", selectedSlot == null ? "—" : `slot ${selectedSlot}`],
    ["Δ yaw", formatControlNumber(payload?.camera_delta_yaw, "°")],
    ["Δ pitch", formatControlNumber(payload?.camera_delta_pitch, "°")],
    ["Source", control?.source?.record_type || (available ? "control_state" : "—")],
  ];
  $("#camera-controls").innerHTML = metrics.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  const packets = sample?.action?.ordered_packets || [];
  $("#packet-actions").innerHTML = packets.length
    ? packets.map((packet) => `<span class="packet-action">${escapeHtml(String(packet.action_type || "unknown").replaceAll("_", " "))}</span>`).join("")
    : '<span class="muted">No applied packet actions in this transition</span>';
  $("#input-note").textContent = available
    ? "Held state reconstructed at 20 Hz from server-observed controls; not raw keyboard or mouse events."
    : "No reconstructed control is available for this transition; button state is unknown.";
}

async function showSample(index) {
  if (!state.samples.length) return;
  const bounded = Math.max(0, Math.min(index, state.samples.length - 1));
  const summary = state.samples[bounded];
  const sampleId = summary.sample_id;
  const request = ++state.sampleRequest;
  try {
    const detail = await api(`/api/v1/datasets/${state.dataset.id}/samples/${sampleId}`);
    if (request !== state.sampleRequest) return;
    const sample = detail.record;
    state.sampleIndex = bounded;
    state.currentSample = { id: detail.sample_id, record: sample };
    $("#sample-slider").value = bounded;
    $("#sample-position").textContent = `${bounded + 1} / ${state.samples.length}`;
    const key = sample.sample_key || {};
    const rgb = sample.modalities?.rgb || {};
    const voxels = sample.modalities?.voxels || {};
    $("#sample-summary").innerHTML = `<p class="eyebrow">TICK ${escapeHtml(key.server_tick ?? sample.server_tick)}</p><h2>${escapeHtml(sample.state?.player_name || key.player_uuid || sample.player_uuid)}</h2><dl><dt>Connection</dt><dd>${escapeHtml(key.connection_id || sample.connection_id)}</dd><dt>Transition</dt><dd>${sample.transition_valid ? "valid" : "invalid"}</dd><dt>RGB</dt><dd>${rgb.available && rgb.valid ? "available" : escapeHtml(rgb.reason || "missing")}</dd><dt>Voxels</dt><dd>${voxels.available && voxels.valid ? "available" : escapeHtml(voxels.reason || "missing")}</dd></dl>`;
    const difference = stateDifference(sample.state, sample.next_state);
    $("#state-diff").textContent = JSON.stringify(Object.keys(difference).length ? difference : { unchanged: true }, null, 2);
    $("#sample-actions").textContent = JSON.stringify({ reconstructed_control: sample.action?.reconstructed_control, ordered_packets: sample.action?.ordered_packets || [] }, null, 2);
    $("#sample-provenance").textContent = JSON.stringify({ peers: sample.peers, transition_valid: sample.transition_valid, transition_invalid_reasons: sample.transition_invalid_reasons, source: sample.source, source_manifest_sha256: sample.source_manifest_sha256 }, null, 2);
    renderInputHud(sample);
    drawTrajectory();

    const image = $("#sample-frame");
    const missing = $("#frame-missing");
    if (rgb.available && rgb.valid && rgb.artifact_id) {
      image.src = `/api/v1/datasets/${state.dataset.id}/samples/${sampleId}/frame`;
      image.hidden = false;
      missing.hidden = true;
    } else {
      image.removeAttribute("src");
      image.hidden = true;
      missing.hidden = false;
    }
    $("#load-voxel").disabled = !(voxels.available && voxels.valid && voxels.artifact_id);
    setDefaultVoxelIndex();
  } catch (error) {
    toast(error.message);
  }
}

function stopPlayback() {
  state.playing = false;
  clearTimeout(state.playbackTimer);
  state.playbackTimer = null;
  $("#sample-play").textContent = "Play";
}

async function playbackStep() {
  if (!state.playing) return;
  const started = performance.now();
  if (state.sampleIndex < state.samples.length - 1) {
    await showSample(state.sampleIndex + 1);
  } else if (state.nextCursor) {
    state.pageHistory.push(state.currentCursor);
    state.currentCursor = state.nextCursor;
    await loadSamplePage();
  } else {
    stopPlayback();
    return;
  }
  if (state.playing) {
    state.playbackTimer = setTimeout(playbackStep, Math.max(0, 50 - (performance.now() - started)));
  }
}

function togglePlay() {
  if (state.playing) {
    stopPlayback();
    return;
  }
  if (!state.samples.length) return;
  state.playing = true;
  $("#sample-play").textContent = "Pause";
  state.playbackTimer = setTimeout(playbackStep, 50);
}

function setDefaultVoxelIndex() {
  const shape = state.currentSample?.record?.modalities?.voxels?.shape;
  const axis = $("#voxel-axis").value;
  if (shape && Number.isInteger(shape[axis]) && shape[axis] > 0) {
    $("#voxel-index").value = Math.floor(shape[axis] / 2);
  } else {
    $("#voxel-index").value = -1;
  }
}

async function loadVoxel() {
  if (!state.currentSample) return;
  try {
    const axis = $("#voxel-axis").value;
    const index = $("#voxel-index").value;
    const slice = await api(`/api/v1/datasets/${state.dataset.id}/samples/${state.currentSample.id}/voxel-slice?axis=${axis}&index=${index}`);
    const canvas = $("#voxel-canvas");
    const context = canvas.getContext("2d");
    canvas.width = slice.width;
    canvas.height = slice.height;
    const image = context.createImageData(slice.width, slice.height);
    (slice.cells || []).forEach((cell, cellIndex) => {
      const color = cell.covered ? (cell.color || [120, 160, 125]) : [28, 32, 30];
      image.data.set([...color, 255], cellIndex * 4);
    });
    context.putImageData(image, 0, 0);
    canvas.style.width = `${Math.min(640, slice.width * 8)}px`;
    $("#voxel-note").textContent = `${slice.covered_cells}/${slice.total_cells} slice cells covered · ${slice.axis}=${slice.world_coordinate}; dark cells are unknown`;
  } catch (error) {
    toast(error.message);
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    activateView(tab.dataset.view);
    if (tab.dataset.view === "datasets") refreshDatasets();
  });
});

$("#start-server").addEventListener("click", () => mutate("/api/v1/server/start"));
$("#stop-server").addEventListener("click", () => mutate("/api/v1/server/stop", "Stop Minecraft and seal all active recordings?"));
$("#refresh-recordings").addEventListener("click", refreshRecordings);
$("#refresh-renders").addEventListener("click", refreshRenders);
$("#refresh-datasets").addEventListener("click", refreshDatasets);
$("#apply-filters").addEventListener("click", () => {
  stopPlayback();
  state.sampleRequest += 1;
  state.currentSample = null;
  state.currentCursor = null;
  state.nextCursor = null;
  state.pageHistory = [];
  renderInputHud(null);
  drawTrajectory();
  Promise.all([loadTrajectory(), loadSamplePage()]);
});
$("#page-prev").addEventListener("click", previousSamplePage);
$("#page-next").addEventListener("click", nextSamplePage);
$("#sample-prev").addEventListener("click", () => { stopPlayback(); showSample(state.sampleIndex - 1); });
$("#sample-next").addEventListener("click", () => { stopPlayback(); showSample(state.sampleIndex + 1); });
$("#sample-slider").addEventListener("input", (event) => { stopPlayback(); showSample(Number(event.target.value)); });
$("#sample-play").addEventListener("click", togglePlay);
$("#voxel-axis").addEventListener("change", setDefaultVoxelIndex);
$("#load-voxel").addEventListener("click", loadVoxel);

if ("ResizeObserver" in window) {
  state.trajectoryResizeObserver = new ResizeObserver(() => drawTrajectory());
  state.trajectoryResizeObserver.observe($("#trajectory-canvas").parentElement);
} else {
  window.addEventListener("resize", drawTrajectory);
}

refreshStatus();
refreshRecordings();
refreshRenders();
setInterval(() => {
  refreshStatus();
  refreshRecordings();
  refreshRenders();
}, 2000);
