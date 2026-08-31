using System;
using System.Collections.Generic;
using System.IO;
using Jellyfin.Plugin.SmartBranching;
using Jellyfin.Plugin.SmartBranching.Configuration;
using Jellyfin.Plugin.SmartBranching.Models;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;
using Xunit;
using SmartBranchingPlugin = Jellyfin.Plugin.SmartBranching.Plugin;

namespace SmartBranching.Plugin.Tests;

[Collection(PluginStateCollection.Name)]
public class ProfileResolverTests
{
    [Fact]
    public void ResolveProfile_Under13User_ResolvesChild()
    {
        CreatePluginContext(BuildConfig(yearsAgo: 12, sex: "male"));

        var result = new ProfileResolver().ResolveProfile(CreateUser(), CreateManifest());

        Assert.Equal("child", result);
    }

    [Fact]
    public void ResolveProfile_TeenMaleUser_ResolvesTeenMale()
    {
        CreatePluginContext(BuildConfig(yearsAgo: 15, sex: "male"));

        var result = new ProfileResolver().ResolveProfile(CreateUser(), CreateManifest());

        Assert.Equal("teen_m", result);
    }

    [Fact]
    public void ResolveProfile_TeenFemaleUser_ResolvesTeenFemale()
    {
        CreatePluginContext(BuildConfig(yearsAgo: 16, sex: "female"));

        var result = new ProfileResolver().ResolveProfile(CreateUser(), CreateManifest());

        Assert.Equal("teen_f", result);
    }

    [Fact]
    public void ResolveProfile_AdultUser_ResolvesAdult()
    {
        CreatePluginContext(BuildConfig(yearsAgo: 25, sex: "female"));

        var result = new ProfileResolver().ResolveProfile(CreateUser(), CreateManifest());

        Assert.Equal("adult", result);
    }

    [Fact]
    public void ResolveProfile_ProfileOverride_WinsOverAgeAndSex()
    {
        CreatePluginContext(BuildConfig(yearsAgo: 25, sex: "male", profileOverride: "child"));

        var result = new ProfileResolver().ResolveProfile(CreateUser(), CreateManifest());

        Assert.Equal("child", result);
    }

    [Fact]
    public void ResolveProfile_ProfileOverride_WorksWhenConfigUserIdHasNoDashes()
    {
        var userId = Guid.Parse("22222222-2222-2222-2222-222222222222");
        var config = new PluginConfiguration { DefaultProfile = "child" };
        config.SetUserProfiles(new Dictionary<string, UserBranchProfile>
        {
            [userId.ToString("N")] = new()
            {
                Birthday = DateOnly.FromDateTime(DateTime.UtcNow).AddYears(-3).ToString("yyyy-MM-dd"),
                ProfileOverride = "adult",
            }
        });
        CreatePluginContext(config);

        var result = new ProfileResolver().ResolveProfile(new UserDto { Id = userId }, CreateManifest());

        Assert.Equal("adult", result);
    }

    private static SmartBranchingPlugin CreatePluginContext(PluginConfiguration configuration)
    {
        var plugin = new SmartBranchingPlugin(new TestApplicationPaths(), new TestXmlSerializer());
        plugin.UpdateConfiguration(configuration);
        return plugin;
    }

    private static PluginConfiguration BuildConfig(int yearsAgo, string sex, string? profileOverride = null)
    {
        var userId = TestUserId.ToString();
        var config = new PluginConfiguration { DefaultProfile = "adult" };
        config.SetUserProfiles(new Dictionary<string, UserBranchProfile>
        {
            [userId] = new()
            {
                Birthday = DateOnly.FromDateTime(DateTime.UtcNow).AddYears(-yearsAgo).ToString("yyyy-MM-dd"),
                Sex = sex,
                ProfileOverride = profileOverride
            }
        });
        return config;
    }

    private static UserDto CreateUser()
    {
        return new UserDto
        {
            Id = TestUserId
        };
    }

    private static BranchManifest CreateManifest()
    {
        return new BranchManifest
        {
            Profiles = new Dictionary<string, UserProfile>
            {
                ["child"] = new(),
                ["teen_m"] = new(),
                ["teen_f"] = new(),
                ["adult"] = new()
            }
        };
    }

    private static readonly Guid TestUserId = Guid.Parse("11111111-1111-1111-1111-111111111111");

    private sealed class TestApplicationPaths : IApplicationPaths
    {
        private readonly string _root = Path.Combine(Path.GetTempPath(), "smartbranching-tests", Guid.NewGuid().ToString("N"));

        public TestApplicationPaths()
        {
            Directory.CreateDirectory(_root);
        }

        public string ProgramDataPath => _root;
        public string WebPath => _root;
        public string ProgramSystemPath => _root;
        public string DataPath => _root;
        public string ImageCachePath => _root;
        public string PluginsPath => _root;
        public string PluginConfigurationsPath => _root;
        public string LogDirectoryPath => _root;
        public string ConfigurationDirectoryPath => _root;
        public string SystemConfigurationFilePath => Path.Combine(_root, "system.xml");
        public string CachePath => _root;
        public string TempDirectory => _root;
        public string VirtualDataPath => _root;
    }

    private sealed class TestXmlSerializer : IXmlSerializer
    {
        public object DeserializeFromStream(Type type, Stream stream) => Activator.CreateInstance(type)!;

        public void SerializeToStream(object obj, Stream stream)
        {
        }

        public void SerializeToFile(object obj, string file)
        {
        }

        public object DeserializeFromFile(Type type, string file) => Activator.CreateInstance(type)!;

        public object DeserializeFromBytes(Type type, byte[] buffer) => Activator.CreateInstance(type)!;
    }
}
