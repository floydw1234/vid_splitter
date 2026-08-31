using System.Collections.Generic;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.Movies;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.MediaInfo;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Movie backed by a first-class <c>.bvf</c> library file.
/// Static file sources are placeholders so playback goes through Smart Branch dynamic sources.
/// </summary>
public class BvfMovie : Movie
{
    /// <inheritdoc />
    public override string GetClientTypeName() => nameof(Movie);

    /// <inheritdoc />
    public override List<MediaSourceInfo> GetMediaSources(bool enablePathSubstitution)
    {
        return BvfMediaSourceHelpers.CreatePlaceholderSources(this);
    }
}

/// <summary>
/// Generic video backed by a first-class <c>.bvf</c> library file.
/// </summary>
public class BvfVideo : Video
{
    /// <inheritdoc />
    public override string GetClientTypeName() => nameof(Video);

    /// <inheritdoc />
    public override List<MediaSourceInfo> GetMediaSources(bool enablePathSubstitution)
    {
        return BvfMediaSourceHelpers.CreatePlaceholderSources(this);
    }
}

internal static class BvfMediaSourceHelpers
{
    public static List<MediaSourceInfo> CreatePlaceholderSources(BaseItem item)
    {
        return new List<MediaSourceInfo>
        {
            new()
            {
                Id = item.Id.ToString("N"),
                Name = item.Name,
                Path = item.Path,
                Protocol = MediaProtocol.File,
                Type = MediaSourceType.Placeholder,
                Container = "mp4",
                RunTimeTicks = item.RunTimeTicks,
                SupportsDirectPlay = false,
                SupportsDirectStream = false,
                SupportsTranscoding = false,
                SupportsProbing = false,
            }
        };
    }
}
