# Public control plane

CVBench's public control plane is one Cloudflare Worker with Static Assets and D1. It validates and queues immutable model submissions; it never runs submitted code. A scheduled or manually dispatched GitHub-hosted Actions runner leases one job at a time and invokes the existing Docker-isolated CVBench engine.

```text
human or agent -> Worker API -> D1 queue
                       ^             |
                       | result      | lease
                       |             v
                 GitHub-hosted Linux runner -> Docker-isolated submitted image
```

The Worker source, site, migrations, and JavaScript tests live in `control-plane/`. The execution bridge is `scripts/run_control_plane_job.py`, and `.github/workflows/control-plane-runner.yml` schedules it.

## Security properties

- Submissions require `Authorization: Bearer ...` and accept only a prebuilt OCI image pinned by SHA-256, a bounded argv array, and bounded descriptive metadata.
- Source repositories, build steps, shell strings, environment variables, Docker socket access, and mutable image tags are rejected.
- Submission keys are compared through fixed-length SHA-256 digests with a constant-time byte comparison. D1 stores only the submitter-key digest.
- `Idempotency-Key` is unique per submitter-key digest. Repeating the same body returns the existing job; changing the body returns `409`.
- Public reads omit contact, notes, authentication data, lease data, raw model output, stderr, and raw evidence artifacts. They return score summaries and finding statements only.
- Operator reads use `OPERATOR_READ_API_KEYS`; adjudication writes use a separate `OPERATOR_ADJUDICATOR_API_KEYS` credential and stable non-secret `OPERATOR_ACTOR_ID`. Submission, runner, and read-only tokens cannot write notes. All bearer verification uses the same SHA-256 digest plus constant-time comparison path; only credential digests and the actor ID are stored, never bearer values.
- Operator flags are deterministic review aids. They never automatically disqualify a model; adjudication is an explicit note/verdict trail.
- A trusted runner bearer token protects leases and callbacks. Each lease also gets an independent random token, stored only as a digest, and state updates require `running -> succeeded|failed`. The 3000-second lease exceeds the 40-minute workflow timeout with callback margin.
- Each lease advertises the Worker's one-MiB result-body budget. The trusted runner preserves the complete scored report and deterministically retains head-and-tail stderr diagnostics that fit, recording original, retained, and omitted counts in the public result.
- Expired leases return to `queued` and can be attempted again. Old callback tokens stop working.
- The GitHub-hosted runner is ephemeral, has read-only repository permission, runs one job, and has no broad GitHub PAT in Cloudflare.
- Before invoking CVBench, the runner removes callback, Cloudflare, and GitHub secrets from the benchmark subprocess environment. The Docker adapter passes only `CVBENCH_INPUT_SOCKET` and explicitly submitted system configuration into the tested container.
- The existing Docker adapter enforces no network, no Docker socket, one socket-directory mount, a host-aligned unprivileged UID/GID, 4 CPUs, 2048 MB memory, and exact image identity verification. Every submitted container also gets a unique job label; both the runner and an `if: always()` workflow step force-remove and assert against survivors.

Public registry images are the zero-credential default. A manually operated runner may pre-authenticate Docker to a private registry, but registry credentials must never be added to submission metadata.

## Local, Docker-free Worker and site development

Node.js 20+ is required. Docker is not needed for the Worker, static site, D1 migration, or API lifecycle tests.

```bash
cd control-plane
npm ci
npm test
npx wrangler d1 migrations apply cvbench-control-plane --local
npx wrangler dev
```

Create `control-plane/.dev.vars` with local-only values (the file is ignored by Git):

```bash
SUBMISSION_API_KEYS="local-submission-key"
RUNNER_TOKEN="local-runner-token"
OPERATOR_READ_API_KEYS="local-operator-read-token"
OPERATOR_ADJUDICATOR_API_KEYS="local-operator-write-token"
OPERATOR_ACTOR_ID="local-operator"
```

Then open the local URL printed by Wrangler. The health, contract, and OpenAPI endpoints are:

```bash
curl -sS http://localhost:8787/api/v1/health
curl -sS http://localhost:8787/api/v1/contract
curl -sS http://localhost:8787/api/v1/openapi.json
```

`npm test` exercises a complete in-memory HTTP lifecycle: authenticated creation, idempotent replay, public read, lease, scored result callback, terminal-state rejection, failure callback, rate limit, payload limit, and lease expiry. It uses a safe baseline container reference and a representative scored CVBench report; it does not execute Docker.

With `wrangler dev` running and a real scored baseline `report.json`, the same lifecycle can be checked against local D1:

```bash
set -a
. ./.dev.vars
set +a
CVBENCH_API_BASE_URL=http://127.0.0.1:8787 \
CVBENCH_API_KEY="$SUBMISSION_API_KEYS" \
CVBENCH_RUNNER_TOKEN="$RUNNER_TOKEN" \
CVBENCH_OPERATOR_READ_TOKEN="$OPERATOR_READ_API_KEYS" \
CVBENCH_OPERATOR_WRITE_TOKEN="$OPERATOR_ADJUDICATOR_API_KEYS" \
CVBENCH_REPORT_PATH=/absolute/path/to/report.json \
npm run test:d1
```

The existing Linux CI `docker-scored-e2e` remains the execution-boundary proof: it builds `examples/Dockerfile.good`, runs the real benchmark engine, asserts a scored report, and checks the tested container is gone.

## Production with Cloudflare Workers Builds

Workers Builds Git integration is the deployment source of truth. Do not add a second GitHub deployment workflow.

In the Cloudflare dashboard:

