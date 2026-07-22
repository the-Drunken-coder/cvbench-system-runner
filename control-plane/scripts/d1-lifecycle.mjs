import { readFile } from "node:fs/promises";

const baseUrl = required("CVBENCH_API_BASE_URL").replace(/\/$/, "");
const submissionKey = required("CVBENCH_API_KEY");
const runnerToken = required("CVBENCH_RUNNER_TOKEN");
const operatorReadToken = required("CVBENCH_OPERATOR_READ_TOKEN");
const operatorWriteToken = required("CVBENCH_OPERATOR_WRITE_TOKEN");
const report = JSON.parse(await readFile(required("CVBENCH_REPORT_PATH"), "utf8"));
const digest = "b".repeat(64);
const idempotencyKey = `safe-baseline-${crypto.randomUUID()}`;

assert(report.outcome?.status === "completed", "baseline report must be scored and completed");
assert(Number(report.metrics?.sample_counts?.matches) > 0, "baseline report must contain scored matches");

const createdResponse = await fetch(`${baseUrl}/api/v1/submissions`, {
  method: "POST",
  headers: {
    authorization: `Bearer ${submissionKey}`,
    "content-type": "application/json",
    "idempotency-key": idempotencyKey,
  },
  body: JSON.stringify({
    image: `ghcr.io/cvbench/safe-baseline@sha256:${digest}`,
    argv: ["python", "-m", "cvbench.examples.good_tracker"],
    name: "CVBench safe baseline lifecycle proof",
    model_version: "1.0.0",
  }),
});
await assertStatus(createdResponse, 201, "create");
const created = await createdResponse.json();

const replayResponse = await fetch(`${baseUrl}/api/v1/submissions`, {
  method: "POST",
  headers: {
    authorization: `Bearer ${submissionKey}`,
    "content-type": "application/json",
    "idempotency-key": idempotencyKey,
  },
  body: JSON.stringify({
    image: `ghcr.io/cvbench/safe-baseline@sha256:${digest}`,
    argv: ["python", "-m", "cvbench.examples.good_tracker"],
    name: "CVBench safe baseline lifecycle proof",
    model_version: "1.0.0",
  }),
});
await assertStatus(replayResponse, 200, "idempotent replay");
assert((await replayResponse.json()).id === created.id, "idempotent replay changed submission ID");

const leaseResponse = await fetch(`${baseUrl}/api/v1/internal/leases`, {
  method: "POST",
  headers: { authorization: `Bearer ${runnerToken}` },
});
await assertStatus(leaseResponse, 200, "lease");
const leased = await leaseResponse.json();
assert(leased.submission.id === created.id, "leased a different submission");

const callbackResponse = await fetch(`${baseUrl}/api/v1/internal/submissions/${created.id}/result`, {
  method: "POST",
  headers: { authorization: `Bearer ${runnerToken}`, "content-type": "application/json" },
  body: JSON.stringify({ status: "succeeded", lease_token: leased.lease.token, report }),
});
await assertStatus(callbackResponse, 200, "callback");

const publicResponse = await fetch(`${baseUrl}/api/v1/submissions/${created.id}`);
assert(publicResponse.status === 200, `public read returned ${publicResponse.status}`);
const completed = await publicResponse.json();
assert(completed.status === "succeeded", "submission did not reach succeeded");
assert(completed.result.scores.sample_counts.matches > 0, "public result lost scored matches");

const operatorHeaders = { authorization: `Bearer ${operatorReadToken}` };
const operatorResponse = await fetch(`${baseUrl}/api/v1/operator/jobs/${created.id}/audit`, { headers: operatorHeaders });
await assertStatus(operatorResponse, 200, "operator audit");
const audit = await operatorResponse.json();
assert(audit.automatic_disqualification === false, "audit flags must not disqualify automatically");
const evidenceResponse = await fetch(`${baseUrl}/api/v1/operator/jobs/${created.id}/evidence`, { headers: operatorHeaders });
await assertStatus(evidenceResponse, 200, "operator evidence");
const evidence = await evidenceResponse.json();
assert(evidence.audit_evidence?.schema_version === "cvbench.audit/v1", "audit evidence was not retrieved");
const noteResponse = await fetch(`${baseUrl}/api/v1/operator/jobs/${created.id}/notes`, {
  method: "POST",
  headers: { authorization: `Bearer ${operatorWriteToken}`, "content-type": "application/json" },
  body: JSON.stringify({ verdict: "accepted", note: "D1 lifecycle audit evidence retrieved." }),
});
await assertStatus(noteResponse, 201, "operator adjudication note");
const note = await noteResponse.json();
assert(note.actorId, "operator note is missing stable actor attribution");

console.log(JSON.stringify({
  submission_id: completed.id,
  status: completed.status,
  matched_samples: completed.result.scores.sample_counts.matches,
  benchmark_outcome: completed.result.outcome.status,
  audit_schema: evidence.audit_evidence.schema_version,
  flagged_review_aids: audit.flags.filter((flag) => flag.status === "flagged").map((flag) => flag.id),
  actor_id: note.actorId,
}));

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function assertStatus(response, expected, operation) {
  if (response.status !== expected) {
    throw new Error(`${operation} returned ${response.status}: ${await response.text()}`);
  }
}
