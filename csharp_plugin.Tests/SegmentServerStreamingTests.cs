using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using Jellyfin.Plugin.SmartBranching;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Controller.LiveTv;
using MediaBrowser.Model.Serialization;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;
using ZstdSharp;
using SmartBranchingPlugin = Jellyfin.Plugin.SmartBranching.Plugin;

namespace SmartBranching.Plugin.Tests;

public class SegmentServerStreamingTests
{
    [Fact]
    public void ResolveAllSegmentsForProfile_OmitsSkippedSegments_UsesSwapTargets_AndPreservesManifestOrder()
    {
        CreatePluginContext();

        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 120000,
              "profiles": {
                "child": { "filters": {} },
                "adult": { "filters": {} }
              },
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 0,
                  "end_ms": 1000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "play", "segment_id": "seg-001" }
                  }
                },
                {
                  "id": "seg-002",
                  "start_ms": 1000,
                  "end_ms": 2000,
                  "tags": ["violence"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "skip", "segment_id": "seg-002" }
                  }
                },
                {
                  "id": "seg-003",
                  "start_ms": 2000,
                  "end_ms": 3000,
                  "tags": ["language"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "swap", "segment_id": "fill-003" }
                  }
                },
                {
                  "id": "fill-003",
                  "start_ms": 2000,
                  "end_ms": 3000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": true,
                  "profiles": {}
                },
                {
                  "id": "seg-004",
                  "start_ms": 3000,
                  "end_ms": 4000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": {}
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvf(
            manifestJson,
            ("seg-001", "payload-1"),
            ("seg-002", "payload-2"),
            ("seg-003", "payload-3"),
            ("fill-003", "payload-fill"),
            ("seg-004", "payload-4"));

        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, new TestApplicationPaths());

        var resolved = InvokeResolveAllSegmentsForProfile(server, bvfFile, "child");

        Assert.Equal(new[] { "seg-001", "fill-003", "seg-004" }, resolved.Select(segment => segment.SegmentId).ToArray());
        Assert.Equal(new[] { "play", "swap", "play" }, resolved.Select(segment => segment.Action).ToArray());
        Assert.Equal(new[] { false, true, false }, resolved.Select(segment => segment.IsSwapped).ToArray());
    }

    [Fact]
    public void OpenMediaSource_GetStream_ReadProducesResolvedPayload_AndIsNotMemoryStream()
    {
        CreatePluginContext();

        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 4000,
              "profiles": {
                "child": { "filters": {} },
                "adult": { "filters": {} }
              },
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 0,
                  "end_ms": 1000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "play", "segment_id": "seg-001" }
                  }
                },
                {
                  "id": "seg-002",
                  "start_ms": 1000,
                  "end_ms": 2000,
                  "tags": ["violence"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "skip", "segment_id": "seg-002" }
                  }
                },
                {
                  "id": "seg-003",
                  "start_ms": 2000,
                  "end_ms": 3000,
                  "tags": ["language"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "swap", "segment_id": "fill-003" }
                  }
                },
                {
                  "id": "fill-003",
                  "start_ms": 2000,
                  "end_ms": 3000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": true,
                  "profiles": {}
                },
                {
                  "id": "seg-004",
                  "start_ms": 3000,
                  "end_ms": 4000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": {}
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvf(
            manifestJson,
            ("seg-001", "AAAA"),
            ("seg-002", "SKIP"),
            ("seg-003", "MMMM"),
            ("fill-003", "FILL"),
            ("seg-004", "ZZ"));

        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, new TestApplicationPaths());
        var liveStream = OpenMediaSource(server, bvfFile, "child");

        using var stream = liveStream.GetStream();
        using var buffer = new MemoryStream();
        stream.CopyTo(buffer);

        Assert.False(stream is MemoryStream);
        Assert.Equal("AAAAFILLZZ", Encoding.UTF8.GetString(buffer.ToArray()));
    }

    [Fact]
    public void OpenMediaSource_GetStream_SeekWorksWithinResolvedPayload()
    {
        CreatePluginContext();

        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 4000,
              "profiles": {
                "child": { "filters": {} },
                "adult": { "filters": {} }
              },
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 0,
                  "end_ms": 1000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "play", "segment_id": "seg-001" }
                  }
                },
                {
                  "id": "seg-002",
                  "start_ms": 1000,
                  "end_ms": 2000,
                  "tags": ["violence"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "skip", "segment_id": "seg-002" }
                  }
                },
                {
                  "id": "seg-003",
                  "start_ms": 2000,
                  "end_ms": 3000,
                  "tags": ["language"],
                  "risk": "mature",
                  "is_filler": false,
                  "profiles": {
                    "child": { "action": "swap", "segment_id": "fill-003" }
                  }
                },
                {
                  "id": "fill-003",
                  "start_ms": 2000,
                  "end_ms": 3000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": true,
                  "profiles": {}
                },
                {
                  "id": "seg-004",
                  "start_ms": 3000,
                  "end_ms": 4000,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": {}
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvf(
            manifestJson,
            ("seg-001", "AAAA"),
            ("seg-002", "SKIP"),
            ("seg-003", "MMMM"),
            ("fill-003", "FILL"),
            ("seg-004", "ZZ"));

        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, new TestApplicationPaths());
        var liveStream = OpenMediaSource(server, bvfFile, "child");

        using var stream = liveStream.GetStream();
        var newPosition = stream.Seek(4, SeekOrigin.Begin);
        var readBuffer = new byte[4];
        var bytesRead = stream.Read(readBuffer, 0, readBuffer.Length);

        Assert.Equal(4, newPosition);
        Assert.False(stream is MemoryStream);
        Assert.Equal(8, stream.Position);
        Assert.Equal(4, bytesRead);
        Assert.Equal("FILL", Encoding.UTF8.GetString(readBuffer, 0, bytesRead));
    }

    private static List<ResolvedSegment> InvokeResolveAllSegmentsForProfile(SegmentServer server, string bvfPath, string profileKey)
    {
        var method = typeof(SegmentServer).GetMethod(
            "ResolveAllSegmentsForProfile",
            BindingFlags.Instance | BindingFlags.NonPublic);

        Assert.NotNull(method);

        var result = method!.Invoke(server, new object[] { bvfPath, profileKey });

        var exception = result as TargetInvocationException;
        if (exception != null)
        {
            throw exception.InnerException ?? exception;
        }

        return Assert.IsType<List<ResolvedSegment>>(result);
    }

    private static ILiveStream OpenMediaSource(SegmentServer server, string bvfPath, string profileKey)
    {
        var token = EncodeMediaSourceToken(bvfPath, profileKey);
        return server.OpenMediaSource(token, new List<ILiveStream>(), CancellationToken.None).GetAwaiter().GetResult();
    }

    private static SmartBranchingPlugin CreatePluginContext()
    {
        return new SmartBranchingPlugin(new TestApplicationPaths(), new TestXmlSerializer());
    }

    private static string EncodeMediaSourceToken(string bvfPath, string profileKey)
    {
        return $"smart-branch:{Base64UrlEncode(bvfPath)}:{Base64UrlEncode(profileKey)}";
    }

    private static string Base64UrlEncode(string value)
    {
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(value))
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');
    }

    private static TempFile CreateTempBvf(string manifestJson, params (string SegmentId, string PayloadText)[] segments)
    {
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
            writer.Write(4000UL);
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

    private sealed class TestApplicationPaths : IApplicationPaths
    {
        private readonly string _root = Path.Combine(Path.GetTempPath(), "smartbranching-tests", Guid.NewGuid().ToString("N"));

        public TestApplicationPaths()
        {
            Directory.CreateDirectory(_root);
        }

        public string ProgramDataPath => _root;
        public string WebPath => _root;
        public string ProgramSystemPath => _root;
        public string DataPath => _root;
        public string ImageCachePath => _root;
        public string PluginsPath => _root;
        public string PluginConfigurationsPath => _root;
        public string LogDirectoryPath => _root;
        public string ConfigurationDirectoryPath => _root;
        public string SystemConfigurationFilePath => Path.Combine(_root, "system.xml");
        public string CachePath => _root;
        public string TempDirectory => _root;
        public string VirtualDataPath => _root;
    }

    private sealed class TestXmlSerializer : IXmlSerializer
    {
        public object DeserializeFromStream(Type type, Stream stream) => Activator.CreateInstance(type)!;

        public void SerializeToStream(object obj, Stream stream)
        {
        }

        public void SerializeToFile(object obj, string file)
        {
        }

        public object DeserializeFromFile(Type type, string file) => Activator.CreateInstance(type)!;

        public object DeserializeFromBytes(Type type, byte[] buffer) => Activator.CreateInstance(type)!;
    }
}
