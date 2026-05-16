# Project Spec: Smart Branching Plugin for Jellyfin

## 1. Project Overview
**Goal:** Create a "Context-Aware" playback system for Jellyfin that filters mature content based on user profiles.
**Architecture:** Python CLI tool for pre-processing + C# Jellyfin plugin for playback.

**Workflow:**
1. **Pre-Processing (Python):** Run `python analyze.py "movie.mp4"` → generates `movie_branch.json` + `.ts` segments
2. **Playback (C# Plugin):** Jellyfin plugin reads the JSON manifest, resolves segments per user profile, serves filtered stream through Jellyfin's native pipeline

## 2. Architecture
```
┌──────────────────────────────────────────────────────────────────┐
│  Python (CLI — run once per movie)                               │
│                                                                  │
│  analyze.py → generates:                                         │
│    - movie_branch.json (manifest with content tags)              │
│    - seg_000.ts, seg_001.ts, ... (HLS segments)                  │
│                                                                  │
│  Stack: Whisper (audio) + Safety Checker (NSFW) + FFmpeg         │
└──────────────────────────────────────────────────────────────────┘
                          │
                          │ drops files next to the movie
                          v
┌──────────────────────────────────────────────────────────────────┐
│  Jellyfin Server (.NET 9)                                         │
│                                                                  │
│  Smart Branching Plugin (C#):                                    │
│    1. On startup: scans library for _branch.json files           │
│    2. Registers "Smart Branch" virtual MediaSource per movie     │
│    3. On Play: reads manifest, resolves segments for user profile│
│    4. Serves .ts segments through Jellyfin's streaming pipeline  │
│    5. Mature segments → swap with filler or skip                 │
│                                                                  │
│  No external services. No Python server. Pure Jellyfin.          │
└──────────────────────────────────────────────────────────────────┘
```

## 3. Data Schema: `movie_branch.json`
Placed alongside the movie file (e.g., `Movie.mp4` + `Movie_branch.json`).

```json
{
  "movie_id": "tt1234567",
  "movie_path": "/srv/jellyfin/media/Movie.mp4",
  "duration_seconds": 7200,
  "analyzed_at": "2026-05-14T19:00:00",
  "profiles": {
    "child": { "age": 10, "gender": "any", "filters": ["nudity", "violence", "language", "fear"] },
    "teen_m": { "age": 15, "gender": "male", "filters": ["nudity", "gore"] },
    "teen_f": { "age": 15, "gender": "female", "filters": ["nudity", "violence"] },
    "adult": { "age": 18, "gender": "any", "filters": [] }
  },
  "segments": [
    {
      "id": "seg_001",
      "start_time": 0,
      "end_time": 300,
      "tags": [],
      "risk": "safe",
      "action": "play"
    },
    {
      "id": "seg_002",
      "start_time": 300,
      "end_time": 345,
      "tags": ["violence", "weapon"],
      "risk": "mature",
      "action": "swap",
      "swap_options": {
        "child": "filler_safe_001",
        "teen_m": "original",
        "teen_f": "original",
        "adult": "original"
      }
    }
  ]
}
```

## 4. Component 1: The Analyzer (Python CLI)
**File:** `analyzer/analyze.py`
**Purpose:** One-time pre-processing tool. Run manually or via cron per movie.

### Dependencies
* `ffmpeg-python` — frame extraction and video probing
* `openai-whisper` — timestamped audio transcription with word-level timing
* `diffusers` + `safety-checker` — NSFW image classification
* `torch`, `transformers`, `torchvision`, `accelerate`
* `Pillow`, `numpy`

### Logic Flow
1. **Input:** Path to `.mp4` file
2. **Audio Analysis:** Whisper with `word_timestamps=True` → flag profanity by time
3. **Visual Analysis:** FFmpeg extracts 1 frame every 5s → Safety Checker flags NSFW
4. **Segment Merging:** Combine overlapping detections into contiguous segments, fill gaps
5. **Output:** `movie_branch.json` + optionally `.ts` segments

### CLI Usage
```bash
python analyze.py "path/to/movie.mp4" --model base --threshold 0.6 --interval 5
```

## 5. Component 2: The Jellyfin Plugin (C#)
**Directory:** `csharp_plugin/`
**Purpose:** Native Jellyfin plugin that intercepts playback and serves profile-filtered content.

### Key Classes
| Class | Purpose |
|-------|---------|
| `Plugin` | Entry point — scans library on startup, registers virtual sources |
| `ManifestScanner` | Finds `_branch.json` files alongside movie files |
| `ManifestReader` | Loads and validates JSON manifests |
| `ProfileResolver` | Maps Jellyfin users to branch profiles, resolves segment actions |
| `SegmentServer` | Serves resolved `.ts` segments through Jellyfin's streaming pipeline |

### How Playback Works
1. User clicks "Play" on a movie
2. Plugin checks for `_branch.json` in the same directory
3. If found, registers a "Smart Branch" virtual MediaSource
4. On playback start, `SegmentServer` resolves all segments for the user's profile
5. Segments are served through Jellyfin's native streaming (no external proxy)
6. Mature segments are swapped with fillers or skipped based on profile filters

### Configuration
Plugin config page in Jellyfin UI:
* Enable/disable smart branching
* Default profile for unknown users
* NSFW confidence threshold
* Filler video directory
* Per-profile filter overrides

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
├── PROJECT_SPEC.md
├── requirements.txt
├── test_pipeline.py
├── .gitignore
│
├── analyzer/
│   ├── __init__.py
│   └── analyze.py          # Python CLI: Whisper + Safety Checker + FFmpeg
│
└── csharp_plugin/
    ├── Directory.Build.props
    ├── SmartBranching.Plugin.csproj
    ├── build.yaml            # Jellyfin plugin manifest
    ├── Plugin.cs             # Entry point + library scanning
    ├── Configuration/
    │   ├── PluginConfiguration.cs
    │   └── configPage.html   # Jellyfin UI config page
    ├── Models/
    │   └── BranchManifest.cs # C# types for the JSON schema
    ├── ManifestScanner.cs    # Scans library for _branch.json files
    ├── ManifestReader.cs     # Loads and validates manifests
    ├── ProfileResolver.cs    # User → profile mapping + segment resolution
    └── SegmentServer.cs      # Serves resolved segments through Jellyfin
```

## 7. Implementation Roadmap

### Phase 1: Analyzer (Python) ✅
* [x] `analyzer/analyze.py` with Whisper + Safety Checker + FFmpeg
* [x] Word-level profanity detection via Whisper timestamps
* [x] NSFW detection via Stable Diffusion Safety Checker
* [x] Segment merging with gap filling
* [x] Manifest generation with profile definitions
* [ ] Test with real video file

### Phase 2: C# Plugin Skeleton ✅
* [x] `csharp_plugin/` with official Jellyfin plugin template structure
* [x] `Plugin.cs` — entry point, library scanning, virtual source registration
* [x] `ManifestScanner.cs` + `ManifestReader.cs` — find and parse manifests
* [x] `ProfileResolver.cs` — user-to-profile mapping, segment resolution
* [x] `SegmentServer.cs` — segment serving through Jellyfin pipeline
* [x] `Configuration/` — config page and settings
* [x] `build.yaml` — Jellyfin plugin metadata
* [ ] Build and test on Jellyfin server

### Phase 3: End-to-End
* [ ] Create test video with known mature content
* [ ] Run analyzer, verify manifest
* [ ] Build C# plugin, install in Jellyfin
* [ ] Test playback with different user profiles
* [ ] Verify segment swapping works in player

## 8. User Workflow
1. **Owner:** Runs `python analyze.py "ActionMovie.mp4"`
2. **Result:** `ActionMovie_branch.json` + `.ts` segments created next to the movie
3. **C# Plugin:** Automatically detects the manifest on next library scan
4. **Viewer:** Logs into Jellyfin as "Child"
5. **Playback:** Clicks "Play" → plugin routes through segment server → child-safe stream
6. **Experience:** Movie plays normally, violence/language scenes are swapped or skipped

## 9. Constraints & Notes
* **No external services** — everything runs inside Jellyfin. Python is only for pre-processing.
* **Manifest caching** — C# plugin caches manifests in memory to avoid disk reads on every segment request.
* **Storage** — filler videos in a dedicated directory (`/srv/jellyfin/filler/`).
* **Extensibility** — JSON schema supports "Blur" and "Mute" actions in the future.
* **Model loading** — Whisper and Safety Checker load once at first analysis, cached in memory.
* **GPU** — Safety Checker can run on GPU for faster frame classification.
