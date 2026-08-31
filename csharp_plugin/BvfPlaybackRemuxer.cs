using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Jellyfin.Plugin.SmartBranching.Models;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Builds profile-resolved progressive MP4 files via ffmpeg stream-copy concat.
/// </summary>
internal sealed class BvfPlaybackRemuxer
{
    private static readonly ConcurrentDictionary<string, SemaphoreSlim> BuildLocks = new(StringComparer.OrdinalIgnoreCase);

    private readonly ILogger _logger;
    private readonly string _cacheRoot;
    private readonly string _ffmpegPath;

    public BvfPlaybackRemuxer(ILogger logger, string cacheRoot, string? ffmpegPath = null)
    {
        _logger = logger;
        _cacheRoot = cacheRoot;
        _ffmpegPath = ffmpegPath ?? ResolveFfmpegPath();
        Directory.CreateDirectory(_cacheRoot);
    }

    /// <summary>
    /// Returns a cached progressive MP4 for the resolved profile, building it on first use.
    /// </summary>
    public async Task<string> GetOrCreatePlaybackFileAsync(
        string bvfPath,
        string profileKey,
        IReadOnlyList<ResolvedSegment> segments,
        CancellationToken cancellationToken)
    {
        if (segments.Count == 0)
            throw new InvalidOperationException("No playable segments found.");

        var cacheKey = ComputeCacheKey(bvfPath, profileKey, segments);
        var outputPath = Path.Combine(_cacheRoot, cacheKey + ".mp4");

        if (IsValidCacheFile(outputPath, bvfPath))
        {
            _logger.LogDebug("BVF playback cache hit for {BvfPath} profile {Profile}", bvfPath, profileKey);
            return outputPath;
        }

        var buildLock = BuildLocks.GetOrAdd(cacheKey, _ => new SemaphoreSlim(1, 1));
        await buildLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            if (IsValidCacheFile(outputPath, bvfPath))
                return outputPath;

            _logger.LogInformation(
                "Building BVF playback cache for {BvfPath} profile {Profile} ({SegmentCount} segments)",
                bvfPath,
                profileKey,
                segments.Count);

            await BuildPlaybackFileAsync(bvfPath, segments, outputPath, cancellationToken).ConfigureAwait(false);
            return outputPath;
        }
        finally
        {
            buildLock.Release();
        }
    }

    public void ClearCache()
    {
        if (!Directory.Exists(_cacheRoot))
            return;

        foreach (var file in Directory.EnumerateFiles(_cacheRoot, "*.mp4", SearchOption.TopDirectoryOnly))
        {
            try
            {
                File.Delete(file);
            }
            catch (Exception ex)
            {
                _logger.LogDebug(ex, "Unable to delete cached playback file {Path}", file);
            }
        }
    }

    /// <summary>
    /// Returns an existing cached playback file path, or <c>null</c> when none is ready.
    /// </summary>
    public string? TryGetCachedPlaybackPath(
        string bvfPath,
        string profileKey,
        IReadOnlyList<ResolvedSegment> segments)
    {
        var cacheKey = ComputeCacheKey(bvfPath, profileKey, segments);
        var outputPath = Path.Combine(_cacheRoot, cacheKey + ".mp4");
        return IsValidCacheFile(outputPath, bvfPath) ? outputPath : null;
    }

    internal static string ComputeCacheKey(
        string bvfPath,
        string profileKey,
        IReadOnlyList<ResolvedSegment> segments)
    {
        var bvfInfo = new FileInfo(bvfPath);
        var segmentKey = string.Join(
            ',',
            segments.Select(segment => $"{segment.SegmentId}:{segment.DataOffset}:{segment.DataLength}"));

        var payload = $"{bvfPath}|{bvfInfo.Length}|{bvfInfo.LastWriteTimeUtc.Ticks}|{profileKey}|{segmentKey}";
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(payload));
        return Convert.ToHexString(hash).ToLowerInvariant();
    }

    internal static bool IsValidCacheFile(string outputPath, string bvfPath)
    {
        if (!File.Exists(outputPath))
            return false;

        var outputInfo = new FileInfo(outputPath);
        if (outputInfo.Length <= 0)
            return false;

        var bvfInfo = new FileInfo(bvfPath);
        return outputInfo.LastWriteTimeUtc >= bvfInfo.LastWriteTimeUtc;
    }

    private async Task BuildPlaybackFileAsync(
        string bvfPath,
        IReadOnlyList<ResolvedSegment> segments,
        string outputPath,
        CancellationToken cancellationToken)
    {
        var tempRoot = Path.Combine(_cacheRoot, "build-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tempRoot);

        var partialPath = Path.Combine(tempRoot, "output.mp4");

        try
        {
            var segmentPaths = BvfSegmentExtractor.ExtractSegments(bvfPath, segments, tempRoot);
            var concatListPath = Path.Combine(tempRoot, "concat.txt");
            WriteConcatList(concatListPath, segmentPaths);

            if (File.Exists(partialPath))
                File.Delete(partialPath);

            await RunFfmpegConcatAsync(concatListPath, partialPath, cancellationToken).ConfigureAwait(false);

            if (!File.Exists(partialPath) || new FileInfo(partialPath).Length <= 0)
                throw new InvalidOperationException("ffmpeg did not produce a playback cache file.");

            if (File.Exists(outputPath))
                File.Delete(outputPath);

            File.Move(partialPath, outputPath);
        }
        finally
        {
            TryDeleteDirectory(tempRoot);
            if (File.Exists(partialPath))
            {
                try
                {
                    File.Delete(partialPath);
                }
                catch (Exception ex)
                {
                    _logger.LogDebug(ex, "Unable to delete partial playback cache {Path}", partialPath);
                }
            }
        }
    }

    private async Task RunFfmpegConcatAsync(
        string concatListPath,
        string outputPath,
        CancellationToken cancellationToken)
    {
        var args = new List<string>
        {
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concatListPath,
            "-c", "copy",
            "-movflags", "+faststart",
            outputPath,
        };

        var stderr = new StringBuilder();
        using var process = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = _ffmpegPath,
                RedirectStandardError = true,
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            },
        };

        foreach (var arg in args)
            process.StartInfo.ArgumentList.Add(arg);

        if (!process.Start())
            throw new InvalidOperationException($"Unable to start ffmpeg at '{_ffmpegPath}'.");

        var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);
        await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        stderr.Append(await stderrTask.ConfigureAwait(false));

        if (process.ExitCode != 0)
        {
            throw new InvalidOperationException(
                $"ffmpeg concat failed with exit code {process.ExitCode}: {stderr}");
        }
    }

    private static void WriteConcatList(string concatListPath, IReadOnlyList<string> segmentPaths)
    {
        var builder = new StringBuilder(segmentPaths.Count * 64);
        foreach (var segmentPath in segmentPaths)
        {
            var escaped = segmentPath.Replace("'", "'\\''", StringComparison.Ordinal);
            builder.Append("file '").Append(escaped).Append('\'').AppendLine();
        }

        File.WriteAllText(concatListPath, builder.ToString(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
    }

    private static string ResolveFfmpegPath()
    {
        var fromEnv = Environment.GetEnvironmentVariable("JELLYFIN_FFMPEG");
        if (!string.IsNullOrWhiteSpace(fromEnv) && File.Exists(fromEnv))
            return fromEnv;

        const string jellyfinBundled = "/usr/lib/jellyfin-ffmpeg/ffmpeg";
        if (File.Exists(jellyfinBundled))
            return jellyfinBundled;

        return "ffmpeg";
    }

    private static void TryDeleteDirectory(string directory)
    {
        try
        {
            if (Directory.Exists(directory))
                Directory.Delete(directory, recursive: true);
        }
        catch
        {
            // Best-effort temp cleanup.
        }
    }
}
