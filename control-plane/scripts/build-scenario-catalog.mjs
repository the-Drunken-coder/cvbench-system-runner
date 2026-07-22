#!/usr/bin/env node

import { createHash } from "node:crypto";
import { cp, lstat, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { parse as parseYaml } from "yaml";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CONTROL_PLANE = path.resolve(HERE, "..");
const ROOT = path.resolve(CONTROL_PLANE, "..");
const PUBLIC = path.join(CONTROL_PLANE, "public");
const CATALOG_SOURCE = path.join(ROOT, "scenario-catalog");
const DEFAULT_OUTPUT = path.join(CONTROL_PLANE, "dist");
const STATIC_FILES = [
  "_headers",
  "app.js",
  "index.html",
  "operator.html",
  "operator.js",
  "scenario-app.js",
  "scenarios/index.html",
  "styles.css",
];
const BENCHMARK_FILES = [
  "benchmarks/long-running-stability.yaml",
  "benchmarks/persistent-target-tracking.yaml",
  "benchmarks/real-video-v1.yaml",
];
const ALLOWED_PUBLISHED_EXTENSIONS = new Set(["", ".css", ".html", ".jpg", ".js", ".json"]);
const PRIVATE_PATH_PATTERN = /(?:^|\/)(?:\.dev\.vars|\.env(?:\.|$)|.*(?:credential|secret|contact|note|failure[-_]?packet|raw[-_]?report|d1[-_]?export|private[-_]?log).*)(?:\/|$)/i;
const PRIVATE_CONTENT_PATTERNS = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  /(?:SUBMISSION_API_KEYS|RUNNER_TOKEN|OPERATOR_READ_API_KEYS|OPERATOR_ADJUDICATOR_CREDENTIALS)\s*=/,
  /"(?:contact|notes|lease_token|submitter_key_hash|operator_key_hash)"\s*:/i,
];
const MAX_ASSET_BYTES = 25 * 1024 * 1024;
const RECOMMENDED_FRAME_BYTES = 2 * 1024 * 1024;
const MAX_CATALOG_BYTES = 256 * 1024;
const MAX_SCENARIO_BYTES = 512 * 1024;
const MAX_SITE_BYTES = 50 * 1024 * 1024;
const PREPARATION_HASH = sha256(Buffer.from(await readFile(path.join(ROOT, "examples/Dockerfile.real-video-prep"))));

function fail(message) {
  throw new Error(`scenario catalog build rejected: ${message}`);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value) {
  return `${JSON.stringify(sortValue(value), null, 2)}\n`;
}

function sortValue(value) {
  if (Array.isArray(value)) return value.map(sortValue);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortValue(value[key])]));
}

async function readYaml(relative) {
  return parseYaml(await readFile(path.join(ROOT, relative), "utf8"));
}

async function assertedRegularFile(file, allowedRoot) {
  const relative = path.relative(allowedRoot, file);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) fail(`path escapes allowlisted root: ${file}`);
  if (PRIVATE_PATH_PATTERN.test(relative.replaceAll(path.sep, "/"))) fail(`private artifact pattern: ${relative}`);
  const info = await lstat(file);
  if (info.isSymbolicLink()) fail(`symlink is not publishable: ${relative}`);
  if (!info.isFile()) fail(`declared asset is not a regular file: ${relative}`);
  return info;
}

function finiteNumber(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value)) fail(`${label} must be finite`);
}

function validateBox(box, width, height, label) {
  if (!Array.isArray(box) || box.length !== 4) fail(`${label} must contain four coordinates`);
  box.forEach((value, index) => finiteNumber(value, `${label}[${index}]`));
  const [x1, y1, x2, y2] = box;
  if (x1 < 0 || y1 < 0 || x2 > width || y2 > height || x2 <= x1 || y2 <= y1) {
    fail(`${label} is outside ${width}x${height}`);
  }
}

