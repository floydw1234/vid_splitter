using System;
using System.Collections.Generic;
using System.IO;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Playback-time topic matching. The BVF generator only labels segments; which
/// topics a viewer avoids, and whether a hit skips or swaps, is plugin config.
/// Swap still requires a filler clip in the BVF; otherwise the hit skips.
/// </summary>
public static class TopicPlayback
{
    public const string PlayAction = "play";
    public const string SkipAction = "skip";
    public const string SwapAction = "swap";

    public static string NormalizeHitAction(string? action)
    {
        if (string.Equals(action?.Trim(), SwapAction, StringComparison.OrdinalIgnoreCase))
            return SwapAction;
        return SkipAction;
    }

    public static string RestrictHit(string preferredAction, bool hasSwapTarget)
    {
        if (string.Equals(NormalizeHitAction(preferredAction), SwapAction, StringComparison.OrdinalIgnoreCase)
            && hasSwapTarget)
        {
            return SwapAction;
        }

        return SkipAction;
    }

    public static string ApplyPreferredAction(
        string bakedAction,
        string preferredAction,
        bool hasSwapTarget)
    {
        if (string.IsNullOrWhiteSpace(bakedAction) ||
            string.Equals(bakedAction, PlayAction, StringComparison.OrdinalIgnoreCase))
        {
            return PlayAction;
        }

        return RestrictHit(preferredAction, hasSwapTarget);
    }

    public static HashSet<string> NormalizeTopics(IEnumerable<string>? topics)
    {
        var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (topics == null)
            return set;

        foreach (var topic in topics)
        {
            if (!string.IsNullOrWhiteSpace(topic))
                set.Add(topic.Trim());
        }

        return set;
    }

    public static IEnumerable<string> Labels(Segment segment)
    {
        if (segment?.Tags != null)
        {
            foreach (var tag in segment.Tags)
            {
                if (!string.IsNullOrWhiteSpace(tag))
                    yield return tag;
            }
        }

        if (segment?.Topics != null)
        {
            foreach (var topic in segment.Topics)
            {
                if (!string.IsNullOrWhiteSpace(topic))
                    yield return topic;
            }
        }
    }

    public static bool Hits(Segment segment, IReadOnlySet<string> avoidedTopics)
    {
        if (segment == null || avoidedTopics == null || avoidedTopics.Count == 0)
            return false;

        foreach (var label in Labels(segment))
        {
            if (avoidedTopics.Contains(label))
                return true;
        }

        return false;
    }

    public static string ResolveAction(
        Segment segment,
        IReadOnlySet<string> avoidedTopics,
        string preferredAction = SwapAction)
    {
        if (!Hits(segment, avoidedTopics))
            return PlayAction;
        return RestrictHit(preferredAction, HasSwapTarget(segment));
    }

    public static bool HasSwapTarget(Segment segment)
    {
        if (!string.IsNullOrWhiteSpace(segment?.ProfileSegmentId))
            return true;
        if (segment?.Profiles == null)
            return false;

        foreach (var profileAction in segment.Profiles.Values)
        {
            if (profileAction != null &&
                string.Equals(profileAction.Action, "swap", StringComparison.OrdinalIgnoreCase) &&
                !string.IsNullOrWhiteSpace(profileAction.SegmentId))
            {
                return true;
            }
        }

        return false;
    }

    public static string ResolveTargetId(Segment segment, string action)
    {
        if (segment == null)
            throw new ArgumentNullException(nameof(segment));
        if (!string.Equals(action, "swap", StringComparison.OrdinalIgnoreCase))
            return segment.Id;
        if (!string.IsNullOrWhiteSpace(segment.ProfileSegmentId))
            return segment.ProfileSegmentId;

        if (segment.Profiles != null)
        {
            foreach (var profileAction in segment.Profiles.Values)
            {
                if (profileAction != null &&
                    string.Equals(profileAction.Action, "swap", StringComparison.OrdinalIgnoreCase) &&
                    !string.IsNullOrWhiteSpace(profileAction.SegmentId))
                {
                    return profileAction.SegmentId;
                }
            }
        }

        throw new InvalidDataException(
            $"BVF segment '{segment.Id}' has no swap target for topic playback.");
    }
}
