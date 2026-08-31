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
    public void Build_WithGapsAndSwaps_EmitsNoDiscontinuities()
    {
        // Timestamps are rewritten into one continuous timeline server-side, so
        // skips and swaps must not produce discontinuity tags.
        var segments = new[]
        {
            MakeSegment(startSec: 0, endSec: 5, durationMs: 5000),
            MakeSegment(startSec: 10, endSec: 15, durationMs: 5000, isSwapped: true),
            MakeSegment(startSec: 20, endSec: 25, durationMs: 5000),
        };

        var playlist = BvfHlsPlaylistBuilder.Build(segments, string.Empty);

        Assert.DoesNotContain("#EXT-X-DISCONTINUITY", playlist, StringComparison.Ordinal);
    }

    [Fact]
    public void Build_WithExactDurations_UsesThemForExtinf()
    {
        var segments = new[]
        {
            MakeSegment(startSec: 0, endSec: 5, durationMs: 5000),
            MakeSegment(startSec: 5, endSec: 10, durationMs: 5000),
        };

        var playlist = BvfHlsPlaylistBuilder.Build(segments, string.Empty, new[] { 5.005, 4.879583 });

        Assert.Contains("#EXTINF:5.005,", playlist, StringComparison.Ordinal);
        Assert.Contains("#EXTINF:4.879583,", playlist, StringComparison.Ordinal);
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

        var fragments = Fmp4ConcatHelper.GetFragmentRanges(payload);
        Assert.NotEmpty(fragments);
        Assert.Equal(mediaStart, fragments[0].Start);
        Assert.Equal(mediaStart + mediaLength, fragments[^1].Start + fragments[^1].Length);
    }

    [Fact]
    public void GetInitRange_NonFmp4Payload_ReturnsEmpty()
    {
        var payload = Encoding.ASCII.GetBytes("this is not an mp4 payload at all");
        var (_, length) = Fmp4ConcatHelper.GetInitRange(payload);
        Assert.Equal(0, length);
    }
}

public class Fmp4TimestampRewriterTests
{
    [Fact]
    public void ParseTracks_SumDurations_AndOffsetTimestamps_RoundTrip()
    {
        if (!FfmpegTestHelpers.IsAvailable())
            return;

        var payload = FfmpegTestHelpers.CreateFragmentedMp4(TimeSpan.FromMilliseconds(200));

        var (_, initLength) = Fmp4ConcatHelper.GetInitRange(payload);
        var tracks = Fmp4TimestampRewriter.ParseTracks(payload.AsSpan(0, (int)initLength));
        var video = Assert.Single(tracks);
        Assert.True(video.IsVideo);
        Assert.True(video.Timescale > 0);

        using var stream = new System.IO.MemoryStream(payload);
        var durationTicks = Fmp4TimestampRewriter.SumTrackDurationTicks(stream, 0, payload.Length, video.TrackId);
        var durationSeconds = durationTicks / (double)video.Timescale;
        Assert.InRange(durationSeconds, 0.1, 0.4);

        var (mediaStart, mediaLength) = Fmp4ConcatHelper.GetMediaRange(payload);
        var media = payload.AsSpan((int)mediaStart, (int)mediaLength).ToArray();

        var baselineTfdt = ReadFirstTfdt(media);
        Assert.Equal(0UL, baselineTfdt);

        Fmp4TimestampRewriter.ApplyTimestampOffset(media, tracks, durationTicks, video.Timescale);
        Assert.Equal(durationTicks, ReadFirstTfdt(media));

        Fmp4TimestampRewriter.SetMovieFragmentSequence(media, 7);
        Assert.Equal(7u, ReadFirstMfhdSequence(media));
    }

    internal static uint ReadFirstMfhdSequence(byte[] media)
    {
        var offset = 0;
        while (offset + 8 <= media.Length)
        {
            var size = ReadUInt32(media, offset);
            var type = Encoding.ASCII.GetString(media, offset + 4, 4);
            if (type == "moof")
            {
                var inner = offset + 8;
                while (inner + 8 <= offset + size)
                {
                    var innerSize = ReadUInt32(media, inner);
                    if (Encoding.ASCII.GetString(media, inner + 4, 4) == "mfhd")
                        return ReadUInt32(media, inner + 12);
                    inner += (int)innerSize;
                }
            }

            offset += (int)size;
        }

        throw new InvalidOperationException("no mfhd found");
    }

    internal static ulong ReadFirstTfdt(byte[] media)
    {
        // Walk moof > traf > tfdt for the first fragment.
        var offset = 0;
        while (offset + 8 <= media.Length)
        {
            var size = ReadUInt32(media, offset);
            var type = Encoding.ASCII.GetString(media, offset + 4, 4);
            if (type == "moof")
            {
                var inner = offset + 8;
                while (inner + 8 <= offset + size)
                {
                    var innerSize = ReadUInt32(media, inner);
                    if (Encoding.ASCII.GetString(media, inner + 4, 4) == "traf")
                    {
                        var trafChild = inner + 8;
                        while (trafChild + 8 <= inner + innerSize)
                        {
                            var childSize = ReadUInt32(media, trafChild);
                            if (Encoding.ASCII.GetString(media, trafChild + 4, 4) == "tfdt")
                            {
                                var version = media[trafChild + 8];
                                if (version == 1)
                                {
                                    ulong value = 0;
                                    for (var i = 0; i < 8; i++)
                                        value = (value << 8) | media[trafChild + 12 + i];
                                    return value;
                                }

                                return ReadUInt32(media, trafChild + 12);
                            }

                            trafChild += (int)childSize;
                        }
                    }

                    inner += (int)innerSize;
                }
            }

            offset += (int)size;
        }

        throw new InvalidOperationException("no tfdt found");
    }

    private static uint ReadUInt32(byte[] buffer, int offset)
        => ((uint)buffer[offset] << 24)
           | ((uint)buffer[offset + 1] << 16)
           | ((uint)buffer[offset + 2] << 8)
           | buffer[offset + 3];
}