function jpegDimensions(buffer) {
  if (buffer[0] !== 0xff || buffer[1] !== 0xd8) fail("frame is not a JPEG");
  let offset = 2;
  while (offset + 9 < buffer.length) {
    if (buffer[offset] !== 0xff) fail("malformed JPEG marker stream");
    const marker = buffer[offset + 1];
    offset += 2;
    if (marker === 0xd8 || marker === 0xd9) continue;
    const length = buffer.readUInt16BE(offset);
    if (length < 2 || offset + length > buffer.length) fail("malformed JPEG segment length");
    if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
      return { height: buffer.readUInt16BE(offset + 3), width: buffer.readUInt16BE(offset + 5) };
    }
    offset += length;
  }
  fail("JPEG dimensions are missing");
}

async function benchmarkInventory() {
  const scenarioPaths = new Map();
  const benchmarkMembership = new Map();
  for (const relative of BENCHMARK_FILES) {
    const benchmark = await readYaml(relative);
    if (benchmark?.schema_version !== "cvbench.benchmark/v1" || !Array.isArray(benchmark.scenarios)) {
      fail(`invalid benchmark manifest: ${relative}`);
    }
    for (const declared of benchmark.scenarios) {
      const absolute = path.resolve(path.dirname(path.join(ROOT, relative)), declared);
      await assertedRegularFile(absolute, path.join(ROOT, "scenarios"));
      const manifest = parseYaml(await readFile(absolute, "utf8"));
      if (!manifest?.id || manifest.schema_version !== "cvbench.scenario/v1") fail(`invalid scenario manifest: ${declared}`);
      const prior = scenarioPaths.get(manifest.id);
      if (prior && prior !== absolute) fail(`duplicate scenario id ${manifest.id}`);
      scenarioPaths.set(manifest.id, absolute);
      const memberships = benchmarkMembership.get(manifest.id) || [];
      if (!memberships.includes(benchmark.id)) memberships.push(benchmark.id);
      benchmarkMembership.set(manifest.id, memberships);
    }
  }
  if (scenarioPaths.size !== 16) fail(`benchmark union must contain 16 scenarios, found ${scenarioPaths.size}`);
  return { benchmarkMembership, scenarioPaths };
}

async function expectedRealHashes() {
  const expected = new Map();
  const content = await readFile(path.join(ROOT, "scenarios/real-video-v1/expected-frame-sha256.txt"), "utf8");
  for (const line of content.trim().split("\n")) {
    const [digest, relative] = line.split("  ");
    if (!/^[a-f0-9]{64}$/.test(digest) || !relative) fail("malformed canonical real-frame hash manifest");
    expected.set(relative, digest);
  }
  if (expected.size !== 78) fail(`canonical real-frame manifest must contain 78 entries, found ${expected.size}`);
  return expected;
}

function annotationPolicy(real) {
  if (!real) {
    return {
      scope: "exhaustive",
      disclosure: "Synthetic annotations are exhaustive. An unannotated synthetic object is not implied.",
    };
  }
  return {
    scope: "targeted_non_exhaustive",
    disclosure: "Real annotations cover selected targets and explicit ignore geometry only; they do not claim every visible object is annotated.",
  };
}

function scoringPolicy(real) {
  return {
    class_aware: true,
    target_matching_precedes_ignore_matching: true,
    ordinary_ignore_match: "IoU > 0.5",
    ignore_region_match: ">= 50% prediction-area coverage",
    duplicate_target_predictions_penalized: true,
    background_hallucinations_inside_roi_penalized: true,
    outside_fixed_roi: real ? "out_of_scope" : "not_applicable_full_frame",
  };
}

async function publishJson(output, relative, value, maximum) {
  const body = Buffer.from(canonicalJson(value));
  if (body.length > maximum) fail(`${relative} is ${body.length} bytes; limit is ${maximum}`);
  const destination = path.join(output, relative);
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, body);
  return { bytes: body.length, sha256: sha256(body), url: `/${relative}` };
}

async function publishContentAddressedJson(output, kind, value) {
  const body = Buffer.from(canonicalJson(value));
  const digest = sha256(body);
  const relative = `scenario-catalog/v1/${kind}/sha256/${digest}.json`;
  const destination = path.join(output, relative);
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, body);
  return { bytes: body.length, sha256: digest, url: `/${relative}` };
}

