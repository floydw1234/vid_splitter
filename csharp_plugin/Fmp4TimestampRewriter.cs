using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Precomputed timing data for one profile's resolved segment list: exact video
/// durations per segment and the cumulative tfdt offsets used to stitch the
/// independently-encoded segments into one continuous timeline.
/// </summary>
internal sealed class BvfHlsPart
{
    public required int ResolvedIndex { get; init; }

    public required int PayloadStart { get; init; }

    public required int PayloadLength { get; init; }

    public required ulong TimestampOffsetTicks { get; init; }

    public required double DurationSeconds { get; init; }

    public required bool ClampAudio { get; init; }

    /// <summary>First fMP4 fragment of a resolved BVF segment (after a skip/swap cut).</summary>
    public required bool SegmentStart { get; init; }
}

internal sealed class BvfHlsTimeline
{
    public required IReadOnlyList<Fmp4TimestampRewriter.TrackInfo> Tracks { get; init; }

    public required uint VideoTrackId { get; init; }

    public required uint VideoTimescale { get; init; }

    public required IReadOnlyList<BvfHlsPart> Parts { get; init; }

    public required IReadOnlyList<double> SegmentDurationsSeconds { get; init; }
}

/// <summary>
/// BVF segment assets are encoded independently, so every fragment's
/// baseMediaDecodeTime (tfdt) restarts at zero. To present the resolved segments
/// as one continuous fMP4/HLS timeline, each served segment's tfdt values must be
/// shifted by the cumulative duration of the segments before it.
/// </summary>
internal static class Fmp4TimestampRewriter
{
    internal readonly record struct TrackInfo(uint TrackId, uint Timescale, bool IsVideo);

    /// <summary>
    /// Parses track ids, timescales and handler types from an fMP4 init segment
    /// (ftyp + moov).
    /// </summary>
    public static IReadOnlyList<TrackInfo> ParseTracks(ReadOnlySpan<byte> init)
    {
        var tracks = new List<TrackInfo>();

        foreach (var (type, offset, size, headerSize) in WalkBoxes(init, 0, init.Length))
        {
            if (type != "moov")
                continue;

            foreach (var (childType, childOffset, childSize, childHeader) in WalkBoxes(init, offset + headerSize, offset + size))
            {
                if (childType != "trak")
                    continue;

                uint trackId = 0;
                uint timescale = 0;
                var isVideo = false;

                foreach (var (trakChild, trakOffset, trakSize, trakHeader) in WalkBoxes(init, childOffset + childHeader, childOffset + childSize))
                {
                    if (trakChild == "tkhd")
                    {
                        var version = init[trakOffset + 8];
                        var idOffset = trakOffset + 12 + (version == 1 ? 16 : 8);
                        trackId = BinaryPrimitives.ReadUInt32BigEndian(init.Slice(idOffset, 4));
                    }
                    else if (trakChild == "mdia")
                    {
                        foreach (var (mdiaChild, mdiaOffset, _, _) in WalkBoxes(init, trakOffset + trakHeader, trakOffset + trakSize))
                        {
                            if (mdiaChild == "mdhd")
                            {
                                var version = init[mdiaOffset + 8];
                                var scaleOffset = mdiaOffset + 12 + (version == 1 ? 16 : 8);
                                timescale = BinaryPrimitives.ReadUInt32BigEndian(init.Slice(scaleOffset, 4));
                            }
                            else if (mdiaChild == "hdlr")
                            {
                                var handler = System.Text.Encoding.ASCII.GetString(init.Slice(mdiaOffset + 16, 4));
                                isVideo = handler == "vide";
                            }
                        }
                    }
                }

                if (trackId != 0 && timescale != 0)
                    tracks.Add(new TrackInfo(trackId, timescale, isVideo));
            }
        }

        return tracks;
    }

