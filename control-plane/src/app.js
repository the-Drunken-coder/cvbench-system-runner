const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const MAX_SUBMISSION_BYTES = 16 * 1024;
const MAX_RESULT_BYTES = 1024 * 1024;
const MAX_OPERATOR_NOTE_BYTES = 8 * 1024;
const VALID_JOB_STATUSES = new Set(["queued", "running", "succeeded", "failed"]);
const IMAGE_PATTERN = /^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?\/)?[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[a-f0-9]{64}$/;
const ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export function createApp(options) {
  const config = {
    store: options.store,
    assets: options.assets,
    submissionKeys: splitKeys(options.submissionKeys),
    runnerToken: String(options.runnerToken || ""),
    operatorReadKeys: splitKeys(options.operatorReadKeys || options.operatorToken),
    operatorAdjudicatorCredentials: parseAdjudicatorCredentials(options.operatorAdjudicatorCredentials),
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
    const report = validation.value.report ? await authoritativeReport(validation.value.report) : null;
    const completed = await config.store.completeJob({
      id: resultMatch[1],
      leaseTokenHash: await sha256(validation.value.leaseToken),
      status: validation.value.status,
      report,
      resultSha256: report ? await sha256(canonicalJson(report)) : null,
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

  const operatorMatch = url.pathname.match(/^\/api\/v1\/operator\/jobs(?:\/([^/]+)(?:\/(audit|evidence|notes))?)?$/);
  if (operatorMatch) {
    const id = operatorMatch[1];
    const subresource = operatorMatch[2];
    const write = request.method === "POST" && subresource === "notes";
    const token = bearerToken(request);
    const actorId = write ? await actorForCredential(token, config.operatorAdjudicatorCredentials) : null;
    if (write ? !actorId : !(await authorized(token, config.operatorReadKeys))) {
      return unauthorized(write ? "adjudicator credential" : "operator read token");
    }
    if (!id) {
      if (request.method !== "GET") return problem(405, "method_not_allowed", "Only GET is supported for the operator job list.");
      const status = url.searchParams.get("status") || "";
      const model = url.searchParams.get("model") || "";
      const limit = boundedInteger(url.searchParams.get("limit"), 25, 1, 100);
      if (status && !VALID_JOB_STATUSES.has(status)) return problem(400, "invalid_status", "status must be queued, running, succeeded, or failed.");
      const cursor = parseCursor(url.searchParams.get("cursor"));
      if (url.searchParams.has("cursor") && !cursor) return problem(400, "invalid_cursor", "cursor must be a timestamp and UUID returned by the operator list.");
      const page = await config.store.listSubmissions({ status, model, limit, cursor });
      const comparisons = await config.store.operatorComparisons();
      return json({ schema_version: "cvbench.operator/v1", jobs: page.rows.map((row) => operatorSummary(row, comparisons)), next_cursor: encodeCursor(page.nextCursor), comparison: comparisonSummary(comparisons) });
    }
    if (!ID_PATTERN.test(id)) return problem(404, "not_found", "Job not found.");
    const row = await config.store.getSubmission(id);
    if (!row) return problem(404, "not_found", "Job not found.");
    if (request.method === "GET" && !subresource) return json(await operatorDetail(row, config.store));
    if (request.method === "GET" && subresource === "audit") return json(await operatorAudit(row, config.store));
    if (request.method === "GET" && subresource === "evidence") return json(operatorEvidence(row));
    if (request.method === "GET" && subresource === "notes") return json({ schema_version: "cvbench.operator/v1", notes: await config.store.listOperatorNotes(id) });
    if (request.method === "POST" && subresource === "notes") {
      const parsed = await readJson(request, MAX_OPERATOR_NOTE_BYTES);
      if (parsed.error) return parsed.error;
      const validation = validateOperatorNote(parsed.value);
      if (validation.error) return problem(422, "invalid_operator_note", validation.error);
      const note = await config.store.addOperatorNote({
        id: crypto.randomUUID(),
        submissionId: id,
        verdict: validation.value.verdict,
        note: validation.value.note,
        createdAt: unixTime(),
        operatorKeyHash: await sha256(token),
        actorId,
      });
      return json(note, 201);
    }
    return problem(405, "method_not_allowed", "Unsupported operator job operation.");
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

async function authoritativeReport(report) {
  const auditEvidence = isObject(report.audit_evidence) ? report.audit_evidence : null;
  return {
    ...report,
    provenance: {
      ...(isObject(report.provenance) ? report.provenance : {}),
      raw_evidence_available: false,
      bounded_audit_evidence_sha256: auditEvidence ? await sha256(canonicalJson(auditEvidence)) : null,
      bounded_audit_evidence_hash_algorithm: "sha256(cvbench.canonical-json/v1)",
    },
  };
}

function publicSubmission(value) {
  return {
    id: value.id,
    status: value.status,
    model: { name: value.name, version: value.modelVersion, image: value.image, argv: value.argv },
    attempt: value.attempt,
    result: publicResultSummary(value.result),
    error: value.error,
    created_at: iso(value.createdAt),
    updated_at: iso(value.updatedAt),
    started_at: iso(value.startedAt),
    completed_at: iso(value.completedAt),
  };
}

function operatorSummary(value, comparisons = null) {
  const report = value.result;
  const provenance = report?.provenance || {};
  const runner = report?.runner || report?.control_plane?.runner || {};
  const audit = report?.audit_evidence;
  const duplicateModel = duplicateStatus(comparisons, "duplicateImages", value.image);
  const duplicateResult = duplicateStatus(comparisons, "duplicateResults", value.resultSha256);
  return {
    id: value.id,
    status: value.status,
    model: { name: value.name, version: value.modelVersion, image: value.image, argv: value.argv },
    queue: {
      attempt: value.attempt,
      retries: Math.max(0, value.attempt - 1),
      created_at: iso(value.createdAt),
      updated_at: iso(value.updatedAt),
      started_at: iso(value.startedAt),
      completed_at: iso(value.completedAt),
      lease_expires_at: iso(value.leaseExpiresAt),
    },
    provenance: {
      benchmark: report?.benchmark || { id: "persistent-target-tracking", version: "1.0.0" },
      scenario_manifests: provenance.scenario_manifests || [],
      comparison_fingerprint: provenance.comparison_fingerprint || null,
      runner_commit: runner.commit || null,
    },
    checks: { workflow_run_url: runner.workflow_run_url || null, check_links: runner.check_links || [] },
    scores: scoreSummary(report),
    diagnostics: {
      failure_reason: value.error || report?.outcome?.errors?.[0] || null,
      finding_count: Array.isArray(report?.findings) ? report.findings.length : 0,
      audit_flag_count: Array.isArray(audit?.flags) ? audit.flags.filter((flag) => flag.status === "flagged").length : 0,
      duplicate_model_fingerprint: duplicateModel,
      duplicate_result_fingerprint: duplicateResult,
      comparison_scope: comparisons?.scope || "unknown",
    },
    result_available: Boolean(report),
  };
}

async function operatorDetail(value, store) {
  const comparisons = await store.operatorComparisons();
  return {
    schema_version: "cvbench.operator/v1",
    job: operatorSummary(value, comparisons),
    raw_result: value.result,
    notes: await store.listOperatorNotes(value.id),
  };
}

async function operatorAudit(value, store) {
  const report = value.result;
  const evidence = report?.audit_evidence || null;
  const flags = evidence?.flags ? [...evidence.flags] : [{ id: "run_pending", status: "pending", review_aid_only: true, reason: "No result has been recorded." }];
  if (report) {
    const perfect = scoreSummary(report).perfect;
    const comparisons = await store.operatorComparisons();
    const duplicateModel = duplicateStatus(comparisons, "duplicateImages", value.image);
    const duplicateResult = duplicateStatus(comparisons, "duplicateResults", value.resultSha256);
    flags.push({ id: "duplicate_model_fingerprint", status: duplicateModel === "review" ? "flagged" : duplicateModel, severity: "medium", review_aid_only: true, reason: duplicateModel === "review" ? "Another stored submission uses the same immutable model digest." : `Model comparison status: ${duplicateModel}.` });
    flags.push({ id: "duplicate_result_fingerprint", status: duplicateResult === "review" ? "flagged" : duplicateResult, severity: "medium", review_aid_only: true, reason: duplicateResult === "review" ? "Another stored result has the same canonical report fingerprint." : `Result comparison status: ${duplicateResult}.` });
    if (perfect) flags.push({ id: "score_review", status: "review", severity: "medium", review_aid_only: true, reason: "Perfect scores require human review, not automatic rejection." });
  }
  return {
    schema_version: "cvbench.audit/v1",
    disposition: "review_aid_only",
    automatic_disqualification: false,
    job_id: value.id,
    flags,
    score_components: scoreSummary(report),
    fairness: {
      counted_from: "deterministic frame/target matching",
      explainable_evidence: Boolean(evidence?.frame_samples?.length),
      adjudication: `/api/v1/operator/jobs/${value.id}/notes`,
    },
  };
}

function duplicateStatus(comparisons, field, value) {
  if (!comparisons || !value) return "unknown";
  if (comparisons[field].has(value)) return "review";
  return comparisons.truncated ? "unknown" : "clear";
}

function comparisonSummary(comparisons) {
  return { scope: comparisons.scope, truncated: comparisons.truncated };
}

function operatorEvidence(value) {
  const report = value.result || {};
  return {
    schema_version: "cvbench.audit/v1",
    job_id: value.id,
    audit_evidence: report.audit_evidence || null,
    artifacts: [],
    raw_evidence_available: report.provenance?.raw_evidence_available === true,
    bounded_audit_evidence_sha256: report.provenance?.bounded_audit_evidence_sha256 || null,
    bounded_audit_evidence_hash_algorithm: report.provenance?.bounded_audit_evidence_hash_algorithm || "sha256(cvbench.canonical-json/v1)",
    raw_artifact_policy: "Raw ground-truth and model-output artifacts are not uploaded or exposed by this public repository; only bounded authenticated audit evidence and integrity hashes are retained.",
  };
}

function scoreSummary(report) {
  const metrics = report?.metrics || {};
  const sampleCounts = metrics.sample_counts || {};
  return {
    sample_counts: sampleCounts,
    acquisition_rate: metrics.acquisition?.rate ?? null,
    observed_coverage: metrics.coverage?.overall_observed ?? null,
    continuity_coverage: metrics.coverage?.overall_continuity ?? null,
    mean_iou: metrics.localization?.mean_iou ?? null,
    id_switches: metrics.identity?.id_switches ?? null,
    false_track_births: metrics.false_detections?.track_births ?? null,
    reacquisition_same_id_rate: metrics.reacquisition?.same_id_rate ?? null,
    latency_p50_ms: metrics.latency?.median ?? null,
    latency_p99_ms: metrics.latency?.p99 ?? null,
    perfect: metrics.coverage?.overall_observed === 1 && metrics.localization?.mean_iou === 1 && metrics.identity?.id_switches === 0 && metrics.false_detections?.track_births === 0,
  };
}

function publicResultSummary(report) {
  if (!report) return null;
  return {
    outcome: report.outcome ? { status: report.outcome.status, exit_code: report.outcome.exit_code ?? null } : null,
    benchmark: report.benchmark || null,
    scores: scoreSummary(report),
    findings: Array.isArray(report.findings)
      ? report.findings.map((finding) => ({ finding_id: finding.finding_id, category: finding.category, severity: finding.severity, statement: finding.interpretation?.statement || null }))
      : [],
    provenance: {
      comparison_fingerprint: report.provenance?.comparison_fingerprint || null,
      resolved_container_image: report.provenance?.resolved_container_image || null,
    },
  };
}

function validateOperatorNote(value) {
  if (!isObject(value)) return { error: "Request body must be a JSON object." };
  if (unknownKeys(value, ["verdict", "note"]).length) return { error: "Only verdict and note are accepted." };
  if (!["unreviewed", "needs_review", "adjudicated", "accepted", "rejected"].includes(value.verdict)) return { error: "verdict is invalid." };
  const note = cleanText(value.note, "note", 1, 4000);
  if (note.error) return note;
  return { value: { verdict: value.verdict, note: note.value } };
}

function cleanActorId(value) {
  const actor = String(value || "unattributed-operator").trim().toLowerCase();
  return /^[A-Za-z0-9._:@/-]{1,100}$/.test(actor) && !/^(?:unattributed|legacy)(?:[._:@/-]|$)/i.test(actor) ? actor : null;
}

function parseAdjudicatorCredentials(value) {
  let parsed = value;
  if (typeof value === "string") {
    try {
      if (hasDuplicateJsonKeys(value)) return [];
      parsed = JSON.parse(value);
    } catch {
      return [];
    }
  }
  if (!isObject(parsed) || Array.isArray(parsed)) return [];
  const seenActors = new Set();
  const seenTokens = new Set();
  const credentials = [];
  for (const [actorId, token] of Object.entries(parsed)) {
    const cleanActor = cleanActorId(actorId);
    if (!cleanActor || seenActors.has(cleanActor) || typeof token !== "string" || token.length === 0 || seenTokens.has(token)) return [];
    seenActors.add(cleanActor);
    seenTokens.add(token);
    credentials.push({ actorId: cleanActor, token });
  }
  return credentials;
}

function hasDuplicateJsonKeys(value) {
  const seen = new Set();
  for (const match of value.matchAll(/"((?:\\.|[^"\\])*)"\s*:/g)) {
    const key = JSON.parse(`"${match[1]}"`);
    if (seen.has(key)) return true;
    seen.add(key);
  }
  return false;
}

function parseCursor(value) {
  if (!value) return null;
  const match = value.match(/^(\d+):([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/);
  return match ? { createdAt: Number(match[1]), id: match[2] } : null;
}

function encodeCursor(value) {
  return value ? `${value.createdAt}:${value.id}` : null;
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

async function actorForCredential(candidate, credentials) {
  if (!candidate || credentials.length === 0) return null;
  const candidateDigest = await digest(candidate);
  let actorId = null;
  let matches = 0;
  for (const credential of credentials) {
    const expectedDigest = await digest(credential.token || "invalid-placeholder");
    if (constantTimeEqual(candidateDigest, expectedDigest)) {
      matches += 1;
      actorId = credential.actorId;
    }
  }
  return matches === 1 ? actorId : null;
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

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (isObject(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

function stableJson(value) {
  return canonicalJson(value);
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
    "/api/v1/operator/jobs": {
      get: {
        operationId: "listOperatorJobs",
        security: [{ operatorReadKey: [] }],
        parameters: [
          { name: "status", in: "query", schema: { type: "string", enum: ["queued", "running", "succeeded", "failed"] } },
          { name: "model", in: "query", schema: { type: "string", maxLength: 100 } },
          { name: "limit", in: "query", schema: { type: "integer", minimum: 1, maximum: 100, default: 25 } },
          { name: "cursor", in: "query", schema: { type: "string" } },
        ],
        responses: { 200: { description: "Operator queue page with a stable next cursor" }, 401: { description: "Operator token required" } },
      },
    },
    "/api/v1/operator/jobs/{id}": {
      get: {
        operationId: "getOperatorJob",
        security: [{ operatorReadKey: [] }],
        parameters: [{ name: "id", in: "path", required: true, schema: { type: "string", format: "uuid" } }],
        responses: { 200: { description: "Operator detail and raw bounded report" }, 401: { description: "Operator token required" } },
      },
    },
    "/api/v1/operator/jobs/{id}/audit": {
      get: { operationId: "getOperatorAudit", security: [{ operatorReadKey: [] }], responses: { 200: { description: "Review-only anomaly flags and fairness explanation" } } },
    },
    "/api/v1/operator/jobs/{id}/evidence": {
      get: { operationId: "getOperatorEvidence", security: [{ operatorReadKey: [] }], responses: { 200: { description: "Bounded sampled frame evidence, integrity hash, and explicit raw-evidence availability" } } },
    },
    "/api/v1/operator/jobs/{id}/notes": {
      get: { operationId: "listOperatorNotes", security: [{ operatorReadKey: [] }], responses: { 200: { description: "Adjudication trail" } } },
      post: { operationId: "addOperatorNote", security: [{ operatorAdjudicatorKey: [] }], responses: { 201: { description: "Appended operator verdict note with configured actor attribution" } } },
    },
  },
  components: {
    securitySchemes: {
      submissionKey: { type: "http", scheme: "bearer" },
      operatorReadKey: { type: "http", scheme: "bearer", description: "Least-privilege operator read credential; never the submission, adjudicator, or runner token." },
      operatorAdjudicatorKey: { type: "http", scheme: "bearer", description: "Credential mapped to one stable actor identity; it cannot read operator routes and is never exposed or stored as a bearer value." },
    },
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