async function publishFrame(output, source, expectedDigest, publishedFrames) {
  const buffer = await readFile(source);
  const digest = sha256(buffer);
  if (expectedDigest && digest !== expectedDigest) fail(`frame hash mismatch for ${source}: expected ${expectedDigest}, got ${digest}`);
  if (buffer.length > MAX_ASSET_BYTES) fail(`frame exceeds Cloudflare's 25 MiB asset limit: ${source}`);
  if (buffer.length > RECOMMENDED_FRAME_BYTES) fail(`frame exceeds the 2 MiB catalog recommendation: ${source}`);
  const relative = `scenario-catalog/v1/assets/sha256/${digest}.jpg`;
  if (!publishedFrames.has(digest)) {
    const destination = path.join(output, relative);
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, buffer);
    publishedFrames.add(digest);
  }
  return { bytes: buffer.length, sha256: digest, url: `/${relative}`, dimensions: jpegDimensions(buffer) };
}

function frameInterval(frames) {
  if (frames.length < 2) return { fps: null, interval_ns: null };
  const intervals = frames.slice(1).map((frame, index) => frame.source_timestamp_ns - frames[index].source_timestamp_ns);
  if (!intervals.every((value) => value === intervals[0] && value > 0)) return { fps: null, interval_ns: null };
  return { fps: Number((1_000_000_000 / intervals[0]).toFixed(6)), interval_ns: intervals[0] };
}

function countTargets(annotations) {
  return new Set(annotations.filter((row) => !row.ignore).map((row) => row.target_id)).size;
}

