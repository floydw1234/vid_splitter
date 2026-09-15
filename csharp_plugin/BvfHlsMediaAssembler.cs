using System;
using System.Collections.Generic;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Stitches one fMP4 HLS part from a resolved BVF segment (slice + timestamp rewrite).
/// </summary>
internal static class BvfHlsMediaAssembler
{
    public static byte[] Assemble(
        string bvfPath,
        IReadOnlyList<ResolvedSegment> segments,
        BvfHlsTimeline timeline,
        int partIndex)
    {
        ArgumentNullException.ThrowIfNull(segments);
        ArgumentNullException.ThrowIfNull(timeline);

        if (partIndex < 0 || partIndex >= timeline.Parts.Count)
            throw new ArgumentOutOfRangeException(nameof(partIndex));

        var part = timeline.Parts[partIndex];
        if (part.ResolvedIndex < 0 || part.ResolvedIndex >= segments.Count)
            throw new InvalidOperationException(
                $"HLS part {partIndex} references resolved index {part.ResolvedIndex} outside segment list.");

        var payload = BvfSegmentExtractor.ReadSegmentPayload(bvfPath, segments[part.ResolvedIndex]);
        var media = Slice(payload, part.PayloadStart, part.PayloadLength);

        Fmp4TimestampRewriter.ApplyTimestampOffset(
            media,
            timeline.Tracks,
            part.TimestampOffsetTicks,
            timeline.VideoTimescale);
        Fmp4TimestampRewriter.SetMovieFragmentSequence(media, (uint)(partIndex + 1));
        if (partIndex > 0)
            Fmp4TimestampRewriter.StripAudioEncoderPriming(media, timeline.Tracks);
        Fmp4TimestampRewriter.SyncAudioStartToVideo(media, timeline.Tracks);
        Fmp4TimestampRewriter.ClampAudioToVideoDuration(media, timeline.Tracks);
        return media;
    }

    private static byte[] Slice(byte[] payload, long start, long length)
    {
        if (start == 0 && length == payload.Length)
            return payload;

        var slice = new byte[length];
        Array.Copy(payload, start, slice, 0, length);
        return slice;
    }
}
