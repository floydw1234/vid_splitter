using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using Jellyfin.Plugin.SmartBranching;
using MediaBrowser.Common.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;
using ZstdSharp;

namespace Jellyfin.Plugin.SmartBranching.Tests;

public class SegmentServerCacheTests
{
    [Fact]
    public void GetManifestRaw_CachesManifestOnFirstRead()
    {
        using var bvfFile = CreateTempBvf(
            movieId: "movie-1",
            title: "Original",
            profileName: "adult");
        var server = CreateServer();

        var first = server.GetManifestRaw(bvfFile);
        var second = server.GetManifestRaw(bvfFile);

        Assert.Same(first, second);
        Assert.Equal("movie-1", second.MovieId);
    }

    [Fact]
    public void GetManifestRaw_RewrittenBvfFile_InvalidatesCacheAndReloadsManifest()
    {
        using var bvfFile = CreateTempBvf(
            movieId: "movie-1",
            title: "Original",
            profileName: "adult");
        var server = CreateServer();

        var first = server.GetManifestRaw(bvfFile);
        RewriteManifest(
            bvfFile,
            movieId: "movie-2",
            title: "Updated",
            profileName: "child");

        var second = server.GetManifestRaw(bvfFile);

        Assert.NotSame(first, second);
        Assert.Equal("movie-2", second.MovieId);
        Assert.Contains("child", second.Profiles.Keys);
    }

    [Fact]
    public void ClearCache_RemovesCachedEntries()
    {
        using var bvfFile = CreateTempBvf(
            movieId: "movie-1",
            title: "Original",
            profileName: "adult");
        var server = CreateServer();

        var first = server.GetManifestRaw(bvfFile);
        server.ClearCache();
        var second = server.GetManifestRaw(bvfFile);

        Assert.NotSame(first, second);
        Assert.Equal("movie-1", second.MovieId);
    }

    private static SegmentServer CreateServer()
    {
        return new SegmentServer(
            NullLogger<SegmentServer>.Instance,
            new TestApplicationPaths());
    }

    private static TempFile CreateTempBvf(string movieId, string title, string profileName)
    {
        var path = Path.Combine(Path.GetTempPath(), $"{Guid.NewGuid():N}.bvf");
        WriteBvf(path, BuildManifestJson(movieId, title, profileName));
        return new TempFile(path);
    }

    private static void RewriteManifest(string path, string movieId, string title, string profileName)
    {
        WriteBvf(path, BuildManifestJson(movieId, title, profileName));
    }

    private static void WriteBvf(string path, string manifestJson)
    {
        var manifestBytes = Encoding.UTF8.GetBytes(manifestJson);
        byte[] compressedManifest;
        using (var compressor = new Compressor())
        {
            compressedManifest = compressor.Wrap(manifestBytes).ToArray();
        }

        using var stream = File.Create(path);
        using var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: false);

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

    private static string BuildManifestJson(string movieId, string title, string profileName)
    {
        return $$"""
            {
              "movie_id": "{{movieId}}",
              "title": "{{title}}",
              "duration_ms": 120000,
              "profiles": {
                "{{profileName}}": { "filters": {} }
              },
              "segments": []
            }
            """;
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
                File.Delete(_path);
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
}
