using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Emby.Naming.Common;
using Jellyfin.Data.Enums;
using Jellyfin.Plugin.SmartBranching;
using Jellyfin.Plugin.SmartBranching.Configuration;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.IO;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;
using ZstdSharp;
using SmartBranchingPlugin = Jellyfin.Plugin.SmartBranching.Plugin;

namespace SmartBranching.Plugin.Tests;

[Collection(PluginStateCollection.Name)]
public class BvfLibraryIntegrationTests
{
    [Fact]
    public void FormatRegistration_AddsBvfExtension_Once()
    {
        var naming = new NamingOptions();
        Assert.DoesNotContain(".bvf", naming.VideoFileExtensions, StringComparer.OrdinalIgnoreCase);

        Assert.True(BvfFormatRegistration.EnsureRegistered(naming));
        Assert.Contains(".bvf", naming.VideoFileExtensions, StringComparer.OrdinalIgnoreCase);

        Assert.False(BvfFormatRegistration.EnsureRegistered(naming));
        Assert.Equal(1, naming.VideoFileExtensions.Count(ext => ext.Equals(".bvf", StringComparison.OrdinalIgnoreCase)));
    }
    [Fact]
    public void PreferBvfSiblingIgnoreRule_IgnoresVideoWhenSiblingBvfExists()
    {
        var naming = new NamingOptions();
        BvfFormatRegistration.EnsureRegistered(naming);
        var rule = new PreferBvfSiblingIgnoreRule(naming);

        var dir = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        try
        {
            var mp4 = Path.Combine(dir, "clip.mp4");
            var bvf = Path.Combine(dir, "clip.bvf");
            File.WriteAllText(mp4, "x");
            File.WriteAllText(bvf, "y");

            Assert.True(rule.ShouldIgnore(new FileSystemMetadata { FullName = mp4, Name = "clip.mp4" }, parent: null));
            Assert.False(rule.ShouldIgnore(new FileSystemMetadata { FullName = bvf, Name = "clip.bvf" }, parent: null));
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }

    [Fact]
    public void BvfItemResolver_ResolveMultiple_CreatesBvfMovieWithMetadata()
    {
        CreatePluginContext();
        var naming = new NamingOptions();
        var resolver = new BvfItemResolver(NullLogger<BvfItemResolver>.Instance, naming);

        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Branch Title",
              "duration_ms": 5000,
              "profiles": { "adult": { "filters": {} } },
              "segments": []
            }
            """;

        using var bvfFile = CreateTempBvf(manifestJson, totalDurationMs: 5000, ("seg-001", "AAAA"));
        var result = resolver.ResolveMultiple(
            parent: new Folder { Path = Path.GetDirectoryName((string)bvfFile)! },
            files: new List<FileSystemMetadata>
            {
                new()
                {
                    FullName = (string)bvfFile,
                    Name = Path.GetFileName((string)bvfFile),
                    IsDirectory = false,
                }
            },
            collectionType: CollectionType.movies,
            directoryService: null!);

        var movie = Assert.IsType<BvfMovie>(Assert.Single(result.Items));
        Assert.Equal("Branch Title", movie.Name);
        Assert.Equal(5000 * TimeSpan.TicksPerMillisecond, movie.RunTimeTicks);
        Assert.Equal("mp4", movie.Container);
        Assert.Equal(nameof(Movie), movie.GetClientTypeName());

        var sources = movie.GetMediaSources(enablePathSubstitution: false);
        Assert.All(sources, source => Assert.Equal(MediaSourceType.Placeholder, source.Type));
    }

    [Fact]
    public async Task GetMediaSources_ForPrimaryBvfItem_ReturnsSmartBranchSources()
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
              "segments": []
            }
            """;

        using var bvfFile = CreateTempBvf(manifestJson, totalDurationMs: 4000, ("seg-001", "AAAA"));
        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, new TestApplicationPaths());
        var item = new BvfVideo
        {
            Path = (string)bvfFile,
            RunTimeTicks = 4000 * TimeSpan.TicksPerMillisecond,
        };

        var sources = (await server.GetMediaSources(item, CancellationToken.None)).ToList();

