using System;
using System.Linq;
using System.Text;
using Jellyfin.Plugin.SmartBranching;
using Jellyfin.Plugin.SmartBranching.Models;
using Xunit;

namespace SmartBranching.Plugin.Tests;

public class BvfHlsPlaylistBuilderTests
{
    [Fact]
    public void Build_WithContinuousSegments_HasNoDiscontinuities()
    {
        var segments = new[]
        {
            MakeSegment(startSec: 0, endSec: 5, durationMs: 5000),
            MakeSegment(startSec: 5, endSec: 10, durationMs: 5000),
            MakeSegment(startSec: 10, endSec: 12.5, durationMs: 2500),
        };

        var playlist = BvfHlsPlaylistBuilder.Build(segments, "?api_key=abc");

        Assert.StartsWith("#EXTM3U", playlist, StringComparison.Ordinal);
        Assert.Contains("#EXT-X-VERSION:7", playlist, StringComparison.Ordinal);
        Assert.Contains("#EXT-X-PLAYLIST-TYPE:VOD", playlist, StringComparison.Ordinal);
        Assert.Contains("#EXT-X-TARGETDURATION:5", playlist, StringComparison.Ordinal);
        Assert.Contains("#EXT-X-MAP:URI=\"init.mp4?api_key=abc\"", playlist, StringComparison.Ordinal);
        Assert.Contains("#EXTINF:5,", playlist, StringComparison.Ordinal);
        Assert.Contains("#EXTINF:2.5,", playlist, StringComparison.Ordinal);
        Assert.Contains("0.m4s?api_key=abc", playlist, StringComparison.Ordinal);
        Assert.Contains("2.m4s?api_key=abc", playlist, StringComparison.Ordinal);
        Assert.Contains("#EXT-X-ENDLIST", playlist, StringComparison.Ordinal);
        Assert.DoesNotContain("#EXT-X-DISCONTINUITY", playlist, StringComparison.Ordinal);
    }

    [Fact]
    public void Build_WithSkippedContentGap_InsertsDiscontinuity()
    {
        var segments = new[]
        {
            MakeSegment(startSec: 0, endSec: 5, durationMs: 5000),
            // Segment covering 5-10s was skipped by the profile.
            MakeSegment(startSec: 10, endSec: 15, durationMs: 5000),
        };

        var playlist = BvfHlsPlaylistBuilder.Build(segments, string.Empty);

        var lines = playlist.Split('\n', StringSplitOptions.RemoveEmptyEntries)
            .Select(line => line.TrimEnd('\r'))
            .ToArray();
        var discontinuityIndex = Array.IndexOf(lines, "#EXT-X-DISCONTINUITY");
        Assert.True(discontinuityIndex > 0, "expected a discontinuity tag");
        Assert.Equal("0.m4s", lines[discontinuityIndex - 1]);
        Assert.Equal(1, lines.Count(line => line == "#EXT-X-DISCONTINUITY"));
    }

    [Fact]
    public void Build_WithSwappedSegment_InsertsDiscontinuityAroundIt()
    {
        var segments = new[]
        {
            MakeSegment(startSec: 0, endSec: 5, durationMs: 5000),
            MakeSegment(startSec: 5, endSec: 10, durationMs: 5000, isSwapped: true),
            MakeSegment(startSec: 10, endSec: 15, durationMs: 5000),
        };

        var playlist = BvfHlsPlaylistBuilder.Build(segments, string.Empty);

        var count = playlist.Split('\n').Count(line => line.TrimEnd('\r') == "#EXT-X-DISCONTINUITY");
        Assert.Equal(2, count);
    }

    private static ResolvedSegment MakeSegment(
        double startSec,
        double endSec,
        ulong durationMs,
        bool isSwapped = false)
        => new()
        {
            Source = new Segment { StartTime = startSec, EndTime = endSec },
            DurationMs = durationMs,
            IsSwapped = isSwapped,
        };
}

public class Fmp4RangeTests
{
    [Fact]
    public void InitAndMediaRanges_SplitFragmentedMp4Cleanly()
    {
        if (!FfmpegTestHelpers.IsAvailable())
            return;

        var payload = FfmpegTestHelpers.CreateFragmentedMp4(TimeSpan.FromMilliseconds(200));

        var (initStart, initLength) = Fmp4ConcatHelper.GetInitRange(payload);
        Assert.Equal(0, initStart);
        Assert.True(initLength > 0, "expected a non-empty init range");
        Assert.Equal("ftyp", Encoding.ASCII.GetString(payload, 4, 4));

        var (mediaStart, mediaLength) = Fmp4ConcatHelper.GetMediaRange(payload);
        Assert.Equal(initLength, mediaStart);
        Assert.True(mediaLength > 0, "expected a non-empty media range");
        Assert.True(mediaStart + mediaLength <= payload.Length);
    }

    [Fact]
    public void GetInitRange_NonFmp4Payload_ReturnsEmpty()
    {
        var payload = Encoding.ASCII.GetBytes("this is not an mp4 payload at all");
        var (_, length) = Fmp4ConcatHelper.GetInitRange(payload);
        Assert.Equal(0, length);
    }
}
