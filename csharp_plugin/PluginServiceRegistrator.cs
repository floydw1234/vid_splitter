using MediaBrowser.Controller;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Plugins;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Registers Smart Branching services with Jellyfin.
/// </summary>
public class PluginServiceRegistrator : IPluginServiceRegistrator
{
    /// <inheritdoc />
    public void RegisterServices(IServiceCollection serviceCollection, IServerApplicationHost applicationHost)
    {
        serviceCollection.AddHttpContextAccessor();
        // Register the concrete type so BvfHlsController can resolve the same instance.
        serviceCollection.AddSingleton<SegmentServer>();
        serviceCollection.AddSingleton<IMediaSourceProvider>(provider => provider.GetRequiredService<SegmentServer>());
    }
}
