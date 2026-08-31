# vid_splitter

<!-- concurrency smoke ticket 01 -->

Command-line tools for creating and testing BVF (Branched Video Format) files.

## End-to-End CLI Demo

Create a small test video:

```bash
mkdir -p /tmp/bvf-demo
ffmpeg -y \
  -f lavfi -i testsrc=size=320x180:rate=24:duration=6 \
  -f lavfi -i sine=frequency=440:duration=6 \
  -c:v libx264 -pix_fmt yuv420p -g 24 -keyint_min 24 \
  -c:a aac -b:a 96k -shortest \
  /tmp/bvf-demo/demo.mp4
```

Analyze it into the custom BVF file. `--demo-branch` is a lightweight deterministic path for local verification: it marks the middle third as mature and embeds playable fMP4/CMAF media assets without loading Whisper/Safety Checker.

```bash
python3 analyzer/analyze.py /tmp/bvf-demo/demo.mp4 --demo-branch --output-dir /tmp/bvf-demo
```

Resolve playback using user JSON:

```bash
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/child_user.json --dry-run
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/adult_user.json --dry-run
```

List the resolved sequence as machine-readable JSON:

```bash
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/child_user.json --list --json
```

Export the resolved streams:

```bash
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/child_user.json --export /tmp/bvf-demo/child.mp4
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/adult_user.json --export /tmp/bvf-demo/adult.mp4
```

Validate the BVF itself:

```bash
python3 tools/bvf_probe.py /tmp/bvf-demo/demo.bvf --profile child --json
```

Validate a resolved export against the selected profile timeline:

```bash
python3 tools/bvf_probe.py /tmp/bvf-demo/demo.bvf --profile child --verify-export /tmp/bvf-demo/child.mp4 --json
```

Play directly with the reference player:

```bash
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/child_user.json
```

User JSON supports:

```json
{
  "birthday": "2016-01-01",
  "sex": "female",
  "profile_override": "child"
}
```

`profile_override` is optional. Without it, the player resolves profiles from birthday and sex: child under 13, teen_m/teen_f under 18, adult otherwise.

## BVF Validation

`tools/bvf_probe.py` is the validator CLI for production-style BVF outputs.

Prerequisites:

- `ffmpeg`
- `ffprobe`

It now checks:

- structural offsets and index/header consistency
- asset block integrity and segment-id matching
- embedded fMP4/CMAF media probeability
- duration consistency between BVF index entries and extracted media assets
- keyframe-boundary alignment for embedded video assets
- profile resolution for `play`, `swap`, and `skip` actions
- optional exported MP4 verification against the resolved profile timeline

## Tests

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_bvf_muxer.py tools/test_bvf_player.py tests/test_cli_e2e.py
```

## Jellyfin Smoke

Use the Jellyfin smoke workflow to verify the production path end to end: select a short real video, generate a `.bvf` in the library folder, validate it locally, refresh Jellyfin discovery, and confirm that the `.bvf` appears as a library item with Smart Branch sources.

Required environment variables:

- `JELLYFIN_BASE_URL`
- `JELLYFIN_API_KEY`

Supported authentication alternative:

- `JELLYFIN_USERNAME`
- `JELLYFIN_PASSWORD`

Optional environment variables:

- `JELLYFIN_SHORTS_DIR`
  Defaults to `/mnt/hdds/Videos/shorts` when unset.
- `RUN_JELLYFIN_SMOKE=1`
  Required only for the real-environment pytest integration test.

Example invocation:

```bash
export JELLYFIN_BASE_URL="http://jellyfin.local:8096"
export JELLYFIN_API_KEY="your-api-key"
export JELLYFIN_SHORTS_DIR="/mnt/hdds/Videos/shorts"
python3 tools/jellyfin_smoke.py --dry-run
```

Optional profile-specific playback check:

```bash
python3 tools/jellyfin_smoke.py --dry-run --profile child
```

Skip behavior:

- If `JELLYFIN_BASE_URL` is missing, or neither `JELLYFIN_API_KEY` nor `JELLYFIN_USERNAME`/`JELLYFIN_PASSWORD` is set, the smoke workflow exits with a skip-style message instead of attempting network calls.
- If no shorts directory is available, or no supported video exists in that directory, the workflow skips with a message that names the missing path or configuration.
- The real pytest smoke test is opt-in. Without `RUN_JELLYFIN_SMOKE=1`, `tests/test_jellyfin_smoke.py::test_real_jellyfin_smoke_workflow_opt_in` skips by design.

For Jellyfin plugin build/test commands, see `csharp_plugin/README.md`.

Optional real-media smoke checks are intentionally gated because they require
local media fixtures plus `ffmpeg` / `ffprobe`:

```bash
RUN_REAL_ANALYZER_TESTS=1 env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_real_video_integration.py -rs
```
