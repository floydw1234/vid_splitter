using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Builds an HLS VOD media playlist that references profile-resolved BVF segments
/// as fMP4 parts served directly from the container (no remux, no cache files).
/// Served segments have their timestamps rewritten into one continuous timeline,
/// so the playlist needs no discontinuity tags.
/// </summary>
internal static class BvfHlsPlaylistBuilder
{
    /// <summary>
    /// Builds the playlist text. Segment URIs are relative to the playlist URL, so
    /// <paramref name="querySuffix"/> (e.g. "?api_key=...") must carry any auth.
    /// <paramref name="exactDurationsSeconds"/> supplies precise per-segment video
    /// durations; entries fall back to the manifest's DurationMs when absent.
    /// </summary>
    public static string Build(
        IReadOnlyList<ResolvedSegment> segments,
        string querySuffix,
        IReadOnlyList<double>? exactDurationsSeconds = null)
    {
        ArgumentNullException.ThrowIfNull(segments);
        querySuffix ??= string.Empty;

        IReadOnlyList<double> durations;
        if (exactDurationsSeconds != null && exactDurationsSeconds.Count > 0)
        {
            durations = exactDurationsSeconds;
        }
        else
        {
            var fallback = new double[segments.Count];
            for (var i = 0; i < segments.Count; i++)
                fallback[i] = segments[i].DurationMs / 1000.0;
            durations = fallback;
        }

        var maxDurationSeconds = 1.0;
        foreach (var duration in durations)
            maxDurationSeconds = Math.Max(maxDurationSeconds, duration);

        var builder = new StringBuilder(durations.Count * 48 + 256);
        builder.AppendLine("#EXTM3U");
        builder.AppendLine("#EXT-X-VERSION:7");
        builder.AppendLine("#EXT-X-PLAYLIST-TYPE:VOD");
        builder.AppendLine("#EXT-X-INDEPENDENT-SEGMENTS");
        builder.Append("#EXT-X-TARGETDURATION:")
            .Append(((int)Math.Ceiling(maxDurationSeconds)).ToString(CultureInfo.InvariantCulture))
            .AppendLine();
        builder.AppendLine("#EXT-X-MEDIA-SEQUENCE:0");
        builder.Append("#EXT-X-MAP:URI=\"init.mp4").Append(querySuffix).AppendLine("\"");

        for (var i = 0; i < durations.Count; i++)
        {
            builder.Append("#EXTINF:")
                .Append(durations[i].ToString("0.######", CultureInfo.InvariantCulture))
                .AppendLine(",");
            builder.Append(i.ToString(CultureInfo.InvariantCulture))
                .Append(".m4s")
                .AppendLine(querySuffix);
        }

        builder.AppendLine("#EXT-X-ENDLIST");
        return builder.ToString();
    }
}
