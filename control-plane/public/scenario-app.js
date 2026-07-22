import { createLatestScenarioLoader, exactFrameFailureMessage, renderExactFrameFailure } from "/scenario-loader.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const detailLoader = createLatestScenarioLoader();
const state = {
  catalog: null,
  detail: null,
  frames: null,
  annotations: null,
  baseline: null,
  selected: 0,
  playing: false,
  playbackSpeed: 1,
  timer: null,
  verifiedMedia: new Set(),
  generation: 0,
};

const byId = (id) => document.getElementById(id);

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function appendDefinition(list, term, value) {
  list.append(element("dt", term), element("dd", value ?? "Not declared"));
}

function humanBytes(bytes) {
  if (!Number.isFinite(bytes)) return "Unknown";
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KiB`;
}

function formatTime(ns) {
  return `${(ns / 1_000_000_000).toFixed(3)} s`;
}

function setCatalogStatus(message, error = false) {
  const output = byId("catalog-status");
  output.textContent = message;
  output.classList.toggle("error", error);
}

async function fetchJson(url, signal) {
  const response = await fetch(url, { headers: { accept: "application/json" }, signal });
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status}).`);
  return response.json();
}

function scenarioMatches(scenario) {
  const query = byId("scenario-search").value.trim().toLocaleLowerCase();
  const pack = byId("pack-filter").value;
  if (pack !== "all" && scenario.pack.id !== pack) return false;
  if (!query) return true;
  return [scenario.id, scenario.title, scenario.description, ...scenario.failure_modes]
    .join(" ")
    .toLocaleLowerCase()
    .includes(query);
}

function renderCatalog() {
  const list = byId("scenario-list");
  list.replaceChildren();
  const scenarios = state.catalog.scenarios.filter(scenarioMatches);
  for (const scenario of scenarios) {
    const card = element("article", undefined, "scenario-card");
    const meta = element("p", `${scenario.pack.id} · ${scenario.frames} frames · ${scenario.resolution}`, "scenario-card-meta");
    const title = element("h3");
    const link = element("a", scenario.title);
    link.href = `/scenarios/?scenario=${encodeURIComponent(scenario.id)}`;
    title.append(link);
    const description = element("p", scenario.description);
    const tags = element("ul", undefined, "scenario-tags");
    for (const mode of scenario.failure_modes) tags.append(element("li", mode));
    const footer = element("p", undefined, "scenario-card-footer");
    footer.append(element("span", scenario.license), element("span", scenario.annotation_scope.replaceAll("_", " ")));
    card.append(meta, title, description, tags, footer);
    list.append(card);
  }
  setCatalogStatus(`${scenarios.length} of ${state.catalog.scenario_count} public scenarios shown.`);
}

function currentAnnotationFrame() {
  return state.annotations.frames[state.selected];
}

function realVideoDetail() {
  return state.detail?.pack?.id === "real-video-v2";
}

function faultLabels(frameIndex) {
  const labels = [];
  for (const fault of state.annotations.faults || []) {
    if (Array.isArray(fault.frame_indices) && fault.frame_indices.includes(frameIndex)) labels.push(fault.type);
    if (fault.after_frame === frameIndex) labels.push(`${fault.type} after frame`);
  }
  return labels;
}

function svgNode(tag, attributes) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  return node;
}

function drawBox(svg, box, kind, label, showLabel, frameWidth) {
  const [x1, y1, x2, y2] = box;
  svg.append(svgNode("rect", { x: x1, y: y1, width: x2 - x1, height: y2 - y1, class: `overlay-box ${kind}` }));
  if (showLabel && label) {
    const fontSize = Math.max(3, frameWidth * 0.014);
    const text = svgNode("text", { x: x1 + fontSize * 0.3, y: Math.max(y1 + fontSize, fontSize), "font-size": fontSize, "stroke-width": frameWidth * 0.003, class: `overlay-label ${kind}` });
    text.textContent = label;
    svg.append(text);
  }
}

function overlayEnabled(name) {
  return document.querySelector(`[data-overlay="${name}"]`).checked;
}

