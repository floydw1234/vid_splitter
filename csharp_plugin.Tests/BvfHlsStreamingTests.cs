using System;
using System.IO;
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
        // Legacy fallback path (no timeline): one EXTINF per resolved segment.
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
    public void BuildFromTimeline_WithNonConsecutiveSegmentChange_EmitsDiscontinuity()
    {
        var timeline = new BvfHlsTimeline
        {
            Tracks = Array.Empty<Fmp4TimestampRewriter.TrackInfo>(),
            VideoTrackId = 1,
            VideoTimescale = 90000,
            Parts = new[]
            {
                new BvfHlsPart
                {
                    ResolvedIndex = 0,
                    PayloadStart = 0,
                    PayloadLength = 100,
                    TimestampOffsetTicks = 0,
                    DurationSeconds = 5,
                    ClampAudio = true,
                    SegmentStart = true,
                },
                new BvfHlsPart
                {
                    ResolvedIndex = 2,
                    PayloadStart = 0,
                    PayloadLength = 100,
                    TimestampOffsetTicks = 450000,
                    DurationSeconds = 5,
                    ClampAudio = true,
                    SegmentStart = true,
                },
            },
            SegmentDurationsSeconds = new[] { 5.0, 5.0 },
        };

        var playlist = BvfHlsPlaylistBuilder.BuildFromTimeline(timeline, "?api_key=abc");

        Assert.Contains("#EXT-X-DISCONTINUITY", playlist, StringComparison.Ordinal);
        Assert.Contains("1.m4s?api_key=abc", playlist, StringComparison.Ordinal);
    }

    [Fact]
    public void BuildFromTimeline_WithConsecutiveSegments_OmitsDiscontinuity()
    {
        var timeline = new BvfHlsTimeline
        {
            Tracks = Array.Empty<Fmp4TimestampRewriter.TrackInfo>(),
            VideoTrackId = 1,
            VideoTimescale = 90000,
            Parts = new[]
            {
                new BvfHlsPart
                {
                    ResolvedIndex = 0,
                    PayloadStart = 0,
                    PayloadLength = 100,
                    TimestampOffsetTicks = 0,
                    DurationSeconds = 5,
                    ClampAudio = true,
                    SegmentStart = true,
                },
                new BvfHlsPart
                {
                    ResolvedIndex = 1,
                    PayloadStart = 0,
                    PayloadLength = 100,
                    TimestampOffsetTicks = 450000,
                    DurationSeconds = 5,
                    ClampAudio = true,
                    SegmentStart = true,
                },
            },
            SegmentDurationsSeconds = new[] { 5.0, 5.0 },
        };

        var playlist = BvfHlsPlaylistBuilder.BuildFromTimeline(timeline, string.Empty);

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

    [Fact]
    public void StripAudioEncoderPriming_RemovesNonStandardLeadingAacFrame()
    {
        var fixture = ResolveMsRachelSegmentFixture();
        if (fixture == null)
            return;

        var payload = File.ReadAllBytes(fixture);
        var (_, initLength) = Fmp4ConcatHelper.GetInitRange(payload);
        var init = payload.AsSpan(0, (int)initLength).ToArray();
        var tracks = Fmp4TimestampRewriter.ParseTracks(payload.AsSpan(0, (int)initLength));
        var audio = tracks.FirstOrDefault(track => !track.IsVideo);
        Assert.NotEqual(default, audio);

        var (mediaStart, mediaLength) = Fmp4ConcatHelper.GetMediaRange(payload);
        var media = payload.AsSpan((int)mediaStart, (int)mediaLength).ToArray();
        var before = Fmp4TimestampRewriter.CountTrunSamples(media, audio.TrackId);
        Assert.True(Fmp4TimestampRewriter.TryReadFirstTrunSampleDuration(media, audio.TrackId, out var firstDuration));
        Assert.True(firstDuration > 1024);

        Fmp4TimestampRewriter.StripAudioEncoderPriming(media, tracks);

        Assert.Equal(before - 1, Fmp4TimestampRewriter.CountTrunSamples(media, audio.TrackId));
        Assert.True(Fmp4TimestampRewriter.TryReadFirstTrunSampleDuration(media, audio.TrackId, out var trimmedFirst));
        Assert.Equal(1024u, trimmedFirst);
        Assert.True(FfprobeAcceptsFragment(init, media), FfprobeDescribe(init, media));
    }

    [Fact]
    public void AssembledMediaRange_ValidatesWithFfprobe()
    {
        if (!FfmpegTestHelpers.IsAvailable())
            return;

        var fixture = ResolveMsRachelSegmentFixture();
        if (fixture == null)
            return;

        var payload = File.ReadAllBytes(fixture);
        var (_, initLength) = Fmp4ConcatHelper.GetInitRange(payload);
        var init = payload.AsSpan(0, (int)initLength).ToArray();
        var tracks = Fmp4TimestampRewriter.ParseTracks(payload.AsSpan(0, (int)initLength));
        var (mediaStart, mediaLength) = Fmp4ConcatHelper.GetMediaRange(payload);
        var media = payload.AsSpan((int)mediaStart, (int)mediaLength).ToArray();

        Fmp4TimestampRewriter.StripAudioEncoderPriming(media, tracks);
        Fmp4TimestampRewriter.SyncAudioStartToVideo(media, tracks);
        Fmp4TimestampRewriter.ClampAudioToVideoDuration(media, tracks);

        Assert.True(FfprobeAcceptsFragment(init, media), FfprobeDescribe(init, media));
    }

    private static bool FfprobeAcceptsFragment(byte[] init, byte[] media)
    {
        var path = Path.Combine(Path.GetTempPath(), $"bvf-fragment-{Guid.NewGuid():N}.mp4");
        try
        {
            using (var stream = File.Create(path))
            {
                stream.Write(init);
                stream.Write(media);
            }

            using var process = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = ResolveFfprobePath(),
                Arguments = $"-v error -show_format \"{path}\"",
                RedirectStandardError = true,
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            });
            process?.WaitForExit(10_000);
            return process?.ExitCode == 0;
        }
        finally
        {
            if (File.Exists(path))
                File.Delete(path);
        }
    }

    private static string FfprobeDescribe(byte[] init, byte[] media)
    {
        var path = Path.Combine(Path.GetTempPath(), $"bvf-fragment-{Guid.NewGuid():N}.mp4");
        try
        {
            using (var stream = File.Create(path))
            {
                stream.Write(init);
                stream.Write(media);
            }

            using var process = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = ResolveFfprobePath(),
                Arguments = $"-v error \"{path}\"",
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            });
            return process?.StandardError.ReadToEnd() ?? "ffprobe unavailable";
        }
        finally
        {
            if (File.Exists(path))
                File.Delete(path);
        }
    }

    private static string ResolveFfprobePath()
    {
        const string jellyfinBundled = "/usr/lib/jellyfin-ffmpeg/ffprobe";
        return File.Exists(jellyfinBundled) ? jellyfinBundled : "ffprobe";
    }

    private static string? ResolveMsRachelSegmentFixture()
    {
        const string cached = "/tmp/seg_002.mp4";
        return File.Exists(cached) ? cached : null;
    }

    private static uint ReadUInt32(byte[] buffer, int offset)
        => ((uint)buffer[offset] << 24)
           | ((uint)buffer[offset + 1] << 16)
           | ((uint)buffer[offset + 2] << 8)
           | buffer[offset + 3];
}
