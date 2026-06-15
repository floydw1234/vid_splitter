using System;
using System.IO;
using System.Text;
using Jellyfin.Plugin.SmartBranching;
using Xunit;
using ZstdSharp;

namespace Jellyfin.Plugin.SmartBranching.Tests;

public class BVFReaderManifestTests
{
    [Theory]
    [InlineData("mute")]
    [InlineData("blur")]
    public void GetSegments_ProfileRuntimeResolver_RejectsUnsupportedActions(string unsupportedAction)
    {
        var manifestJson = $$"""
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {
                "child": { "name": "Child", "filters": {} }
              },
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 1000,
                  "end_ms": 5000,
                  "tags": ["violence"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "{{unsupportedAction}}", "segment_id": "seg-001" }
                  }
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvfWithSegment(manifestJson, "seg-001");

        var ex = Assert.Throws<InvalidDataException>(() => BVFReader.GetSegments(bvfFile, "child"));

        Assert.Contains($"Unsupported BVF action for runtime playback: '{unsupportedAction}'", ex.Message);
    }

    [Fact]
    public void GetSegments_ProfileRuntimeResolver_RejectsMissingSwapTargets()
    {
        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {
                "child": { "name": "Child", "filters": {} }
              },
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 1000,
                  "end_ms": 5000,
                  "tags": ["violence"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "swap", "segment_id": "missing_999" }
                  }
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvfWithSegment(manifestJson, "seg-001");

        var ex = Assert.Throws<InvalidDataException>(() => BVFReader.GetSegments(bvfFile, "child"));

        Assert.Contains("seg-001", ex.Message);
        Assert.Contains("missing_999", ex.Message);
    }

    [Fact]
    public void LoadBvfManifest_PreservesSegmentTopics()
    {
        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {},
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 1000,
                  "end_ms": 5000,
                  "tags": ["violence"],
                  "topics": ["cars", "chase"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {}
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvf(manifestJson);

        var manifest = BVFReader.LoadBvfManifest(bvfFile);

        var segment = Assert.Single(manifest.Segments);
        Assert.Equal(new[] { "cars", "chase" }, segment.Topics);
    }

    [Fact]
    public void LoadBvfManifest_MissingSegmentTopics_DefaultsToEmpty()
    {
        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {},
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 1000,
                  "end_ms": 5000,
                  "tags": ["violence"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {}
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvf(manifestJson);

        var manifest = BVFReader.LoadBvfManifest(bvfFile);

        var segment = Assert.Single(manifest.Segments);
        Assert.NotNull(segment.Topics);
        Assert.Empty(segment.Topics);
    }

    [Fact]
    public void ReadHeader_RejectsTruncatedBvfHeader()
    {
        var path = Path.Combine(Path.GetTempPath(), $"{Guid.NewGuid():N}.bvf");
        File.WriteAllBytes(path, new byte[] { 0x42, 0x56, 0x46, 0x01, 0x00, 0x00, 0x00, 0x00 });
        using var bvfFile = new TempFile(path);

        var ex = Assert.Throws<InvalidDataException>(() => BVFReader.ReadHeader(bvfFile));
        Assert.Contains("header", ex.Message, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("truncated", ex.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void GetSegments_RejectsIndexRegionOutsideFile()
    {
        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {},
              "segments": []
            }
            """;

        using var bvfFile = CreateTempBvf(manifestJson);
        RewriteUInt64(bvfFile, 16, 10000UL);
        RewriteUInt64(bvfFile, 24, 10000UL);

        var ex = Assert.Throws<InvalidDataException>(() => BVFReader.GetSegments(bvfFile));
        Assert.Contains("index", ex.Message, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("outside", ex.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void LoadBvfManifest_RejectsManifestRangeOutsideFile()
    {
        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {},
              "segments": []
            }
            """;

        using var bvfFile = CreateTempBvf(manifestJson);
        RewriteUInt64(bvfFile, 32, 10000UL);
        RewriteUInt64(bvfFile, 40, 10000UL);

        var ex = Assert.Throws<InvalidDataException>(() => BVFReader.LoadBvfManifest(bvfFile));
        Assert.Contains("manifest", ex.Message, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("outside", ex.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void GetSegments_RejectsTruncatedAssetBlockReads()
    {
        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {},
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 1000,
                  "end_ms": 5000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": {}
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvfWithSegment(manifestJson, "seg-001");
        var assetOffset = ReadUInt64(bvfFile, 80);
        TruncateFile(bvfFile, checked((int)assetOffset + 8));

        var ex = Assert.Throws<InvalidDataException>(() => BVFReader.GetSegments(bvfFile));
        Assert.Contains("asset", ex.Message, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("truncated", ex.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void GetSegments_RejectsInvalidAssetBlockMagic()
    {
        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {},
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 1000,
                  "end_ms": 5000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": {}
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvfWithSegment(manifestJson, "seg-001");
        var assetOffset = ReadUInt64(bvfFile, 80);
        RewriteUInt32(bvfFile, (long)assetOffset, 0x12345678U);

        var ex = Assert.ThrowsAny<Exception>(() => BVFReader.GetSegments(bvfFile));
        Assert.Contains("magic", ex.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void LoadBvfManifest_RejectsCorruptZstdManifestPayload()
    {
        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {},
              "segments": []
            }
            """;

        using var bvfFile = CreateTempBvf(manifestJson);
        RewriteBytes(bvfFile, 64, Encoding.UTF8.GetBytes("not-zstd"));

        var ex = Assert.ThrowsAny<Exception>(() => BVFReader.LoadBvfManifest(bvfFile));
        Assert.Contains("manifest", ex.Message, StringComparison.OrdinalIgnoreCase);
    }

    private static TempFile CreateTempBvf(string manifestJson)
    {
        var manifestBytes = Encoding.UTF8.GetBytes(manifestJson);
        byte[] compressedManifest;
        using (var compressor = new Compressor())
        {
            compressedManifest = compressor.Wrap(manifestBytes).ToArray();
        }

        var path = Path.Combine(Path.GetTempPath(), $"{Guid.NewGuid():N}.bvf");
        using (var stream = File.Create(path))
        using (var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: false))
        {
            const ulong magic = 0x0000000001465642;
            const uint flags = 0;
            const ulong headerSize = 64;
            var manifestOffset = headerSize;
            var manifestLength = (ulong)compressedManifest.Length;

            writer.Write(magic);
            writer.Write((ushort)1);
            writer.Write((ushort)0);
            writer.Write(flags);
            writer.Write(manifestOffset);
            writer.Write(0UL);
            writer.Write(manifestOffset);
            writer.Write(manifestLength);
            writer.Write(0U);
            writer.Write(120000UL);
            writer.Write(0U);
            writer.Write(compressedManifest);
        }

        return new TempFile(path);
    }

    private static TempFile CreateTempBvfWithSegment(string manifestJson, string segmentId)
    {
        var manifestBytes = Encoding.UTF8.GetBytes(manifestJson);
        byte[] compressedManifest;
        using (var compressor = new Compressor())
        {
            compressedManifest = compressor.Wrap(manifestBytes).ToArray();
        }

        var segmentPayload = Encoding.UTF8.GetBytes("ftyp....moov....moof....mdat-safe");
        var assetBlock = CreateAssetBlock(segmentId, segmentPayload);
        const ulong headerSize = 64;
        const ulong indexEntrySize = 40;
        var indexOffset = headerSize;
        var indexLength = indexEntrySize;
        var manifestOffset = indexOffset + indexLength;
        var manifestLength = (ulong)compressedManifest.Length;
        var dataOffset = manifestOffset + manifestLength;
        var dataLength = (ulong)assetBlock.Length;

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
            writer.Write(1U);
            writer.Write(120000UL);
            writer.Write(0U);

            WriteSegmentId(writer, segmentId);
            writer.Write(dataOffset);
            writer.Write(dataLength);
            writer.Write(4000UL);

            writer.Write(compressedManifest);
            writer.Write(assetBlock);
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

    private static void RewriteUInt64(string path, long offset, ulong value)
    {
        var bytes = File.ReadAllBytes(path);
        Array.Copy(BitConverter.GetBytes(value), 0, bytes, offset, sizeof(ulong));
        File.WriteAllBytes(path, bytes);
    }

    private static void RewriteUInt32(string path, long offset, uint value)
    {
        var bytes = File.ReadAllBytes(path);
        Array.Copy(BitConverter.GetBytes(value), 0, bytes, offset, sizeof(uint));
        File.WriteAllBytes(path, bytes);
    }

    private static ulong ReadUInt64(string path, long offset)
    {
        var bytes = File.ReadAllBytes(path);
        return BitConverter.ToUInt64(bytes, checked((int)offset));
    }

    private static void RewriteBytes(string path, long offset, byte[] value)
    {
        var bytes = File.ReadAllBytes(path);
        Array.Copy(value, 0, bytes, offset, value.Length);
        File.WriteAllBytes(path, bytes);
    }

    private static void TruncateFile(string path, int length)
    {
        var bytes = File.ReadAllBytes(path);
        Array.Resize(ref bytes, length);
        File.WriteAllBytes(path, bytes);
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