function renderOverlay() {
  const svg = byId("scenario-overlay");
  const frame = state.frames.frames[state.selected];
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${frame.width} ${frame.height}`);
  const region = state.annotations.scoreable_region;
  if (overlayEnabled("roi") && region?.type !== "full_frame") {
    drawBox(svg, region.bounds, "roi", "scoreable boundary", overlayEnabled("labels"), frame.width);
  }
  for (const object of currentAnnotationFrame().objects) {
    if (!object.bbox_xyxy) continue;
    const ignored = Boolean(object.ignore);
    if ((ignored && !overlayEnabled("ignores")) || (!ignored && !overlayEnabled("targets"))) continue;
    const kind = ignored ? "ignore" : "target";
    const label = ignored ? `${object.ignore_region ? "ignore region" : "ignore"}: ${object.ignore_region_id || object.target_id}` : `${object.class_id}: ${object.target_id}`;
    drawBox(svg, object.bbox_xyxy, kind, label, overlayEnabled("labels"), frame.width);
  }
  const faults = faultLabels(frame.frame_index);
  if (overlayEnabled("faults") && faults.length) {
    const group = svgNode("g", { class: "fault-overlay" });
    group.append(svgNode("rect", { x: 0, y: 0, width: frame.width, height: frame.height * 0.08 }));
    const text = svgNode("text", { x: frame.width * 0.01, y: Math.max(frame.width * 0.025, frame.height * 0.055), "font-size": frame.width * 0.02 });
    text.textContent = `FAULT · ${faults.join(", ")}`;
    group.append(text);
    svg.append(group);
  }
}

function renderInspector() {
  const frame = state.frames.frames[state.selected];
  const annotations = currentAnnotationFrame().objects;
  const facts = byId("frame-facts");
  facts.replaceChildren();
  appendDefinition(facts, "Index", frame.frame_index);
  appendDefinition(facts, "Source time", formatTime(frame.source_timestamp_ns));
  appendDefinition(facts, "Resolution", `${frame.width} × ${frame.height}`);
  appendDefinition(facts, "JPEG", `${humanBytes(frame.media.bytes)} · sha256:${frame.media.sha256.slice(0, 16)}…`);
  appendDefinition(facts, realVideoDetail() ? "Tracked movers" : "Targets", annotations.filter((row) => !row.ignore).length);
  if (!realVideoDetail()) appendDefinition(facts, "Ignores", annotations.filter((row) => row.ignore).length);
  appendDefinition(facts, "Faults", faultLabels(frame.frame_index).join(", ") || "None");
  const list = byId("frame-annotations");
  list.replaceChildren();
  if (!annotations.length) list.append(element("p", realVideoDetail()
    ? "No supported moving objects are visible on this frame. This is explicit exhaustive ground truth, not a loading error."
    : "No target or ignore rows on this frame. This is explicit public ground truth, not a loading error."));
  for (const object of annotations) {
    const row = element("article");
    row.append(element("strong", object.ignore ? (object.ignore_region ? "Ignore region" : "Ignore") : (realVideoDetail() ? "Tracked object" : "Target")));
    row.append(element("span", `${object.target_id} · ${object.class_id}`));
    if (object.bbox_xyxy) row.append(element("code", `[${object.bbox_xyxy.join(", ")}]`));
    if (!object.ignore) row.append(element("small", `${object.occlusion} occlusion · ${(object.visibility_fraction * 100).toFixed(0)}% visible · ${object.truncated ? "truncated" : "not truncated"} · ${object.eligible_for_detection ? "scoreable" : "not detection-eligible"}`));
    list.append(row);
  }
}

async function digestHex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function verifyMedia(frame, generation, announce = true) {
  if (state.verifiedMedia.has(frame.media.url)) return true;
  const response = await fetch(frame.media.url, { cache: "force-cache" });
  if (!response.ok) throw new Error(exactFrameFailureMessage(response.status));
  const body = await response.arrayBuffer();
  const digest = await digestHex(body);
  if (digest !== frame.media.sha256) throw new Error(`Exact frame failed its published SHA-256 check.`);
  if (generation !== state.generation) return false;
  state.verifiedMedia.add(frame.media.url);
  if (announce) byId("media-state").textContent = "Exact frame verified.";
  return true;
}

async function prefetchNextFrames(generation) {
  if (navigator.connection?.saveData) return;
  for (let offset = 1; offset <= 2; offset += 1) {
    const frame = state.frames.frames[state.selected + offset];
    if (!frame) break;
    try {
      await verifyMedia(frame, generation, false);
    } catch {
      return;
    }
  }
}

async function showFrame(index, announcement = true) {
  if (!state.frames) return;
  stopPlayback();
  state.selected = Math.max(0, Math.min(index, state.frames.frames.length - 1));
  const frame = state.frames.frames[state.selected];
  const image = byId("scenario-frame");
  const mediaState = byId("media-state");
  const generation = ++state.generation;
  image.removeAttribute("src");
  image.width = frame.width;
  image.height = frame.height;
  image.alt = `${state.detail.title}, exact frame ${frame.frame_index + 1} of ${state.frames.frames.length}`;
  mediaState.hidden = false;
  mediaState.classList.remove("error");
  mediaState.textContent = "Verifying exact frame hash…";
  byId("frame-scrubber").value = String(state.selected);
  byId("frame-position").textContent = `${state.selected + 1} / ${state.frames.frames.length}`;
  byId("frame-time").textContent = formatTime(frame.source_timestamp_ns);
  byId("previous-frame").disabled = state.selected === 0;
  byId("next-frame").disabled = state.selected === state.frames.frames.length - 1;
  renderOverlay();
  renderInspector();
  try {
    const current = await verifyMedia(frame, generation);
    if (!current || generation !== state.generation) return;
    image.src = frame.media.url;
    image.addEventListener("load", () => {
      if (generation === state.generation) mediaState.hidden = true;
    }, { once: true });
    image.addEventListener("error", () => {
      if (generation !== state.generation) return;
      mediaState.hidden = false;
      mediaState.classList.add("error");
      mediaState.textContent = "The verified media could not be decoded by this browser.";
    }, { once: true });
    void prefetchNextFrames(generation);
  } catch (error) {
    if (generation !== state.generation) return;
    renderExactFrameFailure(mediaState, error);
  }
  if (announcement) byId("viewer-announcement").textContent = `Frame ${state.selected + 1} of ${state.frames.frames.length}, ${annotationsSummary()}.`;
}

function annotationsSummary() {
  const rows = currentAnnotationFrame().objects;
  if (realVideoDetail()) return `${rows.filter((row) => !row.ignore).length} tracked moving objects`;
  return `${rows.filter((row) => !row.ignore).length} targets and ${rows.filter((row) => row.ignore).length} ignores`;
}

function stopPlayback() {
  if (state.timer) window.clearTimeout(state.timer);
  state.timer = null;
  state.playing = false;
  const button = byId("play-pause");
  if (button) {
    button.textContent = "Play";
    button.setAttribute("aria-label", "Play sequence");
  }
}

function scheduleNext() {
  if (!state.playing) return;
  const current = state.frames.frames[state.selected];
  const next = state.frames.frames[state.selected + 1];
  if (!next) {
    stopPlayback();
    return;
  }
  const delay = Math.max(8, (next.source_timestamp_ns - current.source_timestamp_ns) / 1_000_000 / state.playbackSpeed);
  state.timer = window.setTimeout(async () => {
    const wasPlaying = state.playing;
    await showFrame(state.selected + 1, false);
    if (wasPlaying && state.selected < state.frames.frames.length - 1) {
      state.playing = true;
      byId("play-pause").textContent = "Pause";
      byId("play-pause").setAttribute("aria-label", "Pause sequence");
      scheduleNext();
    }
  }, delay);
}

function togglePlayback() {
  if (state.playing) {
    stopPlayback();
    byId("viewer-announcement").textContent = `Paused on frame ${state.selected + 1}.`;
    return;
  }
  if (state.selected === state.frames.frames.length - 1) void showFrame(0, false);
  state.playing = true;
  byId("play-pause").textContent = "Pause";
  byId("play-pause").setAttribute("aria-label", "Pause sequence");
  byId("viewer-announcement").textContent = `Playing from frame ${state.selected + 1}.`;
  scheduleNext();
}

function safeLink(label, href) {
  const link = element("a", label);
  const url = new URL(href, location.origin);
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("Unsafe catalog link protocol.");
  link.href = url.href;
  if (url.origin !== location.origin) {
    link.rel = "noreferrer";
    link.target = "_blank";
  }
  return link;
}

function renderDetailFacts() {
  const detail = state.detail;
  const scenario = byId("scenario-facts");
  scenario.replaceChildren();
  appendDefinition(scenario, "Stable ID", detail.id);
  appendDefinition(scenario, "Status", `${detail.status} · ${detail.pack.id} ${detail.pack.version}`);
  appendDefinition(scenario, "Task", detail.task);
  appendDefinition(scenario, "Failure modes", detail.failure_modes.join(", "));
  appendDefinition(scenario, "Timing", `${detail.media.frame_count} frames · ${detail.media.fps ?? "variable"} FPS · ${formatTime(detail.media.duration_ns)}`);
  appendDefinition(scenario, "Resolution", `${detail.media.width} × ${detail.media.height}`);
  appendDefinition(scenario, "Classes", detail.annotations.class_ids.join(", ") || "No targets by design");
  appendDefinition(scenario, "Annotation policy", detail.annotations.policy.disclosure);
  if (detail.annotations.scoring.scoreable_region === "full_frame") {
    appendDefinition(scenario, "Scoring boundary", "Class-aware, full-frame scoring. Every supported visible mover is exhaustive ground truth; misses, duplicates, false tracks, and background predictions are penalized.");
    appendDefinition(scenario, "Temporal scoring", detail.annotations.scoring.temporal_metrics.join(", "));
  } else {
    appendDefinition(scenario, "Scoring boundary", `Class-aware. ${detail.annotations.scoring.outside_fixed_roi.replaceAll("_", " ")}. Target matching precedes ignore matching.`);
    appendDefinition(scenario, "Ignore rules", `Ignore region: ${detail.annotations.scoring.ignore_region_match}; ordinary ignore: ${detail.annotations.scoring.ordinary_ignore_match}.`);
  }

  const provenance = byId("provenance-facts");
  provenance.replaceChildren();
  provenance.append(element("p", detail.provenance.source.attribution));
  const sourceLine = element("p");
  sourceLine.append(safeLink(detail.provenance.source.title, detail.provenance.source.source_url));
  provenance.append(sourceLine);
  if (detail.provenance.source.license_url) provenance.append(safeLink(detail.provenance.source.license, detail.provenance.source.license_url));
  else provenance.append(element("p", detail.provenance.source.license));
  provenance.append(element("p", detail.provenance.source.transformation));
  if (detail.provenance.source.annotation_provenance) provenance.append(element("p", `Annotation provenance: ${detail.provenance.source.annotation_provenance}`));
  if (detail.provenance.source.corrections) provenance.append(element("p", `Audited corrections: ${detail.provenance.source.corrections}`));
  provenance.append(element("p", `Preparation: ${detail.provenance.preparation.identity} · ${detail.provenance.preparation.platform} · definition sha256:${detail.provenance.preparation.dockerfile_sha256.slice(0, 16)}…`));

  const baseline = byId("baseline-facts");
  baseline.replaceChildren();
  appendDefinition(baseline, "System", `${state.baseline.system_name} · ${state.baseline.system_version}`);
  appendDefinition(baseline, "Status", state.baseline.validation_status);
  appendDefinition(baseline, "Run", state.baseline.run_id);
  appendDefinition(baseline, "Report hash", `sha256:${state.baseline.report_sha256}`);
  appendDefinition(baseline, "Sanitized evidence", `${state.baseline.source_evidence.id} · sha256:${state.baseline.source_evidence.sha256}`);
  for (const [name, value] of Object.entries(state.baseline.metrics)) appendDefinition(baseline, name.replaceAll("_", " "), value);

  const links = byId("manifest-links");
  links.replaceChildren();
  const manifests = [
    ["Stable scenario JSON", `/scenario-catalog/v1/scenarios/${detail.id}.json`, null],
    ["Content-addressed frame manifest", detail.media.frame_manifest.url, detail.media.frame_manifest.sha256],
    ["Content-addressed annotation manifest", detail.annotations.annotation_manifest.url, detail.annotations.annotation_manifest.sha256],
    ["Content-addressed baseline manifest", detail.baseline.manifest.url, detail.baseline.manifest.sha256],
  ];
  for (const [label, url, hash] of manifests) {
    const item = element("li");
    item.append(safeLink(label, url));
    if (hash) item.append(element("code", `sha256:${hash}`));
    links.append(item);
  }
}

async function loadDetail(id) {
  stopPlayback();
  setCatalogStatus(`Loading ${id}…`);
  const summary = state.catalog.scenarios.find((scenario) => scenario.id === id);
  if (!summary) {
    detailLoader.cancel();
    throw new Error(`Unknown public scenario: ${id}.`);
  }
  await detailLoader.load(id, async (signal) => {
    const detail = await fetchJson(summary.detail.url, signal);
    const [frames, annotations, baseline] = await Promise.all([
      fetchJson(detail.media.frame_manifest.url, signal),
      fetchJson(detail.annotations.annotation_manifest.url, signal),
      fetchJson(detail.baseline.manifest.url, signal),
    ]);
    if (detail.id !== id || frames.scenario_id !== id || annotations.scenario_id !== id || baseline.scenario_id !== id) throw new Error("Scenario manifest identity mismatch.");
    if (frames.frames.length !== annotations.frames.length) throw new Error("Frame and annotation manifest lengths differ.");
    return { annotations, baseline, detail, frames };
  }, detailFromLocation, ({ annotations, baseline, detail, frames }) => {
    state.detail = detail;
    state.frames = frames;
    state.annotations = annotations;
    state.baseline = baseline;
    state.selected = 0;
    byId("detail-kicker").textContent = `${detail.pack.id} · ${detail.status} · ${detail.id}`;
    byId("detail-title").textContent = detail.title;
    byId("detail-description").textContent = detail.description;
    byId("frame-scrubber").max = String(frames.frames.length - 1);
    const real = detail.pack.id === "real-video-v2";
    byId("objects-overlay-label").querySelector("span").textContent = real ? "Tracked objects" : "Targets";
    byId("ignores-overlay-label").hidden = real;
    byId("region-overlay-label").hidden = real;
    byId("overlay-disclosure").textContent = real
      ? "Public whole-scene overlays are human inspection aids. Systems receive only progressive frames and timestamps—never these boxes, identities, annotations, or future frames."
      : "Public overlays are human inspection aids and are never sent to submitted systems.";
    byId("scenario-detail").hidden = false;
    renderDetailFacts();
    void showFrame(0, false);
    setCatalogStatus(`${state.catalog.scenario_count} current scenarios are public.`);
    byId("scenario-detail").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  });
}

function detailFromLocation() {
  return new URLSearchParams(location.search).get("scenario");
}

async function init() {
  try {
    state.catalog = await fetchJson("/scenario-catalog/v1/catalog.json");
    if (state.catalog.scenario_count !== 16 || !state.catalog.all_current_scenarios_public) throw new Error("The catalog completeness assertion failed.");
    byId("scenario-count").textContent = state.catalog.scenario_count;
    renderCatalog();
    const selected = detailFromLocation();
    if (selected) await loadDetail(selected);
  } catch (error) {
    setCatalogStatus(error.message, true);
  }
}

byId("scenario-search").addEventListener("input", renderCatalog);
byId("pack-filter").addEventListener("change", renderCatalog);
byId("previous-frame").addEventListener("click", () => void showFrame(state.selected - 1));
byId("next-frame").addEventListener("click", () => void showFrame(state.selected + 1));
byId("play-pause").addEventListener("click", togglePlayback);
byId("playback-speed").addEventListener("change", (event) => {
  state.playbackSpeed = Number(event.currentTarget.value);
  if (state.playing) {
    if (state.timer) window.clearTimeout(state.timer);
    scheduleNext();
  }
  byId("viewer-announcement").textContent = `Playback speed ${state.playbackSpeed} times.`;
});
byId("frame-scrubber").addEventListener("input", (event) => void showFrame(Number(event.currentTarget.value)));
for (const toggle of document.querySelectorAll("[data-overlay]")) toggle.addEventListener("change", renderOverlay);
byId("frame-viewer").addEventListener("keydown", (event) => {
  if (!state.frames || ["INPUT", "BUTTON"].includes(event.target.tagName)) return;
  if (event.key === " ") { event.preventDefault(); togglePlayback(); }
  if (event.key === "ArrowLeft") { event.preventDefault(); void showFrame(state.selected - 1); }
  if (event.key === "ArrowRight") { event.preventDefault(); void showFrame(state.selected + 1); }
  if (event.key === "Home") { event.preventDefault(); void showFrame(0); }
  if (event.key === "End") { event.preventDefault(); void showFrame(state.frames.frames.length - 1); }
});
window.addEventListener("popstate", () => {
  const selected = detailFromLocation();
  if (selected) void loadDetail(selected).catch((error) => setCatalogStatus(error.message, true));
  else {
    detailLoader.cancel();
    stopPlayback();
    byId("scenario-detail").hidden = true;
  }
});

void init();
