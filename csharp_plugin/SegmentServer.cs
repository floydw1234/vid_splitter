using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using Jellyfin.Data.Enums;
using MediaBrowser.Model.MediaInfo;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Serves resolved video segments from BVF files through Jellyfin's video pipeline.
/// </summary>
public class SegmentServer : IMediaSourceProvider
{
    private const string TokenPrefix = "smart-branch";
    internal const string JellyfinUserIdClaimType = "Jellyfin-UserId";
    private static readonly HashSet<string> RuntimeSupportedActions = new(StringComparer.OrdinalIgnoreCase)
    {
        "play",
        "swap",
        "skip",
    };

    private readonly ILogger<SegmentServer> _logger;
    private readonly ProfileResolver _profileResolver;
    private readonly BvfManifestCache _bvfManifestCache = new();
    private readonly System.Collections.Concurrent.ConcurrentDictionary<string, BvfHlsTimeline> _hlsTimelines = new();
    private readonly BvfHlsFragmentCache _hlsFragmentCache = new();
    private readonly IHttpContextAccessor? _httpContextAccessor;

    public SegmentServer(
        ILogger<SegmentServer> logger,
        IHttpContextAccessor? httpContextAccessor = null)
    {
        _logger = logger;
        _profileResolver = new ProfileResolver();
        _httpContextAccessor = httpContextAccessor;
    }

    private BranchManifest GetBvfManifest(string bvfPath)
    {
        try
        {
            var result = _bvfManifestCache.GetOrLoad(bvfPath, path => BVFReader.LoadBvfManifest(path));
            switch (result.Disposition)
            {
                case BvfManifestCache.CacheDisposition.Hit:
                    _logger.LogDebug("BVF cache hit for {Path}", bvfPath);
                    break;
                case BvfManifestCache.CacheDisposition.Miss:
                    _logger.LogDebug("BVF cache miss for {Path}", bvfPath);
                    break;
                case BvfManifestCache.CacheDisposition.Invalidated:
                    _logger.LogInformation(
                        "BVF cache invalidated for {Path} because file metadata changed",
                        bvfPath);
                    break;
            }

            return result.Manifest;
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to read BVF manifest from {Path}", bvfPath);
            throw;
        }
    }

    public void ClearCache()
    {
        _bvfManifestCache.Clear();
        _hlsTimelines.Clear();
        _hlsFragmentCache.Clear();
        _logger.LogInformation("BVF manifest, HLS timeline, and fragment caches cleared");
    }

    public string? FindBvfFile(string itemPath)
    {
        if (string.IsNullOrEmpty(itemPath))
            return null;

        if (BvfFormatRegistration.IsBvfPath(itemPath))
            return File.Exists(itemPath) ? itemPath : null;

        var dir = Path.GetDirectoryName(itemPath);
        if (dir == null)
            return null;

        var stem = Path.GetFileNameWithoutExtension(itemPath);
        var bvfPath = Path.Combine(dir, stem + BvfFormatRegistration.Extension);
        return File.Exists(bvfPath) ? bvfPath : null;
    }

    public List<ResolvedSegment> ResolveAllSegments(string bvfPath, UserDto user)
    {
        var manifest = GetBvfManifest(bvfPath);
        var avoided = TopicPlayback.NormalizeTopics(_profileResolver.GetAvoidedTopics(user));
        var hitAction = _profileResolver.GetHitAction(user);
        if (avoided.Count > 0)
            return ResolveAllSegmentsForTopics(bvfPath, avoided, hitAction);

        var profileKey = _profileResolver.ResolveProfile(user, manifest);
        return ResolveAllSegmentsForProfile(bvfPath, profileKey, hitAction);
    }

    public bool HasBvfFile(string moviePath) => FindBvfFile(moviePath) != null;

    public BranchManifest GetManifestRaw(string bvfPath) => GetBvfManifest(bvfPath);

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
            var bvfPath = FindBvfFile(item.Path);
            if (bvfPath == null)
                return Task.FromResult(Enumerable.Empty<MediaSourceInfo>());

            var manifest = GetBvfManifest(bvfPath);
            long? durationTicks = item.RunTimeTicks;
            if (durationTicks is null or <= 0 && manifest.DurationSeconds > 0)
                durationTicks = (long)(manifest.DurationSeconds * TimeSpan.TicksPerSecond);

