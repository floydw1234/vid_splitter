using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Model.Dto;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Parses the zstd-compressed JSON manifest from a BVF file and provides
/// profile-aware segment resolution.
/// </summary>
public static class BVFManifestParser
{
    private static readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
    };

    /// <summary>
    /// Parses the manifest JSON string from a BVF file into a BVFManifest object.
    /// </summary>
    public static BVFManifest Parse(string manifestJson)
    {
        var manifest = JsonSerializer.Deserialize<BVFManifest>(manifestJson, _jsonOptions);
        if (manifest == null)
            throw new InvalidDataException("Failed to parse BVF manifest JSON");

        // Validate required fields
        if (string.IsNullOrEmpty(manifest.MovieId))
            throw new InvalidDataException("BVF manifest missing required field: movie_id");

        if (manifest.Segments.Count == 0)
            throw new InvalidDataException("BVF manifest has no segments");

        return manifest;
    }

    /// <summary>
    /// Resolves which segments to play for a given user profile.
    /// Returns a list of resolved segments in playback order.
    /// 
    /// Algorithm (BVF_SPEC §8):
    /// 1. Walk manifest segments in order
    /// 2. Skip filler segments (not part of narrative timeline)
    /// 3. Look up profile action for each segment
    /// 4. Resolve target segment_id
    /// 5. Return playable segments with their actions
    /// </summary>
    public static List<ResolvedBvfSegment> ResolveSegmentsForProfile(
        BVFManifest manifest,
        string profileKey,
        Dictionary<string, BVFBinaryReader.SegmentBlock> segmentBlocks)
    {
        var resolved = new List<ResolvedBvfSegment>();

        if (manifest.Segments == null || manifest.Segments.Count == 0)
            return resolved;

        foreach (var segment in manifest.Segments)
        {
            // Skip filler segments (they're not part of the narrative timeline)
            if (segment.IsFiller)
                continue;

            // Look up the profile action for this segment
            if (segment.Profiles == null || !segment.Profiles.TryGetValue(profileKey, out var action))
            {
                // No profile entry — default to play
                action = new BVFProfileAction { Action = "play", SegmentId = segment.Id };
            }

            var resolvedSeg = new ResolvedBvfSegment
            {
                Source = segment,
                Action = action.Action,
                TargetSegmentId = action.SegmentId
            };

            // Handle different actions
            switch (action.Action)
            {
                case "play":
                    // Play the referenced segment
                    resolvedSeg.Playable = true;
                    resolvedSeg.IsSkip = false;
                    resolvedSeg.IsMute = false;
                    resolvedSeg.IsSwap = false;
                    break;

                case "swap":
                    // Substitute with a different segment (typically a filler)
                    resolvedSeg.Playable = true;
                    resolvedSeg.IsSkip = false;
                    resolvedSeg.IsSwap = true;
                    resolvedSeg.SwapType = "filler";
                    break;

                case "skip":
                    // Skip entirely — don't include in playback list
                    resolvedSeg.Playable = false;
                    resolvedSeg.IsSkip = true;
                    break;

                case "mute":
                    // Play video but replace audio with silence
                    resolvedSeg.Playable = true;
                    resolvedSeg.IsSkip = false;
                    resolvedSeg.IsMute = true;
                    break;

                case "blur":
                    // Future: play with video blurred
                    // For now, treat as play (blur is pre-processed)
                    resolvedSeg.Playable = true;
                    resolvedSeg.IsSkip = false;
                    break;

                default:
                    // Unknown action — default to play
                    resolvedSeg.Playable = true;
                    break;
            }

            resolved.Add(resolvedSeg);
        }

        return resolved;
    }

    /// <summary>
    /// Computes the profile-adjusted timestamp (PAT) mapping.
    /// Because skipped segments shorten the runtime, the player must maintain a
    /// profile-adjusted timestamp that excludes skipped durations.
    /// 
    /// Returns a list of (original_ms, adjusted_ms) pairs for each playable segment.
    /// </summary>
    public static List<(ulong OriginalMs, ulong AdjustedMs)> ComputePatMapping(
        BVFManifest manifest,
        List<ResolvedBvfSegment> resolvedSegments)
    {
        var mapping = new List<(ulong OriginalMs, ulong AdjustedMs)>();
        var adjustedTime = 0UL;

        foreach (var seg in resolvedSegments)
        {
            if (!seg.Playable || seg.IsSkip)
                continue;

            var startMs = seg.Source.StartMs ?? 0;
            var endMs = seg.Source.EndMs ?? 0;
            var duration = endMs - startMs;

            mapping.Add((startMs, adjustedTime));
            adjustedTime += duration;
        }

        return mapping;
    }

    /// <summary>
    /// Maps a seek position (in profile-adjusted time) to the corresponding segment and offset.
    /// </summary>
    public static (ResolvedBvfSegment? Segment, ulong SeekOffsetMs)? SeekToPat(
        List<ResolvedBvfSegment> resolvedSegments,
        List<(ulong OriginalMs, ulong AdjustedMs)> patMapping,
        ulong patSeekPosition)
    {
        // Walk segments, accumulate playable durations until patSeekPosition is reached
        var accumulated = 0UL;

        for (var i = 0; i < resolvedSegments.Count; i++)
        {
            var seg = resolvedSegments[i];
            if (!seg.Playable || seg.IsSkip)
                continue;

            var startMs = seg.Source.StartMs ?? 0;
            var endMs = seg.Source.EndMs ?? 0;
            var duration = endMs - startMs;

            if (patSeekPosition <= accumulated + duration)
            {
                return (seg, startMs);
            }

            accumulated += duration;
        }

        // Seek past end — return last segment
        for (var i = resolvedSegments.Count - 1; i >= 0; i--)
        {
            if (resolvedSegments[i].Playable && !resolvedSegments[i].IsSkip)
            {
                return (resolvedSegments[i], resolvedSegments[i].Source.StartMs ?? 0);
            }
        }

        return null;
    }

    /// <summary>
    /// Gets the segment data block for a given segment ID.
    /// </summary>
    public static BVFBinaryReader.SegmentBlock? GetSegmentBlock(
        Dictionary<string, BVFBinaryReader.SegmentBlock> segmentBlocks,
        string segmentId)
    {
        return segmentBlocks.TryGetValue(segmentId, out var block) ? block : null;
    }
}

/// <summary>
/// A segment resolved for a specific profile, with playback metadata.
/// </summary>
public class ResolvedBvfSegment
{
    public BVFSegment Source { get; set; } = new();
    public string Action { get; set; } = "play";
    public string TargetSegmentId { get; set; } = string.Empty;
    public bool Playable { get; set; }
    public bool IsSkip { get; set; }
    public bool IsMute { get; set; }
    public bool IsSwap { get; set; }
    public string SwapType { get; set; } = string.Empty;
}
