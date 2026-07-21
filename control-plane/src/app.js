const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const MAX_SUBMISSION_BYTES = 16 * 1024;
const MAX_RESULT_BYTES = 1024 * 1024;
const IMAGE_PATTERN = /^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?\/)?[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[a-f0-9]{64}$/;
const ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export function createApp(options) {
  const config = {
    store: options.store,
    assets: options.assets,
    submissionKeys: splitKeys(options.submissionKeys),
    runnerToken: String(options.runnerToken || ""),
    maxSubmissionsPerHour: boundedInteger(options.maxSubmissionsPerHour, 20, 1, 1000),
    leaseSeconds: boundedInteger(options.leaseSeconds, 3000, 60, 7200),
  };

  return {
    async fetch(request) {
      try {
        return await route(request, config);
      } catch (error) {
        console.error("unhandled request error", error);
        return problem(500, "internal_error", "The control plane could not process the request.");
      }
    },
  };
}

async function route(request, config) {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/v1/")) return serveAsset(request, config.assets);

  if (request.method === "GET" && url.pathname === "/api/v1/health") {
    try {
      await config.store.health();
      return json({ status: "ok", service: "cvbench-control-plane", version: "v1" });
    } catch {
      return problem(503, "database_unavailable", "The queue database is unavailable.");
    }
  }
  if (request.method === "GET" && url.pathname === "/api/v1/contract") return json(CONTRACT);
  if (request.method === "GET" && url.pathname === "/api/v1/openapi.json") return json(OPENAPI);

  if (request.method === "POST" && url.pathname === "/api/v1/submissions") {
    const token = bearerToken(request);
    if (!(await authorized(token, config.submissionKeys))) return unauthorized("submission API key");
    const parsed = await readJson(request, MAX_SUBMISSION_BYTES);
    if (parsed.error) return parsed.error;
    const validation = validateSubmission(parsed.value);
    if (validation.error) return problem(422, "invalid_submission", validation.error);
    const idempotencyKey = request.headers.get("idempotency-key") || "";
    if (!/^[A-Za-z0-9._:-]{8,128}$/.test(idempotencyKey)) {
      return problem(400, "invalid_idempotency_key", "Idempotency-Key must be 8-128 safe ASCII characters.");
    }

    const now = unixTime();
    const requestHash = await sha256(stableJson(validation.value));
    const submitterKeyHash = await sha256(token);
    const result = await config.store.createSubmission(
      {
        id: crypto.randomUUID(),
        ...validation.value,
        idempotencyKey,
        requestHash,
        submitterKeyHash,
        now,
      },
      config.maxSubmissionsPerHour,
    );
    if (result.kind === "conflict") {
      return problem(409, "idempotency_conflict", "This Idempotency-Key was already used for a different request.");
    }
    if (result.kind === "rate_limited") {
      return problem(429, "rate_limited", "This API key has reached its hourly submission limit.", { "retry-after": "3600" });
    }
    return json(publicSubmission(result.submission), result.kind === "created" ? 201 : 200, {
      location: `/api/v1/submissions/${result.submission.id}`,
      "idempotency-replayed": result.kind === "replay" ? "true" : "false",
    });
  }

  const submissionMatch = url.pathname.match(/^\/api\/v1\/submissions\/([^/]+)$/);
  if (request.method === "GET" && submissionMatch) {
    const id = submissionMatch[1];
    if (!ID_PATTERN.test(id)) return problem(404, "not_found", "Submission not found.");
    const submission = await config.store.getSubmission(id);
    return submission ? json(publicSubmission(submission)) : problem(404, "not_found", "Submission not found.");
  }

  if (request.method === "POST" && url.pathname === "/api/v1/internal/leases") {
    if (!(await authorized(bearerToken(request), [config.runnerToken]))) return unauthorized("runner token");
    const leaseToken = randomToken();
    const now = unixTime();
    const submission = await config.store.leaseJob({
      now,
      leaseExpiresAt: now + config.leaseSeconds,
      leaseTokenHash: await sha256(leaseToken),
    });
    if (!submission) return new Response(null, { status: 204 });
    return json({
      submission: runnerSubmission(submission),
      lease: {
        token: leaseToken,
        expires_at: iso(submission.leaseExpiresAt),
        result_url: `${url.origin}/api/v1/internal/submissions/${submission.id}/result`,
        max_result_bytes: MAX_RESULT_BYTES,
      },
    });
  }

  const resultMatch = url.pathname.match(/^\/api\/v1\/internal\/submissions\/([^/]+)\/result$/);
  if (request.method === "POST" && resultMatch) {
    if (!(await authorized(bearerToken(request), [config.runnerToken]))) return unauthorized("runner token");
    if (!ID_PATTERN.test(resultMatch[1])) return problem(404, "not_found", "Submission not found.");
    const parsed = await readJson(request, MAX_RESULT_BYTES);
    if (parsed.error) return parsed.error;
    const validation = validateResult(parsed.value);
    if (validation.error) return problem(422, "invalid_result", validation.error);
    const completed = await config.store.completeJob({
      id: resultMatch[1],
      leaseTokenHash: await sha256(validation.value.leaseToken),
      status: validation.value.status,
      report: validation.value.report,
      error: validation.value.error,
      now: unixTime(),
    });
    return completed
      ? json(publicSubmission(completed))
      : problem(409, "invalid_transition", "The job is not running or the lease token is invalid or stale.");
  }

  if (request.method === "POST" && url.pathname === "/api/v1/internal/maintenance") {
    if (!(await authorized(bearerToken(request), [config.runnerToken]))) return unauthorized("runner token");
    const requeued = await config.store.requeueExpired(unixTime());
    return json({ requeued });
  }

  return problem(404, "not_found", "API route not found.");
}

async function serveAsset(request, assets) {
  if (!assets || !["GET", "HEAD"].includes(request.method)) return problem(404, "not_found", "Not found.");
  const response = await assets.fetch(request);
  const headers = new Headers(response.headers);
  headers.set("x-content-type-options", "nosniff");
  headers.set("referrer-policy", "strict-origin-when-cross-origin");
  headers.set("content-security-policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

function validateSubmission(value) {
  if (!isObject(value)) return { error: "Request body must be a JSON object." };
  if (unknownKeys(value, ["image", "argv", "name", "model_version", "contact", "notes"]).length) {
    return { error: `Unknown fields: ${unknownKeys(value, ["image", "argv", "name", "model_version", "contact", "notes"]).join(", ")}.` };
  }
  if (typeof value.image !== "string" || !IMAGE_PATTERN.test(value.image)) {
    return { error: "image must be a lowercase OCI reference pinned with @sha256:<64 lowercase hex characters>." };
  }
  if (!Array.isArray(value.argv) || value.argv.length < 1 || value.argv.length > 32) {
    return { error: "argv must be an array containing 1-32 arguments." };
  }
  for (const argument of value.argv) {
    if (typeof argument !== "string" || argument.length < 1 || argument.length > 256 || /[\0-\x1f\x7f]/.test(argument)) {
      return { error: "Each argv item must be a 1-256 character string without control characters." };
    }
  }
  const name = cleanText(value.name, "name", 1, 100);
  if (name.error) return name;
  const modelVersion = cleanText(value.model_version, "model_version", 1, 100);
  if (modelVersion.error) return modelVersion;
  const contact = optionalText(value.contact, "contact", 200);
  if (contact.error) return contact;
  const notes = optionalText(value.notes, "notes", 1000);
  if (notes.error) return notes;
  return {
    value: {
      image: value.image,
      argv: [...value.argv],
      name: name.value,
      modelVersion: modelVersion.value,
      contact: contact.value,
      notes: notes.value,
    },
  };
}

function validateResult(value) {
  if (!isObject(value)) return { error: "Request body must be a JSON object." };
  if (unknownKeys(value, ["status", "lease_token", "report", "error"]).length) return { error: "Result contains unknown fields." };
  if (!['succeeded', 'failed'].includes(value.status)) return { error: "status must be succeeded or failed." };
  if (typeof value.lease_token !== "string" || value.lease_token.length < 32 || value.lease_token.length > 200) {
    return { error: "lease_token is invalid." };
  }
  if (value.status === "succeeded" && !isObject(value.report)) return { error: "A succeeded result requires a report object." };
  if (value.status === "failed" && (typeof value.error !== "string" || value.error.length < 1 || value.error.length > 2000)) {
    return { error: "A failed result requires an error string of at most 2000 characters." };
  }
  return {
    value: {
      status: value.status,
      leaseToken: value.lease_token,
      report: value.status === "succeeded" ? value.report : null,
      error: value.status === "failed" ? value.error : null,
    },
  };
}

function publicSubmission(value) {
  return {
    id: value.id,
    status: value.status,
    model: { name: value.name, version: value.modelVersion, image: value.image, argv: value.argv },
    attempt: value.attempt,
    result: value.result,
    error: value.error,
    created_at: iso(value.createdAt),
    updated_at: iso(value.updatedAt),
    started_at: iso(value.startedAt),
    completed_at: iso(value.completedAt),
  };
}

function runnerSubmission(value) {
  return {
    id: value.id,
    image: value.image,
    argv: value.argv,
    model: { name: value.name, version: value.modelVersion },
    attempt: value.attempt,
  };
}

async function readJson(request, maxBytes) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > maxBytes) return { error: problem(413, "payload_too_large", `Payload limit is ${maxBytes} bytes.`) };
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > maxBytes) {
    return { error: problem(413, "payload_too_large", `Payload limit is ${maxBytes} bytes.`) };
  }
  try {
    return { value: JSON.parse(text) };
  } catch {
    return { error: problem(400, "invalid_json", "Request body must be valid JSON.") };
  }
}

