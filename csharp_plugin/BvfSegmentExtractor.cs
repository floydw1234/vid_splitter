using System;
using System.Collections.Generic;
using System.IO;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Extracts resolved BVF segment payloads to standalone files for ffmpeg concat.
/// </summary>
internal static class BvfSegmentExtractor
{
    private const int AssetBlockHeaderSize = 32;

    /// <summary>
    /// Writes each resolved segment payload to <paramref name="outputDirectory"/>.
    /// </summary>
    /// <returns>Absolute paths in playback order.</returns>
    public static IReadOnlyList<string> ExtractSegments(
        string bvfPath,
        IReadOnlyList<ResolvedSegment> segments,
        string outputDirectory)
    {
        Directory.CreateDirectory(outputDirectory);

        var paths = new List<string>(segments.Count);
        using var bvfStream = File.OpenRead(bvfPath);

        for (var i = 0; i < segments.Count; i++)
        {
            var segment = segments[i];
            if (segment.DataLength < AssetBlockHeaderSize)
            {
                throw new InvalidDataException(
                    $"Resolved segment '{segment.SegmentId}' has invalid data length {segment.DataLength}.");
            }

            var payloadLength = checked((long)segment.DataLength - AssetBlockHeaderSize);
            var payloadOffset = checked((long)segment.DataOffset + AssetBlockHeaderSize);
            var outputPath = Path.Combine(outputDirectory, $"seg_{i:D4}.mp4");

            bvfStream.Seek(payloadOffset, SeekOrigin.Begin);
            using (var output = File.Create(outputPath))
            {
                CopyBytes(bvfStream, output, payloadLength);
            }

            paths.Add(outputPath);
        }

        return paths;
    }

    /// <summary>
    /// Reads a single resolved segment payload (asset block minus its header)
    /// directly from the BVF container.
    /// </summary>
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

    private static void CopyBytes(Stream input, Stream output, long byteCount)
    {
        var buffer = new byte[81920];
        var remaining = byteCount;

        while (remaining > 0)
        {
            var toRead = (int)Math.Min(buffer.Length, remaining);
            var read = input.Read(buffer, 0, toRead);
            if (read <= 0)
                throw new EndOfStreamException("Unexpected end of BVF segment payload.");

            output.Write(buffer, 0, read);
            remaining -= read;
        }
    }
}
