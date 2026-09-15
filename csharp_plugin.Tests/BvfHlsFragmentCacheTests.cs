using System;
using System.Threading;
using Jellyfin.Plugin.SmartBranching;
using Xunit;

namespace SmartBranching.Plugin.Tests;

public class BvfHlsFragmentCacheTests
{
    [Fact]
    public void GetOrAdd_ReturnsCachedBytesOnSecondCall()
    {
        var cache = new BvfHlsFragmentCache(maxBytes: 1024 * 1024);
        var calls = 0;

        byte[] Factory()
        {
            calls++;
            return new byte[] { 1, 2, 3 };
        }

        var first = cache.GetOrAdd("stream|p0", Factory);
        var second = cache.GetOrAdd("stream|p0", Factory);

        Assert.Same(first, second);
        Assert.Equal(1, calls);
    }

    [Fact]
    public void PrefetchAhead_WarmsUpcomingFragments()
    {
        var cache = new BvfHlsFragmentCache(maxBytes: 1024 * 1024);
        var timeline = new BvfHlsTimeline
        {
            Tracks = Array.Empty<Fmp4TimestampRewriter.TrackInfo>(),
            VideoTrackId = 1,
            VideoTimescale = 90000,
            Parts = new[]
            {
                MakePart(0, 5.0),
                MakePart(1, 5.0),
            },
            SegmentDurationsSeconds = new[] { 5.0, 5.0 },
        };

        cache.GetOrAdd("stream|p0", () => new byte[] { 9 });
        cache.PrefetchAhead(
            "stream",
            timeline,
            1,
            i => new byte[] { (byte)(10 + i) });

        for (var attempt = 0; attempt < 50; attempt++)
        {
            if (cache.EntryCount >= 2)
                break;
            Thread.Sleep(20);
        }

        Assert.Equal(2, cache.EntryCount);
        var warmed = cache.GetOrAdd("stream|p1", () => throw new InvalidOperationException("should be cached"));
        Assert.Equal(11, warmed[0]);
    }

    [Fact]
    public void Evict_RemovesOldestWhenOverByteLimit()
    {
        var cache = new BvfHlsFragmentCache(maxBytes: 10);
        cache.GetOrAdd("a", () => new byte[6]);
        cache.GetOrAdd("b", () => new byte[6]);

        Assert.Equal(1, cache.EntryCount);
        Assert.True(cache.TotalBytes <= 10);
    }

    private static BvfHlsPart MakePart(int resolvedIndex, double seconds)
        => new()
        {
            ResolvedIndex = resolvedIndex,
            PayloadStart = 0,
            PayloadLength = 1,
            TimestampOffsetTicks = 0,
            DurationSeconds = seconds,
            ClampAudio = true,
            SegmentStart = true,
        };
}
