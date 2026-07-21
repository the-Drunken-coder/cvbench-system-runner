# Development and independent validation

## Fresh environment

```bash
python3 -m venv /tmp/cvbench-review-venv
source /tmp/cvbench-review-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cvbench validate --benchmark benchmarks/persistent-target-tracking.yaml \
  --system systems/example-good-local.yaml
pytest
ruff check .
```

## End-to-end evidence

```bash
cvbench run --benchmark benchmarks/persistent-target-tracking.yaml \
  --system systems/example-good-local.yaml --output /tmp/cvbench-review-runs
python -m json.tool /tmp/cvbench-review-runs/*/report.json >/dev/null
```

The good system must decode and score real JPEG frames. Run the broken system to verify failure detection:

```bash
cvbench run --benchmark benchmarks/persistent-target-tracking.yaml \
  --system systems/example-broken-local.yaml --output /tmp/cvbench-broken-runs
```

## Docker evidence

If a Docker daemon is available:

```bash
docker build -f examples/Dockerfile.good -t cvbench-example-good:v1 .
cvbench run --benchmark benchmarks/persistent-target-tracking.yaml \
  --system systems/example-good-docker.yaml --output /tmp/cvbench-docker-runs
```

The CI workflow covers install, lint, tests, scenario validation, and a real scored Linux Docker run. The Docker job builds the good image and asserts socket-only mounting, disabled networking, applied CPU/RAM limits, resource samples, an immutable image digest, scored matches, and complete container cleanup. Hardware accelerators are not required. NVIDIA collection degrades explicitly to unavailable.
