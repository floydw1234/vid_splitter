using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Controller;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Controller.MediaSources;
using MediaBrowser.Controller.Playback;
using MediaBrowser.Controller.Streaming;
using MediaBrowser.Controller.Videos;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Serves resolved video segments from BVF files through Jellyfin's video pipeline.
/// 
/// Architecture:
/// 1. User clicks "Play" on a movie
/// 2. Jellyfin calls our video processor
/// 3. We read the BVF file, resolve segments for the user's profile
/// 4. We serve the resolved segments through Jellyfin's streaming pipeline
/// 5. Segments marked for swap are replaced with fillers or skipped
/// </summary>
public class SegmentServer : IMediaSourceProvider, IVideoProcessor
{
    private readonly ILogger<SegmentServer> _logger;
    private readonly IApplicationPaths _applicationPaths;
    private readonly ProfileResolver _profileResolver;
    private readonly Dictionary<string, BVFManifest> _bvfManifestCache = new();
    private readonly Dictionary<string, BVFBinaryReader> _bvfReaders = new();

    public SegmentServer(
        ILogger<SegmentServer> logger,
        IApplicationPaths applicationPaths)
    {
        _logger = logger;
        _applicationPaths = applicationPaths;
        _profileResolver = new ProfileResolver();
    }

    /// <summary>
    /// Gets or creates a BVF manifest for a movie, with caching.
    /// </summary>
    private BVFManifest GetBvfManifest(string bvfPath)
    {
        if (_bvfManifestCache.TryGetValue(bvfPath, out var cached))
            return cached;

        try
        {
            var reader = BVFBinaryReader.Open(bvfPath);
            var manifestJson = reader.GetManifestJson();
            var manifest = BVFManifestParser.Parse(manifestJson);
            
            _bvfManifestCache[bvfPath] = manifest;
            _bvfReaders[bvfPath] = reader;

            return manifest;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to read BVF manifest from {Path}", bvfPath);
            throw;
        }
    }

    /// <summary>
    /// Gets the BVF reader for a path (lazy-loaded, cached).
    /// </summary>
    private BVFBinaryReader GetBvfReader(string bvfPath)
    {
        if (!_bvfReaders.TryGetValue(bvfPath, out var reader))
        {
            reader = BVFBinaryReader.Open(bvfPath);
            _bvfReaders[bvfPath] = reader;
        }
        return reader;
    }

    /// <summary>
    /// Clears all caches (call when library changes).
    /// </summary>
    public void ClearCache()
    {
        foreach (var reader in _bvfReaders.Values)
        {
            try { reader.Close(); } catch { }
        }
        _bvfReaders.Clear();
        _bvfManifestCache.Clear();
        _logger.LogInformation("BVF manifest cache cleared");
    }

    /// <summary>
    /// Finds the BVF file for a given movie path.
    /// </summary>
    public string? FindBvfFile(string moviePath)
    {
        var dir = Path.GetDirectoryName(moviePath);
        if (dir == null)
            return null;

        var stem = Path.GetFileNameWithoutExtension(moviePath);
        var bvfPath = Path.Combine(dir, stem + ".bvf");

        return File.Exists(bvfPath) ? bvfPath : null;
    }

    /// <summary>
    /// Resolves all segments for a movie and user profile.
    /// Returns a list of resolved segments with actual file paths.
    /// </summary>
    public List<ResolvedBvfSegment> ResolveAllSegments(string bvfPath, UserDto user)
    {
        var manifest = GetBvfManifest(bvfPath);
        var profileKey = _profileResolver.ResolveProfileForBvf(user, manifest);
        
        var reader = GetBvfReader(bvfPath);
        var segmentBlocks = new Dictionary<string, BVFBinaryReader.SegmentBlock>();
        
        foreach (var block in reader.GetSegmentBlocks())
        {
            segmentBlocks[block.Header.SegmentId] = block;
        }

        var resolved = BVFManifestParser.ResolveSegmentsForProfile(
            manifest, profileKey, segmentBlocks);

        _logger.LogInformation(
            "Resolved {Total} segments for {Movie} (profile: {Profile}, swapped: {Swapped}, skipped: {Skipped})",
            resolved.Count,
            manifest.MovieId,
            profileKey,
            resolved.Count(s => s.IsSwap),
            resolved.Count(s => s.IsSkip));

        return resolved;
    }

    /// <summary>
    /// Checks if a movie has an associated BVF file.
    /// </summary>
    public bool HasBvfFile(string moviePath)
    {
        return FindBvfFile(moviePath) != null;
    }

    /// <summary>
    /// Gets the manifest for a movie without caching (for admin/debug).
    /// </summary>
    public BVFManifest GetManifestRaw(string bvfPath)
    {
        return GetBvfManifest(bvfPath);
    }

    // ─── IMediaSourceProvider ────────────────────────────────────────

