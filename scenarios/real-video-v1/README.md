# Real-video manifests

The three `scenario.yaml` files in this directory are checked-in contracts;
their frame and annotation paths point to the ignored, checksum-verified
prepared assets under `data/real-video-v1/`. Run
`python3 scripts/prepare_real_video.py --output data/real-video-v1` before
validation or scoring. The importer regenerates these manifests
deterministically.
