using System;
using System.IO;
using System.Text.Json;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Reads and parses smart_branch.json manifest files.
/// </summary>
public static class ManifestReader
{
    private static readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
    };

    /// <summary>
    /// Loads a branch manifest from a file path.
    /// </summary>
    public static BranchManifest Load(string manifestPath)
    {
        if (!File.Exists(manifestPath))
            throw new FileNotFoundException($"Manifest not found: {manifestPath}");

        var json = File.ReadAllText(manifestPath);
        var manifest = JsonSerializer.Deserialize<BranchManifest>(json, _jsonOptions);

        if (manifest == null)
            throw new InvalidDataException($"Failed to parse manifest: {manifestPath}");

        // Validate required fields
        if (string.IsNullOrEmpty(manifest.MovieId))
            throw new InvalidDataException("Manifest missing required field: movie_id");

        if (manifest.Segments.Count == 0)
            throw new InvalidDataException("Manifest has no segments");

        return manifest;
    }

    /// <summary>
    /// Finds the manifest file for a given movie path.
    /// </summary>
    public static string? FindForMovie(string moviePath)
    {
        var dir = Path.GetDirectoryName(moviePath);
        if (dir == null)
            return null;

        var stem = Path.GetFileNameWithoutExtension(moviePath);
        var manifestPath = Path.Combine(dir, stem + "_branch.json");

        return File.Exists(manifestPath) ? manifestPath : null;
    }
}
