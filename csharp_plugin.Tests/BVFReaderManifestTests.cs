using System;
using System.IO;
using System.Text;
using Jellyfin.Plugin.SmartBranching;
using Xunit;
using ZstdSharp;

namespace Jellyfin.Plugin.SmartBranching.Tests;

public class BVFReaderManifestTests
{
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