async function scenarioDocument({ id, manifestPath, membership, metadata, baseline, expectedHashes, output, publishedFrames }) {
  const manifest = parseYaml(await readFile(manifestPath, "utf8"));
  const real = id.startsWith("rv1-");
  const source = metadata.sources[real ? id : "synthetic"];
  if (!source || source.license !== manifest.license) fail(`license metadata mismatch for ${id}`);
  const scenarioMeta = metadata.scenarios[id];
  if (!scenarioMeta || !Array.isArray(scenarioMeta.failure_modes) || !scenarioMeta.failure_modes.length) fail(`missing curated metadata for ${id}`);
  if (!baseline) fail(`missing allowlisted baseline evidence for ${id}`);

  const manifestDirectory = path.dirname(manifestPath);
  const localGroundTruth = path.join(manifestDirectory, "ground_truth.jsonl");
  await assertedRegularFile(localGroundTruth, path.join(ROOT, "scenarios"));
  const annotations = (await readFile(localGroundTruth, "utf8")).split("\n").filter(Boolean).map((line, index) => {
    try {
      return JSON.parse(line);
    } catch {
      fail(`malformed JSONL at ${path.relative(ROOT, localGroundTruth)}:${index + 1}`);
    }
  });
  const frameByTimestamp = new Map(manifest.frames.map((frame) => [frame.source_timestamp_ns, frame]));
  const annotationsByTimestamp = new Map(manifest.frames.map((frame) => [frame.source_timestamp_ns, []]));
  for (const [index, row] of annotations.entries()) {
    const frame = frameByTimestamp.get(row.source_timestamp_ns);
    if (!frame) fail(`${id} annotation ${index} references an undeclared timestamp`);
    if (row.sequence_id !== manifest.sequence_id) fail(`${id} annotation ${index} has the wrong sequence_id`);
    if (row.bbox_xyxy) validateBox(row.bbox_xyxy, frame.width, frame.height, `${id} annotation ${index} bbox`);
    annotationsByTimestamp.get(row.source_timestamp_ns).push(row);
  }
  if (manifest.scoreable_roi) validateBox(manifest.scoreable_roi, manifest.frames[0].width, manifest.frames[0].height, `${id} scoreable ROI`);

  const frames = [];
  for (const frame of manifest.frames) {
    if (!Number.isInteger(frame.frame_index) || frame.frame_index !== frames.length) fail(`${id} frame indexes are not contiguous`);
    if (!Number.isInteger(frame.source_timestamp_ns) || frame.source_timestamp_ns < 0) fail(`${id} has an invalid frame timestamp`);
    if (!Number.isInteger(frame.width) || !Number.isInteger(frame.height)) fail(`${id} has invalid frame dimensions`);
    let sourcePath;
    let expectedDigest;
    if (real) {
      const name = `frame-${String(frame.frame_index).padStart(4, "0")}.jpg`;
      sourcePath = path.join(CATALOG_SOURCE, "media/real-video-v1", id, "frames", name);
      expectedDigest = expectedHashes.get(`${id}/frames/${name}`);
      if (!expectedDigest) fail(`no canonical hash declared for ${id}/${name}`);
      await assertedRegularFile(sourcePath, path.join(CATALOG_SOURCE, "media"));
    } else {
      sourcePath = path.resolve(manifestDirectory, frame.path);
      await assertedRegularFile(sourcePath, path.join(ROOT, "scenarios"));
    }
    const published = await publishFrame(output, sourcePath, expectedDigest, publishedFrames);
    if (published.dimensions.width !== frame.width || published.dimensions.height !== frame.height) {
      fail(`${id} frame ${frame.frame_index} dimensions do not match the manifest`);
    }
    frames.push({
      frame_index: frame.frame_index,
      source_timestamp_ns: frame.source_timestamp_ns,
      width: frame.width,
      height: frame.height,
      media: { url: published.url, sha256: published.sha256, bytes: published.bytes, media_type: "image/jpeg" },
    });
  }
  const groupedAnnotations = frames.map((frame) => ({
    frame_index: frame.frame_index,
    source_timestamp_ns: frame.source_timestamp_ns,
    objects: annotationsByTimestamp.get(frame.source_timestamp_ns),
  }));
  const frameManifest = await publishContentAddressedJson(output, "frame-manifests", {
    schema_version: "cvbench.frame-manifest/v1",
    scenario_id: id,
    exact_benchmark_sequence: true,
    frames,
  });
  const annotationManifest = await publishContentAddressedJson(output, "annotation-manifests", {
    schema_version: "cvbench.annotation-manifest/v1",
    scenario_id: id,
    annotation_policy: annotationPolicy(real),
    scoreable_roi: manifest.scoreable_roi || [0, 0, frames[0].width, frames[0].height],
    faults: manifest.faults || [],
    frames: groupedAnnotations,
  });
  const baselineManifest = await publishContentAddressedJson(output, "baseline-manifests", {
    schema_version: "cvbench.scenario-baseline/v1",
    scenario_id: id,
    ...baseline,
  });
  const timing = frameInterval(frames);
  const classes = [...new Set(annotations.filter((row) => !row.ignore).map((row) => row.class_id))].sort();
  return {
    schema_version: "cvbench.public-scenario/v1",
    id,
    stable_id: id,
    sequence_id: manifest.sequence_id,
    version: "1.0.0",
    pack: { id: real ? "real-video-v1" : "synthetic-v1", version: "1.0.0", status: "public" },
    status: "public",
    title: scenarioMeta.title,
    description: scenarioMeta.description,
    task: "online class-aware multi-object tracking",
    failure_modes: scenarioMeta.failure_modes,
    benchmark_membership: [...membership].sort(),
    media: {
      exact_benchmark_frame_sequence: true,
      frame_count: frames.length,
      duration_ns: frames.at(-1).source_timestamp_ns - frames[0].source_timestamp_ns,
      fps: timing.fps,
      frame_interval_ns: timing.interval_ns,
      width: frames[0].width,
      height: frames[0].height,
      frame_manifest: frameManifest,
    },
    annotations: {
      annotation_manifest: annotationManifest,
      policy: annotationPolicy(real),
      scoreable_roi: manifest.scoreable_roi || [0, 0, frames[0].width, frames[0].height],
      target_count: countTargets(annotations),
      class_ids: classes,
      object_rows: annotations.filter((row) => !row.ignore).length,
      ignore_rows: annotations.filter((row) => row.ignore).length,
      scoring: scoringPolicy(real),
    },
    provenance: {
      source,
      author: real ? `${source.creator}; annotations and preparation by CVBench contributors` : source.creator,
      preparation: {
        ...metadata.preparation,
        dockerfile_sha256: PREPARATION_HASH,
      },
    },
    baseline: {
      manifest: baselineManifest,
      system_id: baseline.system_id,
      system_version: baseline.system_version,
      validation_status: baseline.validation_status,
    },
    public_data_disclosure: "All current CVBench scenarios, media, and annotations are public and may be tuned to or memorized. Runtime isolation prevents future-frame and host-data access during execution; it does not make public benchmark data secret. Future hidden challenges are outside catalog v1.",
  };
}

