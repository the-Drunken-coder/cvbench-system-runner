import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { createApp } from "../src/app.js";
import { MemoryStore } from "./memory-store.js";

const SUBMISSION_KEY = "submission-key-with-enough-entropy";
const RUNNER_TOKEN = "runner-token-with-enough-entropy";
const OPERATOR_TOKEN = "operator-token-with-enough-entropy";
const IMAGE = `ghcr.io/example/tracker@sha256:${"a".repeat(64)}`;
let app;
let store;

beforeEach(() => {
  store = new MemoryStore();
  app = createApp({
    store,
    submissionKeys: SUBMISSION_KEY,
    runnerToken: RUNNER_TOKEN,
    operatorToken: OPERATOR_TOKEN,
    maxSubmissionsPerHour: 2,
    leaseSeconds: 3000,
  });
});

test("health and machine-readable metadata are public", async () => {
  assert.equal((await request("/api/v1/health")).status, 200);
  const contract = await jsonRequest("/api/v1/contract");
  assert.equal(contract.container.network, "disabled");
  assert.match(contract.container.image, /sha256/);
  const openapi = await jsonRequest("/api/v1/openapi.json");
  assert.equal(openapi.openapi, "3.1.0");
});

test("submission requires authentication and strict immutable input", async () => {
  assert.equal((await submit(validBody(), "idem-key-0001", "wrong")).status, 401);
  assert.equal((await submit({ ...validBody(), image: "ghcr.io/example/tracker:latest" }, "idem-key-0001")).status, 422);
  assert.equal((await submit({ ...validBody(), command: "curl bad | sh" }, "idem-key-0001")).status, 422);
  assert.equal((await submit({ ...validBody(), argv: "python tracker.py" }, "idem-key-0001")).status, 422);
  assert.equal((await submit(validBody(), "short")).status, 400);
});

test("submission create, public read, idempotent replay, lease, and scored result lifecycle", async () => {
  const createdResponse = await submit(validBody(), "baseline-safe-0001");
  assert.equal(createdResponse.status, 201);
  const created = await createdResponse.json();
  assert.equal(created.status, "queued");
  assert.equal(created.contact, undefined);

  const replayResponse = await submit(validBody(), "baseline-safe-0001");
  assert.equal(replayResponse.status, 200);
  assert.equal(replayResponse.headers.get("idempotency-replayed"), "true");
  assert.equal((await replayResponse.json()).id, created.id);

  const conflict = await submit({ ...validBody(), model_version: "2" }, "baseline-safe-0001");
  assert.equal(conflict.status, 409);

  const publicQueued = await jsonRequest(`/api/v1/submissions/${created.id}`);
  assert.equal(publicQueued.model.image, IMAGE);
  assert.equal(publicQueued.status, "queued");

  assert.equal((await lease("wrong")).status, 401);
  const leaseResponse = await lease();
  assert.equal(leaseResponse.status, 200);
  const leased = await leaseResponse.json();
  assert.equal(leased.submission.id, created.id);
  assert.equal(leased.submission.attempt, 1);
  assert.equal(leased.lease.max_result_bytes, 1024 * 1024);
  assert.equal((await lease()).status, 204);

  const badCallback = await result(created.id, { status: "succeeded", lease_token: "x".repeat(64), report: scoredReport() });
  assert.equal(badCallback.status, 409);
  const completedResponse = await result(created.id, {
    status: "succeeded",
    lease_token: leased.lease.token,
    report: scoredReport(),
  });
  assert.equal(completedResponse.status, 200);
  const completed = await completedResponse.json();
  assert.equal(completed.status, "succeeded");
  assert.equal(completed.result.scores.sample_counts.matches, 12);
  assert.equal((await result(created.id, { status: "failed", lease_token: leased.lease.token, error: "late" })).status, 409);
});

test("failed jobs require a bounded error and valid running lease", async () => {
  const created = await (await submit(validBody(), "failed-job-0001")).json();
  const leased = await (await lease()).json();
  assert.equal((await result(created.id, { status: "failed", lease_token: leased.lease.token })).status, 422);
  const response = await result(created.id, { status: "failed", lease_token: leased.lease.token, error: "container failed readiness" });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).error, "container failed readiness");
});

test("result callback payloads cannot exceed the advertised budget", async () => {
  const created = await (await submit(validBody(), "oversized-result-0001")).json();
  const leased = await (await lease()).json();
  const response = await result(created.id, {
    status: "succeeded",
    lease_token: leased.lease.token,
    report: { diagnostics: { sut_stderr: ["x".repeat(1024 * 1024)] } },
  });
  assert.equal(response.status, 413);
  assert.equal((await jsonRequest(`/api/v1/submissions/${created.id}`)).status, "running");
});

