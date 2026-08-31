using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Claims;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Jellyfin.Plugin.SmartBranching;
using Jellyfin.Plugin.SmartBranching.Configuration;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.LiveTv;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Serialization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;
using ZstdSharp;
using SmartBranchingPlugin = Jellyfin.Plugin.SmartBranching.Plugin;

namespace SmartBranching.Plugin.Tests;

[Collection(PluginStateCollection.Name)]
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
    public void OpenMediaSource_ProducesCachedProgressiveMp4_WhenFfmpegAvailable()
    {
        if (!FfmpegTestHelpers.IsAvailable())
            return;

        CreatePluginContext();

        var segOne = FfmpegTestHelpers.CreateFragmentedMp4(TimeSpan.FromMilliseconds(200));
        var segTwo = FfmpegTestHelpers.CreateFragmentedMp4(TimeSpan.FromMilliseconds(200));

        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 400,
              "profiles": {
                "child": { "filters": {} }
              },
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 0,
                  "end_ms": 200,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": { "child": { "action": "play", "segment_id": "seg-001" } }
                },
                {
                  "id": "seg-002",
                  "start_ms": 200,
                  "end_ms": 400,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": { "child": { "action": "play", "segment_id": "seg-002" } }
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvf(
            manifestJson,
            ("seg-001", segOne),
            ("seg-002", segTwo));

        var paths = new TestApplicationPaths();
        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, paths);
        var liveStream = OpenMediaSource(server, bvfFile, "child");
        var mediaSource = liveStream.MediaSource;

        Assert.Equal(MediaBrowser.Model.MediaInfo.MediaProtocol.File, mediaSource.Protocol);
        Assert.False(mediaSource.IsRemote);
        Assert.EndsWith(".mp4", mediaSource.Path, StringComparison.OrdinalIgnoreCase);
        Assert.True(File.Exists(mediaSource.Path));
        Assert.True(new FileInfo(mediaSource.Path).Length > 0);

        var secondOpen = OpenMediaSource(server, bvfFile, "child");
        Assert.Equal(mediaSource.Path, secondOpen.MediaSource.Path);
    }

    [Fact]
    public async Task GetMediaSources_ReturnsReadyCachedSource_AfterPlaybackCacheBuilt()
    {
        if (!FfmpegTestHelpers.IsAvailable())
            return;

        CreatePluginContext(BuildConfig(yearsAgo: 12, sex: "male", profileOverride: "child"));

        var manifestJson = """
            {
              "movie_id": "movie-123",
              "title": "Example",
              "duration_ms": 400,
              "profiles": {
                "child": { "filters": {} }
              },
              "segments": [
                {
                  "id": "seg-001",
                  "start_ms": 0,
                  "end_ms": 200,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": { "child": { "action": "play", "segment_id": "seg-001" } }
                },
                {
                  "id": "seg-002",
                  "start_ms": 200,
                  "end_ms": 400,
                  "tags": [],
                  "risk": "safe",
                  "is_filler": false,
                  "profiles": { "child": { "action": "play", "segment_id": "seg-002" } }
                }
              ]
            }
            """;

        using var bvfFile = CreateTempBvf(
            manifestJson,
            ("seg-001", FfmpegTestHelpers.CreateFragmentedMp4(TimeSpan.FromMilliseconds(200))),
            ("seg-002", FfmpegTestHelpers.CreateFragmentedMp4(TimeSpan.FromMilliseconds(200))));

        var paths = new TestApplicationPaths();
        var httpContextAccessor = CreateJellyfinHttpContextAccessor(TestUserId);
        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, paths, httpContextAccessor);
        var opened = OpenMediaSource(server, bvfFile, "child");

        var sourcesBefore = (await server.GetMediaSources(new Video { Path = bvfFile }, CancellationToken.None)).ToList();
        var ready = Assert.Single(sourcesBefore);
        Assert.False(ready.RequiresOpening);
        Assert.EndsWith(".mp4", ready.Path, StringComparison.OrdinalIgnoreCase);
        Assert.Equal(opened.MediaSource.Path, ready.Path);
        Assert.Equal(2000L * TimeSpan.TicksPerMillisecond, ready.RunTimeTicks);
    }

    [Fact]
    public async Task GetMediaSources_WithJellyfinUserIdClaim_ReturnsResolvedProfileOnly()
    {
        CreatePluginContext(BuildConfig(yearsAgo: 3, sex: "male", profileOverride: "adult"));

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

        using var bvfFile = CreateTempBvf(manifestJson, ("seg-001", "AAAA"));
        var httpContextAccessor = CreateJellyfinHttpContextAccessor(TestUserId);
        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, new TestApplicationPaths(), httpContextAccessor);
        var item = new Video { Path = bvfFile };

        var sources = (await server.GetMediaSources(item, CancellationToken.None)).ToList();

        var source = Assert.Single(sources);
        Assert.Equal("Smart Branch (auto: adult)", source.Name);
        Assert.Equal("adult", DecodeProfileFromToken(source.OpenToken));
    }

    [Fact]
    public async Task GetMediaSources_WithAuthenticatedUser_ReturnsResolvedProfileOnly()
    {
        CreatePluginContext(BuildConfig(yearsAgo: 12, sex: "male"));

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

        using var bvfFile = CreateTempBvf(manifestJson, ("seg-001", "AAAA"));
        var moviePath = Path.ChangeExtension((string)bvfFile, ".mp4");
        File.WriteAllText(moviePath, "placeholder");

        var httpContextAccessor = CreateAuthenticatedHttpContextAccessor(TestUserId, "kiddo");
        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, new TestApplicationPaths(), httpContextAccessor);
        var item = new Video { Path = moviePath };

        var sources = (await server.GetMediaSources(item, CancellationToken.None)).ToList();

        var source = Assert.Single(sources);
        Assert.Equal("Smart Branch (auto: child)", source.Name);
        Assert.Equal("child", DecodeProfileFromToken(source.OpenToken));
        Assert.True(Guid.TryParse(source.Id, out _));
        Assert.False(source.SupportsTranscoding);
    }

    [Fact]
    public async Task GetMediaSources_WithoutAuthenticatedUser_FallsBackToProfileList()
    {
        CreatePluginContext(BuildConfig(yearsAgo: 12, sex: "male"));

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

        using var bvfFile = CreateTempBvf(manifestJson, ("seg-001", "AAAA"));
        var moviePath = Path.ChangeExtension((string)bvfFile, ".mp4");
        File.WriteAllText(moviePath, "placeholder");

        var server = new SegmentServer(NullLogger<SegmentServer>.Instance, new TestApplicationPaths(), new HttpContextAccessor());
        var item = new Video { Path = moviePath };

        var sources = (await server.GetMediaSources(item, CancellationToken.None)).ToList();

        Assert.Equal(2, sources.Count);
        Assert.Contains(sources, source => source.Name == "Smart Branch (adult)");
        Assert.Contains(sources, source => source.Name == "Smart Branch (child)");
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

    private static SmartBranchingPlugin CreatePluginContext(PluginConfiguration? configuration = null)
    {
        var plugin = new SmartBranchingPlugin(new TestApplicationPaths(), new TestXmlSerializer());
        if (configuration != null)
        {
            plugin.UpdateConfiguration(configuration);
        }

        return plugin;
    }

    private static PluginConfiguration BuildConfig(int yearsAgo, string sex, string? profileOverride = null)
    {
        var userId = TestUserId.ToString();
        var config = new PluginConfiguration { DefaultProfile = "adult" };
        config.SetUserProfiles(new Dictionary<string, UserBranchProfile>
        {
            [userId] = new()
            {
                Birthday = DateOnly.FromDateTime(DateTime.UtcNow).AddYears(-yearsAgo).ToString("yyyy-MM-dd"),
                Sex = sex,
                ProfileOverride = profileOverride
            }
        });
        return config;
    }

    private static HttpContextAccessor CreateJellyfinHttpContextAccessor(Guid userId)
    {
        var claims = new[]
        {
            new Claim(SegmentServer.JellyfinUserIdClaimType, userId.ToString()),
        };
        var context = new DefaultHttpContext
        {
            User = new ClaimsPrincipal(new ClaimsIdentity(claims)),
        };

        return new HttpContextAccessor { HttpContext = context };
    }

    private static HttpContextAccessor CreateAuthenticatedHttpContextAccessor(Guid userId, string userName)
    {
        var claims = new[]
        {
            new Claim(ClaimTypes.NameIdentifier, userId.ToString()),
            new Claim(ClaimTypes.Name, userName),
        };
        var identity = new ClaimsIdentity(claims, authenticationType: "TestAuth");
        var context = new DefaultHttpContext
        {
            User = new ClaimsPrincipal(identity)
        };

        return new HttpContextAccessor { HttpContext = context };
    }

    private static string DecodeProfileFromToken(string token)
    {
        var parts = token.Split(':');
        return Base64UrlDecode(parts[2]);
    }

    private static string Base64UrlDecode(string value)
    {
        var padded = value.Replace('-', '+').Replace('_', '/');
        padded = padded.PadRight(padded.Length + ((4 - padded.Length % 4) % 4), '=');
        return Encoding.UTF8.GetString(Convert.FromBase64String(padded));
    }

    private static readonly Guid TestUserId = Guid.Parse("11111111-1111-1111-1111-111111111111");

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
        => CreateTempBvf(
            manifestJson,
            segments.Select(segment => (segment.SegmentId, Encoding.UTF8.GetBytes(segment.PayloadText))).ToArray());

    private static TempFile CreateTempBvf(string manifestJson, params (string SegmentId, byte[] Payload)[] segments)
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
        foreach (var (segmentId, payload) in segments)
        {
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
