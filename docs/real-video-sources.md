# Real-video tranche 1

The tranche is a deliberately small, reproducible import pack. It contains
three short subclips from three Wikimedia Commons source files. The repository
stores source metadata and the importer, not raw media or generated frames.

## Sources and terms

| Source | Upstream terms and attribution | Pinned media evidence |
| --- | --- | --- |
| [Video Codec Test pedestrian area 1080p25.y4m.webm](https://commons.wikimedia.org/wiki/File:Video_Codec_Test_pedestrian_area_1080p25.y4m.webm) | CC0-1.0; Taurus Media Technik; static pedestrian-area camera | SHA-1 `51e89a672896e45cca17aa46cd223630a6266e26`; 1920x1080; 15.125 s; 375 decoded frames |
| [Cars driving at night.webm](https://commons.wikimedia.org/wiki/File:Cars_driving_at_night.webm) | CC BY 3.0; Editor / [YouTube user Editor](https://www.youtube.com/user/Editor) | SHA-1 `53fc56b2e6c053243f9ef377ada946abce4dcf63`; 1920x1080; 4,796 decoded frames |
| [Self driving cars - EU2016NL.webm](https://commons.wikimedia.org/wiki/File:Self_driving_cars_-_EU2016NL.webm) | CC BY 3.0; EU2016NL; Amsterdam self-driving-car demonstration | SHA-1 `6f26ef4a7bdb39ba361bf86563c82442dcdd2475`; 1920x1080; 2,998 decoded frames |

The two CC BY files are distributed by Commons under the attribution license;
the importer preserves the source page and attribution in `provenance.json`.
The CC0 file does not require attribution, but the source credit is retained
for provenance. The source media is not fetched implicitly by the benchmark.

## Preparation

Run this command from the repository root. It downloads the exact URLs pinned
in `scripts/prepare_real_video.py`, verifies SHA-1 before use, decodes the
selected frame ranges, strips source/container metadata by re-encoding JPEG,
and writes scenario manifests plus ground truth below the ignored
`data/real-video-v1/` directory.

```bash
python3 scripts/prepare_real_video.py --output data/real-video-v1
cvbench validate --benchmark benchmarks/real-video-v1.yaml \
  --system systems/real-video-baseline-local.yaml
```

The preparation transform is fixed: decoded frame ordinals are selected using
the source-frame ranges and strides in the importer, output JPEG quality is
90, and timestamps are normalized to 25 fps (`frame_stride * 40 ms`). The
source WebM files and generated JPEGs are ignored and are never mounted into a
SUT.

## Selected scenarios

| Opaque scenario | Source range | Failure mode captured |
| --- | --- | --- |
| `rv1-crowd-a7f3` | pedestrian frames 80–160, every 4th frame | persistent pedestrian identity in a dense crossing crowd; partial interference from close passers |
| `rv1-night-b2c8` | night highway frames 320–370, every 2nd frame | headlights, taillights, dark vehicles, glare, and adjacent traffic |
| `rv1-motion-c3d1` | Amsterdam frames 300–360, every 2nd frame | moving camera, rapid target scale change, and background motion |

The boxes are human-reviewed keyframes with deterministic linear interpolation
between anchors. The importer keeps original source-frame indices only in the
local provenance record; they are not put in frame metadata or ground truth
sent to the SUT. Scenario IDs, sequence IDs, and target IDs are opaque.

## Baseline evidence

The bundled baseline is intentionally classical: current/previous-frame
foreground detection and nearest-centre association. It receives only the
progressive socket stream and has no source path, annotation path, query box,
or future-frame access. Run it with:

```bash
python3 scripts/prepare_real_video.py --output data/real-video-v1
cvbench run --benchmark benchmarks/real-video-v1.yaml \
  --system systems/real-video-baseline-local.yaml \
  --output reports/real-video-v1
```

Each run directory contains the scored `report.json` and `report.html`, plus
collector-side output and matching decisions. The benchmark uses a fixed
evaluation-order seed, so repeated prepared runs have the same scenario
order and comparison fingerprint while avoiding public manifest-order cues.
