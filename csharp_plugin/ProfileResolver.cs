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
    ///   3. Plugin's DefaultProfile setting
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
        return SelectAvailableProfile(manifest.Profiles, config?.DefaultProfile);
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
