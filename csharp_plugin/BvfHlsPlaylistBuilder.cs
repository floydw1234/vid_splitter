using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Builds an HLS VOD media playlist that references profile-resolved BVF segments
/// as fMP4 parts served directly from the container (no remux, no cache files).
/// </summary>
internal static class BvfHlsPlaylistBuilder
{
    /// <summary>
    /// Maximum allowed gap (seconds) between one segment's source end time and the
    /// next segment's source start time before a discontinuity is declared.
    /// </summary>
    private const double TimelineGapToleranceSeconds = 0.01;

    /// <summary>
    /// Builds the playlist text. Segment URIs are relative to the playlist URL, so
    /// <paramref name="querySuffix"/> (e.g. "?api_key=...") must carry any auth.
    /// </summary>
    public static string Build(IReadOnlyList<ResolvedSegment> segments, string querySuffix)
    {
        ArgumentNullException.ThrowIfNull(segments);
        querySuffix ??= string.Empty;

        var maxDurationSeconds = 1.0;
        foreach (var segment in segments)
            maxDurationSeconds = Math.Max(maxDurationSeconds, segment.DurationMs / 1000.0);

        var builder = new StringBuilder(segments.Count * 48 + 256);
        builder.AppendLine("#EXTM3U");
        builder.AppendLine("#EXT-X-VERSION:7");
        builder.AppendLine("#EXT-X-PLAYLIST-TYPE:VOD");
        builder.AppendLine("#EXT-X-INDEPENDENT-SEGMENTS");
        builder.Append("#EXT-X-TARGETDURATION:")
            .Append(((int)Math.Ceiling(maxDurationSeconds)).ToString(CultureInfo.InvariantCulture))
            .AppendLine();
        builder.AppendLine("#EXT-X-MEDIA-SEQUENCE:0");
        builder.Append("#EXT-X-MAP:URI=\"init.mp4").Append(querySuffix).AppendLine("\"");

        for (var i = 0; i < segments.Count; i++)
        {
            if (i > 0 && !IsTimelineContinuous(segments[i - 1], segments[i]))
                builder.AppendLine("#EXT-X-DISCONTINUITY");

            var durationSeconds = segments[i].DurationMs / 1000.0;
            builder.Append("#EXTINF:")
                .Append(durationSeconds.ToString("0.###", CultureInfo.InvariantCulture))
                .AppendLine(",");
            builder.Append(i.ToString(CultureInfo.InvariantCulture))
                .Append(".m4s")
                .AppendLine(querySuffix);
        }

        builder.AppendLine("#EXT-X-ENDLIST");
        return builder.ToString();
    }

    /// <summary>
    /// Media timestamps are continuous only when neither segment is swapped filler
    /// and no source content was skipped between them.
    /// </summary>
    internal static bool IsTimelineContinuous(ResolvedSegment previous, ResolvedSegment current)
    {
        if (previous.IsSwapped || current.IsSwapped)
            return false;

        return Math.Abs(previous.Source.EndTime - current.Source.StartTime) <= TimelineGapToleranceSeconds;
    }
}
