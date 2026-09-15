using System.Collections.Generic;
using System.IO;
using Jellyfin.Plugin.SmartBranching;
using Jellyfin.Plugin.SmartBranching.Models;
using Xunit;

namespace SmartBranching.Plugin.Tests;

public class TopicPlaybackTests
{
    [Fact]
    public void Hits_WhenSegmentHasAvoidedTopicOrTag()
    {
        var sermon = new Segment { Topics = new List<string> { "religion_christianity" } };
        var nude = new Segment { Tags = new List<string> { "nudity" } };
        var safe = new Segment { Topics = new List<string>() };
        var avoided = TopicPlayback.NormalizeTopics(new[] { "religion_christianity", "nudity" });

        Assert.True(TopicPlayback.Hits(sermon, avoided));
        Assert.True(TopicPlayback.Hits(nude, avoided));
        Assert.False(TopicPlayback.Hits(safe, avoided));
    }

    [Fact]
    public void ResolveAction_SwapsWhenFillerExistsOtherwiseSkips()
    {
        var withFiller = new Segment
        {
            Id = "seg_002",
            Topics = new List<string> { "nudity" },
            ProfileSegmentId = "filler_001",
        };
        var withoutFiller = new Segment
        {
            Id = "seg_003",
            Topics = new List<string> { "nudity" },
        };
        var avoided = TopicPlayback.NormalizeTopics(new[] { "nudity" });

        Assert.Equal("swap", TopicPlayback.ResolveAction(withFiller, avoided));
        Assert.Equal("skip", TopicPlayback.ResolveAction(withoutFiller, avoided));
        Assert.Equal("play", TopicPlayback.ResolveAction(withFiller, TopicPlayback.NormalizeTopics(new[] { "politics" })));
        Assert.Equal("skip", TopicPlayback.ResolveAction(withFiller, avoided, "skip"));
        Assert.Equal("swap", TopicPlayback.ResolveAction(withFiller, avoided, "swap"));
        Assert.Equal("skip", TopicPlayback.ResolveAction(withoutFiller, avoided, "swap"));
        Assert.Equal("filler_001", TopicPlayback.ResolveTargetId(withFiller, "swap"));
    }

    [Fact]
    public void NormalizeHitAction_UnknownOrEmpty_IsSkip()
    {
        Assert.Equal("skip", TopicPlayback.NormalizeHitAction(null));
        Assert.Equal("skip", TopicPlayback.NormalizeHitAction(""));
        Assert.Equal("skip", TopicPlayback.NormalizeHitAction("mute"));
        Assert.Equal("swap", TopicPlayback.NormalizeHitAction("SWAP"));
    }

    [Fact]
    public void ApplyPreferredAction_LeavesPlayAlone_AndSkipsSwapWhenAsked()
    {
        Assert.Equal("play", TopicPlayback.ApplyPreferredAction("play", "skip", hasSwapTarget: true));
        Assert.Equal("skip", TopicPlayback.ApplyPreferredAction("swap", "skip", hasSwapTarget: true));
        Assert.Equal("swap", TopicPlayback.ApplyPreferredAction("skip", "swap", hasSwapTarget: true));
        Assert.Equal("skip", TopicPlayback.ApplyPreferredAction("swap", "swap", hasSwapTarget: false));
    }

    [Fact]
    public void ResolveTargetId_SwapWithoutFiller_Throws()
    {
        var segment = new Segment { Id = "seg_002", Topics = new List<string> { "nudity" } };
        Assert.Throws<InvalidDataException>(() => TopicPlayback.ResolveTargetId(segment, "swap"));
    }
}