    /// <summary>
    /// Sums the duration (in the track's timescale) of one track across all moof
    /// fragments in a byte range of <paramref name="stream"/>. Reads sparsely:
    /// moof boxes are loaded, mdat payloads are skipped by seeking.
    /// </summary>
    public static ulong SumTrackDurationTicks(Stream stream, long payloadOffset, long payloadLength, uint trackId)
    {
        ulong total = 0;
        var header = new byte[16];
        var position = payloadOffset;
        var end = payloadOffset + payloadLength;

        while (position + 8 <= end)
        {
            stream.Seek(position, SeekOrigin.Begin);
            ReadExactly(stream, header, 8);

            long size = BinaryPrimitives.ReadUInt32BigEndian(header.AsSpan(0, 4));
            var type = System.Text.Encoding.ASCII.GetString(header, 4, 4);
            if (size == 1)
            {
                ReadExactly(stream, header, 8);
                size = checked((long)BinaryPrimitives.ReadUInt64BigEndian(header.AsSpan(0, 8)));
            }
            else if (size == 0)
            {
                size = end - position;
            }

            if (size < 8 || position + size > end)
                break;

            if (type == "moof")
            {
                var moof = new byte[size];
                stream.Seek(position, SeekOrigin.Begin);
                ReadExactly(stream, moof, (int)size);
                total += SumMoofTrackDuration(moof, trackId);
            }

            position += size;
        }

        return total;
    }

