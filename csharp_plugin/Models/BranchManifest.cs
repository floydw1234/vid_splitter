using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace Jellyfin.Plugin.SmartBranching.Models;

/// <summary>
/// Normalized branch manifest structure derived from a BVF container.
/// Used by scanner and profile-resolution paths that operate on timeline segments.
/// </summary>
public class BranchManifest
{
    [JsonPropertyName("movie_id")]
    public string MovieId { get; set; } = string.Empty;

    [JsonPropertyName("movie_path")]
    public string MoviePath { get; set; } = string.Empty;

    [JsonPropertyName("duration_seconds")]
    public double DurationSeconds { get; set; }

    [JsonPropertyName("analyzed_at")]
    public string? AnalyzedAt { get; set; }

    [JsonPropertyName("profiles")]
    public Dictionary<string, UserProfile> Profiles { get; set; } = new();

    [JsonPropertyName("segments")]
    public List<Segment> Segments { get; set; } = new();
}

/// <summary>
/// A user profile definition from the manifest.
/// </summary>
public class UserProfile
{
    [JsonPropertyName("age")]
    public int Age { get; set; }

    [JsonPropertyName("gender")]
    public string Gender { get; set; } = string.Empty;

    [JsonPropertyName("filters")]
    public List<string> Filters { get; set; } = new();
}

/// <summary>
/// A single content segment from the manifest.
/// </summary>
public class Segment
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("start_time")]
    public double StartTime { get; set; }

    [JsonPropertyName("end_time")]
    public double EndTime { get; set; }

    [JsonPropertyName("tags")]
    public List<string> Tags { get; set; } = new();

    [JsonPropertyName("risk")]
    public string Risk { get; set; } = string.Empty;

    [JsonPropertyName("action")]
    public string Action { get; set; } = string.Empty;

    [JsonPropertyName("swap_options")]
    public Dictionary<string, string>? SwapOptions { get; set; }

    [JsonPropertyName("profiles")]
    public Dictionary<string, SegmentProfileAction> Profiles { get; set; } = new();

    [JsonPropertyName("is_filler")]
    public bool IsFiller { get; set; }
}
/// <summary>
/// A BVF per-profile segment action.
/// </summary>
public class SegmentProfileAction
{
    [JsonPropertyName("action")]
    public string Action { get; set; } = string.Empty;

    [JsonPropertyName("segment_id")]
    public string SegmentId { get; set; } = string.Empty;
}

/// <summary>
/// The resolved segment after applying profile rules.
/// Contains the actual file path to serve.
/// </summary>
public class ResolvedSegment
{
    public Segment Source { get; set; } = new();
    public string ResolvedPath { get; set; } = string.Empty;
    public bool IsSwapped { get; set; }
    public string SwapType { get; set; } = string.Empty; // "filler", "original", "skip"
    public string SegmentId { get; set; } = string.Empty;
    public ulong DataOffset { get; set; }
    public ulong DataLength { get; set; }
    public ulong DurationMs { get; set; }
    public string AudioHash { get; set; } = string.Empty;
}
