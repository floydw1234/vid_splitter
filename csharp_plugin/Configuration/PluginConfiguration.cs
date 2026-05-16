using System;
using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.SmartBranching.Configuration;

/// <summary>
/// Plugin configuration options.
/// </summary>
public class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>
    /// Initializes a new instance of the <see cref="PluginConfiguration"/> class.
    /// </summary>
    public PluginConfiguration()
    {
        // Default: enable smart branching for all profiles
        Enabled = true;
        
        // Default profile mapping (can be overridden per user)
        DefaultProfile = "adult";
        
        // Filler video directory relative to Jellyfin data path
        FillerDirectory = "smart_branching/filler";
        
        // Minimum NSFW confidence threshold (0.0 to 1.0)
        NsfwThreshold = 0.6f;
        
        // Whether to skip segments or swap them
        DefaultAction = "swap";
    }

    /// <summary>
    /// Gets or sets a value indicating whether smart branching is enabled.
    /// </summary>
    public bool Enabled { get; set; }

    /// <summary>
    /// Gets or sets the default profile to use when user profile is unknown.
    /// </summary>
    public string DefaultProfile { get; set; }

    /// <summary>
    /// Gets or sets the filler video directory.
    /// </summary>
    public string FillerDirectory { get; set; }

    /// <summary>
    /// Gets or sets the NSFW confidence threshold.
    /// </summary>
    public float NsfwThreshold { get; set; }

    /// <summary>
    /// Gets or sets the default action for mature content.
    /// </summary>
    public string DefaultAction { get; set; }
}