1. Create or connect a Worker to `the-Drunken-coder/cvbench-system-runner`.
2. Set the root directory to `/control-plane`.
3. Set the build command to `npm ci`.
4. Set the deploy command to `npx wrangler deploy`.
5. Set the production branch to `main`. Leave branch builds enabled so pull-request branches receive preview versions.
6. Allow Wrangler to provision the D1 binding named `DB` from `wrangler.jsonc`; the narrowly scoped database name is `cvbench-control-plane`.
7. After first provisioning, apply the schema from the same root:

   ```bash
   npx wrangler d1 migrations apply cvbench-control-plane --remote
   ```

8. Add encrypted Worker secrets. Generate independent high-entropy values and retain the runner value for the matching GitHub Actions secret:

   ```bash
   npx wrangler secret put SUBMISSION_API_KEYS
   npx wrangler secret put RUNNER_TOKEN
   npx wrangler secret put OPERATOR_READ_API_KEYS
   npx wrangler secret put OPERATOR_ADJUDICATOR_API_KEYS
   ```

   `SUBMISSION_API_KEYS`, `OPERATOR_READ_API_KEYS`, and `OPERATOR_ADJUDICATOR_API_KEYS` accept comma-separated keys to allow rotation. Set the non-secret Worker variable `OPERATOR_ACTOR_ID` to the stable operator identity. Do not put bearer values in `wrangler.jsonc`, Actions variables, job metadata, PR text, or logs.

9. In GitHub repository settings, add the Actions variable `CVBENCH_API_BASE_URL` with the deployed `https://...workers.dev` origin. Create an environment named `cvbench-production`, restrict its deployment branches to `main` only, and put `CVBENCH_RUNNER_TOKEN` in that environment with exactly the same value as the Worker `RUNNER_TOKEN`. Do not keep a repository-level copy of this secret.
10. Manually dispatch **Trusted benchmark runner** once. The cron schedule checks for one queued job every 15 minutes.

Cloudflare account identifiers and API credentials remain dashboard/runtime configuration and are not committed.

## API lifecycle

Get the live contract before constructing a model:

```bash
curl -sS "$CVBENCH_API_BASE_URL/api/v1/contract"
```

Create a job using a digest returned by the registry, never a locally guessed digest:

```bash
curl -sS "$CVBENCH_API_BASE_URL/api/v1/submissions" \
  -H "Authorization: Bearer $CVBENCH_API_KEY" \
  -H "Idempotency-Key: tracker-v7-001" \
  -H "Content-Type: application/json" \
  --data '{
    "image":"ghcr.io/acme/tracker@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "argv":["python","-m","tracker"],
    "name":"Acme Temporal Tracker",
    "model_version":"7"
  }'
```

Poll the public `Location` returned by the create call. Status moves through `queued`, `running`, and one terminal state: `succeeded` with a score summary or `failed` with a bounded error. Complete report/evidence detail is operator-authenticated.

## Operator console and stable JSON API

The private dashboard is `/operator.html`. It polls JSON rather than scraping UI state. Keep the operator token in an environment variable or a local secret manager:

```bash
export CVBENCH_OPERATOR_READ_TOKEN='local-only-or-secret-manager-value'
curl -sS "$CVBENCH_API_BASE_URL/api/v1/operator/jobs?status=running" \
  -H "Authorization: Bearer $CVBENCH_OPERATOR_READ_TOKEN"

curl -sS "$CVBENCH_API_BASE_URL/api/v1/operator/jobs/$JOB_ID" \
  -H "Authorization: Bearer $CVBENCH_OPERATOR_READ_TOKEN"
curl -sS "$CVBENCH_API_BASE_URL/api/v1/operator/jobs/$JOB_ID/audit" \
  -H "Authorization: Bearer $CVBENCH_OPERATOR_READ_TOKEN"
curl -sS "$CVBENCH_API_BASE_URL/api/v1/operator/jobs/$JOB_ID/evidence" \
  -H "Authorization: Bearer $CVBENCH_OPERATOR_READ_TOKEN"
```

For a terminal-friendly watcher:

```bash
CVBENCH_POLL_MS=5000 node scripts/cvbench-operator.mjs watch "$JOB_ID"
node scripts/cvbench-operator.mjs audit "$JOB_ID"
```

The operator job shape includes queue timestamps, lease expiry, attempts/retries, exact OCI digest, benchmark/scenario and comparison fingerprints, runner commit and workflow link, score components, failure reasons, and audit-flag counts. `/audit` explains counted score components and marks every anomaly as `review_aid_only`; `/evidence` returns bounded frame samples, matching decisions, observed/predicted/coasting counts, occlusion/reacquisition events, false-track segments, resource/isolation evidence, and reproducibility inputs. Large JSONL/video artifacts remain runner-owned and must be exposed only through controlled expiring links recorded under `provenance.evidence_artifacts`.

Leave a fairness/adjudication trail without changing the score:

```bash
curl -sS -X POST "$CVBENCH_API_BASE_URL/api/v1/operator/jobs/$JOB_ID/notes" \
  -H "Authorization: Bearer $CVBENCH_OPERATOR_WRITE_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"verdict":"accepted","note":"Reviewed sampled overlays and latency evidence; legitimate result."}'
```

Model output is untrusted data. The dashboard renders it with DOM text nodes, never `innerHTML`; the API returns JSON and does not turn model text into shell, HTML, or prompts. The operator threat model covers credential separation, hidden annotations and source paths, runner tokens, artifact-link expiry, prompt-injection-like output, duplicate fingerprints, exact-ground-truth replay, impossible timestamps, unread input, and isolation violations. A flag is evidence for a human review, not guilt.

The protected runner endpoints are deliberately omitted from the public OpenAPI operations. Their implementation and workflow are public, but their bearer and lease tokens are not part of the model-submission interface.
