# Operator observability and auditability

CVBench has one control plane: the Cloudflare Worker, Static Assets, and D1. Submitted code is never executed there. A GitHub-hosted Linux runner leases one job and invokes the existing Docker-isolated engine.

## Trust boundaries

- Public submission keys can enqueue jobs; public reads expose only bounded summaries.
- `OPERATOR_READ_API_KEYS` is separate from both submission and runner credentials. `OPERATOR_ADJUDICATOR_API_KEYS` is a separate write credential, bound to the non-secret `OPERATOR_ACTOR_ID`; routes compare both in constant time and never expose bearer values.
- `RUNNER_TOKEN` can lease and callback but cannot read operator routes. Lease tokens are one-use, expiring, and stored only as digests.
- The runner strips control-plane/GitHub secrets from the benchmark subprocess. Docker has no network, no Docker socket, and only the progressive input socket mount.
- Raw JSONL, stderr, overlay videos, and other large evidence stay in trusted runner artifact storage. D1 receives bounded report JSON and the operator API returns bounded samples plus a 7-day authenticated artifact reference and manifest hash.
- Model output is untrusted text. It is JSON data in the API and is inserted with `textContent` in the console; it is never evaluated as JavaScript, HTML, shell, or an agent instruction.

## Stable operator routes

| Route | Use |
| --- | --- |
| `GET /api/v1/operator/jobs?status=running&model=...&limit=25&cursor=...` | queue/running/completed list and polling; follow `next_cursor` for the next page |
| `GET /api/v1/operator/jobs/:id` | admin detail, raw bounded report, provenance, checks, score, failures |
| `GET /api/v1/operator/jobs/:id/audit` | deterministic flags and fairness/adjudication shape |
| `GET /api/v1/operator/jobs/:id/evidence` | sampled overlays-equivalent frame records and evidence references |
| `GET /api/v1/operator/jobs/:id/notes` | operator verdict trail |
| `POST /api/v1/operator/jobs/:id/notes` | append `{verdict,note}` with actor attribution, without modifying scores; requires adjudicator credential |

All flags contain `review_aid_only: true`; none is an automatic disqualification. The evidence packet distinguishes observed and predicted support, states such as coasting/reacquired, matching decisions, occlusion/reacquisition, false-track segments, external collector latency, resource/isolation evidence, and reproducibility fingerprints. The evaluator marks unavailable telemetry as `not_observed` instead of silently treating it as a violation.

Fairness evidence keeps `matched` separate from `counted_toward_score`. Coverage components require an on-screen, detection-eligible row; continuity accepts any gated support for that eligible row; localization counts observed gated geometry, including an ineligible row when that component's metric includes it; acquisition requires an eligible observed confirmed/reacquired match. Each sampled row carries the per-component booleans and an explicit reason.

## Baseline proof

The local D1 lifecycle (`npm run test:d1`) creates an immutable baseline submission, exercises idempotent replay, leases it, posts a scored report, and reads the result. The in-memory API tests additionally prove operator-token separation, admin detail, audit retrieval, and note/verdict persistence. Legacy D1 rows receive `legacy-operator` attribution and no result hash, so duplicate-result status is `unknown` until a new callback records comparison data. The real baseline engine command is:

```bash
cvbench run --benchmark benchmarks/persistent-target-tracking.yaml \
  --system systems/example-good-local.yaml --output /tmp/cvbench-operator-baseline
```

Its `report.json` contains `audit_evidence` with bounded frame samples and flag evaluation. The trusted runner stages the raw `system-output.jsonl`, `ground-truth.jsonl`, `matching-decisions.jsonl`, resources CSV, and optional failure packets into a hashed manifest; the GitHub workflow uploads that directory as `cvbench-evidence-<run-id>` with 7-day retention. Local runs can use the same staging helper under `CVBENCH_ARTIFACT_ROOT`.
