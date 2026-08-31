using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Emby.Naming.Common;
using Jellyfin.Data.Enums;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Controller.Resolvers;
using MediaBrowser.Model.IO;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Resolves first-class <c>.bvf</c> files into library video items.
/// </summary>
public sealed class BvfItemResolver : IItemResolver, IMultiItemResolver
{
    private readonly ILogger<BvfItemResolver> _logger;

    public BvfItemResolver(ILogger<BvfItemResolver> logger, NamingOptions namingOptions)
    {
        _logger = logger;
        BvfFormatRegistration.EnsureRegistered(namingOptions);
    }

    /// <inheritdoc />
    public ResolverPriority Priority => ResolverPriority.Plugin;

    /// <inheritdoc />
    public BaseItem? ResolvePath(ItemResolveArgs args)
    {
        if (args.IsDirectory || !BvfFormatRegistration.IsBvfPath(args.Path))
            return null;

        if (!BVFReader.LooksLikeBvf(args.Path))
            return null;

        return CreateItem(args.Path, args.GetCollectionType());
    }

    /// <inheritdoc />
    public MultiItemResolverResult ResolveMultiple(
        Folder parent,
        List<FileSystemMetadata> files,
        CollectionType? collectionType,
        IDirectoryService directoryService)
    {
        var bvfFiles = files
            .Where(file => !file.IsDirectory && BvfFormatRegistration.IsBvfPath(file.FullName))
            .ToList();

        if (bvfFiles.Count == 0)
            return null!;

        var items = new List<BaseItem>();
        var claimed = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        foreach (var file in bvfFiles)
        {
            if (!BVFReader.LooksLikeBvf(file.FullName))
            {
                _logger.LogDebug("Skipping non-BVF file with .bvf extension: {Path}", file.FullName);
                continue;
            }

            var item = CreateItem(file.FullName, collectionType);
            items.Add(item);
            claimed.Add(file.FullName);
        }

        if (items.Count == 0)
            return null!;

        return new MultiItemResolverResult
        {
            Items = items,
            ExtraFiles = files.Where(file => !claimed.Contains(file.FullName)).ToList(),
        };
    }

    private Video CreateItem(string path, CollectionType? collectionType)
    {
        Video item = collectionType == CollectionType.movies
            ? new BvfMovie()
            : new BvfVideo();

        item.Path = path;
        item.Container = "mp4";
        item.Name = Path.GetFileNameWithoutExtension(path);

        try
        {
            var info = BVFReader.ReadHeader(path);
            if (!string.IsNullOrWhiteSpace(info.title))
                item.Name = info.title;

            if (info.totalDurationMs > 0)
                item.RunTimeTicks = checked((long)info.totalDurationMs * TimeSpan.TicksPerMillisecond);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to read BVF header metadata for {Path}", path);
        }

        // Prevent Jellyfin from treating the raw .bvf bytes as a DirectPlay/transcode input.
        // Empty ShortcutPath makes the default media source a Placeholder that is filtered out.
        item.IsShortcut = true;
        item.ShortcutPath = string.Empty;

        return item;
    }
}
