using System;
using System.IO;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Reads resolved BVF segment payloads (asset block minus its header) directly
/// from the container. Used by HLS serving; no files are written.
/// </summary>
internal static class BvfSegmentExtractor
{
    internal const int AssetBlockHeaderSize = 32;

    public static byte[] ReadSegmentPayload(string bvfPath, ResolvedSegment segment)
    {
        if (segment.DataLength < AssetBlockHeaderSize)
        {
            throw new InvalidDataException(
                $"Resolved segment '{segment.SegmentId}' has invalid data length {segment.DataLength}.");
        }

        var payloadLength = checked((long)segment.DataLength - AssetBlockHeaderSize);
        var payloadOffset = checked((long)segment.DataOffset + AssetBlockHeaderSize);

        using var bvfStream = File.OpenRead(bvfPath);
        bvfStream.Seek(payloadOffset, SeekOrigin.Begin);

        var buffer = new byte[payloadLength];
        var totalRead = 0;
        while (totalRead < buffer.Length)
        {
            var read = bvfStream.Read(buffer, totalRead, buffer.Length - totalRead);
            if (read <= 0)
                throw new EndOfStreamException("Unexpected end of BVF segment payload.");
            totalRead += read;
        }

        return buffer;
    }
}
