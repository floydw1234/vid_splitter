using System.Collections.Generic;
using System.Linq;
using Jellyfin.Data.Enums;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Controller.Dto;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Entities;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Maps Jellyfin users to branch profiles and resolves segment actions.
/// </summary>
public class ProfileResolver
{
    /// <summary>
    /// Maps a Jellyfin user to a branch profile key.
    /// 
    /// Strategy:
    /// - Check user settings for a custom "branch_profile" tag
    /// - Fall back to parental rating ceiling from the user's policy
    /// - Default to "adult" if nothing matches
    /// </summary>
    public string ResolveProfile(UserDto user, BranchManifest manifest)
    {
        // 1. Check for explicit profile override in user metadata
        var explicitProfile = GetExplicitProfile(user);
        if (explicitProfile != null && manifest.Profiles.ContainsKey(explicitProfile))
            return explicitProfile;

        // 2. Infer from the account's maximum parental rating
        return GetProfileFromParentalRating(user);
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
    /// Gets the explicit profile from user metadata.
    /// </summary>
    private static string? GetExplicitProfile(UserDto user)
    {
        // Check user settings for a custom branch profile
        // This would be set via the plugin's config page
        if (user.Settings != null && user.Settings.ContainsKey("branch_profile"))
        {
            return user.Settings["branch_profile"] as string;
        }
        return null;
    }

    /// <summary>
    /// Infers a branch profile from the account's MaxParentalRating policy.
    /// Jellyfin's rating scale: G≈1, PG≈5, PG-13≈7, R≈9, null=unrestricted.
    /// A low ceiling indicates a child/family account; mid-range indicates teen.
    /// </summary>
    private static string GetProfileFromParentalRating(UserDto user)
    {
        var rating = user.Policy?.MaxParentalRating;

        if (rating == null)
            return "adult"; // no restriction — treat as adult

        if (rating <= 5)
            return "child"; // G or PG ceiling

        if (rating <= 7)
            return "teen_m"; // PG-13 ceiling

        return "adult";
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
