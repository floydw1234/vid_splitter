using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Jellyfin.Plugin.SmartBranching;
using Jellyfin.Plugin.SmartBranching.Models;
using Xunit;

namespace SmartBranching.Plugin.Tests;

public class BvfPlaybackRemuxerTests
{
    [Fact]
    public void ComputeCacheKey_ChangesWhenSegmentSelectionChanges()
    {
        var segmentsA = new List<ResolvedSegment>
        {
            new() { SegmentId = "seg-a", DataOffset = 100, DataLength = 200 },
            new() { SegmentId = "seg-b", DataOffset = 300, DataLength = 400 },
        };
        var segmentsB = new List<ResolvedSegment>
        {
            new() { SegmentId = "seg-a", DataOffset = 100, DataLength = 200 },
            new() { SegmentId = "seg-c", DataOffset = 500, DataLength = 600 },
        };

        var tempPath = Path.GetTempFileName();
        try
        {
            File.WriteAllText(tempPath, "bvf");

            var keyA = BvfPlaybackRemuxer.ComputeCacheKey(tempPath, "child", segmentsA);
            var keyB = BvfPlaybackRemuxer.ComputeCacheKey(tempPath, "child", segmentsB);
            var keySame = BvfPlaybackRemuxer.ComputeCacheKey(tempPath, "child", segmentsA);

            Assert.Equal(keySame, keyA);
            Assert.NotEqual(keyA, keyB);
        }
        finally
        {
            File.Delete(tempPath);
        }
    }
}
