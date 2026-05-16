"""
Branch Player — rapid testing tool.

Loads a _branch.json manifest, applies a profile, and plays the filtered video
using FFmpeg's concat demuxer (no segment pre-extraction required).

Usage:
  python play.py movie_branch.json [--profile child|teen_m|teen_f|adult]
  python play.py movie_branch.json --profile child --summary
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROFILE_FILTERS: dict[str, set[str]] = {
    "child":  {"nudity", "violence", "language", "fear"},
    "teen_m": {"nudity", "gore"},
    "teen_f": {"nudity", "violence"},
    "adult":  set(),
}


def resolve_segments(manifest: dict, profile: str) -> list[dict]:
    """
    Apply profile filtering to produce a list of playback decisions.
    Each entry has: start_time, end_time, action ('play' | 'filler' | 'skip'), tags.
    """
    filters = PROFILE_FILTERS.get(profile, set())
    decisions = []

    for seg in manifest["segments"]:
        base = {
            "id": seg["id"],
            "start_time": seg["start_time"],
            "end_time": seg["end_time"],
            "tags": seg.get("tags", []),
        }

        if seg["risk"] == "safe" or seg["action"] == "play":
            decisions.append({**base, "action": "play"})
            continue

        matching = set(seg.get("tags", [])) & filters
        if not matching:
            decisions.append({**base, "action": "play"})
            continue

        swap_choice = seg.get("swap_options", {}).get(profile, "skip")
        if swap_choice == "original":
            decisions.append({**base, "action": "play"})
        elif swap_choice.startswith("filler_"):
            decisions.append({**base, "action": "filler", "filler_id": swap_choice})
        else:
            decisions.append({**base, "action": "skip"})

    return decisions


def print_summary(manifest: dict, decisions: list[dict], profile: str) -> None:
    """Print a human-readable breakdown of what will be played."""
    total_duration = manifest["duration_seconds"]
    play_time = sum(d["end_time"] - d["start_time"] for d in decisions if d["action"] == "play")
    skip_time = sum(d["end_time"] - d["start_time"] for d in decisions if d["action"] == "skip")
    filler_count = sum(1 for d in decisions if d["action"] == "filler")

    print(f"\nMovie:    {manifest['movie_id']}")
    print(f"Profile:  {profile}  (filters: {sorted(PROFILE_FILTERS[profile]) or ['none']})")
    print(f"Duration: {total_duration:.0f}s total → {play_time:.0f}s will play, {skip_time:.0f}s skipped")
    print()
    print(f"{'ID':<10} {'Start':>7} {'End':>7} {'Action':<8} Tags")
    print("-" * 55)
    for d in decisions:
        tags = ", ".join(d["tags"]) or "-"
        extra = f"  [{d.get('filler_id', '')}]" if d["action"] == "filler" else ""
        print(f"{d['id']:<10} {d['start_time']:>7.1f} {d['end_time']:>7.1f} {d['action']:<8} {tags}{extra}")
    print()
    if filler_count:
        print(f"Note: {filler_count} filler segment(s) will be skipped during playback (filler .ts files not yet generated).")
        print()


def build_ffconcat(movie_path: str, decisions: list[dict]) -> str:
    """
    Build an ffconcat playlist that splices the original file at segment boundaries.
    Segments with action='skip' or 'filler' are omitted.
    """
    lines = ["ffconcat version 1.0"]
    for d in decisions:
        if d["action"] not in ("play",):
            continue
        lines.append(f"file '{movie_path}'")
        lines.append(f"inpoint {d['start_time']}")
        lines.append(f"outpoint {d['end_time']}")
    return "\n".join(lines)


def play(manifest_path: str, profile: str, summary_only: bool) -> None:
    with open(manifest_path) as f:
        manifest = json.load(f)

    movie_path = manifest["movie_path"]
    if not Path(movie_path).exists():
        # Try path relative to the manifest file itself
        movie_path = str(Path(manifest_path).parent / Path(movie_path).name)
        if not Path(movie_path).exists():
            print(f"Error: video file not found at '{manifest['movie_path']}'")
            sys.exit(1)

    decisions = resolve_segments(manifest, profile)
    print_summary(manifest, decisions, profile)

    if summary_only:
        return

    playable = [d for d in decisions if d["action"] == "play"]
    if not playable:
        print("Nothing to play for this profile (all segments filtered).")
        return

    playlist = build_ffconcat(movie_path, decisions)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write(playlist)
        tmp_path = tmp.name

    print(f"Playing {len(playable)}/{len(decisions)} segments via ffplay...")
    print("(Press Q to quit)\n")

    # ffplay with concat demuxer; -safe 0 allows absolute paths
    cmd = ["ffplay", "-f", "concat", "-safe", "0", tmp_path]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print("Error: ffplay not found. Install ffmpeg: sudo apt install ffmpeg")
        print(f"\nPlaylist written to: {tmp_path}")
        sys.exit(1)
    except subprocess.CalledProcessError:
        pass  # User quit the player — that's fine


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Play a smart-branching manifest with profile filtering applied"
    )
    parser.add_argument("manifest", help="Path to _branch.json manifest file")
    parser.add_argument(
        "--profile",
        choices=list(PROFILE_FILTERS),
        default=None,
        help="Profile to apply (default: prompt interactively)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print segment breakdown only, do not launch player",
    )
    args = parser.parse_args()

    profile = args.profile
    if not profile:
        print("Select a profile:")
        for i, p in enumerate(PROFILE_FILTERS, 1):
            filters = sorted(PROFILE_FILTERS[p]) or ["none"]
            print(f"  {i}. {p:<8}  filters: {', '.join(filters)}")
        choice = input("\nEnter number: ").strip()
        try:
            profile = list(PROFILE_FILTERS)[int(choice) - 1]
        except (ValueError, IndexError):
            print("Invalid choice.")
            sys.exit(1)

    play(args.manifest, profile, summary_only=args.summary)


if __name__ == "__main__":
    main()
