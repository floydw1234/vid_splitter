using System;
using System.Collections.Generic;
using System.IO;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Caches parsed BVF manifests and invalidates entries when the source file changes.
/// </summary>
public sealed class BvfManifestCache
{
    private readonly Dictionary<string, CacheEntry> _entries = new(StringComparer.Ordinal);

    public BranchManifest GetOrLoad(string bvfPath, Func<string, BranchManifest> loader)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(bvfPath);
        ArgumentNullException.ThrowIfNull(loader);

        var fileInfo = new FileInfo(bvfPath);
        if (!fileInfo.Exists)
            throw new FileNotFoundException($"BVF file not found: {bvfPath}", bvfPath);

        var cacheKey = fileInfo.FullName;
        var lastWriteTimeUtc = fileInfo.LastWriteTimeUtc;
        var length = fileInfo.Length;

        if (_entries.TryGetValue(cacheKey, out var cached) &&
            cached.LastWriteTimeUtc == lastWriteTimeUtc &&
            cached.Length == length)
        {
            return cached.Manifest;
        }

        var manifest = loader(fileInfo.FullName);
        _entries[cacheKey] = new CacheEntry(lastWriteTimeUtc, length, manifest);
        return manifest;
    }

    public void Clear()
    {
        _entries.Clear();
    }

    private sealed record CacheEntry(
        DateTime LastWriteTimeUtc,
        long Length,
        BranchManifest Manifest);
}
