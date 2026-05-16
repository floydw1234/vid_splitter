using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Entities;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Scans the Jellyfin library for _branch.json manifests.
/// </summary>
public static class ManifestScanner
{
    /// <summary>
    /// Scans all video items in the library for corresponding _branch.json files.
    /// </summary>
    public static List<BranchManifest> FindAllManifests(ILibraryManager libraryManager, ILogger logger)
    {
        var manifests = new List<BranchManifest>();
        var videoPaths = GetVideoPaths(libraryManager);

        foreach (var videoPath in videoPaths)
        {
            var manifestPath = GetManifestPath(videoPath);
            if (manifestPath != null && File.Exists(manifestPath))
            {
                try
                {
                    var manifest = ManifestReader.Load(manifestPath);
                    manifests.Add(manifest);
                }
                catch (Exception ex)
                {
                    logger.LogError(ex, "Failed to load manifest for {VideoPath}", videoPath);
                }
            }
        }

        return manifests;
    }

    private static List<string> GetVideoPaths(ILibraryManager libraryManager)
    {
        var query = new InternalItemsQuery
        {
            MediaTypes = new[] { MediaType.Video },
            IsVirtualItem = false,
        };

        return libraryManager.GetItemList(query)
            .Select(i => i.Path)
            .Where(p => !string.IsNullOrEmpty(p))
            .ToList();
    }

    /// <summary>
    /// Given a video path, returns the expected _branch.json path.
    /// e.g., /srv/media/movie.mp4 → /srv/media/movie_branch.json
    /// </summary>
    public static string? GetManifestPath(string videoPath)
    {
        var dir = Path.GetDirectoryName(videoPath);
        var stem = Path.GetFileNameWithoutExtension(videoPath);
        return dir != null ? Path.Combine(dir, stem + "_branch.json") : null;
    }
}
