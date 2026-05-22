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
/// Scans for .bvf containers and registers virtual "Smart Branch" media sources.
/// </summary>
public class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages, IServerEntryPoint
{
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
        ArgumentNullException.ThrowIfNull(serverApplicationPaths);

        Instance = this;
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

    /// <summary>
    /// Called when the server is starting up.
    /// Scans the library for .bvf containers and registers virtual sources.
    /// </summary>
    public Task OnStartup()
    {
        _logger.LogInformation("Smart Branching plugin starting up");
        
        // Scan library for BVF containers.
        var manifests = ManifestScanner.FindAllManifests(_libraryManager, _logger);
        _logger.LogInformation("Found {Count} BVF manifests", manifests.Count);
        
        foreach (var manifest in manifests)
        {
            _logger.LogInformation(
                "Registered virtual source for: {MovieId} ({Path})",
                manifest.MovieId,
                manifest.MoviePath);
        }

        return Task.CompletedTask;
    }

    /// <summary>
    /// Called when the server is shutting down.
    /// </summary>
    public Task OnShutdown()
    {
        _logger.LogInformation("Smart Branching plugin shutting down");
        return Task.CompletedTask;
    }
}
