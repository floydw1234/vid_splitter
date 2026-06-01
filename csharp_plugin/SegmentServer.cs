using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Serves resolved video segments from BVF files through Jellyfin's video pipeline.
/// 
/// Architecture:
/// 1. User clicks "Play" on a movie
/// 2. Jellyfin asks this provider for media sources
/// 3. We expose one Smart Branch source per BVF profile
/// 4. We serve the resolved segments through Jellyfin's streaming pipeline
/// 5. Segment actions are read from the BVF manifest
/// </summary>
public class SegmentServer : IMediaSourceProvider
{
    private const string TokenPrefix = "smart-branch";
    private const int AssetBlockHeaderSize = 32;

    private readonly ILogger<SegmentServer> _logger;
    private readonly ProfileResolver _profileResolver;
    private readonly Dictionary<string, BranchManifest> _bvfManifestCache = new();

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
    private BranchManifest GetBvfManifest(string bvfPath)
    {
        if (_bvfManifestCache.TryGetValue(bvfPath, out var cached))
            return cached;

        try
        {
            var manifest = BVFReader.LoadBvfManifest(bvfPath);
            _bvfManifestCache[bvfPath] = manifest;
            return manifest;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to read BVF manifest from {Path}", bvfPath);
            throw;
        }
    }

    /// <summary>
    /// Clears all caches (call when library changes).
    /// </summary>
    public void ClearCache()
    {
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
    public List<ResolvedSegment> ResolveAllSegments(string bvfPath, UserDto user)
    {
        var manifest = GetBvfManifest(bvfPath);
        var profileKey = _profileResolver.ResolveProfile(user, manifest);
        return ResolveAllSegmentsForProfile(bvfPath, profileKey);
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
    public BranchManifest GetManifestRaw(string bvfPath)
    {
        return GetBvfManifest(bvfPath);
    }

    /// <summary>
    /// Provides media sources for BVF content.
    /// When a movie has a .bvf file, we add one "Smart Branch" source per
    /// manifest profile. The chosen profile is encoded in the OpenToken.
    /// </summary>
    public Task<IEnumerable<MediaSourceInfo>> GetMediaSources(
        BaseItem item,
        CancellationToken cancellationToken)
    {
        if (Plugin.Instance?.Configuration.Enabled == false)
            return Task.FromResult(Enumerable.Empty<MediaSourceInfo>());

        if (item == null || string.IsNullOrEmpty(item.Path))
            return Task.FromResult(Enumerable.Empty<MediaSourceInfo>());

        try
        {
            var moviePath = item.Path;
            var bvfPath = FindBvfFile(moviePath);

            if (bvfPath == null)
                return Task.FromResult(Enumerable.Empty<MediaSourceInfo>());

            var manifest = GetBvfManifest(bvfPath);
            var defaultProfile = GetDefaultProfile(manifest);
            var profiles = manifest.Profiles.Keys
                .OrderByDescending(profile => string.Equals(profile, defaultProfile, StringComparison.Ordinal))
                .ThenBy(profile => profile, StringComparer.Ordinal)
                .DefaultIfEmpty(defaultProfile)
                .Select(profile => CreateMediaSourceInfo(bvfPath, profile));

            return Task.FromResult<IEnumerable<MediaSourceInfo>>(profiles.ToList());
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to get media sources for item {ItemId}", item.Id);
            return Task.FromResult(Enumerable.Empty<MediaSourceInfo>());
        }
    }

    /// <summary>
    /// Opens the selected BVF media source. Jellyfin's IMediaSourceProvider API
    /// does not pass the requesting user into this call, so the profile is encoded
    /// in the media source id generated by <see cref="GetMediaSources"/>.
    /// </summary>
    public Task<ILiveStream> OpenMediaSource(
        string openToken,
        List<ILiveStream> currentLiveStreams,
        CancellationToken cancellationToken)
    {
        if (Plugin.Instance?.Configuration.Enabled == false)
            throw new InvalidOperationException("Smart Branching is disabled.");

        try
        {
            var (bvfFile, profileKey) = DecodeMediaSourceToken(openToken);
            if (!File.Exists(bvfFile))
                throw new FileNotFoundException($"BVF file not found: {bvfFile}");

            var resolvedSegments = ResolveAllSegmentsForProfile(bvfFile, profileKey);
            if (resolvedSegments.Count == 0)
                throw new InvalidOperationException("No playable segments found");

            var liveStream = new BvfLiveStream(
                CreateMediaSourceInfo(bvfFile, profileKey),
                () => BuildResolvedStream(bvfFile, resolvedSegments));
            return Task.FromResult<ILiveStream>(liveStream);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to open BVF media source");
            throw;
        }
    }

    private static Stream BuildResolvedStream(string bvfPath, IEnumerable<ResolvedSegment> segments)
    {
        var output = new MemoryStream();
        foreach (var segment in segments)
        {
            var bvfSegment = new BVFSegment(
                0,
                0,
                string.Empty,
                segment.AudioHash,
                segment.SegmentId,
                segment.DurationMs,
                segment.DataOffset,
                segment.DataLength);
            var block = BVFReader.ReadSegmentData(bvfPath, bvfSegment);
            var payload = ExtractMediaPayload(block);
            output.Write(payload, 0, payload.Length);
        }

        output.Seek(0, SeekOrigin.Begin);
        return output;
    }

    private static byte[] ExtractMediaPayload(byte[] block)
    {
        if (block.Length <= AssetBlockHeaderSize)
            return Array.Empty<byte>();

        var payload = new byte[block.Length - AssetBlockHeaderSize];
        Buffer.BlockCopy(block, AssetBlockHeaderSize, payload, 0, payload.Length);
        return payload;
    }

    private List<ResolvedSegment> ResolveAllSegmentsForProfile(string bvfPath, string profileKey)
    {
        var manifest = GetBvfManifest(bvfPath);
        var index = BVFReader.GetSegments(bvfPath)
            .ToDictionary(s => s.segmentId, StringComparer.Ordinal);
        var resolved = new List<ResolvedSegment>();

        foreach (var segment in manifest.Segments)
        {
            if (segment.IsFiller)
                continue;

            var action = "play";
            var targetSegmentId = segment.Id;
            if (segment.Profiles.TryGetValue(profileKey, out var profileAction))
            {
                action = string.IsNullOrEmpty(profileAction.Action) ? "play" : profileAction.Action;
                targetSegmentId = string.IsNullOrEmpty(profileAction.SegmentId)
                    ? segment.Id
                    : profileAction.SegmentId;
            }

            if (string.Equals(action, "skip", StringComparison.OrdinalIgnoreCase))
                continue;

            if (!index.TryGetValue(targetSegmentId, out var target))
            {
                _logger.LogWarning(
                    "BVF segment {SegmentId} resolves to missing target {TargetSegmentId}",
                    segment.Id,
                    targetSegmentId);
                continue;
            }

            resolved.Add(new ResolvedSegment
            {
                Source = segment,
                ResolvedPath = $"bvf://{bvfPath}?seg_id={targetSegmentId}&offset={target.dataOffset}&length={target.dataLength}",
                IsSwapped = string.Equals(action, "swap", StringComparison.OrdinalIgnoreCase),
                Action = action,
                SwapType = string.Equals(action, "swap", StringComparison.OrdinalIgnoreCase) ? "filler" : "original",
                SegmentId = targetSegmentId,
                DataOffset = target.dataOffset,
                DataLength = target.dataLength,
                DurationMs = target.durationMs,
                AudioHash = target.audioHash,
            });
        }

        _logger.LogInformation(
            "Resolved {Total} segments for {Movie} (profile: {Profile}, swapped: {Swapped}, skipped: {Skipped}, muted: {Muted})",
            resolved.Count,
            manifest.MovieId,
            profileKey,
            resolved.Count(s => s.IsSwapped),
            manifest.Segments.Count(s => !s.IsFiller) - resolved.Count,
            resolved.Count(s => string.Equals(s.Action, "mute", StringComparison.OrdinalIgnoreCase)));

        return resolved;
    }

    private static string GetDefaultProfile(BranchManifest manifest)
    {
        var configured = Plugin.Instance?.Configuration?.DefaultProfile;
        if (!string.IsNullOrEmpty(configured) && manifest.Profiles.ContainsKey(configured))
            return configured;

        foreach (var candidate in new[] { "adult", "teen_m", "teen_f", "teen", "child" })
        {
            if (manifest.Profiles.ContainsKey(candidate))
                return candidate;
        }

        return manifest.Profiles.Keys.FirstOrDefault() ?? "adult";
    }

    private static MediaSourceInfo CreateMediaSourceInfo(string bvfPath, string profileKey)
    {
        var token = EncodeMediaSourceToken(bvfPath, profileKey);
        return new MediaSourceInfo
        {
            Id = token,
            Name = $"Smart Branch ({profileKey})",
            Path = bvfPath,
            Container = "mp4",
            MediaStreams = new List<MediaStream>
            {
                new MediaStream
                {
                    Type = MediaStreamType.Video,
                    Codec = "h264",
                },
            },
            SupportsProbing = false,
            IsRemote = false,
            RequiresOpening = true,
            OpenToken = token,
            RequiresClosing = true,
            SupportsDirectPlay = false,
            SupportsDirectStream = true,
            SupportsTranscoding = true,
        };
    }

    private static string EncodeMediaSourceToken(string bvfPath, string profileKey)
    {
        return $"{TokenPrefix}:{Base64UrlEncode(bvfPath)}:{Base64UrlEncode(profileKey)}";
    }

    private static (string BvfPath, string ProfileKey) DecodeMediaSourceToken(string token)
    {
        var parts = token.Split(':');
        if (parts.Length != 3 || !string.Equals(parts[0], TokenPrefix, StringComparison.Ordinal))
            throw new ArgumentException($"Invalid BVF media source token: {token}", nameof(token));

        return (Base64UrlDecode(parts[1]), Base64UrlDecode(parts[2]));
    }

    private static string Base64UrlEncode(string value)
    {
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(value))
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');
    }

    private static string Base64UrlDecode(string value)
    {
        var padded = value.Replace('-', '+').Replace('_', '/');
        padded = padded.PadRight(padded.Length + ((4 - padded.Length % 4) % 4), '=');
        return Encoding.UTF8.GetString(Convert.FromBase64String(padded));
    }

    private sealed class BvfLiveStream : ILiveStream
    {
        private readonly Func<Stream> _streamFactory;

        public BvfLiveStream(MediaSourceInfo mediaSource, Func<Stream> streamFactory)
        {
            MediaSource = mediaSource;
            _streamFactory = streamFactory;
        }

        public int ConsumerCount { get; set; }
        public string OriginalStreamId { get; set; } = string.Empty;
        public string TunerHostId => "SmartBranching";
        public bool EnableStreamSharing => false;
        public MediaSourceInfo MediaSource { get; set; }
        public string UniqueId { get; } = Guid.NewGuid().ToString("N");

        public Task Open(CancellationToken openCancellationToken)
        {
            return Task.CompletedTask;
        }

        public Task Close()
        {
            return Task.CompletedTask;
        }

        public Stream GetStream()
        {
            return _streamFactory();
        }

        public void Dispose()
        {
        }
    }
}