            if (TryResolveRequestProfile(manifest, out var resolvedProfile))
            {
                var resolvedSegments = ResolveSegmentsForRequest(bvfPath, resolvedProfile);
                var streamingSource = TryCreateHlsMediaSource(
                    bvfPath,
                    resolvedProfile,
                    resolvedSegments,
                    durationTicks);
                if (streamingSource != null)
                {
                    return Task.FromResult<IEnumerable<MediaSourceInfo>>(
                        new[] { streamingSource });
                }

                return Task.FromResult<IEnumerable<MediaSourceInfo>>(
                    new[]
                    {
                        CreateMediaSourceInfo(
                            bvfPath,
                            resolvedProfile,
                            resolvedSegments,
                            isAutomaticSelection: true,
                            fallbackRunTimeTicks: durationTicks)
                    });
            }

            var defaultProfile = GetDefaultProfile(manifest);
            var profiles = manifest.Profiles.Keys
                .OrderByDescending(profile => string.Equals(profile, defaultProfile, StringComparison.Ordinal))
                .ThenBy(profile => profile, StringComparer.Ordinal)
                .DefaultIfEmpty(defaultProfile)
                .Select(profile => CreateMediaSourceInfo(
                    bvfPath,
                    profile,
                    ResolveAllSegmentsForProfile(bvfPath, profile),
                    fallbackRunTimeTicks: durationTicks));