        Assert.Equal(2, sources.Count);
        Assert.Contains(sources, source => source.Name == "Smart Branch (adult)");
        Assert.Contains(sources, source => source.Name == "Smart Branch (child)");
        Assert.All(sources, source => Assert.Equal((string)bvfFile, source.Path));
    }

    [Fact]
    public void FindBvfFile_AcceptsPrimaryBvfPath_AndLegacySibling()
    {
        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, new TestApplicationPaths());
        var dir = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        try
        {
            var bvf = Path.Combine(dir, "movie.bvf");
            var mp4 = Path.Combine(dir, "movie.mp4");
            File.WriteAllText(bvf, "x");
            File.WriteAllText(mp4, "y");

            Assert.Equal(bvf, server.FindBvfFile(bvf));
            Assert.Equal(bvf, server.FindBvfFile(mp4));
            Assert.Null(server.FindBvfFile(Path.Combine(dir, "other.mp4")));
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }

    private static SmartBranchingPlugin CreatePluginContext(PluginConfiguration? configuration = null)
    {
        var plugin = new SmartBranchingPlugin(new TestApplicationPaths(), new TestXmlSerializer());
        if (configuration != null)
            plugin.UpdateConfiguration(configuration);
        return plugin;
    }

    private static TempFile CreateTempBvf(
        string manifestJson,
        ulong totalDurationMs = 0,
        params (string SegmentId, string PayloadText)[] segments)
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
            writer.Write(totalDurationMs);
            writer.Write(0u);

            foreach (var (segmentId, _, dataOffset, dataLength) in assetBlocks)
            {
                var idBytes = Encoding.UTF8.GetBytes(segmentId);
                if (idBytes.Length > 16)
                    throw new InvalidOperationException("segment id too long");
                writer.Write(idBytes);
                writer.Write(new byte[16 - idBytes.Length]);
                writer.Write(dataOffset);
                writer.Write(dataLength);
                writer.Write(1000UL);
            }

            writer.Write(compressedManifest);
            foreach (var (_, block, _, _) in assetBlocks)
                writer.Write(block);
        }

        return new TempFile(path);
    }

    private static byte[] CreateAssetBlock(string segmentId, byte[] payload)
    {
        using var stream = new MemoryStream();
        using (var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true))
        {
            writer.Write(0x00415642);
            var idBytes = Encoding.UTF8.GetBytes(segmentId);
            writer.Write(idBytes);
            writer.Write(new byte[16 - idBytes.Length]);
            writer.Write(1u); // fmp4
            writer.Write(0u);
            writer.Write(0u);
            writer.Write(payload);
        }

        return stream.ToArray();
    }

    private sealed class TempFile : IDisposable
    {
        private readonly string _path;
        public TempFile(string path) => _path = path;
        public static implicit operator string(TempFile file) => file._path;
        public void Dispose()
        {
            if (File.Exists(_path))
                File.Delete(_path);
        }
    }

    private sealed class TestApplicationPaths : IApplicationPaths
    {
        public string ProgramDataPath { get; } = Path.GetTempPath();
        public string WebPath => ProgramDataPath;
        public string ProgramSystemPath => ProgramDataPath;
        public string DataPath => ProgramDataPath;
        public string ImageCachePath => ProgramDataPath;
        public string PluginsPath => ProgramDataPath;
        public string PluginConfigurationsPath => ProgramDataPath;
        public string LogDirectoryPath => ProgramDataPath;
        public string ConfigurationDirectoryPath => ProgramDataPath;
        public string SystemConfigurationFilePath => Path.Combine(ProgramDataPath, "system.xml");
        public string CachePath => ProgramDataPath;
        public string TempDirectory => ProgramDataPath;
        public string VirtualDataPath => ProgramDataPath;
    }

    private sealed class TestXmlSerializer : MediaBrowser.Model.Serialization.IXmlSerializer
    {
        public object DeserializeFromBytes(Type type, byte[] buffer) => Activator.CreateInstance(type)!;
        public object DeserializeFromFile(Type type, string file) => Activator.CreateInstance(type)!;
        public object DeserializeFromStream(Type type, Stream stream) => Activator.CreateInstance(type)!;
        public void SerializeToFile(object obj, string file) { }
        public void SerializeToStream(object obj, Stream stream) { }
        public byte[] SerializeToBytes(Type type, object obj) => Array.Empty<byte>();
    }
}
