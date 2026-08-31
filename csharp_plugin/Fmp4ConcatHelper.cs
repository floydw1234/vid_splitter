using System;
using System.Collections.Generic;
using System.Text;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Computes byte ranges for concatenating self-contained fMP4/CMAF segment assets
/// into one logical fragmented MP4 stream.
/// </summary>
internal static class Fmp4ConcatHelper
{
    /// <summary>
    /// Returns the slice of a segment payload that should be emitted when building
    /// a concatenated stream. Non-fMP4 payloads pass through unchanged.
    /// </summary>
    public static (long EmitStart, long EmitLength) GetEmitRange(
        ReadOnlySpan<byte> payload,
        bool isFirstSegment,
        bool isLastSegment)
    {
        if (payload.Length < 8)
            return (0, payload.Length);

        if (!IsBoxType(payload, 0, "ftyp"))
            return (0, payload.Length);

        var initLength = GetInitSegmentLength(payload);
        if (initLength <= 0 || initLength >= payload.Length)
            return (0, payload.Length);

        var emitStart = isFirstSegment ? 0L : initLength;
        if (emitStart >= payload.Length)
            return (0, 0);

        var emitEnd = (long)payload.Length;
        if (!isLastSegment)
        {
            var mfraOffset = FindTopLevelBox(payload, emitStart, "mfra");
            if (mfraOffset >= 0)
                emitEnd = mfraOffset;
        }

        return (emitStart, emitEnd - emitStart);
    }

    /// <summary>
    /// Returns the byte range of the initialization portion (ftyp + moov) of a
    /// self-contained fMP4 segment payload, or a zero-length range if the payload
    /// is not fMP4.
    /// </summary>
    public static (long Start, long Length) GetInitRange(ReadOnlySpan<byte> payload)
    {
        if (payload.Length < 8 || !IsBoxType(payload, 0, "ftyp"))
            return (0, 0);

        var initLength = GetInitSegmentLength(payload);
        if (initLength <= 0 || initLength > payload.Length)
            return (0, 0);

        return (0, initLength);
    }

    /// <summary>
    /// Returns the byte range of the media portion (moof + mdat, excluding init
    /// boxes and any trailing mfra) of a self-contained fMP4 segment payload.
    /// Non-fMP4 payloads pass through unchanged.
    /// </summary>
    public static (long Start, long Length) GetMediaRange(ReadOnlySpan<byte> payload)
        => GetEmitRange(payload, isFirstSegment: false, isLastSegment: false);

    /// <summary>
    /// Splits a self-contained fMP4 payload into (moof + mdat) fragment ranges,
    /// skipping the init boxes and any trailing mfra. Offsets are relative to
    /// the start of <paramref name="payload"/>.
    /// </summary>
    public static IReadOnlyList<(int Start, int Length)> GetFragmentRanges(ReadOnlySpan<byte> payload)
    {
        var ranges = new List<(int Start, int Length)>();
        var (_, initLength) = GetInitRange(payload);
        var offset = (int)initLength;

        while (offset + 8 <= payload.Length)
        {
            if (!TryReadBox(payload, offset, out var size, out var type))
                break;

            if (type == "mfra")
                break;

            if (type == "moof")
            {
                var start = offset;
                var end = offset + (int)size;
                var next = end;
                if (TryReadBox(payload, next, out var nextSize, out var nextType) && nextType == "mdat")
                    end = next + (int)nextSize;

                ranges.Add((start, end - start));
                offset = end;
                continue;
            }

            offset += (int)size;
        }

        if (ranges.Count == 0)
        {
            var (mediaStart, mediaLength) = GetMediaRange(payload);
            if (mediaLength > 0)
                ranges.Add(((int)mediaStart, (int)mediaLength));
        }

        return ranges;
    }

    private static long GetInitSegmentLength(ReadOnlySpan<byte> payload)
    {
        long offset = 0;
        long initEnd = 0;
        var sawMoov = false;

        while (offset + 8 <= payload.Length)
        {
            if (!TryReadBox(payload, offset, out var size, out var type))
                break;

            if (type is "ftyp" or "moov")
            {
                initEnd = offset + size;
                if (type == "moov")
                    sawMoov = true;

                offset += size;
                continue;
            }

            break;
        }

        return sawMoov ? initEnd : 0;
    }

    private static long FindTopLevelBox(ReadOnlySpan<byte> payload, long startOffset, string boxType)
    {
        var offset = startOffset;
        while (offset + 8 <= payload.Length)
        {
            if (!TryReadBox(payload, offset, out var size, out var type))
                break;

            if (type == boxType)
                return offset;

            offset += size;
        }

        return -1;
    }

    private static bool TryReadBox(ReadOnlySpan<byte> payload, long offset, out long size, out string type)
    {
        size = 0;
        type = string.Empty;

        if (offset + 8 > payload.Length)
            return false;

        var size32 = ReadUInt32BigEndian(payload, offset);
        type = Encoding.ASCII.GetString(payload.Slice((int)offset + 4, 4));

        if (size32 == 0)
        {
            size = payload.Length - offset;
        }
        else if (size32 == 1)
        {
            if (offset + 16 > payload.Length)
                return false;

            size = (long)ReadUInt64BigEndian(payload, offset + 8);
        }
        else
        {
            size = size32;
        }

        return size >= 8 && offset + size <= payload.Length;
    }

    private static bool IsBoxType(ReadOnlySpan<byte> payload, long offset, string boxType)
    {
        if (offset + 8 > payload.Length)
            return false;

        return Encoding.ASCII.GetString(payload.Slice((int)offset + 4, 4)) == boxType;
    }

    private static uint ReadUInt32BigEndian(ReadOnlySpan<byte> buffer, long offset)
        => ((uint)buffer[(int)offset] << 24)
           | ((uint)buffer[(int)offset + 1] << 16)
           | ((uint)buffer[(int)offset + 2] << 8)
           | buffer[(int)offset + 3];

    private static ulong ReadUInt64BigEndian(ReadOnlySpan<byte> buffer, long offset)
    {
        ulong value = 0;
        for (var i = 0; i < 8; i++)
            value = (value << 8) | buffer[(int)offset + i];
        return value;
    }
}