async function assertDeclaredMedia(expectedHashes) {
  const mediaRoot = path.join(CATALOG_SOURCE, "media");
  const actual = [];
  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) fail(`symlink in media allowlist: ${path.relative(mediaRoot, file)}`);
      if (entry.isDirectory()) await walk(file);
      else if (entry.isFile()) actual.push(path.relative(path.join(mediaRoot, "real-video-v1"), file).replaceAll(path.sep, "/"));
      else fail(`non-regular media entry: ${path.relative(mediaRoot, file)}`);
    }
  }
  await walk(mediaRoot);
  const expected = [...expectedHashes.keys()].sort();
  actual.sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) fail("real media directory contains missing or undeclared files");
}

async function assertStaticAllowlist() {
  const actual = [];
  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) fail(`symlink in public source: ${path.relative(PUBLIC, file)}`);
      if (entry.isDirectory()) await walk(file);
      else if (entry.isFile()) actual.push(path.relative(PUBLIC, file).replaceAll(path.sep, "/"));
      else fail(`non-regular public source: ${path.relative(PUBLIC, file)}`);
    }
  }
  await walk(PUBLIC);
  actual.sort();
  const expected = [...STATIC_FILES].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) fail(`public source allowlist mismatch: ${actual.join(", ")}`);
}

async function outputEvidence(output) {
  const files = [];
  let total = 0;
  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(file);
      else {
        const relative = path.relative(output, file).replaceAll(path.sep, "/");
        const extension = path.extname(relative);
        if (!ALLOWED_PUBLISHED_EXTENSIONS.has(extension)) fail(`undeclared published extension: ${relative}`);
        if (PRIVATE_PATH_PATTERN.test(relative)) fail(`private artifact path in output: ${relative}`);
        const body = await readFile(file);
        if (body.length > MAX_ASSET_BYTES) fail(`published file exceeds 25 MiB: ${relative}`);
        if (path.extname(relative) !== ".jpg") {
          const text = body.toString("utf8");
          for (const pattern of PRIVATE_CONTENT_PATTERNS) {
            if (pattern.test(text)) fail(`private artifact content in output: ${relative}`);
          }
        }
        total += body.length;
        files.push({ path: relative, bytes: body.length, sha256: sha256(body) });
      }
    }
  }
  await walk(output);
  if (total > MAX_SITE_BYTES) fail(`published site is ${total} bytes; initial catalog limit is ${MAX_SITE_BYTES}`);
  return { files: files.sort((a, b) => a.path.localeCompare(b.path)), total_bytes: total };
}

