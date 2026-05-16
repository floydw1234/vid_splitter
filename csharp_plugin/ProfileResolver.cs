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

        if (config?.UserProfiles != null &&
            config.UserProfiles.TryGetValue(userId, out var stored))
        {
            // 1. Explicit override wins
            if (!string.IsNullOrEmpty(stored.ProfileOverride) &&
                manifest.Profiles.ContainsKey(stored.ProfileOverride))
                return stored.ProfileOverride;

            // 2. Auto-resolve from birthday + sex
            if (!string.IsNullOrEmpty(stored.Birthday) &&
                DateOnly.TryParse(stored.Birthday, out var dob))
            {
                var age = CalculateAge(dob);
                return ResolveFromAgeSex(age, stored.Sex ?? "unset");
            }
        }

        // 3. Fall back to the plugin's default profile
        return config?.DefaultProfile ?? "adult";
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

    /// <summary>
    /// Resolves which file path to serve for a segment given a user profile.
    /// </summary>
    public ResolvedSegment ResolveSegment(Segment segment, string profile, string movieDirectory, string fillerDirectory)
    {
        var resolved = new ResolvedSegment { Source = segment };

        if (segment.Risk == "safe" || segment.Action == "play")
        {
            // Play the original segment
            resolved.ResolvedPath = GetSegmentPath(movieDirectory, segment.Id);
            resolved.IsSwapped = false;
            resolved.SwapType = "original";
            return resolved;
        }

        // Mature content — check if this profile filters it
        var profileFilters = GetProfileFilters(profile);
        var matchingTags = segment.Tags.Intersect(profileFilters).ToList();

        if (!matchingTags.Any())
        {
            // Profile doesn't filter these tags — play original
            resolved.ResolvedPath = GetSegmentPath(movieDirectory, segment.Id);
            resolved.IsSwapped = false;
            resolved.SwapType = "original";
            return resolved;
        }

        // Profile filters these tags — check swap options
        if (segment.SwapOptions != null && segment.SwapOptions.ContainsKey(profile))
        {
            var swapChoice = segment.SwapOptions[profile];
            if (swapChoice == "original")
            {
                resolved.ResolvedPath = GetSegmentPath(movieDirectory, segment.Id);
                resolved.IsSwapped = false;
                resolved.SwapType = "original";
            }
            else if (swapChoice.StartsWith("filler_"))
            {
                resolved.ResolvedPath = GetFillerPath(fillerDirectory, swapChoice);
                resolved.IsSwapped = true;
                resolved.SwapType = "filler";
            }
            else
            {
                // Unknown swap option — skip
                resolved.ResolvedPath = string.Empty;
                resolved.IsSwapped = true;
                resolved.SwapType = "skip";
            }
        }
        else
        {
            // No swap option defined — skip by default
            resolved.ResolvedPath = string.Empty;
            resolved.IsSwapped = true;
            resolved.SwapType = "skip";
        }

        return resolved;
    }

    /// <summary>
    /// Gets the list of filter tags for a profile key.
    /// </summary>
    private static List<string> GetProfileFilters(string profile)
    {
        return profile switch
        {
            "child" => new List<string> { "nudity", "violence", "language", "fear" },
            "teen_m" => new List<string> { "nudity", "gore" },
            "teen_f" => new List<string> { "nudity", "violence" },
            "adult" => new List<string>(),
            _ => new List<string>()
        };
    }

    /// <summary>
    /// Constructs the path to a segment file.
    /// Segments are stored in the same directory as the movie.
    /// </summary>
    private static string GetSegmentPath(string movieDirectory, string segmentId)
    {
        if (!int.TryParse(segmentId.Split('_').LastOrDefault(), out var index))
            index = 0;
        return Path.Combine(movieDirectory, $"seg_{index:000}.ts");
    }

    /// <summary>
    /// Gets the path to a filler video.
    /// </summary>
    private static string GetFillerPath(string fillerDirectory, string fillerName)
    {
        return Path.Combine(fillerDirectory, $"{fillerName}.ts");
    }
}
