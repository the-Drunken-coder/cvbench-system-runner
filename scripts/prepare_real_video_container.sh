#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
image=${CVBENCH_REAL_VIDEO_PREP_IMAGE:-cvbench-real-video-prep:v1}

docker image inspect "$image" >/dev/null 2>&1 || {
  echo "missing preparation image $image; build examples/Dockerfile.real-video-prep first" >&2
  exit 2
}

docker run --rm \
  --network bridge \
  --user "$(id -u):$(id -g)" \
  --env CVBENCH_PREP_CONTAINER=1 \
  --mount "type=bind,src=$repo_root,dst=/workspace" \
  --workdir /workspace \
  --entrypoint /bin/sh \
  "$image" \
  -c 'python /opt/cvbench/scripts/prepare_real_video.py --repo-root /workspace "$@" && python /opt/cvbench/scripts/verify_real_video_corpus.py --repo-root /workspace' \
  -- "$@"
