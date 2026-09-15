using System;
using System.Collections.Generic;
using System.Linq;
using Jellyfin.Plugin.SmartBranching.Configuration;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Model.Dto;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Maps Jellyfin users to branch profiles and resolves segment actions.
/// Profile data (birthday, sex) is read from the plugin's stored configuration.
/// </summary>
public class ProfileResolver
{
    /// <summary>
    /// Maps a Jellyfin user to a branch profile key.
    /// 
    /// Resolution order:
    ///   1. Explicit ProfileOverride stored in plugin config for this user
    ///   2. Auto-resolved from stored Birthday + Sex
    ///   3. Unfiltered adult path
    /// </summary>
    public string ResolveProfile(UserDto user, BranchManifest manifest)
    {
        var config = Plugin.Instance?.Configuration;
        var userId = user.Id.ToString();

        if (config != null &&
            config.TryGetUserProfile(userId, out var stored))
        {
            // 1. Explicit override wins
            if (!string.IsNullOrEmpty(stored.ProfileOverride))
                return SelectAvailableProfile(manifest.Profiles, stored.ProfileOverride);

            // 2. Auto-resolve from birthday + sex
            if (!string.IsNullOrEmpty(stored.Birthday) &&
                DateOnly.TryParse(stored.Birthday, out var dob))
            {
                var age = CalculateAge(dob);
                return SelectAvailableProfile(manifest.Profiles, ResolveFromAgeSex(age, stored.Sex ?? "unset"));
            }
        }

        // 3. Fall back to the plugin's default profile
        return SelectAvailableProfile(manifest.Profiles, "adult");
    }

    /// <summary>
    /// Topics this Jellyfin user wants avoided. Empty means fall back to baked BVF profiles.
    /// </summary>
    public IReadOnlyList<string> GetAvoidedTopics(UserDto user)
    {
        var config = Plugin.Instance?.Configuration;
        if (config == null || user == null)
            return Array.Empty<string>();

        if (!config.TryGetUserProfile(user.Id.ToString(), out var stored) || stored.Topics == null)
            return Array.Empty<string>();

        var topics = TopicPlayback.NormalizeTopics(stored.Topics);
        if (topics.Count == 0)
            return Array.Empty<string>();

        var ordered = new List<string>(topics);
        ordered.Sort(StringComparer.OrdinalIgnoreCase);
        return ordered;
    }

    /// <summary>
    /// Skip vs swap when an avoided label hits. Falls back to plugin DefaultAction,
    /// then skip. Swap still needs a filler clip in the BVF.
    /// </summary>
    public string GetHitAction(UserDto user)
    {
        var config = Plugin.Instance?.Configuration;
        if (config != null &&
            user != null &&
            config.TryGetUserProfile(user.Id.ToString(), out var stored) &&
            !string.IsNullOrWhiteSpace(stored.HitAction))
        {
            return TopicPlayback.NormalizeHitAction(stored.HitAction);
        }

        return TopicPlayback.NormalizeHitAction(config?.DefaultAction);
    }

    private static string SelectAvailableProfile(Dictionary<string, UserProfile> profiles, string? preferred)
    {
        if (!string.IsNullOrEmpty(preferred) && profiles.ContainsKey(preferred))
            return preferred;

        if ((preferred == "teen_m" || preferred == "teen_f") && profiles.ContainsKey("teen"))
            return "teen";

        foreach (var candidate in new[] { "adult", "teen_m", "teen_f", "teen", "child" })
        {
            if (profiles.ContainsKey(candidate))
                return candidate;
        }

        return profiles.Keys.FirstOrDefault() ?? "adult";
    }

    /// <summary>
    /// Resolves a profile key from age and sex.
    /// </summary>
    public static string ResolveFromAgeSex(int age, string sex)
    {
        if (age < 13)
            return "child";

        if (age < 18)
            return sex == "female" ? "teen_f" : "teen_m";

        return "adult";
    }

    /// <summary>
    /// Calculates age in whole years from a date of birth.
    /// </summary>
    public static int CalculateAge(DateOnly dob)
    {
        var today = DateOnly.FromDateTime(DateTime.UtcNow);
        var age = today.Year - dob.Year;
        if (today < dob.AddYears(age))
            age--;
        return age;
    }

}