    /// <summary>
    /// Provides media sources for BVF content.
    /// When a movie has a .bvf file, we add a "Smart Branch" source
    /// that serves the profile-filtered stream.
    /// </summary>
    public async Task<IEnumerable<MediaSourceInfo>> GetMediaSources(MediaSourceQuery query)
    {
        if (query.ItemId == Guid.Empty)
            return Enumerable.Empty<MediaSourceInfo>();

        try
        {
            var item = await GetItem(query.ItemId).ConfigureAwait(false);
            if (item == null)
                return Enumerable.Empty<MediaSourceInfo>();

            var moviePath = item.Path;
            var bvfPath = FindBvfFile(moviePath);

            if (bvfPath == null)
                return Enumerable.Empty<MediaSourceInfo>();

            // Check if this is a BVF-aware item
            if (!HasBvfFile(moviePath))
                return Enumerable.Empty<MediaSourceInfo>();

            var manifest = GetBvfManifest(bvfPath);

            // Create a media source for the BVF content
            var mediaSource = new MediaSourceInfo
            {
                Id = $"{bvfPath}::smart-branch",
                Name = "Smart Branch",
                Path = bvfPath,
                Container = "bvf",
                MediaStreams = new List<MediaStream>
                {
                    new MediaStream
                    {
                        CodecType = MediaStreamType.Video,
                        CodecName = "BVF",
                        DisplayTitle = "Smart Branch (BVF)"
                    }
                },
                SupportsProbing = false,
                IsRemote = false,
                SupportsTiming = true
            };

            return new[] { mediaSource };
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to get media sources for item {ItemId}", query.ItemId);
            return Enumerable.Empty<MediaSourceInfo>();
        }
    }

    /// <summary>
    /// Gets the media stream for a BVF item.
    /// </summary>
    public async Task<StreamResult> OpenMediaStream(
        MediaStreamRequest request,
        CancellationToken cancellationToken)
    {
        try
        {
            // Parse the item ID and BVF path from the media source ID
            var mediaSourceId = request.MediaSourceId;
            var parts = mediaSourceId.Split("::");
            if (parts.Length != 2)
                throw new ArgumentException($"Invalid media source ID: {mediaSourceId}");

            var bvfPath = parts[0];
            var bvfFile = FindBvfFile(bvfPath);
            if (bvfFile == null)
                throw new FileNotFoundException($"BVF file not found: {bvfPath}");

            var manifest = GetBvfManifest(bvfFile);
            var reader = GetBvfReader(bvfFile);

            // Get user profile
            var user = await GetUser(request.UserId).ConfigureAwait(false);
            if (user == null)
                throw new UnauthorizedAccessException("User not found");

            // Resolve segments for profile
            var resolvedSegments = ResolveAllSegments(bvfFile, user);

            // Handle seek with profile-adjusted timestamp
            var seekPosition = request.StartPositionTicks / TimeSpan.TicksPerMillisecond;
            var patMapping = BVFManifestParser.ComputePatMapping(manifest, resolvedSegments);
            
            var seekResult = BVFManifestParser.SeekToPat(resolvedSegments, patMapping, (ulong)seekPosition);
            
            if (seekResult == null)
            {
                // Seek past end — return first playable segment
                seekResult = resolvedSegments.FirstOrDefault(s => s.Playable && !s.IsSkip);
            }

            if (seekResult == null)
                throw new InvalidOperationException("No playable segments found");

            var targetSegment = (ResolvedBvfSegment)seekResult.Segment!;
            var targetSegmentId = targetSegment.TargetSegmentId;
            var block = reader.GetSegmentBlockByName(targetSegmentId);

            if (block == null)
                throw new FileNotFoundException($"Segment block not found: {targetSegmentId}");

            // Handle mute action — serve video-only stream
            if (targetSegment.IsMute)
            {
                // Filter out audio packets
                var videoOnlyPackets = block.Packets
                    .Where(p => p.IsVideo)
                    .ToList();
                
                return new StreamResult(
                    new MemoryStream(CombinePackets(videoOnlyPackets)),
                    "video/mp2t",
                    videoOnlyPackets.Count,
                    0,
                    block.Header.CodecVideo);
            }

            // Handle swap action — serve the filler segment's data
            if (targetSegment.IsSwap)
            {
                var fillerBlock = reader.GetSegmentBlockByName(targetSegmentId);
                if (fillerBlock != null)
                {
                    return new StreamResult(
                        new MemoryStream(CombinePackets(fillerBlock.Packets)),
                        "video/mp2t",
                        fillerBlock.Packets.Count,
                        0,
                        fillerBlock.Header.CodecVideo);
                }
            }

            // Normal play — serve the segment's packets
            return new StreamResult(
                new MemoryStream(CombinePackets(block.Packets)),
                "video/mp2t",
                block.Packets.Count,
                0,
                block.Header.CodecVideo);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to open media stream");
            throw;
        }
    }

    /// <summary>
    /// Combines packets into a single byte stream.
    /// </summary>
    private byte[] CombinePackets(List<BVFBinaryReader.PacketData> packets)
    {
        using var ms = new MemoryStream();
        foreach (var packet in packets)
        {
            ms.Write(packet.Data, 0, packet.Data.Length);
        }
        return ms.ToArray();
    }

    // ─── IVideoProcessor ─────────────────────────────────────────────

    /// <summary>
    /// Processes video items for BVF content.
    /// This is called by Jellyfin when a video item is accessed.
    /// </summary>
    public async Task ProcessVideo(
        BaseItem item,
        MediaRequest request,
        CancellationToken cancellationToken)
    {
        var bvfPath = FindBvfFile(item.Path);
        if (bvfPath == null)
            return; // Not a BVF item, skip processing

        _logger.LogDebug("Processing BVF content for item {ItemId}: {Path}", item.Id, item.Path);

        try
        {
            var manifest = GetBvfManifest(bvfPath);
            var user = await GetUser(request.UserId).ConfigureAwait(false);
            
            if (user != null)
            {
                var profileKey = _profileResolver.ResolveProfileForBvf(user, manifest);
                _logger.LogInformation(
                    "BVF processing: {MovieId} -> profile {Profile}",
                    manifest.MovieId,
                    profileKey);
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to process BVF content for item {ItemId}", item.Id);
        }
    }
}
