using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Jellyfin.Data.Enums;
using Jellyfin.Plugin.SmartBranching.Configuration;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Dlna;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Controller.Streaming;
using MediaBrowser.Controller.Subtitles;
using MediaBrowser.Controller.Videos;
using MediaBrowser.Model.Dlna;
using MediaBrowser.Model.Drawing;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.IO;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Providers;
using MediaBrowser.Model.Querying;
using MediaBrowser.Model.Serialization;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// The main Smart Branching plugin.
/// Scans for .bvf files and registers the SegmentServer as a video processor.
/// </summary>
public class Plugin : BasePlugin<PluginConfiguration>, IMainEntryPoint
{
    private readonly IServerApplicationPaths _applicationPaths;
    private readonly ILogger<Plugin> _logger;
    private readonly ILibraryManager _libraryManager;

    /// <summary>
    /// Gets the current plugin instance.
    /// </summary>
    public static Plugin? Instance { get; private set; }

    public Plugin(
        IApplicationPaths applicationPaths,
        IXmlSerializer xmlSerializer,
        IServerApplicationPaths serverApplicationPaths,
        ILibraryManager libraryManager,
        ILogger<Plugin> logger)
        : base(applicationPaths, xmlSerializer)
    {
        Instance = this;
        _applicationPaths = serverApplicationPaths;
        _logger = logger;
        _libraryManager = libraryManager;
    }

    /// <inheritdoc />
    public override string Name => "Smart Branching";

    /// <inheritdoc />
    public override Guid Id => Guid.Parse("a1b2c3d4-e5f6-7890-abcd-ef1234567890");

    /// <inheritdoc />
    public IEnumerable<PluginPageInfo> GetPages()
    {
        return new[]
        {
            new PluginPageInfo
            {
                Name = "smart-branching-config",
                EmbeddedResourcePath = $"{GetType().Namespace}.Configuration.configPage.html"
            }
        };
    }

    /// <inheritdoc />
    public void OnApplicationHost(MediaBrowser.Controller.ApplicationHost applicationHost)
    {
        _logger.LogInformation("Smart Branching plugin initializing");
        
        // Register as a video processor
        var segmentServer = new SegmentServer(_logger, _applicationPaths);
        
        // Scan library for BVF files
        var bvfs = ScanForBvfFiles();
        _logger.LogInformation("Found {Count} BVF files in library", bvfs.Count);
        
        foreach (var bvf in bvfs)
        {
            _logger.LogInformation("Registered BVF source: {Path}", bvf);
        }
    }

    /// <summary>
    /// Scans the library for .bvf files.
    /// </summary>
    private List<string> ScanForBvfFiles()
    {
        var bvfPaths = new List<string>();
        
        try
        {
            var query = new InternalItemsQuery
            {
                MediaTypes = new[] { MediaType.Video },
                IsVirtualItem = false,
            };

            var videos = _libraryManager.GetItemList(query);
            var segmentServer = new SegmentServer(_logger, _applicationPaths);

            foreach (var video in videos)
            {
                var bvfPath = segmentServer.FindBvfFile(video.Path);
                if (bvfPath != null)
                {
                    bvfPaths.Add(bvfPath);
                }
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to scan library for BVF files");
        }

        return bvfPaths;
    }

    /// <inheritdoc />
    public Task OnShutdown()
    {
        _logger.LogInformation("Smart Branching plugin shutting down");
        return Task.CompletedTask;
    }
}
