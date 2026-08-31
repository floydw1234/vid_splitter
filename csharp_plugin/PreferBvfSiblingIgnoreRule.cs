using System;
using System.IO;
using Emby.Naming.Common;
using Emby.Naming.Video;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Resolvers;
using MediaBrowser.Model.IO;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// When a sibling <c>.bvf</c> exists, ignore other video files with the same stem
/// so the BVF is the sole library item.
/// </summary>
public sealed class PreferBvfSiblingIgnoreRule : IResolverIgnoreRule
{
    private readonly NamingOptions _namingOptions;

    public PreferBvfSiblingIgnoreRule(NamingOptions namingOptions)
    {
        _namingOptions = namingOptions;
        BvfFormatRegistration.EnsureRegistered(_namingOptions);
    }

    /// <inheritdoc />
    public bool ShouldIgnore(FileSystemMetadata fileInfo, BaseItem? parent)
    {
        if (fileInfo.IsDirectory)
            return false;

        var path = fileInfo.FullName;
        if (string.IsNullOrEmpty(path) || BvfFormatRegistration.IsBvfPath(path))
            return false;

        if (!VideoResolver.IsVideoFile(path, _namingOptions))
            return false;

        var siblingBvf = Path.ChangeExtension(path, BvfFormatRegistration.Extension);
        return !string.IsNullOrEmpty(siblingBvf) && File.Exists(siblingBvf);
    }
}
