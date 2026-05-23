# Project Spec: Smart Branching Plugin for Jellyfin

## 1. Project Overview
**Goal:** Create a "Context-Aware" playback system for Jellyfin that filters mature content based on user profiles.
**Architecture:** Python CLI tool for pre-processing + C# Jellyfin plugin for playback + BVF self-contained container format.

**Workflow:**
1. **Pre-Processing (Python):** Run `python analyze.py "movie.mp4"` → generates `movie.bvf` (self-contained Branched Video Format file)
2. **Playback (C# Plugin):** Jellyfin plugin reads the BVF container, resolves segments per user profile, serves filtered stream through Jellyfin's native pipeline

## 2. Architecture
```
┌──────────────────────────────────────────────────────────────────┐
│  Python (CLI — run once per movie)                               │
│                                                                  │
│  analyze.py → generates:                                         │
│    - movie.bvf (self-contained BVF container with all segments)  │
│                                                                  │
│  Stack: Whisper (audio) + Safety Checker (NSFW) + FFmpeg         │
└──────────────────────────────────────────────────────────────────┘
                          │
                          │ drops .bvf file next to the movie
                          v
┌──────────────────────────────────────────────────────────────────┐
│  Jellyfin Server (.NET 9)                                         │
│                                                                  │
│  Smart Branching Plugin (C#):                                    │
│    1. On startup: scans library for .bvf files                   │
│    2. Registers "Smart Branch" virtual MediaSource per movie     │
│    3. On Play: reads BVF container, resolves segments for user   │
│    4. Serves resolved segments through Jellyfin's streaming pipe │
│    5. Mature segments → swap with filler or skip                 │
│                                                                  │
│  No external services. No Python server. Pure Jellyfin.          │
└──────────────────────────────────────────────────────────────────┘
```

## 3. Data Format: Branched Video Format (BVF)

The project uses **BVF** (`.bvf`) — a self-contained binary container format.
See [BVF_SPEC.md](BVF_SPEC.md) for the full specification.

**Key properties:**
- One file holds everything: all segments (main + fillers + alternates), manifest, and index
- File layout: 64-byte header → segment index (40-byte fixed entries) → zstd-compressed JSON manifest → sequential segment data blocks
- O(1) random access by byte offset via segment index
- Profile-aware: multiple viewer profiles with different tag filters in one file
- Keyframe-aligned: every segment boundary is an IDR/keyframe for seamless seeking
- Streamable: index lives near the front, playback can start before full download

**Manifest schema (zstd-compressed JSON):**
```json
{
  "bvf_version": "1.0",
  "movie_id": "tt1234567",
  "title": "Example Movie",
  "duration_ms": 7200000,
  "profiles": {
    "child": { "label": "Child (under 13)", "filters": ["nudity", "violence", "language", "fear", "gore"] },
    "teen":  { "label": "Teen (13–17)",     "filters": ["nudity", "gore"] },
    "adult": { "label": "Adult (18+)",       "filters": [] }
  },
  "segments": [
    {
      "id": "seg_001",
      "start_ms": 0, "end_ms": 300000,
      "tags": [], "risk": "safe",
      "profiles": {
        "child": { "action": "play", "segment_id": "seg_001" },
        "teen":  { "action": "play", "segment_id": "seg_001" },
        "adult": { "action": "play", "segment_id": "seg_001" }
      }
    },
    {
      "id": "seg_002",
      "start_ms": 300000, "end_ms": 345000,
      "tags": ["violence", "gore"], "risk": "mature",
      "profiles": {
        "child": { "action": "swap", "segment_id": "filler_001" },
        "teen":  { "action": "swap", "segment_id": "filler_001" },
        "adult": { "action": "play",  "segment_id": "seg_002" }
      }
    }
  ],
  "chapters": [...]
}
```

**Segment actions:** `play`, `swap`, `skip`, `mute`, `blur` (future)

## 4. Component 1: The Analyzer (Python CLI)
**File:** `analyzer/analyze.py`
**Purpose:** One-time pre-processing tool. Run manually or via cron per movie.

### Dependencies
* `ffmpeg-python` — frame extraction and video probing
* `openai-whisper` — timestamped audio transcription with word-level timing
* `diffusers` + `safety-checker` — NSFW image classification
* `torch`, `transformers`, `torchvision`, `accelerate`
* `Pillow`, `numpy`, `zstandard`

### Logic Flow
1. **Input:** Path to `.mp4` file
2. **Audio Analysis:** Whisper with `word_timestamps=True` → flag profanity by time
3. **Visual Analysis:** FFmpeg extracts 1 frame every 5s → Safety Checker flags NSFW
4. **Segment Merging:** Combine overlapping detections into contiguous segments, fill gaps
5. **Keyframe Snapping:** Snap segment boundaries to nearest keyframe (IDR for H.264/HEVC)
6. **Output:** `movie.bvf` — self-contained BVF container with all segments + manifest

### CLI Usage
```bash
python analyze.py "path/to/movie.mp4" --model base --threshold 0.6 --interval 5
```

## 4b. Marlin-2B Integration (Phase 1b — Alternative Analyzer Path)

Marlin-2B is a potential replacement or alternative for the analyzer's content understanding layer, which currently uses separate Whisper audio transcription and Safety Checker visual classification passes.

### Why
Marlin-2B is a 2B-parameter video VLM that can answer "what is happening and when?" in a single pass over the video. Instead of separately extracting audio transcripts for language and sampled frames for NSFW detection, the analyzer can use Marlin-2B for unified dense captioning and temporal grounding.

### What It Does
Marlin-2B produces structured scene/event captions with second-precise timestamps. That output maps directly onto the BVF manifest's segment model:
- Caption/event timestamps → `start_ms` / `end_ms` segment boundaries
- Scene/event descriptions → candidate `tags`
- Event severity/context → candidate `risk` levels (`safe`, `caution`, `mature`)

The analyzer can convert Marlin-2B's timestamped event stream into contiguous BVF segments, then run the same keyframe snapping and BVF muxing steps used by the existing analyzer path.

### Tradeoffs
- **Pros:** Potentially fewer moving parts; may drop separate Whisper + Safety Checker dependencies for this analyzer path.
- **Pros:** Better temporal grounding for scene-level events than sparse frame sampling.
- **Cons:** Requires a capable GPU for practical inference.
- **Cons:** VLM latency is per video and may be slower than lightweight sampled-frame classification.
- **Cons:** Marlin-2B is not explicitly a content moderation model, so its output must be mapped into the project's tag taxonomy.

### Tag Mapping
The BVF manifest requires mature-content tags such as `nudity`, `violence`, `language`, `fear`, and `gore`. Marlin-2B captions should be mapped into that taxonomy by either:
- Direct prompting that asks Marlin-2B to emit structured events with the target tags and risk levels.
- A lightweight classifier that converts timestamped captions into normalized tags and risk levels.

The implementation should treat Marlin-2B output as content-understanding evidence, then normalize it before writing the BVF manifest.

### Implementation
- Add `analyzer/marlin_analyze.py` as an alternative analyzer entry point.
- Add requirements support for Marlin-2B inference via `transformers` + `accelerate`.
- Preserve the existing `analyzer/analyze.py` Whisper + Safety Checker path until the Marlin path is validated.
- Reuse existing segment merging, keyframe snapping, and BVF muxer integration where possible.

## 5. Component 2: The Jellyfin Plugin (C#)
**Directory:** `csharp_plugin/`
**Purpose:** Native Jellyfin plugin that intercepts playback and serves profile-filtered content.

### Key Classes
| Class | Purpose |
|-------|---------|
| `Plugin` | Entry point — scans library on startup, registers virtual sources |
| `ManifestScanner` | Finds `.bvf` files alongside movie files |
| `BVFReader` | Parses BVF binary format: reads header, index, decompresses manifest |
| `ProfileResolver` | Maps Jellyfin users to branch profiles, resolves segment actions |
| `SegmentServer` | Serves resolved segments through Jellyfin's streaming pipeline |

### How Playback Works
1. User clicks "Play" on a movie
2. Plugin checks for `.bvf` in the same directory
3. If found, registers a "Smart Branch" virtual MediaSource
4. On playback start, `BVFReader` parses header + index, `ProfileResolver` resolves segments
5. Segments are served through Jellyfin's native streaming (no external proxy)
6. Mature segments are swapped with fillers or skipped based on profile filters

### Configuration — User Profile System
The plugin now uses **explicit user profiles** (birthday + sex) instead of Jellyfin's parental rating ceiling:

**UserBranchProfile:**
- `Birthday` — date of birth for age-based profile resolution
- `Sex` — male/female for teen profile differentiation
- `ProfileOverride` — explicit override (e.g., force "child" for a teen user)

**Resolution order:**
1. Explicit `ProfileOverride` from stored config
2. Auto-resolve from `Birthday` + `Sex`:
   - Age < 13 → `child`
   - Age < 18 → `teen_m` or `teen_f` (based on sex)
3. Fall back to plugin's `DefaultProfile`

**Config page (configPage.html):**
- User profile table with columns: User name, Birthday picker, Sex dropdown, Override dropdown
- Live resolved profile badge (green=child, blue=teen_m, pink=teen_f, gray=adult)
- Changes to birthday/sex/override instantly update the resolved badge

### Build & Install
```bash
cd csharp_plugin
dotnet build -c Release
# Copy SmartBranching.Plugin.dll to Jellyfin's plugin directory
# e.g., /usr/share/jellyfin/data/plugins/
```

## 6. Project Structure
```
vid_splitter/
├── PROJECT_SPEC.md           # This file
├── BVF_SPEC.md               # Branched Video Format specification (v1.0 draft)
├── requirements.txt
├── test_pipeline.py
├── .gitignore
│
├── analyzer/
│   ├── __init__.py
│   └── analyze.py            # Python CLI: Whisper + Safety Checker + FFmpeg + BVF muxer
│
├── csharp_plugin/
│   ├── Directory.Build.props
│   ├── SmartBranching.Plugin.csproj
│   ├── build.yaml            # Jellyfin plugin manifest
│   ├── Plugin.cs             # Entry point + library scanning
│   ├── Configuration/
│   │   ├── PluginConfiguration.cs   # UserBranchProfile + Dictionary<string, UserBranchProfile>
│   │   └── configPage.html            # User profile table with live resolution
│   ├── Models/
│   │   └── BranchManifest.cs  # normalized branch manifest view
│   ├── ManifestScanner.cs     # Scans library for .bvf files
│   ├── BVFReader.cs           # Parses BVF binary format (header + index + manifest)
│   ├── ProfileResolver.cs     # User → profile mapping (birthday+sex override system)
│   └── SegmentServer.cs       # Serves resolved segments through Jellyfin
│
└── tools/
    ├── bvf_muxer.py            # BVF file builder (Phase 1 of BVF roadmap)
    └── bvf_player.py           # Reference BVF player for testing (Phase 2)
```

## 7. Implementation Roadmap

### Phase 1: Analyzer (Python) ✅
* [x] `analyzer/analyze.py` with Whisper + Safety Checker + FFmpeg
* [x] Word-level profanity detection via Whisper timestamps
* [x] NSFW detection via Stable Diffusion Safety Checker
* [x] Segment merging with gap filling
* [x] Keyframe-aligned segment boundaries
* [ ] Phase 1b: `analyzer/marlin_analyze.py` alternative analyzer path with Marlin-2B dense captioning and timestamped risk tags
* [x] BVF muxer integration (write `.bvf` from analyzer output)
* [ ] Test with real video file

### Phase 2: C# Plugin Skeleton ✅
* [x] `csharp_plugin/` with official Jellyfin plugin template structure
* [x] `Plugin.cs` — entry point, library scanning, virtual source registration
* [x] `ManifestScanner.cs` — find `.bvf` files alongside movies
* [x] `ProfileResolver.cs` — user-to-profile mapping (birthday+sex override system)
* [x] `Configuration/` — config page with live profile resolution
* [x] BVF binary format support (header + index + manifest parsing)
* [x] `SegmentServer.cs` — segment serving through Jellyfin pipeline
* [ ] Build and test on Jellyfin server

### Phase 3: BVF Toolchain
* [x] `bvf_muxer.py` — writes `.bvf` containers from analyzer segments
* [x] `bvf_player.py` — reference player: reads `.bvf`, resolves profile, plays via ffplay
* [ ] Keyframe alignment verification tool
* [ ] BVF file validation tool

### Phase 4: End-to-End
* [ ] Create test video with known mature content
* [ ] Run analyzer, verify BVF manifest
* [ ] Build C# plugin, install in Jellyfin
* [ ] Test playback with different user profiles
* [ ] Verify segment swapping works in player

### Phase 5: BVF Ecosystem (Future)
* [ ] `libbvf` — C library for embedding into VLC, mpv, or other players
* [ ] mpv demuxer plugin using `libbvf`
* [ ] Jellyfin BVF-aware transcoding path (serve via HLS)

## 8. User Workflow
1. **Owner:** Runs `python analyze.py "ActionMovie.mp4"`
2. **Result:** `ActionMovie.bvf` (self-contained, all segments + manifest in one file) created next to the movie
3. **C# Plugin:** Automatically detects the `.bvf` file on next library scan
4. **Viewer:** Logs into Jellyfin as "Child" (profile auto-resolved from birthday + sex)
5. **Playback:** Clicks "Play" → plugin routes through BVF reader + segment server → child-safe stream
6. **Experience:** Movie plays normally, violence/language scenes are swapped with fillers or skipped

## 9. Constraints & Notes
* **No external services** — everything runs inside Jellyfin. Python is only for pre-processing.
* **Manifest caching** — C# plugin caches decompressed manifest in memory to avoid re-parsing.
* **Storage** — filler videos in a dedicated directory (`/srv/jellyfin/filler/`). BVF file is larger than original (original + fillers).
* **Extensibility** — JSON schema supports "Blur" and "Mute" actions; BVF codec IDs support H.264/H.265/AV1/VP9 + AAC/Opus/AC-3/EAC-3
* **Model loading** — Whisper and Safety Checker load once at first analysis, cached in memory.
* **GPU** — Safety Checker can run on GPU for faster frame classification.
* **Keyframe alignment** — mandatory IDR/keyframe at every segment boundary; analyzer snaps boundaries to nearest keyframe.
* **Profile system** — explicit user data (birthday + sex) stored in plugin config, no dependency on Jellyfin's rating infrastructure.