test("operator API is separate from public and runner credentials", async () => {
  const created = await (await submit(validBody(), "operator-job-0001")).json();
  assert.equal((await request("/api/v1/operator/jobs")).status, 401);
  assert.equal((await request("/api/v1/operator/jobs", { headers: { authorization: `Bearer ${RUNNER_TOKEN}` } })).status, 401);

  const leased = await (await lease()).json();
  await result(created.id, { status: "succeeded", lease_token: leased.lease.token, report: scoredReport() });
  const headers = { authorization: `Bearer ${OPERATOR_TOKEN}` };
  const list = await (await request("/api/v1/operator/jobs?status=succeeded", { headers })).json();
  assert.equal(list.schema_version, "cvbench.operator/v1");
  assert.equal(list.jobs[0].model.image, IMAGE);
  assert.equal(list.jobs[0].diagnostics.failure_reason, null);
  const filtered = await (await request("/api/v1/operator/jobs?model=does-not-exist", { headers })).json();
  assert.equal(filtered.jobs.length, 0);
  const detail = await (await request(`/api/v1/operator/jobs/${created.id}`, { headers })).json();
  assert.equal(detail.raw_result.metrics.sample_counts.matches, 12);
  assert.equal(detail.raw_result.diagnostics.sut_stderr[0], "<script>throw new Error('untrusted')</script>");
  const audit = await (await request(`/api/v1/operator/jobs/${created.id}/audit`, { headers })).json();
  assert.equal(audit.automatic_disqualification, false);
  const note = await (await request(`/api/v1/operator/jobs/${created.id}/notes`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ verdict: "accepted", note: "<script>untrusted note</script> Baseline evidence reviewed." }),
  })).json();
  assert.equal(note.verdict, "accepted");
  assert.equal((await request(`/api/v1/operator/jobs/${created.id}/notes`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ verdict: "accepted", note: "x".repeat(10_000) }),
  })).status, 413);
  assert.equal((await request(`/api/v1/operator/jobs/${created.id}/notes`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ verdict: "forged", note: "not allowed" }),
  })).status, 422);
  const notes = await (await request(`/api/v1/operator/jobs/${created.id}/notes`, { headers })).json();
  assert.equal(notes.notes[0].note, "<script>untrusted note</script> Baseline evidence reviewed.");
  const publicResult = await jsonRequest(`/api/v1/submissions/${created.id}`);
  assert.equal(publicResult.result.scores.sample_counts.matches, 12);
  assert.equal(publicResult.result.diagnostics, undefined);
  assert.equal((await request("/api/v1/operator/jobs?cursor=bad", { headers })).status, 400);
});

test("hourly limits and payload limits are enforced", async () => {
  assert.equal((await submit(validBody(), "rate-limit-0001")).status, 201);
  assert.equal((await submit(validBody(), "rate-limit-0002")).status, 201);
  const limited = await submit(validBody(), "rate-limit-0003");
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get("retry-after"), "3600");

  const oversized = await submit({ ...validBody(), notes: "x".repeat(20_000) }, "oversized-0001");
  assert.equal(oversized.status, 413);
});

test("concurrent submissions cannot exceed the hourly limit", async () => {
  const responses = await Promise.all(
    Array.from({ length: 20 }, (_, index) => submit(validBody(), `concurrent-rate-${String(index).padStart(4, "0")}`)),
  );
  const statuses = responses.map((response) => response.status);
  assert.equal(statuses.filter((status) => status === 201).length, 2);
  assert.equal(statuses.filter((status) => status === 429).length, 18);
});

test("expired leases are requeued and stale callbacks are rejected", async () => {
  const created = await (await submit(validBody(), "lease-expiry-0001")).json();
  const first = await (await lease()).json();
  const row = store.rows.get(created.id);
  row.leaseExpiresAt = 1;
  const second = await (await lease()).json();
  assert.equal(second.submission.attempt, 2);
  assert.notEqual(second.lease.token, first.lease.token);
  assert.equal((await result(created.id, { status: "succeeded", lease_token: first.lease.token, report: scoredReport() })).status, 409);
});

test("an expired lease cannot complete before maintenance requeues it", async () => {
  const created = await (await submit(validBody(), "expired-callback-0001")).json();
  const leased = await (await lease()).json();
  store.rows.get(created.id).leaseExpiresAt = 1;
  const callback = await result(created.id, {
    status: "succeeded",
    lease_token: leased.lease.token,
    report: scoredReport(),
  });
  assert.equal(callback.status, 409);
  assert.equal((await jsonRequest(`/api/v1/submissions/${created.id}`)).status, "running");
});

test("the configured lease accepts a callback through the full 3000-second budget", async () => {
  const realDateNow = Date.now;
  const startedAt = realDateNow();
  try {
    Date.now = () => startedAt;
    const created = await (await submit(validBody(), "lease-budget-0001")).json();
    const leased = await (await lease()).json();
    assert.equal(Date.parse(leased.lease.expires_at) - Math.floor(startedAt / 1000) * 1000, 3_000_000);

    Date.now = () => Date.parse(leased.lease.expires_at);
    const callback = await result(created.id, {
      status: "succeeded",
      lease_token: leased.lease.token,
      report: scoredReport(),
    });
    assert.equal(callback.status, 200);
  } finally {
    Date.now = realDateNow;
  }
});

function validBody() {
  return {
    image: IMAGE,
    argv: ["python", "-m", "tracker"],
    name: "Safe baseline",
    model_version: "1",
    contact: "private@example.invalid",
  };
}

function scoredReport() {
  return {
    outcome: { status: "completed" },
    metrics: { sample_counts: { matches: 12 }, identity: { id_switches: 0 } },
    runtime_isolation: { status: "verified", network_mode: "none" },
    diagnostics: { sut_stderr: ["<script>throw new Error('untrusted')</script>"] },
  };
}

function request(path, options) {
  return app.fetch(new Request(`https://cvbench.test${path}`, options));
}

async function jsonRequest(path, options) {
  return (await request(path, options)).json();
}

function submit(body, idempotencyKey, token = SUBMISSION_KEY) {
  return request("/api/v1/submissions", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      "idempotency-key": idempotencyKey,
    },
    body: JSON.stringify(body),
  });
}

function lease(token = RUNNER_TOKEN) {
  return request("/api/v1/internal/leases", { method: "POST", headers: { authorization: `Bearer ${token}` } });
}

function result(id, body, token = RUNNER_TOKEN) {
  return request(`/api/v1/internal/submissions/${id}/result`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
