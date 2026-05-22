using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Drawing;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Controller.Streaming;
using MediaBrowser.Controller.Videos;
using MediaBrowser.Model.Dlna;
using MediaBrowser.Model.Dto;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Serves resolved video segments to the Jellyfin player.
/// 
/// Architecture:
/// 1. User clicks "Play" on a movie
/// 2. Jellyfin calls our video processor
/// 3. We read the manifest, resolve segments for the user's profile
/// 4. We serve the resolved BVF segment data blocks through Jellyfin's streaming pipeline
/// 5. Segments marked for swap are replaced with fillers or skipped
/// </summary>
public class SegmentServer : ICustomSubtitleStreamService, ISubtitleEncoder, IImageEncoder, IVideosByNameProcessor
{
    private readonly ILogger<SegmentServer> _logger;
    private readonly ProfileResolver _profileResolver;
    private readonly Dictionary<string, BranchManifest> _manifestCache = new();

    public SegmentServer(
        ILogger<SegmentServer> logger,
        IApplicationPaths applicationPaths)
    {
        ArgumentNullException.ThrowIfNull(applicationPaths);

        _logger = logger;
        _profileResolver = new ProfileResolver();
    }

    /// <summary>
    /// Gets or creates a BVF manifest for a movie, with caching.
    /// </summary>
    private BranchManifest GetManifest(string moviePath)
    {
        var bvfPath = ManifestScanner.GetBvfPath(moviePath)
            ?? throw new FileNotFoundException($"Cannot determine BVF path for: {moviePath}");

        if (_manifestCache.TryGetValue(bvfPath, out var cached))
            return cached;

        if (!File.Exists(bvfPath))
            throw new FileNotFoundException($"No BVF file found for: {moviePath}");

        var manifest = BVFReader.LoadBvfManifest(bvfPath, moviePath);
        _manifestCache[bvfPath] = manifest;

        return manifest;
    }

    /// <summary>
    /// Clears the manifest cache (call when library changes).
    /// </summary>
    public void ClearCache()
    {
        _manifestCache.Clear();
        _logger.LogInformation("BVF manifest cache cleared");
    }

    /// <summary>
    /// Resolves all BVF segments for a movie and user profile.
    /// </summary>
    public List<ResolvedSegment> ResolveAllSegments(string moviePath, UserDto user)
    {
        var bvfPath = ManifestScanner.GetBvfPath(moviePath)
            ?? throw new FileNotFoundException($"Cannot determine BVF path for: {moviePath}");
        var manifest = GetManifest(moviePath);
        var profile = _profileResolver.ResolveProfile(user, manifest);
        var segments = BVFReader.GetSegments(bvfPath, profile);
        var manifestById = manifest.Segments.ToDictionary(s => s.Id, StringComparer.Ordinal);

        var resolved = new List<ResolvedSegment>(segments.Length);
        foreach (var segment in segments)
        {
            manifestById.TryGetValue(segment.segmentId, out var source);
            resolved.Add(new ResolvedSegment
            {
                Source = source ?? new Segment { Id = segment.segmentId },
                ResolvedPath = bvfPath,
                IsSwapped = source?.IsFiller ?? false,
                SwapType = source?.IsFiller == true ? "filler" : "original",
                SegmentId = segment.segmentId,
                DataOffset = segment.dataOffset,
                DataLength = segment.dataLength,
                DurationMs = segment.durationMs,
                AudioHash = segment.audioHash,
            });
        }

        _logger.LogInformation(
            "Resolved {Total} BVF segments for {Movie} (profile: {Profile}, swapped: {Swapped})",
            resolved.Count,
            manifest.MovieId,
            profile,
            resolved.Count(s => s.IsSwapped));

        return resolved;
    }

    /// <summary>
    /// Opens the raw BVF segment data block for a resolved segment.
    /// </summary>
    public Stream OpenSegmentStream(ResolvedSegment segment)
    {
        if (string.IsNullOrEmpty(segment.ResolvedPath))
            throw new ArgumentException("Resolved segment does not point at a BVF file.", nameof(segment));

        return BVFReader.OpenSegmentDataStream(
            segment.ResolvedPath,
            new BVFSegment
            {
                segmentId = segment.SegmentId,
                dataOffset = segment.DataOffset,
                dataLength = segment.DataLength,
                durationMs = segment.DurationMs,
                audioHash = segment.AudioHash,
            });
    }

    /// <summary>
    /// Reads the raw BVF segment data block for a resolved segment.
    /// </summary>
    public byte[] ReadSegmentData(ResolvedSegment segment)
    {
        using var stream = OpenSegmentStream(segment);
        using var memory = new MemoryStream();
        stream.CopyTo(memory);
        return memory.ToArray();
    }

    /// <summary>
    /// Checks if a movie has an associated BVF container.
    /// </summary>
    public bool HasBranchManifest(string moviePath)
    {
        var bvfPath = ManifestScanner.GetBvfPath(moviePath);
        return bvfPath != null && File.Exists(bvfPath);
    }

    /// <summary>
    /// Gets the BVF manifest for a movie without caching (for admin/debug).
    /// </summary>
    public BranchManifest GetManifestRaw(string moviePath)
    {
        var bvfPath = ManifestScanner.GetBvfPath(moviePath)
            ?? throw new FileNotFoundException($"Cannot determine BVF path for: {moviePath}");

        return BVFReader.LoadBvfManifest(bvfPath, moviePath);
    }

    // ─── ICustomSubtitleStreamService (required interface) ────────────

    public Task<SubtitleResponse> GetSubtitleStream(
        SubtitleStreamInfo request,
        CancellationToken cancellationToken)
    {
        // Not used — we're not handling subtitles
        return Task.FromResult<SubtitleResponse>(null!);
    }

    // ─── ISubtitleEncoder (required interface) ────────────────────────

    public Task Encode(
        SubtitleEncodeOptions options,
        Stream output,
        CancellationToken cancellationToken)
    {
        return Task.CompletedTask;
    }

    // ─── IImageEncoder (required interface) ───────────────────────────

    public Task<Stream> Encode(
        ImageEncodeOptions options,
        CancellationToken cancellationToken)
    {
        return Task.FromResult<Stream>(null!);
    }

    // ─── IVideosByNameProcessor (required interface) ──────────────────

    public Task Process(
        BaseItem item,
        MediaAttachmentInfo mediaAttachment,
        CancellationToken cancellationToken)
    {
        return Task.CompletedTask;
    }
}
