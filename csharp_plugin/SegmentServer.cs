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
/// 4. We serve the resolved .ts segments through Jellyfin's streaming pipeline
/// 5. Segments marked for swap are replaced with fillers or skipped
/// </summary>
public class SegmentServer : ICustomSubtitleStreamService, ISubtitleEncoder, IImageEncoder, IVideosByNameProcessor
{
    private readonly ILogger<SegmentServer> _logger;
    private readonly IApplicationPaths _applicationPaths;
    private readonly ProfileResolver _profileResolver;
    private readonly Dictionary<string, BranchManifest> _manifestCache = new();

    public SegmentServer(
        ILogger<SegmentServer> logger,
        IApplicationPaths applicationPaths)
    {
        _logger = logger;
        _applicationPaths = applicationPaths;
        _profileResolver = new ProfileResolver();
    }

    /// <summary>
    /// Gets or creates a manifest for a movie, with caching.
    /// </summary>
    private BranchManifest GetManifest(string moviePath)
    {
        if (_manifestCache.TryGetValue(moviePath, out var cached))
            return cached;

        var manifestPath = ManifestReader.FindForMovie(moviePath);
        if (manifestPath == null)
            throw new FileNotFoundException($"No manifest found for: {moviePath}");

        var manifest = ManifestReader.Load(manifestPath);
        _manifestCache[moviePath] = manifest;

        return manifest;
    }

    /// <summary>
    /// Clears the manifest cache (call when library changes).
    /// </summary>
    public void ClearCache()
    {
        _manifestCache.Clear();
        _logger.LogInformation("Manifest cache cleared");
    }

    /// <summary>
    /// Resolves all segments for a movie and user profile.
    /// Returns a list of resolved segments with actual file paths.
    /// </summary>
    public List<ResolvedSegment> ResolveAllSegments(string moviePath, UserDto user)
    {
        var manifest = GetManifest(moviePath);
        var profile = _profileResolver.ResolveProfile(user, manifest);
        var movieDirectory = Path.GetDirectoryName(moviePath)
            ?? Path.GetDirectoryName(manifest.MoviePath)
            ?? throw new InvalidOperationException($"Cannot determine movie directory for: {moviePath}");

        var fillerDirectory = Path.Combine(
            _applicationPaths.DataPath,
            Plugin.Instance?.Configuration.FillerDirectory ?? "smart_branching/filler");

        var resolved = new List<ResolvedSegment>();

        foreach (var segment in manifest.Segments)
        {
            var resolvedSeg = _profileResolver.ResolveSegment(segment, profile, movieDirectory, fillerDirectory);
            resolved.Add(resolvedSeg);
        }

        _logger.LogInformation(
            "Resolved {Total} segments for {Movie} (profile: {Profile}, swapped: {Swapped})",
            resolved.Count,
            manifest.MovieId,
            profile,
            resolved.Count(s => s.IsSwapped));

        return resolved;
    }

    /// <summary>
    /// Checks if a movie has an associated branch manifest.
    /// </summary>
    public bool HasBranchManifest(string moviePath)
    {
        return ManifestReader.FindForMovie(moviePath) != null;
    }

    /// <summary>
    /// Gets the manifest for a movie without caching (for admin/debug).
    /// </summary>
    public BranchManifest GetManifestRaw(string moviePath)
    {
        return ManifestReader.Load(ManifestReader.FindForMovie(moviePath) 
            ?? throw new FileNotFoundException($"No manifest found for: {moviePath}"));
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
