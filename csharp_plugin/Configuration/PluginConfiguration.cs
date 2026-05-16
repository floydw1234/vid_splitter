using System;
using System.Collections.Generic;
using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.SmartBranching.Configuration;

/// <summary>
/// Per-user profile data stored by the plugin.
/// Keyed by Jellyfin user ID (Guid string) in <see cref="PluginConfiguration.UserProfiles"/>.
/// </summary>
public class UserBranchProfile
{
    /// <summary>
    /// Gets or sets the user's date of birth in ISO format (yyyy-MM-dd), or null if not set.
    /// Used to auto-resolve the BVF profile based on age.
    /// </summary>
    public string? Birthday { get; set; }

    /// <summary>
    /// Gets or sets the user's sex: "male", "female", or "unset".
    /// Used together with age to pick between teen_m / teen_f profiles.
    /// </summary>
    public string Sex { get; set; } = "unset";

    /// <summary>
    /// Gets or sets an explicit profile override ("child", "teen_m", "teen_f", "adult").
    /// When set, Birthday and Sex are ignored and this value is used directly.
    /// </summary>
    public string? ProfileOverride { get; set; }
}

/// <summary>
/// Plugin configuration options.
/// </summary>
public class PluginConfiguration : BasePluginConfiguration
{
    public PluginConfiguration()
    {
        Enabled = true;
        DefaultProfile = "adult";
        FillerDirectory = "smart_branching/filler";
        NsfwThreshold = 0.6f;
        DefaultAction = "swap";
        UserProfiles = new Dictionary<string, UserBranchProfile>();
    }

    /// <summary>
    /// Gets or sets a value indicating whether smart branching is enabled.
    /// </summary>
    public bool Enabled { get; set; }

    /// <summary>
    /// Gets or sets the default profile for users with no entry in <see cref="UserProfiles"/>.
    /// </summary>
    public string DefaultProfile { get; set; }

    /// <summary>
    /// Gets or sets the filler video directory (relative to Jellyfin DataPath).
    /// </summary>
    public string FillerDirectory { get; set; }

    /// <summary>
    /// Gets or sets the NSFW confidence threshold (0.0–1.0).
    /// </summary>
    public float NsfwThreshold { get; set; }

    /// <summary>
    /// Gets or sets the default action for mature content when no swap option is defined.
    /// </summary>
    public string DefaultAction { get; set; }

    /// <summary>
    /// Gets or sets per-user profile data. Key is the Jellyfin user ID as a string.
    /// </summary>
    public Dictionary<string, UserBranchProfile> UserProfiles { get; set; }
}
