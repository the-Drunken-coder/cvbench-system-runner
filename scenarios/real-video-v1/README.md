# Real-video manifests

The three `scenario.yaml` files in this directory are checked-in contracts;
their frame and annotation paths point to the ignored, checksum-verified
prepared assets under `data/real-video-v1/`. Run
`python3 scripts/prepare_real_video.py --output data/real-video-v1` before
validation or scoring. The importer regenerates these manifests
deterministically and verifies `data/real-video-v1/artifacts.sha256`. Raw
media, prepared frames, and annotations remain ignored. The model container
receives only the owner-only frame socket; repository paths, media, and
ground truth are not mounted. The checked-in crowd overlay at
`rv1-a7f3/review/crowd-frames-16-20-overlay.jpg` is the manual QA record
for the corrected target annotations. Each selected frame contains a broad
`ignore_region` full-frame annotation for non-target content; scoreable target
matches are resolved first, and only then are unmatched predictions neutralized
using intersection-over-prediction-area.
