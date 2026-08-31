using System;
using System.Linq;
using Emby.Naming.Common;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Registers <c>.bvf</c> as a recognized Jellyfin video file extension.
/// </summary>
public static class BvfFormatRegistration
{
    public const string Extension = ".bvf";

    /// <summary>
    /// Ensures <c>.bvf</c> is present in <see cref="NamingOptions.VideoFileExtensions"/>.
    /// </summary>
    /// <returns><c>true</c> when the extension was newly added.</returns>
    public static bool EnsureRegistered(NamingOptions namingOptions)
    {
        ArgumentNullException.ThrowIfNull(namingOptions);

        if (namingOptions.VideoFileExtensions.Contains(Extension, StringComparer.OrdinalIgnoreCase))
            return false;

        namingOptions.VideoFileExtensions = namingOptions.VideoFileExtensions
            .Append(Extension)
            .ToArray();
        return true;
    }

    public static bool IsBvfPath(string? path)
    {
        return !string.IsNullOrEmpty(path)
            && path.EndsWith(Extension, StringComparison.OrdinalIgnoreCase);
    }
}
