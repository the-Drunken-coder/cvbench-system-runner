# Example systems

`example-good-local.yaml` runs a deterministic classical OpenCV tracker. It decodes every delivered JPEG, segments the synthetic green targets, performs nearest-neighbor stateful association, emits tentative/confirmed/reacquired observations, and emits predicted/coasting updates during short gaps.

`example-broken-local.yaml` is intentionally bad while remaining schema-valid: it emits a new ID and a fixed false box on every frame. It exists to exercise diagnostics and regression reporting.

Build the container example from the repository root:

```bash
docker build -f examples/Dockerfile.good -t cvbench-example-good:v1 .
cvbench run --benchmark benchmarks/persistent-target-tracking.yaml \
  --system systems/example-good-docker.yaml --output runs/
```

The runner resolves and records the local image ID or repository digest before a scored Docker run. The container receives only the socket directory, has networking disabled, and never receives the source-video directory.