async function main() {
  const outputArgument = process.argv.indexOf("--output");
  const output = outputArgument >= 0 ? path.resolve(process.cwd(), process.argv[outputArgument + 1]) : DEFAULT_OUTPUT;
  if (output === ROOT || output === CONTROL_PLANE || !path.relative(CONTROL_PLANE, output) || path.relative(CONTROL_PLANE, output).startsWith("..")) {
    fail("output must be a dedicated directory below control-plane");
  }
  const metadata = await readYaml("scenario-catalog/metadata.yaml");
  if (metadata?.catalog?.status !== "public") fail("catalog status must be public");
  const baselines = JSON.parse(await readFile(path.join(CATALOG_SOURCE, "baselines.json"), "utf8"));
  const { benchmarkMembership, scenarioPaths } = await benchmarkInventory();
  const ids = [...scenarioPaths.keys()].sort();
  if (JSON.stringify(Object.keys(metadata.scenarios).sort()) !== JSON.stringify(ids)) fail("curated metadata must match the benchmark scenario union exactly");
  if (JSON.stringify(Object.keys(baselines.scenarios).sort()) !== JSON.stringify(ids)) fail("baseline evidence must match the benchmark scenario union exactly");
  if (Object.values(baselines.scenarios).some((value) => value.status !== "public")) fail("baseline evidence must be explicitly public");
  const expectedHashes = await expectedRealHashes();
  await assertDeclaredMedia(expectedHashes);
  await assertStaticAllowlist();
  await rm(output, { recursive: true, force: true });
  await mkdir(output, { recursive: true });
  for (const relative of STATIC_FILES) {
    const source = path.join(PUBLIC, relative);
    await assertedRegularFile(source, PUBLIC);
    const destination = path.join(output, relative);
    await mkdir(path.dirname(destination), { recursive: true });
    await cp(source, destination);
  }

  const publishedFrames = new Set();
  const summaries = [];
  for (const id of ids) {
    const document = await scenarioDocument({
      id,
      manifestPath: scenarioPaths.get(id),
      membership: benchmarkMembership.get(id),
      metadata,
      baseline: baselines.scenarios[id],
      expectedHashes,
      output,
      publishedFrames,
    });
    const detail = await publishJson(output, `scenario-catalog/v1/scenarios/${id}.json`, document, MAX_SCENARIO_BYTES);
    summaries.push({
      id,
      title: document.title,
      description: document.description,
      pack: document.pack,
      failure_modes: document.failure_modes,
      frames: document.media.frame_count,
      fps: document.media.fps,
      resolution: `${document.media.width}x${document.media.height}`,
      annotation_scope: document.annotations.policy.scope,
      license: document.provenance.source.license,
      detail: { url: detail.url, sha256: detail.sha256, bytes: detail.bytes },
    });
  }
  const catalog = {
    schema_version: "cvbench.scenario-catalog/v1",
    id: "cvbench-current-public-scenarios",
    version: metadata.catalog.version,
    status: "public",
    generated_from: { benchmark_manifests: BENCHMARK_FILES, derivation: "exact set union" },
    scenario_count: summaries.length,
    all_current_scenarios_public: true,
    disclosure: "All current scenarios, exact media, and full annotations are public and may be tuned to or memorized. Runtime isolation is an execution boundary, not a secrecy claim. Future hidden challenges are out of scope.",
    scenarios: summaries,
  };
  const catalogOutput = await publishJson(output, "scenario-catalog/v1/catalog.json", catalog, MAX_CATALOG_BYTES);
  await publishJson(output, ".well-known/cvbench-scenarios.json", {
    schema_version: "cvbench.scenario-discovery/v1",
    catalog_url: catalogOutput.url,
    catalog_sha256: catalogOutput.sha256,
    catalog_version: catalog.version,
    scenario_count: catalog.scenario_count,
    all_current_scenarios_public: true,
  }, MAX_CATALOG_BYTES);
  const evidence = await outputEvidence(output);
  await publishJson(output, "scenario-catalog/v1/build-evidence.json", {
    schema_version: "cvbench.scenario-catalog-build/v1",
    scenario_count: summaries.length,
    unique_frame_assets: publishedFrames.size,
    total_bytes_before_evidence: evidence.total_bytes,
    files: evidence.files,
  }, MAX_ASSET_BYTES);
  const final = await outputEvidence(output);
  process.stdout.write(`${JSON.stringify({ output, scenarios: summaries.length, unique_frames: publishedFrames.size, files: final.files.length, bytes: final.total_bytes })}\n`);
}

const invokedAsScript = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedAsScript) await main();

export { assertedRegularFile, jpegDimensions, outputEvidence, publishFrame, validateBox };