    /// <summary>
    /// Adds a per-track offset to every tfdt in a media segment, in place. The
    /// offset is given in the video track's timescale and rescaled per track.
    /// </summary>
    public static void ApplyTimestampOffset(
        byte[] media,
        IReadOnlyList<TrackInfo> tracks,
        ulong videoOffsetTicks,
        uint videoTimescale)
    {
        if (videoOffsetTicks == 0)
            return;

        var span = media.AsSpan();
        foreach (var (type, offset, size, headerSize) in WalkBoxes(span, 0, media.Length))
        {
            if (type != "moof")
                continue;

            foreach (var (childType, childOffset, childSize, childHeader) in WalkBoxes(span, offset + headerSize, offset + size))
            {
                if (childType != "traf")
                    continue;

                uint trackId = 0;
                foreach (var (trafChild, trafOffset, _, _) in WalkBoxes(span, childOffset + childHeader, childOffset + childSize))
                {
                    if (trafChild == "tfhd")
                    {
                        trackId = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 12, 4));
                    }
                    else if (trafChild == "tfdt")
                    {
                        var offsetTicks = RescaleOffset(videoOffsetTicks, videoTimescale, FindTimescale(tracks, trackId));
                        var version = span[trafOffset + 8];
                        if (version == 1)
                        {
                            var current = BinaryPrimitives.ReadUInt64BigEndian(span.Slice(trafOffset + 12, 8));
                            BinaryPrimitives.WriteUInt64BigEndian(span.Slice(trafOffset + 12, 8), current + offsetTicks);
                        }
                        else
                        {
                            var current = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 12, 4));
                            var updated = current + offsetTicks;
                            if (updated > uint.MaxValue)
                                throw new InvalidDataException("tfdt v0 overflow while offsetting BVF segment timestamps.");
                            BinaryPrimitives.WriteUInt32BigEndian(span.Slice(trafOffset + 12, 4), (uint)updated);
                        }
                    }
                }
            }
        }
    }

    /// <summary>
    /// Sets every mfhd sequence_number in <paramref name="media"/> to
    /// <paramref name="sequenceNumber"/>. HLS/MSE parsers can stall when
    /// independently-encoded fragments all restart at 1.
    /// </summary>
    public static void SetMovieFragmentSequence(byte[] media, uint sequenceNumber)
    {
        var span = media.AsSpan();
        foreach (var (type, offset, size, headerSize) in WalkBoxes(span, 0, media.Length))
        {
            if (type != "moof")
                continue;

            foreach (var (childType, childOffset, _, _) in WalkBoxes(span, offset + headerSize, offset + size))
            {
                if (childType != "mfhd")
                    continue;

                BinaryPrimitives.WriteUInt32BigEndian(span.Slice(childOffset + 12, 4), sequenceNumber);
            }
        }
    }

    /// <summary>
    /// Shrinks trailing audio sample durations so audio does not extend past
    /// video. Independently-encoded AAC is typically a few frames longer than
    /// video; leaving that overlap in MSE makes the playhead follow audio while
    /// video holds the last frame.
    /// </summary>
    public static void ClampAudioToVideoDuration(byte[] media, IReadOnlyList<TrackInfo> tracks)
    {
        var video = default(TrackInfo);
        foreach (var track in tracks)
        {
            if (track.IsVideo)
            {
                video = track;
                break;
            }
        }

        if (video.TrackId == 0)
            return;

        var videoEnd = GetTrackEndTicks(media, video.TrackId);
        if (videoEnd == 0)
            return;

        foreach (var track in tracks)
        {
            if (track.IsVideo || track.Timescale == 0)
                continue;

            var audioLimit = RescaleOffset(videoEnd, video.Timescale, track.Timescale);
            TrimTrackEndTo(media, track.TrackId, audioLimit);
        }
    }

    /// <summary>
    /// Forces audio decode timestamps to match video at the start of each moof.
    /// Prevents small A/V lead/lag inside stitched fragments.
    /// </summary>
    public static void SyncAudioStartToVideo(byte[] media, IReadOnlyList<TrackInfo> tracks)
    {
        var video = default(TrackInfo);
        foreach (var track in tracks)
        {
            if (track.IsVideo)
            {
                video = track;
                break;
            }
        }

        if (video.TrackId == 0)
            return;

        var span = media.AsSpan();
        foreach (var (type, offset, size, headerSize) in WalkBoxes(span, 0, media.Length))
        {
            if (type != "moof")
                continue;

            ulong? videoTfdt = null;
            var audioTfdts = new List<(int BoxOffset, uint TrackId, int Version)>();

            foreach (var (childType, childOffset, childSize, childHeader) in WalkBoxes(span, offset + headerSize, offset + size))
            {
                if (childType != "traf")
                    continue;

                uint trackId = 0;
                foreach (var (trafChild, trafOffset, _, _) in WalkBoxes(span, childOffset + childHeader, childOffset + childSize))
                {
                    if (trafChild == "tfhd")
                        trackId = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 12, 4));
                    else if (trafChild == "tfdt")
                    {
                        var version = span[trafOffset + 8];
                        if (trackId == video.TrackId)
                        {
                            videoTfdt = version == 1
                                ? BinaryPrimitives.ReadUInt64BigEndian(span.Slice(trafOffset + 12, 8))
                                : BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 12, 4));
                        }
                        else
                        {
                            audioTfdts.Add((trafOffset, trackId, version));
                        }
                    }
                }
            }

            if (videoTfdt == null)
                continue;

            foreach (var (boxOffset, trackId, version) in audioTfdts)
            {
                var audioTimescale = FindTimescale(tracks, trackId);
                var aligned = RescaleOffset(videoTfdt.Value, video.Timescale, audioTimescale);
                if (version == 1)
                    BinaryPrimitives.WriteUInt64BigEndian(span.Slice(boxOffset + 12, 8), aligned);
                else
                    BinaryPrimitives.WriteUInt32BigEndian(span.Slice(boxOffset + 12, 4), (uint)aligned);
            }
        }
    }

    /// <summary>
    /// Drops AAC encoder priming at the start of independently-encoded segments.
    /// Each BVF chunk restarts its AAC stream; the priming packet clicks when stitched.
    /// </summary>
    public static void StripAudioEncoderPriming(byte[] media, IReadOnlyList<TrackInfo> tracks)
    {
        foreach (var track in tracks)
        {
            if (track.IsVideo || track.Timescale == 0)
                continue;

            // BVF segments begin with a non-standard first AAC frame (~67 ms) before
            // regular 1024-tick frames. Skip it via trun metadata so MSE never reads
            // the priming bytes from mdat.
            if (!TryReadFirstTrunSampleDuration(media, track.TrackId, out var firstDuration))
                continue;

            if (firstDuration <= 1024)
                continue;

            SkipLeadingTrunSamples(media, track.TrackId, 1);
        }
    }

    internal static bool TryReadFirstTrunSampleDuration(byte[] media, uint trackId, out uint duration)
    {
        duration = 0;
        if (!TryFindTrun(media, trackId, out var trun))
            return false;

        var span = media.AsSpan();
        var flags = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trun.BoxOffset + 8, 4)) & 0xFFFFFF;
        if ((flags & 0x100) == 0)
            return false;

        var sampleCount = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trun.BoxOffset + 12, 4));
        if (sampleCount == 0)
            return false;

        var cursor = trun.BoxOffset + 16;
        if ((flags & 0x1) != 0)
            cursor += 4;
        if ((flags & 0x4) != 0)
            cursor += 4;

        duration = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(cursor, 4));
        return true;
    }

    internal static int CountTrunSamples(byte[] media, uint trackId)
    {
        if (!TryFindTrun(media, trackId, out var trun))
            return 0;

        return (int)BinaryPrimitives.ReadUInt32BigEndian(media.AsSpan(trun.BoxOffset + 12, 4));
    }

    internal static void SkipLeadingTrunSamples(byte[] media, uint trackId, int samplesToSkip)
        => RemoveTrunSamples(media, trackId, skipLeading: samplesToSkip, skipTrailing: 0);

    private static void SkipTrailingTrunSamples(byte[] media, uint trackId, int samplesToSkip)
        => RemoveTrunSamples(media, trackId, skipLeading: 0, skipTrailing: samplesToSkip);

    private readonly record struct TrunLocation(int BoxOffset, int BoxSize, int TrafOffset, int MoofOffset);

    private static bool TryFindTrun(byte[] media, uint trackId, out TrunLocation trun)
    {
        trun = default;
        var span = media.AsSpan();

        foreach (var (type, offset, size, headerSize) in WalkBoxes(span, 0, media.Length))
        {
            if (type != "moof")
                continue;

            foreach (var (childType, childOffset, childSize, childHeader) in WalkBoxes(span, offset + headerSize, offset + size))
            {
                if (childType != "traf")
                    continue;

                uint currentTrackId = 0;
                foreach (var (trafChild, trafOffset, trafSize, _) in WalkBoxes(span, childOffset + childHeader, childOffset + childSize))
                {
                    if (trafChild == "tfhd")
                        currentTrackId = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 12, 4));
                    else if (trafChild == "trun" && currentTrackId == trackId)
                    {
                        trun = new TrunLocation(trafOffset, trafSize, childOffset, offset);
                        return true;
                    }
                }
            }
        }

        return false;
    }

    private static void RemoveTrunSamples(byte[] media, uint trackId, int skipLeading, int skipTrailing)
    {
        if (skipLeading <= 0 && skipTrailing <= 0)
            return;

        if (!TryFindTrun(media, trackId, out var trun))
            return;

        var span = media.AsSpan();
        var trunOffset = trun.BoxOffset;
        var flags = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trunOffset + 8, 4)) & 0xFFFFFF;
        var sampleCount = (int)BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trunOffset + 12, 4));
        if (sampleCount <= skipLeading + skipTrailing)
            return;

        var headerBytes = 16;
        var dataOffsetField = -1;
        if ((flags & 0x1) != 0)
        {
            dataOffsetField = trunOffset + 16;
            headerBytes += 4;
        }

        if ((flags & 0x4) != 0)
            headerBytes += 4;

        var perSample = 0;
        if ((flags & 0x100) != 0)
            perSample += 4;
        if ((flags & 0x200) != 0)
            perSample += 4;
        if ((flags & 0x400) != 0)
            perSample += 4;
        if ((flags & 0x800) != 0)
            perSample += 4;

        if (perSample == 0)
            return;

        var samplesOffset = trunOffset + headerBytes;
        var samples = new List<(uint Duration, uint Size)>(sampleCount);
        for (var i = 0; i < sampleCount; i++)
        {
            var cursor = samplesOffset + i * perSample;
            uint duration = 0;
            uint size = 0;
            if ((flags & 0x100) != 0)
            {
                duration = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(cursor, 4));
                cursor += 4;
            }

            if ((flags & 0x200) != 0)
                size = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(cursor, 4));

            samples.Add((duration, size));
        }

        var removedLeading = samples.Take(skipLeading).ToList();
        var kept = samples.Skip(skipLeading).Take(sampleCount - skipLeading - skipTrailing).ToList();
        if (kept.Count == 0)
            return;

        var skipBytes = removedLeading.Aggregate(0UL, (total, sample) => total + sample.Size);
        var removedTicks = removedLeading.Aggregate(0UL, (total, sample) => total + sample.Duration);

        BinaryPrimitives.WriteUInt32BigEndian(span.Slice(trunOffset + 12, 4), (uint)kept.Count);
        if (dataOffsetField >= 0 && skipBytes > 0)
        {
            var dataOffset = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(dataOffsetField, 4));
            BinaryPrimitives.WriteUInt32BigEndian(
                media.AsSpan(dataOffsetField, 4),
                dataOffset + (uint)skipBytes);
        }

        for (var i = 0; i < kept.Count; i++)
        {
            var cursor = samplesOffset + i * perSample;
            if ((flags & 0x100) != 0)
            {
                BinaryPrimitives.WriteUInt32BigEndian(media.AsSpan(cursor, 4), kept[i].Duration);
                cursor += 4;
            }

            if ((flags & 0x200) != 0)
                BinaryPrimitives.WriteUInt32BigEndian(media.AsSpan(cursor, 4), kept[i].Size);
        }

        if (removedTicks > 0)
            AdvanceTfdt(media, trackId, removedTicks);
    }

    private static void AdvanceTfdt(byte[] media, uint trackId, ulong deltaTicks)
    {
        var span = media.AsSpan();
        foreach (var (type, offset, size, headerSize) in WalkBoxes(span, 0, media.Length))
        {
            if (type != "moof")
                continue;

            foreach (var (childType, childOffset, childSize, childHeader) in WalkBoxes(span, offset + headerSize, offset + size))
            {
                if (childType != "traf")
                    continue;

                uint currentTrackId = 0;
                foreach (var (trafChild, trafOffset, _, _) in WalkBoxes(span, childOffset + childHeader, childOffset + childSize))
                {
                    if (trafChild == "tfhd")
                        currentTrackId = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 12, 4));
                    else if (trafChild == "tfdt" && currentTrackId == trackId)
                    {
                        var version = span[trafOffset + 8];
                        if (version == 1)
                        {
                            var current = BinaryPrimitives.ReadUInt64BigEndian(span.Slice(trafOffset + 12, 8));
                            BinaryPrimitives.WriteUInt64BigEndian(span.Slice(trafOffset + 12, 8), current + deltaTicks);
                        }
                        else
                        {
                            var current = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 12, 4));
                            var updated = current + deltaTicks;
                            if (updated > uint.MaxValue)
                                throw new InvalidDataException("tfdt v0 overflow while trimming audio priming.");
                            BinaryPrimitives.WriteUInt32BigEndian(span.Slice(trafOffset + 12, 4), (uint)updated);
                        }
                    }
                }
            }
        }
    }

    private static ulong GetTrackEndTicks(byte[] media, uint trackId)
    {
        ulong end = 0;
        var span = media.AsSpan();
        foreach (var (type, offset, size, headerSize) in WalkBoxes(span, 0, media.Length))
        {
            if (type != "moof")
                continue;

            foreach (var (childType, childOffset, childSize, childHeader) in WalkBoxes(span, offset + headerSize, offset + size))
            {
                if (childType != "traf")
                    continue;

                uint currentTrackId = 0;
                ulong tfdt = 0;
                uint defaultSampleDuration = 0;
                ulong duration = 0;

                foreach (var (trafChild, trafOffset, _, _) in WalkBoxes(span, childOffset + childHeader, childOffset + childSize))
                {
                    if (trafChild == "tfhd")
                    {
                        var flags = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 8, 4)) & 0xFFFFFF;
                        currentTrackId = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 12, 4));
                        var cursor = trafOffset + 16;
                        if ((flags & 0x1) != 0)
                            cursor += 8;
                        if ((flags & 0x2) != 0)
                            cursor += 4;
                        if ((flags & 0x8) != 0)
                            defaultSampleDuration = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(cursor, 4));
                    }
                    else if (trafChild == "tfdt")
                    {
                        var version = span[trafOffset + 8];
                        tfdt = version == 1
                            ? BinaryPrimitives.ReadUInt64BigEndian(span.Slice(trafOffset + 12, 8))
                            : BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trafOffset + 12, 4));
                    }
                    else if (trafChild == "trun" && currentTrackId == trackId)
                    {
                        duration += ReadTrunDuration(span, trafOffset, defaultSampleDuration);
                    }
                }

                if (currentTrackId == trackId)
                    end = Math.Max(end, tfdt + duration);
            }
        }

        return end;
    }

    private static void TrimTrackEndTo(byte[] media, uint trackId, ulong limitTicks)
    {
        const int maxPasses = 32;
        for (var pass = 0; pass < maxPasses; pass++)
        {
            var end = GetTrackEndTicks(media, trackId);
            if (end <= limitTicks)
                return;

            if (!TryFindTrun(media, trackId, out _))
                return;

            var sampleCount = CountTrunSamples(media, trackId);
            if (sampleCount <= 1)
                return;

            SkipTrailingTrunSamples(media, trackId, 1);
        }
    }

    private static ulong ReadTrunDuration(ReadOnlySpan<byte> span, int trunOffset, uint defaultSampleDuration)
    {
        var flags = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trunOffset + 8, 4)) & 0xFFFFFF;
        var sampleCount = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(trunOffset + 12, 4));
        var cursor = trunOffset + 16;
        if ((flags & 0x1) != 0)
            cursor += 4;
        if ((flags & 0x4) != 0)
            cursor += 4;

        if ((flags & 0x100) == 0)
            return (ulong)defaultSampleDuration * sampleCount;

        var perSample = 4
            + (((flags & 0x200) != 0) ? 4 : 0)
            + (((flags & 0x400) != 0) ? 4 : 0)
            + (((flags & 0x800) != 0) ? 4 : 0);
        ulong total = 0;
        for (var i = 0; i < sampleCount; i++)
            total += BinaryPrimitives.ReadUInt32BigEndian(span.Slice(cursor + i * perSample, 4));
        return total;
    }

    private static uint FindTimescale(IReadOnlyList<TrackInfo> tracks, uint trackId)
    {
        foreach (var track in tracks)
        {
            if (track.TrackId == trackId)
                return track.Timescale;
        }

        throw new InvalidDataException($"fMP4 fragment references unknown track {trackId}.");
    }

    private static ulong RescaleOffset(ulong videoOffsetTicks, uint videoTimescale, uint targetTimescale)
    {
        if (videoTimescale == targetTimescale)
            return videoOffsetTicks;

        return (ulong)(((UInt128)videoOffsetTicks * targetTimescale + videoTimescale / 2) / videoTimescale);
    }

    private static ulong SumMoofTrackDuration(byte[] moof, uint trackId)
    {
        ulong total = 0;
        var span = moof.AsSpan();

        // The buffer holds the complete moof box; walk its children for trafs.
        var (moofType, moofOffset, moofSize, moofHeader) = (System.Text.Encoding.ASCII.GetString(span.Slice(4, 4)), 0, moof.Length, 8);
        if (moofType != "moof")
            return 0;

        foreach (var (type, offset, size, headerSize) in WalkBoxes(span, moofOffset + moofHeader, moofSize))
        {
            if (type != "traf")
                continue;

            uint currentTrackId = 0;
            uint defaultSampleDuration = 0;

            foreach (var (childType, childOffset, _, _) in WalkBoxes(span, offset + headerSize, offset + size))
            {
                if (childType == "tfhd")
                {
                    var flags = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(childOffset + 8, 4)) & 0xFFFFFF;
                    currentTrackId = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(childOffset + 12, 4));
                    var cursor = childOffset + 16;
                    if ((flags & 0x1) != 0)
                        cursor += 8; // base_data_offset
                    if ((flags & 0x2) != 0)
                        cursor += 4; // sample_description_index
                    if ((flags & 0x8) != 0)
                        defaultSampleDuration = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(cursor, 4));
                }
                else if (childType == "trun" && currentTrackId == trackId)
                {
                    var flags = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(childOffset + 8, 4)) & 0xFFFFFF;
                    var sampleCount = BinaryPrimitives.ReadUInt32BigEndian(span.Slice(childOffset + 12, 4));
                    var cursor = childOffset + 16;
                    if ((flags & 0x1) != 0)
                        cursor += 4; // data_offset
                    if ((flags & 0x4) != 0)
                        cursor += 4; // first_sample_flags

                    if ((flags & 0x100) != 0)
                    {
                        var perSample = 4
                            + (((flags & 0x200) != 0) ? 4 : 0)
                            + (((flags & 0x400) != 0) ? 4 : 0)
                            + (((flags & 0x800) != 0) ? 4 : 0);
                        for (var i = 0; i < sampleCount; i++)
                            total += BinaryPrimitives.ReadUInt32BigEndian(span.Slice(cursor + i * perSample, 4));
                    }
                    else if (defaultSampleDuration != 0)
                    {
                        total += (ulong)defaultSampleDuration * sampleCount;
                    }
                    else
                    {
                        throw new InvalidDataException(
                            "fMP4 trun has no sample durations and tfhd has no default duration; cannot build timeline.");
                    }
                }
            }
        }

        return total;
    }

    private static void ReadExactly(Stream stream, byte[] buffer, int count)
    {
        var read = 0;
        while (read < count)
        {
            var n = stream.Read(buffer, read, count - read);
            if (n <= 0)
                throw new EndOfStreamException("Unexpected end of fMP4 data.");
            read += n;
        }
    }

    private static IEnumerable<(string Type, int Offset, int Size, int HeaderSize)> WalkBoxes(ReadOnlySpan<byte> buffer, int start, int end)
    {
        var results = new List<(string, int, int, int)>();
        var offset = start;

        while (offset + 8 <= end)
        {
            long size = BinaryPrimitives.ReadUInt32BigEndian(buffer.Slice(offset, 4));
            var type = System.Text.Encoding.ASCII.GetString(buffer.Slice(offset + 4, 4));
            var headerSize = 8;

            if (size == 1)
            {
                if (offset + 16 > end)
                    break;
                size = checked((long)BinaryPrimitives.ReadUInt64BigEndian(buffer.Slice(offset + 8, 8)));
                headerSize = 16;
            }
            else if (size == 0)
            {
                size = end - offset;
            }

            if (size < 8 || offset + size > end)
                break;

            results.Add((type, offset, (int)size, headerSize));
            offset += (int)size;
        }

        return results;
    }
}
