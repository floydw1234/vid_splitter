using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Jellyfin.Plugin.SmartBranching;
using Jellyfin.Plugin.SmartBranching.Models;
using Xunit;
using ZstdSharp;

namespace SmartBranching.Plugin.Tests;

public class ResolvedSegmentStreamTests
{
    [Fact]
    public void Read_SequentiallyAcrossResolvedSegments_ReturnsCombinedPayloadBytes()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        using (stream)
        using (var buffer = new MemoryStream())
        {
            stream.CopyTo(buffer);

            var text = Encoding.UTF8.GetString(buffer.ToArray());

            Assert.Equal("AAAABBBBBCC", text);
        }
    }

    [Fact]
    public void Length_ReturnsTotalPayloadLengthAcrossResolvedSegments()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        Assert.Equal(11, stream.Length);
    }

    [Fact]
    public void Position_AdvancesAsBytesAreRead()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        var buffer = new byte[6];

        Assert.Equal(0, stream.Position);

        var bytesRead = stream.Read(buffer, 0, buffer.Length);

        Assert.Equal(6, bytesRead);
        Assert.Equal(6, stream.Position);
        Assert.Equal("AAAABB", Encoding.UTF8.GetString(buffer));
    }

    [Fact]
    public void CanSeek_IsTrue()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        Assert.True(stream.CanSeek);
    }

    [Fact]
    public void Seek_FromBeginningWithinSingleSegment_UpdatesPosition_AndReadReturnsSegmentSuffix()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        var newPosition = stream.Seek(2, SeekOrigin.Begin);
        var buffer = new byte[2];
        var bytesRead = stream.Read(buffer, 0, buffer.Length);

        Assert.Equal(2, newPosition);
        Assert.Equal(2, stream.Position);
        Assert.Equal(2, bytesRead);
        Assert.Equal("AA", Encoding.UTF8.GetString(buffer, 0, bytesRead));
    }

    [Fact]
    public void Seek_CurrentZero_ReturnsCurrentPosition()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        var buffer = new byte[3];
        var bytesRead = stream.Read(buffer, 0, buffer.Length);
        var reportedPosition = stream.Seek(0, SeekOrigin.Current);

        Assert.Equal(3, bytesRead);
        Assert.Equal(3, reportedPosition);
        Assert.Equal(3, stream.Position);
        Assert.Equal("AAA", Encoding.UTF8.GetString(buffer, 0, bytesRead));
    }

    [Fact]
    public void Seek_ToLastByteOfSegment_ThenRead_CrossesIntoNextSegment()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        var newPosition = stream.Seek(3, SeekOrigin.Begin);
        var buffer = new byte[3];
        var bytesRead = stream.Read(buffer, 0, buffer.Length);

        Assert.Equal(3, newPosition);
        Assert.Equal(6, stream.Position);
        Assert.Equal(3, bytesRead);
        Assert.Equal("ABB", Encoding.UTF8.GetString(buffer, 0, bytesRead));
    }

    [Fact]
    public void Seek_ToStartOfLaterSegment_ThenReadToEnd_ReturnsRemainingSuffixToEof()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        var newPosition = stream.Seek(9, SeekOrigin.Begin);
        using var buffer = new MemoryStream();
        stream.CopyTo(buffer);

        Assert.Equal(9, newPosition);
        Assert.Equal(11, stream.Position);
        Assert.Equal("CC", Encoding.UTF8.GetString(buffer.ToArray()));
    }

    [Fact]
    public void Seek_ToStreamLength_NextReadReturnsZero()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        var eofPosition = stream.Seek(stream.Length, SeekOrigin.Begin);
        var buffer = new byte[4];
        var bytesRead = stream.Read(buffer, 0, buffer.Length);

        Assert.Equal(11, eofPosition);
        Assert.Equal(11, stream.Position);
        Assert.Equal(0, bytesRead);
    }

    [Fact]
    public void Seek_NegativeFromBeginning_Throws()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        Assert.ThrowsAny<IOException>(() => stream.Seek(-1, SeekOrigin.Begin));
    }

    [Fact]
    public void Seek_BeyondLength_CapsAtLength_AndNextReadReturnsZero()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        using var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);

        var newPosition = stream.Seek(100, SeekOrigin.Begin);
        var buffer = new byte[4];
        var bytesRead = stream.Read(buffer, 0, buffer.Length);

        Assert.Equal(stream.Length, newPosition);
        Assert.Equal(stream.Length, stream.Position);
        Assert.Equal(0, bytesRead);
    }

    [Fact]
    public void Read_AfterDispose_ThrowsObjectDisposedException()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);
        stream.Dispose();

        var buffer = new byte[1];

        Assert.Throws<ObjectDisposedException>(() => stream.Read(buffer, 0, buffer.Length));
    }

    [Fact]
    public void Seek_AfterDispose_ThrowsObjectDisposedException()
    {
        using var bvfFile = CreateTempBvf(
            ("seg-a", "AAAA"),
            ("seg-b", "BBBBB"),
            ("seg-c", "CC"));
        var resolvedSegments = CreateResolvedSegments(bvfFile, "seg-a", "seg-b", "seg-c");

        var stream = new ResolvedSegmentStream(
            bvfFile,
            resolvedSegments);
        stream.Dispose();

        Assert.Throws<ObjectDisposedException>(() => stream.Seek(0, SeekOrigin.Begin));
    }

    private static List<ResolvedSegment> CreateResolvedSegments(string bvfPath, params string[] segmentIds)
    {
        var index = BVFReader.GetSegments(bvfPath).ToDictionary(segment => segment.segmentId, StringComparer.Ordinal);
        var resolved = new List<ResolvedSegment>(segmentIds.Length);

        foreach (var segmentId in segmentIds)
        {
            var indexSegment = index[segmentId];
            resolved.Add(CreateResolvedSegment(segmentId, indexSegment.dataOffset, indexSegment.dataLength, indexSegment.durationMs));
        }

        return resolved;
    }

    private static ResolvedSegment CreateResolvedSegment(string segmentId, ulong dataOffset, ulong dataLength, ulong durationMs)
    {
        return new ResolvedSegment
        {
            SegmentId = segmentId,
            DataOffset = dataOffset,
            DataLength = dataLength,
            DurationMs = durationMs,
            AudioHash = string.Empty,
            Action = "play",
            SwapType = "original",
            IsSwapped = false,
            ResolvedPath = $"bvf://fixture?seg_id={segmentId}&offset={dataOffset}&length={dataLength}",
            Source = new Segment
            {
                Id = segmentId,
                StartTime = 0,
                EndTime = 1,
                IsFiller = false,
            }
        };
    }

    private static TempFile CreateTempBvf(params (string SegmentId, string PayloadText)[] segments)
    {
        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Stream Fixture",
              "duration_ms": 3000,
              "profiles": {},
              "segments": [
                { "id": "seg-a", "start_ms": 0, "end_ms": 1000, "tags": [], "risk": "safe", "is_filler": false, "profiles": {} },
                { "id": "seg-b", "start_ms": 1000, "end_ms": 2000, "tags": [], "risk": "safe", "is_filler": false, "profiles": {} },
                { "id": "seg-c", "start_ms": 2000, "end_ms": 3000, "tags": [], "risk": "safe", "is_filler": false, "profiles": {} }
              ]
            }
            """;

        var manifestBytes = Encoding.UTF8.GetBytes(manifestJson);
        byte[] compressedManifest;
        using (var compressor = new Compressor())
        {
            compressedManifest = compressor.Wrap(manifestBytes).ToArray();
        }

        const ulong headerSize = 64;
        const ulong indexEntrySize = 40;
        var indexOffset = headerSize;
        var indexLength = (ulong)segments.Length * indexEntrySize;
        var manifestOffset = indexOffset + indexLength;
        var manifestLength = (ulong)compressedManifest.Length;
        var nextDataOffset = manifestOffset + manifestLength;

        var assetBlocks = new List<(string SegmentId, byte[] Block, ulong DataOffset, ulong DataLength)>(segments.Length);
        foreach (var (segmentId, payloadText) in segments)
        {
            var payload = Encoding.UTF8.GetBytes(payloadText);
            var block = CreateAssetBlock(segmentId, payload);
            assetBlocks.Add((segmentId, block, nextDataOffset, (ulong)block.Length));
            nextDataOffset += (ulong)block.Length;
        }

        var path = Path.Combine(Path.GetTempPath(), $"{Guid.NewGuid():N}.bvf");
        using (var stream = File.Create(path))
        using (var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: false))
        {
            const ulong magic = 0x0000000001465642;
            const uint flags = 0;

            writer.Write(magic);
            writer.Write((ushort)1);
            writer.Write((ushort)0);
            writer.Write(flags);
            writer.Write(indexOffset);
            writer.Write(indexLength);
            writer.Write(manifestOffset);
            writer.Write(manifestLength);
            writer.Write((uint)segments.Length);
            writer.Write(3000UL);
            writer.Write(0U);

            foreach (var (segmentId, _, dataOffset, dataLength) in assetBlocks)
            {
                WriteSegmentId(writer, segmentId);
                writer.Write(dataOffset);
                writer.Write(dataLength);
                writer.Write(1000UL);
            }

            writer.Write(compressedManifest);

            foreach (var (_, block, _, _) in assetBlocks)
            {
                writer.Write(block);
            }
        }

        return new TempFile(path);
    }

    private static byte[] CreateAssetBlock(string segmentId, byte[] payload)
    {
        using var memory = new MemoryStream();
        using var writer = new BinaryWriter(memory, Encoding.UTF8, leaveOpen: true);

        writer.Write(0x00415642);
        WriteSegmentId(writer, segmentId);
        writer.Write(0U);
        writer.Write(0U);
        writer.Write(0U);
        writer.Write(payload);
        writer.Flush();

        return memory.ToArray();
    }

    private static void WriteSegmentId(BinaryWriter writer, string segmentId)
    {
        var bytes = Encoding.UTF8.GetBytes(segmentId);
        if (bytes.Length > 16)
            throw new ArgumentException("Segment ID must be 16 bytes or fewer.", nameof(segmentId));

        writer.Write(bytes);
        if (bytes.Length < 16)
        {
            writer.Write(new byte[16 - bytes.Length]);
        }
    }

    private sealed class TempFile : IDisposable
    {
        private readonly string _path;

        public TempFile(string path)
        {
            _path = path;
        }

        public static implicit operator string(TempFile file) => file._path;

        public void Dispose()
        {
            if (File.Exists(_path))
            {
                File.Delete(_path);
            }
        }
    }
}
