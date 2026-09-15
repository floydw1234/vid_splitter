using System;
using System.Collections.Generic;
using System.Linq;
using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.SmartBranching.Configuration;

/// <summary>
/// Per-user profile data stored by the plugin.
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

    /// <summary>
    /// Gets or sets topic labels this user wants avoided (nudity, religion_christianity, …).
    /// Playback uses this list against each segment's tags/topics. The analyzer only
    /// needs the union of these names, not named BVF profiles.
    /// </summary>
    [System.Xml.Serialization.XmlArray("Topics")]
    [System.Xml.Serialization.XmlArrayItem("Topic")]
    public List<string> Topics { get; set; } = new();

    /// <summary>
    /// Gets or sets what to do when an avoided topic hits: "skip" or "swap".
    /// Empty means use <see cref="PluginConfiguration.DefaultAction"/>.
    /// Swap still requires a replacement clip in the BVF; otherwise playback skips.
    /// </summary>
    public string HitAction { get; set; } = string.Empty;
}

/// <summary>
/// Serializable key-value pair for user profiles.
/// Jellyfin's XmlSerializer cannot serialize IDictionary members, so profiles are
/// stored as a list of entries instead of a dictionary.
/// </summary>
public class UserProfileEntry
{
    /// <summary>
    /// Gets or sets the Jellyfin user ID (Guid string).
    /// </summary>
    public string UserId { get; set; } = string.Empty;

    /// <summary>
    /// Gets or sets the stored branch profile for the user.
    /// </summary>
    public UserBranchProfile Profile { get; set; } = new();
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
        NsfwThreshold = 0.75f;
        DefaultAction = "swap";
        UserProfileEntries = new List<UserProfileEntry>();
        CustomTopics = new List<string>();
    }

    /// <summary>
    /// Gets or sets per-user profile data as a serializable list.
    /// </summary>
    public List<UserProfileEntry> UserProfileEntries { get; set; }

    /// <summary>
    /// Gets or sets a value indicating whether smart branching is enabled.
    /// </summary>
    public bool Enabled { get; set; }

    /// <summary>
    /// Gets or sets the default profile for users with no stored entry.
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
    /// Gets or sets the default hit action ("skip" or "swap") for users with no
    /// per-user <see cref="UserBranchProfile.HitAction"/>. Swap still requires a
    /// filler clip in the BVF; otherwise playback skips.
    /// </summary>
    public string DefaultAction { get; set; }

    /// <summary>
    /// Extra topic labels shown in the config grid that are not in the built-in taxonomy.
    /// </summary>
    [System.Xml.Serialization.XmlArray("CustomTopics")]
    [System.Xml.Serialization.XmlArrayItem("Topic")]
    public List<string> CustomTopics { get; set; } = new();

    /// <summary>
    /// Looks up the stored branch profile for a Jellyfin user by user ID.
    /// </summary>
    public bool TryGetUserProfile(string userId, out UserBranchProfile profile)
    {
        var entries = UserProfileEntries;
        if (entries != null)
        {
            foreach (var entry in entries)
            {
                if (!string.IsNullOrEmpty(entry.UserId) &&
                    UserIdsMatch(entry.UserId, userId))
                {
                    profile = entry.Profile ?? new UserBranchProfile();
                    return true;
                }
            }
        }

        profile = new UserBranchProfile();
        return false;
    }

    private static bool UserIdsMatch(string left, string right)
    {
        if (string.Equals(left, right, StringComparison.OrdinalIgnoreCase))
            return true;

        return Guid.TryParse(left, out var leftGuid) &&
               Guid.TryParse(right, out var rightGuid) &&
               leftGuid == rightGuid;
    }

    /// <summary>
    /// Replaces the stored user profiles from a dictionary keyed by user ID.
    /// </summary>
    public void SetUserProfiles(Dictionary<string, UserBranchProfile> profiles)
    {
        var entries = new List<UserProfileEntry>();
        if (profiles != null)
        {
            foreach (var kvp in profiles)
            {
                entries.Add(new UserProfileEntry
                {
                    UserId = kvp.Key,
                    Profile = kvp.Value
                });
            }
        }

        UserProfileEntries = entries;
    }

    /// <summary>
    /// Union of avoided topics across every stored user profile.
    /// Authoring tools use this as the scan/replace list; per-user filtering stays here.
    /// </summary>
    public List<string> CollectAvoidedTopics()
    {
        var topics = new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
        var entries = UserProfileEntries;
        if (entries == null)
            return new List<string>();

        foreach (var entry in entries)
        {
            var stored = entry?.Profile?.Topics;
            if (stored == null)
                continue;
            foreach (var topic in stored)
            {
                if (!string.IsNullOrWhiteSpace(topic))
                    topics.Add(topic.Trim());
            }
        }

        return topics.ToList();
    }
}
