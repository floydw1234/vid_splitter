# vid_splitter

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

Analyze it into the custom BVF file. `--demo-branch` is a lightweight deterministic path for local verification: it marks the middle third as mature and embeds playable media segment bytes without loading Whisper/Safety Checker.

```bash
python3 analyzer/analyze.py /tmp/bvf-demo/demo.mp4 --demo-branch --output-dir /tmp/bvf-demo
```

Resolve playback using user JSON:

```bash
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/child_user.json --dry-run
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/adult_user.json --dry-run
```

Export the resolved streams:

```bash
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/child_user.json --export /tmp/bvf-demo/child.mp4
python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/adult_user.json --export /tmp/bvf-demo/adult.mp4
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

## Tests

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_bvf_muxer.py tools/test_bvf_player.py tests/test_cli_e2e.py
```