            return Task.FromResult<IEnumerable<MediaSourceInfo>>(profiles.ToList());
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to get media sources for item {ItemId}", item.Id);
            return Task.FromResult(Enumerable.Empty<MediaSourceInfo>());
        }
    }

    public Task<ILiveStream> OpenMediaSource(
        string openToken,
        List<ILiveStream> currentLiveStreams,
        CancellationToken cancellationToken)
    {
        throw new InvalidOperationException(
            "Smart Branching serves HLS directly and does not remux a playback cache.");
    }

    /// <summary>
    /// Builds a media source whose Path is an HLS playlist URL served by
    /// <see cref="BvfHlsController"/>. Stock clients direct-play the playlist via
    /// hls.js (MSE), so segments stream straight from the BVF container with no
    /// remux and no duplicate cache files. Returns null when the request context
    /// is missing the pieces needed to build a client-reachable URL.
    /// </summary>
    private MediaSourceInfo? TryCreateHlsMediaSource(
        string bvfPath,
        string profileKey,
        IReadOnlyList<ResolvedSegment> resolvedSegments,
        long? fallbackRunTimeTicks)
    {
        var httpContext = _httpContextAccessor?.HttpContext;
        var request = httpContext?.Request;
        if (request == null || !request.Host.HasValue || resolvedSegments.Count == 0)
            return null;

        var accessToken = TryGetRequestAccessToken(httpContext!);
        if (string.IsNullOrEmpty(accessToken))
            return null;

        var token = EncodeMediaSourceToken(bvfPath, profileKey);
        var playlistUrl =
            $"{request.Scheme}://{request.Host.Value}{request.PathBase.Value}" +
            $"/SmartBranching/hls/{token}/main.m3u8?api_key={Uri.EscapeDataString(accessToken)}";

        var source = CreateMediaSourceInfo(
            bvfPath,
            profileKey,
            resolvedSegments,
            fallbackRunTimeTicks: fallbackRunTimeTicks);

        source.Name = $"Smart Branch (stream: {profileKey})";
        source.Path = playlistUrl;
        source.Protocol = MediaProtocol.Http;
        source.Container = "mp4";
        // isHls() in jellyfin-web keys off TranscodingSubProtocol even for DirectPlay,
        // which is what routes playback through hls.js instead of a plain <video> src.
        source.TranscodingSubProtocol = MediaStreamProtocol.hls;
        source.IsRemote = false;
        source.Size = null;
        source.SupportsProbing = false;
        source.RequiresOpening = false;
        source.RequiresClosing = false;
        source.SupportsDirectPlay = true;
        source.SupportsDirectStream = false;
        source.SupportsTranscoding = false;

        return source;
    }

    private static string? TryGetRequestAccessToken(HttpContext httpContext)
    {
        var fromClaim = httpContext.User?.FindFirstValue("Jellyfin-Token");
        if (!string.IsNullOrWhiteSpace(fromClaim))
            return fromClaim;

        var fromQuery = FirstNonEmpty(
            httpContext.Request.Query["api_key"].FirstOrDefault(),
            httpContext.Request.Query["ApiKey"].FirstOrDefault());
        if (!string.IsNullOrWhiteSpace(fromQuery))
            return fromQuery;

        var fromHeader = FirstNonEmpty(
            httpContext.Request.Headers["X-Emby-Token"].FirstOrDefault(),
            httpContext.Request.Headers["X-MediaBrowser-Token"].FirstOrDefault());
        if (!string.IsNullOrWhiteSpace(fromHeader))
            return fromHeader;

        var authorization = httpContext.Request.Headers.Authorization.FirstOrDefault();
        if (!string.IsNullOrEmpty(authorization))
        {
            var match = System.Text.RegularExpressions.Regex.Match(
                authorization,
                "Token=\"?([^\",]+)\"?",
                System.Text.RegularExpressions.RegexOptions.IgnoreCase);
            if (match.Success)
                return match.Groups[1].Value;
        }

        return null;
    }

    internal static (string BvfPath, string ProfileKey) DecodeToken(string token)
        => DecodeMediaSourceToken(token);

    internal List<ResolvedSegment> ResolveSegmentsForProfile(string bvfPath, string profileKey)
        => ResolveSegmentsForRequest(bvfPath, profileKey);

    private List<ResolvedSegment> ResolveSegmentsForRequest(string bvfPath, string profileKey)
    {
        var user = TryGetAuthenticatedUser();
        if (user != null)
        {
            var avoided = TopicPlayback.NormalizeTopics(_profileResolver.GetAvoidedTopics(user));
            var hitAction = _profileResolver.GetHitAction(user);
            if (avoided.Count > 0)
                return ResolveAllSegmentsForTopics(bvfPath, avoided, hitAction);

            return ResolveAllSegmentsForProfile(bvfPath, profileKey, hitAction);
        }

        return ResolveAllSegmentsForProfile(bvfPath, profileKey);
    }

    /// <summary>
    /// Returns the cached HLS timeline (exact per-segment durations and cumulative
    /// timestamp offsets) for a resolved profile, building it on first use.
    /// </summary>
    internal BvfHlsTimeline GetHlsTimeline(
        string bvfPath,
        string profileKey,
        IReadOnlyList<ResolvedSegment> segments)
    {
        var fileInfo = new FileInfo(bvfPath);
        var cacheKey = BuildTimelineCacheKey(bvfPath, fileInfo, segments);
        return _hlsTimelines.GetOrAdd(cacheKey, _ => BuildHlsTimeline(bvfPath, segments));
    }

    internal string GetTimelineCacheKey(string bvfPath, IReadOnlyList<ResolvedSegment> segments)
    {
        var fileInfo = new FileInfo(bvfPath);
        return BuildTimelineCacheKey(bvfPath, fileInfo, segments);
    }

    /// <summary>
    /// Returns an assembled HLS media fragment, using the LRU cache and prefetching
    /// ~5 seconds ahead on a background thread (CD-style read-ahead).
    /// </summary>
    internal byte[] GetHlsMediaFragment(
        string bvfPath,
        IReadOnlyList<ResolvedSegment> segments,
        BvfHlsTimeline timeline,
        int partIndex)
    {
        var streamKey = GetTimelineCacheKey(bvfPath, segments);
        var fragmentKey = BvfHlsFragmentCache.FragmentKey(streamKey, partIndex);
        var bytes = _hlsFragmentCache.GetOrAdd(
            fragmentKey,
            () => BvfHlsMediaAssembler.Assemble(bvfPath, segments, timeline, partIndex));

        _hlsFragmentCache.PrefetchAhead(
            streamKey,
            timeline,
            partIndex + 1,
            i => BvfHlsMediaAssembler.Assemble(bvfPath, segments, timeline, i));

        return bytes;
    }

    internal void WarmHlsFragmentCache(
        string bvfPath,
        IReadOnlyList<ResolvedSegment> segments,
        BvfHlsTimeline timeline)
    {
        var streamKey = GetTimelineCacheKey(bvfPath, segments);
        _hlsFragmentCache.PrefetchAhead(
            streamKey,
            timeline,
            0,
            i => BvfHlsMediaAssembler.Assemble(bvfPath, segments, timeline, i));
    }

    private static string BuildTimelineCacheKey(
        string bvfPath,
        FileInfo fileInfo,
        IReadOnlyList<ResolvedSegment> segments)
    {
        var builder = new StringBuilder(segments.Count * 24);
        foreach (var segment in segments)
            builder.Append(segment.SegmentId).Append('|').Append(segment.DataOffset).Append(';');

        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(builder.ToString())));
        return $"{bvfPath}|{fileInfo.LastWriteTimeUtc.Ticks}|{fileInfo.Length}|{digest[..16]}";
    }

    private static BvfHlsTimeline BuildHlsTimeline(string bvfPath, IReadOnlyList<ResolvedSegment> segments)
    {
        var firstPayload = BvfSegmentExtractor.ReadSegmentPayload(bvfPath, segments[0]);
        var (_, initLength) = Fmp4ConcatHelper.GetInitRange(firstPayload);
        if (initLength <= 0)
            throw new InvalidDataException("BVF segment payloads are not fMP4; cannot build an HLS timeline.");

        var tracks = Fmp4TimestampRewriter.ParseTracks(firstPayload.AsSpan(0, (int)initLength));
        if (tracks.Count == 0)
            throw new InvalidDataException("No tracks found in BVF init segment.");

        var video = tracks.FirstOrDefault(track => track.IsVideo);
        if (video.TrackId == 0)
            video = tracks[0];

        var parts = new List<BvfHlsPart>();
        ulong cumulativeTicks = 0;

        for (var i = 0; i < segments.Count; i++)
        {
            var payload = BvfSegmentExtractor.ReadSegmentPayload(bvfPath, segments[i]);
            var fragments = Fmp4ConcatHelper.GetFragmentRanges(payload);
            if (fragments.Count == 0)
                continue;

            using var payloadStream = new MemoryStream(payload, writable: false);
            for (var fragmentIndex = 0; fragmentIndex < fragments.Count; fragmentIndex++)
            {
                var (start, length) = fragments[fragmentIndex];
                var fragmentStartTicks = cumulativeTicks;
                var fallbackSeconds = segments[i].DurationMs / 1000.0 / Math.Max(fragments.Count, 1);
                var ticks = Fmp4TimestampRewriter.SumTrackDurationTicks(payloadStream, start, length, video.TrackId);
                if (ticks == 0)
                    ticks = (ulong)Math.Round(fallbackSeconds * video.Timescale);
                var durationSeconds = ticks > 0
                    ? ticks / (double)video.Timescale
                    : fallbackSeconds;

                parts.Add(new BvfHlsPart
                {
                    ResolvedIndex = i,
                    PayloadStart = start,
                    PayloadLength = length,
                    TimestampOffsetTicks = fragmentStartTicks,
                    DurationSeconds = durationSeconds,
                    ClampAudio = true,
                    SegmentStart = fragmentIndex == 0,
                });
                cumulativeTicks += ticks;
            }
        }

        if (parts.Count == 0)
            throw new InvalidDataException("No fMP4 fragments found in resolved BVF segments.");

        return new BvfHlsTimeline
        {
            Tracks = tracks,
            VideoTrackId = video.TrackId,
            VideoTimescale = video.Timescale,
            Parts = parts,
            SegmentDurationsSeconds = parts.Select(part => part.DurationSeconds).ToArray(),
        };
    }

    private List<ResolvedSegment> ResolveAllSegmentsForProfile(
        string bvfPath,
        string profileKey,
        string? preferredAction = null)
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

            if (!string.IsNullOrEmpty(preferredAction))
            {
                action = TopicPlayback.ApplyPreferredAction(
                    action,
                    preferredAction,
                    TopicPlayback.HasSwapTarget(segment));
                if (!string.Equals(action, "swap", StringComparison.OrdinalIgnoreCase))
                    targetSegmentId = segment.Id;
            }

            if (!RuntimeSupportedActions.Contains(action))
                throw new InvalidOperationException(
                    $"Unsupported BVF action for runtime playback: '{action}'. " +
                    "Supported actions: play, skip, swap.");

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
            "Resolved {Total} segments for {Movie} (profile: {Profile}, swapped: {Swapped}, skipped: {Skipped})",
            resolved.Count,
            manifest.MovieId,
            profileKey,
            resolved.Count(s => s.IsSwapped),
            manifest.Segments.Count(s => !s.IsFiller) - resolved.Count);

        return resolved;
    }

    private List<ResolvedSegment> ResolveAllSegmentsForTopics(
        string bvfPath,
        IReadOnlySet<string> avoidedTopics,
        string preferredAction)
    {
        var manifest = GetBvfManifest(bvfPath);
        var index = BVFReader.GetSegments(bvfPath)
            .ToDictionary(s => s.segmentId, StringComparer.Ordinal);
        var resolved = new List<ResolvedSegment>();
        var skipped = 0;

        foreach (var segment in manifest.Segments)
        {
            if (segment.IsFiller)
                continue;

            var action = TopicPlayback.ResolveAction(segment, avoidedTopics, preferredAction);
            if (!RuntimeSupportedActions.Contains(action))
                throw new InvalidOperationException(
                    $"Unsupported BVF action for runtime playback: '{action}'. " +
                    "Supported actions: play, skip, swap.");

            if (string.Equals(action, "skip", StringComparison.OrdinalIgnoreCase))
            {
                skipped++;
                continue;
            }

            var targetSegmentId = TopicPlayback.ResolveTargetId(segment, action);
            if (!index.TryGetValue(targetSegmentId, out var target))
            {
                if (string.Equals(action, "swap", StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException(
                        $"BVF segment '{segment.Id}' resolves to missing target '{targetSegmentId}'.");
                }

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
            "Resolved {Total} segments for {Movie} (topics: {Topics}, swapped: {Swapped}, skipped: {Skipped})",
            resolved.Count,
            manifest.MovieId,
            string.Join(",", avoidedTopics),
            resolved.Count(s => s.IsSwapped),
            skipped);

        return resolved;
    }

    private bool TryResolveRequestProfile(BranchManifest manifest, out string profileKey)
    {
        profileKey = string.Empty;
        var user = TryGetAuthenticatedUser();
        if (user == null)
            return false;

        profileKey = _profileResolver.ResolveProfile(user, manifest);
        return !string.IsNullOrEmpty(profileKey);
    }

    private UserDto? TryGetAuthenticatedUser()
    {
        var httpContext = _httpContextAccessor?.HttpContext;
        if (httpContext == null)
            return null;

        var claimsPrincipal = httpContext.User;
        var userId = FirstNonEmpty(
            claimsPrincipal?.FindFirstValue(JellyfinUserIdClaimType),
            claimsPrincipal?.FindFirstValue(ClaimTypes.NameIdentifier),
            claimsPrincipal?.FindFirstValue("UserId"),
            claimsPrincipal?.FindFirstValue("user_id"),
            httpContext.Request.Query["UserId"].FirstOrDefault(),
            httpContext.Request.Query["userId"].FirstOrDefault(),
            TryReadUserIdFromRequestBody(httpContext.Request));

        if (!Guid.TryParse(userId, out var parsedUserId) || parsedUserId == Guid.Empty)
            return null;

        var userName = FirstNonEmpty(
            claimsPrincipal?.Identity?.Name,
            claimsPrincipal?.FindFirstValue(ClaimTypes.Name),
            claimsPrincipal?.FindFirstValue("JellyfinUserName"),
            claimsPrincipal?.FindFirstValue("username"));

        return new UserDto
        {
            Id = parsedUserId,
            Name = userName ?? parsedUserId.ToString(),
        };
    }

    private static string? TryReadUserIdFromRequestBody(HttpRequest request)
    {
        if (!HttpMethods.IsPost(request.Method) && !HttpMethods.IsPut(request.Method))
            return null;

        try
        {
            request.EnableBuffering();
            if (!request.Body.CanSeek)
                return null;

            var originalPosition = request.Body.Position;
            request.Body.Position = 0;
            using var reader = new StreamReader(request.Body, Encoding.UTF8, detectEncodingFromByteOrderMarks: false, bufferSize: 1024, leaveOpen: true);
            var json = reader.ReadToEnd();
            request.Body.Position = originalPosition;

            if (string.IsNullOrWhiteSpace(json))
                return null;

            using var document = JsonDocument.Parse(json);
            foreach (var propertyName in new[] { "UserId", "userId" })
            {
                if (!document.RootElement.TryGetProperty(propertyName, out var property))
                    continue;

                return property.ValueKind switch
                {
                    JsonValueKind.String => property.GetString(),
                    _ => property.GetRawText().Trim('"'),
                };
            }
        }
        catch
        {
            // Body may already be consumed by model binding.
        }

        return null;
    }

    private static string? FirstNonEmpty(params string?[] values)
        => values.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value));

    private static string GetDefaultProfile(BranchManifest manifest)
    {
        foreach (var candidate in new[] { "adult", "teen_m", "teen_f", "teen", "child" })
        {
            if (manifest.Profiles.ContainsKey(candidate))
                return candidate;
        }

        return manifest.Profiles.Keys.FirstOrDefault() ?? "adult";
    }

    private long? ComputeProfileRunTimeTicks(IReadOnlyList<ResolvedSegment> segments)
    {
        if (segments.Count == 0)
            return null;

        long totalMs = 0;
        foreach (var segment in segments)
            totalMs += (long)segment.DurationMs;

        if (totalMs <= 0)
            return null;

        return checked(totalMs * TimeSpan.TicksPerMillisecond);
    }

    private MediaSourceInfo CreateMediaSourceInfo(
        string bvfPath,
        string profileKey,
        IReadOnlyList<ResolvedSegment> resolvedSegments,
        bool isAutomaticSelection = false,
        long? fallbackRunTimeTicks = null)
    {
        return new MediaSourceInfo
        {
            // HLS helpers Guid.Parse(MediaSourceId); keep this a real GUID.
            Id = CreateMediaSourceId(bvfPath, profileKey),
            Name = isAutomaticSelection ? $"Smart Branch (auto: {profileKey})" : $"Smart Branch ({profileKey})",
            Path = bvfPath,
            Protocol = MediaProtocol.File,
            Container = "mp4",
            VideoType = VideoType.VideoFile,
            RunTimeTicks = ComputeProfileRunTimeTicks(resolvedSegments) ?? fallbackRunTimeTicks,
            MediaStreams = new List<MediaStream>
            {
                new MediaStream
                {
                    Type = MediaStreamType.Video,
                    Codec = "h264",
                    Width = 1920,
                    Height = 1080,
                    Index = 0,
                    IsDefault = true,
                },
                new MediaStream
                {
                    Type = MediaStreamType.Audio,
                    Codec = "aac",
                    Channels = 2,
                    SampleRate = 48000,
                    Index = 1,
                    IsDefault = true,
                },
            },
            Bitrate = 8_000_000,
            SupportsProbing = false,
            IsRemote = false,
            RequiresOpening = false,
            OpenToken = EncodeMediaSourceToken(bvfPath, profileKey),
            RequiresClosing = false,
            SupportsDirectPlay = true,
            SupportsDirectStream = false,
            SupportsTranscoding = false,
        };
    }

    private static string CreateMediaSourceId(string bvfPath, string profileKey)
    {
        var hash = MD5.HashData(Encoding.UTF8.GetBytes($"{TokenPrefix}|{bvfPath}|{profileKey}"));
        return new Guid(hash).ToString("N");
    }

    private static string EncodeMediaSourceToken(string bvfPath, string profileKey)
        => $"{TokenPrefix}:{Base64UrlEncode(bvfPath)}:{Base64UrlEncode(profileKey)}";

    private static (string BvfPath, string ProfileKey) DecodeMediaSourceToken(string token)
    {
        var payload = token;
        var prefixIndex = payload.IndexOf($"{TokenPrefix}:", StringComparison.Ordinal);
        if (prefixIndex > 0)
            payload = payload[prefixIndex..];

        var parts = payload.Split(':');
        if (parts.Length != 3 || !string.Equals(parts[0], TokenPrefix, StringComparison.Ordinal))
            throw new ArgumentException($"Invalid BVF media source token: {token}", nameof(token));

        return (Base64UrlDecode(parts[1]), Base64UrlDecode(parts[2]));
    }

    private static string Base64UrlEncode(string value)
        => Convert.ToBase64String(Encoding.UTF8.GetBytes(value))
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');

    private static string Base64UrlDecode(string value)
    {
        var padded = value.Replace('-', '+').Replace('_', '/');
        padded = padded.PadRight(padded.Length + ((4 - padded.Length % 4) % 4), '=');
        return Encoding.UTF8.GetString(Convert.FromBase64String(padded));
    }
}
