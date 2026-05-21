using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace Jellyfin.Plugin.SmartBranching.Models;

/// <summary>
/// Complete BVF manifest structure parsed from the zstd-compressed JSON in the BVF file.
/// Mirrors the BVF_SPEC.md §5.1 schema.
/// </summary>
public class BVFManifest
{
    [JsonPropertyName("bvf_version")]
    public string BvfVersion { get; set; } = "1.0";

    [JsonPropertyName("movie_id")]
    public string MovieId { get; set; } = string.Empty;

    [JsonPropertyName("title")]
    public string Title { get; set; } = string.Empty;

    [JsonPropertyName("duration_ms")]
    public ulong DurationMs { get; set; }

    [JsonPropertyName("analyzed_at")]
    public string? AnalyzedAt { get; set; }

    [JsonPropertyName("video_info")]
    public VideoInfo? VideoInfo { get; set; }

    [JsonPropertyName("profiles")]
    public Dictionary<string, BVFProfile> Profiles { get; set; } = new();

    [JsonPropertyName("segments")]
    public List<BVFSegment> Segments { get; set; } = new();

    [JsonPropertyName("chapters")]
    public List<BVFChapter>? Chapters { get; set; }
}

/// <summary>
/// Video metadata from the manifest.
/// </summary>
public class VideoInfo
{
    [JsonPropertyName("width")]
    public int Width { get; set; }

    [JsonPropertyName("height")]
    public int Height { get; set; }

    [JsonPropertyName("frame_rate")]
    public string FrameRate { get; set; } = string.Empty;

    [JsonPropertyName("color_space")]
    public string ColorSpace { get; set; } = string.Empty;
}

/// <summary>
/// A viewer profile definition (child, teen, adult, etc.).
/// </summary>
public class BVFProfile
{
    [JsonPropertyName("label")]
    public string Label { get; set; } = string.Empty;

    [JsonPropertyName("filters")]
    public List<string> Filters { get; set; } = new();
}

/// <summary>
/// A content segment with branching information.
/// </summary>
public class BVFSegment
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("start_ms")]
    public ulong? StartMs { get; set; }

    [JsonPropertyName("end_ms")]
    public ulong? EndMs { get; set; }

    [JsonPropertyName("tags")]
    public List<string> Tags { get; set; } = new();

    [JsonPropertyName("risk")]
    public string Risk { get; set; } = string.Empty;

    [JsonPropertyName("is_filler")]
    public bool IsFiller { get; set; }

    [JsonPropertyName("profiles")]
    public Dictionary<string, BVFProfileAction>? Profiles { get; set; }
}

/// <summary>
/// Action to take for a specific profile on a segment.
/// </summary>
public class BVFProfileAction
{
    [JsonPropertyName("action")]
    public string Action { get; set; } = "play"; // play, swap, skip, mute, blur

    [JsonPropertyName("segment_id")]
    public string SegmentId { get; set; } = string.Empty;
}

/// <summary>
/// Chapter marker in the manifest.
/// </summary>
public class BVFChapter
{
    [JsonPropertyName("title")]
    public string Title { get; set; } = string.Empty;

    [JsonPropertyName("start_ms")]
    public ulong StartMs { get; set; }

    [JsonPropertyName("end_ms")]
    public ulong EndMs { get; set; }
}