async function authorized(candidate, expectedTokens) {
  if (!candidate || expectedTokens.length === 0 || expectedTokens.every((value) => !value)) return false;
  const candidateDigest = await digest(candidate);
  let match = 0;
  for (const token of expectedTokens) {
    const expectedDigest = await digest(token || "invalid-placeholder");
    match |= constantTimeEqual(candidateDigest, expectedDigest) ? 1 : 0;
  }
  return match === 1;
}

function constantTimeEqual(left, right) {
  let difference = left.length ^ right.length;
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) difference |= (left[index % left.length] ^ right[index % right.length]);
  return difference === 0;
}

async function digest(value) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
}

async function sha256(value) {
  return [...await digest(value)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function bearerToken(request) {
  const match = request.headers.get("authorization")?.match(/^Bearer ([!-~]{1,512})$/);
  return match ? match[1] : "";
}

function cleanText(value, name, minimum, maximum) {
  if (typeof value !== "string") return { error: `${name} must be a string.` };
  const clean = value.trim();
  if (clean.length < minimum || clean.length > maximum || /[\0-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(clean)) {
    return { error: `${name} must contain ${minimum}-${maximum} safe characters.` };
  }
  return { value: clean };
}

function optionalText(value, name, maximum) {
  if (value === undefined || value === null || value === "") return { value: null };
  return cleanText(value, name, 1, maximum);
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (isObject(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

function splitKeys(value) {
  return String(value || "").split(",").map((key) => key.trim()).filter(Boolean);
}

function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? fallback), 10);
  return Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum ? parsed : fallback;
}

function unknownKeys(value, allowed) {
  return Object.keys(value).filter((key) => !allowed.includes(key));
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function randomToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function unixTime() {
  return Math.floor(Date.now() / 1000);
}

function iso(timestamp) {
  return timestamp ? new Date(timestamp * 1000).toISOString() : null;
}

function unauthorized(kind) {
  return problem(401, "unauthorized", `A valid ${kind} is required.`, { "www-authenticate": "Bearer" });
}

function problem(status, code, message, headers = {}) {
  return json({ error: { code, message } }, status, headers);
}

function json(value, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(value, null, 2), { status, headers: { ...JSON_HEADERS, ...extraHeaders } });
}

export const CONTRACT = {
  schema_version: "cvbench.contract/v1",
  benchmark: {
    id: "persistent-target-tracking",
    version: "1.0.0",
    measures: ["accuracy", "robustness", "latency", "resource use", "diagnostics"],
    input: "Progressive timestamped JPEG frames over a Unix-domain socket; models never receive future frames.",
    temporal_support: "The socket stays open across ordered frames, so models may retain in-memory multi-frame state.",
  },
  container: {
    image: "Prebuilt OCI image pinned as registry/repository@sha256:<64 lowercase hex characters>.",
    platform: "linux/amd64",
    network: "disabled",
    filesystem: "container default plus one read/write socket directory mount at /run/cvbench",
    user: "unprivileged host-aligned numeric UID/GID",
    environment: { CVBENCH_INPUT_SOCKET: "/run/cvbench/input.sock" },
    readiness: "Print exactly CVBENCH_READY as a stdout line after connecting.",
    output: "Then print one cvbench.track/v1 JSON object per stdout line; diagnostics go to stderr.",
    resources: { cpus: 4, memory_mb: 2048 },
  },
  submission: {
    accepted: ["image", "argv", "name", "model_version", "contact", "notes"],
    rejected: ["source repositories", "build instructions", "shell command strings", "Docker socket access", "custom environment variables"],
    authentication: "Bearer submission API key plus a unique Idempotency-Key header.",
  },
};

export const OPENAPI = {
  openapi: "3.1.0",
  info: { title: "CVBench Control Plane API", version: "1.0.0", description: "Submit immutable model containers to the public CVBench queue." },
  servers: [{ url: "/" }],
  paths: {
    "/api/v1/health": { get: { operationId: "health", responses: { 200: { description: "Healthy" }, 503: { description: "D1 unavailable" } } } },
    "/api/v1/contract": { get: { operationId: "getContract", responses: { 200: { description: "Benchmark and container contract" } } } },
    "/api/v1/openapi.json": { get: { operationId: "getOpenApi", responses: { 200: { description: "This document" } } } },
    "/api/v1/submissions": {
      post: {
        operationId: "createSubmission",
        security: [{ submissionKey: [] }],
        parameters: [{ name: "Idempotency-Key", in: "header", required: true, schema: { type: "string", minLength: 8, maxLength: 128 } }],
        requestBody: { required: true, content: { "application/json": { schema: { $ref: "#/components/schemas/CreateSubmission" } } } },
        responses: { 201: { description: "Queued" }, 200: { description: "Idempotent replay" }, 401: { description: "Unauthorized" }, 422: { description: "Invalid submission" }, 429: { description: "Hourly limit reached" } },
      },
    },
    "/api/v1/submissions/{id}": {
      get: {
        operationId: "getSubmission",
        parameters: [{ name: "id", in: "path", required: true, schema: { type: "string", format: "uuid" } }],
        responses: { 200: { description: "Public submission status/result" }, 404: { description: "Not found" } },
      },
    },
  },
  components: {
    securitySchemes: { submissionKey: { type: "http", scheme: "bearer" } },
    schemas: {
      CreateSubmission: {
        type: "object",
        additionalProperties: false,
        required: ["image", "argv", "name", "model_version"],
        properties: {
          image: { type: "string", pattern: "@sha256:[a-f0-9]{64}$" },
          argv: { type: "array", minItems: 1, maxItems: 32, items: { type: "string", minLength: 1, maxLength: 256 } },
          name: { type: "string", maxLength: 100 },
          model_version: { type: "string", maxLength: 100 },
          contact: { type: "string", maxLength: 200 },
          notes: { type: "string", maxLength: 1000 },
        },
      },
    },
  },
};
